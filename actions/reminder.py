"""Reminders: said once, shown on this computer when they are due.

A reminder has to fire whether or not the eagle is running, so the operating
system's own scheduler owns it: a systemd user timer on Linux, a launchd agent
on macOS, a scheduled task on Windows. What it runs is a small script written
next to a record of what was registered, both under ~/.aethelark/reminders.
The script shows the notification, then unregisters itself and deletes both.

Two things this used to get wrong, and both made every reminder fail:

  - The script was built from f-strings that themselves contained
    `print(f"... {_e}")`. The OUTER f-string evaluated `{_e}` while writing the
    file, so writing it raised NameError on every OS, every time. The template
    below is a plain string with two JSON literals substituted in; no braces in
    it are ever evaluated here.
  - The Linux timer was a transient `systemd-run` unit, which does not survive
    a reboot. These are unit files with Persistent=true: a reminder due while
    the machine was off fires at the next login instead of never.
"""
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from core.run_cmd import run_cmd
from core.tool_result import ToolResult

_CNW: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if platform.system() == "Windows" else {}
)


def _get_os() -> str:
    _sys = platform.system()
    if _sys == "Darwin":
        return "mac"
    if _sys == "Linux":
        return "linux"
    return "windows"


def _scripts_dir() -> Path:
    d = Path.home() / ".aethelark" / "reminders"
    # Migrate the old ~/.jarvis/reminders in place so scheduled reminders survive.
    legacy = Path.home() / ".jarvis" / "reminders"
    try:
        if legacy.exists() and not d.exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            legacy.rename(d)
    except OSError as e:
        print(f"[Reminder] could not migrate {legacy}: {e}")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _sanitise(text: str, max_len: int = 200) -> str:
    return " ".join(str(text or "").split())[:max_len]


#: The notifier. A plain string: __MESSAGE__ and __RECORD__ are replaced with
#: JSON literals, and nothing else in it is interpreted while it is written.
_NOTIFY_TEMPLATE = r'''# Written by Aethelark. Shows one reminder, then removes itself.
import json, pathlib, platform, subprocess, sys

MESSAGE = __MESSAGE__
RECORD = pathlib.Path(__RECORD__)
TITLE = "Aethelark reminder"


def _run(argv):
    try:
        return subprocess.run(argv, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def notify():
    system = platform.system()
    if system == "Linux":
        return (_run(["notify-send", "--app-name=Aethelark", "--urgency=critical",
                      TITLE, MESSAGE])
                or _run(["gdbus", "call", "--session",
                         "--dest", "org.freedesktop.Notifications",
                         "--object-path", "/org/freedesktop/Notifications",
                         "--method", "org.freedesktop.Notifications.Notify",
                         "Aethelark", "0", "", TITLE, MESSAGE, "[]", "{}", "0"]))
    if system == "Darwin":
        script = "display notification %s with title %s sound name \"Glass\"" % (
            json.dumps(MESSAGE), json.dumps(TITLE))
        return _run(["osascript", "-e", script])
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(TITLE, MESSAGE, duration=15, threaded=False)
        return True
    except Exception:
        return _run(["msg", "*", "/TIME:120", MESSAGE])


def unregister():
    try:
        record = json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = {}
    kind = record.get("scheduler")
    if kind == "systemd":
        timer = record.get("timer", "")
        _run(["systemctl", "--user", "disable", "--now", timer])
        for unit in record.get("units", []):
            try:
                pathlib.Path(unit).unlink()
            except OSError:
                pass
        _run(["systemctl", "--user", "daemon-reload"])
    elif kind == "launchd":
        plist = record.get("plist", "")
        _run(["launchctl", "unload", plist])
        try:
            pathlib.Path(plist).unlink()
        except OSError:
            pass
    elif kind == "schtasks":
        _run(["schtasks", "/Delete", "/TN", record.get("task", ""), "/F"])
    for path in (RECORD, pathlib.Path(__file__)):
        try:
            path.unlink()
        except OSError:
            pass


if not notify():
    print("Aethelark reminder: no way to show a notification: " + MESSAGE,
          file=sys.stderr)
unregister()
'''


def _write_notify_script(task_name: str, message: str, os_name: str) -> Path:
    """The notifier for one reminder. `os_name` is kept for callers; the
    script picks its notifier at run time, on the machine that runs it."""
    base = _scripts_dir()
    script_path = base / f"{task_name}.py"
    body = (_NOTIFY_TEMPLATE
            .replace("__MESSAGE__", json.dumps(message))
            .replace("__RECORD__", json.dumps(str(base / f"{task_name}.json"))))
    script_path.write_text(body, encoding="utf-8")
    script_path.chmod(0o600)   # owner read/write only
    return script_path


def _write_record(task_name: str, record: dict) -> Path:
    path = _scripts_dir() / f"{task_name}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return path


def _records() -> list[dict]:
    """Every reminder still registered, soonest first."""
    out = []
    for path in _scripts_dir().glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            rec["_path"] = str(path)
            out.append(rec)
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda r: r.get("when", ""))


