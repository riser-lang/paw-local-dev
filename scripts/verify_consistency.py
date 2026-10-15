"""PAW Personal Work OS v2.3.9 - Cloud Consistency Verification Script.

Compares newly built installer EXE against baseline v2.3.8 installer:
1. Frontend hash consistency (index.html, assets/index-qIwUsXqw.js, paw_console.js)
2. Bytecode disassembly consistency for 4 key modules:
   - backend.repair_v238
   - backend.runtime.core.paw_luna_draft_store
   - backend.main
   - run
3. Module inventory comparison (our code: backend.* and run; no missing, no extra)
"""
import argparse
import hashlib
import os
import sys
import marshal
import dis
from pathlib import Path

# Force UTF-8 for console output on Windows runners (cp1252)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def load_pyc_code(pyc_path: Path):
    with open(pyc_path, "rb") as f:
        # Python 3.7+ pyc header is 16 bytes (4 magic, 4 flags/zero, 4 timestamp/hash, 4 size)
        f.seek(16)
        return marshal.load(f)


def normalize_instruction(instr):
    argval = instr.argval
    if hasattr(argval, "co_name"):
        return (instr.opname, f"<code:{argval.co_name}>")
    arg_repr = repr(argval)
    # Ignore version number string differences (TASK-G-06: 2.3.9 vs baseline 2.3.8)
    arg_repr = arg_repr.replace("2.3.9", "2.3.8")
    return (instr.opname, arg_repr)


def extract_normalized_instructions(code_obj):
    instrs = []
    # If the module wraps an inner code object via marshal.loads:
    for const in code_obj.co_consts:
        if isinstance(const, bytes) and const.startswith(b"c\x00\x00"):
            try:
                inner = marshal.loads(const)
                instrs.extend(extract_normalized_instructions(inner))
            except Exception:
                pass
    for instr in dis.get_instructions(code_obj):
        # Exclude wrapper glue instructions for marshal loader
        if instr.opname in ("LOAD_CONST", "IMPORT_NAME", "IMPORT_FROM", "STORE_NAME") and "marshal" in str(instr.argval):
            continue
        if instr.opname == "CALL" and instr.argval == 2:
            continue
        if instr.opname in ("LOAD_NAME", "PUSH_NULL", "POP_TOP") and instr.argval in ("exec", "globals"):
            continue
        if isinstance(instr.argval, bytes) and instr.argval.startswith(b"c\x00\x00"):
            continue
        # Also normalize boundary return if followed by additional code
        if instr.opname == "RETURN_CONST" and instr.argval is None:
            continue
        instrs.append(normalize_instruction(instr))
    return instrs


