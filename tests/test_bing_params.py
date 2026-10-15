import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
import lxml.html

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

query = "数字化转型方法论与实施路径"

param_variants = [
    {"q": query, "ensearch": "0"},
    {"q": query, "cc": "CN"},
    {"q": query, "FORM": "BESBTB"},
    {"q": query, "pq": query},
    {"q": query, "mkt": "zh-CN"},
]

for p in param_variants:
    r = httpx.get("https://www.bing.com/search", params=p, headers=headers, follow_redirects=True)
    doc = lxml.html.fromstring(r.text)
    algos = doc.xpath('//li[contains(@class, "b_algo")]')
    first_title = algos[0].xpath(".//h2")[0].text_content().strip() if algos and algos[0].xpath(".//h2") else "NONE"
    print(f"Params: {p} -> {len(algos)} algos, first: {first_title}")
