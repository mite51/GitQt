#ifndef AppVersion
#define AppVersion "0.1.0"
#endif
[Setup]
AppId={{EA9A1DE8-74EC-456B-9377-F2AEE08B90D9}
AppName=GitQt
AppVersion={#AppVersion}
AppPublisher=GitQt contributors
DefaultDirName={localappdata}\Programs\GitQt
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DefaultGroupName=GitQt
UninstallDisplayIcon={app}\GitQt.exe
OutputDir=..\dist\releases
OutputBaseFilename=GitQt-{#AppVersion}-windows-x86_64-setup
Compression=lzma2
SolidCompression=yes
LicenseFile=..\LICENSE
SetupIconFile=..\build\gitqt.ico
[Files]
Source: "..\dist\GitQt\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\GitQt"; Filename: "{app}\GitQt.exe"
[Run]
Filename: "{app}\GitQt.exe"; Description: "Launch GitQt"; Flags: nowait postinstall skipifsilent
