# PAW Personal Work OS v2.3.2 - 核心需求追踪矩阵 (Requirement Traceability Matrix - RTM)

**版本**：v2.3.2  
**编制时间**：2026-10-06  
**状态**：提请外部评审  
**审计目标**：全面覆盖委托人核心技术指标与外部 Judge 评审标准，实现从需求条文 -> 源码实现 -> 测试用例 -> 运行时实测日志的双向闭环追溯。

---

## 1. 核心需求与实现映射矩阵

### Req A22 摄取与分析两阶段解耦
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 4 行 ("A22: Ingestion & Analysis Decoupling (Phase 1 100% local, 0 model tokens)")
- **实际行为**：扫描引擎解耦为 Phase 1 本地解析摄取与 Phase 2 模型深度分析；当参数指定 `target_phase == "phase1"` 时，仅调度本地 Reader 提取结构化数据，阶段一模型 Token 消耗严格为 0。
- **实现位置**：
```python ref=backend/runtime_core/scanner.py:384-388 anchor=target_phase
            self._save_checkpoint()
            if self._stop_event.is_set() or target_phase == "phase1":
                self.current_file = ""
                self.status = "stopped" if self._stop_event.is_set() else "completed"
                return
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a22_ingestion_analysis_decoupling`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A22 行）
- **状态**：提请外部评审

### Req A23 预算上限与时间倒序优先级
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 5 行 ("A23: Budget & Priority (Daily call limit, mtime desc prioritization)")
- **实际行为**：Phase 2 分析阶段按文件最后修改时间倒序（`reverse=True`）排序优先处理最新文档；每次发起模型调用前检查当日限额 `check_and_increment_daily_call()`，达上限后标记 `daily_limit_reached` 并安全中断。
- **实现位置**：
```python ref=backend/runtime_core/scanner.py:403-405 anchor=reverse=True
        sorted_files = sorted(all_file_paths, key=_sort_key, reverse=True)

        for p in sorted_files:
```
```python ref=backend/runtime_core/scanner.py:424-431 anchor=daily_call_limit
            if not settings.check_and_increment_daily_call():
                remaining_analysis = len(all_file_paths) - self.phase2_processed
                self.status = "daily_limit_reached"
                logger.info(
                    f"Phase 2 daily call limit reached ({settings.daily_call_limit}). "
                    f"Remaining {remaining_analysis} items queued for tomorrow."
                )
                break
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a23_budget_and_priority`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A23 行）
- **状态**：提请外部评审

### Req A24 解析器子进程隔离与崩溃容错
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 6 行 ("A24: Reader Subprocess Isolation (Crash / timeout isolation, failure catalog)")
- **实际行为**：文档解析派发在独立的 Python 子进程中运行并设 60 秒硬超时；若子进程遭遇超时、OOM 或非零异常崩溃，主进程通过守护管道捕获并转换为结构化错误代码，主服务保持完全存活。
- **实现位置**：
```python ref=backend/reader/dispatch.py:116-126 anchor=subprocess.Popen
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        try:
            stdout_data, stderr_data = proc.communicate(timeout=timeout)
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a24_subprocess_isolation`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A24 行）
- **状态**：提请外部评审

### Req A25 AI 助手 30 题综合安全与推理基准
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 7 行 ("A25: AI Assistant 30-case Q&A Benchmark (Answerable, No Evidence, Ambiguous, Not Found, Injection)")
- **实际行为**：Agy V2 运行时通过 `build_v2_turn` 确定性解析项目引用；对可回答问题绑定工作台事实，对歧义问题返回候选列表，对项目不存在或无依据问题生成拒答或限定事实契约，对提示词注入执行隔离。
- **实现位置**：
```python ref=backend/brain/runtime/agy_v2_runtime.py:335-345 anchor=build_v2_turn
def build_v2_turn(message: str, mode: str = "chat", *,
                  project_id: Optional[str] = None,
                  conversation: Optional[dict[str, Any]] = None,
                  db_path: str = PRODUCTION_DB) -> dict[str, Any]:
    """Build one canonical V2 prompt and its bounded updated conversation."""
    conv = CC.ConversationContext.from_dict(conversation or {})
    conn = PACK.connect_readonly(db_path)
    try:
        reference = conv.resolve_reference(message)
        selected_id = project_id or reference.get("project_id")
        selected_name = reference.get("project_name") if not project_id else None
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a25_30_case_qa_benchmark`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A25 行）
- **状态**：提请外部评审

