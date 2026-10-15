import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
import lxml.html

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
}

query = "数字化转型方法论与实施路径"

# 1. Test DDG html (html.duckduckgo.com/html/) vs lite (lite.duckduckgo.com/lite/)
for ddg_url in ["https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"]:
    try:
        r = httpx.post(ddg_url, data={"q": query}, headers=headers, timeout=10.0, follow_redirects=True)
        print(f"{ddg_url} -> status {r.status_code}")
        if r.status_code == 200:
            doc = lxml.html.fromstring(r.text)
            links = doc.xpath('//a[contains(@class, "result__url") or contains(@class, "result-link")]')
            print(f"  found {len(links)} links")
            for l in links[:3]:
                print("   -", l.text_content().strip())
    except Exception as e:
        print(f"{ddg_url} -> error {e}")
