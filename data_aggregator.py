"""
data_aggregator.py — Compute all 12 playbook indicators from QuestDB + Mantle DeFi signals.

Called by run_pipeline.py once per symbol per analysis cycle.
All computations are deterministic (no LLM). The output snapshot is the input to agents/pipeline.py.

Indicators computed:
  1.  VWMA 20D      — volume-weighted moving average (macro regime check)
  2.  EMA 4H 21     — exponential MA (BTC local momentum)
  3.  MACD 1H       — momentum (12, 26, 9)
  4.  RSI 14 1H     — relative strength index (Wilder smoothing)
  5.  ATR 14 1H     — average true range (stop-loss sizing)
  6.  Spot CVD      — rolling 4H cumulative volume delta (Binance spot)
  7.  Futures CVD   — rolling 4H CVD (Bybit + Binance futures)
  8.  OI Trend      — open interest slope from last 6 5-min records
  9.  Funding Rate  — current + bucket classification
  10. Bid/Ask Delta — top-20 orderbook imbalance
  11. Liq Events    — recent liquidation cascade events (24H)
  12. VPVR (approx) — volume-profile visible range from 4H OHLCV (30 days)
  13. Mantle Signal — mETH yield vs ETH funding + FusionX DEX CVD (NEW)

Decision tree (deterministic, no LLM):
  Q1-Q4 → scenario 1-9 per the Complete Trading System v3.0 playbook
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import pg8000
from dotenv import load_dotenv

from mantle_integrations import get_mantle_signals

# ── Global market context (CoinGecko /global — one call per pipeline run) ────
# Cached at module level: refreshed each time get_global_market_context() is called.
# Called once from run_pipeline.py before the symbol loop — shared across all symbols.
_global_market_cache: dict = {}

def get_global_market_context() -> dict:
    """
    Fetch BTC dominance, ETH dominance, stablecoin dominance, market cap change.
    Fixes the documented gap: BULL regime prompt requires stablecoin supply context
    but previously had no data source for it.
    Zero new dependencies — uses existing requests library.
    """
    import requests as _req
    try:
        r = _req.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=8,
            headers={"Accept": "application/json"},
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            pct  = data.get("market_cap_percentage", {})
            btc_dom  = round(float(pct.get("btc",  0)), 2)
            eth_dom  = round(float(pct.get("eth",  0)), 2)
            usdt_dom = round(float(pct.get("usdt", 0)), 2)
            mcap_chg = round(float(data.get("market_cap_change_percentage_24h_usd", 0)), 2)

            result = {
                "available":       True,
                "btc_dominance":   btc_dom,
                "eth_dominance":   eth_dom,
                "stablecoin_dom":  usdt_dom,
                "mcap_change_24h": mcap_chg,
                "risk_sentiment":  ("RISK_ON"  if mcap_chg > 3  else
                                    "RISK_OFF" if mcap_chg < -3 else "NEUTRAL"),
                "altcoin_season":  btc_dom < 50,
                # Regime hint — codified market observation used in macro prompt
                "btc_dom_signal":  ("REDUCE_ALTCOIN_CONFIDENCE" if btc_dom > 56 else
                                    "ALTCOIN_SEASON_FAVORABLE"  if btc_dom < 50 else
                                    "NEUTRAL"),
                "stablecoin_trend_note": (
                    "stablecoin dominance high — sideline capital available (BULL fuel)"
                    if usdt_dom > 7 else
                    "stablecoin dominance low — capital already deployed"
                ),
            }
            _global_market_cache.update(result)
            logger.info(
                f"[Global] BTC dom={btc_dom}% | ETH dom={eth_dom}% | "
                f"USDT dom={usdt_dom}% | Mkt chg={mcap_chg:+.1f}% | {result['risk_sentiment']}"
            )
            return result
    except Exception as e:
        logger.warning(f"[Global] CoinGecko fetch failed: {e}")

    # Return cached or neutral fallback
    if _global_market_cache:
        return _global_market_cache
    return {
        "available": False, "btc_dominance": None, "eth_dominance": None,
        "stablecoin_dom": None, "mcap_change_24h": None,
        "risk_sentiment": "NEUTRAL", "altcoin_season": None,
        "btc_dom_signal": "NEUTRAL",
        "stablecoin_trend_note": "global market data unavailable",
    }

load_dotenv()

logger = logging.getLogger(__name__)

# ── QuestDB connection — module-level, reconnects on failure ──────────────────
# Opening a new pg8000 connection per query costs ~5-10ms each.
# 12 indicators × 6 symbols = 72 connections per pipeline run → 360-720ms wasted.
# Instead: one persistent connection, recreated only on error.

_DB_PARAMS = dict(
    host     = os.getenv("QUESTDB_HOST",    "localhost"),
    port     = int(os.getenv("QUESTDB_PG_PORT", "8812")),
    database = "qdb",
    user     = "admin",
    password = "quest",
)

# Thread-local connections: each thread (altcoin worker) gets its own pg8000
# connection, eliminating lock contention while still reusing connections across
# queries within the same thread (fixes both the 72-connections-per-run and the
# thread-safety issue introduced by parallel pipeline execution).
import threading as _threading
_tls = _threading.local()


def _get_conn() -> pg8000.Connection:
    if not getattr(_tls, "conn", None):
        _tls.conn = pg8000.connect(**_DB_PARAMS)
    return _tls.conn


def _query(sql: str) -> list:
    """Execute SQL against QuestDB. Thread-local connection, reconnects once on error."""
    for attempt in range(2):
        try:
            conn = _get_conn()
            c    = conn.cursor()
            c.execute(sql)
            return c.fetchall()
        except Exception as e:
            if attempt == 0:
                logger.debug(f"QuestDB query error — reconnecting: {e}")
                try:
                    if getattr(_tls, "conn", None):
                        _tls.conn.close()
                except Exception:
                    pass
                _tls.conn = None
            else:
                logger.warning(f"QuestDB query failed after reconnect: {e} | SQL: {sql[:120]}")
                return []
    return []


# ── Candle helper ─────────────────────────────────────────────────────────────

def _get_candles(symbol: str, sample_by: str, limit: int) -> pd.DataFrame:
    """
    Derive OHLCV candles at any timeframe from 1-minute base data.
    Returns DataFrame with columns [open, high, low, close, volume, ts], oldest first.
    """
    rows = _query(f"""
        SELECT first(open) o, max(high) h, min(low) l, last(close) c,
               sum(volume) v, timestamp ts
        FROM candles
        WHERE symbol='{symbol}' AND interval='1'
        SAMPLE BY {sample_by} ALIGN TO CALENDAR
        ORDER BY ts DESC
        LIMIT {limit}
    """)
    if not rows:
        return pd.DataFrame(columns=["open","high","low","close","volume","ts"])
    df = pd.DataFrame(rows, columns=["open","high","low","close","volume","ts"])
    # Reverse so oldest is first (needed for EMA/MACD/RSI rolling calculations)
    return df.iloc[::-1].reset_index(drop=True)


# ── 1. VWMA 20D ───────────────────────────────────────────────────────────────

def compute_vwma_20d(symbol: str) -> Tuple[Optional[float], Optional[bool]]:
    """
    20-day Volume Weighted Moving Average.
    Fetch 22 daily candles, drop the most-recent (incomplete), use last 20 complete days.
    Returns (vwma_value, price_above_vwma). Returns (None, None) if insufficient data.
    """
    df = _get_candles(symbol, "1d", 22)
    if len(df) < 21:
        logger.debug(f"[{symbol}] VWMA 20D: insufficient data ({len(df)} days)")
        return None, None

    df = df.iloc[:-1].tail(20)   # drop partial current day, keep 20 complete
    vwma = (df["close"] * df["volume"]).sum() / df["volume"].sum()

    # Current price from latest 1H candle
    price_rows = _get_candles(symbol, "1h", 2)
    if price_rows.empty:
        return float(vwma), None
    current_price = float(price_rows["close"].iloc[-1])
    return float(vwma), current_price > float(vwma)


# ── 2. EMA 4H 21 ─────────────────────────────────────────────────────────────

def compute_ema_4h_21(symbol: str) -> Tuple[Optional[float], Optional[bool]]:
    """
    21-period EMA on 4H closes. Fetch 63 periods for convergence warmup (3×).
    Returns (ema_value, price_above_ema). Returns (None, None) if insufficient.
    """
    df = _get_candles(symbol, "4h", 63)
    if len(df) < 42:   # 2× minimum
        return None, None

    alpha = 2.0 / (21 + 1)
    ema = df["close"].ewm(alpha=alpha, adjust=False).mean()
    ema_val = float(ema.iloc[-1])
    price   = float(df["close"].iloc[-1])
    return ema_val, price > ema_val


# ── 3. MACD(12,26,9) on 1H ───────────────────────────────────────────────────

def compute_macd_1h(symbol: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Standard MACD on 1H close prices.
    Returns (macd_line, signal_line, histogram). All None if insufficient data.
    """
    df = _get_candles(symbol, "1h", 100)
    if len(df) < 34:   # 26 + 9 - 1 minimum
        return None, None, None

    close = df["close"]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    hist  = macd - sig

    return float(macd.iloc[-1]), float(sig.iloc[-1]), float(hist.iloc[-1])


