"""Fetch official HKEX market totals."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import time
from urllib.request import urlopen

import pandas as pd

HKEX = "https://www.hkex.com.hk/eng/stat/smstat"
ARCHIVE = f"{HKEX}/mthbull/rpt_data_statistics_archive_trading_data"
MARKET_COLUMNS = ["main_board_turnover_hkd", "main_board_volume_shares"]


def download(url):
    for attempt in range(3):
        try:
            with urlopen(url, timeout=30) as response:
                raw = response.read()
            return raw, {"url": url, "sha256": hashlib.sha256(raw).hexdigest()}
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def integer(text):
    return int(text.replace(",", ""))


def archive_rows(raw):
    table = json.loads(raw)["tables"][0]
    headers = {cell["col"]: cell["text"] for cell in table["header"]}
    if "Value" not in headers[2].title() or "Shares" not in headers[3]:
        raise ValueError(f"Unexpected HKEX archive fields: {headers}")
    rows = {}
    for cell in table["body"]:
        rows.setdefault(cell["row"], {})[cell["col"]] = cell["text"]
    return [
        (pd.Timestamp(row[0]), integer(row[2]), integer(row[3]))
        for row in rows.values()
        if re.fullmatch(r"\d{4}/\d{2}/\d{2}", row.get(0, ""))
    ]


def daily_row(date):
    raw, record = download(f"{HKEX}/dayquot/d{date:%y%m%d}e.htm")
    page = raw.decode("ascii", errors="replace")
    markets = re.search(
        r"Today's Turnover:\s*\(HK\$\):\s*([\d,]+)\s*\(Shares\):\s*([\d,]+)", page
    )
    if not markets or date.strftime("%d %b %Y").upper() not in page:
        raise ValueError(f"Cannot verify HKEX market totals for {date.date()}")
    return (date, *map(integer, markets.groups())), record


def fetch_supplement(dates, folder):
    rows, manifest = [], []
    for first in range(2005, dates.max().year + 1, 5):
        raw, record = download(f"{ARCHIVE}_{first}_{first + 4}.json")
        rows.extend(archive_rows(raw))
        manifest.append(record)
    last_archive_date = max(row[0] for row in rows)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for row, record in pool.map(daily_row, dates[dates > last_archive_date]):
            rows.append(row)
            manifest.append(record)
    market = pd.DataFrame(rows, columns=["date", *MARKET_COLUMNS]).set_index("date")
    if not market.index.is_unique:
        raise ValueError("Duplicate dates in HKEX market data")
    market = market.reindex(dates)
    if market.isna().any().any() or not market.gt(0).all().all():
        raise ValueError(f"Invalid HKEX market totals: {market[market.isna().any(axis=1)].index.tolist()}")

    market.to_csv(folder / "market_supplement.csv")
    (folder / "supplement_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