def _schedule_windows(target_dt: datetime, task_name: str,
                      script_path: Path) -> dict | None:
    python_exe = Path(sys.executable)
    pythonw = python_exe.parent / "pythonw.exe"
    if pythonw.exists():
        python_exe = pythonw

    xml_path = _scripts_dir() / f"{task_name}.xml"
    xml_content = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <RegistrationInfo><Description>Aethelark Reminder</Description></RegistrationInfo>\n'
        '  <Triggers><TimeTrigger>\n'
        f'    <StartBoundary>{target_dt.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
        '    <Enabled>true</Enabled>\n'
        '  </TimeTrigger></Triggers>\n'
        '  <Actions><Exec>\n'
        f'    <Command>{python_exe}</Command>\n'
        f'    <Arguments>"{script_path}"</Arguments>\n'
        '  </Exec></Actions>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <StartWhenAvailable>true</StartWhenAvailable>\n'
        '    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n'
        '    <Enabled>true</Enabled>\n'
        '  </Settings>\n'
        '  <Principals><Principal>\n'
        '    <LogonType>InteractiveToken</LogonType>\n'
        '    <RunLevel>LeastPrivilege</RunLevel>\n'
        '  </Principal></Principals>\n'
        '</Task>'
    )
    xml_path.write_text(xml_content, encoding="utf-16")
    result = run_cmd(
        ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
        capture_output=True, text=True, **_CNW,
    )
    xml_path.unlink(missing_ok=True)
    if result.returncode != 0:
        print(f"[Reminder] schtasks: {(result.stderr or result.stdout).strip()}")
        return None
    return {"scheduler": "schtasks"}


def _schedule_mac(target_dt: datetime, task_name: str,
                  script_path: Path) -> dict | None:
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    label = f"com.aethelark.reminder.{task_name}"
    plist_path = agents_dir / f"{label}.plist"
    # No Year key: launchd does not support one and ignores it, which made the
    # agent a yearly one. The script unloads and deletes it after it fires.
    plist_path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{script_path}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Month</key>  <integer>{target_dt.month}</integer>
    <key>Day</key>    <integer>{target_dt.day}</integer>
    <key>Hour</key>   <integer>{target_dt.hour}</integer>
    <key>Minute</key> <integer>{target_dt.minute}</integer>
  </dict>
  <key>RunAtLoad</key>         <false/>
  <key>StandardOutPath</key>   <string>/dev/null</string>
  <key>StandardErrorPath</key> <string>/dev/null</string>
