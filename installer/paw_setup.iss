; =====================================================================
; PAW Personal Work OS v2.3 - Official Windows Installer Setup Script
; Inno Setup 6 Script
; =====================================================================

#define MyAppName "PAW Personal Work OS"
#define MyAppVersion "2.3.9"
#define MyAppPublisher "PAW Work OS Team"
#define MyAppURL "http://127.0.0.1:8800/app"
#define MyAppExeName "PAW_Personal_Work_OS.exe"
#define MyAppAppId "{{E6F7A9D2-3B4C-5D6E-7F8A-9B0C1D2E3F4A}"
#define MyAppRegistryKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\{E6F7A9D2-3B4C-5D6E-7F8A-9B0C1D2E3F4A}_is1"

[Setup]
; Basic Application Identity
AppId={#MyAppAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Installation Target (Default: C:\Program Files\PAW Personal Work OS)
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE.txt

; Output Directory & Installer Name
OutputDir=..\dist
OutputBaseFilename=PAW_Personal_Work_OS_Setup_v2.3.9
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

; SmartScreen & Windows Compatibility Metadata
VersionInfoVersion=2.3.9.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装向导
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion=2.3.9.0
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}. All rights reserved.

; Privileges & Architecture (Universal compatibility across Admin and Standard user accounts)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no

[CustomMessages]
chinesesimp.NameAndVersion=%1 版本 %2
chinesesimp.AdditionalIcons=附加快捷方式：
chinesesimp.CreateDesktopIcon=创建桌面快捷方式(&D)
chinesesimp.CreateQuickLaunchIcon=创建快速启动栏快捷方式(&Q)
chinesesimp.ProgramOnTheWeb=%1 网站
chinesesimp.UninstallProgram=卸载 %1
chinesesimp.LaunchProgram=运行 %1
chinesesimp.AssocFileExtension=将 %2 文件扩展名与 %1 建立关联(&A)
chinesesimp.AssocingFileExtension=正在将 %2 文件扩展名与 %1 建立关联...
chinesesimp.AutoStartProgramGroupDescription=启动：
chinesesimp.AutoStartProgram=自动启动 %1
chinesesimp.AddonHostProgramNotFound=您选择的文件夹中无法找到 %1。%n%n您确定要继续吗？

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式 (&D)"; GroupDescription: "附加快捷方式:"; Flags: checkedonce
Name: "startupicon"; Description: "开机自动启动 PAW (&S)"; GroupDescription: "启动选项:"; Flags: unchecked

[Files]
Source: "..\dist\PAW_Personal_Work_OS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Pre-installation & Pre-uninstallation Graceful Shutdown (Requirement G3)
procedure RequestGracefulShutdown();
var
  ResultCode: Integer;
