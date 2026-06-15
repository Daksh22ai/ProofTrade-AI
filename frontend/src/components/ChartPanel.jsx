import { useState, useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { createChart, ColorType, CrosshairMode } from 'lightweight-charts'
import { api } from '../api.js'

// Base chart options - watermark disabled so no TradingView logo appears
const BASE_OPTS = {
  layout: {
    background: { type: ColorType.Solid, color: '#0A0E1A' },
    textColor:  '#6b7280',
    fontSize:   10,
  },
  grid: {
    vertLines: { color: 'rgba(26,39,68,0.5)' },
    horzLines: { color: 'rgba(26,39,68,0.5)' },
  },
  crosshair:       { mode: CrosshairMode.Normal },
  rightPriceScale: { borderColor: '#1a2744', minimumWidth: 60 },
  timeScale:       { borderColor: '#1a2744', timeVisible: true, secondsVisible: false, rightOffset: 3 },
  watermark:       { visible: false },   // removes TradingView logo
  handleScroll:    { vertTouchDrag: false },
}

// Overlay for empty charts while data is accumulating
function EmptyOverlay({ label, source }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center bg-dark/70 backdrop-blur-sm rounded">
      <div className="text-2xl mb-2">📡</div>
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className="text-[10px] text-muted/60 mt-1">Accumulating live data from {source}</div>
      <div className="mt-3 flex gap-1">
        {[0,1,2].map(i => (
          <motion.div
            key={i}
            className="w-1.5 h-1.5 rounded-full bg-mantle/50"
            animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.4 }}
          />
        ))}
      </div>
    </div>
  )
}

function CandleChart({ symbol, tf, height = 260 }) {
  const ref      = useRef(null)
  const chartRef = useRef(null)
  const csRef    = useRef(null)
  const [hasData, setHasData] = useState(false)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      ...BASE_OPTS,
      width:  ref.current.clientWidth,
      height,
    })
    chartRef.current = chart

    const cs = chart.addCandlestickSeries({
      upColor:         '#00D4AA',
      downColor:       '#FF6B6B',
      borderUpColor:   '#00D4AA',
      borderDownColor: '#FF6B6B',
      wickUpColor:     '#00D4AA45',
      wickDownColor:   '#FF6B6B45',
    })
    csRef.current = cs

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.resize(ref.current.clientWidth, height)
    })
    ro.observe(ref.current)
    return () => { ro.disconnect(); chart.remove() }
  }, [height])

  useEffect(() => {
    if (!csRef.current) return
    setHasData(false)
    api.chart(symbol, tf).then(d => {
      if (d.candles?.length >= 2) {
        csRef.current.setData(d.candles)
        setHasData(true)
      }
    }).catch(() => {})
  }, [symbol, tf])

  return (
    <div className="relative">
      <div ref={ref} className="chart-container w-full" style={{ height }} />
      {!hasData && <EmptyOverlay label={`${symbol} ${tf.toUpperCase()} candles`} source="QuestDB" />}
    </div>
  )
}

