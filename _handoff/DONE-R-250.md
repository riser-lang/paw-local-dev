# DONE-R-250 — 准备 v2.5.0-rc1（参赛候选版本）

- 执行者：DeepSeek v4.1 flash ｜ 审核人：Claude Code
- 分支：`gemini/R-250`，PR **#13**（合入 main）
- 本报告对应头部提交 **`7b23689`**（已按 REVIEW-C-11 的 R-250 一节改完 6 项，**并已合并 main、补上第 3 条"修复"**）
- **已 `git merge origin/main`**（把已合并的 C-11 带进来），合并无冲突
- **未打 tag，未建 Release**（按指示由审核人来做）

---

## 0. 按 REVIEW 的六项修改（本轮）

| # | 审核人要求 | 处理 | 证据 |
|---|---|---|---|
| 1 | 版本说明"修复"只保留有负对照证明的两条，删掉 3–6 | **已改** | 本轮为两条，**C-11 合并后按要求补上第 3 条**（见第 0.5 节），现在共 3 条，每条都对上一个负对照 |
| 2 | "新增 10"改为搜索特殊字符不出错 | **已改** | 现为「搜索词里含双引号、括号、星号、冒号、连字符等特殊字符时不再出错。」 |
| 3 | 删掉"重装 2.3.9 回退"，改为写备份目录并附出处 | **已改** | 见第 0.1 节 |
| 4 | 已知问题新增【资料】页 200 份上限 | **已改** | 见第 0.2 节 |
| 5 | 把 `docs/使用说明.txt` 原样装进安装目录 + 开始菜单"使用说明"快捷方式 + 新增安装检查 | **已改** | 见第 0.3 节；B1-01 的三项新检查在云端实测为 `true` |
| 6 | 等 C-11 合并后再 `git merge origin/main` 重跑 | **已做** | 见第 0.5 节：合并无冲突，push 与 PR 两条流水线各自 GREEN |

### 0.1 备份目录（以实际代码为准，附出处）

初稿写的是 `%LOCALAPPDATA%\PAW\backups`。核对代码后发现**覆盖安装时的备份并不在那个目录**，因此按"以实际为准"改写：

| 备份 | 实际位置 | 出处 |
|---|---|---|
| 覆盖安装前自动备份 | `%LOCALAPPDATA%\PAW\workspace_pre_update.db`（或同目录 `snapshots\*.db`） | `installer/paw_setup.iss` 的 `BackupDatabaseBeforeInstall()`：`BackupFile := DataDir + '\workspace_pre_update.db'; CopyFile(DbFile, BackupFile, False);`，其中 `DataDir := ExpandConstant('{localappdata}\PAW')`；由 `CurStepChanged` 在 `ssInstall` 调用。端到端证据：`tests/run_e2e_tests.py` 的 B8-02 检查 `upgrade_snapshot_exists` 就是找这个文件或 `snapshots\*.db` |
| 程序日常自动备份 | `%LOCALAPPDATA%\PAW\backups\workspace_backup_<时间>_<tag>_<随机码>.db` | `backend/core/config.py`：`DATA_DIR = Path(local_app_data) / "PAW"`、`BACKUPS_DIR = DATA_DIR / "backups"`；`backend/pm/backups.py`：`name='workspace_backup_'+...` |

版本说明因此写成"覆盖安装前备份到 `workspace_pre_update.db`（也可能在 `snapshots` 里），日常自动备份另放 `backups`"，两个都点名，读者任一条都能找到。

### 0.2 【资料】页 200 份上限

- 接口默认只取 200 条：`backend/workspace_api/router.py:524` —— `def sources(limit: int = Query(200, ge=1, le=500)):`。
- 前端搜索框只在这批数据里做本地筛选：主界面包里【资料】页的搜索框 placeholder 为「搜索文件名或分类」，列表由已取回的 `sources()` 结果 `.filter(...)` 得到（打包文件 `frontend/dist/assets/index-qIwUsXqw.js`，已核对含「搜索文件名或分类」「资料索引」「没有符合条件的资料」）。
- 版本说明据此写成已知问题，并提示资料多时用【AI助手】或【知识】页搜索。

### 0.3 把完整使用说明装进安装目录

| 改动 | 文件 | 说明 |
|---|---|---|
| 新增 [Files] 条目 | `installer/paw_setup.iss` | `Source: "..\docs\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion` —— 从 `docs/` 原样复制进安装目录 |
| 新增开始菜单快捷方式 | `installer/paw_setup.iss` | `Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"` |
| 删掉 12 行短版 | `scripts/package_app.py` | 不再向 `dist/` 写自动生成的短版，并删除可能残留的旧文件；这样只剩 `docs/使用说明.txt` 一份要维护 |
| 新增安装检查（3 项） | `tests/run_e2e_tests.py` 的 B1-01 | `installed_guide_exists`、`installed_guide_matches_docs`（逐字节比对 `docs/使用说明.txt`）、`start_menu_guide_shortcut_exists`。**加在原有检查之后，原有 4 项检查一行未改** |
| 新增静态守卫（6 项） | `tests/unit/test_release_docs.py` | 断言 iss 确实从 `docs/` 取文件、有 `{group}\使用说明` 图标、短版已移除、两份文档都是 UTF-8 带 BOM、版本说明只保留两条有据的"修复"、且写了正确备份路径与 200 上限 |

云端实测（运行 39018709517 的 `report_B1.json`，B1-01）：

```
installer_exit_code             = 0        (原有)
installed_during_this_case      = true     (原有)
desktop_shortcut_exists         = true     (原有)
start_menu_shortcut_exists      = true     (原有)
installed_guide_exists          = true     (新增)
installed_guide_matches_docs    = true     (新增)
start_menu_guide_shortcut_exists= true     (新增)
```

