# SERVER-BRAIN INTEGRATION PLAN V1
> Version: 1.0.0
> Created: 2026-08-17
> Status: DESIGN_ONLY — PREPARE ONLY, DO NOT DEPLOY
> Prerequisite: SERVER_BRAIN_FOUNDATION_CALIBRATED + AGNES_RUNTIME_BINDING_PREP_READY

## 1. ARCHITECTURE

```
Personal Workspace (SQLite)
  = 唯一长期 canonical memory

OpenClaw + Agnes
  = 本地信息处理 / 检索 / 抽取 / 推演
  = Candidate Generation
  = Server Ops

Custom GPT
  = Knowledge Keeper
  = Project Brain
  = Decision Partner
  = Correction Authority
  = Workspace Controller

Windows + WPS
  = 持续信息入口
```

### HARD RULE
Agnes 不得建立第二套业务 Knowledge Base。
OpenClaw memory 不得作为 canonical fact source。

## 2. GATE SEQUENCE

任何 Gate FAIL → 不得进入下一 Gate。

### GATE 0: Windows Source Pipeline FULL_CUTOVER_PASS
- **Owner**: WorkBuddy
- **Condition**: Windows Source Pipeline 完成最终 Gate，所有 source 数据完成 reconciliation
- **Verification**: source pipeline 产出完整、无 pending batch、无 unresolved conflict
- **Block**: 如果 Source Pipeline 未完成，所有后续 Gate 不得开始
- **Status**: PENDING (WORKBUDDY_LONG_TASK)

### GATE 1: OpenClaw Runtime Reconciliation
- **Owner**: Server Ops
- **Condition**: 统一 CLI 版本与生产版本，统一 systemd unit
- **Current State**:
  - CLI: 2026.6.9 (pnpm shim)
  - Production: 2026.7.1-2 (nvm v22.22.3)
  - Disk unit: 指向 2026.6.9 + node v22.22.2
  - Loaded ExecStart: 通过 override 指向 node v22.22.3 + openclaw gateway run --port 18789
- **Plan**: See `openclaw_runtime_plan_v1.md`
- **Verification**: CLI version == production version, systemd disk unit == loaded unit, gateway PID stable, port 18789 listening, Agnes sessions functional
- **Rollback**: See `rollback_plan_v1.md`

### GATE 2: Persona Merge
- **Owner**: Server Ops + Custom GPT
- **Condition**: 将 Brain persona/policy 内容合并到 `/root/.openclaw/workspace-agnes/`
- **Current State**: SOUL.md 已包含 Server Ops identity + ChatGPT relationship + 工作方式 + 权限边界
- **Plan**: See `persona_merge_plan_v1.md`
- **Verification**: SOUL.md 包含 PERSONAL_WORKSPACE_ROLE + MEMORY_BOUNDARY + DECISION_BOUNDARY; AGENTS.md 包含 workspace/analysis/extraction/simulation/candidate modes; 生产 Server Ops identity 不丢失
- **Rollback**: 恢复 SOUL.md/AGENTS.md 备份

### GATE 3: Candidate Migration
- **Owner**: Server Ops
- **Condition**: 将 Brain schema (v4 → v5) migration 应用到生产 workspace.db
- **Current State**: MIGRATION_STATUS = PREPARED_NOT_APPLIED
- **Plan**: See `migration_validator.py` + `rollback_plan_v1.md`
- **Verification**: analysis_run + candidate tables 存在, indexes 存在, FK valid, existing v4 tables untouched, ambiguity type supported
- **Rollback**: DROP TABLE analysis_run, candidate + DROP INDEX (See rollback SQL)

### GATE 4: Project Brain Route Integration
- **Owner**: Server Ops
- **Condition**: 将 staging router 挂载到 paw-actions production main.py
- **Current State**: router_staging.py 为 staging-only, 未 import 到 production
- **Plan**: See `router_staging.py` + `deployment_checklist_v1.md`
- **Verification**: /v1/brain/projects/{id} 返回完整 context, /v1/brain/candidates 返回 pending list, auth dependency 生效
- **Rollback**: 从 main.py 移除 router import

### GATE 5: AgnesRuntimeAdapter Binding
- **Owner**: Server Ops
- **Condition**: 将 AgnesRuntimeAdapter 从 DRY_RUN 切换为真实调用
- **Current State**: DRY_RUN = True (design-only)
- **Verification**: health_check pass, create_or_resume_session functional, send_message returns real response, analysis_pass + review_pass produce real candidates
- **Rollback**: DRY_RUN = True

