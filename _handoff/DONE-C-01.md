# DONE-C-01：调研、测试底座、设计变量

2026-10-15；执行者 Codex；分支 `codex/C-01`；[PR #1](https://github.com/riser-lang/paw-local-dev/pull/1)，目标 main，未合并。C-02 尚未开始，未激活任何 pending 用例。

## 1. 任务逐项对照

| 子项 | 状态 | 证据 |
|---|---|---|
| 根因 C1–C7 只读核实 | 完成 | `_handoff/ROOT_CAUSES_C-01.md`，逐条判定、路径/行号、查询命令；没有把静态判断称为运行复现 |
| Ubuntu / Python 3.13 单元流水线 | 完成 | `.github/workflows/unit.yml`，任意 push/PR 触发，5 分钟 job 上限 |
| 内存 SQLite 关键行为测试 | 完成 | `tests/unit/test_smoke.py`，12 条参数展开测试：id、名称、未知名称扁平新建/带壳拒绝、原文唯一命中、多项目拒绝，另含错误 id 和归档项目 |
| E2E job summary | 完成 | `.github/workflows/test.yml` 增加 always 步骤，把门禁生成的 summary.md 原样追加到 GITHUB_STEP_SUMMARY；缺文件时只说明缺失，不伪造结果 |
| 设计变量提取 | 完成 | `_handoff/DESIGN_TOKENS.md`，区分编译 CSS 两次定义的最终值及 console 内联样式 |
| C-02…C-07 方案 | 完成 | `_handoff/PLAN-C-02_07.md`，文件/表/契约/治理接入与审核人用例编号 |
| 首次云端 unit + E2E | 完成运行 | unit GREEN，E2E 28/28 GREEN；独立 build.yml 一致性检查 RED，见下方，不宣称所有检查通过 |

## 2. 改动清单

- `.github/workflows/unit.yml`：新增纯逻辑快速测试。
- `.github/workflows/test.yml`：新增 verdict 的 GitHub job summary 展示，未改门禁和用例。
- `tests/unit/test_smoke.py`：测试已有 `_flat_project_update`，不安装程序、不导入应用启动入口。
- `_handoff/ROOT_CAUSES_C-01.md`：本地 C1–C7 调查。
- `_handoff/DESIGN_TOKENS.md`：现有主题提取。
- `_handoff/PLAN-C-02_07.md`：后续实施方案。
- `_handoff/DONE-C-01.md`、`_handoff/STATUS.md`：本报告与状态交接（云端结果出来后才写）。

没有修改产品代码、压缩前端、模板数据库、审核人判定器或 required/pending 用例；没有删除文件或改写历史；没有访问禁入目录/私有仓库，没有调用真实模型。

## 3. 测试与运行证据

写本报告前的 PR 头部：`99eafefa594c2811978f70dd816ba3c1a3ca6772`。PR 测试 checkout 的 GitHub 临时合并提交：`8d8582c00619a35c50817cc2e7de9257316d7650`（main + 本 PR，未由执行者合并）。以下 unit 和完整 E2E 是同一 PR 头部触发。随后追加报告/状态仅变更文档；最终头部复跑应由 [PR Checks](https://github.com/riser-lang/paw-local-dev/pull/1/checks) 核实，不能把旧运行伪称为文档提交的运行。

- [unit.yml / run 38727905586](https://github.com/riser-lang/paw-local-dev/actions/runs/38727905586)：success / GREEN，12 passed。
- [test.yml / run 38727905811](https://github.com/riser-lang/paw-local-dev/actions/runs/38727905811)：success / GREEN，28 PASS、0 FAIL、0 NOT RUN；3 个负对照在预定检查失败，门禁判定有效。
- [build.yml / run 38727905658](https://github.com/riser-lang/paw-local-dev/actions/runs/38727905658)：failure / RED；`verify_consistency.py` 对照 v2.3.8-baseline，`backend.repair_v238` 的指令不一致（idx=65）。前端 3 项、另外 3 个模块及 164 个 backend 模块清单一致。**本次没有修改 repair_v238，也没有改一致性比较标准。** 开工前 main 同样存在 build.yml 失败记录 [run 38727358803](https://github.com/riser-lang/paw-local-dev/actions/runs/38727358803)；未将这种既有差异豁免为 GREEN。
- push 事件重复触发的 E2E run 38727893741 已取消以释放 runner；它不是 PASS 证据，实际采用上述完整 PR 运行。未对失败用例进行重跑碰运气。

本地命令及关键输出：

```text
python -m pytest tests/unit -q
12 passed in 0.03s
python tests/paw_lint.py
lint clean
python scripts/scan_secrets_leak.py --scan-dir .
扫描文本文件数: 413
AGY_CREDENTIAL 凭证原文出现次数: 0
真实 API Key / Token 特征出现次数 (sk-, ghp-): 0
VERDICT: PASS
git diff --name-only 08d40d7 -- backend frontend tests/paw_case.py tests/paw_verdict.py tests/paw_lint.py 'tests/required_cases*.json' 'tests/pending/*.json'
（无输出）
```

扫描数来自首次提交前的本地扫描，不包括之后生成的本报告/临时下载文件；不能把文件数当成覆盖或安全完备性证明。所有新测试资料均为虚构。C-01 无新增 E2E 必过项，无 proposed_cases 文件（12 条新增的是 unit 测试）；pending 仍保持原位及原内容。

### summary.md 全文（run 38727905811 产物，原样）

```markdown
# Pipeline verdict for 8d8582c00619a35c50817cc2e7de9257316d7650

Required cases: 28 | PASS 28 | FAIL 0 | NOT RUN 0

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

## No problems

VERDICT: GREEN
```

## 4. 截图产物

均来自上述 E2E run 38727905811 的 `screens-B1`…`screens-B8` 产物，由现有测试生成。本任务没有新增页面，因此没有新增 PM 页、390px 或并排设计验收截图；这些是 C-06 的待做项，NOT RUN。

- `screen_B1_02.png`
- `screen_B2_01.png`、`screen_B2_02.png`、`screen_B2_03.png`
- `screen_B3_01.png`…`screen_B3_04.png`
- `screen_B4_01.png`…`screen_B4_07.png`
- `screen_B5_01.png`…`screen_B5_03.png`
- `screen_B6_01.png`、`screen_B6_02.png`
- `screen_B7_01.png`…`screen_B7_03.png`
- `screen_B8_01.png`…`screen_B8_03.png`

以上仅登记文件名及运行出处，没有额外声称人工查看全部图片；门禁的截图检查见原始报告。

## 5. 风险、未确认与停下原因

**触发开工说明第 3.1 / 9.1 条红线停点：发现现有巡航直接写正式项目字段。** 这是基线已有路径，本任务没有引入或运行该本地写入路径；本地仅静态读取，其动态复现 NOT RUN。证据如下：

1. `backend/runtime_core/scheduler.py:108` 的 `run_cruise(..., analyze_projects=True)` 默认开启分析；`scheduler.py:200` 调用 `project_analyzer.analyze_projects(...)`。
2. `backend/runtime_core/project_analyzer.py:256` / `:270`，在正式项目的进展/下一步为空时，用模型说明填补 `latest_progress_summary` / `next_action_summary`。
3. 同文件 `:289` 直接 `UPDATE project SET ... WHERE id = ?`，`:309` 写 `derived_field_auto_update`，其中 `change_id`、`commit_id` 明确为 NULL；`:323` 提交事务，没有 ReviewDecision → ApprovedChange → Committer。
4. `backend/main.py:760` 还提供直接调用该分析器的入口。仅给新档案另接治理链，无法消除此旧路径对正式数据的旁路写入。

复核命令与关键输出：

```powershell
rg -n 'def run_cruise|analyze_projects\(' backend/runtime_core/scheduler.py backend/main.py
# scheduler.py:108 默认 True；scheduler.py:200、main.py:760 调用分析器
rg -n 'if not proj.get|UPDATE project|derived_field_auto_update|conn.commit' backend/runtime_core/project_analyzer.py
# 256、270 取待填字段；289 UPDATE project；309 审计 change_id/commit_id 为 NULL；323 commit
```

按用户明确的“遇到红线停下”要求，不继续 C-02，不自行裁定例外。请审核人确认该既有旁路的修复安排与接续顺序；不能因目前 E2E GREEN 就认定它满足治理红线。另需审核人补齐公开仓库缺失的 `_handoff/REVIEW-04.md`，`git log --all --oneline -- _handoff/REVIEW-04.md` 无输出；05、06 已完整阅读。

稳定性备注：开工前同一 main 头部 `08d40d7` 有 [GREEN run 38727278779](https://github.com/riser-lang/paw-local-dev/actions/runs/38727278779) 和 [RED run 38727358769](https://github.com/riser-lang/paw-local-dev/actions/runs/38727358769)；后者 summary 为 27/28、B4-03 的 `empty_update_candidate_found` 失败。本次 PR 首次完整运行 B4-03 为 PASS。原因未确认，不以本次 PASS 掩盖已有不稳定迹象。

审核人验收：尚未进行。产品负责人验收：尚未进行。本 PR 没有合并，没有发布版本，没有替换保底提交。

## 6. 大白话摘要（5 行以内）

测试底座和后续方案已提交，12 条单元测试、28 条完整验收测试通过。
旧版本一致性检查仍报红，没有改规则绕过。
发现旧巡航会跳过审核，直接改项目正式字段。
按红线要求停在 C-01，等审核人确认修复安排，再继续 C-02。