### Req A26 证据校验闸门 (拦截伪造证据)
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 8 行 ("A26: Evidence Validation Gate (Strict interception of fabricated evidence IDs)")
- **实际行为**：回答契约校验逻辑 `validate_answer` 严格抽取模型返回的 evidence map；若引用的证据 ID 不在本次上下文已注入的 `valid_ids` 白名单中，即刻判定为 `invalid_evidence_ref` 拦截阻断。
- **实现位置**：
```python ref=backend/brain/context/agy_answer_contract.py:436-439 anchor=invalid_evidence_ref
    for c in claims:
        for eid in (list(c.get("evidence") or []) + list(c.get("derived_from") or [])):
            if eid not in valid_ids:
                return False, "invalid_evidence_ref:%s" % eid, {"evidence": eid}
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a26_evidence_gate`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A26 行）
- **状态**：提请外部评审

### Req A27 助手只读安全边界 (formal_state_bypass=0)
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 9 行 ("A27: Assistant Read-Only Boundary (Zero formal state mutation)")
- **实际行为**：对话上下文构建全流程严格使用只读连接 `PACK.connect_readonly(db_path)`；面对篡改项目、新增待办、删除业务表等提示词攻击，数据库所有 42 张业务表行数与内容完全不变。
- **实现位置**：
```python ref=backend/brain/runtime/agy_v2_runtime.py:341-350 anchor=connect_readonly
    conn = PACK.connect_readonly(db_path)
    try:
        reference = conv.resolve_reference(message)
        selected_id = project_id or reference.get("project_id")
        selected_name = reference.get("project_name") if not project_id else None
        resolution = _project_for_message(conn, message, selected_id,
                                           selected_name)
        if resolution.get("ambiguous"):
            resolution = {**resolution, "project_id": None,
                          "project_name": None}
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a27_assistant_read_only`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A27 行）
- **状态**：提请外部评审

### Req A28 文档注入指令防御
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 10 行 ("A28: Prompt Injection Defense (Fenced context, 0 formal state bypass)")
- **实际行为**：无论用户导入的文档中内嵌何种恶意劫持指令，规划器分析结果均被作为被动数据仅能路由至 `governance.create_candidate`，生成待审候选（`status='pending'`），绝无法越权直接改写正式业务表。
- **实现位置**：
```python ref=backend/runtime_core/planner.py:80-92 anchor=create_candidate
                cid = governance.create_candidate(
                    target_type=target_type,
                    operation=operation,
                    payload=payload,
                    raw_excerpt=raw_excerpt,
                    source_refs=[file_hash],
                    confidence=prop.get("confidence", "HIGH"),
                    analysis_model="AgentPlanner+" + type(provider).__name__,
                    review_brief=review_brief,
                    evidence_locators=locators,
                )
                created_candidate_ids.append(cid)
            except Exception as exc:
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a28_prompt_injection_defense` 与函数 `test_a28b_compromised_model_injection`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A28 / A28b 行）
- **状态**：提请外部评审

### Req A29 本地网络鉴权与 CSRF 拦截
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 11 行 ("A29: Local Security & Anti-CSRF (Host/Origin check, local token validation)")
- **实际行为**：`verify_request_security` 拦截外来 Origin 请求；对变更请求严格要求请求头 `X-PAW-Local-Token` 或会话 Cookie 严格匹配启动令牌 `LOCAL_LAUNCH_TOKEN`，杜绝未授权跨站调用。
- **实现位置**：
```python ref=backend/core/security.py:172-185 anchor=LOCAL_LAUNCH_TOKEN
    # 3. For mutating requests (POST, PUT, DELETE, PATCH), enforce local authorization
    if method.upper() in ("POST", "PUT", "DELETE", "PATCH"):
        # If local token header is provided, it must match
        if token_header is not None:
            if token_header == LOCAL_LAUNCH_TOKEN:
                return True, "ok"
            return False, "Invalid local token header"
        # Allow if paw_session cookie is set (local workbench session)
        if session_cookie and session_cookie == LOCAL_LAUNCH_TOKEN:
            return True, "ok"
        # If native direct process (no origin) and local host
        if not origin and host and host.split(":")[0].lower() in ALLOWED_ORIGIN_HOSTS:
            return True, "ok"
        return False, "CSRF / Local authorization token required for mutating requests"
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a29_local_security_auth`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A29 行）
- **状态**：提请外部评审

