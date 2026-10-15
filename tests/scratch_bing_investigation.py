import httpx
import lxml.html
import urllib.parse

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

query = "数字化转型方法论与实施路径"
print("Testing query:", query)

# Test 1: cn.bing.com vs www.bing.com
for host in ["cn.bing.com", "www.bing.com"]:
    url = f"https://{host}/search"
    resp = httpx.get(url, params={"q": query}, headers=headers, follow_redirects=True)
    doc = lxml.html.fromstring(resp.text)
    print(f"\n--- HOST: {host} (final url: {resp.url}) ---")
    algos = doc.xpath('//li[contains(@class, "b_algo")]')
    print(f"Total algos: {len(algos)}")
    for i, a in enumerate(algos[:5]):
        h2 = a.xpath(".//h2/a")
        t = h2[0].text_content().strip() if h2 else "NO_TITLE"
        href = h2[0].get("href") if h2 else ""
        print(f"  [{i+1}] {t} -> {href}")