# ── 4. RSI(14) on 1H ─────────────────────────────────────────────────────────

def compute_rsi_1h(symbol: str) -> Optional[float]:
    """
    Wilder-smoothed RSI(14) on 1H close prices.
    Returns RSI value [0-100]. Returns None if insufficient data.
    """
    df = _get_candles(symbol, "1h", 50)
    if len(df) < 28:   # 2× minimum for Wilder convergence
        return None

    delta = df["close"].diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rs    = gain / loss.replace(0, 1e-10)   # avoid division by zero
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


# ── 5. ATR(14) on 1H ─────────────────────────────────────────────────────────

def compute_atr_1h(symbol: str) -> Optional[float]:
    """
    Average True Range (14) on 1H OHLC. Used for stop-loss distance calculation.
    """
    df = _get_candles(symbol, "1h", 30)
    if len(df) < 15:
        return None

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    return float(atr.iloc[-1])


# ── 6 & 7. CVD (rolling 4H window) ───────────────────────────────────────────

def compute_cvd(symbol: str, market_type: str, window_hours: int = 4) -> dict:
    """
    Rolling CVD: cumulative (buy_volume - sell_volume) over the last N hours.
    Stateless across restarts — window is always relative to now.

    sign convention: Buy trade → +size, Sell trade → -size
    This matches: Binance isBuyerMaker=False → 'Buy' → positive CVD

    market_type: 'spot'    → Binance spot (Spot CVD — the truth signal)
                 'futures' → Bybit + Binance futures (Futures CVD)
    """
    rows = _query(f"""
        SELECT timestamp ts,
               sum(CASE WHEN side='Buy' THEN size ELSE -size END) net_vol
        FROM trades
        WHERE symbol='{symbol}' AND market_type='{market_type}'
          AND timestamp > dateadd('h', -{window_hours}, now())
        SAMPLE BY 1m ALIGN TO CALENDAR
        ORDER BY ts
    """)

    if not rows or len(rows) < 6:
        return {
            "direction": "flat",
            "slope": 0.0,
            "series_tail": [],
            "history_minutes": len(rows) if rows else 0,
            "available": len(rows) >= 3,
        }

    net_vols = [float(r[1] or 0) for r in rows]
    cvd_series = list(pd.Series(net_vols).cumsum())

    # Slope from last 6 buckets (direction signal)
    last6 = cvd_series[-6:]
    baseline = abs(last6[0]) if abs(last6[0]) > 1e-10 else 1.0
    slope = (last6[-1] - last6[0]) / baseline

    if slope > 0.02:       direction = "rising"
    elif slope < -0.02:    direction = "falling"
    else:                  direction = "flat"

    return {
        "direction": direction,
        "slope": round(slope, 6),
        "series_tail": [round(v, 4) for v in cvd_series[-20:]],
        "history_minutes": len(rows),
        "available": True,
    }


