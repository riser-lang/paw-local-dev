# R-262 扫描超时调查结果

## 结论

扫描分为两个阶段。Phase 1 读取文件和登记来源，完全本地运行，不调用模型。Phase 2 对每个文件做 AI 分析，会调用 SiliconFlow 的 OpenAI 兼容聊天补全接口。两次真实模型运行都在 Phase 2 超过现有 240 秒验收等待上限；第二次没有完成扫描，8 道题都未能运行。进度状态持续为 `phase2_analyzing`，不是轮询状态名不匹配或完成条件写错。没有增加等待上限，也没有第三次重跑；这是软件在本次真实模型配置下分析过慢的证据，现停下来等产品负责人决定下一步。

## 模型调用代码路径

- `backend/runtime_core/scanner.py:333-389` 标记并执行 `phase1_ingesting`；`_process_phase1_file()`（约第 499 行）使用隔离的本地 reader 提取内容并登记来源，不调用模型。
- `backend/runtime_core/scanner.py:410-466` 执行 `phase2_analyzing`；`_process_phase2_file()`（第 605 行起）在第 630 行调用 `agent_planner.plan_document_impact(...)`。
- `backend/runtime_core/planner.py:26` 定义 `plan_document_impact()`；第 51-57 行取活动 provider、调用 `process_source(file_hash, provider)`，随后对每个文件调用 `provider.propose(...)`。
- 对已有项目版本成员，`backend/pm/updates.py:32-55` 的 `process_source()` 还会经 `backend/pm/semantic.py:9-34` 调用 `provider.chat_completion(...)` 执行 `section_update`。该调用取决于文件是否匹配项目版本族。
- `backend/provider/adapter.py:343-402` 中 `UniversalOpenAIProvider.propose()` 请求 `{base_url}/chat/completions`。本次配置模型为 `deepseek-ai/DeepSeek-V4.1-Flash`，provider 为 SiliconFlow，因此调用聊天补全接口。该路径没有调用 embeddings 接口。adapter 设置单次 HTTP timeout 为 45 秒、最多重试两次（第 395-402 行）；这可能与长耗时相关，但本次进度无法证明具体哪次内部请求耗时，因此这里只作为可能因素。

## 验收脚本变更

`tests/run_real_model_acceptance.py` 每 10 秒打印一次安全进度行，只包含状态、完成数/总数、当前文件、最近错误；轮询前抑制 httpx 请求级 INFO 日志，不输出请求内容或 Key。脚本记录最近一次进度行，并在超时报告中原样保存。运行开始时先把 8 道问题登记为“未能运行”，只有扫描完成后才逐题执行并更新。当前 240 秒上限未变。

本地检查：`python tests/paw_lint.py` lint clean；`python -m pytest tests/unit -q`：177 passed。此前 CI 的 mock freeform 空跑也通过，覆盖安装、导入样例、8 题及报告生成脚本路径。

## 真实模型运行记录

模型 ID 两次均为 `deepseek-ai/DeepSeek-V4.1-Flash`。

### 第一次运行：39228185020（commit `f8e44232e57c7671101e8c9be27ab1fc100fbded`）

结果 RED，扫描在 240 秒后超时。最后状态为 `phase2_analyzing`，完成 3/4，当前文件 `青禾试点计划_v1.docx`，最近错误为 none。捕获到的 Phase 2 文件耗时：

| 文件 | 阶段 | 耗时 | 结果 |
|---|---|---:|---|
| 青禾试点周报.pptx | Phase 2 | 64.6 秒 | 完成 |
| 青禾试点详情.md | Phase 2 | 41.4 秒 | 完成 |
| 青禾试点计划_v2.docx | Phase 2 | 13.1 秒 | 完成 |
| 青禾试点计划_v1.docx | Phase 2 | 118.1 秒 | 超时点当前文件 |

当时每秒采样导致 Phase 1 文件计时不完整，因此不将缺失的 Phase 1 计时推断为零。

### 第二次运行：39229210227（commit `0c28f0faec9d3f13a6be0109a1233042f2a1b167`）

结果 RED，240 秒超时；状态仍为 `phase2_analyzing`，完成 1/4，当前文件 `青禾试点详情.md`，最近错误为 none。报告里的最后一次进度原文如下：

`Scan progress: status=phase2_analyzing completed=1/4 current_file=青禾试点详情.md recent_error=none`

| 文件 | 阶段 | 耗时 | 结果 |
|---|---|---:|---|
| 青禾试点周报.pptx | Phase 1 本地导入 | 0.8 秒 | 完成 |
| 青禾试点计划_v1.docx | Phase 1 本地导入 | 0.7 秒 | 完成 |
| 青禾试点计划_v2.docx | Phase 1 本地导入 | 0.8 秒 | 完成 |
| 青禾试点详情.md | Phase 1 本地导入 | 0.6 秒 | 完成 |
| 青禾试点周报.pptx | Phase 2 模型分析 | 140.4 秒 | 完成 |
| 青禾试点详情.md | Phase 2 模型分析 | 96.7 秒 | 当前文件；扫描超时 |
| 青禾试点计划_v2.docx | Phase 2 模型分析 | 未到达 | 等待超时后未运行 |
| 青禾试点计划_v1.docx | Phase 2 模型分析 | 未到达 | 等待超时后未运行 |

第二次两项已完成的 Phase 2 工作共耗时 237.1 秒；到超时前仍未完成当前文件。由此可确认主要耗时位于真实模型 Phase 2，而本地扫描/导入很快。当前观测只能定位到文件和 Phase 2，无法从进度 API 判断该文件内部具体哪一次 HTTP 调用耗时。

## 8 道题的结果

两次运行均未开始提问。第二次报告中每题的 answer 为空、sources 为空、status 为“未能运行”；没有回答原文或来源可记录：

| ID | 问题 | 状态 | 回答 / 来源 |
|---|---|---|---|
| PM-U1-03 | 青禾试点计划目标是多少？ | 未能运行 | 未发送；无来源 |
| PM-U4-01 | 青禾试点后续最好如何优化？ | 未能运行 | 未发送；无来源 |
| PM-S1-03 | 青禾试点详情.md 里写了什么？ | 未能运行 | 未发送；无来源 |
| X01 | 星火导师计划第一期要培养多少名导师？ | 未能运行 | 未发送；无来源 |
| X02 | 星火导师计划的导师认证通过率目标是多少？ | 未能运行 | 未发送；无来源 |
| X18 | 星火导师计划最早的 v1 方案里原定培养多少名导师？ | 未能运行 | 未发送；无来源 |
| X19 | 星火导师计划后续最好如何优化？ | 未能运行 | 未发送；无来源 |
| X20 | 人才盘点下一步应该做什么？ | 未能运行 | 未发送；无来源 |

通过数：0/8（未能运行，不代表判题失败）。

## CI 与交付状态

- 修复代码 commit `0c28f0faec9d3f13a6be0109a1233042f2a1b167`：push unit run `39229196482` GREEN；PR unit run `39229203047` GREEN；PR installer run `39229203071` GREEN；push verification pipeline `39229196786` GREEN；PR verification pipeline `39229203201` GREEN。两条完整流水线均成功。
- PR #15 保持未合并；没有打 tag。两条完整 verification pipeline 均已结束并 GREEN。

## 停止点

真实模型下 Phase 2 每个文件的处理耗时过长，第二次扫描在 240 秒窗口内只完成 1/4。没有通过延长等待来掩盖问题。需要产品负责人决定是否进一步优化 Phase 2 模型调用或调整产品侧扫描策略。

