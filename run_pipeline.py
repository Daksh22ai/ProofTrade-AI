"""
run_pipeline.py — Orchestrate the full analysis pipeline for all symbols.

Execution order per symbol:
  1. get_snapshot()      — compute all 12 indicators from QuestDB + Mantle DeFi
  2. analyze_symbol()    — 2-call Groq pipeline (macro regime + full analysis)
  3. compute_hash()      — deterministic keccak256 of analysis output
  4. submit_to_mantle()  — log hash on Mantle Sepolia AuditLog contract
  5. atomic_write()      — write JSON to analysis_results/{SYMBOL}_latest.json

BTC macro is run first and shared with all altcoins (same macro regime applies).
All steps after the Groq call are non-blocking: on-chain failure ≠ analysis failure.
Atomic writes ensure Streamlit dashboard never reads a half-written file.
"""

import os
import sys
import json
import time
import logging
import tempfile
import concurrent.futures
from pathlib import Path
from datetime import datetime, timezone
from web3 import Web3

from dotenv import load_dotenv

load_dotenv()

# ── Dirs created BEFORE logging setup so the file handler always works ────────

OUTPUT_DIR = Path("analysis_results")
OUTPUT_DIR.mkdir(exist_ok=True)

LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)8s] %(name)-20s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/pipeline.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("run_pipeline")

# ── Constants ─────────────────────────────────────────────────────────────────

# BTC always first — its macro result is reused for altcoins
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "MNTUSDT"]


# ── Post-LLM enforcement: verdict direction + confidence calibration ───────────
# The LLM receives the base_direction and is instructed not to change it.
# This code guard is the backstop — catches any violation deterministically.

def _enforce_verdict_and_confidence(result: dict) -> dict:
    """
    1. Verdict direction: LLM cannot change direction from the deterministic base.
       LONG scenarios cannot produce SHORT. NO_TRADE scenarios always stay NO_TRADE.
    2. Confidence calibration: programmatic cap, not just a prompt instruction.
       Prevents the ETHUSDT 80%/1-confluence incident from recurring.
    """
    analysis   = result.get("analysis", {})
    verdict    = analysis.get("verdict", "NO_TRADE")
    confidence = int(analysis.get("confidence_score", 50))
    confluence = int(analysis.get("confluence_count", 0))
    meets_min  = analysis.get("meets_minimum", False)
    base_dir   = result.get("base_direction", "NEUTRAL")
    scenario   = result.get("scenario_number", 8)

    enforced = False

    # ── Direction enforcement ──────────────────────────────────────────────────
    valid_verdicts = {
        "LONG":    {"LONG", "STRONG_LONG", "NO_TRADE"},
        "SHORT":   {"SHORT", "STRONG_SHORT", "NO_TRADE"},
        "NEUTRAL": {"NEUTRAL", "NO_TRADE"},
        "NO_TRADE":{"NO_TRADE"},
    }
    allowed = valid_verdicts.get(base_dir, {"NO_TRADE"})

    if verdict not in allowed:
        # LLM violated direction constraint — hard override
        corrected = base_dir if base_dir not in ("NEUTRAL", "NO_TRADE") else "NO_TRADE"
        logger.warning(
            f"[{result.get('symbol')}] VERDICT OVERRIDDEN: LLM said '{verdict}' "
            f"but base_direction={base_dir} (S{scenario}). Corrected to '{corrected}'."
        )
        analysis["verdict"]             = corrected
        analysis["verdict_overridden"]  = True
        analysis["verdict_override_reason"] = (
            f"LLM output '{verdict}' contradicted deterministic base_direction '{base_dir}' "
            f"for S{scenario}. Direction is set by the decision tree, not the LLM."
        )
        verdict  = corrected
        enforced = True

    # ── Confidence calibration ─────────────────────────────────────────────────
    original_confidence = confidence

    if verdict == "NO_TRADE":
        confidence = min(confidence, 40)
    elif not meets_min:
        confidence = min(confidence, 35)
    elif confluence < 5:
        confidence = min(confidence, 40)
    elif confluence < 7:
        confidence = min(confidence, 60)
    elif confluence < 9:
        confidence = min(confidence, 75)

    if confidence != original_confidence:
        logger.info(
            f"[{result.get('symbol')}] Confidence calibrated: {original_confidence} → {confidence} "
            f"(confluence={confluence}, meets_min={meets_min})"
        )
        analysis["confidence_score"]          = confidence
        analysis["confidence_calibrated"]     = True
        analysis["confidence_original"]       = original_confidence
        enforced = True

    result["analysis"]          = analysis
    result["enforcement_applied"] = enforced
    return result


