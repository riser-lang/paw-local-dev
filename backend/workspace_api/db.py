import os
import sqlite3
from pathlib import Path
from typing import Iterator

DB_PATH = Path(os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parent.parent.parent / "data" / "workspace.db")))
SCHEMA_VERSION = 7

# Source Registry v2 is deliberately additive.  Existing rows are not
# classified during migration; a verified WPS read is required before a
# caller may populate these fields.
SOURCE_REGISTRY_V2_COLUMNS = {
    "drive_id": "TEXT",
    "parent_id": "TEXT",
    "source_path": "TEXT",
    "source_kind": "TEXT",
    "source_origin": "TEXT",
    "canonical_state": "TEXT",
    "canonical_source_id": "TEXT",
    "content_hash": "TEXT",
    "wps_updated_at": "TEXT",
    "indexed_at": "TEXT",
}

# Knowledge Confirmation Gate v4 is additive.  Existing Knowledge rows keep
# their draft status and NULL confirmation fields; only an explicit human
# confirmation action may populate these columns.
KNOWLEDGE_CONFIRMATION_COLUMNS = {
    "confirmed_revision_no": "INTEGER",
    "confirmed_at": "TEXT",
    "confirmed_by": "TEXT",
}

SCHEMA_SQL = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS domain (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project (
    id TEXT PRIMARY KEY,
    domain_id TEXT,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'planning',
    stage TEXT,
    priority TEXT,
    due_date TEXT,
    next_action_summary TEXT,
    latest_progress_summary TEXT,
    privacy_level TEXT NOT NULL DEFAULT 'normal',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(domain_id) REFERENCES domain(id)
);

CREATE TABLE IF NOT EXISTS daily (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL UNIQUE,
    domain_id TEXT,
    title TEXT NOT NULL,
    wps_document_id TEXT,
    wps_url TEXT,
    sync_status TEXT NOT NULL DEFAULT 'local',
    last_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(domain_id) REFERENCES domain(id)
);

CREATE TABLE IF NOT EXISTS capture (
    id TEXT PRIMARY KEY,
    daily_id TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'chatgpt_action',
    source_device_id TEXT,
    user_edited INTEGER NOT NULL DEFAULT 0,
    sync_status TEXT NOT NULL DEFAULT 'local',
    wps_anchor TEXT,
    processed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(daily_id) REFERENCES daily(id)
);

CREATE TABLE IF NOT EXISTS todo (
    id TEXT PRIMARY KEY,
    domain_id TEXT,
    project_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT,
    due_at TEXT,
    start_at TEXT,
    waiting_for TEXT,
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_capture_id TEXT,
    ai_generated INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    wps_task_ref TEXT,
    sync_status TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(domain_id) REFERENCES domain(id),
    FOREIGN KEY(project_id) REFERENCES project(id),
    FOREIGN KEY(source_capture_id) REFERENCES capture(id)
);

CREATE TABLE IF NOT EXISTS decision (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT,
    content TEXT NOT NULL,
    effective_at TEXT,
    status TEXT NOT NULL DEFAULT 'current',
    source_capture_id TEXT,
    source_document_id TEXT,
    supersedes_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES project(id)
);

CREATE TABLE IF NOT EXISTS pending_item (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    content TEXT NOT NULL,
    due_at TEXT,
    confidence REAL,
    source_capture_id TEXT,
    source_document_id TEXT,
    resolved_at TEXT,
    resolved_as TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES project(id)
);

CREATE TABLE IF NOT EXISTS idea (
    id TEXT PRIMARY KEY,
    domain_id TEXT,
    project_id TEXT,
    title TEXT,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'captured',
    source_capture_id TEXT,
    wps_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(domain_id) REFERENCES domain(id),
    FOREIGN KEY(project_id) REFERENCES project(id)
);

CREATE TABLE IF NOT EXISTS material (
    id TEXT PRIMARY KEY,
    domain_id TEXT,
    project_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    material_type TEXT NOT NULL DEFAULT 'other',
    status TEXT NOT NULL DEFAULT 'working',
    wps_document_id TEXT,
    wps_url TEXT,
    source_document_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(domain_id) REFERENCES domain(id),
    FOREIGN KEY(project_id) REFERENCES project(id)
);

CREATE TABLE IF NOT EXISTS wiki_page (
    id TEXT PRIMARY KEY,
    domain_id TEXT,
    title TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL DEFAULT '',
    category TEXT,
    wps_document_id TEXT,
    wps_url TEXT,
    knowledge_status TEXT NOT NULL DEFAULT 'current',
    last_compiled_at TEXT,
    last_linted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    drive_id TEXT,
    parent_id TEXT,
    source_path TEXT,
    source_kind TEXT,
    source_origin TEXT,
    canonical_state TEXT,
    canonical_source_id TEXT,
    content_hash TEXT,
    wps_updated_at TEXT,
    indexed_at TEXT,
    FOREIGN KEY(domain_id) REFERENCES domain(id)
);

CREATE INDEX IF NOT EXISTS idx_capture_daily ON capture(daily_id);
CREATE INDEX IF NOT EXISTS idx_capture_processed ON capture(processed_at);
CREATE INDEX IF NOT EXISTS idx_todo_status ON todo(status);
CREATE INDEX IF NOT EXISTS idx_todo_project ON todo(project_id);
CREATE INDEX IF NOT EXISTS idx_project_status ON project(status);
CREATE INDEX IF NOT EXISTS idx_decision_project ON decision(project_id);
CREATE INDEX IF NOT EXISTS idx_pending_project ON pending_item(project_id);
CREATE INDEX IF NOT EXISTS idx_wiki_status ON wiki_page(knowledge_status);

-- Knowledge Truth v0.1.  These tables are additive: the existing wiki_page
-- table remains the Source Registry and is intentionally not rewritten.
CREATE TABLE IF NOT EXISTS knowledge_page (
    id TEXT PRIMARY KEY,
    domain_id TEXT,
    title TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    knowledge_type TEXT NOT NULL DEFAULT 'project'
        CHECK (knowledge_type IN ('project', 'domain', 'rule', 'method', 'analysis')),
    one_line_summary TEXT NOT NULL DEFAULT '',
    applicability TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'current', 'needs_review', 'deprecated')),
    last_compiled_at TEXT,
    last_reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(domain_id) REFERENCES domain(id)
);

