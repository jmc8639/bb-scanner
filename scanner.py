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
 
    results = []
    for i, t in enumerate(tickers, 1):
        print(f"  [{i}/{len(tickers)}] {t}")
        r = analyze_ticker(t)
        r["setup"] = assess_setup(r)
        results.append(r)
 
    # Write report
    Path(REPORTS_DIR).mkdir(exist_ok=True)
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = Path(REPORTS_DIR) / f"{run_date}.txt"
    report_text = format_report(results, run_date)
    report_path.write_text(report_text)
    print(f"\nReport written to {report_path}")
    print(f"Report size: {report_path.stat().st_size} bytes")
 
 
if __name__ == "__main__":
    main()
