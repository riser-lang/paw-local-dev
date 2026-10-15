# DEPLOYMENT CHECKLIST V1
> Version: 1.0.0
> Created: 2026-08-17
> Status: DESIGN_ONLY — not executed
> Each item MUST be PASS or FAIL — no ambiguous states.

## 1. PRECHECK

| # | Item | Verification | Status |
|---|------|-------------|--------|
| P1 | WorkBuddy Source Pipeline FULL_CUTOVER_PASS | Source pipeline complete, no pending batch | ☐ PASS ☐ FAIL |
| P2 | OpenClaw runtime reconciliation plan reviewed | openclaw_runtime_plan_v1.md reviewed and approved | ☐ PASS ☐ FAIL |
| P3 | Persona merge plan reviewed | persona_merge_plan_v1.md reviewed and approved | ☐ PASS ☐ FAIL |
| P4 | Migration validator tests pass | M1-M8 all PASS on temp DB | ☐ PASS ☐ FAIL |
| P5 | Staging router tests pass | I8-I18 all PASS | ☐ PASS ☐ FAIL |
| P6 | Brain foundation tests pass | B1-B12 + A8-A10 + C1-C10 all PASS | ☐ PASS ☐ FAIL |
| P7 | AUTO_WRITE_ENABLED = False | Confirmed in candidates/models.py + router_staging.py | ☐ PASS ☐ FAIL |
| P8 | User authorization obtained | Explicit user confirmation for deployment | ☐ PASS ☐ FAIL |

## 2. BACKUP

| # | Item | Verification | Status |
|---|------|-------------|--------|
| B1 | workspace.db backup | `cp workspace.db workspace.db.bak.YYYYMMDD` | ☐ PASS ☐ FAIL |
| B2 | SOUL.md backup | `cp SOUL.md SOUL.md.bak.YYYYMMDD` | ☐ PASS ☐ FAIL |
| B3 | AGENTS.md backup | `cp AGENTS.md AGENTS.md.bak.YYYYMMDD` | ☐ PASS ☐ FAIL |
| B4 | systemd unit backup | `cp openclaw-gateway.service .bak` | ☐ PASS ☐ FAIL |
| B5 | paw-actions main.py backup | `cp main.py main.py.bak.YYYYMMDD` | ☐ PASS ☐ FAIL |
| B6 | OpenClaw config backup | `cp openclaw.json openclaw.json.bak` | ☐ PASS ☐ FAIL |
| B7 | All workspace files backup | IDENTITY/USER/TOOLS/HEARTBEAT.md | ☐ PASS ☐ FAIL |

## 3. OPENCLAW RUNTIME

| # | Item | Verification | Status |
|---|------|-------------|--------|
| O1 | CLI version == production version | `openclaw --version` == 2026.7.1-2 | ☐ PASS ☐ FAIL |
| O2 | systemd disk unit ExecStart == loaded | No override needed | ☐ PASS ☐ FAIL |
| O3 | ConditionPathExists correct | Points to 2026.7.1-2 path | ☐ PASS ☐ FAIL |
| O4 | Node version unified | v22.22.3 in disk unit | ☐ PASS ☐ FAIL |
| O5 | Gateway PID stable | `systemctl show --property=MainPID` | ☐ PASS ☐ FAIL |
| O6 | Port 18789 listening | `ss -tlnp | grep 18789` | ☐ PASS ☐ FAIL |
| O7 | Agnes sessions functional | Session creation test | ☐ PASS ☐ FAIL |
| O8 | Provider/model unchanged | openclaw.json config verified | ☐ PASS ☐ FAIL |

## 4. PERSONA

| # | Item | Verification | Status |
|---|------|-------------|--------|
| PE1 | SOUL.md V2 deployed | Contains all 8 target sections | ☐ PASS ☐ FAIL |
| PE2 | Server Ops identity preserved | Inspect/Reuse/Backup/Test/Rollback rules intact | ☐ PASS ☐ FAIL |
| PE3 | PERSONAL_WORKSPACE_ROLE added | Brain workspace role in SOUL.md | ☐ PASS ☐ FAIL |
| PE4 | MEMORY_BOUNDARY explicit | OpenClaw memory ≠ canonical | ☐ PASS ☐ FAIL |
| PE5 | DECISION_BOUNDARY explicit | recommend ≠ decision | ☐ PASS ☐ FAIL |
| PE6 | AGENTS.md V2 deployed | Contains all 6 task modes | ☐ PASS ☐ FAIL |
| PE7 | Server Ops 10-step flow preserved | AGENTS.md SERVER_OPS_MODE intact | ☐ PASS ☐ FAIL |
| PE8 | workspace_* modes READ_ONLY | No shell write in workspace modes | ☐ PASS ☐ FAIL |
| PE9 | Protected Core rules preserved | SSH/22, Xray/443, Tailscale, 8443 | ☐ PASS ☐ FAIL |

