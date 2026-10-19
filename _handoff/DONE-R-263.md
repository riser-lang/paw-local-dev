# DONE-R-263：修复扫描 Phase 2 调用模型过慢

## 结论

先测量再改。在旧代码上先加"每次模型调用记一行"的明细（commit `c11e943`，真实模型运行 `39242072868`），量到慢的原因不是网络也不是 Phase 1，而是 Phase 2 那一次 `propose` 调用：**深度思考 token 没关 + 45 秒超时后原样重发同一个请求 + 没有 max_tokens**。同一个文件关掉思考后 completion 从 3541 token 降到 457、reasoning 从 3139 降到 0，耗时从 44.83 秒降到 5.76 秒。

修复后真实模型运行 `39243246729`：4 个样例文件 Phase 2 合计 **23.3 秒**，整个扫描 **25.0 秒**（原 211.9 / 214.7 秒），0 次超时、0 次重试、0 次规则兜底、reasoning token 为 0。240 秒等待上限没有改。

8 道题 0/8：全部 8 题都在 **15.0 秒客户端读取超时**，请求没有返回，因此没有回答原文和来源。问答调用按任务要求没有动，`timed_answer` 的 15 秒等待也属"不改等待时间"的禁改范围，所以这里停下等审核人决定（详见"需要审核人决定"）。

## 改了哪些文件、为什么

| 文件 | 改动 | 原因 |
|---|---|---|
| `backend/provider/model_calls.py`（新增） | 扫描上下文 + 每次调用一行的遥测（文件名、来源、第几次、秒数、HTTP 状态、是否超时、prompt 字符数、prompt/completion/reasoning token、是否规则兜底） | 任务要求"先测量，不要猜"；只在扫描文件上下文里记录，聊天问答的调用不记录、不受影响；只存字符数不存请求内容和 Key |
| `backend/provider/adapter.py` | scan 路径请求体加 `enable_thinking=false`、`max_tokens=1500`；单次超时 45→20 秒；**只对 429/503 重试**；超时/其它失败立即改规则兜底不再重发；400 拒绝 `enable_thinking` 时去掉该参数重试一次并记录；每次尝试写一行遥测。`chat_completion` 只在扫描上下文内改请求形状（关思考、max_tokens、20 秒超时、429/503 重试），扫描上下文之外的聊天问答请求体与 120 秒超时保持原样 | 三条可疑点都成立，且超时重发是第二大浪费 |
| `backend/runtime_core/planner.py` | `plan_document_impact` 里用 `model_calls.begin_file(display_name)` 包住 `process_source(file_hash, provider)` 和 `provider.propose(...)`，`finally` 复位 | 让每次调用能归属到具体文件 |
| `backend/pm/semantic.py` | `invoke` 里给 `chat_completion` 调用套 `model_calls.begin_task(name)` | 调用来源能标出 `section_update` / `project_advice` 等 |
| `backend/runtime_core/scanner.py` | 扫描开始时 `model_calls.reset()`；`get_progress()` 增加 `model_calls`（逐次明细）和 `model_call_summary`（汇总） | 明细要能从进度接口读到，才能进验收报告 |
| `tests/run_real_model_acceptance.py` | 报告新增 `model_calls`、`model_call_summary`、`phase1_file_timings`、`phase2_file_timings`、`phase2_seconds`、`scan_seconds`；进度行补 `model_calls/timeouts/rule_fallbacks`；结束后把每次调用明细、每文件 Phase 2 耗时打印到日志（不含请求内容与 Key） | 任务要求明细写进 real-model 报告 |
| `tests/unit/test_scan_model_calls.py`（新增） | 11 个用例 | 覆盖超时不重试、关闭思考参数、400 回退、429/503 重试、500 不重试、聊天请求形状不变、遥测脱敏 |

没有改：`tests/paw_case.py`、`tests/paw_verdict.py`、`tests/paw_lint.py`、`tests/required_cases*.json`、`tests/pending/**`；没有改 240 秒上限、8 道题和判定；没有在 mock 或产品代码里写死样例/考题答案；没有删除文件、force push、合并 PR 或打 tag。

## 测量明细（旧代码，commit `c11e943`，运行 `39242072868`）

真实模型 `deepseek-ai/DeepSeek-V4.1-Flash`，SiliconFlow。每次调用一行（`source` 全部是 `propose`）：

