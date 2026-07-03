; Inno Setup script for the XRD Graphitization Analyzer Windows installer.
; Produces a single self-contained .exe (no separate MSIX cert-trust step --
; unlike the MSIX path, a traditional installer only needs a SmartScreen
; "More info -> Run anyway" click on first run, since it isn't signed with a
; trusted CA cert).
;
; Build the Release app first (windows\scripts\build.ps1 -Configuration Release),
; then compile with Inno Setup 6 (https://jrsoftware.org/isinfo.php, or
; `winget install JRSoftware.InnoSetup`):
;   ISCC.exe /DAppVersion=1.0.0 windows\scripts\installer.iss
; Output: windows\scripts\Output\XRD-Graphitization-Analyzer-Windows-Setup.exe
; (Output\ is gitignored -- built locally / in CI, uploaded to GitHub Releases,
; never committed.)
;
; File association + single-instance both reuse the app's existing unpackaged
; activation path (App.xaml.cs already reads argv for `.xy` paths and
; registers a single-instance AppInstance key), so no MSIX-specific code is
; needed here.

#ifndef AppVersion
#define AppVersion "1.0.0"
#endif

#define AppPublisher "Arun Vaithianathan"
#define AppURL "https://github.com/akvaithi/xrd-graphitization-analyzer"
#define BuildOutDir "..\XRDAnalyzer\bin\Release\net8.0-windows10.0.26100.0\win-x64"

[Setup]
AppId={{8599138A-4158-4CB3-9092-016A4AE3ADEE}
AppName=XRD Graphitization Analyzer
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\XRD Graphitization Analyzer
DefaultGroupName=XRD Graphitization Analyzer
UninstallDisplayIcon={app}\XRDAnalyzer.exe
OutputDir=Output
OutputBaseFilename=XRD-Graphitization-Analyzer-Windows-Setup
SetupIconFile=..\XRDAnalyzer\Assets\AppIcon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "fileassoc"; Description: "Open .xy scans with XRD Graphitization Analyzer"; GroupDescription: "File association:"; Flags: checkedonce

[Files]
Source: "{#BuildOutDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\XRD Graphitization Analyzer"; Filename: "{app}\XRDAnalyzer.exe"
Name: "{group}\Uninstall XRD Graphitization Analyzer"; Filename: "{uninstallexe}"
Name: "{autodesktop}\XRD Graphitization Analyzer"; Filename: "{app}\XRDAnalyzer.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.xy"; ValueType: string; ValueName: ""; ValueData: "XRDGraphitizationAnalyzer.xy"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\XRDGraphitizationAnalyzer.xy"; ValueType: string; ValueName: ""; ValueData: "XRD Scan File"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\XRDGraphitizationAnalyzer.xy\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\XRDAnalyzer.exe,0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\XRDGraphitizationAnalyzer.xy\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\XRDAnalyzer.exe"" ""%1"""; Tasks: fileassoc

[Run]
Filename: "{app}\XRDAnalyzer.exe"; Description: "Launch XRD Graphitization Analyzer"; Flags: nowait postinstall skipifsilent
