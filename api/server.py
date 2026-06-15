"""
api/server.py — FastAPI backend for the Mantle AI Trading Copilot.

Serves the React frontend and exposes REST + SSE endpoints for:
  - Analysis results (from analysis_results/*.json)
  - QuestDB chart data (candles, CVD, OI, funding)
  - Deployment info (AuditLog contract address)
  - Live updates via Server-Sent Events

Run: uvicorn api.server:app --reload --port 8000 --host 0.0.0.0
"""

import os
import sys
import json
import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, AsyncGenerator

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from dotenv import load_dotenv
import pg8000

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)8s] %(name)s | %(message)s")

# ── Constants ─────────────────────────────────────────────────────────────────

SYMBOLS        = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "MNTUSDT"]
RESULTS_DIR    = Path(__file__).parent.parent / "analysis_results"
DEPLOYMENT     = Path(__file__).parent.parent / "on_chain" / "deployment.json"
FALLBACK       = Path(__file__).parent.parent / "dashboard" / "demo_fallback.json"
FRONTEND_BUILD = Path(__file__).parent.parent / "frontend" / "dist"

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Mantle AI Trading Copilot API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── QuestDB connection (thread-local) ─────────────────────────────────────────

import threading
_tls = threading.local()

_DB_PARAMS = dict(
    host=os.getenv("QUESTDB_HOST", "localhost"),
    port=int(os.getenv("QUESTDB_PG_PORT", "8812")),
    database="qdb", user="admin", password="quest",
)


def _db_query(sql: str) -> list:
    for attempt in range(2):
        try:
            if not getattr(_tls, "conn", None):
                _tls.conn = pg8000.connect(**_DB_PARAMS)
            c = _tls.conn.cursor()
            c.execute(sql)
            return c.fetchall()
        except Exception as e:
            if attempt == 0:
                try:
                    if _tls.conn: _tls.conn.close()
                except Exception: pass
                _tls.conn = None
            else:
                logger.warning(f"QuestDB query failed: {e} | SQL: {sql[:80]}")
                return []
    return []


# ── Analysis loaders ──────────────────────────────────────────────────────────

def _load_analysis(symbol: str) -> Optional[dict]:
    path = RESULTS_DIR / f"{symbol}_latest.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    if FALLBACK.exists():
        try:
            with open(FALLBACK) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _load_deployment() -> Optional[dict]:
    if DEPLOYMENT.exists():
        try:
            with open(DEPLOYMENT) as f:
                return json.load(f)
        except Exception:
            pass
    return None


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": time.time()}


@app.get("/api/deployment")
def deployment():
    d = _load_deployment()
    if not d:
        raise HTTPException(404, "Contract not deployed yet")
    return d


@app.get("/api/signals")
def all_signals():
    """Summary of all 6 symbols — used by the signal board."""
    result = {}
    for sym in SYMBOLS:
        data = _load_analysis(sym)
        if data:
            analysis = data.get("analysis", {})
            result[sym] = {
                "symbol":          sym,
                "verdict":         analysis.get("verdict", "NEUTRAL"),
                "confidence":      analysis.get("confidence_score", 0),
                "confluence":      analysis.get("confluence_count", 0),
                "scenario_number": data.get("scenario_number", 0),
                "scenario_name":   data.get("scenario_name", ""),
                "cvd_state":       data.get("cvd_matrix_state", ""),
                "current_price":   data.get("current_price", 0),
                "session":         data.get("session", ""),
                "macro_regime":    data.get("macro_regime", {}).get("macro_regime", ""),
                "timestamp_utc":   data.get("timestamp_utc", ""),
                "audit_tx_hash":   data.get("audit_tx_hash"),
                "audit_explorer_url": data.get("audit_explorer_url"),
            }
        else:
            result[sym] = {"symbol": sym, "verdict": None}
    return result


@app.get("/api/analysis/{symbol}")
def analysis(symbol: str):
    """Full analysis for one symbol."""
    if symbol not in SYMBOLS:
        raise HTTPException(400, f"Unknown symbol: {symbol}")
    data = _load_analysis(symbol)
    if not data:
        raise HTTPException(404, f"No analysis for {symbol}")
    return data


