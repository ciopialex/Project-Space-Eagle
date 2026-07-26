"""Send WhatsApp messages through WhatsApp Web — the reliable path.

Why this exists: the legacy desktop send (actions/send_message._desktop_send)
blind-types into whatever window has focus. On Linux that meant "Go to sleep"
landed in the terminal instead of a chat. This drives web.whatsapp.com inside
the SAME Playwright session infrastructure browser_control already uses, on the
user's real (or persistent automation) profile — so it targets the actual chat,
deterministically, in one tool call instead of six.

Login story: if the real profile is locked (Chrome already open) the session
falls back to ~/.aethelark_profiles/chrome. First run there shows the WhatsApp QR;
scan it once and the login persists across sessions.

Selectors: WhatsApp Web localizes aria-labels (the user may run a non-English
UI), so we anchor on the non-localized `data-tab` contenteditable attributes with
structural fallbacks. If WhatsApp ships a breaking change, the layered fallbacks
+ verbose logging make it a one-line tuning fix.
"""
from __future__ import annotations

import time

from core.tool_result import ToolResult

WA_URL = "https://web.whatsapp.com/"


#  WhatsApp rows are role="row" (they were role="listitem" in older builds — that
#  selector now matches NOTHING, which silently broke contact lookup). Keep both.
_ROW_SEL = '#pane-side [role="row"], #pane-side [role="listitem"]'


async def _visible_chat_titles(page) -> list[str]:
    """Contact/chat NAMES visible in the side pane (search results or recent
    chats). Lets the caller disambiguate a fuzzy name like 'mom' against what's
    actually saved (e.g. 'Mama', 'mom❤️').

    Per row the FIRST span[title] is the contact name and later ones are the
    message preview — scanning span[title] globally mixed previews into the list.
    """
    titles: list[str] = []
    try:
        rows = page.locator(_ROW_SEL)
        count = min(await rows.count(), 30)
        for i in range(count):
            try:
                span = rows.nth(i).locator("span[title]").first
                # Section-header rows ("Chats"/"Messages") have NO span[title].
                # count() is instant; get_attribute would otherwise WAIT the full
                # default timeout on those empty rows — that was the 75s hang.
                if await span.count() == 0:
                    continue
                t = ((await span.get_attribute("title", timeout=1_000)) or "").strip()
            except Exception:
                continue
            if t and t not in titles:
                titles.append(t)
    except Exception:
        pass
    return titles


async def _click_title(page, title: str) -> bool:
    try:
        await page.locator(f'span[title="{title}"]').first.click(timeout=6_000)
        return True
    except Exception:
        return False


async def _open_best_match(page, receiver: str) -> str | None:
    """Open the chat for `receiver` ONLY on a safe match — exact (case-insensitive)
    or a single unambiguous substring hit. Never guesses a fuzzy match, because
    opening the wrong chat would send the message to the wrong person. Returns the
    opened contact's display name, or None (caller then offers candidates)."""
    rl = receiver.strip().lower()
    titles = await _visible_chat_titles(page)

    # 1) exact, case-insensitive
    for t in titles:
        if t.strip().lower() == rl and await _click_title(page, t):
            return t
    # 2) a SINGLE contact whose name contains the term (e.g. 'mommy' → 'Mommy D.')
    subs = [t for t in titles if rl in t.strip().lower()]
    if len(subs) == 1 and await _click_title(page, subs[0]):
        return subs[0]
    # 3) last try: WhatsApp's own exact-title node even if not in our scan
    try:
        node = page.locator(f'span[title="{receiver}"]').first
        if await node.count() > 0:
            await node.click(timeout=6_000)
            return receiver
    except Exception as _e:
        print(f"[whatsapp_web.py] Non-fatal error at line 84: {_e}")
    return None


