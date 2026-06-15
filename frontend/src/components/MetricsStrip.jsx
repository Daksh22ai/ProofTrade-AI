import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { api } from '../api.js'
import { fmtPct } from '../utils.js'

function MetricBox({ label, value, sub, color, badge, loading }) {
  return (
    <div className="glass rounded-xl px-4 py-3 flex flex-col gap-1 min-w-0">
      <div className="text-[10px] text-muted uppercase tracking-wider">{label}</div>
      <div className="flex items-baseline gap-2">
        <span
          className="font-mono font-bold text-sm truncate"
          style={{ color: color || '#C9D1D9' }}
        >
          {loading ? (
            <span className="inline-block w-16 h-4 bg-border/50 rounded animate-pulse" />
          ) : (value ?? '-')}
        </span>
        {badge && !loading && (
          <span
            className="text-[9px] px-1.5 py-0.5 rounded font-bold flex-shrink-0"
            style={{ backgroundColor: (color || '#6b7280') + '20', color: color || '#6b7280' }}
          >
            {badge}
          </span>
        )}
      </div>
      {sub && !loading && (
        <div className="text-[10px] text-muted truncate">{sub}</div>
      )}
    </div>
  )
}

function FundingGauge({ rate }) {
  if (rate == null) return <div className="text-[10px] text-muted">-</div>

  const pct  = rate * 100
  const bucket = Math.abs(rate) < 0.0002 ? 'neutral' :
                 rate > 0.001            ? 'extreme+' :
                 rate > 0.0005           ? 'high+' :
                 rate > 0                ? 'mod+' : 'negative'

  const color = rate > 0.0008  ? '#FF6B6B' :
                rate > 0.0003  ? '#FF8C00' :
                rate < -0.0003 ? '#00D4AA' :
                rate < 0       ? '#60A5FA' : '#FFD700'

  const badge = bucket === 'extreme+' ? '⚠ Extreme+' :
                bucket === 'high+'    ? 'High+' :
                bucket === 'negative' ? 'Negative' :
                'Normal'

  return { value: `${pct.toFixed(4)}%`, color, badge, sub: `per 8h - ${bucket}` }
}

function LSRBar({ buy, sell }) {
  if (buy == null) return null
  const buyPct  = (buy  * 100).toFixed(1)
  const sellPct = (sell * 100).toFixed(1)
  const isExLong  = buy > 0.70
  const isExShort = buy < 0.30

  return (
    <div>
      <div className="flex justify-between text-[10px] mb-1">
        <span style={{ color: '#00D4AA' }}>L {buyPct}%</span>
        <span style={{ color: '#FF6B6B' }}>S {sellPct}%</span>
      </div>
      <div className="h-2 rounded-full bg-border overflow-hidden flex">
        <motion.div
          className="h-full rounded-l-full"
          style={{ backgroundColor: '#00D4AA', width: `${buyPct}%` }}
          initial={{ width: 0 }}
          animate={{ width: `${buyPct}%` }}
          transition={{ duration: 0.5 }}
        />
        <motion.div
          className="h-full rounded-r-full"
          style={{ backgroundColor: '#FF6B6B', width: `${sellPct}%` }}
          initial={{ width: 0 }}
          animate={{ width: `${sellPct}%` }}
          transition={{ duration: 0.5 }}
        />
      </div>
      {(isExLong || isExShort) && (
        <div className="text-[9px] mt-0.5" style={{ color: isExLong ? '#FF6B6B' : '#00D4AA' }}>
          {isExLong ? 'Crowded Longs - Contrarian Bearish' : 'Crowded Shorts - Contrarian Bullish'}
        </div>
      )}
    </div>
  )
}

export default function MetricsStrip({ symbol }) {
  const [market,  setMarket]  = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.market(symbol).then(d => { setMarket(d); setLoading(false) }).catch(() => setLoading(false))
    const t = setInterval(() => api.market(symbol).then(setMarket).catch(() => {}), 30_000)
    return () => clearInterval(t)
  }, [symbol])

  const fr    = market?.funding_rate
  const fgauge = FundingGauge({ rate: fr })
  const liq   = market?.liq_usd_24h || 0
  const oi    = market?.open_interest

  const oiColor = market?.open_interest
    ? '#C9D1D9'
    : '#6b7280'

  const liqColor = liq > 50e6  ? '#FF6B6B' :
                   liq > 10e6  ? '#FF8C00' :
                   liq > 1e6   ? '#FFD700' : '#C9D1D9'

  return (
    <div className="space-y-2">
      {/* Label */}
      <div className="flex items-center gap-2">
        <div className="text-[10px] text-muted uppercase tracking-widest font-semibold">
          Market Metrics
        </div>
        <div className="text-[9px] text-muted/50">Bybit + Binance USDT-M - updates every 60s</div>
      </div>

      {/* Metric boxes */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">

        {/* Open Interest */}
        <MetricBox
          label="Open Interest"
          value={oi ? (oi >= 1e9 ? `$${(oi/1e9).toFixed(2)}B` : `$${(oi/1e6).toFixed(1)}M`) : null}
          sub="USDT-M futures total"
          color={oiColor}
          loading={loading}
        />

        {/* Funding Rate */}
        <MetricBox
          label="Funding Rate (8h)"
          value={fgauge?.value}
          sub={fgauge?.sub}
          color={fgauge?.color}
          badge={fgauge?.badge}
          loading={loading}
        />

        {/* 24H Liquidations */}
        <MetricBox
          label="Liquidations 24H"
          value={liq > 0 ? (liq >= 1e9 ? `$${(liq/1e9).toFixed(2)}B` : liq >= 1e6 ? `$${(liq/1e6).toFixed(1)}M` : `$${(liq/1e3).toFixed(0)}K`) : null}
          sub={`${market?.liq_count_24h || 0} cascade events`}
          color={liqColor}
          badge={liq > 50e6 ? '⚠ High Cascade' : liq > 10e6 ? 'Elevated' : null}
          loading={loading}
        />

        {/* Long/Short Ratio */}
        <div className="glass rounded-xl px-4 py-3">
          <div className="text-[10px] text-muted uppercase tracking-wider mb-2">Long/Short Ratio</div>
          {loading ? (
            <div className="h-4 bg-border/50 rounded animate-pulse" />
          ) : market?.lsr_buy != null ? (
            <LSRBar buy={market.lsr_buy} sell={market.lsr_sell} />
          ) : (
            <div className="text-[10px] text-muted">-</div>
          )}
        </div>
      </div>
    </div>
  )
}
