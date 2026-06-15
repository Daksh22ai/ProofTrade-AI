import { useState, useRef } from 'react'
import { motion, useInView, AnimatePresence } from 'framer-motion'

function FadeIn({ children, delay = 0 }) {
  const ref = useRef(null)
  const inView = useInView(ref, { once: true, margin: '-40px' })
  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 20 }}
      animate={inView ? { opacity: 1, y: 0 } : {}}
      transition={{ duration: 0.45, delay }}
    >
      {children}
    </motion.div>
  )
}

function Expand({ title, color = '#00D4AA', children }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-xl border overflow-hidden" style={{ borderColor: open ? color + '35' : '#1a2744' }}>
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full text-left px-5 py-3.5 flex items-center justify-between hover:bg-white/[0.02] transition-all"
      >
        <span className="text-sm font-semibold text-text">{title}</span>
        <span className="text-xs font-bold flex-shrink-0 ml-3" style={{ color: open ? color : '#6b7280' }}>
          {open ? 'Less' : 'More'}
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="c"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="border-t px-5 py-4" style={{ borderColor: color + '20' }}>
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

const SCENARIOS = [
  {
    num: 1, name: 'Healthy Uptrend', verdict: 'LONG', color: '#00D4AA',
    conditions: 'Price above VWMA. Spot CVD rising. OI rising. Funding neutral-positive. MACD above zero.',
    meaning: 'All five conditions confirm the same thing: real money is buying, new positions are entering, and trend conditions are intact. This is the highest-probability long setup.',
    action: 'Enter long on the next 15m candle confirmation. Full position size allowed.',
    risk: 'Stop below the most recent higher low plus 0.3% buffer. ATR times 1.5 minimum distance.',
  },
  {
    num: 2, name: 'Uptrend Weakening', verdict: 'NEUTRAL', color: '#FFD700',
    conditions: 'Price above VWMA. Spot CVD not rising (flat or falling).',
    meaning: 'Price is still above the macro trend level, but real money is no longer confirming the move. This is a warning, not a trade signal.',
    action: 'No new longs. Watch for Spot CVD to resume rising before re-entering.',
    risk: 'If already long, tighten stop to breakeven or reduce size.',
  },
  {
    num: 3, name: 'Confirmed Reversal from Top', verdict: 'SHORT', color: '#FF6B6B',
    conditions: 'Price below VWMA. Spot CVD falling. OI falling (long liquidations).',
    meaning: 'Distribution is confirmed. Real money is selling, overleveraged longs are being flushed, and the trend has broken. The most reliable short setup.',
    action: 'Enter short on first 1H close below a key support level with CVD making new lows.',
    risk: 'Stop above the recent lower high plus 0.5% buffer.',
  },
  {
    num: 4, name: 'Healthy Downtrend', verdict: 'SHORT', color: '#FF6B6B',
    conditions: 'Price below VWMA. Spot CVD falling. OI rising (new shorts entering).',
    meaning: 'Unlike S3, new short positions are entering into the decline. This means trend continuation is likely. Shorting into this setup has positive carry.',
    action: 'Enter short on retests of former support turned resistance. Scale into weakness.',
    risk: 'Stop above structure plus buffer. Bear regime cap of 5x leverage applies.',
  },
  {
    num: 5, name: 'Dead Cat Bounce', verdict: 'NO_TRADE', color: '#FF8C00',
    conditions: 'Price above VWMA. Spot CVD rising. OI falling.',
    meaning: 'Price is rising and spot CVD is rising, which looks bullish. But OI is falling, meaning this is short covering, not new longs entering. The move has no conviction behind it.',
    action: 'Do not enter longs. Wait for OI to confirm with rising positions.',
    risk: 'If caught long, treat any Spot CVD stall as exit signal.',
  },
  {
    num: 6, name: 'Bottom Forming', verdict: 'NEUTRAL', color: '#FFD700',
    conditions: 'Price below VWMA. Spot CVD not falling.',
    meaning: 'Price is below the macro level but selling pressure is easing. Potential base building. Too early to confirm, but worth watching for transition to S7.',
    action: 'No directional trade. Monitor for Spot CVD to turn rising as confirmation trigger.',
    risk: 'Small position for range-trading scalps at support only with 6+ confluence.',
  },
  {
    num: 7, name: 'Confirmed Reversal from Bottom', verdict: 'LONG', color: '#00CC88',
    conditions: 'Price above VWMA. Spot CVD rising. OI rising. But funding negative or MACD below zero.',
    meaning: 'Most long conditions are met but the funding and momentum conditions are not yet fully aligned. The reversal is confirmed but not mature. Lower conviction than S1.',
    action: 'Enter smaller initial position. Wait for funding to normalize and MACD to cross above zero for full size.',
    risk: 'Tighter stop. Position size cap at 50% of normal until S1 conditions fully form.',
  },
  {
    num: 8, name: 'Ranging Consolidation', verdict: 'NEUTRAL', color: '#6b7280',
    conditions: 'Price within 3% of VWMA with flat CVD and flat OI.',
    meaning: 'No directional conviction in either direction. The market is in balance. Trend trades in either direction have poor expected value.',
    action: 'Range-fade only: small size longs at range support, small size shorts at range resistance. 7+ confluence minimum.',
    risk: 'Tight stops just outside range boundaries. Exit before funding resets.',
  },
]

const CVD_STATES = [
  {
    state: 'BOTH RISING', color: '#00D4AA', verdict: 'STRONG LONG',
    spot: 'rising', fut: 'rising',
    meaning: 'Real money (spot buyers) and leveraged money (futures longs) are both accumulating. This is the highest-conviction long setup because two independent markets are confirming the same direction.',
    note: 'Before any long: Spot CVD must be rising. This cannot be faked by derivatives.',
  },
  {
    state: 'BOTH FALLING', color: '#FF6B6B', verdict: 'STRONG SHORT',
    spot: 'falling', fut: 'falling',
    meaning: 'Both real and leveraged money are reducing exposure or going short. Full distribution. Highest-conviction short or exit setup.',
    note: 'Correlates with S3 and S4 decision tree scenarios.',
  },
  {
    state: 'FUT + SPOT FLAT', color: '#FFD700', verdict: 'NO TRADE',
    spot: 'flat', fut: 'rising',
    meaning: 'Futures are being bought but spot is not following. This is speculative positioning without underlying real money support. Classic pump-before-dump pattern.',
    note: 'PROHIBITED: Do not enter new longs in this state. If already long, reduce or close.',
  },
  {
    state: 'FUT - SPOT FLAT', color: '#60A5FA', verdict: 'WATCH',
    spot: 'flat', fut: 'falling',
    meaning: 'Futures shorts are building but spot is not confirming selling. Often indicates smart money quietly accumulating while futures players pile on shorts. Watch for reversal.',
    note: 'Correlates with S6 and early S7 patterns. Not a trade signal by itself.',
  },
]

const RISK_RULES = [
  { rule: '1% Account Risk', detail: 'Maximum loss per trade is 1% of account value. On a $10,000 account, this is $100 at risk regardless of leverage.' },
  { rule: 'ATR-Based Stops', detail: 'Stop distance = ATR(14, 1H) times 1.5 as the minimum. Always placed beyond a structural level (higher low for longs, lower high for shorts) with 0.3-0.5% buffer.' },
  { rule: 'Session-Based Leverage', detail: 'Asian session: maximum 5x. London and NY: full confluence table. Dead Hours: 50% position size cap, no new trend trades.' },
  { rule: 'Regime Override', detail: 'Bear regime: 5x absolute maximum regardless of confluence. Transition: 5x maximum. Bull: full table up to 10x at 8-9 confluence.' },
  { rule: 'Confluence-Leverage Table', detail: '5-6 confluence: 5x. 7 confluence: 7x. 8-9 confluence: 10x. 8-9 confluence with BOTH_RISING CVD matrix: 12x. Never exceed regime cap.' },
  { rule: 'Never Move Stop Against Position', detail: 'Once placed, a stop can only move in favor of the trade (trailing). Moving a stop wider to avoid being stopped out is the most common cause of catastrophic losses.' },
  { rule: 'Funding Reset Window', detail: 'Funding rate shows 0% for 30 minutes after resets at 00:00, 08:00, and 16:00 UTC. Do not use funding as a signal during this window.' },
]

const PRE_TRADE = [
  { label: 'Why this works', color: '#00D4AA', desc: 'State the single most important reason this setup has edge. Must cite a specific confluence reading, not a general observation.' },
  { label: 'What proves me wrong', color: '#FF6B6B', desc: 'A specific, observable, measurable condition that would mean the thesis is invalidated. Price level, CVD stall, or indicator flip. Vague invalidation conditions are not acceptable.' },
  { label: 'When to add size', color: '#FFD700', desc: 'A specific, measurable trigger for adding to a winning position. Example: OI rises 5% above entry level with Spot CVD making a new high.' },
]

export default function Playbook({ onBack }) {
  return (
    <div className="min-h-screen bg-dark text-text overflow-x-hidden">

      {/* Background */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute inset-0 grid-bg opacity-70" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[700px] h-[300px] rounded-full opacity-[0.05]"
             style={{ background: 'radial-gradient(ellipse, #00D4AA 0%, transparent 70%)' }} />
      </div>

      {/* Nav */}
      <nav className="relative z-20 flex items-center justify-between px-8 py-4 border-b border-border/50 sticky top-0 bg-dark/90 backdrop-blur-sm">
        <div className="flex items-center gap-4">
          <button onClick={onBack} className="text-xs text-muted hover:text-text transition-colors">
            Back
          </button>
          <div className="w-px h-4 bg-border" />
          <div className="flex items-center gap-2">
            <img src="/logo.png" alt="Logo" className="w-6 h-6 rounded object-contain" />
            <span className="text-xs font-semibold text-text">Trading Playbook</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {['Scenarios', 'CVD Matrix', 'Risk Rules', 'Pre-Trade'].map(s => (
            <a key={s} href={`#${s.toLowerCase().replace(' ', '-')}`}
               className="text-[10px] text-muted hover:text-text transition-colors hidden md:block">
              {s}
            </a>
          ))}
        </div>
      </nav>

      <div className="relative z-10 max-w-4xl mx-auto px-8 py-16 space-y-20">

        {/* Header */}
        <FadeIn>
          <div className="text-center">
            <div className="text-[10px] text-muted uppercase tracking-widest mb-4">The Intelligence Layer</div>
            <h1 className="text-4xl font-bold mb-5 leading-tight">
              The Trading Playbook<br />
              <span className="text-mantle">That Powers the AI</span>
            </h1>
            <p className="text-muted text-base leading-relaxed max-w-2xl mx-auto">
              Every recommendation this system produces is traceable to a specific rule
              in this framework. No black box. No invented logic. The AI explains rules
              from this playbook. It does not invent new ones.
            </p>
          </div>
        </FadeIn>

        {/* PART 0: MACRO REGIME */}
        <section>
          <FadeIn>
            <div className="mb-8">
              <div className="text-[10px] text-mantle uppercase tracking-widest mb-2">Part 0</div>
              <h2 className="text-2xl font-bold mb-3">Macro Regime Filter</h2>
              <p className="text-muted text-sm leading-relaxed">
                Before any individual symbol is analyzed, the macro regime is determined from BTC.
                This sets the maximum allowable leverage, minimum confluence requirement, and position
                size cap for all symbols in that session. No altcoin trade is taken in isolation from
                the broader macro context.
              </p>
            </div>
          </FadeIn>
          <div className="grid md:grid-cols-3 gap-4">
            {[
              { regime: 'BULL', color: '#00D4AA', icon: 'B', conditions: 'Price above 20D VWMA. Funding neutral-to-positive (0.005-0.03% per 8h). Stablecoin supply growing.', rules: 'Max leverage 10x on A+ setups. Min 5 confluences. Altcoin longs allowed.' },
              { regime: 'BEAR', color: '#FF6B6B', icon: 'S', conditions: 'Price below 20D VWMA. Funding negative. OI falling or flat with declining price.', rules: 'Max leverage 5x. Min 7 confluences required. No altcoin longs. 3x cap for counter-trend.' },
              { regime: 'TRANSITION', color: '#FFD700', icon: 'T', conditions: 'VWMA flat or choppy. Funding near zero. OI direction unclear.', rules: 'Max leverage 5x. Min 7 confluences. Treat like Bear until resolved.' },
            ].map(r => (
              <FadeIn key={r.regime} delay={0.05}>
                <div className="rounded-xl border p-5 h-full" style={{ borderColor: r.color + '30', background: r.color + '06' }}>
                  <div className="flex items-center gap-2 mb-3">
                    <span className="w-7 h-7 rounded font-bold text-sm flex items-center justify-center"
                          style={{ backgroundColor: r.color + '20', color: r.color }}>
                      {r.icon}
                    </span>
                    <span className="font-bold text-sm" style={{ color: r.color }}>{r.regime}</span>
                  </div>
                  <div className="text-[10px] text-muted mb-3 leading-relaxed">{r.conditions}</div>
                  <div className="text-[10px] text-text leading-relaxed">{r.rules}</div>
                </div>
              </FadeIn>
            ))}
          </div>
        </section>

        {/* PART 1: SESSION */}
        <section>
          <FadeIn>
            <div className="mb-8">
              <div className="text-[10px] text-mantle uppercase tracking-widest mb-2">Part 1</div>
              <h2 className="text-2xl font-bold mb-3">Session Awareness</h2>
              <p className="text-muted text-sm leading-relaxed">
                Every session has a different character. Wrong session equals wrong setup quality.
                The system automatically detects the current session and adjusts leverage caps
                and minimum confluence requirements accordingly.
              </p>
            </div>
          </FadeIn>
          <div className="grid md:grid-cols-2 gap-4">
            {[
              { name: 'LONDON', hours: '07:00-11:00 UTC', color: '#A78BFA', quality: 'High', desc: 'Scenarios 1, 3, and 4 have highest reliability. Institutional order flow initiates trends. Full leverage table applies.' },
              { name: 'NEW YORK', hours: '13:00-17:00 UTC', color: '#60A5FA', quality: 'Highest', desc: 'Highest-probability window for all trending scenarios. 15-minute triggers most reliable. All leverage table levels active.' },
              { name: 'ASIAN', hours: '01:00-07:00 UTC', color: '#F59E0B', quality: 'Low', desc: 'Max 5x leverage. False breakouts extremely common. Only Scenario 8 range-fades are safe. 6+ confluences required.' },
              { name: 'DEAD HOURS', hours: '17:00-01:00 UTC', color: '#6b7280', quality: 'None', desc: 'Position size capped at 50%. No new Scenario 1-4 entries. Scenario 8 only with 7+ confluences.' },
            ].map(s => (
              <FadeIn key={s.name} delay={0.05}>
                <div className="glass rounded-xl p-5 border border-border">
                  <div className="flex items-center justify-between mb-3">
                    <span className="font-bold text-sm" style={{ color: s.color }}>{s.name}</span>
                    <span className="text-[9px] font-mono text-muted">{s.hours}</span>
                  </div>
                  <div className="text-[10px] text-muted leading-relaxed">{s.desc}</div>
                </div>
              </FadeIn>
            ))}
          </div>
        </section>

        {/* PART 2: SCENARIOS */}
        <section id="scenarios">
          <FadeIn>
            <div className="mb-8">
              <div className="text-[10px] text-mantle uppercase tracking-widest mb-2">Part 2</div>
              <h2 className="text-2xl font-bold mb-3">The 9-Scenario Decision Tree</h2>
              <p className="text-muted text-sm leading-relaxed">
                Four binary questions about price, Spot CVD, OI trend, and MACD/funding map
                to one of nine market scenarios. This process is entirely deterministic.
                No LLM is involved. The scenario determines the base direction.
                The AI explains why, not what.
              </p>
            </div>
          </FadeIn>

          {/* Decision tree visual */}
          <FadeIn delay={0.05}>
            <div className="glass rounded-xl p-5 mb-6 border border-border">
              <div className="text-[10px] text-muted mb-4 uppercase tracking-wider">Decision Flow (Q1 to Q4)</div>
              <div className="grid grid-cols-4 gap-2 text-center text-[9px]">
                {[
                  { q: 'Q1', label: 'Price vs VWMA', yes: 'Above', no: 'Below' },
                  { q: 'Q2', label: 'Spot CVD Direction', yes: 'Rising', no: 'Falling' },
                  { q: 'Q3', label: 'OI Trend', yes: 'Rising', no: 'Falling' },
                  { q: 'Q4', label: 'MACD + Funding', yes: 'Both OK', no: 'Either not OK' },
                ].map(q => (
                  <div key={q.q} className="rounded-lg bg-dark border border-border p-3">
                    <div className="text-mantle font-bold mb-1">{q.q}</div>
                    <div className="text-text font-medium mb-2">{q.label}</div>
                    <div className="flex justify-center gap-2">
                      <span className="text-mantle">Y: {q.yes}</span>
                    </div>
                    <div className="flex justify-center gap-2">
                      <span className="text-bear">N: {q.no}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </FadeIn>

          <div className="space-y-3">
            {SCENARIOS.map((s, i) => (
              <FadeIn key={s.num} delay={i * 0.04}>
                <Expand
                  title={`S${s.num}: ${s.name} - ${s.verdict}`}
                  color={s.color}
                >
                  <div className="space-y-3">
                    <div className="grid md:grid-cols-2 gap-3">
                      <div className="rounded-lg p-3 bg-dark border border-border">
                        <div className="text-[9px] text-muted uppercase tracking-wider mb-1.5">Required Conditions</div>
                        <div className="text-[11px] text-text leading-relaxed">{s.conditions}</div>
                      </div>
                      <div className="rounded-lg p-3 bg-dark border border-border">
                        <div className="text-[9px] text-muted uppercase tracking-wider mb-1.5">What It Means</div>
                        <div className="text-[11px] text-muted leading-relaxed">{s.meaning}</div>
                      </div>
                    </div>
                    <div className="grid md:grid-cols-2 gap-3">
                      <div className="rounded-lg p-3" style={{ backgroundColor: s.color + '08', border: `1px solid ${s.color}25` }}>
                        <div className="text-[9px] uppercase tracking-wider mb-1.5" style={{ color: s.color }}>Recommended Action</div>
                        <div className="text-[11px] text-text leading-relaxed">{s.action}</div>
                      </div>
                      <div className="rounded-lg p-3 bg-bear/5 border border-bear/20">
                        <div className="text-[9px] text-bear uppercase tracking-wider mb-1.5">Risk Management</div>
                        <div className="text-[11px] text-muted leading-relaxed">{s.risk}</div>
                      </div>
                    </div>
                  </div>
                </Expand>
              </FadeIn>
            ))}
          </div>
        </section>

        {/* PART 3: CVD MATRIX */}
        <section id="cvd-matrix">
          <FadeIn>
            <div className="mb-8">
              <div className="text-[10px] text-mantle uppercase tracking-widest mb-2">Part 3</div>
              <h2 className="text-2xl font-bold mb-3">CVD Divergence Master Matrix</h2>
              <p className="text-muted text-sm leading-relaxed mb-3">
                The single most informative indicator combination. Spot CVD measures real money flow.
                Futures CVD measures leveraged speculative flow. When both agree, the signal is strong.
                When they diverge, the divergence itself is the signal.
              </p>
              <div className="rounded-lg px-4 py-3 bg-mantle/5 border border-mantle/20 text-xs text-mantle">
                Core principle: Spot CVD is the truth. It cannot be faked by derivatives activity.
                Before any long entry, Spot CVD must be rising.
              </div>
            </div>
          </FadeIn>
          <div className="grid md:grid-cols-2 gap-4">
            {CVD_STATES.map((c, i) => (
              <FadeIn key={c.state} delay={i * 0.07}>
                <div className="rounded-xl p-5 border h-full" style={{ borderColor: c.color + '30', background: c.color + '05' }}>
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <div className="font-bold text-sm mb-1" style={{ color: c.color }}>{c.state}</div>
                      <div className="flex gap-3 text-[10px] text-muted">
                        <span>Spot: <span className="text-text">{c.spot}</span></span>
                        <span>Futures: <span className="text-text">{c.fut}</span></span>
                      </div>
                    </div>
                    <span className="text-[10px] font-bold px-2 py-1 rounded" style={{ backgroundColor: c.color + '20', color: c.color }}>
                      {c.verdict}
                    </span>
                  </div>
                  <p className="text-[11px] text-muted leading-relaxed mb-3">{c.meaning}</p>
                  <div className="text-[10px] text-text/70 italic">{c.note}</div>
                </div>
              </FadeIn>
            ))}
          </div>
        </section>

        {/* PART 8: RISK MANAGEMENT */}
        <section id="risk-rules">
          <FadeIn>
            <div className="mb-8">
              <div className="text-[10px] text-mantle uppercase tracking-widest mb-2">Part 8</div>
              <h2 className="text-2xl font-bold mb-3">Risk Management Rules</h2>
              <p className="text-muted text-sm leading-relaxed">
                Risk management is not a suggestion. These are hard rules that apply to every trade,
                every session, every regime. The system enforces them in code, not just prompts.
              </p>
            </div>
          </FadeIn>
          <div className="space-y-3">
            {RISK_RULES.map((r, i) => (
              <FadeIn key={i} delay={i * 0.04}>
                <div className="glass rounded-xl px-5 py-4 border border-border flex gap-4">
                  <div className="w-1 rounded-full bg-mantle flex-shrink-0" />
                  <div>
                    <div className="text-sm font-semibold text-text mb-1">{r.rule}</div>
                    <div className="text-xs text-muted leading-relaxed">{r.detail}</div>
                  </div>
                </div>
              </FadeIn>
            ))}
          </div>
        </section>

        {/* PART 9: PRE-TRADE NOTE */}
        <section id="pre-trade">
          <FadeIn>
            <div className="mb-8">
              <div className="text-[10px] text-mantle uppercase tracking-widest mb-2">Part 9</div>
              <h2 className="text-2xl font-bold mb-3">Pre-Trade Commitment Note</h2>
              <p className="text-muted text-sm leading-relaxed">
                Before executing any trade, three commitments must be written down.
                This is not journaling after the fact. It is a pre-commitment written
                before the position is opened, covering why the trade has edge, what
                conditions invalidate the thesis, and when to add to a winner.
                The AI system generates this note as part of every analysis, and it is
                included in the on-chain hash as a permanent pre-trade record.
              </p>
            </div>
          </FadeIn>
          <div className="space-y-4">
            {PRE_TRADE.map((p, i) => (
              <FadeIn key={i} delay={i * 0.07}>
                <div className="rounded-xl p-5 border" style={{ borderColor: p.color + '25', background: p.color + '06' }}>
                  <div className="font-bold text-sm mb-2" style={{ color: p.color }}>{p.label}</div>
                  <div className="text-sm text-muted leading-relaxed">{p.desc}</div>
                </div>
              </FadeIn>
            ))}
          </div>
          <FadeIn delay={0.2}>
            <div className="mt-6 rounded-xl p-5 border border-mantle/20 bg-mantle/5">
              <div className="text-xs font-bold text-mantle mb-2">Why this is in the on-chain hash</div>
              <div className="text-xs text-muted leading-relaxed">
                All three pre-trade commitment fields are included in the keccak256 hash that is logged on
                Mantle Sepolia before the recommendation is shown. This means the system's reasoning about
                why it is taking a trade, what would prove it wrong, and when to add is permanently recorded
                before any price action occurs. Retroactive justification is not possible.
              </div>
            </div>
          </FadeIn>
        </section>

        {/* Bottom CTA */}
        <FadeIn>
          <div className="text-center pt-4">
            <p className="text-muted text-sm mb-6">
              This playbook is the foundation of the deterministic decision tree and all AI prompts.
              Every recommendation traces back to a specific rule on this page.
            </p>
            <button
              onClick={onBack}
              className="px-8 py-3 rounded-xl bg-mantle text-dark font-bold text-sm hover:bg-mantle-dim transition-all"
            >
              Back to Landing
            </button>
          </div>
        </FadeIn>

      </div>
    </div>
  )
}
