# REVIEW-01：推送前审核（阶段 0）

- 审核人：Claude Code ｜ 时间：2026-10-13 ｜ 审核对象：paw-beta 本地仓库（25 个提交）+ PROJECT_MAP / SECRETS_SCAN / PUSH_MANIFEST / .gitignore
- **结论：打回（暂不批准推送）**。问题小、范围明确，按 TASK-G-00 修完后由我复核，通过即批准推送。

## 审核人亲自核实的事实

| 检查项 | 结果 |
|---|---|
| 全部 25 个历史提交里的密钥特征（sk-、ghp_、AKIA、私钥头、JWT） | 仅 4 个假测试值（sk-test-…-8888 等），**无真实密钥** |
| 历史中 config.json 的 key 字段 | 始终为空字符串 |
| 历史中 >2MB 的文件 | 无 |
| 历史中是否出现过 .db/.exe/.zip/.pfx/.png/data/ | 从未出现 |
| template/workspace_template.db | 35 张表，**全部 0 行**，干净 |
| 签名脚本（_run_sign.ps1、sign_with_sunao13.py） | 只含证书指纹（公开信息），无 pfx、无密码 |

## 打回原因

0. **【阻断·最重要】源码版本落后于最新安装包。** 本仓库最新提交是 v2.3.7（a14ce37）；产品负责人告知 v2.3.8 是在另一台电脑上升级的，这里只有安装包、没有源码。我已核实桌面 `PAW_Personal_Work_OS_Setup_v2.3.8.exe`：26,887,584 字节，SHA256 `3BEA84DD…5A0F4`，文件版本 2.3.8.0，安装向导描述也与 2.3.7 不同（说明安装脚本也改过）。
   云端流水线只能从源码打包，**如果直接用现有源码，评委拿到的会是 v2.3.7，v2.3.8 的改动会丢失。** 处理顺序：① 产品负责人先确认另一台电脑上是否还有源码文件夹（首选）；② 同时由 Gemini 拆包比对，列出 v2.3.8 相对 v2.3.7 到底改了哪些文件（TASK-G-00 B 部分），用于决定"补回"还是"放弃 2.3.8 改动"。
   现有 v2.3.7 源码可以作为第一个基线推送，但**在 2.3.8 问题有结论前，不得开始搭建打包流水线。**
1. **【阻断】清单与事实不符：已被 git 跟踪的文件不受 .gitignore 约束。**
   SECRETS_SCAN 2.3 写"*.log、verification_logs/、fix_run/ 已排除"，但实际以下 17 个文件早已被 git 跟踪，照样会被推送：
   `install.log`、`fix_run/` 下 9 个文件、`verification_logs/` 下 7 个日志。PUSH_MANIFEST 的 top-10 里也列出了 install.log，自相矛盾。
   这些日志含本机用户目录路径（C:\Users\…），属于个人信息，且是测试遗留物，不该进入参赛仓库。
2. **【需改】SECRETS_SCAN 只扫描了当前文件，没有扫描 git 历史。** 本次我已代为扫描历史，结果干净；以后的扫描报告必须写明是否覆盖历史。
3. （已撤回）D:\PAW\_handoff 与 D:\PAW\paw-beta\_handoff 经核实是同一个文件夹（链接），不是两份，无需处理。

## 可接受、无需处理

- 历史提交中这些日志的旧版本含本机用户名路径：仓库为私有，不重写历史（重写历史需要 force push，违反红线）。
- backend/runtime/reader-runtime/*.json 中含 /home/ubuntu/... 的服务器路径字符串：无凭证，不影响。
- 文档中的 sunao13 签名证书指纹：公开信息。

## 对 PROJECT_MAP 的补充（供流水线清空步骤使用，不阻断推送）

清空证据除 PROJECT_MAP 列出的位置外，**还必须检查**：`%APPDATA%\PAW*`、`%ProgramData%\PAW*`、`HKCU\Software\PAW*`、Windows 凭据管理器中含 PAW 的条目、`%TEMP%` 下 PAW 临时文件，以及 data_dir_pointer.txt 指向的外部目录。即使预期为空，也要在报告中列出"已检查、为空"。
