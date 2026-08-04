"""
Fundamental + Bollinger Band Multi-Timeframe Screener
======================================================

Scans US large/mid-cap equities (S&P 500 + NASDAQ 100 + Russell 1000)
and outputs the tickers that pass ALL of:

  FUNDAMENTAL GATE:
    - Market cap > $300M
    - Quarterly EPS diluted growth, YoY > 20%
    - Average daily volume (60-day) > 200,000

  TECHNICAL GATE (all three must be BULL):
    - Daily BB round-trip state = BULL
    - Weekly BB round-trip state = BULL
    - Monthly BB round-trip state = BULL

Uses "Interpretation B" round-trip logic: the reference high/low is the
highest/lowest extreme since the last state change (not the most recent).

Output: reports/YYYY-MM-DD.txt containing only the passing ticker symbols.
"""

import os
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from io import StringIO
from urllib.request import Request, urlopen

warnings.filterwarnings("ignore")

# Wikipedia requires a proper User-Agent or it returns 403 Forbidden
USER_AGENT = "Mozilla/5.0 (compatible; bb-scanner/1.0; +https://github.com)"


def fetch_html(url: str) -> str:
    """Fetch a URL with a proper User-Agent and return the HTML text."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

# =============================================================================
# CONFIGURATION
# =============================================================================
BB_PERIOD = 20
BB_STDEV = 2.0
SWING_N = 5
HISTORY_YEARS = 20

# Fundamental thresholds
MIN_MARKET_CAP = 300_000_000            # $300M
MIN_EPS_GROWTH_YOY = 0.20               # 20%
MIN_AVG_VOLUME = 200_000                # 200k shares/day
AVG_VOLUME_WINDOW = 60                  # 60 trading days for avg volume

REPORTS_DIR = "reports"

# Rate limiting: yfinance will block us if we hammer it.
# 1500+ tickers means we need to be gentle.
DOWNLOAD_SLEEP_MS = 100                 # sleep between price downloads
FUNDAMENTAL_SLEEP_MS = 50               # sleep between fundamental fetches


# =============================================================================
# UNIVERSE: fetch live index constituents from Wikipedia
# =============================================================================
def get_sp500_tickers() -> list:
    """S&P 500 constituents from Wikipedia."""
    try:
        html = fetch_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        tables = pd.read_html(StringIO(html))
        df = tables[0]
        tickers = df["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
        return tickers
    except Exception as e:
        print(f"  Warning: could not fetch S&P 500 list: {e}")
        return []


def get_nasdaq100_tickers() -> list:
    """NASDAQ 100 constituents from Wikipedia."""
    try:
        html = fetch_html("https://en.wikipedia.org/wiki/Nasdaq-100")
        tables = pd.read_html(StringIO(html))
        for tbl in tables:
            cols = [str(c).lower() for c in tbl.columns]
            if any("ticker" in c or "symbol" in c for c in cols):
                col = next(c for c in tbl.columns if "ticker" in str(c).lower() or "symbol" in str(c).lower())
                tickers = tbl[col].astype(str).str.replace(".", "-", regex=False).tolist()
                tickers = [t for t in tickers if t.replace("-", "").replace(".", "").isalpha()]
                if len(tickers) > 50:
                    return tickers
        return []
    except Exception as e:
        print(f"  Warning: could not fetch NASDAQ 100 list: {e}")
        return []


def get_russell1000_tickers() -> list:
    """Russell 1000 constituents from Wikipedia."""
    try:
        html = fetch_html("https://en.wikipedia.org/wiki/Russell_1000_Index")
        tables = pd.read_html(StringIO(html))
        for tbl in tables:
            cols = [str(c).lower() for c in tbl.columns]
            if any("ticker" in c or "symbol" in c for c in cols):
                col = next(c for c in tbl.columns if "ticker" in str(c).lower() or "symbol" in str(c).lower())
                tickers = tbl[col].astype(str).str.replace(".", "-", regex=False).tolist()
                tickers = [t for t in tickers if t.replace("-", "").replace(".", "").isalpha()]
                if len(tickers) > 500:
                    return tickers
        return []
    except Exception as e:
        print(f"  Warning: could not fetch Russell 1000 list: {e}")
        return []


def build_universe() -> list:
    """Combine and deduplicate all index constituents."""
    print("Fetching index constituents...")
    sp500 = get_sp500_tickers()
    print(f"  S&P 500:      {len(sp500)} tickers")
    ndx = get_nasdaq100_tickers()
    print(f"  NASDAQ 100:   {len(ndx)} tickers")
    r1000 = get_russell1000_tickers()
    print(f"  Russell 1000: {len(r1000)} tickers")

    # Deduplicate while preserving order
    seen = set()
    universe = []
    for t in sp500 + ndx + r1000:
        t = t.strip().upper()
        if t and t not in seen:
            seen.add(t)
            universe.append(t)
    print(f"  Deduplicated: {len(universe)} unique tickers\n")
    return universe


# =============================================================================
# FUNDAMENTAL SCREEN
# =============================================================================
def check_fundamentals(symbol: str) -> dict:
    """
    Fetch fundamentals via yfinance .info and check the gate.
    Returns:
      {"pass": bool, "reason": str, "market_cap": float, "eps_growth": float, "avg_vol": float}
    """
    try:
        info = yf.Ticker(symbol).info
    except Exception as e:
        return {"pass": False, "reason": f"info fetch failed: {str(e)[:50]}"}

    if not info or not isinstance(info, dict):
        return {"pass": False, "reason": "no info returned"}

    market_cap = info.get("marketCap")
    # earningsQuarterlyGrowth = quarterly EPS growth YoY (fraction, e.g., 0.25 = 25%)
    eps_growth = info.get("earningsQuarterlyGrowth")
    avg_vol = info.get("averageDailyVolume10Day") or info.get("averageVolume")

    if market_cap is None:
        return {"pass": False, "reason": "market cap missing"}
    if eps_growth is None:
        return {"pass": False, "reason": "eps growth missing"}
    if avg_vol is None:
        return {"pass": False, "reason": "avg volume missing"}

    if market_cap < MIN_MARKET_CAP:
        return {"pass": False, "reason": f"mkt cap ${market_cap/1e6:.0f}M < $300M"}
    if eps_growth < MIN_EPS_GROWTH_YOY:
        return {"pass": False, "reason": f"eps growth {eps_growth*100:.1f}% < 20%"}
    if avg_vol < MIN_AVG_VOLUME:
        return {"pass": False, "reason": f"avg vol {avg_vol:,.0f} < 200k"}

    return {
        "pass": True,
        "reason": "ok",
        "market_cap": market_cap,
        "eps_growth": eps_growth,
        "avg_vol": avg_vol,
    }


# =============================================================================
# BOLLINGER BAND CLASSIFICATION (Interpretation B: highest/lowest extreme)
# =============================================================================
def add_bollinger_bands(df: pd.DataFrame, period=BB_PERIOD, stdev=BB_STDEV) -> pd.DataFrame:
    df = df.copy()
    df["bb_mid"] = df["Close"].rolling(period).mean()
    df["bb_std"] = df["Close"].rolling(period).std()
    df["bb_upper"] = df["bb_mid"] + stdev * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - stdev * df["bb_std"]
    return df


def find_swing_highs_at_upper(df: pd.DataFrame, n=SWING_N) -> list:
    highs = df["High"].values
    upper = df["bb_upper"].values
    swings = []
    for i in range(n, len(df) - n):
        if np.isnan(upper[i]):
            continue
        if highs[i] == max(highs[i - n : i + n + 1]) and highs[i] >= upper[i]:
            swings.append((df.index[i], highs[i]))
    return swings


def find_swing_lows_at_lower(df: pd.DataFrame, n=SWING_N) -> list:
    lows = df["Low"].values
    lower = df["bb_lower"].values
    swings = []
    for i in range(n, len(df) - n):
        if np.isnan(lower[i]):
            continue
        if lows[i] == min(lows[i - n : i + n + 1]) and lows[i] <= lower[i]:
            swings.append((df.index[i], lows[i]))
    return swings


def classify(df: pd.DataFrame) -> str:
    """
    Interpretation B state machine.
    Returns "BULL", "BEAR", or "NEUTRAL".
    """
    df = add_bollinger_bands(df).dropna(subset=["bb_upper", "bb_lower"])
    if len(df) < 2 * SWING_N + BB_PERIOD:
        return "NEUTRAL"

    swing_highs = find_swing_highs_at_upper(df)
    swing_lows = find_swing_lows_at_lower(df)
    events = [(d, "HIGH", p) for d, p in swing_highs] + [(d, "LOW", p) for d, p in swing_lows]
    events.sort(key=lambda e: e[0])

    state = "NEUTRAL"
    pending_high = None
    pending_low = None
    high_round_tripped = False
    low_round_tripped = False

    event_iter = iter(events)
    next_event = next(event_iter, None)

    for i in range(len(df)):
        date = df.index[i]
        bar_low = df["Low"].iloc[i]
        bar_high = df["High"].iloc[i]
        bar_close = df["Close"].iloc[i]
        upper = df["bb_upper"].iloc[i]
        lower = df["bb_lower"].iloc[i]

        while next_event is not None and next_event[0] == date:
            ev_date, ev_type, ev_price = next_event
            if ev_type == "HIGH":
                if pending_high is None or ev_price > pending_high[1]:
                    pending_high = (ev_date, ev_price)
                    high_round_tripped = False
            else:
                if pending_low is None or ev_price < pending_low[1]:
                    pending_low = (ev_date, ev_price)
                    low_round_tripped = False
            next_event = next(event_iter, None)

        if pending_high is not None and bar_low <= lower:
            high_round_tripped = True
        if pending_low is not None and bar_high >= upper:
            low_round_tripped = True

        if pending_high is not None and high_round_tripped and bar_close > pending_high[1] and state != "BULL":
            state = "BULL"
            pending_low = None
            low_round_tripped = False

        if pending_low is not None and low_round_tripped and bar_close < pending_low[1] and state != "BEAR":
            state = "BEAR"
            pending_high = None
            high_round_tripped = False

    return state


# =============================================================================
# RESAMPLING
# =============================================================================
def resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    return daily.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()


def resample_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    return daily.resample("ME").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()


# =============================================================================
# TECHNICAL SCREEN (all three timeframes BULL)
# =============================================================================
def check_technicals(symbol: str) -> dict:
    """Download prices and check monthly/weekly/daily all BULL."""
    try:
        daily = yf.download(
            symbol,
            period=f"{HISTORY_YEARS}y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if daily is None or daily.empty or len(daily) < 60:
            return {"pass": False, "reason": "insufficient price history"}

        if isinstance(daily.columns, pd.MultiIndex):
            daily.columns = daily.columns.get_level_values(0)
        daily.index = pd.to_datetime(daily.index)
        if daily.index.tz is not None:
            daily.index = daily.index.tz_localize(None)

        weekly = resample_weekly(daily)
        monthly = resample_monthly(daily)

        d_state = classify(daily)
        w_state = classify(weekly)
        m_state = classify(monthly)

        if m_state == "BULL" and w_state == "BULL" and d_state == "BULL":
            return {"pass": True, "reason": "M/W/D all BULL"}
        return {"pass": False, "reason": f"M:{m_state} W:{w_state} D:{d_state}"}
    except Exception as e:
        return {"pass": False, "reason": f"tech screen error: {str(e)[:50]}"}


# =============================================================================
# MAIN
# =============================================================================
def main():
    print(f"yfinance version: {yf.__version__}")
    print(f"pandas version:   {pd.__version__}")
    print()

    universe = build_universe()
    if not universe:
        print("ERROR: universe is empty. Cannot proceed.")
        sys.exit(1)

    # Phase 1: fundamental gate (fast, cheap)
    print(f"Phase 1: fundamental screen on {len(universe)} tickers...")
    print(f"  Filters: mkt cap > $300M, EPS growth YoY > 20%, avg vol > 200k")
    print()

    fund_passed = []
    fund_stats = {"passed": 0, "failed": 0, "errored": 0}
    for i, symbol in enumerate(universe, 1):
        if i % 100 == 0:
            print(f"  [{i}/{len(universe)}] {fund_stats['passed']} passed so far")
        f = check_fundamentals(symbol)
        if f["pass"]:
            fund_passed.append(symbol)
            fund_stats["passed"] += 1
        elif f["reason"].startswith("info fetch"):
            fund_stats["errored"] += 1
        else:
            fund_stats["failed"] += 1
        time.sleep(FUNDAMENTAL_SLEEP_MS / 1000)

    print()
    print(f"Fundamental screen complete:")
    print(f"  Passed:  {fund_stats['passed']}")
    print(f"  Failed:  {fund_stats['failed']}")
    print(f"  Errored: {fund_stats['errored']}")
    print()

    # Phase 2: technical screen (slower, only on fundamental survivors)
    print(f"Phase 2: technical screen on {len(fund_passed)} tickers...")
    print(f"  Requires: Monthly + Weekly + Daily all BULL (Interpretation B round-trip)")
    print()

    tech_passed = []
    for i, symbol in enumerate(fund_passed, 1):
        print(f"  [{i}/{len(fund_passed)}] {symbol}", end="", flush=True)
        t = check_technicals(symbol)
        if t["pass"]:
            tech_passed.append(symbol)
            print(f"  -> PASS")
        else:
            print(f"  -> {t['reason']}")
        time.sleep(DOWNLOAD_SLEEP_MS / 1000)

    print()
    print(f"Technical screen complete: {len(tech_passed)} tickers passed all filters")
    print()

    # Write report — ticker symbols only
    Path(REPORTS_DIR).mkdir(exist_ok=True)
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = Path(REPORTS_DIR) / f"{run_date}.txt"
    report_lines = [
        "=" * 60,
        f"SCREENED TICKERS - {run_date}",
        "=" * 60,
        f"Universe:              {len(universe)}",
        f"Passed fundamentals:   {fund_stats['passed']}",
        f"Passed all filters:    {len(tech_passed)}",
        "=" * 60,
        "",
    ]
    if tech_passed:
        report_lines.extend(tech_passed)
    else:
        report_lines.append("(no tickers passed all filters today)")
    report_path.write_text("\n".join(report_lines))
    print(f"Report written to {report_path}")

    sys.exit(0)


if __name__ == "__main__":
    main()
