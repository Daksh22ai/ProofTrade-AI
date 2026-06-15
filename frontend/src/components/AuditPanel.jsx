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
      <button
        onClick={copy}
        className="text-muted hover:text-mantle transition-colors flex-shrink-0"
      >
        {copied ? '✓' : '⧉'}
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
      className={`rounded-xl p-4 border ${
        result.match
          ? 'bg-mantle/5 border-mantle/30 text-mantle'
          : 'bg-bear/5 border-bear/30 text-bear'
      }`}
    >
      <div className="font-bold mb-2">
        {result.match ? '✅ Hash Verified' : '❌ Hash Mismatch'}
      </div>
      {result.match ? (
        <div className="text-xs opacity-80">
          The recomputed hash matches the on-chain record. This analysis was logged at block {result.audit_block} before any price movement.
        </div>
      ) : (
        <div className="text-xs opacity-80">
          The recomputed hash does not match. Expected: {result.claimed?.slice(0,20)}…<br/>
          Got: {result.recomputed?.slice(0,20)}…
        </div>
      )}
    </motion.div>
  )
}

export default function AuditPanel({ symbol, deployment }) {
  const [data,      setData]      = useState(null)
  const [verifying, setVerifying] = useState(false)
  const [verResult, setVerResult] = useState(null)
  const [allSignals, setAllSignals] = useState({})
  const [gateResult, setGateResult] = useState(null)
  const [gateLoading, setGateLoading] = useState(false)
  const [gateInput, setGateInput] = useState(10)

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
    <div className="space-y-6">

      {/* Hero explanation */}
      <div className="glass rounded-xl p-6 border border-mantle/10">
        <div className="flex items-start gap-4">
          <div className="text-4xl">⛓</div>
          <div>
            <h2 className="text-lg font-bold text-text mb-2">
              Verifiable AI — On-Chain Audit Trail
            </h2>
            <p className="text-sm text-muted leading-relaxed mb-3">
              Every AI recommendation is <span className="text-mantle font-medium">keccak256-hashed</span> and
              permanently logged on <span className="text-mantle font-medium">Mantle Sepolia</span> before you see it.
              The block timestamp is cryptographic proof that the recommendation pre-dates any price movement.
            </p>
            <div className="flex flex-wrap gap-3 text-xs">
              {[
                '🔐 Cryptographic pre-trade proof',
                '⛓ Immutable on Mantle Sepolia',
                '🔍 Anyone can verify independently',
                '📖 Full analysis payload shown below',
              ].map(item => (
                <span key={item} className="px-2.5 py-1 rounded-full bg-mantle/10 text-mantle border border-mantle/20">
                  {item}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Contract info */}
      {deployment && (
        <div className="glass rounded-xl p-5">
          <div className="text-xs font-semibold text-muted uppercase tracking-wider mb-3">
            TradingSignalOracle Contract
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
            <div>
              <div className="text-muted mb-1">Contract Address</div>
              <a href={deployment.explorer_url} target="_blank" rel="noreferrer"
                 className="font-mono text-mantle hover:underline break-all">
                {deployment.address}
              </a>
            </div>
            <div>
              <div className="text-muted mb-1">Network</div>
              <div className="text-text">Mantle Sepolia (chainId 5003)</div>
            </div>
            <div>
              <div className="text-muted mb-1">Version</div>
              <div className="font-mono text-text">{deployment.version || 'v2.0'}</div>
            </div>
            <div>
              <div className="text-muted mb-1">Deployed</div>
              <div className="text-text">{deployment.deployed_at_utc?.slice(0,19).replace('T',' ')} UTC</div>
            </div>
          </div>
        </div>
      )}

      {/* All-symbols tx feed */}
      <div className="glass rounded-xl p-5">
        <div className="text-xs font-semibold text-muted uppercase tracking-wider mb-3">
          Recent On-Chain Transactions
        </div>
        <div className="space-y-2">
          {SYMBOLS.map(sym => {
            const s = allSignals[sym]
            if (!s?.audit_tx_hash) return (
              <div key={sym} className="flex items-center gap-3 py-2 text-xs text-muted border-b border-border">
                <span className="w-20 font-medium">{sym}</span>
                <span className="w-2 h-2 rounded-full bg-muted/30" />
                <span>No tx yet</span>
              </div>
            )
            return (
              <div key={sym} className="flex items-center gap-3 py-2 text-xs border-b border-border">
                <span className="w-20 font-medium text-text">{sym}</span>
                <span className="w-2 h-2 rounded-full bg-mantle flex-shrink-0" />
                <a href={s.audit_explorer_url} target="_blank" rel="noreferrer"
                   className="font-mono text-mantle hover:underline flex-1 truncate">
                  {shortAddr(s.audit_tx_hash)}
                </a>
                <span className="text-muted">{s.verdict}</span>
                <span className="text-muted">{timeAgo(s.timestamp_utc)}</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Current symbol audit */}
      {data ? (
        <div className="glass rounded-xl p-5 space-y-5">
          <div className="text-sm font-semibold text-text">{symbol} — Latest Analysis Audit</div>

          {txHash ? (
            <div className="rounded-xl p-4 bg-mantle/5 border border-mantle/30">
              <div className="text-xs font-bold text-mantle mb-2">
                ✅ On-chain audit confirmed — Block #{data.audit_block}
              </div>
              <div className="space-y-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-muted w-24 flex-shrink-0">Transaction:</span>
                  <a href={expUrl} target="_blank" rel="noreferrer"
                     className="font-mono text-mantle hover:underline truncate">
                    {txHash}
                  </a>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-muted w-24 flex-shrink-0">Explorer:</span>
                  <a href={expUrl} target="_blank" rel="noreferrer"
                     className="text-mantle hover:underline">
                    Mantle Sepolia Explorer ↗
                  </a>
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-xl p-4 bg-caution/5 border border-caution/30 text-xs text-caution">
              ⚠ Analysis complete but not yet submitted on-chain. Check wallet MNT balance.
            </div>
          )}

          {/* Hash */}
          {hash && (
            <div>
              <div className="text-xs font-semibold text-muted mb-2">
                keccak256 Hash (what was stored on-chain)
              </div>
              <HashDisplay hash={hash} />
              <div className="text-[10px] text-muted mt-1">
                Hash of the deterministic analysis JSON (sort_keys=True, no whitespace).
                Recompute with: <span className="font-mono">Web3.keccak(text=payload_json).hex()</span>
              </div>
            </div>
          )}

          {/* Verify button */}
          <div className="space-y-3">
            <motion.button
              onClick={verify}
              disabled={verifying}
              whileTap={{ scale: 0.97 }}
              className={`px-5 py-2.5 rounded-lg text-sm font-medium transition-all ${
                verifying
                  ? 'glass text-muted cursor-not-allowed'
                  : 'bg-mantle text-dark hover:bg-mantle-dim'
              }`}
            >
              {verifying ? '⏳ Verifying…' : '🔍 Verify Hash Independently'}
            </motion.button>
            <VerifyResult result={verResult} />
          </div>

          {/* Dual hash: snapshot hash (input data) */}
          {data?.snapshot_hash && (
            <div>
              <div className="text-xs font-semibold text-muted mb-2 flex items-center gap-2">
                🔍 Snapshot Hash
                <span className="text-[10px] text-muted/60 font-normal">(proves what data the system SAW)</span>
              </div>
              <HashDisplay hash={data.snapshot_hash} />
              <div className="text-[10px] text-muted mt-1">
                keccak256 of raw indicator values (VWMA, RSI, OI, funding, CVD directions, BTC dominance).
                Combines with the output hash to prove both what the AI said AND what it observed.
              </div>
            </div>
          )}

          {/* StrategyGate — Composable Oracle Demo */}
          <div className="rounded-xl p-4 border border-mantle/20 bg-mantle/5">
            <div className="text-xs font-bold text-mantle mb-3 flex items-center gap-2">
              🔗 StrategyGate.sol — Live On-Chain Position Check
              <span className="text-[10px] font-normal text-muted">
                Any DeFi protocol on Mantle can call this
              </span>
            </div>
            <div className="flex items-center gap-3 mb-3">
              <div className="text-xs text-muted">Requested leverage:</div>
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
                className="px-3 py-1.5 rounded-lg text-xs font-medium bg-mantle text-dark hover:bg-mantle-dim transition-all disabled:opacity-50"
              >
                {gateLoading ? '⏳ Calling contract…' : '⛓ Check on Mantle'}
              </motion.button>
            </div>
            {gateResult && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className={`rounded-lg p-3 text-xs ${
                  gateResult.allowed
                    ? 'bg-mantle/10 border border-mantle/30 text-mantle'
                    : 'bg-bear/10 border border-bear/30 text-bear'
                }`}
              >
                <div className="font-bold mb-1">
                  {gateResult.allowed ? '✅ Position APPROVED' : '❌ Position RESTRICTED'}
                  {gateResult.max_leverage ? ` — max ${gateResult.max_leverage}x allowed` : ''}
                </div>
                <div className="opacity-80">{gateResult.reason}</div>
                {gateResult.gate_address && (
                  <a href={gateResult.gate_explorer} target="_blank" rel="noreferrer"
                     className="mt-2 block text-[10px] font-mono opacity-60 hover:opacity-100 hover:underline">
                    {gateResult.source} → {gateResult.gate_address.slice(0,12)}…
                  </a>
                )}
              </motion.div>
            )}
            <div className="text-[10px] text-muted mt-2">
              StrategyGate reads TradingSignalOracle on-chain. A lending protocol could call this before approving leveraged positions. Regime-aware leverage caps: BULL=10x, TRANSITION=5x, BEAR=3x.
            </div>
          </div>

          {/* Payload */}
          {payload && (
            <details className="group">
              <summary className="text-xs font-semibold text-muted cursor-pointer hover:text-text transition-colors list-none flex items-center gap-2">
                <span className="group-open:rotate-90 transition-transform">▶</span>
                📄 Hashed Payload (Full Audit Evidence)
              </summary>
              <div className="mt-3 bg-dark rounded-lg p-4 border border-border overflow-auto max-h-64">
                <pre className="text-[10px] font-mono text-text/80 whitespace-pre-wrap">
                  {JSON.stringify(JSON.parse(payload), null, 2)}
                </pre>
              </div>
              <div className="text-[10px] text-muted mt-2">
                This exact JSON was keccak256-hashed and stored on Mantle Sepolia before you saw this analysis.
                Any post-hoc modification to any field produces a different hash — immediately detectable.
                <br/>The payload includes the snapshot_hash (input data proof) and playbook_prompt_hash (version traceability).
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