### GATE 6: Chat Gateway Integration
- **Owner**: Server Ops
- **Condition**: Brain Chat Gateway 通过 paw-actions 暴露, OpenClaw 18789 保持 loopback-only
- **Verification**: /v1/brain/chat functional, session namespace = paw-brain:*, auth dependency 生效, OpenClaw 18789 不直接暴露
- **Rollback**: 移除 chat route

### GATE 7: Real Agnes Calibration
- **Owner**: Server Ops + Custom GPT
- **Condition**: 运行 20-case calibration dataset 对比模型质量
- **Plan**: See `calibration_deploy_plan_v1.md`
- **Batches**: A (5 simple), B (10 ambiguity/decision), C (5 conflict/binding)
- **Quality Gates**: classification_accuracy >= 90%, unsafe_inference = 0, plan_as_done = 0, idea_as_decision = 0, confirmed_conflict_overwrite = 0, project_misbinding <= 1, date_loss = 0
- **Rollback**: 回退到 DRY_RUN mode

### GATE 8: Candidate-only Production Observation
- **Owner**: Server Ops
- **Condition**: 24h candidate-only 观察, 不自动写 canonical
- **Observation**: analysis errors, candidate noise, duplicates, latency, token cost, Agnes runtime failures
- **AUTO_WRITE**: FALSE (必须保持)
- **Pass Criteria**: 无 critical error, candidate noise < threshold, latency acceptable
- **Rollback**: 停止 Brain route

### GATE 9: Custom GPT Brain Actions Integration
- **Owner**: Custom GPT + Server Ops
- **Condition**: 部署高级 Brain Actions 到 Custom GPT
- **Actions**: getProjectBrain, reviewWorkspaceCandidates, compileKnowledge, applyWorkspaceDecision, correctWorkspaceInterpretation, confirmKnowledge
- **Plan**: See `custom_gpt_brain_interface_v1.md`
- **Verification**: Actions schema valid, auth functional, no low-level action proliferation
- **Rollback**: 移除 GPT Actions

### GATE 10: Controlled AUTO_WRITE Evaluation
- **Owner**: User + Custom GPT + Server Ops
- **Condition**: 评估是否开启 AUTO_WRITE for specific candidate types
- **Current**: AUTO_WRITE_ENABLED = False
- **Requirement**: 另立任务, 明确 user confirmation gate, 限定 candidate type + scope
- **Rollback**: AUTO_WRITE_ENABLED = False

## 3. END-TO-END FUTURE FLOW

```
WPS / Local / Capture
  ↓
Source / Raw Input
  ↓
Agnes Analysis (analysis_pass → review_pass)
  ↓
Candidate (pending)
  ↓
Custom GPT Review
  ↓
User Decision
  ↓
Canonical Workspace (applied)
  ↓
Project Brain (context refresh)
  ↓
Next Action
```

## 4. TWO-PASS COST CONTROL

| Request Type | Pass Strategy |
|--------------|---------------|
| Simple extract | Single pass |
| 明确查询 | Single pass |
| Candidate generation | Two pass |
| Knowledge candidate | Two pass |
| Conflict | Two pass |
| Ambiguity | Two pass |

## 5. MODEL FALLBACK POLICY

- Primary: mistral/mistral-small-latest
- Fallback: Nemotron → GLM
- Calibration 未通过的模型: 不得用于 Candidate generation
- Calibration 最低质量 Gate 通过的模型: 可用于普通 chat/extract
- 不要改变当前 provider/model

## 6. SAFETY INVARIANTS

- AUTO_WRITE_ENABLED = False (本 Integration Package 全程)
- OpenClaw 18789 loopback-only
- Brain Chat 默认 READ_ONLY_TOOLS
- Server Ops mode 与 workspace mode 分离
- Session namespace: paw-brain:* (不复用 main)
- Personal Workspace = canonical, OpenClaw memory = runtime-only

## 7. BLOCKING GATES

1. WORKBUDDY_LONG_TASK (GATE 0)
2. OPENCLAW_RUNTIME_RECONCILIATION (GATE 1)

## 8. NEXT STEP

WAIT_FOR_WORKBUDDY_LONG_TASK → Source Reconciliation → Knowledge 人工确认 → 按 GATE 0-10 顺序集成