@app.get("/api/chart/{symbol}")
def chart(symbol: str, tf: str = "1h", limit: int = 200):
    """OHLCV candles for TradingView Lightweight Charts."""
    if symbol not in SYMBOLS:
        raise HTTPException(400, f"Unknown symbol: {symbol}")
    tf_safe = tf if tf in ("1m", "5m", "15m", "1h", "4h", "1d") else "1h"
    rows = _db_query(f"""
        SELECT first(open), max(high), min(low), last(close), sum(volume), timestamp
        FROM candles
        WHERE symbol='{symbol}' AND interval='1'
        SAMPLE BY {tf_safe} ALIGN TO CALENDAR
        ORDER BY timestamp DESC
        LIMIT {min(limit, 500)}
    """)
    if not rows:
        return {"symbol": symbol, "tf": tf_safe, "candles": []}
    rows = list(reversed(rows))

    def _to_ts(v):
        if hasattr(v, "timestamp"):
            return int(v.timestamp())
        if isinstance(v, int):
            return v // 1_000_000 if v > 1e12 else v // 1_000 if v > 1e9 else v
        return int(v)

    candles = []
    seen_ts = set()
    for r in rows:
        if not all(v is not None for v in r[:4]):
            continue
        ts = _to_ts(r[5])
        if ts in seen_ts:
            continue
        seen_ts.add(ts)
        candles.append({
            "time":   ts,
            "open":   float(r[0]),
            "high":   float(r[1]),
            "low":    float(r[2]),
            "close":  float(r[3]),
            "volume": float(r[4] or 0),
        })

    return {"symbol": symbol, "tf": tf_safe, "candles": candles}


@app.get("/api/cvd/{symbol}")
def cvd(symbol: str, market_type: str = "spot", hours: int = 4):
    """Rolling CVD series for the CVD chart."""
    if symbol not in SYMBOLS:
        raise HTTPException(400, f"Unknown symbol: {symbol}")
    mt = "spot" if market_type == "spot" else "futures"
    rows = _db_query(f"""
        SELECT timestamp,
               sum(CASE WHEN side='Buy' THEN size ELSE -size END) net
        FROM trades
        WHERE symbol='{symbol}' AND market_type='{mt}'
          AND timestamp > dateadd('h', -{hours}, now())
        SAMPLE BY 1m ALIGN TO CALENDAR
        ORDER BY timestamp
    """)
    if not rows:
        return {"symbol": symbol, "market_type": mt, "series": []}

    import pandas as pd

    net_vols = [float(r[1] or 0) for r in rows]
    cvd_vals = list(pd.Series(net_vols).cumsum())

    def _to_ts(v):
        if hasattr(v, "timestamp"):
            return int(v.timestamp())
        if isinstance(v, int):
            # QuestDB returns epoch microseconds when using SAMPLE BY
            return v // 1_000_000 if v > 1e12 else v // 1_000 if v > 1e9 else v
        return int(v)

    series = [
        {"time": _to_ts(r[0]), "value": round(cvd_vals[i], 4)}
        for i, r in enumerate(rows)
        if r[0] is not None
    ]
    # lightweight-charts requires strictly ascending time — deduplicate
    seen, deduped = set(), []
    for s in series:
        if s["time"] not in seen:
            seen.add(s["time"])
            deduped.append(s)

    return {"symbol": symbol, "market_type": mt, "series": deduped}


@app.get("/api/market/{symbol}")
def market_metrics(symbol: str):
    """OI, funding rate, liquidation stats for metrics panel."""
    if symbol not in SYMBOLS:
        raise HTTPException(400, f"Unknown symbol: {symbol}")

    oi_rows   = _db_query(f"SELECT open_interest, timestamp FROM open_interest WHERE symbol='{symbol}' AND interval='5min' ORDER BY timestamp DESC LIMIT 1")
    fr_rows   = _db_query(f"SELECT funding_rate, timestamp FROM funding_rates WHERE symbol='{symbol}' ORDER BY timestamp DESC LIMIT 1")
    liq_rows  = _db_query(f"SELECT sum(price*size), count() FROM liquidations WHERE symbol='{symbol}' AND timestamp > dateadd('h',-24,now())")
    lsr_rows  = _db_query(f"SELECT buy_ratio, sell_ratio, timestamp FROM long_short_ratio WHERE symbol='{symbol}' ORDER BY timestamp DESC LIMIT 1")

    return {
        "symbol":       symbol,
        "open_interest": float(oi_rows[0][0]) if oi_rows else None,
        "funding_rate":  float(fr_rows[0][0]) if fr_rows else None,
        "liq_usd_24h":  float(liq_rows[0][0] or 0) if liq_rows else 0,
        "liq_count_24h": int(liq_rows[0][1] or 0) if liq_rows else 0,
        "lsr_buy":       float(lsr_rows[0][0]) if lsr_rows else None,
        "lsr_sell":      float(lsr_rows[0][1]) if lsr_rows else None,
    }


