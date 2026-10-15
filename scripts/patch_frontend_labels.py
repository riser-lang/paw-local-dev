"""Patch frontend JS and CSS assets to:
1. Rename 'Antigravity' and '工作台助手' to 'AI助手'
2. Clean up sidebar styles and text
3. Apply to both frontend/dist and dist/PAW_Personal_Work_OS/_internal/frontend/dist
"""
from pathlib import Path

ROOT = Path(r"d:\PAW\paw-beta")
targets = [
    ROOT / "frontend" / "dist",
    ROOT / "dist" / "PAW_Personal_Work_OS" / "_internal" / "frontend" / "dist",
]

for d in targets:
    js_path = d / "assets" / "index-qIwUsXqw.js"
    css_path = d / "assets" / "index-DZlw_Tae.css"
    
    if js_path.is_file():
        content = js_path.read_text(encoding="utf-8")
        
        # 1. Sidebar entry
        old_sidebar = 's.jsx("strong",{children:"Antigravity"}),s.jsx("small",{children:"工作台助手"})'
        new_sidebar = 's.jsx("strong",{children:"AI助手"}),s.jsx("small",{children:""})'
        if old_sidebar in content:
            content = content.replace(old_sidebar, new_sidebar)
            print(f"Patched sidebar entry in {js_path}")
        else:
            print(f"Sidebar target not found in {js_path}")

        # 2. Page header
        old_pattern = 'eyebrow:"Workspace Copilot",title:"Antigravity"'
        new_pattern = 'eyebrow:"",title:"AI助手"'
        if old_pattern in content:
            content = content.replace(old_pattern, new_pattern)
            print(f"Patched page header in {js_path}")
        else:
            print(f"Page header target not found in {js_path}")

        # Also replace the description if present
        idx = content.find('title:"AI助手",description:"')
        if idx != -1:
            end_desc = content.find('",illustration:"agy"', idx)
            if end_desc != -1:
                content = content[:idx] + 'title:"AI助手",description:"基于当前工作台状态回答问题，不自动覆写任何数据。' + content[end_desc:]
                print(f"Patched page header description in {js_path}")

        # 3. Text statuses
        content = content.replace('agnes:"Agy 对话"', 'agnes:"AI助手对话"')
        content = content.replace('children:"Agy 在后台整理数据…"', 'children:"AI助手正在整理数据…"')
        content = content.replace('title:"Agy 在做什么"', 'title:"AI助手在做什么"')
        
        js_path.write_text(content, encoding="utf-8")
        print(f"Saved updated JS: {js_path}")

    # CSS adjustments
    if css_path.is_file():
        css = css_path.read_text(encoding="utf-8")
        rule = "\n.desktop-agy-entry small:empty{display:none;}\n.desktop-agy-entry{min-height:38px;padding:0 12px;gap:12px;}\n"
        if ".desktop-agy-entry small:empty" not in css:
            css += rule
            css_path.write_text(css, encoding="utf-8")
            print(f"Appended CSS rule in {css_path}")

print("Frontend patch script completed.")
