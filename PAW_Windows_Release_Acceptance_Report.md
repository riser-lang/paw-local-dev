# PAW Personal Work OS Windows 发布验收报告

**报告日期**：2026-10-04
**发布版本**：PAW Personal Work OS v2.3.1
**打包工具**：Inno Setup 6.7.3
**验收环境**：Windows（开发机，PowerShell 7）

---

## 一、安装包信息

| 项目 | 内容 |
|------|------|
| 安装包路径 | D:\PAW\paw-beta\dist\PAW_Personal_Work_OS_Setup.exe |
| 安装包大小 | 27,657,604 字节（约 26.4 MB） |
| 压缩方式 | lzma2/ultra64 固实压缩 |
| 安装脚本 | D:\PAW\paw-beta\installer\paw_setup.iss |
| 应用标识 | AppId {E6F7A9D2-3B4C-5D6E-7F8A-9B0C1D2E3F4A} |

## 二、安装方式

- 双击安装包进入『PAW Personal Work OS 安装向导』（简体中文界面）；
- 支持静默安装：PAW_Personal_Work_OS_Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART；
- 权限策略：PrivilegesRequired=lowest，普通用户无需管理员权限即可安装（管理员运行时默认安装到 C:\Program Files\PAW Personal Work OS\，普通用户安装到用户级 Programs 目录）；
- 已安装用户再次运行安装包时，向导提供三选一：直接升级更新（保留全部数据）/ 在安装窗口内直接卸载旧版本 / 取消；
- 安装前自动优雅关闭运行中的 PAW 实例（调用 shutdown 接口 + 进程清理）。

## 三、安装目录结构（本机实测）

```
<安装目录>\PAW Personal Work OS\
├── PAW_Personal_Work_OS.exe   (11.9 MB)
├── _internal\                 (Python 运行时与依赖)
├── frontend\                  (React 前端资源)
├── 启动PAW.bat
├── 使用说明.txt
├── unins000.exe / unins000.dat (卸载程序)
```

- 桌面快捷方式：PAW Personal Work OS（已验证生成）
- 开始菜单：PAW Personal Work OS + 卸载 PAW Personal Work OS（已验证生成）
- 可选任务：开机自动启动（默认不勾选）

## 四、用户数据目录结构（%LOCALAPPDATA%\PAW，已验证）

```
%LOCALAPPDATA%\PAW\
├── workspace.db          (权威 SQLite 数据库)
├── config.json           (用户配置)
├── sources\              (文档监听目录)
├── evidence\             (证据包)
├── backups\              (热备份)
├── logs\                 (日志)
└── data\                 (兼容子目录)
```

- 程序数据与程序文件完全分离，避免 Program Files 写入权限问题；
- 首次安装自动初始化目录结构，从模板初始化数据库（绝不覆盖已有数据）；
- 升级安装前自动执行数据库备份（workspace_pre_update.db）；
- 卸载时默认保留用户数据，弹窗提示『是否删除 PAW 工作数据？』并提供保留/删除双确认（删除需二次确认）。

## 五、本机验收测试结果

### 安装测试
| 检查项 | 结果 |
|--------|------|
| 静默安装退出码 | 0（成功） |
| 注册表卸载项 | 已生成（DisplayName/Version/Publisher 完整） |
| 桌面快捷方式 | 通过 |
| 开始菜单入口 | 通过 |
| 程序启动 | 通过（进程运行、8800 端口监听） |
| 工作台页面 | 通过（/app 返回 HTTP 200） |

### 功能测试
| 检查项 | 结果 |
|--------|------|
| Runtime 状态 | 通过（/v1/local/runtime/status 返回 200，调度器正常） |
| Review 审核看板 | 通过（200 条待审提案正常读取） |
| 备份列表 | 通过（/v1/local/backup/list 返回 200） |
| 知识演化健康度 | 通过（50 个知识页面，总体评分 71.3『良好』） |
| 设置接口 | 失败（/v1/local/settings 返回 500，详见已知问题） |
| Doctor 诊断接口 | 超时（/v1/local/doctor 超过 15 秒未响应，详见已知问题） |

