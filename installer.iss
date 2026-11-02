#define MyAppName "MediaForge"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "KERZOX"
#define MyAppPublisherURL "https://github.com/kerzox/MediaForge"
#define MyAppExeName "MediaForge.exe"

[Setup]
; Unique AppId for upgrade matching
AppId={{E6F0C050-6B5B-4713-90AE-2C8F8A1BE3EF}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppPublisherURL}
AppSupportURL={#MyAppPublisherURL}
AppUpdatesURL={#MyAppPublisherURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=release
OutputBaseFilename=MediaForge-Setup
SetupIconFile=companion\resources\icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=no
DisableDirPage=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} v{#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "dist\MediaForge.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\MediaForgeBackend.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "VERSION"; DestDir: "{app}"; Flags: ignoreversion
Source: "installed_mode.marker"; DestDir: "{app}"; Flags: ignoreversion
Source: "MediaForge Backend.bat"; DestDir: "{app}"; Flags: ignoreversion
; Ship only runtime backend modules.  Do not install tests, caches, or Python
; bytecode from the source checkout into the production installation.
Source: "backend\app.py"; DestDir: "{app}\backend"; Flags: ignoreversion
Source: "backend\diagnostics.py"; DestDir: "{app}\backend"; Flags: ignoreversion
Source: "backend\downloader.py"; DestDir: "{app}\backend"; Flags: ignoreversion
Source: "backend\recovery.py"; DestDir: "{app}\backend"; Flags: ignoreversion
Source: "backend\requirements.txt"; DestDir: "{app}\backend"; Flags: ignoreversion
Source: "backend\startup_check.py"; DestDir: "{app}\backend"; Flags: ignoreversion
Source: "extension\*"; DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion recursesubdirs createallsubdirs
; Documentation
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "CONTRIBUTING.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "SECURITY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "CODE_OF_CONDUCT.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
