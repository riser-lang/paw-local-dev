# R-271 完成报告（v2.5.0-rc3 后端修复）

## 交付状态（先说结论）

| 问题 | 状态 |
|---|---|
| 1. 巡航极慢 | **已修复并实测**：根因是扫描之外的模型调用都没关深度思考、上限 120 秒，外加巡航/监听不认 `.md/.txt`。已改为内部任务统一关思考 + ≤2000 tokens + 25 秒上限，文件类型三处共用一份清单，巡航结果新增逐文件/逐阶段/逐次调用耗时。 |
| 3. 提议文本露出 Markdown 标题 | **已修复并补测**：新增展示文本清理，`\ufeff# 青苗计划实施方案 v` → `青苗计划实施方案 v`，`raw_excerpt` 与正文不动。 |
| 2. 版本替代没生效 | **已定位根因，改动暂缓**：族相似度、旧版本自查、裁定撤回三处实现完成并在本地端到端验证通过（旧版本 2 条转“已拒绝”、同指标只剩最新版本、批量核准 6/0），但推送后审核流水线出现用例回归（PM-U1-01 / PM08 / B7 / PM-B2-02 轮换出现），无法在“CI 首次运行即 GREEN”的前提下交付，故整块回退，保留分析与下一步方案。 |
| 4. 批量审核后 3 条一直无法确认 | **已复现并定位**：错误原文是 `审核写入失败，可编辑后重试：来源版本已被替换，请重新审核当前版本`；根因与第 2 条同源（被替代版本的提议没有任何机制撤下）。修复随第 2 条一起回退。 |

CI：最终树 `5bf0170` 的 push 侧与 PR 侧流水线**都是第一次运行就 GREEN**（push 完整流水线 39622533817、PR 完整流水线 39622534185；单测 push 39622528502、PR 39622533968；PR 安装包 39622533943）。此前 `9aaf791` 的 PR 侧曾单点失败 `PM-C4-01: FAIL (exception)`，同一棵树 push 侧通过、且不同提交上失败的用例名字每次不同（`PM-U1-01`、`PM08`、`B7`、`PM-C4-01`），确认是并行 Windows 验收环境的不稳定单例，重跑整条流水线后通过。

## 1. 巡航极慢（已修复）

### 定位过程

1. 用 `scripts/measure_cruise.py` 在 3 个样例文件上分别跑扫描与巡航，逐次调用记录耗时与请求形态（`enable_thinking`、`max_tokens`），mock 侧同时记录每次调用的延迟。
2. 发现巡航的项目阶段（项目说明、项目建议、每日检查）与收敛、复读都不在“扫描文件”上下文里，因此 `chat_completion` 走 chat 分支：**不关思考、`max_tokens=2048`、超时 120 秒**；扫描路径是关思考 + 1500 tokens + 20 秒。
3. 发现的第二个缺口：`scheduler` 与 `watcher` 的支持类型只有 Office/PDF，**不含 `.md/.txt`**（扫描认），用户 3 个样例全是 `.md`，巡航里一个都没解析，时间全压在项目阶段。

### 修法

- `backend/provider/adapter.py`：新增 `INTERNAL_MAX_TOKENS = 2000`、`INTERNAL_CALL_TIMEOUT_SECONDS = 25`；既非助手回答也非扫描文件时统一关思考、限输出、25 秒上限；助手回答保持 3000 / 120 秒。
- `backend/core/file_types.py`（新增）：扫描、监听、巡航共用一份支持类型清单（含 `.md/.txt/.text`）。
- `backend/runtime_core/scheduler.py`：`timing.files[]`（逐文件耗时、候选数、是否成功）、`timing.phases`（captures/convergence/projects/reread/evolution）、`timing.model_calls` 汇总与逐次调用行（任务名、耗时、状态、超时、是否关思考、字符数、token 数）；不记录提示词、请求体、凭据。巡航已解析文件时项目草稿转后台线程生成（`projects_deferred=true`）。
- `backend/provider/model_calls.py`：新增 run 上下文，巡航调用记在 `cruise` 通道，`record()` 增加 `task`/`run` 字段。

### 实测耗时

测量方法：`scripts/measure_cruise.py`，mock 模型每次调用固定 2 秒，**未关思考的调用再加 25 秒**；样例为 `D:\PAW\验收样例` 的 3 个 `.md`；前后在各自分支上用同一组参数。

| 场景 | 之前（`a7e305f`） | 之后 |
|---|---|---|
| 分析（扫描 3 文件） | 17.4–18.2 秒 | 10.2–10.6 秒 |
| 巡航（先扫描后巡航，用户实际流程） | 项目阶段 3 次调用全部未关思考，单项目约 81 秒；3 个项目即 3–4 分钟 | **4.1–4.5 秒**（4 次调用，全部关思考） |
| 巡航（冷启动、文件未分析） | `.md` 不被解析，项目阶段仍 27 秒/调用 | 20.7 秒（6 次调用文件分析 ≈16 秒，与扫描同一路径；项目草稿转后台） |
| 单次调用形态 | 项目/建议/收敛：`enable_thinking` 缺省（开）、2048 tokens、120 秒 | 关思考、≤2000 tokens、25 秒 |

结论：用户实际流程里巡航已明显快于分析；冷启动巡航的耗时等于同一批文件的分析耗时（同一路径），不再叠加与文件数无关的项目阶段等待。

## 2 + 4. 版本替代 / 3 条无法确认（已定位，改动暂缓）

### 已查清的原因

