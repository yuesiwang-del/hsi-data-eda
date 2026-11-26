"""Fetch the original evidence for the curated EDA event annotations."""
from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path
import re

import pandas as pd
from pypdf import PdfReader

from supplemental import download

REPORT = Path(__file__).resolve().parent / "report"
TURNOVER = {
    "2024-10-08": ("8 Oct 2024", "620.7"),
    "2025-04-07": ("7 Apr 2025", "621.0"),
}


def verify():
    events = pd.read_csv(REPORT / "events.csv")
    candidates = pd.read_csv(REPORT / "news_candidates.csv")
    requests = lru_cache(maxsize=None)(download)
    evidence = []
    for event in events.itertuples(index=False):
        raw, source = requests(event.hkex_context_url)
        if event.source == "HKEX":
            page = raw.decode("utf-8")
            match = re.search(
                r"The Hang Seng Index \(HSI\) closed at 11015\.84 today, down 1602\.54 points, or 12\.7 per cent\.",
                page,
            )
            if not match or "27 Oct 2008" not in page:
                raise ValueError(f"HKEX notice contents changed for {event.date}")
            hkex_excerpt = match.group()
        else:
            day, turnover = TURNOVER[event.date]
            rows = PdfReader(BytesIO(raw)).pages[1].extract_text(extraction_mode="layout").splitlines()
            match = next(
                (line for line in rows if "Daily Market Turnover" in line
                 and day in line and turnover in line), None
            )
            if not match:
                raise ValueError(f"HKEX PDF turnover not verified for {event.date}")
            hkex_excerpt = f"Daily market turnover: HKD {turnover} billion ({day})"

            candidate = candidates.loc[
                candidates.date.eq(event.date) & candidates.url.eq(event.url)
            ].iloc[0]
            payload, tencent_source = requests(candidate.request_url)
            result = json.loads(payload.decode("utf-8").removeprefix("finance_news=").strip().rstrip(";"))
            article = next(
                (row for row in result["data"]["data"] if row["url"] == event.url
                 and row["time"] == candidate.published_at_hkt and row["title"] == candidate.title),
                None,
            )
            if article is None:
                raise ValueError(f"Tencent article not on saved feed page for {event.date}; rerun news_events.py")
            evidence.append({
                "date": event.date, "source": "Tencent Finance", **tencent_source,
                "article_url": event.url, "published_at_hkt": article["time"], "title": article["title"],
            })
        evidence.append({"date": event.date, "source": "HKEX", **source, "excerpt": hkex_excerpt})
    (REPORT / "event_source_manifest.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    verify()