function CvdChart({ symbol, marketType, color, label, source, height = 120 }) {
  const ref       = useRef(null)
  const chartRef  = useRef(null)
  const seriesRef = useRef(null)
  const zeroRef   = useRef(null)
  const [hasData,   setHasData]   = useState(false)
  const [direction, setDirection] = useState('flat')
  const [lastVal,   setLastVal]   = useState(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      ...BASE_OPTS,
      width:  ref.current.clientWidth,
      height,
    })
    chartRef.current = chart

    const area = chart.addAreaSeries({
      lineColor:   color,
      topColor:    color + '30',
      bottomColor: color + '08',
      lineWidth:   1.5,
      priceLineVisible: false,
    })
    seriesRef.current = area

    // Zero baseline
    const zero = chart.addLineSeries({
      color:     '#1a2744',
      lineWidth: 1,
      priceLineVisible: false,
    })
    zeroRef.current = zero

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.resize(ref.current.clientWidth, height)
    })
    ro.observe(ref.current)
    return () => { ro.disconnect(); chart.remove() }
  }, [height, color])

  useEffect(() => {
    if (!seriesRef.current) return
    setHasData(false)
    api.cvd(symbol, marketType).then(d => {
      if (d.series?.length >= 2) {
        seriesRef.current.setData(d.series)
        // Zero line at first value
        const first = d.series[0].value
        const last  = d.series[d.series.length - 1]
        zeroRef.current?.setData(d.series.map(p => ({ time: p.time, value: first })))
        setDirection(last.value > first + 0.01 ? 'rising' : last.value < first - 0.01 ? 'falling' : 'flat')
        setLastVal(last.value)
        setHasData(true)
      }
    }).catch(() => {})
  }, [symbol, marketType])

  const dirColor  = direction === 'rising' ? '#00D4AA' : direction === 'falling' ? '#FF6B6B' : '#6b7280'
  const dirLabel  = direction === 'rising' ? 'Rising' : direction === 'falling' ? 'Falling' : 'Flat'
  const dirArrow  = direction === 'rising' ? '▲' : direction === 'falling' ? '▼' : '='

  return (
    <div className="relative">
      <div className="flex items-center justify-between mb-1.5 px-0.5">
        <div className="text-[10px] text-muted">{label}</div>
        {hasData && (
          <div className="flex items-center gap-2 text-[10px]">
            <span style={{ color: dirColor }} className="font-bold">{dirArrow} {dirLabel}</span>
            <span className="font-mono text-muted">{lastVal?.toFixed(2)}</span>
          </div>
        )}
      </div>
      <div className="relative">
        <div ref={ref} className="chart-container w-full" style={{ height }} />
        {!hasData && <EmptyOverlay label={`${marketType === 'spot' ? 'Spot' : 'Futures'} CVD`} source={source} />}
      </div>
    </div>
  )
}

const TF_OPTIONS = [
  { v: '1h', l: '1H' },
  { v: '4h', l: '4H' },
  { v: '1d', l: '1D' },
]

export default function ChartPanel({ symbol }) {
  const [tf, setTf] = useState('1h')

  return (
    <div className="space-y-3">

      {/* Price chart row */}
      <div className="glass rounded-xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold text-text">Price Chart</span>
            <span className="text-[10px] text-muted">Bybit OHLCV - 1min base, SAMPLE BY {tf}</span>
          </div>
          <div className="flex gap-1">
            {TF_OPTIONS.map(t => (
              <button
                key={t.v}
                onClick={() => setTf(t.v)}
                className={`px-2.5 py-1 rounded text-[10px] font-medium transition-all ${
                  tf === t.v ? 'bg-mantle text-dark' : 'text-muted hover:text-text glass'
                }`}
              >
                {t.l}
              </button>
            ))}
          </div>
        </div>
        <div className="p-3">
          <CandleChart symbol={symbol} tf={tf} height={250} />
        </div>
      </div>

      {/* CVD charts row - side by side */}
      <div className="grid grid-cols-2 gap-3">
        {/* Spot CVD */}
        <div className="glass rounded-xl overflow-hidden">
          <div className="px-4 py-2 border-b border-border flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-mantle" />
            <span className="text-[10px] font-semibold text-text">Spot CVD</span>
            <span className="text-[9px] text-muted ml-auto">
              {symbol === 'MNTUSDT' ? 'FusionX DEX (Mantle)' : 'Binance Spot'}
            </span>
          </div>
          <div className="p-3">
            <CvdChart
              symbol={symbol}
              marketType="spot"
              color="#00D4AA"
              label="4H rolling - real money flow"
              source={symbol === 'MNTUSDT' ? 'FusionX DEX (Mantle mainnet)' : 'Binance aggTrades'}
              height={110}
            />
          </div>
        </div>

        {/* Futures CVD */}
        <div className="glass rounded-xl overflow-hidden">
          <div className="px-4 py-2 border-b border-border flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-bear" />
            <span className="text-[10px] font-semibold text-text">Futures CVD</span>
            <span className="text-[9px] text-muted ml-auto">Bybit + Binance USDT-M</span>
          </div>
          <div className="p-3">
            <CvdChart
              symbol={symbol}
              marketType="futures"
              color="#FF6B6B"
              label="4H rolling - leveraged flow"
              source="Bybit Linear + Binance USDT-M"
              height={110}
            />
          </div>
        </div>
      </div>

    </div>
  )
}
