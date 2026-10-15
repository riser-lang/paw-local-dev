"""Evidence Materializer — unified Reader Result → Evidence Refs contract.

PAW v1.3D Evidence Materialization Contract Enhancement.

WHY THIS MODULE EXISTS
----------------------
`_deterministic_prepare` and `rebuild_patrol_evidence_packet` each carried their
own copy of the same two-loop evidence derivation:

    for text_blocks -> text_block ref
    for tables      -> table ref
    if not evidence_refs: WAIT_EVIDENCE_MATERIALIZATION

That binary assumption ("evidence lives in text_blocks and tables") is only
valid for DOCX. Other Reader families legitimately carry their evidence
elsewhere:

    XLSX             -> structured_semantics.sheets[].cells   (+ visual_pages)
    image-only PDF   -> visual_pages[]                          (no text layer)

The Reader had already done its job; the Evidence Adapter could not consume it,
so structurally valid sources were fail-closed forever.

This module makes the contract explicit and single-sourced. Both call sites
delegate here, so the two paths can no longer diverge.

HARD CONSTRAINTS (must not be relaxed)
--------------------------------------
* No fabricated evidence. A ref is emitted only for a locator that exists in
  the Reader Result right now.
* No invented locators. Every locator points at a concrete, re-readable path.
* Fail-closed preserved. If nothing is materializable, return `[]` and let the
  caller keep the item in `WAIT_EVIDENCE_MATERIALIZATION`.
* The Evidence Identity Gate (`_validate_evidence_refs`) is still called by the
  caller on the returned refs. Bypassing it is forbidden.
* Never convert a whole spreadsheet into a long string masquerading as
  `text_blocks`. XLSX evidence is emitted as ranged `spreadsheet_range` refs.

CONTRACT SHAPE
--------------
Every returned ref carries:
    source_version_id      identity (gate-checked)
    evidence_locator       non-empty, concrete (gate-checked)
    evidence_kind          text_block | table | spreadsheet_range | visual_page
    artifact_reference     reader_result_ref / raw_artifact_sha256 /
                           content_fingerprint (+ kind-specific detail)
    ref_type               "reader_evidence"
"""
from __future__ import annotations

from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Bounds. Deliberately modest: an Evidence Packet is a bounded pointer set for
# semantic review, not a data dump. These caps are part of the contract.
# ---------------------------------------------------------------------------
MAX_TEXT_BLOCKS = 200
MAX_TABLES = 50
MAX_SPREADSHEET_RANGES = 200
MAX_VISUAL_PAGES = 50

# Citation column used for range locators. The locator names the *sheet order*,
# and the range is expressed in the sheet's own citation.
_MAX_TITLE_IN_LOCATOR = 60


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _base_reference(
    *,
    reader_path: str,
    artifact_hash: str,
    content_fingerprint: str,
) -> dict[str, Any]:
    """The artifact_reference fields required by the Evidence Identity Gate."""
    return {
        "reader_result_ref": reader_path,
        "raw_artifact_sha256": artifact_hash,
        "content_fingerprint": content_fingerprint,
    }


def _fidelity_state(reader_data: Mapping[str, Any]) -> str | None:
    flags = reader_data.get("fidelity_flags")
    if isinstance(flags, Mapping):
        return _text(flags.get("state")) or None
    return None


def _text_block_refs(
    reader_data: Mapping[str, Any],
    *,
    sid: str,
    reader_path: str,
    artifact_hash: str,
    content_fingerprint: str,
    fidelity_state: str | None,
) -> list[dict[str, Any]]:
    """DOCX / PDF / PPTX primary path — unchanged legacy semantics."""
    refs: list[dict[str, Any]] = []
    for idx, block in enumerate(_as_list(reader_data.get("text_blocks"))[:MAX_TEXT_BLOCKS]):
        if not isinstance(block, Mapping):
            continue
        ref = {
            "source_version_id": sid,
            "evidence_locator": f"text_blocks[{idx}]",
            "evidence_kind": "text_block",
            "artifact_reference": {
                **_base_reference(
                    reader_path=reader_path,
                    artifact_hash=artifact_hash,
                    content_fingerprint=content_fingerprint,
                ),
                "block_index": block.get("index", idx),
                "block_where": _text(block.get("where")) or None,
            },
            "excerpt": (_text(block.get("text")) or None),
            "ref_type": "reader_evidence",
            "fidelity_state": fidelity_state,
        }
        refs.append(ref)
    return refs