---

## 0.4 与 C-11 的关系（重要）

按指示，本任务**只做 R-250**：

- 本分支从 **main** 拉出，**不以 C-11 为基础**。
- C-11 由 Codex 在 `D:\PAW\paw-dev-codex` 并行完成，**我没有碰那个目录**。
- 版本说明「搜索词里含双引号、括号、星号、冒号、连字符等特殊字符时不再出错」一句**已按审核人给的措辞写入**，但当时该修复的代码**不在本 PR 内**，需由审核人在 C-11 合并后确认。
- 我先前在 `gemini/C-11` 上的部分工作已停止、提交并推送（见第 6 节）。审核人已裁定 **PR #11 关闭、不合并**。
- **C-11 现已合并进 main（PR #12，`_handoff/REVIEW-C-11.md` 已批准），本轮已把它合并进本分支，上一段的前提已消解。**

---

## 0.5 合并 main 与补第 3 条"修复"（本轮新增）

### 0.5.1 合并过程

`git fetch origin` 后 `git merge --no-edit origin/main`（快进以外的正常合并，**未 rebase、未 force push**）：

```
Merge made by the 'ort' strategy.
 _handoff/DONE-C-11.md                             | 289 ++++++++++++++++++++++++
 _handoff/REVIEW-C-11.md                           |  57 +++++
 _handoff/STATUS.md                                |   4 +-
 backend/runtime/core/paw_retrieval_core.py        |  81 ++++--
 backend/runtime_core/scanner.py                   |  36 +++
 backend/workspace_api/repository.py               |   6 +-
 tests/pending/required_cases_C-11.json            |  11 -
 tests/required_cases_C-11.json                    |  11 +
 tests/run_pm_health_tests.py                      |   2 +-
 tests/run_pm_search_tests.py                      | 157 +++++++++++
 tests/run_pm_tests.py                             |   2 +-
 tests/unit/test_retrieval_search_special_chars.py |  77 ++++++++++
 tests/unit/test_scanner_source_registry.py        |  59 ++++++
```

**没有任何冲突**，因此"冲突时以 main 的产品代码为准、只保留我的 `docs/`、`installer/`、`scripts/package_app.py` 和版本号改动"这条规则**没有触发**（无冲突可处理）。合并后本分支相对 main 的差异只剩我的 R-250 改动：

```
.github/workflows/build.yml          (版本号)
.github/workflows/test.yml           (版本号)
_handoff/DONE-R-250.md               (本报告)
audit_check.py                       (版本号)
backend/brain/context/paw_product_guide.py (版本号)
backend/main.py                      (版本号)
docs/使用说明.txt                     (重写)
docs/版本说明.txt                     (新增)
frontend/dist/index.html             (版本号 ?v=2.5.0)
installer/LICENSE.txt                (版本号)
installer/paw_setup.iss              (版本号 + 安装说明 + 快捷方式)
paw_desktop.spec                     (版本号)
run.py                               (版本号)
scripts/build_windows_dist.py        (版本号)
scripts/generate_fixtures.py         (版本号)
scripts/package_app.py               (删短版说明)
tests/run_e2e_tests.py               (B1-01 追加 3 项检查)
tests/unit/test_release_docs.py      (新增静态守卫)
```

产品代码文件（`backend/runtime/core/paw_retrieval_core.py`、`backend/runtime_core/scanner.py`、`backend/workspace_api/repository.py`、`tests/run_pm_search_tests.py` 等）**全部来自 main，我一个字节都没改**——这正好符合"产品代码以 main 为准"。

### 0.5.2 补第 3 条"修复"及其依据

按要求在 `docs/版本说明.txt` 的"二、修复"下新增：

```
3. 扫描导入的文件现在会出现在【资料】页，并能按完整文件名（含扩展名）搜索到。
```

依据是 main 上 C-11 的负对照 **NC-PM-S1**（`tests/required_cases_C-11.json`）：

```json
"NC-PM-S1": {"installer": "v2.3.9-baseline", "runs": "PM-S1-01", "expect_failed_check": "scanned_file_listed_in_sources"}
```

即：2.3.9 基线在 `scanned_file_listed_in_sources` 这一项上必须**失败**；而 2.5.0 的 PM-S1-01 在同一项上通过（`tests/run_pm_search_tests.py:100` `c.check("scanned_file_listed_in_sources", found, True)`，紧接着 101 行才是完整文件名搜索）。所以"出现在【资料】页"和"按完整文件名搜得到"两件事都有负对照背书，措辞与检查项一一对应。

顺带把静态守卫从"恰好 2 条"改成"恰好 3 条"，并**新增一条测试把每条"修复"绑到具体负对照**（`test_version_notes_fix_items_match_shipped_negative_controls`）：如果哪条负对照以后从 `required_cases*.json` 里消失，"修复"条数就和负对照条数对不上，测试直接红，杜绝"修复"条数悄悄长于证据。

### 0.5.3 核对 Q5 那句话

使用说明 Q5「资料归错了项目怎么办？」现有句：

```
撤销不删资料，原始资料一直留在【资料】里可查。
```

这句话在 C-11 合并前**其实不成立**（扫描导入的文件根本进不了【资料】页，见 NC-PM-S1 的 2.3.9 基线表现）。C-11 合并后，扫描文件会进【资料】页，这句话**现在成立**，因此**保留原句、未改动**。`git diff` 确认使用说明在本轮**一行未改**。

---

## 1. 本任务子项逐条对照

> 本轮按 REVIEW 追加的 6 项修改单列在第 0 节，下表是 TASK-R-250 原始子项。

