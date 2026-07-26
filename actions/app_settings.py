"""Central read/write for everything the Settings panel exposes.

The Settings panel (the gear in the dashboard title bar) is the one surface where
the user connects/disconnects accounts, toggles start-on-boot, renames the eagle,
switches brains, and re-runs the ignition flow. Rather than let the web bridge
poke config/api_keys.json directly, everything funnels through here so there is a
single, skip-safe writer and a single snapshot() the UI renders from.

Design rules mirrored from onboarding.complete():
  • writes are a MERGE — we never rewrite the whole file from a partial patch, so
    saving one field can't wipe another.
  • secrets are never sent to the UI. snapshot() reports only whether a key is
    set (has_brain_key), plus non-secret identity/preferences.
"""
from __future__ import annotations

import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent
CONFIG = BASE / "config" / "api_keys.json"

# Fields the UI may write through save(). Anything else in a patch is ignored so
# a compromised/confused front-end can't set arbitrary config (e.g. api paths).
_WRITABLE = {
    "assistant_name", "user_name", "address_style",
    "brain_mode", "brain_provider",
    "morning_brief_enabled", "default_browser", "os_system", "camera_index",
    "onboarded",
}


def _read() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(cfg: dict) -> bool:
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[app_settings] write failed: {e}")
        return False


def snapshot() -> dict:
    """Everything the Settings panel needs — no secrets, just presence flags."""
    cfg = _read()

    # Account connections
    try:
        from actions.google_auth import connected_account, _load_client_id
        google = connected_account()  # {email,name} or None
        google_configured = bool(_load_client_id())  # OAuth client id present?
    except Exception:
        google = None
        google_configured = False

    # Start on boot
    try:
        from actions import autostart
        boot_on = autostart.is_enabled()
        boot_supported = autostart._OS in autostart._TABLE
    except Exception:
        boot_on, boot_supported = False, False

    # Installed browsers for the picker (empty list if detection unavailable).
    try:
        from actions.browser_control import list_browsers
        browsers = list_browsers()
    except Exception:
        browsers = []

    # Use the SAME source of truth the runtime gates the brief on, so the toggle
    # never disagrees with reality (get_brief_enabled defaults True when unset —
    # reading cfg.get(...) directly would wrongly show OFF on a fresh config).
    try:
        from memory.config_manager import get_brief_enabled
        brief_on = bool(get_brief_enabled())
    except Exception:
        brief_on = bool(cfg.get("morning_brief_enabled"))

    brain_mode = (cfg.get("brain_mode") or "api").lower()
    provider = (cfg.get("brain_provider") or "google").lower()
    has_key = bool(
        cfg.get("gemini_api_key") if provider == "google" else cfg.get("brain_api_key")
    )

    return {
        "identity": {
            "assistant_name": cfg.get("assistant_name") or "Aethelark",
            "user_name": cfg.get("user_name") or "",
            "address_style": cfg.get("address_style") or "",
        },
        "accounts": {
            "google": {
                "connected": bool(google),
                "configured": google_configured,
                "email": (google or {}).get("email", ""),
                "name": (google or {}).get("name", ""),
            },
            # WhatsApp Web login lives in the browser profile, not a token we own,
            # so we can only offer the linking action, not a reliable status.
            "whatsapp": {"connected": None},
        },
        "brain": {
            "mode": brain_mode,
            "provider": provider,
            "has_key": has_key,
        },
        "startup": {
            "boot_enabled": boot_on,
            "boot_supported": boot_supported,
        },
        "preferences": {
            "morning_brief_enabled": brief_on,
            "default_browser": cfg.get("default_browser") or "",
            "os_system": cfg.get("os_system") or "",
            "camera_index": cfg.get("camera_index", 0),
        },
        "browsers": browsers,
    }


def save(patch: dict) -> dict:
    """Merge whitelisted fields into config; return a fresh snapshot."""
    cfg = _read()
    for k, v in (patch or {}).items():
        if k not in _WRITABLE:
            continue
        # Empty strings are allowed (e.g. clearing default_browser) — but a blank
        # assistant_name should fall back to the default rather than persist "".
        if k == "assistant_name" and not str(v).strip():
            v = "Aethelark"
        cfg[k] = v
    _write(cfg)
    return snapshot()


def set_brain_key(provider: str, key: str) -> dict:
    """Store an API key for a provider. The live voice runtime is Gemini today, so
    a Google key becomes the runtime key; others go to the multi-brain slot."""
    key = (key or "").strip()
    if not key:
        return snapshot()
    cfg = _read()
    provider = (provider or "google").lower()
    cfg["brain_provider"] = provider
    cfg["brain_mode"] = "api"
    if provider == "google":
        cfg["gemini_api_key"] = key
    else:
        cfg["brain_api_key"] = key
    _write(cfg)
    return snapshot()


def set_autostart(on: bool) -> dict:
    try:
        from actions import autostart
        autostart.set_enabled(bool(on))
    except Exception as e:
        print(f"[app_settings] autostart change failed: {e}")
    return snapshot()


def disconnect_google() -> dict:
    try:
        from actions.google_auth import disconnect_google as _dc
        _dc()
    except Exception as e:
        print(f"[app_settings] google disconnect failed: {e}")
    # auth_provider falls back to guest once the account is gone.
    cfg = _read()
    if cfg.get("auth_provider") == "google":
        cfg["auth_provider"] = "guest"
        _write(cfg)
    return snapshot()