CREATE TABLE IF NOT EXISTS knowledge_revision (
    id TEXT PRIMARY KEY,
    knowledge_id TEXT NOT NULL,
    revision_no INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    change_summary TEXT,
    compiled_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(knowledge_id, revision_no),
    FOREIGN KEY(knowledge_id) REFERENCES knowledge_page(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_source_link (
    knowledge_id TEXT NOT NULL,
    source_page_id TEXT NOT NULL,
    source_role TEXT NOT NULL DEFAULT 'supporting'
        CHECK (source_role IN ('primary', 'supporting', 'data', 'historical', 'conflicting')),
    note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(knowledge_id, source_page_id),
    FOREIGN KEY(knowledge_id) REFERENCES knowledge_page(id) ON DELETE CASCADE,
    FOREIGN KEY(source_page_id) REFERENCES wiki_page(id)
);

CREATE TABLE IF NOT EXISTS knowledge_project_link (
    knowledge_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'primary'
        CHECK (relation_type IN ('primary', 'related')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(knowledge_id, project_id),
    FOREIGN KEY(knowledge_id) REFERENCES knowledge_page(id) ON DELETE CASCADE,
    FOREIGN KEY(project_id) REFERENCES project(id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_page_status ON knowledge_page(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_page_domain ON knowledge_page(domain_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_revision_knowledge ON knowledge_revision(knowledge_id, revision_no);
CREATE INDEX IF NOT EXISTS idx_knowledge_source_link_source ON knowledge_source_link(source_page_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_project_link_project ON knowledge_project_link(project_id);
"""

def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_source_registry_v2_columns(conn: sqlite3.Connection) -> None:
    """Add Source Registry v2 columns to an existing v2 database.

    SQLite's CREATE TABLE IF NOT EXISTS does not update an already-created
    table, so the explicit column check keeps the migration safe for both a
    pristine database and the current 447-row production registry.
    """
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(wiki_page)").fetchall()
    }
    for name, sql_type in SOURCE_REGISTRY_V2_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE wiki_page ADD COLUMN {name} {sql_type}")


def _ensure_knowledge_confirmation_columns(conn: sqlite3.Connection) -> None:
    """Add Knowledge Confirmation Gate columns to an existing v3 database.

    Same additive pattern as the Source Registry v2 columns: CREATE TABLE IF
    NOT EXISTS does not alter existing tables, so check explicitly.
    """
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(knowledge_page)").fetchall()
    }
    for name, sql_type in KNOWLEDGE_CONFIRMATION_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE knowledge_page ADD COLUMN {name} {sql_type}")


def _ensure_luna_draft_projection_table(conn: sqlite3.Connection) -> None:
    """Additive Luna Draft Projection table — analysis plane only, not Canonical."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS luna_draft_projection (
            draft_id TEXT PRIMARY KEY,
            source_id TEXT,
            source_version_id TEXT NOT NULL UNIQUE,
            source_title TEXT,
            source_path TEXT,
            project_id TEXT,
            project_name TEXT,
            project_tier TEXT,
            summary TEXT,
            facts_json TEXT NOT NULL DEFAULT '[]',
            decisions_json TEXT NOT NULL DEFAULT '[]',
            metrics_json TEXT NOT NULL DEFAULT '[]',
            timeline_json TEXT NOT NULL DEFAULT '[]',
            risks_json TEXT NOT NULL DEFAULT '[]',
            ambiguities_json TEXT NOT NULL DEFAULT '[]',
            related_projects_json TEXT NOT NULL DEFAULT '[]',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            preanalysis_processor TEXT,
            preanalysis_model TEXT,
            status TEXT NOT NULL,
            authority_level TEXT NOT NULL DEFAULT 'draft',
            is_canonical INTEGER NOT NULL DEFAULT 0,
            review_bundle_id TEXT,
            canonical_entity_type TEXT,
            canonical_entity_id TEXT,
            coverage_state TEXT,
            semantic_hash TEXT,
            ingestion_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_luna_draft_project ON luna_draft_projection(project_id);
        CREATE INDEX IF NOT EXISTS idx_luna_draft_status ON luna_draft_projection(status);
        CREATE INDEX IF NOT EXISTS idx_luna_draft_source ON luna_draft_projection(source_version_id);
        CREATE INDEX IF NOT EXISTS idx_luna_draft_updated ON luna_draft_projection(updated_at);
        """
    )


def _ensure_project_handbook_schema(conn: sqlite3.Connection) -> None:
    """Add the canonical Project Handbook projection and its patrol ledger.

    The existing Project Outcome/Relation tables are deliberately left intact.
    Handbook is an additive, machine-owned projection over those records; it
    never reads or rewrites Source content.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_handbook (
            project_id TEXT PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
            schema_version TEXT NOT NULL,
            sections_json TEXT NOT NULL DEFAULT '{}',
            section_metadata_json TEXT NOT NULL DEFAULT '{}',
            generated_at TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            input_watermark TEXT NOT NULL,
            evidence_fingerprint TEXT NOT NULL DEFAULT '',
            quality_gate TEXT NOT NULL,
            evidence_sufficient INTEGER NOT NULL DEFAULT 0,
            baseline_input_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_project_handbook_quality
            ON project_handbook(quality_gate);
        CREATE INDEX IF NOT EXISTS idx_project_handbook_watermark
            ON project_handbook(input_watermark);

        CREATE TABLE IF NOT EXISTS project_handbook_event (
            event_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            change_type TEXT NOT NULL,
            changed_sections_json TEXT NOT NULL DEFAULT '[]',
            input_watermark TEXT,
            impacted_projects_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}',
            processed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_project_handbook_event_project
            ON project_handbook_event(project_id, processed_at);

        -- Minimal governance ledger for clean test databases.  Production
        -- already owns these tables; IF NOT EXISTS preserves that schema.
        CREATE TABLE IF NOT EXISTS approved_change (
            change_id TEXT PRIMARY KEY,
            target_type TEXT NOT NULL,
            operation TEXT NOT NULL,
            payload TEXT NOT NULL,
            approval_ref TEXT NOT NULL,
            source_refs TEXT,
            expected_target_version TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS commit_record (
            id TEXT PRIMARY KEY,
            change_id TEXT NOT NULL,
            transaction_id TEXT,
            result TEXT NOT NULL,
            committed_at TEXT NOT NULL,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_event (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            change_id TEXT,
            commit_id TEXT,
            details TEXT,
            created_at TEXT NOT NULL
        );
        """
    )

    # Production already has Project Outcome/Relation from the previous
    # Project-first layer, but the base migration must also work on a clean DB.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_outcome (
            project_id TEXT PRIMARY KEY REFERENCES project(id),
            schema_version TEXT NOT NULL,
            current_status TEXT,
            phase TEXT,
            executive_summary TEXT NOT NULL,
            objectives_json TEXT NOT NULL DEFAULT '[]',
            scope_json TEXT NOT NULL DEFAULT '{}',
            latest_progress_json TEXT NOT NULL DEFAULT '[]',
            completed_milestones_json TEXT NOT NULL DEFAULT '[]',
            open_items_json TEXT NOT NULL DEFAULT '[]',
            key_decisions_json TEXT NOT NULL DEFAULT '[]',
            current_effective_rules_json TEXT NOT NULL DEFAULT '[]',
            risks_json TEXT NOT NULL DEFAULT '[]',
            blockers_json TEXT NOT NULL DEFAULT '[]',
            ambiguities_json TEXT NOT NULL DEFAULT '[]',
            key_metrics_json TEXT NOT NULL DEFAULT '[]',
            canonical_knowledge_refs_json TEXT NOT NULL DEFAULT '[]',
            source_evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            related_projects_json TEXT NOT NULL DEFAULT '[]',
            unresolved_items_json TEXT NOT NULL DEFAULT '{}',
            synthesized_at TEXT NOT NULL,
            input_watermark TEXT NOT NULL,
            synthesis_revision INTEGER NOT NULL DEFAULT 1,
            evidence_fingerprint TEXT NOT NULL,
            evidence_sufficient INTEGER NOT NULL DEFAULT 0,
            quality_gate TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_relation (
            relation_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            from_project_id TEXT NOT NULL REFERENCES project(id),
            to_project_id TEXT NOT NULL REFERENCES project(id),
            relation_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            confidence TEXT NOT NULL,
            evidence_sufficiency TEXT NOT NULL,
            temporal_state TEXT NOT NULL DEFAULT 'current',
            revision INTEGER NOT NULL DEFAULT 1,
            input_watermark TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def _ensure_project_explainer_schema(conn: sqlite3.Connection) -> None:
    """Optional Level-1 Project Explainer storage (additive, no derivation).

    Each row stores a human/AI-authored plain-language explainer, never a
    machine-fabricated projection.  Absence of a row simply means the
    explainer is not ready yet; no business field is rewritten here.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_explainer (
            project_id TEXT PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
            schema_version TEXT NOT NULL DEFAULT 'paw-project-explainer/1',
            explainer_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        """
    )


def _ensure_v2_3_1_migration(conn: sqlite3.Connection) -> None:
    """v2.3.1 (Schema v7): Add search query cache, derived update audit ledger, and metadata ledger tables."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_query_cache (
            query_hash TEXT PRIMARY KEY,
            query_text TEXT NOT NULL,
            backend TEXT NOT NULL,
            results_json TEXT NOT NULL,
            relevance_score REAL NOT NULL DEFAULT 1.0,
            cached_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_search_cache_expires ON search_query_cache(expires_at);

        CREATE TABLE IF NOT EXISTS derived_update_record (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            old_value_hash TEXT,
            new_value_hash TEXT,
            source_run_id TEXT,
            model_provider TEXT,
            source_evidence_refs TEXT,
            analysis_task_id TEXT,
            details TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_derived_update_entity ON derived_update_record(entity_type, entity_id);
        """
    )


def _ensure_governance_and_runtime_schema(conn: sqlite3.Connection) -> None:
    """Ensure candidate governance, source versioning, and metric tables exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS analysis_run (
            id TEXT PRIMARY KEY,
            trigger_type TEXT NOT NULL,
            raw_text TEXT,
            raw_input_refs TEXT,
            analysis_model TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidate (
            id TEXT PRIMARY KEY,
            analysis_run_id TEXT NOT NULL,
            candidate_type TEXT NOT NULL CHECK (candidate_type IN (
                'fact', 'todo', 'project_update', 'knowledge', 'conflict', 'ambiguity', 'question', 'project_merge'
            )),
            source_refs TEXT,
            raw_excerpt TEXT,
            proposed_change TEXT,
            confidence TEXT NOT NULL CHECK (confidence IN ('HIGH', 'MEDIUM', 'LOW')),
            inference_level TEXT NOT NULL CHECK (inference_level IN (
                'EXPLICIT', 'STRONG_INFERENCE', 'WEAK_INFERENCE'
            )),
            recommended_action TEXT NOT NULL CHECK (recommended_action IN (
                'AUTO_WRITE', 'DRAFT_ONLY', 'ASK_USER', 'DO_NOT_WRITE'
            )),
            analysis_model TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                'pending', 'accepted', 'rejected', 'applied'
            )),
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by TEXT,
            applied_entity_type TEXT,
            applied_entity_id TEXT,
            dedup_fingerprint TEXT,
            last_seen_at TEXT,
            review_brief_json TEXT,
            review_display_json TEXT,
            review_internal_evidence_json TEXT,
            FOREIGN KEY (analysis_run_id) REFERENCES analysis_run(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_analysis_run ON candidate(analysis_run_id);
        CREATE INDEX IF NOT EXISTS idx_candidate_status ON candidate(status);
        CREATE INDEX IF NOT EXISTS idx_candidate_type ON candidate(candidate_type);

        CREATE TABLE IF NOT EXISTS candidate_resolution (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            resolution_type TEXT NOT NULL,
            resolution_status TEXT NOT NULL,
            user_response TEXT,
            selected_option TEXT,
            reanalysis_run_id TEXT,
            resolved_by TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            reanalysis_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_candres_candidate ON candidate_resolution(candidate_id);

        CREATE TABLE IF NOT EXISTS candidate_dedup (
            dedup_fingerprint TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT,
            FOREIGN KEY (candidate_id) REFERENCES candidate(id)
        );
        CREATE INDEX IF NOT EXISTS idx_dedup_candidate ON candidate_dedup(candidate_id);

        CREATE TABLE IF NOT EXISTS review_decision (
            id TEXT PRIMARY KEY,
            candidate_id TEXT,
            decision TEXT NOT NULL CHECK (decision IN ('approve', 'edit_and_approve', 'reject', 'defer')),
            reviewer TEXT NOT NULL,
            review_channel TEXT NOT NULL DEFAULT 'chatgpt_explicit_user',
            approval_source TEXT NOT NULL DEFAULT 'candidate_review'
                CHECK (approval_source IN ('candidate_review', 'direct_user', 'standing_user_rule')),
            original_payload TEXT,
            reviewed_payload TEXT,
            authorization_payload_hash TEXT,
            review_notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS logical_document (
            logical_document_id TEXT PRIMARY KEY,
            object_id TEXT,
            display_name TEXT,
            family_status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_version_governance (
            source_version_id TEXT PRIMARY KEY,
            logical_document_id TEXT NOT NULL REFERENCES logical_document(logical_document_id),
            governance_status TEXT NOT NULL,
            supersedes_source_version_id TEXT,
            effective_from TEXT,
            effective_to TEXT,
            authority_rank INTEGER NOT NULL DEFAULT 99,
            authority_reason TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'HIGH',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_version_decision_log (
            decision_id TEXT PRIMARY KEY,
            logical_document_id TEXT NOT NULL,
            ruling_json TEXT NOT NULL,
            approval_ref TEXT,
            change_id TEXT,
            project_ids_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metric_definition (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            unit TEXT,
            direction TEXT CHECK (direction IN ('higher_better', 'lower_better', 'neutral')),
            target_value REAL,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'deprecated')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES project(id)
        );

        CREATE TABLE IF NOT EXISTS metric_observation (
            id TEXT PRIMARY KEY,
            metric_definition_id TEXT NOT NULL,
            project_id TEXT,
            value REAL NOT NULL,
            observed_at TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (metric_definition_id) REFERENCES metric_definition(id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES project(id)
        );

        CREATE TABLE IF NOT EXISTS retrieval_document (
            document_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            revision_ref TEXT,
            title TEXT NOT NULL DEFAULT '',
            search_text TEXT NOT NULL DEFAULT '',
            authority_state TEXT,
            valid_from TEXT,
            valid_to TEXT,
            known_from TEXT,
            known_to TEXT,
            canonical INTEGER NOT NULL DEFAULT 0,
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            project_ids_json TEXT NOT NULL DEFAULT '[]',
            input_watermark TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )


def migrate() -> dict:
    with connect() as conn:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current_version} is newer than supported {SCHEMA_VERSION}"
            )
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        conn.executescript(SCHEMA_SQL)
        _ensure_source_registry_v2_columns(conn)
        _ensure_knowledge_confirmation_columns(conn)
        _ensure_luna_draft_projection_table(conn)
        _ensure_project_handbook_schema(conn)
        _ensure_project_explainer_schema(conn)
        _ensure_v2_3_1_migration(conn)
        _ensure_governance_and_runtime_schema(conn)
        from backend.pm.storage import migrate as migrate_pm
        migrate_pm(conn)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_wiki_drive_parent ON wiki_page(drive_id, parent_id);
            CREATE INDEX IF NOT EXISTS idx_wiki_source_origin ON wiki_page(source_origin);
            CREATE INDEX IF NOT EXISTS idx_wiki_canonical_state ON wiki_page(canonical_state);
            CREATE INDEX IF NOT EXISTS idx_wiki_canonical_source ON wiki_page(canonical_source_id);
            CREATE INDEX IF NOT EXISTS idx_wiki_content_hash ON wiki_page(content_hash);
            """
        )
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    return {
        "path": str(DB_PATH),
        "journal_mode": str(mode).lower(),
        "schema_version": version,
        "integrity_check": integrity,
    }

def status() -> dict:
    if not DB_PATH.exists():
        return {
            "exists": False,
            "path": str(DB_PATH),
            "schema_version": 0,
            "journal_mode": None,
        }
    with connect() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
    return {
        "exists": True,
        "path": str(DB_PATH),
        "schema_version": version,
        "journal_mode": str(mode).lower(),
        "integrity_check": integrity,
        "tables": tables,
        "size_bytes": DB_PATH.stat().st_size,
    }
