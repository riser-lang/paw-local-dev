# ROLLBACK PLAN V1
> Version: 1.0.0
> Created: 2026-08-17
> Status: DESIGN_ONLY — not executed
> Scope: Covers all GATE 1-10 components

## 1. ROLLBACK TRIGGERS

Rollback is triggered when:
- Any GATE fails and cannot be resolved
- Production DB counts change unexpectedly
- Protected Core is impacted
- paw-actions or OpenClaw crashes after integration
- User explicitly requests rollback
- AUTO_WRITE is triggered unexpectedly

## 2. ROLLBACK SCOPE

### 2.1 OpenClaw Runtime Rollback (GATE 1)

**Trigger**: Gateway fails to start after unit reconciliation

**Steps**:
```bash
# 1. Restore original systemd unit
cp /etc/systemd/system/openclaw-gateway.service.bak.* \
   /etc/systemd/system/openclaw-gateway.service

# 2. Restore override directory if needed
cp -r /etc/systemd/system/openclaw-gateway.service.d.bak.* \
  /etc/systemd/system/openclaw-gateway.service.d/

# 3. Reload and restart
systemctl daemon-reload
systemctl restart openclaw-gateway

# 4. Verify
systemctl show openclaw-gateway --property=MainPID
ss -tlnp | grep 18789
```

**Verification**:
- Gateway PID stable
- Port 18789 listening
- Agnes sessions functional
- Provider/model unchanged

**Rollback condition**: If gateway still fails after restore, escalate to manual recovery.

### 2.2 Persona Rollback (GATE 2)

**Trigger**: SOUL.md or AGENTS.md merge causes identity loss or behavior change

**Steps**:
```bash
# 1. Restore SOUL.md
cp /root/.openclaw/workspace-agnes/SOUL.md.bak.* \
   /root/.openclaw/workspace-agnes/SOUL.md

# 2. Restore AGENTS.md
cp /root/.openclaw/workspace-agnes/AGENTS.md.bak.* \
   /root/.openclaw/workspace-agnes/AGENTS.md

# 3. Restore other workspace files if modified
cp /root/.openclaw/workspace-agnes/IDENTITY.md.bak.* \
   /root/.openclaw/workspace-agnes/IDENTITY.md 2>/dev/null
cp /root/.openclaw/workspace-agnes/USER.md.bak.* \
   /root/.openclaw/workspace-agnes/USER.md 2>/dev/null
cp /root/.openclaw/workspace-agnes/TOOLS.md.bak.* \
   /root/.openclaw/workspace-agnes/TOOLS.md 2>/dev/null

# 4. Verify Agnes session picks up restored files
# (OpenClaw reads workspace files on session start)
```

**Verification**:
- SOUL.md contains original Server Ops identity
- AGENTS.md contains original 10-step flow
- No PERSONAL_WORKSPACE_ROLE section (reverted)
- No MEMORY_BOUNDARY section (reverted)
- Protected Core rules intact

### 2.3 Migration Rollback (GATE 3)

**Trigger**: Migration causes data loss, schema corruption, or v4 table impact

**Steps**:
```bash
# 1. Connect to production DB
sqlite3 /var/lib/personal-workspace/workspace.db

# 2. Execute rollback SQL
-- Only drop Brain tables, NEVER touch v4 tables
DROP INDEX IF EXISTS idx_candidate_analysis_run;
DROP INDEX IF EXISTS idx_candidate_status;
DROP INDEX IF EXISTS idx_candidate_type;
DROP TABLE IF EXISTS candidate;
DROP TABLE IF EXISTS analysis_run;

# 3. Reset schema version if needed
PRAGMA user_version = 4;

# 4. Verify v4 tables intact
SELECT COUNT(*) FROM project;   -- Expected: 10
SELECT COUNT(*) FROM todo;      -- Expected: 24
SELECT COUNT(*) FROM knowledge_page;      -- Expected: 16
SELECT COUNT(*) FROM knowledge_revision;  -- Expected: 22
SELECT COUNT(*) FROM knowledge_source_link; -- Expected: 92
```

**Verification**:
- analysis_run table does not exist
- candidate table does not exist
- All v4 tables exist with correct counts
- No data loss in v4 tables
- PRAGMA integrity_check = ok

**Critical**: Rollback SQL ONLY drops Brain tables. NEVER touches:
- project
- todo
- knowledge_page
- knowledge_revision
- knowledge_source_link
- daily
- capture
- wiki_page

### 2.4 Router Rollback (GATE 4)

**Trigger**: Brain router causes paw-actions crash or route conflict

**Steps**:
```python
# 1. Remove router import from main.py
# Edit /opt/paw-actions/main.py
# Remove: from brain.integration.router_staging import router
# Remove: app.include_router(router)

# 2. Restart paw-actions
systemctl restart paw-actions

# 3. Verify
systemctl show paw-actions --property=MainPID
curl -s http://127.0.0.1:8787/v1/workspace/health
```

