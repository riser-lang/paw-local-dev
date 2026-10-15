# OPENCLAW MEMORY POLICY V1
> Version: 1.0.0
> Created: 2026-08-17
> Status: DESIGN_ONLY — not deployed to production
> Classification: HARD RULE

## 1. SCOPE

This policy governs all OpenClaw memory mechanisms:
- Session history (conversation context)
- MEMORY.md (persistent memory file)
- memory/YYYY-MM-DD.md (daily memory logs)
- Any future OpenClaw memory mechanism

## 2. ALLOWED MEMORY CONTENT

OpenClaw memory MAY store:

### Conversation Continuity
- Current conversation context
- Recent user messages (for reference)
- Session state (what was discussed, what was asked)

### Runtime Lessons
- Tool usage notes (e.g., 'sqlite3 needs mode=ro for read-only')
- Server operation lessons (e.g., 'paw-actions requires Bearer token')
- Environment quirks (e.g., 'node v22.22.3 is the active version')
- Error patterns encountered and resolved

### Agnes Self-Experience
- What Agnes learned about its own workflow
- What analysis patterns worked well
- What calibration results showed
- Tool configuration notes

### Server Operations Lessons
- Service restart procedures that worked
- Backup locations used
- Rollback procedures executed
- Health check patterns

## 3. PROHIBITED MEMORY CONTENT

OpenClaw memory MUST NOT store as canonical:

### Business Facts
- User business decisions
- Business metrics or KPIs
- Business rules or policies
- Organizational structure information

### Project State
- Project status (in-progress, completed, etc.)
- Project metadata changes
- Project timeline updates

### Todo State
- Todo items (new, completed, pending)
- Todo priority changes
- Todo assignments

### Knowledge
- Knowledge page content
- Knowledge revisions
- Knowledge source links
- Knowledge confirmation status

### WPS Business Conclusions
- WPS document conclusions
- WPS spreadsheet data
- WPS presentation content

### Formal System Rules
- Institutional regulations
- Standard operating procedures
- Compliance requirements
- Formal policy documents

### Long-term User Decisions
- User preferences that affect business logic
- User decisions about project direction
- User corrections to knowledge
- User-approved changes

## 4. CANONICAL SOURCE RULE

```
Personal Workspace (SQLite)
  = CANONICAL
  = Single source of truth
  = Long-term memory

OpenClaw memory
  = RUNTIME-ONLY
  = Conversation continuity
  = Tool/ops lessons
  = NOT canonical
```

## 5. CONFLICT RESOLUTION

If OpenClaw memory conflicts with Personal Workspace:

1. **Personal Workspace wins.** Always.
2. OpenClaw memory is runtime-only and cannot override canonical.
3. Agnes must re-fetch facts from Personal Workspace each turn.
4. Session history is NOT a fact source.
5. If conflict detected: output CONFLICT candidate, do not auto-resolve.

### Example

- OpenClaw MEMORY.md says: 'Project X status = completed'
- Personal Workspace says: 'Project X status = in_progress'
- Result: Project X status = in_progress (Personal Workspace canonical)
- Action: Generate CONFLICT candidate for review

## 6. MEMORY HYGIENE

### Session History
- Session history provides conversation continuity only
- Key facts must be re-fetched from Personal Workspace each turn
- Do not rely on session history for factual claims

### MEMORY.md
- May contain: tool usage notes, server ops lessons, Agnes workflow experience
- Must NOT contain: business facts, project state, knowledge content
- If a business fact appears in MEMORY.md: it must be migrated to Personal Workspace and removed from MEMORY.md

### memory/YYYY-MM-DD.md
- May contain: daily runtime lessons, ops notes
- Must NOT contain: daily business decisions, project updates
- If a business decision appears in daily memory: it must be migrated to Personal Workspace

## 7. MIGRATION RULE

If prohibited content is found in OpenClaw memory:

1. Do NOT delete without verification
2. Check if content exists in Personal Workspace
3. If exists in Personal Workspace: remove from OpenClaw memory
4. If NOT in Personal Workspace: generate migration candidate → user review → apply to Personal Workspace → then remove from OpenClaw memory
5. Never auto-migrate without user confirmation

## 8. ENFORCEMENT

- This policy is a HARD RULE
- Violation = Agnes producing unverified canonical claims from memory
- Violation = OpenClaw memory overriding Personal Workspace data
- Violation = Business facts stored in OpenClaw memory without Personal Workspace backing
- All violations must be logged and reported

## 9. FUTURE REVIEW

- When persona merge (GATE 2) is executed, this policy must be referenced in SOUL.md MEMORY_BOUNDARY section
- When AgnesRuntimeAdapter is bound (GATE 5), session memory must comply with this policy
- When Brain Chat is integrated (GATE 6), chat history must comply with this policy
- Regular audit: scan OpenClaw memory files for prohibited content