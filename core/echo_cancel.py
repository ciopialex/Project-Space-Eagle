"""The microphone stops hearing the speakers, on every machine.

WHY THIS EXISTS
---------------
The eagle streams raw microphone audio to Gemini, and Gemini's server-side VAD
decides where a turn ends. So anything coming out of the speakers is, to the
eagle, someone talking: it interrupts itself, and it answers the podcast. That
is not a cosmetic fault — talking to it while your hands and ears are busy is
the product.

Measured on the first machine this was tried on: a playback tone arrived at the
microphone at +59.4 dB over the silent floor. With WebRTC AEC in front of it and
converged, −12.7 dB — quieter than silence — while ambient speech kept 56 dB of
range. About 81 dB of suppression, and no application code changed at all,
because `sd.InputStream()` is opened with no `device=` and therefore takes
whatever the system default is.

So this is not a DSP project. It is a setup step. The only thing missing was
that a human had to type the commands.

WHAT IT REFUSES TO DO
---------------------
**It never reports the module, only the default.** "libpipewire-module-echo-
cancel is loaded" is not "the eagle's microphone is cancelled" — the module can
be loaded and the defaults still pointed at the bare hardware, which is the
"reported before verifying" failure this codebase keeps paying for.

**Both defaults, or none.** AEC subtracts the playback signal from the capture
signal, so it must be handed the playback signal. A machine whose default
source is the cancelled node while its default sink is the hardware cancels
nothing at all, and checking only the microphone would call that a success.

**It does not act off Linux.** Windows applies AEC at the WASAPI communications
endpoint and macOS at CoreAudio's VoiceProcessingIO; there is nothing here to
install and pretending otherwise would be a fake success. Those platforms get
an honest `supported=False` naming the mechanism that already applies.

There is no user-facing surface for any of this. No toggle, no settings pane. A
switch for echo cancellation is a confession that it might not work.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from actions.cli.base import CommandMixin

#: The virtual nodes the module below creates. These names — not the module,
#: not the description — are what `state()` compares the defaults against.
SOURCE_NODE = "effect_source.echo-cancel"
SINK_NODE = "effect_sink.echo-cancel"

#: PipeWire reads every `.conf` in this drop-in directory, so adding one file
#: needs no edit to any file PipeWire ships and survives its upgrades.
CONFIG_PATH = (Path.home() / ".config" / "pipewire" / "pipewire.conf.d"
               / "99-echo-cancel.conf")

#: Exactly what was measured working. Written verbatim rather than generated,
#: because "the config we tested" and "the config we emit" being the same
#: string is the whole reason the measurement above transfers to other machines.
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

#: How long the nodes get to appear after the restart, and how often we look.
#: Bounded and polled, never slept: systemd returns before WirePlumber has
#: re-read the config and published the nodes, and how long that takes is a
#: property of the machine, not a number anyone can pick correctly. A fixed
#: delay standing in for a measurement is a recurring defect shape here.
POLL_TIMEOUT_S = 5.0
POLL_INTERVAL_S = 0.25

#: Long enough for three systemd units to come back on a slow box, short enough
#: that a wedged restart surfaces as an error instead of hanging first run.
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
    """What is true right now.

    `supported` and `active` answer different questions and must not be
    collapsed: "this machine is not cancelled" invites a fix, "this machine
    cannot be asked" does not, and offering a fix that cannot work is how a
    preflight turns a missing tool into a confident wrong instruction.
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
    """The injected-runner seam from `actions/cli/base`, reused rather than
    reinvented, so this module can be exercised in full on a machine that has
    no PipeWire — and so the developer's audio is never a test fixture."""

    def run(self, argv: Sequence[str], timeout: float = QUERY_TIMEOUT_S):
        """`(returncode, stdout)`, or None when the program is not installed.

        The None is the point: `run_cmd` raises `FileNotFoundError` for a
        missing binary, and "wpctl is absent" has to stay distinguishable from
        "wpctl ran and answered no". Every other failure — a timeout, a dead
        daemon — is a non-zero return, which is what it is.

        `text=True` is load-bearing: `core.run_cmd.run_cmd` is a Popen wrapper
        with no text default, so without it every regex below meets bytes.
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
    """The `node.name` property out of `wpctl inspect`, and nothing else.

    Anchored on the property name because the same output carries
    `media.name`, `node.description` and `library.name`, any of which a looser
    pattern happily returns instead.
    """
    m = re.search(r'^\s*\*?\s*node\.name\s*=\s*"([^"]*)"\s*$', text, re.M)
    return m.group(1) if m else None


_TOP = re.compile(r"^([A-Z][A-Za-z ]*)$")
_SUB = re.compile(r"(Sink endpoints|Source endpoints|Devices|Sinks|Sources"
                  r"|Streams|Clients):\s*$")
_ROW = re.compile(r"(\d+)\.\s+\S")


def _audio_node_ids(status: str) -> list[int]:
    """Ids of the audio sinks and sources listed by `wpctl status`.

    `wpctl status` prints descriptions, never node names, and `wpctl inspect`
    takes only a numeric id — so finding a node by name genuinely costs a
    status plus an inspect per candidate. Video devices and clients are skipped
    because their ids share the same numbering and inspecting them is pure
    waste.
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
    """What the eagle's microphone is actually attached to.

    Reads only. The doctor calls this on a machine somebody is using, so it
    must never restart a service or move a default as a side effect of being
    asked a question.
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
    """Make it true, then check whether it is — and report the check.

    Returns `(ok, what happened)`. `ok` is the re-read state, never the fact
    that the commands exited zero: a machine can accept every one of them and
    still leave the defaults where they were.
    """
    plat = platform or _plat()
    if plat != "linux":
        return False, _OS_DETAIL[plat]

    wp = _Wpctl(run)
    before = state(run, platform=plat)
    if not before.supported:
        # Refusing whole beats acting half: no config dropped for a stack that
        # cannot use it, and no restart of an audio server we then cannot
        # verify the result of.
        return False, before.detail
    if before.active:
        return True, "already active — " + before.detail

    # The canceller can already be running with one default pointing past it:
    # found on the first machine this shipped to, source cancelled and sink
    # the bare hardware. Two defaults fix that. Restarting the audio server to
    # reload a config it already has would cut every sound playing on the
    # machine for nothing.
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
