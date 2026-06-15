import { useState, useEffect, useRef } from 'react'
import { motion, useInView, AnimatePresence } from 'framer-motion'

const HASH_DEMO = '0x4f2e9a1b8c3d7e6f5a4b2c1d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0'
const BLOCK_DEMO = '4,821,903'
const TX_DEMO    = '0x232eaf4296b2776c9a28ec5c5fdf2a6e3f15d1e2521816fa2ecb7749efae2a4e'
const EXPLORER   = 'https://explorer.sepolia.mantle.xyz/tx/0x232eaf4296b2776c9a28ec5c5fdf2a6e3f15d1e2521816fa2ecb7749efae2a4e'

// Typewriter for the hash
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
  return <span className="font-mono text-mantle">{shown}<span className="animate-pulse">|</span></span>
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

const PIPELINE = [
  { icon: '📡', title: 'Data Collection',    desc: 'Bybit WS + Binance aggTrades + Mantle FusionX DEX',  detail: '130k candles/symbol, 4H backfill, real-time streaming' },
  { icon: '⚡', title: 'Stream Processing', desc: 'Redpanda (Kafka) → QuestDB WAL+DEDUP',                 detail: 'Sub-100ms latency, idempotent ingestion, 7 tables' },
  { icon: '🧮', title: 'Deterministic AI',  desc: '12 indicators + 9-scenario decision tree',              detail: 'No LLM at this layer — pure Python, fully auditable' },
  { icon: '🤖', title: 'LLM Explanation',   desc: 'Groq llama-3.3-70b — explains, not decides',           detail: '2-call pipeline: macro regime then full analysis' },
  { icon: '⛓',  title: 'On-Chain Proof',   desc: 'keccak256(analysis) → Mantle Sepolia AuditLog',         detail: 'Logged BEFORE you see it. Block timestamp is proof.' },
]

const MOAT = [
  {
    title: 'Pre-Trade Cryptographic Proof',
    color: '#00D4AA',
    icon: '🔐',
    desc: 'Every recommendation is keccak256-hashed and logged on Mantle Sepolia before you see it. Block timestamp proves the AI predicted BEFORE price moved — not after.',
    vs: 'Every other AI trading signal can be fabricated retrospectively. There is no proof.',
  },
  {
    title: 'Deterministic Decision Tree',
    color: '#60A5FA',
    icon: '🌳',
    desc: 'Scenarios 1-9 are identified by a deterministic Q1-Q4 decision tree — no LLM, no hallucination. The LLM explains the verdict, it does not make it.',
    vs: 'Black-box models give you a number with no reasoning you can challenge.',
  },
  {
    title: 'Composable On-Chain Oracle',
    color: '#A78BFA',
    icon: '🔗',
    desc: 'StrategyGate.sol lets any Mantle DeFi protocol call getLatestSignal("BTCUSDT") and gate positions by regime. The AI signal becomes a trustless DeFi primitive.',
    vs: 'No other AI trading system has an on-chain composable oracle that DeFi protocols can integrate.',
  },
  {
    title: 'Mantle-Native Intelligence',
    color: '#F59E0B',
    icon: '🌐',
    desc: 'For MNTUSDT: FusionX DEX CVD replaces Binance spot CVD as the "real money" signal. On-chain swap flow is more truthful than CEX orderflow for Mantle-native assets.',
    vs: 'Generic tools apply the same BTC analysis to every asset. We use Mantle DEX data no CEX-only system can access.',
  },
]

const INDICATORS = [
  'VWMA 20D', 'Long/Short Ratio', 'Futures CVD', 'Spot CVD',
  'Bid/Ask Delta', 'Funding Rate', 'Open Interest', 'Liq Events',
  'Order Book Depth', 'MACD 1H', 'RSI 14', 'VPVR',
]

