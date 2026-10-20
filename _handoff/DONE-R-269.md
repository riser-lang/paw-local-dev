# R-269 完成报告

## 任务结果

| 要求 | 结果与证据 |
|---|---|
| 接手 PR #16、rebase 到最新 main | 完成。在 `D:\PAW\paw-dev-codex` 执行 `git fetch origin --prune`、`git switch codex/R-265`、`git rebase origin/main`。基线为 `origin/main` = `3fb84fb`（PR #19 / R-268 已合并）。R-265 的 3 个提交（`0195ec2`、`147adc3`、`b8c542f`）原样重放，无冲突。 |
| 远端只做精确 force-with-lease | 完成。两次推送都用显式租约：`--force-with-lease=codex/R-265:b8c542f…`（首次）与 `--force-with-lease=codex/R-265:041834b…`（修正 BOM 后 amend）。未使用 `--force`，未覆盖他人提交。 |
| 版本说明占位文字定稿 | 完成。删除“已知限制”第 2、3 条的“v2.5.0 已优化”和“上述速度表述为发布文案，仍待审核人确认后定稿。”，改写为实测口径；“已知限制”由 6 条整理为 8 条，逐条如实。 |
| 版本说明补三条新增 | 完成。第 11 条为关闭深度思考后的实测时长（12 个样例文件扫描约 90–100 秒，问答一般 10–30 秒）；第 12 条为回答来源同时列出知识条目的原始文件；第 13 条为数据卡缺标题或项目名称时提示“请补充标题和项目名称后再接受”。 |
| 版本说明已知限制如实写 | 完成。第 4 条写明个别问题可能因依据不足拒答、换个问法或补充资料即可；第 5 条写明来源显示在正文末尾“来源：”行、没有单独的“查看来源”面板；第 2 条写明首次扫描时间与文件数量、模型服务速度有关。 |
| 数字只用实测、不夸大 | 完成。取 R-264/267/268 五次真实模型运行的 `scan_seconds`：89.5、99.7、118.1、88.3、100.1 秒（均为 12 个样例文件），故写“约 90–100 秒”；单题耗时实测 7.3–50.9 秒、多数集中在 13–26 秒，故写“一般 10–30 秒”。偏慢的 118.1 秒和 50.9 秒没有隐藏，也未写成更快的数字，由“文件更多、文件更大或模型服务响应更慢时需要更久”“模型服务响应慢时需要等待更久”覆盖。 |
| 使用说明对照成品核对 | 完成。核对 `frontend/dist/paw_console.js`、`frontend/dist/assets/index-qIwUsXqw.js`、`frontend/pm-src/*`、`installer/paw_setup.iss`，改正 4 处不一致（见下表），其余标签逐一确认与成品一致。 |
| CI 首次运行即 GREEN | 完成。同一提交 `1c2737c` 上 push 侧与 PR 侧都是第一次运行就通过，未重跑。 |
| 边界 | 只改两份发布文档并新增本报告；未改产品代码、审核人文件、`test.yml`、题目与判定；未删除文件；未改文件夹权限或系统设置；未出现任何 Key；未合并、未打 tag。 |

### 使用说明改正项（对照成品）

| 原文档写法 | 成品实际 | 依据 |
|---|---|---|
| “点击页面右下角悬浮按钮” | 悬浮按钮固定在**左下角**，按钮文字是【控制中心】 | `paw_console.js` 中 `#paw-fab-btn{position:fixed;bottom:20px;left:20px}`，按钮内含 `<span>控制中心</span>`，`title="PAW 本地控制中心"` |
| “打开右下角【PAW 本地控制中心】” | 同上，改为左下角 | 同上 |
| “点击左侧【项目】进入项目列表，打开项目后可查看项目档案” | 点【项目】**直接打开当前项目档案**；没有项目时才落到项目列表 | `frontend/pm-src/nav.js` 拦截【项目】点击并跳转 `/app/pm/projects/<first>` |
| “再决定接受或拒绝”“保存草稿后再批准”“不能批准” | 按钮是【拒绝】/【核准并写入】/【保存草稿】 | `index-qIwUsXqw.js`：`children:"拒绝"`、`children:w?"处理中…":"核准并写入"`、`children:"保存草稿"` |

