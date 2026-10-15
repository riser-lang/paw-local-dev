# TASK-G-04：修正测试代码，让判定规则和 lint 全绿

- 下达：Claude Code ｜ 2026-10-14 ｜ 依据：REVIEW-05
- **截止：10-14 18:00 前交出同一个提交的一次完整运行**（有红照交）。冻结 10-15 22:00。
- 在 gemini/G-03 上继续：先 `git merge origin/claude/p0-project-update`（取到 5487cb7），禁止 rebase 或 force push。PR #4 继续使用。
- `tests/paw_case.py`、`paw_verdict.py`、`paw_lint.py`、`required_cases.json` 不得修改。
- 本地先跑 `python tests/paw_lint.py`，必须输出 `lint clean` 才能推送。

## 逐项要求
1. **每个用例**都以 `c.fresh_install(clean_environment, lambda: silent_install(exe)[2], <安装目录>)` 开头，然后启动程序。用例需要的数据在本用例内重新造，不得依赖其他用例。
2. **造数据只能走产品的正常途径**：项目可以通过正式接口创建，或用 INSERT project 插入；待办、知识、候选、审核记录一律禁止直接写库。
   - 候选：把文档放入监控文件夹，经正常分析流程生成。mock-llm 的分析回复必须包含 TASK-G-03 第 7 条列出的四种候选。**如果 mock 已经返回了项目更新，产品却没有生成候选，这本身就是产品问题**，如实记 FAIL 并写进报告，不要插库补救。
   - 驳回：调用产品的驳回接口。
   - B8 的待办和知识通过"核准候选"产生。
3. **核准类检查**（B4-02 至 B4-06）必须读回接口返回体中的 `failed_count == 0`，并读回数据库：
   - B4-02：目标项目的 latest_progress_summary **等于** mock 给出的 new_summary 原文。
   - B4-03：目标项目的 latest_progress_summary 包含 mock 给出的陈述原文。
   - B4-06：第一次批量核准后计数**增加**，第二次核准后计数不变。
4. **页面检查**要用浏览器渲染后的文字（Playwright 的 `page.inner_text("body")`），不能用原始 HTML：
   - B2-01：页面文字含 "API Key" 和设置入口的名称。
   - B5-01：列表页文字含每个项目名和中文阶段名。
   - B5-02：详情页文字含"30 秒"标题，以及四项内容的原文。
   - B5-03：打开 /projects/<id> 后，最终网址是 /app/projects/<id>，且页面文字含项目名。
5. **B6-02**：收敛后同名有效项目**恰好 1 个**；backups 目录里的备份文件数比收敛前**多**，且新文件是在本用例内创建的。
6. **B7**：问题里不得包含要检查的关键词。B7-03 的问题问文档内容（比如"周报里提到的上线日期是哪天？"），检查回答里包含 fixture 文档中的那个**具体事实原文**，或文件名。mock 的回答只能从请求里拿到的上下文中摘取。
7. **截图**：B4、B5、B7 截图对应的具体页面（审核页、项目列表页、项目详情页、助手页），`c.screenshot(path, 该页面的网址)`。
8. 负对照 NC-01、NC-02 同样以 fresh_install 开头，使用 2.3.7 安装包。

## 交付（DONE-G-04.md）
- 运行链接（PR 头部提交）；`summary.md` 全文原样粘贴；lint 输出原样粘贴。
- 每个红色项写明：用例编号、失败的检查名、实际值；属于产品问题的，单独列出。
