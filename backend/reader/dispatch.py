from __future__ import annotations

from pathlib import Path

from .airpage_reader import read_airpage
from .contract import ReaderInput, ReaderResult
from .docx_reader import read_docx
from .legacy_reader import read_legacy
from .pdf_reader import read_pdf
from .pptx_reader import read_pptx
from .xlsx_reader import read_xlsx


def reader_contract_current(family: str, reader_version: str, parser_version: str) -> bool:
    """Return whether a checkpoint result belongs to the current Reader contract."""
    family = family.upper()
    reader_prefixes = {
        "PPTX": "pptx-ooxml+visual-proxy/0.3.1",
        "PDF": "pypdf+ghostscript/0.1.0",
        "DOCX": "python-docx+ooxml/0.1.0",
        "XLSX": "xlsx-ooxml/0.2.0",
        "AIRPAGE": "airpage-normalized-json/0.2.0",
        "LEGACY": "legacy-raw+validated-conversion/0.1.0",
    }
    parser_prefixes = {
        "XLSX": ("openpyxl+ooxml+Pillow/2", "stdlib-ooxml+Pillow/1"),
        "AIRPAGE": ("python-json+airpage-normalizer/2",),
    }
    prefix = reader_prefixes.get(family, "")
    if not prefix or reader_version != prefix:
        return False
    allowed = parser_prefixes.get(family)
    return parser_version.startswith(allowed) if allowed else bool(parser_version)


from .contract import ReaderInput, ReaderResult, artifact_sha256


def read_txt(inp: ReaderInput, output_dir: str | Path) -> ReaderResult:
    p = Path(inp.path)
    text = p.read_text(encoding="utf-8", errors="replace")
    sha256 = inp.raw_artifact_sha256 or artifact_sha256(p)
    blocks = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for idx, line in enumerate(lines):
        is_heading = line.startswith("#") or (line.startswith("【") and line.endswith("】")) or (len(line) < 30 and (line.endswith("：") or line.endswith(":")))
        blocks.append({
            "index": idx,
            "text": line,
            "heading_level": 1 if is_heading else 0,
            "style": "Heading 1" if is_heading else "Normal",
        })
    res = ReaderResult(
        source_object_id=inp.source_object_id,
        source_version=inp.source_version,
        reader_type="plain-text/0.1.0",
        raw_artifact_sha256=sha256,
        text_blocks=blocks,
        structured_semantics={"line_count": len(lines), "char_count": len(text)},
        parser_version="stdlib-txt/1.0",
        raw_source_available=True,
    )
    return res.finalize()


def read_artifact(inp: ReaderInput, output_dir: str | Path) -> ReaderResult:
    """Dispatch a committed artifact; no acquisition or Formal State access."""
    if not inp.committed:
        raise ValueError("Reader requires an already COMMITTED artifact")
    ext = Path(inp.path).suffix.lower()
    if ext == ".pptx": result = read_pptx(inp, output_dir)
    elif ext == ".pdf": result = read_pdf(inp, output_dir)
    elif ext == ".docx": result = read_docx(inp, output_dir)
    elif ext in {".xlsx", ".xlsm"}: result = read_xlsx(inp, output_dir)
    elif ext in {".doc", ".xls"}: result = read_legacy(inp, output_dir)
    elif ext in {".json", ".semantic"}: result = read_airpage(inp, output_dir)
    elif ext in {".txt", ".md", ".text"}: result = read_txt(inp, output_dir)
    else:
        raise ValueError(f"unsupported reader extension: {ext}")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    result.write_json(Path(output_dir) / "reader_result.json")
    return result


import json
import subprocess
import sys
import tempfile


def isolated_read_artifact(inp: ReaderInput, output_dir: str | Path, timeout: int = 60) -> tuple[bool, Optional[ReaderResult], Optional[str], Optional[str]]:
    """Requirement R2: Parse document in an isolated subprocess with watchdog timeout and crash isolation.

    Returns: (ok, reader_result, failure_code, failure_reason)
    """
    out_p = Path(output_dir)
    out_p.mkdir(parents=True, exist_ok=True)

    # 1. Create temporary parameter file
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({
            "path": str(Path(inp.path).resolve()),
            "source_object_id": inp.source_object_id,
            "source_version": inp.source_version,
            "display_name": inp.display_name,
            "raw_artifact_sha256": inp.raw_artifact_sha256,
        }, f, ensure_ascii=False)
        inp_json_path = f.name

    try:
        # Run worker in separate Python process (F2: Frozen environment uses self binary with flag)
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--reader-worker", inp_json_path, str(out_p)]
        else:
            cmd = [sys.executable, "-m", "backend.reader.subprocess_worker", inp_json_path, str(out_p)]
        from backend.pm.reader_process import process_options
        try:
            proc = subprocess.Popen(cmd, **process_options())
        except OSError as exc:
            return False, None, 'SUBPROCESS_START_FAILED', '解析进程无法启动（系统错误码：' + str(getattr(exc, 'winerror', None) or exc.errno) + '）'

        try:
            stdout_data, stderr_data = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return False, None, "TIMEOUT", f"文档高保真解析超时（已超过 {timeout} 秒），为避免影响其他工作已安全跳过"

        if proc.returncode != 0:
            err_details = stderr_data.strip()[:180] or f"退出代码 {proc.returncode}"
            if "MemoryError" in err_details:
                return False, None, "OUT_OF_MEMORY", "文档解析消耗内存超限，子进程已安全熔断"
            elif "BadZipFile" in err_details:
                return False, None, "CORRUPTED", "文档内部结构损坏，非标准压缩文档"
            elif "password" in err_details.lower():
                return False, None, "ENCRYPTED", "文档受密码保护或加密，无法读取"
            return False, None, "SUBPROCESS_CRASH", f"解析子进程异常退出: {err_details}"

        # Parse success output
        res_file = out_p / "reader_result.json"
        if res_file.is_file():
            try:
                res_dict = json.loads(res_file.read_text(encoding="utf-8"))
                result_obj = ReaderResult(
                    source_object_id=res_dict.get("source_object_id", inp.source_object_id),
                    source_version=res_dict.get("source_version", inp.source_version),
                    reader_type=res_dict.get("reader_type", "UNKNOWN"),
                    raw_artifact_sha256=res_dict.get("raw_artifact_sha256", inp.raw_artifact_sha256),
                    structured_semantics=res_dict.get("structured_semantics", {}),
                    text_blocks=res_dict.get("text_blocks", []),
                    tables=res_dict.get("tables", []),
                    visual_pages=res_dict.get("visual_pages", []),
                    relationships=res_dict.get("relationships", []),
                    embedded_objects=res_dict.get("embedded_objects", []),
                    warnings=res_dict.get("warnings", []),
                    fidelity_flags=res_dict.get("fidelity_flags", {}),
                )
                if not result_obj.fidelity_flags.get("structure_parsed", True):
                    warn_text = "; ".join(result_obj.warnings) if result_obj.warnings else "文档结构损坏，无法解析"
                    return False, result_obj, "CORRUPTED", f"文档内部结构损坏，高保真解析失败: {warn_text}"
                return True, result_obj, None, None
            except Exception as e:
                return False, None, "RESULT_READ_ERROR", f"读取解析产物失败: {str(e)}"

        return False, None, "NO_OUTPUT", "解析子进程未生成有效产物"

    finally:
        try:
            Path(inp_json_path).unlink(missing_ok=True)
        except Exception:
            pass

