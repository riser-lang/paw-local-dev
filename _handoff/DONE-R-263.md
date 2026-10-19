# DONE-R-263：修复扫描 Phase 2 调用模型过慢

## 结论

先测量再改。在旧代码上先加"每次模型调用记一行"的明细（commit `c11e943`，真实模型运行 `39242072868`），量到慢的原因不是网络也不是 Phase 1，而是 Phase 2 那一次 `propose` 调用：**深度思考 token 没关 + 45 秒超时后原样重发同一个请求 + 没有 max_tokens**。同一个文件关掉思考后 completion 从 3541 token 降到 457、reasoning 从 3139 降到 0，耗时从 44.83 秒降到 5.76 秒。

修复后（commit `efc8d78`，运行 `39243246729`）：4 个样例文件 Phase 2 合计 **23.3 秒**、整个扫描 **25.0 秒**；再跑一次（commit `3eb285c`，运行 `39249795534`）Phase 2 **24.6 秒**、扫描 **27.5 秒**，两次都 0 超时、0 重试、0 规则兜底、reasoning token 为 0。240 秒等待上限没有改。

8 道题：**0/8**，但原因已经不在扫描。经确认后只把 real-model 验收的回答等待从 15 秒放宽到 60 秒（`timed_answer` 默认仍 15 秒，其它 18 个 e2e 块不变），运行 `39249795534` 的结果是 2 题拿到回答但判定不通过、6 题在 60 秒读取超时。真实模型单次问答就要 9.9–53.5 秒（放行门失败还会重试一次），另有 5 题（X01/X02/X18/X19/X20）问的是星火导师计划/人才盘点，而本验收只创建 4 个青禾样例、从未把这些语料放进工作台，所以这 5 题没有依据可答。8/8 需要审核人在验收口径上做决定，不是扫描性能问题。

## 改了哪些文件、为什么

| 文件 | 改动 | 原因 |
|---|---|---|
| `backend/provider/model_calls.py`（新增） | 扫描上下文 + 每次调用一行的遥测（文件名、来源、第几次、秒数、HTTP 状态、是否超时、prompt 字符数、prompt/completion/reasoning token、是否规则兜底） | 任务要求"先测量，不要猜"；只在扫描文件上下文里记录，聊天问答的调用不记录、不受影响；只存字符数不存请求内容和 Key |
| `backend/provider/adapter.py` | scan 路径请求体加 `enable_thinking=false`、`max_tokens=1500`；单次超时 45→20 秒；**只对 429/503 重试**；超时/其它失败立即改规则兜底不再重发；400 拒绝 `enable_thinking` 时去掉该参数重试一次并记录；每次尝试写一行遥测。`chat_completion` 只在扫描上下文内改请求形状（关思考、max_tokens、20 秒超时、429/503 重试），扫描上下文之外的聊天问答请求体与 120 秒超时保持原样 | 三条可疑点都成立，且超时重发是第二大浪费 |
| `backend/runtime_core/planner.py` | `plan_document_impact` 里用 `model_calls.begin_file(display_name)` 包住 `process_source(file_hash, provider)` 和 `provider.propose(...)`，`finally` 复位 | 让每次调用能归属到具体文件 |
| `backend/pm/semantic.py` | `invoke` 里给 `chat_completion` 调用套 `model_calls.begin_task(name)` | 调用来源能标出 `section_update` / `project_advice` 等 |
| `backend/runtime_core/scanner.py` | 扫描开始时 `model_calls.reset()`；`get_progress()` 增加 `model_calls`（逐次明细）和 `model_call_summary`（汇总） | 明细要能从进度接口读到，才能进验收报告 |
| `tests/run_real_model_acceptance.py` | 报告新增 `model_calls`、`model_call_summary`、`phase1/phase2_file_timings`、`phase2_seconds`、`scan_seconds`、`answer_timeout_seconds`，每题记录 `timings`；进度行补 `model_calls/timeouts/rule_fallbacks`；结束后打印每次调用明细、每文件 Phase 2 耗时、每题状态与耗时（不含请求内容与 Key） | 任务要求明细写进 real-model 报告；8 题需要逐题证据 |
| `tests/run_e2e_tests.py` | `timed_answer` 增加可选参数 `timeout`，**默认仍是 15.0 秒** | 真实模型单次回答 9.9–53.5 秒，15 秒量不到任何一题；默认值不变，其它块行为不动 |
| `tests/run_real_model_acceptance.py` | 仅 real-model 验收传 `timeout=60.0`（`ANSWER_TIMEOUT_SECONDS`） | 同上；240 秒扫描上限、8 道题、判定、产品聊天调用都不动。这一处超出了任务原来的"不改等待时间"，已单独向派单人确认后实施，只影响 real-model 验收这一条测量线 |
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

## 修复后测量明细

运行 `39243246729`（commit `efc8d78`）：

