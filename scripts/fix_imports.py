"""Fix future import positions and /opt/paw/current/runtime paths."""
from pathlib import Path
import re

backend = Path(__file__).resolve().parent.parent / "backend"

for p in backend.rglob("*.py"):
    try:
        txt = p.read_text(encoding="utf-8")
    except Exception:
        continue
    
    modified = False
    
    # 1. Fix future annotations
    if "from __future__ import annotations" in txt:
        parts = txt.split("from __future__ import annotations")
        head = parts[0]
        rest = "from __future__ import annotations" + "".join(parts[1:])
        
        # Check if head has imports that should be after future annotations
        imports_in_head = []
        clean_head_lines = []
        for line in head.splitlines():
            s = line.strip()
            if s.startswith("import ") or (s.startswith("from ") and "__future__" not in s):
                imports_in_head.append(line)
            else:
                clean_head_lines.append(line)
                
        if imports_in_head:
            clean_head = "\n".join(clean_head_lines).rstrip()
            txt = clean_head + "\n\nfrom __future__ import annotations\n\n" + "\n".join(imports_in_head) + "\n" + "".join(parts[1:])
            modified = True
            print(f"Fixed future annotations in: {p.relative_to(backend)}")

    # 2. Fix /opt/paw/current/runtime
    if "/opt/paw/current/runtime" in txt or "/opt/paw" in txt:
        txt = txt.replace('"/opt/paw/current/runtime"', 'str(Path(__file__).resolve().parents[1 if Path(__file__).parent.name == "backend" else 2] / "runtime")')
        txt = txt.replace("'/opt/paw/current/runtime'", 'str(Path(__file__).resolve().parents[1 if Path(__file__).parent.name == "backend" else 2] / "runtime")')
        txt = txt.replace('"/opt/paw"', 'str(Path(__file__).resolve().parents[1 if Path(__file__).parent.name == "backend" else 2])')
        modified = True
        print(f"Fixed runtime path in: {p.relative_to(backend)}")

    if modified:
        p.write_text(txt, encoding="utf-8")