def compute_cvd_matrix_state(spot_dir: str, futures_dir: str) -> str:
    """
    Map the Spot CVD direction and Futures CVD direction to one of the 4 playbook states.
    See Part 3 of the Complete Trading System v3.0.
    """
    if spot_dir == "rising"  and futures_dir == "rising":                        return "BOTH_RISING"
    if spot_dir == "falling" and futures_dir == "falling":                        return "BOTH_FALLING"
    if futures_dir == "rising"  and spot_dir in ("flat", "falling"):              return "FUT_UP_SPOT_FLAT"
    if futures_dir == "falling" and spot_dir in ("flat", "rising"):               return "FUT_DOWN_SPOT_FLAT"
    # Fallback: use spot direction as truth
    if spot_dir == "rising":   return "BOTH_RISING"
    if spot_dir == "falling":  return "BOTH_FALLING"
    return "BOTH_FLAT"     # flat/flat = neutral, NOT bullish (prevents spurious long bias)


# ── 8. OI Trend ───────────────────────────────────────────────────────────────

def compute_oi_trend(symbol: str) -> Tuple[str, float]:
    """
    Open interest trend from last 6 five-minute records.
    Returns (trend_str, slope_pct).
    """
    rows = _query(f"""
        SELECT open_interest, timestamp
        FROM open_interest
        WHERE symbol='{symbol}' AND interval='5min'
        ORDER BY timestamp DESC
        LIMIT 6
    """)
    if not rows or len(rows) < 3:
        return "unknown", 0.0

    vals = [float(r[0]) for r in reversed(rows)]   # oldest first
    if vals[0] == 0:
        return "unknown", 0.0

    slope = (vals[-1] - vals[0]) / vals[0]

    if slope > 0.003:       return "rising",  round(slope, 6)
    elif slope < -0.003:    return "falling", round(slope, 6)
    else:                   return "flat",    round(slope, 6)


# ── 9. Funding Rate ───────────────────────────────────────────────────────────

def compute_funding(symbol: str) -> dict:
    """
    Latest funding rate and context for regime analysis.
    Buckets align with playbook thresholds (Part 0A and Part 3).
    """
    rows = _query(f"""
        SELECT funding_rate, timestamp
        FROM funding_rates
        WHERE symbol='{symbol}'
        ORDER BY timestamp DESC
        LIMIT 4
    """)
    if not rows:
        return {"current": 0.0, "bucket": "unknown", "extreme_positive": False, "extreme_negative": False}

    current = float(rows[0][0])
    prev    = [float(r[0]) for r in rows[1:]]

    # Playbook thresholds (per 8h):
    # extreme+: ≥0.1% (0.001) = longs paying 0.3%/day → flush imminent
    # high+:    ≥0.05% (0.0005) = distributing
    # moderate+: 0.01-0.05% = healthy bull
    # near_zero: |rate| < 0.02% (0.0002) = balanced
    # negative:  < 0 = shorts paying, potential squeeze
    if current >= 0.001:      bucket = "extreme+"
    elif current >= 0.0005:   bucket = "high+"
    elif current >= 0.0001:   bucket = "moderate+"
    elif abs(current) < 0.0002: bucket = "near_zero"
    else:                     bucket = "negative"

    return {
        "current": current,
        "prev_3":  prev,
        "bucket":  bucket,
        "extreme_positive": current >= 0.0008,   # 0.08% per 8h — playbook threshold
        "extreme_negative": current <= -0.0005,  # -0.05% per 8h
    }


