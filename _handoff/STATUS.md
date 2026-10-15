# PAW 本地版 · 开发状态板（公开仓库 riser-lang/paw-local-dev）

- 更新：2026-10-15（Claude Code）
- 执行者：Codex ｜ 审核人：Claude Code ｜ 产品负责人：用户
- 起点：参赛版 2.3.9 的清理快照（私有仓库 tag v1.0.0-rc1，代码相同）。清理内容：一个测试网页样例中的服务器 IP 改为文档专用地址 203.0.113.10；测试流水线的触发分支改为 main / codex/** / claude/**；判定脚本支持审核人批准的新用例文件 required_cases_*.json。

## 已定决定
- 评委 / 用户自带 API Key；没有 Key 时不编造内容。
- 用户规则 R1–R5（2026-10-15 设定）见 specs 文档 4.4。
- 本地版改编和路线图见 CODEX_START_HERE.md 第 4、9 节。
- 历史审核记录（REVIEW-01 至 REVIEW-10）保留在本目录，说明测试判定规则为什么这样定。

## 参赛目标（2026-10-15 产品负责人决定）
提交做完全部功能的版本（C-01 至 C-10 → v2.5.0），可以推迟两三天；2.3.9 一直保留作为保底，新版没有通过全部验收就不替换。

## 审核人预定的验收用例（git blob id，激活时审核人核对，内容不得改动）
| 文件 | blob |
|---|---|
| tests/pending/required_cases_C-02.json | 05984c38b34abbf566b0e46095a46dc9ff546755 |
| tests/pending/required_cases_C-03.json | 6e1d9c4ab9825ba17fb876ee3080adde2c222936 |
| tests/pending/required_cases_C-04.json | c4bd8dee04b9267ae514a5c36722e60ad6718619 |
| tests/pending/required_cases_C-05.json | 9804f9d0fc22ac58f10f283b5fc4207b4d6a8792 |
| tests/pending/required_cases_C-06.json | 18c112f2c92c7d28533b9653ccea06135e3e1fb5 |
| tests/pending/required_cases_C-07.json | 1f23fb53054628b0858b0cd0e38c65700a688ce2 |
| tests/pending/required_cases_C-08_10.json | 8ff20a22c015cb7429872f9006ac76289e6bb62c |

## 看板
| 任务 | 内容 | 状态 |
|---|---|---|
| C-01 | 调研 + 测试底座 + 设计变量 | 实现与首次运行完成，PR #1 待审核；unit GREEN、E2E 28/28 GREEN、独立一致性检查 RED；发现既有治理旁路，按红线停下，详见 DONE-C-01.md |
| C-02 至 C-04 | 档案数据层、版本裁定、栏目更新与冲突、覆盖率（第 1 天，完成后停点） | 已规划，用例已定 |
| C-05 至 C-07 | 自动归属与回执、前端驾驶舱与项目档案、本地运维 → v2.4.0-rc（第 2 天，停点） | 已规划，用例已定 |
| C-08 至 C-10 | 建议、健康检查、周报、指标、考题 → v2.5.0-rc（第 3 天，停点）→ 产品负责人验收 → 替换参赛提交 | 已规划，用例已定 |
