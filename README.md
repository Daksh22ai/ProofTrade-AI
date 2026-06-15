# Mantle AI Trading Copilot: The Turing Test

> **The first AI trading copilot where every recommendation is cryptographically proven to exist before price moves.**
> Explainable. Auditable. Built on Mantle.

[![Tests](https://img.shields.io/badge/tests-passing-00D4AA)](tests/)
[![Network](https://img.shields.io/badge/network-Mantle%20Sepolia-00D4AA)](https://explorer.sepolia.mantle.xyz)
[![Model](https://img.shields.io/badge/model-llama--3.3--70b-blue)](https://console.groq.com)

---

## What This System Does

Every AI trading signal in the market has the same problem: there is no way to verify that the signal existed before the price move. Any service can claim "we called this" after the fact.

This system solves that with a simple mechanism: before the user ever sees a recommendation, the full analysis JSON is keccak256-hashed and logged on Mantle Sepolia. The block timestamp is the proof. Anyone can recompute the hash from the displayed analysis and confirm it matches the on-chain record. Retroactive fabrication is structurally impossible.

Beyond the proof mechanism, the system provides institutional-quality analysis using a 41-page trading playbook, cross-exchange data from three sources, and a deterministic decision tree that maps market conditions to one of nine scenarios before any LLM is involved.

---

## Architecture Overview

```
Layer 1: Data Collection
  Bybit WebSocket (pybit)         candles, trades, OI, funding, orderbook, liquidations, LSR
  Binance aggTrade WebSocket      spot + USDT-M futures trades (cross-exchange CVD)
  Mantle FusionX eth_getLogs      on-chain WMNT/USDT swap events (MNTUSDT spot CVD)
  Binance REST API                long/short ratio, funding, open interest
  CoinGecko /global               BTC dominance, stablecoin dominance, market sentiment

Layer 2: Stream Processing
  Redpanda (Kafka-compatible)     single topic, auto-negotiated API version
  QuestDB 8.1.0                   WAL mode, DEDUP UPSERT KEYS, ILP TCP ingestion
  7 tables: candles, trades, orderbook, open_interest, funding_rates, liquidations, long_short_ratio

Layer 3: Deterministic Intelligence (no LLM)
  data_aggregator.py              12 computed indicators from QuestDB time-series data
  mantle_integrations.py          mETH yield signal + FusionX DEX CVD
  Decision tree (Q1-Q4)           maps conditions to one of 9 scenarios with a fixed base direction

Layer 4: LLM Explanation
  Groq llama-3.3-70b              2-call pipeline per cycle
  Call 1: MacroRegimeAgent        BTC regime + session caps (reused for all 6 symbols)
  Call 2: FullAnalysisAgent       12-indicator scoring, bull/bear debate, risk plan, pre-trade note
  Code guard (run_pipeline.py)    enforces direction and calibrates confidence after LLM output

Layer 5: On-Chain Proof
  keccak256(analysis JSON)        deterministic hash, reproducible from displayed data
  AuditLog.sol logAnalysis()      emits hash, verdict, confidence, scenario as an immutable event
  AuditLog.sol updateSignal()     stores current regime in contract state, readable by other contracts
  StrategyGate.sol                composable oracle: any Mantle DeFi protocol can call checkPositionAllowedView()

Layer 6: Frontend
  FastAPI                         serves React build, REST endpoints, Server-Sent Events
  React + Vite + Tailwind         terminal layout, landing page, playbook page
  Lightweight Charts              candlestick + CVD charts (no TradingView watermark)
```

---

## The Intelligence Layer in Detail

### 12 Indicators (all computed from QuestDB, no LLM)

| # | Indicator | Source | Notes |
|---|---|---|---|
| 1 | VWMA 20D | candles table | 20 complete days, current partial excluded |
| 2 | Long/Short Ratio | long_short_ratio table | Bybit + Binance combined |
| 3 | Futures CVD | trades (market_type=futures) | 4H rolling window, Bybit + Binance |
| 4 | Spot CVD | trades (market_type=spot) | Binance spot; FusionX DEX for MNTUSDT |
| 5 | Bid/Ask Delta | orderbook table | Top-20 levels, aggregate imbalance |
| 6 | Funding Rate | funding_rates table | Bucketed: neutral / moderate+ / high+ / extreme+ / negative |
| 7 | Open Interest | open_interest table | Slope over last 6 five-minute records |
| 8 | Liquidation Events | liquidations table | 24H history, not a predictive heatmap |
| 9 | Order Book Depth | orderbook table | Top-5 vs levels 16-20, wall detection |
| 10 | MACD 1H | candles table | (12, 26, 9) via pandas ewm |
| 11 | RSI 14 | candles table | Wilder smoothing, 1H closes |
| 12 | VPVR | candles table | Approximate from 4H OHLCV, 100 price buckets |

### Decision Tree (9 Scenarios)

Q1: Is price above 20D VWMA?
Q2: Is Spot CVD rising (Q2A above VWMA) or falling (Q2B below)?
Q3: Is OI rising?
Q4: Is funding neutral-positive AND is MACD above zero?

These four binary questions produce a scenario number (1-9). The scenario determines the base direction (LONG, SHORT, NEUTRAL, NO_TRADE). The LLM receives this as a hard constraint: it explains and calibrates intensity but cannot change the direction.

| Scenario | Name | Base Direction |
|---|---|---|
| S1 | Healthy Uptrend | LONG |
| S2 | Uptrend Weakening | NEUTRAL |
| S3 | Confirmed Reversal from Top | SHORT |
| S4 | Healthy Downtrend | SHORT |
| S5 | Dead Cat Bounce | NO_TRADE |
| S6 | Bottom Forming | NEUTRAL |
| S7 | Confirmed Reversal from Bottom | LONG |
| S8 | Ranging Consolidation | NEUTRAL |
| S9 | Manipulation Detected | NO_TRADE |

### CVD Matrix States

The four-state CVD matrix is the single most informative combination:

- **BOTH_RISING**: Real money (spot) and leveraged money (futures) both buying. Full long size allowed.
- **BOTH_FALLING**: Both markets selling. Full short size or exit.
- **FUT_UP / SPOT_FLAT**: Speculative pump with no real money confirmation. Close longs. Do not enter.
- **FUT_DOWN / SPOT_FLAT**: Smart money accumulating quietly. Potential reversal incoming.

For MNTUSDT, the Spot CVD source is FusionX DEX on-chain swap flow rather than Binance. This is the only system that uses Mantle-native DEX data as a primary signal.

---

## Mantle Integration

| Contract / Feature | Address | Purpose |
|---|---|---|
| AuditLog.sol (TradingSignalOracle v2) | `0xdc9AF27a1C764871b71B478A2D6D3FA2cB442Cd4` | Pre-trade hash + live oracle state |
| StrategyGate.sol | `0x1150499F3D0E712a5a96FD4622656877E6700Ce3` | Composable position gate for DeFi protocols |

Both contracts are on Mantle Sepolia (chainId 5003). The deployment is reused on each run: `deploy.js` checks for an existing deployment with the same deployer address before creating new contracts.

### Dual Hash Architecture

Each analysis cycle produces two hashes:

- `data_hash`: keccak256 of the AI output (verdict, confidence, pre-trade note, playbook rules cited). Stored on-chain via `logAnalysis()`. This is what anyone can verify.
- `snapshot_hash`: keccak256 of the raw indicator values the system observed (VWMA, RSI, OI trend, funding bucket, CVD directions, BTC dominance). Stored in the on-chain payload. This proves what data the system SAW, not just what it said.
- `playbook_prompt_hash`: short fingerprint of the exact prompt version used. Any future prompt change produces a different fingerprint, making prompt drift permanently detectable.

### StrategyGate Composability

Any DeFi protocol on Mantle can call:

```solidity
StrategyGate.checkPositionAllowedView(
    "BTCUSDT",   // symbol
    10,          // requested leverage
    60           // minimum confidence required (0 to skip)
)
// returns (bool allowed, uint8 maxLev, string reason)
```

Regime-based leverage caps match the playbook exactly: BULL regime at 10x maximum, TRANSITION at 5x, BEAR at 3x absolute cap.

---

## Verdict Enforcement

The LLM receives a hard constraint in the prompt specifying the base direction and the instruction that it cannot change it. A post-LLM code guard in `run_pipeline.py` then enforces this regardless of what the model outputs:

```
_enforce_verdict_and_confidence(result)
```

This function:
- Checks the verdict against the allowed set for the base direction (LONG scenarios cannot produce SHORT)
- Applies confidence caps by confluence count: 5+ gives 40% max, 7+ gives 60%, 9+ gives 75%
- Logs any override with the exact contradiction for audit purposes
- Saves the original confidence as `confidence_original` for transparency

---

## Port Reference

All five processes use distinct ports. Run them in separate terminals.

| Port | Process | Notes |
|---|---|---|
| `8000` | FastAPI (uvicorn) | Main web server, serves the frontend |
| `8001` | `data_collector.py` health endpoint | `/health` JSON status |
| `9100` | `data_collector.py` Prometheus metrics | Standard Prometheus exporter port |
| `9000` | QuestDB HTTP console | Web UI for inspecting tables |
| `8812` | QuestDB PostgreSQL wire | Used by `data_aggregator.py` and `api/server.py` |
| `19092` | Redpanda Kafka | Used by both collectors and the pipeline |

`data_collector.py` previously used port 8000 for Prometheus metrics, which conflicted with FastAPI. It now uses port 9100.

---

## Quick Start

Five processes must run simultaneously. Open five terminals.

### Prerequisites

- Docker Desktop (for Redpanda + QuestDB)
- Python 3.11+
- Node.js 18+
- Mantle Sepolia wallet with MNT (faucet: https://faucet.sepolia.mantle.xyz)

### 1. Environment Setup

```bash
cp .env.example .env
```

Edit `.env`:
```
GROQ_API_KEY=gsk_...
PRIVATE_KEY=0x...
MANTLE_RPC=https://rpc.sepolia.mantle.xyz
```

### 2. Start Infrastructure (Terminal 1)

```bash
docker compose up -d
# Wait 15 seconds, then verify:
# QuestDB console: http://localhost:9000
# Redpanda:        localhost:19092
```

### 3. Start Bybit Collector (Terminal 2)

```bash
python data_collector.py
# Streams: candles, trades, OI, funding, orderbook, liquidations, LSR
# Prometheus metrics: http://localhost:9100/metrics
# Health:            http://localhost:8001/health
```

### 4. Start Binance Collector (Terminal 3)

```bash
python binance_collector.py
# Streams: spot + USDT-M futures aggTrades for BTC/ETH/SOL/BNB/XRP
# MNTUSDT excluded: Binance has no MNT pair; FusionX DEX handles it instead
# Backfills 4H of aggTrade history on startup so CVD is immediately available
```

Wait at least 5 minutes after starting both collectors before running analysis.
You can verify data is flowing in QuestDB: `SELECT count() FROM trades`

### 5. Deploy Contracts (first time only)

```bash
cd on_chain
npx hardhat run scripts/deploy.js --network mantleSepolia
# On subsequent runs: detects existing deployment automatically, skips redeployment
cd ..
```

### 6. Build Frontend (once)

```bash
cd frontend && npm run build && cd ..
# Output goes to frontend/dist/ and is served by FastAPI at http://localhost:8000
```

### 7. Start FastAPI Backend (Terminal 4)

```bash
uvicorn api.server:app --host 0.0.0.0 --port 8000
# Visit: http://localhost:8000
# If port conflict: data_collector.py may still be starting up on 8000 from an old version.
# Ensure you are running the latest data_collector.py which uses port 9100.
```

### 8. Run Analysis Pipeline (Terminal 5)

```bash
# Run once:
python run_pipeline.py

# Run every 30 minutes automatically (recommended):
python run_pipeline.py --loop

# Run every 60 minutes:
python run_pipeline.py --loop --interval 60

# Run specific symbols only:
python run_pipeline.py BTCUSDT ETHUSDT
```

The `--loop` flag keeps the pipeline running on an interval. Each run: fetches fresh data from QuestDB, calls Groq twice per symbol, enforces verdicts in code, submits the hash on-chain, and writes results to `analysis_results/`. The frontend SSE stream picks up changes automatically.

### 9. Run Tests

```bash
pytest tests/ -v
# 28 decision tree scenario tests, 14 hash determinism tests
```

---

## Repository Structure

```
Mantle-The_turing_test/
├── data_collector.py           Bybit WebSocket collector (candles, trades, OI, funding, orderbook, LSR)
├── binance_collector.py        Binance aggTrade WebSocket (spot + USDT-M futures)
├── data_aggregator.py          12 indicators + decision tree (deterministic, no LLM)
├── mantle_integrations.py      mETH yield baseline + FusionX DEX CVD
├── run_pipeline.py             Orchestrator: snapshot, LLM, enforce, hash, submit, write
├── backtest.py                 Scenario correlation analysis (90-day retrospective)
├── agents/
│   ├── prompts.py              Condensed playbook system prompts (Call 1 and Call 2)
│   └── pipeline.py             Groq 2-call pipeline with retry and schema validation
├── on_chain/
│   ├── contracts/
│   │   ├── AuditLog.sol        TradingSignalOracle v2: event audit + live oracle state
│   │   └── StrategyGate.sol    Composable position gate for DeFi protocols
│   ├── scripts/
│   │   └── deploy.js           Deploys both contracts; reuses existing if same deployer
│   ├── submit_audit.py         Hash computation, EIP-1559 tx submission, RPC fallbacks
│   └── deployment.json         Written by deploy.js; read by submit_audit.py and api/server.py
├── api/
│   └── server.py               FastAPI: REST endpoints + SSE, serves React build
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Landing.jsx     Landing page with expandable pipeline + Mantle sections
│       │   ├── Terminal.jsx    3-panel trading terminal
│       │   └── Playbook.jsx    Full playbook documentation page
│       └── components/
│           ├── AnalysisPanel.jsx   Decision tree, 12 indicators, debate, risk plan
│           ├── AuditPanel.jsx      On-chain audit, hash verify, StrategyGate widget
│           ├── ChartPanel.jsx      OHLCV candles + Spot CVD + Futures CVD (no watermark)
│           └── MetricsStrip.jsx    OI, funding rate, liquidations, L/S ratio
├── tests/
│   ├── test_decision_tree.py   All 9 scenarios + edge cases
│   └── test_hash.py            Hash determinism, sensitivity, payload fields
├── archive/                    Reference documents (playbook PDF, judging criteria)
├── docker-compose.yml          Redpanda + QuestDB
└── requirements.txt            Python dependencies
```

---

## Business Model

### Market Opportunity

- 30M+ active retail crypto traders globally
- Institutional-grade tools cost thousands per month
- No existing product provides cryptographic pre-trade proof

### Revenue Tiers

| Tier | Price | Features |
|---|---|---|
| Free | $0/month | BTCUSDT only, 30-minute delay, no on-chain audit |
| Individual | $99/month | All 6 symbols, live signals, on-chain audit per analysis |
| Pro | $299/month | REST API access, webhook alerts, custom symbols |
| Institutional | $999/month | White-label, custom playbook integration, SLA |

Year 1 target: 5,000 Individual + 500 Pro + 50 Institutional = $5.5M ARR

### Competitive Moat

1. **On-chain pre-trade proof**: No competitor has this. The audit trail is on Mantle and cannot be altered.
2. **Mantle-native data edge**: FusionX DEX CVD and mETH yield signals are only accessible to Mantle-integrated systems.
3. **Switching cost**: Signal history permanently on Mantle Sepolia. Moving to a competitor means losing the audit trail.
4. **Playbook-bound AI**: The 41-page rulebook creates consistent, legally defensible decisions vs opaque models.

### Tokenomics (COPILOT Token)

- **Utility**: Token-gate premium signals; staking earns protocol revenue share.
- **Distribution**: 30% community, 25% team (4-year vest), 20% treasury, 15% liquidity, 10% early backers.
- **Value accrual**: 50% of subscription revenue directed to token buyback and distribution to stakers.
- **Governance**: Token holders vote on playbook integrations and new signal types.

---

## BGA Track Alignment

This system directly addresses the BGA mission of expanding access to professional financial tools:

- Retail traders in Southeast Asia, India, and Africa have access to Bybit but not Bloomberg terminals
- The playbook methodology used here is the same framework institutional desks use, now accessible at $99/month
- Every decision is fully traceable: any user can verify what the AI saw, what it concluded, and when
- The on-chain oracle makes AI signals composable with DeFi, creating a new primitive for Mantle protocols

---

## Track

Mantle AI Awakening Hackathon Phase II: AI Trading and Strategy

Contracts on Mantle Sepolia (chainId 5003):
- AuditLog (TradingSignalOracle v2): `0xdc9AF27a1C764871b71B478A2D6D3FA2cB442Cd4`
- StrategyGate: `0x1150499F3D0E712a5a96FD4622656877E6700Ce3`
- Explorer: https://explorer.sepolia.mantle.xyz/address/0xdc9AF27a1C764871b71B478A2D6D3FA2cB442Cd4
