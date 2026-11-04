"""The full window: opened from the pill the way the last onboarding screen says
(double-click the bar), a typed question answered, and settings that stick."""
import json

from conftest import connections, log_lines, log_tail, wait_for


def open_full_window(eagle):
    pill = eagle.page("pill.html")
    pill.click("#pill", count=2)
    dash = eagle.page("dashboard.html")
    dash.wait("document.visibilityState === 'visible' && document.hasFocus()", 10,
              what="the full window to open and take focus")
    return dash


def ask_typed(dash, text):
    dash.click(".cmd input")
    dash.type(text)
    assert dash.eval("document.querySelector('.cmd input').value") == text, "the question is in the box"
    dash.press("Enter")


def test_a_double_click_opens_the_window_and_a_typed_question_is_answered(launch):
    sb, eagle = launch()
    wait_for(lambda: connections(sb) >= 1, 40, "the voice loop to connect\n" + log_tail(sb))
    dash = open_full_window(eagle)

    ask_typed(dash, "What is 7 times 8? Reply with just the number.")
    assert dash.eval("document.querySelector('.cmd input').value") == "", "the box clears once sent"
    wait_for(lambda: any("56" in t for t in log_lines(eagle, "ae")), 40,
             "an answer of 56\n" + log_tail(sb))


def test_a_new_name_is_saved_and_shown_again(launch):
    sb, eagle = launch()
    wait_for(lambda: connections(sb) >= 1, 40, "the voice loop to connect\n" + log_tail(sb))
    dash = open_full_window(eagle)

    dash.click(".stud")                                       # the gear
    dash.wait("document.getElementById('set-addr') && document.getElementById('set-addr').value === 'Sam'",
              10, what="settings to show the saved name")
    dash.click("#set-addr")
    dash.eval("document.getElementById('set-addr').select()")
    dash.type("Riley")
    assert dash.eval("document.getElementById('set-addr').value") == "Riley"
    dash.click("#set-id-save")
    wait_for(lambda: json.loads(sb.config_path.read_text()).get("user_name") == "Riley", 10,
             "the new name to be saved")

    # Closed and opened again, the panel shows what Python holds, not what was typed.
    dash.click("#set-close")
    dash.eval("document.getElementById('set-addr').value = ''")
    dash.click(".stud")
    dash.wait("document.getElementById('set-addr').value === 'Riley'", 10,
              what="the saved name when settings open again")


def test_a_new_name_is_used_in_the_conversation(launch):
    sb, eagle = launch()
    wait_for(lambda: connections(sb) >= 1, 40, "the voice loop to connect\n" + log_tail(sb))
    dash = open_full_window(eagle)
    dash.click(".stud")
    dash.wait("document.getElementById('set-addr').value === 'Sam'", 10)
    dash.click("#set-addr")
    dash.eval("document.getElementById('set-addr').select()")
    dash.type("Riley")
    assert dash.eval("document.getElementById('set-addr').value") == "Riley"
    dash.click("#set-id-save")
    wait_for(lambda: json.loads(sb.config_path.read_text()).get("user_name") == "Riley", 10,
             "the new name to be saved")
    dash.click("#set-close")

    ask_typed(dash, "What is my name? Reply with just my name.")
    answer = wait_for(lambda: [t for t in log_lines(eagle, "ae") if t.strip()], 40,
                      "an answer\n" + log_tail(sb))
    assert any("Riley" in t for t in answer), f"still uses the old name: {answer}"


def test_what_is_on_the_screen_is_seen(launch):
    """The vision tool end to end on X11 (the sandbox's screen): captured, sent,
    and described from what is really there."""
    sb, eagle = launch()
    wait_for(lambda: connections(sb) >= 1, 40, "the voice loop to connect\n" + log_tail(sb))
    dash = open_full_window(eagle)
    ask_typed(dash, "Look at my screen. Which app's window is open? Answer in one short sentence.")
    wait_for(lambda: "[Vision]" in sb.log_path.read_text(errors="replace"), 40,
             "the screen to be captured\n" + log_tail(sb))
    seen = ("aethelark", "eagle", "dashboard", "microphone", "listening")
    wait_for(lambda: any(w in t.lower() for t in log_lines(eagle, "ae") for w in seen), 60,
             "a description of the open window\n" + log_tail(sb))


def test_after_esc_stops_an_answer_the_next_question_is_answered_at_once(launch):
    """The server holds a stopped answer's turn until its audio would have played
    out; a typed question used to wait behind it for 31-36 s."""
    import time
    sb, eagle = launch()
    wait_for(lambda: connections(sb) >= 1, 40, "the voice loop to connect\n" + log_tail(sb))
    dash = open_full_window(eagle)
    pill = eagle.page("pill.html")
    ask_typed(dash, "Count slowly from one to forty, every number spelled out.")
    pill.wait("window.island.state === 'speaking'", 30, what="the count to start")
    time.sleep(1.5)
    dash.press("Escape")
    pill.wait("window.island.state !== 'speaking'", 2, what="Esc to stop the eagle")
    n = len(log_lines(eagle, "ae"))
    t0 = time.monotonic()
    ask_typed(dash, "What is 2 plus 2? Just the number.")
    wait_for(lambda: any("4" in t or "four" in t.lower() for t in log_lines(eagle, "ae")[n:]), 10,
             "an answer within 10 s of stopping\n" + log_tail(sb))
    print(f"answered {time.monotonic() - t0:.1f}s after the question")


def test_a_refused_key_typed_into_settings_is_not_saved_and_says_why(launch):
    sb, eagle = launch()
    wait_for(lambda: connections(sb) >= 1, 40, "the voice loop to connect\n" + log_tail(sb))
    good = json.loads(sb.config_path.read_text())["gemini_api_key"]
    dash = open_full_window(eagle)
    dash.click(".stud")
    dash.click("#set-key")
    dash.paste("AIza" + "y" * 35)
    dash.click("#set-key-save")
    msg = dash.wait("(t => t && !t.startsWith('Checking') && t)(document.getElementById('set-key-msg').innerText)",
                    30, what="Google's verdict on the key")
    assert "updated" not in msg.lower(), msg
    assert json.loads(sb.config_path.read_text())["gemini_api_key"] == good
    print("message:", msg)


def test_quit_in_settings_closes_the_eagle(launch):
    sb, eagle = launch()
    wait_for(lambda: connections(sb) >= 1, 40, "the voice loop to connect\n" + log_tail(sb))
    dash = open_full_window(eagle)
    dash.click(".stud")
    x, y = dash.center("#set-quit")
    try:
        for kind in ("mousePressed", "mouseReleased"):
            dash.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)
    except Exception:
        pass                              # the page may close under the click
    wait_for(lambda: sb.proc.poll() is not None, 15, "the eagle to exit")
