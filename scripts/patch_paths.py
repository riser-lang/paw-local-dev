"""Helper script to replace hardcoded Linux paths in backend modules with configurable paths."""
from pathlib import Path
import re

backend_dir = Path(__file__).resolve().parent.parent / "backend"
target_db = "/var/lib/personal-workspace/workspace.db"

count = 0
for p in backend_dir.rglob("*.py"):
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        continue
    
    modified = False
    if target_db in text:
        # Replace string literal
        text = text.replace(f'"{target_db}"', 'os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))')
        text = text.replace(f"'{target_db}'", 'os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))')
        
        # Replace mode=ro URI
        text = text.replace(f'file:{target_db}?mode=ro', '" + os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db")) + "')
        
        if "import os" not in text:
            text = "import os\n" + text
        if "from pathlib import Path" not in text:
            text = "from pathlib import Path\n" + text
        modified = True

    # Also clean up /opt/personal-workspace imports
    if "/opt/personal-workspace" in text:
        text = text.replace('"/opt/personal-workspace"', 'str(Path(__file__).resolve().parents[1 if "backend" in str(Path(__file__).parent.name) else 2])')
        text = text.replace("'/opt/personal-workspace'", 'str(Path(__file__).resolve().parents[1 if "backend" in str(Path(__file__).parent.name) else 2])')
        modified = True

    if modified:
        p.write_text(text, encoding="utf-8")
        count += 1
        print(f"Patched: {p.relative_to(backend_dir)}")

print(f"Total patched files: {count}")
