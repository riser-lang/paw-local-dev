# DONE-C-11：第二次返工

- PR：[#12](https://github.com/riser-lang/paw-local-dev/pull/12)，分支 `codex/C-11`
- 最终分支头：`9827606098fa3b1cd1eace0c395658e9610bf8c7`（同步 `origin/main` 后）
- push 完整验证：GREEN，[run 39022857027](https://github.com/riser-lang/paw-local-dev/actions/runs/39022857027)
- PR 完整验证：GREEN，[run 39022859325](https://github.com/riser-lang/paw-local-dev/actions/runs/39022859325)；PR 工作流在 GitHub merge ref `33f74d449afef417f06014cea8a2b90855ca461d` 上执行
- push / PR 单元工作流：均 GREEN（[push run 39022857069](https://github.com/riser-lang/paw-local-dev/actions/runs/39022857069)，[PR run 39022859323](https://github.com/riser-lang/paw-local-dev/actions/runs/39022859323)）

## 1. 任务核对

| 要求 | 结果 | 证据 |
|---|---|---|
| 扫描导入的资料进入【资料】页登记 | 完成 | 扫描阶段通过 `repository.register_source(...)` 写入资料库；PM-S1-01 在资料 API 和渲染页面上检查完整文件名 |
| 标题含扩展名、路径/来源/哈希/类型信息齐全 | 完成 | `title=p.name`、canonical path、`local_scan`、文件哈希；按扩展名映射 source_kind |
| 同路径重新扫描更新原记录 | 完成 | repository 的稳定 ID 由来源与路径决定；单测检查重复登记仅一行且 content_hash 更新 |
| PM-S1-01 走正常“监控文件夹 → 扫描”路径 | 完成 | 未调用 `/sources/register`；检查 `scanned_file_listed_in_sources` 先于浏览器步骤，页面检查使用 `.source-grid.inner_text()` |
| NC-PM-S1 在 v2.3.9 同步骤、同名检查失败 | 完成 | 两次完整流程报告均记录 `NC-PM-S1` 在 `scanned_file_listed_in_sources` 失败；负对照报告 artifact 也确认同名失败项 |
| PM-S1-02、PM-S1-03 | 完成 | 未更改原用例与既有检查；两份 summary 均为 PASS |
| 本地 lint 与单元测试 | 完成 | `python tests/paw_lint.py` → `lint clean`；`python -m pytest tests/unit -q` → `162 passed` |

## 2. 改动清单

- `backend/runtime_core/scanner.py`：正常扫描成功与旧检查点快速路径都会登记扫描来源，便于旧版本已经扫过的文件在重新扫描后进入资料库。
- `backend/workspace_api/repository.py`：仅为 `source_origin` 增加 `local_scan` 允许值；资料表字段没有数据库取值约束，无需迁移。
- `tests/unit/test_scanner_source_registry.py`：验证完整文件名、扩展名、路径、local_scan、类型及哈希；相同路径二次登记仅留一条并更新哈希。
- `tests/run_pm_search_tests.py`：PM-S1-01 改为生成文件、配置监控目录、启动扫描、等待扫描并检查资料 API 与渲染文本；恢复 `run_c11_negative`，使用传入的 v2.3.9-baseline 安装包跑相同步骤。
- `tests/run_pm_tests.py`：PM 负对照流水线调用 NC-PM-S1。
- `tests/required_cases_C-11.json`：使用审核人第二次修订的必测用例；内容未改动。
- `_handoff/REVIEW-C-11.md` 与 `_handoff/STATUS.md`：随 `origin/main` 同步提交带入审核人已提交的更新，用于消除 PR 冲突；未手工撰写状态或审核内容。

## 3. 测试结果

### 本地

```text
python tests/paw_lint.py
lint clean

python -m pytest tests/unit -q
162 passed
```

### 云端 push

- Commit：`9827606098fa3b1cd1eace0c395658e9610bf8c7`
- Full verification：[run 39022857027](https://github.com/riser-lang/paw-local-dev/actions/runs/39022857027) — GREEN
- Unit：[run 39022857069](https://github.com/riser-lang/paw-local-dev/actions/runs/39022857069) — GREEN
- required cases：83 PASS，0 FAIL，0 NOT RUN。

### 云端 PR

- PR：[#12](https://github.com/riser-lang/paw-local-dev/pull/12)
- PR source head：`9827606098fa3b1cd1eace0c395658e9610bf8c7`
- Full verification：[run 39022859325](https://github.com/riser-lang/paw-local-dev/actions/runs/39022859325) — GREEN
- Unit：[run 39022859323](https://github.com/riser-lang/paw-local-dev/actions/runs/39022859323) — GREEN
- required cases：83 PASS，0 FAIL，0 NOT RUN。

### 新增/保留用例核对

- PM-S1-01、PM-S1-02、PM-S1-03 均 PASS。
- NC-PM-S1 作为负对照在 `scanned_file_listed_in_sources` 失败，符合预期；负对照整体由 verdict gate 判为通过。
- PM-S1-01 页面结果取自渲染后的 `.source-grid` 文本；资料 API 也返回扫描文件。

### Summary 原文（push run 39022857027）

```summary-push
# Pipeline verdict for 9827606098fa3b1cd1eace0c395658e9610bf8c7

Required cases: 83 | PASS 83 | FAIL 0 | NOT RUN 0

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
| NEGATIVE-CONTROL | NC-PM-S1 | FAIL | scanned_file_listed_in_sources | mock-llm |
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
| PM09 | PM-S1-01 | PASS |  | mock-llm |
| PM09 | PM-S1-02 | PASS |  | mock-llm |
| PM09 | PM-S1-03 | PASS |  | mock-llm |
| PM10 | PM-F1-01 | PASS |  | mock-llm |
| PM10 | PM-F2-01 | PASS |  | mock-llm |
| PM10 | PM-F3-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### Summary 原文（PR run 39022859325）

```summary-pr
# Pipeline verdict for 33f74d449afef417f06014cea8a2b90855ca461d

Required cases: 83 | PASS 83 | FAIL 0 | NOT RUN 0

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
| NEGATIVE-CONTROL | NC-PM-S1 | FAIL | scanned_file_listed_in_sources | mock-llm |
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
| PM09 | PM-S1-01 | PASS |  | mock-llm |
| PM09 | PM-S1-02 | PASS |  | mock-llm |
| PM09 | PM-S1-03 | PASS |  | mock-llm |
| PM10 | PM-F1-01 | PASS |  | mock-llm |
| PM10 | PM-F2-01 | PASS |  | mock-llm |
| PM10 | PM-F3-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

## 4. 截图

- push 运行 artifact：`screens-PM09`，来自 [run 39022857027](https://github.com/riser-lang/paw-local-dev/actions/runs/39022857027)。
- PR 运行 artifact：`screens-PM09`，来自 [run 39022859325](https://github.com/riser-lang/paw-local-dev/actions/runs/39022859325)。
- PM-S1-01 的通过依据是已渲染页面文字检查与资料 API；没有以截图替代断言。

## 5. 风险与未确认事项

- 【资料】页现有列表最多加载最近 200 份资料，搜索仍只过滤当前加载列表；本任务未调整该既有限制。
- 未合并 PR；等待审核人复核并决定是否合并。

## 6. 大白话摘要

扫描完成后，文件会进入【资料】页，标题保留扩展名。
同一路径重新扫描会更新现有资料记录。
完整文件名搜索与特殊字符检索用例均通过。
2.3.9 负对照按预期在扫描登记检查失败。
push 和 PR 的完整流水线及单元测试均为 GREEN。
