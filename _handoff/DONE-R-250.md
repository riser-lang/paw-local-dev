# DONE-R-250 — 准备 v2.5.0-rc1（参赛候选版本）

- 执行者：DeepSeek v4.1 flash ｜ 审核人：Claude Code
- 分支：`gemini/R-250`，PR **#13**（合入 main）
- 本报告对应头部提交 **`350d16a`**（已按 REVIEW-C-11 的 R-250 一节改完 6 项）
- **未打 tag，未建 Release**（按指示等审核人合并后再处理）
- **尚未 `git merge origin/main`**：按第 6 项，要等审核人通知 C-11 已合并后再合并、再跑一次

---

## 0. 按 REVIEW 的六项修改（本轮）

| # | 审核人要求 | 处理 | 证据 |
|---|---|---|---|
| 1 | 版本说明"修复"只保留有负对照证明的两条，删掉 3–6 | **已改** | 现在只剩「巡航绕过审核直接写正式字段」（NC-PM-GOV `formal_fields_unchanged_after_cruise`）和「新旧版本并列，无法判断哪个是当前版」（NC-PM-B2 `old_version_superseded`） |
| 2 | "新增 10"改为搜索特殊字符不出错 | **已改** | 现为「搜索词里含双引号、括号、星号、冒号、连字符等特殊字符时不再出错。」 |
| 3 | 删掉"重装 2.3.9 回退"，改为写备份目录并附出处 | **已改** | 见第 0.1 节 |
| 4 | 已知问题新增【资料】页 200 份上限 | **已改** | 见第 0.2 节 |
| 5 | 把 `docs/使用说明.txt` 原样装进安装目录 + 开始菜单"使用说明"快捷方式 + 新增安装检查 | **已改** | 见第 0.3 节；B1-01 的三项新检查在云端实测为 `true` |
| 6 | 等 C-11 合并后再 `git merge origin/main` 重跑 | **未做（按要求等待）** | 本次只改并推送，未合并 main |

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
- 版本说明「搜索词里含双引号、括号、星号、冒号、连字符等特殊字符时不再出错」一句**已按审核人给的措辞写入**，但该修复的代码**不在本 PR 内**，需由审核人在 C-11 合并后确认。
- 我先前在 `gemini/C-11` 上的部分工作已停止、提交并推送（见第 6 节）。审核人已裁定 **PR #11 关闭、不合并**。

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
| §3 只写有测试证据的内容 | **完成** | 按 REVIEW 收紧为只有负对照证明的两条"修复"，见第 4 节 |
| §4 打 tag / 建 Release | **未做（按要求）** | 等审核人合并后 |
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

`docs/版本说明.txt` 每条都对应已有自动用例，未写无证据内容。按 REVIEW 要求，**"修复"一节只保留有负对照证明的两条**。

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
| 新增 10（搜索特殊字符不再出错） | **C-11 —— 不在本 PR 内，待审核人在 C-11 合并后确认**；措辞按审核人所给 |
| 升级：覆盖安装前自动备份 | `installer/paw_setup.iss` 的 `BackupDatabaseBeforeInstall()`；端到端证据 B8-02 `upgrade_snapshot_exists`（见第 0.1 节） |
| 已知问题：【资料】页 200 份上限 | `backend/workspace_api/router.py:524` 默认 `limit=200`；前端本地筛选（见第 0.2 节） |

**按 REVIEW 删掉的 4 条"修复"**（它们是新功能，或没有证据证明 2.3.9 真有该问题）：
- "补充与矛盾分不清、矛盾被自动覆盖"——是新功能（C-04），不是修复。
- "搜索带扩展名的完整文件名时搜不到任何结果"——2.3.9 在【资料】页本来就能按文件名找到文件，该前提不成立。
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
156 passed in 14.15s

