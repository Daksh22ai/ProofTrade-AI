"""
agents/pipeline.py — 2-call Groq pipeline per symbol.

Call 1 (MacroRegimeAgent): Part 0+1 → regime, session, leverage caps.
  Runs once for BTC, result is reused for all altcoins (they share the same macro regime).

Call 2 (FullAnalysisAgent): Parts 2-11 + pre-computed decision tree → full recommendation.
  Receives the deterministic decision tree result and all 12 indicator readings.
  Outputs the final RecommendationOutput including the pre-trade note and risk plan.

All LLM calls use JSON mode (response_format={"type":"json_object"}) for reliable parsing.
tenacity retries: 3 attempts, exponential backoff 2s/4s/8s.
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv

from .prompts import MACRO_REGIME_PROMPT, FULL_ANALYSIS_PROMPT

load_dotenv()

logger = logging.getLogger(__name__)

# ── Groq client ───────────────────────────────────────────────────────────────

_groq_client: Optional[Groq] = None

def _get_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set in environment")
        _groq_client = Groq(api_key=api_key)
    return _groq_client

GROQ_MODEL = "llama-3.3-70b-versatile"


# ── Retry-wrapped Groq call ───────────────────────────────────────────────────

# Required keys that must be present in each LLM response.
# Missing keys → retry, not silent None propagation.
_MACRO_REQUIRED = {"macro_regime", "bias", "max_leverage", "min_confluences",
                   "session_leverage_cap", "position_size_cap_pct", "session"}
_ANALYSIS_REQUIRED = {"verdict", "confidence_score", "confluence_count",
                      "meets_minimum", "indicator_scores", "bull_case", "bear_case",
                      "pre_trade_note_why", "pre_trade_note_wrong", "pre_trade_note_add",
                      "playbook_rules_cited", "entry_trigger", "stop_price"}


class _LLMSchemaError(Exception):
    """Raised when the LLM response is valid JSON but missing required fields."""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _groq_json_call(system_prompt: str, user_content: str,
                    max_tokens: int = 1500,
                    required_keys: set = None) -> dict:
    """
    Call Groq with JSON mode. Validates required keys before returning.
    Retries up to 3× on any error including schema violations and truncated JSON.
    """
    client = _get_client()
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0.15,
        max_tokens=max_tokens,
    )
    raw = resp.choices[0].message.content

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        # Truncated response (max_tokens hit mid-JSON) — retry with more tokens note
        raise _LLMSchemaError(
            f"JSON decode failed (likely truncated at max_tokens={max_tokens}): {e} "
            f"| raw tail: ...{raw[-80:]}"
        ) from e

    if required_keys:
        missing = required_keys - result.keys()
        if missing:
            raise _LLMSchemaError(
                f"LLM response missing required fields: {missing} "
                f"| got keys: {list(result.keys())[:10]}"
            )

    return result


# ── Call 1: Macro Regime ─────────────────────────────────────────────────────

def run_macro_regime_agent(snapshot: dict) -> dict:
    """
    Analyse BTC macro regime and session. Result is shared across all symbols.
    Call once for BTCUSDT; reuse the dict for altcoin symbols.
    """
    gm = snapshot.get("global_market", {})
    # Build global market block — data the prompt rules reference
    global_market_line = ""
    if gm.get("available"):
        global_market_line = (
            f"\nGlobal Market (for BTC dominance + stablecoin supply rules):"
            f"\n  BTC Dominance: {gm.get('btc_dominance')}% ({gm.get('btc_dom_signal')})"
            f"\n  Stablecoin Dom (USDT): {gm.get('stablecoin_dom')}% — {gm.get('stablecoin_trend_note')}"
            f"\n  Market Risk Sentiment: {gm.get('risk_sentiment')}"
            f"\n  Altcoin Season: {'YES (BTC dom <50%)' if gm.get('altcoin_season') else 'NO (BTC dom ≥50%)'}"
        )

    user_content = f"""
