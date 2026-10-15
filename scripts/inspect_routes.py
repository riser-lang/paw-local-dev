import re
from pathlib import Path

js_file = Path("frontend/dist/assets/index-qIwUsXqw.js")
text = js_file.read_text(encoding="utf-8")

routes = set(re.findall(r'path:"([^"]+)"', text))
print("Routes:", sorted(routes))

keywords = ["今日", "项目", "知识", "待办", "审核", "设置", "文件", "来源", "日历"]
found_kw = {}
for kw in keywords:
    matches = set(re.findall(r'("[^"]*' + kw + r'[^"]*")', text))
    found_kw[kw] = list(matches)[:5]

for k, v in found_kw.items():
    print(f"\n[{k}]: {v}")
