# DONE-R-263a：真实模型连通性测试

## 执行结果

- 分支：`codex/R-261`
- 手动 workflow run：`39235263452`，run number `5`，结论 `failure`。
- Workflow run URL：<https://github.com/riser-lang/paw-local-dev/actions/runs/39235263452>
- 报告 artifact：`real-model-report`（artifact ID `12179676814`），其中包含 `model-ping-report.json` 和 `real-model-report.json`。
- 真实模型 ID：`deepseek-ai/DeepSeek-V4.1-Flash`。
- `model-ping.yml` 曾在前一提交中新增，之后由新提交 `d94781c` 删除；没有改写历史。探测已加入已有的 `.github/workflows/real-model.yml`。

## 四次连通性请求

四个请求均返回 HTTP 200，均未超时（单次超时上限 120 秒）。

| 请求 | 状态码 | 耗时（秒） | 超时 | 回答前 100 字 | prompt tokens | completion tokens | reasoning tokens | 附加结果 |
|---|---:|---:|---|---|---:|---:|---:|---|
| GET `/models` | 200 | 0.869 | 否 | N/A | N/A | N/A | N/A | 清单包含目标模型 |
| POST `/chat/completions`，默认参数，提示“只回复：连通正常” | 200 | 1.924 | 否 | `连通正常` | 35 | 32 | 30 | — |
| POST `/chat/completions`，`enable_thinking=false`，相同提示 | 200 | 1.354 | 否 | `连通正常` | 9 | 2 | 0 | — |
| POST `/chat/completions`，`enable_thinking=false`，约 2000 字虚构中文样例，要求输出 JSON 数组 | 200 | 1.572 | 否 | `[{"place":"雾杉湾","activity":"潮汐观测"},{"place":"白石岭","activity":"种子交换"}]` | 1386 | 26 | 0 | 返回 2 项 JSON 数组 |

探测脚本只从 `PAW_TEST_API_KEY` 环境变量读取 Key；未输出请求头或 Key，报告和 artifact 中不含 Key。`Model ping` 步骤设置了 `continue-on-error: true`，因此探测失败也会继续完整验收。此次四项探测均成功。

## 完整真实模型验收

验收步骤按预期因扫描超时失败。报告状态为 `RED`，问题原因为 `TimeoutError`；扫描在 240 秒等待窗口内未完成，进度为 3/4，当前文件是 `青禾试点计划_v1.docx`，最近错误为 `none`。最后进度原文：

`Scan progress: status=phase2_analyzing completed=3/4 current_file=青禾试点计划_v1.docx recent_error=none`

各文件耗时如下：

| 阶段 | 文件 | 耗时（秒） | 结果 |
|---|---|---:|---|
| Phase 1 本地导入 | 青禾试点周报.pptx | 0.8 | 完成 |
| Phase 1 本地导入 | 青禾试点计划_v1.docx | 0.6 | 完成 |
| Phase 1 本地导入 | 青禾试点计划_v2.docx | 0.7 | 完成 |
| Phase 1 本地导入 | 青禾试点详情.md | 0.7 | 完成 |
| Phase 2 模型分析 | 青禾试点详情.md | 79.1 | 完成 |
| Phase 2 模型分析 | 青禾试点周报.pptx | 140.8 | 完成 |
| Phase 2 模型分析 | 青禾试点计划_v2.docx | 16.8 | 完成 |
| Phase 2 模型分析 | 青禾试点计划_v1.docx | 0.4 | 超时点当前文件；进度仍为 3/4，不能据此视为完成 |

八道验收题均未能运行；回答为空、来源为空，通过数 `0/8`。本次失败是扫描超时，不是连通性请求失败。

## CI 和 PR

本次功能提交为 `d94781cf322fe42fc999316216608b7aeddd1fd1`。push 整体验证 `39235261951` 和 PR 整体验证 `39235268892` 均 GREEN；push 单测 `39235261956`、PR 单测 `39235268898`、PR 安装包构建 `39235268873` 也均 GREEN。

此前由中间提交 `bb75130` 和 `9a1f503` 触发的过渡版整体验证（`39234969044`、`39234975388`、`39235039198`、`39235047787`）在最终提交验证前取消；最终功能提交的 push 和 PR 整体验证均完整运行并 GREEN。

PR #15 仍为 OPEN，未合并；没有打 tag。
