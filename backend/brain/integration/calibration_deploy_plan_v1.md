# CALIBRATION DEPLOY PLAN V1
> Version: 1.0.0
> Created: 2026-08-17
> Status: DESIGN_ONLY — not executed
> Gate: GATE 7
> Prerequisite: GATE 1-6 all PASS

## 1. OVERVIEW

使用 20-case calibration dataset 对真实 Agnes runtime 进行校准。
分三批执行，每批完成后评估质量门控。
任何门控 FAIL → 停止校准，回退到 DRY_RUN。

## 2. CALIBRATION DATASET

Source: `brain/calibration/agnes_calibration_cases_v1.json`
Total: 20 cases

## 3. BATCHES

### BATCH A: Simple Explicit Cases (5 cases)

**Case Types**: Explicit facts, clear dates, direct statements
**Difficulty**: Low
**Expected**: Near-perfect classification

**Quality Gates**:
| Gate | Threshold | Required |
|------|-----------|----------|
| classification_accuracy | >= 95% | YES |
| unsafe_inference | = 0 | YES |
| plan_as_done | = 0 | YES |
| idea_as_decision | = 0 | YES |
| date_loss | = 0 | YES |

**Pass Criteria**: All 5 gates PASS
**Fail Action**: Stop, investigate, adjust prompt/model, re-run BATCH A

### BATCH B: Ambiguity/Decision/Status Cases (10 cases)

**Case Types**: Ambiguous statements, planning vs done, ideas vs decisions, tentative dates
**Difficulty**: Medium-High
**Expected**: High accuracy with proper AMBIGUITY/CONFLICT detection

**Quality Gates**:
| Gate | Threshold | Required |
|------|-----------|----------|
| classification_accuracy | >= 90% | YES |
| unsafe_inference | = 0 | YES |
| plan_as_done | = 0 | YES |
| idea_as_decision | = 0 | YES |
| confirmed_conflict_overwrite | = 0 | YES |
| date_loss | = 0 | YES |
| ambiguity_detected | >= 80% | YES (of actual ambiguous cases) |

**Pass Criteria**: All gates PASS
**Fail Action**: Stop, investigate ambiguity handling, adjust prompt, re-run BATCH B

### BATCH C: Conflict/Project Binding Cases (5 cases)

**Case Types**: Conflicting facts, wrong project binding, cross-project references
**Difficulty**: High
**Expected**: Proper CONFLICT candidate generation, correct project binding

**Quality Gates**:
| Gate | Threshold | Required |
|------|-----------|----------|
| classification_accuracy | >= 85% | YES |
| conflict_detected | >= 80% | YES (of actual conflict cases) |
| project_misbinding | <= 1 | YES |
| confirmed_conflict_overwrite | = 0 | YES |
| unsafe_inference | = 0 | YES |

**Pass Criteria**: All gates PASS
**Fail Action**: Stop, investigate conflict resolution, adjust fact_precedence policy, re-run BATCH C

## 4. QUALITY GATE DEFINITIONS

### classification_accuracy
- Definition: Percentage of cases where the candidate type is correctly classified
- Calculation: correct_classifications / total_cases * 100
- Measurement: Compare candidate_type against expected_type in dataset

### unsafe_inference
- Definition: Cases where Agnes makes an inference that should have been marked AMBIGUITY or CONFLICT
- Threshold: 0 (zero tolerance)
- Example: '准备安排初试' inferred as '初试已完成' = unsafe_inference

### plan_as_done
- Definition: Cases where a planning statement is incorrectly marked as completed action
- Threshold: 0 (zero tolerance)
- Example: '时间需要调整' → candidate_type=todo, status=done = plan_as_done

### idea_as_decision
- Definition: Cases where an idea/suggestion is treated as a decision
- Threshold: 0 (zero tolerance)
- Example: '可以考虑增加面试环节' → candidate_type=fact, status=confirmed = idea_as_decision

### confirmed_conflict_overwrite
- Definition: Cases where confirmed knowledge is overwritten without CONFLICT candidate
- Threshold: 0 (zero tolerance)

### project_misbinding
- Definition: Cases where input is bound to wrong project without evidence
- Threshold: <= 1 (allow 1 edge case)
- Example: Input about '星火' project bound to '运营导师体系' = project_misbinding

### date_loss
- Definition: Cases where date/time information is dropped or altered
- Threshold: 0 (zero tolerance)
- Example: '8月19日上午10点' → candidate with no date = date_loss

## 5. TWO-PASS COST CONTROL

| Request Type | Pass Strategy | Rationale |
|--------------|---------------|----------|
| Simple extract (BATCH A) | Single pass | Low complexity, single pass sufficient |
| 明确查询 | Single pass | Direct lookup, no candidate generation |
| Candidate generation (BATCH B/C) | Two pass | Analysis + review needed for quality |
| Knowledge candidate | Two pass | High stakes, needs review |
| Conflict (BATCH C) | Two pass | Conflict detection needs careful review |
| Ambiguity (BATCH B) | Two pass | Ambiguity handling needs validation |

## 6. MODEL FALLBACK POLICY

### Primary Model
- mistral/mistral-small-latest

### Fallback Chain
1. mistral/mistral-small-latest (primary)
2. Nemotron (fallback 1)
3. GLM (fallback 2)

### Model Quality Rules
- Calibration FAILED model → CANNOT be used for Candidate generation
- Calibration FAILED model → CAN be used for chat/extract IF minimum quality gate passed
- Minimum quality gate: classification_accuracy >= 80%, unsafe_inference = 0
- Do NOT change current provider/model without explicit authorization

## 7. EXECUTION PROCEDURE

> ⚠️ 以下步骤在 GATE 7 执行时操作，本轮 DO NOT EXECUTE。

### Step 1: Verify Prerequisites
- GATE 1-6 all PASS
- AgnesRuntimeAdapter DRY_RUN = False
- Brain router mounted and functional
- 20-case dataset loaded

### Step 2: Run BATCH A
```
python -m brain.calibration.harness --batch A --model mistral/mistral-small-latest
```
- Evaluate quality gates
- If ALL PASS → proceed to BATCH B
- If ANY FAIL → stop, investigate, re-run

### Step 3: Run BATCH B
```
python -m brain.calibration.harness --batch B --model mistral/mistral-small-latest
```
- Evaluate quality gates
- If ALL PASS → proceed to BATCH C
- If ANY FAIL → stop, investigate, re-run

### Step 4: Run BATCH C
```
python -m brain.calibration.harness --batch C --model mistral/mistral-small-latest
```
- Evaluate quality gates
- If ALL PASS → calibration complete, proceed to GATE 8
- If ANY FAIL → stop, investigate, re-run or try fallback model

### Step 5: Generate Report
- Aggregate results across all 20 cases
- Document any failures and adjustments
- Report model quality assessment
- Recommend production readiness

## 8. ROLLBACK

If calibration fails catastrophically:
1. Set DRY_RUN = True
2. Stop all candidate generation
3. Review all failed cases
4. Document findings
5. Adjust prompt/policy/model as needed
6. Re-run calibration from BATCH A

## 9. POST-CALIBRATION

After all 20 cases PASS:
- Proceed to GATE 8 (Candidate-only Production Observation)
- 24h observation with AUTO_WRITE = False
- Monitor: analysis errors, candidate noise, duplicates, latency, token cost
- If observation PASS → proceed to GATE 9
- If observation FAIL → rollback to DRY_RUN