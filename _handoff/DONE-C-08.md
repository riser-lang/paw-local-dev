# DONE-C-08 — 有依据的建议与逐句回答校验

2026-10-16；PR #8。起点为 main 的8684a31（v2.4.0-rc1），没有未合并前置PR。后续C-09/C-10叠加，未经审核不自行合并。

## 1. 子项对照
- 完成：REVIEW-C-05_07的四项P1、版本统一和RC草稿。详见DONE-v2.4.0-rc1.md，包含全部历史RED、冻结tag全量70/70 GREEN、安装包SHA256及前后原始截图。使用说明未改，repair_v238模块名保留，Release仍为草稿。
- 完成：每项目的缺口、风险、下一步建议经project_advice契约校验，独立保存到pm_suggestion。来源必须当前有效，quote逐字来自原文；无Key/无资料不生成。非法schema丢弃并记录，不覆盖已有合格建议。
- 完成：首页与项目页使用真实本地建议，带AI建议标签、底色、类别和可打开的来源。网页读取不调用模型；旧事实、历史和撤销保留。沿用已迁移设计，本次没有再次读取/改写设计导出包，也未引入字体或第三方库。
- 完成：转为待办候选后仍需用户核准，正式写入走ReviewDecision→ApprovedChange→Committer。重复点击幂等，候选状态与建议关联；点击按钮不计为已采纳。
- 完成：建议问答的事实带引用，推断分在“建议（基于以上依据的推断）”；逐句过滤无依据数字、人物指派及冒充既有决定的建议，不因此丢弃其他事实和合格推断。保留原事实门禁并补充引用结构检查；原有15秒标准未改。
- 完成：审核人组合用例按原_note拆为5+2+3，只有E1/E2五项在本任务激活；逐项与8ff20a22原blob比较完全一致。考题文件、paw_case/verdict/lint及既有required_cases未改。

## 2. 改动清单
- backend/pm/{suggestions,answer_advice,semantic,storage,cockpit,router}.py与contracts/project_advice.json：派生建议、引用/实体校验、缓存、候选采纳、本地接口及视图。
- backend/runtime_core/{project_analyzer,scheduler}.py：按项目逐项生成与轮转，保留每轮总预算；模型调用前释放写事务，避免新建议调用继承解释器写锁。
- backend/brain/api/local_agy_facade.py与context/agy_answer_contract.py：主答/单次修复答都逐句处理，事实引用结构更严格，展示引用标题。
- frontend/pm-src与frontend/dist/pm：可选本地视图字段、建议区、采纳回调、来源弹层。鼠标悬停后的点击维持弹层打开，触屏仍可切换。
- scripts/mock_{grounded_answer,openai_server}.py：只从请求资料摘录的通用建议契约；故障模型按通用故障类型注入，不判断题号/题文，不读取考试文件。
- tests/run_pm_advice_tests.py、E2E分发和CI矩阵：真实安装/扫描/模型/治理验收；新增纯逻辑及巡航预算/无写锁单测。

## 3. 测试
功能提交：87e70c9f133c812aba1c61ba360e60a570c8f15f。报告提交会另触发push与PR，两次均保留；其头部运行结果后续补充在PR描述，下述静态判定只对应明确列出的提交。

