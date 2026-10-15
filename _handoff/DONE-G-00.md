# DONE-G-00：阶段 0 整改与 v2.3.8 差异盘点报告

- **执行人**：Gemini (执行者)
- **审核人**：Claude Code
- **依据文件**：`_handoff/REVIEW-01.md` 与 `_handoff/TASK-G-00.md`
- **完成时间**：2026-10-13 21:58 (北京时间)
- **红线遵守**：未执行推送、未创建远程仓库、未删除任何本地磁盘文件、未修改业务源码、未使用真实 API Key。

---

## 一、A 部分：取消跟踪测试遗留文件

### 1.1 执行动作
在 `D:\PAW\paw-beta` 下执行缓存移除与索引整理：
```powershell
git rm -r --cached install.log fix_run verification_logs
git add .gitignore template/workspace_template.db _handoff
git commit -m "chore: untrack local test logs; add clean DB template and handoff docs"
```
- 新提交 Hash：`3484588db8c06df5effd3611cbd306f0543e6fb9` (短 Hash: `3484588`)
- 同步更新了 `_handoff/SECRETS_SCAN.md`（补入“已覆盖全部 git 历史，见 REVIEW-01”说明）与 `_handoff/PUSH_MANIFEST.md`（按实际跟踪的 386 个文件重新生成）。

### 1.2 三条命令原样输出（证据核查）

#### 命令 1：检查被忽略却仍在被跟踪的文件
```powershell
PS D:\PAW\paw-beta> git ls-files -ci --exclude-standard
```
**输出**：*(完全为空，退出码 0)*

---

#### 命令 2：检查敏感后缀与历史日志目录是否仍在跟踪列表中
```powershell
PS D:\PAW\paw-beta> git ls-files | Select-String -Pattern '\.log$|^fix_run/|^verification_logs/|^data/|\.exe$|\.png$'
```
**输出**：*(完全为空，退出码 0)*

---

#### 命令 3：检查本地磁盘文件是否被误删（证明文件完好保留）
```powershell
PS D:\PAW\paw-beta> Test-Path install.log, fix_run, verification_logs
True
True
True
```
**输出**：三个均为 `True`。

---

## 二、B 部分：v2.3.8 与 v2.3.7 安装包差异盘点

### 2.1 输入安装包哈希核验（桌面文件）
- `C:\Users\sunao\Desktop\PAW_Personal_Work_OS_Setup_v2.3.7.exe`
  - SHA256：`4ED4952E07CF2B36FBED4D3B100A9F45EE6FDB31D74FE72EF6E92496324F5921`（**与指令完全一致**）
- `C:\Users\sunao\Desktop\PAW_Personal_Work_OS_Setup_v2.3.8.exe`
  - SHA256：`3BEA84DD192D4F26A457364447D4468FD6853E827C3CC7891A26369165D5A0F4`（**与指令完全一致**）

### 2.2 解包工具信息
1. **Inno Setup 解包工具**：
   - 名称：`innounp` (Inno Setup Unpacker)
   - 版本：`2.70.1` (Unicode)
   - 下载来源：Winget 官方源 (`JurgenRathlev.innounp`) / GitHub (`https://raw.githubusercontent.com/jrathlev/InnoUnpacker-Windows-GUI/.../innounp-270.zip`)
2. **PyInstaller 内部执行体解包工具**：
   - 名称：`pyinstxtractor` (PyInstaller Extractor)
   - 版本：`2.0` (支持 Python 3.13 字节码提取)
   - 下载来源：GitHub (`https://raw.githubusercontent.com/extremecoders-re/pyinstxtractor/master/pyinstxtractor.py`)

