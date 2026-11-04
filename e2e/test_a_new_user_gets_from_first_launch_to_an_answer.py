"""A first run, the way a new user meets it: onboarding by clicks and keys, then a
spoken question answered out loud.

The mic plays one clip, a few seconds after each time it is opened: onboarding's
mic check hears it, then the voice loop hears it again as the first question.
"""
import json
import re
import stat

from conftest import connections, log_lines, log_tail, spoken, wait_for

STEP = "document.querySelector('.screen.active').dataset.step"


def at(ob, step, timeout=15):
    ob.wait(f"{STEP} === {json.dumps(step)}", timeout, what=f"the {step} screen")


def test_onboarding_to_a_spoken_answer(launch, say, gemini_key):
    sb, eagle = launch(fresh=True, mic_wav=str(say("What is twelve times twelve?")), mic_wav_at=2.5)
    ob = eagle.page("onboarding.html")
    try:
        at(ob, "welcome")
        ob.click("text=Get started")

        at(ob, "modules")
        while ob.exists("#modlist input:checked"):       # nothing to install: keeps the run offline-light
            ob.click("#modlist input:checked")
        ob.click("text=Continue")

        at(ob, "key")
        ob.click("text=Get my free key")
        ob.wait("document.getElementById('key-status').innerText.includes('Waiting for your key')", 10)
        wait_for(lambda: any("aistudio.google.com" in u for u in sb.opened_urls), 10,
                 "the key page to be opened")

        ob.click("#api-key")
        ob.paste("AIza" + "x" * 35)                     # the right shape, refused by Google
        assert ob.text("#key-status") == "", "a key in the field is no longer being waited for"
        ob.press("Enter")
        ob.wait("document.getElementById('key-status').classList.contains('err')", 30,
                what="Google to refuse the made-up key")
        assert at_least_words(ob.text("#key-status"), 3)

        ob.eval("document.getElementById('api-key').select()")
        ob.paste(gemini_key)
        ob.click("text=Continue")
        ob.wait("document.getElementById('key-status').innerText === 'Connected.'", 30,
                what="Google to accept the key")

        at(ob, "name")
        ob.click("#name-input")
        ob.type("Sam")
        ob.press("Enter")

        at(ob, "mic")
        ob.wait("document.getElementById('mic-status').innerText === 'I can hear you.'", 20,
                what="the mic check to hear the clip")
        at(ob, "done", timeout=10)
        assert ob.text("#done-title") == "You're all set, Sam."
        ob.screenshot("done")
        opens_before_start = sb.mic_opens
        ob.click("text=Start")
    except Exception:
        ob.screenshot("failed")
        raise

    # The key and name are saved, readable by the user alone.
    cfg_path = sb.config_path
    wait_for(lambda: cfg_path.exists() and "gemini_api_key" in cfg_path.read_text(), 10,
             "the config to be written")
    cfg = json.loads(cfg_path.read_text())
    assert cfg["gemini_api_key"] == gemini_key and cfg["user_name"] == "Sam"
    assert stat.S_IMODE(cfg_path.stat().st_mode) == 0o600

    # The pill takes over and the voice loop opens the mic and reaches Gemini.
    eagle.page("pill.html", timeout=30)
    wait_for(lambda: connections(sb) >= 1 and sb.mic_opens > opens_before_start, 40,
             "the voice loop to connect and open the mic\n" + log_tail(sb))

    # The clip plays again into the live mic: it is heard and answered out loud.
    wait_for(lambda: any("twelve" in t.lower() or "12" in t for t in log_lines(eagle, "you")), 40,
             "the question to be heard\n" + log_tail(sb))
    wait_for(lambda: any("144" in t or "hundred" in t.lower() and "forty" in t.lower()
                         for t in log_lines(eagle, "ae")), 40,
             "an answer of 144\n" + log_tail(sb))
    wait_for(lambda: spoken(sb) > 0.5, 20, "the answer to be spoken")


def at_least_words(text: str, n: int) -> bool:
    return len(re.findall(r"\w+", text or "")) >= n
