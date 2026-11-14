; Instalador de Sound para Windows. Se compila con Inno Setup 6:
;   iscc /DMyAppVersion=1.3.0 installer.iss
#define MyAppName "Sound"
#ifndef MyAppVersion
  #define MyAppVersion "1.3.0"
#endif
#define MyAppPublisher "Mateo Arias"
#define MyAppURL "https://github.com/MateoAriasCaicedo/sound"
#define MyAppExeName "Sound.exe"

[Setup]
AppId={{6C1B0E42-6D67-4B0D-9E5A-2B7A8C4D1F31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=Sound-{#MyAppVersion}-setup
SetupIconFile=packaging\sound.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Iniciar {#MyAppName} al iniciar Windows"; GroupDescription: "Inicio:"; Flags: checkedonce
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Sound"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: autostart
; Aunque la tarea no se marcara al instalar, si luego se activa desde Ajustes la
; desinstalación también debe quitar el arranque automático.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Sound"; Flags: uninsdeletevalue

[Files]
Source: "dist\Sound\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; La biblioteca, el historial y las preferencias NO se borran al desinstalar.
Type: filesandordirs; Name: "{app}"