def _table_refs(
    reader_data: Mapping[str, Any],
    *,
    sid: str,
    reader_path: str,
    artifact_hash: str,
    content_fingerprint: str,
) -> list[dict[str, Any]]:
    """DOCX / PDF / PPTX secondary path — unchanged legacy semantics."""
    refs: list[dict[str, Any]] = []
    for idx, table in enumerate(_as_list(reader_data.get("tables"))[:MAX_TABLES]):
        if not isinstance(table, Mapping):
            continue
        refs.append({
            "source_version_id": sid,
            "evidence_locator": f"tables[{idx}]",
            "evidence_kind": "table",
            "artifact_reference": {
                **_base_reference(
                    reader_path=reader_path,
                    artifact_hash=artifact_hash,
                    content_fingerprint=content_fingerprint,
                ),
                "table_index": idx,
            },
            "ref_type": "reader_evidence",
        })
    return refs


def _sheet_order(sheet: Mapping[str, Any], fallback_index: int) -> int:
    order = _as_int(sheet.get("order"))
    return order if order is not None else fallback_index


def _range_extent(cells: list[Any]) -> tuple[str | None, int | None, int | None, int | None, int | None]:
    """Derive the A1 bounding box of the *cited* cells.

    Returns (a1_range, min_row, max_row, min_col, max_col). The range is built
    from the observed cells, never from `max_row`/`max_column` sheet metadata,
    so the locator never claims cells that were not actually read.
    """
    rows: list[int] = []
    cols: list[int] = []
    coords: list[str] = []
    for cell in cells:
        if not isinstance(cell, Mapping):
            continue
        r = _as_int(cell.get("row"))
        c = _as_int(cell.get("column"))
        coord = _text(cell.get("coordinate"))
        if r is not None:
            rows.append(r)
        if c is not None:
            cols.append(c)
        if coord:
            coords.append(coord)
    if not rows or not cols:
        return (None, None, None, None, None)
    min_row, max_row = min(rows), max(rows)
    min_col, max_col = min(cols), max(cols)

    def col_name(index: int) -> str:
        name = ""
        n = index
        while n > 0:
            n, rem = divmod(n - 1, 26)
            name = chr(ord("A") + rem) + name
        return name or "?"

    a1 = f"{col_name(min_col)}{min_row}:{col_name(max_col)}{max_row}"
    return (a1, min_row, max_row, min_col, max_col)


def _cell_excerpt(cells: list[Any], limit: int = 3) -> str | None:
    """A short, factual excerpt of cited cell values. Never the whole sheet."""
    parts: list[str] = []
    for cell in cells:
        if not isinstance(cell, Mapping):
            continue
        value = cell.get("display_value")
        if value is None or _text(value) == "":
            value = cell.get("raw_value")
        if value is None or _text(value) == "":
            continue
        coord = _text(cell.get("coordinate"))
        parts.append(f"{coord}={_text(value)}" if coord else _text(value))
        if len(parts) >= limit:
            break
    return " | ".join(parts) if parts else None