| TASK-R-250 子项 | 状态 | 说明 |
|---|---|---|
| §1 所有版本号改为 2.5.0 | **完成** | 见第 2 节；`audit_check.py` CHECK 1 通过 |
| §1 安装包名 `..._Setup_v2.5.0.exe` | **完成** | CI 构建产物已确认（见 5.1） |
| §2 使用说明：安装包文件名改 v2.5.0 | **完成** | |
| §2 第一次打开介绍【驾驶舱】四块 + 右上角【系统状态】 | **完成** | |
| §2 五分钟路线按要求改为 5 步 | **完成** | 填 Key → 监控文件夹+全量扫描（含"等一两分钟"）→ 驾驶舱处理 1–2 条 → 项目档案页看栏目/来源/最近变化+试撤销 → 问"这个项目后续怎么优化" |
| §2 新增"系统会自动做什么、什么一定交给你" | **完成** | |
| §2 新增"周报和指标"（含未填 Key 提示） | **完成** | |
| §2 常见问题新增两条 | **完成** | 资料归错了项目 / 为什么有的建议被删掉 |
| §2 每个界面词照抄实际文字 + 出处表 | **完成** | 见第 3 节 |
| §2 UTF-8 带 BOM、3 页以内 | **完成** | BOM 已核（EF BB BF）；120 行 |
| §3 新建 `docs/版本说明.txt`，UTF-8 带 BOM，一页以内 | **完成** | BOM 已核；44 行 |
| §3 只写有测试证据的内容 | **完成** | "修复"三条各有负对照（NC-PM-GOV / NC-PM-B2 / NC-PM-S1），见第 4 节 |
| §4 打 tag / 建 Release | **未做（按要求）** | 由审核人来做；本轮未打 tag（已核 `git tag -l "v2.5.0*"` 为空） |
| 不修改 `_handoff/STATUS.md` | **遵守** | `git status _handoff/STATUS.md` 为空 |
| 不删文件、不改判定规则与考题 | **遵守** | 本 PR 未触及 `tests/paw_*.py`、`required_cases*.json`、`tests/pending/` |

---

## 2. 版本号统一为 2.5.0

### 2.1 改动的文件

| 文件 | 改动 |
|---|---|
| `run.py` | 启动横幅 `v2.5.0` |
| `backend/main.py` | 文件头注释、`FastAPI(version="2.5.0")`、`app.version = "2.5.0"` |
| `backend/brain/context/paw_product_guide.py` | `GUIDE_VERSION = "2.5.0"` |
| `paw_desktop.spec` | PyInstaller 注释版本 |
| `installer/paw_setup.iss` | `MyAppVersion`、`OutputBaseFilename=PAW_Personal_Work_OS_Setup_v2.5.0`、`VersionInfoVersion=2.5.0.0`、`VersionInfoProductVersion=2.5.0.0` |
| `installer/LICENSE.txt` | 许可头 `(v2.5.0)` |
| `scripts/package_app.py` | 横幅、bat、readme 标题、安装包名（2 处） |
| `scripts/build_windows_dist.py` | 横幅、bat、readme、桌面安装包名 |
| `audit_check.py` | 自身标题与目标版本 |
| `scripts/generate_fixtures.py` | 夹具文档标题 |
| `frontend/dist/index.html` | 缓存版本号 `?v=2.5.0`（2 处） |
| `.github/workflows/test.yml`、`build.yml` | 安装包名与产物名 |

`repair_v238` 模块名**未改**。

### 2.2 验证

```
$ python audit_check.py
CHECK 1: Version Uniformity Check (Target: v2.5.0)
  [OK] run.py: Contains target version 2.5.0
  [OK] backend/main.py: Contains target version 2.5.0
  [OK] paw_desktop.spec: Contains target version 2.5.0
  [OK] installer/LICENSE.txt: Contains target version 2.5.0
  [OK] installer/paw_setup.iss: Contains target version 2.5.0
>> RESULT: [PASS] Version Uniformity verified across all entrypoints and configurations.
```

全仓 `git grep -n "2\.4\.0"` 除 `_handoff/` 历史文档外**无命中**。

CI 构建日志确认安装包名（运行 39009257327）：

```
D:\a\paw-local-dev\paw-local-dev\dist\PAW_Personal_Work_OS_Setup_v2.5.0.exe
$exe = "dist/PAW_Personal_Work_OS_Setup_v2.5.0.exe"
"$hash  PAW_Personal_Work_OS_Setup_v2.5.0.exe" | Out-File ... .exe.sha256
```

---

## 3. 使用说明的界面词出处表

`docs/使用说明.txt` 里凡是加【】或引号的界面词，都取自产品实际文字。下表给出文件与原文片段（行号为该文件当前 HEAD 的行号）。

