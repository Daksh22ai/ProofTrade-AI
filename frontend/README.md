# Frontend: Mantle AI Trading Copilot

React + Vite single-page application with a terminal-style trading interface, a landing page, and a full playbook documentation page.

## Stack

- **React 18** with hooks
- **Vite 5** for build tooling
- **Tailwind CSS** for styling
- **Framer Motion** for animations
- **Lightweight Charts v4** for OHLCV and CVD charts (TradingView watermark disabled)
- **Axios** for API calls
- **Server-Sent Events** for real-time signal push from the backend

## Quick Start

```bash
# Install
npm install

# Development server (proxies /api to localhost:8000)
npm run dev

# Production build (output to dist/, served by FastAPI)
npm run build
```

## Pages

### Landing Page (`src/pages/Landing.jsx`)

The entry point for new visitors. Covers:
- Animated hash computation demo showing how the on-chain proof works
- Problem/solution comparison (traditional signals vs cryptographic pre-trade proof)
- Expandable pipeline steps with full technical detail for each layer
- Four unfair advantages section
- 12-indicator overview with scenario table
- Expandable Mantle integration cards (AuditLog, StrategyGate, FusionX, mETH)
- BGA alignment section
- Live contract address fetched from `/api/deployment` (not hardcoded)

### Trading Terminal (`src/pages/Terminal.jsx`)

Three-panel layout for the active trading interface:
- **Left sidebar (144px)**: 6 symbol cards with verdict, price, confluence bar, and timestamp. Clicking the logo area navigates back to the landing page.
- **Center panel**: Price chart, Spot CVD chart (Binance or FusionX DEX for MNTUSDT), Futures CVD chart (Bybit + Binance), market metrics strip.
- **Right panel (320px)**: AI Analysis tab and On-Chain Audit tab.

Also includes a symbol search modal (keyboard shortcut: Ctrl+K or Cmd+K).

### Playbook Page (`src/pages/Playbook.jsx`)

Full documentation of the trading methodology:
- Part 0: Macro regime filter (BULL/BEAR/TRANSITION with conditions and rules)
- Part 1: Session awareness (LONDON/NY/ASIAN/DEAD_HOURS leverage caps)
- Part 2: All 9 scenarios, each expandable with conditions, meaning, action, and risk
- Part 3: CVD matrix states with explanations
- Part 8: 7 risk management rules
- Part 9: Pre-trade commitment note (why, what proves me wrong, when to add)

## Components

```
src/
├── App.jsx                  # Route controller: landing | terminal | playbook
├── api.js                   # All API calls + SSE subscription
├── utils.js                 # Constants: SYMBOLS, VERDICT_META, CVD_META, formatters
├── index.css                # Tailwind + custom classes (glass, grid-bg, mantle-glow, pulse-ring)
├── pages/
│   ├── Landing.jsx          # Landing page
│   ├── Terminal.jsx         # 3-panel trading terminal
│   └── Playbook.jsx         # Playbook documentation
└── components/
    ├── Header.jsx            # Compact top bar: logo, live status dots, UTC clock, run button
    ├── GridBackground.jsx    # Animated CSS grid with radial glow
    ├── ChartPanel.jsx        # CandleChart + two CvdChart components (empty state overlays included)
    ├── MetricsStrip.jsx      # OI, funding rate (with bucket badge), liquidations, LSR bar
    ├── AnalysisPanel.jsx     # Decision tree, 12-indicator rows, pre-trade note, debate, audit rules
    ├── AuditPanel.jsx        # Hash display, verify button, StrategyGate widget, tx feed
    ├── VerdictHero.jsx       # Compact banner: symbol, price, verdict, regime, CVD state, confluence
    └── SignalSidebar.jsx     # Deprecated: replaced by the sidebar inside Terminal.jsx
```

## Design Tokens

| Token | Value | Usage |
|---|---|---|
| `--mantle` | `#00D4AA` | Primary accent, active states, positive signals |
| `--dark` | `#0A0E1A` | Page background |
| `--card` | `#0F1629` | Card background |
| `--border` | `#1a2744` | Subtle borders |
| `--bear` | `#FF6B6B` | Bearish, negative signals |
| `--caution` | `#FFD700` | NO_TRADE, warning states |
| Space Mono | monospace | Prices, hashes, raw indicator values |
| Inter | sans-serif | All other text |

## API Endpoints Used

| Endpoint | Used By |
|---|---|
| `GET /api/signals` | Terminal sidebar, polling every 30s |
| `GET /api/analysis/{symbol}` | AnalysisPanel, AuditPanel |
| `GET /api/chart/{symbol}?tf=1h` | ChartPanel candlestick chart |
| `GET /api/cvd/{symbol}?market_type=spot` | ChartPanel CVD charts |
| `GET /api/market/{symbol}` | MetricsStrip (OI, funding, LSR, liquidations) |
| `GET /api/verify/{symbol}` | AuditPanel hash verification button |
| `GET /api/deployment` | Terminal TopBar, AuditPanel, Landing page nav and CTA |
| `GET /api/position-check` | AuditPanel StrategyGate widget |
| `GET /api/stream` | Terminal SSE subscription for real-time updates |

## CVD Chart Empty State

When CVD data is not yet available (the collectors just started), the chart shows an animated overlay reading "Accumulating live data from [source]" with pulsing dots. The chart area is not left blank. Data populates automatically as trades flow in.

## Em Dash Policy

No em dashes appear in any source file. LLM-generated text from the API (which may contain em dashes in `ind.reading`, `bull_case`, `pre_trade_note_*`, etc.) is sanitized at render time by the `c()` function in `AnalysisPanel.jsx`.
