# TASK-G-01：建私有仓库并推送 2.3.7 底包；在 PR 中还原 2.3.8；最小云端打包

- 下达：Claude Code ｜ 2026-10-13 ｜ 依据：REVIEW-02

## 第 1 步：建私有仓库并推送（已批准）
```
cd D:\PAW\paw-beta
git add _handoff
git commit -m "docs: handoff review records"
git branch -M main
gh repo create riser-lang/paw-local --private --source . --remote origin
gh repo view riser-lang/paw-local --json visibility    # 必须输出 PRIVATE，否则立即停止，不得推送
git push -u origin main
```
禁止：--public、force push、推送 scratch/ 或任何被忽略的文件。

## 第 2 步：分支 gemini/G-01 还原 2.3.8 源码
原则：**只搬运 2.3.8 安装包里已经存在的改动，不做任何新修复、不"顺手优化"。**
1. `backend/repair_v238.py`：从 scratch\v238_diff\v238\{app}\_internal\backend\repair_v238.py 原样复制。
2. `backend/runtime/core/paw_luna_draft_store.py`：用 2.3.8 安装包里的 .py 原样替换。
3. `backend/main.py`：在文件末尾补上 2.3.8 新增的那几行（导入并执行 repair_v238.install、版本号 2.3.8），以 main.pyc 反汇编为准。
4. `run.py`：按 run.pyc 反汇编补上 PAW_NO_BROWSER=1 时不打开浏览器的逻辑。
5. `frontend/dist/`：用 2.3.8 安装包里的 index.html、assets/index-qIwUsXqw.js、paw_console.js 原样替换。
6. `installer/paw_setup.iss`、启动 bat、使用说明：只改版本号、x64 检查、中文快捷方式文案，与 2.3.8 一致。
7. 确认 `paw_desktop.spec` 会把 repair_v238 打进包（PyInstaller 能否自动发现它；不能就按 2.3.8 的做法加入）。
8. 全仓库搜索其他仍写着 2.3.7 的版本号，列表报告，改成 2.3.8。

## 第 3 步：最小云端打包流程（.github/workflows/build.yml）
- 运行在 GitHub 的 windows-latest 上；任何 PR 和 main 推送都会触发。
- 从源码安装依赖 → PyInstaller → Inno Setup → 上传安装包 EXE 作为产物；在日志中打印 EXE 大小和 SHA256。
- EXE 小于 20MB 时，流程判为失败。
- 本步骤不涉及 API Key，也不涉及 agy。

## 第 4 步：还原一致性证明（同一 job 或单独 job，在云端做）
用云端刚打出的 EXE 解包，与 2.3.8 原安装包比对：
- 前端 3 个文件：哈希必须完全一致。
- repair_v238、main、run、paw_luna_draft_store 四个模块：比较去掉文件名和行号后的反汇编（`dis`）结果，必须一致。
- 我们自己的代码中，不得多出、也不得缺少模块（第三方库只报告差异，不判失败）。
- 2.3.8 原安装包不进仓库。比对时，在 workflow 里从仓库的 Release 附件下载，或由产品负责人手动上传为私有 Release 附件。两种做法选一种，在报告里写明。

## 完成标准与证据（DONE-G-01.md）
- 仓库可见性的输出必须是 PRIVATE；附上 main 的提交 hash。
- PR 链接；云端运行链接；EXE 大小和 SHA256。
- 还原一致性比对表，每一行写"一致"或"不一致"。
- 第 2 步第 8 条的版本号清单。
- 写完停步，**不要自行合并 PR**，等待审核。
