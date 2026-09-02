; Transcribr - Windows installer
;
; Packs build\stage\ (produced by windows\build-exe.ps1) into a single
; Setup .exe. Compile with:
;
;     iscc /DAppVersion=0.9.14 windows\transcribr.iss
;
; Installs per-user into %LOCALAPPDATA%, which means no UAC prompt and
; no administrator password. It also leaves the tree writable, so the
; app's Models tab can pip-install the optional engines into it later.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName      "Transcribr"
#define AppPublisher "James Leaver"
#define AppURL       "https://github.com/jamesleaver/Transcribr"

[Setup]
; Keep this GUID stable forever - it is how Windows recognises an
; existing installation and upgrades it in place.
AppId={{3FE8B61A-037C-459D-B664-5ADC1B6A83FD}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}

; Per-user install: no elevation, no UAC prompt, and the install tree
; stays writable for the Models tab.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto

OutputDir=..\dist
OutputBaseFilename=Transcribr-{#AppVersion}-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\icon.ico
UninstallDisplayName={#AppName} {#AppVersion}
WizardStyle=modern
LicenseFile=..\LICENSE

; The payload is mostly already-compressed wheels and DLLs; lzma2/max
; with a solid block still buys a lot on a tree this size.
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; The whole staged tree, runtime included.
Source: "..\build\stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; pythonw.exe rather than python.exe so no console window flashes up.
Name: "{group}\{#AppName}"; \
    Filename: "{app}\python\pythonw.exe"; \
    Parameters: """{app}\transcribr.py"""; \
    WorkingDir: "{app}"; \
    IconFilename: "{app}\icon.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; \
    Filename: "{app}\python\pythonw.exe"; \
    Parameters: """{app}\transcribr.py"""; \
    WorkingDir: "{app}"; \
    IconFilename: "{app}\icon.ico"; \
    Tasks: desktopicon

[Run]
; Install the WebView2 runtime first, and only if it is actually
; missing - without it the interface cannot render. Per-user install, so
; no elevation; needs a connection, hence the forgiving error handling.
Filename: "{app}\MicrosoftEdgeWebview2Setup.exe"; \
    Parameters: "/silent /install"; \
    StatusMsg: "Installing the Microsoft Edge WebView2 runtime..."; \
    Check: WebView2Missing; \
    Flags: waituntilterminated skipifdoesntexist runasoriginaluser

Filename: "{app}\python\pythonw.exe"; \
    Parameters: """{app}\transcribr.py"""; \
    WorkingDir: "{app}"; \
    Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[Code]
function WebView2Version(RootKey: Integer; SubKey: String): String;
begin
  Result := '';
  if not RegQueryStringValue(RootKey, SubKey, 'pv', Result) then
    Result := '';
end;

// True when the Evergreen WebView2 runtime is absent. Microsoft records
// it under EdgeUpdate; "0.0.0.0" means registered but not installed.
function WebView2Missing: Boolean;
var
  Version: String;
begin
  // Inno's Pascal Script has no local const blocks, so the client GUID
  // is written out in full rather than named once.
  Version := WebView2Version(HKEY_LOCAL_MACHINE,
      'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
  if (Version = '') or (Version = '0.0.0.0') then
    Version := WebView2Version(HKEY_CURRENT_USER,
        'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
  Result := (Version = '') or (Version = '0.0.0.0');
end;

[UninstallDelete]
; Bytecode and anything the Models tab installed later - none of it is
; tracked by the installer, so it has to be swept explicitly. The user's
; transcripts and settings live elsewhere and are never touched.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\webdist"