Symbol: {snapshot['symbol']}
Current price: {snapshot.get('current_price', 'N/A')}
20D VWMA: {snapshot.get('vwma_20d', 'N/A')} → price is {'ABOVE' if snapshot.get('above_vwma_20d') else 'BELOW' if snapshot.get('above_vwma_20d') is False else 'UNKNOWN'}
4H 21 EMA: {snapshot.get('ema_4h_21', 'N/A')} → price is {'ABOVE' if snapshot.get('above_ema_4h_21') else 'BELOW' if snapshot.get('above_ema_4h_21') is False else 'UNKNOWN'}
Funding rate (8h): {snapshot.get('funding', {}).get('current', 0):.6f} [{snapshot.get('funding', {}).get('bucket', 'unknown')}]
OI trend: {snapshot.get('oi_trend', 'unknown')} (slope: {snapshot.get('oi_slope', 0):.5f})
Session (UTC): {snapshot.get('session', 'UNKNOWN')}
Near funding reset (30min window): {snapshot.get('near_funding_reset', False)}{global_market_line}
"""
    logger.info(f"[{snapshot['symbol']}] Calling Groq — MacroRegimeAgent...")
    result = _groq_json_call(MACRO_REGIME_PROMPT, user_content,
                             max_tokens=600, required_keys=_MACRO_REQUIRED)
    logger.info(f"[{snapshot['symbol']}] Macro: {result.get('macro_regime')} | Session: {result.get('session')}")
    return result


# ── Call 2: Full Analysis ────────────────────────────────────────────────────

def run_full_analysis_agent(snapshot: dict, macro: dict) -> dict:
    """
    Full playbook analysis: 12-indicator scoring, bull/bear debate, synthesis, risk plan.
    Receives pre-computed decision tree result and macro context from Call 1.
    """
    funding = snapshot.get("funding", {})
    macd    = snapshot.get("macd", {})
    cvd_s   = snapshot.get("spot_cvd", {})
    cvd_f   = snapshot.get("futures_cvd", {})
    liq     = snapshot.get("liquidations", {})
    vpvr    = snapshot.get("vpvr", {})
    bid_ask = snapshot.get("bid_ask", {})
    mantle  = snapshot.get("mantle_signals")

    lsr     = snapshot.get("lsr", {})
    ob      = snapshot.get("ob_depth", {})
    gm      = snapshot.get("global_market", {})

    # Format Mantle signal if available
    mantle_block = ""
    if mantle:
        meth = mantle.get("meth_yield_signal", {})
        dex  = mantle.get("fusionx_dex_cvd", {})
        # For MNTUSDT: DEX CVD is the primary spot signal — label it clearly
        mntusdt_note = ""
        if snapshot.get("symbol") == "MNTUSDT":
            spot_src = snapshot.get("spot_cvd", {}).get("source", "binance_spot")
            if "FusionX" in spot_src:
                mntusdt_note = "\n  *** MNTUSDT: FusionX DEX CVD is the PRIMARY spot signal (on-chain Mantle conviction) ***"
        mantle_block = f"""
MANTLE ECOSYSTEM SIGNALS:{mntusdt_note}
  mETH Yield: {meth.get('meth_apy_pct', 'N/A')}% APY (source: {meth.get('data_source', 'N/A')})
  vs ETH funding annualized: {meth.get('eth_funding_annualized_pct', 'N/A')}%
  mETH Carry Signal: {meth.get('signal', 'N/A')} | Carry edge: {meth.get('carry_edge_bps', 'N/A')} bps
  mETH Reasoning: {meth.get('reasoning', '')}
  FusionX DEX CVD (Mantle on-chain): {dex.get('direction', 'N/A')} | net={dex.get('cvd_delta', 0)} | {dex.get('swap_count', 0)} swaps | pair={dex.get('pair','WMNT/USDT')}
  {dex.get('interpretation', '')}
  Combined Mantle signal: {mantle.get('combined_direction', 'N/A')} — {mantle.get('combined_note', '')}
"""

    # Global market context block
    global_block = ""
    if gm.get("available"):
        global_block = f"""
GLOBAL MARKET CONTEXT (BTC Dominance Rule applies to altcoin setups):
  BTC Dominance: {gm.get('btc_dominance')}% — {gm.get('btc_dom_signal')}
  {'  → RULE: altcoin max leverage capped at 5x when BTC dom > 56%' if (gm.get('btc_dominance') or 0) > 56 else '  → BTC dom < 56%: normal altcoin leverage table applies'}
  ETH Dominance: {gm.get('eth_dominance')}%
  Stablecoin Dominance (USDT): {gm.get('stablecoin_dom')}% — {gm.get('stablecoin_trend_note')}
  Market Cap Change 24H: {gm.get('mcap_change_24h'):+.1f}%
  Risk Sentiment: {gm.get('risk_sentiment')}
  Altcoin Season: {'YES — BTC dom < 50%, altcoin opportunities elevated' if gm.get('altcoin_season') else 'NO — BTC dom ≥ 50%'}
