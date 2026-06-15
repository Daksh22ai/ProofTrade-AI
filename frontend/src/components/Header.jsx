import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'

export default function Header({ deployment, lastUpdate, onRefresh, onRunPipeline, runningPipeline }) {
  const [clock, setClock] = useState(new Date())

  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  const utcStr = clock.toUTCString().slice(0, 25)

  return (
    <header className="h-12 flex-shrink-0 glass border-b border-border flex items-center px-4 gap-4 z-50">

      {/* Logo */}
      <div className="flex items-center gap-2 flex-shrink-0">
        <div className="relative">
          <img src="/logo.png" alt="Logo" className="w-7 h-7 rounded-lg object-contain" />
          <span className="absolute -top-0.5 -right-0.5 flex h-2 w-2">
            <span className="pulse-ring" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-mantle" />
          </span>
        </div>
        <div className="hidden sm:block">
          <div className="text-xs font-bold text-text leading-tight">Mantle AI Copilot</div>
          <div className="text-[9px] text-muted leading-tight">The Turing Test</div>
        </div>
      </div>

      {/* Live status dots */}
      <div className="hidden md:flex items-center gap-4 text-[10px] text-muted">
        <div className="flex items-center gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-mantle animate-pulse" />
          <span>Bybit + Binance</span>
        </div>
        <div className="flex items-center gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse" />
          <span>Mantle FusionX</span>
        </div>
        {deployment && (
          <a
            href={deployment.explorer_url}
            target="_blank" rel="noreferrer"
            className="flex items-center gap-1 hover:text-mantle transition-colors"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
            <span className="font-mono">{deployment.address?.slice(0, 8)}...</span>
          </a>
        )}
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* UTC clock */}
      <span className="hidden lg:block font-mono text-[10px] text-muted">{utcStr}</span>

      {/* Last update */}
      {lastUpdate && (
        <span className="hidden md:block text-[10px] text-muted">
          {Math.round((Date.now() - lastUpdate) / 1000)}s ago
        </span>
      )}

      {/* Controls */}
      <button
        onClick={onRefresh}
        className="px-2.5 py-1 rounded glass text-[10px] text-muted hover:text-text transition-all"
      >
        ↻
      </button>

      <motion.button
        onClick={onRunPipeline}
        disabled={runningPipeline}
        whileTap={{ scale: 0.97 }}
        className={`px-3 py-1 rounded text-[10px] font-medium transition-all flex-shrink-0 ${
          runningPipeline
            ? 'bg-mantle/20 text-mantle cursor-not-allowed'
            : 'bg-mantle text-dark hover:bg-mantle-dim'
        }`}
      >
        {runningPipeline ? '⏳ Analyzing...' : '▶ Run Analysis'}
      </motion.button>

    </header>
  )
}