| 界面词 | 出处文件 | 原文片段 |
|---|---|---|
| 驾驶舱 | `frontend/pm-src/index.html`（L3）、`frontend/pm-src/nav.js`（L8） | `<a href="/app/pm/today">驾驶舱</a>` / `link('驾驶舱','/app/pm/today')` |
| 今天 / 项目 / 待我审核 / AI助手 | `frontend/pm-src/index.html`（L3） | `<a href="/app/today">今天</a>` … `<a href="/app/review">待我审核</a><a href="/app/agnes">AI 助手</a>` |
| 需要你决定 | `frontend/pm-src/design/pages/TodayCockpit.tsx`（L46） | `<SectionHeading id="pm-h-decisions" title="需要你决定" ...>` |
| 项目总览 | `frontend/pm-src/design/pages/TodayCockpit.tsx`（L51） | `<SectionHeading id="pm-h-portfolio" title="项目总览" />` |
| AI 建议 | `frontend/pm-src/design/pages/TodayCockpit.tsx`（L63）、`design/pages/ProjectDossier.tsx`（L102） | `title="AI 建议"` / `<h2>AI 建议</h2>` |
| 资料去向 | `frontend/pm-src/design/pages/TodayCockpit.tsx`（L67）、`pm-src/receipts.js`（L4） | `title="资料去向"` / `node('h1','资料去向')` |
| 系统状态 | `frontend/pm-src/design/pages/TodayCockpit.tsx`（L42） | `<h2 className="pm-system-heading">系统状态</h2>` |
| 最近变化 | `frontend/pm-src/design/components/Gantt.tsx`（L339）、`backend/pm/dossier_sections.json` | `>最近变化<` / `{"id": "changes", "title": "最近变化"}` |
| 撤销 | `frontend/pm-src/design/components/Sections.tsx`（L51、L199） | `... onClick={revert}>撤销</button>` |
| 项目周报 | `frontend/pm-src/design/components/EvolutionAndReports.tsx`（L188） | `<section className="pm-block" aria-label="项目周报">` |
| 跨项目对比与项目周报 | 同上（L51） | `aria-label="跨项目对比与项目周报"` |
| 系统进化指标 | 同上（L7） | `aria-label="系统进化指标"` |
| 周趋势 | 同上（L28） | `<strong>周趋势：</strong>{trendStr}` |
| 需要填写 Key | `frontend/pm-src/design/pages/ProjectDossier.tsx`（L100） | `需要填写 Key 才能生成新结论；已有正式记录和来源可直接查看。` |
| 进度与里程碑 / 来源 / 一句话现状 / 目标与指标 / 缺口 / 下一步 / 风险与冲突 | `backend/pm/dossier_sections.json` | `"title": "进度与里程碑"` 等，由 `ProjectDossier.tsx`（L117）`title={s.title}` 原样渲染 |
| 添加监控文件夹 / 监控工作目录 (支持添加多个文件夹) / 开始全量扫描 / 文件夹扫描 / 系统与模型 / 推理大脑 Provider 预设模板 / API Key (DPAPI 加密存储) / 测试当前模型连通性 / 保存所有设置 | `frontend/dist/paw_console.js`（本地控制中心） | 逐字出现在该打包文件中（已用 UTF-8 解码核对，全部 FOUND） |
| 暂停 / 恢复巡航 (&C) / 系统健康自检 (&D) / 安全退出 (&X) | 托盘菜单（沿用 2.3.9 已审定文案） | 2.3.9 已审定文本，本次未改 |
| 待我审核 / AI助手 / 今天 / 项目 / 知识 / 资料 / 待办 / 日历 | `frontend/dist/assets/index-qIwUsXqw.js`（主界面导航） | 逐字出现在该打包文件中（已核对 FOUND） |
| 搜索文件名或分类 / 资料索引 / 没有符合条件的资料 | `frontend/dist/assets/index-qIwUsXqw.js`（【资料】页） | 逐字出现在该打包文件中（已核对 FOUND）；搜索框只在前端已取回的列表里筛选 |
| 使用说明 / 使用说明.txt | `installer/paw_setup.iss`（本轮新增） | `Name: "{group}\使用说明"` 与 `Source: "..\docs\使用说明.txt"` —— 这是安装包本次新增的快捷方式名与文件名，不是产品原有界面词，故以安装脚本为出处 |

**不属于产品界面词、属于转述系统提示或用户口令的**（已在文中明确语境，未当作产品功能名）：
- “Windows 已保护你的电脑”“更多信息”→“仍要运行”：Windows SmartScreen 系统提示原文。
- “允许运行”“信任此应用”“加入信任白名单”：杀毒软件的选项说法。
- “只看工作台”“不要联网”：给用户的提问口令提示（说明里是让用户这样打字，不是按钮）。

---

## 4. 版本说明的内容依据

`docs/版本说明.txt` 每条都对应已有自动用例，未写无证据内容。**"修复"一节现有三条，每条对上一个负对照**（C-11 合并后按要求补了第 3 条）。

| 条目 | 证据 |
|---|---|
| 驾驶舱、项目档案 | PM06（PM-C1-01…C5-01）截图与文字检查 |
| 版本自动替代 | PM03（PM-B2-01…05），含负对照 NC-PM-B2 `old_version_superseded` |
| 冲突清单 | PM04（PM-B3-01…06、PM-B4-01/02） |
| 自动归属与去向回执 | PM05（PM-D1-01…D2-02） |
| 有依据的 AI 建议 | PM08（PM-E1-01…E2-02） |
| 每日健康检查与知识缺口 | PM09（PM-E3-01、PM-E4-01） |
| 周报与进化指标 | PM10（PM-F1-01、PM-F2-01），考题 PM-F3-01 |
| 备份轮转、恢复自检、系统状态 | PM07（PM-A1-01…UP-01） |
| **修复 1：巡航绕过审核直接写正式字段** | **负对照 NC-PM-GOV `formal_fields_unchanged_after_cruise`** |
| **修复 2：新旧版本并列、无法判断当前版** | **负对照 NC-PM-B2 `old_version_superseded`** |
| **修复 3：扫描导入的文件出现在【资料】页、按完整文件名搜得到** | **负对照 NC-PM-S1 `scanned_file_listed_in_sources`**（main 上的 `tests/required_cases_C-11.json`；正向用例 PM-S1-01，见第 0.5.2 节与第 5.1 节的本地实测） |
| 新增 10（搜索特殊字符不再出错） | C-11 正向用例 PM-S1-02（**已随 C-11 合并进 main 并合入本分支**）；措辞按审核人所给 |
| 升级：覆盖安装前自动备份 | `installer/paw_setup.iss` 的 `BackupDatabaseBeforeInstall()`；端到端证据 B8-02 `upgrade_snapshot_exists`（见第 0.1 节） |
| 已知问题：【资料】页 200 份上限 | `backend/workspace_api/router.py:524` 默认 `limit=200`；前端本地筛选（见第 0.2 节） |

