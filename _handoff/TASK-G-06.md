# TASK-G-06：版本号 2.3.9 + 使用说明定稿 + 发布触发

- 下达：Claude Code ｜ 2026-10-14 ｜ 依据：REVIEW-07
- **截止：10-15 12:00。** 冻结时间 10-15 22:00。
- 分支：从最新的 `origin/claude/p0-project-update` 拉出 `gemini/G-06`，开 PR 合入 `claude/p0-project-update`。禁止 rebase 和 force push。四个判定规则文件不得修改。

## 1. 版本号改为 2.3.9
参赛版包含了 2.3.8 之后的两个 P0 修复，要和微信里流传的 2.3.8 区分开。
- 所有写着 2.3.8 的**版本号**改为 2.3.9：iss、main.py 里的 `app.version`、run.py 的标题、产品指南的 GUIDE_VERSION、audit_check.py 的版本检查、启动 bat、使用说明，以及前端 index.html 的 `?v=` 参数。
- **不要改** `repair_v238.py` 的文件名和模块名。
- 一致性比对脚本（verify_consistency.py）是拿来和原 2.3.8 安装包比对的。版本号字符串变了，比对会出现差异。如果需要调整这个脚本，只允许把"版本号字符串"加入忽略列表，并在 DONE 报告里贴出改动的 diff。
- 最终 EXE 文件名固定为 `PAW_Personal_Work_OS_Setup_v2.3.9.exe`（不能带空格），在 build 步骤里重命名或修改 iss 都可以。
- 全仓库搜索 2.3.8，列出剩余位置并说明为什么保留。

## 2. 使用说明定稿
按 REVIEW-07 的 8 条逐条修改 docs/使用说明.txt。出处表要补全，每个界面词都要有出处。

## 3. 发布触发
test.yml 和 build.yml 增加 `push: tags: ['v*']` 触发。打 tag 时必须完整跑一遍全部板块 + 判定门禁，并上传 EXE 和 SHA256 文本文件作为产物。

## 交付（DONE-G-06.md）
PR 链接；PR 头部提交的全量运行链接和 summary.md 全文（必须 GREEN）；产物里的 EXE 文件名、大小、SHA256；剩余 2.3.8 位置清单；使用说明的修改对照（原句 → 新句）。写完停步。
