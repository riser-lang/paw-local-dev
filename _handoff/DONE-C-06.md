# DONE-C-06 — 本地驾驶舱与设计迁移

2026-10-16；PR #6，依赖 PR #5；按 #5 → #6 顺序审核合并。未合并 PR，未改版本、打 tag 或发布。

## 1. 子项对照
- 完成：迁移交付的 TodayCockpit / ProjectDossier、DecisionInbox、手绘甘特图与手机竖向时间线。界面数据由本地 backend/pm/cockpit.py 按导出的两个 View Model 组装，页面读取不调用模型。
- 完成：统一收件箱汇总全部待审核候选、待确认归属/版本、冲突及用户专属栏目事项；冲突关联的 assessment 只计一次。每项说明决定内容与两种后果；批量操作逐条返回真实治理结果，稍后保留待决。
- 完成：项目页按配置显示栏目、真实来源、历史与撤销；已有版本裁定、覆盖率、派生说明保留。里程碑使用已核准的项目待办，未知日期为 null，日期待补同时进入缺口；不从散文猜测日期/进度。
- 完成：最近变化与图中编号共用真实 change_id/时间；固定随机种子；390px 改竖向布局。手机正文和审批来源标签自然换行，取消用隐藏溢出掩盖裁切。
- 完成：旧侧栏进入驾驶舱、去向页；旧项目详情提供本项目档案入口。AI 助手链接使用既有 /app/agnes，并检查真实标题和输入框。没有 Key 时首页和项目页明确提示；指标目标文案改为由用户确认。
- 本地系统状态五项为巡航、扫描、备份、模型连通、磁盘；本任务显示数据结构与未知状态，真实运维观察由后续 C-07 接入。

