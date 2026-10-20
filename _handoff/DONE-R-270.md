# R-270 完成报告

## 任务结果

| 要求 | 结果与证据 |
|---|---|
| 从最新 origin/main 开新分支 | 完成。`git fetch origin --prune` 后从 `origin/main` = `d6e059a`（PR #16 / R-269 已合并）创建 `deepseek/R-270`，未复用旧分支，未 force push。 |
| 只改与成品不一致处 | 完成。三份文档共 31 行替换、31 行新增，全部是标签/步骤/文件名/文案的事实错误，未改其他内容。 |
| 保持原编码 | 完成。三份文件原本都是 UTF-8 **无 BOM** + CRLF；改后仍无 BOM，行尾仍全为 CRLF（安装说明 43 行、新手 56 行、清单 116 行，裸 LF 均为 0），行数与改动前一致。 |
| CI 首次运行即 GREEN | **push 侧达成**（首次运行 GREEN）；**PR 侧未达成**：第 1、2 次运行都是 Reviewer Verdict Gate 判 RED（全部 91 个用例 PASS，仅"截图重复使用"一条问题），整条流水线重跑第 3 次 GREEN。原因见下节。 |
| 边界 | 未改代码、审核人文件、`test.yml`、题目与判定；未删除文件；未改文件夹权限或系统设置；Key 未出现在任何位置；未合并、未打 tag。 |

## 改正清单（按 R-269 已核对的成品实际）

### docs/安装说明.txt（9 处）
| 原文 | 改为 | 依据 |
|---|---|---|
| 标题 `v2.3` | `v2.5.0` | `installer/paw_setup.iss` 的 `MyAppVersion 2.5.0` |
| `PAW Setup.exe`（安装、升级两处） | `PAW_Personal_Work_OS_Setup_v2.5.0.exe` | `.iss` 的 `OutputBaseFilename` |
| 屏幕右下角自动弹出「PAW 控制中心 - 新手引导」 | 首次启动自动打开【PAW 本地控制中心】的「引导」页 | `paw_console.js`：`fetch('/v1/local/onboarding')` 未完成时 `overlay.style.display="flex"`，标签为「引导」 |
| 步骤二：选择 AI 推理服务（推荐 DeepSeek 或阿里云通义千问…） | 步骤二：选择 AI 模型服务（DeepSeek、通义千问 Qwen、OpenAI、自定义 OpenAI 兼容接口、纯离线规则推理） | 引导内 `<option>` 实际文案 |
| 步骤三：选择扫描限流模式 | 步骤三：选择资源限流模式 | 引导内标签「3. 资源限流模式」 |
| 工作台的「审核」／控制中心的「智能批量审核」 | 【待我审核】／「批量审核」标签页 | 侧栏与 `data-tab="batch_review"` |
| 右下角控制中心的「💬 AI 助手 (Agy)」标签页，每条回答附带文件证据定位卡片 | 左侧【AI助手】；来源在回答末尾「来源：」行，引用的知识条目写出原始文件名 | 控制中心无 AI 助手标签（仅 系统与模型／文件夹扫描／批量审核／巡航与快照／知识演进／系统状态）；R-269 已核实来源显示方式 |
| 「⚙️ 系统与模型」…一键创建快照与一键无损灾难恢复 | 「系统与模型」页面下方「数据库自动备份与灾难恢复」、「+ 立即创建安全快照」、备份清单里的「恢复」 | `paw_console.js` 该区标题与按钮文案 |
| 退出软件：关闭后台黑窗口或任务管理器结束任务 | 关闭浏览器不会退出程序；需在任务管理器结束 `PAW_Personal_Work_OS.exe` | `paw_desktop.spec` 为 `console=False`，安装包无控制台窗口；`.iss` 的 `MyAppExeName` |
| 卸载提示「是否同时删除本地用户数据与知识库？」 | 「是否同时删除 PAW 工作数据？」，默认按钮「否」 | `.iss` 的 `MsgBox` 原文与 `MB_DEFBUTTON2` |

### docs/新手使用说明.txt（11 处）
「审核中心」→【待我审核】；「文件夹扫描与进度」→「文件夹扫描」标签页；「暂停」/「停止」→「暂停扫描」/「终止扫描」；「智能批量审核」→「批量审核」；「批量通过」→「批量核准入库」；右下角「⚙️ PAW 控制中心」→左下角【控制中心】；控制中心的「💬 AI 助手 (Agy)」标签页→左侧【AI助手】；回答底部「段落定位卡片」→末尾「来源：」行且引用知识条目写出原始文件名；「⚙️ 系统与模型」→「系统与模型」并补「数据库自动备份与灾难恢复」；「一键恢复」→备份清单里的「恢复」；「跳过清单」→「跳过与解析异常清单」。

