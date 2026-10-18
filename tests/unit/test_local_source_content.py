import hashlib
import json

from backend.workspace_api import source_content


def test_local_scan_markdown_detail_reads_original_text(tmp_path):
    path = tmp_path / "说明.md"
    path.write_text("扫描资料正文原文。", encoding="utf-8")

    assert source_content.local_scan_source_content({
        "source_origin": "local_scan",
        "source_path": str(path),
        "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
    }) == "扫描资料正文原文。"


def test_local_scan_office_detail_reads_hash_verified_extraction(tmp_path, monkeypatch):
    path = tmp_path / "计划.docx"
    path.write_bytes(b"fixture office document")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    extracted = tmp_path / f"计划_{digest[:8]}"
    extracted.mkdir()
    (extracted / "reader_result.json").write_text(json.dumps({
        "raw_artifact_sha256": digest,
        "text_blocks": [{"text": "已扫描提取的正文"}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(source_content, "EVIDENCE_DIR", tmp_path)

    assert source_content.local_scan_source_content({
        "source_origin": "local_scan",
        "source_path": str(path),
        "content_hash": digest,
    }) == "已扫描提取的正文"


def test_local_scan_office_detail_rejects_wrong_hash(tmp_path, monkeypatch):
    path = tmp_path / "计划.docx"
    path.write_bytes(b"fixture office document")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    extracted = tmp_path / f"计划_{digest[:8]}"
    extracted.mkdir()
    (extracted / "reader_result.json").write_text(json.dumps({
        "raw_artifact_sha256": "0" * 64,
        "text_blocks": [{"text": "must not be served"}],
    }), encoding="utf-8")
    monkeypatch.setattr(source_content, "EVIDENCE_DIR", tmp_path)

    assert source_content.local_scan_source_content({
        "source_origin": "local_scan",
        "source_path": str(path),
        "content_hash": digest,
    }) is None
