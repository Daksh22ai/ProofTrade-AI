# api/: FastAPI Backend

Single-file FastAPI server that serves the React frontend build and exposes REST and SSE endpoints.

## File

`server.py` - all routes, QuestDB queries, deployment loading, and SSE event streaming.

## Start

```bash
# Development (with auto-reload)
uvicorn api.server:app --reload --port 8000

# Production
uvicorn api.server:app --host 0.0.0.0 --port 8000
```

The React build in `frontend/dist/` is served at `/` via `StaticFiles`. All `/api/*` routes are handled by FastAPI.

## Endpoints

### Analysis and Signals

| Method | Path | Description |
|---|---|---|
| GET | `/api/signals` | Latest verdict, confidence, confluence, price, tx hash for all 6 symbols |
| GET | `/api/analysis/{symbol}` | Full analysis JSON for one symbol from `analysis_results/` |
| GET | `/api/verify/{symbol}` | Recompute hash and compare to stored value |

### Chart Data (from QuestDB)

| Method | Path | Query Params | Description |
|---|---|---|---|
| GET | `/api/chart/{symbol}` | `tf=1h` (1h, 4h, 1d), `limit=200` | OHLCV candles via SAMPLE BY |
| GET | `/api/cvd/{symbol}` | `market_type=spot`, `hours=4` | Rolling CVD series, 1-minute buckets |
| GET | `/api/market/{symbol}` | none | OI, funding rate, liquidation stats, LSR |

### On-Chain

| Method | Path | Query Params | Description |
|---|---|---|---|
| GET | `/api/deployment` | none | Returns `deployment.json` content (contract addresses, explorer URLs) |
| GET | `/api/position-check` | `symbol`, `leverage`, `min_confidence` | Calls StrategyGate.checkPositionAllowedView() on Mantle Sepolia |

### Real-time

| Method | Path | Query Params | Description |
|---|---|---|---|
| GET | `/api/stream` | `symbol=BTCUSDT` (optional) | Server-Sent Events; pushes `analysis_update` when JSON files change |

### Utility

| Method | Path |
|---|---|
| GET | `/api/health` |

## QuestDB Connection

Uses `pg8000` (pure Python, no binary dependencies) connecting to QuestDB's PostgreSQL wire protocol on port 8812.

Timestamps returned by QuestDB `SAMPLE BY` queries are epoch microseconds (16 digits). The CVD and candle endpoints include `_to_ts()` which detects the magnitude and converts to Unix seconds for Lightweight Charts compatibility. Duplicate timestamps (which SAMPLE BY can produce) are deduplicated before returning, as Lightweight Charts requires strictly ascending time.

## CVD Endpoint Notes

The CVD series is the cumulative sum of `(buy_volume - sell_volume)` per 1-minute bucket over the requested window. The baseline is the first value in the window (not zero), so the chart shows change rather than a total that grows indefinitely.

For MNTUSDT Spot CVD: when the FusionX DEX source is active, the trades come from Mantle mainnet swap events decoded via `mantle_integrations.py`. These still flow through the same `trades` table tagged with `market_type=spot` and `exchange=fusionx`, so the CVD endpoint works identically for all symbols.

## Analysis Results

`run_pipeline.py` writes results to `analysis_results/{SYMBOL}_latest.json` using `atomic_write()` (tempfile + `os.replace()`). The API reads these files directly; no database is involved for analysis results. This means the analysis endpoint is always reading a complete, consistent JSON file.

## StrategyGate Position Check

The `/api/position-check` endpoint:
1. Reads `strategy_gate_address` from `deployment.json`
2. Calls `StrategyGate.checkPositionAllowedView(symbol, leverage, minConfidence)` as a view call (no gas, no transaction)
3. Returns the result including whether the position is allowed, the maximum permitted leverage, and the reason string

If StrategyGate is not deployed yet (no `strategy_gate_address` in `deployment.json`), the endpoint returns HTTP 503 with instructions to run `deploy.js`.

## SSE Event Format

```json
{
  "type": "analysis_update",
  "symbol": "BTCUSDT",
  "verdict": "LONG",
  "confidence": 72,
  "confluence": 8,
  "price": 104832.0,
  "timestamp": "2026-06-15T14:08:22Z",
  "tx_hash": "0x..."
}
```

The SSE generator polls the JSON files every 10 seconds and emits an event only when the `timestamp_utc` field has changed. The frontend receives these events and updates the signal board without a full page refresh.
