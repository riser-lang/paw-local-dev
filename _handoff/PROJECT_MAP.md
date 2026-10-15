# PAW 本地版 (Windows EXE) 项目盘点地图 (PROJECT_MAP)

> 本文档为**一页大白话**项目盘点，供产品负责人及审核人（Claude Code）快速掌握软件整体技术架构、安装包构成及数据落盘位置。

---

## 1. 什么是 PAW 本地版
PAW（Personal Work OS）是一款运行在用户 Windows 电脑上的**个人 AI 工作台系统**。
它在本地监控用户指定的文件夹，自动解析放入的 Word、Excel、PPT、PDF 文档，提取关键事实、证据与待办，并生成“候选提案”。经用户在界面审核通过后沉淀为持久资产。软件还自带 AI 助手问答、巡航检测和项目自动收敛功能，并且 API Key 采用 Windows 本地加密，所有数据默认不出本机。

---

## 2. 技术栈（用什么写的）
- **后台服务**：Python 3.13 + FastAPI + Uvicorn。提供本地 127.0.0.1 端口的 REST 接口与后台任务调度。
- **文档高保真解析**：内置 `python-docx`（Word）、`openpyxl`（Excel）、`python-pptx`（PPT）、`pypdf`（PDF）。
- **本地数据库**：SQLite 3。采用独立的数据库文件存储项目、事实、证据及审核记录（共 35 张业务表）。
- **前端交互界面**：React 18 单页面应用（SPA），预先编译打包在 `frontend/dist` 目录下，直接由 Python 后台内嵌托管。
- **系统托盘**：`pystray`。程序启动后在 Windows 任务栏右下角显示图标，无黑框控制台窗口，双击或右键可打开浏览器工作台或退出。
- **大模型与联网**：
  - 本地自带免 Key 联网搜索（优先调用 Bing 网页端，故障自动切换 DuckDuckGo Lite 兜底）。
  - 支持 OpenAI / DeepSeek / 通义千问 / 本地模型等兼容网关，API Key 仅在当前用户登录态下解密使用。

---

## 3. 程序入口与打包方式
- **源码运行入口**：`run.py`
  - 启动 Uvicorn 异步服务（默认监听 `http://127.0.0.1:8800`）。
  - 启动系统托盘图标，并在首次启动时自动调起系统默认浏览器访问工作台。
- **第一级打包（源码 -> 免安装运行目录）**：
  - 工具：PyInstaller（配置文件 `paw_desktop.spec`）。
  - 产物目录：`dist/PAW_Personal_Work_OS/`。
  - 内部主程序：`PAW_Personal_Work_OS.exe`（整合了 Python 运行时、第三方依赖库、空白数据库模板与前端产物，控制台窗口已隐藏）。
- **第二级打包（运行目录 -> 单文件安装包）**：
  - 工具：Inno Setup 6（脚本文件 `installer/paw_setup.iss`）。
  - 产物：`PAW_Personal_Work_OS_Setup.exe`。
  - 特性：单文件标准 Windows 安装向导，支持一键安装、静默安装、覆盖升级与干净卸载。

---

## 4. 安装包形式与当前体积
- **最新收到的安装包（微信交付包 v2.3.8）**：
  - 文件名：`PAW_Personal_Work_OS_Setup_v2.3.8.exe`
  - 文件大小：26,887,584 字节（约 **25.64 MB**）
  - SHA256 哈希：`3BEA84DD192D4F26A457364447D4468FD6853E827C3CC7891A26369165D5A0F4`
- **源码构建历史安装包（上一版构建产物 v2.3.7）**：
  - 文件名：`dist/PAW_Personal_Work_OS_Setup.exe`
  - 文件大小：26,480,432 字节（约 **25.25 MB**）
  - SHA256 哈希：`4ED4952E07CF2B36FBED4D3B100A9F45EE6FDB31D74FE72EF6E92496324F5921`
- **打包内部核心执行文件**：
  - 文件名：`dist/PAW_Personal_Work_OS/PAW_Personal_Work_OS.exe`
  - 文件大小：6,242,304 字节（约 **5.95 MB**）
  - SHA256 哈希：`A485296428C220E18E95F727FCDD5501D5D17C007BE06636784260E73AED9AF0`

---

## 5. 软件在电脑上存放数据的所有位置（测试清空必查）
软件运行和测试时，**程序代码与业务数据完全物理隔离**。所有需要清空或恢复出厂状态的位置如下：

1. **软件安装程序目录**：
   - 路径：`%LOCALAPPDATA%\Programs\PAW Personal Work OS`（普通用户安装）或 `C:\Program Files\PAW Personal Work OS`（管理员安装）。
2. **业务与运行数据主目录**（核心数据区）：
   - 路径：`%LOCALAPPDATA%\PAW\`（即 `C:\Users\<用户名>\AppData\Local\PAW\`）
   - 包含文件与子文件夹：
     - `workspace.db`：主业务数据库（所有项目、文档解析结果、审核条目均在此）。
     - `workspace_pre_update.db`：软件升级安装时自动留存的快照。
     - `config.json`：软件配置文件（包含经 DPAPI 加密保存的模型密钥）。
     - `data_dir_pointer.txt`：若用户迁移过数据目录，该文件记录真实数据路径。
     - `sources/`：监控和缓存的原始文件目录。
     - `evidence/`：提炼出的事实证据切片文件。
     - `backups/`：运行中周期性生成的 SQLite 自动备份。
     - `logs/`：运行时输出的所有日志。
     - `data/`：向下兼容数据子目录（含次级 `workspace.db`）。
3. **注册表记录**：
   - 当前用户卸载项：`HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{E6F7A9D2-3B4C-5D6E-7F8A-9B0C1D2E3F4A}_is1`
   - 全机安装卸载项：`HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\{E6F7A9D2-3B4C-5D6E-7F8A-9B0C1D2E3F4A}_is1`
4. **系统快捷方式**：
   - 桌面快捷方式：`%USERPROFILE%\Desktop\PAW Personal Work OS.lnk`
   - 开始菜单项：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\PAW Personal Work OS\`
   - 开机启动项（若开启）：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PAW Personal Work OS.lnk`
5. **Windows DPAPI 密钥**：
   - 用户填入的 API Key 不在 Windows 凭据管理器明文建项，而是通过 Windows DPAPI (`CryptProtectData`) 加密后密文写入 `config.json`，绑定当前 Windows 账号。
6. **网络端口**：
   - 仅绑定回环端口 `127.0.0.1:8800`（若被占用顺延至 8801-8805），无公网开放端口。