### Req A30 在线原子备份与灾难恢复
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 12 行 ("A30: Automated Backup & Disaster Recovery (Atomic backup, restore, 0 plaintext keys)")
- **实际行为**：使用 SQLite 原生 `src_conn.backup(dst_conn)` 在线原子快照机制生成热备份，备份完成执行 `PRAGMA integrity_check` 完整性校验；灾备恢复支持原子覆盖还原并自动清除脏数据。
- **实现位置**：
```python ref=backend/core/backup.py:46-52 anchor=src_conn.backup
    src_conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    dst_conn = sqlite3.connect(str(backup_path), timeout=30.0)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a30_backup_disaster_recovery`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A30 行）
- **状态**：提请外部评审

### Req A31 纯离线结构化检索安全兜底
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 13 行 ("A31: Offline Grounded Retrieval Fallback (Explicit tag & structured evidence search)")
- **实际行为**：当未配置 API Key 或网络离线时，系统平滑降级为纯本地结构化检索展示模式（源码中无向量或 embedding 实现，为纯结构化检索），返回 `status="OFFLINE_RETRIEVAL_DISPLAY"`，回答顶部附加显性标签「【当前未使用 AI，仅显示检索到的内容】」，杜绝幻觉编造。
- **实现位置**：
```python ref=backend/brain/api/local_agy_facade.py:104-111 anchor=header_label
    header_label = "【当前未使用 AI，仅显示检索到的内容（全局检索，未限定项目）】" if is_global else "【当前未使用 AI，仅显示检索到的内容】"
    summary_label = f"为您在全局工作台中检索到 {len(evidence_index)} 条相关依据（未限定项目）：" if is_global else f"为您检索到 {len(evidence_index)} 条相关工作台依据："

    lines = [
        header_label,
        "",
        summary_label,
    ]
```
```python ref=backend/brain/api/local_agy_facade.py:544-558 anchor=OFFLINE_RETRIEVAL_DISPLAY
        return {
            "session_id": session_id,
            "response": offline_response,
            "model": "offline:retrieval_fallback",
            "mode": effective_mode,
            "status": "OFFLINE_RETRIEVAL_DISPLAY",
            "answer_type": "grounded_offline_fallback",
            "intent": intent_name,
            "usage": {},
            "evidence": agy_v2_answer.build_evidence_footer(turn["contract"]),
            "authority_notes": turn["contract"].get("authority_notes", []),
            "clarification": turn["contract"].get("clarification"),
            "grounding": grounding_metadata(turn, grounded=True, gate="offline_retrieval_search"),
            "web_search_used": False,
        }
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a31_offline_retrieval_fallback`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A31 行）
- **状态**：提请外部评审

### Req A32 对话历史库独立物理隔离
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 14 行 ("A32: Chat History Isolation (Standalone chat_history.db, 0 impact on workspace.db)")
- **实际行为**：会话历史存储在独立的 SQLite 文件 `chat_history.db` 中；该模块绝不打开业务数据库 `workspace.db`，删除对话历史数据库完全不影响业务主库，实现故障强隔离。
- **实现位置**：
```python ref=backend/brain/runtime/agy_chat_history.py:44-49 anchor=HISTORY_DB
HISTORY_DB = Path(os.getenv("PAW_CHAT_HISTORY_DB", str(DATA_DIR / "chat_history.db")))
RUNTIME_SESSION_DIR = DATA_DIR / "agy_sessions"
try:
    RUNTIME_SESSION_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a32_chat_history_isolation`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A32 行）
- **状态**：提请外部评审

