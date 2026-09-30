"""Space-Eagle / Aethelark 3D Environment Bootstrap & Dependency Installer."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

# Refuse execution if invoked erroneously by pip as a build backend
if "egg_info" in sys.argv or os.environ.get("PIP_BUILD_TRACKER"):
    sys.stderr.write(
        "Error: setup.py is an installer script, not a setuptools build backend.\n"
        "To install Space-Eagle dependencies, run: python3 setup.py\n"
    )
    sys.exit(1)


def _resolve_python_executable() -> str:
    """Finds active virtualenv interpreter or falls back to current runtime."""
    root_dir = Path(__file__).resolve().parent
    is_win = platform.system() == "Windows"
    venv_bin = root_dir / ".venv" / ("Scripts/python.exe" if is_win else "bin/python3")
    if venv_bin.exists():
        return str(venv_bin)
    return sys.executable


def run_bootstrap() -> None:
    py_exe = _resolve_python_executable()
    print(f"[*] Bootstrapping Space-Eagle environment with: {py_exe}")

    req_file = Path(__file__).resolve().parent / "requirements.txt"
    if not req_file.exists():
        print(f"[!] requirements.txt not found at {req_file}")
        sys.exit(1)

    # Step 1: Install Python dependencies
    print("[*] Installing requirements...")
    res = subprocess.run([py_exe, "-m", "pip", "install", "-r", str(req_file)])
    if res.returncode != 0:
        print("[*] Retrying with --break-system-packages (PEP 668 managed environment fallback)...")
        res2 = subprocess.run([py_exe, "-m", "pip", "install", "-r", str(req_file), "--break-system-packages"])
        if res2.returncode != 0:
            print("[!] Failed to install Python requirements.")
            sys.exit(res2.returncode)

    # Step 2: Install Playwright browser binaries
    print("[*] Installing Playwright browser engines...")
    pw_res = subprocess.run([py_exe, "-m", "playwright", "install"])
    if pw_res.returncode != 0:
        print("[!] Warning: Playwright browser installation failed. Browser automation may be offline.")

    # Step 3: Platform specific post-install checks
    if platform.system() == "Windows":
        try:
            import win32com.client  # noqa: F401
        except ImportError:
            postinstall = Path(sys.executable).parent / "Scripts" / "pywin32_postinstall.py"
            print(
                "\n[!] Notice: pywin32 requires post-installation registration on Windows.\n"
                f'    Run: "{sys.executable}" "{postinstall}" -install\n'
            )

    print("\n[✓] Environment setup successfully completed! "
          "Run 'python3 aethelark_web.py' to launch.")


if __name__ == "__main__":
    run_bootstrap()
