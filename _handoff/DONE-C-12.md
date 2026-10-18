# C-12 完成报告

## 分支与提交

- PR：#14，`codex/C-12` → `main`。
- 最终代码提交：`9fe212bb6e058f49853eec89a179206019c800f1`。
- 验收用例通过 `git mv` 激活，内容未改；`git hash-object tests/required_cases_C-12.json` 返回 `1ec5e9e9fa8b92756e301d019bb73df48327988a`。
- 本地：`python tests/paw_lint.py` 为 `lint clean`；`python -m pytest tests/unit -q` 为 `177 passed`。前端 bundle 通过 `node --check`。
- 最新完整工作流：push 验证 run `39145301396` GREEN；PR 验证 run `39145305531` GREEN。push 单测 `39145301397`、PR 单测 `39145305535`、PR 安装包构建 `39145305547` 均 GREEN。

## 问题根因与证据

1. **PM-U1-01 / PM-U1-03：新版目标未显示，助手把进度当目标。** 方案 XLSX 的结构化表格单元格没有进入扫描分析提示；项目视图也没有读取仍待审核的目标候选，左侧【项目】默认入口留在旧版详情。现在将结构化单元格纳入扫描提示、在项目视图中读取待确认目标，并将入口直接路由到新版档案。push PM12 日志记录候选目标 `40`、前值 `30`、生效日期 `2026-10-08`、来源 `青禾计划_v2.docx`；页面证据的目标、日期、待确认文案和“进度不当目标”四项均为 true。PM-U1-03 使用助手提问并检查 v2 来源，通过。
2. **PM-U1-02：周报风险没有出现在“当前最大问题”。** 风险只保留在待审核候选/评估中，项目说明器只取正式风险字段，因此空字段文案覆盖了已有待确认风险。现在项目视图和说明器同时读取当前风险与待确认风险，并将后者标注“待确认”。PM-U1-02 通过。
3. **PM-U2-01：扫描资料详情没有正文。** 扫描登记记录了本地路径和内容哈希，但详情 API 只返回资料登记行，未回读本地文本或扫描提取结果。新增本地扫描正文读取：md/txt 直接读取，Office/PDF 从扫描提取缓存读取并核对原件哈希。PM12 日志显示 docx 与 markdown 来源均找到、正文长度各 31，正文匹配和页面渲染检查通过。
4. **PM-U2-02：知识详情接口遇到旧版内容形状时失败。** 旧修订的 `content_json` 可能是无效 JSON 或 JSON 标量，而详情响应模型要求对象，导致响应校验错误。读取时将旧内容保存在稳定的 `content` 字段，并在路由边界做明确校验、返回不含正文或路径的错误诊断。单元测试覆盖形状转换；PM-U2-02 详情及投影接口通过。
5. **PM-U3-01：内部枚举和英文标题进入用户界面/模型上下文。** 审核卡片沿用旧摘要中的英文 `project update`；助手证据块直接输出英文层级、权威和时效枚举。增加标题中文兜底，并把发送给模型的枚举替换为中文标签。PM-U3-01 通过；最终完整摘要列出的必需用例全 PASS。
6. **PM-U3-02：数据/指标候选没有完整的补充后批准路径。** 候选字段嵌套在 `payload` 中，前端没有把候选类型识别为可处理数据卡，也未在批准时先保存补充字段。现在从包装中读取字段、提供标题和项目名称输入，并在批准前保存用户填写内容。最终 PM12 中输入框检查与批准流程通过。中间一次 RED 是 Playwright 用非精确文本定位“已提交”同时命中两条状态提示；断言改为匹配精确状态文案后通过。
7. **PM-U4-01：自然语言建议被要求提供结构化证据标记。** 真实模型可能只返回普通句子，旧清理器会因找不到结构化映射而拒绝答案。新增按检索证据重合度校验自然语言句子、为建议附来源的路径，并增加不带结构化标记的通用 mock 模式。PM-U4-01 通过。
8. **安装使用说明：缺少 Error 5 的处理建议。** 在安装故障常见问题中补充管理员启动和安全软件信任说明；B1 文档一致性用例通过。

