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

DALIO_PYTHON = str(Path.home() / 'Projects/dalio-agent/venv/bin/python3')
IBKR_SCRIPT  = '''
import json, asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB, util
util.logToConsole(level=50)
ib = IB()
ib.connect("127.0.0.1", 4001, clientId=97, timeout=8)
tags = ("NetLiquidation", "TotalCashValue", "GrossPositionValue")
account = {v.tag: float(v.value) for v in ib.accountValues() if v.tag in tags and v.currency == "USD"}
positions = [{"symbol": p.contract.symbol, "shares": p.position, "avg_cost": p.avgCost} for p in ib.positions()]
ib.disconnect()
print(json.dumps({"account": account, "positions": positions}))
'''

def get_ibkr_balance():
    """Query live IBKR balance via dalio venv Python. Returns dict or None."""
    try:
        import subprocess
        r = subprocess.run([DALIO_PYTHON, '-c', IBKR_SCRIPT],
                          capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout.strip())
            print('[ibkr] balance fetched live', file=sys.stderr)
            return data
        print(f'[ibkr] script error: {r.stderr[:200]}', file=sys.stderr)
        return None
    except Exception as e:
        print(f'[ibkr] offline or error: {e}', file=sys.stderr)
        return None

def load_journal(path):
    """Load .trade_journal.jsonl, return list of entry dicts."""
    entries = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return entries


def summarise_journal(entries):
    """Compute realized P&L totals split by trading_mode."""
    live_pnl = 0.0
    live_wins = live_losses = 0
    closed_live = []
    for e in entries:
        if e.get("status") != "closed":
            continue
        pnl = e.get("realized_pnl") or 0
        if e.get("trading_mode") == "live":
            live_pnl += pnl
            if pnl >= 0:
                live_wins += 1
            else:
                live_losses += 1
            closed_live.append({
                "ticker":      e["ticker"],
                "exit_date":   e.get("exit_date"),
                "realized_pnl": round(pnl, 2),
                "realized_pnl_pct": e.get("realized_pnl_pct"),
                "exit_type":   e.get("exit_type"),
            })
    return {
        "live_realized_pnl":    round(live_pnl, 2),
        "live_wins":            live_wins,
        "live_losses":          live_losses,
        "live_closed_trades":   closed_live,
    }


def generate():
    positions  = load_json(DALIO_DIR / ".position_metadata.json")
    stop_ids   = load_json(DALIO_DIR / ".stop_order_ids.json")
    pending    = load_json(DALIO_DIR / ".pending_orders.json")
    signals    = load_json(DALIO_DIR / ".signal_history.json")
    radar_raw  = load_json(RADAR_DIR / "radar_watchlist.json", {})
    scheduler  = load_json(DALIO_DIR / ".scheduler_state.json")
    journal    = summarise_journal(load_journal(DALIO_DIR / ".trade_journal.jsonl"))

    all_tickers = list(set(
        list(positions.keys()) +
        list(pending.keys()) +
        [k for k, v in signals.items() if v.get("action") == "BUY"]
    ))
    print(f"[generate] fetching prices for {len(all_tickers)} tickers…", file=sys.stderr)
    prices = yahoo_prices(all_tickers)
    print(f"[generate] got {len(prices)}/{len(all_tickers)} prices", file=sys.stderr)

    radar_by_ticker = {item["ticker"]: item for item in (radar_raw.get("tickers") or [])}

    ibkr = get_ibkr_balance()
    STARTING_BALANCE = float(os.environ.get('LIVE_BALANCE', '10000'))
    balance = {
        "starting":    STARTING_BALANCE,
        "net_liq":     ibkr["account"].get("NetLiquidation")     if ibkr else None,
        "cash":        ibkr["account"].get("TotalCashValue")     if ibkr else None,
        "positions_value": ibkr["account"].get("GrossPositionValue") if ibkr else None,
        "ibkr_live":   ibkr is not None,
        "ibkr_positions": ibkr["positions"] if ibkr else [],
    }

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
        "ibkr_online":  ibkr is not None,
        "balance":      balance,
        "journal":      journal,
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
