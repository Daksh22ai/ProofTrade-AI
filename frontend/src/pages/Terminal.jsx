import { useState, useEffect, useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { api, subscribeSSE } from '../api.js'
import { SYMBOLS, VERDICT_META, REGIME_META, CVD_META, fmtPrice, timeAgo } from '../utils.js'
import ChartPanel from '../components/ChartPanel.jsx'
import MetricsStrip from '../components/MetricsStrip.jsx'
import AnalysisPanel from '../components/AnalysisPanel.jsx'
import AuditPanel from '../components/AuditPanel.jsx'
import GridBackground from '../components/GridBackground.jsx'

// ── Left sidebar: signal cards ────────────────────────────────────────────────

function SignalRow({ sym, data, selected, onClick }) {
  const verdict = data?.verdict
  const meta    = VERDICT_META[verdict] || {}
  const price   = data?.current_price
  const conf    = data?.confluence || 0
  const pct     = Math.round((conf / 12) * 100)
  const pctColor = pct >= 67 ? '#00D4AA' : pct >= 42 ? '#FFD700' : '#FF6B6B'

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 transition-all border-l-2 ${
        selected
          ? 'bg-mantle/5 border-l-mantle'
          : 'hover:bg-white/[0.02] border-l-transparent'
      }`}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="font-bold text-[11px] text-text">
          {sym.replace('USDT', '')}<span className="text-muted font-normal">/USDT</span>
        </span>
        {verdict && (
          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded"
                style={{ color: meta.color, backgroundColor: meta.bg, border: `1px solid ${meta.border}` }}>
            {meta.label}
          </span>
        )}
      </div>
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] text-muted">{price ? fmtPrice(price) : '-'}</span>
        {conf > 0 && (
          <div className="flex items-center gap-1">
            <div className="w-12 h-1 bg-border rounded-full overflow-hidden">
              <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: pctColor }} />
            </div>
            <span className="text-[9px]" style={{ color: pctColor }}>{conf}/12</span>
          </div>
        )}
      </div>
    </button>
  )
}

function Sidebar({ signals, loading, selected, onSelect, onBack }) {
  return (
    <div className="h-full flex flex-col border-r border-border bg-dark/60">
      {/* Logo — entire row navigates back to landing page */}
      <button
        onClick={onBack}
        title="Back to landing page"
        className="px-3 py-3 border-b border-border flex items-center gap-2 flex-shrink-0 w-full text-left hover:bg-white/[0.03] transition-colors group"
      >
        <img src="/logo.png" alt="Logo" className="w-6 h-6 rounded object-contain flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-[10px] font-bold text-text leading-tight group-hover:text-mantle transition-colors">AI Copilot</div>
          <div className="text-[8px] text-muted leading-tight">The Turing Test</div>
        </div>
        <span className="text-[8px] text-muted/40 group-hover:text-muted transition-colors flex-shrink-0">Home</span>
      </button>

      {/* Signal label */}
      <div className="px-3 py-2 border-b border-border/50 flex items-center gap-2 flex-shrink-0">
        <span className="text-[9px] text-muted uppercase tracking-widest font-semibold">Signals</span>
        <span className="w-1.5 h-1.5 rounded-full bg-mantle animate-pulse ml-auto" />
      </div>

      {/* Symbol list */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="p-2 space-y-1">
            {SYMBOLS.map(s => <div key={s} className="h-12 rounded bg-border/20 animate-pulse" />)}
          </div>
        ) : (
          SYMBOLS.map(sym => (
            <SignalRow
              key={sym}
              sym={sym}
              data={signals[sym]}
              selected={selected === sym}
              onClick={() => onSelect(sym)}
            />
          ))
        )}
      </div>

      {/* Bottom: data sources */}
      <div className="px-3 py-2 border-t border-border/50 flex-shrink-0 space-y-1">
        <div className="flex items-center gap-1.5 text-[8px] text-muted">
          <span className="w-1 h-1 rounded-full bg-green-400" />Bybit WS
        </div>
        <div className="flex items-center gap-1.5 text-[8px] text-muted">
          <span className="w-1 h-1 rounded-full bg-blue-400" />Binance aggTrades
        </div>
        <div className="flex items-center gap-1.5 text-[8px] text-muted">
          <span className="w-1 h-1 rounded-full bg-purple-400" />FusionX DEX (Mantle)
        </div>
      </div>
    </div>
  )
}

// ── Top verdict bar ───────────────────────────────────────────────────────────

function VerdictBar({ symbol, signal, deployment }) {
  const verdict  = signal?.verdict
  const regime   = signal?.macro_regime
  const cvdState = signal?.cvd_state
  const price    = signal?.current_price
  const conf     = signal?.confidence || 0
  const confl    = signal?.confluence || 0
  const session  = signal?.session
  const ts       = signal?.timestamp_utc
  const txHash   = signal?.audit_tx_hash

  const vMeta = VERDICT_META[verdict] || {}
  const rMeta = REGIME_META[regime]   || {}
  const cvdM  = CVD_META[cvdState]    || {}

  return (
    <div className="h-11 flex-shrink-0 flex items-center px-4 gap-3 border-b border-border overflow-x-auto"
         style={{ background: verdict ? `linear-gradient(90deg, ${vMeta.color || '#00D4AA'}06 0%, transparent 50%)` : 'transparent' }}>

      {/* Symbol + price */}
      <div className="flex items-baseline gap-1.5 flex-shrink-0">
        <span className="font-bold text-sm text-text">{symbol.replace('USDT','')}</span>
        <span className="text-[9px] text-muted">/USDT</span>
        <span className="font-mono font-semibold text-sm text-text ml-1">{price ? fmtPrice(price) : '-'}</span>
      </div>

      <div className="w-px h-6 bg-border flex-shrink-0" />

      {/* Verdict */}
      {verdict ? (
        <motion.div
          key={verdict}
          initial={{ scale: 0.85, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1 rounded-lg font-bold text-[11px]"
          style={{ backgroundColor: vMeta.bg, color: vMeta.color, border: `1px solid ${vMeta.border}` }}
        >
          <span>{vMeta.icon}</span>
          <span>{vMeta.label}</span>
        </motion.div>
      ) : (
        <span className="text-[10px] text-muted flex-shrink-0">Awaiting analysis</span>
      )}

      {/* Regime */}
      {regime && (
        <div className="flex-shrink-0 flex items-center gap-1 px-2 py-1 rounded bg-dark border border-border text-[10px]">
          <span>{rMeta.icon}</span>
          <span style={{ color: rMeta.color }} className="font-bold">{rMeta.label}</span>
        </div>
      )}

      {/* CVD state */}
      {cvdState && cvdM.label && (
        <div className="flex-shrink-0 flex items-center gap-1.5 px-2 py-1 rounded bg-dark border border-border text-[10px]">
          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: cvdM.color }} />
          <span className="font-bold text-text whitespace-nowrap">{cvdM.label}</span>
        </div>
      )}

      {/* Confluence */}
      {confl > 0 && (
        <div className="flex-shrink-0 flex items-center gap-1.5">
          <div className="w-20 h-1.5 bg-border rounded-full overflow-hidden">
            <div className="h-full rounded-full bg-mantle transition-all" style={{ width: `${Math.round(confl/12*100)}%` }} />
          </div>
          <span className="text-[9px] font-mono text-mantle">{confl}/12</span>
          <span className="text-[9px] text-muted">{conf}%</span>
        </div>
      )}

      {/* Session */}
      {session && (
        <div className={`flex-shrink-0 text-[9px] px-1.5 py-0.5 rounded font-medium ${
          session === 'NY'     ? 'bg-blue-500/10 text-blue-400' :
          session === 'LONDON' ? 'bg-purple-500/10 text-purple-400' :
          session === 'ASIAN'  ? 'bg-yellow-500/10 text-yellow-400' :
          'text-muted'
        }`}>
          {session}
        </div>
      )}

      <div className="flex-1" />

      {/* On-chain badge */}
      {txHash && (
        <a href={`https://explorer.sepolia.mantle.xyz/tx/${txHash}`}
           target="_blank" rel="noreferrer"
           className="flex-shrink-0 flex items-center gap-1 text-[9px] text-mantle hover:underline">
          <span className="w-1.5 h-1.5 rounded-full bg-mantle" />
          On-chain
        </a>
      )}

      {ts && <span className="flex-shrink-0 text-[9px] text-muted">{timeAgo(ts)}</span>}
    </div>
  )
}

