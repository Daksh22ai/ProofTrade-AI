"""
dashboard/app.py — Mantle AI Trading Copilot Dashboard

Three tabs:
  Tab 1 "Market Intelligence" — live charts: candlestick, Spot CVD, Futures CVD,
         FusionX DEX CVD (Mantle on-chain), OI, funding, liquidations
  Tab 2 "AI Analysis"        — playbook decision tree trace, CVD matrix state,
         12-indicator confluence scorecard, bull/bear debate, verdict card,
         pre-trade note, Mantle DeFi signal, playbook rules cited
  Tab 3 "On-Chain Audit"     — tx hash, Mantle explorer link, hash verify button,
         hashable payload display for full transparency

Run: streamlit run dashboard/app.py --server.port 8501
"""

import os
import sys
import json
import subprocess
import tempfile
import time
from pathlib import Path
from datetime import datetime

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import pg8000
from streamlit_autorefresh import st_autorefresh
from dotenv import load_dotenv

# Add project root to path so imports work from dashboard/
sys.path.insert(0, str(Path(__file__).parent.parent))
from on_chain.submit_audit import verify_hash

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Mantle AI Trading Copilot",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Auto-refresh every 30 seconds (non-blocking — uses JS timer not sleep)
st_autorefresh(interval=30_000, key="auto_refresh")

# ── Constants ─────────────────────────────────────────────────────────────────

SYMBOLS     = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "MNTUSDT"]
OUTPUT_DIR  = Path(__file__).parent.parent / "analysis_results"
FALLBACK    = Path(__file__).parent / "demo_fallback.json"
DEPLOYMENT  = Path(__file__).parent.parent / "on_chain" / "deployment.json"

SCENARIO_NAMES = {
    1: "Healthy Uptrend",      2: "Uptrend Weakening",
    3: "Confirmed Reversal ↓", 4: "Healthy Downtrend",
    5: "Dead Cat Bounce",      6: "Bottom Forming",
    7: "Confirmed Reversal ↑", 8: "Ranging / Consolidation",
    9: "Manipulation Pump",
}

VERDICT_ICONS = {
    "STRONG_LONG":  ("🟢🟢", "success"),
    "LONG":         ("🟢",   "success"),
    "NEUTRAL":      ("⚪",   "info"),
    "SHORT":        ("🔴",   "error"),
    "STRONG_SHORT": ("🔴🔴", "error"),
    "NO_TRADE":     ("⛔",   "warning"),
}

CVD_ICONS = {
    "BOTH_RISING":       "🟢",
    "BOTH_FALLING":      "🔴",
    "FUT_UP_SPOT_FLAT":  "🟠",
    "FUT_DOWN_SPOT_FLAT":"🔵",
}

SIGNAL_COLORS = {
    "BULLISH":  "#00D4AA",
    "BEARISH":  "#FF6B6B",
    "CAUTION":  "#FFD700",
    "WARNING":  "#FF8C00",
    "TRAP":     "#FF0000",
    "NEUTRAL":  "#888888",
}


# ── DB helper ────────────────────────────────────────────────────────────────

@st.cache_resource
def _db():
    try:
        return pg8000.connect(
            host=os.getenv("QUESTDB_HOST", "localhost"),
            port=int(os.getenv("QUESTDB_PG_PORT", "8812")),
            database="qdb", user="admin", password="quest",
        )
    except Exception as e:
        return None

def q(sql: str):
    conn = _db()
    if conn is None:
        return []
    try:
        c = conn.cursor()
        c.execute(sql)
        return c.fetchall()
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        # Reset cache so next call reconnects
        _db.clear()
        return []


# ── Analysis loader ───────────────────────────────────────────────────────────

def load_analysis(symbol: str) -> dict | None:
    path = OUTPUT_DIR / f"{symbol}_latest.json"
    # Try live result first
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass  # file may be mid-write — try fallback
    # Demo fallback
    if FALLBACK.exists():
        try:
            with open(FALLBACK) as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if item.get("symbol") == symbol:
                        return item
                return data[0] if data else None
            return data
        except Exception:
            pass
    return None

