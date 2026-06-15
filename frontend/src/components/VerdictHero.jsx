import { motion } from 'framer-motion'
import { VERDICT_META, REGIME_META, CVD_META, fmtPrice, timeAgo } from '../utils.js'

export default function VerdictHero({ symbol, signal, deployment }) {
  const verdict    = signal?.verdict
  const regime     = signal?.macro_regime
  const cvdState   = signal?.cvd_state
  const price      = signal?.current_price
  const conf       = signal?.confidence || 0
  const confluence = signal?.confluence || 0
  const scenario   = signal?.scenario_name || ''
  const session    = signal?.session || ''
  const ts         = signal?.timestamp_utc
  const txHash     = signal?.audit_tx_hash

  const vMeta  = VERDICT_META[verdict] || VERDICT_META.NEUTRAL
  const rMeta  = REGIME_META[regime]   || REGIME_META.TRANSITION
  const cvdM   = CVD_META[cvdState]    || {}

  const confPct   = Math.round((confluence / 12) * 100)
  const confColor = confPct >= 67 ? '#00D4AA' : confPct >= 42 ? '#FFD700' : '#FF6B6B'

  return (
    <div
      className="border-b border-border px-4 py-3 flex flex-wrap items-center gap-3"
      style={{
        background: verdict
          ? `linear-gradient(90deg, ${vMeta.color}08 0%, transparent 60%)`
          : 'transparent',
      }}
    >
      {/* Symbol + price */}
      <div className="flex items-baseline gap-2 min-w-[120px]">
        <span className="text-lg font-bold text-text">{symbol.replace('USDT', '')}</span>
        <span className="text-xs text-muted">/USDT</span>
        <span className="font-mono font-bold text-text ml-1">
          {price ? fmtPrice(price) : '-'}
        </span>
      </div>

      {/* Divider */}
      <div className="w-px h-8 bg-border hidden sm:block" />

      {/* Verdict */}
      {verdict ? (
        <motion.div
          key={verdict}
          initial={{ scale: 0.9, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg font-bold text-sm"
          style={{ backgroundColor: vMeta.bg, color: vMeta.color, border: `1px solid ${vMeta.border}` }}
        >
          <span>{vMeta.icon}</span>
          <span>{vMeta.label}</span>
        </motion.div>
      ) : (
        <div className="text-sm text-muted px-3 py-1.5 glass rounded-lg">Awaiting analysis</div>
      )}

      {/* Regime */}
      {regime && (
        <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-dark border border-border text-xs">
          <span>{rMeta.icon}</span>
          <span style={{ color: rMeta.color }} className="font-bold">{rMeta.label}</span>
          <span className="text-muted">regime</span>
        </div>
      )}

      {/* CVD Matrix state */}
      {cvdState && cvdM.label && (
        <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-dark border border-border text-xs">
          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: cvdM.color }} />
          <span className="font-bold text-text">{cvdM.label}</span>
        </div>
      )}

      {/* Confluence bar */}
      {confluence > 0 && (
        <div className="flex items-center gap-2 min-w-[120px]">
          <div className="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
            <motion.div
              className="h-full rounded-full"
              style={{ backgroundColor: confColor }}
              initial={{ width: 0 }}
              animate={{ width: `${confPct}%` }}
              transition={{ duration: 0.6 }}
            />
          </div>
          <span className="text-[10px] font-mono" style={{ color: confColor }}>
            {confluence}/12
          </span>
          <span className="text-[10px] text-muted">{conf}%</span>
        </div>
      )}

      {/* Session */}
      {session && (
        <div className={`text-[10px] px-2 py-1 rounded font-medium ${
          session === 'NY'          ? 'bg-blue-500/10 text-blue-400' :
          session === 'LONDON'      ? 'bg-purple-500/10 text-purple-400' :
          session === 'ASIAN'       ? 'bg-yellow-500/10 text-yellow-400' :
          'bg-border text-muted'
        }`}>
          {session}
        </div>
      )}

      {/* On-chain indicator */}
      {txHash && (
        <a
          href={`https://explorer.sepolia.mantle.xyz/tx/${txHash}`}
          target="_blank" rel="noreferrer"
          className="flex items-center gap-1 text-[10px] text-mantle hover:underline ml-auto"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-mantle" />
          On-chain verified
        </a>
      )}

      {ts && (
        <span className="text-[10px] text-muted ml-auto">{timeAgo(ts)}</span>
      )}
    </div>
  )
}
