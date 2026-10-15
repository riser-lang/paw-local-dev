"""PAW Agy Context V2 — the ordered cognition layer (spec §14).

Agy must answer from PAW's grown Project / Knowledge / Data / Relation /
Decisions layer, not from a flat file list.  This module defines the single
authoritative ordering, the authority class of each layer, and the rules that
decide what may be stated as a Formal Fact.

ORDER (highest authority first) — spec §14
------------------------------------------
 1  PROJECT_OUTCOME          project_outcome
 2  PROJECT_HANDBOOK         project_handbook
 3  CONFIRMED_KNOWLEDGE      knowledge_page (status=current) + confirmed revision
 4  PROJECT_DATA_METRIC      metric_definition / metric_observation
 5  PROJECT_RELATION         project_relation / knowledge_project_link
 6  APPROVED_DECISION        approved_change + commit_record
 7  TODO                     todo
 8  EVIDENCE_UNIT            evidence units attached to a source version
 9  SOURCE_VERSION           source_version_governance
10  HISTORICAL_SUPERSEDED    anything superseded / archived / deprecated
11  RAW_SOURCE               last resort only

Pure module: no I/O, no writes, no LLM.
"""
from __future__ import annotations

from typing import Any, Iterable

# --------------------------------------------------------------------------
# Authority classes
# --------------------------------------------------------------------------
A_FORMAL = "FORMAL_FACT"        # may be stated as a formal fact
A_CONFIRMED = "CONFIRMED"       # user-confirmed, may be stated as confirmed
A_DRAFT = "DRAFT"               # never a formal fact; must be labelled
A_EVIDENCE = "EVIDENCE"         # evidence, not a conclusion
A_RAW = "RAW_SOURCE"            # raw material, last resort
A_HISTORICAL = "HISTORICAL"     # superseded; only on explicit historical ask

# --------------------------------------------------------------------------
# The ordered context layers (spec §14)
# --------------------------------------------------------------------------
LAYERS: tuple[dict[str, Any], ...] = (
    {"rank": 1, "key": "PROJECT_OUTCOME", "table": "project_outcome",
     "authority": A_FORMAL, "temporal": "current", "canonical": True,
     "label": "项目结论"},
    {"rank": 2, "key": "PROJECT_HANDBOOK", "table": "project_handbook",
     "authority": A_FORMAL, "temporal": "current", "canonical": True,
     "label": "项目手册"},
    {"rank": 3, "key": "CONFIRMED_KNOWLEDGE", "table": "knowledge_page",
     "authority": A_CONFIRMED, "temporal": "current", "canonical": True,
     "label": "当前已确认知识"},
    {"rank": 4, "key": "PROJECT_DATA_METRIC", "table": "metric_observation",
     "authority": A_FORMAL, "temporal": "current", "canonical": True,
     "label": "当前项目数据/指标"},
    {"rank": 5, "key": "PROJECT_RELATION", "table": "project_relation",
     "authority": A_FORMAL, "temporal": "current", "canonical": True,
     "label": "当前项目关系"},
    {"rank": 6, "key": "APPROVED_DECISION", "table": "approved_change",
     "authority": A_FORMAL, "temporal": "current", "canonical": True,
     "label": "已批准决策/最近正式变更"},
    {"rank": 7, "key": "TODO", "table": "todo",
     "authority": A_FORMAL, "temporal": "current", "canonical": True,
     "label": "待办"},
    {"rank": 8, "key": "EVIDENCE_UNIT", "table": "source_version_decision_log",
     "authority": A_EVIDENCE, "temporal": "current", "canonical": False,
     "label": "证据单元"},
    {"rank": 9, "key": "SOURCE_VERSION", "table": "source_version_governance",
     "authority": A_EVIDENCE, "temporal": "current", "canonical": False,
     "label": "来源版本"},
    {"rank": 10, "key": "HISTORICAL_SUPERSEDED", "table": None,
     "authority": A_HISTORICAL, "temporal": "historical", "canonical": False,
     "label": "历史/已取代"},
    {"rank": 11, "key": "RAW_SOURCE", "table": "capture",
     "authority": A_RAW, "temporal": "current", "canonical": False,
     "label": "原始来源（兜底）"},
)

LAYER_ORDER: tuple[str, ...] = tuple(l["key"] for l in LAYERS)
LAYER_BY_KEY: dict[str, dict[str, Any]] = {l["key"]: l for l in LAYERS}
LAYER_RANK: dict[str, int] = {l["key"]: l["rank"] for l in LAYERS}

#: knowledge statuses that may be treated as confirmed
CONFIRMED_KNOWLEDGE_STATUS = ("current",)
#: knowledge statuses that are never a formal fact
DRAFT_KNOWLEDGE_STATUS = ("draft", "needs_review", "deprecated")

#: temporal states that mean "not current" and are excluded unless the user
#: explicitly asked about history
HISTORICAL_TEMPORAL_STATES = ("historical", "superseded", "archived")

