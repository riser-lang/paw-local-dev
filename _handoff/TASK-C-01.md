# TASK-C-01：调研 + 测试底座 + 设计变量

- 下达：Claude Code ｜ 2026-10-15
- 分支：`codex/C-01`，PR 合入 main

## 1. 根因核实（只读）
逐条核实需求文档第 5 节的 C1–C7，在本地代码中是否存在。每条写明：存在 / 不存在 / 部分存在 + 文件:行号 + 一句话证据。

## 2. 测试底座
1. 新建 `.github/workflows/unit.yml`：在 ubuntu-latest、Python 3.13 上运行 `pytest tests/unit -q`，任何推送和 PR 都触发。
2. 新建 `tests/unit/test_smoke.py`：至少包含审核写入链路的现有关键行为，例如 `backend/repair_v238.py` 中 `_flat_project_update` 的 6 种情形（id 命中、项目名命中、未知名称时扁平格式新建 / 带外壳报错、原文摘录逐字命中、两个项目都出现时报错）。用内存 SQLite，不安装程序。
3. 在 test.yml 的 summary job 里，把 `summary.md` 同时写进 GitHub 的 job summary（`$GITHUB_STEP_SUMMARY`），方便审核人直接查看。

## 3. 设计变量
只读阅读 `frontend/dist/` 中的 CSS（`assets/index-*.css`、`paw_console.js` 内联样式），提取颜色、字体、圆角、边框、间距，写入 `_handoff/DESIGN_TOKENS.md`（变量名、取值、出处）。

## 4. 任务拆解
把路线图中的 C-02 至 C-07 各写成半页的实施方案，放进 `_handoff/PLAN-C-02_07.md`：涉及的文件、新表或新字段、新的语义任务契约名称，以及准备新增的端到端用例编号与一句话描述（例如 `PM-B2-01：同族新版本上传后，旧版标为 superseded，栏目引用自动切换到新版`）。

## 5. 首次运行
推送后等 unit.yml 和 test.yml 都跑完。test.yml 应该与参赛版一样是 GREEN（28/28）。如果是 RED，先判断是否因为仓库迁移导致（例如找不到 release 附件），如实写进报告。

## 交付
按 CODEX_START_HERE.md 第 7 节写 `_handoff/DONE-C-01.md`。最后一句按第 2 节第 5 条的固定格式。
