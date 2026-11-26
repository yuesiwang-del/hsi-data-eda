"""Scan the Tencent HSI news feed and save dated event candidates for review."""
import json
from pathlib import Path
from functools import lru_cache
import re
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

ENDPOINT = "https://proxy.finance.qq.com/ifzqgtimg/appstock/news/info/search"
KEYWORDS = re.compile(r"恒生指数|恒指")


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
    @lru_cache(maxsize=None)
    def lookup(page):
        return fetch_page(page)

    first, _ = lookup(1)
    last_page = int(first["total_page"])
    latest = first["data"][0]["time"][:10]
    oldest = lookup(last_page)[0]["data"][-1]["time"][:10]
    anomalies = pd.read_csv(folder / "extreme_returns.csv")
    coverage, candidates = [], {}
    for date in anomalies.date:
        coverage_status = "outside_feed" if not oldest <= date <= latest else "searched"
        pages = set()
        if coverage_status == "searched":
            # Find the first page whose oldest date is at or before the anomaly.
            left, right = 1, last_page
            while left < right:
                middle = (left + right) // 2
                rows = lookup(middle)[0]["data"]
                days = [row["time"][:10] for row in rows]
                if not days or days != sorted(days, reverse=True):
                    raise ValueError(f"Invalid Tencent date order on page {middle}")
                if days[-1] > date:
                    left = middle + 1
                else:
                    right = middle
            for page in range(left, last_page + 1):
                result, url = lookup(page)
                pages.add(page)
                for row in result["data"]:
                    if (row["time"][:10] == date and KEYWORDS.search(row["title"])
                            and "hkHSI" in row.get("symbols", [])):
                        candidates[row["id"]] = (date, row["time"], row["title"], row["url"], url)
                if not result["data"] or result["data"][-1]["time"][:10] < date:
                    break
        coverage.append((date, coverage_status, oldest, latest, ",".join(map(str, sorted(pages)))))
    pd.DataFrame(coverage, columns=["date", "status", "feed_oldest", "feed_latest", "pages"]).to_csv(
        folder / "news_coverage.csv", index=False
    )
    pd.DataFrame(candidates.values(), columns=[
        "date", "published_at_hkt", "title", "url", "request_url"
    ]).sort_values("published_at_hkt").to_csv(folder / "news_candidates.csv", index=False)


if __name__ == "__main__":
    scan(Path(__file__).resolve().parent / "report")