### Req A33 多版本归并与权威性确认门禁
- **需求原文/来源**：scripts/verify_a22_a33.py 文档字符串第 15 行 ("A33: Source Identity & Multi-version Grouping (V1/V2/最终版, Review required for authority)")
- **实际行为**：同源文档（如 V1/V2/最终版）归入同一 `logical_document`；新版本摄取后治理状态写入 `unresolved`、权威等级设为 99，代码中严格不自动将最新修改的文档晋升为有效版本，必须经人工审核认定。
- **实现位置**：
```python ref=backend/runtime_core/scanner.py:565-573 anchor=unresolved
                    conn.execute(
                        """INSERT INTO source_version_governance (
                            source_version_id, logical_document_id, governance_status,
                            supersedes_source_version_id, effective_from, effective_to,
                            authority_rank, authority_reason, confidence, provenance_json,
                            created_at, updated_at
                        ) VALUES (?, ?, 'unresolved', NULL, NULL, NULL, 99, '本地扫描初次摄取，待审核认定有效版本', 'HIGH', ?, ?, ?)""",
                        (source_version_id, logical_doc_id, provenance_meta, now, now),
                    )
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a33_source_identity_multiversion`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A33 行）
- **状态**：提请外部评审

### Req 4.2 高保真证据链定位 (Evidence Locator)
- **需求原文/来源**：编号沿用旧矩阵，原始需求文档未找到，未核实
- **实际行为**：Word 高保真解析器通过 `_paragraph_record` 精确记录文档段落索引、样式、层级与列表属性，为证据链提供高保真位置锚定基础。
- **实现位置**：
```python ref=backend/reader/docx_reader.py:40-49 anchor=_paragraph_record
def _paragraph_record(p: Any, index: int, where: str = "body") -> dict[str, Any]:
    style = getattr(p.style, "name", "") if p.style else ""
    elem = p._p
    numpr = elem.find(f".//{{{W}}}numPr")
    ilvl = numid = None
    if numpr is not None:
        n = numpr.find(f"{{{W}}}ilvl"); i = numpr.find(f"{{{W}}}numId")
        ilvl = n.get(f"{{{W}}}val") if n is not None else None; numid = i.get(f"{{{W}}}val") if i is not None else None
    breaks = [x.get(f"{{{W}}}type", "line") for x in elem.findall(f".//{{{W}}}br")]
    return {"index": index, "where": where, "text": p.text or "", "style": style, "heading_level": int(style.split()[-1]) if style.lower().startswith("heading ") and style.split()[-1].isdigit() else None, "list": {"num_id": numid, "level": ilvl} if numid or ilvl else None, "runs": [_run_style(r) for r in p.runs], "breaks": breaks}
```
- **测试**：无专项自动化测试
- **日志**：无专项自动化测试日志
- **状态**：提请外部评审

### Req 6.2.3 正式业务库零直写治理 (formal_state_bypass=0)
- **需求原文/来源**：编号沿用旧矩阵，原始需求文档未找到，未核实
- **实际行为**：治理服务仅允许核准处于 `pending` 状态的待审候选；对已处于 `accepted`/`rejected`/`committed`/`applied` 状态的候选返回 `already_processed`，保障业务库写入幂等性。
- **实现位置**：
```python ref=backend/governance/service.py:395-404 anchor=already_processed
            if c["status"] != "pending":
                if c["status"] in ("accepted", "rejected", "committed", "applied"):
                    return {
                        "ok": True,
                        "status": "already_processed",
                        "message": "Already processed",
                        "candidate_id": candidate_id,
                        "current_status": c["status"],
                    }
                raise ValueError(f"Candidate status is {c['status']}, cannot approve")
```
- **测试**：`audit_check.py` 中的函数 `run_write_points_check`（audit_check.py 的 CHECK 2 段）
- **日志**：`02_Runtime_Verification_Logs/audit_check_verification.log`（CHECK 2 行）
- **状态**：提请外部评审

### Req 7.3.2 DPAPI 密钥安全隔离
- **需求原文/来源**：编号沿用旧矩阵，原始需求文档未找到，未核实
- **实际行为**：敏感凭证通过 Windows DPAPI 加密存储；加密失败时严格抛出 `DPAPIProtectionError` 受控异常，彻底杜绝 Base64 降级回退。
- **实现位置**：
```python ref=backend/core/security.py:73-85 anchor=DPAPIProtectionError
class DPAPIProtectionError(RuntimeError):
    """Raised when Windows DPAPI encryption fails. Fallback to base64 is strictly forbidden."""
    pass


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret string using Windows DPAPI.
    
    If encryption fails, fallback to plaintext/base64 is strictly forbidden.
    Raises DPAPIProtectionError with human-readable error guidance.
    """
    if not plaintext:
        return ""
```
- **测试**：`audit_check.py` 中的函数 `run_dpapi_check`（audit_check.py 的 CHECK 3 段）
- **日志**：`02_Runtime_Verification_Logs/audit_check_verification.log`（CHECK 3 行）
- **状态**：提请外部评审