# ── 10. Bid/Ask Delta ────────────────────────────────────────────────────────

def compute_bid_ask_delta(symbol: str) -> Tuple[float, str]:
    """
    Bid vs Ask volume imbalance from the latest orderbook snapshot (top-20 levels).
    Returns (ratio, sentiment). ratio > 0 = more bids (bullish).
    """
    rows = _query(f"""
        SELECT bids, asks
        FROM orderbook
        WHERE symbol='{symbol}'
        LATEST ON timestamp PARTITION BY symbol
    """)
    if not rows:
        return 0.0, "neutral"

    try:
        bids = json.loads(rows[0][0])[:20]
        asks = json.loads(rows[0][1])[:20]
        bid_vol = sum(float(b[1]) for b in bids)
        ask_vol = sum(float(a[1]) for a in asks)
        total = bid_vol + ask_vol
        if total < 1e-10:
            return 0.0, "neutral"
        ratio = (bid_vol - ask_vol) / total
        sentiment = "positive" if ratio > 0.1 else ("negative" if ratio < -0.1 else "neutral")
        return round(float(ratio), 4), sentiment
    except Exception as e:
        logger.debug(f"[{symbol}] Bid/ask delta parse error: {e}")
        return 0.0, "neutral"


# ── 10b. Long/Short Ratio — replaces the hallucinated "Volume" indicator ──────

def compute_lsr(symbol: str) -> dict:
    """
    Long/Short Ratio from Bybit + Binance combined (last 6 five-minute records).
    Returns direction, current ratio, and whether positioning is extreme.
    Replaces the "Volume" indicator which had no data source — this IS collected.
    Extreme long positioning (>70%) is a CONTRARIAN BEARISH signal per the playbook.
    Extreme short positioning (<30%) is a CONTRARIAN BULLISH signal.
    """
    rows = _query(f"""
        SELECT buy_ratio, sell_ratio, timestamp
        FROM long_short_ratio
        WHERE symbol='{symbol}' AND interval='5min'
        ORDER BY timestamp DESC
        LIMIT 6
    """)
    if not rows or len(rows) < 2:
        return {
            "available":     False,
            "buy_ratio":     None,
            "sell_ratio":    None,
            "trend":         "unknown",
            "extreme_long":  False,
            "extreme_short": False,
            "signal":        "NEUTRAL",
        }

    buy_now  = float(rows[0][0] or 0)
    sell_now = float(rows[0][1] or 0)
    buy_old  = float(rows[-1][0] or 0)

    trend = ("rising_longs"  if buy_now > buy_old + 0.02 else
             "falling_longs" if buy_now < buy_old - 0.02 else
             "stable")

    extreme_long  = buy_now > 0.70   # crowded longs → contrarian bearish
    extreme_short = buy_now < 0.30   # crowded shorts → contrarian bullish

    signal = ("BEARISH" if extreme_long else
              "BULLISH" if extreme_short else
              "NEUTRAL")

    return {
        "available":     True,
        "buy_ratio":     round(buy_now, 4),
        "sell_ratio":    round(sell_now, 4),
        "trend":         trend,
        "extreme_long":  extreme_long,
        "extreme_short": extreme_short,
        "signal":        signal,
    }


# ── 10c. Order Book Depth — real depth analysis (replaces the OB≡BidAsk duplicate) ─

def compute_order_book_depth(symbol: str) -> dict:
    """
    Real order book analysis: compare top-5 vs bottom-5 bid/ask levels separately.
    The bid_ask_delta (indicator #5) uses aggregate 20-level ratio.
    This uses level structure to detect walls and thin zones — genuinely different data.
    """
    rows = _query(f"""
        SELECT bids, asks
        FROM orderbook
        WHERE symbol='{symbol}'
        LATEST ON timestamp PARTITION BY symbol
    """)
    if not rows:
        return {"available": False, "wall_side": "none", "wall_strength": 0.0}

    try:
        bids = json.loads(rows[0][0])[:20]
        asks = json.loads(rows[0][1])[:20]

        # Top-5 levels (closest to price) vs levels 16-20 (further from price)
        top5_bid  = sum(float(b[1]) for b in bids[:5])
        deep5_bid = sum(float(b[1]) for b in bids[15:20]) if len(bids) >= 20 else 0
        top5_ask  = sum(float(a[1]) for a in asks[:5])
        deep5_ask = sum(float(a[1]) for a in asks[15:20]) if len(asks) >= 20 else 0

        # Wall detection: if top-5 bid >> top-5 ask, there is a bid wall (support)
        total_top = top5_bid + top5_ask
        wall_ratio = (top5_bid - top5_ask) / max(total_top, 1e-10)

        wall_side     = "bid_wall" if wall_ratio > 0.2 else "ask_wall" if wall_ratio < -0.2 else "balanced"
        wall_strength = round(abs(wall_ratio), 4)

        return {
            "available":     True,
            "wall_side":     wall_side,
            "wall_strength": wall_strength,
            "top5_bid_vol":  round(top5_bid, 4),
            "top5_ask_vol":  round(top5_ask, 4),
            "signal": "BULLISH" if wall_side == "bid_wall" else
                      "BEARISH" if wall_side == "ask_wall" else "NEUTRAL",
        }
    except Exception as e:
        logger.debug(f"[{symbol}] Order book depth parse error: {e}")
        return {"available": False, "wall_side": "none", "wall_strength": 0.0}