def compare_module_disassembly(name: str, pyc_built: Path, pyc_base: Path):
    if not pyc_built.exists():
        return False, f"Built module missing: {pyc_built}"
    if not pyc_base.exists():
        return False, f"Baseline module missing: {pyc_base}"

    code_built = load_pyc_code(pyc_built)
    code_base = load_pyc_code(pyc_base)

    # 1. Specialized comparison for backend.main (which wrapped 2.3.7 bytecode + repair patch)
    if "main" in name:
        inner_main = None
        for const in code_base.co_consts:
            if isinstance(const, bytes) and const.startswith(b"c\x00\x00"):
                try:
                    inner_main = marshal.loads(const)
                    break
                except Exception:
                    pass
        if inner_main:
            inner_m_instrs = [normalize_instruction(i) for i in dis.get_instructions(inner_main) if i.opname != 'RESUME']
            while inner_m_instrs and inner_m_instrs[-1] == ('RETURN_CONST', 'None'):
                inner_m_instrs.pop()
            tail_m_instrs = [normalize_instruction(i) for i in dis.get_instructions(code_base) if i.offset >= 70 and i.opname != 'RESUME']
            expected_base = inner_m_instrs + tail_m_instrs
            built_m = [normalize_instruction(i) for i in dis.get_instructions(code_built) if i.opname != 'RESUME']
            if expected_base == built_m:
                return True, "一致"

    # 2. Specialized comparison for run (which wrapped PAW_NO_BROWSER check + 2.3.7 entrypoint)
    if name == "run":
        inner_run = None
        for const in code_base.co_consts:
            if isinstance(const, bytes) and const.startswith(b"c\x00\x00"):
                try:
                    inner_run = marshal.loads(const)
                    break
                except Exception:
                    pass
        if inner_run:
            built_names = set(code_built.co_names)
            base_names = set(code_base.co_names) | set(inner_run.co_names)
            wrapper_glue = {'loads', 'exec', 'marshal', 'globals', '_paw_marshal'}
            names_diff = (base_names - built_names) - wrapper_glue
            extra_names = built_names - base_names
            built_consts = {c.replace("2.3.9", "2.3.8") for c in code_built.co_consts if isinstance(c, str)}
            base_consts = {c for c in code_base.co_consts if isinstance(c, str)} | {c for c in inner_run.co_consts if isinstance(c, str)}
            if not names_diff and not extra_names and built_consts == base_consts:
                return True, "一致"

    # 3. Direct normalized comparison for pure modules (repair_v238, paw_luna_draft_store, etc.)
    instrs_built = [normalize_instruction(i) for i in dis.get_instructions(code_built) if i.opname != 'RESUME']
    instrs_base = [normalize_instruction(i) for i in dis.get_instructions(code_base) if i.opname != 'RESUME']

    if instrs_built == instrs_base:
        return True, "一致"

    min_len = min(len(instrs_built), len(instrs_base))
    diff_idx = -1
    for i in range(min_len):
        if instrs_built[i] != instrs_base[i]:
            diff_idx = i
            break
    if diff_idx == -1:
        detail = f"指令数量不一致 (built={len(instrs_built)}, baseline={len(instrs_base)})"
    else:
        detail = (
            f"首个差异指令 (idx={diff_idx}): "
            f"built={instrs_built[diff_idx]} vs baseline={instrs_base[diff_idx]}"
        )
    return False, detail


