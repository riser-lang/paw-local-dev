# CLOUD BRAIN READY BASELINE V1
> BASELINE_ID: CLOUD_BRAIN_READY_BASELINE_V1
> Generated: 2026-08-18 21:16 CST
> Status: FROZEN — use for drift check before Brain Integration
> Cloud Brain Gate: READY
> Windows Gate: EXTERNAL_PENDING

## 1. SYSTEM IDENTITY

| Field | Value |
|-------|-------|
| Hostname | VM-0-15-ubuntu |
| Kernel | 6.8.0-124-generic |
| Python | 3.12.3 |

## 2. SERVICE BASELINE (SNAPSHOT)

### paw-actions
| Field | Value | Classification |
|-------|-------|---------------|
| Active | active | HARD_INVARIANT |
| MainPID | 2164245 | SOFT_OBSERVATION |
| NRestarts | 0 | SOFT_OBSERVATION |
| ExecStart | /opt/paw-actions/.venv/bin/uvicorn | HARD_INVARIANT |
| NeedDaemonReload | no | HARD_INVARIANT |

### openclaw-gateway
| Field | Value | Classification |
|-------|-------|---------------|
| Active | active | HARD_INVARIANT |
| MainPID | 2203706 | SOFT_OBSERVATION |
| NRestarts | 0 | SOFT_OBSERVATION |
| ExecStart | /root/.nvm/versions/node/v22.22.3/bin/openclaw gateway run --port 18789 | HARD_INVARIANT |
| DropInPaths | 20-tavily-env.conf | HARD_INVARIANT |
| NeedDaemonReload | no | HARD_INVARIANT |

> PID changes are SOFT_OBSERVATION — legitimate restarts may change PID.
> ExecStart/DropInPaths changes are HARD_INVARIANT.

## 3. OPENCLAW RUNTIME BASELINE

| Field | Value | Classification |
|-------|-------|---------------|
| Node path | /root/.nvm/versions/node/v22.22.3/bin/node | HARD_INVARIANT |
| OpenClaw path | /root/.nvm/versions/node/v22.22.3/bin/openclaw | HARD_INVARIANT |
| Version | 2026.7.1-2 | HARD_INVARIANT |
| Disk ExecStart | .../openclaw gateway run --port 18789 | HARD_INVARIANT |
| Loaded ExecStart | .../openclaw gateway run --port 18789 | HARD_INVARIANT |
| Running exe | /root/.nvm/versions/node/v22.22.3/bin/node | HARD_INVARIANT |
| Listener scope | 127.0.0.1:18789 + [::1]:18789 (loopback) | HARD_INVARIANT |
| Health | {"ok":true,"status":"live"} | HARD_INVARIANT |
| Active drop-ins | 20-tavily-env.conf only | HARD_INVARIANT |
| Three-layer match | YES | HARD_INVARIANT |

## 4. CLI OBSERVATION

| Field | Value | Classification |
|-------|-------|---------------|
| Path | /root/.local/share/pnpm/openclaw | SOFT_OBSERVATION |
| Version | 2026.6.9 | SOFT_OBSERVATION |
| Status | LEGACY_PATH_OBSERVATION | SOFT_OBSERVATION |

> CLI PATH legacy does NOT affect production runtime. Not a hard invariant.

## 5. WORKSPACE DB STRUCTURAL BASELINE

| Field | Value | Classification |
|-------|-------|---------------|
| Path | /var/lib/personal-workspace/workspace.db | HARD_INVARIANT |
| Integrity | ok | HARD_INVARIANT |
| Foreign keys | [] (clean) | HARD_INVARIANT |
| Schema version | 4 | HARD_INVARIANT |

### Table Snapshot

| Table | Exists | Count | Classification |
|-------|--------|-------|---------------|
| project | YES | 10 | HARD (exists), DRIFT_REPORT (count) |
| todo | YES | 24 | HARD (exists), DRIFT_REPORT (count) |
| knowledge_page | YES | 16 | HARD (exists), DRIFT_REPORT (count) |
| knowledge_revision | YES | 22 | HARD (exists), DRIFT_REPORT (count) |
| knowledge_source_link | YES | 92 | HARD (exists), DRIFT_REPORT (count) |
| daily | YES | 8 | HARD (exists), EXPECTED_TO_CHANGE (count) |
| capture | YES | 11 | HARD (exists), EXPECTED_TO_CHANGE (count) |
| wiki_page | YES | 1084 | HARD (exists), EXPECTED_TO_CHANGE (count) |

