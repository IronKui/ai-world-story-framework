; 安装包脚本
;
; 用法（在项目根目录执行）：
;   "C:\Users\<你>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" tools\innosetup\AIWorldStoryFramework.iss
;
; 前置条件：先跑过 PyInstaller，dist\AIWorldStoryFramework\ 里有东西。

#define MyAppName "动态世界观文字游戏框架"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "轻羽若凡"
#define MyAppURL "https://github.com/IronKui/ai-world-story-framework"
#define MyAppExeName "AIWorldStoryFramework.exe"
#define MyAppId "{{8F3A1C42-7B6E-4D91-9E52-3C7A5B0D8F41}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; 装到用户目录而不是 Program Files：
;   · 不需要管理员权限，安装时不弹 UAC —— 对普通用户少一道坎
;   · 也避免程序尝试往 Program Files 写数据（那是只读的）
; 默认安装位置由 [Code] 里的 GetDefaultInstallDir 决定：
; 优先挑一个非 C 盘的固定硬盘，没有就回退到系统默认。
; 安装向导的「选择安装位置」页面仍然会显示，用户可以自己改。
DefaultDirName={code:GetDefaultInstallDir}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; 卸载时**不动用户数据**。存档在 %LOCALAPPDATA%\AIWorldStoryFramework，
; 不在安装目录里，所以默认就不会被删 —— 这里再确认一次，绝不能删。
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

OutputDir=..\..\dist\installer
OutputBaseFilename=AIWorldStoryFramework-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\assets\app.ico

; 程序要求 Win10 以上（PyQt6 6.11 的下限）
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
; 整个 PyInstaller 产物原样搬进去
Source: "..\..\dist\AIWorldStoryFramework\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清理安装目录。**不要**加 {localappdata}\AIWorldStoryFramework ——
; 那里面是用户的存档与 API Key，卸载程序无权替用户决定删掉。
Type: filesandordirs; Name: "{app}"

[Code]
// Inno Setup 的脚本函数集里没有 GetDriveType，得自己从 kernel32 调。
// 必须用 W 版：Inno Setup 6 是 Unicode 的，调 A 版会拼错盘符。
function GetDriveType(lpRootPathName: String): UINT;
  external 'GetDriveTypeW@kernel32.dll stdcall';

const
  DRIVE_REMOVABLE = 2;
  DRIVE_FIXED     = 3;

  // 低于这个剩余空间就不考虑这个盘。
  // 程序本体约 96MB，留 1GB 足够；这里刻意不写成 2GB ——
  // Pascal Script 里 2GB(2147483648) 超过 32 位整数上限，
  // 而且它不支持 Int64(...) 这种转换写法，会直接编译不过。
  MIN_FREE_BYTES = 1073741824;

// 挑选默认安装目录。
//
// 不写死 D 盘 —— 有些机器根本没有 D 盘，有的 D 盘是光驱、或者只剩几百兆。
// 所以按顺序扫描 D~Z，只要满足「固定硬盘 + 剩余空间够」就用它。
// C 盘作为兜底，任何异常情况都不会导致装不上。
//
// 注意：Inno Setup 的 Pascal Script 里没有 Char 类型，
// 所以盘符要用整数循环 + Chr() 拼，写成 for Letter := 'D' to 'Z'
// 会直接编译不过。
function GetDefaultInstallDir(Param: String): String;
var
  Index: Integer;
  Drive: String;
  FreeBytes, TotalBytes: Int64;
begin
  for Index := Ord('D') to Ord('Z') do
  begin
    Drive := Chr(Index) + ':\';

    // 只认固定硬盘：U 盘、光驱、网络盘都不能装 ——
    // 程序装在会被拔掉的盘上，下次开机就是个死快捷方式
    if GetDriveType(Drive) <> DRIVE_FIXED then
      continue;

    if not GetSpaceOnDisk64(Drive, FreeBytes, TotalBytes) then
      continue;

    if FreeBytes < MIN_FREE_BYTES then
      continue;

    Result := Drive + 'AIWorldStoryFramework';
    Exit;
  end;

  // 没有可用的其它盘，就用系统默认位置（非管理员装到用户目录下）
  Result := ExpandConstant('{autopf}\AIWorldStoryFramework');
end;

// 卸载前提醒一句：存档不会被删
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\AIWorldStoryFramework');
    if DirExists(DataDir) then
      if MsgBox('程序将被卸载。' + #13#10 + #13#10 +
                '你的存档、API Key 和世界观文档存放在：' + #13#10 +
                DataDir + #13#10 + #13#10 +
                '它们不会被删除。要一并删除吗？',
                mbConfirmation, MB_YESNO) = IDYES then
        // 用户主动确认才删，默认保留
        DelTree(DataDir, True, True, True);
  end;
end;
