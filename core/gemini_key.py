"""Is this Gemini key one Google will accept? Asked before it is saved."""
from __future__ import annotations


def check(key: str, timeout_s: float = 12.0) -> tuple[bool, str]:
    """(True, "") when Google accepts the key, else (False, what to tell the user)."""
    key = (key or "").strip()
    if not key:
        return False, "Paste your key first."
    try:
        from google import genai
        from google.genai import errors, types
    except Exception:
        return False, "The Gemini library is missing. Reinstall Aethelark."

    try:
        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        from core.models import VOICE
        client.models.get(model=VOICE)
        return True, ""
    except errors.ClientError as e:
        text = str(e)
        if getattr(e, "code", None) == 404:
            return False, ("This key works, but it can't use Gemini's voice "
                           "model. Create a key in Google AI Studio.")
        if "API_KEY_INVALID" in text or "API key not valid" in text:
            return False, ("Google says this key isn't valid. Check that you "
                           "copied all of it.")
        if "expired" in text.lower():
            return False, "That key has expired. Create a new one."
        if getattr(e, "code", None) == 403 or "PERMISSION_DENIED" in text:
            return False, ("This key isn't allowed to use Gemini. Create a "
                           "new one in Google AI Studio.")
        if getattr(e, "code", None) == 429:
            # Rate-limited means the key itself was accepted.
            return True, ""
        return False, "Google turned the key down."
    except Exception:
        return False, ("Couldn't reach Google to check the key. Check your "
                       "internet connection and try again.")


SHAPE = r"AIza[0-9A-Za-z_\-]{35}"
_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def shaped(text: str) -> bool:
    import re
    return bool(re.fullmatch(SHAPE, (text or "").strip()))


def on_this_computer(environ=None, home=None) -> list[str]:
    """Gemini keys already set up on this computer, for the Gemini CLI and
    Google's own libraries: the environment, then ~/.gemini/.env and ~/.env."""
    import os
    from pathlib import Path
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    found = [environ.get(n, "").strip() for n in _NAMES]
    for f in (home / ".gemini" / ".env", home / ".env"):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            k, _, v = line.removeprefix("export ").partition("=")
            if k.strip() in _NAMES:
                found.append(v.strip().strip("'\""))
    out: list[str] = []
    for k in found:
        if shaped(k) and k not in out:
            out.append(k)
    return out
