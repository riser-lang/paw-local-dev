# C-02 至 C-07 实施方案

2026-10-15；依赖 C-01。所有新增核心放 `backend/pm/`，标准库实现，通过 SQLite 存储适配读写，迁移只增加表/列。正式写入必须走 ReviewDecision → ApprovedChange → Committer（含自动规则授权与撤销）；用户专属事项只能等待用户。规则来源统一为“用户 2026-10-15 设定”。下列编号直接采用审核人 pending 用例；激活时 git mv、核对 blob，不修改内容。

## C-02：项目档案、契约与预算

新增 `backend/pm/{storage,dossier,contracts,budget}.py`、`dossier_sections.json`、`contracts/*.json`，以适配层连接 `workspace_api/db.py` 的 additive migrate；增加 `pm_section_revision`（project/section/text/source_version/change/previous）、`pm_section_current`（每项目栏目唯一指针）、`pm_undo`、`pm_contract_rejection`。治理层增加栏目变更及撤销执行器，把已批准的项目进展同步为带来源的栏目结论，同一事务保留历史。提供档案、历史、来源、撤销、上下文 API；先验证来源存在，再写入。语义契约 `dossier_synthesis` 输入项目当前结论与来源，输出按栏目列出的结论和来源引用；用通用严格 schema 校验，不合法仅记录拒绝。缺 Key 返回明确提示。字节预算统计整个序列化结果（含 UTF-8 和元数据），旧阶段采用有来源的摘要，原文保持可读取；单元测试覆盖极小预算、中文和边界。

激活 `required_cases_C-02.json`；新增 E2E：PM-B1-01 配置顺序/标题一致，02 改标题重启，03 正常核准后真实引用/原文，04 第二条替换并留历史，05 撤销恢复及正式计数；PM-B5-01 超预算仍能打开原文。每例独立 fresh_install，资料和候选经正常 API/扫描产生，不用 SQL 写候选。

## C-03：文档族与版本裁定

新增 `backend/pm/versions.py`，存储 `pm_version_ruling`、`pm_family_decision` 与重综合队列，复用现有 source_version / source_version_governance 和治理变更审计。接入 scanner 的来源注册/处理完成与 scheduler，替换“最新观测仅 unresolved”的单一路径；按文内生效日 > 版本号 > 摄取时间比较，保持源可查；不能唯一确定文档族/项目时生成待决项，不能因文件名相似就擅归。每次裁定及栏目引用替换在同一治理事务，撤销同时恢复。语义契约 `family_match`、`source_metadata`、`version_resynthesis`，模型只能提出有证据的归族/日期，规则再裁定；日期必须能定位原文。当前证据读取与助手统一过滤 superseded，不只改页面标签。

激活 C-03；PM-B2-01 v1/v2 与引用切换，02 生效日优先，03 模糊归族不改正式数据，04 撤销恢复版本/引用，05 助手只引用当前版本；NC-PM-B2 使用 `v2.3.9-baseline`，复跑 PM-B2-01，必须失败在 `old_version_superseded`，不能接受安装失败替代负对照。每例新安装，baseline 也正常扫描。

## C-04：栏目更新、冲突、覆盖与复读

新增 `backend/pm/{updates,conflicts,coverage,reread}.py`，表 `pm_conflict`、`pm_coverage`（单位类型/总数/已读/遗漏原因）、`pm_reread_job`（trigger/version/section/轮次/状态）。语义契约 `section_update` 给出一致/补充/替换/冲突及依据；严格校验来源、当前版本及关系。一致不重复写；补充/同族新版替换经自动规则决策进入 Committer；等效互斥资料只建冲突，不改当前结论。项目/KPI/政策/绩效/人员/赛制通过确定性 R3 门禁转用户。冲突裁决沿治理链，答复上下文加冲突提示。扫描提取阶段记录 docx 段落/表、xlsx sheet/表、pptx slide、pdf page 的读取情况，无法可靠判页的格式如实标注，不伪造覆盖率。只在新版/冲突/低覆盖时复读，队列幂等、逐条、每轮 5–10 条，无触发不重读。

