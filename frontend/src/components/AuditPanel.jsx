import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import axios from 'axios'
import { api } from '../api.js'
import { SYMBOLS, shortAddr, timeAgo } from '../utils.js'

function HashDisplay({ hash }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(hash).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <div className="flex items-center gap-2 bg-dark rounded-lg px-3 py-2 border border-border font-mono text-xs">
      <span className="text-muted break-all flex-1">{hash}</span>
      <button onClick={copy} className="text-muted hover:text-mantle transition-colors flex-shrink-0">
        {copied ? '+' : 'copy'}
      </button>
    </div>
  )
}

function VerifyResult({ result }) {
  if (!result) return null
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className={`rounded-xl p-4 border ${result.match ? 'bg-mantle/5 border-mantle/30 text-mantle' : 'bg-bear/5 border-bear/30 text-bear'}`}
    >
      <div className="font-bold mb-2">
        {result.match ? 'Hash Verified' : 'Hash Mismatch'}
      </div>
      {result.match ? (
        <div className="text-xs opacity-80">
          Recomputed hash matches the on-chain record. This analysis was logged at block {result.audit_block} before any price movement.
        </div>
      ) : (
        <div className="text-xs opacity-80">
          Hash mismatch. Expected: {result.claimed?.slice(0,20)}...<br />
          Got: {result.recomputed?.slice(0,20)}...
        </div>
      )}
    </motion.div>
  )
}

