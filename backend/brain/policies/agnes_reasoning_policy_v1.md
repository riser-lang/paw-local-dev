# AGNES REASONING POLICY V1

> Version: 1.0.0
> Created: 2026-08-17
> Status: DESIGN_ONLY — not deployed to production

## REASONING PIPELINE

Every analysis run follows a 9-step external reasoning protocol.

### 1. OBSERVE

Receive raw input (capture text, source metadata, user message).
Preserve original text verbatim. Do not paraphrase at this stage.

### 2. CLASSIFY

Identify input type: fact, event, decision, plan, idea, question,
completed action, pending action, status change, person, date, conflict.

### 3. GROUND

Locate evidence in Workspace: which Project, Todo, Knowledge,
Source, Daily, or Capture does this input relate to?

If no grounding found: mark AMBIGUITY, do not guess.

### 4. CONTEXTUALIZE

Fetch relevant Project Context (read-only).
Identify what is already known (confirmed knowledge) vs draft vs unknown.

### 5. COMPARE

Compare new input against:
- Confirmed Knowledge (conflict detection)
- Existing Todos (duplicate detection)
- Existing Project state (status consistency)
- Raw Source evidence (corroboration)

### 6. RESOLVE

Attempt to resolve ambiguities using evidence only.
If resolution requires assumption: mark UNRESOLVED, do not fabricate.

### 7. PROPOSE

Generate candidates with:
- statement
- evidence_refs
- raw_excerpt
- confidence (HIGH/MEDIUM/LOW)
- inference_level (EXPLICIT/STRONG_INFERENCE/WEAK_INFERENCE)
- recommended_action (DRAFT_ONLY/ASK_USER/DO_NOT_WRITE)

### 8. GATE

Apply write-safety rules:
- Knowledge Candidate → never AUTO_WRITE to confirmed
- Ambiguous Todo → never AUTO_WRITE done
- Conflict → always ASK_USER
- Ambiguity → always ASK_USER

### 9. REPORT

Output: conclusions, evidence, uncertainties.

Do NOT save hidden chain-of-thought.

This is an external reasoning protocol, not a private reasoning transcript.

## TWO-PASS REVIEW MODEL

### PASS 1: ANALYST

Generates preliminary interpretation.

Produces draft candidates with confidence and inference_level.

### PASS 2: REVIEWER

Checks Pass 1 output for:

- plan_as_done: planning statement incorrectly marked as completed
- idea_as_decision: idea incorrectly treated as decision
- date_loss: date/time information dropped or altered
- project_misbinding: input bound to wrong project without evidence
- unsupported_inference: inference lacks sufficient evidence
- confirmed_knowledge_conflict: new input conflicts with confirmed knowledge
- evidence_mismatch: cited evidence does not support the statement

Only after Pass 2 review passes: produce FINAL_CANDIDATES.

## DEFAULT WRITE POLICY (V1)

| Candidate Type | Default Action |
|----------------|---------------|
| Fact | DRAFT_ONLY |
| Todo | DRAFT_ONLY |
| Project Update | DRAFT_ONLY |
| Knowledge | DRAFT_ONLY |
| Conflict | ASK_USER |
| Ambiguity | ASK_USER |

AUTO_WRITE exists in schema but feature flag is OFF by default.

No AUTO_WRITE is enabled in V1 Foundation.

## SESSION PRINCIPLE

OpenClaw session provides conversation continuity only.

Key facts are re-fetched from Personal Workspace each turn.

Session history is NOT a canonical fact source.