"""

    # Deterministic direction — injected into prompt so LLM knows its constraint
    base_dir = snapshot.get("base_direction", "NEUTRAL")
    min_conf  = snapshot.get("scenario_min_confluence", 5)
    can_esc   = snapshot.get("scenario_can_escalate", False)

    user_content = f"""
Symbol: {snapshot['symbol']} | Price: {snapshot.get('current_price', 'N/A')} | Session: {snapshot.get('session')}
Spot CVD Source: {snapshot.get('spot_cvd', {}).get('source', 'binance_spot')}

MACRO CONTEXT (from Call 1):
  Regime: {macro.get('macro_regime')} | Bias: {macro.get('bias')}
  Session leverage cap: {macro.get('session_leverage_cap')}x | Position size cap: {macro.get('position_size_cap_pct')}%
  Min confluences required: {macro.get('min_confluences')} | Max leverage: {macro.get('max_leverage')}x
  Regime reasoning: {macro.get('reasoning', '')}
{global_block}
DECISION TREE RESULT (computed deterministically — NON-NEGOTIABLE):
  Scenario: S{snapshot.get('scenario_number')} — {snapshot.get('scenario_name')}
  BASE DIRECTION: {base_dir} ← You CANNOT change this direction.
  {"You MAY escalate " + base_dir + " → STRONG_" + base_dir + " if confluence ≥ 9." if can_esc else "This scenario does NOT allow escalation."}
  MINIMUM CONFLUENCE for this scenario: {min_conf} (must call NO_TRADE if below)
  Trace: {json.dumps(snapshot.get('decision_tree_trace', {}))}

CVD MATRIX STATE: {snapshot.get('cvd_matrix_state')}
  Spot CVD: {cvd_s.get('direction')} (slope: {cvd_s.get('slope', 0):.5f}, {cvd_s.get('history_minutes', 0)} min history)
  Futures CVD: {cvd_f.get('direction')} (slope: {cvd_f.get('slope', 0):.5f})

12 INDICATOR READINGS (score each 0 or 1 for alignment with {base_dir}):
  1.  VWMA 20D:         ${snapshot.get('vwma_20d') or 'N/A'} | Price {'ABOVE ✓' if snapshot.get('above_vwma_20d') else 'BELOW ✗'} | Gap: {abs((snapshot.get('current_price') or 0) - (snapshot.get('vwma_20d') or 0)) / max(snapshot.get('vwma_20d') or 1, 1) * 100:.2f}%
  2.  Long/Short Ratio: buy={lsr.get('buy_ratio', 'N/A')} / sell={lsr.get('sell_ratio', 'N/A')} | trend={lsr.get('trend', 'N/A')} | extreme_long={lsr.get('extreme_long')} | extreme_short={lsr.get('extreme_short')} | contrarian_signal={lsr.get('signal', 'NEUTRAL')}
  3.  Futures CVD:      {cvd_f.get('direction')} (tail: {cvd_f.get('series_tail', [])[-3:]})
  4.  Spot CVD:         {cvd_s.get('direction')} (tail: {cvd_s.get('series_tail', [])[-3:]}) [SOURCE: {cvd_s.get('source', 'binance_spot')}]
  5.  Bid/Ask Delta:    ratio={bid_ask.get('ratio', 0):.4f} [{bid_ask.get('sentiment')}] (>0 = more bids = buying pressure)
  6.  Funding Rate:     {funding.get('current', 0):.6f} per 8h [{funding.get('bucket')}] | extreme+: {funding.get('extreme_positive')} | extreme-: {funding.get('extreme_negative')}
  7.  Open Interest:    trend={snapshot.get('oi_trend')} (slope={snapshot.get('oi_slope', 0):.5f})
  8.  Liq Events 24H:  long liq ${liq.get('long_liq_usd_24h', 0):,.0f} | short liq ${liq.get('short_liq_usd_24h', 0):,.0f}
     Top level: {liq.get('significant_levels', [{}])[:1]}
     NOTE: {liq.get('note', '')}
  9.  Order Book Depth: wall={ob.get('wall_side', 'N/A')} | strength={ob.get('wall_strength', 0):.3f} | top5_bid={ob.get('top5_bid_vol', 0):.2f} vs top5_ask={ob.get('top5_ask_vol', 0):.2f} | signal={ob.get('signal', 'NEUTRAL')}
  10. MACD 1H:          value={macd.get('value', 'N/A')}, histogram={macd.get('histogram', 'N/A')} [{'ABOVE' if macd.get('above_zero') else 'BELOW'} zero]
  11. RSI 14 1H:        {snapshot.get('rsi_14') or 'N/A'} [{'overbought >70 ✗' if (snapshot.get('rsi_14') or 0)>70 else 'oversold <30 ✓ for longs' if (snapshot.get('rsi_14') or 100)<30 else 'neutral 30-70'}]
  12. VPVR (approx):    POC=${vpvr.get('poc', 'N/A')} | in_HVN={vpvr.get('in_hvn')} | in_LVN={vpvr.get('in_lvn')}
{mantle_block}
RISK INPUTS:
  Account: $10,000 | Risk per trade: 1% = $100
  ATR(14, 1H): {snapshot.get('atr_14', 'N/A')} (use ×1.5 as minimum stop distance)
  Suggested stop buffer: 0.3-0.5% beyond structure level