- unit push：[38906341753](https://github.com/riser-lang/paw-local-dev/actions/runs/38906341753)，GREEN。
- unit PR：[38906356256](https://github.com/riser-lang/paw-local-dev/actions/runs/38906356256)，GREEN。
- 安装包验收 push：[38906341763](https://github.com/riser-lang/paw-local-dev/actions/runs/38906341763)，GREEN。
- 安装包验收 PR：[38906356325](https://github.com/riser-lang/paw-local-dev/actions/runs/38906356325)，GREEN。
- 独立打包 PR：[38906356462](https://github.com/riser-lang/paw-local-dev/actions/runs/38906356462)，GREEN。

本地命令：`python -m pytest tests/unit -q` → 123 passed；`python tests/paw_lint.py` → lint clean；`node frontend/pm-src/build.mjs`成功。隔离源码环境用假Key、本机mock与虚构DOCX实际完成扫描、建议生成、非法schema丢弃、两类实体故障逐句删除、来源打开、1440/390有界面Edge与候选核准。源码探针不冒充云端安装包验收。

新增用例：PM-E1-01、02、03、PM-E2-01、02，均为审核人预定文字，不是自拟proposed项。两次PM08各为5/5；逐项检查数为25、19、24、7、13。每例均fresh_install，SQL只读回核验，没有写AI/候选/审核/正式记录造结果。

15秒标准未改；真实安装包回答耗时（秒）及模型/搜索/上下文计数如下：

**push**
```text
Assistant answer B7-01 elapsed_seconds=0.1423 timings={'model_calls': 1, 'model_seconds': 0.0372, 'prompt_bytes': 1057, 'search_seconds': 0.0, 'context_seconds': 0.0051, 'evidence_count': 0, 'context_bytes': 3334, 'total_seconds': 0.1292}
Assistant answer B7-02 elapsed_seconds=0.0854 timings={'model_calls': 0, 'model_seconds': 0.0, 'prompt_bytes': 0, 'search_seconds': 0.0, 'context_seconds': 0.0053, 'evidence_count': 0, 'context_bytes': 3358, 'total_seconds': 0.0732}
Assistant answer B7-03 elapsed_seconds=0.1182 timings={'model_calls': 1, 'model_seconds': 0.0144, 'prompt_bytes': 4793, 'search_seconds': 0.0, 'context_seconds': 0.0043, 'evidence_count': 1, 'context_bytes': 4423, 'total_seconds': 0.0939}
Assistant answer PM-E2-01 elapsed_seconds=0.1652 timings={'model_calls': 1, 'model_seconds': 0.0146, 'prompt_bytes': 7535, 'search_seconds': 0.0, 'context_seconds': 0.0054, 'evidence_count': 9, 'context_bytes': 7039, 'total_seconds': 0.1282}
Assistant answer PM-E2-02 elapsed_seconds=0.1822 timings={'model_calls': 1, 'model_seconds': 0.03, 'prompt_bytes': 7535, 'search_seconds': 0.0, 'context_seconds': 0.0053, 'evidence_count': 9, 'context_bytes': 7039, 'total_seconds': 0.17}
```

**pr**
```text
Assistant answer B7-01 elapsed_seconds=0.2080 timings={'model_calls': 1, 'model_seconds': 0.0554, 'prompt_bytes': 1057, 'search_seconds': 0.0, 'context_seconds': 0.0047, 'evidence_count': 0, 'context_bytes': 3334, 'total_seconds': 0.1957}
Assistant answer B7-02 elapsed_seconds=0.1331 timings={'model_calls': 0, 'model_seconds': 0.0, 'prompt_bytes': 0, 'search_seconds': 0.0, 'context_seconds': 0.0052, 'evidence_count': 0, 'context_bytes': 3358, 'total_seconds': 0.1211}
Assistant answer B7-03 elapsed_seconds=0.1906 timings={'model_calls': 1, 'model_seconds': 0.0227, 'prompt_bytes': 4793, 'search_seconds': 0.0, 'context_seconds': 0.0042, 'evidence_count': 1, 'context_bytes': 4423, 'total_seconds': 0.1684}
Assistant answer PM-E2-01 elapsed_seconds=0.1416 timings={'model_calls': 1, 'model_seconds': 0.0251, 'prompt_bytes': 7535, 'search_seconds': 0.0, 'context_seconds': 0.0053, 'evidence_count': 9, 'context_bytes': 7039, 'total_seconds': 0.1289}
Assistant answer PM-E2-02 elapsed_seconds=0.1273 timings={'model_calls': 1, 'model_seconds': 0.0287, 'prompt_bytes': 7535, 'search_seconds': 0.0, 'context_seconds': 0.0053, 'evidence_count': 9, 'context_bytes': 7039, 'total_seconds': 0.1101}
```


### push 原始summary.md全文

```text
# Pipeline verdict for 87e70c9f133c812aba1c61ba360e60a570c8f15f

Required cases: 75 | PASS 75 | FAIL 0 | NOT RUN 0

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
| PM07 | PM-A1-01 | PASS |  | mock-llm |
| PM07 | PM-A2-01 | PASS |  | mock-llm |
| PM07 | PM-A4-01 | PASS |  | mock-llm |
| PM07 | PM-UP-01 | PASS |  | mock-llm |
| PM08 | PM-E1-01 | PASS |  | mock-llm |
| PM08 | PM-E1-02 | PASS |  | mock-llm |
| PM08 | PM-E1-03 | PASS |  | mock-llm |
| PM08 | PM-E2-01 | PASS |  | mock-llm |
| PM08 | PM-E2-02 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN
```

### pr 原始summary.md全文

```text
# Pipeline verdict for a6fdefcada74872164fbca6e232a437f353a3f60

Required cases: 75 | PASS 75 | FAIL 0 | NOT RUN 0

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
| PM07 | PM-A1-01 | PASS |  | mock-llm |
| PM07 | PM-A2-01 | PASS |  | mock-llm |
| PM07 | PM-A4-01 | PASS |  | mock-llm |
| PM07 | PM-UP-01 | PASS |  | mock-llm |
| PM08 | PM-E1-01 | PASS |  | mock-llm |
| PM08 | PM-E1-02 | PASS |  | mock-llm |
| PM08 | PM-E1-03 | PASS |  | mock-llm |
| PM08 | PM-E2-01 | PASS |  | mock-llm |
| PM08 | PM-E2-02 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN
```

## 4. 截图
- P1前后原图与并排HTML：_handoff/visuals/v2.4.0-rc1/{before,after}-{1440,390}.png及comparison.html；来源与哈希核对见RC报告。
- 本任务两次安装包screens-PM08产物：screen_PM_E1_01_1440.png、screen_PM_E1_01_390.png；E1_02/E1_03同名后缀各两张，均由c.screenshot记录真实项目页网址。已查看PR运行的E1_02桌面/手机版原图，建议与事实独立呈现，无整页横滚。

## 5. 风险与未确认
- 项目建议和语义判断的质量仍取决于用户模型；schema、逐字依据和引用有效性为确定性门禁，建议不自动成为事实。
- 人名过滤检查人物指派/责任等语境，不能宣称对所有自然语言专名完成通用NER。数值按含单位token核验，版本号不授权同数字的期限。新增前缀规则曾误把“由李明”视作完整名字，单测暴露后修解析，原测试未改；最终123项通过。
- 历史B7-03、PM-B3-01及启动不稳定记录的精确根因没有因本次通过而宣布查明。RC报告记录所有已观察失败及诊断范围；本次保持相同门槛、两事件运行和耗时证据。
- C-09/C-10与v2.5.0尚未完成，本报告不代表审核通过或正式上线。

## 6. 大白话摘要
建议现在能从资料生成，也能看出依据在哪里。
建议与正式记录分开，只有你核准后才写成待办。
助手的一句坏建议会被删除，其他有依据的回答仍保留。
RC1只做了草稿，尚未正式发布；继续C-09、C-10。
