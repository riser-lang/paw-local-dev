import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
import lxml.html

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
}
url = "https://www.bing.com/search?q=%E6%95%B0%E5%AD%97%E5%8C%96%E8%BD%AC%E5%9E%8B%E6%96%B9%E6%B3%95%E8%AE%BA%E4%B8%8E%E5%AE%9E%E6%96%BD%E8%B7%AF%E5%BE%84"
resp = httpx.get(url, headers=headers, follow_redirects=True)
doc = lxml.html.fromstring(resp.text)

# Let's inspect all li or div under #b_results
results_container = doc.xpath('//ol[@id="b_results"]')
if results_container:
    print("Found ol#b_results")
    children = results_container[0].xpath('./li')
    print("Total children in #b_results:", len(children))
    for i, child in enumerate(children):
        cls = child.get("class", "")
        h2 = child.xpath(".//h2")
        h2_text = h2[0].text_content().strip() if h2 else ""
        print(f"[{i}] class='{cls}' | h2='{h2_text}'")
else:
    print("No ol#b_results found!")
