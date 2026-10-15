"""PAW Current-State Admission Policy.

Deterministic.  Side-effect free.  No LLM, no DB access, no DB write.
Single source of truth for "what may enter Current Project Context".

    CURRENT != every row that exists
    CURRENT == rows that passed their governance state

The business-Project rule is NOT re-implemented here: it re-exports the
canonical definition from ``workspace_api.project_handbook`` so that exactly
one rule exists in the codebase.

Records are accepted as *mapping-like*, not strictly as ``Mapping``: the
production readers hand this module ``sqlite3.Row`` objects, which are neither
``collections.abc.Mapping`` instances nor ``.get()``-capable.  A strict Mapping
check would fail every row closed and silently empty the context.

NOTE: this module is intentionally duplicated verbatim in
/home/ubuntu/paw-dev/paw-ksheet-worker/current_context_policy.py and
/opt/personal-workspace/workspace_api/current_context_policy.py because the two
packages do not share a sys.path.  Any rule change MUST be applied to both.
"""
from __future__ import annotations

from typing import Any, Mapping

KNOWLEDGE_CURRENT = "current"
PROJECT_ACTIVE = "active"
PROJECT_ARCHIVED = "archived"
RELATION_CURRENT = "current"

# -- canonical business-Project rule (re-exported, never re-defined) --------
try:  # pragma: no cover - import root differs between the two packages
    from workspace_api.project_handbook import (  # type: ignore
        EVIDENCE_CONTAINER_NAME_MARKERS as _CANON_EVIDENCE_MARKERS,
        NON_BUSINESS_PROJECT_NAMES as _CANON_NON_BUSINESS,
        is_active_business_project as _canon_is_active_business_project,
    )

    _CANON_AVAILABLE = True
except Exception:  # noqa: BLE001
    _CANON_NON_BUSINESS = frozenset({"\u4e2a\u4eba\u751f\u6d3b"})
    _CANON_EVIDENCE_MARKERS = ()
    _canon_is_active_business_project = None
    _CANON_AVAILABLE = False

NON_BUSINESS_PROJECT_NAMES = _CANON_NON_BUSINESS
EVIDENCE_CONTAINER_NAME_MARKERS = _CANON_EVIDENCE_MARKERS

# Only explicit, strong history markers.  Generic words are deliberately
# excluded: over-triggering would leak provisional Knowledge into ordinary
# questions.
HISTORICAL_MARKERS = (
    "\u5386\u53f2\u7248\u672c", "\u4e4b\u524d\u7248\u672c", "\u65e7\u7248\u672c",
    "\u66f4\u65e9\u7684\u7248\u672c", "\u88ab\u53d6\u4ee3", "\u5f52\u6863\u7248\u672c",
    "\u539f\u7248", "\u8fc7\u5f80", "\u66fe\u7ecf", "\u5df2\u5e9f\u5f03",
    "\u5386\u53f2", "\u4ee5\u524d\u7684", "\u65e7\u7248",
    "superseded", "deprecated", "historical", "archive", "history", "legacy",
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_int(value: Any):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_record(value: Any) -> bool:
    """True for any mapping-like record.

    ``sqlite3.Row`` - the row type returned by ``workspace_api.repository`` -
    is NOT a ``collections.abc.Mapping`` and has no ``.get()``.  It does expose
    ``keys()`` and name indexing, which is the contract used here.
    """
    if isinstance(value, Mapping):
        return True
    return callable(getattr(value, "keys", None))


def record_get(source: Any, key: str, default: Any = None) -> Any:
    """Mapping-like field access that also works for ``sqlite3.Row``."""
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(key, default)
    try:
        keys = source.keys()
    except Exception:  # noqa: BLE001
        return default
    try:
        if key in keys:
            return source[key]
    except Exception:  # noqa: BLE001
        return default
    return default


def is_archived_evidence_project(project: Any) -> bool:
    """An evidence/container record masquerading as a Project."""
    if not is_record(project):
        return False
    name = _text(record_get(project, "name"))
    return any(marker in name for marker in EVIDENCE_CONTAINER_NAME_MARKERS)


def is_current_business_project(project: Any) -> bool:
    """Active business Project admitted to default Current-State synthesis.

    Archived Projects, personal-life Projects and evidence-container records
    never enter it.  Delegates to the canonical rule when importable.
    """
    if not is_record(project):
        return False
    if _canon_is_active_business_project is not None:
        try:
            probe = project if isinstance(project, Mapping) else dict(project)
            return bool(_canon_is_active_business_project(probe))
        except Exception:  # noqa: BLE001
            pass
    return (
        _text(record_get(project, "status")).lower() == PROJECT_ACTIVE
        and _text(record_get(project, "name")) not in NON_BUSINESS_PROJECT_NAMES
        and not is_archived_evidence_project(project)
    )


def knowledge_authority_state(knowledge: Any) -> str:
    """CURRENT_CONFIRMED | PROVISIONAL | REVIEW_REQUIRED | HISTORICAL_DEPRECATED
    | PROVISIONAL_UNCONFIRMED | PROVISIONAL_UNKNOWN.

    Fail closed: a ``current`` page whose latest revision is not confirmed, or
    whose confirmation is unknown, is NOT canonical.
    """
    if not is_record(knowledge):
        return "PROVISIONAL_UNKNOWN"
    status = _text(record_get(knowledge, "status")).lower()
    if status == "deprecated":
        return "HISTORICAL_DEPRECATED"
    if status == "needs_review":
        return "REVIEW_REQUIRED"
    if status == "draft":
        return "PROVISIONAL"
    if status != KNOWLEDGE_CURRENT:
        return "PROVISIONAL_UNKNOWN"
    confirmed = _as_int(record_get(knowledge, "confirmed_revision_no"))
    latest = _as_int(record_get(knowledge, "latest_revision_no"))
    if confirmed is None or latest is None:
        return "PROVISIONAL_UNCONFIRMED"
    return "CURRENT_CONFIRMED" if confirmed == latest else "PROVISIONAL_UNCONFIRMED"


def is_current_confirmed_knowledge(knowledge: Any) -> bool:
    return knowledge_authority_state(knowledge) == "CURRENT_CONFIRMED"


def is_current_relation(relation: Any) -> bool:
    if not is_record(relation):
        return False
    return _text(record_get(relation, "temporal_state")).lower() == RELATION_CURRENT


def source_version_admission(governance_status: Any) -> str:
    """INCLUDE | EXCLUDE | UNRESOLVED_FAIL_CLOSED.

    A MISSING governance row is NOT an exclusion.  PAW governance coverage is
    incomplete, so absence must fail closed with disclosure rather than
    mass-exclude SourceVersions (which would erase Project content).
    """
    state = _text(governance_status).lower()
    if state == "current":
        return "INCLUDE"
    if state in ("superseded", "archived"):
        return "EXCLUDE"
    return "UNRESOLVED_FAIL_CLOSED"


def is_historical_query(message: str | None) -> bool:
    """True only when the user explicitly asks for history / old versions."""
    text = _text(message).lower()
    if not text:
        return False
    return any(marker in text for marker in HISTORICAL_MARKERS)
