; 简体中文语言包（部分翻译）
;
; Inno Setup 官方只随附 29 种语言，不含简体中文；社区完整翻译在
; GitHub 上，但国内网络取不到，所以这里自己写一份**部分翻译**。
;
; 只覆盖安装向导主流程用得上的字符串。Inno Setup 对缺失的键
; 会自动回退到 Default.isl 里的英文，所以这份文件不完整也不影响使用，
; 只是少数边角提示会显示英文。
;
; 键名照 Default.isl 抄的，改动前先核对，写错键名不报错、只是不生效。

[LangOptions]
LanguageName=简体中文
LanguageID=$0804
LanguageCodePage=936
DialogFontName=Microsoft YaHei UI
DialogFontSize=10
WelcomeFontName=Microsoft YaHei UI
WelcomeFontSize=18

[Messages]
SetupAppTitle=安装程序
SetupWindowTitle=安装 - %1
UninstallAppFullTitle=卸载 %1
UninstallAppTitle=卸载

; ---- 按钮 ----
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonNo=否(&N)
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&B)...
ButtonOK=确定

; ---- 欢迎页 ----
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=安装程序将把 [name/ver] 安装到你的电脑上。%n%n建议在继续之前关闭其它正在运行的程序。

; ---- 选择安装位置 ----
SelectDirDesc=[name] 应该安装到哪里？
SelectDirLabel3=安装程序将把 [name] 安装到下面的文件夹中。
SelectDirBrowseLabel=点击「下一步」继续。如果想装到别的地方，点击「浏览」。

; ---- 选择附加任务 ----
SelectTasksDesc=需要执行哪些附加任务？
SelectTasksLabel2=选择安装 [name] 时要执行的附加任务，然后点击「下一步」。

; ---- 准备安装 ----
ReadyLabel1=安装程序已经准备好，可以开始安装 [name] 了。
ReadyLabel2a=点击「安装」开始，或者点击「上一步」检查、修改设置。
ReadyMemoDir=安装位置：
ReadyMemoGroup=开始菜单文件夹：
ReadyMemoTasks=附加任务：
ReadyMemoUserInfo=用户信息：

; ---- 安装中 ----
InstallingLabel=正在安装 [name]，请稍候。

; ---- 完成 ----
FinishedHeadingLabel=[name] 安装向导完成
FinishedLabelNoIcons=[name] 已安装完成。
FinishedLabel=[name] 已安装完成。可以从开始菜单里的快捷方式启动它。
ClickFinish=点击「完成」退出安装程序。

; ---- 卸载 ----
ConfirmUninstall=确定要完全卸载 %1 及其所有组件吗？%n%n你的存档不会被删除。
UninstalledAll=%1 已成功卸载。
UninstalledMost=%1 卸载完成。%n%n有些文件没能删除，可以手动删掉。
UninstalledAndNeedsRestart=要完成 %1 的卸载，需要重启电脑。%n%n现在重启吗？
UninstallStatusLabel=正在从你的电脑上删除 %1，请稍候。

; ---- 错误 ----

[CustomMessages]
; 这些是 Inno Setup 的自定义消息，不在 [Messages] 里。
; 不补的话安装向导的标题栏、开始菜单项、快捷方式说明会显示英文。
NameAndVersion=%1 版本 %2
AdditionalIcons=附加快捷方式：
CreateDesktopIcon=创建桌面快捷方式(&D)
CreateQuickLaunchIcon=创建快速启动栏快捷方式(&Q)
ProgramOnTheWeb=%1 的网页
UninstallProgram=卸载 %1
LaunchProgram=运行 %1
AutoStartProgramGroupDescription=启动项：
AutoStartProgram=自动启动 %1
AddonHostProgramNotFound=在你选择的文件夹里找不到 %1。%n%n仍然要继续吗？
