; volksmond.iss - Inno Setup script for Volksmond.
; Produces a single per-user installer (no admin) from the PyInstaller one-folder build:
; Start menu shortcut, Add/Remove Programs entry, a clean uninstaller, and in-place upgrades.
; build-app.ps1 passes MyAppVersion / MySourceDir / MyOutputDir via ISCC /D defines; the #ifndef
; fallbacks let the script also open and compile standalone in the Inno Setup IDE.

#define MyAppName "Volksmond"
#define MyAppPublisher "DigiPhyte"
#define MyAppURL "https://volksmond.digiphyte.com"
#define MyAppExeName "Volksmond.exe"

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef MySourceDir
  #define MySourceDir "."
#endif
#ifndef MyOutputDir
  #define MyOutputDir "."
#endif

[Setup]
; A stable AppId keeps upgrades in place: a new version replaces the old one. Settings, downloaded
; models and every transcript location live outside {app}, so install, upgrade and uninstall all
; leave them untouched.
AppId={{7C8E2A14-3B5D-4E9F-A1C2-9D4F6B0E8A37}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Per-user install: no admin prompt, anyone can install it (right for a download-and-run app).
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir={#MyOutputDir}
OutputBaseFilename=Volksmond-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,Volksmond}"; Flags: nowait postinstall skipifsilent
