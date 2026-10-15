# C-01 本地根因核实

调查基线：`08d40d7`；2026-10-15。以下是静态代码调查，不代表已跑安装包验收。

| 根因 | 判定 | 证据与本地影响 |
|---|---|---|
| C1 版本裁定要求显式指令 | 存在 | `backend/runtime/core/paw_source_version_governance.py:296-315`：文件名或版本标签没有显式裁定时返回 `USER_DECISION_REQUIRED`。实际扫描入口 `backend/runtime_core/scanner.py:570-576` 把版本注册为 `unresolved`，`589-622` 把多版本变为知识候选，并没有按生效日期/版本号自动裁定。 |
| C2 新版本没有触发项目重新综合 | 存在（本地活动链路） | 旧核心 `paw_source_version_governance.py:452` 能写重新综合标记，`paw_project_outcome.py:707-708` 能读取；但是本地 `backend/runtime_core/scheduler.py:187-207` 调用的是 convergence 与 project_analyzer，未消费这些标记。全库搜索调用处未发现本地入口串联。不能把旧模块存在当成实际执行。 |
| C3 历史资料不能定向复读 | 存在 | `backend/runtime_core/scheduler.py:149-155` 明确说明即使 deep cruise 也不再次分析已处理文件，按 hash/claim 跳过；`174-182` 仅取未处理随手记。需要增加有触发条件、额度和幂等键的独立复读队列。 |
| C4 项目上下文没有总量上限 | 部分存在 | `backend/workspace_api/repository.py:452-584` 对 todo 等有条数限制，knowledge/source 为全量，且返回多份投影，没有序列化字节预算。`backend/runtime_core/project_analyzer.py:98-110` 对描述、待办和知识有局部裁剪，但这不是项目上下文全局预算。云端的具体体积未在本地复现，不沿用其数字。 |
| C5 建议核验导致整条拒答 | 部分存在 | `backend/brain/context/agy_answer_contract.py:358-364,388-389` 已允许带依据的分析/建议，无须建议本身为已存事实；但 `backend/brain/api/local_agy_facade.py:1182-1217` 的校验失败仍会整条重试、失败关闭，没有逐句移除不合格建议。本地未复现特定问题的 `ANSWER_NOT_VERIFIED` 字面状态，不能宣称完全等同云端。 |
| C6 没有生成缺口/风险/下一步/周报 | 部分存在 | `backend/runtime_core/evolution_engine.py:75-80,153-202,589-629` 有知识健康与维护建议；`project_analyzer.py:85-128` 生成四字段说明。它们没有构成每项目分栏目、事实与建议分存、带来源、可撤销的档案及周报流程。 |
| C7 待决定事项分散 | 存在 | `backend/governance/router.py:110-115,184-189` 管 candidate 审核和冲突标记，`backend/workspace_api/repository.py:465-468` 另查 pending_item；扫描产生版本认定候选（scanner.py:598-622）。未发现汇总这些类型、说明两种选择后果并支持批量治理写入的统一收件箱。 |

复核命令（只读）：

```powershell
rg -n 'parse_capture_version_directive|USER_DECISION_REQUIRED' backend/runtime/core/paw_source_version_governance.py
rg -n 'resynthesis|filter_current_source_versions|identify_stale_outcome_refs' backend --glob '*.py'
rg -n 'processed_hashes|claim_source|analyze_projects|unprocessed_captures' backend/runtime_core/scheduler.py
rg -n 'project_context|LIMIT 100|admitted_sources' backend/workspace_api/repository.py
rg -n 'validate_answer|fail_closed_after_retry' backend/brain/api/local_agy_facade.py
rg -n 'suggestions|health_report' backend/runtime_core/evolution_engine.py
rg -n 'pending|conflict' backend/governance/router.py
```

关键输出分别为：`filename_or_label_only_not_authoritative`；重新综合引用只在旧核心/旧巡航；`if f_hash in self._processed_hashes or not governance.claim_source(f_hash)`；上下文仅局部 LIMIT；`fail_closed_after_retry`；知识维护建议；分散的 candidate/pending 查询。

资料缺失：仓库没有 `_handoff/REVIEW-04.md`，`git log --all --oneline -- _handoff/REVIEW-04.md` 无输出。已完整读 REVIEW-05、REVIEW-06；没有尝试读取禁入目录或私有仓库。