## 2. 迁移与改动清单
- 只读来源 D:\PAW\design-export\src\pm 复制到 frontend/pm-src/design：pages/{TodayCockpit,ProjectDossier}.tsx、components/*.tsx、rough.tsx、time.ts、types.ts、index.ts、styles/{pm,fonts}.css、vendor/rough.esm.js 与声明文件，以及198个字体子集。许可证原文复制到 licenses/，导出 JSON schema 复制到 view_model.schema.json。
- 已改造：本地 app.tsx 实现数据读取、治理回调、真实来源弹窗、归属选择；旧入口 app.js 用 git mv 保留为 native_project_v1.js。receipt/version/coverage/AI派生说明模块继续使用；冲突旧清单只读展示，主裁决交设计组件，避免重复按钮。
- 本地覆盖样式 local.css：原生导航、弹窗、迁移扩展区，以及长审批标签/栏目标题和手机正文换行。设计页面只调整本地 Key/指标说明、系统状态标题、补充真实栏目条目；保持纸面设计、字号层级、色彩和绘图组件。
- backend/pm/cockpit.py、router.py：View Model 组合与批量治理；dossier_views.json 为单独的呈现配置，原 dossier_sections.json 的已验收格式保持原样。main.py 托管 /app/pm/today。
- frontend/pm-src/build.mjs/package*.json：React18.3.1与ReactDOM锁定，esbuild构建、本地字体与许可证；paw_desktop.spec 打包呈现配置，现有打包流程与两份CI均有Node22。
- 未迁移：mock、demo、demo-src、演示截图、独立gantt示例包及其示例数据；组件版甘特图已直接接入，因此不重复打包独立示例组件。没有引入云端登录、OAuth、MCP或云端接口。
- 字体仅Noto Serif SC600的101个子集、文楷Regular400的97个子集，共7,853,936字节；二进制保持原样。rough.js4.6.6、React/ReactDOM18.3.1、scheduler0.23.2、两字体许可证见THIRD_PARTY.md与licenses/，产物随安装包携带。
- 激活 tests/required_cases_C-06.json，blob18c112f2c92c7d28533b9653ccea06135e3e1fb5，一字未改；新增PM06运行器、矩阵与NC-PM-C1；未改判定器及既有预期。

## 3. 测试、运行链接与完整原始 summary
最终功能提交 **ac77f8168c2ba9e11b445f51efb93292fcdd2e0e**。本报告后续提交会另触发push与PR；其结果在PR描述补齐，以下结论只对应明确列出的提交。

| 工作流 | 事件 | 运行 | 结果 |
|---|---|---|---|
| PAW Unit Tests | push | [38834446689](https://github.com/riser-lang/paw-local-dev/actions/runs/38834446689) | GREEN |
| PAW Verification Pipeline (TASK-G-03) | push | [38834446702](https://github.com/riser-lang/paw-local-dev/actions/runs/38834446702) | GREEN |
| Build Installer | pull_request | [38834451755](https://github.com/riser-lang/paw-local-dev/actions/runs/38834451755) | GREEN |
| PAW Verification Pipeline (TASK-G-03) | pull_request | [38834451757](https://github.com/riser-lang/paw-local-dev/actions/runs/38834451757) | GREEN |
| PAW Unit Tests | pull_request | [38834451852](https://github.com/riser-lang/paw-local-dev/actions/runs/38834451852) | GREEN |

push原始判定：66/66 GREEN；六个负对照按各自指定检查失败。

pull_request原始判定：66/66 GREEN；六个负对照按各自指定检查失败。

B7原15秒标准保留；以下为最终两次实际回答耗时（秒），原日志在对应B7 job：

| 回答 | push | PR |
|---|---|---|
| B7-01 | 0.1339 | 0.1805 |
| B7-02 | 0.1275 | 0.1178 |
| B7-03 | 0.1543 | 0.1491 |

push B7-03计数与上下文：`{'model_calls': 1, 'model_seconds': 0.0291, 'prompt_bytes': 4793, 'search_seconds': 0.0, 'context_seconds': 0.0028, 'evidence_count': 1, 'context_bytes': 4423, 'total_seconds': 0.123}`。

pull_request B7-03计数与上下文：`{'model_calls': 1, 'model_seconds': 0.0237, 'prompt_bytes': 4793, 'search_seconds': 0.0, 'context_seconds': 0.0042, 'evidence_count': 1, 'context_bytes': 4423, 'total_seconds': 0.1374}`。
- 本地unit92项通过，lint clean，前端构建通过；隔离的真实API和批量治理探针通过。实际API输出按导出JSON Schema校验，Today/Project均PASS。核心仅标准库与存储适配，jsonschema只用于测试。
- PM06验收为PM-C1-01…05、PM-C2-01…03、PM-C3-01、PM-C4-01、PM-C5-01；NC-PM-C1在v2.3.9上必须且确实失败于cockpit_rendered，不能用安装/导入失败替代。没有新增proposed_cases，直接激活审核人预定文件。
- 首轮问题与修正：新增view_kind曾改变原栏目配置格式，移到独立配置并打包；重复冲突按钮由只读补充清单解决；真实初次使用弹窗通过正常关闭按钮处理。一次批量列表5秒等待失败，增加POST完成与真实视图重取的同步及逐条回包/耗时证据，原断言保留；后续push测得0.217秒。历史那次未留网络回包，确切时序未确认，不能认定已证明产品慢或根因已查明。
- 主动有界面复核发现导出手机样式曾隐藏溢出，长审批标签把正文挤出可见范围。改为换行并取消隐藏，增加正文与标签边界检查；来源、原文和撤销仍然实测。
- PM-B3-01出现过源身份不独立的夹具风险：同正文DOCX在同一ZIP时间片可能字节相同，导致去重后读取首份supplement判定。已用相同正文的两次普通保存复现相同SHA；写入各自真实文档标题后SHA不同。import_doc统一记录每份SHA，验收额外保证比较独立source_id，consistent预期不变、不增加等待。历史云端未留SHA，精确归因仍为推断；后续哈希证据用于判断。
### 87261de push [38829342321](https://github.com/riser-lang/paw-local-dev/actions/runs/38829342321) — failure

```text
# Pipeline verdict for 87261de41fd80823e095d9f69443456a24236825

Required cases: 66 | PASS 63 | FAIL 3 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | FAIL | sections_match_configuration | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | FAIL | exception | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | FAIL | exception | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## Problems
- PM-B1-01: FAIL (sections_match_configuration)
- PM-B3-06: FAIL (exception)
- PM-C3-01: FAIL (exception)

VERDICT: RED```

### 87261de pull_request [38829367475](https://github.com/riser-lang/paw-local-dev/actions/runs/38829367475) — failure

```text
# Pipeline verdict for 4576465c8853b02ec091998f0bb7e10259c480d5

Required cases: 66 | PASS 62 | FAIL 4 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | FAIL | sections_match_configuration | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | FAIL | exception | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | FAIL | exception | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | FAIL | exception | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## Problems
- PM-B1-01: FAIL (sections_match_configuration)
- PM-B3-06: FAIL (exception)
- PM-C1-03: FAIL (exception)
- PM-C3-01: FAIL (exception)

VERDICT: RED```

### a4877c2 push [38830257210](https://github.com/riser-lang/paw-local-dev/actions/runs/38830257210) — failure

```text
# Pipeline verdict for a4877c250332d9c8b7a310f84cbcf1d04207cae0

Required cases: 66 | PASS 65 | FAIL 1 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | FAIL | exception | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## Problems
- PM-C3-01: FAIL (exception)

VERDICT: RED```

### a4877c2 pull_request [38830261033](https://github.com/riser-lang/paw-local-dev/actions/runs/38830261033) — failure

```text
# Pipeline verdict for 7458b7a3786d76ee2ce36ebd8326aa7fdb896988

Required cases: 66 | PASS 65 | FAIL 1 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | FAIL | exception | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## Problems
- PM-C3-01: FAIL (exception)

VERDICT: RED```

### e0933f5 push [38830776757](https://github.com/riser-lang/paw-local-dev/actions/runs/38830776757) — success

```text
# Pipeline verdict for e0933f5a8600da9ffda4c222cc28151eb15637d4

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### e0933f5 pull_request [38830783615](https://github.com/riser-lang/paw-local-dev/actions/runs/38830783615) — success

```text
# Pipeline verdict for 32fee3713eaf61663ef5daff907ecb3d5ef9e161

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### 1d411e9 push [38831154429](https://github.com/riser-lang/paw-local-dev/actions/runs/38831154429) — success

```text
# Pipeline verdict for 1d411e9edad1974760f57f31743bc81a2ec98ed1

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### 1d411e9 pull_request [38831158910](https://github.com/riser-lang/paw-local-dev/actions/runs/38831158910) — success

```text
# Pipeline verdict for ddd3aae1fbaf85fcda38b9e267b7a78a446b874f

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### f03e7c1 push [38832059914](https://github.com/riser-lang/paw-local-dev/actions/runs/38832059914) — success

```text
# Pipeline verdict for f03e7c15adfd7f483e9c3497f46867ca648332f7

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### f03e7c1 pull_request [38832064418](https://github.com/riser-lang/paw-local-dev/actions/runs/38832064418) — success

```text
# Pipeline verdict for 52b06900dbfcda070603ba17e95734e00e220681

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### 23d52fa push [38832482490](https://github.com/riser-lang/paw-local-dev/actions/runs/38832482490) — success

```text
# Pipeline verdict for 23d52fad796cc7d4e0dfe6b6b894819ba17a15f3

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### 23d52fa pull_request [38832487910](https://github.com/riser-lang/paw-local-dev/actions/runs/38832487910) — success

```text
# Pipeline verdict for f964be1b63a2c554f97ae802ddb26f2336a7a3cd

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### c70606c push [38833216374](https://github.com/riser-lang/paw-local-dev/actions/runs/38833216374) — success

```text
# Pipeline verdict for c70606ce28806b2589e17a71f0fe5c9d8bc5627e

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### c70606c pull_request [38833221546](https://github.com/riser-lang/paw-local-dev/actions/runs/38833221546) — success

```text
# Pipeline verdict for bfbe00c6865adae7d3ccac92bf64cc2b7e2a2f7d

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### 2d5ab69 push [38833438661](https://github.com/riser-lang/paw-local-dev/actions/runs/38833438661) — success

```text
# Pipeline verdict for 2d5ab69ebc947119669801d7fedf64d618bdaeca

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### 2d5ab69 pull_request [38833444117](https://github.com/riser-lang/paw-local-dev/actions/runs/38833444117) — success

```text
# Pipeline verdict for fb75f2942623c566010a2d56fe2a7113a7706d5d

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### bbc0eaf push [38833517870](https://github.com/riser-lang/paw-local-dev/actions/runs/38833517870) — success

```text
# Pipeline verdict for bbc0eaf7b8e6d1c7c9f9a27e04e4bea170795f42

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### bbc0eaf pull_request [38833522875](https://github.com/riser-lang/paw-local-dev/actions/runs/38833522875) — success

```text
# Pipeline verdict for b696b2ab5bcbb603a3702581dbd270526a64b9a6

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### ac77f81 push [38834446702](https://github.com/riser-lang/paw-local-dev/actions/runs/38834446702) — success

```text
# Pipeline verdict for ac77f8168c2ba9e11b445f51efb93292fcdd2e0e

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```

### ac77f81 pull_request [38834451757](https://github.com/riser-lang/paw-local-dev/actions/runs/38834451757) — success

```text
# Pipeline verdict for 176d75d594e2b4b846399d4d5a41ef6156bd18fd

Required cases: 66 | PASS 66 | FAIL 0 | NOT RUN 0

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
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
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
| NEGATIVE-CONTROL | NC-PM-C1 | FAIL | cockpit_rendered | mock-llm |
| NEGATIVE-CONTROL | NC-PM-B2 | FAIL | old_version_superseded | mock-llm |
| PM02 | PM-B1-01 | PASS |  | mock-llm |
| PM02 | PM-B1-02 | PASS |  | mock-llm |
| PM02 | PM-B1-03 | PASS |  | mock-llm |
| PM02 | PM-B1-04 | PASS |  | mock-llm |
| PM02 | PM-B1-05 | PASS |  | mock-llm |
| PM02 | PM-B5-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-01 | PASS |  | mock-llm |
| PM02 | PM-GOV-02 | PASS |  | mock-llm |
| PM02 | PM-GOV-03 | PASS |  | mock-llm |
| PM03 | PM-B2-01 | PASS |  | mock-llm |
| PM03 | PM-B2-02 | PASS |  | mock-llm |
| PM03 | PM-B2-03 | PASS |  | mock-llm |
| PM03 | PM-B2-04 | PASS |  | mock-llm |
| PM03 | PM-B2-05 | PASS |  | mock-llm |
| PM04 | PM-B3-01 | PASS |  | mock-llm |
| PM04 | PM-B3-02 | PASS |  | mock-llm |
| PM04 | PM-B3-03 | PASS |  | mock-llm |
| PM04 | PM-B3-04 | PASS |  | mock-llm |
| PM04 | PM-B3-05 | PASS |  | mock-llm |
| PM04 | PM-B3-06 | PASS |  | mock-llm |
| PM04 | PM-B4-01 | PASS |  | mock-llm |
| PM04 | PM-B4-02 | PASS |  | mock-llm |
| PM05 | PM-D1-01 | PASS |  | mock-llm |
| PM05 | PM-D1-02 | PASS |  | mock-llm |
| PM05 | PM-D1-03 | PASS |  | mock-llm |
| PM05 | PM-D2-01 | PASS |  | mock-llm |
| PM05 | PM-D2-02 | PASS |  | mock-llm |
| PM06 | PM-C1-01 | PASS |  | mock-llm |
| PM06 | PM-C1-02 | PASS |  | mock-llm |
| PM06 | PM-C1-03 | PASS |  | mock-llm |
| PM06 | PM-C1-04 | PASS |  | mock-llm |
| PM06 | PM-C1-05 | PASS |  | mock-llm |
| PM06 | PM-C2-01 | PASS |  | mock-llm |
| PM06 | PM-C2-02 | PASS |  | mock-llm |
| PM06 | PM-C2-03 | PASS |  | mock-llm |
| PM06 | PM-C3-01 | PASS |  | mock-llm |
| PM06 | PM-C4-01 | PASS |  | mock-llm |
| PM06 | PM-C5-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN```


## 4. 截图
- [38834446702](https://github.com/riser-lang/paw-local-dev/actions/runs/38834446702)的screens-PM06：compare_PM_C3_01.html、screen_PM_C1_01_1440.png、screen_PM_C1_02_1440.png、screen_PM_C1_04_1440.png、screen_PM_C1_05_1440.png、screen_PM_C2_01_1440.png、screen_PM_C2_03_1440.png、screen_PM_C3_01_1440.png、screen_PM_C3_01_1440_legacy_chat.png、screen_PM_C4_01_1440.png、screen_PM_C5_01_390_cockpit.png、screen_PM_C5_01_390_project.png
- [38834451757](https://github.com/riser-lang/paw-local-dev/actions/runs/38834451757)的screens-PM06：compare_PM_C3_01.html、screen_PM_C1_01_1440.png、screen_PM_C1_02_1440.png、screen_PM_C1_04_1440.png、screen_PM_C1_05_1440.png、screen_PM_C2_01_1440.png、screen_PM_C2_03_1440.png、screen_PM_C3_01_1440.png、screen_PM_C3_01_1440_legacy_chat.png、screen_PM_C4_01_1440.png、screen_PM_C5_01_390_cockpit.png、screen_PM_C5_01_390_project.png
有界面Chromium打开真实本地网址。对照HTML只并排展示本次真实的旧助手与驾驶舱原始PNG，原PNG没有改动；分别由c.screenshot记录实际页面URL。已人工查看桌面与手机图。
本地补充检查使用系统Edge，隔离源程序数据目录，验证实际批量操作、手机正文可见、真实助手输入和侧栏导航；这不能代替云端安装包验收。本地下载版Chromium启动失败（spawn UNKNOWN），没有把该失败写成PASS。

## 5. 风险与未确认
- 依赖PR #5的报告提交abaf89a复跑出现PM-B3-05启动失败（push54/55 RED、PR55/55 GREEN），未进入该例功能检查；内部启动根因未确认，详见DONE-C-05补充。C-06本提交的两次完整验收均66/66 GREEN，不用这一事实掩盖依赖分支的独立失败。
- 没有真实模型/真实用户资料验收；NoKey没有生成虚构建议。C-08建议、C-09健康分析、C-10考题不在本任务中，考题只校验指纹未读取或使用。
- 进度比例、项目起始日及缺失里程碑日期保持null。已批准待办按执行里程碑展示，不从文字估算实际开工时间。
- 保留旧界面的资料版本/覆盖/派生说明补充区；这些是实际功能入口，未改旧压缩React资源。历史B7-03精确15秒超时根因仍未确认，各次耗时保留在云端日志。
- PR #7继续叠加本任务；发布仍等待C-07固定停点后的审核人放行。

## 6. 大白话摘要
首页集中显示要决定的事、项目、建议区、输入去向和本地状态。
项目档案可以看来源、历史和撤销；缺日期就明确写待补。
采用交付的纸面设计，手机改成竖向时间线，文字完整可见。
所有操作连接本地数据与审核链，字体和绘图库随程序安装。
云端各次红绿结果均保留，最终结果见上述完整报告。
