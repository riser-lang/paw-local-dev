# B4-03 不稳定调查（C-02）

## 已确认的产品问题

监控器、文件夹扫描、巡航共享同一文件 hash 的处理权，但旧的“已分析”判断把**任意一条候选存在**当作完成。监控器逐条写候选时，扫描器可能看见第 1–3 条，认为文件已分析并跳过，然后把扫描状态改成 completed；第 4 条候选仍未写完。测试原来又用“至少 3 条”作为返回条件，进一步放大了竞态。

这是完成状态的产品问题。仅改为等待扫描器 completed，不能修好跨管线完成语义；更不能靠延长超时或重跑碰运气。

旧代码证据（main 5824851）：

- `backend/governance/service.py` 的 `is_source_analyzed()`：先查 analyzed_source，查不到时只要 candidate.source_refs 中出现文件 hash 就返回 True，没有排除 `_sources_in_progress`。
- `backend/runtime_core/scanner.py` 的扫描循环：这个判断为 True 就跳过；整个循环结束便标 completed。另一个管线持有 hash 时，`_process_phase2_file()` 同样直接跳过。
- `backend/watcher/observer.py`：独立的 provider → 逐条 create_candidate 循环，返回后才释放文件处理权。
- `tests/run_e2e_tests.py` 的旧等待函数：`len(cand_list) >= 3` 就返回。

## 历史失败证据

[main run 38727358769 / B4 job 116397109670](https://github.com/riser-lang/paw-local-dev/actions/runs/38727358769/job/116397109670) 的原始日志：

```text
2026-10-15T11:31:59.7805571Z Candidates generated after 2s: 3 found (scanner_status=completed)
```

对应报告 B4-03：`FAIL / empty_update_candidate_found`。同一 main 08d40d7 的 run 38727278779 为 GREEN，已在 C-01 报告登记为不稳定迹象。

这条日志排除了“只是没等到 scanner completed”的解释。历史运行没有上传后台管线完整日志，因此无法证明该次历史失败没有其他共同因素；下面对同源产品代码的复现不依赖这种猜测。

## 确定性复现

命令：`python scripts/verify_source_completion.py`。该脚本使用新建的隔离数据目录、虚构输入和按输入产生提议的 mock，不调用真实模型，不操作本机安装、不清空全局 PAW 目录。

它通过正常 Planner / Governance 创建前 3 条候选，在写第 4 条前用线程事件暂停；然后让扫描管线处理相同 hash。没有直接向 candidate / approved_change / review_decision 插测试记录。

关键输出：

```text
Partial proposals: 3; legacy analyzed predicate: True; fixed predicate: False
Scanner waited for owner release; final proposals: 4; completion record: complete; PASS
```

脚本验证：旧 SQL 的判断在只写完 3 条时为 True；新判断此时为 False；扫描器不能提前返回；释放处理权后完整 4 条才可读。`tests/unit/test_source_barrier.py` 另验证等待与释放通知，`test_scanner_wait.py` 验证已完成的扫描还要等其他管线完成。

## 修复

1. `is_source_analyzed()` 不再把正在处理中的资料视为完成；处理权释放时通知等待者。
2. 扫描跳过同一文件前等待其处理者完成，不能因为其他管线持有文件就宣布完成。
3. analyzed_source 通过增加 completion_state 列区分 complete / failed；候选落库失败会向上传播，不把半成品登记为成功。
4. 监控器改走统一 Planner，和扫描/巡航共用原文快照、候选生成与失败传播。
5. 测试等待 completed，并核对检查点中成功摄取的 hash 已登记完成；没有增加原有超时。记录扫描与来源完成状态，便于后续定位。
6. 本次第一版新查询使用 SQLite connection 上下文但未显式 close，Windows 中会残留数据库句柄；改用 closing 确定关闭，保留原有清空判定。这是本次引入并纠正的测试辅助函数错误。

## 云端验证记录

首次 C-02：[run 38731752910](https://github.com/riser-lang/paw-local-dev/actions/runs/38731752910)，RED，B4 空候选及后续清空失败；未当作 PASS。
完成第一轮修复后：[run 38733209431](https://github.com/riser-lang/paw-local-dev/actions/runs/38733209431)，RED，清空和 PM-GOV-01…03、B5-02 已通过，文档候选仍失败。独立解包验证了 reader 正常、监控路径曾生成候选却未保存 PM 来源；因此统一了监控路径，并增加了完成证据日志。

最终运行与 B4-03 的判定，以 `_handoff/DONE-C-02.md` 中最后一个实现提交的完整门禁为准；本文件不把尚未完成的运行计为 GREEN。

后续 run 38735466289 的 B4 日志进一步区分了问题：B4-03 已 PASS；其他空候选用例的真实摄取状态是 `phase1_success=0, failed_files=1`，并非仅仅候选等待不足。解包产物在本机独立目录中可读文件；云端失败条件尚不能在本机完整复现。最后一次自修将 reader worker 的入口提前到配置/uvicorn/托盘导入之前，且显式绑定 stdin/stdout/stderr，避免窗口程序对子进程标准流和桌面初始化的依赖；失败原因现在输出在摄取诊断中。不把该措施宣称为已证实修复，需看最终完整运行。

依据：[PyInstaller 6.12 无控制台与子进程说明](https://pyinstaller.org/en/v6.12.0/common-issues-and-pitfalls.html)、[PyInstaller 官方 subprocess recipe](https://github.com/pyinstaller/pyinstaller/wiki/Recipe-subprocess)。这些资料说明了启动隔离的注意事项；本地完整子进程启动、stdin EOF 与入口不初始化桌面的测试提供代码层证据。

最终实现 cdca0d2 / run 38737413019 仍为 RED（27/37）；B4-03 也在该轮 FAIL，不算稳定修复。B4 job 116430991523 的失败记录为 `SUBPROCESS_CRASH / Worker parse error: 'charmap' codec can't encode characters in position 107-112`。这是 Windows 子进程中文输出的编码故障；现有 subprocess_worker.py 使用 ensure_ascii=False 写标准流，与该诊断相符，完整抛错行号因父进程截断日志仍未确认。已停止产品改动；建议审核人确认 worker stdout/stderr 的 UTF-8 或 ASCII JSON 策略后在英文 Windows 继续验证。没有进行第四次自修。完整原文门禁、三个修复提交与失败项见 DONE-C-02.md。
