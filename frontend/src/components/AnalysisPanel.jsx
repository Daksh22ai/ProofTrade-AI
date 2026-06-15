import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { api } from '../api.js'
import { VERDICT_META, REGIME_META, CVD_META, fmtPrice, timeAgo } from '../utils.js'

const SIGNAL_COLORS = {
  BULLISH: '#00D4AA',
  BEARISH: '#FF6B6B',
  CAUTION: '#FFD700',
  WARNING: '#FF8C00',
  TRAP:    '#FF0000',
  NEUTRAL: '#6b7280',
}

// Indicator row: uses a CSS grid with fixed columns so nothing overlaps regardless of panel width
function IndicatorRow({ ind, rawValues }) {
  const color = SIGNAL_COLORS[ind.signal] || '#6b7280'
  const score = ind.score === 1
  const raw   = rawValues || {}

  const RAW_MAP = {
    'vwma':          raw.vwma_20d          ? `$${Number(raw.vwma_20d).toLocaleString()}` : null,
    'long/short':    raw.lsr_buy           ? `L${(raw.lsr_buy*100).toFixed(0)}%/S${(raw.lsr_sell*100).toFixed(0)}%` : null,
    'rsi':           raw.rsi_14            ? `${Number(raw.rsi_14).toFixed(1)}` : null,
    'macd':          raw.macd_histogram != null ? `${Number(raw.macd_histogram).toFixed(4)}` : null,
    'open interest': raw.oi_trend          ? `${raw.oi_trend}` : null,
    'funding':       raw.funding_current != null ? `${(raw.funding_current*100).toFixed(4)}%` : null,
    'spot cvd':      raw.spot_cvd_direction ? `${raw.spot_cvd_direction}` : null,
    'futures cvd':   raw.futures_cvd_direction ? `${raw.futures_cvd_direction}` : null,
    'order book':    raw.ob_wall_side      ? `${raw.ob_wall_side}` : null,
  }

  const rawVal = Object.entries(RAW_MAP).find(([k]) =>
    ind.indicator?.toLowerCase().includes(k)
  )?.[1]

  return (
    <div
      className="rounded-lg border px-2.5 py-2 overflow-hidden"
      style={{ borderColor: color + '25', background: color + '08' }}
    >
      {/* Row 1: score + name + signal */}
      <div className="flex items-center gap-2 mb-1">
        <span
          className="flex-shrink-0 w-5 h-5 rounded text-[9px] font-bold flex items-center justify-center"
          style={{
            backgroundColor: score ? '#00D4AA18' : '#6b728018',
            color: score ? '#00D4AA' : '#6b7280',
            border: `1px solid ${score ? '#00D4AA35' : '#6b728035'}`,
          }}
        >
          {score ? '1' : '0'}
        </span>
        <span className="text-[11px] font-semibold text-text truncate flex-1 min-w-0">
          {ind.indicator}
        </span>
        <span className="text-[9px] font-medium flex-shrink-0" style={{ color }}>
          {ind.signal}
        </span>
      </div>
      {/* Row 2: raw value (if any) */}
      {rawVal && (
        <div className="text-[9px] font-mono px-1 py-0.5 mb-1 rounded truncate"
             style={{ backgroundColor: color + '12', color: color }}>
          {rawVal}
        </div>
      )}
      {/* Row 3: LLM reading - sanitize em dashes from API data */}
      <div className="text-[9px] text-muted leading-snug overflow-hidden"
           style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
        {(ind.reading || '').replace(/—/g, '-').replace(/–/g, '-')}
      </div>
    </div>
  )
}

