; Inno Setup 6 - tao bo cai PDF Black & White Studio
#define MyAppName "PDF Black & White Studio"
#ifndef MyAppVersion
  #define MyAppVersion "2.2.0"
#endif
#define MyAppExe "PDF_BW_Studio.exe"

[Setup]
AppId={{B7A3C1E2-5D4F-4A86-9E31-7C2D8F0A1B64}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\PDF BW Studio
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExe}
SetupIconFile=icon.ico
OutputDir=installer_output
OutputBaseFilename=PDF_BW_Studio_Setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Tao shortcut ngoai Desktop"; GroupDescription: "Shortcut:"
Name: "openwith"; Description: "Them vao menu chuot phai 'Open with' cua file .pdf"; GroupDescription: "Tich hop:"

[Files]
Source: "dist\PDF_BW_Studio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Go cai dat {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; Chi them vao "Open with", KHONG chiem quyen mo PDF mac dinh cua ban
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExe}\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExe}"" ""%1"""; Tasks: openwith; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithList\{#MyAppExe}"; ValueType: none; Tasks: openwith; Flags: uninsdeletekey

[InstallDelete]
; Cai bo cai day du = ve trang thai sach, bo cac ban cap nhat da cai truoc do
Type: filesandordirs; Name: "{localappdata}\PDFBWStudio\updates"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\PDFBWStudio\updates"

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Chay {#MyAppName} ngay"; Flags: nowait postinstall skipifsilent
