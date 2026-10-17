# DONE-R-250 — 准备 v2.5.0-rc1（参赛候选版本）

- 执行者：DeepSeek v4.1 flash ｜ 审核人：Claude Code
- 分支：`gemini/R-250`，PR **#13**（合入 main）
- 本报告对应头部提交 **`6e138d0`**
- **未打 tag，未建 Release**（按指示等审核人合并后再处理）

---

## 0. 与 C-11 的关系（重要）

按指示，本任务**只做 R-250**：

- 本分支从 **main** 拉出（`git checkout main && git pull` 之后），**不以 C-11 为基础**。
- C-11（搜索修复）由另一位执行者（Codex）在 `D:\PAW\paw-dev-codex` 并行完成，**我没有碰那个目录**。
- 版本说明里「搜索支持完整文件名」这一条**已按要求写入**，但该功能**不在本 PR 的代码里**，需由审核人在 C-11 合并后确认。
- 我先前在 `gemini/C-11` 分支上开展过 C-11 的部分工作，已按要求停止、提交并推送，未开新 PR（详情见第 6 节）。

---

## 1. 本任务子项逐条对照

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
| §2 UTF-8 带 BOM、3 页以内 | **完成** | BOM 已核（EF BB BF）；118 行 |
| §3 新建 `docs/版本说明.txt`，UTF-8 带 BOM，一页以内 | **完成** | BOM 已核；43 行 |
| §3 只写有测试证据的内容 | **完成** | 逐条对应已有用例，见第 4 节 |
| §4 打 tag / 建 Release | **未做（按要求）** | 等审核人合并后 |
| 不修改 `_handoff/STATUS.md` | **遵守** | `git status _handoff/` 为空 |
| 不删文件、不改判定规则与考题 | **遵守** | 本 PR 未触及 `tests/` |

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

**不属于产品界面词、属于转述系统提示或用户口令的**（已在文中明确语境，未当作产品功能名）：
- “Windows 已保护你的电脑”“更多信息”→“仍要运行”：Windows SmartScreen 系统提示原文。
- “允许运行”“信任此应用”“加入信任白名单”：杀毒软件的选项说法。
- “只看工作台”“不要联网”：给用户的提问口令提示（说明里是让用户这样打字，不是按钮）。

---

## 4. 版本说明的内容依据

`docs/版本说明.txt` 每条都对应已有自动用例，未写无证据内容。

| 条目 | 证据 |
|---|---|
| 驾驶舱、项目档案 | PM06（PM-C1-01…C5-01）截图与文字检查 |
| 版本自动替代 | PM03（PM-B2-01…05），含负对照 NC-PM-B2 `old_version_superseded` |
| 冲突清单 | PM04（PM-B3-01…06、PM-B4-01/02） |
| 自动归属与去向回执 | PM05（PM-D1-01…D2-02） |
| 有依据的 AI 建议 | PM08（PM-E1-01…E2-02） |
| 每日健康检查与知识缺口 | PM09（PM-E3-01、PM-E4-01） |
| 周报与进化指标 | PM10（PM-F1-01、PM-F2-01），考题 PM-F3-01 |
| 巡航不再直接改正式内容 | PM02（PM-GOV-01…03），负对照 NC-PM-GOV `formal_fields_unchanged_after_cruise` |
| 搜索支持完整文件名 | **C-11（PM-S1-01…03）——不在本 PR 内，待审核人在 C-11 合并后确认** |
| 依据不足不再变空白、上下文裁剪修数字 | C-10 返工（REVIEW-C-10b 已核实，考题 24/24） |
| 备份轮转、恢复自检、系统状态 | PM07（PM-A1-01…UP-01） |

**未写进版本说明的**：任何没有用例覆盖的说法（例如此前版本说明里"加固了 SQLite 写入事务"这类无据表述，本次不写）。

---

## 5. 测试运行

### 5.1 本地自查

```
$ python tests/paw_lint.py
lint clean

$ python -m pytest tests/unit -q
150 passed in 14.72s

$ python audit_check.py     # CHECK 1
>> RESULT: [PASS] Version Uniformity verified across all entrypoints and configurations.
```

### 5.2 云端两次运行（头部提交 `6e138d0`）