export default function Landing({ onEnter }) {
  const [hashVisible, setHashVisible] = useState(false)
  const [confirmed, setConfirmed] = useState(false)

  useEffect(() => {
    const t1 = setTimeout(() => setHashVisible(true), 1200)
    const t2 = setTimeout(() => setConfirmed(true), 3800)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [])

  return (
    <div className="min-h-screen bg-dark text-text overflow-x-hidden">

      {/* Ambient background */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute inset-0 grid-bg opacity-100" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[900px] h-[500px] rounded-full opacity-[0.06]"
             style={{ background: 'radial-gradient(ellipse, #00D4AA 0%, transparent 70%)' }} />
        <div className="absolute bottom-0 right-0 w-96 h-96 opacity-[0.04]"
             style={{ background: 'radial-gradient(ellipse at bottom right, #7C3AED, transparent)' }} />
      </div>

      {/* NAV */}
      <nav className="relative z-20 flex items-center justify-between px-6 py-4 border-b border-border/50">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-mantle/10 border border-mantle/30 flex items-center justify-center">🧠</div>
          <span className="font-bold text-sm text-text">Mantle AI Copilot</span>
          <span className="text-[10px] text-muted hidden sm:block">- The Turing Test</span>
        </div>
        <div className="flex items-center gap-3">
          <a href={EXPLORER} target="_blank" rel="noreferrer"
             className="hidden sm:flex items-center gap-1.5 text-[10px] text-muted hover:text-mantle transition-colors">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
            Live on Mantle Sepolia
          </a>
          <motion.button
            onClick={onEnter}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            className="px-4 py-2 rounded-lg bg-mantle text-dark text-xs font-bold hover:bg-mantle-dim transition-all"
          >
            Open Terminal →
          </motion.button>
        </div>
      </nav>

      {/* ═══════════════════════════════════════ HERO ══════════════════════════════════════ */}
      <section className="relative z-10 pt-20 pb-16 px-6 max-w-6xl mx-auto">
        <div className="grid lg:grid-cols-2 gap-12 items-center">

          {/* Left: copy */}
          <div>
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6 }}
            >
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-mantle/30 bg-mantle/5 text-[10px] text-mantle font-medium mb-6">
                <span className="w-1.5 h-1.5 rounded-full bg-mantle animate-pulse" />
                Mantle AI Awakening Hackathon - Phase II
              </div>

              <h1 className="text-4xl lg:text-5xl font-bold leading-tight mb-6">
                AI Trading Signals
                <br />
                <span className="text-mantle">Cryptographically Proven</span>
                <br />
                Before Price Moves.
              </h1>

              <p className="text-muted text-base leading-relaxed mb-8 max-w-lg">
                Every recommendation is keccak256-hashed and locked on{' '}
                <span className="text-mantle font-medium">Mantle Sepolia</span> before
                you see it. Block timestamp is immutable proof — not a claim.
              </p>

              <div className="flex flex-wrap gap-3">
                <motion.button
                  onClick={onEnter}
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.98 }}
                  className="px-6 py-3 rounded-xl bg-mantle text-dark font-bold text-sm hover:bg-mantle-dim transition-all mantle-glow"
                >
                  Open Trading Terminal →
                </motion.button>
                <a
                  href="#how-it-works"
                  className="px-6 py-3 rounded-xl glass border border-border text-sm font-medium hover:border-mantle/30 transition-all"
                >
                  How it works ↓
                </a>
              </div>
            </motion.div>
          </div>

          {/* Right: animated proof card */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.7, delay: 0.3 }}
            className="relative"
          >
            <div className="glass rounded-2xl p-6 border border-border" style={{ boxShadow: '0 0 40px rgba(0,212,170,0.08)' }}>

              {/* Signal being analyzed */}
              <div className="flex items-center gap-3 mb-5">
                <div className="flex-1">
                  <div className="text-[10px] text-muted mb-1">Live Analysis - BTCUSDT</div>
                  <div className="text-2xl font-bold text-text">$104,832</div>
                </div>
                <div className="px-4 py-2 rounded-xl font-bold text-sm"
                     style={{ backgroundColor: 'rgba(0,212,170,0.1)', color: '#00D4AA', border: '1px solid rgba(0,212,170,0.3)' }}>
                  STRONG LONG
                </div>
              </div>

              {/* Confluence */}
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

              {/* Hash computation animation */}
              <div className="bg-dark rounded-xl p-4 border border-border mb-4">
                <div className="text-[10px] text-muted mb-2 flex items-center gap-2">
                  <span>Computing keccak256 hash...</span>
                  {confirmed && <span className="text-green-400">✓ Complete</span>}
                </div>
                <div className="text-[10px] break-all leading-relaxed" style={{ minHeight: '32px' }}>
                  {hashVisible ? <TypedHash value={HASH_DEMO} /> : <span className="text-muted">Waiting...</span>}
                </div>
              </div>

              {/* On-chain confirmation */}
              <AnimatePresence>
                {confirmed && (
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="rounded-xl p-3 flex items-start gap-3"
                    style={{ backgroundColor: 'rgba(0,212,170,0.06)', border: '1px solid rgba(0,212,170,0.2)' }}
                  >
                    <span className="text-lg flex-shrink-0">⛓</span>
                    <div className="min-w-0">
                      <div className="text-[10px] font-bold text-mantle mb-0.5">Confirmed on Mantle Sepolia</div>
                      <div className="text-[9px] text-muted">Block #{BLOCK_DEMO} - Logged BEFORE recommendation shown</div>
                      <a href={EXPLORER} target="_blank" rel="noreferrer"
                         className="text-[9px] font-mono text-mantle/70 hover:text-mantle truncate block mt-0.5">
                        {TX_DEMO.slice(0, 30)}...
                      </a>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Decorative glow */}
            <div className="absolute -inset-px rounded-2xl pointer-events-none opacity-30"
                 style={{ background: 'linear-gradient(135deg, rgba(0,212,170,0.1) 0%, transparent 60%)' }} />
          </motion.div>
        </div>
      </section>

      {/* ═══════════════════════════════════ THE PROBLEM ══════════════════════════════════ */}
      <section className="relative z-10 py-16 px-6 border-y border-border/50 bg-dark/40">
        <div className="max-w-6xl mx-auto">
          <FadeIn>
            <div className="text-center mb-12">
              <div className="text-[10px] text-muted uppercase tracking-widest mb-3">The Problem We Solve</div>
              <h2 className="text-3xl font-bold">AI Trading Signals Are Unverifiable</h2>
              <p className="text-muted mt-3 max-w-2xl mx-auto">
                Any trading signal service can claim "we called this move." There is no way to prove
                a recommendation existed before price moved — until now.
              </p>
            </div>
          </FadeIn>

          <div className="grid lg:grid-cols-2 gap-8">
            {/* Before */}
            <FadeIn delay={0.1}>
              <div className="rounded-2xl p-6 border border-bear/20 bg-bear/5">
                <div className="text-bear font-bold text-sm mb-5 flex items-center gap-2">
                  <span>✗</span> Traditional AI Signal
                </div>
                {[
                  'AI generates signal (private)',
                  'Price moves in predicted direction',
                  'Signal published → "We called it!"',
                  'No timestamp. No proof. No audit.',
                  'Survivorship bias hides all failures.',
                ].map((s, i) => (
                  <div key={i} className="flex items-start gap-3 mb-3 text-xs text-muted">
                    <span className="text-bear mt-0.5">→</span>
                    <span>{s}</span>
                  </div>
                ))}
                <div className="mt-5 p-3 rounded-lg bg-dark border border-border text-[10px] text-muted italic">
                  "Trust us — our AI predicted the move." No verifiable evidence.
                </div>
              </div>
            </FadeIn>

            {/* After */}
            <FadeIn delay={0.2}>
              <div className="rounded-2xl p-6 border border-mantle/20 bg-mantle/5">
                <div className="text-mantle font-bold text-sm mb-5 flex items-center gap-2">
                  <span>✓</span> Mantle AI Copilot
                </div>
                {[
                  '12 indicators + decision tree computed',
                  'LLM generates full analysis + pre-trade note',
                  'keccak256(analysis) logged on Mantle Sepolia',
                  'Block timestamp = cryptographic pre-trade proof',
                  'You see the recommendation AFTER the hash',
                ].map((s, i) => (
                  <div key={i} className="flex items-start gap-3 mb-3 text-xs">
                    <span className="text-mantle mt-0.5">→</span>
                    <span className={i === 2 || i === 3 ? 'text-text font-medium' : 'text-muted'}>{s}</span>
                  </div>
                ))}
                <div className="mt-5 p-3 rounded-lg bg-dark border border-mantle/20 text-[10px] text-mantle">
                  Anyone can recompute the hash from the displayed analysis and verify it matches the on-chain record.
                </div>
              </div>
            </FadeIn>
          </div>
        </div>
      </section>

      {/* ══════════════════════════════════ ARCHITECTURE ══════════════════════════════════ */}
      <section id="how-it-works" className="relative z-10 py-16 px-6">
        <div className="max-w-6xl mx-auto">
          <FadeIn>
            <div className="text-center mb-12">
              <div className="text-[10px] text-muted uppercase tracking-widest mb-3">Under The Hood</div>
              <h2 className="text-3xl font-bold">Institutional-Grade Pipeline</h2>
              <p className="text-muted mt-3">Five layers, each auditable. Nothing is a black box.</p>
            </div>
          </FadeIn>

          <div className="relative">
            {/* Connecting line */}
            <div className="absolute left-6 top-10 bottom-10 w-px bg-gradient-to-b from-mantle via-mantle/30 to-transparent hidden md:block" />

            <div className="space-y-4">
              {PIPELINE.map((step, i) => (
                <FadeIn key={i} delay={i * 0.1}>
                  <div className="flex gap-4 items-start group">
                    <div className="relative z-10 flex-shrink-0">
                      <div className="w-12 h-12 rounded-xl bg-dark border border-border group-hover:border-mantle/40 transition-all flex items-center justify-center text-xl">
                        {step.icon}
                      </div>
                    </div>
                    <div className="flex-1 glass rounded-xl px-5 py-4 group-hover:border-mantle/20 transition-all">
                      <div className="flex flex-wrap items-baseline gap-3 mb-1">
                        <span className="text-[10px] text-mantle font-bold uppercase tracking-wider">Step {i + 1}</span>
                        <span className="text-sm font-bold text-text">{step.title}</span>
                      </div>
                      <div className="text-xs text-muted mb-1">{step.desc}</div>
                      <div className="text-[10px] text-muted/60 font-mono">{step.detail}</div>
                    </div>
                  </div>
                </FadeIn>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ════════════════════════════════════ MOAT ════════════════════════════════════════ */}
      <section className="relative z-10 py-16 px-6 border-t border-border/50">
        <div className="max-w-6xl mx-auto">
          <FadeIn>
            <div className="text-center mb-12">
              <div className="text-[10px] text-muted uppercase tracking-widest mb-3">Why We Win</div>
              <h2 className="text-3xl font-bold">Four Unfair Advantages</h2>
            </div>
          </FadeIn>

          <div className="grid sm:grid-cols-2 gap-5">
            {MOAT.map((m, i) => (
              <FadeIn key={i} delay={i * 0.1}>
                <div className="rounded-2xl p-6 border border-border hover:border-opacity-50 transition-all glass"
                     style={{ borderColor: m.color + '30' }}>
                  <div className="flex items-center gap-3 mb-4">
                    <span className="text-2xl">{m.icon}</span>
                    <span className="font-bold text-sm text-text">{m.title}</span>
                  </div>
                  <p className="text-xs text-muted leading-relaxed mb-4">{m.desc}</p>
                  <div className="rounded-lg p-3 bg-dark border border-border">
                    <div className="text-[9px] text-muted uppercase tracking-wider mb-1">vs. Competition</div>
                    <div className="text-[10px] text-muted/70 italic">{m.vs}</div>
                  </div>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* ════════════════════════════════ 12 INDICATORS ═══════════════════════════════════ */}
      <section className="relative z-10 py-16 px-6 border-t border-border/50 bg-dark/40">
        <div className="max-w-6xl mx-auto">
          <FadeIn>
            <div className="grid lg:grid-cols-2 gap-12 items-center">
              <div>
                <div className="text-[10px] text-muted uppercase tracking-widest mb-3">Intelligence Layer</div>
                <h2 className="text-3xl font-bold mb-5">
                  12 Confluence Indicators<br />
                  <span className="text-mantle">One Deterministic Verdict</span>
                </h2>
                <p className="text-muted text-sm leading-relaxed mb-6">
                  Each indicator is computed deterministically from QuestDB time-series data.
                  The decision tree maps Q1-Q4 answers to one of 9 scenarios — no LLM involved.
                  The AI explains the result, it does not produce it.
                </p>
                <div className="space-y-2 text-xs text-muted">
                  <div className="flex items-center gap-2"><span className="text-mantle">→</span> S1: Healthy Uptrend → LONG</div>
                  <div className="flex items-center gap-2"><span className="text-mantle">→</span> S3: Confirmed Reversal from Top → SHORT</div>
                  <div className="flex items-center gap-2"><span className="text-mantle">→</span> S5: Dead Cat Bounce → NO_TRADE</div>
                  <div className="flex items-center gap-2"><span className="text-mantle">→</span> S8: Ranging Consolidation → NEUTRAL</div>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2">
                {INDICATORS.map((ind, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, scale: 0.8 }}
                    whileInView={{ opacity: 1, scale: 1 }}
                    viewport={{ once: true }}
                    transition={{ delay: i * 0.04, duration: 0.3 }}
                    className="rounded-lg px-3 py-2.5 glass border border-border text-[10px] text-muted text-center hover:border-mantle/30 hover:text-text transition-all"
                  >
                    {ind}
                  </motion.div>
                ))}
              </div>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ══════════════════════════════════ MANTLE ════════════════════════════════════════ */}
      <section className="relative z-10 py-16 px-6 border-t border-border/50">
        <div className="max-w-6xl mx-auto">
          <FadeIn>
            <div className="text-center mb-12">
              <div className="text-[10px] text-muted uppercase tracking-widest mb-3">Mantle Ecosystem</div>
              <h2 className="text-3xl font-bold">Built for Mantle. Not Ported.</h2>
              <p className="text-muted mt-3 max-w-2xl mx-auto">
                Every Mantle integration serves a functional purpose in the intelligence layer.
              </p>
            </div>
          </FadeIn>

          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { icon: '⛓', name: 'AuditLog.sol', type: 'ESSENTIAL', desc: 'Pre-trade hash registry. Immutable proof of prediction.' },
              { icon: '🔮', name: 'StrategyGate.sol', type: 'ESSENTIAL', desc: 'Composable oracle. Any DeFi protocol can gate positions by regime.' },
              { icon: '💧', name: 'FusionX DEX CVD', type: 'USEFUL', desc: 'On-chain swap flow as primary Spot CVD for MNTUSDT.' },
              { icon: '🌊', name: 'mETH Yield Signal', type: 'USEFUL', desc: 'Carry trade edge: ETH funding vs Mantle liquid staking APY.' },
            ].map((m, i) => (
              <FadeIn key={i} delay={i * 0.08}>
                <div className="glass rounded-xl p-4 border border-border hover:border-mantle/30 transition-all h-full">
                  <div className="text-2xl mb-3">{m.icon}</div>
                  <div className="text-sm font-bold text-text mb-1">{m.name}</div>
                  <div className="mb-2">
                    <span className={`text-[9px] px-2 py-0.5 rounded-full font-bold ${
                      m.type === 'ESSENTIAL' ? 'bg-mantle/10 text-mantle' : 'bg-blue-500/10 text-blue-400'
                    }`}>
                      {m.type}
                    </span>
                  </div>
                  <p className="text-[10px] text-muted leading-relaxed">{m.desc}</p>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* ══════════════════════════════════ BGA ═══════════════════════════════════════════ */}
      <section className="relative z-10 py-16 px-6 border-t border-border/50 bg-dark/40">
        <div className="max-w-4xl mx-auto text-center">
          <FadeIn>
            <div className="text-[10px] text-muted uppercase tracking-widest mb-3">BGA Alignment</div>
            <h2 className="text-3xl font-bold mb-6">Better Systems. Not Highest PnL.</h2>
            <p className="text-muted leading-relaxed mb-8 max-w-2xl mx-auto">
              Retail traders in Southeast Asia, India, and Africa have access to Bybit but not Bloomberg.
              This system gives them the same analytical framework institutional desks use — with full
              transparency into every decision and no black boxes.
            </p>
          </FadeIn>

          <FadeIn delay={0.1}>
            <div className="grid sm:grid-cols-3 gap-5 text-left">
              {[
                { icon: '⚖️', t: 'Market Fairness', d: 'Every decision trace is auditable. The AI cannot claim a call it did not make.' },
                { icon: '🌍', t: 'Financial Inclusion', d: 'Professional playbook methodology accessible to retail traders globally.' },
                { icon: '🚫', t: 'No Black Boxes', d: 'Deterministic decision tree + LLM explanation. Reason is always shown.' },
              ].map((b, i) => (
                <div key={i} className="glass rounded-xl p-5 border border-border">
                  <div className="text-2xl mb-3">{b.icon}</div>
                  <div className="text-sm font-bold text-text mb-2">{b.t}</div>
                  <div className="text-[10px] text-muted leading-relaxed">{b.d}</div>
                </div>
              ))}
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ═══════════════════════════════════ CTA ══════════════════════════════════════════ */}
      <section className="relative z-10 py-24 px-6 text-center border-t border-border/50">
        <FadeIn>
          <div className="max-w-2xl mx-auto">
            <h2 className="text-4xl font-bold mb-5">
              See it live.{' '}
              <span className="text-mantle">Verify it yourself.</span>
            </h2>
            <p className="text-muted mb-10">
              The trading terminal is running. Every recommendation made today is logged on Mantle Sepolia.
              You can verify any recommendation independently — we cannot alter them after the fact.
            </p>
            <motion.button
              onClick={onEnter}
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.97 }}
              className="px-10 py-4 rounded-xl bg-mantle text-dark font-bold text-base hover:bg-mantle-dim transition-all"
              style={{ boxShadow: '0 0 40px rgba(0,212,170,0.3)' }}
            >
              Open Trading Terminal →
            </motion.button>
            <div className="mt-6 text-[10px] text-muted">
              Mantle AI Awakening Hackathon Phase II - AI Trading &amp; Strategy
              <span className="mx-2">·</span>
              Contract:{' '}
              <a href={EXPLORER} target="_blank" rel="noreferrer"
                 className="text-mantle hover:underline font-mono">
                AuditLog + StrategyGate on Mantle Sepolia
              </a>
            </div>
          </div>
        </FadeIn>
      </section>

    </div>
  )
}
