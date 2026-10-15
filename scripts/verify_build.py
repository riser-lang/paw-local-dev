# -*- coding: utf-8 -*-
import sys
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    js_path = root / "frontend" / "dist" / "paw_console.js"
    assert js_path.is_file(), f"{js_path} does not exist"
    
    text = js_path.read_text(encoding="utf-8")
    print(f"paw_console.js size: {len(text)} bytes")
    
    required_tabs = ["settings", "scanner", "batch_review", "status", "evolution", "doctor", "onboarding"]
    for tab in required_tabs:
        assert f'tab === "{tab}"' in text, f"Missing tab handler for: {tab}"
        print(f"  [PASS] Tab handler: {tab}")
        
    assert 'class="paw-batch-group-header"' in text, "Missing paw-batch-group-header"
    print("  [PASS] paw-batch-group-header exists")
    
    assert "btn-trigger-deep-cruise" in text, "Missing deep cruise button"
    print("  [PASS] Deep cruise button exists")
    
    assert "latest_cruise_result" in text, "Missing latest_cruise_result display"
    print("  [PASS] Latest cruise result report card exists")
    
    # Check Python syntax across backend
    import backend.runtime_core.scheduler as s
    import backend.runtime_core.project_analyzer as pa
    import backend.governance.committer as c
    import backend.governance.service as g
    import backend.workspace_api.repository as r
    print("  [PASS] All backend Python modules import cleanly!")
    print("ALL VERIFICATIONS PASSED PERFECTLY!")

if __name__ == "__main__":
    main()
