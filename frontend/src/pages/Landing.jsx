import { useState, useEffect, useRef } from 'react'
import { motion, useInView, AnimatePresence } from 'framer-motion'
import { api } from '../api.js'

// Demo values for the animated hero card only (illustrative, not linked anywhere)
const HASH_DEMO  = '0x4f2e9a1b8c3d7e6f5a4b2c1d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0'
const BLOCK_DEMO = '4,821,903'

function TypedHash({ value, speed = 18 }) {
  const [shown, setShown] = useState('')
  useEffect(() => {
    setShown('')
    let i = 0
    const t = setInterval(() => {
      setShown(value.slice(0, i + 1))
      i++
      if (i >= value.length) clearInterval(t)
    }, speed)
    return () => clearInterval(t)
  }, [value, speed])
  return <span className="font-mono text-mantle break-all">{shown}<span className="animate-pulse">|</span></span>
}

function FadeIn({ children, delay = 0, className = '' }) {
  const ref = useRef(null)
  const inView = useInView(ref, { once: true, margin: '-60px' })
  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 28 }}
      animate={inView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.55, delay, ease: 'easeOut' }}
      className={className}
    >
      {children}
    </motion.div>
  )
}

// Expandable pipeline step
const PIPELINE = [
  {
    icon: '📡',
    title: 'Data Collection',
    desc: 'Bybit WS + Binance aggTrades + Mantle FusionX DEX',
    detail: '130k candles/symbol, 4H backfill, real-time streaming',
    expanded: [
      'Bybit pybit WebSocket streams live OHLCV, trades, orderbook, and liquidations at 10ms granularity.',
      'Binance aggTrade WebSocket feeds both spot and USDT-M futures for cross-exchange CVD computation.',
      'On startup, 4 hours of Binance historical aggTrades are backfilled so Spot CVD is immediately populated.',
      'FusionX DEX swap events are fetched via raw eth_getLogs on Mantle mainnet for MNTUSDT-specific intelligence.',
      'All raw feeds push to Redpanda (Kafka-compatible) on topic bybit-market-data with exchange and market_type tags.',
    ],
  },
  {
    icon: '⚡',
    title: 'Stream Processing',
    desc: 'Redpanda (Kafka) to QuestDB WAL+DEDUP',
    detail: 'Sub-100ms latency, idempotent ingestion, 7 tables',
    expanded: [
      'Redpanda v23 consumes the Kafka topic with 4 parallel consumer threads, one per data type.',
      'QuestDB 8.1.0 receives data via ILP TCP protocol (line protocol) for zero-parse ingestion.',
      'WAL mode and DEDUP UPSERT KEYS on every table ensure exactly-once delivery even on reconnects.',
      'Seven tables: candles, trades, orderbook, open_interest, funding_rates, liquidations, long_short_ratio.',
      'All ILP writes use float coercion on bid/ask JSON to prevent inner-quote corruption from closing TCP.',
    ],
  },
  {
    icon: '🧮',
    title: 'Deterministic Intelligence',
    desc: '12 indicators + 9-scenario decision tree',
    detail: 'No LLM at this layer. Pure Python. Fully auditable.',
    expanded: [
      'VWMA 20D computed from 20 complete daily candles (partial current day excluded).',
      'Spot CVD and Futures CVD are rolling 4-hour cumulative volume deltas, stateless on restart.',
      'For MNTUSDT, FusionX DEX swap CVD replaces Binance spot CVD as the primary signal.',
      'Q1-Q4 decision tree maps price vs VWMA, Spot CVD direction, OI trend, and MACD/funding to one of 9 scenarios.',
      'The scenario determines the base direction. The LLM cannot override this. It can only explain and calibrate confidence.',
      'All 12 indicators are computed before any LLM call. Results are fully reproducible from QuestDB data.',
    ],
  },
  {
    icon: '🤖',
    title: 'LLM Explanation Layer',
    desc: 'Groq llama-3.3-70b. Explains, not decides.',
    detail: '2-call pipeline: macro regime then full analysis',
    expanded: [
      'Call 1 (Macro Regime Agent): Runs once for BTC and is reused for all 5 altcoins. Determines BULL/BEAR/TRANSITION regime and session-based leverage caps.',
      'Call 2 (Full Analysis Agent): Scores all 12 indicators, runs a structured Bull vs Bear debate, then synthesizes the pre-trade commitment note.',
      'The base direction from the decision tree is injected as a hard constraint. The LLM is explicitly told it cannot change it.',
      'A post-LLM code guard in run_pipeline.py catches any direction violation and overrides it back, logging the incident.',
      'Confidence calibration is also enforced in code: low confluence always produces low confidence, regardless of what the LLM outputs.',
      'All 5 altcoins run in parallel after BTC completes, reducing total pipeline time from 90 seconds to approximately 20 seconds.',
    ],
  },
  {
    icon: '⛓',
    title: 'On-Chain Proof',
    desc: 'keccak256(analysis) logged on Mantle Sepolia AuditLog',
    detail: 'Logged BEFORE you see the result. Block timestamp is proof.',
    expanded: [
      'The full analysis JSON is serialized with sorted keys, then keccak256-hashed before the result is written to disk.',
      'logAnalysis() on AuditLog.sol emits an event with the hash, verdict, confluence count, and scenario name.',
      'updateSignal() on TradingSignalOracle stores the latest regime and verdict in contract state, readable by any Mantle DeFi protocol.',
      'StrategyGate.sol can then be called by any protocol to gate leveraged positions: it checks regime, confidence, and applies playbook leverage caps.',
      'A second snapshot_hash commits to the raw indicator values the system observed, not just the AI output. Both are in the audit payload.',
      'The playbook_prompt_hash fingerprints the exact prompt version used, so any future prompt change is permanently detectable.',
    ],
  },
]

