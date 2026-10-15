# DONE-C-04：自动验收通过，C-04 固定停点

2026-10-15；分支 codex/C-04；[PR #4](https://github.com/riser-lang/paw-local-dev/pull/4) → main，未合并。**依赖待审核的 PR #3 / C-03**，从其 9f10e8c 叠加；请先审核、合并 #3，再审核 #4 的 C-04 增量。没有自行合并任何 GitHub PR。

已切回 main 执行 git pull --ff-only，更新到 70a6677；随后以 ee65734 正常合并 main 的设计交付说明到本分支，没有改写历史。C-06 迁移遵循 STATUS 最新要求，当前不复制或修改只读 design-export，不提前开始 C-05/C-06。

## 1. 子项对照

| 子项 | 状态、行为与证据 |
|---|---|
| 一致 | 完成；严格契约判定并保存非正式 assessment，相同结论不新增正式版本，不要求用户裁决。PM-B3-01 PASS |
| 补充 | 完成；R3 常设用户规则 → ReviewDecision → ApprovedChange → Committer；保留旧句和新句、两个实际来源及各自提取快照，撤销恢复旧版本。PM-B3-02 PASS |
| 同族新版替换 | 完成；沿版本裁定的同一 Committer 事务刷新当前引用，保留旧版历史与撤销快照；版本投影明确显示“新版原文节选”，不把旧句重新挂上新来源。修复旧句仍包含于新版时漏掉新增信息的问题。PM-B3-03 PASS，新增回归验证新增句与撤销 |
| 等效资料冲突 | 完成；不改当前结论；保留双方文本和实际来源，页面显示冲突、裁决后果、采纳/保留，问答明确提示“存在冲突 / 待用户裁决”。PM-B3-04 PASS，真实 headed 页面 1440/390 截图 |
| 用户专属事项 | 完成；新项目、KPI 目标值、政策、绩效、人员选择、赛制均转用户，不自动写入；确定性门禁在模型分类后及 Committer 写入前复查。PM-B3-05 六类均 PASS，正式项目与栏目未变 |
| 用户裁决与撤销 | 完成；真实浏览器点“采纳新资料”后读回当前栏目、冲突状态、direct_user 审批及 committed 回执；撤销恢复栏目并重新打开冲突。PM-B3-06 PASS；新档案页通过既有 /v1/session 建立本地会话，未改安全校验 |
| 真实覆盖率 | 完成；DOCX 段落/表、XLSX 工作表/结构表、PPTX 幻灯片、PDF 页按 Reader 的实际结果记录；空白或图像页标未读并说明原因。Word 分页未知明确标注，未假装 OCR 或图片语义已成功。PM-B4-01 四个真实文件 PASS |
| 定向复读与 R5 | 完成；仅新版/栏目冲突/覆盖率 < 0.8 触发；不可变来源、触发身份幂等，逐条执行；API 每轮 5–10 条，巡航文件/捕获/分析/复读合计预算 10，失败读取也计入尝试预算。PM-B4-02 验证无触发 0、新版触发 6、两轮 5+1、第三轮 0，读回真实 Reader 类型和覆盖记录；单元验证 13 条按 10+3+0 |
| 原文及配置边界 | 完成；旧原文不覆盖，复读另存不可变 extraction；栏目来源固定到具体快照；Excel 单元格、公式、实际 Word 注释/脚注及幻灯片备注进入可读证据，空样式元数据不混入正文；配置超过默认八栏的九栏测试 PASS |
| 回归与稳定性 | 完成；最终 push / PR 均 50/50 GREEN，旧 B4-03、B5-02、B7-03 均 PASS。保留所有双运行及 RED，B5-01 增加条件等待及接口/耗时证据，未改原断言 |

## 2. 改动清单

- backend/pm/updates.py：栏目契约输出校验、R3 分流、冲突、裁决与撤销元数据。冲突逻辑集中于此，没有另建重复 conflicts.py。基于实际原文的逐字 quote/text，检查栏目配置、当前来源和比较依据；模型标签不能授权跨族替换。
- backend/pm/coverage.py、reread.py、storage.py：新增八表 pm_coverage、pm_source_location、pm_source_extraction、pm_reread_job、pm_revision_source、pm_section_assessment、pm_conflict、pm_update_resolution；additive migrate，旧栏目来源回填；初始来源不覆盖，复读结果保留独立快照。运行时解析与模型调用不持有长时间 SQLite 写事务。
- backend/pm/governance.py、versions.py、rules.py、backend/governance/committer.py：正式补充/替换/裁决/撤销只在治理执行器内写；当前来源和后续变更检查，多来源结论换版只更新该族部分，保留其他来源；版本撤销仍可恢复之前引用及正式字段。
- backend/pm/contracts/section_update.json、semantic.py：四关系契约、配置驱动栏目、严格输出 schema 和逐字引用验证；无 Key 明确 needs_key，不调用模型生成。
- backend/runtime_core/planner.py、scanner.py、scheduler.py：接入实际读取覆盖、源文件位置、语义判定与顺序复读；失败读取标未知覆盖，巡航按尝试数控制预算。
- backend/pm/router.py：本地覆盖、冲突、assessment、用户裁决、复读队列与限额接口；backend/pm/dossier.py 返回多来源和快照链接。
- backend/pm/assistant_context.py、answer_timing.py、backend/brain/api/local_agy_facade.py：加入仍有效的未决冲突及提示，避免把已失效冲突或过期复合来源当成当前依据；保留每次回答耗时记录。
- frontend/pm-src/updates.js、app.js、style.css 及 frontend/dist/pm/ 对应产物：基础冲突对比/治理操作/撤销/覆盖与复读 UI；写入请求使用既有本地会话；esbuild 构建同步。没有新增 CDN 或运行时第三方依赖。
- scripts/mock_grounded_answer.py、mock_openai_server.py：配置标题驱动、从请求原文摘句的通用 section_update 回答，不按用例/题号/题目分支；没有使用 C-10 考题。
- tests/run_pm_updates_tests.py：8 项独立 fresh_install 的安装版验收，真实资料正常扫描，不用 SQL 写入候选/栏目/冲突；SQL 只读回证据。真实有界面浏览器点击裁决及截图。
- tests/run_e2e_tests.py、.github/workflows/test.yml：加入 PM04；B5-01 用内容就绪条件替代仅固定睡眠后立即判定，记录项目接口状态和等待耗时，原名称/阶段断言保留。
- tests/unit/test_updates.py、test_coverage.py：治理与撤销、用户专属门禁、错引拒绝、多来源换版、新版增量、真实覆盖、快照、复读幂等/额度、九栏配置等。

## 3. 测试与运行

最后功能/验收代码头部 **7babd17e2a4c564340ba1b46a11ada10b7f57497**，产品修复提交 4e7f49a；PR 测试的临时合并提交为 **c5a6704c8a39656572677f1f03af662ddffdccc0**，见下方原始 summary。报告是在两套完整门禁结束后写入；后续只追加交接文档，不拿文档运行覆盖以下历史结果。

| 头部 | 事件 / 完整 E2E | 结果 | unit |
|---|---|---|---|
| 0be61f3 | push [38759570469](https://github.com/riser-lang/paw-local-dev/actions/runs/38759570469) | RED 48/50；PM-B3-03 新内容丢失、PM-B3-06 浏览器裁决超时 | [38759570450](https://github.com/riser-lang/paw-local-dev/actions/runs/38759570450) GREEN 74 |
| 0be61f3 | PR [38759582951](https://github.com/riser-lang/paw-local-dev/actions/runs/38759582951) | RED 48/50；与 push 同两项 | [38759582728](https://github.com/riser-lang/paw-local-dev/actions/runs/38759582728) GREEN 74 |
| ee65734 | push [38760791457](https://github.com/riser-lang/paw-local-dev/actions/runs/38760791457) | RED 48/50；同两项；本提交仅自检补齐配置/可读证据边界及合并设计说明，尚无这两处修复 | [38760791475](https://github.com/riser-lang/paw-local-dev/actions/runs/38760791475) GREEN 76 |
| ee65734 | PR [38760800243](https://github.com/riser-lang/paw-local-dev/actions/runs/38760800243) | RED 48/50；同两项，运行完整保留 | [38760800209](https://github.com/riser-lang/paw-local-dev/actions/runs/38760800209) GREEN 76 |
| 4e7f49a | push [38761697849](https://github.com/riser-lang/paw-local-dev/actions/runs/38761697849) | RED 49/50；PM04 全部 PASS；B5-01 取文时名称未就绪 | [38761698301](https://github.com/riser-lang/paw-local-dev/actions/runs/38761698301) GREEN 77 |
| 4e7f49a | PR [38761705821](https://github.com/riser-lang/paw-local-dev/actions/runs/38761705821) | GREEN 50/50 | [38761705648](https://github.com/riser-lang/paw-local-dev/actions/runs/38761705648) GREEN 77 |
| 7babd17 | push [38762954177](https://github.com/riser-lang/paw-local-dev/actions/runs/38762954177) | **GREEN 50/50** | [38762954354](https://github.com/riser-lang/paw-local-dev/actions/runs/38762954354) GREEN，77 passed in 2.82s |
| 7babd17 | PR [38762963209](https://github.com/riser-lang/paw-local-dev/actions/runs/38762963209) | **GREEN 50/50** | [38762963224](https://github.com/riser-lang/paw-local-dev/actions/runs/38762963224) GREEN，77 passed in 1.72s |

[最终 Build Installer 38762963723](https://github.com/riser-lang/paw-local-dev/actions/runs/38762963723) GREEN；此前 38759583023、38760800148、38761705725 构建也 GREEN。不把打包成功当功能验收，不取消 push 或 pull_request，不通过反复 rerun 挑一次绿灯。

PM04 新增用例直接来自审核人的 required_cases_C-04.json：PM-B3-01…06、PM-B4-01…02。git mv 激活，blob **c4bd8dee04b9267ae514a5c36722e60ad6718619** 未变；不另建 proposed_cases 改标准。最终两套 summary 的五个负对照均在审核人指定检查有效失败，整体门禁 GREEN。PM04 八例分别有 6、9、7、14、11、11、17、9 项检查，八例均有独立清空/安装证据（最终 PR report_PM04.json）。

### 失败原因与修复证据

1. **PM-B3-03 产品问题**：旧代码若旧句仍为新文档子串，就保留旧句；因此版本/来源切换成功，实际新增“联调已启动”却缺失。4e7f49a 改为用明确标注的新版原文片段刷新，不再因为旧句仍存在就丢掉其他新句。增加旧句仍存在 + 新句新增 + 撤销恢复的治理回归；最终安装版同一断言 PASS。
2. **PM-B3-06 产品问题**：新 PM 页缺少本地会话，浏览器带 Origin 的写请求不能满足既有 R5 校验；原先本机无 Origin 的 API 验证未覆盖这个条件。4e7f49a 在页面写入前请求既有 /v1/session 并带 x-paw-local-token，未改后端安全规则。最终真实浏览器点按钮，显示已裁决，且读取 direct_user/committed、当前栏目与撤销恢复全部 PASS。针对这两项云端问题的产品自修为一次。
3. **B5-01 稳定性问题**：4e7f49a push 的 screen_B5_01.png 显示首次启动控制中心覆盖、背景项目页尚未就绪，旧测试 DOMContentLoaded 后固定 1.2 秒便取正文；同提交 PR PASS。历史该次项目接口状态未记录，不能完全排除瞬态接口失败，未伪称已证明只有前端耗时。7babd17 增加实际内容条件轮询（两项目名、两中文阶段，12 秒有界等待），原检查名与断言不变；记录接口状态、最初匹配数及就绪耗时。最终 push / PR 均 initial_matches=4/4、project_response_statuses=[200]，条件等待分别 **0.0155 / 0.0161 秒**，两套均 PASS。若接口失败或始终未就绪，仍会 RED，没有用更长固定睡眠掩盖。

### B7-03 / B4-03 持续观察

C-03 历史超时调查及初始/修复双运行在 DONE-C-03.md、B7-03_INVESTIGATION_C-03.md；历史 15 秒超时的确切停顿位置仍未确认，15 秒标准未改。C-03 报告文档提交 9f10e8c 的后续 push [38755056421](https://github.com/riser-lang/paw-local-dev/actions/runs/38755056421) / PR [38755067064](https://github.com/riser-lang/paw-local-dev/actions/runs/38755067064) 也均 42/42 GREEN，unit 38755056346 / 38755067411 GREEN 54，build 38755067639 GREEN。

最后 C-04 两套运行每次回答均有日志，包括异常路径；以下数值直接来自各自 B7 job 的 Assistant answer 行：

| 回答 | push 秒 | PR 秒 |
|---|---:|---:|
| B7-01 | 0.1318 | 0.1326 |
| B7-02 | 0.9318 | 0.3092 |
| B7-03 | **0.0983** | **0.0872** |

B7-03 两套均 model_calls=1、search_seconds=0、context_bytes=4423；模型耗时 0.0145 / 0.0107 秒，未发生模型重试。B4-03、B5-02 在以上 C-04 八次完整运行中均 PASS。最终 B5-01 的就绪日志见各自 B5 job；所有数字均为 mock / 云端 Windows 测试数据，不外推真实模型耗时。

本地命令与关键输出：

- python -m pytest tests/unit -q → **77 passed in 3.26s**；python tests/paw_lint.py → **lint clean**；git diff --check 无错误。
- python scripts/verify_source_completion.py → Partial proposals: 3; legacy analyzed predicate: True; fixed predicate: False / Scanner waited for owner release; final proposals: 4; completion record: complete; PASS。
- 隔离源码 API 联调：真实扫描 → 双来源补充 → 来源快照可打开 → 撤销；冲突 → 助手提示 → 裁决 → 撤销；真实 PPTX 两页其中空白一页，ratio=0.5；复读实际处理冲突双方 + 低覆盖共 3 条，均 completed。只用独立 scratch 数据目录与假 Key/mock，仅清理本次自建进程，未运行本机全局清空/安装。
- 本机额外 headed 浏览器验证在启动时出现 spawn UNKNOWN，**NOT RUN / 未计为 PASS**；不能拿该 API 联调冒充按钮操作。上面按钮验证与截图由云端安装版 headed Playwright 完成。
- python scripts/scan_secrets_leak.py --scan-dir backend/pm → 扫描 24 文件，凭证/真实 Key 特征 0，PASS；最终两套 CI 产物泄露扫描亦通过。
- git diff main -- tests/paw_case.py tests/paw_verdict.py tests/paw_lint.py tests/required_cases.json tests/required_cases_C-02.json tests/required_cases_C-02b.json tests/pending/exam → 无输出。C-03 blob 6e1d9c4ab9825ba17fb876ee3080adde2c222936；exam corpus/questions 仍为 4616ae79d0712747a2f8a26736e6c12c156d0726 / 9e49d51a0876f0df0d210f4e4e3a6546b58da706；未读取考题内容或改动 pending 其他任务。仅 C-03/C-04 的 R100 移动。

### 最终 push summary.md 全文（原样）

```text
# Pipeline verdict for 7babd17e2a4c564340ba1b46a11ada10b7f57497

Required cases: 50 | PASS 50 | FAIL 0 | NOT RUN 0

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

## No problems

VERDICT: GREEN```

### 最终 PR summary.md 全文（原样）

```text
# Pipeline verdict for c5a6704c8a39656572677f1f03af662ddffdccc0

Required cases: 50 | PASS 50 | FAIL 0 | NOT RUN 0

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

## No problems

VERDICT: GREEN```

## 4. 截图

最终 PR 运行 **38762963209** 的 **screens-PM04** 产物：screen_PM_B3_04_1440.png、screen_PM_B3_04_390.png。均为有界面浏览器在实际 /app/pm/projects/<本例项目 ID> 页面截取，c.screenshot 保存真实 URL。已下载检查：桌面双侧冲突/来源/裁决后果可见，390px 单列，DOM 横向溢出断言通过，没有拼图或替换数据。最终 report-PM04 的 PM-B3-06 验证真正的页面点击和落库，不依赖截图证明写入。

B5-01 的失败证据来自 38761697849 的 screens-B5/screen_B5_01.png，保留在该次运行，未伪装成成功截图；就绪修复证据来自最终两个 B5 job 的原始日志。

## 5. 风险、边界与后续

- 本次只使用假 Key 与 mock，真实模型分类质量和真实联网耗时未验证；无 Key 不生成语义结论。B7-03 历史超时位置和 B5-01 历史单次接口状态仍未确认，保留原 RED 和新增计时，不申请放宽标准。
- 覆盖率度量结构可读取情况，不能等同逐字理解正确率。未执行 OCR；DOCX/Excel 物理分页未确认；图像/图表语义不声称已读。模型上下文仍受预算约束，原文和提取快照可打开。同族版本切换使用明确原文节选（最多 2000 字符）作为同步投影，未提供的后续段落不伪称已经语义综合。
- 复读 completed 表示执行了实际 Reader，不保证原来的图像/空白限制被修复；限制仍展示。仅待执行项自动运行；失败/源字节改变的项不无限重试；进程意外退出时 running 项的恢复策略尚未实现，交 C-07 运维完善。源文件改动必须重新摄取新版本，不用变化后的字节冒充旧来源。
- 栏目/裁决已有后续变化时，旧的提交或撤销会拒绝，需重新判定；不覆盖新事实。重复冲突复核采用稳定触发身份，避免一轮复读再造一轮相同任务。巡航的 files_total 仍是本轮遍历计数，不能据此宣称已处理整个目录。
- C-06 仍按 STATUS 已交付设计迁移：只读 D:\PAW\design-export；TodayViewModel / ProjectViewModel 由本地后端组装，回调走本地治理；本地状态为巡航/扫描/备份/模型连通/磁盘；演示数据不进产品；CI 安装 Node，产物进入安装包；字体按实际字重精简并保留许可证。未迁移任何设计文件，本任务基础交互页后续换外观。
- PR #3/#4 均待 Claude Code 审核，未改版本号、未发布或替换 2.3.9 比赛保底。C-04 固定停点，不开始 C-05；C-10 仍要求通用 mock 摘句与建议标注，题库不动。

## 6. 大白话摘要

资料一致时不重复写，补充资料会保留双方来源。
新版不再漏掉新增内容；相互矛盾的资料等待用户裁决，助手会提醒。
裁决和自动写入都有治理记录，并可撤销。
四种资料显示实际读取范围，复读有触发条件和每轮上限。
最终两套验收都 50/50 通过；停在 C-04，交审核人审查。
