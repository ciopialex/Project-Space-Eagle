# config/__init__.py
import json, os, platform
from pathlib import Path
from core import user_paths

_CONFIG_PATH = user_paths.api_keys_path()

def _platform_os() -> str:
    """Auto-detect OS when config file is absent."""
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def get_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", _platform_os()).lower()

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"

def get_client(api_version: str | None = None):
    """Process-scoped caching client factory to reuse TCP/TLS sessions."""
    import threading
    from google import genai
    
    # Initialize cache on the function object itself
    if not hasattr(get_client, "_cache"):
        get_client._cache = {}
        get_client._lock = threading.Lock()
        
    key = api_version or "default"
    with get_client._lock:
        if key not in get_client._cache:
            cfg = get_config()
            # Try getting api key from config or environment
            api_key = cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
            opts = {"api_version": api_version} if api_version else None
            get_client._cache[key] = genai.Client(api_key=api_key, http_options=opts)
        return get_client._cache[key]
