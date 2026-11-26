"""Download and audit HSI daily observations, then build the group handoff."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
from urllib.request import urlopen

import exchange_calendars as xcals
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
from statsmodels.tsa.stattools import adfuller

from supplemental import MARKET_COLUMNS, fetch_supplement

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
REPORT = ROOT / "report"
FIGURES = REPORT / "figures"
AS_OF = pd.Timestamp("2026-11-26")
YEARS = range(2005, AS_OF.year + 1)
COUNT = 4800
TEST_START = AS_OF - pd.DateOffset(years=3)
SOURCE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
COLUMNS = ["date", "open", "close", "high", "low", "provider_activity"]
FEATURES = ["log_return"] + [f"lag_{i}" for i in (1, 2, 3, 5, 10)] + [
    f"rolling_{stat}_{n}" for n in (5, 20, 60) for stat in ("mean", "std")
]


def fetch_year(year):
    end = min(pd.Timestamp(year, 12, 31), AS_OF).date()
    url = f"{SOURCE}?param=hkHSI,day,{year}-01-01,{end},640,qfq"
    for attempt in range(3):
        try:
            with urlopen(url, timeout=25) as response:
                payload = response.read()
            rows = json.loads(payload)["data"]["hkHSI"]["day"]
            if not 200 < len(rows) < 270:
                raise ValueError(f"Unexpected row count: {len(rows)}")
            return rows, {"year": year, "url": url, "rows": len(rows),
                          "sha256": hashlib.sha256(payload).hexdigest()}
        except (OSError, ValueError, KeyError, TypeError) as error:
            if attempt == 2:
                raise RuntimeError(f"Could not download {year}") from error
            time.sleep(2 ** attempt)


def download():
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(fetch_year, YEARS))
    rows = [row[:6] for year_rows, _ in results for row in year_rows]
    pd.DataFrame(rows, columns=COLUMNS).to_csv(DATA / "source_snapshot.csv", index=False)
    (DATA / "source_manifest.json").write_text(
        json.dumps([record for _, record in results], indent=2) + "\n"
    )


def clean(refresh=False):
    raw = pd.read_csv(DATA / "source_snapshot.csv", parse_dates=["date"])
    dedup = raw.drop_duplicates()
    if dedup.date.isna().any() or dedup.date.duplicated().any():
        raise ValueError("Missing dates or conflicting observations on the same date")
    prices = dedup.sort_values("date").set_index("date")
    ohlc = prices[["open", "high", "low", "close"]]
    if not (np.isfinite(prices.close) & prices.close.gt(0)).all():
        raise ValueError("Missing, non-finite or nonpositive closing price")
    valid = (
        np.isfinite(ohlc).all(axis=1) & ohlc.gt(0).all(axis=1)
        & prices.high.ge(ohlc[["open", "close", "low"]].max(axis=1))
        & prices.low.le(ohlc[["open", "close", "high"]].min(axis=1))
    )
    prices["ohlc_flag"] = ~valid
    suspect_ohlc = prices.loc[~valid, ["open", "high", "low", "close"]]
    prices.loc[~valid, ["open", "high", "low"]] = np.nan
    activity = prices.provider_activity
    prices["provider_activity"] = activity.where(np.isfinite(activity) & activity.ge(0))
    calendar = xcals.get_calendar("XHKG", start="2005-01-01", end=AS_OF)
    sessions = calendar.sessions_in_range(prices.index.min(), prices.index.max())
    outside = ~prices.index.isin(sessions)
    stub = (
        prices.provider_activity.eq(0)
        & prices[["open", "high", "low", "close"]].eq(prices.close, axis=0).all(axis=1)
        & prices.close.eq(prices.close.shift())
    )
    if (outside & ~stub).any():
        raise ValueError(f"Review dates outside XHKG: {prices.index[outside & ~stub].tolist()}")
    dropped = prices.index[outside & stub]
    prices = prices.loc[~outside].tail(COUNT).copy()
    if len(prices) < COUNT:
        raise ValueError(f"Expected at least {COUNT} observations")
    if prices.index.max() != AS_OF:
        raise ValueError(f"Expected closing data through {AS_OF.date()}")
    suspect_ohlc.loc[suspect_ohlc.index.intersection(prices.index)].to_csv(
        REPORT / "ohlc_audit.csv"
    )
    expected = calendar.sessions_in_range(prices.index.min(), prices.index.max())
    absent = expected.difference(prices.index)
    audit = pd.DataFrame(
        [(d, "calendar_session_missing_from_source") for d in absent]
        + [(d, "non_session_zero_activity_carryforward_removed") for d in dropped],
        columns=["date", "issue"],
    )
    audit.to_csv(REPORT / "calendar_audit.csv", index=False)
    if len(absent):
        raise ValueError("Missing trading sessions. See calendar_audit.csv")
    prices["log_return"] = np.log(prices.close).diff()
    prices["split"] = np.where(prices.index < TEST_START, "train", "test")
    train = prices.loc[prices.split.eq("train"), "log_return"].dropna()
    median = train.median()
    mad = (train - median).abs().median()
    prices["extreme_return"] = (prices.log_return - median).abs() > 6 * 1.4826 * mad
    supplement_path = DATA / "market_supplement.csv"
    if refresh or not supplement_path.exists():
        fetch_supplement(prices.index, DATA)
    supplement = pd.read_csv(supplement_path, index_col="date", parse_dates=True)
    if not supplement.index.equals(prices.index):
        raise ValueError("Market supplement dates differ. Refresh with --download.")
    if supplement[MARKET_COLUMNS].isna().any().any() or not supplement[MARKET_COLUMNS].gt(0).all().all():
        raise ValueError("HKEX market volume and turnover must cover all trading days")
    prices = prices.join(supplement[MARKET_COLUMNS])
    prices.to_csv(DATA / "hsi_clean.csv")
    prices[["close", "log_return", "split"]].dropna().to_csv(DATA / "returns.csv")
    prices.loc[prices.extreme_return, ["close", "log_return"]].to_csv(
        REPORT / "extreme_returns.csv"
    )
    return prices, {
        "source_rows": len(raw),
        "ohlc_flagged": int(prices.ohlc_flag.sum()),
        "exact_duplicates_removed": len(raw) - len(dedup),
        "missing_activity": int(prices.provider_activity.isna().sum()),
        "calendar_missing": len(absent), "non_session_stubs_removed": len(dropped),
        "extreme_returns": int(prices.extreme_return.sum()),
    }


def make_features(prices):
    returns = prices.log_return
    features = pd.DataFrame({"log_return": returns})
    for lag in (1, 2, 3, 5, 10):
        features[f"lag_{lag}"] = returns.shift(lag)
    for n in (5, 20, 60):
        window = returns.rolling(n)
        features[f"rolling_mean_{n}"] = window.mean()
        features[f"rolling_std_{n}"] = window.std(ddof=1)
    features["target_date"] = prices.index.to_series().shift(-1)
    features["target_return"] = returns.shift(-1)
    features["target_close"] = prices.close.shift(-1)
    features["origin_close"] = prices.close
    features["split"] = prices.split.shift(-1)
    features = features.dropna().rename_axis("origin_date")
    features.to_csv(DATA / "features.csv")
    # Publish optional market activity without changing existing model inputs.
    features[["target_date", "split"]].join(prices[MARKET_COLUMNS]).to_csv(
        DATA / "optional_market_features.csv"
    )
    pd.DataFrame({"feature": FEATURES}).to_csv(DATA / "feature_columns.csv", index=False)
    features.groupby("split")["target_date"].agg(["count", "min", "max"]).to_csv(
        DATA / "split_summary.csv"
    )
    return features


def diagnostics(prices, audit):
    summary, tests = [], []
    for scope, sample in (("train", prices[prices.split.eq("train")]), ("all", prices)):
        returns = sample.log_return.dropna()
        jb = stats.jarque_bera(returns)
        summary.append({
            "scope": scope, "n": len(returns), "mean": returns.mean(),
            "variance": returns.var(), "skewness": returns.skew(),
            "excess_kurtosis": returns.kurt(), "min": returns.min(), "max": returns.max(),
            "jarque_bera_statistic": jb.statistic, "jarque_bera_p": jb.pvalue,
        })
        if scope == "all":
            continue
        for name, series in (("log_close", np.log(sample.close)), ("log_return", returns)):
            adf = adfuller(series, regression="c", autolag="AIC")
            tests.append((name, "ADF", adf[2], adf[0], adf[1]))
        for name, series in (("log_return", returns), ("squared_return", returns**2)):
            lb = acorr_ljungbox(series, lags=[5, 10, 20], return_df=True)
            for lag, row in lb.iterrows():
                tests.append((name, "Ljung-Box", lag, row.lb_stat, row.lb_pvalue))
        lm, pvalue, _, _ = het_arch(returns - returns.mean(), nlags=10)
        tests.append(("demeaned_return", "ARCH-LM", 10, lm, pvalue))
        monthly = returns.groupby(returns.index.to_period("M")).sum()
        monthly = monthly.iloc[1:-1]
        subsets = [monthly[monthly.index.month == m] for m in range(1, 13)]
        result = stats.kruskal(*subsets)
        tests.append(("monthly_return", "Kruskal-Wallis by month", 11,
                      result.statistic, result.pvalue))
        monthly_lb = acorr_ljungbox(monthly, lags=[12], return_df=True).iloc[0]
        tests.append(("monthly_return", "Ljung-Box", 12,
                      monthly_lb.lb_stat, monthly_lb.lb_pvalue))
    pd.DataFrame(summary).to_csv(REPORT / "descriptive_stats.csv", index=False)
    pd.DataFrame(tests, columns=["series", "test", "lag_or_df", "statistic", "p_value"]).to_csv(
        REPORT / "train_tests.csv", index=False
    )
    (REPORT / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIGURES / f"{name}.png", dpi=130, bbox_inches="tight")
    plt.close(fig)


def plot(prices):
    plt.rcParams.update({"axes.grid": True, "grid.alpha": 0.22, "axes.spines.top": False,
                         "axes.spines.right": False})
    r = prices.log_return
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
    axes[0].plot(prices.close, label="Daily close", lw=0.8)
    axes[0].plot(prices.close.rolling(60).mean(), label="60-session mean", lw=1)
    axes[0].set_ylabel("Index points")
    axes[0].legend(loc="upper right")
    events = pd.read_csv(REPORT / "events.csv", parse_dates=["date"])
    for date, label in events[["date", "label"]].itertuples(index=False, name=None):
        point = pd.Timestamp(date)
        axes[0].axvline(point, color="firebrick", alpha=0.6, lw=0.8)
        axes[0].text(point, 0.03, label, rotation=90, va="bottom", ha="right",
                     transform=axes[0].get_xaxis_transform(), fontsize=8)
    axes[1].plot(r * 100, lw=0.55, color="#266971")
    axes[2].plot(r.rolling(20).mean() * 100, lw=0.8, color="#266971")
    axes[3].plot(r.rolling(20).std() * 100, lw=0.8, color="#266971")
    for ax, label in zip(axes[1:], ["Log return (%)", "20-session mean (%)", "20-session SD (%)"]):
        ax.set_ylabel(label)
    for ax in axes:
        ax.axvline(TEST_START, color="black", ls="--", lw=0.8)
    fig.suptitle("Hang Seng Index | Daily trends and volatility")
    save(fig, "01_trends")

    train = r.loc[r.index < TEST_START].dropna()
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    plot_acf(train, lags=40, ax=axes[0], zero=False)
    plot_pacf(train, lags=40, ax=axes[1], zero=False, method="ywm")
    plot_acf(train**2, lags=40, ax=axes[2], zero=False, title="ACF of squared log returns")
    fig.suptitle("Training set only")
    save(fig, "02_acf_pacf")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    axes[0].hist(train * 100, bins=70, color="#266971", density=True)
    x = np.linspace(train.min(), train.max(), 300) * 100
    axes[0].plot(x, stats.norm.pdf(x, train.mean()*100, train.std()*100), color="firebrick")
    axes[0].set(xlabel="Daily log return (%)", ylabel="Density", title="Empirical and fitted normal")
    stats.probplot(train * 100, dist="norm", plot=axes[1])
    save(fig, "03_distribution")

    monthly = train.groupby(train.index.to_period("M")).sum().iloc[1:-1]
    annual = r.groupby(r.index.year).sum()
    fig, axes = plt.subplots(2, 1, figsize=(11, 8))
    axes[0].boxplot([monthly[monthly.index.month == m] * 100 for m in range(1, 13)])
    axes[0].axhline(0, color="grey", lw=0.8)
    axes[0].set(xlabel="Calendar month", ylabel="Monthly log return (%)",
                title="Calendar-month distributions | Training set, full months")
    axes[1].bar(annual.index, annual * 100, color=np.where(annual >= 0, "#266971", "firebrick"))
    axes[1].set_xticks(annual.index, annual.index, rotation=45)
    axes[1].set(ylabel="Annual log return (%)", title="Calendar-year returns | First and last years partial")
    save(fig, "04_seasonality")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Refresh the source snapshot")
    args = parser.parse_args()
    for folder in (DATA, FIGURES):
        folder.mkdir(parents=True, exist_ok=True)
    if args.download or not (DATA / "source_snapshot.csv").exists():
        download()
    prices, audit = clean(refresh=args.download)
    features = make_features(prices)
    diagnostics(prices, audit)
    plot(prices)
    print(f"{len(prices)} prices, {len(features)} supervised rows")
    print(pd.read_csv(DATA / "split_summary.csv").to_string(index=False))


if __name__ == "__main__":
    main()