**按 REVIEW 删掉的 4 条"修复"**（它们是新功能，或没有证据证明 2.3.9 真有该问题），仍在删掉状态：
- "补充与矛盾分不清、矛盾被自动覆盖"——是新功能（C-04），不是修复。
- "搜索带扩展名的完整文件名时搜不到任何结果"——**注意**：本条当初被删的理由是"2.3.9 在【资料】页本来就能按文件名找到文件"。C-11 的负对照 NC-PM-S1 证明的并不是这条，而是**扫描导入的文件根本没进【资料】页**。因此本轮补的第 3 条措辞严格限定在"扫描导入的文件……出现在【资料】页"，**没有**把当初被否掉的"2.3.9 搜不到完整文件名"重新写回来。
- "助手把依据不足变成空白"——没有对 2.3.9 的负对照。
- "资料多时回答里的数字被上下文裁剪"——没有对 2.3.9 的负对照。

**未写进版本说明的**：任何没有用例覆盖的说法（例如此前"加固了 SQLite 写入事务"这类无据表述），以及**未经测试的回退承诺**（重装 2.3.9 能否打开新库未验证，已删除）。

---

## 5. 测试运行

### 5.1 本地自查

```
$ python tests/paw_lint.py
lint clean

$ python -m pytest tests/unit -q
169 passed in 20.35s

$ python -m pytest tests/unit/test_release_docs.py -q
7 passed in 0.08s

$ python audit_check.py   # CHECK 1
>> RESULT: [PASS] Version Uniformity verified across all entrypoints and configurations.
```

单元测试由上一轮的 156 项增至 **169 项**：新增 `tests/unit/test_release_docs.py` 的 6 项 + 本轮 1 项（负对照绑定），以及合并 main 带进来的 C-11 的 13 项（`test_retrieval_search_special_chars.py` 9 项、`test_scanner_source_registry.py` 4 项）。

`audit_check.py` 其余各项：CHECK 2/3/4 PASS；CHECK 5 报 `__pycache__`/`.pyc` 残留——这是本地反复跑测试留下的、**已被 `.gitignore` 忽略的构建残留**（`git ls-files | grep __pycache__` 为 0），不在提交里，云端全新检出不受影响。

**本轮针对第 3 条"修复"的本地实测**（合并 main 后的代码，开发模式起 `run.py` + `scripts/mock_openai_server.py`，独立数据目录 `tmp_pawdata_r250`，未装包、未改测试）：

```
sources_count 2
titles ['星火导师计划_方案_v2.docx', '星火导师计划_方案_v2.docx']
LISTED_IN_SOURCES True
FRONTEND_FILTER_WOULD_MATCH True
```

即：把 `星火导师计划_方案_v2.docx` 放进监控文件夹走**正常扫描流程**后，`GET /v1/web/workspace/sources` 返回的条目 `title` 就是**完整文件名（含 `.docx` 扩展名）**，而前端【资料】页的搜索框正是在这批 `title` 上做本地筛选（见第 0.2 节），因此"按完整文件名搜得到"成立。这与 PM-S1-01 的断言口径一致（该用例断的就是 `sources` 列表里 `title == FILENAME`）。

补充说明一处**不影响结论但需交代**的观察：`GET /v1/web/workspace/projection/knowledge-search?q=<完整文件名>` 在开发模式下返回 0 条，因为【知识】检索索引里存的是规范化后的知识标题（如「【知识】星火导师计划 方案 v2」，空格化、无扩展名），而非原始文件名；用"星火""v2"等子串查同一文档能命中。**版本说明第 3 条说的"按完整文件名搜索"指的是【资料】页（PM-S1-01 的判定口径），不是【知识】页检索**，措辞与此一致，未越界承诺。

### 5.2 云端运行（本轮头部提交 `7b23689`，合并 main 之后）

