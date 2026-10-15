# TASK-G-04 交付报告 (DONE-G-04.md)

- **交付日期**：2026-10-14 09:12 (北京时间)
- **执行人**：Gemini
- **审核人**：Claude Code
- **工作分支**：`gemini/G-03`
- **目标 PR**：[PR #4 (https://github.com/riser-lang/paw-local/pull/4)](https://github.com/riser-lang/paw-local/pull/4) -> 合入 `claude/p0-project-update`
- **PR 头部提交**：`cea49ea2bd87daeae69eb0d6b67f71b84dc568f1`
- **完整流水线运行链接**：[GitHub Actions Run 38493437929](https://github.com/riser-lang/paw-local/actions/runs/38493437929)

---

## 一、审核人门禁文件完整性核验

依据 `TASK-G-04` 红线纪律，由审核人（Claude Code）掌握的 4 个判定门禁文件**绝对未作任何修改**，原样保留自 `origin/claude/p0-project-update`（提交 `5487cb7`）：

1. `tests/paw_case.py`：未修改（`git diff tests/paw_case.py` 输出为空）
2. `tests/paw_verdict.py`：未修改（`git diff tests/paw_verdict.py` 输出为空）
3. `tests/paw_lint.py`：未修改（`git diff tests/paw_lint.py` 输出为空）
4. `tests/required_cases.json`：未修改（`git diff tests/required_cases.json` 输出为空）

---

## 二、本地静态检查 (paw_lint.py) 输出原文

在提交与推送前，本地执行 `python tests/paw_lint.py` 的原样输出如下：

```
lint clean
```

门禁退出码为 **0**，无任何绕过规则（无缺失 fresh_install、无常量比对、无无用检查、无 `>= 0`、无直接操作受保护表 SQL、无裸露 `/app` 截图）。

---

## 三、云端裁决结果 (Verbatim summary.md)

以下为云端 Reviewer Verdict Gate（Job ID 115625655732）由审核人脚本 `python tests/paw_verdict.py reports cea49ea2bd87daeae69eb0d6b67f71b84dc568f1 summary.md` 直接生成的报告原文：

```markdown
# Pipeline verdict for b7c3ec118756eb5a3d2ecdc9255273915c5e2edb

Required cases: 28 | PASS 27 | FAIL 1 | NOT RUN 0

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
| B4 | B4-03 | FAIL | accept_empty_proposed_change_failed_count_zero | mock-llm |
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

## Problems
- B4-03: FAIL (accept_empty_proposed_change_failed_count_zero)

VERDICT: RED
```

---

## 四、红色项（失败用例）详细排查与产品问题归属说明

本次完整云端测试流水线中，28 个必需用例中 **27 个 PASS**，仅 **1 个红色项**：

### 失败用例：B4-03
- **用例标题**：核准 proposed_change 为空、陈述中含项目名的项目更新：写入成功，项目总数不变
- **失败的检查名**：`accept_empty_proposed_change_failed_count_zero`
- **实际值**：`1`
- **预期值**：`0`
- **错误堆栈**（摘自 `report_B4.json`）：
  ```python
  Traceback (most recent call last):
    File "D:\a\paw-local\paw-local\tests\run_e2e_tests.py", line 640, in run_b4
      c.check("accept_empty_proposed_change_failed_count_zero", acc2.get("failed_count"), 0)
    File "D:\a\paw-local\paw-local\tests\paw_case.py", line 73, in check
      raise CheckFailed(name)
  tests.paw_case.CheckFailed: accept_empty_proposed_change_failed_count_zero
  ```

### 产品问题判定与根因分析（属于产品问题，非测试脚本问题）
1. **模型替身已准确按格式返回**：
   在 `scripts/mock_openai_server.py` 中，按 `engine.py` 提示词规范和 TASK-G-03 第 6 条明确要求，返回了 Candidate 4：
   `{"statement": "针对【冲刺项目】补充架构治理陈述与技术演进说明。", "proposed_change": {}}`。
2. **入库封装信封**：
   在文档监控摄取链路中（`scanner.py` -> `agent_planner.py` -> `governance.create_candidate`），系统将所有提议统一包裹了治理信封入库：
   `proposed_change = {"target_type": "project", "operation": "update", "payload": {}}`。
3. **核准解析逻辑未走 `_flat_project_update`**：
   当调用产品正式批量核准接口 `/v1/web/brain/review/batch_accept` 时，底层进入 `backend/repair_v238.py` 的猴子补丁 `_normalize`。
   在第 114 行：
   ```python
   if target == 'project' and candidate['candidate_type'] == 'project_update' and not (
           isinstance(change, dict) and 'payload' in change and 'target_type' in change):
       operation, payload = _flat_project_update(conn, candidate, payload)
   ```
   因为摄取服务已将字段包装为 `{"target_type": ..., "payload": ...}`，条件 `not (isinstance(change, dict) and 'payload' in change and 'target_type' in change)` 计算为 `False`，导致针对陈述中提取项目名的专用补丁 `_flat_project_update` **未被执行**。
4. **ID 解析抛异常**：
   代码紧接着执行第 128 行的标准 `_resolve_id(conn, table, payload, candidate)`。因 `payload` 为 `{}`（空字典），无法提取 `id` 或 `name`，抛出：
   `ValueError: 无法唯一确定更新目标，请在编辑草稿中填写目标 id；原有记录未改变。`
   接口捕获异常并记录在 `failed_details` 中，返回 `failed_count: 1`。
5. **红线遵循**：
   依据 `TASK-G-04` 红线指令：
   > “如果 mock 已经返回了项目更新，产品却没有生成候选，这本身就是产品问题，如实记 FAIL 并写进报告，不要插库补救。”
   > “产品问题只记录不修，不得修改产品代码或用例来让测试通过。”

   因此测试代码完全遵循真实用户与系统调用路径，如实记录此产品逻辑缺陷，绝不直接写库注入伪造数据。

---

## 五、关键板块核心检查点与修复验证结论

1. **B4-02 扁平项目更新核准（已修复并验证生效）**：
   - 检查点 `flat_update_candidate_found`：`True`
   - 检查点 `accept_flat_update_failed_count_zero`：`0`
   - 检查点 `project_update_committed`：数据库 `latest_progress_summary` 成功更新为 `"本周推进核心模块执行与云端测试验证"`
   - 检查点 `project_count_unchanged`：项目总数保持为 1，未误增项目
   - 判定：**PASS**
2. **负对照 NC-01（基线 2.3.7 缺陷复现有效）**：
   - 运行于 `v2.3.7-baseline` 安装包上，执行完全相同的 B4-02 扁平项目更新操作。
   - 实际读取值为 `当前所处阶段：处于落地执行期，核心模块已打通`（旧值），未能更新为 `本周推进核心模块执行与云端测试验证`。
   - 检查点 `project_update_committed` 准确判定为 `FAIL`，准确命中并证明了审核人对 2.3.8 的 P0 修复有效。
3. **负对照 NC-02 与 NC-03**：
   - NC-02 在 2.3.7 上准确失败于 `explainer_fields_filled`。
   - NC-03 在注入残留文件时准确失败于 `environment_clean`。
   - 证明整体负对照系统处于严密防御状态。
4. **页面 Playwright 真实渲染文字与精准截图**：
   - B1、B2、B4、B5、B6、B7、B8 全部用例在各自的用例块中独立执行了完整的卸载、残留清空与全新安装（`c.fresh_install`）。
   - B4、B5、B7 所有截图均通过真实浏览器渲染定位到目标功能页（`/app/review`、`/app/projects`、`/app/agnes`），绝无裸露 `/app` 首页截图。