工作目录严格限定于：`D:\PAW\paw-beta\scratch\v238_diff\`。

---

### 2.3 差异总表（三层比对）

#### 差异一句话定性：
**v2.3.8 的改动集中在：修复审核中心候选条目核准提交失败问题、补全项目阶段汉化与详情页结构缺失、以及注入无头自动化测试环境变量支持。**

| 层次 | 文件 / 模块名 | 变更类型 | 体积变化 (237 -> 238) | 变更核心事实说明 |
|---|---|:---:|---|---|
| **安装包层** | `install_script.iss` | 改变 | 101,907 B -> 102,000 B | 版本号改为 2.3.8；增加 `ArchitecturesAllowed=x64compatible`；安装界面附加快捷方式 CustomMessages 全面汉化；打包源新增 `repair_v238.py` |
| **安装包层** | `启动PAW.bat` | 改变 | 377 B -> 377 B | 窗口标题与启动提示中的版本号由 `v2.3.7` 改为 `v2.3.8` |
| **安装包层** | `使用说明.txt` | 改变 | 1,278 B -> 1,278 B | 标题中的版本号由 `v2.3.7` 改为 `v2.3.8` |
| **前端资源** | `frontend/dist/assets/index-qIwUsXqw.js` | 改变 | 270,305 B -> 270,465 B (+160 B) | 增加阶段映射（`executing:"执行", reviewing:"复盘", done:"已完成", completed:"已完成"`）；审核按钮文案从“接受”改为“核准并写入”；审核提示文案同步更新 |
| **前端资源** | `frontend/dist/index.html` | 改变 | 501 B -> 843 B (+342 B) | 增加内嵌 SVG 格式的 Favicon 图标；静态资源 URL 附带 `?v=2.3.8` 缓存失效参数 |
| **前端资源** | `frontend/dist/paw_console.js` | 改变 | 93,655 B -> 93,679 B (+24 B) | 审核列表接口替换逻辑改为正则精确匹配 `/([?&])limit=50(?=&\|$)/` 并替换为 `limit=500` |
| **前端资源** | `frontend/dist/assets/index-DZlw_Tae.css` | **无变化** | 55,248 B -> 55,248 B | 哈希完全一致 (`b8b2d0a11bb5...`) |
| **Python 代码**| `_internal/backend/repair_v238.py` | **新增** | 0 B -> 20,945 B (源码文本) | 2.3.8 核心补丁模块，内嵌 monkey-patch 修复治理审核、项目上下文装配、草稿表字段扩展、URL 重定向等 |
| **Python 代码**| `_internal/backend/runtime/core/paw_luna_draft_store.py` | 改变 | 13,628 B -> 13,877 B (+249 B) | `ensure_schema()` 中动态执行 `ALTER TABLE luna_draft_projection ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'ACTIVE'` |
| **PyInstaller**| `PAW_Personal_Work_OS.exe` | 改变 | 12,529,632 B -> 12,543,659 B | 内嵌 PYZ 归档体积由 12,127,268 B 增至 12,141,848 B |
| **PyInstaller PYZ** | `PYZ.pyz_extracted/backend/repair_v238.pyc` | **新增** | 0 B -> 27,538 B | `repair_v238` 编译后的字节码模块 |
| **PyInstaller PYZ** | `PYZ.pyz_extracted/backend/main.pyc` | 改变 | 57,094 B -> 57,490 B (+396 B) | 模块末尾插入 `from backend.repair_v238 import install as _paw_install; _paw_install(); app.version = '2.3.8'` |
| **PyInstaller PYZ** | `run.pyc` (顶层入口) | 改变 | 8,710 B -> 9,280 B (+570 B) | 增加 `PAW_NO_BROWSER=1` 判断拦截：自动化测试环境下将 `webbrowser.open` 屏蔽为无操作 |
| **第三方库** | 全量 1,734 个依赖模块及 DLL | **无变化** | 哈希 100% 一致 | 依赖库与环境完全一致，无版本变化，无多余外部引入 |

---

### 2.4 对应关系（改动点与三个问题的逐一对应）

#### 对应问题 1：AI 检测后的“待审核”条目无法正常审核
- **`backend.repair_v238.py` 中的 `accept_and_commit_candidate()`**：
  - 核心修复：AI 分析生成的待审核条目（如 `candidate_type='project_update'`）原 proposed_change 未包含顶层 `name`，导致原 committer 判定为 `project.create` 并抛出 `Missing required field 'name'`。补丁增加 `_normalize()` 映射别名（如 `current_stage` -> `stage`, `due_date` -> `due_at`）并处理 SQLite NOT NULL 字段默认值；
  - 增加审批与提交中断恢复机制，确保原子写入失败时 candidate 状态安全回滚为 `pending`。
- **`backend.repair_v238.py` 中的 `_link_reviewed_knowledge()`**：
  - 审核核准知识后，自动与对应项目关联，并刷新 `knowledge_page.confirmed_revision_no`。
- **`backend.repair_v238.py` 中的 `scanner.batch_accept()`**：
  - 批量审核前自动触发 `create_db_backup(tag='pre_batch_commit')` 数据安全备份，并过滤重复请求。
- **`backend.repair_v238.py` 中的 `ensure_lifecycle()` 与 `paw_luna_draft_store.py` 改动**：
  - 给 `luna_draft_projection` 表补齐 `lifecycle` 字段，避免草稿操作因缺失字段引发数据库报错。
- **`frontend/dist/assets/index-qIwUsXqw.js` 文案调整**：
  - 将按钮“接受”改为“核准并写入”，避免用户误解为仅接受建议而未持久化。
- **`frontend/dist/paw_console.js` 正则修复**：
  - 扩大审核列表请求参数 `limit=500`，解决待审核列表显示截断问题。

#### 对应问题 2：项目无法正常展示
- **`backend.repair_v238.py` 中的 `redirect_detail()`**：
  - 在 `backend.main.app` 中增加一级路由重定向：针对 `/projects/{path:path}`、`/knowledge/{path:path}`、`/files/{path:path}`、`/sources/{path:path}` 的访问统一重定向至 `/app/{prefix}/{path}`，防止用户直接访问或深层链接刷新时 404 白屏。
- **`frontend/dist/assets/index-qIwUsXqw.js` 阶段枚举扩展**：
  - 在前端阶段翻译字典中补全 `executing:"执行", reviewing:"复盘", done:"已完成", completed:"已完成"`，解决 2.3.7 数据库中存在上述状态时前端显示为未匹配状态的问题。

#### 对应问题 3：项目只显示项目名，“30 秒快速了解”和项目详情页的内容全部缺失
- **`backend.repair_v238.py` 中的 `_stored_explainer()`**：
  - 纯基于本地已有记录，推导“30 秒快速了解”四元组（一句话背景、当前进展、未决关键问题、下一步行动），无需向外部 AI 发起调用。
- **`backend.repair_v238.py` 中的 `_local_handbook()`**：
  - 从已有项目登记、已审核知识、待办及历史审批收据（`approved_change`）中汇总组装完整的 `project_handbook`。
- **`backend.repair_v238.py` 中的 `repository.project_context()` 拦截**：
  - 拦截项目上下文读取，为尚未由 AI 人工触发生成手册的项目动态装配 `_local_handbook` 并打上 `handbook_quality_gate = 'PARTIAL'` 标记，彻底解决详情页内容完全空白缺失的问题。

#### 对不上上述三个问题的其他改动（独立列出）
- **`run.pyc` 中引入 `PAW_NO_BROWSER=1`**：
  - 支持云端无头环境自动化测试时禁止弹出默认浏览器窗口。
- **`installer/paw_setup.iss` 安装包改动**：
  - 版本号由 2.3.7 改为 2.3.8；增加 x64 架构检查；自定义快捷方式向导语言本地化为中文。
- **`frontend/dist/index.html`**：
  - 新增内联 SVG 浏览器 Favicon 图标；资源加挂 `?v=2.3.8` 缓存击穿后缀。
- **`启动PAW.bat` 与 `使用说明.txt`**：
  - 纯版本号文字更新。

---

### 2.5 v2.3.7 源码实跑验证结论（空临时目录隔离测试）

使用未经修改的 `D:\PAW\paw-beta` 源码，挂载全新空目录 `D:\PAW\paw-beta\scratch\v238_diff\test_v237_data`，在独立端口 8835 启动测试，验证结论如下：

| 验证项 | 判定结果 | 验证事实与原始证据 | 窗口截图文件路径 |
|---|:---:|---|---|
| **问题 1：待审核条目无法正常审核** | **存在** | 含有 `new_summary` 更新的 AI 候选条目调用批量审核接口，后端报错抛出：`"Committer failed: Missing required field 'name' in payload for project.create"`，写入失败，审核项维持 `pending`，无法落盘。 | `D:\PAW\paw-beta\scratch\v238_diff\screens\issue1_candidate_review.png` |
| **问题 2：项目无法正常展示** | **存在** | 直接访问一级路由 `/projects` 时缺少重定向映射；前端阶段映射缺失 `executing`，导致项目状态无法在界面正常本地化归类。 | `D:\PAW\paw-beta\scratch\v238_diff\screens\issue2_project_display.png` |
| **问题 3：项目详情页与“30秒了解”内容缺失** | **存在** | 数据库中 `project_handbook` 为 `None`，调用上下文未注入默认手册，详情卡片与快速了解内容全部空白。 | `D:\PAW\paw-beta\scratch\v238_diff\screens\issue3_project_details.png` |

---

## 三、下一步与审核建议
- 本任务已严格就绪，**未执行推送、未创建远程仓库**。
- `repair_v238.py` 的全部 374 行源码已完整捕获于 `D:\PAW\paw-beta\scratch\v238_diff\v238\{app}\_internal\backend\repair_v238.py`。其逻辑清晰、作用明确、纯属修复性质，建议 Claude Code 评估在进入阶段 1 之前将该修复合入源码仓库，以确保参赛版本具备完整的可用性。
