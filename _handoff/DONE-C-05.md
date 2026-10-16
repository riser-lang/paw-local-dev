# DONE-C-05 — 本地输入流转、去向回执与审批来源

2026-10-16；PR #5：https://github.com/riser-lang/paw-local-dev/pull/5 。基于已合并 main 3befc63，未合并 PR、未改版本、未打 tag。

## 1. 子项对照
- 完成：监控文件夹唯一明确项目名按 R1/R2 归属；R3 判定后更新栏目。多项目、明确否定或不确定归属等待用户，不自动创建项目。
- 完成：随手记接现有 captures 入口与 scheduler；成功处理后才标记完成。每次输入独立留回执，重复扫描同一资料版本不重复。
- 完成：确认归属、拒绝、撤销通过 ReviewDecision → ApprovedChange → Committer；原始资料保留可读。回执从实际已提交归属和栏目变更读取，含项目、栏目、状态和来源。
- 完成审核补充：常设规则来源为 standing_user_rule，页面与新增审计 details 明示“按用户常设规则自动写入（R1/R2 或 R3，用户 2026-10-15 设定）”；手动批准另标“用户亲手批准”。
- 实施方案调整：复用 pm_family_decision 和已提交的版本裁定作为唯一归属权威，不另建重复的 pm_assignment_decision；回执是只读投影，避免两份正式去向不一致。明确项目名采用保守的确定性匹配，无 input_assignment 模型推断；不确定交用户，栏目语义仍由既有 section_update 契约校验。

## 2. 改动清单
- backend/pm/{receipts,routing}.py：原始输入登记、实际去向投影、待确认/拒绝/撤销。
- backend/pm/{versions,governance,storage,router}.py：捕获输入归族、撤销后重新确认、本地 API。
- backend/runtime_core/{planner,scheduler}.py：接文件与随手记，失败不误标完成。
- backend/pm/authorization.py、governance/{committer,service}.py、workspace_api/db.py：审批来源迁移与审计；dossier.py 提供来源标签。
- frontend/pm-src/{receipts,app,versions}.js、index.html/style.css 及构建产物：输入与回执、审批来源展示。
- tests/unit/test_{authorization,routing}.py、新 run_pm_routing_tests.py、E2E 分发与矩阵：新增 8 项单元测试和 5 项安装包验收。
- 用 git mv 激活 tests/required_cases_C-05.json，blob 9804f9d0fc22ac58f10f283b5fc4207b4d6a8792，与审核人指纹一致；未改判定器及其他 required_cases 内容。

## 3. 测试与完整原始 summary
最终功能提交 c7110e92ca53ce0d6bcee608b9a7c09305bcdd1f。
- 本地：python -m pytest tests/unit -q → 88 passed；python tests/paw_lint.py → lint clean；node frontend/pm-src/build.mjs、git diff --check 通过。
- 隔离源程序探针：python scratch/check_C05_api.py → watcher/R1/R3/回执、未知归属拒绝撤销确认、capture 均 PASS；integrity_check=ok；审计视图 direct_user 3、standing_user_rule 5（本次虚构场景统计）。使用单独 scratch 数据目录与仅本机 mock，不清理用户安装。
- 云端 unit：push https://github.com/riser-lang/paw-local-dev/actions/runs/38828162670 与 PR https://github.com/riser-lang/paw-local-dev/actions/runs/38828185733 均 success（88 项）。独立 PR 打包 https://github.com/riser-lang/paw-local-dev/actions/runs/38828185697 success。
- 完整 push/PR 均 55/55 GREEN；PR 按 GitHub 测试合并提交 6439744afd636653aae5a1d08e639467eca735c8 执行，来源头部为上述功能提交。B4、B7、既有 PM 未退化，所有既有负对照有效。
- 首次 git add 引用了 git mv 后已不存在的旧路径，暂存失败；因此 857f913 仅激活用例，push 为 50 PASS、5 NOT RUN、RED。随即补充实现提交，未取消、重跑或隐去该运行。首次 unit 38828149088 success（80 项）。
- 新增验收：PM-D1-01/02/03、PM-D2-01/02，来自审核人预定文件，无新增 proposed_cases。

### 首次激活推送（保留 RED）
https://github.com/riser-lang/paw-local-dev/actions/runs/38828149176

```markdown
# Pipeline verdict for 857f913a8aa2945295d9ea0a99dd1d60f7a58bce

Required cases: 55 | PASS 50 | FAIL 0 | NOT RUN 5

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

## Problems
- PM-D1-01: appears 0 times (expected 1)
- PM-D1-02: appears 0 times (expected 1)
- PM-D1-03: appears 0 times (expected 1)
- PM-D2-01: appears 0 times (expected 1)
- PM-D2-02: appears 0 times (expected 1)

VERDICT: RED
```

### 完整实现 push
https://github.com/riser-lang/paw-local-dev/actions/runs/38828162684

```markdown
# Pipeline verdict for c7110e92ca53ce0d6bcee608b9a7c09305bcdd1f

Required cases: 55 | PASS 55 | FAIL 0 | NOT RUN 0

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
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN
```

### 完整实现 pull_request
https://github.com/riser-lang/paw-local-dev/actions/runs/38828185744

```markdown
# Pipeline verdict for 6439744afd636653aae5a1d08e639467eca735c8

Required cases: 55 | PASS 55 | FAIL 0 | NOT RUN 0

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
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN
```

## 4. 截图
PR 运行 38828185744 的 screens-PM05：screen_PM_D1_01_1440.png、screen_PM_D1_01_390.png、screen_PM_D1_02_1440.png、screen_PM_D2_01_1440.png、screen_PM_D2_01_390.png。有界面 Chromium，真实 /app/pm/receipts；1440/390 无整页横滚检查通过。已人工查看去向页桌面及手机图，实际文件名、项目、栏目和两类批准标签可见。push 同名产物也保留。

## 5. 风险与未确认
- 旧 SQLite CHECK 枚举不能在“不重建表、不删改历史”的约束下原地扩充。升级新增 approval_source_v2（含新枚举）和 pm_review_decision_audit 视图，v2 为权威；旧物理列保留约束，自动规则兼容槽为 candidate_review，绝不再写 direct_user。新库原列也接受 standing_user_rule。旧审计行不重写，只对具备明确 standing_user_rule 渠道/审核者及规则日期的记录在视图中标明来源，legacy_record=true。
- 自动归属只处理唯一明确项目名；模糊语义、多个项目、否定表达不猜测归属。资料字节去重沿用现有内容哈希策略，同一字节版本只有一条文件回执。
- 未使用真实模型、用户资料或真实 Key；本地安装包/本地有界面验收 NOT RUN，安装包和有界面截图证据来自上述 Windows CI。历史 B7-03 15 秒超时根因仍未确认，本次两次未复现，不能宣称已消除。
- PR #6 在本分支功能提交上叠加；等待审核人按 #5 → #6 顺序审核合并。

## 6. 大白话摘要
资料写明唯一项目名时会自动归入，并展示实际更新了哪些栏目。
不确定的资料先问用户，拒绝或撤销不会丢掉原文。
随手记也有去向回执。
常设规则自动处理与用户手动批准已分开标注。
完整实现两次云端都是 55/55 通过；首次漏带实现的 RED 如实保留。