export default function AuditPanel({ symbol, deployment }) {
  const [data,       setData]       = useState(null)
  const [verifying,  setVerifying]  = useState(false)
  const [verResult,  setVerResult]  = useState(null)
  const [allSignals, setAllSignals] = useState({})
  const [gateResult, setGateResult] = useState(null)
  const [gateLoading,setGateLoading]= useState(false)
  const [gateInput,  setGateInput]  = useState(10)

  useEffect(() => {
    api.analysis(symbol).then(setData).catch(() => {})
    api.signals().then(setAllSignals).catch(() => {})
    setGateResult(null)
  }, [symbol])

  const verify = async () => {
    setVerifying(true)
    setVerResult(null)
    try {
      const r = await api.verify(symbol)
      setVerResult(r)
    } catch (e) {
      setVerResult({ match: false, claimed: '', recomputed: 'Error: ' + e.message })
    } finally {
      setVerifying(false)
    }
  }

  const checkGate = async () => {
    setGateLoading(true)
    setGateResult(null)
    try {
      const r = await axios.get(`/api/position-check?symbol=${symbol}&leverage=${gateInput}&min_confidence=0`)
      setGateResult(r.data)
    } catch (e) {
      setGateResult({ allowed: false, reason: e.response?.data?.detail || e.message })
    } finally {
      setGateLoading(false)
    }
  }

  const txHash = data?.audit_tx_hash
  const expUrl = data?.audit_explorer_url
  const hash   = data?.data_hash
  const payload= data?.hash_payload_json

  return (
    <div className="space-y-5">

      {/* Hero */}
      <div className="glass rounded-xl p-5 border border-mantle/10">
        <div className="flex items-start gap-4">
          <div className="text-3xl flex-shrink-0">⛓</div>
          <div>
            <h2 className="text-sm font-bold text-text mb-2">
              Verifiable AI: On-Chain Audit Trail
            </h2>
            <p className="text-xs text-muted leading-relaxed mb-3">
              Every AI recommendation is{' '}
              <span className="text-mantle font-medium">keccak256-hashed</span> and permanently logged on{' '}
              <span className="text-mantle font-medium">Mantle Sepolia</span> before you see it.
              Block timestamp is cryptographic proof that the recommendation pre-dates any price movement.
            </p>
            <div className="flex flex-wrap gap-2 text-xs">
              {[
                'Cryptographic pre-trade proof',
                'Immutable on Mantle Sepolia',
                'Anyone can verify independently',
              ].map(item => (
                <span key={item} className="px-2 py-0.5 rounded-full bg-mantle/10 text-mantle border border-mantle/20">
                  {item}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Contract info — reads from live deployment.json, not hardcoded */}
      {deployment && (
        <div className="glass rounded-xl p-4">
          <div className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-3">
            On-Chain Contracts (Mantle Sepolia)
          </div>
          <div className="space-y-2 text-[10px] mb-3">
            <div className="flex items-start gap-2">
              <span className="text-muted w-28 flex-shrink-0 flex-shrink-0">AuditLog.sol</span>
              <a href={deployment.explorer_url} target="_blank" rel="noreferrer"
                 className="font-mono text-mantle hover:underline break-all">
                {deployment.address}
              </a>
            </div>
            {deployment.strategy_gate_address && (
              <div className="flex items-start gap-2">
                <span className="text-muted w-28 flex-shrink-0">StrategyGate.sol</span>
                <a href={deployment.gate_explorer_url} target="_blank" rel="noreferrer"
                   className="font-mono text-mantle hover:underline break-all">
                  {deployment.strategy_gate_address}
                </a>
              </div>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
            <div>
              <div className="text-muted mb-1">Network</div>
              <div className="text-text">Mantle Sepolia (chainId 5003)</div>
            </div>
          </div>
        </div>
      )}

      {/* All-symbols tx feed */}
      <div className="glass rounded-xl p-4">
        <div className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-3">
          On-Chain Transactions
        </div>
        <div className="space-y-1.5">
          {SYMBOLS.map(sym => {
            const s = allSignals[sym]
            if (!s?.audit_tx_hash) return (
              <div key={sym} className="flex items-center gap-3 py-1.5 text-[10px] text-muted border-b border-border/50">
                <span className="w-16 font-medium">{sym.replace('USDT','')}</span>
                <span className="text-muted/40">No tx yet</span>
              </div>
            )
            return (
              <div key={sym} className="flex items-center gap-2 py-1.5 text-[10px] border-b border-border/50">
                <span className="w-16 font-medium text-text">{sym.replace('USDT','')}</span>
                <span className="w-1.5 h-1.5 rounded-full bg-mantle flex-shrink-0" />
                <a href={s.audit_explorer_url} target="_blank" rel="noreferrer"
                   className="font-mono text-mantle hover:underline flex-1 truncate">
                  {shortAddr(s.audit_tx_hash)}
                </a>
                <span className="text-muted">{s.verdict}</span>
                <span className="text-muted/60">{timeAgo(s.timestamp_utc)}</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Current symbol */}
      {data ? (
        <div className="glass rounded-xl p-4 space-y-4">
          <div className="text-xs font-semibold text-text">{symbol} - Latest Analysis Audit</div>

          {txHash ? (
            <div className="rounded-xl p-4 bg-mantle/5 border border-mantle/30">
              <div className="text-[10px] font-bold text-mantle mb-2">
                On-chain audit confirmed - Block #{data.audit_block}
              </div>
              <div className="space-y-1.5 text-[10px]">
                <div className="flex items-start gap-2">
                  <span className="text-muted w-20 flex-shrink-0">Transaction:</span>
                  <a href={expUrl} target="_blank" rel="noreferrer"
                     className="font-mono text-mantle hover:underline truncate">
                    {txHash}
                  </a>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-muted w-20 flex-shrink-0">Explorer:</span>
                  <a href={expUrl} target="_blank" rel="noreferrer" className="text-mantle hover:underline">
                    Mantle Sepolia Explorer
                  </a>
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-xl p-3 bg-caution/5 border border-caution/30 text-xs text-caution">
              Analysis complete but on-chain submission pending. Check wallet MNT balance.
            </div>
          )}

          {hash && (
            <div>
              <div className="text-[10px] font-semibold text-muted mb-2">keccak256 Hash (stored on-chain)</div>
              <HashDisplay hash={hash} />
            </div>
          )}

          {data?.snapshot_hash && (
            <div>
              <div className="text-[10px] font-semibold text-muted mb-2">
                Snapshot Hash (what the system SAW)
              </div>
              <HashDisplay hash={data.snapshot_hash} />
              <div className="text-[9px] text-muted mt-1">
                keccak256 of raw indicator values. Proves the input data, not just the output.
              </div>
            </div>
          )}

          <div className="space-y-3">
            <motion.button
              onClick={verify}
              disabled={verifying}
              whileTap={{ scale: 0.97 }}
              className={`px-4 py-2 rounded-lg text-xs font-medium transition-all ${
                verifying ? 'glass text-muted cursor-not-allowed' : 'bg-mantle text-dark hover:bg-mantle-dim'
              }`}
            >
              {verifying ? 'Verifying...' : 'Verify Hash Independently'}
            </motion.button>
            <VerifyResult result={verResult} />
          </div>

          {/* StrategyGate */}
          <div className="rounded-xl p-4 border border-mantle/20 bg-mantle/4">
            <div className="text-[10px] font-bold text-mantle mb-3">
              StrategyGate.sol - Live On-Chain Position Check
            </div>
            <div className="flex items-center gap-3 mb-3">
              <div className="text-[10px] text-muted">Leverage:</div>
              <select
                value={gateInput}
                onChange={e => setGateInput(Number(e.target.value))}
                className="bg-dark border border-border rounded px-2 py-1 text-xs text-text"
              >
                {[1,2,3,5,7,10,15,20].map(v => (
                  <option key={v} value={v}>{v}x</option>
                ))}
              </select>
              <motion.button
                onClick={checkGate}
                disabled={gateLoading}
                whileTap={{ scale: 0.97 }}
                className="px-3 py-1.5 rounded-lg text-[10px] font-medium bg-mantle text-dark hover:bg-mantle-dim transition-all disabled:opacity-50"
              >
                {gateLoading ? 'Calling...' : 'Check on Mantle'}
              </motion.button>
            </div>
            {gateResult && (
              <motion.div
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                className={`rounded-lg p-3 text-xs ${
                  gateResult.allowed ? 'bg-mantle/10 border border-mantle/30 text-mantle' : 'bg-bear/10 border border-bear/30 text-bear'
                }`}
              >
                <div className="font-bold mb-1">
                  {gateResult.allowed ? 'Position APPROVED' : 'Position RESTRICTED'}
                  {gateResult.max_leverage ? ` - max ${gateResult.max_leverage}x allowed` : ''}
                </div>
                <div className="opacity-80 text-[10px]">{gateResult.reason}</div>
                {gateResult.gate_address && (
                  <a href={gateResult.gate_explorer} target="_blank" rel="noreferrer"
                     className="mt-1.5 block text-[9px] font-mono opacity-50 hover:opacity-100 hover:underline truncate">
                    {gateResult.source} - {gateResult.gate_address.slice(0,14)}...
                  </a>
                )}
              </motion.div>
            )}
            <div className="text-[9px] text-muted mt-2 leading-relaxed">
              StrategyGate reads TradingSignalOracle on-chain. Any lending protocol or DEX on Mantle
              can integrate this to apply AI regime-based leverage caps.
            </div>
          </div>

          {payload && (
            <details className="group">
              <summary className="text-[10px] font-semibold text-muted cursor-pointer hover:text-text transition-colors list-none flex items-center gap-2">
                <span className="group-open:rotate-90 transition-transform inline-block">+</span>
                Full Audit Payload
              </summary>
              <div className="mt-2 bg-dark rounded-lg p-3 border border-border overflow-auto max-h-56">
                <pre className="text-[9px] font-mono text-text/70 whitespace-pre-wrap">
                  {JSON.stringify(JSON.parse(payload), null, 2)}
                </pre>
              </div>
              <div className="text-[9px] text-muted mt-1.5">
                This JSON was keccak256-hashed and stored on Mantle Sepolia before you saw this analysis.
                Any post-hoc modification produces a different hash - immediately detectable.
              </div>
            </details>
          )}
        </div>
      ) : (
        <div className="glass rounded-xl p-8 text-center text-muted text-sm">
          No analysis for {symbol} yet. Run the pipeline to generate one.
        </div>
      )}

    </div>
  )
}
