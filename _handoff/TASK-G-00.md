# TASK-G-00：推送前清理 + v2.3.8 差异盘点

- 下达：Claude Code ｜ 2026-10-13 ｜ 依据：REVIEW-01
- **本任务全程禁止推送、禁止建远程仓库。** 完成后写 DONE-G-00.md 并停步。

## A 部分：取消跟踪测试遗留文件

目标：让 install.log、fix_run/、verification_logs/ 不再进入 git，但**本地文件保留不删**。

允许执行（在 D:\PAW\paw-beta）：
```
git rm -r --cached install.log fix_run verification_logs
git add .gitignore template/workspace_template.db _handoff
git commit -m "chore: untrack local test logs; add clean DB template and handoff docs"
```
禁止：删除任何磁盘文件；`git filter-branch` / `filter-repo` / 改写历史；修改 .gitignore 以外的配置；推送。

完成标准（三条命令的原样输出贴进 DONE-G-00.md）：
1. `git ls-files -ci --exclude-standard` → 输出为空
2. `git ls-files | Select-String -Pattern '\.log$|^fix_run/|^verification_logs/|^data/|\.exe$|\.png$'` → 输出为空
3. `Test-Path install.log, fix_run, verification_logs` → 三个都是 True（证明本地文件没删）

然后重新生成 PUSH_MANIFEST.md（以 `git ls-files` 为准，不要自己估算），SECRETS_SCAN.md 补一句"已覆盖全部 git 历史，见 REVIEW-01"。

## B 部分：v2.3.8 与 v2.3.7 安装包差异盘点（只读）

目标：弄清 v2.3.8 改了什么，供审核人决定"补回源码"还是"放弃改动"。

- 输入：桌面上的 `PAW_Personal_Work_OS_Setup_v2.3.7.exe`（SHA256 4ED4952E…5921）和 `PAW_Personal_Work_OS_Setup_v2.3.8.exe`（SHA256 3BEA84DD…5A0F4）。先核对哈希，不符就停。
- 工作目录：只能用 `D:\PAW\paw-beta\scratch\v238_diff\`（已被 .gitignore 排除），两个安装包各解到独立空文件夹。
- **禁止运行这两个安装包**，只解包（innoextract 或 innounp 解安装包，pyinstxtractor 解内部 exe）。
- 比对三层并列表：
  1. 安装包层：安装脚本差异（版本号、描述、安装目录、快捷方式、新增/删除文件）。
  2. 前端：`frontend/dist` 每个文件的哈希对比；不同的话，与仓库里 `frontend/dist` 再对比一次。
  3. 后端：PyInstaller 包内每个模块的 .pyc 哈希对比，列出**新增、删除、改变**的模块名（只列我们自己的代码：backend.* / run 等，第三方库只报"依赖版本变化"）。
- 不要求反编译。若某个改变的模块很小，可附 `python -m dis` 摘要里出现的新字符串常量（最多每模块 20 行），帮助判断改了什么功能。

## 必须提交的证据（DONE-G-00.md）

- A 部分三条命令原样输出 + 新提交的 hash
- B 部分：差异总表（文件/模块名 ｜ 新增/删除/改变 ｜ 大小变化），以及一句话判断"改动集中在哪些功能"
- 用到的解包工具名称、版本、下载来源
