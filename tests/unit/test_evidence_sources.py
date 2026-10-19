"""R-267: answers that cite a knowledge page also name its original file."""
import json

from backend.pm import evidence_sources as sources


def test_clean_source_name_drops_a_machine_id():
    assert sources.clean_source_name("星火导师计划_10月周报.pptx(None)") == "星火导师计划_10月周报.pptx"
    assert sources.clean_source_name("方案_v2.docx(abc-123)") == "方案_v2.docx"
    assert sources.clean_source_name("方案_v2.docx（abc-123）") == "方案_v2.docx"


def test_clean_source_name_keeps_human_parentheses():
    value = "盘点(第二轮).docx"
    assert sources.clean_source_name(value) == value
    assert sources.clean_source_name("") == ""


def test_source_files_skip_api_paths_and_duplicates():
    item = {
        "provenance": ["星火导师计划_10月周报.pptx(None)"],
        "source_refs": ["星火导师计划_10月周报.pptx(None)", "/v1/pm/sources/abc"],
    }
    assert sources.source_files_for(item) == ["星火导师计划_10月周报.pptx"]


def test_files_for_cited_only_returns_the_cited_items():
    items = [
        {"layer": "KNOWLEDGE", "item_id": "k1", "title": "【知识】10月周报",
         "provenance": ["星火导师计划_10月周报.pptx(None)"]},
        {"layer": "SOURCE", "item_id": "s1", "title": "别的资料",
         "source_refs": ["其它文件.docx(None)"]},
    ]
    assert sources.files_for_cited(items, ["KNOWLEDGE:k1"]) == ["星火导师计划_10月周报.pptx"]
    assert sources.files_for_cited(items, []) == ["星火导师计划_10月周报.pptx", "其它文件.docx"]


def test_evidence_key_accepts_item_id_or_entity_id():
    assert sources.evidence_key({"layer": "KNOWLEDGE", "item_id": "k1"}) == "KNOWLEDGE:k1"
    assert sources.evidence_key({"layer": "SOURCE", "entity_id": "e1"}) == "SOURCE:e1"
    assert sources.evidence_key({}) == ""


def test_cited_ids_from_map_reads_facts_and_advice():
    answer_map = {"claims": [
        {"kind": "fact", "text": "x", "evidence": ["KNOWLEDGE:k1"]},
        {"kind": "recommendation", "text": "y", "derived_from": ["SOURCE:s1", "KNOWLEDGE:k1"]},
    ]}
    assert sources.cited_ids_from_map(answer_map) == ["KNOWLEDGE:k1", "SOURCE:s1"]
    assert sources.cited_ids_from_map(None) == []


def test_footer_appends_only_files_the_answer_does_not_name():
    text = "回答正文，引用了 星火导师计划_10月周报.pptx 的结论。"
    assert sources.append_sources_footer(text, ["星火导师计划_10月周报.pptx"]) == text
    appended = sources.append_sources_footer("回答正文。", ["星火导师计划_10月周报.pptx"])
    assert appended == "回答正文。\n\n来源：星火导师计划_10月周报.pptx"


def test_footer_is_appended_after_existing_sources_without_removing_them():
    text = "回答正文。"
    out = sources.append_sources_footer(text, ["星火导师计划_10月周报.pptx", "青禾试点周报.pptx"])
    assert out.startswith(text)
    assert "星火导师计划_10月周报.pptx" in out and "青禾试点周报.pptx" in out


def test_evidence_footer_carries_the_original_file_for_a_knowledge_entry():
    from backend.brain.context.agy_answer_contract import build_evidence_footer

    contract = {
        "evidence_index": [
            {"evidence_id": "KNOWLEDGE:k1", "layer": "KNOWLEDGE", "title": "【知识】10月周报",
             "authority": "canonical", "temporal_state": "current", "is_formal_fact": True},
            {"evidence_id": "SOURCE:s1", "layer": "SOURCE", "title": "普通资料",
             "authority": "evidence", "temporal_state": "current", "is_formal_fact": False},
        ],
        "context_items": [
            {"layer": "KNOWLEDGE", "item_id": "k1", "title": "【知识】10月周报",
             "provenance": ["星火导师计划_10月周报.pptx(None)"]},
            {"layer": "SOURCE", "item_id": "s1", "title": "普通资料", "source_refs": []},
        ],
    }
    footer = build_evidence_footer(contract)
    assert footer["label"] == "依据：2 条"
    by_title = {item["title"]: item for item in footer["items"]}
    assert by_title["【知识】10月周报"]["source_files"] == ["星火导师计划_10月周报.pptx"]
    assert by_title["普通资料"]["source_files"] == []


def _knowledge_db():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE candidate (applied_entity_id TEXT, source_refs TEXT, created_at TEXT, candidate_type TEXT)")
    conn.execute("CREATE TABLE pm_source (source_version_id TEXT, title TEXT)")
    return conn


def test_knowledge_source_files_resolve_through_the_creation_candidate():
    conn = _knowledge_db()
    conn.execute("INSERT INTO pm_source VALUES ('abc123fullhash', '星火导师计划_10月周报.pptx')")
    conn.execute(
        "INSERT INTO candidate VALUES ('k1', ?, '2026-10-19T00:00:00', 'knowledge')",
        (json.dumps(["abc123fullhash"]),),
    )
    assert sources.knowledge_source_files(conn, "k1") == ["星火导师计划_10月周报.pptx"]


def test_knowledge_source_files_accept_a_hash_prefix_and_skip_unknown_sources():
    conn = _knowledge_db()
    conn.execute("INSERT INTO pm_source VALUES ('deadbeef00112233', '方案.docx')")
    conn.execute(
        "INSERT INTO candidate VALUES ('k2', ?, '2026-10-19T00:00:00', 'knowledge')",
        (json.dumps(["deadbeef00112233", "missinghash"]),),
    )
    assert sources.knowledge_source_files(conn, "k2") == ["方案.docx"]


def test_knowledge_source_files_is_quiet_without_a_trail():
    conn = _knowledge_db()
    assert sources.knowledge_source_files(conn, "unknown") == []
    assert sources.knowledge_source_files(None, "k1") == []