#: item fields every context item must carry
ITEM_FIELDS = ("layer", "item_id", "title", "content", "authority",
               "temporal_state", "is_formal_fact", "source_ref")

#: only these layers may ever be stated as a formal fact
FORMAL_FACT_LAYERS = frozenset(
    l["key"] for l in LAYERS if l["authority"] in (A_FORMAL, A_CONFIRMED))


def layer_spec() -> dict[str, Any]:
    """Machine-readable layer specification (used by the frontend contract)."""
    return {
        "pack_version": "PAW_AGY_CONTEXT_V2",
        "order": [{"rank": l["rank"], "key": l["key"], "label": l["label"],
                   "authority": l["authority"], "temporal": l["temporal"],
                   "canonical": l["canonical"], "table": l["table"]}
                  for l in LAYERS],
        "formal_fact_layers": sorted(FORMAL_FACT_LAYERS),
        "confirmed_knowledge_status": list(CONFIRMED_KNOWLEDGE_STATUS),
        "draft_knowledge_status": list(DRAFT_KNOWLEDGE_STATUS),
        "raw_source_is_last_resort": True,
        "historical_only_on_explicit_request": True,
    }


def rank_of(layer_key: str) -> int:
    return LAYER_RANK.get(layer_key, 99)


def order_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable-sort context items by the §14 layer order.

    Within a layer, items keep their incoming order (callers pass them already
    ordered by relevance or recency).
    """
    return sorted(items, key=lambda i: rank_of(str(i.get("layer"))))


def make_item(*, layer: str, item_id: str, title: str, content: str,
              temporal_state: str | None = None,
              source_ref: str = "", authority: str | None = None,
              extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one normalised context item with its authority class attached."""
    spec = LAYER_BY_KEY.get(layer)
    if spec is None:
        raise ValueError("unknown layer: %r" % (layer,))
    auth = authority or spec["authority"]
    temporal = temporal_state or spec["temporal"]
    item = {
        "layer": layer,
        "item_id": item_id,
        "title": title,
        "content": content,
        "authority": auth,
        "temporal_state": temporal,
        "is_formal_fact": bool(auth in (A_FORMAL, A_CONFIRMED)
                               and temporal == "current"),
        "source_ref": source_ref,
    }
    if extra:
        item.update(extra)
    return item


def knowledge_authority(status: str, *, confirmed_revision_no: Any = None) -> str:
    """Map a knowledge_page.status to an authority class (spec §23).

    Draft is never a Formal Fact.  A page is CONFIRMED only when its status is
    ``current`` AND a revision has actually been confirmed.
    """
    s = (status or "").strip()
    if s in CONFIRMED_KNOWLEDGE_STATUS and confirmed_revision_no is not None:
        return A_CONFIRMED
    return A_DRAFT


def knowledge_is_formal_fact(status: str, *, confirmed_revision_no: Any = None) -> bool:
    return knowledge_authority(status, confirmed_revision_no=confirmed_revision_no) \
        == A_CONFIRMED


def relation_state(row: dict[str, Any]) -> str:
    """Classify a project_relation as current / superseded / historical / disputed."""
    ts = str(row.get("temporal_state") or "current").strip().lower()
    if ts in ("superseded", "historical", "archived"):
        return ts
    conf = str(row.get("confidence") or "").strip().upper()
    suff = str(row.get("evidence_sufficiency") or "").strip().lower()
    if conf == "LOW" or suff in ("insufficient", "low", "weak"):
        return "disputed"
    return "current"


def relation_is_formal_fact(row: dict[str, Any]) -> bool:
    return relation_state(row) == "current"


def summarise(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts per layer + the formal-fact subset — for the context header."""
    counts: dict[str, int] = {}
    for it in items:
        counts[it["layer"]] = counts.get(it["layer"], 0) + 1
    return {
        "total": len(items),
        "by_layer": counts,
        "layers_present": [l["key"] for l in LAYERS if counts.get(l["key"])],
        "formal_fact_items": sum(1 for it in items if it.get("is_formal_fact")),
        "draft_items": sum(1 for it in items if it.get("authority") == A_DRAFT),
        "historical_items": sum(1 for it in items
                                if it.get("temporal_state") != "current"),
    }


def render_context_block(items: list[dict[str, Any]], *,
                         max_items: int = 40) -> str:
    """Render the ordered context as text for the model prompt."""
    lines = ["--- PAW 上下文 V2（按权威顺序）---"]
    current_layer = ""
    for it in order_items(items)[:max_items]:
        if it["layer"] != current_layer:
            current_layer = it["layer"]
            spec = LAYER_BY_KEY[current_layer]
            lines.append("\n## %d. %s [%s]" % (spec["rank"], spec["label"],
                                               spec["authority"]))
        flag = "" if it.get("is_formal_fact") else "（非正式事实）"
        lines.append("- [%s] %s%s\n  %s" % (it["item_id"], it["title"], flag,
                                            it["content"]))
    return "\n".join(lines)
