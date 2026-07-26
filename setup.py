import subprocess
import sys
import platform
from pathlib import Path

def _get_python_exe() -> str:
    base_dir = Path(__file__).resolve().parent
    venv_py = base_dir / ".venv" / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python3")
    if venv_py.exists():
        return str(venv_py)
    return sys.executable

py_exe = _get_python_exe()
print(f"Installing requirements using {py_exe}...")
try:
    subprocess.run([py_exe, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
except subprocess.CalledProcessError:
    # Fallback for system python with PEP 668
    subprocess.run([py_exe, "-m", "pip", "install", "-r", "requirements.txt", "--break-system-packages"], check=True)

print("Installing Playwright browsers...")
subprocess.run([py_exe, "-m", "playwright", "install"], check=True)


if platform.system() == "Windows":
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        postinstall = Path(sys.executable).parent / "Scripts" / "pywin32_postinstall.py"
        print(
            "\n⚠️  pywin32 did not install correctly — desktop shortcut creation "
            "will fall back to a slower method that may not work on this machine.\n"
            "    Try fixing it manually with:\n"
            f'    "{sys.executable}" -m pip install --force-reinstall pywin32\n'
            f'    "{sys.executable}" "{postinstall}" -install\n'
        )

print("\n✅ Setup complete! Run 'python main.py' to start MARK XXV.")

