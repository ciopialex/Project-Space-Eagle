; Inno Setup script for the Aethelark Windows installer.
;
; Built in CI by:  iscc /DMyAppVersion=1.2.3 packaging/aethelark.iss
; Inno Setup ships preinstalled on GitHub's windows-latest runners.
;
; Installs per-user (no UAC prompt). The app writes to %APPDATA%\Aethelark, so
; it never needs to write into its own install directory - see runtime_paths.py.

#define MyAppName "Aethelark"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppPublisher "Aethelark"
#define MyAppExeName "Aethelark.exe"

[Setup]
AppId={{8F3A6C21-2B4D-4E19-9A77-1C5E0D9B4A62}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=Output
OutputBaseFilename=AethelarkSetup
SetupIconFile=..\config\aethelark.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user install: no administrator prompt, and an upgrade cannot clobber
; another account's data.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
; Refuse to install over a newer build rather than silently downgrading.
AppMutex=AethelarkSingleInstance

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startup";     Description: "Start {#MyAppName} when I sign in";       GroupDescription: "Startup:"; Flags: unchecked

[Files]
; The whole PyInstaller onedir tree.
Source: "..\dist\Aethelark\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove only what the install created. %APPDATA%\Aethelark holds the user's
; API keys and memory and is deliberately left behind - uninstalling to fix a
; problem should not cost them their setup.
Type: filesandordirs; Name: "{app}"