$ python audit_check.py   # CHECK 1
>> RESULT: [PASS] Version Uniformity verified across all entrypoints and configurations.
```

单元测试由上一轮的 150 项增至 **156 项**，新增 `tests/unit/test_release_docs.py` 的 6 项。

### 5.2 云端两次运行（头部提交 `350d16a`）

| 事件 | 运行 | 结论 |
|---|---|---|
| push | [39018709517](https://github.com/riser-lang/paw-local-dev/actions/runs/39018709517) | **GREEN**（80/80 PASS，0 FAIL，0 NOT RUN） |
| pull_request | [39018711575](https://github.com/riser-lang/paw-local-dev/actions/runs/39018711575) | **GREEN**（80/80 PASS，0 FAIL，0 NOT RUN） |
| push 单元测试 | 见该 push 运行的 unit job | GREEN（156 passed） |
| PR 安装包构建 | 见该 PR 运行的 Build job | GREEN（产物 `PAW_Personal_Work_OS_Setup_v2.5.0.exe`） |

负对照按预期 FAIL（NC-01、NC-02、NC-03、NC-PM-GOV、NC-PM-C1、NC-PM-B2），属于设计如此。
新增的 B1-01 三项安装检查在云端实测均为 `true`，见第 0.3 节。

### 5.3 push 运行的 `summary.md` 全文（原样粘贴）

```markdown
# Pipeline verdict for 350d16a59bc0bcb923dae240f551dd61fd0bfcec

Required cases: 80 | PASS 80 | FAIL 0 | NOT RUN 0

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
| PM10 | PM-F1-01 | PASS |  | mock-llm |
| PM10 | PM-F2-01 | PASS |  | mock-llm |
| PM10 | PM-F3-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN
```

### 5.4 PR 运行的 `summary.md` 全文（原样粘贴）

> 与 5.3 唯一差别是首行 hash：PR 运行的门禁报告的是 GitHub 的合并 ref `cd6e570a…`，其 `headSha` 实际是 `350d16a`（已核对：`gh run view 39018711575 --json headSha` 返回 `350d16a5`，与 PR 头部提交一致）。以下为原样粘贴，未做任何替换。

```markdown
# Pipeline verdict for cd6e570a8056595d27d356679f08ed55ece508f6

Required cases: 80 | PASS 80 | FAIL 0 | NOT RUN 0

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
| PM10 | PM-F1-01 | PASS |  | mock-llm |
| PM10 | PM-F2-01 | PASS |  | mock-llm |
| PM10 | PM-F3-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN
```

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

1. **本 PR 不含 C-11 代码**，版本说明"新增 10"已按审核人所给措辞写入（只提特殊字符不再出错，不再声称"能搜完整文件名"）。该修复的实际代码在 C-11，**请审核人在 C-11 合并后确认**。
2. **安装包现在会带完整使用说明**（本轮改动）：`[Files]` 从 `docs/使用说明.txt` 原样复制到安装目录，`[Icons]` 增加开始菜单【使用说明】，`package_app.py` 不再生成 12 行短版。云端实测 B1-01 三项新检查均为 `true`。
   - 仍属**已知边界**：`installer/paw_setup.iss` 里的 [Files] 使用相对路径 `..\docs\使用说明.txt`，依赖"从仓库根目录编译"这一既有约定（`package_app.py` 以仓库根为工作目录调用 ISCC），与既有 `..\dist\...` 写法一致。
   - **版本说明.txt 仍未装进安装目录**，只作为发布附件；审核人这次只要求装使用说明，如需一并装入请指示。
3. **备份目录已在版本说明中按实际代码改写**，与初稿不同（初稿写 `backups`，实际安装期备份在 `workspace_pre_update.db`）。出处见第 0.1 节。
4. **页面长度**：使用说明 120 行、版本说明 44 行。按记事本默认窗口估算分别约 3 页和 1 页，符合要求；但不同字号/窗口会有偏差，已尽量留余量。
5. **未做**：tag、Release（按要求等审核人合并后）；`git merge origin/main`（等审核人通知 C-11 已合并后再做并重跑）。

---

## 8. 五行摘要

1. 按 REVIEW 改完 6 项：版本说明"修复"只留两条有负对照证明的、新增 10 换成特殊字符措辞、删掉未经验证的 2.3.9 回退承诺并写明实际备份位置、【资料】页 200 份上限写进已知问题。
2. 完整使用说明现在真的装进安装目录：`[Files]` 从 `docs/使用说明.txt` 原样复制，开始菜单加【使用说明】，`package_app.py` 不再生成 12 行短版，只剩一份要维护。
3. 新增安装检查（B1-01 三项：文件存在、与 docs 逐字节一致、开始菜单快捷方式存在）加在原有检查之后、原有检查一行未改；另加 6 项单元测试守住打包与 BOM。云端实测三项全 `true`。
4. push 与 PR 两次完整运行均 **GREEN 80/80**，lint `clean`，单元测试 156 项全过，`audit_check.py` 版本一致性 PASS。
5. 未打 tag、未建 Release、未改 STATUS.md、未碰 C-11 目录；**尚未 merge origin/main**，等审核人通知 C-11 合并后再合并重跑。
