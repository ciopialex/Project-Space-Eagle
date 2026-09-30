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
        pager = client.models.list(config={"page_size": 1})
        next(iter(pager), None)
        return True, ""
    except errors.ClientError as e:
        text = str(e)
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
