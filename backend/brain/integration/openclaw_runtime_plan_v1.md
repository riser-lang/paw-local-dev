# OPENCLAW RUNTIME RECONCILIATION PLAN V1
> Version: 1.0.0
> Created: 2026-08-17
> Status: DESIGN_ONLY — DO NOT EXECUTE
> Gate: GATE 1

## 1. CURRENT STATE

### Version Matrix

| Component | Version | Path |
|-----------|---------|------|
| CLI (pnpm shim) | 2026.6.9 | /root/.local/share/pnpm/openclaw |
| Production binary | 2026.7.1-2 | /root/.nvm/versions/node/v22.22.3/lib/node_modules/openclaw/ |
| systemd disk unit | 2026.6.9 | /etc/systemd/system/openclaw-gateway.service |
| systemd loaded ExecStart | 2026.7.1-2 (via override) | override: runtime.conf |

### systemd Unit Details

**Disk unit** (`/etc/systemd/system/openclaw-gateway.service`):
- ConditionPathExists: `/root/.local/share/pnpm/global/5/.pnpm/openclaw@2026.6.9/node_modules/openclaw/openclaw.mjs`
- ExecStart: `/root/.nvm/versions/node/v22.22.2/bin/node ... openclaw@2026.6.9 ... gateway run`
- PATH: `/root/.nvm/versions/node/v22.22.2/bin:...`

**Loaded unit** (via override `runtime.conf`):
- ExecStart (overridden): `/root/.nvm/versions/node/v22.22.3/bin/openclaw gateway run --port 18789`
- PATH (overridden): `/root/.nvm/versions/node/v22.22.3/bin:...`
- Additional drop-in: `20-tavily-env.conf` (EnvironmentFile)

### Runtime Facts

- Gateway MainPID: 735124 (stable)
- Listening: port 18789
- Node runtime: v22.22.3 (loaded), v22.22.2 (disk unit)
- ConditionPathExists still checks 2026.6.9 path (may fail on fresh start if 2026.6.9 uninstalled)

### Risk

- `OPENCLAW_RUNTIME_RECONCILIATION_REQUIRED = YES`
- Disk unit 与 loaded ExecStart 不一致
- If service restarts without override (e.g., after daemon-reload without override), may fail to start
- ConditionPathExists may block start if 2026.6.9 path removed

## 2. FUTURE RECONCILIATION PROCEDURE

> ⚠️ 以下步骤为设计文档，本轮 DO NOT EXECUTE。

### Step 1: Backup unit
```bash
# Backup current disk unit
cp /etc/systemd/system/openclaw-gateway.service \
   /etc/systemd/system/openclaw-gateway.service.bak.$(date +%Y%m%d)

# Backup override
cp -r /etc/systemd/system/openclaw-gateway.service.d/ \
   /etc/systemd/system/openclaw-gateway.service.d.bak.$(date +%Y%m%d)
```

### Step 2: Confirm 2026.7.1-2 binary
```bash
# Verify production binary exists and version matches
node -e "const p=require('/root/.nvm/versions/node/v22.22.3/lib/node_modules/openclaw/package.json'); console.log(p.version)"
# Expected: 2026.7.1-2

# Verify openclaw binary is executable
/root/.nvm/versions/node/v22.22.3/bin/openclaw --version
# Expected: 2026.7.1-2
```

### Step 3: Unify ConditionPathExists
```ini
# Update disk unit ConditionPathExists to match 2026.7.1-2
# FROM:
ConditionPathExists=/root/.local/share/pnpm/global/5/.pnpm/openclaw@2026.6.9/node_modules/openclaw/openclaw.mjs
# TO:
ConditionPathExists=/root/.nvm/versions/node/v22.22.3/lib/node_modules/openclaw/openclaw.mjs
```

### Step 4: Unify ExecStart
```ini
# Update disk unit ExecStart to match loaded override
# FROM:
ExecStart=/root/.nvm/versions/node/v22.22.2/bin/node /root/.local/share/pnpm/global/5/.pnpm/openclaw@2026.6.9/node_modules/openclaw/openclaw.mjs gateway run
# TO:
ExecStart=/root/.nvm/versions/node/v22.22.3/bin/openclaw gateway run --port 18789

# Update PATH in disk unit
# FROM:
Environment=PATH=/root/.nvm/versions/node/v22.22.2/bin:...
# TO:
Environment=PATH=/root/.nvm/versions/node/v22.22.3/bin:...
```

### Step 5: daemon-reload
```bash
systemctl daemon-reload
```

### Step 6: Restart gateway
```bash
systemctl restart openclaw-gateway
```

### Step 7: Confirm version
```bash
# Verify running version matches
journalctl -u openclaw-gateway --since '1 min ago' --no-pager | head -20
```

### Step 8: Confirm PID
```bash
systemctl show openclaw-gateway --property=MainPID
# Record new PID (should differ from 735124 after restart)
```

### Step 9: Confirm port 18789
```bash
ss -tlnp | grep 18789
# Expected: LISTEN on 18789
```

### Step 10: Confirm Agnes sessions
```bash
# Verify Agnes workspace is accessible
cat /root/.openclaw/workspace-agnes/SOUL.md | head -5

# Verify session creation works (manual test via OpenClaw CLI)
# /root/.nvm/versions/node/v22.22.3/bin/openclaw session list
```

### Step 11: Confirm provider/model
```bash
# Verify openclaw.json config
cat /root/.openclaw/openclaw.json | grep -E 'provider|model'
# Expected: mistral/mistral-small-latest (or configured provider)
```

### Step 12: Rollback condition

If ANY step fails, execute rollback:

```bash
# Restore original unit
 cp /etc/systemd/system/openclaw-gateway.service.bak.* \
   /etc/systemd/system/openclaw-gateway.service

# Restore override if needed
cp -r /etc/systemd/system/openclaw-gateway.service.d.bak.* \
  /etc/systemd/system/openclaw-gateway.service.d/

# Reload and restart
systemctl daemon-reload
systemctl restart openclaw-gateway

# Verify
systemctl show openclaw-gateway --property=MainPID
ss -tlnp | grep 18789
```

## 3. CLI SHIM UPDATE

After unit reconciliation, update CLI shim:
```bash
# Point pnpm shim to 2026.7.1-2
# Option A: Update pnpm global install
pnpm add -g openclaw@2026.7.1-2

# Option B: Create symlink
ln -sf /root/.nvm/versions/node/v22.22.3/bin/openclaw /root/.local/share/pnpm/openclaw

# Verify
openclaw --version
# Expected: 2026.7.1-2
```

## 4. POST-RECONCILIATION CHECKLIST

- [ ] Disk unit ExecStart == loaded ExecStart
- [ ] Disk unit ConditionPathExists points to 2026.7.1-2 path
- [ ] Disk unit PATH uses v22.22.3
- [ ] CLI version == 2026.7.1-2
- [ ] Gateway PID stable
- [ ] Port 18789 listening
- [ ] Agnes sessions functional
- [ ] Provider/model unchanged
- [ ] paw-actions unaffected (PID 1647917 stable)
- [ ] No Protected Core impact (SSH/22, Xray/443, Tailscale, 8443 NO_LISTENER)

## 5. SAFETY

- This plan is DESIGN ONLY
- Do NOT execute any step in this round
- Execution requires: GATE 0 (Source Pipeline) PASS + explicit user authorization
- All steps must be executed in order
- Any step failure → rollback immediately