"""Unread-messages brief across the user's channels (Gmail, WhatsApp, …).

The eagle-as-comms-briefer: "what did I miss?" → one short spoken summary of
unread mail + chats. Built to be pluggable — each source is a reader returning a
uniform dict, and messages_brief() composes them:

    {"source": str, "ok": bool, "count": int,
     "items": [{"who": str, "preview": str}], "note": str}

Sources today:
  • Gmail    — Gmail API (readonly) when Google is connected in Settings; else a
               note pointing at setup (Aethelark_Google_Setup.md).
  • WhatsApp — unread chats from WhatsApp Web via the shared browser session
               (best-effort; WhatsApp's DOM is obfuscated + localized, so it may
               degrade to a bare count).

Add more sources (Telegram, Slack…) by writing another reader + registering it.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

WA_URL = "https://web.whatsapp.com/"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

# "N unread" across the languages WhatsApp Web localizes into (the user may run a
# non-English UI). The DIGIT is what we actually need; the word just anchors it.
_UNREAD_RE = re.compile(
    r"(\d+)\s*(unread|okunmam|neciti|no le[íi]d|non lus|ungelesen|não lida|mensajes? sin)",
    re.IGNORECASE,
)


# ── Gmail ─────────────────────────────────────────────────────────────────────
# ── Triage heuristics ─────────────────────────────────────────────────────────
# Senders that are machines, not people. "Real people first" is the whole point:
# an inbox of 200 unread is ~all machines, and burying a human under OLX alerts is
# the failure mode we're fixing.
_AUTOMATED_RE = re.compile(
    r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|notification|newsletter|digest|bulletin|"
    r"mailer|bounce|community|team@|members?@|support@|info@|hello@|contact@|news@|"
    r"updates?@|alert|marketing|promo|promotion|billing@|noreply|via .+? \(|"
    r"@.*\.(mailchimp|sendgrid|substack|mailgun|hubspot)\b)",
    re.IGNORECASE,
)
# Full-inbox search terms for "do I have anything to pay / action required" — this
# runs as a Gmail QUERY so it catches bills buried deep in 200 unread, not just the
# recent window. Romanian included (the user's inbox is bilingual).
_ACTION_QUERY = (
    'is:unread in:inbox ('
    'invoice OR payment OR "amount due" OR "past due" OR overdue OR unpaid OR bill '
    'OR "action required" OR "response required" OR "confirm your" OR "verify your" '
    'OR expiring OR "expires" OR deadline OR renew OR suspended OR "final notice" '
    'OR factura OR "de plata" OR scadent OR restant OR neachitat OR somatie'
    ')'
)
# Things that need the user to DO something. Romanian included — the user is
# Romanian and half these emails will be too.
_ACTION_RE = re.compile(
    r"\b(invoice|payment|pay (now|your)|amount due|past due|overdue|unpaid|bill|"
    r"receipt required|action required|response required|confirm|verify|validate|"
    r"expires?|expiring|deadline|renew|suspend(ed)?|final notice|reminder to|"
    r"factur[ăa]|plat[ăa]|de plat[ăa]|scadent|restant|urgent|confirm[ăa]|"
    r"expir[ăa]|termen|somat|neachitat)\b",
    re.IGNORECASE,
)


def _classify(who: str, addr: str, subject: str, labels: list[str]) -> tuple[str, bool]:
    """→ (bucket, action_required). Buckets: 'action' | 'person' | 'noise'."""
    lab = set(labels or [])
    promo = bool(lab & {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"})
    automated = bool(_AUTOMATED_RE.search(addr or "")) or bool(_AUTOMATED_RE.search(who or ""))
    action = bool(_ACTION_RE.search(subject or ""))

    if action and not promo:
        return "action", True
    if promo or automated:
        return "noise", False
    return "person", False


def _gmail_unread(max_items: int = 6, scan: int = 40) -> dict:
    """Unread Gmail, TRIAGED. Scans a window of unread mail and splits it into
    action-required / real people / noise, so the brief leads with what matters
    instead of reciting marketing."""
    out = {"source": "Gmail", "ok": False, "count": 0, "items": [], "note": "",
           "action": [], "people": [], "noise_count": 0}
    try:
        from actions.google_auth import get_access_token, is_connected
    except Exception as e:
        out["note"] = f"Gmail unavailable ({e})."
        return out

    if not is_connected():
        out["note"] = ("Gmail isn't connected yet — connect Google in Settings "
                       "(see Aethelark_Google_Setup.md).")
        return out

    token = get_access_token()
    if not token:
        out["note"] = "Google is connected but I couldn't get a valid token — try reconnecting."
        return out

    hdr = {"Authorization": f"Bearer {token}"}
    try:
        q = urllib.parse.urlencode({"q": "is:unread in:inbox", "maxResults": scan})
        req = urllib.request.Request(f"{GMAIL_API}/messages?{q}", headers=hdr)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        out["note"] = f"Couldn't reach Gmail ({e})."
        return out

    msgs = data.get("messages", []) or []
    out["ok"] = True
    out["count"] = int(data.get("resultSizeEstimate", len(msgs)) or 0)

    for m in msgs:
        try:
            mq = urllib.parse.urlencode(
                [("format", "metadata"),
                 ("metadataHeaders", "From"), ("metadataHeaders", "Subject")])
            mreq = urllib.request.Request(f"{GMAIL_API}/messages/{m['id']}?{mq}", headers=hdr)
            with urllib.request.urlopen(mreq, timeout=15) as r:
                meta = json.loads(r.read().decode())
            headers = {h["name"].lower(): h["value"]
                       for h in meta.get("payload", {}).get("headers", [])}
            raw_from = headers.get("from", "")
            addr = (re.search(r"<([^>]+)>", raw_from) or [None, raw_from])[1] \
                if "<" in raw_from else raw_from
            who = re.sub(r"\s*<[^>]+>", "", raw_from).strip().strip('"') or addr
            subject = headers.get("subject", "") or (meta.get("snippet", "") or "")[:90]

            bucket, action = _classify(who, addr, subject, meta.get("labelIds", []))
            entry = {"who": who or "Unknown sender", "preview": subject,
                     "id": m["id"], "action": action}
            if bucket == "action":
                out["action"].append(entry)
            elif bucket == "person":
                out["people"].append(entry)
            else:
                out["noise_count"] += 1
        except Exception:
            continue

    # ── Full-inbox action sweep: catch bills / "action required" buried BELOW the
    # scan window (a payment at position #150 in 200 unread would otherwise be
    # missed). Merge any new hits into the action bucket, deduped by id.
    seen = {e["id"] for e in out["action"]}
    try:
        aq = urllib.parse.urlencode({"q": _ACTION_QUERY, "maxResults": 10})
        areq = urllib.request.Request(f"{GMAIL_API}/messages?{aq}", headers=hdr)
        with urllib.request.urlopen(areq, timeout=15) as r:
            ahits = (json.loads(r.read().decode()).get("messages") or [])
        for m in ahits:
            if m["id"] in seen:
                continue
            mq = urllib.parse.urlencode(
                [("format", "metadata"),
                 ("metadataHeaders", "From"), ("metadataHeaders", "Subject")])
            mreq = urllib.request.Request(f"{GMAIL_API}/messages/{m['id']}?{mq}", headers=hdr)
            with urllib.request.urlopen(mreq, timeout=15) as r:
                meta = json.loads(r.read().decode())
            hh = {h["name"].lower(): h["value"]
                  for h in meta.get("payload", {}).get("headers", [])}
            # Skip promotions even if they contain "expires" etc.
            if set(meta.get("labelIds", [])) & {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"}:
                continue
            who = re.sub(r"\s*<[^>]+>", "", hh.get("from", "")).strip().strip('"') or "Unknown"
            out["action"].append({"who": who, "preview": hh.get("subject", ""),
                                  "id": m["id"], "action": True})
            seen.add(m["id"])
    except Exception:
        pass

    # What the brief shows: action first, then real people. Noise stays a number.
    out["items"] = (out["action"] + out["people"])[:max_items]
    return out


def gmail_mark_read(query: str = "", ids: list[str] | None = None) -> str:
    """Mark mail as read (removes UNREAD). Give either explicit `ids` or a Gmail
    `query` such as 'category:promotions is:unread'. Requires the gmail.modify
    scope — reconnect Google once if this reports insufficient permission."""
    try:
        from actions.google_auth import get_access_token, is_connected
    except Exception as e:
        return f"Gmail unavailable ({e})."
    if not is_connected():
        return "Gmail isn't connected — connect Google in Settings first."
    token = get_access_token()
    if not token:
        return "Couldn't get a valid Google token — try reconnecting."
    hdr = {"Authorization": f"Bearer {token}",
           "Content-Type": "application/json"}

    target = list(ids or [])
    if not target:
        if not query:
            return "Tell me which emails to mark as read (a sender, or 'promotions')."
        try:
            q = urllib.parse.urlencode({"q": query, "maxResults": 200})
            req = urllib.request.Request(f"{GMAIL_API}/messages?{q}", headers=hdr)
            with urllib.request.urlopen(req, timeout=20) as r:
                target = [m["id"] for m in (json.loads(r.read().decode()).get("messages") or [])]
        except Exception as e:
            return f"Couldn't search Gmail ({e})."
    if not target:
        return "Nothing matched — no emails were changed."

    try:
        body = json.dumps({"ids": target[:1000],
                           "removeLabelIds": ["UNREAD"]}).encode()
        req = urllib.request.Request(f"{GMAIL_API}/messages/batchModify",
                                     data=body, headers=hdr, method="POST")
        with urllib.request.urlopen(req, timeout=25):
            pass
    except Exception as e:
        if "403" in str(e) or "insufficient" in str(e).lower():
            return ("I don't have permission to modify Gmail yet — reconnect Google "
                    "in Settings once to grant it, then ask me again.")
        return f"Couldn't mark them as read ({e})."
    return f"Marked {len(target)} email{'s' if len(target) != 1 else ''} as read."


# ── WhatsApp ──────────────────────────────────────────────────────────────────
async def _wa_read_unread(sess, max_items: int = 8) -> dict:
    out = {"source": "WhatsApp", "ok": False, "count": 0, "items": [], "note": ""}
    page = await sess._get_page()

    if "web.whatsapp.com" not in (page.url or ""):
        try:
            await page.goto(WA_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"[MsgBrief] WA nav warning: {e}")

    logged_in = False
    for _ in range(60):  # up to ~30s
        if await page.locator("#pane-side").count() > 0:
            logged_in = True
            break
        await page.wait_for_timeout(500)
    if not logged_in:
        out["note"] = "WhatsApp Web isn't logged in — open it once and scan the QR."
        return out

    await page.wait_for_timeout(2_500)
    out["ok"] = True

    # The tab title carries the authoritative unread total, e.g. "(3) WhatsApp".
    title_total = None
    try:
        m = re.match(r"\((\d+)\)", (await page.title() or "").strip())
        if m:
            title_total = int(m.group(1))
    except Exception as _e:
        print(f"[messages_brief.py] Non-fatal error at line 122: {_e}")

    # Rows are role="row" (older builds used role="listitem" → matched NOTHING,
    # which is why this silently reported 0 unread). Accept both.
    rows = page.locator('#pane-side [role="row"], #pane-side [role="listitem"]')
    try:
        n = await rows.count()
    except Exception:
        n = 0

    total = 0
    for i in range(min(n, 40)):
        row = rows.nth(i)
        # Unread badge: any aria-label mentioning "unread" (WhatsApp localizes the
        # word, so also accept the multilingual regex). Count may be absent → 1.
        c = 0
        try:
            badges = row.locator('[aria-label*="unread" i]')
            if await badges.count() > 0:
                al = (await badges.first.get_attribute("aria-label")) or ""
                d = re.search(r"(\d+)", al)
                c = int(d.group(1)) if d else 1
            else:
                labels = row.locator("[aria-label]")
                lc = min(await labels.count(), 8)
                for j in range(lc):
                    al = (await labels.nth(j).get_attribute("aria-label")) or ""
                    mm = _UNREAD_RE.search(al)
                    if mm:
                        c = int(mm.group(1))
                        break
        except Exception:
            c = 0
        if c <= 0:
            continue

        who = "Someone"
        try:
            who = (await row.locator("span[title]").first.get_attribute("title")) or who
        except Exception as _e:
            print(f"[messages_brief.py] Non-fatal error at line 162: {_e}")
        total += c
        out["items"].append({"who": who, "preview": f"{c} new"})
        if len(out["items"]) >= max_items:
            break

    out["count"] = total or title_total or len(out["items"])
    if out["count"] == 0:
        out["note"] = "No unread WhatsApp chats detected."
    return out


def _whatsapp_unread(max_items: int = 8) -> dict:
    """Blocking wrapper — reuses browser_control's session registry."""
    try:
        from actions.browser_control import _registry
        sess = _registry.get(None)
        return sess.run(_wa_read_unread(sess, max_items), timeout=90)
    except Exception as e:
        return {"source": "WhatsApp", "ok": False, "count": 0, "items": [],
                "note": f"Couldn't read WhatsApp ({e})."}


