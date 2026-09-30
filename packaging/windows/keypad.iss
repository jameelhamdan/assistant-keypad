; Keypad for Windows - per-user installer (no administrator rights).
; Build: make windows && iscc /DVersion=1.0.0 packaging\windows\keypad.iss

#ifndef Version
  #define Version "dev"
#endif

[Setup]
AppId={{6C0F7E2B-3A0D-4F57-9B1E-5B0B8D1C4A11}
AppName=Keypad
AppVersion={#Version}
AppPublisher=Keypad
DefaultDirName={localappdata}\Programs\Keypad
DefaultGroupName=Keypad
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=Keypad-{#Version}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\keypadw.exe
CloseApplications=yes

[Files]
Source: "..\..\dist\windows\keypad.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\windows\keypadw.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Keypad"; Filename: "{app}\keypadw.exe"; Parameters: "tray"
Name: "{group}\Keypad Settings"; Filename: "{app}\keypadw.exe"; Parameters: "settings"

[Run]
; Stop an older agent, then register hooks + MCP and start at login (starts agent and tray now).
Filename: "{app}\keypad.exe"; Parameters: "install"; Flags: runhidden waituntilterminated; StatusMsg: "Connecting Claude Code..."
Filename: "{app}\keypadw.exe"; Parameters: "settings"; Description: "Open Keypad settings"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\keypad.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "KeypadUninstall"