# ── 11. Liquidation Events ────────────────────────────────────────────────────

def compute_liquidation_events(symbol: str) -> dict:
    """
    Recent liquidation cascade events (past 24H).
    NOT a predictive heatmap — these are actual historical forced liquidations.
    Used to identify recent price levels where large positions were liquidated
    (these levels may act as support/resistance or indicate position cluster exhaustion).
    """
    rows = _query(f"""
        SELECT side, price, size, timestamp
        FROM liquidations
        WHERE symbol='{symbol}'
          AND timestamp > dateadd('h', -24, now())
        ORDER BY size DESC
        LIMIT 50
    """)
    if not rows:
        return {
            "long_liq_usd_24h":   0.0,
            "short_liq_usd_24h":  0.0,
            "significant_levels": [],
            "note": "Historical liquidation cascade events (past 24H), not a predictive heatmap",
        }

    long_liq  = sum(float(r[2]) * float(r[1]) for r in rows if r[0] == "Sell")
    short_liq = sum(float(r[2]) * float(r[1]) for r in rows if r[0] == "Buy")
    top5 = sorted(
        [(float(r[1]), float(r[2]) * float(r[1]), r[0]) for r in rows],
        key=lambda x: -x[1]
    )[:5]

    return {
        "long_liq_usd_24h":   round(long_liq, 2),
        "short_liq_usd_24h":  round(short_liq, 2),
        "significant_levels": [{"price": l[0], "usd_notional": round(l[1], 2), "side": l[2]} for l in top5],
        "note": "Historical liquidation cascade events (past 24H), not a predictive heatmap",
    }


# ── 12. VPVR (approximate) ───────────────────────────────────────────────────

def compute_vpvr(symbol: str, n_buckets: int = 100) -> dict:
    """
    Approximate Volume Profile Visible Range from 4H OHLCV data (last 30 days).
    Method: assign each candle's volume to its typical price bucket.
    Returns POC (highest volume price), whether current price is in HVN or LVN.

    Label as approximate in the UI — real VPVR uses tick-level data.
    """
    df = _get_candles(symbol, "4h", 180)   # 30 days × 6 4H periods
    if len(df) < 20:
        return {"available": False, "poc": None, "in_hvn": False, "in_lvn": False}

    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    if price_min >= price_max:
        return {"available": False, "poc": None, "in_hvn": False, "in_lvn": False}

    bins = np.linspace(price_min, price_max, n_buckets + 1)
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0

    bucket_idx = np.clip(np.digitize(typical_price.values, bins) - 1, 0, n_buckets - 1)
    vol_by_bucket = np.zeros(n_buckets)
    for idx, vol in zip(bucket_idx, df["volume"].values):
        vol_by_bucket[idx] += vol

    poc_idx   = int(np.argmax(vol_by_bucket))
    poc_price = float((bins[poc_idx] + bins[poc_idx + 1]) / 2)

    current_price = float(df["close"].iloc[-1])
    cur_bucket    = int(np.clip(np.digitize([current_price], bins)[0] - 1, 0, n_buckets - 1))

    hvn_threshold = float(np.percentile(vol_by_bucket, 70))
    lvn_threshold = float(np.percentile(vol_by_bucket, 20))

    return {
        "available": True,
        "poc": round(poc_price, 4),
        "in_hvn": bool(vol_by_bucket[cur_bucket] >= hvn_threshold),
        "in_lvn": bool(vol_by_bucket[cur_bucket] <= lvn_threshold),
        "current_bucket_vol_pct": round(float(vol_by_bucket[cur_bucket]) / max(vol_by_bucket.sum(), 1) * 100, 2),
        "note": "Approximate VPVR from 4H OHLCV (30D window). Real VPVR uses tick data.",
        # Compact profile for dashboard chart
        "profile": [
            {"price": round(float((bins[i] + bins[i+1]) / 2), 2), "volume": round(float(v), 2)}
            for i, v in enumerate(vol_by_bucket) if v > 0
        ],
    }


# ── Session detection ─────────────────────────────────────────────────────────

def detect_session() -> str:
    """
    Map current UTC hour to trading session per the playbook (Part 1).
    Funding resets at 00:00, 08:00, 16:00 UTC — flagged in the snapshot.
    """
    now_utc = datetime.now(timezone.utc)
    h = now_utc.hour
    m = now_utc.minute

    # Session boundaries match the trading playbook Part 1 exactly:
    #   LONDON     07:00-11:00 UTC  (checked first to prevent ASIAN swallowing 07:00)
    #   NY         13:00-17:00 UTC
    #   ASIAN      01:00-07:00 UTC
    #   DEAD_HOURS everything else: 11:00-13:00 (pre-NY gap), 17:00-01:00 (overnight)
    session = (
        "LONDON"     if  7 <= h < 11  else
        "NY"         if 13 <= h < 17  else
        "ASIAN"      if  1 <= h <  7  else
        "DEAD_HOURS"                          # 0:00, 11-12, 17-23 UTC
    )

    # Flag the 30-minute funding reset noise window
    funding_reset_hours = {0, 8, 16}
    near_funding_reset = h in funding_reset_hours and m < 30

    return session, near_funding_reset


