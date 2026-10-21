# R-271 完成报告（v2.5.0-rc3 后端修复）

## 交付状态（先说结论）

| 问题 | 状态 |
|---|---|
| 1. 巡航极慢 | **已修复并实测**：根因是扫描之外的模型调用都没关深度思考、上限 120 秒，外加巡航/监听不认 `.md/.txt`。已改为内部任务统一关思考 + ≤2000 tokens + 25 秒上限，文件类型三处共用一份清单，巡航结果新增逐文件/逐阶段/逐次调用耗时。 |
| 2. 版本替代没生效 | **本轮交付**：族相似度按“去掉项目名后的文件名”比较；以文档族当前指针（裁定结果）判定“已被替代”，不用 intake 排名；被替代版本不再生成提议，已生成的待确认提议在裁定时撤回并留审计；`PM-B2-02` 的“生效日期优先于版本号”规则未改动。 |
| 3. 提议文本露出 Markdown 标题 | **已修复并补测**：新增展示文本清理，`\ufeff# 青苗计划实施方案 v` → `青苗计划实施方案 v`，`raw_excerpt` 与正文不动。 |
| 4. 批量审核后 3 条一直无法确认 | **已修复**：根因与第 2 条同源（被替代版本的提议没有任何机制撤下）。裁定时撤回 + 启动时清理旧版本遗留的待确认项，实测批量核准 6 成功 / 0 失败。 |

本轮还要回答审核人三个问题：`PM-U1-01` 的根因、`PM-C4-01` 的结论、真实模型的运行结果；分别在第 2、3、6 节。

## 1. 巡航极慢（已修复）

### 定位过程

1. 用 `scripts/measure_cruise.py` 在 3 个样例文件上分别跑扫描与巡航，逐次调用记录耗时与请求形态（`enable_thinking`、`max_tokens`），mock 侧同时记录每次调用的延迟。
2. 发现巡航的项目阶段（项目说明、项目建议、每日检查）与收敛、复读都不在“扫描文件”上下文里，因此 `chat_completion` 走 chat 分支：**不关思考、`max_tokens=2048`、超时 120 秒**；扫描路径是关思考 + 1500 tokens + 20 秒。
3. 第二个缺口：`scheduler` 与 `watcher` 的支持类型只有 Office/PDF，**不含 `.md/.txt`**（扫描认），用户 3 个样例全是 `.md`，巡航里一个都没解析，时间全压在项目阶段。

### 修法

- `backend/provider/adapter.py`：`INTERNAL_MAX_TOKENS = 2000`、`INTERNAL_CALL_TIMEOUT_SECONDS = 25`；既非助手回答也非扫描文件时统一关思考、限输出、25 秒上限；助手回答保持 3000 / 120 秒。
- `backend/core/file_types.py`（新增）：扫描、监听、巡航共用一份支持类型清单（含 `.md/.txt/.text`）。
- `backend/runtime_core/scheduler.py`：`timing.files[]`（逐文件耗时、候选数、是否成功）、`timing.phases`（captures/convergence/projects/reread/evolution）、`timing.model_calls` 汇总与逐次调用行（任务名、耗时、状态、超时、是否关思考、字符数、token 数）；不记录提示词、请求体、凭据。
- `backend/provider/model_calls.py`：新增 run 上下文，巡航调用记在 `cruise` 通道，记录增加 `task`/`run` 字段。

### 实测耗时

测量方法：`scripts/measure_cruise.py`，mock 模型每次调用固定 2 秒，**未关思考的调用再加 25 秒**；样例为 `D:\PAW\验收样例` 的 3 个 `.md`；前后在各自分支上用同一组参数。