| 文件 | 来源 | 尝试 | 耗时(秒) | HTTP | 超时 | prompt 字符 | prompt/completion/reasoning tokens | 规则兜底 | 关思考 |
|---|---|---:|---:|---:|---|---:|---|---|---|---|
| 青禾试点周报.pptx | propose | 1 | 6.67 | 200 | 否 | 707 | 340/615/0 | 否 | 是 |
| 青禾试点详情.md | propose | 1 | 5.76 | 200 | 否 | 660 | 309/457/0 | 否 | 是 |
| 青禾试点计划_v2.docx | propose | 1 | 5.87 | 200 | 否 | 696 | 336/533/0 | 否 | 是 |
| 青禾试点计划_v1.docx | propose | 1 | 4.04 | 200 | 否 | 665 | 314/316/0 | 否 | 是 |

Phase 2 每文件：`青禾试点周报.pptx` 6.8 秒、`青禾试点详情.md` 6.2 秒、`青禾试点计划_v2.docx` 6.1 秒、`青禾试点计划_v1.docx` 4.2 秒，合计 **23.3 秒**；扫描 **25.0 秒**。汇总：`calls=4 timeouts=0 failed=0 rule_fallbacks=0 model_seconds=22.3 reasoning_tokens=0`。

运行 `39249795534`（commit `3eb285c`，回答等待放宽到 60 秒后复跑）复核：Phase 2 每文件 7.0 / 6.0 / 6.6 / 5.0 秒，合计 **24.6 秒**；扫描 **27.5 秒**；`calls=4 timeouts=0 failed=0 rule_fallbacks=0 reasoning_tokens=0`。两次独立运行都在 30 秒内，距 240 秒上限有很大余量。

## 8 道题

运行 `39249795534`（`answer_timeout_seconds=60.0`，报告里逐题记录了 `timings`）：

| ID | 问题 | 结果 | 问题/原因 | 回答原文 | 来源 |
|---|---|---|---|---|---|
| PM-U1-03 | 青禾试点计划目标是多少？ | 未能运行 | `ReadTimeout`：60 秒客户端读取超时 | 无 | 无 |
| PM-U4-01 | 青禾试点后续最好如何优化？ | 未能运行 | 同上 | 无 | 无 |
| PM-S1-03 | 青禾试点详情.md 里写了什么？ | FAIL | 10.03 秒返回（1 次模型调用 9.89 秒）。判定要求答案逐字包含「本地扫描正文样本：青禾试点已完成首轮访谈，下一步整理访谈纪要。」，模型把原文改写成「青禾试点已完成首轮访谈，下一步整理访谈纪要。」（前缀被挪到叙述里、还加了 `CONFIRMED_KNOWLEDGE:` 标记），逐字子串不命中 | 「结论/答案 青禾试点详情.md 目前在工作台中可查到的正文内容是：「青禾试点已完成首轮访谈，下一步整理访谈纪要。」 即该文件记录了两条信息——首轮访谈已完成，以及下一步动作是整理访谈纪要。…」（共 610 字） | 青禾试点详情.md（资料证据，非正式结论） |
| X01 | 星火导师计划第一期要培养多少名导师？ | 未能运行 | `ReadTimeout`：60 秒客户端读取超时 | 无 | 无 |
| X02 | 星火导师计划的导师认证通过率目标是多少？ | 未能运行 | 同上 | 无 | 无 |
| X18 | 星火导师计划最早的 v1 方案里原定培养多少名导师？ | FAIL | 55.47 秒返回（2 次模型调用合计 53.54 秒，放行门重试了一次），模型拒答；本次验收只扫描了 4 个青禾样例，工作台里没有星火语料 | 「当前没有足够的已确认工作台依据来回答这个问题。请先明确项目或补充可核对的信息。」（39 字） | 青禾试点计划_v1.docx、青禾试点计划_v2.docx、青禾试点详情.md、青禾试点周报.pptx（资料证据，非正式结论） |
| X19 | 星火导师计划后续最好如何优化？ | 未能运行 | `ReadTimeout`：60 秒客户端读取超时 | 无 | 无 |
| X20 | 人才盘点下一步应该做什么？ | 未能运行 | 同上 | 无 | 无 |

通过数 **0/8**（2 题有回答但未达判定，6 题 60 秒读取超时，判题逻辑没有执行的部分不算判题失败）。

从这次运行读出的三条（都不是扫描性能问题）：

1. **回答链路慢**：单次回答 9.89 秒（PM-S1-03）、两次调用 53.54 秒（X18），6 题在 60 秒仍被截断。聊天调用按任务要求没有动（思考仍开着、120 秒内部超时），放行门失败还会再调一次模型。
2. **5 题在本验收里没有语料**：X01/X02/X18/X19/X20 问的是星火导师计划/人才盘点，题目要求引用 `星火导师计划_方案_v2.docx` 等（`tests/pending/exam/questions.json`），语料在 `tests/fixtures/exam/`，但 `tests/run_real_model_acceptance.py` 从 `4a1a5f3` 起只创建 4 个青禾样例，工作台里没有这些语料。X18 的回答来源只有 4 个青禾文件，就是证据：即使等待再放宽，这 5 题也无依据可答。
3. **逐字判定对真实模型偏严**：PM-S1-03 的回答内容正确、来源正确，但把原文引文缩短了 9 个字符，`DETAIL_SENTENCE in text` 不命中。判定按要求没有改。

