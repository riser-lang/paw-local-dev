# TASK-G-07：合入 main → 打 tag → 准备参赛提交包

- 下达：Claude Code ｜ 2026-10-15 ｜ 依据：REVIEW-08
- **代码冻结：10-15 22:00。** 本任务第 1–3 步必须在冻结前完成。提交包截止 10-16 12:00。
- 任何一步不是 GREEN：**立即停下**写报告，不得修改代码、不得反复重跑碰运气。

## 1. PR #3 最终全量运行
PR #3（claude/p0-project-update → main）头部 9843a2f 上的 test.yml 运行必须 GREEN（28/28 PASS，3 个负对照按预期 FAIL）。如果没有自动触发，用 workflow_dispatch 手动触发。

## 2. 合入 main（审核人预先授权）
第 1 步 GREEN 后执行 `gh pr merge 3 --repo riser-lang/paw-local --merge`（普通合并）。之后 main 上的 push 运行也必须 GREEN。

## 3. 打 tag（审核人预先授权）
在第 2 步 GREEN 的那个 main 提交上执行 `git tag -a v1.0.0-rc1 -m "PAW 2.3.9 contest release candidate 1"`，然后 `git push origin v1.0.0-rc1`。tag 触发的运行也必须 GREEN。**提交包只能用这次 tag 运行的产物。**

## 4. 准备提交包：`D:\PAW\参赛提交\`（项目文件夹内，不进仓库）
从 tag 运行下载产物，放入以下文件：
- `PAW_Personal_Work_OS_Setup_v2.3.9.exe`：tag 运行产物原样放入，不得重新打包。
- `SHA256.txt`：EXE 的 SHA256。本地用 `Get-FileHash` 重新计算一遍，必须与 tag 运行日志里的值一致。
- `使用说明.txt`：从 tag 提交里的 docs/使用说明.txt 原样复制。
- `版本说明.txt`（新写，一页以内，大白话，UTF-8 带 BOM）：版本 2.3.9；相对 2.3.8 修复了什么（AI 建议核准失败、项目展示、30 秒快速了解等）；已知问题和规避方法（Windows 会出现蓝色安全提示；不填 Key 时只能用离线规则；联网搜索会发出搜索词）。
- 不放任何测试报告、日志、截图，也不放 2.3.7、2.3.8 的安装包。

## 5. 稳定性统计
列出 PR #1 合并以来所有使用 paw_verdict 判定的运行中，**出现过 FAIL 的必测用例**（包括本次的 B4-03 及其原因），以及有没有"一次失败、重跑后通过"的情况。

## 补充：B 方案（见 REVIEW-09；仅在产品负责人选 B，或 10-16 08:00 时 A 仍未解决才执行）
1. 执行 `gh pr merge 3 --repo riser-lang/paw-local --merge`。
2. 在合并后的 main 提交上打 tag v1.0.0-rc1 并推送（tag 运行会因账单问题失败，属于预期，如实记录即可）。
3. 用 `git diff --name-only f04d489 v1.0.0-rc1` 证明代码相同，**只允许出现 _handoff/ 下的文件**，否则立即停下。
4. 从 Run 38598860995 下载 built-installer-exe，与该运行日志中打印的 SHA256 核对一致后，按第 4 步准备提交包。
5. 在 DONE 报告里写明本次走的是 B 方案，并附上第 3 步的输出。

## 交付（DONE-G-07.md）
第 1–3 步的三个运行链接、对应提交 hash、各自的 summary.md 最后三行；提交包文件清单（文件名、大小、SHA256）；第 5 步的统计。写完停步。