// ── Search modal ──────────────────────────────────────────────────────────────

function SearchModal({ open, onClose, onSelect }) {
  const [q, setQ] = useState('')
  const ref = useRef(null)

  useEffect(() => {
    if (open) { setQ(''); setTimeout(() => ref.current?.focus(), 50) }
  }, [open])

  const results = SYMBOLS.filter(s =>
    s.toLowerCase().includes(q.toLowerCase()) ||
    s.replace('USDT','').toLowerCase().includes(q.toLowerCase())
  )

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-start justify-center pt-32"
          onClick={onClose}
        >
          <div className="absolute inset-0 bg-dark/80 backdrop-blur-sm" />
          <motion.div
            initial={{ y: -20, scale: 0.95 }}
            animate={{ y: 0, scale: 1 }}
            exit={{ y: -20, scale: 0.95 }}
            onClick={e => e.stopPropagation()}
            className="relative w-full max-w-md glass rounded-2xl border border-border overflow-hidden"
          >
            <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
              <span className="text-muted">🔍</span>
              <input
                ref={ref}
                value={q}
                onChange={e => setQ(e.target.value)}
                placeholder="Search symbol..."
                className="flex-1 bg-transparent text-text text-sm outline-none placeholder:text-muted"
              />
              <button onClick={onClose} className="text-muted hover:text-text text-xs">ESC</button>
            </div>
            <div className="py-2">
              {results.map(sym => (
                <button
                  key={sym}
                  onClick={() => { onSelect(sym); onClose() }}
                  className="w-full text-left px-4 py-2.5 hover:bg-white/[0.04] transition-all flex items-center gap-3"
                >
                  <span className="font-bold text-sm text-text">{sym.replace('USDT', '')}</span>
                  <span className="text-muted text-xs">/USDT</span>
                  {sym === 'MNTUSDT' && (
                    <span className="ml-auto text-[9px] px-1.5 py-0.5 rounded bg-mantle/10 text-mantle">
                      Mantle Native
                    </span>
                  )}
                </button>
              ))}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

// ── Right panel: analysis ─────────────────────────────────────────────────────

function RightPanel({ symbol, activeTab, setActiveTab }) {
  return (
    <div className="h-full flex flex-col border-l border-border">
      {/* Tab bar */}
      <div className="flex border-b border-border flex-shrink-0">
        {[
          { id: 'analysis', icon: '🤖', label: 'Analysis' },
          { id: 'audit',    icon: '⛓', label: 'Audit' },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-[10px] font-medium transition-all border-b-2 ${
              activeTab === tab.id
                ? 'text-mantle border-mantle bg-mantle/3'
                : 'text-muted border-transparent hover:text-text'
            }`}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-3">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab + symbol}
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -8 }}
            transition={{ duration: 0.15 }}
          >
            {activeTab === 'analysis' && <AnalysisPanel symbol={symbol} compact />}
            {activeTab === 'audit'    && <AuditPanel    symbol={symbol} compact />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}

// ── Top bar ───────────────────────────────────────────────────────────────────

function TopBar({ deployment, lastUpdate, onSearch, onRunPipeline, runningPipeline }) {
  const [clock, setClock] = useState(new Date())
  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="h-10 flex-shrink-0 flex items-center px-3 gap-3 border-b border-border bg-dark/80 backdrop-blur-sm">
      <div className="flex items-center gap-3 text-[9px] text-muted">
        <div className="flex items-center gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          <span>Live</span>
        </div>
        {deployment && (
          <a href={deployment.explorer_url} target="_blank" rel="noreferrer"
             className="hidden sm:flex items-center gap-1 hover:text-mantle transition-colors font-mono">
            <span className="w-1 h-1 rounded-full bg-mantle" />
            {deployment.address?.slice(0,10)}...
          </a>
        )}
      </div>

      <div className="flex-1" />

      <span className="font-mono text-[9px] text-muted hidden md:block">
        {clock.toUTCString().slice(0, 25)}
      </span>

      {lastUpdate && (
        <span className="text-[9px] text-muted hidden sm:block">
          {Math.round((Date.now() - lastUpdate) / 1000)}s ago
        </span>
      )}

      {/* Search icon */}
      <button
        onClick={onSearch}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded glass text-[9px] text-muted hover:text-text transition-all"
        title="Search symbol (⌘K)"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <span className="hidden sm:block">Search</span>
        <span className="text-[8px] text-muted/50 hidden md:block">⌘K</span>
      </button>

      <motion.button
        onClick={onRunPipeline}
        disabled={runningPipeline}
        whileTap={{ scale: 0.96 }}
        className={`px-3 py-1 rounded text-[9px] font-bold transition-all ${
          runningPipeline
            ? 'bg-mantle/20 text-mantle cursor-not-allowed'
            : 'bg-mantle text-dark hover:bg-mantle-dim'
        }`}
      >
        {runningPipeline ? '⏳ Analyzing...' : '▶ Run Analysis'}
      </motion.button>
    </div>
  )
}

// ── Main Terminal ─────────────────────────────────────────────────────────────

export default function Terminal({ onBack }) {
  const [signals,         setSignals]         = useState({})
  const [selected,        setSelected]        = useState('BTCUSDT')
  const [activeTab,       setActiveTab]       = useState('analysis')
  const [deployment,      setDeployment]      = useState(null)
  const [loading,         setLoading]         = useState(true)
  const [lastUpdate,      setLastUpdate]      = useState(null)
  const [runningPipeline, setRunningPipeline] = useState(false)
  const [searchOpen,      setSearchOpen]      = useState(false)

  const fetchSignals = useCallback(async () => {
    try {
      const data = await api.signals()
      setSignals(data)
      setLastUpdate(new Date())
    } catch (e) {
      console.warn('Signals fetch failed:', e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchSignals()
    api.deployment().then(setDeployment).catch(() => {})
    const poll  = setInterval(fetchSignals, 30_000)
    const unsub = subscribeSSE((event) => {
      if (event.type === 'analysis_update') {
        setSignals(prev => ({
          ...prev,
          [event.symbol]: {
            ...(prev[event.symbol] || {}),
            verdict:       event.verdict,
            confidence:    event.confidence,
            confluence:    event.confluence,
            current_price: event.price,
            timestamp_utc: event.timestamp,
            audit_tx_hash: event.tx_hash,
          }
        }))
        setLastUpdate(new Date())
      }
    })
    return () => { clearInterval(poll); unsub() }
  }, [fetchSignals])

  // Keyboard shortcut for search
  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setSearchOpen(v => !v)
      }
      if (e.key === 'Escape') setSearchOpen(false)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const runPipeline = async () => {
    setRunningPipeline(true)
    try { setTimeout(fetchSignals, 3000) }
    finally { setTimeout(() => setRunningPipeline(false), 8000) }
  }

  const selectedSignal = signals[selected] || {}

  return (
    <div className="h-screen flex flex-col bg-dark overflow-hidden">
      <GridBackground />
      <div className="relative z-10 flex flex-col h-full">

        {/* Top bar */}
        <TopBar
          deployment={deployment}
          lastUpdate={lastUpdate}
          onSearch={() => setSearchOpen(true)}
          onRunPipeline={runPipeline}
          runningPipeline={runningPipeline}
        />

        {/* 3-panel body */}
        <div className="flex flex-1 overflow-hidden">

          {/* LEFT: signal sidebar */}
          <div className="w-44 flex-shrink-0 hidden lg:block">
            <Sidebar
              signals={signals}
              loading={loading}
              selected={selected}
              onSelect={setSelected}
              onBack={onBack}
            />
          </div>

          {/* CENTER: charts + metrics */}
          <div className="flex-1 flex flex-col overflow-hidden min-w-0">

            {/* Verdict banner */}
            <VerdictBar
              symbol={selected}
              signal={selectedSignal}
              deployment={deployment}
            />

            {/* Scrollable chart area */}
            <div className="flex-1 overflow-y-auto p-3 space-y-3">
              <ChartPanel symbol={selected} />
              <MetricsStrip symbol={selected} />
            </div>

          </div>

          {/* RIGHT: analysis panel */}
          <div className="w-80 flex-shrink-0 hidden xl:block">
            <RightPanel
              symbol={selected}
              activeTab={activeTab}
              setActiveTab={setActiveTab}
            />
          </div>

        </div>

        {/* Mobile: tabs below charts */}
        <div className="xl:hidden border-t border-border flex-shrink-0">
          <div className="flex">
            {[
              { id: 'analysis', label: '🤖 Analysis' },
              { id: 'audit',    label: '⛓ Audit' },
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex-1 py-2 text-[10px] font-medium border-b-2 transition-all ${
                  activeTab === tab.id ? 'text-mantle border-mantle' : 'text-muted border-transparent'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="overflow-y-auto max-h-96 p-3">
            {activeTab === 'analysis' && <AnalysisPanel symbol={selected} />}
            {activeTab === 'audit'    && <AuditPanel    symbol={selected} />}
          </div>
        </div>

        {/* Footer strip */}
        <div className="h-6 flex-shrink-0 border-t border-border/50 flex items-center justify-between px-4 text-[8px] text-muted bg-dark/60">
          <span>Mantle AI Trading Copilot - The Turing Test</span>
          <span>Groq llama-3.3-70b · Bybit + Binance · Mantle Sepolia</span>
        </div>

      </div>

      {/* Search modal */}
      <SearchModal
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onSelect={(sym) => { setSelected(sym); setActiveTab('analysis') }}
      />
    </div>
  )
}
