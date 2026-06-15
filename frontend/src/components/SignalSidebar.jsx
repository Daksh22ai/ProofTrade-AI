import { motion } from 'framer-motion'
import { SYMBOLS, VERDICT_META, fmtPrice, timeAgo } from '../utils.js'

function SidebarCard({ sym, data, selected, onClick }) {
  const verdict = data?.verdict
  const meta    = VERDICT_META[verdict] || VERDICT_META.NEUTRAL
  const price   = data?.current_price
  const conf    = data?.confidence || 0
  const confluence = data?.confluence || 0
  const regime  = data?.macro_regime
  const ts      = data?.timestamp_utc

  const confPct   = Math.round((confluence / 12) * 100)
  const confColor = confPct >= 67 ? '#00D4AA' : confPct >= 42 ? '#FFD700' : '#FF6B6B'

  return (
    <motion.button
      onClick={onClick}
      whileTap={{ scale: 0.98 }}
      className={`w-full text-left px-3 py-3 border-b border-border transition-all duration-150 ${
        selected ? 'bg-mantle/5 border-l-2 border-l-mantle' : 'hover:bg-white/[0.02] border-l-2 border-l-transparent'
      }`}
    >
      {/* Row 1: symbol + regime */}
      <div className="flex items-center justify-between mb-1">
        <span className="font-bold text-sm text-text">{sym.replace('USDT', '')}<span className="text-muted font-normal text-[10px]">/USDT</span></span>
        {regime && (
          <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold ${
            regime === 'BULL' ? 'bg-mantle/15 text-mantle' :
            regime === 'BEAR' ? 'bg-bear/15 text-bear' :
            'bg-caution/15 text-caution'
          }`}>
            {regime}
          </span>
        )}
      </div>

      {/* Row 2: price */}
      <div className="font-mono text-sm font-semibold text-text mb-1.5">
        {price ? fmtPrice(price) : '-'}
      </div>

      {/* Row 3: verdict */}
      {verdict ? (
        <div
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold mb-1.5"
          style={{ backgroundColor: meta.bg, color: meta.color, border: `1px solid ${meta.border}` }}
        >
          {meta.label}
        </div>
      ) : (
        <div className="text-[10px] text-muted mb-1.5">No analysis yet</div>
      )}

      {/* Row 4: confluence bar */}
      {confluence > 0 && (
        <div>
          <div className="h-1 bg-border rounded-full overflow-hidden">
            <motion.div
              className="h-full rounded-full"
              style={{ backgroundColor: confColor, width: `${confPct}%` }}
              initial={{ width: 0 }}
              animate={{ width: `${confPct}%` }}
              transition={{ duration: 0.5 }}
            />
          </div>
          <div className="flex justify-between mt-0.5 text-[9px] text-muted">
            <span>{confluence}/12 conf</span>
            <span style={{ color: conf >= 65 ? '#00D4AA' : conf >= 45 ? '#FFD700' : '#FF6B6B' }}>
              {conf}%
            </span>
          </div>
        </div>
      )}

      {ts && (
        <div className="text-[9px] text-muted/50 text-right mt-0.5">{timeAgo(ts)}</div>
      )}
    </motion.button>
  )
}

export default function SignalSidebar({ signals, loading, selected, onSelect }) {
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-3 py-2.5 border-b border-border flex items-center justify-between">
        <div className="text-[10px] text-muted uppercase tracking-widest font-semibold">Signal Board</div>
        <div className="flex items-center gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-mantle animate-pulse" />
          <span className="text-[9px] text-muted">Live</span>
        </div>
      </div>

      {/* Signal cards */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="p-3 space-y-2">
            {SYMBOLS.map(s => (
              <div key={s} className="h-20 rounded bg-border/30 animate-pulse" />
            ))}
          </div>
        ) : (
          <div>
            {SYMBOLS.map(sym => (
              <SidebarCard
                key={sym}
                sym={sym}
                data={signals[sym]}
                selected={selected === sym}
                onClick={() => onSelect(sym)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Bottom: mobile symbol selector fallback */}
      <div className="border-t border-border p-2 text-[9px] text-muted text-center">
        6 symbols - real-time
      </div>
    </div>
  )
}