@app.get("/api/verify/{symbol}")
def verify(symbol: str):
    """Recompute hash and return verification result."""
    if symbol not in SYMBOLS:
        raise HTTPException(400, f"Unknown symbol: {symbol}")
    data = _load_analysis(symbol)
    if not data:
        raise HTTPException(404, f"No analysis for {symbol}")
    try:
        from on_chain.submit_audit import compute_hash, verify_hash as _verify
        recomputed, _ = compute_hash(data)
        claimed       = data.get("data_hash", "")
        match         = _verify(data, claimed)
        return {
            "symbol":        symbol,
            "match":         match,
            "recomputed":    recomputed,
            "claimed":       claimed,
            "audit_tx_hash": data.get("audit_tx_hash"),
            "audit_block":   data.get("audit_block"),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ── StrategyGate — on-chain position check ───────────────────────────────────

@app.get("/api/position-check")
def position_check(symbol: str, leverage: int = 5, min_confidence: int = 0):
    """
    Call StrategyGate.checkPositionAllowedView() on Mantle Sepolia.
    Demonstrates composability: the AI oracle is readable by any DeFi protocol.
    """
    if symbol not in SYMBOLS:
        raise HTTPException(400, f"Unknown symbol: {symbol}")

    d = _load_deployment()
    gate_addr = (d or {}).get("strategy_gate_address")
    if not gate_addr:
        raise HTTPException(503, "StrategyGate not deployed. Run: cd on_chain && npx hardhat run scripts/deploy.js --network mantleSepolia")

    try:
        from web3 import Web3
        from on_chain.submit_audit import _get_w3
        w3 = _get_w3()
        gate_abi = [
            {
                "inputs": [
                    {"name": "symbol",               "type": "string"},
                    {"name": "requestedLeverage",    "type": "uint8"},
                    {"name": "minConfidenceRequired","type": "uint8"},
                ],
                "name": "checkPositionAllowedView",
                "outputs": [
                    {"name": "allowed",  "type": "bool"},
                    {"name": "maxLev",   "type": "uint8"},
                    {"name": "reason",   "type": "string"},
                ],
                "stateMutability": "view", "type": "function",
            }
        ]
        gate = w3.eth.contract(
            address=Web3.to_checksum_address(gate_addr), abi=gate_abi
        )
        allowed, max_lev, reason = gate.functions.checkPositionAllowedView(
            symbol, min(leverage, 125), min_confidence
        ).call()
        return {
            "symbol":            symbol,
            "requested_leverage": leverage,
            "allowed":           allowed,
            "max_leverage":      int(max_lev),
            "reason":            reason,
            "gate_address":      gate_addr,
            "gate_explorer":     f"https://explorer.sepolia.mantle.xyz/address/{gate_addr}",
            "source":            "on-chain (Mantle Sepolia)",
        }
    except Exception as e:
        raise HTTPException(500, f"StrategyGate call failed: {e}")


# ── Server-Sent Events — live analysis updates ────────────────────────────────

_last_modified: dict = {}


async def _sse_generator(symbol: Optional[str]) -> AsyncGenerator[str, None]:
    """
    Yields SSE events when analysis_results/*.json files change.
    Frontend connects once and receives push updates.
    """
    symbols_to_watch = [symbol] if symbol else SYMBOLS

    while True:
        for sym in symbols_to_watch:
            path = RESULTS_DIR / f"{sym}_latest.json"
            if path.exists():
                mtime = path.stat().st_mtime
                if _last_modified.get(sym, 0) != mtime:
                    _last_modified[sym] = mtime
                    data = _load_analysis(sym)
                    if data:
                        analysis = data.get("analysis", {})
                        payload  = json.dumps({
                            "type":      "analysis_update",
                            "symbol":    sym,
                            "verdict":   analysis.get("verdict", "NEUTRAL"),
                            "confidence": analysis.get("confidence_score", 0),
                            "confluence": analysis.get("confluence_count", 0),
                            "price":     data.get("current_price", 0),
                            "timestamp": data.get("timestamp_utc", ""),
                            "tx_hash":   data.get("audit_tx_hash"),
                        })
                        yield f"data: {payload}\n\n"

        await asyncio.sleep(5)   # poll every 5 seconds


@app.get("/api/stream")
async def stream(symbol: Optional[str] = None):
    """Server-Sent Events endpoint — frontend subscribes once for live updates."""
    return StreamingResponse(
        _sse_generator(symbol),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":          "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── Serve React frontend build ────────────────────────────────────────────────

if FRONTEND_BUILD.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_BUILD / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        index = FRONTEND_BUILD / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(404, "Frontend not built. Run: cd frontend && npm run build")
else:
    @app.get("/")
    def root():
        return {"message": "API running. Build frontend: cd frontend && npm run build"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