**Verification**:
- paw-actions running
- Port 8787 listening
- /v1/brain/* routes return 404
- /v1/workspace/* routes functional
- No import error in paw-actions logs

### 2.5 AgnesRuntimeAdapter Rollback (GATE 5)

**Trigger**: Runtime adapter causes OpenClaw session issues or model errors

**Steps**:
```python
# 1. Set DRY_RUN = True in agnes_runtime.py
# Edit /opt/personal-workspace/brain/runtime/agnes_runtime.py
# Set: DRY_RUN = True

# 2. Restart paw-actions if router is mounted
systemctl restart paw-actions

# 3. Verify
# Brain chat returns DRY_RUN responses
# No real OpenClaw API calls from Brain
```

**Verification**:
- DRY_RUN = True
- No real model calls
- Brain chat returns staging responses
- OpenClaw sessions unaffected

### 2.6 Chat Gateway Rollback (GATE 6)

**Trigger**: Chat gateway exposes OpenClaw or causes auth issues

**Steps**:
```python
# 1. Remove chat routes from router_staging.py
# Or remove entire router from main.py (see 2.4)

# 2. Verify OpenClaw 18789 is loopback-only
ss -tlnp | grep 18789
# Expected: 127.0.0.1:18789 only
```

**Verification**:
- /v1/brain/chat/* returns 404
- OpenClaw 18789 loopback-only
- No external exposure

### 2.7 Calibration Rollback (GATE 7)

**Trigger**: Calibration quality gates fail

**Steps**:
```python
# 1. Set DRY_RUN = True (see 2.5)
# 2. Disable candidate generation
# 3. Review failed cases
# 4. Adjust model/prompt if needed
# 5. Re-run calibration
```

**Verification**:
- No candidates generated
- DRY_RUN active
- Failed cases documented

### 2.8 Observation Rollback (GATE 8)

**Trigger**: Critical errors during 24h observation

**Steps**:
```python
# 1. Stop Brain router (see 2.4)
# 2. Set DRY_RUN = True (see 2.5)
# 3. Review observation logs
# 4. Document issues
# 5. Fix and re-deploy
```

### 2.9 Custom GPT Actions Rollback (GATE 9)

**Trigger**: GPT Actions cause issues or incorrect canonical changes

**Steps**:
- Remove Brain Actions from Custom GPT configuration
- Verify no pending candidate decisions are lost
- Review any applied changes

### 2.10 AUTO_WRITE Rollback (GATE 10)

**Trigger**: AUTO_WRITE causes incorrect canonical writes

**Steps**:
```python
# 1. Immediately set AUTO_WRITE_ENABLED = False
# 2. Review all auto-applied candidates
# 3. Restore DB from backup if needed
# 4. Audit all changes made by AUTO_WRITE
# 5. Document and report
```

**Verification**:
- AUTO_WRITE_ENABLED = False
- All auto-applied candidates reviewed
- DB restored if needed
- No further auto-writes

## 3. FULL ROLLBACK (ALL GATES)

If catastrophic failure requires full rollback:

1. Remove router from main.py (2.4)
2. Set DRY_RUN = True (2.5)
3. Drop Brain tables (2.3)
4. Restore SOUL.md/AGENTS.md (2.2)
5. Restore systemd unit (2.1)
6. Restart paw-actions
7. Restart OpenClaw gateway
8. Restore DB from backup if needed
9. Verify all production counts
10. Verify Protected Core

## 4. ROLLBACK VERIFICATION CHECKLIST

- [ ] paw-actions running (PID stable)
- [ ] OpenClaw gateway running (PID stable)
- [ ] Port 8787 listening
- [ ] Port 18789 listening (loopback)
- [ ] Port 8443 NO_LISTENER
- [ ] SSH/22 active
- [ ] Xray/443 active
- [ ] Tailscale active
- [ ] project count = 10
- [ ] todo count = 24
- [ ] knowledge_page count = 16
- [ ] knowledge_revision count = 22
- [ ] knowledge_source_link count = 92
- [ ] AUTO_WRITE_ENABLED = False
- [ ] /v1/brain/* routes removed (404)
- [ ] /v1/workspace/* routes functional
- [ ] SOUL.md original content restored
- [ ] AGENTS.md original content restored
- [ ] No Brain tables in DB
- [ ] PRAGMA integrity_check = ok

## 5. ROLLBACK SAFETY

- Rollback NEVER touches v4 production tables
- Rollback NEVER deletes business data
- Rollback NEVER modifies Protected Core
- Rollback NEVER leaves AUTO_WRITE enabled
- Rollback is logged and reported
- Rollback can be executed partially (per GATE) or fully