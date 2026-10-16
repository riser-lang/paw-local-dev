# TASK-C-10（执行者：Gemini）：周报、进化指标、标准考题

- 下达：Claude Code ｜ 2026-10-16
- 工作目录 `D:\PAW\paw-dev`。先 `git checkout main && git pull`，再拉分支 **`gemini/C-10`**，开 PR 合入 main。

## 0. 先读（必读，不能跳过）
1. `_handoff/CODEX_START_HERE.md`：规则对任何执行者都一样适用，把文中的"Codex"理解为"你"。
2. `_handoff/STATUS.md`、`REVIEW-C-08_09.md`。
3. **`_handoff/REVIEW-04.md`、`REVIEW-05.md`**：这是你（Gemini）在本项目早期的测试被打回的记录，包括条件永远成立的检查、直接写库、一次安装多个用例共用、问题里自带答案、截图对着首页。现在 `tests/paw_lint.py` 和 `tests/paw_verdict.py` 会自动拦截其中大部分，剩下的由审核人看代码。

## 1. 硬性规定（违反即打回）
- **不得删除任何文件**（以前 REVIEW-03、REVIEW-04 曾在旧目录被删）。不得改写或删掉 STATUS.md 里审核人写的段落，只能在看板里更新自己那一行。
- 不得直接推送 main，不得自行合并 PR，不得 force push。
- 不得修改 `tests/paw_case.py`、`paw_verdict.py`、`paw_lint.py`、`tests/required_cases*.json`、`tests/pending/exam/*`。
- DONE 报告必须**原样粘贴** summary.md 全文，不得另写总结代替；红色项如实写。没有做到的就写"未做"或"NOT RUN"。
- 测试只用 mock-llm 和假 Key；mock 只能根据请求内容作答，不得按题号或题目文字做特殊处理。

## 2. 要做的事
1. **激活用例**：`git mv tests/pending/required_cases_C-10.json tests/required_cases_C-10.json`（内容不改），共 PM-F1-01、PM-F2-01、PM-F3-01 三条。
2. **F1 周报**（PM-F1-01）：每个项目一份周报（进展、变化、风险、建议），再加一份跨项目对比。内容只能来自已有记录和带依据的建议（复用 C-08 的建议存储和过滤），在工作台可以查看。没填 Key 时，"建议"部分显示需要填写 Key，其余部分照常由已有记录生成。
3. **F2 进化指标**（PM-F2-01）：待决定数、资料覆盖率、冲突数、知识缺口数、建议被采纳率、积压量，按周记录，在驾驶舱上显示趋势。
4. **F3 标准考题**（PM-F3-01）：
   - 新写 `scripts/build_exam_corpus.py`：把 `tests/pending/exam/corpus.json` 中的每份资料按指定类型（docx / pptx / xlsx / md）**逐字**生成出来。
   - 新写考试执行器（作为 PM-F3-01 用例的一部分）：在用例内 fresh_install → 按 step 顺序把资料放进监控文件夹、每步扫描完成后再放下一份 → 把所有**不需要用户决定**的候选走正常流程核准（需要用户决定的保持原样）→ 每道题开一个新的助手会话提问 → 按 `questions.json` 里 `check_semantics` 的定义逐题判分。
   - 把每道题的问题、回答原文、返回的来源、各项检查结果写成 `reports/exam_result.json` 并上传为产物。
   - 判定：通过率 ≥ 90%（`gate.min_pass_rate`）。目前还没有带考题的正式版本，所以"不得低于上一版"这一条先记录本次结果作为基线；DONE 报告里写明基线数值。
   - 某道题不通过时，先判断是产品问题还是考题与产品行为不匹配；**不得修改考题**，有疑问写进 DONE，由审核人决定。
5. 每个新功能都要有单元测试（ubuntu 上的 tests/unit）。

## 3. 交付
- 写 `_handoff/DONE-C-10.md`（格式见 CODEX_START_HERE 第 7 节），附上 push 和 PR 两次运行的链接与 summary.md 全文，以及考题通过率和每道错题的原因。
- 停下时只回复这一行：
  `Gemini 进度：已完成 C-10，PR #<编号>，测试 <push 与 PR 各自 GREEN/RED>，考题通过率 <x/24>，报告 _handoff/DONE-C-10.md；停下原因：<…>`
