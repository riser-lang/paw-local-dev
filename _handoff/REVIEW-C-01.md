# REVIEW-C-01

- 审核人：Claude Code ｜ 2026-10-15
- **结论：通过，PR #1 已合并。** 报告诚实，证据完整；遇到红线就停下，做法正确。

## 已核实
- PR #1 只改了 workflow、单元测试和文档；没有动产品代码、判定规则或 pending 用例。
- 有代码的那个提交（99eafef）上 unit GREEN、E2E 28/28 GREEN；之后的提交只改了 DONE / STATUS 两份文档。
- 治理旁路属实：`backend/runtime_core/project_analyzer.py:256-323` 在巡航中直接 `UPDATE project` 写入 latest_progress_summary / next_action_summary，审计记录里 change_id 和 commit_id 都是 NULL。

## 裁定
1. **治理旁路：列为 P0，放进 C-02 一并修。** 新的项目档案本来就要求"事实与 AI 建议分开存放"，这条旧路径正好由此替换：
   - 巡航和分析接口**不再写 project 表的正式字段**。模型生成的说明改存到派生区（新表，非正式数据），在档案页上标注"AI 生成"，可以作为建议展示，也可以由用户一键"采纳"；采纳时生成候选，走 ReviewDecision → ApprovedChange → Committer。
   - `main.py:760` 的入口同样处理。
   - 新增审核人用例 `tests/pending/required_cases_C-02b.json`（PM-GOV-01 至 03，外加负对照：2.3.9 上必须在 `formal_fields_unchanged_after_cruise` 这项检查失败），随 C-02 一起激活。
   - 注意：B5-02（30 秒快速了解）不能因此退化；`_stored_explainer` 已有"尚未记录"的兜底文字，派生区的 AI 说明也可以作为展示来源，但必须标注出来。
2. **build.yml 一致性比对：停用。** 它只是 2.3.8 还原阶段的一次性证明，审核人修复 P0 时已经有意改动了 repair_v238，所以必然不一致。请在 C-02 中删掉 build.yml 里的一致性比对步骤（保留打包），并在 DONE 中说明。scripts/verify_consistency.py 文件保留，不删除。
3. **B4-03 不稳定**（main 08d40d7 上的 run 38727358769，`empty_update_candidate_found` 失败）：列为"不稳定用例"。C-02 期间查明原因：是测试等待时间不够，还是产品生成候选本身不稳定？如果是产品问题就修产品；如果只是测试等待不足，可以改成"轮询直到扫描状态显示完成"，**不得只是把超时时间加长**。附上证据。
4. REVIEW-04 原文已丢失（在旧工作目录中被删除，从未提交），审核人已补写摘要。

## 继续
按路线图推进 C-02（另加上面第 1–3 条）→ C-03 → C-04，到 C-04 完成后停下。
