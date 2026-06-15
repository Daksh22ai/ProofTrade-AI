export const SYMBOLS = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','MNTUSDT']

export const VERDICT_META = {
  STRONG_LONG:  { label: 'STRONG LONG',  color: '#00D4AA', bg: 'rgba(0,212,170,0.1)',  border: 'rgba(0,212,170,0.3)',  icon: 'LL' },
  LONG:         { label: 'LONG',          color: '#00CC88', bg: 'rgba(0,204,136,0.08)', border: 'rgba(0,204,136,0.25)', icon: 'L'  },
  NEUTRAL:      { label: 'NEUTRAL',       color: '#6b7280', bg: 'rgba(107,114,128,0.1)',border: 'rgba(107,114,128,0.2)',icon: 'N'  },
  NO_TRADE:     { label: 'NO TRADE',      color: '#FFD700', bg: 'rgba(255,215,0,0.08)', border: 'rgba(255,215,0,0.25)', icon: 'X'  },
  SHORT:        { label: 'SHORT',         color: '#FF4444', bg: 'rgba(255,68,68,0.08)', border: 'rgba(255,68,68,0.25)', icon: 'S'  },
  STRONG_SHORT: { label: 'STRONG SHORT',  color: '#FF6B6B', bg: 'rgba(255,107,107,0.1)',border: 'rgba(255,107,107,0.3)',icon: 'SS' },
}

export const REGIME_META = {
  BULL:       { color: '#00D4AA', label: 'BULL',       icon: 'B' },
  BEAR:       { color: '#FF6B6B', label: 'BEAR',       icon: 'S' },
  TRANSITION: { color: '#FFD700', label: 'TRANSITION', icon: 'T' },
}

export const CVD_META = {
  BOTH_RISING:        { color: '#00D4AA', label: 'BOTH RISING',     desc: 'Real + leveraged money buying. Genuine accumulation.' },
  BOTH_FALLING:       { color: '#FF6B6B', label: 'BOTH FALLING',    desc: 'Real + leveraged money selling. Genuine distribution.' },
  BOTH_FLAT:          { color: '#6b7280', label: 'BOTH FLAT',       desc: 'No directional conviction in either market. Wait for breakout.' },
  FUT_UP_SPOT_FLAT:   { color: '#FFD700', label: 'FUT + SPOT FLAT', desc: 'Speculative pump without real money. Trap likely.' },
  FUT_DOWN_SPOT_FLAT: { color: '#60A5FA', label: 'FUT - SPOT FLAT', desc: 'Smart money accumulating quietly. Reversal incoming.' },
}

export function fmtPrice(p) {
  if (!p) return '-'
  if (p > 1000) return '$' + p.toLocaleString('en-US', { maximumFractionDigits: 0 })
  if (p > 1)    return '$' + p.toFixed(3)
  return '$' + p.toFixed(5)
}

export function fmtPct(v) {
  if (v == null) return '-'
  return (v > 0 ? '+' : '') + (v * 100).toFixed(4) + '%'
}

export function timeAgo(ts) {
  if (!ts) return ''
  const diff = Date.now() - new Date(ts).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  return `${Math.floor(m/60)}h ago`
}

export function shortAddr(addr) {
  if (!addr) return ''
  return addr.slice(0, 6) + '...' + addr.slice(-4)
}
