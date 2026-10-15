# DONE-C-03：自动验收通过，待 Claude Code 审核

2026-10-15；分支 codex/C-03；[PR #3](https://github.com/riser-lang/paw-local-dev/pull/3) → main，未合并。依赖已合并的 C-02 / PR #2，保留审核人 e9dc3a2 的 UTF-8 标准流修复。

## 1. 子项对照

| 子项 | 结果与证据 |
|---|---|
| R1 版本自动裁定 | 已实现；文内唯一有效生效日 > 文件名版本号 > 摄取时间；PM-B2-01、02 PASS。旧版本保留且标 superseded，原文可打开 |
| R2 不确定归族/归属 | 已实现；项目名称必须逐字且唯一命中，归族仅自动接受明确的相同基本名；相似名称、多个生效日、不明确项目进入非正式待决卡片。PM-B2-03 验证正式项目与栏目未变 |
| C1 本地版本链路 | 已修复本地摄取路径；scanner 仅登记不可变摄取身份，Planner 后按用户常设规则进 ReviewDecision → ApprovedChange → Committer，兼容同步 source_version_governance，避免所有版本长期 unresolved |
| C2 新版本刷新 | 已接通版本裁定 → 栏目来源及新版原文投影，在同一 Committer 事务内完成。旧结论不伪装成来自新版本；不再适用的结论以“新版原文节选”明确展示新材料，并保留历史。此处是确定性投影，不冒称模型语义综合；逐栏目语义更新属 C-04 |
| 撤销 | 已实现，PM-B2-04 PASS；旧当前版、栏目引用、受影响的项目字段和旧版本登记一同恢复，防止覆盖后续变更 |
| 助手只取当前版 | 已实现；过滤过期原文及有旧源审计记录的派生资产，过期来源候选不能提交为当前结论；PM-B2-05 PASS，回答与依据中均没有 v1，包含 v2 原文 |
| 新版候选来源 | 首次双运行暴露内容去重丢失新版提议，已修复；fingerprint 包含不可变来源集合，同版本仍去重，新版本独立保留提议及来源 |
| B7-03 调查 | 修复了已复现的错误取证/路由路径，添加逐次耗时；最终双运行 B7-03 分别 0.5115 / 0.5432 秒，15 秒标准未改。历史那一次超时的确切停顿位置仍未确认，见专项报告，未伪称已证明是搜索卡住 |
| 不重复取消运行 | 已遵守；初版与修复版的 push、PR 四次均完成并保留，结果均在下表 |
| 新页面 | 增加资料版本、旧版原文入口、版本撤销及归族确认基础 UI；1440/390 headed 截图，手机无整页横滚。C-06 将按 STATUS 的设计来源迁移，不抢做另一套驾驶舱/甘特图 |

## 2. 改动清单

- backend/pm/versions.py、storage.py：新增六表 pm_document_intake、pm_family、pm_family_member、pm_family_decision、pm_version_ruling、pm_resynthesis；additive migrate，原库数据和表不删除。版本/来源/字段/栏目快照与兼容旧登记在治理事务中修改。
- backend/pm/rules.py：显式记录“用户 2026-10-15 设定”及 R1/R2，reviewer/channel 为 standing_user_rule，不假装用户刚点击；Committer 内复核归族和排序。敏感栏目不自动覆盖。
- backend/governance/committer.py、backend/pm/governance.py：pm_version 执行类型、撤销及过期来源校验；保留冻结载荷、授权 hash、幂等和并发写锁。
- backend/runtime_core/planner.py、scanner.py：统一来源摄取后进入版本裁定；撤下旧的“每个近似版本都生成知识候选询问”的路径，未知材料由新的待决卡片处理，避免双重裁定入口。
- backend/pm/source_fingerprint.py、backend/governance/service.py：同内容不同来源版本不互相吞掉；同一来源集合的顺序不影响去重。
- backend/pm/assistant_context.py、backend/brain/context/agy_project_context_pack.py、backend/brain/runtime/agy_v2_runtime.py：当前版本过滤；项目/全局共用 PM 证据适配；非正式原文明确标注；无项目但有资料的问题保留全局取证。
- backend/pm/answer_timing.py、backend/brain/api/local_agy_facade.py、backend/provider/adapter.py：记录总耗时、上下文、模型和搜索分段指标，测试可读回；不打印 Key、提示词或回答正文。
- backend/search/assistant_bridge.py、service.py、backends/duckduckgo.py：内部资料问题避免误联网、回退共享剩余预算。不是严格抢占系统调用的硬墙钟期限，不宣称所有网络状态下都必然小于 8 秒。
- backend/pm/router.py：family / decisions / user resolution 的本地接口。
- frontend/pm-src/versions.js、app.js、frontend/dist/pm/app.js：基础版本/确认/撤销 UI，构建产物同步；没有引入新的外部库/CDN。
- scripts/mock_grounded_answer.py、mock_openai_server.py：只从请求上下文通用排序摘句、输出证据映射与“建议”段；移除无证据时默认 sample_doc.docx 的回答，未读取或改动标准考题。
- tests/run_pm_versions_tests.py、run_e2e_tests.py、run_pm_tests.py、两个 workflow、tests/unit/test_versions.py、test_answer_policy.py：五项安装版验收、版本负对照、B7 逐次计时和单元验证；unit 环境增加搜索策略测试所需 httpx/lxml。
- _handoff/B7-03_INVESTIGATION_C-03.md、本报告与 STATUS：证据、未确认事项及交接。

核心新逻辑只依赖标准库与存储适配。语义改写不在 C-03 冒用确定性规则实现：日期/版本提取无需模型；新版本事实投影明确是原文节选，语义类别与 R3 栏目流程接在 C-04。

## 3. 测试与运行

最终产品代码提交 d12d7a0bdf3c7396a8c3bb0b59cc6d6273f4a093；后续 e1c9ca6 合并 main 仅引入 STATUS 的前端设计来源说明，没有合并 GitHub PR。以下最后两份完整门禁覆盖同一产品代码，之后只追加交接文档。

| 提交 | 事件 | 完整 E2E | unit | 说明 |
|---|---|---|---|---|
| 7fafec1 | push [38751917805](https://github.com/riser-lang/paw-local-dev/actions/runs/38751917805) | RED，37/42 | [38751918191](https://github.com/riser-lang/paw-local-dev/actions/runs/38751918191) GREEN，52 | 五个 PM03 在新版来源提议前置检查失败；B7-03、B4-03 PASS |
| 7fafec1 | PR [38751933641](https://github.com/riser-lang/paw-local-dev/actions/runs/38751933641) | RED，37/42 | [38751932917](https://github.com/riser-lang/paw-local-dev/actions/runs/38751932917) GREEN，52 | 与 push 一致；没有取消 |
| d12d7a0 | push [38753297168](https://github.com/riser-lang/paw-local-dev/actions/runs/38753297168) | **GREEN，42/42** | [38753297156](https://github.com/riser-lang/paw-local-dev/actions/runs/38753297156) GREEN，54 | 五个负对照均在指定检查有效失败 |
| d12d7a0 | PR [38753306161](https://github.com/riser-lang/paw-local-dev/actions/runs/38753306161) | **GREEN，42/42** | [38753305936](https://github.com/riser-lang/paw-local-dev/actions/runs/38753305936) GREEN，54 passed in 0.51s | 同一代码双运行一致；临时合并提交见原文门禁 |

[最终 Build Installer 38753305803](https://github.com/riser-lang/paw-local-dev/actions/runs/38753305803) GREEN；初版 Build Installer 38751933688 也 GREEN，不拿打包成功代替功能验收。只有一次云端失败后的产品自修（d12d7a0），没有重复点击 rerun 来挑一次 GREEN。文档提交自动运行另见 PR Checks，不用于抹去上表 RED。

新增编号全部来自审核人原文件：PM-B2-01…05、NC-PM-B2。tests/required_cases_C-03.json 以 git mv 激活，blob 为 6e1d9c4ab9825ba17fb876ee3080adde2c222936，未改内容；无需另建 proposed_cases。

NC-PM-B2 使用 v2.3.9-baseline，正常导入两个版本、核准第一版项目提议，再检查旧版是否 superseded，预定失败检查 old_version_superseded 有效。负对照用 ASCII 文件名 plan_v1/plan_v2，避开旧版无窗口中文路径编码故障；资料仍含中文，验证 analyzed_source 中两份哈希及实际 reader_result 原文，不接受导入失败代替版本失败。baseline 对第二版可能复用内容候选，不强行要求它额外生成一个相同提议；版本判定断言完全保留。

本地：python -m pytest tests/unit -q → 54 passed in 0.90s；python tests/paw_lint.py → lint clean；python scripts/verify_source_completion.py → Partial proposals: 3; legacy analyzed predicate: True; fixed predicate: False / Scanner waited for owner release; final proposals: 4; completion record: complete; PASS。隔离真实源码 API 验证导入、版本替换、问答、撤销、模糊归族及全局原文取证；不运行本机全局清空/安装，只停止自己创建的进程。云端用例每例 fresh_install。

保护核对：git diff 8a239d2 -- tests/paw_case.py tests/paw_verdict.py tests/paw_lint.py tests/required_cases.json tests/required_cases_C-02.json tests/required_cases_C-02b.json tests/pending/exam → 无输出。exam corpus/questions 的 blob 仍为 4616ae79d0712747a2f8a26736e6c12c156d0726 / 9e49d51a0876f0df0d210f4e4e3a6546b58da706；C-04 尚未激活时 blob 为 c4bd8dee04b9267ae514a5c36722e60ad6718619。

### 最终 push summary.md 全文（原样）

```markdown
# Pipeline verdict for d12d7a0bdf3c7396a8c3bb0b59cc6d6273f4a093

Required cases: 42 | PASS 42 | FAIL 0 | NOT RUN 0

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

## No problems

VERDICT: GREEN
```

### 最终 PR summary.md 全文（原样）

```markdown
# Pipeline verdict for 323351f4291c9d593fe414dd854c92332b71eec8

Required cases: 42 | PASS 42 | FAIL 0 | NOT RUN 0

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

## No problems

VERDICT: GREEN
```

## 4. 截图

run 38753306161 的 screens-PM03：screen_PM_B2_01_1440.png、screen_PM_B2_01_390.png。headed Chromium 实际打开 /app/pm/projects/{id} 后截取并 c.screenshot 登记，已人工查看：新版原文/历史、v1 已替换、v2 当前版、撤销入口都可见，手机单列无整页横滚。没有兜底图片或改尺寸。完整驾驶舱/字体/甘特图为 C-06，本次未迁移 design-export，也没有导入设计稿示例数据。

## 5. 风险与未确认事项

- B7-03 的历史那一次 15 秒超时没有分段日志，确切停顿位置仍未确认；当前四次样本为 0.2715–0.5432 秒，不能称为长期性能保证。已修复的取证/路由错误有源码与独立复现，见专项报告；未请求放宽 15 秒标准。
- 当前版过滤依据已知的版本与审计来源；不能自动发现一个用户手工无引用录入的结论究竟来自哪份历史文件。文件原文证据是非正式资料，不会仅因被助手引用就升级为事实。
- 生效日期须有明确声明且唯一有效；无法确认的归属不自动猜测。当前归族确认在基础档案页及本地 API 中，统一收件箱/全局未知项目卡片的正式展示属于 C-06。
- C2 当前使用可审计的原文重投影；语义栏目综合、冲突与覆盖率在 C-04 继续实现，不把两个阶段混称为全部完成。
- Claude Code 尚未审核 PR #3；产品负责人验收未进行；版本仍为 2.3.9，未打 tag、发布或替换保底版本。C-04 按授权从本分支叠加，PR 描述将注明依赖。
- 保留审核人的编码修复、预定用例和考题；没有访问禁入目录/私有仓库，没有 SSH、真实 Key 或真实模型调用。测试资料全部虚构。

## 6. 大白话摘要（5 行）

同族文件现在能自动选出当前版本，拿不准的会交给用户。
新版会更新档案来源，撤销能同时恢复旧版和旧结论。
助手只取当前版，未核准的原文仍标作资料证据。
最终 push 和 PR 都是 42/42；历史超时原因仍有证据缺口，15 秒标准没有放宽。
报告完成后直接继续 C-04，到指定停点再交接。
