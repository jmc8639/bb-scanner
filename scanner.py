"""
Bollinger Band Bull/Bear Multi-Timeframe Scanner
=================================================

Implements the strategy described in the project instructions:
  - Bollinger Bands: 20-period SMA +/- 2 stdev
  - Bull confirmed: swing high at upper band -> round-trip to lower band -> breakout above the reference high
  - Bear confirmed: swing low at lower band -> round-trip to upper band -> breakdown below the reference low
  - Multi-timeframe alignment: monthly + weekly + daily

Runs on GitHub Actions. Writes a text report to reports/YYYY-MM-DD.txt.
"""

import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
BB_PERIOD = 20
BB_STDEV = 2.0
SWING_N = 5  # bars before/after for swing detection
TICKER_FILE = "tickers.txt"
REPORTS_DIR = "reports"
HISTORY_YEARS = 20  # download up to 20 years of daily data


# -----------------------------------------------------------------------------
# BOLLINGER BAND CALCULATION
# -----------------------------------------------------------------------------
def add_bollinger_bands(df: pd.DataFrame, period: int = BB_PERIOD, stdev: float = BB_STDEV) -> pd.DataFrame:
    """Add BB middle, upper, lower columns to an OHLC dataframe."""
    df = df.copy()
    df["bb_mid"] = df["Close"].rolling(period).mean()
    df["bb_std"] = df["Close"].rolling(period).std()
    df["bb_upper"] = df["bb_mid"] + stdev * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - stdev * df["bb_std"]
    return df


# -----------------------------------------------------------------------------
# SWING HIGH/LOW DETECTION AT THE BANDS
# -----------------------------------------------------------------------------
def find_swing_highs_at_upper(df: pd.DataFrame, n: int = SWING_N) -> list:
    """Return list of (index, price) tuples for swing highs touching/above the upper band."""
    highs = df["High"].values
    upper = df["bb_upper"].values
    swings = []
    for i in range(n, len(df) - n):
        if np.isnan(upper[i]):
            continue
        # local high: higher than n bars before and after
        if highs[i] == max(highs[i - n : i + n + 1]):
            # must be at or above the upper band
            if highs[i] >= upper[i]:
                swings.append((df.index[i], highs[i]))
    return swings


def find_swing_lows_at_lower(df: pd.DataFrame, n: int = SWING_N) -> list:
    """Return list of (index, price) tuples for swing lows touching/below the lower band."""
    lows = df["Low"].values
    lower = df["bb_lower"].values
    swings = []
    for i in range(n, len(df) - n):
        if np.isnan(lower[i]):
            continue
        if lows[i] == min(lows[i - n : i + n + 1]):
            if lows[i] <= lower[i]:
                swings.append((df.index[i], lows[i]))
    return swings


