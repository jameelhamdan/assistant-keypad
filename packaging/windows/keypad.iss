; Keypad for Windows - per-user installer (no administrator rights).
; Build: packaging\windows\build.ps1 -Version 1.0.0  (runs PyInstaller, then this script)

#ifndef Version
  #define Version "dev"
#endif

[Setup]
AppId={{6C0F7E2B-3A0D-4F57-9B1E-5B0B8D1C4A11}
AppName=Keypad
AppVersion={#Version}
AppPublisher=Jameel Hamdan
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
Source: "..\..\dist\Keypad\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; files from an older build that the new one no longer has
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\Keypad"; Filename: "{app}\keypadw.exe"; Parameters: "tray"

[Run]
; Stop an older agent, then register hooks + MCP and start at login (starts agent and tray now).
Filename: "{app}\keypad.exe"; Parameters: "install"; Flags: runhidden waituntilterminated; StatusMsg: "Connecting Claude Code..."

[UninstallRun]
Filename: "{app}\keypad.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "KeypadUninstall"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Settings and pairing keys live outside the app folder (in %APPDATA%\Keypad),
  // so the installer's own file removal never touches them; ask once, after
  // the uninstall above has already removed the hooks and login items.
  if CurUninstallStep = usPostUninstall then
    if MsgBox('Also delete Keypad''s settings and pairing keys?', mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{userappdata}\Keypad'), True, True, True);
end;