def load_deployment() -> dict | None:
    if DEPLOYMENT.exists():
        try:
            with open(DEPLOYMENT) as f:
                return json.load(f)
        except Exception:
            pass
    return None


# ── Chart builders ────────────────────────────────────────────────────────────

PLOTLY_DARK = dict(
    template="plotly_dark",
    paper_bgcolor="#0D1117",
    plot_bgcolor="#161B22",
    font=dict(color="#C9D1D9", family="monospace"),
    margin=dict(l=40, r=20, t=40, b=30),
)

def build_candle_chart(symbol: str, tf: str = "1h") -> go.Figure | None:
    rows = q(f"""
        SELECT first(open) o, max(high) h, min(low) l, last(close) c,
               sum(volume) v, timestamp ts
        FROM candles WHERE symbol='{symbol}' AND interval='1'
        SAMPLE BY {tf} ALIGN TO CALENDAR
        ORDER BY ts DESC LIMIT 200
    """)
    if not rows:
        return None
    rows = list(reversed(rows))
    fig = go.Figure(go.Candlestick(
        x    =[r[5] for r in rows],
        open =[r[0] for r in rows],
        high =[r[1] for r in rows],
        low  =[r[2] for r in rows],
        close=[r[3] for r in rows],
        increasing=dict(line=dict(color="#00D4AA"), fillcolor="#00D4AA"),
        decreasing=dict(line=dict(color="#FF6B6B"), fillcolor="#FF6B6B"),
        name="Price",
    ))
    fig.update_layout(
        title=f"{symbol} ({tf.upper()})", xaxis_rangeslider_visible=False,
        height=380, **PLOTLY_DARK
    )
    return fig


def build_cvd_chart(symbol: str, market_type: str, color: str, title: str) -> go.Figure | None:
    rows = q(f"""
        SELECT timestamp ts,
               sum(CASE WHEN side='Buy' THEN size ELSE -size END) net
        FROM trades
        WHERE symbol='{symbol}' AND market_type='{market_type}'
          AND timestamp > dateadd('h', -4, now())
        SAMPLE BY 1m ALIGN TO CALENDAR ORDER BY ts
    """)
    if not rows or len(rows) < 3:
        return None
    net_vols = [float(r[1] or 0) for r in rows]
    cvd = list(pd.Series(net_vols).cumsum())
    fig = go.Figure(go.Scatter(
        x=[r[0] for r in rows], y=cvd,
        line=dict(color=color, width=1.5),
        fill="tozeroy", fillcolor=color.replace(")", ", 0.1)").replace("rgb", "rgba") if "rgb" in color else color,
        name=title,
    ))
    fig.add_hline(y=0, line=dict(color="#555", dash="dot"), line_width=1)
    fig.update_layout(title=title, height=180, showlegend=False, **PLOTLY_DARK)
    fig.update_xaxes(showticklabels=False)
    return fig


def build_dex_cvd_chart(dex_data: dict) -> go.Figure | None:
    if not dex_data or not dex_data.get("available"):
        return None
    delta = dex_data.get("cvd_delta", dex_data.get("cvd_delta_eth", 0))
    color = "#00D4AA" if delta >= 0 else "#FF6B6B"
    fig = go.Figure(go.Bar(
        x=["FusionX DEX CVD (4H)"],
        y=[delta],
        marker_color=color,
        text=[f"{delta:+.3f} ETH"],
        textposition="outside",
    ))
    fig.update_layout(
        title=f"Mantle FusionX DEX CVD ({dex_data.get('swap_count',0)} swaps)",
        height=180, showlegend=False, **PLOTLY_DARK
    )
    return fig