def _build_snapshot_hash(result: dict) -> str:
    """
    Hash the DATA INPUTS that produced the analysis.
    Stored alongside the output hash so judges can verify BOTH:
      - what the AI said (data_hash — on-chain)
      - what the system SAW when it said it (snapshot_hash — in payload)
    """
    raw = result.get("snapshot_raw", {})
    ts  = result.get("timestamp_utc", "")[:16] + ":00Z"

    key_inputs = {
        "symbol":             result.get("symbol"),
        "timestamp_minute":   ts,
        "vwma_20d":           raw.get("vwma_20d"),
        "above_vwma":         raw.get("above_vwma_20d"),
        "rsi_14":             round(raw.get("rsi_14") or 0, 2),
        "oi_trend":           raw.get("oi_trend"),
        "funding_bucket":     raw.get("funding_bucket"),
        "spot_cvd_direction": raw.get("spot_cvd_direction"),
        "spot_cvd_source":    raw.get("spot_cvd_source"),
        "futures_cvd_dir":    raw.get("futures_cvd_direction"),
        "btc_dominance":      raw.get("btc_dominance"),
        "risk_sentiment":     raw.get("risk_sentiment"),
        "lsr_signal":         raw.get("lsr_signal"),
        "session":            result.get("session"),
        "scenario_number":    result.get("scenario_number"),
        "base_direction":     result.get("base_direction"),
        "cvd_matrix_state":   result.get("cvd_matrix_state"),
    }
    payload = json.dumps(key_inputs, sort_keys=True, separators=(',', ':'))
    return Web3.keccak(text=payload).hex()


# ── Atomic file write ─────────────────────────────────────────────────────────

def atomic_write(path: Path, data: dict):
    """
    Write JSON atomically using temp-file + os.replace().
    Streamlit will never read a half-written file because os.replace()
    is atomic on both POSIX and Windows (same filesystem volume).
    """
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, path)  # atomic rename
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


# ── Main pipeline ─────────────────────────────────────────────────────────────

def _analyse_one(symbol: str, btc_macro: dict | None,
                  global_market: dict | None = None) -> dict:
    """Analyse a single symbol end-to-end. Runs in a thread pool for altcoins."""
    from data_aggregator import get_snapshot
    from agents.pipeline import analyze_symbol
    from on_chain.submit_audit import compute_hash, submit_to_mantle

    sym_start = time.monotonic()
    logger.info(f"[{symbol}] Starting analysis...")

    include_mantle = symbol in ("ETHUSDT", "MNTUSDT")
    snapshot = get_snapshot(symbol, include_mantle=include_mantle,
                            global_market=global_market)

    result = analyze_symbol(snapshot, btc_macro=btc_macro)

    # ── Code-enforced verdict direction + confidence calibration ───────────────
    result = _enforce_verdict_and_confidence(result)

    # ── Dual hash: output hash (on-chain) + input hash (transparency) ─────────
    result["snapshot_hash"]  = _build_snapshot_hash(result)
    data_hash, payload_json  = compute_hash(result)
    result["data_hash"]      = data_hash
    result["hash_payload_json"] = payload_json

    try:
        audit_info = submit_to_mantle(result)
        result.update(audit_info)
        logger.info(f"[{symbol}] ✅ On-chain: {audit_info['audit_tx_hash'][:20]}...")
    except Exception as e:
        logger.warning(f"[{symbol}] ⚠️  On-chain submission failed (non-fatal): {e}")
        result["audit_tx_hash"]      = None
        result["audit_explorer_url"] = None
        result["audit_block"]        = None

    out_path = OUTPUT_DIR / f"{symbol}_latest.json"
    atomic_write(out_path, result)

    elapsed = time.monotonic() - sym_start
    v   = result.get("analysis", {}).get("verdict", "?")
    c   = result.get("analysis", {}).get("confluence_count", "?")
    pct = result.get("analysis", {}).get("confidence_score", "?")
    logger.info(f"[{symbol}] ✅ {elapsed:.1f}s | {v} | {c}/12 conf | {pct}/100")
    return result