| 事件 | 运行 | 结论 |
|---|---|---|
| push | [39009242487](https://github.com/riser-lang/paw-local-dev/actions/runs/39009242487) | **GREEN**（80/80 PASS，0 FAIL，0 NOT RUN） |
| pull_request | [39009257327](https://github.com/riser-lang/paw-local-dev/actions/runs/39009257327) | **GREEN**（80/80 PASS，0 FAIL，0 NOT RUN） |
| push 单元测试 | [39009242490](https://github.com/riser-lang/paw-local-dev/actions/runs/39009242490) | GREEN |
| PR 单元测试 | [39009257361](https://github.com/riser-lang/paw-local-dev/actions/runs/39009257361) | GREEN |
| PR 安装包构建 | [39009257325](https://github.com/riser-lang/paw-local-dev/actions/runs/39009257325) | GREEN（产物 `PAW_Personal_Work_OS_Setup_v2.5.0.exe`） |

负对照按预期 FAIL（NC-01、NC-02、NC-03、NC-PM-GOV、NC-PM-C1、NC-PM-B2），属于设计如此。

### 5.3 push 运行的 `summary.md` 全文（原样粘贴）

```markdown
# Pipeline verdict for 6e138d08aba2d3d27d221516f5fbcfadf3a2b056

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

> 与 5.3 唯一差别是首行 hash：PR 运行的门禁报告的是 GitHub 的合并 ref `f0083449…`，其 `headSha` 实际是 `6e138d0`（已核对：`gh run view 39009257327 --json headSha` 返回 `6e138d08`，与 PR 头部提交一致）。以下为原样粘贴，未做任何替换。

```markdown
# Pipeline verdict for f0083449b616280c939577856df858300fbf36b4

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

### 5.5 本报告提交后的运行（只有文档改动）

本报告本身也推到了同一分支，因此 PR 头部会再前进一步。该提交只新增本文件，两次运行同样 GREEN：

| 事件 | 运行 | 提交 | 结论 |
|---|---|---|---|
| push | [39010817666](https://github.com/riser-lang/paw-local-dev/actions/runs/39010817666) | `4f1ee3f` | **GREEN**（80/80 PASS，0 FAIL，0 NOT RUN） |
| pull_request | [39010820037](https://github.com/riser-lang/paw-local-dev/actions/runs/39010820037) | `4f1ee3f` | **GREEN**（80/80 PASS，0 FAIL，0 NOT RUN） |

（本表是在这两次运行结束后补写的，所以写入本表的这次提交又会成为新的头部；其代码内容与 `4f1ee3f` 完全相同，只有这份 Markdown 不同。）

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

1. **本 PR 不含 C-11 代码**，但版本说明第 10 条已写"搜索支持完整文件名"。若 C-11 最终未合并或改动行为，此条需再审。**请审核人合入后确认。**
2. **安装包内不包含这两份文档**：`installer/paw_setup.iss` 只打包 `dist/PAW_Personal_Work_OS\*`，而 `scripts/package_app.py` 会向该目录写一个约 12 行的短版 `使用说明.txt`（旧行为）。也就是说 `docs/使用说明.txt` 是**发布附件**，不是安装目录内文件——这与 G-07 的做法一致（当时由发布流程把 `docs/使用说明.txt` 原样复制进发布包，见 DONE-G-07）。我没有改打包脚本，以免偏离既有发布流程；**如果希望安装目录里也放完整版，请指示，我再改 `package_app.py`。**（另注：那个短版里还写着"审核中心"，与现在的界面用词不一致，属既有问题。）
3. **页面长度**：使用说明 118 行、版本说明 43 行。按记事本默认窗口估算分别约 3 页和 1 页，符合要求；但不同字号/窗口会有偏差，已尽量留余量。
4. **未做**：tag、Release、安装包副本文档替换（按要求等审核人合并后）。

---

## 8. 五行摘要

1. 版本号统一切到 2.5.0（启动横幅、API、spec、Inno Setup、许可、打包脚本、夹具、前端缓存号、CI），`audit_check.py` 版本一致性检查通过，安装包名已是 `Setup_v2.5.0.exe`。
2. 重写 `docs/使用说明.txt`（UTF-8 带 BOM，约 3 页）：新增驾驶舱四块与系统状态介绍，五分钟路线按要求改成 5 步，新增"系统会自动做什么/什么一定交给你"和"周报和指标"，FAQ 加两条。
3. 新建 `docs/版本说明.txt`（带 BOM，约 1 页）：只写有用例证据的新增、修复、已知问题与升级须知。
4. 界面词全部照抄产品实际文案，出处表按文件+原文片段列在第 3 节；不确定是产品界面词的（Windows 提示、杀毒软件选项、提问口令）已在文中区分并说明。
5. push 与 PR 两次完整运行均 **GREEN 80/80**，lint `clean`，单元测试 150 项全过；未改 STATUS.md，未打 tag，未建 Release，未碰 C-11 目录。
