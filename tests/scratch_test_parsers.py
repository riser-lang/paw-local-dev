import lxml.html

def test_bing():
    with open('tests/fixtures/search_html/bing_sample.html', 'r', encoding='utf-8') as f:
        doc = lxml.html.fromstring(f.read())
    algos = doc.xpath('//li[contains(@class, "b_algo")]')
    print("Bing algos:", len(algos))

def test_ddg():
    with open('tests/fixtures/search_html/duckduckgo_lite_sample.html', 'r', encoding='utf-8') as f:
        doc = lxml.html.fromstring(f.read())
    tables = doc.xpath('//table')
    print("DDG tables:", len(tables))
    results = []
    if tables:
        rows = tables[-1].xpath('.//tr')
        i = 0
        while i < len(rows):
            r = rows[i]
            link_el = r.xpath('.//a[contains(@class, "result-link")]')
            if link_el:
                title = link_el[0].text_content().strip()
                url = link_el[0].get('href', '').strip()
                snippet = ''
                if i + 1 < len(rows):
                    snip_el = rows[i+1].xpath('.//td[contains(@class, "result-snippet")]')
                    if snip_el:
                        snippet = snip_el[0].text_content().strip()
                        i += 1
                results.append((title, url, snippet))
            i += 1
    print(f"Parsed {len(results)} items from DDG Lite:")
    for idx, (t, u, s) in enumerate(results[:5]):
        print(f"  {idx+1}. {t} -> {u}")
        print(f"     Snippet: {s[:60]}...")

if __name__ == '__main__':
    test_bing()
    test_ddg()