激活 C-04；PM-B3-01 一致不增记录，02 补充治理写入可撤销，03 新版替换留历史，04 冲突不改结论且问答提示，05 六类用户专属决策均阻止自动写，06 用户裁决落库；PM-B4-01 四格式覆盖与遗漏，02 触发/额度/无条件不复读。完成后固定停点，待审核人批审 C-01…04。

## C-05：输入流转与回执

新增 `backend/pm/{routing,receipts}.py`，表 `pm_input_receipt`、`pm_assignment_decision`，连接文件夹扫描、现有 `/captures` 与 scheduler.process_capture；不另建重复录入入口。契约 `input_assignment` 输入项目候选与原文，输出 project_id、置信度、逐字依据及建议栏目。只有明确且唯一的项目依据才自动归入；低置信度/多项目进入统一决策存储，用户选择后才写。回执与栏目写回同一事务更新，含文件名/摘要、目标、栏目、待确认/已归入/失败状态，重复扫描不重复发回执。项目新建永远不自动。确认、拒绝与撤销均有审计。

激活 C-05；PM-D1-01 监控文件夹自动归入，02 模糊资料待决→选择后写入，03 随手记提到已有项目，PM-D2-01 回执实际包含去向与栏目，02 待确认→已归入。不能只验证 HTTP 200，要读回项目栏目、回执和治理表增量；用例每次清空安装。

## C-06：独立前端包

新增 `frontend/pm-src/` 原生 JS + esbuild、`frontend/dist/pm/`，本地打包 rough.js/中文衬线字体与许可证 `THIRD_PARTY.md`，`scripts/package_app.py` 先构建，spec 包含产物。后端在 SPA catch-all 之前托管 `/app/pm/...`；独立导航注入脚本连接旧侧栏与项目详情。契约消费前序数据接口，本任务无需新模型契约。新增 `backend/pm/cockpit.py` 只读组合 inbox、项目总览、建议、状态和时间线，不把读取变成 AI 生成。治理 API 批量决策，待决项解释是/否后果；档案卡片含来源、历史和撤销。rough 固定种子，周/月视图，未知日期虚线“日期待补”；640px 以下竖向时间线与待决优先。空态明确，无 Key 无模型请求。

激活 C-06；PM-C1-01…05 验证四标题、全量待决数、批量治理写入、固定 SVG、无 Key 零请求；PM-C2-01…03 来源/撤销/缺日期；PM-C3-01 旧导航回归；PM-C4-01 时间线；PM-C5-01 390px 无整页横滚。Playwright 有界面打开实际页面，1440/390 截图并 c.screenshot 记录真实 URL；另与旧对话页并排截图。NC-PM-C1 基线失败必须在 `cockpit_rendered`。

## C-07：本地运维与升级

新增 `backend/pm/{backups,health}.py`，运维状态表 `pm_system_health`；轮转配置接现有备份入口，保留最新可读备份、限制自动备份数/字节（用户手动备份另列），使用 SQLite backup API 保证 WAL 一致性。恢复自检只在临时目录打开副本、integrity_check 和关键表计数，比对成功后清理本次临时文件；生产库永不被自检覆盖。契约无需模型语义输出；模型连通健康用用户已配 provider，但测试仅 mock。巡航、扫描、备份、模型均存最后成功时间，过期/失败文字说明，不能仅报灯色。旧库覆盖升级只增加 schema，测试先通过旧版正常流程造数据再覆盖安装。

激活 C-07；PM-A1-01 六次批核准验证数量上限/最新备份计数，PM-A2-01 临时恢复自检及清理，PM-A4-01 四状态和四小时前巡航过期，PM-UP-01 基线→新版数据计数及页面。完成后固定停点；只有审核人放行后才改版本和制作 v2.4.0-rc 草稿。C-08…10 后目标 v2.5.0，任何发布均须全部自动/审核/产品验收。
