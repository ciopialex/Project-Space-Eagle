"""A module borrows a sign-in the user made in the eagle's own browser.

The eagle has one place to sign in to a website: its own browser, opened from
Settings. A module is a separate program with its own HTTP client, and it
never reads that browser -- it could not tell one site's session from
another's, and it has no business holding any of them. So the seam is a
hand-over: a module declares the one account it needs (`[[accounts]]` in its
manifest: the site, the cookie that proves a login, and the command that
receives a session), and the host passes it that site's cookies, and only
those, on the command's stdin -- never in argv, where any process can read
them.

The hand-over happens at the three moments it can matter:

* right after the user signs in to that site in the eagle's browser;
* right after the module is installed, if the browser is already signed in;
* when the module answers a call with `needs_account` -- a session that
  expired, or was never handed over -- after which the host hands over again
  and, if that worked, retries the call once.

Measured 2026-09-25: a module's model downloads were refused ("Please log in
to download models") on every install but the developer's, whose only session
was a cookie copied by hand in August; signing in through Settings changed
nothing, because the module could not see it.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable, Iterable

from .manifest import ModuleAccount, ModuleManifest

#: Cookie fields passed on. The value is what the module needs; the rest lets
#: it rebuild the cookie faithfully for its own client.
_FIELDS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure",
           "sameSite")

_HANDOVER_TIMEOUT_S = 30.0

_subscribers: list[Callable[[str], None]] = []


def normalise(site: str) -> str:
    """'https://www.MakerWorld.com/en' -> 'makerworld.com'."""
    d = (site or "").strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.split("/")[0].split("?")[0]
    return (d[4:] if d.startswith("www.") else d).strip(".")


def matches(account_site: str, site: str) -> bool:
    """A sign-in at `site` covers an account at `account_site`."""
    a, s = normalise(account_site), normalise(site)
    return bool(a) and (s == a or s.endswith("." + a))


def browser_cookies(site: str, *, start: bool = False,
                    timeout: float = 20.0) -> list[dict] | None:
    """The cookies the eagle's browser would send to `site`, or None if the
    browser is not running and `start` is False (or it cannot start)."""
    from actions.grounding.web.browser import default_browser
    browser = default_browser()
    if not browser.running:
        if not start:
            return None
        browser.start()
        if not browser.running:
            return None
    urls = [f"https://{site}/", f"https://www.{site}/"]
    found = browser.call(lambda page: page.context.cookies(urls), timeout=timeout)
    return [{k: c[k] for k in _FIELDS if k in c} for c in (found or [])]


def hand_over(manifest: ModuleManifest, account: ModuleAccount,
              cookies: list[dict], which: Callable[[str], str | None]) -> dict:
    """Give the module this site's session. Never raises."""
    if not any(c.get("name") == account.proof for c in cookies or ()):
        return {"ok": False, "signed_in": False,
                "detail": f"the eagle's browser is not signed in to {account.site}"}
    binary = which(manifest.binary)
    if not binary:
        return {"ok": False, "signed_in": True,
                "detail": f"{manifest.key} is not installed"}
    payload = json.dumps({"site": account.site, "cookies": cookies})
    try:
        proc = subprocess.run([binary, *account.receive], input=payload,
                              capture_output=True, text=True,
                              timeout=_HANDOVER_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "signed_in": True,
                "detail": f"{manifest.key} did not take the session: {e}"}
    try:
        answer = json.loads(proc.stdout or "{}")
    except ValueError:
        answer = {}
    answer = answer if isinstance(answer, dict) else {}
    ok = proc.returncode == 0 and answer.get("success", True) is not False
    detail = str(answer.get("error") or "").strip() or (
        "handed over" if ok else (proc.stderr or proc.stdout or "").strip()[:200])
    return {"ok": ok, "signed_in": True, "detail": detail}


def account_for(manifest: ModuleManifest | None, site: str) -> ModuleAccount | None:
    if manifest is None:
        return None
    return next((a for a in manifest.accounts if matches(a.site, site)), None)


def repair(manifest: ModuleManifest | None, site: str,
           which: Callable[[str], str | None],
           cookies_fn: Callable[..., list[dict] | None] = browser_cookies) -> dict:
    """The module said it needs `site`: hand the browser's session over again.

    Starts the eagle's browser if it is not running -- it is headless, and the
    alternative is telling the user to sign in to a site they are signed in to.
    """
    account = account_for(manifest, site)
    if account is None:
        return {"ok": False, "signed_in": False,
                "detail": f"the module never declared an account for {site}"}
    try:
        cookies = cookies_fn(account.site, start=True)
    except Exception as e:
        return {"ok": False, "signed_in": False,
                "detail": f"the eagle's browser could not be asked: {e}"}
    if cookies is None:
        return {"ok": False, "signed_in": False,
                "detail": "the eagle's browser is not available"}
    return hand_over(manifest, account, cookies, which)


def after_sign_in(url: str, manifests: Iterable[ModuleManifest],
                  which: Callable[[str], str | None],
                  cookies_fn: Callable[..., list[dict] | None] = browser_cookies,
                  log: Callable[[str], None] = print) -> list[tuple[str, str, dict]]:
    """The user just finished signing in at `url`: give it to every module
    that declared that account."""
    done = []
    for manifest in manifests:
        for account in manifest.accounts:
            if not matches(account.site, url):
                continue
            try:
                cookies = cookies_fn(account.site, start=False)
            except Exception as e:
                result = {"ok": False, "signed_in": False,
                          "detail": f"the eagle's browser could not be asked: {e}"}
            else:
                result = (hand_over(manifest, account, cookies, which)
                          if cookies is not None else
                          {"ok": False, "signed_in": False,
                           "detail": "the eagle's browser is not running"})
            log(f"[accounts] {manifest.key} <- {account.site}: {result['detail']}")
            done.append((manifest.key, account.site, result))
    return done


def sync(manifests: Iterable[ModuleManifest], signed_sites: Iterable[str],
         which: Callable[[str], str | None],
         cookies_fn: Callable[..., list[dict] | None] = browser_cookies,
         log: Callable[[str], None] = print) -> list[tuple[str, str, dict]]:
    """Hand over every declared account the browser is already signed in to.

    Run after a module is installed, so a user who signed in before clicking
    Get never sees the module fail for want of a session it could have had.
    """
    signed = list(signed_sites)
    done = []
    for manifest in manifests:
        for account in manifest.accounts:
            if not any(matches(account.site, s) for s in signed):
                continue
            result = repair(manifest, account.site, which, cookies_fn)
            log(f"[accounts] {manifest.key} <- {account.site}: {result['detail']}")
            done.append((manifest.key, account.site, result))
    return done


def status(manifests: Iterable[ModuleManifest],
           signed_sites: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    """What Settings shows under each installed module: its accounts, and
    whether the eagle's browser has signed in to each."""
    signed = list(signed_sites)
    return {m.key: [{"site": a.site, "why": a.why,
                     "signed_in": any(matches(a.site, s) for s in signed)}
                    for a in m.accounts]
            for m in manifests if m.accounts}


def subscribe(fn: Callable[[str], None]) -> None:
    """Call `fn(url)` whenever a sign-in in the eagle's browser completes."""
    if fn not in _subscribers:
        _subscribers.append(fn)


def signed_in(url: str) -> None:
    """A sign-in completed. Called by the browser's sign-in flow; never raises,
    because bookkeeping must not fail a sign-in that already succeeded."""
    for fn in list(_subscribers):
        try:
            fn(url)
        except Exception as e:
            print(f"[accounts] sign-in listener failed: {e}")
