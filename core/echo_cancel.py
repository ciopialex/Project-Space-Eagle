"""Echo cancellation, so the microphone stops hearing the speakers.

Gemini's server-side VAD treats anything from the speakers as speech, so without
this the eagle interrupts itself and answers the podcast. PipeWire's WebRTC echo-
cancel module, made the default source and sink, takes the playback tone from +59
dB to below silence, with no application change: sd.InputStream() opens the system
default.

It reports the defaults, never just that the module is loaded, and requires both
defaults (the canceller needs the playback signal). Linux only: Windows (WASAPI
communications endpoint) and macOS (VoiceProcessingIO) already apply it, so they
get supported=False.

Offered in setup, never applied unasked: it changes the default microphone and
speakers for every app.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from actions.cli.base import CommandMixin

# The virtual nodes the module creates; state() compares the defaults against these
# names.
SOURCE_NODE = "effect_source.echo-cancel"
SINK_NODE = "effect_sink.echo-cancel"

# PipeWire reads every .conf in this drop-in directory, so one file needs no edit to
# anything PipeWire ships.
CONFIG_PATH = (Path.home() / ".config" / "pipewire" / "pipewire.conf.d"
               / "99-echo-cancel.conf")

# Exactly the configuration measured working, written verbatim.
CONFIG_BODY = """context.modules = [
    {   name = libpipewire-module-echo-cancel
        args = {
            library.name  = aec/libspa-aec-webrtc
            aec.args = {
                webrtc.gain_control      = true
                webrtc.noise_suppression = false
                webrtc.voice_detection   = true
                webrtc.extended_filter   = true
                webrtc.delay_agnostic    = true
            }
            capture.props  = { node.name = "effect_input.echo-cancel"   node.passive = true }
            source.props   = { node.name = "effect_source.echo-cancel"  node.description = "Echo-Cancelled Microphone" }
            sink.props     = { node.name = "effect_sink.echo-cancel"    node.description = "Echo-Cancelled Output" }
            playback.props = { node.name = "effect_output.echo-cancel"  node.passive = true }
        }
    }
]
"""

_SERVICES = ("pipewire.service", "pipewire-pulse.service", "wireplumber.service")

# How long the nodes get to appear after the restart, polled, never slept.
POLL_TIMEOUT_S = 5.0
POLL_INTERVAL_S = 0.25

# Long enough for three systemd units on a slow machine.
RESTART_TIMEOUT_S = 30.0
QUERY_TIMEOUT_S = 5.0

_OS_DETAIL = {
    "windows": "the eagle's audio does not go through Windows' voice "
               "processing, so its own voice reaches the microphone; talking "
               "over it needs headphones or a raised voice",
    "macos": "the eagle's audio does not go through macOS voice processing, "
             "so its own voice reaches the microphone; talking over it needs "
             "headphones or a raised voice",
}


@dataclass(frozen=True)
class State:
    """What is true now. `supported` and `active` stay separate: "not cancelled"
    invites a fix, "cannot be asked" does not.
    """
    supported: bool
    active: bool
    detail: str = ""


def _plat() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


class _Wpctl(CommandMixin):
    """The injected-runner seam from actions/cli/base, so tests need no PipeWire."""

    def run(self, argv: Sequence[str], timeout: float = QUERY_TIMEOUT_S):
        """(returncode, stdout), or None when the program is not installed. text=True
        is required.
        """
        try:
            proc = self._run(list(argv), capture_output=True, timeout=timeout,
                             text=True)
        except FileNotFoundError:
            return None
        except Exception:
            return (1, "")
        return (proc.returncode, proc.stdout or "")


def _node_name(text: str) -> str | None:
    """The `node.name` property from wpctl inspect, anchored so media.name and
    node.description do not match.
    """
    m = re.search(r'^\s*\*?\s*node\.name\s*=\s*"([^"]*)"\s*$', text, re.M)
    return m.group(1) if m else None


_TOP = re.compile(r"^([A-Z][A-Za-z ]*)$")
_SUB = re.compile(r"(Sink endpoints|Source endpoints|Devices|Sinks|Sources"
                  r"|Streams|Clients):\s*$")
_ROW = re.compile(r"(\d+)\.\s+\S")


def _audio_node_ids(status: str) -> list[int]:
    """Ids of audio sinks and sources from wpctl status, which prints descriptions,
    never names; finding a node by name takes an inspect per candidate.
    """
    ids: list[int] = []
    audio = False
    sub = ""
    for line in status.splitlines():
        top = _TOP.match(line)
        if top:
            audio = top.group(1).strip() == "Audio"
            sub = ""
            continue
        head = _SUB.search(line)
        if head:
            sub = head.group(1)
            continue
        if audio and sub in ("Sinks", "Sources"):
            row = _ROW.search(line)
            if row:
                ids.append(int(row.group(1)))
    return ids


def _find_nodes(wp: _Wpctl) -> dict[str, int]:
    """`{node.name: id}` for the two nodes we care about, as they exist now."""
    r = wp.run(["wpctl", "status"])
    if r is None or r[0] != 0:
        return {}
    found: dict[str, int] = {}
    for nid in _audio_node_ids(r[1]):
        got = wp.run(["wpctl", "inspect", str(nid)])
        if got is None or got[0] != 0:
            continue
        name = _node_name(got[1])
        if name in (SOURCE_NODE, SINK_NODE):
            found[name] = nid
        if len(found) == 2:
            break
    return found


def _default_node(wp: _Wpctl, target: str):
    """`(ok, node.name)` for `@DEFAULT_AUDIO_SOURCE@` / `..._SINK@`."""
    r = wp.run(["wpctl", "inspect", target])
    if r is None:
        return None, None
    if r[0] != 0:
        return False, None
    return True, _node_name(r[1])


def state(run: Callable | None = None, *, platform: str | None = None) -> State:
    """What the eagle's microphone is attached to. Read-only: never restarts or moves
    a default.
    """
    plat = platform or _plat()
    if plat != "linux":
        return State(False, False, _OS_DETAIL[plat])

    wp = _Wpctl(run)
    ok, source = _default_node(wp, "@DEFAULT_AUDIO_SOURCE@")
    if ok is None:
        return State(False, False,
                     "wpctl is not installed, so the audio defaults cannot be "
                     "read — PipeWire's control tool is what answers this")
    if not ok:
        return State(False, False,
                     "wpctl could not read the default source; PipeWire may "
                     "not be running")
    ok, sink = _default_node(wp, "@DEFAULT_AUDIO_SINK@")
    if not ok:
        return State(False, False,
                     "wpctl could not read the default sink; PipeWire may "
                     "not be running")

    src_ok = source == SOURCE_NODE
    sink_ok = sink == SINK_NODE
    if src_ok and sink_ok:
        return State(True, True, "both audio defaults are the cancelled nodes")
    if src_ok:
        return State(True, False,
                     f"the default source is cancelled but the default sink is "
                     f"{sink!r} — playback never reaches the canceller, so it "
                     f"has nothing to subtract and nothing is cancelled")
    if sink_ok:
        return State(True, False,
                     f"the default sink is cancelled but the default source is "
                     f"{source!r} — the eagle still records the bare microphone")
    return State(True, False,
                 f"neither default is cancelled (source {source!r}, "
                 f"sink {sink!r})")


def install(run: Callable | None = None, *, platform: str | None = None,
            config_path: Path | None = None,
            sleep: Callable[[float], None] = time.sleep,
            now: Callable[[], float] = time.monotonic) -> tuple[bool, str]:
    """Make it true, then report the re-read state: (ok, what happened). Commands
    exiting zero is not ok.
    """
    plat = platform or _plat()
    if plat != "linux":
        return False, _OS_DETAIL[plat]

    wp = _Wpctl(run)
    before = state(run, platform=plat)
    if not before.supported:
        # Refuse whole rather than act half.
        return False, before.detail
    if before.active:
        return True, "already active — " + before.detail

    # The canceller may already run with one default pointing past it: fix the defaults
    # without restarting the audio server.
    nodes = _find_nodes(wp)
    if len(nodes) < 2:
        path = Path(config_path) if config_path is not None else CONFIG_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(CONFIG_BODY)
        except OSError as e:
            return False, f"could not write {path}: {e}"

        r = wp.run(["systemctl", "--user", "restart", *_SERVICES],
                   timeout=RESTART_TIMEOUT_S)
        if r is None:
            return False, ("systemctl is not available, so the audio services "
                           f"cannot be restarted to load {path}")
        if r[0] != 0:
            return False, (f"the audio services failed to restart, so {path} "
                           "was never loaded")

        deadline = now() + POLL_TIMEOUT_S
        while True:
            nodes = _find_nodes(wp)
            if len(nodes) == 2:
                break
            if now() >= deadline:
                return False, (f"the echo-cancel nodes did not appear within "
                               f"{POLL_TIMEOUT_S:g}s of the restart; "
                               f"{path} may not have been loaded")
            sleep(POLL_INTERVAL_S)

    for node in (SOURCE_NODE, SINK_NODE):
        got = wp.run(["wpctl", "set-default", str(nodes[node])])
        if got is None or got[0] != 0:
            return False, f"wpctl refused to make {node} the default"

    after = state(run, platform=plat)
    if after.active:
        return True, "echo cancellation is active — " + after.detail
    return False, "the commands were accepted but " + after.detail


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--install" in argv:
        ok, detail = install()
        print(("✓ " if ok else "✗ ") + detail)
        return 0 if ok else 1
    st = state()
    print(f"supported={st.supported} active={st.active} — {st.detail}")
    return 0 if st.active else 1


if __name__ == "__main__":
    raise SystemExit(main())
