"""Scan the Tencent HSI news feed and save dated event candidates for review."""
import json
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

ENDPOINT = "https://proxy.finance.qq.com/ifzqgtimg/appstock/news/info/search"
KEYWORDS = re.compile(r"首次扩容|指数调整|熔断|暂停交易|紧急降息|加息决议|申请破产|大流行")


def fetch_page(page):
    query = urlencode({"_appver": "1.0", "page": page, "symbol": "hkHSI",
                       "n": 51, "_var": "finance_news", "type": 2})
    url = f"{ENDPOINT}?{query}"
    with urlopen(url, timeout=20) as response:
        payload = response.read().decode("utf-8")
    result = json.loads(payload.removeprefix("finance_news=").strip().rstrip(";"))
    if result["code"] != 0:
        raise ValueError(f"Tencent news page {page} failed: {result['msg']}")
    return result["data"], url


def scan(folder):
    first, url = fetch_page(1)
    total = int(first["total_page"])
    pages = {1: (first, url)}
    for page in sorted({*range(2, min(total, 10) + 1), total} - {1}):
        pages[page] = fetch_page(page)

    coverage, candidates = [], {}
    for page, (result, url) in pages.items():
        rows = result["data"]
        times = [row["time"] for row in rows]
        coverage.append((page, len(rows), min(times, default=""), max(times, default=""), url))
        for row in rows:
            if KEYWORDS.search(row["title"]):
                candidates[row["id"]] = (row["time"], row["title"], row["url"])
    pd.DataFrame(coverage, columns=["page", "rows", "first_time", "last_time", "request_url"]).to_csv(
        folder / "news_coverage.csv", index=False
    )
    pd.DataFrame(candidates.values(), columns=["published_at_hkt", "title", "url"]).sort_values(
        "published_at_hkt"
    ).to_csv(folder / "news_candidates.csv", index=False)


if __name__ == "__main__":
    scan(Path(__file__).resolve().parent / "report")