# ── Scenario → Playbook Mapping (deterministic — no LLM) ─────────────────────
# Maps each scenario number to: base direction, minimum confluence for that direction,
# and the maximum direction the LLM is allowed to escalate to.
# The LLM CANNOT change direction. It can only escalate intensity or call NO_TRADE
# if confluence falls below the minimum. This makes verdicts explainable and defensible.

SCENARIO_PLAYBOOK = {
    1: {"base_direction": "LONG",    "min_confluence": 5,  "can_escalate": True,
        "description": "HEALTHY_UPTREND — price above VWMA, spot CVD rising, OI rising, MACD positive"},
    2: {"base_direction": "NEUTRAL", "min_confluence": 6,  "can_escalate": False,
        "description": "UPTREND_WEAKENING — price above VWMA but spot CVD not rising. Wait."},
    3: {"base_direction": "SHORT",   "min_confluence": 6,  "can_escalate": True,
        "description": "CONFIRMED_REVERSAL_FROM_TOP — below VWMA, spot CVD falling, OI flushing"},
    4: {"base_direction": "SHORT",   "min_confluence": 5,  "can_escalate": True,
        "description": "HEALTHY_DOWNTREND — below VWMA, spot falling, OI rising (new shorts)"},
    5: {"base_direction": "NO_TRADE","min_confluence": 99, "can_escalate": False,
        "description": "DEAD_CAT_BOUNCE — price above VWMA but OI falling (short covering only)"},
    6: {"base_direction": "NEUTRAL", "min_confluence": 7,  "can_escalate": False,
        "description": "BOTTOM_FORMING — below VWMA, CVD not falling. Wait for confirmation."},
    7: {"base_direction": "LONG",    "min_confluence": 6,  "can_escalate": True,
        "description": "CONFIRMED_REVERSAL_FROM_BOTTOM — above VWMA, CVD rising, OI rising but MACD/funding not bullish yet"},
    8: {"base_direction": "NEUTRAL", "min_confluence": 7,  "can_escalate": False,
        "description": "RANGING_CONSOLIDATION — range-fade only at extremes"},
    9: {"base_direction": "NO_TRADE","min_confluence": 99, "can_escalate": False,
        "description": "MANIPULATION_DETECTED — do not trade"},
}


# ── Deterministic Decision Tree (Q1-Q4) ──────────────────────────────────────

def run_decision_tree(snapshot: dict) -> Tuple[int, str, dict]:
    """
    Run the 4-question phase identification decision tree from Part 2 of the playbook.
    All questions are answered deterministically from the snapshot — no LLM needed here.
    Returns (scenario_number, scenario_name, trace_dict).
    """
    trace = {}

    price        = snapshot.get("current_price")
    vwma         = snapshot.get("vwma_20d")
    spot_dir     = snapshot.get("spot_cvd", {}).get("direction", "flat")
    oi_trend     = snapshot.get("oi_trend", "flat")
    funding_cur  = snapshot.get("funding", {}).get("current", 0.0)
    macd_hist    = snapshot.get("macd", {}).get("histogram")

    # Need at least price and VWMA to run the tree
    if price is None or vwma is None:
        trace["error"] = "insufficient_data"
        return 8, "RANGING_CONSOLIDATION", trace

    # ── Range shortcut (check before Q1) ──────────────────────────────────────
    # If price has been oscillating near VWMA, CVD is flat, → S8
    price_near_vwma = abs(price - vwma) / max(vwma, 1) < 0.03
    if price_near_vwma and spot_dir == "flat" and oi_trend == "flat":
        trace["range_shortcut"] = True
        return 8, "RANGING_CONSOLIDATION", trace

    trace["range_shortcut"] = False

    # ── Q1: Price above or below 4H VWMA? ────────────────────────────────────
    q1_above_vwma = price > vwma
    trace["Q1_above_vwma"] = q1_above_vwma

    if q1_above_vwma:
        # ── Q2A: Spot CVD rising over last 6+ candles? ───────────────────────
        q2a_spot_rising = spot_dir == "rising"
        trace["Q2A_spot_cvd_rising"] = q2a_spot_rising

        if q2a_spot_rising:
            # ── Q3A: OI rising alongside price? ──────────────────────────────
            q3a_oi_rising = oi_trend == "rising"
            trace["Q3A_oi_rising_with_price"] = q3a_oi_rising

            if q3a_oi_rising:
                # ── Q4: Funding neutral-to-positive AND MACD above zero? ─────
                q4_funding_ok = funding_cur >= 0
                q4_macd_ok    = macd_hist is not None and macd_hist > 0
                q4            = q4_funding_ok and q4_macd_ok
                trace["Q4_funding_positive"] = q4_funding_ok
                trace["Q4_macd_above_zero"]  = q4_macd_ok

                if q4:
                    return 1, "HEALTHY_UPTREND", trace
                else:
                    return 7, "CONFIRMED_REVERSAL_FROM_BOTTOM", trace
            else:
                # OI falling while price rising = short covering only
                return 5, "DEAD_CAT_BOUNCE", trace
        else:
            # Price above VWMA but Spot CVD not rising → uptrend weakening
            return 2, "UPTREND_WEAKENING", trace

    else:   # price BELOW vwma
        # ── Q2B: Spot CVD falling over last 6+ candles? ──────────────────────
        q2b_spot_falling = spot_dir == "falling"
        trace["Q2B_spot_cvd_falling"] = q2b_spot_falling

        if q2b_spot_falling:
            # ── Q3B: OI rising? ───────────────────────────────────────────────
            q3b_oi_rising = oi_trend == "rising"
            trace["Q3B_oi_rising"] = q3b_oi_rising

            if q3b_oi_rising:
                # New shorts entering into declining price = healthy downtrend
                return 4, "HEALTHY_DOWNTREND", trace
            else:
                # OI crashing (liquidations) while price falls = reversal from top
                return 3, "CONFIRMED_REVERSAL_FROM_TOP", trace
        else:
            # Price below VWMA but Spot CVD not falling → bottom forming
            return 6, "BOTTOM_FORMING", trace


