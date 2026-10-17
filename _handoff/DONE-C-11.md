# DONE-C-11（返工更新）

## 1. 子项对照

| 子项 | 状态 | 证据 / 说明 |
|---|---|---|
| 激活并保留审核人修订后的 C-11 required cases | 完成 | 审核人提交 `d7a7e79` 修订了 PM-S1-01 页面描述，并撤销 NC-PM-S1；测试判定文件未由执行者修改。 |
| `_recall()` 安全 FTS 查询、有界 LIKE 兜底和脱敏诊断 | 完成 | 保留前次实现；内存 SQLite/FTS5 单元覆盖 9 种点号、特殊字符、英文和中文长度情形。 |
| PM-S1-01 核验【资料】页与资料接口 | 完成 | 通过受支持的 `/v1/web/workspace/sources/register` 产品 API 登记虚构文件元数据；检查 `/v1/web/workspace/sources` 返回项，并在 `/app/files` 输入“搜索文件名或分类”后检查 `.source-grid` 渲染文字。 |
| PM-S1-02、PM-S1-03 及已有检查 | 完成 | 其检查逻辑保持不变；最终 PM09 区块 GREEN。 |
| 删除已撤销的 NC-PM-S1 执行代码 | 完成 | 从 `tests/run_pm_search_tests.py` 移除负对照执行函数，从 `tests/run_pm_tests.py` 移除调用。 |

## 2. 改动清单

- `tests/run_pm_search_tests.py`：PM-S1-01 改为资料源登记接口、资料列表接口和资料页搜索的双重检查；保留 PM-S1-02/03 检查。
- `tests/run_pm_tests.py`：删除已撤销的 NC-PM-S1 调用。
- `backend/runtime/core/paw_retrieval_core.py`、`tests/unit/test_retrieval_search_special_chars.py`：沿用前次提交的修复和单元测试，本次未修改。
- `tests/required_cases_C-11.json`：由审核人在 `d7a7e79` 修订，本次未修改。

PR：[#12](https://github.com/riser-lang/paw-local-dev/pull/12)。最终测试头部提交 `f340fcc5625af21a28789673351f80f1a65e778e`。

## 3. 测试

本地（`D:\PAW\paw-dev-codex`，最终代码提交 f340fcc）：

- `python tests/paw_lint.py` → `lint clean`
- `python -m pytest tests/unit -q` → `161 passed in 14.43s`
- `git diff --check`、`python -m py_compile tests/run_pm_search_tests.py` → 成功

云端完整流水线（同一 head SHA `f340fcc5625af21a28789673351f80f1a65e778e`）：

- push：[运行 39019532245](https://github.com/riser-lang/paw-local-dev/actions/runs/39019532245) → GREEN；83 PASS / 0 FAIL / 0 NOT RUN。
- PR：[运行 39019536499](https://github.com/riser-lang/paw-local-dev/actions/runs/39019536499) → GREEN；83 PASS / 0 FAIL / 0 NOT RUN。PR summary.md 内报告 SHA 为 `c7ce41ab667b25847989abfe95b285f3ba4909c0`；该 PR 运行 head SHA 与 push 一致，为 f340fcc。
- 两侧 unit 工作流均成功：push `39019532249`、PR `39019536481`；PR 安装包构建 `39019536502` 成功。
- 中间提交 `5e1dc2b` 的 PR 完整流程曾因扫描夹具未写入资料源注册表而失败；最终提交改由产品源登记 API 准备资料记录，最终 push/PR 均 GREEN。该中间运行不作为最终验收结果。

### push 运行 summary.md 全文（原样）

```text
# Pipeline verdict for f340fcc5625af21a28789673351f80f1a65e778e

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

VERDICT: GREEN
```

### PR 运行 summary.md 全文（原样）

```text
# Pipeline verdict for c7ce41ab667b25847989abfe95b285f3ba4909c0

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

VERDICT: GREEN
```

## 4. 截图

最终运行的 `screens-PM09` artifact 含 PM-E3-01 与 PM-E4-01 的既有截图：`screen_PM_E3_01_1440_cockpit.png`、`screen_PM_E3_01_1440_project.png`、`screen_PM_E3_01_390_cockpit.png`、`screen_PM_E3_01_390_project.png`、`screen_PM_E4_01_1440_cockpit.png`、`screen_PM_E4_01_1440_project.png`、`screen_PM_E4_01_390_cockpit.png`、`screen_PM_E4_01_390_project.png`。PM-S1-01 未单独生成截图；其页面与接口检查通过。

## 5. 风险与未确认事项

- 测试通过调用正式支持的 source registry 注册 API 构造页面使用的资料条目；该检查覆盖源注册接口、列表接口和资料页前端筛选，不覆盖监控目录扫描自动创建 source registry 记录的行为。
- PM-S1-02 与 PM-S1-03 代码未改，最终 PM09 区块通过。
- 未修改判定规则、考试目录、其他 required cases 或 `_handoff/STATUS.md`；未合并 PR。

## 6. 大白话摘要

审核人修订了 PM-S1-01，测试现在检查【资料】页面和资料接口。
NC-PM-S1 已按审核意见撤销，PM-S1-02/03 保持原有检查。
本地 lint clean，161 项单元测试通过。
push 和 PR 完整流水线均为 GREEN，报告附有两份完整 summary。