| 文件 | 来源 | 尝试 | 耗时(秒) | HTTP | 超时 | prompt 字符 | prompt/completion/reasoning tokens | 规则兜底 |
|---|---|---:|---:|---:|---|---:|---|---|
| 青禾试点详情.md | propose | 1 | 44.83 | 200 | 否 | 660 | 334/3541/3139 | 否 |
| 青禾试点周报.pptx | propose | 1 | 45.71 | – | **是** | 707 | 0/0/0 | 否（继续重试） |
| 青禾试点周报.pptx | propose | 2 | 45.70 | – | **是** | 707 | 0/0/0 | 否（继续重试） |
| 青禾试点周报.pptx | propose | 3 | 12.98 | 200 | 否 | 707 | 365/1172/471 | 否 |
| 青禾试点计划_v2.docx | propose | 1 | 13.83 | 200 | 否 | 696 | 361/1335/586 | 否 |
| 青禾试点计划_v1.docx | propose | 1 | 45.00 | 200 | 否 | 665 | 339/3670/3257 | 否 |

每文件耗时：Phase 1 四个文件各 0.7 秒；Phase 2 `青禾试点详情.md` 44.9 秒、`青禾试点周报.pptx` 107.6 秒、`青禾试点计划_v2.docx` 14.1 秒、`青禾试点计划_v1.docx` 45.3 秒，合计 211.9 秒；扫描 214.7 秒；6 次调用合计 208.1 秒；reasoning 合计 7453 token。

`section_update` 一次也没有出现：这四个样例文件没有归属到任何 `pm_family`，`backend/pm/updates.py` 的 `process_source` 在调用模型之前就返回 `needs_assignment`，所以本次全部是 `propose`。

### 从测量读出的三条

1. **贵在思考 token**：prompt 只有 660 字符（334 token），completion 3541 里 3139 是 reasoning。关掉思考后同一文件 completion 457、reasoning 0、5.76 秒（修复后运行里实测）。
2. **超时后原样重发是第二个浪费点**：`青禾试点周报.pptx` 连续两次 45.71/45.70 秒超时，第 3 次才成功；单文件 107.6 秒里 91.4 秒是同一个请求发了两遍。旧代码在"任何异常"上都会重试两次。
3. **prompt 不大、调用次数不多**：prompt 660–707 字符（309–365 token）；每文件 1 次调用（pptx 因超时被调 3 次，全部来自重试）。因此本次没有精简 prompt：证据已经是有界切片（正文 10 块 × 100 字，表格 8 表 × 10 行 × 500 字），再删就会丢掉"目标/版本/风险/下一步"这些判断需要的原文依据。

## 修复内容（commit `efc8d78`）

- 扫描请求带 `enable_thinking=false`；`max_tokens=1500`（输出是小 JSON 数组）。供应商 400 拒绝该参数时，去掉 `enable_thinking` 重试一次，模型不认识这个参数也能正常返回（遥测里记 `thinking_param_fallback`）。
- 单次超时 45 → **20 秒**（扫描调用）；聊天问答仍是 120 秒。
- **只对 429/503 重试**（最多 2 次，指数退避）。超时、连接错误、5xx、空结果一律不再重发，直接改规则兜底，并在遥测 `fallback_rule` 和进度行的 `rule_fallbacks` 里如实标注。

## 修复后测量明细（commit `efc8d78`，运行 `39243246729`）

| 文件 | 来源 | 尝试 | 耗时(秒) | HTTP | 超时 | prompt 字符 | prompt/completion/reasoning tokens | 规则兜底 | 关思考 |
|---|---|---:|---:|---:|---|---:|---|---|---|---|
| 青禾试点周报.pptx | propose | 1 | 6.67 | 200 | 否 | 707 | 340/615/0 | 否 | 是 |
| 青禾试点详情.md | propose | 1 | 5.76 | 200 | 否 | 660 | 309/457/0 | 否 | 是 |
| 青禾试点计划_v2.docx | propose | 1 | 5.87 | 200 | 否 | 696 | 336/533/0 | 否 | 是 |
| 青禾试点计划_v1.docx | propose | 1 | 4.04 | 200 | 否 | 665 | 314/316/0 | 否 | 是 |

Phase 2 每文件：`青禾试点周报.pptx` 6.8 秒、`青禾试点详情.md` 6.2 秒、`青禾试点计划_v2.docx` 6.1 秒、`青禾试点计划_v1.docx` 4.2 秒，合计 **23.3 秒**；扫描全部 **25.0 秒**（上限仍是 240 秒）。汇总：`calls=4 timeouts=0 failed=0 rule_fallbacks=0 model_seconds=22.3 reasoning_tokens=0`。

## 8 道题