# ── Master snapshot builder ───────────────────────────────────────────────────

def get_snapshot(symbol: str, include_mantle: bool = True,
                  global_market: dict = None) -> dict:
    """
    Build the complete per-symbol market snapshot used by agents/pipeline.py.
    global_market: pass result of get_global_market_context() — fetched once per run,
                   shared across all symbols to avoid repeated CoinGecko calls.
    """
    logger.info(f"[{symbol}] Building market snapshot...")

    # ── Price (from latest 1H candle) ─────────────────────────────────────────
    price_df = _get_candles(symbol, "1h", 2)
    current_price = float(price_df["close"].iloc[-1]) if not price_df.empty else None

    # ── Core indicators ───────────────────────────────────────────────────────
    vwma_20d, above_vwma   = compute_vwma_20d(symbol)
    ema_4h_21, above_ema   = compute_ema_4h_21(symbol)
    macd_val, macd_sig, macd_hist = compute_macd_1h(symbol)
    rsi_14                 = compute_rsi_1h(symbol)
    atr_14                 = compute_atr_1h(symbol)

    # ── CVD — MNTUSDT uses FusionX DEX as primary spot signal ───────────────
    # For Mantle's native token, on-chain DEX flow = "real money" conviction.
    # FusionX WMNT/USDT swaps are the equivalent of Binance spot CVD for BTC.
    # Cached here and reused in get_mantle_signals() to avoid a duplicate RPC call.
    _mnt_dex_cvd_cache = None   # shared with mantle_signals call below

    if symbol == "MNTUSDT" and include_mantle:
        from mantle_integrations import get_fusionx_dex_cvd as _get_dex_cvd
        _dex = _get_dex_cvd()
        _mnt_dex_cvd_cache = _dex   # cache to pass to mantle_signals
        if _dex.get("available") and _dex.get("swap_count", 0) >= 3:
            _dex_dir = _dex.get("direction", "flat")
            spot_cvd = {
                "direction":       _dex_dir,
                "slope":           _dex.get("cvd_delta", 0) / max(abs(_dex.get("cvd_delta", 1)), 1),
                "series_tail":     [],
                "history_minutes": _dex.get("minutes_scanned", 0),
                "available":       True,
                "source":          "FusionX_DEX_onchain",  # Mantle-native primary signal
                "swap_count":      _dex.get("swap_count", 0),
                "cvd_delta":       _dex.get("cvd_delta", 0),
            }
        else:
            spot_cvd = compute_cvd(symbol, "spot", window_hours=4)
            spot_cvd["source"] = "binance_spot_fallback"
        futures_cvd = compute_cvd(symbol, "futures", window_hours=4)
    else:
        spot_cvd    = compute_cvd(symbol, "spot",    window_hours=4)
        futures_cvd = compute_cvd(symbol, "futures", window_hours=4)

    cvd_state = compute_cvd_matrix_state(spot_cvd["direction"], futures_cvd["direction"])

    # ── Market structure ─────────────────────────────────────────────────────
    oi_trend, oi_slope = compute_oi_trend(symbol)
    funding            = compute_funding(symbol)
    bid_ask_ratio, bid_ask_sent = compute_bid_ask_delta(symbol)
    liq_events         = compute_liquidation_events(symbol)
    vpvr               = compute_vpvr(symbol)
    lsr                = compute_lsr(symbol)
    ob_depth           = compute_order_book_depth(symbol)

    # ── Session ───────────────────────────────────────────────────────────────
    session, near_funding_reset = detect_session()

    # ── Mantle DeFi signals (for ETHUSDT and MNTUSDT primarily) ──────────────
    mantle_signals = None
    if include_mantle and symbol in ("ETHUSDT", "MNTUSDT"):
        try:
            eth_funding_8h = funding["current"] if symbol == "ETHUSDT" else 0.0
            if symbol == "MNTUSDT" and _mnt_dex_cvd_cache is not None:
                # Reuse already-fetched FusionX data — avoid a second RPC call
                from mantle_integrations import (
                    get_meth_yield_signal as _get_meth,
                )
                meth_sig = _get_meth(eth_funding_8h)
                dex_dir  = _mnt_dex_cvd_cache.get("direction", "flat")
                meth_dir = meth_sig.get("signal", "NEUTRAL")
                combined = ("BULLISH"         if meth_dir == "BULLISH" and dex_dir == "rising" else
                            "BEARISH"         if meth_dir == "BEARISH" and dex_dir == "falling" else
                            "CAUTIOUS_BULLISH" if meth_dir == "BULLISH" or dex_dir == "rising" else
                            "CAUTIOUS_BEARISH" if meth_dir == "BEARISH" or dex_dir == "falling" else
                            "NEUTRAL")
                mantle_signals = {
                    "meth_yield_signal":  meth_sig,
                    "fusionx_dex_cvd":    _mnt_dex_cvd_cache,
                    "combined_direction": combined,
                    "combined_note":      f"Cached FusionX DEX CVD ({_mnt_dex_cvd_cache.get('swap_count',0)} swaps)",
                }
            else:
                mantle_signals = get_mantle_signals(eth_funding_rate_8h=eth_funding_8h)
        except Exception as e:
            logger.warning(f"[{symbol}] Mantle signals failed (non-fatal): {e}")

    # ── Assemble snapshot ─────────────────────────────────────────────────────
    snapshot = {
        "symbol":        symbol,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "current_price": current_price,
        "session":       session,
        "near_funding_reset": near_funding_reset,

        # Indicator 1: VWMA 20D
        "vwma_20d":      vwma_20d,
        "above_vwma_20d": above_vwma,

        # Indicator 2: EMA 4H 21
        "ema_4h_21":     ema_4h_21,
        "above_ema_4h_21": above_ema,

        # Indicator 3: MACD 1H
        "macd": {
            "value":     macd_val,
            "signal":    macd_sig,
            "histogram": macd_hist,
            "above_zero": macd_hist is not None and macd_hist > 0,
        },

        # Indicator 4: RSI 14 1H
        "rsi_14":  rsi_14,

        # Indicator 5: ATR 14 1H (for stop-loss sizing)
        "atr_14":  atr_14,

        # Indicators 6 & 7: CVD
        "spot_cvd":    spot_cvd,
        "futures_cvd": futures_cvd,
        "cvd_matrix_state": cvd_state,

        # Indicator 8: OI
        "oi_trend": oi_trend,
        "oi_slope": oi_slope,

        # Indicator 9: Funding
        "funding": funding,

        # Indicator 10: Bid/Ask Delta (aggregate imbalance)
        "bid_ask": {"ratio": bid_ask_ratio, "sentiment": bid_ask_sent},

        # Indicator 11 (NEW): Long/Short Ratio — replaces hallucinated "Volume"
        # This is COLLECTED data (long_short_ratio table). Extreme positioning = contrarian signal.
        "lsr": lsr,

        # Indicator 12: Liquidations
        "liquidations": liq_events,

        # Indicator 13: VPVR (approximate)
        "vpvr": vpvr,

        # Indicator 14: Order Book Depth (real wall detection — not a duplicate of bid_ask)
        "ob_depth": ob_depth,

        # Indicator 15: Mantle DeFi (mETH yield + FusionX DEX CVD)
        "mantle_signals": mantle_signals,

        # Global macro context (BTC dominance, stablecoin supply, risk sentiment)
        # Fixes the documented gap: BULL regime required stablecoin supply data
        "global_market":  global_market or {"available": False},
    }

    # ── Deterministic decision tree ───────────────────────────────────────────
    scenario_num, scenario_name, tree_trace = run_decision_tree(snapshot)
    playbook_entry = SCENARIO_PLAYBOOK.get(scenario_num, SCENARIO_PLAYBOOK[8])

    snapshot["scenario_number"]      = scenario_num
    snapshot["scenario_name"]        = scenario_name
    snapshot["decision_tree_trace"]  = tree_trace
    # Deterministic base direction — LLM cannot override this
    snapshot["base_direction"]       = playbook_entry["base_direction"]
    snapshot["scenario_min_confluence"] = playbook_entry["min_confluence"]
    snapshot["scenario_can_escalate"]   = playbook_entry["can_escalate"]

    logger.info(
        f"[{symbol}] Snapshot complete → "
        f"S{scenario_num} {scenario_name} | CVD: {cvd_state} | "
        f"Session: {session} | RSI: {rsi_14:.1f}" if rsi_14 else
        f"[{symbol}] Snapshot complete → S{scenario_num} | CVD: {cvd_state}"
    )

    return snapshot


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    snap = get_snapshot("BTCUSDT", include_mantle=False)
    # Exclude large VPVR profile from print for readability
    snap_print = {k: v for k, v in snap.items() if k != "vpvr"}
    print(json.dumps(snap_print, indent=2, default=str))