## 需要审核人决定

8/8 不是扫描性能问题，需要审核人在验收口径上定，我没有自行改：

- (a) **补语料**：让 real-model 验收把 `tests/fixtures/exam/` 的语料（星火导师计划 v1/v2、10 月周报，可能还有人才盘点）也放进扫描目录并走正常审阅，这样 X01/X02/X18/X19/X20 才有依据；按现在的单文件 5–7 秒估算，7–8 个文件仍远低于 240 秒。
- (b) **再放宽回答等待**：当前 60 秒，6 题被截断；X18 已用 55.47 秒，可能需要 120–180 秒。
- (c) **PM-S1-03 的逐字要求**：要么接受"内容与来源正确但引文被缩短"的判定方式，要么保留逐字要求并把该题视为模型改写问题。

这三项都在"不改题目和判定""聊天问答的调用不要动"的护栏里，需要审核人明确后才能动。

## 单元测试与本地检查

- 新增 `tests/unit/test_scan_model_calls.py`（11 例），其中任务要求的两类：
  - "超时不重试"：`test_propose_timeout_is_not_resent_and_falls_back_to_rules`、`test_scan_section_update_timeout_is_not_resent`（断言只发出 1 个请求、遥测 `timed_out=True`、propose 走规则兜底）；另加 `test_propose_does_not_retry_after_a_server_error`。
  - "关闭思考参数"：`test_propose_disables_thinking_and_bounds_output`、`test_scan_chat_completion_disables_thinking`、`test_propose_drops_unsupported_thinking_parameter_instead_of_failing`（400 回退后仍拿到 AI 结果），以及 `test_chat_completion_outside_scan_keeps_the_chat_request_shape` 守住聊天调用不被改。
- `python tests/paw_lint.py`：lint clean；`python -m pytest tests/unit -q`：188 passed。
- 本地还用 freeform mock 起了一次完整扫描（`run.py` + `scripts/mock_openai_server.py`）：4 文件 Phase 2 共 4 次 `propose`、9.1 秒、每次 `thinking_disabled=true`，进度接口能读到明细。

## CI

- 测量提交 `c11e943`：push unit `39242029052` GREEN、PR unit `39242034752` GREEN。
- 修复提交 `efc8d78`：push unit `39242836231` GREEN；PR unit `39242842264` GREEN；push verification pipeline `39242836329` GREEN；PR verification pipeline `39242842129` GREEN（含 mock freeform 的 Real Model Acceptance Smoke）。
- 回答等待提交 `3eb285c`：push unit `39249758881` GREEN；PR unit `39249763441` GREEN；push verification pipeline `39249758910` GREEN；PR verification pipeline `39249763466` GREEN（**第 2 次尝试**：第 1 次因 PM12 的 PM-U3-02 `missing_fields_have_inputs` 偶发失败，按规则整条流水线重跑后 GREEN）。
- 本 DONE 是纯文档提交，推送后按同一套流水线单独运行。

## 运行编号

- 真实模型测量（旧代码）：`39242072868`，commit `c11e94336a8623e2f2ef8c8eea5dcadee132eb33`。
- 真实模型验证（修复后）：`39243246729`，commit `efc8d788d06b3334286263abdd884860e115e419`。
- 真实模型验证（回答等待 60 秒、含 8 题）：`39249795534`，commit `3eb285c303a8bbb651224c8ab5e819731f832a83`。
- 三次报告都从 artifact `real-model-report` 下载核对（`.r263-runA` / `.r263-runB` / `.r263-runC`，未提交）。

## 停止点

扫描目标已达成（两次独立运行 25.0 秒 / 27.5 秒，Phase 2 23.3 / 24.6 秒，240 秒上限未动），8 道题卡在回答链路耗时与验收语料缺口的组合上，两项都超出本次授权，故停下等审核人。

执行者进度：R-263，PR #15；push CI 编号 39249758910 结果 GREEN、PR CI 编号 39249763466 结果 GREEN（第 2 次尝试，首次 PM12 偶发失败后整条重跑）；真实模型运行编号 39249795534；扫描用时 27.5 秒（Phase 2 24.6 秒）；8 题通过数 0/8（2 题有回答未通过、6 题 60 秒读取超时、其中 5 题本验收无对应语料）；停下原因：回答链路单次 9.9–53.5 秒仍在 60 秒被截断，且 5 题缺语料，等审核人定验收口径。