def run(symbols: list = None):
    """
    Run the full analysis pipeline.
    1. Fetch global market context once (CoinGecko) — shared across all symbols.
    2. Run BTCUSDT first (its macro regime is shared with all altcoins).
    3. Run altcoins in parallel (up to 3 concurrent, respecting Groq TPM limits).
    """
    from data_aggregator import get_global_market_context

    target_symbols = symbols or SYMBOLS
    logger.info(f"Pipeline starting for: {target_symbols}")
    started_at = datetime.now(timezone.utc)

    # ── Step 0: Global market context (one call, shared across all symbols) ────
    logger.info("Fetching global market context (CoinGecko)...")
    global_market = get_global_market_context()

    results = {}

    # ── Step 1: BTC first (macro regime needed by altcoins) ───────────────────
    btc_macro = None
    if "BTCUSDT" in target_symbols:
        try:
            btc_result = _analyse_one("BTCUSDT", btc_macro=None, global_market=global_market)
            btc_macro  = btc_result.get("macro_regime")
            results["BTCUSDT"] = btc_result
        except Exception as e:
            logger.error(f"[BTCUSDT] ✗ Failed: {e}", exc_info=True)
            results["BTCUSDT"] = {"error": str(e), "symbol": "BTCUSDT"}

    # ── Step 2: Altcoins in parallel ──────────────────────────────────────────
    altcoins = [s for s in target_symbols if s != "BTCUSDT"]
    if altcoins:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3,
                                                   thread_name_prefix="altcoin") as pool:
            future_to_sym = {
                pool.submit(_analyse_one, sym, btc_macro, global_market): sym
                for sym in altcoins
            }
            for future in concurrent.futures.as_completed(future_to_sym):
                sym = future_to_sym[future]
                try:
                    results[sym] = future.result()
                except Exception as e:
                    logger.error(f"[{sym}] ✗ Failed: {e}", exc_info=True)
                    results[sym] = {"error": str(e), "symbol": sym}

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed_total = (datetime.now(timezone.utc) - started_at).total_seconds()
    ok  = sum(1 for r in results.values() if "error" not in r)
    err = len(results) - ok

    logger.info(f"\n{'='*60}\nPipeline complete in {elapsed_total:.0f}s | ✅ {ok}/{len(target_symbols)} | ❌ {err}/{len(target_symbols)}")
    for sym in target_symbols:
        r = results.get(sym, {})
        if "error" in r:
            logger.info(f"  {sym}: ❌ {r['error'][:80]}")
        else:
            v  = r.get("analysis", {}).get("verdict", "?")
            c  = r.get("analysis", {}).get("confluence_count", "?")
            p  = r.get("analysis", {}).get("confidence_score", "?")
            on = "✅" if r.get("audit_tx_hash") else "⚠️"
            logger.info(f"  {sym}: {v} ({p}/100 conf, {c}/12) {on}")

    return results


# ── CLI entry point ────────────────────────────────────────────────────────────
#
# Usage:
#   python run_pipeline.py                   # run once, all symbols
#   python run_pipeline.py BTCUSDT           # run once, one symbol
#   python run_pipeline.py --loop            # run every 30 minutes forever
#   python run_pipeline.py --loop --interval 60  # run every 60 minutes

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mantle AI Trading Copilot pipeline")
    parser.add_argument("symbols", nargs="*", help="Symbols to analyse (default: all 6)")
    parser.add_argument("--loop", action="store_true",
                        help="Run on a repeating interval instead of once")
    parser.add_argument("--interval", type=int, default=30,
                        help="Interval in minutes between runs (default: 30, used with --loop)")
    args = parser.parse_args()

    target_symbols = args.symbols if args.symbols else None

    if args.loop:
        interval_s = args.interval * 60
        logger.info(
            f"Pipeline scheduler started: every {args.interval} minutes "
            f"for {target_symbols or 'all symbols'}"
        )
        run_count = 0
        while True:
            run_count += 1
            logger.info(f"{'='*60}\nScheduled run #{run_count}")
            try:
                run(target_symbols)
            except Exception as e:
                logger.error(f"Pipeline run #{run_count} failed: {e}", exc_info=True)
            from datetime import timedelta as _td
            next_run = datetime.now(timezone.utc) + _td(seconds=interval_s)
            logger.info(f"Next run at {next_run.strftime('%H:%M:%S')} UTC (in {args.interval} min)")
            time.sleep(interval_s)
    else:
        if target_symbols:
            logger.info(f"Running for specific symbols: {target_symbols}")
        run(target_symbols)
