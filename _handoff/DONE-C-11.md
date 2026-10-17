# DONE-C-11

## 1. 子项对照

| 子项 | 状态 | 证据 / 说明 |
|---|---|---|
| 激活审核人提供的 C-11 required cases，内容不改 | 完成 | `git mv tests/pending/required_cases_C-11.json tests/required_cases_C-11.json`；原文件 SHA-256 `27618f4e3b86bea75e56366ccb1eab25ecb0ff73`，移动后未改。 |
| `_recall()` 将输入安全转换为 FTS5 查询 | 完成 | 词元逐个双引号包裹，内部引号转义；限制输入长度。 |
| FTS 无结果或失败时执行有界 LIKE 兜底，匹配标题和正文 | 完成 | LIKE 转义 `\`、`%`、`_`；诊断日志只记录异常类别，不含搜索原文。 |
| 单元覆盖特殊字符、英文与中文长度 | 完成 | `tests/unit/test_retrieval_search_special_chars.py`，9 种查询均覆盖。 |
| 检查其他直接 MATCH 调用 | 完成 | `git grep -n "MATCH" -- backend tests`：唯一执行 MATCH 位于 `backend/runtime/core/paw_retrieval_core.py` 的 `_recall()`。 |
| 云端已激活验收用例 | 部分完成 | PM-S1-02 与 PM-S1-03 通过；PM-S1-01 未通过，NC-PM-S1 未能按预期失败检查结束；因此 C-11 尚未达到验收通过。 |

## 2. 改动清单

- `backend/runtime/core/paw_retrieval_core.py`：安全 FTS 查询、任意长度有界 LIKE 兜底及脱敏诊断；检索索引补充可审计的关联来源名称。
- `tests/unit/test_retrieval_search_special_chars.py`：特殊字符、点号文件名、英文和中英文长度的内存 SQLite/FTS5 回归测试。
- `tests/run_pm_search_tests.py`：新增 PM-S1-01/02/03 和 NC-PM-S1 的安装版验收步骤。
- `tests/run_pm_health_tests.py`、`tests/run_pm_tests.py`：将 C-11 验收步骤挂入现有运行器。
- `tests/required_cases_C-11.json`：通过 git mv 激活；内容保持原样。

PR：[#12](https://github.com/riser-lang/paw-local-dev/pull/12)，头部提交 `d19c6a37e7595efb56a72b4da0500e4e97eb491b`。

## 3. 测试

本地（工作树 `D:\PAW\paw-dev-codex`，头部提交 d19c6a3）：

- `python tests/paw_lint.py` → `lint clean`
- `python -m pytest tests/unit -q` → `161 passed in 12.90s`

云端完整流水线（同一头部提交）：

- push： [运行 39011832951](https://github.com/riser-lang/paw-local-dev/actions/runs/39011832951) → RED；报告 82 PASS / 1 FAIL / 0 NOT RUN。PM-S1-01 的 `full_filename_search_found` 失败，NC-PM-S1 由于 `exception` 而非预期的 `full_filename_search_found` 失败。
- PR： [运行 39011837393](https://github.com/riser-lang/paw-local-dev/actions/runs/39011837393) → RED；报告 82 PASS / 1 FAIL / 0 NOT RUN。失败项相同。PR 的 summary.md 内部标识 SHA 为 `13c6d4f931645a9e871507accd2f23e3c3fc1631`（PR 流水线生成的报告文本）；运行本身的 head SHA 是 d19c6a3。
- 两个任务专用单元工作流均 GREEN：push `39011832952`，PR `39011837379`；PR installer build `39011837350` 成功。
- PM-S1-01 的云端调试输出为 `C-11 full filename search evidence {"count": 0, "items": []}`。PM-S1-02、PM-S1-03 通过。

### push 运行 summary.md 全文（原样）

```text
# Pipeline verdict for d19c6a37e7595efb56a72b4da0500e4e97eb491b

Required cases: 83 | PASS 82 | FAIL 1 | NOT RUN 0

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
| NEGATIVE-CONTROL | NC-PM-S1 | FAIL | exception | mock-llm |
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
| PM09 | PM-S1-01 | FAIL | full_filename_search_found | mock-llm |
| PM09 | PM-S1-02 | PASS |  | mock-llm |
| PM09 | PM-S1-03 | PASS |  | mock-llm |
| PM10 | PM-F1-01 | PASS |  | mock-llm |
| PM10 | PM-F2-01 | PASS |  | mock-llm |
| PM10 | PM-F3-01 | PASS |  | mock-llm |

## Problems
- PM-S1-01: FAIL (full_filename_search_found)
- NC-PM-S1: expected FAIL on full_filename_search_found, got FAIL on exception

VERDICT: RED
```

### PR 运行 summary.md 全文（原样）

```text
# Pipeline verdict for 13c6d4f931645a9e871507accd2f23e3c3fc1631

Required cases: 83 | PASS 82 | FAIL 1 | NOT RUN 0

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
| NEGATIVE-CONTROL | NC-PM-S1 | FAIL | exception | mock-llm |
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
| PM09 | PM-S1-01 | FAIL | full_filename_search_found | mock-llm |
| PM09 | PM-S1-02 | PASS |  | mock-llm |
| PM09 | PM-S1-03 | PASS |  | mock-llm |
| PM10 | PM-F1-01 | PASS |  | mock-llm |
| PM10 | PM-F2-01 | PASS |  | mock-llm |
| PM10 | PM-F3-01 | PASS |  | mock-llm |

## Problems
- PM-S1-01: FAIL (full_filename_search_found)
- NC-PM-S1: expected FAIL on full_filename_search_found, got FAIL on exception

VERDICT: RED
```

## 4. 截图

本任务 PM-S1-01 未通过，未产出 C-11 专属截图。两次运行的 `screens-PM09` artifact 含既有 PM-E3-01 / PM-E4-01 截图，文件名为 `screen_PM_E3_01_1440_cockpit.png`、`screen_PM_E3_01_1440_project.png`、`screen_PM_E3_01_390_cockpit.png`、`screen_PM_E3_01_390_project.png`、`screen_PM_E4_01_1440_cockpit.png`、`screen_PM_E4_01_1440_project.png`、`screen_PM_E4_01_390_cockpit.png`、`screen_PM_E4_01_390_project.png`；它们不代表 C-11 通过。

## 5. 风险与未确认事项

- 完整文件名搜寻的系统验收仍失败；PM-S1-01 通过正常导入、审核与查询路径后得到 0 条结果，需审核人继续查明正式知识与关联源文件名如何进入可检索索引。
- 负对照 harness 遇到异常，未证明 2.3.9 恰好在规定检查项失败。
- 按 `_handoff/CODEX_START_HERE.md` 的同一问题最多自修 3 次规则，本次针对 C-11 云端集成验收已用 3 次修复提交（`511cfe6`、`c425da5`、`d19c6a3`）；停止并交审核人判断后续方向。
- 未修改判定规则、已有 required cases、考试目录或 `_handoff/STATUS.md`；未合并 PR。

## 6. 大白话摘要

FTS 查询语法错误和兜底已修好，本地 lint 与 161 项单元测试通过。
云端特殊字符查询和助手来源检查通过。
完整文件名搜索仍没有结果，负对照也被测试 harness 异常打断。
push 与 PR 完整流水线都是 RED，需审核人继续定位索引关联问题。