### Brain Tables (NOT YET DEPLOYED)

| Table | Status | Classification |
|-------|--------|---------------|
| analysis_run | ABSENT | HARD_INVARIANT (must be ABSENT) |
| candidate | ABSENT | HARD_INVARIANT (must be ABSENT) |

> Canonical table counts (project/todo/knowledge_*) are DRIFT_REPORT only.
> Daily/capture/wiki_page counts are EXPECTED_TO_CHANGE.

## 6. BRAIN FILE HASH BASELINE

See: `brain/REALITY_MANIFEST_V1.txt` for full file list with sha256.

Key files (HARD_INVARIANT — hash must match unless explicitly updated):

- brain/policies/agnes_persona_v1.md
- brain/policies/agnes_reasoning_policy_v1.md
- brain/policies/fact_precedence_v1.md
- brain/policies/openclaw_memory_policy_v1.md
- brain/analysis/models.py
- brain/candidates/models.py
- brain/context/builder.py
- brain/chat/gateway.py
- brain/runtime/agnes_runtime.py
- brain/calibration/agnes_calibration_cases_v1.json
- brain/calibration/harness.py
- brain/integration/*.py + *.md

## 7. POLICY INVARIANTS (HARD)

| Invariant | Status |
|-----------|--------|
| FACT AUTHORITY present in persona | PASS |
| FACT PRIORITY old global ranking absent | PASS |
| Persona references fact_precedence_v1.md | PASS |
| USER_INTENT_STATE tier exists | PASS |
| BUSINESS_RULE tier exists | PASS |
| BUSINESS_DATA tier exists | PASS |
| CANONICAL_ENTITY_STATE tier exists | PASS |
| AI_DERIVED tier exists | PASS |
| Memory policy: Personal Workspace canonical | PASS |
| Memory policy: OpenClaw memory runtime-only | PASS |
| AUTO_WRITE = FALSE | PASS |

## 8. NOT-YET-DEPLOYED INVARIANTS (HARD)

| Invariant | Status | Classification |
|-----------|--------|---------------|
| candidate table | ABSENT | HARD_INVARIANT |
| analysis_run table | ABSENT | HARD_INVARIANT |
| /v1/brain production router | NOT_MOUNTED | HARD_INVARIANT |
| Production SOUL Brain merge | ABSENT | HARD_INVARIANT |
| Production AGENTS Brain merge | ABSENT | HARD_INVARIANT |
| Agnes Runtime production binding | NOT_BOUND | HARD_INVARIANT |
| Real Agnes calibration | NOT_RUN | HARD_INVARIANT |
| AUTO_WRITE | FALSE | HARD_INVARIANT |

## 9. DRIFT CLASSIFICATION

### HARD_DRIFT (must investigate before proceeding)

- OpenClaw production runtime version changed
- Disk/loaded/runtime three-layer inconsistency
- DB integrity failure
- FK violation
- Brain core file missing
- Brain policy hash changed without record
- AUTO_WRITE enabled
- candidate/analysis_run table unexpectedly exists
- /v1/brain router unexpectedly mounted
- Production Persona unexpectedly modified

### SAFE_DRIFT (report but do not block)

- paw-actions PID changed
- openclaw-gateway PID changed
- daily count increased
- capture count increased
- wiki_page count increased
- interactive CLI PATH still legacy

### EXPECTED_DRIFT (natural growth)

- daily count growth
- capture count growth
- wiki_page count growth (WorkBuddy backfill)
- service PID after legitimate restart

## 10. VERIFICATION

Run: `python verify_cloud_ready_baseline.py`

Output:
- BASELINE_STATUS: MATCH / SAFE_DRIFT / HARD_DRIFT
- HARD_DRIFT_ITEMS: [list]
- SOFT_DRIFT_ITEMS: [list]
- EXPECTED_DRIFT_ITEMS: [list]

## 11. CLOUD BRAIN GATE

```
CLOUD_BRAIN_GATE: READY
WINDOWS_GATE: EXTERNAL_PENDING
```

No Cloud-side blockers identified.
All Brain preparation work complete.
Ready for PERSONAL_AI_WORKSPACE_BRAIN_INTEGRATION_V1 after Windows Gate passes.