# ── SIDEBAR ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ Controls")
    selected_symbol = st.selectbox("Symbol", SYMBOLS, key="global_symbol")

    st.markdown("---")
    deployment = load_deployment()
    if deployment:
        st.markdown(f"**Contract:** `{deployment['address'][:12]}...`")
        st.markdown(f"[View on Explorer]({deployment.get('explorer_url','')})")
    else:
        st.warning("Contract not deployed yet")

    st.markdown("---")
    st.markdown("**Data sources:**")
    st.markdown("- Bybit WS (futures trades/OI)")
    st.markdown("- Binance WS (spot + futures trades)")
    st.markdown("- Mantle FusionX (DEX swaps)")
    st.markdown("- mETH Staking (Mantle DeFi)")

    st.markdown("---")
    if st.button("🔄 Refresh Analysis", type="primary", use_container_width=True):
        with st.spinner(f"Running playbook analysis for all symbols..."):
            try:
                result = subprocess.run(
                    ["python", str(Path(__file__).parent.parent / "run_pipeline.py")],
                    capture_output=True, text=True, timeout=300,
                    cwd=str(Path(__file__).parent.parent),
                )
                if result.returncode == 0:
                    st.success("Analysis complete ✓")
                else:
                    st.error(f"Pipeline error:\n{result.stderr[:300]}")
            except subprocess.TimeoutExpired:
                st.warning("Analysis timed out (>5 min). Results may be partial.")
        st.rerun()


# ── HEADER ────────────────────────────────────────────────────────────────────

st.markdown("""
<div style='text-align:center; padding:10px 0 4px 0'>
<h1 style='color:#00D4AA; margin:0; font-size:2rem'>🧠 Mantle AI Trading Copilot</h1>
<p style='color:#aaa; margin:4px 0 2px 0; font-size:1rem'>
  Playbook-Driven · Explainable by Design · Every Recommendation Audited On-Chain Before You Trade
</p>
<p style='color:#555; font-size:0.8rem; margin:0'>
  Powered by cross-exchange CVD (Bybit + Binance) · Mantle FusionX DEX CVD · mETH Yield Baseline · Groq llama-3.3-70b
</p>
</div>
""", unsafe_allow_html=True)

# ── LIVE SIGNAL BOARD — all 6 symbols at a glance ────────────────────────────
st.markdown("---")
st.markdown("#### 📡 Live Signal Board")

_verdict_bg = {
    "STRONG_LONG":  "#0D2B1A", "LONG":  "#0D2B1A",
    "STRONG_SHORT": "#2B0D0D", "SHORT": "#2B0D0D",
    "NO_TRADE": "#1A1A0D", "NEUTRAL": "#1A1A1A",
}
_verdict_fg = {
    "STRONG_LONG": "#00D4AA", "LONG": "#00CC88",
    "STRONG_SHORT": "#FF6B6B", "SHORT": "#FF4444",
    "NO_TRADE": "#FFD700", "NEUTRAL": "#888888",
}

board_cols = st.columns(6)
for _i, _sym in enumerate(SYMBOLS):
    _d = load_analysis(_sym)
    with board_cols[_i]:
        if _d:
            _v = _d.get("analysis", {}).get("verdict", "?")
            _c = _d.get("analysis", {}).get("confluence_count", 0)
            _conf = _d.get("analysis", {}).get("confidence_score", 0)
            _price = _d.get("current_price", 0)
            _bg = _verdict_bg.get(_v, "#1A1A1A")
            _fg = _verdict_fg.get(_v, "#888")
            _icon, _ = VERDICT_ICONS.get(_v, ("⚪", "info"))
            st.markdown(f"""
<div style='background:{_bg}; border:1px solid {_fg}; border-radius:6px; padding:8px; text-align:center; cursor:pointer'>
  <div style='color:#888; font-size:0.7rem; font-weight:bold'>{_sym.replace('USDT','')}/USDT</div>
  <div style='color:{_fg}; font-size:0.85rem; font-weight:bold; margin:2px 0'>{_icon} {_v}</div>
  <div style='color:#aaa; font-size:0.7rem'>{_c}/12 conf · {_conf}%</div>
  <div style='color:#666; font-size:0.65rem'>${_price:,.1f}</div>
</div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""
<div style='background:#111; border:1px solid #333; border-radius:6px; padding:8px; text-align:center'>
  <div style='color:#888; font-size:0.7rem'>{_sym.replace('USDT','')}/USDT</div>
  <div style='color:#555; font-size:0.75rem'>No data</div>
</div>""", unsafe_allow_html=True)

