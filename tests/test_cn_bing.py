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

with httpx.Client(headers=headers, follow_redirects=False) as client:
    r = client.get("https://cn.bing.com/search", params={"q": "数字化转型方法论与实施路径"})
    print("cn.bing.com status:", r.status_code)
    print("headers:", dict(r.headers))
    if "location" in r.headers:
        print("Redirect Location:", r.headers["location"])
