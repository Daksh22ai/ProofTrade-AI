# agents/: LLM Pipeline

The AI explanation layer. Every recommendation produced by this module traces to a specific rule in the trading playbook. The LLM explains and calibrates; it does not decide.

## Files

- `prompts.py`: Two system prompts condensed from the 41-page trading playbook, each under 1500 tokens.
- `pipeline.py`: The 2-call Groq pipeline with retry, schema validation, and JSON mode.

## Design Principle

The decision tree in `data_aggregator.py` runs first and identifies the scenario deterministically. The base direction (LONG, SHORT, NEUTRAL, NO_TRADE) is fixed before any LLM call. The prompts communicate this as a hard constraint: "BASE DIRECTION: LONG. You CANNOT change this direction."

A second enforcement layer in `run_pipeline.py` catches any violation in the LLM output and corrects it in code, logging the incident.

## Call 1: MacroRegimeAgent

**Input**: BTC snapshot (price, VWMA, EMA, funding, OI trend, session, global market context)

**Output**:
```json
{
  "macro_regime": "BULL",
  "bias": "LONGS",
  "max_leverage": 10,
  "min_confluences": 5,
  "session_leverage_cap": 10,
  "position_size_cap_pct": 100,
  "session": "LONDON",
  "reasoning": "Price 3.2% above 20D VWMA (Part 0A BULL), funding +0.018% in moderate+ band, LONDON session (Part 1 full leverage)."
}
```

This result is computed once for BTC and reused for all 5 altcoins. This ensures all symbols share the same macro context and saves 5 API calls per cycle.

## Call 2: FullAnalysisAgent

**Input**: Symbol snapshot including: all 12 indicators with raw values, the decision tree result and scenario name, the macro result from Call 1, CVD matrix state, global market context (BTC dominance, stablecoin supply, risk sentiment), and Mantle signals for ETHUSDT and MNTUSDT.

**Output** (partial):
```json
{
  "indicator_scores": [
    {"indicator": "VWMA 20D", "reading": "...", "signal": "BULLISH", "score": 1},
    {"indicator": "Long/Short Ratio", "reading": "...", "signal": "NEUTRAL", "score": 0},
    ...12 items total
  ],
  "confluence_count": 8,
  "meets_minimum": true,
  "bull_case": "...",
  "bear_case": "...",
  "verdict": "LONG",
  "confidence_score": 70,
  "entry_trigger": "...",
  "stop_price": 103200.0,
  "target_1": 107500.0,
  "leverage_recommended": 7,
  "pre_trade_note_why": "...",
  "pre_trade_note_wrong": "...",
  "pre_trade_note_add": "...",
  "failure_mode": "...",
  "playbook_rules_cited": ["Part 0A: BULL regime", "Part 3: BOTH_RISING", "Part 2: S1"]
}
```

## Retry Mechanism

`_groq_json_call()` uses `tenacity` with:
- Up to 3 attempts
- Exponential backoff: 2s, 4s, 8s
- Retries on any exception, including schema violations and truncated JSON

Schema validation is done before returning: if required keys are missing, a `_LLMSchemaError` is raised, which triggers a retry. Silent `None` propagation is not allowed.

## JSON Mode

All calls use `response_format={"type": "json_object"}`. This eliminates markdown wrapping and significantly reduces parse errors. Temperature is set to 0.15 for consistent structured output.

## Parallel Execution

BTC runs first. After its `macro_regime` is available, the remaining 5 altcoins are analyzed in parallel using `concurrent.futures.ThreadPoolExecutor(max_workers=3)`. This reduces total pipeline time from approximately 90 seconds (sequential) to approximately 20-25 seconds.

The thread limit of 3 respects Groq free-tier token-per-minute limits.

## Model

`llama-3.3-70b-versatile` via Groq API (OpenAI-compatible endpoint). This is not the Grok model from xAI.

## MNTUSDT-Specific Handling

When analyzing MNTUSDT, the prompt notes that the Spot CVD source is FusionX DEX on-chain swaps rather than Binance spot aggTrades. The LLM is informed that on-chain conviction differs from CEX order flow and should weight the signal accordingly.

## Prompt Version Tracking

`submit_audit.py` computes `playbook_prompt_hash` as a 16-character keccak256 fingerprint of both prompts concatenated. This hash is included in the on-chain payload. Any future prompt change produces a different fingerprint, making prompt drift permanently detectable in the audit trail.
