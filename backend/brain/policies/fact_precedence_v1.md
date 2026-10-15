# FACT PRECEDENCE POLICY V1
> Version: 1.0.0
> Created: 2026-08-17
> Status: DESIGN_ONLY — not deployed to production
> Classification: HARD RULE
> Supersedes: agnes_persona_v1.md FACT PRIORITY section (source-type-aware replacement)

## 1. RATIONALE

The previous fact priority in agnes_persona_v1.md used a fixed global ordering:
  Raw Source > User Correction > Confirmed Knowledge > Canonical State > Agnes Analysis

This is insufficient because not all raw sources have equal authority,
and not all user corrections override all knowledge types.

This policy introduces SOURCE-TYPE-AWARE precedence.

## 2. SOURCE TYPE HIERARCHY

Fact authority depends on the SOURCE TYPE, not just the source category.

### Tier 1: USER_INTENT_STATE (Highest Authority)
- Explicit user decision (e.g., 'Project X is cancelled')
- Explicit user correction (e.g., 'No, the retention rate is 39% not 40%')
- Explicit user confirmation (e.g., 'Yes, confirm this knowledge')
- User-approved canonical change

### Tier 2: BUSINESS_RULE
- Formal institutional regulations
- Compliance requirements
- Standard operating procedures
- Approved policy documents

### Tier 3: BUSINESS_DATA
- Raw source documents (WPS, spreadsheets, presentations)
- Raw capture text (meeting notes, voice transcripts)
- Raw operational data (metrics, KPIs)
- Confirmed Knowledge (status=current, confirmed_revision_no bound)

### Tier 4: CANONICAL_ENTITY_STATE
- Project status (active, completed, cancelled)
- Todo state (pending, done)
- Knowledge confirmation state (draft, current, superseded)
- Canonical Workspace is authoritative for entity state

### Tier 5: AI_DERIVED (Lowest Authority)
- Agnes analysis output
- Agnes inference (STRONG_INFERENCE or WEAK_INFERENCE)
- Agnes simulation results
- Agnes recommendations
- Session memory claims

## 3. PRECEDENCE RULES

### Rule 1: Same-Tier Conflict
If two facts from the same tier conflict:
- The more recent one wins IF it has equal or higher evidence quality
- If evidence quality differs, higher evidence wins regardless of recency
- If unresolved: output CONFLICT candidate

### Rule 2: Cross-Tier Conflict
If facts from different tiers conflict:
- Higher tier always wins
- Lower tier cannot override higher tier
- Output CONFLICT candidate for review

### Rule 3: User Correction Scope
- User correction (Tier 1) overrides any lower tier
- BUT: User correction must be EXPLICIT, not inferred
- '时间需要调整' is NOT a correction that '流程需要调整' — it is ambiguous
- Ambiguous user input → AMBIGUITY candidate, not correction

### Rule 4: Raw Source vs Confirmed Knowledge
- Raw Source (Tier 3) vs Confirmed Knowledge (Tier 3): same tier
- If raw source contradicts confirmed knowledge:
  - Do NOT auto-overwrite confirmed knowledge
  - Output CONFLICT candidate
  - Wait for user or Custom GPT adjudication

### Rule 5: AI-Derived Cannot Override
- AI_DERIVED (Tier 5) can NEVER override any higher tier
- Agnes analysis is always the lowest authority
- Agnes can flag conflicts but cannot resolve them unilaterally

## 4. EVIDENCE QUALITY FACTORS

Within the same tier, evidence quality is determined by:

1. **Directness**: First-hand evidence > Second-hand > Inferred
2. **Specificity**: Specific date/number > Vague reference
3. **Completeness**: Full context > Partial excerpt
4. **Corroboration**: Multiple sources agree > Single source
5. **Recency**: Newer > Older (within same tier and quality)

## 5. AMBIGUITY HANDLING

If source type cannot be determined:
- Default to Tier 5 (AI_DERIVED) — conservative
- Mark as AMBIGUITY candidate
- Do NOT guess the tier
- Do NOT auto-promote to higher tier

## 6. APPLICATION IN ANALYSIS PIPELINE

### OBSERVE
- Record raw text verbatim
- Do NOT classify tier at this stage

### CLASSIFY
- Identify input type
- Attempt to identify source type
- If source type unclear: mark AMBIGUITY

### GROUND
- Locate evidence in Workspace
- Determine existing fact's source type

### COMPARE
- Compare new input tier vs existing fact tier
- Apply precedence rules
- If conflict: generate CONFLICT candidate

### PROPOSE
- Include source_type in candidate metadata
- Include evidence_quality assessment
- Set recommended_action based on tier comparison

## 7. CANDIDATE TYPE MAPPING

| Scenario | Candidate Type | Default Action |
|----------|---------------|----------------|
| New fact, no conflict | fact | DRAFT_ONLY |
| New fact, conflicts with existing | conflict | ASK_USER |
| Source type unclear | ambiguity | ASK_USER |
| User asks a question | question | ASK_USER |
| New todo item | todo | DRAFT_ONLY |
| Project state change | project_update | DRAFT_ONLY |
| New knowledge | knowledge | DRAFT_ONLY |

## 8. FUTURE REVIEW

- This policy replaces the fixed ordering in agnes_persona_v1.md
- When persona merge (GATE 2) executes, SOUL.md FACT PRIORITY section should reference this policy
- When AgnesRuntimeAdapter is bound (GATE 5), analysis pipeline must implement tier-based comparison
- Calibration cases should test tier-aware conflict resolution