| 场景 | 之前（`a7e305f`） | 本轮 |
|---|---|---|
| 分析（扫描 3 文件） | 17.4–18.2 秒 | 16.4 秒（同机另一次 10.2–10.6 秒，差异来自本机负载） |
| 巡航（先分析再巡航，用户实际流程） | 项目阶段 3 次调用全部未关思考，单项目约 81 秒；3 个项目即 3–4 分钟，且 `.md` 根本没读 | **0.02 秒**（分析已登记这 3 个版本，`claim_source` 拒绝重复分析；项目草稿命中缓存） |
| 巡航做第一条入口（文件未分析） | `.md` 不被解析，项目阶段 27 秒/调用 | 31.6 秒（读 3 文件 ≈20.6 秒，与扫描同一路径；项目草稿 7.07 秒、复读 3.91 秒，全部同步） |
| 单次调用形态 | 项目/建议/收敛：`enable_thinking` 缺省（开）、2048 tokens、120 秒 | 关思考、≤2000 tokens、25 秒 |

## 2. 版本替代（本轮交付）与 `PM-U1-01` 根因

### 原来的两个缺口

1. **族相似度按整串文件名比较**（阈值 0.55）：`青苗计划_方案` 与 `青苗计划_10月周报` 共享项目名前缀被判成“同族”，于是两版方案各进一张“需要你决定”卡，**永不做版本裁定**，v1/v2 都按当前版本各自提议 30 与 40。
2. **旧提议不会被撤下**：即使裁定成功，被替代版本已生成的待确认提议仍留在列表里；提交器按“来源版本已被替换”拒绝，于是那几条永远确认不了。

### 修法

- `backend/pm/versions.py`
  - `similar_family()`：先去掉项目名前缀再比较，同一份文档的正副版本仍然同族，不同文档不再互相牵连；
  - `is_replaced()`：以**文档族当前指针**（`pm_family.current_source_id`，即裁定结果）判定“已被替代”，不用 intake 排名，所以 `PM-B2-02` 的“生效日期优先于版本号”排序保持原样；
  - `withdraw_pending_candidates()`：撤回被替代版本的待确认提议，写 `review_decision`（`reviewer=system:version-superseded`、`review_notes=来源版本已被替换`）留审计；
  - `retire_replaced_candidates()`：启动时清理旧版本遗留的待确认项（幂等），升级到 rc3 的用户不用手工点掉那几条卡住的项。
- `backend/runtime_core/planner.py`：分析前查一次“本版本是否已被替代”，是则不发声；分析后再查一次（覆盖两个版本并行分析的情况），只撤自己的候选。
- `backend/pm/cockpit.py`：目标卡的“由 A 改为 B”在 A 那条提议被撤回后仍然成立——`previous` 允许从已经离开待确认列表（已接受/已撤回）的同字段候选里取，并给出 `previous_source_title`（哪份文件提的 30）。

### `PM-U1-01` 的根因（带日志定位的结论）

`PM-U1-01` 的现象是 `pending_targets: []`、页面既没有 40 也没有生效日期。定位结论：**v2 其实产生了候选，丢的是“旧目标值”**。

本地用与用例相同的 HTTP/DB 链路复现（mock freeform、青禾项目、`青禾计划_v1.docx` → `青禾计划_v2.docx`）：

| 事实 | 值 |
|---|---|
| v2 的 `pm_document_intake` | `family_key=青苗计划_方案`（同时去掉了项目名前缀）、`effective_date=2026-10-08`、`version_json=[2]` |
| `pm_family.current_source_id` | 指向 v2（裁定正确，v1 为 `superseded`） |
| v1 候选的 `review_decision` | 2 条 `reject` / `reviewer=system:version-superseded` / `来源版本已被替换` |
| v2 候选 | 4 条 `pending`（含 40 与 2026-10-08） |

关键在 `cockpit` 计算目标卡：它是一个“由 A 改为 B”的差值，A 过去是从**仍待确认**的同字段候选里取的。撤下 v1 的 30 之后，A 就找不到了 → 目标是 40 的那条候选因为“没有前值”被跳过，页面上 40 与生效日期一起消失。用同一场景对照（临时去掉 cockpit 改动再跑一次）复现了 CI 里的一模一样的结果：`has 40: False / has 2026-10-08: False / pending_targets: []`；改回后为 `40 / 2026-10-08 / 待确认：目标由 30 改为 40`，并带 `previous_source_title=青禾计划_v1.docx`。