"""
    logger.info(f"[{snapshot['symbol']}] Calling Groq — FullAnalysisAgent...")
    result = _groq_json_call(FULL_ANALYSIS_PROMPT, user_content,
                             max_tokens=2000, required_keys=_ANALYSIS_REQUIRED)
    logger.info(
        f"[{snapshot['symbol']}] Analysis: {result.get('verdict')} "
        f"| Confluence: {result.get('confluence_count')}/12 "
        f"| Confidence: {result.get('confidence_score')}"
    )
    return result


# ── Main entry: analyze one symbol ───────────────────────────────────────────

def analyze_symbol(snapshot: dict, btc_macro: Optional[dict] = None) -> dict:
    """
    Run the full 2-call analysis for one symbol.
    If btc_macro is provided (from a previous BTCUSDT call), it's reused for altcoin macro context.
    Returns the complete analysis dict (merged snapshot + macro + analysis).
    """
    # Call 1: macro regime (or reuse BTC macro for alts)
    if btc_macro is not None and snapshot["symbol"] != "BTCUSDT":
        macro = btc_macro
        logger.info(f"[{snapshot['symbol']}] Reusing BTC macro context: {macro.get('macro_regime')}")
    else:
        macro = run_macro_regime_agent(snapshot)

    # Call 2: full analysis
    analysis = run_full_analysis_agent(snapshot, macro)

    return {
        "symbol":            snapshot["symbol"],
        "timestamp_utc":     snapshot["timestamp_utc"],
        "current_price":     snapshot.get("current_price"),
        "session":           snapshot.get("session"),
        "scenario_number":   snapshot.get("scenario_number"),
        "scenario_name":     snapshot.get("scenario_name"),
        "base_direction":    snapshot.get("base_direction"),
        "decision_tree_trace": snapshot.get("decision_tree_trace"),
        "cvd_matrix_state":  snapshot.get("cvd_matrix_state"),
        "macro_regime":      macro,
        "analysis":          analysis,
        "mantle_signals":    snapshot.get("mantle_signals"),
        "global_market":     snapshot.get("global_market"),
        # Raw computed values for transparency UI + snapshot hash
        "snapshot_raw": {
            "vwma_20d":        snapshot.get("vwma_20d"),
            "above_vwma_20d":  snapshot.get("above_vwma_20d"),
            "ema_4h_21":       snapshot.get("ema_4h_21"),
            "above_ema_4h_21": snapshot.get("above_ema_4h_21"),
            "rsi_14":          snapshot.get("rsi_14"),
            "atr_14":          snapshot.get("atr_14"),
            "oi_trend":        snapshot.get("oi_trend"),
            "oi_slope":        snapshot.get("oi_slope"),
            "spot_cvd_direction":   snapshot.get("spot_cvd", {}).get("direction"),
            "spot_cvd_source":      snapshot.get("spot_cvd", {}).get("source", "binance_spot"),
            "futures_cvd_direction":snapshot.get("futures_cvd", {}).get("direction"),
            "funding_current": snapshot.get("funding", {}).get("current"),
            "funding_bucket":  snapshot.get("funding", {}).get("bucket"),
            "lsr_buy":         snapshot.get("lsr", {}).get("buy_ratio"),
            "lsr_sell":        snapshot.get("lsr", {}).get("sell_ratio"),
            "lsr_signal":      snapshot.get("lsr", {}).get("signal"),
            "ob_wall_side":    snapshot.get("ob_depth", {}).get("wall_side"),
            "macd_histogram":  snapshot.get("macd", {}).get("histogram"),
            "macd_above_zero": snapshot.get("macd", {}).get("above_zero"),
            "btc_dominance":   snapshot.get("global_market", {}).get("btc_dominance"),
            "risk_sentiment":  snapshot.get("global_market", {}).get("risk_sentiment"),
        },
    }
