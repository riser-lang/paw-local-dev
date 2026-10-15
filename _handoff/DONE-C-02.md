# DONE-C-02：部分完成，自动验收 RED，达到三次自修上限

2026-10-15（北京时间）；分支 `codex/C-02`；[PR #2](https://github.com/riser-lang/paw-local-dev/pull/2) → main，**未合并**。C-01 已审核合并；C-02 不满足完成标准，C-03、C-04 未开始，相关 pending 未激活。

## 1. 子项逐条对照

| 子项 | 状态 | 证据与边界 |
|---|---|---|
| 配置驱动的八个默认栏目 | 完成实现、对应自动用例 PASS | PM-B1-01 配置数量/顺序/标题一致；PM-B1-02 修改安装包中配置标题后重启，返回新标题 |
| 已核准进展同步到档案、带来源版本 | 部分完成 | Committer 事务内实现；隔离源码 API 验证通过，但安装版 PM-B1-03 因摄取失败没有得到候选，FAIL，不算验收通过 |
| 一个当前结论 + 历史 | 部分完成 | 数据层与页面实现；源码 API 验证通过，安装版 PM-B1-04 在摄取阶段 FAIL |
| 治理链内撤销 | 部分完成 | 新 pm_undo ApprovedChange 在 Committer 内恢复指针及受影响正式字段，防止覆盖后续修改；源码 API 验证通过，安装版 PM-B1-05 在摄取阶段 FAIL |
| 语义任务契约框架 | 完成框架与单元验证 | 输入/输出 schema、无 Key 不调用模型、错误输出拒绝记录；项目说明及档案综合契约；没有把缺少的模型输出编造为事实 |
| 项目上下文字节预算 | 部分完成 | 新/旧上下文接口及模型 messages 的 UTF-8 预算、阶段摘录和来源链接；纯逻辑及源码 API 验证通过；安装版 PM-B5-01 摄取阶段 FAIL，未能完整验收 |
| 巡航不写正式 project 字段 | 对应自动用例 PASS | PM-GOV-01、02、03 均 PASS；新派生表标注 AI 生成，分析接口与巡航都不写正式项目字段；采纳生成候选，用户核准后走 ReviewDecision → ApprovedChange → Committer |
| B5-02 不退化 | PASS | 同一最终 E2E 中 B5-02 PASS；旧项目详情通过带“AI 生成”标注的派生说明显示四项内容 |
| 治理负对照 | 有效 | NC-PM-GOV 在 v2.3.9-baseline 上确实 FAIL 于 formal_fields_unchanged_after_cruise；另外三个既有负对照也符合预定检查 |
| 删除一次性一致性比对、保留打包 | 完成 | build.yml 保留完整打包及 SHA256 产物，移除 baseline 下载/解包/比较；scripts/verify_consistency.py 未改、未删除；最终 Build Installer GREEN |
| B4-03 不稳定调查及修复 | 部分完成，仍 RED | 复现并修复跨管线“部分候选即完成”问题；源代码/解包独立检查可通过；最终云端又在摄取子进程发生 charmap 编码错误，B4-03 及另外五个 B4 项仍 FAIL |

**整体结论：40 个 unit PASS，完整 E2E 37 项中 27 PASS / 10 FAIL / 0 NOT RUN。不能把其中五项 PM PASS 或 B5-02 PASS 写成 C-02 已完成。**

## 2. 改动清单

- `backend/pm/storage.py`、`dossier.py`、`dossier_sections.json`：存储适配、只增迁移、栏目与来源读取。新增 pm_source、pm_section_revision、pm_section_current、pm_change_snapshot、pm_undo、pm_ai_explainer、pm_contract_rejection、pm_semantic_draft 八张表；旧表与旧记录不删除。
- `backend/pm/governance.py`、`backend/governance/committer.py`：增加 pm_section / pm_undo 写入类型；通过已有授权校验与同一事务记录项目更新、栏目修订、撤销和收据。没有新增正式数据旁路。
- `backend/pm/contracts/`、`semantic.py`：标准库 schema 校验和模型边界；错误输出仅记录拒绝原因，不保存为正式结论。
- `backend/pm/budget.py`、`legacy_context.py`、`backend/repair_v238.py`：完整序列化 UTF-8 预算；保留来源索引，使用明确标注的阶段摘录；模型请求的 schema/指令和 JSON 转义也计入限制。
- `backend/runtime_core/project_analyzer.py`、`backend/workspace_api/project_handbook.py`：删除模型说明直接写 project 的路径，使用独立非正式派生表；已有四项快速了解读取派生区并标注 AI 生成。
- `backend/pm/router.py`、`backend/main.py`：档案、来源、送审、采纳送审、撤销、上下文及契约综合入口；`/app/pm/projects/{id}` 基础档案页。完整驾驶舱/甘特图仍属 C-06，未做。
- `backend/runtime_core/planner.py`、`backend/watcher/observer.py`：监控/扫描/巡航统一进入 Planner，保存来源快照，候选失败不再默默当作完整分析。
- `backend/pm/source_barrier.py`、`backend/governance/service.py`、`backend/runtime_core/scanner.py`：处理权完成同步、analyzed_source 增加 completion_state 列、异常时明确失败。
- `backend/pm/reader_process.py`、`backend/reader/dispatch.py`、`run.py`：最后一次自修的 worker 提前分流与显式标准流；**未解决最终云端编码错误**。
- `frontend/pm-src/`、`frontend/dist/pm/`：独立 esbuild 源码与构建产物，事实/历史/来源/撤销及 AI 生成区；第三方 esbuild 0.25.10 / MIT 记录于 THIRD_PARTY.md。字体、rough.js 与完整导航属于 C-06，尚未引入。
- `scripts/package_app.py`、`paw_desktop.spec`、两个 build/test workflow：前端构建与 PM 数据文件打包、九项 PM 安装版用例、v2.3.9 治理负对照。
- `tests/scanner_wait.py`、`tests/run_e2e_tests.py`、`tests/run_pm_tests.py`：等待真实完成状态及处理权登记，原 40 秒/负对照 30 秒上限未加长；查询连接确定关闭；保留全部断言。
- `tests/unit/test_pm_core.py`、`test_scanner_wait.py`、`test_source_barrier.py`、`test_reader_bootstrap.py`：纯逻辑与实际子进程单元验证。
- `scripts/verify_source_completion.py`：隔离的跨管线竞态复现，输出明确的 3 → 4 条完成证据。
- `_handoff/B4-03_INVESTIGATION_C-02.md`、本报告、STATUS：调查与如实交接。

审核提交原来留在已合并的 codex/C-01 分支，没有进入 main：按用户要求阅读后，将 999ce47 / 4160fb0 原样 cherry-pick 到 C-02（f79cf94 / 8d6a852），保留审核作者与原内容。没有自行合并任何 PR，没有 force push 或改写历史。

## 3. 最终实现提交的测试

PR 头部实现提交：`cdca0d27aa4b57fcb0bb9d65d07334b352df7163`。GitHub 临时测试合并提交：`afc4dc8a88ae2467d26bf7f13c712631b678d083`；以下完整测试和 unit 都由同一头部触发，不是执行者合并 PR。

| 运行 | 结论 | 关键输出 |
|---|---|---|
| [unit.yml 38737417441](https://github.com/riser-lang/paw-local-dev/actions/runs/38737417441) | GREEN | 40 passed in 0.19s |
| [test.yml 38737413019](https://github.com/riser-lang/paw-local-dev/actions/runs/38737413019) | RED | Required 37 / PASS 27 / FAIL 10 / NOT RUN 0，四个负对照有效 |
| [build.yml 38737413011](https://github.com/riser-lang/paw-local-dev/actions/runs/38737413011) | GREEN | 打包、安装包与 SHA256 上传成功；没有再跑一次性还原比较 |

本报告在上述两个测试结束后才写，后续只追加交接文档；文档提交的自动检查与实现提交的这次 RED 应分别看待，不会用一次偶然重跑的 GREEN 覆盖它。最新检查见 [PR #2 Checks](https://github.com/riser-lang/paw-local-dev/pull/2/checks)。

自修记录（都改变了代码，不是对同一失败提交点击 rerun）：

| 阶段 | 提交 | 完整运行 | 结果 |
|---|---|---|---|
| 初版 | 0cbf90f | [38731752910](https://github.com/riser-lang/paw-local-dev/actions/runs/38731752910) | RED，空候选及 SQLite 清空残留 |
| 第 1 次自修 | 771bca0 | [38733209431](https://github.com/riser-lang/paw-local-dev/actions/runs/38733209431) | RED；清空问题解决，PM-GOV 与 B5-02 PASS，候选仍失败 |
| 第 2 次自修 | 30f813d | [38735466289](https://github.com/riser-lang/paw-local-dev/actions/runs/38735466289) | RED；B4-03 当轮 PASS，其他 B4 及四项 PM 文档用例仍失败，不视为稳定修好 |
| 第 3 次自修 | cdca0d2 | [38737413019](https://github.com/riser-lang/paw-local-dev/actions/runs/38737413019) | RED；最终诊断为 worker charmap 中文编码错误；达到上限，停止 |

push 与 PR 事件重复触发的 push E2E（38731738223、38733201980、38735458958、38737401925）已取消以释放 runner；它们不是 PASS 证据。每个阶段采用上表完整 PR 运行，未跳过用例或降低判定。

本地命令与关键输出：

```text
python -m pytest tests/unit -q
40 passed in 0.15s
python tests/paw_lint.py
lint clean
python scripts/verify_source_completion.py
Partial proposals: 3; legacy analyzed predicate: True; fixed predicate: False
Scanner waited for owner release; final proposals: 4; completion record: complete; PASS
git diff 5824851 -- tests/paw_case.py tests/paw_verdict.py tests/paw_lint.py tests/required_cases.json
（无输出）
git hash-object tests/required_cases_C-02.json tests/required_cases_C-02b.json
05984c38b34abbf566b0e46095a46dc9ff546755
adf900ce0560415197552d40920940b03121800e
```

这两份用例以 git mv 激活，内容哈希与审核人原件相同。新增 E2E 编号为 PM-B1-01…05、PM-B5-01、PM-GOV-01…03、NC-PM-GOV；都是预先批准的用例，不另建 proposed_cases 文件。C-03/C-04 等 pending 没有激活或改写。

另在独立目录、独立端口、假 Key/mock 下验证了源码 API 的分析不改正式字段、候选核准、原文、历史、字节预算和撤销。解包检查没有运行安装程序，只启动自己创建的进程树，没有执行本机全局清空或终止其他 PAW 实例。**这些补充检查不能替代失败的云端安装版验收。**

### summary.md 全文（run 38737413019，原样）

```markdown
# Pipeline verdict for afc4dc8a88ae2467d26bf7f13c712631b678d083

Required cases: 37 | PASS 27 | FAIL 10 | NOT RUN 0

| Block | Case | Status | Failed check | Model |
|---|---|---|---|---|
| B1 | B1-01 | PASS |  | mock-llm |
| B1 | B1-02 | PASS |  | mock-llm |
| B1 | B1-03 | PASS |  | mock-llm |
| B2 | B2-01 | PASS |  | mock-llm |
| B2 | B2-02 | PASS |  | mock-llm |
| B2 | B2-03 | PASS |  | mock-llm |
| B3 | B3-01 | PASS |  | mock-llm |
| B3 | B3-02 | PASS |  | mock-llm |
| B3 | B3-03 | PASS |  | mock-llm |
| B3 | B3-04 | PASS |  | mock-llm |
| B4 | B4-01 | PASS |  | mock-llm |
| B4 | B4-02 | FAIL | flat_update_candidate_found | mock-llm |
| B4 | B4-03 | FAIL | empty_update_candidate_found | mock-llm |
| B4 | B4-04 | FAIL | todo_candidate_found | mock-llm |
| B4 | B4-05 | FAIL | knowledge_candidate_found | mock-llm |
| B4 | B4-06 | FAIL | candidates_available_for_batch_accept | mock-llm |
| B4 | B4-07 | FAIL | candidate_available_for_reject | mock-llm |
| B5 | B5-01 | PASS |  | mock-llm |
| B5 | B5-02 | PASS |  | mock-llm |
| B5 | B5-03 | PASS |  | mock-llm |
| B6 | B6-01 | PASS |  | mock-llm |
| B6 | B6-02 | PASS |  | mock-llm |
| B7 | B7-01 | PASS |  | mock-llm |
| B7 | B7-02 | PASS |  | mock-llm |
| B7 | B7-03 | PASS |  | mock-llm |
| B8 | B8-01 | PASS |  | mock-llm |
| B8 | B8-02 | PASS |  | mock-llm |
| B8 | B8-03 | PASS |  | mock-llm |
| NEGATIVE-CONTROL | NC-01 | FAIL | project_update_committed | mock-llm |
| NEGATIVE-CONTROL | NC-02 | FAIL | explainer_fields_filled | mock-llm |
| NEGATIVE-CONTROL | NC-03 | FAIL | environment_clean | mock-llm |
| NEGATIVE-CONTROL | NC-PM-GOV | FAIL | formal_fields_unchanged_after_cruise | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | FAIL | exception | mock-llm |
| PM02 | PM-B1-04 | FAIL | exception | mock-llm |
| PM02 | PM-B1-05 | FAIL | exception | mock-llm |
| PM02 | PM-B5-01 | FAIL | exception | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |

## Problems
- B4-02: FAIL (flat_update_candidate_found)
- B4-03: FAIL (empty_update_candidate_found)
- B4-04: FAIL (todo_candidate_found)
- B4-05: FAIL (knowledge_candidate_found)
- B4-06: FAIL (candidates_available_for_batch_accept)
- B4-07: FAIL (candidate_available_for_reject)
- PM-B1-03: FAIL (exception)
- PM-B1-04: FAIL (exception)
- PM-B1-05: FAIL (exception)
- PM-B5-01: FAIL (exception)

VERDICT: RED
```

## 4. 截图

最终 run 38737413019 的 `screens-PM02` 产物：

- `screen_PM_GOV_02_1440.png`：真实档案页、八栏目及 AI 生成区。
- `screen_PM_GOV_02_390.png`：手机单列，无整页横向滚动，带 AI 生成说明及采纳入口。

使用 headed Chromium 在实际 `/app/pm/projects/{id}` 页面截图并 c.screenshot 登记，未缩放补尺寸、未使用兜底图片。上一实现运行相同页面的两个原始截图已人工查看；最终运行的对应文件 SHA256 完全相同：1440 图为 `64e0e9660ada32ee9f8211c965d13b5836fef1f8e255c852a03f1ad64d301eaf`，390 图为 `2d9bbbc7daa43ace24fb0ccece9665b89e3dd92244d561f868ce03236798af97`。完整驾驶舱、手绘甘特图、旧对话页并排图和打包中文字体尚属 C-06，NOT RUN，不宣称已通过完整设计验收。

## 5. 未解决问题、风险和停下原因

**已达到同一“文档摄取后空候选”问题三次自修上限，停止 C-02，不叠加 C-03/C-04。**

最终 B4 job：[116430991523](https://github.com/riser-lang/paw-local-dev/actions/runs/38737413019/job/116430991523)。日志关键输出：

```text
Scan completion evidence: status=completed, total_files=1,
phase1_success=0, phase2_success=0, failed_files=1, new_candidates_count=0
Scan ingestion failures: error_type=SUBPROCESS_CRASH
Worker parse error: 'charmap' codec can't encode characters in position 107-112:
character maps to <undefined>
```

复核命令：

```powershell
gh api repos/riser-lang/paw-local-dev/actions/jobs/116430991523/logs --allow-escape-sequences | Select-String -Pattern 'Scan ingestion failures|Scan completion evidence|Completion barrier read'
rg -n 'sys.stdout.write|sys.stderr.write|ensure_ascii' backend/reader/subprocess_worker.py
```

现有 `backend/reader/subprocess_worker.py:45-51` 将含中文路径的 JSON 以 `ensure_ascii=False` 写到 stdout，`:57-62` 还写 stderr/错误 JSON。该文件本 PR 没有改动。云端的明确错误是 charmap 编码失败；**最可能的具体位置是这些标准流输出，但父进程把 stderr 截为 180 字符，完整抛错行号仍未确认**。本机中文系统、独立目录的解包测试不能复现云端英文环境的错误，不将其写成“已修好”。

给审核人的下一步建议（**未执行**）：明确为 worker 现有的 stdout/stderr 选择 UTF-8，或让传输 JSON 使用 ASCII 转义，并在英文 Windows/中文文件名下验证退出码、结果文件和整条入库链；仍需同一个新实现提交的全量 GREEN。不是继续增加等待时间，也不是接受目录存在就当解析成功。

其他限制：

- PM-B1-03/04/05、PM-B5-01 均卡在摄取，不是这些功能已通过验收。源码单独检查通过只能算补充证据。
- B3 按既有用例 PASS；它有按证据目录名计数的回退。不能单靠其 GREEN 排除子进程失败，后者已经有明确日志。没有修改 B3 预期或放宽它的检查。
- 基础页面与短上下文已验证；长期大项目、旧库长描述裁剪后的完整回看还需后续验收，不能把字节数达标当成语义覆盖率达标。
- 治理修复中的“采纳”是生成待审核候选，正式字段要在用户继续核准后才写入。没有把 AI 派生内容偷偷升级为事实。
- C-02 未通过自动验收，Claude Code 尚未审核本 PR；产品负责人验收未进行。版本仍为 2.3.9，没有打 tag、发布或替换保底版本。
- 没有访问禁入目录或私有仓库，没有使用 SSH、真实 Key 或真实模型。新增测试资料全部虚构。

## 6. 大白话摘要（5 行）

治理旁路已改，三个治理用例和旧项目快速了解都通过了。
档案、来源、历史、撤销和预算已有实现，但四项安装版验收仍卡在导入文档。
最终全量结果是 27/37，通过数不能冒充全部完成。
已经取得子进程中文输出编码错误的明确日志，尚未修好。
按三次自修上限停在 C-02，等审核人处理后再继续。