# -----------------------------------------------------------------------------
# BULL / BEAR CLASSIFICATION (FULL ROUND-TRIP STATE MACHINE)
# -----------------------------------------------------------------------------
def classify(df: pd.DataFrame) -> dict:
    """
    Walk through the dataframe chronologically and classify the current state.
    Returns: {
        "state": "BULL" | "BEAR" | "NEUTRAL",
        "confirmed_date": pd.Timestamp or None,
        "confirmed_price": float or None,
        "reference_level": float or None,
        "history": list of (date, "BULL"/"BEAR", price) events
    }
    """
    df = df.copy()
    df = add_bollinger_bands(df)
    df = df.dropna(subset=["bb_upper", "bb_lower"])
    if len(df) < 2 * SWING_N + BB_PERIOD:
        return {
            "state": "NEUTRAL",
            "confirmed_date": None,
            "confirmed_price": None,
            "reference_level": None,
            "history": [],
            "reason": "insufficient data",
        }

    swing_highs = find_swing_highs_at_upper(df)
    swing_lows = find_swing_lows_at_lower(df)
    # Combine and sort all band-touching swing events chronologically
    events = []
    for idx, price in swing_highs:
        events.append((idx, "HIGH", price))
    for idx, price in swing_lows:
        events.append((idx, "LOW", price))
    events.sort(key=lambda e: e[0])

    # State machine
    # We track:
    #   pending_ref_high: most recent unbroken upper-band swing high (potential bull reference)
    #   pending_ref_low: most recent unbroken lower-band swing low (potential bear reference)
    #   has_visited_opposite: whether price has touched the opposite band since the pending ref was set
    #   current_state: BULL / BEAR / NEUTRAL
    state = "NEUTRAL"
    confirmed_date = None
    confirmed_price = None
    reference_level = None
    history = []

    pending_high = None  # (date, price) - waiting for round-trip then break above
    pending_low = None  # (date, price) - waiting for round-trip then break below
    high_round_tripped = False
    low_round_tripped = False

    # Iterate bar by bar; check for swing events and confirmation breakouts
    event_iter = iter(events)
    next_event = next(event_iter, None)

    for i in range(len(df)):
        date = df.index[i]
        bar_high = df["High"].iloc[i]
        bar_low = df["Low"].iloc[i]
        bar_close = df["Close"].iloc[i]
        upper = df["bb_upper"].iloc[i]
        lower = df["bb_lower"].iloc[i]

        # Register any swing events on this date
        while next_event is not None and next_event[0] == date:
            ev_date, ev_type, ev_price = next_event
            if ev_type == "HIGH":
                # Update pending high to most recent
                pending_high = (ev_date, ev_price)
                high_round_tripped = False
            else:  # LOW
                pending_low = (ev_date, ev_price)
                low_round_tripped = False
            next_event = next(event_iter, None)

        # Track round-trip: did this bar touch the opposite band?
        if pending_high is not None and bar_low <= lower:
            high_round_tripped = True
        if pending_low is not None and bar_high >= upper:
            low_round_tripped = True

        # Bull confirmation: break above pending high after round-trip
        if (
            pending_high is not None
            and high_round_tripped
            and bar_close > pending_high[1]
            and state != "BULL"
        ):
            state = "BULL"
            confirmed_date = date
            confirmed_price = pending_high[1]
            reference_level = pending_high[1]
            history.append((date, "BULL", pending_high[1]))
            # Reset bear tracking
            pending_low = None
            low_round_tripped = False

        # Bear confirmation: break below pending low after round-trip
        if (
            pending_low is not None
            and low_round_tripped
            and bar_close < pending_low[1]
            and state != "BEAR"
        ):
            state = "BEAR"
            confirmed_date = date
            confirmed_price = pending_low[1]
            reference_level = pending_low[1]
            history.append((date, "BEAR", pending_low[1]))
            pending_high = None
            high_round_tripped = False

    return {
        "state": state,
        "confirmed_date": confirmed_date,
        "confirmed_price": confirmed_price,
        "reference_level": reference_level,
        "history": history,
    }


# -----------------------------------------------------------------------------
# CURRENT BAND PROXIMITY
# -----------------------------------------------------------------------------
def current_band_position(df: pd.DataFrame) -> dict:
    """Where is the latest price relative to the bands?"""
    df = add_bollinger_bands(df)
    valid = df.dropna(subset=["bb_upper", "bb_lower"])
    if len(valid) == 0:
        # Not enough bars to compute BB (typical for monthly timeframe on very young tickers)
        last = df.iloc[-1]
        return {
            "close": last["Close"],
            "upper": float("nan"),
            "lower": float("nan"),
            "mid": float("nan"),
            "position": float("nan"),
            "touching_upper": False,
            "touching_lower": False,
            "insufficient_data": True,
        }
    last = valid.iloc[-1]
    close = last["Close"]
    upper = last["bb_upper"]
    lower = last["bb_lower"]
    mid = last["bb_mid"]
    band_width = upper - lower

    # Position: 0 = at lower band, 1 = at upper band
    if band_width > 0:
        position = (close - lower) / band_width
    else:
        position = 0.5

    touching_upper = last["High"] >= upper
    touching_lower = last["Low"] <= lower

    return {
        "close": close,
        "upper": upper,
        "lower": lower,
        "mid": mid,
        "position": position,
        "touching_upper": touching_upper,
        "touching_lower": touching_lower,
        "insufficient_data": False,
    }