def _spreadsheet_range_refs(
    reader_data: Mapping[str, Any],
    *,
    sid: str,
    reader_path: str,
    artifact_hash: str,
    content_fingerprint: str,
) -> list[dict[str, Any]]:
    """XLSX path — one ref per sheet, located by cited A1 range.

    Locator format:  sheets[<order>].cells[<A1 range>]
    The locator always carries sheet identity, the row span, the column span and
    the cell/range literal, so a reviewer can re-open the exact evidence.
    """
    refs: list[dict[str, Any]] = []
    semantics = reader_data.get("structured_semantics")
    if not isinstance(semantics, Mapping):
        return refs
    sheets = _as_list(semantics.get("sheets"))
    for sheet_index, sheet in enumerate(sheets):
        if not isinstance(sheet, Mapping):
            continue
        cells = [c for c in _as_list(sheet.get("cells")) if isinstance(c, Mapping)]
        if not cells:
            continue
        order = _sheet_order(sheet, sheet_index)
        a1, min_row, max_row, min_col, max_col = _range_extent(cells)
        if not a1:
            continue
        title = _text(sheet.get("title"))[:_MAX_TITLE_IN_LOCATOR]
        # Additive identity fields, mirroring the rest of the codebase. The
        # gate reads only source_version_id + evidence_locator.
        refs.append({
            "source_version_id": sid,
            "evidence_locator": f"sheets[{order}].cells[{a1}]",
            "evidence_kind": "spreadsheet_range",
            "artifact_reference": {
                **_base_reference(
                    reader_path=reader_path,
                    artifact_hash=artifact_hash,
                    content_fingerprint=content_fingerprint,
                ),
                "sheet_order": order,
                "sheet_title": title or None,
                "sheet_state": _text(sheet.get("sheet_state")) or None,
                "sheet_hidden": bool(sheet.get("hidden")) if "hidden" in sheet else None,
                "cell_range": a1,
                "row_span": [min_row, max_row],
                "column_span": [min_col, max_col],
                "cited_cell_count": len(cells),
            },
            "excerpt": _cell_excerpt(cells),
            "ref_type": "reader_evidence",
        })
        if len(refs) >= MAX_SPREADSHEET_RANGES:
            break
    return refs


def _visual_page_refs(
    reader_data: Mapping[str, Any],
    *,
    sid: str,
    reader_path: str,
    artifact_hash: str,
    content_fingerprint: str,
) -> list[dict[str, Any]]:
    """Rendered-page path — located by array position, with the page number recorded.

    A visual page is materializable only when it carries a durable locator.

    Locator policy (deliberately explicit, never guessed):
      * The locator is ALWAYS a 0-based index into the `visual_pages` array
        (`visual_pages[{idx}]`), matching every other locator namespace in this
        contract (`text_blocks[{idx}]`, `tables[{idx}]`). It therefore always
        resolves against the array it names.
      * A PDF page number is 1-based and is NOT the array index. Emitting it as
        the locator would produce a dangling `visual_pages[3]` for a 3-element
        array, so the page number is carried in
        `artifact_reference.visual_page_number` instead, together with
        `visual_page_index_basis`:
          - `"page_number"`  -> Reader supplied `page`; the PDF page number is
                                also recorded in `visual_page_number`.
          - `"array_position"` -> Reader supplied no `page` (XLSX sheets carry
                                `sheet`, not `page`). `visual_page_number` is null.
        A reviewer is thus never misled into reading the locator as a page number.
      * neither index nor path -> skipped (no reliable locator)

    In every case the ref stays re-readable: `visual_page_ref` (artifact path) and
    `visual_page_sha256` are carried whenever the Reader supplied them.
    """
    refs: list[dict[str, Any]] = []
    for idx, page in enumerate(_as_list(reader_data.get("visual_pages"))[:MAX_VISUAL_PAGES]):
        if not isinstance(page, Mapping):
            continue
        page_no = _as_int(page.get("page"))
        page_path = _text(page.get("path"))
        if page_no is None and not page_path:
            # No reliable locator possible -> stay fail-closed for this page.
            continue
        index = idx
        basis = "page_number" if page_no is not None else "array_position"
        refs.append({
            "source_version_id": sid,
            "evidence_locator": f"visual_pages[{index}]",
            "evidence_kind": "visual_page",
            "artifact_reference": {
                **_base_reference(
                    reader_path=reader_path,
                    artifact_hash=artifact_hash,
                    content_fingerprint=content_fingerprint,
                ),
                "visual_page_index": index,
                "visual_page_index_basis": basis,
                "visual_page_number": page_no,
                "visual_page_kind": _text(page.get("kind")) or None,
                "visual_page_sheet": _text(page.get("sheet")) or None,
                "visual_page_ref": page_path or None,
                "visual_page_sha256": _text(page.get("sha256")) or None,
                "render_mode": _text(page.get("render_mode")) or None,
            },
            "ref_type": "reader_evidence",
        })
    return refs