CI 侧同样能读到这份证据：`PM-U1-01` 会打印 `C-12 target API evidence`，其中 `pending_targets` 的每一条现在都带有 `previous`、`target`、`pending_label`、`effective_date`、`source_title`、`previous_source_title`，无需看提示词或 Key。

### 版本替代的验证结果（3 个样例文件）

- `pm_family`：`青苗计划_方案` 族当前版本 = v2；`青苗计划_10月周报` 独立成族，没有被误并；
- v1 的 2 条目标提议全部变为 `已拒绝 · 来源版本已被替换`，待确认里只剩 v2 的 40，**没有 30 的提议**；
- 批量核准：**6 成功 / 0 失败**（rc2 实测是 6 成功 / 2 失败）；
- `PM-B2-02` 场景（`方案_v3.docx` 生效 2026-10-01、`方案_v2.docx` 生效 2026-10-14）：当前版本 = v2，v3 为 `superseded`，生效日期优先于版本号。

## 3. `PM-C4-01` 的结论（run 39619207086 / job 119288972546，提交 `9aaf791`）

### 失败的原文（用例报告 `report_PM06.json`）

```
PM-C4-01  status=FAIL  failed_check=exception
Traceback (most recent call last):
  File "...\tests\run_pm_frontend_tests.py", line 88, in run_c06
    pid,_,initial=seed(case,'原型已完成；联调已启动')
  File "...\tests\run_pm_updates_tests.py", line 19, in seed
    sid,_=import_doc(case,'方案_v1.docx','冲刺项目。\n进度与里程碑：'+text+'。')
  File "...\tests\run_pm_versions_tests.py", line 30, in import_doc
    candidates=poll_candidates_from_scanner()
  File "...\tests\run_e2e_tests.py", line 336, in poll_candidates_from_scanner
    return wait_for_scan(candidates, progress, timeout_sec)
  File "...\tests\scanner_wait.py", line 16, in wait_for_scan
    raise TimeoutError(f'Scanner did not reach completed within {timeout_sec}s (last state={state})')
TimeoutError: Scanner did not reach completed within 40s (last state=phase2_analyzing)
```

即：卡住的是**扫描的 phase2**（40 秒轮询一次都没到 `completed`），不是巡航，也不是页面。

### 与后台线程的关系

- 代码层面：`project_analyzer` 与 `provider` 里没有共享锁、没有信号量，后台线程只在最后写结果时短暂拿 `self._lock`；mock 模型是 `ThreadingHTTPServer`，不会把扫描的模型请求排在项目分析后面。**所以“后台线程持有锁”这条路排除。**
- 但同一 job 里这一段 App 启动探针耗时 **31.3 秒**（同一 job 其它用例是 3.1 秒），紧接着 14:56:55 起的 40 秒窗口内既没有扫描完成记录、也没有任何模型调用行；同一提交的 push 侧同一用例通过。属“机器负载把 phase2 拖过 40 秒”还是“两个模型调用方在同一进程里互相拖”，**证据不足以下结论**。
- 按审核人给的处置办法：**把项目草稿改回同步执行**（`projects_deferred` 与 `CruiseProjectAnalysis` 线程整段删除），巡航慢一点换确定性。代价见第 1 节：冷启动巡航 20.7 秒 → 31.6 秒；用户实际流程（先分析再巡航）仍是 0.02 秒。
- 补了回归测试 `test_project_drafts_are_produced_inside_the_cruise`：项目草稿必须在巡航线程上跑、结果里不再有 `projects_deferred`、进程里不允许再出现 `CruiseProjectAnalysis` 线程。

## 4. 提议文本露出 Markdown 标题（已修复）