已逐项确认一致（未改动）：【系统与模型】【文件夹扫描】【开始全量扫描】【从断点继续扫描】【继续扫描】【推理大脑 Provider 预设模板】【自定义 OpenAI 兼容接口 / 网关】【API Key】【Base URL】【Model Name】【测试当前模型连通性】【保存所有设置】【监控工作目录 (支持添加多个文件夹)】【添加监控文件夹】【驾驶舱】【项目档案】【查看来源】【资料】【搜索文件名或分类】【知识】【搜索正式知识】【待我审核】【待确认】【需要你决定】【稍后】【项目周报】【跨项目对比与项目周报】【AI助手】【使用说明】【PAW Personal Work OS】。其中驾驶舱页含“需要你决定／项目总览／AI 建议／资料去向／系统状态”，项目档案含【项目周报】，与文档描述一致。

## 改动清单

- `docs/版本说明_v2.5.0.txt`：删除占位文字，按实测数据定稿；新增第 11–13 条；重写“已知限制”为 8 条。保留 UTF-8 BOM。
- `docs/使用说明.txt`：改正悬浮按钮位置与名称、【项目】跳转行为、审核按钮名、数据卡提示原文、AI 助手来源显示位置；扫描等待的描述与实测口径对齐。保留 UTF-8 BOM。
- `_handoff/DONE-R-269.md`：记录任务、证据和 CI 结果。

## 检查结果

- `git diff --check`：通过，无空白错误。
- `python tests/paw_lint.py`：lint clean。
- `python -m pytest tests/unit -q`：227 passed（含 `tests/unit/test_release_docs.py` 的 UTF-8 BOM 与安装包同步检查）。
- 过程说明：首次提交 `041834b` 用编辑器改文档时丢了 UTF-8 BOM，`test_release_docs` 会因此失败。已恢复 BOM 后 `git commit --amend`，并把被取代提交上仍在跑的流水线取消；最终提交 `1c2737c` 的流水线是第一次运行即通过。
- push 侧完整流水线：[run 39363686644](https://github.com/riser-lang/paw-local-dev/actions/runs/39363686644) — **GREEN**（22 个作业全部成功，含 Reviewer Verdict Gate）。
- push 侧单测：[run 39363686719](https://github.com/riser-lang/paw-local-dev/actions/runs/39363686719) — **GREEN**。
- push 侧等价 workflow_dispatch（同一提交 `1c2737c`）：[run 39363694209](https://github.com/riser-lang/paw-local-dev/actions/runs/39363694209) — **GREEN**。
- PR 侧完整流水线：[run 39363690702](https://github.com/riser-lang/paw-local-dev/actions/runs/39363690702) — **GREEN**（22 个作业全部成功，含 Reviewer Verdict Gate；`codex/**` 有 push 触发，故 push 侧同时有自动流水线）。
- PR 侧单测：[run 39363690698](https://github.com/riser-lang/paw-local-dev/actions/runs/39363690698) — **GREEN**。
- PR 侧安装包构建：[run 39363690701](https://github.com/riser-lang/paw-local-dev/actions/runs/39363690701) — **GREEN**。
- PR：[#16](https://github.com/riser-lang/paw-local-dev/pull/16)，目标 `main`，状态 OPEN / MERGEABLE；未合并、未打 tag。标题与说明已更新为 R-269 定稿内容。

## 待审核

- 版本说明写“12 个样例文件扫描约 90–100 秒、问答一般 10–30 秒”，取五次真实模型运行的中位区间；实测最慢一次扫描 118.1 秒、单题最慢 50.9 秒，已由两条“会更久”的限制覆盖，若希望正文直接写区间上限，请示意，我再改。
- 安装包内的 `docs/使用说明.txt` 逐字节同步（B1），本次只改文档，未重新生成安装包；发布前仍需按正常流程重打包。
- 版本说明开头仍写“相对 2.3.9 的变化”，沿用 R-265 原句，未改版本基线。

执行者进度：R-269，PR #16；push CI 编号 39363686644 结果 GREEN、PR CI 编号 39363690702 结果 GREEN（push 单测 39363686719、PR 单测 39363690698 均 GREEN，push 侧等价 dispatch 39363694209 GREEN）；改的是两份发布文档，未跑真实模型测试（本轮无后端改动）；CI 均为第一次运行即 GREEN；停下原因：无。
