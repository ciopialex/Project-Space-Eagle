import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions.send_message as SM  # noqa: E402
from core import contact_aliases  # noqa: E402
from core.tool_fallback import with_fallback_guidance  # noqa: E402

pytestmark = pytest.mark.usefixtures("user_answers_every_question")


class FakeChat:
    platform = "whatsapp"

    def __init__(self, titles, state=None, lands=True, presses=True):
        self.titles, self.state, self.lands, self.presses = titles, state, lands, presses
        self.opened, self.sent, self.before = None, [], None

    def ready(self):
        return self.state

    def search(self, q):
        return list(self.titles)

    def open_chat(self, title):
        self.opened = title
        return title in self.titles

    def send(self, text):
        if not self.presses:
            return False
        self.sent.append((self.opened, text))
        return True

    def last_outgoing(self):
        text = self.sent[-1][1] if self.sent and self.lands else self.before
        return " ".join(text.split()) if text else text

    def current_title(self):
        return self.opened


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("core.user_paths.user_data_dir", lambda: tmp_path)
    clock = {"t": 1000.0}
    monkeypatch.setattr(SM, "_now", lambda: clock["t"])
    monkeypatch.setattr(SM, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    SM._reset_for_tests()
    return clock


def call(chat, **kw):
    params = {"receiver": "Tata", "message_text": "hi", "platform": "WhatsApp", **kw}
    return SM.send_message(params, surfaces={"whatsapp": chat})


def nothing_sent(r):
    return not r.ok and "Nothing was sent." in (r.message + " " + r.guidance)


def test_an_exact_name_sends_and_is_verified():
    chat = FakeChat(["Tata", "Mama ❤️"])
    r = call(chat)
    assert r.ok and chat.sent == [("Tata", "hi")]
    assert "Tata" in r.message


def test_a_two_line_message_verifies():
    chat = FakeChat(["Tata"])
    r = call(chat, message_text="Hi dad\nsee you at  five")
    assert r.ok and len(chat.sent) == 1


def test_a_close_name_asks_first_and_sends_only_with_the_token():
    chat = FakeChat(["Tata", "Mama ❤️"])
    r = call(chat, receiver="mom")
    assert nothing_sent(r) and r.data["needs_confirmation"] and chat.sent == []
    r2 = call(chat, receiver="mom", confirm_token=r.data["confirm_token"])
    assert r2.ok and chat.sent == [("Mama ❤️", "hi")]


def test_the_token_reaches_the_model():
    chat = FakeChat(["Tata", "Mama ❤️"])
    r = with_fallback_guidance("send_message", call(chat, receiver="mom"))
    wire = r.to_response()
    assert wire["ok"] is False and r.data["confirm_token"] in wire["guidance"]
    assert "web_agency" not in wire["guidance"]


def test_a_confirmed_nickname_is_remembered():
    chat = FakeChat(["Tata", "Mama ❤️"])
    tok = call(chat, receiver="mom").data["confirm_token"]
    call(chat, receiver="mom", confirm_token=tok)
    SM._reset_for_tests()
    chat.sent.append(("Mama ❤️", "earlier"))
    r = call(chat, receiver="mom", message_text="again")
    assert r.ok and chat.sent[-1] == ("Mama ❤️", "again")


def test_a_token_cannot_be_reused_for_different_words():
    chat = FakeChat(["Mama ❤️"])
    tok = call(chat, receiver="mom").data["confirm_token"]
    r = call(chat, receiver="mom", message_text="something else", confirm_token=tok)
    assert nothing_sent(r) and chat.sent == []


def test_a_token_cannot_be_moved_to_another_person():
    chat = FakeChat(["Mama ❤️", "Mamaia"])
    r = call(chat, receiver="mom")
    tok = r.data["confirm_token"]
    r2 = call(chat, receiver="Mamaia", confirm_token=tok)
    assert nothing_sent(r2) and "That yes was for Mama ❤️" in r2.message
    assert chat.sent == [] and contact_aliases.load("whatsapp") == {}


def test_a_token_is_spent_by_the_send_it_allowed():
    chat = FakeChat(["Mama ❤️"])
    tok = call(chat, receiver="mom").data["confirm_token"]
    assert call(chat, receiver="mom", confirm_token=tok).ok
    SM._SENT.clear()
    r = call(chat, receiver="mom", confirm_token=tok)
    assert nothing_sent(r) and len(chat.sent) == 1


def test_two_alexes_is_a_question_with_no_send():
    chat = FakeChat(["Alex Popescu", "Alex Ionescu"])
    r = call(chat, receiver="alex")
    assert nothing_sent(r) and "confirm_token" not in r.data and chat.sent == []
    assert "Alex Popescu" in r.message and "Alex Ionescu" in r.message


def test_an_unfiltered_list_never_makes_a_name_certain():
    chat = FakeChat(["Alex Popescu", "Alex Ionescu"])
    chat.search = lambda q: ["Alex Popescu"] if q == "" else None
    r = call(chat, receiver="alex")
    assert nothing_sent(r) and r.data.get("needs_confirmation") and chat.sent == []


def test_two_chats_with_the_same_name_are_never_offered_as_a_choice():
    chat = FakeChat(["Alex", "Alex", "Mama"])
    r = call(chat, receiver="Alex")
    assert nothing_sent(r) and "confirm_token" not in r.data and chat.sent == []
    assert "more than one chat named Alex" in r.message


def test_signed_out_says_how_to_fix_and_sends_nothing():
    chat = FakeChat(["Tata"], state="signed_out")
    r = call(chat)
    assert nothing_sent(r) and "scan" in r.guidance.lower() and chat.sent == []


def test_a_page_still_loading_is_waited_for():
    chat = FakeChat(["Tata"])
    states = iter(["not_loaded", "not_loaded"])
    chat.ready = lambda: next(states, None)
    r = call(chat)
    assert r.ok and chat.sent == [("Tata", "hi")]


def test_a_page_that_never_loads_sends_nothing():
    chat = FakeChat(["Tata"], state="not_loaded")
    r = call(chat)
    assert nothing_sent(r) and chat.sent == []


def test_a_bubble_that_never_appears_is_not_reported_sent():
    chat = FakeChat(["Tata"], lands=False)
    r = call(chat)
    assert not r.ok


def test_an_enter_that_failed_is_not_reported_sent():
    chat = FakeChat(["Tata"], presses=False)
    r = call(chat)
    assert not r.ok


def test_a_chat_that_changed_under_the_send_is_not_reported_sent():
    chat = FakeChat(["Tata"])
    chat.current_title = lambda: "Someone else"
    r = call(chat)
    assert not r.ok and "Tata" in r.message and len(chat.sent) == 1


def test_a_retry_after_an_unconfirmed_send_sends_nothing_new(isolated):
    chat = FakeChat(["Tata"], lands=False)
    assert not call(chat).ok
    chat.lands = True
    r = call(chat)
    assert not r.ok and "Nothing new was sent" in r.message and len(chat.sent) == 1
    isolated["t"] += 600
    r = call(chat)
    assert nothing_sent(r) and len(chat.sent) == 1


def test_words_already_at_the_end_of_the_chat_are_not_sent_again():
    chat = FakeChat(["Tata"])
    chat.before = "hi"
    r = call(chat)
    assert nothing_sent(r) and chat.sent == []
    r2 = call(chat, confirm_token=r.data["confirm_token"])
    assert r2.ok and chat.sent == [("Tata", "hi")]


def test_a_retry_the_user_asked_for_is_sent_once_more():
    chat = FakeChat(["Tata"], lands=False)
    call(chat)
    chat.lands = True
    tok = call(chat).data["confirm_token"]
    r = call(chat, confirm_token=tok)
    assert r.ok and len(chat.sent) == 2


def test_the_same_message_twice_is_sent_once():
    chat = FakeChat(["Tata"])
    call(chat)
    r = call(chat)
    assert r.ok and r.data.get("deduped") and len(chat.sent) == 1


def test_the_same_words_later_after_other_messages_are_sent(isolated):
    chat = FakeChat(["Tata"])
    call(chat)
    isolated["t"] += 600
    chat.sent.append(("Tata", "how are you"))
    r = call(chat)
    assert r.ok and chat.sent[-1] == ("Tata", "hi")


def test_a_second_message_waits_for_the_first():
    entered, release = threading.Event(), threading.Event()
    slow = FakeChat(["Tata"])
    plain_send = slow.send

    def blocking_send(text):
        entered.set()
        release.wait(5)
        return plain_send(text)

    slow.send = blocking_send
    first = {}
    t = threading.Thread(target=lambda: first.setdefault("r", call(slow)))
    t.start()
    assert entered.wait(5)
    other = FakeChat(["Mama"])
    r = call(other, receiver="Mama", message_text="yo")
    release.set()
    t.join(5)
    assert nothing_sent(r) and "Another message" in r.message and other.sent == []
    assert first["r"].ok and slow.sent == [("Tata", "hi")]


def test_other_apps_point_to_the_website_path():
    r = SM.send_message({"receiver": "x", "message_text": "y", "platform": "Instagram"}, surfaces={})
    assert nothing_sent(r) and "website" in r.guidance