begin
  Exec('powershell.exe', '-NoProfile -Command "8800..8805 | ForEach-Object { try { Invoke-RestMethod -Uri \"http://127.0.0.1:$_/v1/local/system/shutdown\" -Method Post -TimeoutSec 1 | Out-Null } catch {} }"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);
  Exec('taskkill.exe', '/F /IM PAW_Personal_Work_OS.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// Existing installation detection functions
function GetInstalledUninstallString(): string;
var
  S: string;
begin
  Result := '';
  if RegQueryStringValue(HKLM, '{#MyAppRegistryKey}', 'UninstallString', S) then
    Result := S
  else if RegQueryStringValue(HKCU, '{#MyAppRegistryKey}', 'UninstallString', S) then
    Result := S;
end;

function GetInstalledDisplayVersion(): string;
var
  S: string;
begin
  Result := '';
  if RegQueryStringValue(HKLM, '{#MyAppRegistryKey}', 'DisplayVersion', S) then
    Result := S
  else if RegQueryStringValue(HKCU, '{#MyAppRegistryKey}', 'DisplayVersion', S) then
    Result := S;
end;

// Initialize Setup: Check existing installation & offer Direct Upgrade / Uninstall
function InitializeSetup(): Boolean;
var
  Uninstaller: string;
  PrevVer: string;
  Msg: string;
  Ret: Integer;
  ResultCode: Integer;
begin
  Result := True;
  
  // Check if an existing version of PAW is installed
  Uninstaller := GetInstalledUninstallString();
  if Uninstaller <> '' then
  begin
    if WizardSilent then
    begin
      RequestGracefulShutdown();
      Result := True;
      Exit;
    end;

    PrevVer := GetInstalledDisplayVersion();
    if PrevVer = '' then
      PrevVer := '现有版本';
      
    Msg := '检测到您的电脑上已安装 PAW Personal Work OS (已安装版本: ' + PrevVer + ')。' + #13#10#13#10 +
           '请选择您要执行的操作：' + #13#10 +
           '• 点击「是 (Yes)」：直接升级更新至最新版本 (版本: {#MyAppVersion})' + #13#10 +
           '  (将完整保留您的所有业务知识库、项目资产与配置)' + #13#10#13#10 +
           '• 点击「否 (No)」：在安装窗口内直接卸载现有版本' + #13#10#13#10 +
           '• 点击「取消 (Cancel)」：退出本次安装向导';
           
    Ret := MsgBox(Msg, mbConfirmation, MB_YESNOCANCEL or MB_DEFBUTTON1);
    if Ret = IDYES then
    begin
      // Direct Upgrade: graceful shutdown of any running instance, proceed with installation
      RequestGracefulShutdown();
      Result := True;
    end
    else if Ret = IDNO then
    begin
      // Direct Uninstall from installer window
      RequestGracefulShutdown();
      if Exec(RemoveQuotes(Uninstaller), '', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
      begin
        MsgBox('旧版本已成功完成卸载。' + #13#10 + '您可以随时再次运行本安装向导以安装新版本。', mbInformation, MB_OK);
      end;
      Result := False;
    end
    else
    begin
      // Cancel installer
      Result := False;
    end;
  end
  else
  begin
    RequestGracefulShutdown();
  end;
end;

function InitializeUninstall(): Boolean;
begin
  RequestGracefulShutdown();
  Result := True;
end;

// Pre-update Automatic Database Backup (v2.3.1 §6.2.5 & G3.5)
procedure BackupDatabaseBeforeInstall();
var
  DataDir, DbFile, BackupFile: string;
begin
  DataDir := ExpandConstant('{localappdata}\PAW');
  DbFile := DataDir + '\workspace.db';
  if FileExists(DbFile) then
  begin
    BackupFile := DataDir + '\workspace_pre_update.db';
    CopyFile(DbFile, BackupFile, False);
  end;
end;

// User Data Directory Initialization (Requirement 4 & 6)
procedure InitializeUserDataDirectory();
var
  DataDir, SubDataDir, TemplateSrc, DestDb1, DestDb2: string;
begin
  DataDir := ExpandConstant('{localappdata}\PAW');
  SubDataDir := DataDir + '\data';
  
  // 1. Ensure directory hierarchy
  ForceDirectories(DataDir);
  ForceDirectories(SubDataDir);
  ForceDirectories(DataDir + '\sources');
  ForceDirectories(DataDir + '\evidence');
  ForceDirectories(DataDir + '\backups');
  ForceDirectories(DataDir + '\logs');
  ForceDirectories(SubDataDir + '\sources');
  ForceDirectories(SubDataDir + '\evidence');
  ForceDirectories(SubDataDir + '\backups');
  
  // 2. Safe template initialization (Never overwrite existing user database!)
  TemplateSrc := ExpandConstant('{app}\_internal\template\workspace_template.db');
  DestDb1 := DataDir + '\workspace.db';
  DestDb2 := SubDataDir + '\workspace.db';
  
  if FileExists(TemplateSrc) then
  begin
    if not FileExists(DestDb1) then
      CopyFile(TemplateSrc, DestDb1, False);
    if not FileExists(DestDb2) then
      CopyFile(TemplateSrc, DestDb2, False);
  end;
  
  // 3. Default config.json if not present
  if not FileExists(DataDir + '\config.json') then
  begin
    SaveStringToFile(DataDir + '\config.json', '{"host": "127.0.0.1", "port": 8800, "provider": "deepseek"}', False);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  AppDir: string;
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    BackupDatabaseBeforeInstall();
  end
  else if CurStep = ssPostInstall then
  begin
    // Initialize %LOCALAPPDATA%\PAW structure
    InitializeUserDataDirectory();
    
    // Strip Mark of the Web (Zone.Identifier) from all installed files
    AppDir := ExpandConstant('{app}');
    Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath ''' + AppDir + ''' -Recurse -ErrorAction SilentlyContinue | Unblock-File"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

// Check command line parameter for automated uninstall Path B (G4.1)
function ShouldPurgeData(): Boolean;
var
  i: Integer;
begin
  Result := False;
  for i := 1 to ParamCount do
  begin
    if (CompareText(ParamStr(i), '/PURGEDATA=1') = 0) or
       (CompareText(ParamStr(i), '/DELETEDATA=1') = 0) or
       (CompareText(ParamStr(i), '/PURGEDATA') = 0) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

// Custom Uninstall Behavior: Path A (Preserve Data) vs Path B (Purge Data)
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
  MsgRes: Integer;
  ConfirmRes: Integer;
  DoPurge: Boolean;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\PAW');
    if DirExists(DataDir) then
    begin
      DoPurge := False;
      if ShouldPurgeData() then
      begin
        DoPurge := True;
      end
      else if not UninstallSilent then
      begin
        MsgRes := MsgBox('是否同时删除 PAW 工作数据？' + #13#10#13#10 +
                         '【保留（推荐）】：点击「否」，保留您的所有知识、项目、待办与配置，以便日后重新安装或升级。' + #13#10 +
                         '【删除】：点击「是」，彻底删除本地数据目录。',
                         mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
        if MsgRes = IDYES then
        begin
          ConfirmRes := MsgBox('【重要警告 - 彻底清除数据确认】' + #13#10#13#10 +
                               '即将永久删除本地所有 PAW 工作数据：' + #13#10 +
                               DataDir + #13#10#13#10 +
                               '删除后将无法恢复！是否确定要删除？',
                               mbCriticalError, MB_YESNO or MB_DEFBUTTON2);
          if ConfirmRes = IDYES then
            DoPurge := True;
        end;
      end;

      if DoPurge then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