st.markdown("---")

# ── TABS ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3 = st.tabs(["📈 Market Intelligence", "🤖 AI Analysis", "⛓ On-Chain Audit"])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — Market Intelligence
# ════════════════════════════════════════════════════════════════════════════

with tab1:
    sym = selected_symbol
    tf_col, _ = st.columns([1, 5])
    with tf_col:
        tf = st.radio("Timeframe", ["1h","4h","1d"], horizontal=True, key="tf")

    db_ok = _db() is not None

    if not db_ok:
        st.error("⚠️ QuestDB not reachable — start `docker compose up -d` first")
    else:
        # Candlestick chart
        candle_fig = build_candle_chart(sym, tf)
        if candle_fig:
            st.plotly_chart(candle_fig, use_container_width=True)
        else:
            st.info(f"No candle data yet for {sym} — collectors starting up...")

        # CVD row
        st.markdown("#### Cumulative Volume Delta")
        c1, c2, c3 = st.columns(3)

        with c1:
            spot_fig = build_cvd_chart(sym, "spot", "#00D4AA", f"Spot CVD — Binance {sym} (4H)")
            if spot_fig:
                st.plotly_chart(spot_fig, use_container_width=True)
            else:
                st.info("Spot CVD: Binance collector starting up...")

        with c2:
            fut_fig = build_cvd_chart(sym, "futures", "#FF6B6B", f"Futures CVD — Cross-Exchange (4H)")
            if fut_fig:
                st.plotly_chart(fut_fig, use_container_width=True)
            else:
                st.info("Futures CVD: collecting...")

        with c3:
            # Load Mantle DEX CVD from latest analysis if available
            analysis_data = load_analysis(sym)
            mantle = analysis_data.get("mantle_signals") if analysis_data else None
            dex_data = mantle.get("fusionx_dex_cvd") if mantle else None
            dex_fig = build_dex_cvd_chart(dex_data)
            if dex_fig:
                st.plotly_chart(dex_fig, use_container_width=True)
            else:
                st.info("Mantle FusionX DEX CVD: loading from on-chain...")

        # OI + Funding row
        st.markdown("#### Market Structure")
        m1, m2, m3, m4 = st.columns(4)

        oi_rows = q(f"SELECT open_interest, timestamp FROM open_interest WHERE symbol='{sym}' AND interval='5min' ORDER BY timestamp DESC LIMIT 1")
        if oi_rows:
            with m1:
                st.metric("Open Interest", f"${float(oi_rows[0][0]):,.0f}")

        fr_rows = q(f"SELECT funding_rate, timestamp FROM funding_rates WHERE symbol='{sym}' ORDER BY timestamp DESC LIMIT 1")
        if fr_rows:
            fr = float(fr_rows[0][0])
            with m2:
                st.metric("Funding Rate (8h)", f"{fr*100:.4f}%", delta="pos" if fr > 0 else "neg" if fr < 0 else "neutral")

        liq_rows = q(f"SELECT sum(price*size), count() FROM liquidations WHERE symbol='{sym}' AND timestamp > dateadd('h',-24,now())")
        if liq_rows:
            with m3:
                st.metric("Liq. Events (24H)", f"${float(liq_rows[0][0] or 0):,.0f}", delta=f"{int(liq_rows[0][1] or 0)} trades")

        if analysis_data:
            with m4:
                session = analysis_data.get("session", "?")
                session_icons = {"NY":"🔵","LONDON":"🟣","ASIAN":"🟤","DEAD_HOURS":"⚫"}
                st.metric("Session", f"{session_icons.get(session,'⚪')} {session}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — AI Analysis
# ════════════════════════════════════════════════════════════════════════════

with tab2:
    sym2 = selected_symbol
    data = load_analysis(sym2)

    if data is None:
        st.info(f"No analysis for {sym2} yet. Click '🔄 Refresh Analysis' in the sidebar.")
        st.stop()

    analysis  = data.get("analysis", {})
    macro     = data.get("macro_regime", {})
    mantle2   = data.get("mantle_signals")
    s_num     = data.get("scenario_number", 0)
    s_name    = data.get("scenario_name", "")

    # ── Header row ───────────────────────────────────────────────────────────
    regime_icons = {"BULL":"🟢","BEAR":"🔴","TRANSITION":"🟡"}
    session_icons = {"NY":"🔵 NY Open","LONDON":"🟣 London","ASIAN":"🟤 Asian","DEAD_HOURS":"⚫ Dead Hours"}

    col_r, col_s, col_p, col_t = st.columns(4)
    with col_r:
        st.markdown(f"### {regime_icons.get(macro.get('macro_regime',''),'⚪')} {macro.get('macro_regime','?')} Regime")
    with col_s:
        st.markdown(f"### {session_icons.get(data.get('session',''),'?')}")
    with col_p:
        st.markdown(f"### ${data.get('current_price', 0):,.2f}")
    with col_t:
        ts = data.get("timestamp_utc", "")[:16].replace("T", " ")
        st.markdown(f"### 🕐 {ts} UTC")

    # ── CVD Matrix state ─────────────────────────────────────────────────────
    cvd_state = data.get("cvd_matrix_state", "?")
    cvd_descriptions = {
        "BOTH_RISING":       "Real money (spot) AND leveraged money (futures) both buying — GENUINE ACCUMULATION",
        "BOTH_FALLING":      "Real money AND leveraged money both selling — GENUINE DISTRIBUTION",
        "FUT_UP_SPOT_FLAT":  "Futures rising BUT Spot flat/falling — SPECULATIVE PUMP, trap likely",
        "FUT_DOWN_SPOT_FLAT":"Futures falling BUT Spot flat/rising — SMART MONEY ACCUMULATING quietly",
    }
    cvd_icon = CVD_ICONS.get(cvd_state, "⚪")
    st.markdown(f"""
<div style='background:#161B22; border-left:4px solid #00D4AA; padding:12px 16px; border-radius:4px; margin:8px 0'>
<b>CVD Matrix State:</b> {cvd_icon} <b>{cvd_state}</b><br>
<span style='color:#888; font-size:0.9em'>{cvd_descriptions.get(cvd_state,'')}</span>
</div>
""", unsafe_allow_html=True)

    # ── Decision Tree Trace ───────────────────────────────────────────────────
    with st.expander(f"📋 Phase ID Decision Tree → S{s_num}: {SCENARIO_NAMES.get(s_num, s_name)}", expanded=True):
        trace = data.get("decision_tree_trace", {})
        trace_col1, trace_col2 = st.columns(2)
        with trace_col1:
            st.json(trace)
        with trace_col2:
            st.markdown(f"""
**Scenario identified: S{s_num} — {SCENARIO_NAMES.get(s_num, s_name)}**

Decision trace (deterministic, no LLM):
{"✓ Range Shortcut triggered — price oscillating near VWMA, CVD flat" if trace.get('range_shortcut') else
 f"Q1 Price {'above' if trace.get('Q1_above_vwma') else 'below'} VWMA → " +
 (f"Q2A Spot CVD {'rising' if trace.get('Q2A_spot_cvd_rising') else 'NOT rising'}" if 'Q2A_spot_cvd_rising' in trace else
  f"Q2B Spot CVD {'falling' if trace.get('Q2B_spot_cvd_falling') else 'NOT falling'}" if 'Q2B_spot_cvd_falling' in trace else "")
}
""")

    # ── 12-Indicator Confluence Scorecard ────────────────────────────────────
    st.markdown("### 📊 12-Indicator Confluence Scorecard")
    indicators = analysis.get("indicator_scores", [])
    confluence = analysis.get("confluence_count", 0)
    meets_min  = analysis.get("meets_minimum", False)

    if indicators:
        cols = st.columns(4)
        for i, ind in enumerate(indicators):
            sig   = ind.get("signal", "NEUTRAL")
            score = ind.get("score", 0)
            color = SIGNAL_COLORS.get(sig, "#888")
            with cols[i % 4]:
                st.markdown(f"""
<div style='background:#161B22; border-left:3px solid {color}; padding:8px 10px; margin:3px 0; border-radius:3px'>
<b style='color:{color}'> {'✓' if score == 1 else '✗'} {ind.get('indicator','?')}</b><br>
<span style='color:#888; font-size:0.8em'>{sig}</span><br>
<span style='font-size:0.75em'>{ind.get('reading','')[:60]}</span>
</div>
""", unsafe_allow_html=True)

        # Mantle 13th signal
        mantle_score = analysis.get("mantle_signal_score", {})
        if mantle_score:
            ms  = mantle_score.get("signal", "NEUTRAL")
            msc = mantle_score.get("score", 0)
            mcol = SIGNAL_COLORS.get(ms, "#888")
            st.markdown(f"""
<div style='background:#0D1F1A; border-left:3px solid {mcol}; padding:8px 10px; margin:3px 0; border-radius:3px'>
<b style='color:{mcol}'> {'✓' if msc == 1 else '✗'} Mantle Signal (13th)</b><br>
<span style='color:#888; font-size:0.8em'>{ms}</span><br>
<span style='font-size:0.75em'>{mantle_score.get('reading','')[:80]}</span>
</div>
""", unsafe_allow_html=True)

        total = confluence + (analysis.get("mantle_signal_score", {}).get("score", 0))
        min_c = macro.get("min_confluences", 5)
        st.markdown(f"""
**Confluence Score: {confluence}/12 (+{analysis.get('mantle_signal_score',{}).get('score',0)} Mantle)
= {total}/13 | Minimum required: {min_c} | Status: {'✅ MEETS MINIMUM' if meets_min else '❌ BELOW MINIMUM'}**
""")

    # ── Bull/Bear Debate ──────────────────────────────────────────────────────
    st.markdown("### ⚔️ Structured Bull/Bear Debate")
    bull_col, bear_col = st.columns(2)
    with bull_col:
        st.markdown("#### 🐂 Bull Agent")
        st.success(analysis.get("bull_case", "No bull case generated"))
    with bear_col:
        st.markdown("#### 🐻 Bear Agent")
        st.error(analysis.get("bear_case", "No bear case generated"))

    # ── Final Verdict Card ────────────────────────────────────────────────────
    verdict = analysis.get("verdict", "NEUTRAL")
    conf    = analysis.get("confidence_score", 0)
    lev     = analysis.get("leverage_recommended", 1)
    stop    = analysis.get("stop_price", 0)
    t1      = analysis.get("target_1", 0)
    t2      = analysis.get("target_2", 0)
    pos_sz  = analysis.get("position_size_coins", 0)

    v_icon, v_type = VERDICT_ICONS.get(verdict, ("⚪", "info"))

    st.markdown("### 🎯 Final Verdict")
    v_container = getattr(st, v_type)
    v_container(f"""
**{v_icon} {verdict}** — Confidence: {conf}/100 | Confluence: {confluence}/12 | Leverage: {lev}x

**Entry Trigger:** {analysis.get('entry_trigger', 'N/A')}

**Stop Loss:** ${stop:,.4f} — *{analysis.get('stop_reasoning', '')}*

**Targets:** T1 = ${t1:,.4f} | T2 = ${t2:,.4f}

**Position Size:** {pos_sz} {sym2.replace('USDT','')} (~$100 risk at $10k account / {lev}x leverage)

**Session adjustment:** {analysis.get('session_adjustment', 'N/A')}

**Regime adjustment:** {analysis.get('regime_adjustment', 'N/A')}
""")

    # ── Pre-Trade Note ────────────────────────────────────────────────────────
    st.markdown("### 📝 Pre-Trade Commitment Note")
    st.markdown("""*This note is generated BEFORE the trade and locked. It defines what would invalidate
the thesis — the foundation of auditable, transparent trading decisions (Tom Hougaard, Part 9).*""")

    ptn_col1, ptn_col2, ptn_col3 = st.columns(3)
    with ptn_col1:
        st.markdown("**Why this works:**")
        st.info(analysis.get("pre_trade_note_why", ""))
    with ptn_col2:
        st.markdown("**What proves me wrong:**")
        st.warning(analysis.get("pre_trade_note_wrong", ""))
    with ptn_col3:
        st.markdown("**What triggers an add:**")
        st.success(analysis.get("pre_trade_note_add", ""))

    # ── Mantle DeFi Signal ────────────────────────────────────────────────────
    if mantle2:
        st.markdown("### 🔮 Mantle Ecosystem Signals")
        meth = mantle2.get("meth_yield_signal", {})
        dex  = mantle2.get("fusionx_dex_cvd", {})

        m_col1, m_col2 = st.columns(2)
        with m_col1:
            st.markdown("**mETH Yield Baseline (Mantle Liquid Staking)**")
            if meth.get("available"):
                meth_sig = meth.get("signal", "NEUTRAL")
                meth_col = {"BULLISH": "success", "BEARISH": "error", "NEUTRAL": "info"}.get(meth_sig, "info")
                getattr(st, meth_col)(f"""
**{meth_sig}** — {meth.get('reasoning', '')}

mETH APY: **{meth.get('meth_apy_pct', 'N/A')}%** | ETH Funding (annualized): **{meth.get('eth_funding_annualized_pct', 'N/A')}%**
Carry edge: **{meth.get('carry_edge_bps', 'N/A')} bps** ({'+' if (meth.get('carry_edge_bps') or 0) > 0 else ''}favours {'longs' if (meth.get('carry_edge_bps') or 0) > 0 else 'staking'})
""")
            else:
                st.warning(f"mETH signal unavailable: {meth.get('reason', 'query failed')}")

        with m_col2:
            st.markdown("**FusionX DEX CVD (Mantle On-Chain)**")
            if dex.get("available"):
                dex_sig = dex.get("direction", "flat")
                dex_col = {"rising": "success", "falling": "error", "flat": "info"}.get(dex_sig, "info")
                getattr(st, dex_col)(f"""
**{dex_sig.upper()}** — {dex.get('interpretation', '')}

Net ETH flow: **{dex.get('cvd_delta_eth', 0):+.3f} ETH** across {dex.get('swap_count', 0)} swaps ({dex.get('blocks_scanned', 0)} blocks)
Pool: `{dex.get('pool_address', 'N/A')[:20]}...` on Mantle Mainnet
""")
            else:
                st.info(f"FusionX DEX CVD: {dex.get('reason', 'unavailable')}")

        combined = mantle2.get("combined_direction", "NEUTRAL")
        combined_col = "success" if "BULLISH" in combined else "error" if "BEARISH" in combined else "info"
        getattr(st, combined_col)(f"**Mantle Combined Signal: {combined}** — {mantle2.get('combined_note', '')}")

    # ── Playbook Rules Cited ──────────────────────────────────────────────────
    with st.expander("📚 Playbook Rules Cited in This Analysis"):
        for rule in analysis.get("playbook_rules_cited", []):
            st.markdown(f"- {rule}")
        st.markdown(f"\n*Failure mode: {analysis.get('failure_mode', 'N/A')}*")


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — On-Chain Audit
# ════════════════════════════════════════════════════════════════════════════

with tab3:
    st.markdown("""
### ⛓ Verifiable AI — On-Chain Audit Trail on Mantle Sepolia

**The problem with AI trading signals:** Anyone can claim their AI predicted a move *after* it happened. There's no way to verify the signal was generated *before* the price moved.

**Our solution:** Every recommendation is keccak256-hashed and logged to Mantle Sepolia *before* you act. The block timestamp is the cryptographic proof. Judges, users, and regulators can verify any past recommendation in seconds.

**What's hashed:** Symbol · Verdict · Confluence count · Entry trigger · Stop price · Pre-trade note (Why/Wrong/Add) · Playbook rules cited — sorted, deterministic, reproducible.
""")

    sym3 = selected_symbol
    data3 = load_analysis(sym3)
    depl  = load_deployment()

    if depl:
        st.markdown(f"""
**Contract:** [`{depl['address']}`]({depl.get('explorer_url','')})
| Network: Mantle Sepolia (chainId 5003)
| Deployed: {depl.get('deployed_at_utc','?')[:16]} UTC
""")

    if data3 is None:
        st.info("No analysis yet. Run analysis from the sidebar.")
    else:
        tx_hash  = data3.get("audit_tx_hash")
        exp_url  = data3.get("audit_explorer_url")
        d_hash   = data3.get("data_hash")
        payload  = data3.get("hash_payload_json")

        if tx_hash:
            st.success(f"✅ On-chain audit confirmed — Block: {data3.get('audit_block', 'N/A')}")
            st.markdown(f"**Transaction:** [`{tx_hash}`]({exp_url})")
            st.markdown(f"**Mantle Explorer:** [{exp_url}]({exp_url})")
        else:
            st.warning("Analysis complete but not yet submitted on-chain. Check wallet MNT balance.")

        if d_hash:
            st.markdown("#### Data Hash (keccak256)")
            st.code(d_hash, language="text")
            st.caption("Hash of the deterministic analysis JSON. Recomputable from the payload below.")

            # Verify button
            if st.button("🔍 Verify Hash Locally", key="verify_btn"):
                if payload and data3:
                    recomputed, _ = __import__("on_chain.submit_audit", fromlist=["compute_hash"]).compute_hash(data3)
                    match = recomputed.lower().lstrip("0x") == d_hash.lower().lstrip("0x")
                    if match:
                        st.success(f"✅ Hash verified! Recomputed: {recomputed[:20]}...  matches on-chain.")
                    else:
                        st.error(f"Hash mismatch!\nExpected:   {d_hash[:20]}...\nRecomputed: {recomputed[:20]}...")
                else:
                    st.warning("Cannot verify — payload not available")

        if payload:
            with st.expander("📄 Hashed Payload (Audit Evidence — judges can verify this)"):
                try:
                    st.json(json.loads(payload))
                except Exception:
                    st.code(payload, language="json")
                st.markdown("""
*This exact JSON (sort_keys=True, no whitespace) was keccak256-hashed and stored on-chain.
To verify: `web3.keccak(text=payload_json).hex()` must match the hash above.*
""")


# ── Footer ────────────────────────────────────────────────────────────────────

st.markdown("---")
depl_footer = load_deployment()
contract_addr = depl_footer.get("address", "not deployed") if depl_footer else "not deployed"
st.markdown(f"""
<div style='text-align:center; color:#444; font-size:0.75em; padding:8px 0'>
  <b style='color:#00D4AA'>Mantle AI Trading Copilot</b> · Mantle AI Awakening Hackathon Phase II — AI Trading & Strategy ·
  AuditLog: <code>{contract_addr[:18]}…</code> on Mantle Sepolia (chainId 5003) ·
  Groq llama-3.3-70b · Bybit + Binance + FusionX (Mantle) + mETH (Mantle Liquid Staking)
</div>
""", unsafe_allow_html=True)
