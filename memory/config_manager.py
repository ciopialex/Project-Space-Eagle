import json

from core import prefs, user_paths

CONFIG_FILE = user_paths.api_keys_path()


def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to load api_keys.json: {e}")
        return {}


def get_brief_enabled() -> bool:
    return prefs.enabled("morning_brief_enabled")


def save_brief_enabled(enabled: bool) -> None:
    prefs.save({"morning_brief_enabled": bool(enabled)})