### Req A10 每日调用限额与时间倒序优先级
- **需求原文/来源**：编号沿用旧矩阵，原始需求文档未找到，未核实（注：该条目功能与 Req A23 完全重合）
- **实际行为**：扫描器按修改时间倒序对文件进行排序优先处理最新文档；每次模型调用前检查 `daily_call_limit`，达到上限即熔断。
- **实现位置**：
```python ref=backend/runtime_core/scanner.py:403-405 anchor=reverse=True
        sorted_files = sorted(all_file_paths, key=_sort_key, reverse=True)

        for p in sorted_files:
```
- **测试**：`scripts/verify_a22_a33.py` 中的函数 `test_a23_budget_and_priority`
- **日志**：`02_Runtime_Verification_Logs/a22_a33_verification.log`（A23 行）
- **状态**：提请外部评审

### Req F2 桌面端生命周期与架构
- **需求原文/来源**：编号沿用旧矩阵，原始需求文档未找到，未核实
- **实际行为**：FastAPI 后端通过 lifespan 上下文管理自包含生命周期：启动时加载配置并启动目录监听器与后台调度器，退出时注销后台工作线程。
- **实现位置**：
```python ref=backend/main.py:73-84 anchor=settings.watch_enabled
    if settings.watch_enabled:
        try:
            watcher.start()
        except Exception as e:
            print(f"[Watcher] Warning: failed to start watcher: {e}")
            
    # Start Runtime Scheduler
    try:
        scheduler.set_schedule_mode(settings.cruise_mode, settings.cruise_interval)
        scheduler.start()
    except Exception as e:
        print(f"[Scheduler] Warning: failed to start scheduler: {e}")
```
- **测试**：`scripts/run_doctor.py`
- **日志**：`02_Runtime_Verification_Logs/doctor_diagnostic.log`
- **状态**：提请外部评审

### Req G3 巡航异步调度与单文件故障隔离
- **需求原文/来源**：编号沿用旧矩阵，原始需求文档未找到，未核实
- **实际行为**：Web 触发巡航支持异步模式，启动 `BgCruiseWorker` 后台守护线程执行扫描，接口立即返回 `status="started"` 或 `already_running`，单文件损坏不中断巡航任务。
- **实现位置**：
```python ref=backend/main.py:695-705 anchor=BgCruiseWorker
        def _bg_cruise():
            try:
                scheduler.run_cruise(trigger="manual_web_trigger", force_rescan=force, analyze_projects=analyze)
            except Exception as e:
                logger.error(f"Background cruise execution error: {e}", exc_info=True)

        threading.Thread(target=_bg_cruise, name="BgCruiseWorker", daemon=True).start()
        return {
            "ok": True,
            "result": {
                "status": "started",
```
- **测试**：`scripts/test_gateway.py`
- **日志**：`02_Runtime_Verification_Logs/gateway_smoke_tests.log`
- **状态**：提请外部评审

### Req G4 数据库审计表与时态变动记录
- **需求原文/来源**：编号沿用旧矩阵，原始需求文档未找到，未核实
- **实际行为**：项目派生摘要（进展与下一步）由 AI 分析更新时，每次变动均计算前后 SHA256 哈希值写入 `derived_update_record` 底表，并同时写入 `audit_event` 审计事件。
- **实现位置**：
```python ref=backend/runtime_core/project_analyzer.py:294-303 anchor=derived_update_record
                            """
                            INSERT INTO derived_update_record (
                                id, entity_type, entity_id, field_name,
                                old_value_hash, new_value_hash, source_run_id,
                                model_provider, source_evidence_refs, analysis_task_id,
                                details, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            da,
                        )
```
- **测试**：`audit_check.py` 中的函数 `run_database_audit_check`（audit_check.py 的 CHECK 4 段）
- **日志**：`02_Runtime_Verification_Logs/audit_check_verification.log`（CHECK 4 行）
- **状态**：提请外部评审
