# CUSTOM GPT BRAIN INTERFACE PLAN V1
> Version: 1.0.0
> Created: 2026-08-17
> Status: DESIGN_ONLY — not deployed
> Gate: GATE 9

## 1. DESIGN PRINCIPLE

- 提供少量高级 Actions，不无限增加低层 action
- 每个 Action 有明确的安全边界
- Custom GPT 是 Correction Authority + Knowledge Keeper
- confirmed Knowledge 仍必须用户确认 gate

## 2. TARGET ACTIONS

### 2.1 getProjectBrain

**Purpose**: 获取项目完整 Brain context
**Method**: GET
**Endpoint**: `/v1/brain/projects/{project_id}`
**Auth**: Bearer token (PAW_OPS_API_KEY)

**Response Fields**:
- project: 项目基本信息
- open_todos: 未完成 todo 列表
- recent_done: 最近完成 todo
- draft_knowledge: 草稿知识
- confirmed_knowledge: 已确认知识
- knowledge_source_summary: 来源汇总 (link_total, unique_source_total)
- recent_daily: 最近日志
- recent_capture: 最近捕获
- candidate_summary: 候选项汇总
- known_conflicts: 已知冲突
- open_questions: 待解决问题
- latest_activity: 最新活动

**Safety**: READ_ONLY, no canonical write

### 2.2 reviewWorkspaceCandidates

**Purpose**: 审查 pending candidates
**Method**: GET
**Endpoint**: `/v1/brain/candidates?status=pending`
**Auth**: Bearer token

**Response Fields**:
- candidates: 候选项列表 (id, type, statement, confidence, inference_level, recommended_action)
- total: 总数
- pending: pending 数量

**Safety**: READ_ONLY

### 2.3 compileKnowledge

**Purpose**: 编译 Knowledge Revision
**Method**: POST
**Endpoint**: `/v1/brain/candidates/{candidate_id}/decision`
**Auth**: Bearer token

**Request**:
```json
{
  "decision": "accept",
  "reviewed_by": "custom_gpt",
  "notes": "Compiled from source X and Y"
}
```

**Response** (staging):
```json
{
  "candidate_id": "...",
  "decision": "accept",
  "status": "accepted_for_future_apply",
  "canonical_applied": false,
  "message": "Staging mode: decision recorded but NOT applied"
}
```

**Safety**: Staging — DO_NOT_APPLY_CANONICAL. Future: accepted candidates go to user confirmation queue.

### 2.4 applyWorkspaceDecision

**Purpose**: 请求 approved canonical change
**Method**: POST (future)
**Endpoint**: `/v1/workspace/apply` (existing workspace API)
**Auth**: Bearer token + user confirmation

**Flow**:
1. Custom GPT reviews candidate
2. Custom GPT accepts/modifies candidate
3. Custom GPT requests apply
4. User confirms
5. Workspace applies change
6. Candidate status → applied

**Safety**: Requires user confirmation gate. No auto-apply.

### 2.5 correctWorkspaceInterpretation

**Purpose**: 纠正 Agnes 的理解错误
**Method**: POST (future)
**Endpoint**: `/v1/brain/corrections`
**Auth**: Bearer token

**Request**:
```json
{
  "candidate_id": "...",
  "correction": "Agnes misinterpreted 'time adjustment' as 'process adjustment'",
  "corrected_interpretation": "User meant schedule time, not workflow process",
  "reviewed_by": "custom_gpt"
}
```

**Safety**: Creates correction record. Does not auto-modify candidate. Goes to user review.

### 2.6 confirmKnowledge

**Purpose**: 确认 Knowledge Revision
**Method**: POST (future)
**Endpoint**: `/v1/workspace/knowledge/{page_id}/confirm`
**Auth**: Bearer token + user confirmation

**Flow**:
1. Custom GPT compiles knowledge revision
2. Custom GPT requests confirmation
3. User confirms
4. Knowledge status → current
5. confirmed_revision_no updated

**Safety**: Knowledge confirmation ALWAYS requires user gate. No auto-confirm.

## 3. CUSTOM GPT AUTHORITY MATRIX

| Action | Can Review | Can Modify | Can Reject | Can Apply | User Gate Required |
|--------|-----------|-----------|-----------|----------|-------------------|
| Candidate | YES | YES | YES | NO (staging) | YES (for apply) |
| Knowledge Revision | YES | YES | YES | NO | YES (for confirm) |
| Project Update | YES | YES | YES | NO | YES |
| Todo | YES | YES | YES | NO | YES |
| Fact | YES | YES | YES | NO | YES |
| Conflict | YES | YES | YES | NO | YES |

### Key Rules
- Custom GPT CAN: review, modify, reject candidates
- Custom GPT CAN: compile Knowledge Revision
- Custom GPT CAN: request approved canonical change
- Custom GPT CANNOT: auto-apply without user confirmation
- Custom GPT CANNOT: confirm Knowledge without user gate
- User is final authority for all canonical writes

## 4. ACTION PROLIFERATION PREVENTION

### Principle
- Do NOT add low-level actions for every workspace operation
- High-level actions only
- If a new need arises, check if existing actions can cover it

### Current Action Set (Complete)
1. getProjectBrain — project context
2. reviewWorkspaceCandidates — candidate review
3. compileKnowledge — knowledge compilation
4. applyWorkspaceDecision — canonical change request
5. correctWorkspaceInterpretation — correction
6. confirmKnowledge — knowledge confirmation

### Future Action Addition Criteria
- Must solve a recurring need not covered by existing actions
- Must be high-level (not a raw CRUD operation)
- Must have clear safety boundary
- Must be approved by user

## 5. END-TO-END FLOW

```
WPS / Local / Capture
  ↓
Source / Raw Input
  ↓
Agnes Analysis (analysis_pass → review_pass)
  ↓
Candidate (pending)
  ↓
Custom GPT: reviewWorkspaceCandidates
  ↓
Custom GPT: correctWorkspaceInterpretation (if needed)
  ↓
Custom GPT: compileKnowledge (if knowledge)
  ↓
Custom GPT: applyWorkspaceDecision
  ↓
User Confirmation Gate
  ↓
Canonical Workspace (applied)
  ↓
Candidate status → applied
  ↓
getProjectBrain (context refresh)
  ↓
Next Action
```

## 6. SAFETY INVARIANTS

- No action auto-applies canonical changes
- All canonical writes require user confirmation
- Knowledge confirmation always has user gate
- Custom GPT cannot bypass user authority
- Agnes cannot bypass Custom GPT review
- OpenClaw 18789 never directly exposed to GPT
- All actions go through paw-actions Brain Gateway
- AUTO_WRITE_ENABLED = False