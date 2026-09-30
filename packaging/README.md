# packaging

The launchers the installers put on a user's machine. `install.sh` and
`install.ps1` copy them with the app's folder filled in.

| File | What it is |
|---|---|
| `eagle` | The `eagle` command on macOS and Linux: opens the eagle, and `install`, `remove`, `modules`, `update`. |
| `eagle.ps1` | The same on Windows (`eagle.cmd` calls it). |
| `uninstall.ps1` | Windows uninstall, run from Settings → Apps. |

Edit these files, not the copies under `~/.local/bin` or
`%LOCALAPPDATA%\Aethelark\bin`: the next install or `eagle update` overwrites the
copies from these.