- 原因：模型 payload 的标题沿用原文首行（含 BOM 与 `# `）。
- 修法：`backend/pm/display_text.py`，候选落库前清理展示字段（标题/名称/指标名/摘要等）的 BOM、标题符号、引用符、列表符号、代码围栏、强调符，并把换行压成空格；`raw_excerpt`、`content`、正文一律不动。
- 实测：`\ufeff# 青苗计划实施方案 v` → `青苗计划实施方案 v`。

## 5. 验收脚本与判定

未改：`tests/paw_case.py`、`tests/paw_verdict.py`、`tests/paw_lint.py`、`tests/required_cases*.json`、`tests/pending/**`、`.github/workflows/test.yml`、题目与判定、前端文件。未删除文件、未 force push、未动 `D:\PAW\参赛提交`；代码、日志、报告中没有真实 Key 与提示词。

## 6. 真实模型运行

`real-model.yml` 用 secret 里的 Key 跑安装版验收（深浅思考、3000/2000/1500 tokens 与 25 秒上限都在真实模型下走一遍），逐次调用耗时见该运行的 `Real Model Acceptance` 日志（`Model call: file=… seconds=… status=… timed_out=… tokens=… thinking_disabled=…`，不含提示词与 Key）与 `real-model-report` 产物。**运行号与逐次调用汇总见本轮审核回复**（报告中不写运行号，避免再为一份文档追加提交）。

## 7. 回归测试

`tests/unit/test_r271_rc3.py`（15 项）：

- 巡航：三条链路共用同一份支持类型、逐文件与分阶段耗时、逐次调用记录在 `cruise` 通道且不含提示词/Key、内部调用关思考且 ≤2000 tokens、**项目草稿必须在巡航线程同步执行**；
- 版本替代：`similar_family` 不被项目名前缀骗、`is_replaced` 以裁定为准、被替代版本不发声、旧提议撤离待确认（审计可查）、当前版本提议不受影响、启动清理幂等；
- 展示文本：标题/BOM/列表符清理、只清展示字段、标签不会被清空；
- 目标卡：撤回旧提议后仍显示“由 30 改为 40”并给出提出 30 的文件名。

## 8. 改动清单

- `backend/provider/adapter.py`、`backend/provider/model_calls.py`、`backend/core/file_types.py`（新增）
- `backend/runtime_core/scheduler.py`（耗时记录 + 项目草稿改回同步）、`scanner.py`、`backend/watcher/observer.py`
- `backend/runtime_core/planner.py`（展示文本清理 + 被替代版本不发提议）
- `backend/pm/versions.py`（族相似度、裁定判定、撤回、启动清理、版本证据日志）、`backend/pm/cockpit.py`（目标卡前值来源与证据）、`backend/pm/display_text.py`（新增）
- `backend/main.py`（启动时清理被替代版本的遗留待确认项）
- `scripts/mock_openai_server.py`（可选延迟模型，默认 0）、`scripts/measure_cruise.py`（新增）
- `tests/unit/test_r271_rc3.py`（新增）、`tests/unit/test_scan_model_calls.py`、`tests/unit/test_answer_model_calls.py`

## 9. 检查结果

- `python -m pytest tests/unit -q`：243 passed。
- `python tests/paw_lint.py`：lint clean。
- 本地端到端（mock + 真实 HTTP 链路）：版本替代只剩 v2 的 40、v1 两条转已拒绝、批量核准 6/0；`PM-U1-01` 目标卡 `待确认：目标由 30 改为 40` 正常；`PM-B2-02` 当前版本 = v2。
- push 侧与 PR 侧完整流水线：本轮最终提交各运行一次，结果与运行号见审核回复。

执行者进度：R-271，PR #22；本轮最终提交的 push 侧完整流水线与 PR 侧完整流水线各只运行一次，真实模型验收在同一提交上派发，三者运行号与结果见审核回复；未改审核人文件与前端、未 force push、未删文件、未动参赛提交、报告内无 Key 与提示词；停下原因：无，等待审核人复核。
