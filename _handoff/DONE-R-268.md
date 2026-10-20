# DONE-R-268：成品数据卡空字段的中文提示 + 成品行为回填源代码

## 1. 在当前成品里实测：数据卡留空点"接受"看到什么

用真机（安装版的 `/app/review` 数据卡，后端为 main 上的成品代码）分别测了两种留空：

| 场景 | 前端调用 | 实测结果 | 用户看到的原文 |
|---|---|---|---|
| 项目名称留空（标题有 `name` 兜底） | 成品点"接受"→ `editCandidate({...payload, title: title\|\|name})` | **HTTP 200，候选被 applied，记录写入且项目为空** | **没有任何提示**（只有成功提示"已提交，等待 AI 巡航。"） |
| 标题留空（`title` 与 `name` 都空） | 同上 | 走 `_normalize` 的名称检查 | 「标题或项目名称不能为空，请编辑草稿后重试。」（中文，但与项目名称那条不一致） |

结论：项目名称留空时**根本没有提示**——空项目被静默写入。按第 1 条要求只改后端返回的提示文字，不改成品包。

## 2. 修复：一句中文提示（只改后端）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `backend/repair_v238.py` | 新增 `DATA_CARD_INCOMPLETE = '请补充标题和项目名称后再接受'` 与 `data_card_missing_fields(target, operation, payload)`；在 `_normalize` 里、data/metric 的 `create` 分支上，标题（`title` 或 `name`）或项目（`project_name` 或 `project_id`）为空时抛这句中文 | `install()` 把 `GovernanceService.accept_and_commit_candidate` 换成这里的实现，`/accept` 与 `/edit` 都走它；提示经 `HTTPException(400, detail=…)` 回到前端，成品包用 `ApiError.message` 显示在审核详情里（`role="alert"`），不需要动包 |
| `tests/unit/test_data_card_validation.py`（新增） | 8 个用例 | 字段判定、精确文案、`_normalize` 抛错、完整卡放行 |

判定规则：`title` 缺失时用 `name`（卡片本来就用它填"标题"）；项目接受 `project_name` 或 `project_id`；只对 `data/metric` 的 `create` 生效，其它目标类型与操作不受影响。检查放在原来那条通用名称规则**之前**，所以"标题空"和"项目名称空"两种情况成品现在显示同一句中文。

### 改动后同场景实测（真机，同一接口）

```
accept（项目名称空）  -> HTTP 400  {"detail": "请补充标题和项目名称后再接受"}   用户看到：请补充标题和项目名称后再接受
edit  （项目名称空）  -> HTTP 400  {"detail": "请补充标题和项目名称后再接受"}
edit  （标题空）      -> HTTP 400  {"detail": "请补充标题和项目名称后再接受"}
edit  （标题+项目都空）-> HTTP 400  {"detail": "请补充标题和项目名称后再接受"}
edit  （补全后）      -> HTTP 200  applied（正常写入，项目已关联）
```

## 3. 把"待确认列表对 data 目标放行"补回源代码（只改源代码）

成品包里这一项的判定是 `!["data","metric"].includes(pC12TargetType(proposedChange))`：候选即使 `effectiveReviewState` 不是 `NEEDS_REVIEW`，只要 proposed target 是 data/metric 就仍然显示。`backend/runtime/App.tsx` 之前没有这条例外。现在补上：

- 新增 `proposalTargetTypeOf(value)` 与 `isDataTargetProposal(value)`（解析字符串/对象、再看 `original_payload.target_type`）；
- `ReviewPage` 的 `visible` 过滤加上 `&& !isDataTargetProposal(item.proposedChange)`；
- `ReviewDetailView` 里原来的内联计算改为调用同一个 helper，避免两处判定漂移。

**只改源代码**：成品包 `frontend/dist/assets/index-qIwUsXqw.js` 一个字没动（它本来就有这条行为，所以用户当前看到的已经是"数据卡可见"）。

## 4. 单测与本地检查

- 新增 `tests/unit/test_data_card_validation.py`（8 例）：`data_card_missing_fields` 的四种组合、非 data/create 不受影响、精确文案、`_normalize` 对"项目空/标题空"抛错、完整卡成功且解析出 `project_id`。
- `python -m pytest tests/unit -q` → **227 passed**；`python tests/paw_lint.py` → lint clean。

## 5. 真实模型（任务 3）

后端改了，重跑真实模型：运行 **`39357111534`**（commit `dc222fa`），结果 **GREEN，8/8**。扫描 12 个文件 **99.9 秒**（Phase 2 91.7 秒；18 次扫描调用、0 超时、1 次规则兜底），回答等待 60 秒未改。

| 题 | 耗时 | 结果 | 证据闸门 |
|---|---:|---|---|
| PM-U1-03 | 18.4 秒 | PASS | ok |
| PM-U4-01 | 23.8 秒 | PASS | ok |
| PM-S1-03 | 13.2 秒 | PASS | ok |
| X01 | 17.2 秒 | PASS | ok |
| X02 | 18.2 秒 | PASS | ok |
| X18 | 14.8 秒 | PASS | ok |
| X19 | 21.0 秒 | PASS | ok |
| X20 | 23.3 秒 | PASS | ok |

8 题全部 `answer_type=grounded`、`grounding.gate=ok`、`thinking_disabled=true`、`finish_reason=stop`，每题都在 60 秒内（4 题在 20 秒内）。

## 6. CI 与 PR

- PR：**#19**（`deepseek/R-268` → `main`），未合并、未打 tag。
- `deepseek/**` 不在 `.github/workflows/test.yml` 的 push 分支列表（只有 `codex/**`、`claude/**`、`gemini/**`），而任务禁止改该文件，所以 push 侧用 `gh workflow run test.yml --ref deepseek/R-268` 跑同一条完整流水线：
  - push 侧（workflow_dispatch）完整流水线 `39357092782` **GREEN**；push 单测 `39357044591` **GREEN**。
  - PR 侧完整流水线 `39357078338` **GREEN**；PR 单测 `39357078410` **GREEN**。
- 本 DONE 是纯文档提交，推送后会再触发一次 PR 流水线。

## 7. 规则遵守

- 未改审核人文件（`tests/paw_case.py`、`tests/paw_verdict.py`、`tests/paw_lint.py`、`tests/required_cases*.json`、`tests/pending/**`）；未改题目与判定；未改 `.github/workflows/test.yml`；未改成品包 `frontend/dist/**`。
- 未写死答案或文件名；未删除文件；未 force push；未合并；未打 tag；未改文件夹权限或系统设置。
- Key 只在 workflow secret 里，代码、日志、报告里都没有；报告下载目录（`.r268-runH` 等）未提交。

## 停止点

三项任务都已完成，没有需要审核人先决定才能继续的事项。成品包按要求没有动；如果之后决定重新生成 `/app`，第 3 节的源代码改动会自动生效。

执行者进度：R-268，PR #19；push CI 编号 39357092782 结果 GREEN、PR CI 编号 39357078338 结果 GREEN（push 单测 39357044591、PR 单测 39357078410 均 GREEN）；真实模型运行编号 39357111534；扫描用时 99.9 秒（Phase 2 91.7 秒，12 个文件）；8 题通过数 8/8（每题耗时 18.4 / 23.8 / 13.2 / 17.2 / 18.2 / 14.8 / 21.0 / 23.3 秒）；停下原因：无。
