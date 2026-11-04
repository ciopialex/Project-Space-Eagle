"""What the user sees when it goes wrong: a key Google stops accepting, and a voice
loop that cannot start."""
import json
import sys

from conftest import ROOT, connections, log_tail, wait_for

sys.path.insert(0, str(ROOT))


def test_a_refused_key_is_fixed_on_the_spot_and_the_eagle_reconnects(launch, gemini_key):
    sb, eagle = launch(config={"gemini_api_key": "AIza" + "x" * 35})

    ob = eagle.page("onboarding.html", timeout=60)
    ob.wait("document.querySelector('.screen.active').dataset.step === 'key'", 15,
            what="the key screen\n" + log_tail(sb))
    assert ob.eval("document.querySelectorAll('#dots .dot').length") in (0, 1) or \
        ob.eval("getComputedStyle(document.getElementById('dots')).display") == "none", \
        "fixing a key is one screen, not the whole setup again"

    ob.click("text=I already have a key")
    ob.click("#api-key")
    ob.paste(gemini_key)
    ob.click("text=Continue")
    ob.wait("document.getElementById('key-status').innerText === 'Connected.'", 30,
            what="Google to accept the key")

    wait_for(lambda: json.loads(sb.config_path.read_text()).get("gemini_api_key") == gemini_key, 15,
             "the new key to be saved")
    wait_for(lambda: connections(sb) >= 1, 60, "the eagle to reconnect\n" + log_tail(sb))


def test_closing_the_key_screen_leaves_the_eagle_running(launch):
    """The ✕ on the key screen used to quit the whole app: with the full window
    hidden, Qt saw its last window close."""
    sb, eagle = launch(config={"gemini_api_key": "AIza" + "x" * 35})
    ob = eagle.page("onboarding.html", timeout=60)
    ob.wait("document.querySelector('.screen.active').dataset.step === 'key'", 15)
    ob.click("#quit")
    ob.wait("document.visibilityState === 'hidden'", 10, what="the key screen to close")
    import time
    time.sleep(3)
    assert sb.proc.poll() is None, f"the eagle quit (exit {sb.proc.returncode})\n" + log_tail(sb)
    pill = eagle.page("pill.html")
    assert pill.eval("window.island.state") == "notice", "the pill still says the key needs fixing"


def test_the_pill_says_when_the_voice_loop_cannot_start(launch, tmp_path):
    from aethelark_web import VOICE_DID_NOT_START
    broken = tmp_path / "breaks-main"
    broken.mkdir()
    (broken / "sitecustomize.py").write_text(
        "import sys\nsys.modules['main'] = None   # every `from main import` raises\n")
    import os
    sb, eagle = launch(env=dict(os.environ, PYTHONPATH=str(broken)))

    pill = eagle.page("pill.html")
    pill.wait("window.island && window.island.state === 'notice'", 40,
              what="a notice on the pill\n" + log_tail(sb))
    assert pill.text(".pnote") == VOICE_DID_NOT_START
    # Measured once the pill has finished growing into its notice size.
    pill.wait("(e => e.scrollWidth <= e.clientWidth)(document.querySelector('.pnote'))", 3,
              what="the whole message to fit on the pill")