| 事件 | 运行 | attempt | 结论 |
|---|---|---|---|
| push 流水线 | [39089026564](https://github.com/riser-lang/paw-local-dev/actions/runs/39089026564) | **1**（未重跑） | **GREEN**（83/83 PASS，0 FAIL，0 NOT RUN） |
| pull_request 流水线 | [39089030009](https://github.com/riser-lang/paw-local-dev/actions/runs/39089030009) | **1**（未重跑） | **GREEN**（83/83 PASS，0 FAIL，0 NOT RUN） |
| push 单元测试 | [39089026565](https://github.com/riser-lang/paw-local-dev/actions/runs/39089026565) | 1 | GREEN（**169 passed**） |
| PR 单元测试 | [39089030004](https://github.com/riser-lang/paw-local-dev/actions/runs/39089030004) | 1 | GREEN（**169 passed**） |
| PR 安装包构建 | [39089030016](https://github.com/riser-lang/paw-local-dev/actions/runs/39089030016) | 1 | GREEN（产物 `PAW_Personal_Work_OS_Setup_v2.5.0`，33.5 MB） |

用例数由上轮的 **80 增至 83**：合并进来的 C-11 带来 PM-S1-01、PM-S1-02、PM-S1-03 三条，并新增负对照 **NC-PM-S1**。
负对照按设计 FAIL（NC-01、NC-02、NC-03、NC-PM-GOV、NC-PM-S1、NC-PM-C1、NC-PM-B2），其中 **NC-PM-S1 恰好 FAIL 在 `scanned_file_listed_in_sources`**，正是版本说明第 3 条"修复"的证明项。三条 C-11 正向用例 `PM-S1-01 / PM-S1-02 / PM-S1-03` 本次均为 PASS。

**本轮没有任何 flaky 失败**：两条流水线都在**第 1 次**尝试即全绿，未触发"整条流水线重跑"。

push 与 PR 的 `summary.md` 首行哈希不同属正常：push 运行报的是提交本身 `7b23689…`，PR 运行报的是 GitHub 生成的合并引用 `6a25a6a…`；两者的 `headSha` 都是 `7b236892421742b491e309455108dbbb213f3703`（上面运行表已核对）。

### 5.3 push 运行的 `summary.md` 全文（原样粘贴）

```markdown
# Pipeline verdict for 7b236892421742b491e309455108dbbb213f3703

Required cases: 83 | PASS 83 | FAIL 0 | NOT RUN 0

| Block | Case | Status | Failed check | Model |
|---|---|---|---|---|
| B1 | B1-01 | PASS |  | mock-llm |
| B1 | B1-02 | PASS |  | mock-llm |
| B1 | B1-03 | PASS |  | mock-llm |
| B2 | B2-01 | PASS |  | mock-llm |
| B2 | B2-02 | PASS |  | mock-llm |
| B2 | B2-03 | PASS |  | mock-llm |
| B3 | B3-01 | PASS |  | mock-llm |
| B3 | B3-02 | PASS |  | mock-llm |
| B3 | B3-03 | PASS |  | mock-llm |
| B3 | B3-04 | PASS |  | mock-llm |
| B4 | B4-01 | PASS |  | mock-llm |
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
| B5 | B5-01 | PASS |  | mock-llm |
| B5 | B5-02 | PASS |  | mock-llm |
| B5 | B5-03 | PASS |  | mock-llm |
| B6 | B6-01 | PASS |  | mock-llm |
| B6 | B6-02 | PASS |  | mock-llm |
| B7 | B7-01 | PASS |  | mock-llm |
| B7 | B7-02 | PASS |  | mock-llm |
| B7 | B7-03 | PASS |  | mock-llm |
| B8 | B8-01 | PASS |  | mock-llm |
| B8 | B8-02 | PASS |  | mock-llm |
| B8 | B8-03 | PASS |  | mock-llm |
| NEGATIVE-CONTROL | NC-01 | FAIL | project_update_committed | mock-llm |
| NEGATIVE-CONTROL | NC-02 | FAIL | explainer_fields_filled | mock-llm |
| NEGATIVE-CONTROL | NC-03 | FAIL | environment_clean | mock-llm |
| NEGATIVE-CONTROL | NC-PM-GOV | FAIL | formal_fields_unchanged_after_cruise | mock-llm |
| NEGATIVE-CONTROL | NC-PM-S1 | FAIL | scanned_file_listed_in_sources | mock-llm |
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |
| PM07 | PM-A1-01 | PASS |  | mock-llm |
| PM07 | PM-A2-01 | PASS |  | mock-llm |
| PM07 | PM-A4-01 | PASS |  | mock-llm |
| PM07 | PM-UP-01 | PASS |  | mock-llm |
| PM08 | PM-E1-01 | PASS |  | mock-llm |
| PM08 | PM-E1-02 | PASS |  | mock-llm |
| PM08 | PM-E1-03 | PASS |  | mock-llm |
| PM08 | PM-E2-01 | PASS |  | mock-llm |
| PM08 | PM-E2-02 | PASS |  | mock-llm |
| PM09 | PM-E3-01 | PASS |  | mock-llm |
| PM09 | PM-E4-01 | PASS |  | mock-llm |
| PM09 | PM-S1-01 | PASS |  | mock-llm |
| PM09 | PM-S1-02 | PASS |  | mock-llm |
| PM09 | PM-S1-03 | PASS |  | mock-llm |
| PM10 | PM-F1-01 | PASS |  | mock-llm |
| PM10 | PM-F2-01 | PASS |  | mock-llm |
| PM10 | PM-F3-01 | PASS |  | mock-llm |

## No problems

```

### 5.4 PR 运行的 `summary.md` 全文（原样粘贴）

```markdown
# Pipeline verdict for 6a25a6ad78e8f05c5d61fe1bcb40aa2983874377

Required cases: 83 | PASS 83 | FAIL 0 | NOT RUN 0

| Block | Case | Status | Failed check | Model |
|---|---|---|---|---|
| B1 | B1-01 | PASS |  | mock-llm |
| B1 | B1-02 | PASS |  | mock-llm |
| B1 | B1-03 | PASS |  | mock-llm |
| B2 | B2-01 | PASS |  | mock-llm |
| B2 | B2-02 | PASS |  | mock-llm |
| B2 | B2-03 | PASS |  | mock-llm |
| B3 | B3-01 | PASS |  | mock-llm |
| B3 | B3-02 | PASS |  | mock-llm |
| B3 | B3-03 | PASS |  | mock-llm |
| B3 | B3-04 | PASS |  | mock-llm |
| B4 | B4-01 | PASS |  | mock-llm |
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
| B5 | B5-01 | PASS |  | mock-llm |
| B5 | B5-02 | PASS |  | mock-llm |
| B5 | B5-03 | PASS |  | mock-llm |
| B6 | B6-01 | PASS |  | mock-llm |
| B6 | B6-02 | PASS |  | mock-llm |
| B7 | B7-01 | PASS |  | mock-llm |
| B7 | B7-02 | PASS |  | mock-llm |
| B7 | B7-03 | PASS |  | mock-llm |
| B8 | B8-01 | PASS |  | mock-llm |
| B8 | B8-02 | PASS |  | mock-llm |
| B8 | B8-03 | PASS |  | mock-llm |
| NEGATIVE-CONTROL | NC-01 | FAIL | project_update_committed | mock-llm |
| NEGATIVE-CONTROL | NC-02 | FAIL | explainer_fields_filled | mock-llm |
| NEGATIVE-CONTROL | NC-03 | FAIL | environment_clean | mock-llm |
| NEGATIVE-CONTROL | NC-PM-GOV | FAIL | formal_fields_unchanged_after_cruise | mock-llm |
| NEGATIVE-CONTROL | NC-PM-S1 | FAIL | scanned_file_listed_in_sources | mock-llm |
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |
| PM07 | PM-A1-01 | PASS |  | mock-llm |
| PM07 | PM-A2-01 | PASS |  | mock-llm |
| PM07 | PM-A4-01 | PASS |  | mock-llm |
| PM07 | PM-UP-01 | PASS |  | mock-llm |
| PM08 | PM-E1-01 | PASS |  | mock-llm |
| PM08 | PM-E1-02 | PASS |  | mock-llm |
| PM08 | PM-E1-03 | PASS |  | mock-llm |
| PM08 | PM-E2-01 | PASS |  | mock-llm |
| PM08 | PM-E2-02 | PASS |  | mock-llm |
| PM09 | PM-E3-01 | PASS |  | mock-llm |
| PM09 | PM-E4-01 | PASS |  | mock-llm |
| PM09 | PM-S1-01 | PASS |  | mock-llm |
| PM09 | PM-S1-02 | PASS |  | mock-llm |
| PM09 | PM-S1-03 | PASS |  | mock-llm |
| PM10 | PM-F1-01 | PASS |  | mock-llm |
| PM10 | PM-F2-01 | PASS |  | mock-llm |
| PM10 | PM-F3-01 | PASS |  | mock-llm |

## No problems

```

### 5.5 本报告提交后的运行（只有本文件改动）

报告推到分支后 PR 头部再前进一步，头部提交为 **`c62e3cd`**（只有本 Markdown）。如实记录全部经过：

| 事件 | 运行 | 尝试 | 结论 |
|---|---|---|---|
| pull_request | [39022496567](https://github.com/riser-lang/paw-local-dev/actions/runs/39022496567) | attempt 1 | **GREEN**（80/80 PASS，首次即通过） |
| push | [39022494198](https://github.com/riser-lang/paw-local-dev/actions/runs/39022494198) | attempt 1 | **RED**：`PM-A4-01` 报 `playwright._impl._errors.TargetClosedError: Page.screenshot: Target page, context or browser has been closed`（浏览器在截图时被关闭） |
| push | 同上 | attempt 2（只重跑失败作业） | **RED**，但**全部 80 个用例 PASS**；门禁失败原因是 `screenshot … reused 2 times`——只重跑 PM07 导致两次尝试的产物混在一起，PM-A4-01 新截图与上一次的重复 |
| push | 同上 | attempt 3（重跑整个 workflow） | **GREEN**（80/80 PASS，0 FAIL，0 NOT RUN） |

关于这些 RED，如实说明：
- **attempt 1** 是浏览器/页面在截图时被关闭（`TargetClosedError`），不是断言失败。审核人的不稳定记录里 PM-A2-01 / PM-A4-01 本就属于"截图时机"一类问题。
- **attempt 2** 是我操作造成的：只重跑失败作业，使报告目录里混入两次尝试的截图，出现哈希重复。**教训：这类含截图校验的流水线要整条重跑，不能只重跑失败作业。**
- 两次 RED 都不是产品问题：该提交只改本文件，不含任何代码；上一提交 `350d16a` 的 push 与 PR 均为 GREEN。
- **没有改动任何测试、断言或截图逻辑来让它通过**；attempt 3 是整条 workflow 干净重跑。

（本表写入后，头部会再前进一次；那次提交同样只改本文件。按 TASK-R-250 第 6 项，C-11 合并通知到达后还要 `git merge origin/main` 并整条重跑一次，届时以那一轮的结果为准。）

### 5.6 再上一个提交 `775fec8` 的运行

上表那次提交把头部推到 `775fec8`（同样只改本文件），结果：

| 事件 | 运行 | 尝试 | 结论 |
|---|---|---|---|
| push | [39024305174](https://github.com/riser-lang/paw-local-dev/actions/runs/39024305174) | attempt 1 | **GREEN**（80/80 PASS） |
| pull_request | [39024308485](https://github.com/riser-lang/paw-local-dev/actions/runs/39024308485) | attempt 1 | **RED**：80 个用例全 PASS，但门禁报 `screenshot … reused 2 times` |
| pull_request | 同上 | attempt 2（整条 workflow 重跑） | **GREEN**（80/80 PASS） |

attempt 1 的截图重复，定位结果是 **PM-E1-02 与 PM-E1-03 各两张截图逐字节相同**（`screen_PM_E1_02_1440.png` 与 `screen_PM_E1_03_1440.png`、以及对应的 390 版）：

```
DUP fe6408906e6b  PM-E1-02 screen_PM_E1_02_1440.png  /app/pm/projects/e74c4d89-…
                  PM-E1-03 screen_PM_E1_03_1440.png  /app/pm/projects/e2154a0c-…
DUP 0a3cfaef6173  PM-E1-02 screen_PM_E1_02_390.png
                  PM-E1-03 screen_PM_E1_03_390.png
```

这两条是 **C-08 的既有用例，本轮未改动**；同一提交的 push 运行里它们是 PASS。判断属于"截图时机"这一类偶发问题（页面尚未渲染出各自内容就截图，两张图都一样），已在审核人的不稳定记录同类项中。**没有改动 PM-E1-02/PM-E1-03 的检查或截图逻辑**，只是整条重跑；重跑即通过。

> 说明：以上两次 RED 都发生在"只改本报告"的提交上，代码提交 `350d16a` 的 push 与 PR 均为首次 GREEN。**按第 6 项，最终以 C-11 合并后那一轮全量运行为准**——那一轮即第 5.2 节的 `7b23689`，push 与 PR 均 attempt 1 GREEN 83/83，无任何 flaky。

---
## 6. 我在 C-11 分支上做过的部分工作（已按要求停下）

按新指示，C-11 改由 Codex 并行完成。我先前已开始，**已停止、提交并推送到 `gemini/C-11`**，**没有再开 PR**。已推送两个提交：

| 提交 | 内容 | 状态 |
|---|---|---|
| `f1a5dee` | **搜索缺陷修复本体**：用户输入先分词再逐词加引号后才进 FTS5；LIKE 兜底不再限于「短中文词」，任何长度都走且同时匹配 `title` 与 `search_text`，并对 `%` `_` `\` 转义；FTS 被拒时记一条只含异常类名的诊断日志；`git mv` 激活 `tests/required_cases_C-11.json`（blob 前后同为 `27618f4`，内容未改）；新增 `tests/unit/test_retrieval_search.py` 7 项 | **完整，可参考** |
| `6cc5f26` | 新增 `tests/run_pm_search_tests.py`（PM-S1-01…03、NC-PM-S1）、`PM11` block 与 `--search-negative` 入口、CI 矩阵与负对照步骤 | **未完成**：用例未在本地跑通，最后一次 push 运行报这 3 条 NOT RUN |

**给 Codex 的提醒**：搜索缺陷修复（`f1a5dee`）已通过 lint 与 157 项单元测试，可直接取用；验收用例部分（`6cc5f26`）尚未验证，建议重新实现或充分调试后再用。

**PR 现状（我查到的）**：C-11 现在有两个开着的 PR —— **#11**（`gemini/C-11`，我的部分工作）和 **#12**（`codex/C-11`，Codex 的实现）。**我没有关闭 #11，也没有动 #12**；按规矩合并/关闭由审核人决定。如果 Codex 的 #12 已覆盖全部内容，建议审核人关闭 #11。

---

## 7. 风险、未确认事项

1. **C-11 已合并进 main，本轮已合并进本分支**：版本说明"新增 10"的特殊字符修复、以及新补的第 3 条"修复"（扫描导入的文件出现在【资料】页、按完整文件名搜得到）都由 C-11 的实现支撑，云端 PM-S1-01/02/03 均 PASS、NC-PM-S1 按预期 FAIL 在 `scanned_file_listed_in_sources`。
2. **安装包现在会带完整使用说明**（本轮改动）：`[Files]` 从 `docs/使用说明.txt` 原样复制到安装目录，`[Icons]` 增加开始菜单【使用说明】，`package_app.py` 不再生成 12 行短版。云端实测 B1-01 三项新检查均为 `true`。
   - 仍属**已知边界**：`installer/paw_setup.iss` 里的 [Files] 使用相对路径 `..\docs\使用说明.txt`，依赖"从仓库根目录编译"这一既有约定（`package_app.py` 以仓库根为工作目录调用 ISCC），与既有 `..\dist\...` 写法一致。
   - **版本说明.txt 仍未装进安装目录**，只作为发布附件；审核人这次只要求装使用说明，如需一并装入请指示。
3. **备份目录已在版本说明中按实际代码改写**，与初稿不同（初稿写 `backups`，实际安装期备份在 `workspace_pre_update.db`）。出处见第 0.1 节。
4. **页面长度**：使用说明 120 行、版本说明 44 行。按记事本默认窗口估算分别约 3 页和 1 页，符合要求；但不同字号/窗口会有偏差，已尽量留余量。
5. **仍需交代的一处措辞边界**（见第 5.1 节末尾）：版本说明第 3 条的"按完整文件名搜索"指的是【资料】页（PM-S1-01 的判定口径）。【知识】页的检索索引存的是规范化知识标题（不含原始文件名与扩展名），因此用完整文件名去搜【知识】页并不保证命中——**本条没有把这点写成承诺**，也未写成"搜索万能"。
6. **未做**：tag、Release（按指示由审核人来做）。`git merge origin/main` 已在第 0.5 节完成，合并无冲突。

---

## 8. 五行摘要

1. 按 REVIEW 改完 6 项：版本说明"修复"只留有负对照证明的、新增 10 换成特殊字符措辞、删掉未经验证的 2.3.9 回退承诺并写明实际备份位置、【资料】页 200 份上限写进已知问题。
2. **C-11 合并后已 `git merge origin/main`：(合并无冲突)**，产品代码全部取 main，本分支只剩 `docs/`、`installer/`、`scripts/package_app.py` 与版本号改动；并把"扫描导入的文件现在会出现在【资料】页，并能按完整文件名（含扩展名）搜索到"补为第 3 条"修复"（依据 NC-PM-S1）。
3. 完整使用说明装进安装目录：`[Files]` 从 `docs/使用说明.txt` 原样复制，开始菜单加【使用说明】，`package_app.py` 不再生成短版；B1-01 追加三项安装检查、原有检查一行未改。使用说明 Q5 的"原始资料一直留在【资料】里可查"经核对**现在成立**，一字未改。
4. 云端本轮（头部 `7b23689`）push 与 PR 两条流水线**均 attempt 1 GREEN 83/83**（新增 C-11 的 PM-S1-01/02/03，NC-PM-S1 按预期 FAIL），单元测试 169 项全过，lint `clean`，`audit_check.py` 版本一致性 PASS。
5. **未打 tag、未建 Release、未改 STATUS.md、未碰 C-11 目录**；合并无冲突，无 flaky，未重跑流水线。