async def _wa_send(sess, receiver: str, message: str) -> str:
    # ── Phase timing: prints how long each step takes so we can pinpoint the
    # bottleneck (⏱ lines in the console). Remove once WhatsApp send is snappy.
    _t0 = _tlast = time.monotonic()

    def _lap(label: str):
        nonlocal _tlast
        now = time.monotonic()
        print(f"[WhatsAppWeb] ⏱ {label}: +{now - _tlast:.1f}s (total {now - _t0:.1f}s)")
        _tlast = now

    page = await sess._get_page()
    # Cap EVERY Playwright action so a wedged page/dialog can't hang the whole send
    # to the outer 120s timeout — it fails fast and returns an honest error instead.
    try:
        page.set_default_timeout(15_000)
    except Exception:
        pass
    _lap("get_page (browser launch/reuse)")
    print(f"[WhatsAppWeb] → {receiver}: {message[:50]}")

    # ── REUSE an already-open WhatsApp tab (act like a real user, not a fresh
    # browser every time). A prior attempt may have opened WhatsApp and it's still
    # finishing its slow first load; navigating again throws that away and restarts
    # the ~30s load. Only navigate when we're not already on WhatsApp.
    on_wa = "web.whatsapp.com" in (page.url or "")
    if not on_wa:
        try:
            await page.goto(WA_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"[WhatsAppWeb] navigation warning (non-fatal): {e}")
    else:
        print("[WhatsAppWeb] reusing already-open WhatsApp tab")

    # ── Wait for login + chat list. Be patient: a fresh profile or slow link can
    # take a while to sync ("Loading your chats…"), during which the search box
    # isn't interactive yet. Poll #pane-side for up to ~60s.
    logged_in = False
    for _ in range(120):  # up to ~60s
        if await page.locator("#pane-side").count() > 0:
            logged_in = True
            break
        await page.wait_for_timeout(500)
    if not logged_in:
        return ToolResult.failure(
            "WhatsApp Web isn't logged in yet — I've opened it in the browser.",
            guidance="Ask the user to scan the QR code once with their phone, then "
                     "try again. Do not claim the message was sent.")
    _lap("navigate + login (#pane-side)")

    # ── Brief settle, then the search-box wait_for_selector below gates on actual
    # visibility (so this is just a small buffer, not the old blind 2s).
    await page.wait_for_timeout(600)
    search = None
    for sel, tmo in (
        # Current WhatsApp: the search is a real <input data-tab="3">. It used to
        # be a contenteditable div — those selectors now match NOTHING, which is
        # what produced "search box didn't load in time" on a fully-loaded page.
        ('input[data-tab="3"]',                       20_000),
        ('#side input[type="text"]',                   8_000),
        ('#side input',                                6_000),
        ('[role="textbox"]',                           6_000),
        ('div[contenteditable="true"][data-tab="3"]',  4_000),  # legacy fallbacks
        ('#side div[contenteditable="true"]',          3_000),
    ):
        try:
            el = await page.wait_for_selector(sel, state="visible", timeout=tmo)
            if el:
                search = el
                break
        except Exception:
            continue

    if search is None:
        return ToolResult.failure(
            "Opened WhatsApp but the search box didn't load in time — the chat list "
            "may still be syncing.",
            guidance="Tell the user it's still loading and try again in a few seconds; "
                     "the same tab is reused so it'll be ready. Nothing was sent.")
    _lap("search box visible")

    try:
        await search.click(timeout=10_000)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Delete")
        await page.keyboard.type(receiver, delay=25)  # type into the focused box
    except Exception as e:
        return f"Couldn't use the WhatsApp search box: {e}"
    _lap("typed contact name")

    # Wait for results to REFLECT the query instead of a blind 2s sleep — proceed
    # the instant a plausible match appears (a warm session shows it in ~300ms),
    # capped at ~2s for a cold/slow one. This is the delay you felt.
    rl = receiver.strip().lower()
    for _ in range(10):  # ≤ ~2s, but exits early the moment the contact shows
        titles = await _visible_chat_titles(page)
        if any(rl == t.strip().lower() or rl in t.strip().lower() for t in titles):
            break
        await page.wait_for_timeout(200)

    _lap("search results reflected query")

    # ── Open the chat, but ONLY on a safe (exact / unambiguous) match — never a
    # fuzzy guess, which could message the wrong person.
    opened_name = await _open_best_match(page, receiver)
    _lap(f"open_best_match → {opened_name!r}")

    # ── Confirm a chat ACTUALLY opened by requiring the real message composer to
    # become visible. Do NOT fall back to "any editable box" — that is the search
    # field, and typing there is what silently dropped messages.
    composer = None
    if opened_name is not None:
        for sel in ('div[contenteditable="true"][data-tab="10"]',
                    'footer div[contenteditable="true"][role="textbox"]',
                    'footer div[contenteditable="true"]'):
            try:
                composer = await page.wait_for_selector(sel, state="visible", timeout=6_000)
                if composer:
                    break
            except Exception:
                continue

    if composer is None:
        # No safe match — surface the ACTUAL contacts so the eagle can recognise
        # which one is '{receiver}' (e.g. 'mom' → 'Mama') and remember it. Show
        # search results; if empty, clear search to reveal the recent chat list.
        candidates = await _visible_chat_titles(page)
        if not candidates:
            try:
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Delete")
                await page.wait_for_timeout(900)
                candidates = await _visible_chat_titles(page)
            except Exception as _e:
                print(f"[whatsapp_web.py] Non-fatal error at line 189: {_e}")
        if candidates:
            return ToolResult.failure(
                f"No WhatsApp chat is saved exactly as '{receiver}'. Visible chats: "
                f"{', '.join(candidates[:14])}.",
                guidance=f"If one of those IS '{receiver}' (e.g. 'mom'→'Mama'), re-send "
                         f"to that EXACT name and save_memory(relationships) so it's "
                         f"remembered. If none match, ask the user. Nothing was sent.",
                candidates=candidates[:14])
        return ToolResult.failure(
            f"I searched WhatsApp for '{receiver}' but couldn't open that chat and no "
            f"chats were visible — nothing sent.",
            guidance="Ask the user whether that contact is saved in WhatsApp.")

    _lap("composer found")

    # ── Type into the confirmed composer and send.
    try:
        await composer.click(timeout=6_000)
        await composer.type(message, delay=15)
        await page.wait_for_timeout(150)
        await page.keyboard.press("Enter")
    except Exception as e:
        return ToolResult.failure(
            f"Opened {opened_name}'s chat but couldn't type the message ({e}).",
            guidance="Nothing was sent — report the failure, don't claim success.")

    # ── VERIFY the send happened. On a successful send WhatsApp CLEARS the composer
    # AND re-renders it — which DETACHES the old `composer` handle, so reading THAT
    # threw and produced a false "couldn't confirm". Re-query the composer FRESH each
    # check (a Locator re-resolves to the live node) and confirm it's empty.
    _composer_sel = ('div[contenteditable="true"][data-tab="10"], '
                     'footer div[contenteditable="true"][role="textbox"], '
                     'footer div[contenteditable="true"]')
    sent_ok = False
    for _ in range(10):  # ≤ ~1.5s, exits the moment the composer is empty
        await page.wait_for_timeout(150)
        try:
            txt = (await page.locator(_composer_sel).first.inner_text(timeout=1_000)).strip()
            if txt == "":
                sent_ok = True
                break
        except Exception:
            continue  # transient re-render — keep polling, don't bail
    if not sent_ok:
        # Secondary confirm: the message shows as an outgoing bubble (selector-tolerant).
        try:
            snippet = message[:40].replace('"', '').replace("'", "")
            sent_ok = await page.locator(
                f'[aria-label*="You"]:has-text("{snippet}"), '
                f'div.message-out:has-text("{snippet}"), '
                f'span.selectable-text:has-text("{snippet}")'
            ).count() > 0
        except Exception:
            sent_ok = False
    _lap(f"typed message + sent + verified (ok={sent_ok})")

    if not sent_ok:
        return ToolResult.failure(
            f"I typed the message into {opened_name}'s chat but couldn't confirm it sent.",
            guidance="Ask the user to check WhatsApp; do NOT mark this as sent.")
    # Note the resolved name so the model learns e.g. 'mom' → 'Mama'.
    matched = opened_name.strip().lower() != receiver.strip().lower()
    msg = (f"Message sent to {opened_name} on WhatsApp"
           + (f" (matched your request for '{receiver}')." if matched else "."))
    return ToolResult.success(msg, contact=opened_name, matched_from=receiver if matched else None)


def send_whatsapp_web(receiver: str, message: str, browser: str | None = None) -> ToolResult:
    """Blocking entry point. Reuses browser_control's session registry so it
    shares the same real/automation profile and event loop."""
    from actions.browser_control import _registry
    # browser=None → registry resolves to the configured default_browser, then
    # the OS default — i.e. the browser the user is actually logged into.
    sess = _registry.get(browser)
    try:
        # 75s outer cap: a cold browser+WhatsApp load is ~10-15s and every inner
        # action is capped at 15s, so a genuine hang fails in well under the old 2min.
        return sess.run(_wa_send(sess, receiver, message), timeout=75)
    except Exception as e:
        return ToolResult.failure(
            f"WhatsApp Web send failed: {e}",
            guidance="The browser session errored — nothing was sent. Don't claim success.")
