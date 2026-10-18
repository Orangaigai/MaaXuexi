# 安装指南

## 准备环境

- Windows 10/11 64 位。当前完整运行环境按 **Python 3.12 64 位**验证；不承诺其他系统或 Python 次版本直接可用。
- 从 [Python 官方网站](https://www.python.org/downloads/windows/)安装 Python 3.12，并保留 Python Launcher（`py`）。在终端输入 `py -3.12 --version` 应显示版本号。
- 安装带 `nx_main/MuMuManager.exe` 与 `nx_main/adb.exe` 的 [MuMu 模拟器](https://mumu.163.com/)。当前实测使用这类 MuMu Windows 实例；不将其他模拟器标记为已支持。
- 在 MuMu 安装学习强国并本人登录。应用运行时应提供 **720×1280 的竖屏画面**；确认字体缩放、分辨率和窗口方向适合现有识别资源。
- Maa 原生组件若提示缺少 DLL，安装 [Microsoft 官方 Visual C++ x64 运行库](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)。

不要把已经登录的模拟器或 API 密钥提交到 GitHub。下载包内不应包含你的登录态。

## 下载项目

新用户可使用 GitHub 页面的 **Code → Download ZIP**，解压到例如 `D:\MaaXuexi`。

熟悉 Git 的用户也可以：

```powershell
git clone https://github.com/Orangaigai/MaaXuexi.git
cd MaaXuexi
```

源码包含识别资源；不需要 npm、前端构建工具或额外下载某个“题库”。不要把 `.venv` 从别人的电脑复制过来。

## 首次安装

双击根目录 **安装MaaXuexi.cmd**。出现 `MuMu install folder (contains nx_main)` 时输入 MuMu 安装目录，例如：

```text
D:\Apps\MuMuPlayer
```

填的是包含 `nx_main` 的上一级目录。安装程序会：

1. 建立项目自己的 `.venv`，安装锁定的 Python 依赖。
2. 只在文件不存在时创建模拟器配置、基础任务清单、关闭的观点规则与初始时间配置。
3. 注册属于本安装目录的 Windows 计划任务，**首次保持关闭**。需要时在控制台启用。

安装不启动学习、不连接 DeepSeek、不自动登录，也不覆盖已有密钥、任务或计划。已有同目录计划会保留其启用状态与时间。

看到 `Installation complete` 后，双击 **启动MaaXuexi.cmd**。启动会准备模拟器和浏览器；在网页点击“立即执行”才开始手动任务。第一次建议先用基础任务。

## 用命令安装

在项目根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-MaaXuexi.ps1 -MuMuRoot "D:\Apps\MuMuPlayer" -VmIndex 0
```

找不到 `py` 时，可明确指定已安装的 Python 3.12：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Install-MaaXuexi.ps1 -MuMuRoot "D:\Apps\MuMuPlayer" -PythonPath "C:\Path\To\Python312\python.exe"
```

`-VmIndex` 是 MuMu 实例编号，默认 `0`。脚本按本机路径生成 `config/emulator.json`；可参考[配置说明](configuration.md#模拟器)。

`-SkipSchedule` 仅用于不注册计划任务的开发/隔离安装；`-SkipDependencies` 仅用于依赖已安装后的本地检查。跳过计划注册时，不把控制台定时功能记为已配置。

## 更新与迁移

先结束当前任务并关闭本项目后台，再备份 `config`。Git 用户可拉取源码后重新运行安装，现有设置不会被覆盖；非 Git 用户使用新目录时，需要调整模拟器路径并重新注册该目录的计划。

同一 Windows 用户下只维护一个 `MaaXuexi-Daily-0900` 计划。若安装提示另一个目录拥有它，脚本不会替换旧计划：确认旧安装不再使用后，在 Windows 任务计划程序中核实该任务的动作路径，再删除旧项目专属任务，重新安装。不要删除不属于本项目的任务。

DPAPI 密钥与当前 Windows 用户及机器环境相关。换电脑或 Windows 用户后，在控制台重新配置密钥；不要把解密后的密钥写进配置示例。不要删除订阅和观点数据库来“清理”，它们用于避免重复操作。