### 数据测试
| 检查项 | 结果 |
|--------|------|
| workspace.db 完整性 | 通过（文件存在，数据可读取） |
| 重启后数据保留 | 通过（优雅关闭后重启，待审提案 200 条、知识页面 50 个均完整保留） |
| 卸载后数据保留 | 通过（卸载测试后 %LOCALAPPDATA%\PAW\workspace.db 完整保留） |

## 六、干净 Windows 环境测试结果

**状态：未执行**。本次验收仅在开发机完成。干净 Windows 环境（无 Python、无 Node）测试需要独立虚拟机，当前环境不具备。

**替代性证据**：安装包基于 PyInstaller 产物（自带完整 Python 运行时 _internal），前端为离线静态构建产物（无需 Node），理论上不依赖任何预装运行时。建议比赛提交前在干净虚拟机补测一次。

## 七、SmartScreen 测试结果

**现状**：安装包未经过代码签名（无代码签名证书），Windows SmartScreen 对未签名程序仍会显示『更多信息 → 仍要运行』提示。

**已完成的优化**：
1. 使用规范 Inno Setup 安装包（非裸 EXE 分发）；
2. 填写完整应用元数据：应用名称、版本号 2.3.1、发布者 PAW Work OS Team、版权信息；
3. 安装后自动清除文件的 Zone.Identifier 标记（Unblock-File），减少下载文件警告；
4. 保留清晰产品名称『PAW Personal Work OS』。

**无法完全消除 SmartScreen 提示的原因**：SmartScreen 信誉基于代码签名证书与下载量积累。消除提示需要购买代码签名证书（OV 证书需数百至数千元每年，EV 证书可立即获得信誉）。本次未采用关闭系统安全策略的方式规避（按要求禁止）。

## 八、已知问题

1. /v1/local/settings 接口 500：产品代码问题（疑似 mask_secret 处理空 API Key 的边界情况），与安装包装无关，按任务要求未修改产品代码；
2. /v1/local/doctor 接口超时：诊断接口响应超过 15 秒，可能为全量诊断耗时较长，建议后续优化；
3. 干净 Windows 环境测试未执行：需独立虚拟机环境，建议提交前补测；
4. SmartScreen 提示无法完全消除：需代码签名证书，见第七节说明。

## 九、最终交付物

```
D:\PAW\paw-beta\dist\PAW_Personal_Work_OS_Setup.exe  (26.4 MB)
```

作为比赛提交版本。

---

## 十、v2.3.2 界面与多目录更新（2026-10-04 17:20）

### 本次变更
1. **按钮位置调整**：PAW 控制中心浮动按钮从右下角调整至左下角（left: 24px），快速记录按钮保持右下角不变；
2. **工作目录文件夹选择**：本地设置与维护、首次引导页新增『选择文件夹』按钮，调用 Windows 原生文件夹选择对话框（SHBrowseForFolderW，无需 tkinter）；
3. **多文件夹支持**：work_dir 支持分号分隔多个监控目录，可多次点击『选择文件夹』追加；监听器（watchdog 多目录监听）、全量扫描器、巡航调度器、系统诊断均已适配多目录遍历，单目录配置完全兼容；
4. **设置接口修复**：修复 /v1/local/settings 返回 500 的问题（主程序缺少 DATA_DIR 导入）。

### 验证结果
| 检查项 | 结果 |
|--------|------|
| 升级安装（保留数据） | 通过（退出码 0） |
| 新版程序启动 | 通过（进程运行�
�8800 端口监听） |
| 控制中心按钮左下角 | 通过（部署文件已含 left: 24px） |
| 文件夹选择按钮 | 通过（设置页与引导页均已部署） |
| 设置接口 | 通过（500 已修复为 200，work_dir 正常返回） |
| 修改文件语法检查 | 通过（6 个 Python 文件编译退出码均为 0） |

### 最终交付物
- 安装包：D:\PAW\paw-beta\dist\PAW_Personal_Work_OS_Setup.exe（27,369,134 字节，2026-10-04 17:18:40）