</dict>
</plist>
""", encoding="utf-8")
    plist_path.chmod(0o644)
    result = run_cmd(["launchctl", "load", str(plist_path)],
                     capture_output=True, text=True)
    if result.returncode != 0:
        plist_path.unlink(missing_ok=True)
        print(f"[Reminder] launchctl: {result.stderr.strip()}")
        return None
    return {"scheduler": "launchd", "plist": str(plist_path)}


def _schedule_linux(target_dt: datetime, task_name: str,
                    script_path: Path) -> dict | None:
    """A systemd user timer, persistent across reboots; `at` if there is none."""
    if shutil.which("systemctl"):
        units = _unit_dir()
        units.mkdir(parents=True, exist_ok=True)
        service = units / f"{task_name}.service"
        timer = units / f"{task_name}.timer"
        service.write_text(
            "[Unit]\nDescription=Aethelark reminder\n\n"
            "[Service]\nType=oneshot\n"
            f'ExecStart="{sys.executable}" "{script_path}"\n',
            encoding="utf-8")
        timer.write_text(
            "[Unit]\nDescription=Aethelark reminder\n\n"
            "[Timer]\n"
            f"OnCalendar={target_dt.strftime('%Y-%m-%d %H:%M:00')}\n"
            "Persistent=true\nAccuracySec=1s\n"
            f"Unit={service.name}\n\n"
            "[Install]\nWantedBy=timers.target\n",
            encoding="utf-8")
        reload_ = run_cmd(["systemctl", "--user", "daemon-reload"],
                          capture_output=True, text=True)
        enable = run_cmd(["systemctl", "--user", "enable", "--now", timer.name],
                         capture_output=True, text=True)
        if reload_.returncode == 0 and enable.returncode == 0:
            return {"scheduler": "systemd", "timer": timer.name,
                    "units": [str(service), str(timer)]}
        print(f"[Reminder] systemd refused the timer: "
              f"{(enable.stderr or reload_.stderr).strip()}; trying 'at'")
        service.unlink(missing_ok=True)
        timer.unlink(missing_ok=True)

    if shutil.which("at"):
        result = run_cmd(["at", target_dt.strftime("%H:%M %Y-%m-%d")],
                         input=f'"{sys.executable}" "{script_path}"\n',
                         capture_output=True, text=True)
        if result.returncode == 0:
            job = ""
            for word, nxt in zip(result.stderr.split(), result.stderr.split()[1:]):
                if word == "job":
                    job = nxt
                    break
            return {"scheduler": "at", "job": job}
        print(f"[Reminder] at: {result.stderr.strip()}")
    return None


def _cancel(record: dict) -> None:
    """Undo what `set` registered, whichever scheduler holds it."""
    kind = record.get("scheduler")
    if kind == "systemd":
        run_cmd(["systemctl", "--user", "disable", "--now", record.get("timer", "")],
                capture_output=True, text=True)
        for unit in record.get("units", []):
            Path(unit).unlink(missing_ok=True)
        run_cmd(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
    elif kind == "at" and record.get("job"):
        run_cmd(["atrm", record["job"]], capture_output=True, text=True)
    elif kind == "launchd":
        run_cmd(["launchctl", "unload", record.get("plist", "")],
                capture_output=True, text=True)
        Path(record.get("plist", "")).unlink(missing_ok=True)
    elif kind == "schtasks":
        run_cmd(["schtasks", "/Delete", "/TN", record.get("task", ""), "/F"],
                capture_output=True, text=True, **_CNW)
    for key in ("script", "_path"):
        if record.get(key):
            Path(record[key]).unlink(missing_ok=True)


def _spoken_when(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    today = datetime.now().date()
    day = ("today" if dt.date() == today
           else "tomorrow" if (dt.date() - today).days == 1
           else dt.strftime("%A %B %d"))
    return f"{day} at {dt.strftime('%H:%M')}"


def _list() -> ToolResult:
    upcoming = [r for r in _records() if r.get("when", "") >= datetime.now().isoformat()]
    if not upcoming:
        return ToolResult.success("There are no reminders set.", count=0)
    said = "; ".join(f"{_spoken_when(r['when'])}: {r.get('message', '')}"
                     for r in upcoming[:3])
    more = f" And {len(upcoming) - 3} more." if len(upcoming) > 3 else ""
    return ToolResult.success(
        f"{len(upcoming)} reminder{'s' if len(upcoming) != 1 else ''}: {said}.{more}",
        count=len(upcoming))


def _cancel_matching(words: str) -> ToolResult:
    records = _records()
    if not records:
        return ToolResult.failure("There are no reminders to cancel.",
                                  guidance="Tell the user nothing is set.")
    wanted = (words or "").strip().lower()
    if wanted in ("all", "everything", "all of them"):
        chosen = records
    elif wanted:
        chosen = [r for r in records if wanted in r.get("message", "").lower()]
    else:
        chosen = records if len(records) == 1 else []
    if not chosen:
        names = "; ".join(f"{_spoken_when(r['when'])}: {r.get('message', '')}"
                          for r in records[:5])
        return ToolResult.failure(
            "I could not tell which reminder to cancel.",
            guidance=f"Ask which one. The reminders are: {names}.")
    for record in chosen:
        _cancel(record)
    what = ("all reminders" if len(chosen) > 1
            else f"the reminder for {_spoken_when(chosen[0]['when'])}")
    return ToolResult.success(f"Cancelled {what}.", cancelled=len(chosen))


def reminder(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> ToolResult:
    action = str(parameters.get("action") or "set").strip().lower()
    if action == "list":
        return _list()
    if action in ("cancel", "delete", "remove"):
        return _cancel_matching(parameters.get("message", ""))

    date_str = str(parameters.get("date") or "").strip()
    time_str = str(parameters.get("time") or "").strip()
    message = _sanitise(parameters.get("message") or "Reminder")

    if not date_str or not time_str:
        return ToolResult.failure(
            "A reminder needs a date and a time.",
            guidance="Ask the user when, then call again with date and time.")
    try:
        target_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return ToolResult.failure(
            "That date or time could not be read.",
            guidance="Call again with date as YYYY-MM-DD and time as HH:MM.")
    if target_dt <= datetime.now():
        return ToolResult.failure(
            "That time has already passed.",
            guidance="Ask the user for a time in the future.")

    os_name = _get_os()
    task_name = f"aethelark-reminder-{target_dt.strftime('%Y%m%d-%H%M%S')}"
    if (_scripts_dir() / f"{task_name}.json").exists():
        task_name += f"-{datetime.now().strftime('%f')}"

    script_path = _write_notify_script(task_name, message, os_name)
    try:
        if os_name == "windows":
            registered = _schedule_windows(target_dt, task_name, script_path)
        elif os_name == "mac":
            registered = _schedule_mac(target_dt, task_name, script_path)
        else:
            registered = _schedule_linux(target_dt, task_name, script_path)
    except Exception as e:
        print(f"[Reminder] scheduling failed: {e}")
        registered = None

    if not registered:
        script_path.unlink(missing_ok=True)
        return ToolResult.failure(
            "The computer's scheduler would not take the reminder.",
            guidance="Tell the user the reminder was NOT set.")

    _write_record(task_name, {
        "task": task_name, "when": target_dt.isoformat(), "message": message,
        "script": str(script_path), **registered})

    if player:
        player.write_log(f"[Reminder] {date_str} {time_str} — {message[:40]}")
    return ToolResult.success(f"Reminder set for {_spoken_when(target_dt.isoformat())}.",
                              when=target_dt.isoformat())
