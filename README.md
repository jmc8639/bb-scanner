# Daily US Equity Screener

Automated daily scan of ~1,700 US large/mid-cap stocks. Outputs the ticker symbols that pass all filters.

## What it does

Every weekday at 5pm ET (after US market close), GitHub Actions runs `scanner.py`, which:

1. Pulls the current S&P 500 + NASDAQ 100 + Russell 1000 constituents from Wikipedia.
2. Applies the fundamental filter:
   - Market cap > $300M
   - Quarterly EPS diluted growth YoY > 20%
   - Average daily volume > 200,000 shares
3. Applies the technical filter (survivors only):
   - Monthly BB round-trip state = BULL
   - Weekly BB round-trip state = BULL
   - Daily BB round-trip state = BULL
   - Uses Interpretation B logic (highest-extreme reference tracking)
4. Writes a text file to `reports/YYYY-MM-DD.txt` containing just the passing ticker symbols.

Typical run time: 30-60 minutes. Typical output: a small number of symbols, often single digits.

## Where the files sit

Everything lives in your `bb-scanner` GitHub repository at the URL:
```
https://github.com/<your-username>/bb-scanner
```

Repository layout:

```
bb-scanner/
├── scanner.py                       ← the Python program (do not edit)
├── requirements.txt                 ← list of Python libraries the scan needs
├── README.md                        ← this file
├── .github/
│   └── workflows/
│       └── daily_scan.yml           ← the daily schedule (runs 5pm ET, Mon-Fri)
└── reports/
    ├── 2026-07-15.txt               ← one file per scan day
    ├── 2026-07-16.txt
    └── ...
```

## Daily routine

1. Open your repo in a browser: `https://github.com/<your-username>/bb-scanner`
2. Click the `reports/` folder.
3. Click the most recent dated file — that's today's scan.
4. The file contains the ticker symbols that passed all filters. Nothing else.

The scan runs itself on schedule. You don't need to do anything to trigger it.

## Reading a report

A typical report looks like this:

```
============================================================
SCREENED TICKERS - 2026-07-15
============================================================
Universe:              1687
Passed fundamentals:   142
Passed all filters:    7
============================================================

NVDA
AVGO
CRWD
PLTR
UBER
MSTR
COIN
```

The header tells you how many tickers were in the universe, how many passed fundamentals, and how many made it through both filters. Below that: just the tickers themselves. Copy them, open them in TradingView, and do your chart review.

## Running a scan on-demand

You don't have to wait for the scheduled time.

1. Go to your repo's **Actions** tab.
2. Click **Daily Scan** in the left sidebar.
3. Click the **Run workflow** dropdown button (right side).
4. Click the green **Run workflow** button.
5. Wait 30-60 minutes; refresh the Actions tab to watch progress.
6. When it's done (green checkmark), the new report is in `reports/`.

## Concurrency

If you trigger a manual run while a scheduled scan is already running, the older run is automatically cancelled and the new one starts. Only one scan is ever running at a time.

## Adjusting filters

Every threshold lives at the top of `scanner.py` in the CONFIGURATION section:

```python
MIN_MARKET_CAP = 300_000_000            # $300M
MIN_EPS_GROWTH_YOY = 0.20               # 20%
MIN_AVG_VOLUME = 200_000                # 200k shares/day
```

To change a threshold: click `scanner.py` in your repo, click the pencil icon, edit the value, click Commit changes. Next scheduled run uses the new value.

## Where the ticker universe comes from

The universe isn't stored in a file. It's fetched fresh from Wikipedia at the start of each run:

- S&P 500: `https://en.wikipedia.org/wiki/List_of_S%26P_500_companies`
- NASDAQ 100: `https://en.wikipedia.org/wiki/Nasdaq-100`
- Russell 1000: `https://en.wikipedia.org/wiki/Russell_1000_Index`

This means the universe stays current automatically as index constituents change. It also means if any of these Wikipedia pages are unreachable or change format, that source is skipped (with a warning) and the scan continues with whatever it could fetch.

## When something goes wrong

If a scheduled run fails (red X in the Actions tab), click the failed run and look at the "Run the scanner" step for the error message. The most common issues:

- **Rate limiting from Yahoo Finance** — yfinance is unofficial and Yahoo occasionally blocks high-volume access. If this happens, you'll see many tickers erroring out in the "info fetch failed" category. The scan still completes and writes a report; the passing set just may be smaller than usual.
- **Wikipedia format change** — if the index constituent tables change layout, one or more of the three sources may return zero tickers. The scan continues with what it has.
- **Missing fundamentals** — yfinance's `earningsQuarterlyGrowth` field is missing for some tickers. Those are skipped, not passed.

If runs consistently fail, share the error text in a Claude chat and I'll debug it.