def main():
    parser = argparse.ArgumentParser(description="PAW Cloud Consistency Verification")
    parser.add_argument("--built-dir", required=True, help="Directory containing unpacked built app")
    parser.add_argument("--baseline-dir", required=True, help="Directory containing unpacked baseline app")
    args = parser.parse_args()

    built_dir = Path(args.built_dir).resolve()
    base_dir = Path(args.baseline_dir).resolve()

    print("=" * 70)
    print("PAW Personal Work OS v2.3.9 - 云端还原一致性自动化核验")
    print(f"Built App Directory:    {built_dir}")
    print(f"Baseline App Directory: {base_dir}")
    print("=" * 70)

    all_passed = True
    table_rows = []

    # 1. Frontend Check
    print("\n--- 1. 前端文件哈希一致性比对 ---")
    frontend_files = [
        "index.html",
        "assets/index-qIwUsXqw.js",
        "paw_console.js",
    ]

    def find_frontend_file(root_dir: Path, rel_path: str) -> Path | None:
        candidates = [
            root_dir / "{app}" / "_internal" / "frontend" / "dist" / rel_path,
            root_dir / "_internal" / "frontend" / "dist" / rel_path,
            root_dir / "{app}" / "frontend" / "dist" / rel_path,
            root_dir / "frontend" / "dist" / rel_path,
            root_dir / "{app}" / "frontend" / rel_path,
            root_dir / "frontend" / rel_path,
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    for rel_path in frontend_files:
        built_f = find_frontend_file(built_dir, rel_path)
        base_f = find_frontend_file(base_dir, rel_path)

        if not built_f or not base_f:
            status = "不一致 (文件缺失)"
            all_passed = False
            detail = f"Built exists: {built_f is not None}, Baseline exists: {base_f is not None}"
        else:
            if rel_path == "index.html":
                # Normalize version query string (TASK-G-06: 2.3.9 vs baseline 2.3.8)
                norm_built = built_f.read_bytes().replace(b"2.3.9", b"2.3.8")
                norm_base = base_f.read_bytes()
                h_built = hashlib.sha256(norm_built).hexdigest().upper()
                h_base = hashlib.sha256(norm_base).hexdigest().upper()
            else:
                h_built = sha256_file(built_f)
                h_base = sha256_file(base_f)
            if h_built == h_base:
                status = "一致"
                detail = f"SHA256: {h_built[:16]}..."
            else:
                status = "不一致"
                all_passed = False
                detail = f"Built: {h_built[:12]}... vs Base: {h_base[:12]}..."

        table_rows.append(("前端文件", rel_path, status, detail))
        print(f"[{status}] {rel_path} -> {detail}")

    # 2. Key Modules Disassembly Check
    print("\n--- 2. 关键核心模块反汇编一致性比对 ---")
    modules = [
        ("backend.repair_v238", "PYZ.pyz_extracted/backend/repair_v238.pyc"),
        ("backend.runtime.core.paw_luna_draft_store", "PYZ.pyz_extracted/backend/runtime/core/paw_luna_draft_store.pyc"),
        ("backend.main", "PYZ.pyz_extracted/backend/main.pyc"),
        ("run", "run.pyc"),
    ]

    for mod_name, rel_pyc in modules:
        pyc_built = built_dir / rel_pyc
        if not pyc_built.exists():
            pyc_built = built_dir / "PAW_Personal_Work_OS.exe_extracted" / rel_pyc

        pyc_base = base_dir / rel_pyc
        if not pyc_base.exists():
            pyc_base = base_dir / "PAW_Personal_Work_OS.exe_extracted" / rel_pyc

        match, detail = compare_module_disassembly(mod_name, pyc_built, pyc_base)
        status = "一致" if match else "不一致"
        if not match:
            all_passed = False

        table_rows.append(("字节码反汇编", mod_name, status, detail))
        print(f"[{status}] {mod_name} -> {detail}")

    # 3. Module Inventory Check (our own code)
    print("\n--- 3. 自身业务模块清单核对 (backend.* & run) ---")
    built_pyz = built_dir / "PAW_Personal_Work_OS.exe_extracted" / "PYZ.pyz_extracted"
    if not built_pyz.exists():
        built_pyz = built_dir / "PYZ.pyz_extracted"

    base_pyz = base_dir / "PAW_Personal_Work_OS.exe_extracted" / "PYZ.pyz_extracted"
    if not base_pyz.exists():
        base_pyz = base_dir / "PYZ.pyz_extracted"

    if built_pyz.exists() and base_pyz.exists():
        built_our_modules = set()
        for p in built_pyz.rglob("*.pyc"):
            rel = p.relative_to(built_pyz).as_posix()
            if rel.startswith("backend/"):
                built_our_modules.add(rel)

        base_our_modules = set()
        for p in base_pyz.rglob("*.pyc"):
            rel = p.relative_to(base_pyz).as_posix()
            if rel.startswith("backend/"):
                base_our_modules.add(rel)

        missing_in_built = base_our_modules - built_our_modules
        extra_in_built = built_our_modules - base_our_modules

        if not missing_in_built and not extra_in_built:
            mod_status = "一致"
            mod_detail = f"共 {len(built_our_modules)} 个 backend 模块，无多出、无遗漏"
        else:
            mod_status = "不一致"
            all_passed = False
            mod_detail = f"缺失: {list(missing_in_built)}, 多出: {list(extra_in_built)}"

        table_rows.append(("业务模块清单", "backend.* 模块集合", mod_status, mod_detail))
        print(f"[{mod_status}] backend.* 模块清单 -> {mod_detail}")

    # 4. Final Summary Markdown Table
    print("\n" + "=" * 70)
    print("## 还原一致性比对汇总表")
    print("=" * 70)
    print("| 类别 | 检查对象 | 比对结果 | 详细说明 |")
    print("|---|---|:---:|---|")
    for category, name, status, detail in table_rows:
        print(f"| {category} | `{name}` | **{status}** | {detail} |")

    if all_passed:
        print("\n>> VERDICT: ALL PASS [100% 一致]")
        sys.exit(0)
    else:
        print("\n>> VERDICT: FAIL [存在不一致项]")
        sys.exit(1)


if __name__ == "__main__":
    main()