// Expandable Mantle integration card
const MANTLE_CARDS = [
  {
    icon: '⛓',
    name: 'AuditLog.sol',
    type: 'ESSENTIAL',
    color: '#00D4AA',
    short: 'Pre-trade hash registry. Immutable proof of prediction.',
    expanded: [
      'Deployed on Mantle Sepolia (chainId 5003). Every analysis run calls logAnalysis() before the result is shown to the user.',
      'The emitted event contains: keccak256 hash, symbol, verdict, confidence score, confluence count, scenario name, and block.timestamp.',
      'Because the event is emitted in a confirmed block before the user sees the recommendation, the block timestamp is cryptographic proof that the signal existed at that time.',
      'Anyone can recompute the hash independently: keccak256(json.dumps(payload, sort_keys=True)) and compare to the on-chain event.',
      'The contract also stores the current regime signal in state via updateSignal(), making it readable by any contract on Mantle.',
    ],
  },
  {
    icon: '🔮',
    name: 'StrategyGate.sol',
    type: 'ESSENTIAL',
    color: '#A78BFA',
    short: 'Composable oracle. Any DeFi protocol can gate positions by regime.',
    expanded: [
      'StrategyGate reads TradingSignalOracle and implements the playbook leverage caps in Solidity.',
      'checkPositionAllowedView(symbol, requestedLeverage, minConfidence) returns (bool, maxLev, reason).',
      'BEAR regime: 3x absolute cap. TRANSITION: 5x cap. BULL with 9+ confluence: 10x allowed.',
      'Any lending protocol, perpetual DEX, or vault strategy on Mantle can integrate this as a position gate.',
      'Signals older than 4 hours are treated as stale and all positions are rejected until a fresh analysis runs.',
      'This makes the AI signal a trustlessly composable DeFi primitive, not just a dashboard indicator.',
    ],
  },
  {
    icon: '💧',
    name: 'FusionX DEX CVD',
    type: 'USEFUL',
    color: '#60A5FA',
    short: 'On-chain swap flow as primary Spot CVD for MNTUSDT.',
    expanded: [
      'For BTC and ETH, Spot CVD comes from Binance aggTrades (CEX order flow, highly liquid).',
      'For MNTUSDT, Mantle native token, CEX flow is less representative than on-chain activity.',
      'FusionX WMNT/USDT pool Swap events are fetched via eth_getLogs on Mantle mainnet and decoded manually.',
      'When amount0 is negative (WMNT leaving the pool), a taker bought WMNT. Positive means selling. This is the CVD signal.',
      'If FusionX has fewer than 3 swaps in the window, the system falls back to Binance spot CVD gracefully.',
      'This means the Spot CVD for MNTUSDT is derived from actual on-chain conviction, not CEX speculation.',
    ],
  },
  {
    icon: '🌊',
    name: 'mETH Yield Signal',
    type: 'USEFUL',
    color: '#F59E0B',
    short: 'Carry trade edge: ETH funding vs Mantle liquid staking APY.',
    expanded: [
      'Mantle liquid staking converts ETH to mETH and earns staking yield. The current APY is fetched on-chain via mETHToETH().',
      'The ETH perpetual funding rate is collected every minute from Binance USDT-M and Bybit.',
      'When ETH perp funding (annualized) exceeds mETH APY, longs earn more from holding perps than staking.',
      'When mETH APY exceeds funding, there is structural incentive to unwind perp longs and stake instead. This is a BEARISH carry signal.',
      'The carry edge in basis points is computed and included as the 13th signal in the analysis context.',
      'If the on-chain call fails, the system falls back to Mantle API, then DeFiLlama, then a published baseline of 4.5%.',
    ],
  },
]

