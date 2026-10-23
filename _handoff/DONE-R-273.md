# R-273 完成报告（rc3 扫描 phase2 卡住的定位、诊断与修复）

## 先说结论

`Scanner did not reach completed within 40s (last state=phase2_analyzing)` 不是死锁，而是 **phase2 里有三处没有上限的等待**，任何一处触发都会让扫描线程停在 `phase2_analyzing`，而同一进程的 HTTP 侧照常响应（所以进度接口一直是 200 OK）：

| 等待点 | 代码位置 | 原来最长 | 现在最长 |
|---|---|---|---|
| 等别的管线释放同一文件版本 | `backend/runtime_core/scanner.py` → `governance.wait_for_source_completion`（`backend/pm/source_barrier.py`） | **60 s** | 15 s（超时后记 `owner_still_running` 并继续，不再抛错把整次扫描判失败） |
| 等 SQLite 写锁写 `analyzed_source` | `backend/governance/service.py: release_source` | **30 s × 每条语句** | 4 s/次、总量 12 s，期间记 `db_write_busy` |
| 429/503 重试风暴 | `backend/provider/adapter.py` | **3 × 20 s + 3 s = 63 s**（持有 claim 全程） | 预算 25 s（`SCAN_RETRY_BUDGET_SECONDS`），超预算直接走规则回退 |

三者之和远超验收的 40 秒窗口，而 rc3 之前的多次运行只是没撞上。**本地 40 次（安装版）/ 60 次（开发态）都没能复现 CI 那次停顿**，所以本轮交付的是：把三处等待全部加上限、把等待者与被等待者写进日志、并在失败时把证据打包成 artifact——下一次出现会直接指出是哪一处，而不是再猜一次。

## 1. 诊断能力（本次新增）