## 5. MIGRATION

| # | Item | Verification | Status |
|---|------|-------------|--------|
| M1 | analysis_run table created | `SELECT name FROM sqlite_master WHERE name='analysis_run'` | ☐ PASS ☐ FAIL |
| M2 | candidate table created | `SELECT name FROM sqlite_master WHERE name='candidate'` | ☐ PASS ☐ FAIL |
| M3 | Indexes created | idx_candidate_analysis_run, idx_candidate_status, idx_candidate_type | ☐ PASS ☐ FAIL |
| M4 | FK constraint active | `PRAGMA foreign_key_check` clean | ☐ PASS ☐ FAIL |
| M5 | ambiguity type in CHECK | `INSERT ... 'ambiguity'` succeeds | ☐ PASS ☐ FAIL |
| M6 | v4 tables untouched | project/todo/knowledge_* counts unchanged | ☐ PASS ☐ FAIL |
| M7 | Schema version updated | `PRAGMA user_version` == 5 | ☐ PASS ☐ FAIL |
| M8 | No triggers created | `SELECT * FROM sqlite_master WHERE type='trigger'` empty | ☐ PASS ☐ FAIL |

## 6. ROUTER

| # | Item | Verification | Status |
|---|------|-------------|--------|
| R1 | Staging router imported to main.py | `from brain.integration.router_staging import router` | ☐ PASS ☐ FAIL |
| R2 | /v1/brain/projects/{id} responds | GET returns ProjectBrainResponse | ☐ PASS ☐ FAIL |
| R3 | /v1/brain/candidates responds | GET returns CandidateListResponse | ☐ PASS ☐ FAIL |
| R4 | /v1/brain/chat responds | POST creates session | ☐ PASS ☐ FAIL |
| R5 | /v1/brain/chat/{id}/messages responds | POST returns ChatMessageResponse | ☐ PASS ☐ FAIL |
| R6 | Auth dependency active | Unauthorized request → 401 | ☐ PASS ☐ FAIL |
| R7 | No production port conflict | Brain routes on paw-actions port 8787 | ☐ PASS ☐ FAIL |

## 7. AUTH

| # | Item | Verification | Status |
|---|------|-------------|--------|
| A1 | Bearer token auth active | PAW_OPS_API_KEY verification | ☐ PASS ☐ FAIL |
| A2 | OpenClaw 18789 loopback-only | `ss -tlnp | grep 18789` = 127.0.0.1 | ☐ PASS ☐ FAIL |
| A3 | Brain Gateway auth flow | auth → validation → context → runtime | ☐ PASS ☐ FAIL |
| A4 | No direct OpenClaw exposure | External clients cannot reach 18789 | ☐ PASS ☐ FAIL |

## 8. PROJECT_CONTEXT

| # | Item | Verification | Status |
|---|------|-------------|--------|
| PC1 | GET /v1/brain/projects/{id} returns full context | project + todos + knowledge + sources + daily + capture + candidates | ☐ PASS ☐ FAIL |
| PC2 | Read-only verified | No DB write after GET request | ☐ PASS ☐ FAIL |
| PC3 | Unknown project safe | Returns None / 404 gracefully | ☐ PASS ☐ FAIL |

## 9. AGNES_RUNTIME

| # | Item | Verification | Status |
|---|------|-------------|--------|
| AR1 | DRY_RUN = False | AgnesRuntimeAdapter DRY_RUN disabled | ☐ PASS ☐ FAIL |
| AR2 | health_check pass | `agnes_runtime.health_check()` returns True | ☐ PASS ☐ FAIL |
| AR3 | create_or_resume_session functional | Session created via OpenClaw API | ☐ PASS ☐ FAIL |
| AR4 | send_message returns response | Real model response received | ☐ PASS ☐ FAIL |
| AR5 | analysis_pass produces draft | Structured Draft generated | ☐ PASS ☐ FAIL |
| AR6 | review_pass produces validated candidate | Validated Candidate generated | ☐ PASS ☐ FAIL |
| AR7 | No CoT saved | Chain-of-thought not persisted | ☐ PASS ☐ FAIL |

## 10. CALIBRATION