# ── Compose ───────────────────────────────────────────────────────────────────
_SOURCES = {
    "gmail": _gmail_unread,
    "whatsapp": _whatsapp_unread,
}


def _compose(results: list[dict]) -> str:
    """Lead with what needs the user (action-required, then real people); the rest
    (marketing/automated) collapses to a single number. This is the whole point of
    triage — a 200-unread inbox should brief as "2 need you", not a wall of OLX."""
    total = sum(r["count"] for r in results if r.get("ok"))
    lines: list[str] = []
    for r in results:
        src = r["source"]
        if not r.get("ok"):
            if r.get("note"):
                lines.append(f"{src}: {r['note']}")
            continue
        if r["count"] == 0:
            lines.append(f"{src}: nothing unread.")
            continue

        # Gmail carries triage buckets; WhatsApp just has items.
        action = r.get("action") or []
        people = r.get("people") or []
        noise = r.get("noise_count", 0)

        if action or people or noise:
            parts = [f"{src}: {r['count']} unread"]
            if action:
                names = ", ".join(dict.fromkeys(a["who"] for a in action))[:120]
                parts.append(f"⚠ {len(action)} need action ({names})")
            if people:
                pnames = ", ".join(dict.fromkeys(p["who"] for p in people[:4]))
                extra = len(people) - 4
                parts.append(f"{len(people)} from real people"
                             + (f" — {pnames}" + (f" +{extra}" if extra > 0 else "") if pnames else ""))
            if noise and not action and not people:
                parts.append("all marketing/automated")
            elif noise:
                parts.append(f"{noise} marketing/automated (skipped)")
            lines.append(". ".join(parts) + ".")
        else:
            who = [it["who"] for it in r.get("items", []) if it.get("who")]
            head = f"{src}: {r['count']} unread"
            if who:
                shown = ", ".join(who[:4])
                more = r["count"] - len(who[:4])
                head += f" — from {shown}" + (f" and {more} more" if more > 0 else "")
            lines.append(head + ".")

    if not lines:
        return "I couldn't reach any of your message sources right now."
    header = (f"You have {total} unread in total. " if total else "")
    return header + " ".join(lines)


def messages_brief(parameters: dict | None = None, response=None,
                   player=None, session_memory=None) -> str:
    """Brief the user on unread messages across channels.

    parameters:
      source : optional — 'gmail' | 'whatsapp' | 'all' (default 'all').
    """
    params = parameters or {}
    want = (params.get("source") or "all").lower().strip()
    order = ["gmail", "whatsapp"] if want in ("all", "") else [want]

    if player:
        player.write_log(f"[brief] reading unread: {', '.join(order)}")

    results: list[dict] = []
    for name in order:
        fn = _SOURCES.get(name)
        if not fn:
            continue
        try:
            results.append(fn())
        except Exception as e:
            results.append({"source": name.title(), "ok": False, "count": 0,
                            "items": [], "note": f"failed ({e})"})

    brief = _compose(results)
    print(f"[MsgBrief] {brief}")
    return brief