## 中间 RED 与修复记录

- `9ba6495` 的完整 push/PR 验证因 PM-U3-01 英文卡片标题、PM-U3-02 数据卡交互失败。根因分别是旧版英文标题未过滤和指标补充/批准路径不完整。
- `3389660` 修复上述产品问题；PM-U3-01 通过，PM-U3-02 的补充与批准已执行，但测试在状态提示上触发 strict-mode 歧义（两条文案都包含“已提交”）。
- `9fe212b` 将测试改成精确匹配完成状态。最终 push/PR 验证均 GREEN，完整报告为下方原样粘贴的 `summary.md`。

## real-model 手动运行

- 已新增 `.github/workflows/real-model.yml`。工作流内配置仅从 secret `PAW_TEST_API_KEY` 读取 key，从 `/v1/models` 解析 DeepSeek V4.1 Flash 的实际 ID；本报告不记录或读取 key。
- 在 `codex/C-12` 上尝试手动 dispatch：`gh workflow run real-model.yml --ref codex/C-12`，GitHub 返回 `HTTP 404: workflow real-model.yml not found on the default branch`。`gh workflow list` 也未列出此工作流。GitHub 当前未将 PR 分支的 workflow 注册为可手动调度项，因此没有产生 run 编号，`/v1/models` 未被请求，模型 ID 未能确认，real-model 验收未运行。没有绕过该限制去访问模型服务。

## 最终 push 验证 `summary.md`（原文）

# Pipeline verdict for 9fe212bb6e058f49853eec89a179206019c800f1

Required cases: 91 | PASS 91 | FAIL 0 | NOT RUN 0

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
| NEGATIVE-CONTROL | NC-PM-U1 | FAIL | current_target_visible | mock-llm |
| NEGATIVE-CONTROL | NC-PM-U2 | FAIL | source_body_readable | mock-llm |
| NEGATIVE-CONTROL | NC-PM-U3 | FAIL | no_internal_terms_visible | mock-llm |
| NEGATIVE-CONTROL | NC-PM-U4 | FAIL | advice_not_refused | mock-llm |
| NEGATIVE-CONTROL | NC-01 | FAIL | project_update_committed | mock-llm |
| NEGATIVE-CONTROL | NC-02 | FAIL | explainer_fields_filled | mock-llm |
| NEGATIVE-CONTROL | NC-03 | FAIL | environment_clean | mock-llm |
| NEGATIVE-CONTROL | NC-PM-GOV | FAIL | formal_fields_unchanged_after_cruise | mock-llm |
| NEGATIVE-CONTROL | NC-PM-S1 | FAIL | scanned_file_listed_in_sources | mock-llm |
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
| PM09 | PM-E3-01 | PASS |  | mock-llm |
| PM09 | PM-E4-01 | PASS |  | mock-llm |
| PM09 | PM-S1-01 | PASS |  | mock-llm |
| PM09 | PM-S1-02 | PASS |  | mock-llm |
| PM09 | PM-S1-03 | PASS |  | mock-llm |
| PM10 | PM-F1-01 | PASS |  | mock-llm |
| PM10 | PM-F2-01 | PASS |  | mock-llm |
| PM10 | PM-F3-01 | PASS |  | mock-llm |
| PM12 | PM-U1-01 | PASS |  | mock-llm |
| PM12 | PM-U1-02 | PASS |  | mock-llm |
| PM12 | PM-U1-03 | PASS |  | mock-llm |
| PM12 | PM-U2-01 | PASS |  | mock-llm |
| PM12 | PM-U2-02 | PASS |  | mock-llm |
| PM12 | PM-U3-01 | PASS |  | mock-llm |
| PM12 | PM-U3-02 | PASS |  | mock-llm |
| PM12 | PM-U4-01 | PASS |  | mock-llm |

## No problems

VERDICT: GREEN