| ID | 问题 | 结果 | 问题/原因 | 回答原文 | 来源 |
|---|---|---|---|---|---|
| PM-U1-03 | 青禾试点计划目标是多少？ | 未能运行 | `ReadTimeout`：15.0 秒客户端读取超时，请求未返回 | 无 | 无 |
| PM-U4-01 | 青禾试点后续最好如何优化？ | 未能运行 | 同上 | 无 | 无 |
| PM-S1-03 | 青禾试点详情.md 里写了什么？ | 未能运行 | 同上 | 无 | 无 |
| X01 | 星火导师计划第一期要培养多少名导师？ | 未能运行 | 同上 | 无 | 无 |
| X02 | 星火导师计划的导师认证通过率目标是多少？ | 未能运行 | 同上 | 无 | 无 |
| X18 | 星火导师计划最早的 v1 方案里原定培养多少名导师？ | 未能运行 | 同上 | 无 | 无 |
| X19 | 星火导师计划后续最好如何优化？ | 未能运行 | 同上 | 无 | 无 |
| X20 | 人才盘点下一步应该做什么？ | 未能运行 | 同上 | 无 | 无 |

通过数 **0/8**（8 题都在 15.0 秒客户端读取超时，判题逻辑没有执行，不代表判题失败）。两次运行的日志里每题都是 `elapsed_seconds=15.00xx timings=no response / request failed`。

对照证据：运行 A 里 `PM-S1-03` 是唯一在 15 秒内返回的题，`timings={'model_calls': 1, 'model_seconds': 9.3483, 'prompt_bytes': 6173, 'total_seconds': 9.4645}`。也就是真实模型单次问答本身就要 9–15 秒以上，`tests/run_e2e_tests.py:1066` 的 `timed_answer` 固定 `timeout=15.0`，用这个上限测不到真实模型的问答。

## 需要审核人决定

问答链路的等待和思考不在本次授权内（"聊天问答的调用不要动"、"不改等待时间"），所以我停在这里，请审核人二选一：

- (a) 允许把验收侧 `timed_answer` 的客户端等待从 15 秒提到 60 秒左右（`tests/run_e2e_tests.py:1066`，只影响测量，不改判题）；
- (b) 允许问答调用也关掉深度思考（属聊天调用；同一模型扫描路径关掉后单次 4–7 秒）。

任一放开后重跑 `real-model.yml` 就能拿到 8 题的真实结果；扫描侧已经不再是瓶颈（25.0 秒）。

## 单元测试与本地检查

- 新增 `tests/unit/test_scan_model_calls.py`（11 例），其中任务要求的两类：
  - "超时不重试"：`test_propose_timeout_is_not_resent_and_falls_back_to_rules`、`test_scan_section_update_timeout_is_not_resent`（断言只发出 1 个请求、遥测 `timed_out=True`、propose 走规则兜底）；另加 `test_propose_does_not_retry_after_a_server_error`。
  - "关闭思考参数"：`test_propose_disables_thinking_and_bounds_output`、`test_scan_chat_completion_disables_thinking`、`test_propose_drops_unsupported_thinking_parameter_instead_of_failing`（400 回退后仍拿到 AI 结果），以及 `test_chat_completion_outside_scan_keeps_the_chat_request_shape` 守住聊天调用不被改。
- `python tests/paw_lint.py`：lint clean；`python -m pytest tests/unit -q`：188 passed。
- 本地还用 freeform mock 起了一次完整扫描（`run.py` + `scripts/mock_openai_server.py`）：4 文件 Phase 2 共 4 次 `propose`、9.1 秒、每次 `thinking_disabled=true`，进度接口能读到明细。

## CI（均为完整流水线，GREEN）

- 测量提交 `c11e943`：push unit `39242029052` GREEN、PR unit `39242034752` GREEN。
- 修复提交 `efc8d78`：push unit `39242836231` GREEN；PR unit `39242842264` GREEN；push verification pipeline `39242836329` GREEN；PR verification pipeline `39242842129` GREEN（含 mock freeform 的 Real Model Acceptance Smoke）。
- 本 DONE 是纯文档提交，推送后按同一套流水线单独运行。

## 运行编号

- 真实模型测量（旧代码）：`39242072868`，commit `c11e94336a8623e2f2ef8c8eea5dcadee132eb33`。
- 真实模型验证（修复后）：`39243246729`，commit `efc8d788d06b3334286263abdd884860e115e419`。
- 两次报告都从 artifact `real-model-report` 下载核对（`.r263-runA` / `.r263-runB`，未提交）。

## 停止点

扫描目标已达成（Phase 2 23.3 秒、整次扫描 25.0 秒，240 秒上限未动），8 道题卡在验收侧 15 秒回答等待上，改它或改问答调用都超出本次规则，故停下等审核人。

执行者进度：R-263，PR #15；push CI 编号 39242836329 结果 GREEN、PR CI 编号 39242842129 结果 GREEN；真实模型运行编号 39243246729；扫描用时 25.0 秒（Phase 2 23.3 秒）；8 题通过数 0/8；停下原因：8 题全部在 15.0 秒客户端读取超时（问答等待上限与问答思考不在本次可改范围），等审核人决定是否放开来测量。
