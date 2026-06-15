# Frontend — Mantle AI Trading Copilot

React + Vite single-page app with:
- TradingView Lightweight Charts (professional-grade OHLCV + CVD)
- Framer Motion animations (staggered card entry, tab transitions)
- Glassmorphism dark theme with Mantle teal (#00D4AA) branding
- Server-Sent Events for real-time signal updates
- Fully responsive (mobile + desktop)

## Quick Start

```bash
# Install dependencies
npm install

# Development (proxies /api to localhost:8000)
npm run dev

# Production build
npm run build
# Outputs to dist/ — served by FastAPI
```

## Architecture

```
frontend/src/
├── App.jsx                  # Root: state, SSE subscription, tab routing
├── api.js                   # All API calls + SSE subscription
├── utils.js                 # Constants, formatters, verdict metadata
├── index.css                # Tailwind + custom animations
└── components/
    ├── Header.jsx            # Sticky header: logo, live clock, run button
    ├── GridBackground.jsx    # Animated grid + glow effects
    ├── SignalBoard.jsx       # Hero: 6 animated symbol cards with confluence bars
    ├── ChartPanel.jsx        # TradingView candles + CVD + market metrics
    ├── AnalysisPanel.jsx     # Decision tree, 12 indicators, debate, risk plan
    └── AuditPanel.jsx        # On-chain tx feed, hash verify, payload display
```

## Design System

| Token | Value | Usage |
|---|---|---|
| `--mantle` | `#00D4AA` | Primary, active states, positive signals |
| `--dark` | `#0A0E1A` | Page background |
| `--card` | `#0F1629` | Card background |
| `--border` | `#1a2744` | Subtle borders |
| `--bear` | `#FF6B6B` | Negative/bearish signals |
| `--caution` | `#FFD700` | Warning/NO_TRADE |
| Font mono | Space Mono | Prices, hashes, indicators |
| Font sans | Inter | All other text |
