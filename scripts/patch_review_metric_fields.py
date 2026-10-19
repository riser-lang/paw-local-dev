"""R-264: data/metric review cards must always offer 标题 and 项目名称.

The legacy /app bundle in ``frontend/dist/assets/index-qIwUsXqw.js`` is shipped
as-is (CI does not rebuild it from ``backend/runtime/App.tsx``), and it derives
the editable draft fields from whatever keys the candidate payload happens to
carry. A data/metric candidate therefore showed only the fields its payload had
— usually 项目名称 but no 标题 — so a user could not fill a required field and
PM-U3-02 failed whenever such a card was picked.

This script patches the bundle so a data/metric card always renders one title
field and one project field, mirroring ``App.tsx``'s ``dataCard`` behaviour, and
falls back to ``title``/``project_name`` when the payload only has ``name``/
``project_id``. It is idempotent: running it twice changes nothing.
"""
from __future__ import annotations

from pathlib import Path

BUNDLE = Path("frontend/dist/assets/index-qIwUsXqw.js")

OLD = (
    'oe=Object.keys(je).filter(he=>["title","name","project_name","description",'
    '"due_at","priority","project_id","statement"].includes(he)),'
)
NEW = (
    'oe=(he=>{const ke=["data","metric"].includes(String(i.candidateType))||'
    '["data","metric"].includes(pC12TargetType(i.proposedChange));'
    'const $=Object.keys(je).filter(h=>["title","name","project_name","description",'
    '"due_at","priority","project_id","statement"].includes(h));'
    'if(!ke)return $;'
    'const q=$.filter(h=>!["title","name","project_name","project_id"].includes(h));'
    'return ["title"in je?"title":("name"in je?"name":"title"),'
    '"project_name"in je?"project_name":("project_id"in je?"project_id":"project_name"),...q]})(),'
)


def main() -> int:
    if not BUNDLE.is_file():
        print(f"bundle not found: {BUNDLE}")
        return 1
    content = BUNDLE.read_text(encoding="utf-8")
    if NEW in content:
        print("bundle already patched")
        return 0
    if OLD not in content:
        print("target expression not found; the bundle may have been rebuilt")
        return 2
    BUNDLE.write_text(content.replace(OLD, NEW, 1), encoding="utf-8")
    print("patched review draft fields in", BUNDLE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
