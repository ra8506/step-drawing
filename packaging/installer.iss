; Inno Setup script. Built in CI:  iscc /DAppVersion=1.2.3 /DSourceDir=..\build\app packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\app"
#endif
#define AppName "STEP Drawing"
#define AppExe "STEP Drawing.exe"

[Setup]
AppId={{6C3F2E9A-4B1D-4E8C-9A57-2D1F0B7C5E31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=ra8506
AppPublisherURL=https://github.com/ra8506/step-drawing
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
; Per-user install: no admin prompt, and the in-app updater can replace files silently.
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=STEP-Drawing-{#AppVersion}-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; Replace the bundled runtime wholesale on every update so no stale files linger.
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Adds STEP Drawing to "Open with" for .step/.stp without taking over the default app.
Root: HKCU; Subkey: "Software\Classes\StepDrawing.step"; ValueType: string; ValueData: "STEP model"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\StepDrawing.step\DefaultIcon"; ValueType: string; ValueData: """{app}\{#AppExe}"",0"
Root: HKCU; Subkey: "Software\Classes\StepDrawing.step\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\.step\OpenWithProgids"; ValueType: string; ValueName: "StepDrawing.step"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.stp\OpenWithProgids"; ValueType: string; ValueName: "StepDrawing.step"; ValueData: ""; Flags: uninsdeletevalue
; Gerber board outlines (.gbr, .gko, .gm1) open the same way.
Root: HKCU; Subkey: "Software\Classes\.gbr\OpenWithProgids"; ValueType: string; ValueName: "StepDrawing.step"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.gko\OpenWithProgids"; ValueType: string; ValueName: "StepDrawing.step"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.gm1\OpenWithProgids"; ValueType: string; ValueName: "StepDrawing.step"; ValueData: ""; Flags: uninsdeletevalue

[Run]
; Runs after both normal installs (as a checkbox) and silent in-app updates (relaunch).
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall; Check: not HasParam('/NOLAUNCH')

[Code]
// /NOLAUNCH skips starting the app after install (used by automated tests).
function HasParam(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;
