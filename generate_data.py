#!/usr/bin/env python3
"""
Generates data.json for the Dalio Dashboard GitHub Pages site.
Reads live Dalio/RayDar agent files + fetches Yahoo Finance prices.

Usage:
    python3 generate_data.py          # writes data.json next to this script
    python3 generate_data.py --dry-run  # prints JSON, doesn't write
"""
import json
import sys
import os
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone

DALIO_DIR = Path.home() / "Projects/dalio-agent"
RADAR_DIR = Path.home() / "Projects/radar-agent"
OUT_FILE  = Path(__file__).parent / "data.json"

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def yahoo_prices(tickers):
    if not tickers:
        return {}
    prices = {}
    syms = ",".join(tickers)
    url  = f"https://query1.finance.yahoo.com/v8/finance/spark?symbols={urllib.parse.quote(syms)}&range=1d&interval=1d"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            for item in (data.get("spark", {}).get("result") or []):
                sym = item.get("symbol")
                for resp in (item.get("response") or []):
                    p = resp.get("meta", {}).get("regularMarketPrice")
                    if sym and p:
                        prices[sym] = p
                        break
    except Exception as e:
        print(f"[prices] batch failed: {e}", file=sys.stderr)

    for t in [x for x in tickers if x not in prices]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(t)}?interval=1d&range=1d"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read())
                p = (data.get("chart", {}).get("result") or [{}])[0].get("meta", {}).get("regularMarketPrice")
                if p:
                    prices[t] = p
        except Exception:
            pass

    return prices

def generate():
    positions  = load_json(DALIO_DIR / ".position_metadata.json")
    stop_ids   = load_json(DALIO_DIR / ".stop_order_ids.json")
    pending    = load_json(DALIO_DIR / ".pending_orders.json")
    signals    = load_json(DALIO_DIR / ".signal_history.json")
    radar_raw  = load_json(RADAR_DIR / "radar_watchlist.json", {})
    scheduler  = load_json(DALIO_DIR / ".scheduler_state.json")

    all_tickers = list(set(
        list(positions.keys()) +
        list(pending.keys()) +
        [k for k, v in signals.items() if v.get("action") == "BUY"]
    ))
    print(f"[generate] fetching prices for {len(all_tickers)} tickers…", file=sys.stderr)
    prices = yahoo_prices(all_tickers)
    print(f"[generate] got {len(prices)}/{len(all_tickers)} prices", file=sys.stderr)

    radar_by_ticker = {item["ticker"]: item for item in (radar_raw.get("tickers") or [])}

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "positions":     positions,
        "stop_ids":      stop_ids,
        "pending_orders": pending,
        "signals":       signals,
        "radar": {
            "generated_at": radar_raw.get("generated_at"),
            "fear_greed":   radar_raw.get("fear_greed", {}),
            "by_ticker":    radar_by_ticker,
            "top":          (radar_raw.get("tickers") or [])[:20],
        },
        "live_prices":  prices,
        "scheduler":    scheduler,
        "ibkr_online":  False,
    }
    return data

if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    data = generate()
    out  = json.dumps(data, indent=2)
    if dry:
        print(out)
    else:
        with open(OUT_FILE, "w") as f:
            f.write(out)
        print(f"[generate] wrote {OUT_FILE} ({len(out):,} bytes)", file=sys.stderr)