function ExpandableStep({ step, index }) {
  const [open, setOpen] = useState(false)
  return (
    <FadeIn delay={index * 0.08}>
      <div className="flex gap-4 items-start">
        <div className="relative z-10 flex-shrink-0">
          <div
            className="w-12 h-12 rounded-xl bg-dark border flex items-center justify-center text-xl transition-all"
            style={{ borderColor: open ? '#00D4AA50' : '#1a2744' }}
          >
            {step.icon}
          </div>
        </div>
        <div className="flex-1">
          <button
            onClick={() => setOpen(v => !v)}
            className="w-full text-left glass rounded-xl px-5 py-4 hover:border-mantle/20 transition-all"
          >
            <div className="flex items-center justify-between">
              <div>
                <div className="flex items-baseline gap-3 mb-1">
                  <span className="text-[10px] text-mantle font-bold uppercase tracking-wider">Step {index + 1}</span>
                  <span className="text-sm font-bold text-text">{step.title}</span>
                </div>
                <div className="text-xs text-muted">{step.desc}</div>
                <div className="text-[10px] text-muted/50 font-mono mt-0.5">{step.detail}</div>
              </div>
              <div
                className="w-6 h-6 rounded flex items-center justify-center flex-shrink-0 ml-4 text-xs transition-all"
                style={{ color: open ? '#00D4AA' : '#6b7280' }}
              >
                {open ? '-' : '+'}
              </div>
            </div>
          </button>
          <AnimatePresence>
            {open && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.22 }}
                className="overflow-hidden"
              >
                <div className="ml-0 mt-2 rounded-xl border border-mantle/15 bg-mantle/3 px-5 py-4 space-y-2">
                  {step.expanded.map((point, pi) => (
                    <div key={pi} className="flex items-start gap-2.5 text-[11px] text-muted leading-relaxed">
                      <span className="text-mantle mt-0.5 flex-shrink-0">+</span>
                      <span>{point}</span>
                    </div>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </FadeIn>
  )
}

function ExpandableMantleCard({ card, delay }) {
  const [open, setOpen] = useState(false)
  return (
    <FadeIn delay={delay}>
      <div
        className="rounded-2xl border transition-all"
        style={{ borderColor: open ? card.color + '40' : '#1a2744', background: open ? card.color + '05' : 'rgba(15,22,41,0.8)' }}
      >
        <button
          onClick={() => setOpen(v => !v)}
          className="w-full text-left p-5"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xl">{card.icon}</span>
                <span className="text-sm font-bold text-text">{card.name}</span>
                <span
                  className="text-[9px] px-2 py-0.5 rounded-full font-bold ml-auto"
                  style={{
                    backgroundColor: card.type === 'ESSENTIAL' ? '#00D4AA15' : '#60A5FA15',
                    color: card.type === 'ESSENTIAL' ? '#00D4AA' : '#60A5FA',
                  }}
                >
                  {card.type}
                </span>
              </div>
              <p className="text-[11px] text-muted leading-relaxed">{card.short}</p>
            </div>
            <div
              className="w-5 h-5 rounded flex items-center justify-center flex-shrink-0 text-xs mt-1"
              style={{ color: open ? card.color : '#6b7280' }}
            >
              {open ? '-' : '+'}
            </div>
          </div>
        </button>
        <AnimatePresence>
          {open && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.22 }}
              className="overflow-hidden"
            >
              <div
                className="px-5 pb-5 pt-2 border-t space-y-2"
                style={{ borderColor: card.color + '20' }}
              >
                {card.expanded.map((point, pi) => (
                  <div key={pi} className="flex items-start gap-2.5 text-[11px] text-muted leading-relaxed">
                    <span className="flex-shrink-0 mt-0.5 font-bold" style={{ color: card.color }}>+</span>
                    <span>{point}</span>
                  </div>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </FadeIn>
  )
}

const MOAT = [
  {
    title: 'Pre-Trade Cryptographic Proof',
    color: '#00D4AA',
    icon: '🔐',
    desc: 'Every recommendation is keccak256-hashed and logged on Mantle Sepolia before you see it. Block timestamp proves the AI predicted before price moved, not after.',
    vs: 'Every other AI trading signal can be fabricated retrospectively. There is no proof.',
  },
  {
    title: 'Deterministic Decision Tree',
    color: '#60A5FA',
    icon: '🌳',
    desc: 'Scenarios 1-9 are identified by a Q1-Q4 decision tree with no LLM and no hallucination. The LLM explains the verdict, it does not make it.',
    vs: 'Black-box models give you a number with no reasoning you can challenge.',
  },
  {
    title: 'Composable On-Chain Oracle',
    color: '#A78BFA',
    icon: '🔗',
    desc: 'StrategyGate.sol lets any Mantle DeFi protocol read getLatestSignal and gate positions by regime. The AI signal becomes a trustless DeFi primitive.',
    vs: 'No other AI trading system has an on-chain composable oracle that DeFi protocols can integrate.',
  },
  {
    title: 'Mantle-Native Intelligence',
    color: '#F59E0B',
    icon: '🌐',
    desc: 'For MNTUSDT: FusionX DEX CVD replaces Binance spot CVD as the real money signal. On-chain swap flow is more truthful than CEX orderflow for Mantle-native assets.',
    vs: 'Generic tools apply the same BTC analysis to every asset. We use Mantle DEX data no CEX-only system can access.',
  },
]

const INDICATORS = [
  'VWMA 20D', 'Long/Short Ratio', 'Futures CVD', 'Spot CVD',
  'Bid/Ask Delta', 'Funding Rate', 'Open Interest', 'Liq Events',
  'Order Book Depth', 'MACD 1H', 'RSI 14', 'VPVR',
]

export default function Landing({ onEnter, onPlaybook }) {
  const [hashVisible,  setHashVisible]  = useState(false)
  const [confirmed,    setConfirmed]    = useState(false)
  const [deployment,   setDeployment]   = useState(null)

  useEffect(() => {
    const t1 = setTimeout(() => setHashVisible(true), 1200)
    const t2 = setTimeout(() => setConfirmed(true), 3800)
    // Fetch live deployment so every link on this page points to the actual contract
    api.deployment().then(setDeployment).catch(() => {})
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [])

  // Live contract URLs — fall back to Mantle explorer root if not deployed yet
  const contractExplorerUrl  = deployment?.explorer_url  || 'https://explorer.sepolia.mantle.xyz'
  const contractAddress      = deployment?.address       || null
  const deployedAt           = deployment?.deployed_at_utc?.slice(0, 10) || null

  return (
    <div className="min-h-screen bg-dark text-text overflow-x-hidden">

      {/* Background */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute inset-0 grid-bg" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[900px] h-[500px] rounded-full opacity-[0.06]"
             style={{ background: 'radial-gradient(ellipse, #00D4AA 0%, transparent 70%)' }} />
        <div className="absolute bottom-0 right-0 w-96 h-96 opacity-[0.04]"
             style={{ background: 'radial-gradient(ellipse at bottom right, #7C3AED, transparent)' }} />
      </div>

      {/* NAV */}
      <nav className="relative z-20 flex items-center justify-between px-8 py-4 border-b border-border/50">
        <div className="flex items-center gap-3">
          <img src="/logo.png" alt="Mantle AI Copilot" className="w-8 h-8 rounded-lg object-contain" />
          <div>
            <div className="font-bold text-sm text-text leading-tight">Mantle AI Copilot</div>
            <div className="text-[9px] text-muted leading-tight">The Turing Test</div>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <a href="#how-it-works" className="hidden sm:block text-[11px] text-muted hover:text-text transition-colors">
            How it works
          </a>
          <a href="#mantle" className="hidden sm:block text-[11px] text-muted hover:text-text transition-colors">
            Mantle
          </a>
          <button
            onClick={onPlaybook}
            className="hidden sm:block text-[11px] text-muted hover:text-mantle transition-colors"
          >
            Playbook
          </button>
          <a href={contractExplorerUrl} target="_blank" rel="noreferrer"
             className="hidden md:flex items-center gap-1.5 text-[10px] text-muted hover:text-mantle transition-colors">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            {contractAddress ? `${contractAddress.slice(0,8)}... on Mantle` : 'Live on Mantle Sepolia'}
          </a>
          <motion.button
            onClick={onEnter}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            className="px-5 py-2 rounded-lg bg-mantle text-dark text-xs font-bold hover:bg-mantle-dim transition-all"
          >
            Open Terminal
          </motion.button>
        </div>
      </nav>

      {/* HERO */}
      <section className="relative z-10 pt-24 pb-20 px-8 max-w-6xl mx-auto">
        <div className="grid lg:grid-cols-2 gap-16 items-center">
          <div>
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6 }}
            >
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-mantle/30 bg-mantle/5 text-[10px] text-mantle font-medium mb-8">
                <span className="w-1.5 h-1.5 rounded-full bg-mantle animate-pulse" />
                Mantle AI Awakening Hackathon - Phase II
              </div>
              <h1 className="text-5xl font-bold leading-[1.15] mb-6 tracking-tight">
                AI Trading Signals
                <br />
                <span className="text-mantle">Cryptographically</span>
                <br />
                <span className="text-mantle">Proven</span> Before Price Moves.
              </h1>
              <p className="text-muted text-base leading-relaxed mb-10 max-w-lg">
                Every recommendation is keccak256-hashed and locked on{' '}
                <span className="text-mantle font-medium">Mantle Sepolia</span> before you see it.
                Block timestamp is immutable proof, not a claim.
              </p>
              <div className="flex flex-wrap gap-3">
                <motion.button
                  onClick={onEnter}
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.98 }}
                  className="px-7 py-3.5 rounded-xl bg-mantle text-dark font-bold text-sm hover:bg-mantle-dim transition-all"
                  style={{ boxShadow: '0 0 30px rgba(0,212,170,0.25)' }}
                >
                  Open Trading Terminal
                </motion.button>
                <motion.button
                  onClick={onPlaybook}
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.98 }}
                  className="px-7 py-3.5 rounded-xl border border-mantle/30 text-mantle text-sm font-medium hover:bg-mantle/5 transition-all"
                >
                  See What Powers the AI
                </motion.button>
                <a href="#how-it-works"
                   className="px-7 py-3.5 rounded-xl glass border border-border text-sm font-medium hover:border-mantle/30 transition-all">
                  How it works
                </a>
              </div>
            </motion.div>
          </div>

          {/* Proof card */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.7, delay: 0.3 }}
          >
            <div className="glass rounded-2xl p-6 border border-border"
                 style={{ boxShadow: '0 0 50px rgba(0,212,170,0.07)' }}>
              <div className="flex items-center justify-between mb-5">
                <div>
                  <div className="text-[10px] text-muted mb-1">Live Analysis - BTCUSDT</div>
                  <div className="text-2xl font-bold font-mono text-text">$104,832</div>
                </div>
                <div className="px-4 py-2 rounded-xl font-bold text-sm"
                     style={{ backgroundColor: 'rgba(0,212,170,0.1)', color: '#00D4AA', border: '1px solid rgba(0,212,170,0.3)' }}>
                  STRONG LONG
                </div>
              </div>
              <div className="mb-5">
                <div className="flex justify-between text-[10px] text-muted mb-1.5">
                  <span>Confluence Score</span>
                  <span className="text-mantle font-bold">9/12</span>
                </div>
                <div className="h-2 bg-border rounded-full overflow-hidden">
                  <motion.div
                    className="h-full rounded-full bg-mantle"
                    initial={{ width: 0 }}
                    animate={{ width: '75%' }}
                    transition={{ duration: 1.2, delay: 0.8 }}
                  />
                </div>
              </div>
              <div className="bg-dark rounded-xl p-4 border border-border mb-4">
                <div className="flex items-center justify-between text-[10px] text-muted mb-2">
                  <span>Computing keccak256 hash...</span>
                  {confirmed && <span className="text-green-400">Complete</span>}
                </div>
                <div className="text-[10px] break-all" style={{ minHeight: '32px' }}>
                  {hashVisible ? <TypedHash value={HASH_DEMO} /> : <span className="text-muted">Waiting...</span>}
                </div>
              </div>
              <AnimatePresence>
                {confirmed && (
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="rounded-xl p-3.5 flex items-start gap-3"
                    style={{ backgroundColor: 'rgba(0,212,170,0.06)', border: '1px solid rgba(0,212,170,0.2)' }}
                  >
                    <span className="text-lg flex-shrink-0">⛓</span>
                    <div className="min-w-0">
                      <div className="text-[10px] font-bold text-mantle mb-0.5">Confirmed on Mantle Sepolia</div>
                      <div className="text-[9px] text-muted">Block #{BLOCK_DEMO} - Logged BEFORE recommendation shown</div>
                      <a href={contractExplorerUrl} target="_blank" rel="noreferrer"
                         className="text-[9px] font-mono text-mantle/60 hover:text-mantle truncate block mt-0.5">
                        {contractAddress ? `${contractAddress.slice(0, 32)}...` : 'View on Mantle Explorer'}
                      </a>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        </div>
      </section>

      {/* PROBLEM */}
      <section className="relative z-10 py-20 px-8 border-y border-border/50 bg-dark/50">
        <div className="max-w-6xl mx-auto">
          <FadeIn>
            <div className="text-center mb-14">
              <div className="text-[10px] text-muted uppercase tracking-widest mb-3">The Problem We Solve</div>
              <h2 className="text-3xl font-bold">AI Trading Signals Are Unverifiable</h2>
              <p className="text-muted mt-4 max-w-2xl mx-auto text-sm leading-relaxed">
                Any signal service can claim "we called this move." There is no way to prove a recommendation
                existed before price moved. Until now.
              </p>
            </div>
          </FadeIn>
          <div className="grid lg:grid-cols-2 gap-8">
            <FadeIn delay={0.1}>
              <div className="rounded-2xl p-7 border border-bear/20 bg-bear/4 h-full">
                <div className="text-bear font-bold text-sm mb-5 flex items-center gap-2">
                  <span className="text-bear">X</span> Traditional AI Signal
                </div>
                {[
                  'AI generates signal privately',
                  'Price moves in predicted direction',
                  'Signal published retroactively: "We called it!"',
                  'No timestamp. No proof. No audit.',
                  'Survivorship bias hides all failures.',
                ].map((s, i) => (
                  <div key={i} className="flex items-start gap-3 mb-3.5 text-sm text-muted">
                    <span className="text-bear/60 mt-0.5 flex-shrink-0">-</span>
                    <span>{s}</span>
                  </div>
                ))}
                <div className="mt-6 p-3.5 rounded-lg bg-dark border border-border text-[10px] text-muted italic">
                  "Trust us, our AI predicted the move." No verifiable evidence.
                </div>
              </div>
            </FadeIn>
            <FadeIn delay={0.2}>
              <div className="rounded-2xl p-7 border border-mantle/20 bg-mantle/4 h-full">
                <div className="text-mantle font-bold text-sm mb-5 flex items-center gap-2">
                  <span>+</span> Mantle AI Copilot
                </div>
                {[
                  '12 indicators + decision tree computed deterministically',
                  'LLM generates full analysis with pre-trade note',
                  'keccak256(analysis) logged on Mantle Sepolia',
                  'Block timestamp is cryptographic pre-trade proof',
                  'You see the recommendation AFTER the on-chain hash',
                ].map((s, i) => (
                  <div key={i} className="flex items-start gap-3 mb-3.5 text-sm">
                    <span className="text-mantle mt-0.5 flex-shrink-0">+</span>
                    <span className={i === 2 || i === 3 ? 'text-text font-medium' : 'text-muted'}>{s}</span>
                  </div>
                ))}
                <div className="mt-6 p-3.5 rounded-lg bg-dark border border-mantle/20 text-[10px] text-mantle">
                  Anyone can recompute the hash from the displayed analysis and verify it matches the on-chain record.
                </div>
              </div>
            </FadeIn>
          </div>
        </div>
      </section>

      {/* ARCHITECTURE - Expandable */}
      <section id="how-it-works" className="relative z-10 py-20 px-8">
        <div className="max-w-5xl mx-auto">
          <FadeIn>
            <div className="text-center mb-14">
              <div className="text-[10px] text-muted uppercase tracking-widest mb-3">Under The Hood</div>
              <h2 className="text-3xl font-bold">Institutional-Grade Pipeline</h2>
              <p className="text-muted mt-4 text-sm">
                Five layers, each auditable. Click any step for full technical detail.
              </p>
            </div>
          </FadeIn>
          <div className="relative">
            <div className="absolute left-6 top-12 bottom-12 w-px bg-gradient-to-b from-mantle/60 via-mantle/20 to-transparent hidden md:block" />
            <div className="space-y-4">
              {PIPELINE.map((step, i) => (
                <ExpandableStep key={i} step={step} index={i} />
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* MOAT */}
      <section className="relative z-10 py-20 px-8 border-t border-border/50">
        <div className="max-w-6xl mx-auto">
          <FadeIn>
            <div className="text-center mb-14">
              <div className="text-[10px] text-muted uppercase tracking-widest mb-3">Why We Win</div>
              <h2 className="text-3xl font-bold">Four Unfair Advantages</h2>
            </div>
          </FadeIn>
          <div className="grid sm:grid-cols-2 gap-5">
            {MOAT.map((m, i) => (
              <FadeIn key={i} delay={i * 0.1}>
                <div className="rounded-2xl p-7 border glass h-full" style={{ borderColor: m.color + '30' }}>
                  <div className="flex items-center gap-3 mb-5">
                    <span className="text-2xl">{m.icon}</span>
                    <span className="font-bold text-sm text-text">{m.title}</span>
                  </div>
                  <p className="text-sm text-muted leading-relaxed mb-5">{m.desc}</p>
                  <div className="rounded-lg p-3.5 bg-dark border border-border">
                    <div className="text-[9px] text-muted uppercase tracking-wider mb-1.5">vs. Competition</div>
                    <div className="text-[11px] text-muted/70 italic">{m.vs}</div>
                  </div>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* 12 INDICATORS */}
      <section className="relative z-10 py-20 px-8 border-t border-border/50 bg-dark/50">
        <div className="max-w-6xl mx-auto">
          <FadeIn>
            <div className="grid lg:grid-cols-2 gap-16 items-center">
              <div>
                <div className="text-[10px] text-muted uppercase tracking-widest mb-4">Intelligence Layer</div>
                <h2 className="text-3xl font-bold mb-6 leading-tight">
                  12 Confluence Indicators.<br />
                  <span className="text-mantle">One Deterministic Verdict.</span>
                </h2>
                <p className="text-muted text-sm leading-relaxed mb-7">
                  Each indicator is computed from QuestDB time-series data. The Q1-Q4
                  decision tree maps to one of 9 scenarios with no LLM involvement.
                  The AI explains the result. It does not produce it.
                </p>
                <div className="space-y-2.5 text-sm text-muted">
                  {[
                    ['S1', 'Healthy Uptrend', 'LONG'],
                    ['S3', 'Confirmed Reversal from Top', 'SHORT'],
                    ['S5', 'Dead Cat Bounce', 'NO_TRADE'],
                    ['S8', 'Ranging Consolidation', 'NEUTRAL'],
                  ].map(([s, name, verdict]) => (
                    <div key={s} className="flex items-center gap-3">
                      <span className="text-mantle font-mono text-xs w-3">{s}</span>
                      <span className="text-muted">{name}</span>
                      <span className="ml-auto text-[10px] font-bold" style={{
                        color: verdict === 'LONG' ? '#00D4AA' : verdict === 'SHORT' ? '#FF6B6B' : verdict === 'NO_TRADE' ? '#FFD700' : '#6b7280'
                      }}>{verdict}</span>
                    </div>
                  ))}
                </div>
                <motion.button
                  onClick={onPlaybook}
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.98 }}
                  className="mt-6 px-5 py-2.5 rounded-xl border border-mantle/25 text-mantle text-xs font-medium hover:bg-mantle/5 transition-all flex items-center gap-2"
                >
                  <span>Read the full playbook</span>
                  <span className="text-mantle/60">9 scenarios, CVD matrix, risk rules, pre-trade notes</span>
                </motion.button>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {INDICATORS.map((ind, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, scale: 0.8 }}
                    whileInView={{ opacity: 1, scale: 1 }}
                    viewport={{ once: true }}
                    transition={{ delay: i * 0.04, duration: 0.3 }}
                    className="rounded-lg px-3 py-3 glass border border-border text-[10px] text-muted text-center hover:border-mantle/30 hover:text-text transition-all"
                  >
                    {ind}
                  </motion.div>
                ))}
              </div>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* MANTLE - Expandable */}
      <section id="mantle" className="relative z-10 py-20 px-8 border-t border-border/50">
        <div className="max-w-5xl mx-auto">
          <FadeIn>
            <div className="text-center mb-14">
              <div className="text-[10px] text-muted uppercase tracking-widest mb-3">Mantle Ecosystem</div>
              <h2 className="text-3xl font-bold">Built for Mantle. Not Ported.</h2>
              <p className="text-muted mt-4 text-sm max-w-2xl mx-auto">
                Every Mantle integration serves a functional purpose in the intelligence layer.
                Click any card for the full technical specification.
              </p>
            </div>
          </FadeIn>
          <div className="grid sm:grid-cols-2 gap-4">
            {MANTLE_CARDS.map((card, i) => (
              <ExpandableMantleCard key={i} card={card} delay={i * 0.08} />
            ))}
          </div>
        </div>
      </section>

      {/* BGA */}
      <section className="relative z-10 py-20 px-8 border-t border-border/50 bg-dark/50">
        <div className="max-w-4xl mx-auto text-center">
          <FadeIn>
            <div className="text-[10px] text-muted uppercase tracking-widest mb-4">BGA Alignment</div>
            <h2 className="text-3xl font-bold mb-6">Better Systems. Not Highest PnL.</h2>
            <p className="text-muted leading-relaxed mb-10 max-w-2xl mx-auto text-sm">
              Retail traders in Southeast Asia, India, and Africa have access to Bybit but not Bloomberg.
              This system gives them the same analytical framework institutional desks use, with full
              transparency into every decision and no black boxes.
            </p>
          </FadeIn>
          <FadeIn delay={0.1}>
            <div className="grid sm:grid-cols-3 gap-5 text-left">
              {[
                { icon: '⚖️', t: 'Market Fairness',     d: 'Every decision trace is auditable. The AI cannot claim a call it did not make.' },
                { icon: '🌍', t: 'Financial Inclusion', d: 'Professional playbook methodology accessible to retail traders globally.' },
                { icon: '🔍', t: 'No Black Boxes',      d: 'Deterministic decision tree plus LLM explanation. Reason is always shown.' },
              ].map((b, i) => (
                <div key={i} className="glass rounded-xl p-6 border border-border">
                  <div className="text-2xl mb-4">{b.icon}</div>
                  <div className="text-sm font-bold text-text mb-2">{b.t}</div>
                  <div className="text-xs text-muted leading-relaxed">{b.d}</div>
                </div>
              ))}
            </div>
          </FadeIn>
        </div>
      </section>

      {/* CTA */}
      <section className="relative z-10 py-28 px-8 text-center border-t border-border/50">
        <FadeIn>
          <div className="max-w-2xl mx-auto">
            <h2 className="text-4xl font-bold mb-6 leading-tight">
              See it live.<br />
              <span className="text-mantle">Verify it yourself.</span>
            </h2>
            <p className="text-muted mb-12 text-sm leading-relaxed">
              The trading terminal is running. Every recommendation made today is logged on Mantle Sepolia.
              You can verify any recommendation independently. We cannot alter them after the fact.
            </p>
            <motion.button
              onClick={onEnter}
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.97 }}
              className="px-12 py-4 rounded-xl bg-mantle text-dark font-bold text-base hover:bg-mantle-dim transition-all"
              style={{ boxShadow: '0 0 50px rgba(0,212,170,0.3)' }}
            >
              Open Trading Terminal
            </motion.button>
            <div className="mt-8 text-[10px] text-muted">
              Mantle AI Awakening Hackathon Phase II - AI Trading and Strategy
              <span className="mx-2 text-muted/30">|</span>
              <a href={contractExplorerUrl} target="_blank" rel="noreferrer" className="text-mantle hover:underline font-mono">
                {contractAddress
                  ? `${contractAddress.slice(0,10)}... on Mantle Sepolia`
                  : 'AuditLog + StrategyGate on Mantle Sepolia'}
              </a>
              {deployedAt && <span className="ml-2 text-muted/40">deployed {deployedAt}</span>}
            </div>
          </div>
        </FadeIn>
      </section>

    </div>
  )
}
