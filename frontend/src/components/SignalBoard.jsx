import { motion } from 'framer-motion'
import { SYMBOLS, VERDICT_META, fmtPrice, timeAgo } from '../utils.js'

const CONTAINER = {
  hidden: {},
  show: { transition: { staggerChildren: 0.07 } }
}
const CARD = {
  hidden: { opacity: 0, y: 24, scale: 0.96 },
  show:   { opacity: 1, y: 0,  scale: 1, transition: { type: 'spring', stiffness: 200, damping: 20 } }
}

function ConfluenceBar({ value, max = 12 }) {
  const pct = Math.round((value / max) * 100)
  const color = pct >= 75 ? '#00D4AA' : pct >= 50 ? '#FFD700' : '#FF6B6B'
  return (
    <div className="mt-2">
      <div className="flex justify-between text-[10px] text-muted mb-1">
        <span>Confluence</span>
        <span style={{ color }}>{value}/{max}</span>
      </div>
      <div className="h-1 rounded-full bg-border overflow-hidden">
        <motion.div
          className="h-full rounded-full"
          style={{ backgroundColor: color }}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, delay: 0.2 }}
        />
      </div>
    </div>
  )
}

function SignalCard({ sym, data, selected, onClick }) {
  const verdict = data?.verdict
  const meta    = VERDICT_META[verdict] || VERDICT_META.NEUTRAL
  const price   = data?.current_price
  const conf    = data?.confidence || 0
  const confluence = data?.confluence || 0
  const regime  = data?.macro_regime
  const ts      = data?.timestamp_utc

  return (
    <motion.div
      variants={CARD}
      onClick={onClick}
      className={`
        relative cursor-pointer rounded-xl p-4 transition-all duration-200 select-none
        ${selected
          ? 'mantle-glow'
          : 'glass glass-hover'
        }
      `}
      style={{
        border: selected
          ? `1px solid ${meta.color}66`
          : `1px solid ${verdict ? meta.border : 'rgba(26,39,68,1)'}`,
        background: selected ? meta.bg : undefined,
      }}
      whileHover={{ scale: 1.02, transition: { duration: 0.15 } }}
      whileTap={{ scale: 0.99 }}
    >
      {/* Selected indicator */}
      {selected && (
        <motion.div
          layoutId="selected-card"
          className="absolute inset-0 rounded-xl pointer-events-none"
          style={{ boxShadow: `0 0 0 1.5px ${meta.color}` }}
        />
      )}

      {/* Symbol + regime */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="font-bold text-sm text-text">{sym.replace('USDT','')}</div>
          <div className="text-[10px] text-muted">/USDT</div>
        </div>
        {regime && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
            regime === 'BULL'       ? 'bg-mantle/10 text-mantle' :
            regime === 'BEAR'       ? 'bg-bear/10 text-bear' :
            'bg-caution/10 text-caution'
          }`}>
            {regime}
          </span>
        )}
      </div>

      {/* Price */}
      <div className="font-mono text-base font-bold text-text mb-2">
        {price ? fmtPrice(price) : '-'}
      </div>

      {/* Verdict badge */}
      {verdict ? (
        <div
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-bold"
          style={{ backgroundColor: meta.bg, color: meta.color, border: `1px solid ${meta.border}` }}
        >
          <span>{meta.icon}</span>
          <span>{meta.label}</span>
        </div>
      ) : (
        <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs text-muted glass">
          No data
        </div>
      )}

      {/* Confidence */}
      {verdict && (
        <>
          <ConfluenceBar value={confluence} />
          <div className="flex justify-between mt-2 text-[10px] text-muted">
            <span>Confidence</span>
            <span style={{ color: conf >= 70 ? '#00D4AA' : conf >= 50 ? '#FFD700' : '#FF6B6B' }}>
              {conf}%
            </span>
          </div>
        </>
      )}

      {/* Timestamp */}
      {ts && (
        <div className="mt-2 text-[10px] text-muted/60 text-right">
          {timeAgo(ts)}
        </div>
      )}
    </motion.div>
  )
}

export default function SignalBoard({ signals, loading, selected, onSelect }) {
  return (
    <section>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-muted uppercase tracking-wider">
          📡 Live Signal Board
        </h2>
        <div className="flex items-center gap-2 text-xs text-muted">
          <span className="w-1.5 h-1.5 rounded-full bg-mantle animate-pulse" />
          <span>Auto-updating via SSE</span>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {SYMBOLS.map(sym => (
            <div key={sym} className="glass rounded-xl h-40 animate-pulse" />
          ))}
        </div>
      ) : (
        <motion.div
          variants={CONTAINER}
          initial="hidden"
          animate="show"
          className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3"
        >
          {SYMBOLS.map(sym => (
            <SignalCard
              key={sym}
              sym={sym}
              data={signals[sym]}
              selected={selected === sym}
              onClick={() => onSelect(sym)}
            />
          ))}
        </motion.div>
      )}
    </section>
  )
}