function Section({ title, badge, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-xl border border-border overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/[0.02] transition-colors bg-dark/40"
      >
        <div className="flex items-center gap-2.5">
          <span className="text-[11px] font-semibold text-text">{title}</span>
          {badge}
        </div>
        <span className="text-muted text-sm w-4 text-center">{open ? '-' : '+'}</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="border-t border-border">
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function MantleSignals({ signals }) {
  if (!signals) return null
  const meth     = signals.meth_yield_signal || {}
  const dex      = signals.fusionx_dex_cvd   || {}
  const combined = signals.combined_direction || 'NEUTRAL'
  const combColor = combined.includes('BULL') ? '#00D4AA' : combined.includes('BEAR') ? '#FF6B6B' : '#FFD700'

  return (
    <Section title="Mantle Ecosystem Signals" defaultOpen>
      <div className="p-4 space-y-3">
        <div className="grid grid-cols-1 gap-3">
          {/* mETH */}
          <div className="rounded-lg p-3 bg-dark border border-border">
            <div className="text-[10px] text-muted font-medium mb-2">mETH Liquid Staking Yield Baseline</div>
            {meth.available ? (
              <div className="space-y-1.5">
                <div className={`text-xs font-bold ${meth.signal === 'BULLISH' ? 'text-mantle' : meth.signal === 'BEARISH' ? 'text-bear' : 'text-caution'}`}>
                  {meth.signal}
                </div>
                <div className="text-[10px] text-muted leading-snug">{meth.reasoning}</div>
                <div className="flex gap-4 text-[10px] pt-1">
                  <span className="text-muted">mETH APY: <span className="text-text font-mono">{meth.meth_apy_pct}%</span></span>
                  <span className="text-muted">ETH Funding: <span className="text-text font-mono">{meth.eth_funding_annualized_pct}%</span></span>
                  <span className="text-muted">Edge: <span className="font-mono font-bold" style={{ color: (meth.carry_edge_bps||0) > 0 ? '#00D4AA' : '#FF6B6B' }}>
                    {(meth.carry_edge_bps||0) > 0 ? '+' : ''}{meth.carry_edge_bps} bps
                  </span></span>
                </div>
                <div className="text-[9px] text-muted/50">Source: {meth.data_source}</div>
              </div>
            ) : (
              <div className="text-[10px] text-muted">{meth.reason || 'Unavailable'}</div>
            )}
          </div>

          {/* FusionX DEX CVD */}
          <div className="rounded-lg p-3 bg-dark border border-border">
            <div className="text-[10px] text-muted font-medium mb-2">{dex.dex || 'FusionX'} DEX CVD - Mantle On-Chain</div>
            {dex.available ? (
              <div className="space-y-1.5">
                <div className={`text-xs font-bold ${dex.direction === 'rising' ? 'text-mantle' : dex.direction === 'falling' ? 'text-bear' : 'text-muted'}`}>
                  {dex.direction?.toUpperCase()}
                </div>
                <div className="text-[10px] text-muted leading-snug">{dex.interpretation}</div>
                <div className="flex gap-4 text-[10px] pt-1">
                  <span className="text-muted">Net: <span className="font-mono text-text">{dex.cvd_delta?.toFixed(2)} {dex.pair?.split('/')[0]}</span></span>
                  <span className="text-muted">Swaps: <span className="font-mono text-text">{dex.swap_count}</span></span>
                </div>
              </div>
            ) : (
              <div className="text-[10px] text-muted">{dex.reason || 'Unavailable'}</div>
            )}
          </div>
        </div>

        <div className="rounded-lg p-2.5 text-center" style={{ background: combColor + '10', border: `1px solid ${combColor}25` }}>
          <div className="text-[10px] font-bold" style={{ color: combColor }}>Combined: {combined}</div>
          <div className="text-[9px] text-muted mt-0.5">{signals.combined_note}</div>
        </div>
      </div>
    </Section>
  )
}

// Strip em/en dashes from any LLM-generated string — they appear in API responses
function c(s) {
  if (!s) return s
  return String(s).replace(/—/g, '-').replace(/–/g, '-')
}

export default function AnalysisPanel({ symbol, compact = false }) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.analysis(symbol)
      .then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [symbol])

  if (loading) return (
    <div className="space-y-3 p-1">
      {[...Array(4)].map((_, i) => (
        <div key={i} className="rounded-xl h-20 bg-border/20 animate-pulse" />
      ))}
    </div>
  )

  if (!data) return (
    <div className="rounded-xl p-10 text-center border border-border bg-dark/40">
      <div className="text-3xl mb-3">📡</div>
      <div className="text-sm text-muted">No analysis for {symbol} yet.</div>
      <div className="text-[10px] text-muted/60 mt-2">Run the pipeline to generate one.</div>
    </div>
  )

  const analysis   = data.analysis || {}
  const macro      = data.macro_regime || {}
  const trace      = data.decision_tree_trace || {}
  const verdict    = analysis.verdict || 'NEUTRAL'
  const vMeta      = VERDICT_META[verdict] || VERDICT_META.NEUTRAL
  const rMeta      = REGIME_META[macro.macro_regime] || REGIME_META.TRANSITION
  const indicators = analysis.indicator_scores || []
  const confluence = analysis.confluence_count || 0
  const meetsMin   = analysis.meets_minimum
  const ts         = data.timestamp_utc

  const confPct   = Math.round((confluence / 12) * 100)
  const confColor = confPct >= 67 ? '#00D4AA' : confPct >= 42 ? '#FFD700' : '#FF6B6B'

  const treeSteps = Object.entries(trace).filter(([k]) => !['range_shortcut','error'].includes(k))

  return (
    <div className="space-y-3">

      {/* Verdict + Stats bar */}
      <div className="rounded-xl border border-border bg-dark/40 p-4">
        <div className="flex flex-wrap items-center gap-3 mb-4">
          {/* Verdict */}
          <div
            className="flex items-center gap-3 px-4 py-2.5 rounded-lg flex-shrink-0"
            style={{ background: vMeta.bg, border: `1px solid ${vMeta.border}` }}
          >
            <div>
              <div className="text-[9px] text-muted uppercase tracking-wider">Verdict</div>
              <div className="text-base font-bold leading-tight" style={{ color: vMeta.color }}>{vMeta.label}</div>
            </div>
          </div>

          {/* Regime */}
          <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-dark border border-border flex-shrink-0">
            <div>
              <div className="text-[9px] text-muted">Regime</div>
              <div className="text-sm font-bold" style={{ color: rMeta.color }}>{rMeta.label}</div>
            </div>
          </div>

          {/* CVD state */}
          {data.cvd_matrix_state && (() => {
            const cm = CVD_META[data.cvd_matrix_state] || {}
            return (
              <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-dark border border-border min-w-0 flex-shrink-0">
                <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: cm.color }} />
                <div>
                  <div className="text-[9px] text-muted">CVD Matrix</div>
                  <div className="text-[11px] font-bold text-text whitespace-nowrap">{cm.label}</div>
                </div>
              </div>
            )
          })()}

          {/* Time */}
          <div className="ml-auto text-right flex-shrink-0">
            <div className="font-mono text-lg font-bold text-text">{fmtPrice(data.current_price)}</div>
            <div className="text-[9px] text-muted">{data.session} session - {timeAgo(ts)}</div>
          </div>
        </div>

        {/* Confluence bar */}
        <div>
          <div className="flex justify-between text-[10px] mb-1.5">
            <span className="text-muted">Confluence Score</span>
            <span className="font-bold" style={{ color: confColor }}>{confluence}/12 - {analysis.confidence_score || 0}% confidence</span>
          </div>
          <div className="h-2 bg-border rounded-full overflow-hidden">
            <motion.div
              className="h-full rounded-full"
              style={{ backgroundColor: confColor }}
              initial={{ width: 0 }}
              animate={{ width: `${confPct}%` }}
              transition={{ duration: 0.6 }}
            />
          </div>
          {!meetsMin && (
            <div className="text-[9px] text-bear mt-1">Below minimum confluence for this scenario - NO_TRADE recommended</div>
          )}
        </div>
      </div>

      {/* Decision Tree */}
      <Section
        title={`Decision Tree - S${data.scenario_number}: ${data.scenario_name?.replace(/_/g,' ')}`}
        defaultOpen
      >
        <div className="p-4 space-y-2">
          {trace.range_shortcut && (
            <div className="text-[10px] text-mantle bg-mantle/5 rounded-lg p-2.5 border border-mantle/20">
              Range shortcut: price near VWMA with flat CVD - S8 Ranging Consolidation
            </div>
          )}
          {trace.error && (
            <div className="text-[10px] text-bear bg-bear/5 rounded-lg p-2.5 border border-bear/20">
              Insufficient data for decision tree
            </div>
          )}
          {treeSteps.map(([key, value], i) => (
            <div key={key} className="flex items-center gap-3 text-[10px]">
              <div
                className="w-4 h-4 rounded flex items-center justify-center text-[8px] font-bold flex-shrink-0"
                style={{
                  backgroundColor: value ? '#00D4AA15' : '#6b728015',
                  color: value ? '#00D4AA' : '#6b7280',
                  border: `1px solid ${value ? '#00D4AA30' : '#6b728030'}`,
                }}
              >
                {value ? 'Y' : 'N'}
              </div>
              <span className={value ? 'text-text' : 'text-muted'}>
                {key.replace(/_/g, ' ').toLowerCase()}
              </span>
            </div>
          ))}
          <div className="mt-2 pt-2 border-t border-border text-[11px] font-bold text-mantle">
            Result: S{data.scenario_number} - {data.scenario_name?.replace(/_/g,' ')}
          </div>
          {data.base_direction && (
            <div className="text-[9px] text-muted">
              Base direction: <span className="text-text font-medium">{data.base_direction}</span> (set by tree, LLM cannot override)
            </div>
          )}
        </div>
      </Section>

      {/* 12-Indicator Scorecard - vertical list */}
      <Section
        title="12-Indicator Confluence Scorecard"
        badge={
          <span
            className={`text-[9px] px-2 py-0.5 rounded font-bold ${meetsMin ? 'bg-mantle/15 text-mantle' : 'bg-bear/15 text-bear'}`}
          >
            {confluence}/12 {meetsMin ? 'meets min' : 'below min'}
          </span>
        }
        defaultOpen
      >
        <div className="p-3">
          {/* Column headers */}
          <div className="flex items-center gap-3 px-3 mb-2 text-[9px] text-muted/60 uppercase tracking-wider">
            <div className="w-6 flex-shrink-0 text-center">Pt</div>
            <div className="w-32 flex-shrink-0">Indicator</div>
            <div className="w-28 flex-shrink-0">Raw Value</div>
            <div className="flex-1">LLM Reading</div>
          </div>
          {indicators.length > 0 ? (
            <div className="grid grid-cols-2 gap-1.5">
              {indicators.map((ind, i) => (
                <IndicatorRow key={i} ind={ind} rawValues={data.snapshot_raw} />
              ))}
            </div>
          ) : (
            <div className="text-[10px] text-muted px-3 py-4">No indicator scores available</div>
          )}
          {/* Score tally */}
          <div className="mt-3 pt-2 border-t border-border flex items-center justify-between px-1">
            <div className="flex gap-2">
              <span className="text-[10px] text-mantle font-bold">{confluence} bullish</span>
              <span className="text-[10px] text-muted">/</span>
              <span className="text-[10px] text-bear">{12 - confluence} not</span>
            </div>
            <div className="text-[9px] text-muted">Min required: {data.macro_regime?.min_confluences || 5}</div>
          </div>
        </div>
      </Section>

      {/* Risk Plan */}
      <div className="rounded-xl border border-border bg-dark/40 p-4">
        <div className="text-[11px] font-semibold text-text mb-3">Risk Plan</div>
        <div className="grid grid-cols-2 gap-2 mb-3">
          {[
            { label: 'Stop Loss',  value: fmtPrice(analysis.stop_price) },
            { label: 'Target 1',   value: fmtPrice(analysis.target_1) },
            { label: 'Leverage',   value: analysis.leverage_recommended ? `${analysis.leverage_recommended}x` : '-' },
            { label: 'Account Risk', value: analysis.risk_per_trade_usd ? `$${analysis.risk_per_trade_usd}` : '$100' },
          ].map((item, i) => (
            <div key={i} className="rounded-lg p-2.5 bg-dark border border-border">
              <div className="text-[9px] text-muted mb-1">{item.label}</div>
              <div className="text-[11px] font-mono font-bold text-text">{item.value || '-'}</div>
            </div>
          ))}
        </div>
        {analysis.entry_trigger && (
          <div className="rounded-lg p-2.5 bg-dark border border-border">
            <div className="text-[9px] text-muted mb-1">Entry Trigger</div>
            <div className="text-[10px] text-text leading-snug">{c(analysis.entry_trigger)}</div>
          </div>
        )}
      </div>

      {/* Pre-Trade Commitment */}
      <Section title="Pre-Trade Commitment Note" defaultOpen>
        <div className="p-4 space-y-2">
          <div className="text-[9px] text-muted italic mb-2">
            Locked before entry. The system commits to these conditions before the recommendation is shown.
          </div>
          {[
            { label: 'Why this works',       value: c(analysis.pre_trade_note_why),   color: '#00D4AA' },
            { label: 'What proves me wrong', value: c(analysis.pre_trade_note_wrong), color: '#FF6B6B' },
            { label: 'When to add size',     value: c(analysis.pre_trade_note_add),   color: '#FFD700' },
          ].map((item, i) => (
            <div key={i} className="rounded-lg p-3 bg-dark border border-border">
              <div className="text-[9px] font-bold mb-1.5" style={{ color: item.color }}>{item.label}</div>
              <div className="text-[10px] text-text/80 leading-relaxed">{item.value || '-'}</div>
            </div>
          ))}
        </div>
      </Section>

      {/* Bull / Bear Debate */}
      <Section title="Bull / Bear Agent Debate">
        <div className="p-4 grid grid-cols-1 gap-3">
          <div className="rounded-lg p-3 bg-mantle/5 border border-mantle/15">
            <div className="text-[9px] font-bold text-mantle mb-2">BULL CASE</div>
            <div className="text-[10px] text-text/80 leading-relaxed">{c(analysis.bull_case)}</div>
          </div>
          <div className="rounded-lg p-3 bg-bear/5 border border-bear/15">
            <div className="text-[9px] font-bold text-bear mb-2">BEAR CASE</div>
            <div className="text-[10px] text-text/80 leading-relaxed">{c(analysis.bear_case)}</div>
          </div>
        </div>
      </Section>

      {/* Mantle Ecosystem */}
      {data.mantle_signals && <MantleSignals signals={data.mantle_signals} />}

      {/* Playbook rules */}
      <Section title="Playbook Rules Cited">
        <div className="p-4 space-y-1.5">
          {(analysis.playbook_rules_cited || []).map((rule, i) => (
            <div key={i} className="flex items-start gap-2 text-[10px] text-muted">
              <span className="text-mantle flex-shrink-0 mt-0.5">+</span>
              <span>{c(rule)}</span>
            </div>
          ))}
          {analysis.failure_mode && (
            <div className="mt-3 pt-3 border-t border-border text-[10px] text-muted italic">
              Failure mode: {c(analysis.failure_mode)}
            </div>
          )}
        </div>
      </Section>

    </div>
  )
}
