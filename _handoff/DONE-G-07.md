# DONE-G-07：合入 main、打 tag 与准备参赛提交包 交付报告

- 交付人：Gemini
- 交付时间：2026-10-15
- 依据任务：[_handoff/TASK-G-07.md](file:///D:/PAW/paw-beta/_handoff/TASK-G-07.md) ｜ 审核反馈：[_handoff/REVIEW-09.md](file:///D:/PAW/paw-beta/_handoff/REVIEW-09.md)
- **方案执行：B 方案（产品负责人选择 B 方案，云端账单限制下的无风险发布流程）**

---

## 1. 方案说明与执行结论

由于 GitHub Actions 组织账户 `riser-lang` 遭遇账单限额拦截（`recent account payments have failed or your spending limit needs to be increased`），导致云端 Runner 无法继续派发执行。
依据产品负责人决议与 [REVIEW-09](file:///D:/PAW/paw-beta/_handoff/REVIEW-09.md) / [TASK-G-07](file:///D:/PAW/paw-beta/_handoff/TASK-G-07.md) 补充规定，本次全面执行 **B 方案**：
1. 普通合并 PR #3 至 `main` 分支；
2. 在合并后的 `main` 提交上创建并推送正式发布标签 `v1.0.0-rc1`；
3. 执行 `git diff --name-only f04d489 v1.0.0-rc1` 严格验证代码树完全一致（仅允许 `_handoff/` 文档差异）；
4. 从已取得 `VERDICT: GREEN` 门禁判定的 Run 38598860995 下载构建产物 `built-installer-exe`；
5. 核验 SHA256 一致后，在 `D:\PAW\参赛提交\` 严格归档最终提交物；
6. 整理全周期测试用例稳定性统计并交付。

---

## 2. 代码一致性严格核验（第 3 步输出）

执行对比命令：
```bash
git diff --name-only f04d489 v1.0.0-rc1
```

**实际完整输出：**
```text
_handoff/DONE-G-06.md
_handoff/STATUS.md
```

**核验结论：**
输出内容严格且仅包含 `_handoff/` 目录下的交接文档。`v1.0.0-rc1` 标签所对应的所有产品源码、前端产物、打包配置、依赖及测试规则，与此前通过 28/28 必测项全量验证的提交 `f04d489` **100% 逐字节完全相同**。

---

## 3. 工作流运行记录与提交 Hash

### 第 1 步：PR #3 全量触发
- **PR 链接**：https://github.com/riser-lang/paw-local/pull/3
- **分支头部提交**：`9843a2f` (Merge PR #5 into claude/p0-project-update)
- **运行链接**：https://github.com/riser-lang/paw-local/actions/runs/38658165486
- **运行状态**：因 GitHub Actions 账户账单限制被系统拦截（未分配 Runner，无 summary.md 产物）

### 第 2 步：合并 PR #3 入 main
- **合并方式**：`gh pr merge 3 --repo riser-lang/paw-local --merge`（普通合并，非 force）
- **合并提交 (main HEAD)**：`7de5aed`
- **运行链接**：https://github.com/riser-lang/paw-local/actions/runs/38659409900
- **运行状态**：因 GitHub Actions 账户账单限制被系统拦截（未分配 Runner，无 summary.md 产物）

### 第 3 步：打 tag v1.0.0-rc1 并推送
- **标签信息**：`v1.0.0-rc1`（指向提交 `7de5aed`，附注："PAW 2.3.9 contest release candidate 1"）
- **触发运行链接**：
  - test.yml: https://github.com/riser-lang/paw-local/actions/runs/38659522438
  - build.yml: https://github.com/riser-lang/paw-local/actions/runs/38659522431
- **运行状态**：因 GitHub Actions 账户账单限制被系统拦截（未分配 Runner，无 summary.md 产物）

### 基准全绿验证运行（安装包产物来源）
- **运行链接**：https://github.com/riser-lang/paw-local/actions/runs/38598860995
- **对应提交**：`f04d489`
- **判定结果**：**`VERDICT: GREEN`**（28/28 PASS，3/3 负对照符合预期 FAIL）
- **summary.md 最后三行原文**：
```markdown
## No problems

VERDICT: GREEN
```

---

## 4. 参赛提交包清单（`D:\PAW\参赛提交\`）

提交包存放于仓库外部的独立目录 `D:\PAW\参赛提交\` 中，不纳入 git 版本管理。所有文件信息如下：

| 文件名 | 大小 (字节) | SHA256 校验和 | 来源与规范说明 |
|---|---|---|---|
| `PAW_Personal_Work_OS_Setup_v2.3.9.exe` | 26,420,358 (26.4 MB) | `7CC5C7284998C8FF82824A4147672123927998E74D23E47EA9B50BFC46A7A771` | Run 38598860995 构建产物 `built-installer-exe` 原样放入，未做二次打包 |
| `SHA256.txt` | 105 | `7FD529720099F5CE6D362D7A3A01014EF68ABE3D80E5B662770E0CE5E227E72D` | 本地执行 `Get-FileHash` 计算重算，内容与云端运行日志打印值严格一致 |
| `使用说明.txt` | 8,126 | `2AFBDA81F966548CB160A39D65F14B312E10942EA2C7E103C8258823BBDA2846` | 从标签提交 `docs/使用说明.txt` 原样复制，UTF-8 带 BOM |
| `版本说明.txt` | 3,244 | `AF7B016B0002BD7B9BD361E910E6109CB7553CEB82E1BD557BB7653A4C92CDAA` | 全新编写，一页以内大白话，UTF-8 带 BOM，详述修复项与规避方法 |

### 纯净度检查确认
- 目录内**仅且仅有**上述 4 个文件。
- 未放置任何测试报告、测试代码、运行日志、截图等开发过程产物。
- 未混入历史版本（v2.3.7、v2.3.8）的任何安装包。

---

## 5. 全周期用例稳定性统计（第 5 步）

针对 PR #1 合并以来，所有通过 `paw_verdict` 与 `required_cases.json` 门禁规则进行判定的测试流水线运行记录：

1. **唯一出现过 FAIL 的必测用例**：
   - **用例编号**：`B4-03`（候选批量核准持久化）
   - **失败根因**：确认为真实的产品代码缺陷（P0 级问题）。旧逻辑中全量扫描流程提交的项目更新携带了外壳封装且内部 payload 为空，上一轮扁平修复未能覆盖该数据格式。
   - **修复闭环**：审核人在提交 `0260041` 中完成了产品逻辑修复，经本地多形态验证及随后的云端全量 CI 运行（Run 38557745377、Run 38598860995）核验证实，B4-03 均稳固通过，再无异常。

2. **是否存在“一次失败、重跑后通过”的不稳定/Flaky 用例**：
   - **不存在（0 例）**。
   - 所有 28 个必测用例在历次有效运行中只要满足前提条件均表现出严格的确定性通过，不存在任何时序竞争、网络抖动或偶发重跑才通过的不稳定情况（该结论在 [REVIEW-09](file:///D:/PAW/paw-beta/_handoff/REVIEW-09.md) 第 18 行已获审核人独立核实与认可）。

---

## 6. 停步说明

按照 [TASK-G-07](file:///D:/PAW/paw-beta/_handoff/TASK-G-07.md) 与 [REVIEW-09](file:///D:/PAW/paw-beta/_handoff/REVIEW-09.md) 的明确要求：“在 DONE 报告里写明本次走的是 B 方案，并附上第 3 步的输出。写完停步。”
本报告交付完毕，执行立即停步。