| # | Item | Verification | Status |
|---|------|-------------|--------|
| C1 | BATCH A (5 simple cases) complete | All 5 cases run | ☐ PASS ☐ FAIL |
| C2 | BATCH B (10 ambiguity cases) complete | All 10 cases run | ☐ PASS ☐ FAIL |
| C3 | BATCH C (5 conflict cases) complete | All 5 cases run | ☐ PASS ☐ FAIL |
| C4 | classification_accuracy >= 90% | Measured across all 20 cases | ☐ PASS ☐ FAIL |
| C5 | unsafe_inference = 0 | No unsafe inferences | ☐ PASS ☐ FAIL |
| C6 | plan_as_done = 0 | No plan-as-done errors | ☐ PASS ☐ FAIL |
| C7 | idea_as_decision = 0 | No idea-as-decision errors | ☐ PASS ☐ FAIL |
| C8 | confirmed_conflict_overwrite = 0 | No confirmed knowledge overwritten | ☐ PASS ☐ FAIL |
| C9 | project_misbinding <= 1 | At most 1 misbinding | ☐ PASS ☐ FAIL |
| C10 | date_loss = 0 | No date/time information lost | ☐ PASS ☐ FAIL |

## 11. OBSERVATION

| # | Item | Verification | Status |
|---|------|-------------|--------|
| OB1 | 24h candidate-only observation complete | No auto-write for 24 hours | ☐ PASS ☐ FAIL |
| OB2 | No critical errors | No analysis/candidate/runtime critical errors | ☐ PASS ☐ FAIL |
| OB3 | Candidate noise acceptable | Duplicate/noise rate below threshold | ☐ PASS ☐ FAIL |
| OB4 | Latency acceptable | Average response time within budget | ☐ PASS ☐ FAIL |
| OB5 | Token cost acceptable | Daily token usage within budget | ☐ PASS ☐ FAIL |
| OB6 | Agnes runtime stable | No runtime crashes in 24h | ☐ PASS ☐ FAIL |

## 12. ROLLBACK

| # | Item | Verification | Status |
|---|------|-------------|--------|
| RB1 | Rollback plan documented | rollback_plan_v1.md complete | ☐ PASS ☐ FAIL |
| RB2 | Rollback SQL tested | M3 test passed on temp DB | ☐ PASS ☐ FAIL |
| RB3 | Persona rollback tested | SOUL.md/AGENTS.md backup restore verified | ☐ PASS ☐ FAIL |
| RB4 | Router rollback tested | Remove import from main.py, verify | ☐ PASS ☐ FAIL |
| RB5 | OpenClaw rollback tested | Unit restore, daemon-reload, restart | ☐ PASS ☐ FAIL |

## 13. FINAL ACCEPTANCE

| # | Item | Verification | Status |
|---|------|-------------|--------|
| F1 | All PRECHECK items PASS | P1-P8 all PASS | ☐ PASS ☐ FAIL |
| F2 | All BACKUP items PASS | B1-B7 all PASS | ☐ PASS ☐ FAIL |
| F3 | All OPENCLAW items PASS | O1-O8 all PASS | ☐ PASS ☐ FAIL |
| F4 | All PERSONA items PASS | PE1-PE9 all PASS | ☐ PASS ☐ FAIL |
| F5 | All MIGRATION items PASS | M1-M8 all PASS | ☐ PASS ☐ FAIL |
| F6 | All ROUTER items PASS | R1-R7 all PASS | ☐ PASS ☐ FAIL |
| F7 | All AUTH items PASS | A1-A4 all PASS | ☐ PASS ☐ FAIL |
| F8 | All PROJECT_CONTEXT items PASS | PC1-PC3 all PASS | ☐ PASS ☐ FAIL |
| F9 | All AGNES_RUNTIME items PASS | AR1-AR7 all PASS | ☐ PASS ☐ FAIL |
| F10 | All CALIBRATION items PASS | C1-C10 all PASS | ☐ PASS ☐ FAIL |
| F11 | All OBSERVATION items PASS | OB1-OB6 all PASS | ☐ PASS ☐ FAIL |
| F12 | All ROLLBACK items PASS | RB1-RB5 all PASS | ☐ PASS ☐ FAIL |
| F13 | AUTO_WRITE_ENABLED = False | Still False after all deployment | ☐ PASS ☐ FAIL |
| F14 | Production DB counts unchanged | project=10, todo=24, knowledge_page=16, knowledge_revision=22, knowledge_source_link=92 | ☐ PASS ☐ FAIL |
| F15 | paw-actions PID stable | No unintended restart | ☐ PASS ☐ FAIL |
| F16 | OpenClaw PID stable | No unintended restart (post-reconciliation) | ☐ PASS ☐ FAIL |
| F17 | Protected Core intact | SSH/22, Xray/443, Tailscale, 8443 NO_LISTENER | ☐ PASS ☐ FAIL |

## AUTO_WRITE POLICY

AUTO_WRITE_ENABLED = False

This Integration Package MUST keep AUTO_WRITE disabled.
Deployment success does NOT auto-enable AUTO_WRITE.
Future AUTO_WRITE evaluation: separate task with explicit user gate.