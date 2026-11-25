#define MyAppName "SettAx"
#define MyAppVersion "1.7.0"
#define MyAppExeName "SettAx.exe"
[Setup]
AppId={{8F7954AE-7A94-45F9-937B-163EFCA87F40}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\SettAx
DefaultGroupName={#MyAppName}
OutputDir=installer
OutputBaseFilename=SettAx_Setup_v1_6
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayName={#MyAppName}
CloseApplications=yes
RestartApplications=no
[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
