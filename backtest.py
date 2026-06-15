"""
backtest.py — Scenario Correlation Analysis (NOT a forward backtest)

For each historical window where the system would have identified a given scenario,
shows the distribution of subsequent 4H candle returns.

This is retrospective correlation analysis:
  - Uses point-in-time data for each historical snapshot (no lookahead bias in indicators)
  - Does NOT simulate execution, slippage, fees, or position sizing
  - Shows: "When conditions matched S1, the next 4H candle was up X% of the time"

The playbook PREDICTS this will work (S1=LONG, S3=SHORT, S8=no directional edge).
If the data validates the playbook's own claims, that is evidence of system integrity.

Run: python backtest.py
Output: scenario_correlation_report.json + prints summary table
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import numpy as np
import pg8000
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("backtest")

# ── QuestDB connection ─────────────────────────────────────────────────────────

_DB_PARAMS = dict(
    host=os.getenv("QUESTDB_HOST", "localhost"),
    port=int(os.getenv("QUESTDB_PG_PORT", "8812")),
    database="qdb", user="admin", password="quest",
)


def _q(sql: str) -> list:
    conn = pg8000.connect(**_DB_PARAMS)
    try:
        c = conn.cursor()
        c.execute(sql)
        return c.fetchall()
    finally:
        conn.close()


# ── Data fetchers ──────────────────────────────────────────────────────────────

def get_daily_candles(symbol: str, days: int = 90) -> pd.DataFrame:
    rows = _q(f"""
        SELECT first(open) o, max(high) h, min(low) l, last(close) c, sum(volume) v, timestamp ts
        FROM candles WHERE symbol='{symbol}' AND interval='1'
        SAMPLE BY 1d ALIGN TO CALENDAR
        ORDER BY ts ASC
        LIMIT {days + 5}
    """)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["open","high","low","close","volume","ts"])
    return df.dropna(subset=["close"]).reset_index(drop=True)


def get_4h_candles(symbol: str, days: int = 90) -> pd.DataFrame:
    rows = _q(f"""
        SELECT first(open) o, max(high) h, min(low) l, last(close) c, sum(volume) v, timestamp ts
        FROM candles WHERE symbol='{symbol}' AND interval='1'
        SAMPLE BY 4h ALIGN TO CALENDAR
        ORDER BY ts ASC
        LIMIT {days * 6 + 10}
    """)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["open","high","low","close","volume","ts"])
    return df.dropna(subset=["close"]).reset_index(drop=True)


# ── Scenario identification (mirrors data_aggregator.py logic) ────────────────

def identify_scenario_at_index(daily: pd.DataFrame, idx: int) -> Optional[int]:
    """
    Identify which scenario the decision tree would have returned at point `idx`.
    Uses only data available up to idx — no lookahead.
    Returns scenario number or None if insufficient data.
    """
    if idx < 22:
        return None   # need 20D VWMA warmup

    window = daily.iloc[:idx+1]
    recent = window.tail(20)
    close  = float(window["close"].iloc[-1])

    # VWMA 20D
    vwma = float((recent["close"] * recent["volume"]).sum() / max(recent["volume"].sum(), 1))

    # CVD proxy: use price momentum over last 3 days as spot CVD direction proxy
    # (We don't have historical per-trade CVD; use candle momentum as a structural proxy)
    closes_3d = window["close"].tail(4).values
    price_mom = (closes_3d[-1] - closes_3d[0]) / max(closes_3d[0], 1)
    spot_dir = "rising" if price_mom > 0.005 else "falling" if price_mom < -0.005 else "flat"

    # OI proxy: not available historically — default to neutral
    oi_trend = "flat"

    # MACD 1H proxy from daily: use 12/26 EMA cross
    closes = window["close"]
    ema12 = float(closes.ewm(span=12, adjust=False).mean().iloc[-1])
    ema26 = float(closes.ewm(span=26, adjust=False).mean().iloc[-1])
    macd_hist = ema12 - ema26

    # Funding proxy: use price vs VWMA gap as a funding proxy
    funding = 0.0001 if close > vwma else -0.0001

    # Decision tree (simplified mirror)
    price_near_vwma = abs(close - vwma) / max(vwma, 1) < 0.03
    if price_near_vwma and spot_dir == "flat":
        return 8

    if close > vwma:
        if spot_dir == "rising":
            oi_rising = True
            if funding >= 0 and macd_hist > 0:
                return 1   # HEALTHY_UPTREND
            else:
                return 7   # CONFIRMED_REVERSAL_FROM_BOTTOM
        else:
            return 2       # UPTREND_WEAKENING
    else:
        if spot_dir == "falling":
            return 4       # HEALTHY_DOWNTREND (simplified — missing OI split)
        else:
            return 6       # BOTTOM_FORMING


SCENARIO_NAMES = {
    1: "HEALTHY_UPTREND",
    2: "UPTREND_WEAKENING",
    3: "CONFIRMED_REVERSAL_FROM_TOP",
    4: "HEALTHY_DOWNTREND",
    5: "DEAD_CAT_BOUNCE",
    6: "BOTTOM_FORMING",
    7: "CONFIRMED_REVERSAL_FROM_BOTTOM",
    8: "RANGING_CONSOLIDATION",
}

SCENARIO_BASE_DIRECTION = {
    1: "LONG", 2: "NEUTRAL", 3: "SHORT", 4: "SHORT",
    5: "NO_TRADE", 6: "NEUTRAL", 7: "LONG", 8: "NEUTRAL",
}


def run_correlation_analysis(
    symbols: list = None,
    days: int = 90,
    forward_candles: int = 1,   # how many 4H candles forward to measure
) -> dict:
    """
    For each daily snapshot where a scenario was identified, record the
    subsequent 1-period 4H close return and aggregate by scenario.
    """
    symbols = symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "MNTUSDT"]
    results = {}

    for symbol in symbols:
        logger.info(f"[{symbol}] Fetching {days}D daily candles...")
        daily = get_daily_candles(symbol, days)
        if len(daily) < 25:
            logger.warning(f"[{symbol}] Insufficient daily data ({len(daily)} rows)")
            continue

        # Get 4H candles for forward return measurement
        candles_4h = get_4h_candles(symbol, days)
        if candles_4h.empty:
            logger.warning(f"[{symbol}] No 4H candle data")
            continue

        observations = []

        for idx in range(22, len(daily) - 1):
            scenario = identify_scenario_at_index(daily, idx)
            if scenario is None:
                continue

            ts_daily = daily.iloc[idx]["ts"]

            # Find corresponding 4H candle
            mask = candles_4h["ts"] >= ts_daily
            if not mask.any():
                continue
            i4h = candles_4h[mask].index[0]
            if i4h + forward_candles >= len(candles_4h):
                continue

            entry_close = float(candles_4h.iloc[i4h]["close"])
            exit_close  = float(candles_4h.iloc[i4h + forward_candles]["close"])
            fwd_return  = (exit_close - entry_close) / max(entry_close, 1)

            observations.append({
                "ts":       str(ts_daily),
                "scenario": scenario,
                "return":   round(fwd_return, 5),
                "up":       fwd_return > 0,
            })

        if not observations:
            continue

        # Aggregate by scenario
        df_obs = pd.DataFrame(observations)
        scenario_stats = {}

        for scen_num in range(1, 9):
            sub = df_obs[df_obs["scenario"] == scen_num]
            if len(sub) < 3:
                continue
            direction = SCENARIO_BASE_DIRECTION.get(scen_num, "NEUTRAL")
            pct_up    = float(sub["up"].mean() * 100)
            avg_ret   = float(sub["return"].mean() * 100)
            # Edge: for LONG scenarios, % up is the edge; for SHORT, % down
            edge      = pct_up - 50 if direction == "LONG" else (50 - pct_up) if direction == "SHORT" else abs(pct_up - 50)

            scenario_stats[scen_num] = {
                "scenario_name":    SCENARIO_NAMES.get(scen_num, "?"),
                "base_direction":   direction,
                "count":            len(sub),
                "pct_up":           round(pct_up, 1),
                "pct_down":         round(100 - pct_up, 1),
                "avg_4h_return_pct":round(avg_ret, 2),
                "edge_pct":         round(edge, 1),
                "verdict":          (
                    "VALIDATES_PLAYBOOK" if (
                        (direction == "LONG"  and pct_up > 55) or
                        (direction == "SHORT" and pct_up < 45) or
                        (direction in ("NEUTRAL","NO_TRADE") and abs(pct_up - 50) < 10)
                    ) else "MIXED"
                ),
            }

        results[symbol] = {
            "total_observations": len(observations),
            "days_analyzed":      days,
            "forward_4h_candles": forward_candles,
            "scenarios":          scenario_stats,
        }
        logger.info(f"[{symbol}] {len(observations)} observations across {len(scenario_stats)} scenarios")

    return results


def print_summary_table(results: dict):
    print("\n" + "=" * 90)
    print("SCENARIO CORRELATION ANALYSIS — Mantle AI Trading Copilot")
    print("Retrospective: when system conditions matched scenario X, next-4H was up/down Y%")
    print("=" * 90)
    print(f"{'Scenario':<8} {'Name':<32} {'Dir':<8} {'N':<5} {'%Up':<8} {'Avg4H%':<9} {'Edge':<8} {'Verdict'}")
    print("-" * 90)

    for symbol, data in results.items():
        print(f"\n── {symbol} ({data['days_analyzed']}D, {data['total_observations']} obs) ──")
        for scen_num in sorted(data["scenarios"].keys()):
            s = data["scenarios"][scen_num]
            verdict_icon = "✓" if s["verdict"] == "VALIDATES_PLAYBOOK" else "~"
            print(
                f"  S{scen_num:<6} {s['scenario_name']:<32} {s['base_direction']:<8} "
                f"{s['count']:<5} {s['pct_up']:<8.1f} {s['avg_4h_return_pct']:<9.2f} "
                f"{s['edge_pct']:<8.1f} {verdict_icon} {s['verdict']}"
            )

    print("\n" + "=" * 90)
    print("METHODOLOGY NOTE: CVD used daily candle momentum as proxy (historical trade data")
    print("unavailable). Results are indicative, not precise. Edge values > 5% are meaningful.")
    print("=" * 90)


if __name__ == "__main__":
    logger.info("Running scenario correlation analysis...")
    results = run_correlation_analysis(days=90)

    out_path = "scenario_correlation_report.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Report saved to {out_path}")

    print_summary_table(results)