### docs/交给委托人的手动测试清单.md（8 处）
安装包名 `v2.3.2`→`v2.5.0`（两处）；「工作台右下角自动弹出的新手引导对话框」→自动打开的【PAW 本地控制中心】「引导」页；模型模板补全为成品选项；导航「今日」→「今天」、「知识库」→「知识」，并注明点【项目】会直接打开当前项目档案；「AI助手」标签页或右下角悬浮球→左侧【AI助手】；「回答下方…证据编号、来源文件与定位段落」→末尾「来源：」行（目前没有单独来源面板）；备份区所在标签页「巡航与快照」→「系统与模型」页面下方「数据库自动备份与灾难恢复」。

未改的近似项（非界面标签，故按"只改不一致处"保留）：「智能批量审核」「Agy」等出现在小节标题里的产品代号与描述性标题；「审核中心」「知识库」作为通称出现在正文行文中的位置。

## CI 与 PR

- push 侧完整流水线（`gh workflow run test.yml --ref deepseek/R-270`）：[run 39377621552](https://github.com/riser-lang/paw-local-dev/actions/runs/39377621552) — **GREEN**，22 个作业全部成功，首次运行即通过。
- push 侧单测（`unit.yml`，随 push 自动触发）：[run 39377618069](https://github.com/riser-lang/paw-local-dev/actions/runs/39377618069) — **GREEN**。
- PR 侧单测：[run 39377651103](https://github.com/riser-lang/paw-local-dev/actions/runs/39377651103) — **GREEN**。
- PR 侧安装包构建：[run 39377651529](https://github.com/riser-lang/paw-local-dev/actions/runs/39377651529) — **GREEN**。
- PR 侧完整流水线：[run 39377651118](https://github.com/riser-lang/paw-local-dev/actions/runs/39377651118) — attempt 1 **RED**、attempt 2 **RED**、attempt 3 **GREEN**（22 个作业全部成功）。

### PR 侧两次 RED 的原因（与本次改动无关）

两次 RED 都只有 `Reviewer Verdict Gate` 失败，汇总里 91 个必测用例全部 PASS、0 FAIL，问题只有一类：

- attempt 1：`screenshot 513c70911f3c reused 2 times`、`screenshot 82d3540fb814 reused 2 times`
- attempt 2：`screenshot 4989e7a3ba7d reused 2 times`、`screenshot bf956e4d40b8 reused 2 times`

取证（已下载 attempt 2 与 push 侧产物对比）：

- 两对重复文件都是 `screens-PM08/screen_PM_E1_02_{1440,390}.png` 与 `screen_PM_E1_03_{1440,390}.png`。
- `tests/run_pm_advice_tests.py` 里 `PM-E1-02` 与 `PM-E1-03` 用同一份种子（同一项目、同一份 `研制方案_v1.docx`、同一段文本、同一个 mock 模型），差别只在数据库层的动作（一个走采纳链、一个造 schema 拒绝），两种页面因此可能渲染成完全相同的整页截图。
- 对比 push 侧那一对不重复的截图，像素差只落在 26 条 9–13 像素高的窄带（页内"档案更新于/数据生成于/生成于"等按分钟显示的时间行与少量计数行）。也就是说：两个用例的截图落在同一分钟时字节完全相同，跨分钟时不同 —— 这解释了同一份代码 push 侧 GREEN、PR 侧时红时绿。
- 该判定与"截图不得复用"的防伪规则属审核人所有（`tests/paw_verdict.py`），本轮未改；按"失败只重跑整条流水线"的既有规则处理，第 3 次整条流水线 GREEN。

## 其他检查

- `git diff --check`：通过。
- `python tests/paw_lint.py`：lint clean。
- `python -m pytest tests/unit -q`：227 passed。
- PR：[#20](https://github.com/riser-lang/paw-local-dev/pull/20)，目标 `main`，状态 OPEN / MERGEABLE；未合并、未打 tag。

## 待审核

- PR 侧完整流水线不是一次通过（第 3 次才 GREEN），原因如上，属既有判定器的截图复用规则撞上两个同页面用例的时间戳粒度；是否允许在判定器侧放宽"同页面用例允许同图"由审核人决定，执行者未改判定器。
- 三份文档只按 R-269 已核对过的成品事实改正；若还有本轮未覆盖的界面差异，请指出具体行，我按同一方式补正。

执行者进度：R-270，PR #20；push CI 编号 39377621552 结果 GREEN、PR CI 编号 39377651118 结果 GREEN（attempt 3；attempt 1、2 因判定器"截图重复使用"判 RED，91 个必测用例全 PASS；push 单测 39377618069、PR 单测 39377651103 均 GREEN）；改的是三份文档、无后端改动故未跑真实模型测试；停下原因：无。