# -----------------------------------------------------------------------------
# TIMEFRAME RESAMPLING
# -----------------------------------------------------------------------------
def resample_to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Convert daily OHLC to weekly (Mon-Fri) bars."""
    weekly = daily.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    return weekly


def resample_to_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    """Convert daily OHLC to monthly bars."""
    monthly = daily.resample("ME").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    return monthly


# -----------------------------------------------------------------------------
# PER-TICKER ANALYSIS
# -----------------------------------------------------------------------------
def analyze_ticker(symbol: str) -> dict:
    """Download data and run classification on all three timeframes."""
    try:
        # yf.download is more forgiving than Ticker().history for indices
        daily = yf.download(
            symbol,
            period=f"{HISTORY_YEARS}y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if daily is None or daily.empty:
            return {"symbol": symbol, "error": "no data returned from yfinance"}

        # yf.download returns MultiIndex columns when given a single ticker as a list;
        # flatten if needed
        if isinstance(daily.columns, pd.MultiIndex):
            daily.columns = daily.columns.get_level_values(0)

        if len(daily) < 60:
            return {"symbol": symbol, "error": f"only {len(daily)} bars available"}

        # Ensure we have a clean tz-naive DatetimeIndex
        daily.index = pd.to_datetime(daily.index)
        if daily.index.tz is not None:
            daily.index = daily.index.tz_localize(None)

        weekly = resample_to_weekly(daily)
        monthly = resample_to_monthly(daily)

        return {
            "symbol": symbol,
            "daily_class": classify(daily),
            "weekly_class": classify(weekly),
            "monthly_class": classify(monthly),
            "daily_pos": current_band_position(daily),
            "weekly_pos": current_band_position(weekly),
            "monthly_pos": current_band_position(monthly),
            "last_date": daily.index[-1],
            "last_close": float(daily["Close"].iloc[-1]),
        }
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"  ERROR for {symbol}: {tb}")
        return {"symbol": symbol, "error": f"{type(e).__name__}: {str(e)[:150]}"}


# -----------------------------------------------------------------------------
# SETUP DETECTION (THE ACTIONABLE SIGNALS)
# -----------------------------------------------------------------------------
def assess_setup(result: dict) -> dict:
    """Classify the setup strength based on multi-timeframe alignment."""
    if "error" in result:
        return {"strength": "ERROR", "label": result["error"]}

    m_state = result["monthly_class"]["state"]
    w_state = result["weekly_class"]["state"]
    d_state = result["daily_class"]["state"]

    w_touch_lower = result["weekly_pos"]["touching_lower"]
    w_touch_upper = result["weekly_pos"]["touching_upper"]
    d_touch_lower = result["daily_pos"]["touching_lower"]
    d_touch_upper = result["daily_pos"]["touching_upper"]

    # ---- BUY SETUPS ----
    if m_state == "BULL":
        if w_touch_lower and d_touch_lower:
            return {"strength": "HIGHEST_BUY", "label": "Monthly BULL + Weekly lower BB + Daily lower BB"}
        if w_state == "BULL" and d_touch_lower:
            return {"strength": "VERY_STRONG_BUY", "label": "Monthly BULL + Weekly BULL + Daily lower BB"}
        if w_touch_lower:
            return {"strength": "STRONG_BUY", "label": "Monthly BULL + Weekly lower BB"}
        # Partial alignment (watch list)
        if w_state == "BULL" and d_state == "BULL":
            return {"strength": "WATCH_BUY", "label": "All timeframes BULL aligned (waiting for pullback)"}
        if w_state == "BULL":
            return {"strength": "WATCH_BUY", "label": "Monthly + Weekly BULL (waiting for daily pullback)"}

    # ---- SHORT SETUPS ----
    if m_state == "BEAR":
        if w_touch_upper and d_touch_upper:
            return {"strength": "HIGHEST_SHORT", "label": "Monthly BEAR + Weekly upper BB + Daily upper BB"}
        if w_state == "BEAR" and d_touch_upper:
            return {"strength": "VERY_STRONG_SHORT", "label": "Monthly BEAR + Weekly BEAR + Daily upper BB"}
        if w_touch_upper:
            return {"strength": "STRONG_SHORT", "label": "Monthly BEAR + Weekly upper BB"}
        if w_state == "BEAR" and d_state == "BEAR":
            return {"strength": "WATCH_SHORT", "label": "All timeframes BEAR aligned (waiting for rally)"}
        if w_state == "BEAR":
            return {"strength": "WATCH_SHORT", "label": "Monthly + Weekly BEAR (waiting for daily rally)"}

    return {"strength": "NONE", "label": "No actionable setup"}


# -----------------------------------------------------------------------------
# REPORT GENERATION
# -----------------------------------------------------------------------------
def format_report(results: list, run_date: str) -> str:
    """Generate a plain text report."""
    lines = []
    lines.append("=" * 78)
    lines.append("BOLLINGER BAND MULTI-TIMEFRAME SCAN")
    lines.append(f"Run date: {run_date}")
    lines.append(f"Tickers scanned: {len(results)}")
    lines.append("=" * 78)
    lines.append("")

    # Group by setup strength
    priority_order = [
        "HIGHEST_BUY", "VERY_STRONG_BUY", "STRONG_BUY",
        "HIGHEST_SHORT", "VERY_STRONG_SHORT", "STRONG_SHORT",
        "WATCH_BUY", "WATCH_SHORT", "NONE", "ERROR",
    ]
    grouped = {p: [] for p in priority_order}
    for r in results:
        s = r["setup"]["strength"]
        grouped.setdefault(s, []).append(r)

    # --- Actionable section ---
    lines.append("ACTIONABLE SETUPS")
    lines.append("-" * 78)
    has_actionable = False
    for strength in ["HIGHEST_BUY", "VERY_STRONG_BUY", "STRONG_BUY",
                     "HIGHEST_SHORT", "VERY_STRONG_SHORT", "STRONG_SHORT"]:
        for r in grouped.get(strength, []):
            has_actionable = True
            lines.append("")
            lines.append(f"TICKER:  {r['symbol']}")
            lines.append(f"SIGNAL:  {strength.replace('_', ' ')}")
            lines.append(f"REASON:  {r['setup']['label']}")
            mc = r["monthly_class"]
            wc = r["weekly_class"]
            dc = r["daily_class"]
            lines.append(f"  Monthly: {mc['state']:<8}"
                         + (f" (confirmed {mc['confirmed_date'].date()} at {mc['confirmed_price']:.2f})"
                            if mc['confirmed_date'] is not None else ""))
            lines.append(f"  Weekly:  {wc['state']:<8}"
                         + (f" (confirmed {wc['confirmed_date'].date()} at {wc['confirmed_price']:.2f})"
                            if wc['confirmed_date'] is not None else ""))
            lines.append(f"  Daily:   {dc['state']:<8}"
                         + (f" (confirmed {dc['confirmed_date'].date()} at {dc['confirmed_price']:.2f})"
                            if dc['confirmed_date'] is not None else ""))
            lines.append(f"  Last close: ${r['last_close']:.2f} on {r['last_date'].date()}")
    if not has_actionable:
        lines.append("  No actionable setups today.")
    lines.append("")

    # --- Watch list ---
    lines.append("")
    lines.append("WATCH LIST (partial alignment, awaiting band touch)")
    lines.append("-" * 78)
    has_watch = False
    for strength in ["WATCH_BUY", "WATCH_SHORT"]:
        for r in grouped.get(strength, []):
            has_watch = True
            lines.append(f"  {r['symbol']:<8} {strength.replace('_', ' '):<12} {r['setup']['label']}")
    if not has_watch:
        lines.append("  None.")
    lines.append("")

    # --- Full classification table ---
    lines.append("")
    lines.append("FULL CLASSIFICATION TABLE")
    lines.append("-" * 78)
    lines.append(f"{'Ticker':<8} {'Monthly':<10} {'Weekly':<10} {'Daily':<10} {'Last Close':<12}")
    lines.append("-" * 78)
    for r in results:
        if "error" in r:
            lines.append(f"{r['symbol']:<8} ERROR: {r['error']}")
            continue
        lines.append(
            f"{r['symbol']:<8} "
            f"{r['monthly_class']['state']:<10} "
            f"{r['weekly_class']['state']:<10} "
            f"{r['daily_class']['state']:<10} "
            f"${r['last_close']:<11.2f}"
        )
    lines.append("")
    lines.append("=" * 78)
    lines.append("END OF REPORT")
    lines.append("=" * 78)
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
def main():
    # Read tickers
    if not Path(TICKER_FILE).exists():
        print(f"ERROR: {TICKER_FILE} not found", file=sys.stderr)
        sys.exit(1)
    with open(TICKER_FILE) as f:
        tickers = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if not tickers:
        print(f"ERROR: no tickers in {TICKER_FILE}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {len(tickers)} tickers...")
    print(f"yfinance version: {yf.__version__}")
    print(f"pandas version: {pd.__version__}")
    print("")

    results = []
    success_count = 0
    error_count = 0
    for i, t in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {t}")
        try:
            r = analyze_ticker(t)
            r["setup"] = assess_setup(r)
            if "error" in r:
                error_count += 1
                print(f"      -> {r['error']}")
            else:
                success_count += 1
                print(f"      -> M:{r['monthly_class']['state']} "
                      f"W:{r['weekly_class']['state']} "
                      f"D:{r['daily_class']['state']} "
                      f"close=${r['last_close']:.2f}")
        except Exception as e:
            # Should never happen given analyze_ticker handles its own errors,
            # but belt-and-suspenders so the whole run doesn't die.
            error_count += 1
            r = {"symbol": t, "error": f"unhandled: {type(e).__name__}: {str(e)[:100]}",
                 "setup": {"strength": "ERROR", "label": "unhandled exception"}}
            print(f"      -> UNHANDLED ERROR: {e}")
        results.append(r)

    print(f"\nSuccess: {success_count}, Errors: {error_count}")

    # Always write a report, even on partial failure
    Path(REPORTS_DIR).mkdir(exist_ok=True)
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = Path(REPORTS_DIR) / f"{run_date}.txt"
    try:
        report_text = format_report(results, run_date)
        report_path.write_text(report_text)
        print(f"\nReport written to {report_path}")
        print(f"Report size: {report_path.stat().st_size} bytes")
    except Exception as e:
        # Even if report formatting fails, write something so the commit step has a file
        import traceback
        report_path.write_text(f"Report generation failed: {e}\n\n{traceback.format_exc()}")
        print(f"Report formatting failed: {e}")

    # Exit cleanly so the commit step still runs
    sys.exit(0)


if __name__ == "__main__":
    main()