def materialize_evidence_refs(
    reader_data: Mapping[str, Any],
    *,
    sid: str,
    reader_path: str,
    artifact_hash: str = "",
    content_fingerprint: str = "",
) -> list[dict[str, Any]]:
    """Unified Reader Result -> Evidence Refs materializer.

    Single source of truth for both the prepare path and the rebuild path.

    Selection is *additive and capability-based*, not type-branched guesswork:
    each source is consulted, and whatever the Reader actually produced becomes
    evidence. This keeps DOCX/PDF/PPTX byte-identical to the previous behaviour
    (their text_blocks/tables are non-empty, so ordering is preserved) while
    letting XLSX and image-only PDF contribute their real carriers.

    Ordering matters for downstream stability: text_blocks -> tables ->
    spreadsheet ranges -> visual pages. For DOCX/PPTX, which emit text_blocks +
    tables, spreadsheet ranges are absent and visual pages append afterwards —
    a strict superset with identical prefix.

    Returns [] when nothing is materializable. Callers MUST treat [] as
    fail-closed (keep `WAIT_EVIDENCE_MATERIALIZATION`); they must not fabricate.
    """
    if not isinstance(reader_data, Mapping):
        return []
    if not _text(sid) or not _text(reader_path):
        # Identity is not constructible -> nothing may be materialized.
        return []

    fidelity_state = _fidelity_state(reader_data)
    refs: list[dict[str, Any]] = []
    refs.extend(
        _text_block_refs(
            reader_data,
            sid=sid,
            reader_path=reader_path,
            artifact_hash=artifact_hash,
            content_fingerprint=content_fingerprint,
            fidelity_state=fidelity_state,
        )
    )
    refs.extend(
        _table_refs(
            reader_data,
            sid=sid,
            reader_path=reader_path,
            artifact_hash=artifact_hash,
            content_fingerprint=content_fingerprint,
        )
    )
    refs.extend(
        _spreadsheet_range_refs(
            reader_data,
            sid=sid,
            reader_path=reader_path,
            artifact_hash=artifact_hash,
            content_fingerprint=content_fingerprint,
        )
    )
    refs.extend(
        _visual_page_refs(
            reader_data,
            sid=sid,
            reader_path=reader_path,
            artifact_hash=artifact_hash,
            content_fingerprint=content_fingerprint,
        )
    )
    return refs


def has_materializable_evidence(reader_data: Mapping[str, Any]) -> bool:
    """Capability probe for the Reader Gate / diagnostics.

    Deliberately NOT `text_blocks != []`. Any Reader family that can produce at
    least one citable carrier counts as materializable, so XLSX and image-only
    PDF are not misclassified as evidence-less.
    """
    if not isinstance(reader_data, Mapping):
        return False
    if _as_list(reader_data.get("text_blocks")):
        return True
    if _as_list(reader_data.get("tables")):
        return True
    semantics = reader_data.get("structured_semantics")
    if isinstance(semantics, Mapping):
        for sheet in _as_list(semantics.get("sheets")):
            if isinstance(sheet, Mapping) and any(
                isinstance(c, Mapping) for c in _as_list(sheet.get("cells"))
            ):
                return True
    for page in _as_list(reader_data.get("visual_pages")):
        if not isinstance(page, Mapping):
            continue
        if _as_int(page.get("page")) is not None or _text(page.get("path")):
            return True
    return False


def materializer_contract() -> dict[str, Any]:
    """Introspectable contract description (diagnostics / governance audit)."""
    return {
        "name": "paw_evidence_materializer",
        "version": "1.0.0",
        "evidence_schema_version": "reader-evidence/3",
        "locator_formats": {
            "text_block": "text_blocks[{idx}]",
            "table": "tables[{idx}]",
            "spreadsheet_range": "sheets[{order}].cells[{A1 range}]",
            "visual_page": "visual_pages[{array_idx}] (0-based; PDF page number in artifact_reference.visual_page_number)",
        },
        "kinds": ["text_block", "table", "spreadsheet_range", "visual_page"],
        "bounds": {
            "text_blocks": MAX_TEXT_BLOCKS,
            "tables": MAX_TABLES,
            "spreadsheet_ranges": MAX_SPREADSHEET_RANGES,
            "visual_pages": MAX_VISUAL_PAGES,
        },
        "no_fabrication": True,
        "fail_closed_on_empty": True,
        "identity_gate": "caller_must_invoke_validate_evidence_refs",
    }