- `backend/core/log_setup.py`（新增）：桌面版是 `console=False`，stdout 被丢进 devnull，**之前安装版根本没有日志**。现在把 `paw.scanner / paw.scan.diag / paw.governance / paw.planner / paw.scheduler / paw.watcher / paw.provider` 写到 `%LOCALAPPDATA%\PAW\logs\paw-desktop.log`（8 MB × 3 轮转，挂 `SensitiveDataLogFilter`），并镜像一份到 `%TEMP%\paw-scan-diagnostics\`——因为验收用例之间会 `clean_environment()` 删掉 `%LOCALAPPDATA%\PAW`，只放那里的话失败那一例的日志到 job 结束就被删了。
- `backend/runtime_core/scan_diagnostics.py`（新增）：`note()` 结构化事件；`Phase2Watchdog` 每 10 秒没有进展就 `faulthandler.dump_traceback(all_threads=True)` 写进日志，同时把扫描状态快照写成 `scan_state.json`。
- phase2 事件：`phase2_file_start / phase2_model_done / phase2_file_end / phase2_barrier_wait / phase2_claim_busy / phase2_owner_still_running / phase2_claim_wait_done`；claim 侧：`claim_wait / claim_wait_timeout / claim_held / db_write_busy / db_write_slow / db_read_slow`；planner 侧：`planner_step_slow`。全部只有文件名、hash 前缀、秒数、次数、owner，**没有提示词、没有请求体、没有 Key**。
- `.github/workflows/test.yml`（只加这一步能力）：测试块失败时 `if: failure()` 收集 `%LOCALAPPDATA%\PAW\logs`、`scan_checkpoint.json`、`workspace.db` 与 `%TEMP%\paw-scan-diagnostics\`，先跑 `scripts/check_scan_diagnostics.py` 脱敏并复查——**发现疑似 Key 就让这一步失败**，然后才上传 `scan-diagnostics-<block>-attempt-<n>`。

## 2. 第 3 项：逐项排查（代码位置 + 结论）

| 项 | 代码位置 | 会不会和扫描抢锁/抢数据库 | 最长占用 |
|---|---|---|---|
| a. 启动清理被替代版本遗留项 | `backend/main.py:72-82` → `backend/pm/versions.py:149-177`（`retire_replaced_candidates`） | **不会**。它在 lifespan `yield` 之前跑完（启动轨迹 `lifespan_ready` ≈ 1 s），此时还没有任何请求进来；不吃 claim、不调模型、不起线程。只有一个连接 + 对 pending 候选的一次遍历。 | 与待审条数线性，验收样例上 0.02–0.1 s |
| b. “分析后再查一次” | `backend/runtime_core/planner.py:157-165` | **不会**。一次 `pm_family_member` 主键读；只有命中“已被替代”才会再开一个连接做 `withdraw_candidates_for`。没有轮询、没有 sleep、没有锁、不等待任何线程。 | < 5 ms（实测） |
| c. 巡航改同步后与扫描并发 | `backend/runtime_core/scheduler.py:224-238`（项目草稿在巡航线程上）；`scanner` 用 `self._lock`、`scheduler` 用 `self._lock`，**两个不同的锁** | **不共享锁**。共享的是 SQLite 写锁与 claim 注册表。巡航只在处理单个文件期间持 claim（`scheduler.py:164-191`），项目阶段不持 claim；验收用例在 seed 之前还会 `wait_for_startup_cruise`（`tests/run_pm_tests.py:60`），启动巡航不会和扫描重叠。 | 巡航持 claim = 一个文件的解析+模型调用（≤25 s） |
| d. 扫描 20 秒超时 + 重试 | `backend/provider/adapter.py:423-435`（`SCAN_MAX_RETRIES=2`、`SCAN_CALL_TIMEOUT_SECONDS=20`）；`500-516` 超时不重发；`491-501` 只对 429/503 重试 | **会**：重试期间 claim 一直被持有，3 次尝试 = 63 s，**比整个验收窗口还长**。这就是本轮要改的第一件事。 | 63 s → 现在 25 s 预算 |

## 3. 复现与测量

### 3.1 关键复现（开发态，同一套序列：建项目 → 放 docx → 指向监控目录 → 全量扫描 → 每秒读进度）

把文件放在监控目录建立**之后**写，让 watcher 先拿到它，扫描就会等 owner：

```
23:23:40,406 INFO paw.scan.diag scan phase2_file_start file=研制方案_v1.docx hash=38114155ca81 pipeline=watcher
23:23:50,987 WARNING paw.provider AI Provider HTTP 503:
23:23:51,008 INFO paw.planner 版本提议 source=38114155ca81 ... proposals=2 stored=2
23:23:51,013 INFO paw.scan.diag scan claim_held hash=38114155ca81 pipeline=watcher seconds=13.2 ok=True
23:23:51,013 INFO paw.scan.diag scan claim_wait hash=38114155ca81 seconds=10.6 owner=watcher thread=Thread-7
23:23:51,013 INFO paw.scan.diag scan phase2_barrier_wait file=研制方案_v1.docx seconds=10.6
```

owner 因为 503 重试把 claim 持了 13.2 秒，扫描在 barrier 里等了 10.6 秒——**这就是 40 秒窗口被吃掉的方式**。健康 mock 下同一条路径只有 3.5 秒 claim、扫描等待 ≈ 0 秒（`claim_held ... seconds=3.5`、`phase2_file_end ok=skipped seconds=0.0`）。

### 3.2 本地 20 次（半数打满 CPU），两版各一遍

同一台 16 核机器、同一套序列、同一份驱动脚本（`repro.py`：建项目 → 放 docx → 指向监控目录 → 全量扫描 → 每秒读进度），**安装版形态**（PyInstaller 产物，即安装包里的那个 exe）各跑 10 次正常 + 10 次 32 进程打满 CPU：

| 构建 | 条件 | 次数 | 失败 | 中位数 | 最大 | 超过 40 s 窗口 |
|---|---|---|---|---|---|---|
| rc2 `a7e305f` | 正常 | 10 | 0 | 9.3 s | 10.76 s | 0 |
| rc2 `a7e305f` | 满负载 | 10 | 0 | **41.5 s** | **56.53 s** | **5 / 10** |
| 本分支（R-273） | 正常 | 10 | 0 | 3.5 s | 9.71 s | 0 |
| 本分支（R-273） | 满负载 | 10 | 0 | **18.0 s** | **18.41 s** | **0 / 10** |

rc2 满负载逐次：34.9 / 56.1 / 56.5 / 37.4 / 31.1 / 38.2 / 41.5 / 52.4 / 56.4 / 17.5 秒 —— **一半的扫描超过了验收的 40 秒窗口**，也就是在负载高的 runner 上必然报 `Scanner did not reach completed within 40s`。
本分支满负载逐次：18.4 / 17.9 / 17.5 / 18.0 / 18.0 / 17.7 / 18.1 / 17.5 / 11.6 / 18.1 秒 —— 全部落在窗口内。

再补一组开发态（非冻结）的数据，用于确认 rc3 与 rc2 的固定成本一致：

| 版本 | 条件 | 次数 | 失败 | 最大 | p95 |
|---|---|---|---|---|---|
| rc3（本分支） | 正常 | 20 | 0 | 7.39 s | 7.39 s |
| rc3（本分支） | 满负载 | 20 | 0 | 31.83 s | 31.69 s |
| rc2（`a7e305f`） | 满负载 | 8 | 0 | 31.75 s | 31.75 s |

结论一：**rc3 没有把扫描变慢**（开发态满负载 31.83 s vs rc2 31.75 s）。
结论二：扫描对机器速度极敏感（开发态 7.4 → 31.8 s，4.3 倍；安装版 9.3 → 41.5 s 中位数）。
结论三：本分支把满负载下的最坏值从 **56.5 s 压到 18.4 s**，因为三处等待都不再能占满整个窗口。

### 3.3 负载下 phase2 的实际构成（诊断日志）

满载时单文件 phase2 只有 2.5–3.5 秒，其中模型调用占 2.5–3.4 秒；**25 秒左右花在 phase2 之前**（reader 子进程 + 注册），也就是说本地看到的“慢”主要是固定开销，不是等待。

## 4. 修复

1. `backend/provider/adapter.py`：新增 `SCAN_RETRY_BUDGET_SECONDS = 25.0`，两条重试循环（`propose` 与 `chat_completion`）在预算用尽时停止重试并记 `retry budget spent`，保证一次不可用调用不会把 claim 持到分钟级。
2. `backend/runtime_core/scanner.py`：`SCAN_OWNER_WAIT_SECONDS = 15.0`；phase2 的屏障等待超时后记 `phase2_owner_still_running`、把 checkpoint 标成 `owner_still_running` 并**继续**（原来会 `raise RuntimeError` 把整次扫描判成失败，或者傻等 60 秒）；owner 结束但没写完成记录时标 `owner_finished_unrecorded` 并留到下次，不再让整次扫描失败。
3. `backend/governance/service.py`：`release_source` 的完成记录改为 `_record_analyzed()`，`busy_timeout` 4 秒、总量 12 秒、每次等待记 `db_write_busy`；claim 注册表从 `set` 改成带 owner 的字典（`pipeline / since / thread`），等待者会打印“在等谁、等了多久”。
4. 其它调用方（watcher/cruise/capture）传入 pipeline 名，日志里能区分是谁持有。

## 5. 已知问题（沿用上一轮第 6 条，只写在这里，不改仓库文档）

> 说明里“旧版待确认项自动撤回”只在系统判定两份文件是**同一份资料的新旧版本**时才成立；找不到对应项目的文件（例如样例里的“青禾试点计划”）不会被判为同一族，仍会进入“需要你决定”。比赛后再修正说明措辞。

## 6. 改动清单

- 新增：`backend/core/log_setup.py`、`backend/runtime_core/scan_diagnostics.py`、`scripts/check_scan_diagnostics.py`、`tests/unit/test_r273_scan_diagnostics.py`
- 修改：`backend/runtime_core/scanner.py`、`backend/governance/service.py`、`backend/runtime_core/planner.py`、`backend/provider/adapter.py`、`backend/main.py`、`run.py`、`backend/watcher/observer.py`、`backend/runtime_core/scheduler.py`、`.github/workflows/test.yml`
- 未改：任何用例、等待上限、判定、`tests/paw_*`、`required_cases*.json`、`tests/pending/**`、前端、`D:\PAW\参赛提交`；未删文件、未 force push；日志与代码里没有真实 Key。

## 7. 检查结果

- `python -m pytest tests/unit -q`：257 passed
- `python tests/paw_lint.py`：lint clean
- 第一次提交 `e1dc3db` 的 PR 完整流水线**第一次运行 RED**：`B4-01` 报
  `Scanner did not reach completed within 40s (last state=completed)`。根因是本轮给
  `claim_source()` 加了 `pipeline` 参数后，它“该版本已被分析过”的分支会用
  `release_source(pipeline=...)` 写一条 `candidates_created` 为空的记录，也就是把
  owner 刚写好的 `complete` 覆盖成 `failed`，完成屏障因此永远不算完成。修法是这条
  分支只释放 claim、不写记录（`9d31f19`），并补一条测试固定住原记录不变。
- 修好后的最终提交 `9d31f19`（其后 `92de6f8` 只补本文档）：push 与 PR 完整流水线、单测、安装包构建的运行号与结果见审核回复。
- 真实模型预检（同一提交、分支上 `workflow_dispatch`，不是标签运行）：第一次 RED
  （一题在 60 秒客户端超时、两题两次被证据门拒绝后给出 67 字兜底答复），约十分钟后
  同一提交第二次 **GREEN 8/8**（全部一次调用通过，扫描 18 次调用 / 0 超时 / 95.2 秒）。
  两次的 Phase 2 形状一致（18 次调用、0 超时、1 次规则回退），回答证据条数 20–22，
  因此第一次 RED 是模型服务侧的延迟/合规抖动，不是本分支改动（本分支不碰回答提示词、
  证据门、重试后缀与 token 预算）。
- 已知的显示问题：PR 检查列表里 `Test Block B4` 的 job 状态条目会停在 `in_progress`
  （同一次运行里该 job 日志已正常收尾，判定门读到 B4-02…B4-07 全 PASS、`VERDICT: GREEN`），
  属 GitHub 侧状态滞后；另一次运行（dispatch）里同一个 block 正常 complete。

执行者进度：R-273，分支 `deepseek/R-273-scan-stall`；把 phase2 三处无上限等待全部加上限并加诊断与失败 artifact；本地 40 次安装版 + 60 次开发态未复现停顿，已给出等待链复现证据；停下原因：等待合并后再打 v2.5.0-rc4。

## 8. 审核后的补充（本轮）

### 8.1 B4-01 的 `WinError 10054`：现有证据能确定到哪一步

- 失败 run 的 artifact（`scan-diagnostics-B4-attempt-1`）**只保住了最后一例的日志**：验收清理规则里有 `%TEMP%\PAW*`（`scripts/clean_env.py` 的 `get_check_locations`），而我原来的镜像目录叫 `paw-scan-diagnostics`，每个用例开始时正好被删掉。这是本轮的 artifact 设计缺陷，不是应用缺陷。
- 现有证据能确定的：第一例的时间线是 安装 → `/app` 200 → `POST /v1/local/settings` 200 → **之后没有任何 `scanner/start` 的服务端痕迹**，9.2 秒后直接开始下一例安装；该例报告里的错误是客户端 `httpx.ReadError: [WinError 10054]`。也就是说失败发生在 `POST /v1/local/scanner/start` 这一次请求上。
- 现有证据**不能**确定的是：应用是崩溃了、还是 uvicorn 关掉了连接。因为那一例的应用日志已经被清理。
- 已修（让下一次能直接回答）：镜像目录改名为 `%LOCALAPPDATA%\Temp\scan-diagnostics`（不再匹配 `PAW*`，用例之间不再被删）；新增 `fatal-crash.log`（`faulthandler.enable`，原生崩溃会写栈）；新增扫描相关请求的服务端 trace（`http_request` / `http_response` / `http_error`，含方法、路径、状态、耗时），这样连接被重置时能看出请求是否到达服务端、是否产生了响应。

### 8.2 真实模型 RED 的逐题拒绝原因（run 39827152574 的 `grounding.gate_diagnostics`）

| 题目 | 第一次 | 重试 | 结论 |
|---|---|---|---|
| PM-U1-03 | ok | — | PASS |
| PM-U4-01 | 未运行（客户端 60 秒超时） | — | 第一次调用没在窗口内返回 |
| PM-S1-03 | `recommendation_as_fact` | `recommendation_as_fact` | 建议句写成既成事实 |
| X01 / X02 / X18 | ok | — | PASS |
| X19 | `missing_evidence_map` | ok | 缺机器块，重试恢复 |
| X20 | `recommendation_as_fact` | `recommendation_as_fact` | 建议句写成既成事实 |

即：本轮两题失败都不是缺机器块，而是「把建议/判断写成已确认/已确定」；`missing_evidence_map` 只出现一次且重试恢复。

### 8.3 修法（不放宽判定）

1. 提示词最前面新增「先看这两条」：机器块模板（含三种 kind 的完整示例）、句子归类示例（fact / analysis / recommendation），以及「分析/建议句不能写已确认/已确定/已批准」的正反例（✗「P2 的校准范围已确定」/ ✓「还没有定，建议把基准定在 2026-10-25」）。
2. 重试分两类：**块级问题**（缺块、JSON 坏、引用 id 不存在、缺 evidence/derived_from）改为「只重生成机器块」的轻量请求——正文逐字保留，合并后仍跑同一套 `validate_answer`；**句子级问题**（recommendation_as_fact、unsolicited_advice、draft_stated_as_confirmed）仍然整篇重答，因为句子本身要改。重试文本现在引用**完整**的争议句（原来截断到 50 字），并明确写出该怎么改。
3. 这样也顺带压缩了慢回答：块级修复比整篇重答快，减少「第一次超时 + 重试超时」越过 60 秒窗口的概率。