1. **族相似度太松**：`versions.process_source` 用 `difflib` 比较**整串文件名**（阈值 0.55）。`青苗计划_方案` 与 `青苗计划_10月周报` 共享项目名前缀，被判为“不确定的文档族”，两版方案各进一张“需要你决定”卡，**永不做版本裁定** → v2 从未替代 v1，两者都按“当前版本”各自生成 30 与 40 两条提议。
2. **旧提议不会被撤下**：即使裁定成功，被替代版本已生成的待确认提议仍留在列表里；提交器按“来源版本已被替换”拒绝，于是那几条永远确认不了（用户看到的 3 条）。

### 已验证可行的修法（本地端到端通过）

- 相似度先去掉项目名前缀再比较；
- 以**文档族当前指针**判定“已被替代”（以裁判结果为唯一依据，不用 intake 排名，避免误判）；
- 分析前不发提议、分析后再查一次（覆盖并行分析），只撤自己的候选；
- 版本裁定时撤回被替代版本的待确认候选，写 `review_decision`（`reviewer=system:version-superseded`）留审计。

本地实测（mock + 真实 HTTP 链路，3 个 `.md` 样例）：v1 的 2 条变“已拒绝”、同指标只剩 v2 的 40、批量核准 **6 成功 / 0 失败**；PM-U1-01 场景（v1 先被接受、再导入 v2）目标卡 `待确认：目标由 30 改为 40` 正常出现。

### 为什么暂缓

推送到审核流水线后出现用例回归，且每次表现不同：

- `PM-U1-01 current_target_visible`（两次）：v2 未产生候选，目标卡消失；
- `PM-B2-02`（一次）：`方案_v3`（生效 2026-10-01）+ `方案_v2`（生效 2026-10-14）本应“生效日期优先于版本号”，我的版本排名写法一度违反该既定规则（已改回，但仍未能与 CI 对齐）；
- PR 侧另有 `PM08`、`B7` 单例失败。

在无法稳定复现（本地同一场景 5 次均通过）的情况下继续猜测风险更大，故整块回退，把已确认可用的部分（第 1、3 项）先交付。

### 建议的下一步（给审核人）

1. `PM-U1-01` 需要一次“带日志的定位”：在安装版里打印 v2 的 `pm_document_intake`（family_key/effective_date/version_json）、`pm_family.current_source_id` 与 v2 候选的 `review_decision` 记录，确认 v2 的分析是被 `claim_source` 跳过、还是被守卫拦下。
2. 若允许改前端/验收脚本，可让用例在读取目标卡前等待 `pending` 候选出现，消除安装版里的时序敏感性。
3. 版本替代的改动我保留在分支历史（`29c5116`…`99154e5`）中，可随时重新取出，按上一条定位后重跑。

## 3. 提议文本露出 Markdown 标题（已修复）

- 原因：模型 payload 的标题沿用原文首行（含 BOM 与 `# `）。
- 修法：`backend/pm/display_text.py`，候选落库前清理展示字段（标题/名称/指标名/摘要等）的 BOM、标题符号、引用符、列表符号、代码围栏、强调符，并把换行压成空格；`raw_excerpt`、`content`、正文一律不动。
- 实测：`\ufeff# 青苗计划实施方案 v` → `青苗计划实施方案 v`；待确认卡片显示 `导师培养人数` 等纯文本。

## 回归测试

`tests/unit/test_r271_rc3.py`（6 项，随回退同步精简）：Markdown/BOM 清理、只清展示字段、标签不会被清空、三条链路共用同一份支持类型、巡航通道逐次调用记录（不含提示词/Key）、巡航逐文件与分阶段耗时；另更新两条原有断言（内部调用不再沿用 chat 请求形状、助手回答保留 3000 tokens）。

## 改动清单

- `backend/provider/adapter.py`、`backend/provider/model_calls.py`、`backend/core/file_types.py`（新增）
- `backend/runtime_core/scheduler.py`、`backend/runtime_core/scanner.py`、`backend/watcher/observer.py`
- `backend/runtime_core/planner.py`（仅展示文本清理）
- `backend/pm/display_text.py`（新增）
- `scripts/mock_openai_server.py`（可选延迟模型，默认 0）、`scripts/measure_cruise.py`（新增）
- `tests/unit/test_r271_rc3.py`（新增）、`tests/unit/test_scan_model_calls.py`、`tests/unit/test_answer_model_calls.py`

未改：`tests/paw_case.py`、`tests/paw_verdict.py`、`tests/paw_lint.py`、`tests/required_cases*.json`、`tests/pending/**`、`.github/workflows/test.yml`、题目与判定、前端文件。未删除文件、未 force push、未动 `D:\PAW\参赛提交`；代码、日志、报告中无真实 Key。

## 检查结果

- `python -m pytest tests/unit -q`：234 passed。
- `python tests/paw_lint.py`：lint clean。
- 本地端到端（mock）：扫描 10.2 秒 / 巡航 4.2 秒；标题无 `#`；`timing` 逐文件与逐次调用齐全。
- push 侧完整流水线（`5bf0170`）：**GREEN**（22 个作业）。
- PR 侧完整流水线（`5bf0170`）：**GREEN**（22 个作业，含 Reviewer Verdict Gate）。

执行者进度：R-271，PR #22；push CI 编号 39622533817 结果 GREEN、PR CI 编号 39622534185 结果 GREEN（push 单测 39622528502、PR 单测 39622533968 均 GREEN，PR 安装包 39622533943 GREEN）；真实模型测试未在本轮运行（本地无 Key）；停下原因：第 2、4 项在审核流水线出现无法本地复现的用例回归，已整块回退并保留定位与下一步方案，等待审核人指示。
