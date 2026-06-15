# Mantle AI Trading Copilot — The Turing Test

> **The first AI trading copilot where every recommendation is cryptographically proven to exist before price moves.**
> Explainable. Auditable. Built on Mantle.

[![Tests](https://img.shields.io/badge/tests-passing-00D4AA)](tests/) [![Network](https://img.shields.io/badge/network-Mantle%20Sepolia-00D4AA)](https://explorer.sepolia.mantle.xyz) [![Model](https://img.shields.io/badge/model-llama--3.3--70b-blue)](https://console.groq.com)

---

## The Problem We Solve

Retail crypto traders face a three-sided information asymmetry:

1. **Opacity** — AI trading signals are black boxes. You can't verify whether the AI actually predicted a move before it happened, or fabricated the call after.
2. **Complexity** — Institutional-grade multi-exchange CVD analysis, playbook-based confluence scoring, and session-aware risk management require tools that cost thousands per month.
3. **Trust** — When a trading signal service says "we called this move", there is no way to verify the claim without an immutable, timestamped record.

**This system solves all three:**
- Every recommendation is keccak256-hashed and logged on Mantle Sepolia **before** the user sees it — block timestamp is cryptographic proof
- Full playbook reasoning is shown: which of 12 indicators aligned, which scenario was identified, and exactly why
- Cross-exchange CVD (Bybit futures + Binance spot + Mantle FusionX DEX) gives retail traders institutional-grade signal quality

---

## Architecture

```
Bybit WS + REST backfill (130k candles/symbol)
Binance aggTrade WS + FAPI REST (OI, funding, LSR)
        ↓
   Redpanda (Kafka v23, topic: bybit-market-data)
        ↓
  QuestDB 8.1.0 (WAL + DEDUP UPSERT KEYS, 7 tables)
        ↓
  data_aggregator.py — 12 Indicators (deterministic Python)
  + Mantle FusionX DEX CVD (on-chain swap events)
  + mETH yield baseline (Mantle liquid staking APY)
        ↓
  Groq llama-3.3-70b — 2-call pipeline
  Call 1: Macro Regime (BTC, reused for altcoins)
  Call 2: Full Analysis (12-indicator score + bull/bear debate)
        ↓
  keccak256(analysis JSON) → TradingSignalOracle.sol on Mantle Sepolia
  logAnalysis()   — immutable event audit trail
  updateSignal()  — live readable oracle state (any contract can read)
        ↓
  FastAPI + React Frontend (real-time SSE updates)
```

---

## Mantle Ecosystem Integration

| Integration | Type | Signal |
|---|---|---|
| **FusionX DEX CVD** | On-chain (Mantle mainnet) | Smart money flow on Mantle's primary DEX |
| **mETH Yield Baseline** | On-chain (Ethereum mainnet) | Carry trade edge vs ETH perp funding |
| **MNTUSDT** | CeFi (Bybit/Binance) | Mantle's native token analysis |
| **TradingSignalOracle.sol** | On-chain (Mantle Sepolia) | Live AI signal readable by any Mantle DeFi protocol |
| **AuditLog events** | On-chain (Mantle Sepolia) | Immutable pre-trade proof |

### What Makes the On-Chain Component Meaningful

The `TradingSignalOracle.sol` contract does more than store hashes:
- Any DeFi protocol on Mantle can call `getLatestSignal("MNTUSDT")` to read the current AI regime
- `isBullish("BTCUSDT")` returns a boolean — a lending protocol could use this to adjust collateral ratios during BEAR regime
- The oracle stores regime, verdict, confidence score, and confluence count on-chain, readable trustlessly

---

## Technical Implementation

### Data Pipeline
- **6 symbols**: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, MNTUSDT
- **Bybit**: pybit WebSocket (live) + REST backfill (90 days, 130k candles/symbol)
- **Binance**: aggTrade WebSocket for spot + USDT-M futures, with 4H startup backfill for CVD
- **Redpanda**: Kafka-compatible, 4 partitions, WAL-mode consumer group
- **QuestDB**: 7 tables with WAL + DEDUP UPSERT KEYS for idempotent ingestion
- **ILP TCP**: Non-blocking send with float coercion for orderbook JSON safety

### AI Pipeline
- **Call 1** (BTC only): Macro Regime Agent — BULL/BEAR/TRANSITION + session leverage caps
- **Call 2** (all symbols): Full Analysis Agent — 12-indicator scoring + structured bull/bear debate
- **BTC macro reused** for altcoins (saves API calls, ensures regime consistency)
- **Schema validation**: Required fields enforced, JSONDecodeError caught specifically
- **Confidence calibration**: Explicit rule prevents high confidence at low confluence

### On-Chain
- **TradingSignalOracle.sol**: event-only audit + state oracle, keccak256 pre-trade hash
- **EIP-1559** gas pricing with `estimate_gas()` + 30% buffer
- **3 RPC fallbacks** for Mantle Sepolia (demo resilience)

### Testing
```bash
pytest tests/ -v
# Tests: 28 decision tree cases, 14 hash determinism cases
```

---

## Business Model

### The Market

- **TAM**: 30M+ active crypto retail traders globally
- **SAM**: 3M traders seeking professional-grade tools
- **SOM (Year 1)**: 10,000 paying users

### Revenue Model

| Tier | Price | Features |
|---|---|---|
| **Free** | $0/month | 1 symbol (BTCUSDT), delayed 30min, no on-chain audit |
| **Individual** | $99/month | All 6 symbols, live signals, on-chain audit per analysis |
| **Pro** | $299/month | API access, webhook alerts, custom symbol list |
| **Institutional** | $999/month | White-label, custom playbook integration, SLA |

**Year 1 Revenue Projection**: 5,000 Individual + 500 Pro + 50 Institutional = $5.5M ARR

### Tokenomics (COPILOT Token)

- **Utility**: Token-gate premium signals; staking earns a share of protocol revenue
- **Distribution**: 30% community, 25% team (4yr vest), 20% treasury, 15% liquidity, 10% early backers
- **Value accrual**: 50% of subscription revenue → token buyback + distribute to stakers
- **On-chain governance**: Token holders vote on which playbooks to integrate, which signals to add

### Go-To-Market Strategy

**Q3 2026 (Post-Hackathon)**
- Launch on BGA Discord and Bybit ecosystem partner list
- Free tier for first 500 users to build CVD history and validate signals
- Partner with 2-3 Mantle DeFi protocols to consume the on-chain oracle

**Q4 2026**
- Launch Individual tier with on-chain audit subscription
- Integrate COPILOT token for premium signal gating
- Publish 90-day backtesting report with scenario win rates

**Q1 2027**
- B2B API for prop trading firms and signal aggregators
- Expand to 20+ symbols including Mantle ecosystem tokens
- Partner with Lendle/Agni Finance for DeFi-native leverage signals

**Q2 2027**
- Mobile app (iOS/Android) with gasless AA for signal verification
- White-label for Bybit institutional clients

### Moat / Competitive Advantage

1. **On-chain pre-trade proof** — no competitor has this. Creates a tamper-proof track record on Mantle.
2. **Mantle DeFi data edge** — FusionX DEX CVD + mETH yield signals are exclusive to Mantle-integrated systems
3. **Switching cost** — Your AI signal history is permanently on Mantle Sepolia. Moving to a competitor loses your audit trail.
4. **Playbook-bound AI** — The 41-page rulebook creates consistent, defensible decisions vs opaque black boxes

---

## Quick Start

### Prerequisites
- Docker Desktop
- Python 3.11+
- Node.js 18+

### 1. Environment
```bash
cp .env.example .env
# Set: GROQ_API_KEY, PRIVATE_KEY
```

### 2. Infrastructure
```bash
docker compose up -d
```

### 3. Data Collection
```bash
python data_collector.py    # Bybit (background)
python binance_collector.py # Binance (background)
```

### 4. Deploy Contract
```bash
cd on_chain
npx hardhat run scripts/deploy.js --network mantleSepolia
```

### 5. Run Analysis
```bash
python run_pipeline.py      # all 6 symbols
# or: python run_pipeline.py BTCUSDT  (single symbol)
```

### 6. Frontend
```bash
# FastAPI + React (see frontend/README.md)
uvicorn api.server:app --reload --port 8000
# Frontend dev server:
cd frontend && npm run dev
```

### 7. Run Tests
```bash
pytest tests/ -v
```

---

## Track & Contact

**Mantle AI Awakening Hackathon Phase II — AI Trading & Strategy**

Contract: [`TradingSignalOracle.sol`](https://explorer.sepolia.mantle.xyz/address/CONTRACT_ADDRESS) on Mantle Sepolia (chainId 5003)
