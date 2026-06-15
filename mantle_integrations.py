"""
mantle_integrations.py — Mantle ecosystem DeFi signals for the AI trading copilot.

1. mETH Yield Baseline — compare ETH perp funding rate to mETH staking APY.
   Fallback chain: on-chain mETHToETH → convertToAssets → Mantle API → DeFiLlama → 4.5%

2. FusionX / Agni Finance DEX CVD — on-chain Spot CVD from Mantle DEXes.
   Uses raw eth_getLogs (more reliable than ContractEvent.get_logs in web3.py v6).
   Fallback chain: FusionX WMNT/USDT → FusionX WMNT/USDC → Agni WMNT/USDT → WETH pairs.
"""

import os
import json
import logging
import time
from typing import Optional, Tuple
from datetime import datetime, timezone

import requests
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── RPC connections ────────────────────────────────────────────────────────────

MANTLE_MAINNET_RPC = os.getenv("MANTLE_MAINNET_RPC", "https://rpc.mantle.xyz")

_ETH_RPC_FALLBACKS = [
    os.getenv("ETHEREUM_MAINNET_RPC", ""),
    "https://cloudflare-eth.com",
    "https://rpc.ankr.com/eth",
    "https://ethereum.publicnode.com",
    "https://eth.llamarpc.com",
    "https://1rpc.io/eth",
]

_MANTLE_RPC_FALLBACKS = [
    os.getenv("MANTLE_MAINNET_RPC", ""),
    "https://rpc.mantle.xyz",
    "https://mantle.drpc.org",
    "https://1rpc.io/mantle",
]

_http = requests.Session()
_http.headers.update({"Accept": "application/json"})


def _eth_w3() -> Optional[Web3]:
    for rpc in _ETH_RPC_FALLBACKS:
        if not rpc:
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 8}))
            w3.eth.block_number   # connectivity probe
            return w3
        except Exception:
            continue
    logger.error("[mETH] All Ethereum mainnet RPCs failed")
    return None


def _mantle_w3() -> Optional[Web3]:
    for rpc in _MANTLE_RPC_FALLBACKS:
        if not rpc:
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            w3.eth.block_number
            return w3
        except Exception:
            continue
    logger.error("[FusionX] All Mantle mainnet RPCs failed")
    return None


# ── 1. mETH Yield Baseline ────────────────────────────────────────────────────

METH_ADDRESS = "0xd5F7838F5C461fefF7FE49ea5ebaF7728bB0ADfa"

# Try both the proprietary and ERC-4626 standard functions
METH_ABI = [
    {
        "inputs": [{"internalType": "uint256", "name": "mETHAmount", "type": "uint256"}],
        "name":   "mETHToETH",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "shares", "type": "uint256"}],
        "name":   "convertToAssets",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [],
        "name":   "exchangeRate",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view", "type": "function",
    },
]


def get_meth_exchange_rate() -> Optional[float]:
    """
    Returns mETH/ETH exchange rate via on-chain call.
    Tries mETHToETH → convertToAssets (ERC-4626) → exchangeRate across multiple RPCs.
    """
    w3 = _eth_w3()
    if w3 is None:
        return None

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(METH_ADDRESS),
        abi=METH_ABI,
    )
    ONE = 10 ** 18

    for fn_name in ("mETHToETH", "convertToAssets", "exchangeRate"):
        try:
            fn = getattr(contract.functions, fn_name)
            # exchangeRate takes no args; others take uint256
            result = fn(ONE).call() if fn_name != "exchangeRate" else fn().call()
            rate = result / ONE
            if 0.9 < rate < 2.0:   # sanity: mETH should be ~1 ETH
                logger.info(f"[mETH] On-chain rate via {fn_name}: {rate:.6f} ETH/mETH")
                return float(rate)
        except Exception as e:
            logger.debug(f"[mETH] {fn_name} failed: {e}")

    return None


def _fetch_meth_apy_from_apis() -> Optional[float]:
    """
    Fallback chain for mETH APY when on-chain rate is unavailable.
    Tries: Mantle staking API → DeFiLlama yields API.
    """
    # 1. Mantle's own mETH API
    for url in [
        "https://meth.mantle.xyz/api/v1/meth/apy",
        "https://meth.mantle.xyz/api/v1/meth/stats",
        "https://meth.mantle.xyz/api/v1/staking/apy",
    ]:
        try:
            r = _http.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                for key in ("apy", "stakingApy", "currentApy", "annualizedReturn"):
                    val = data.get(key) or (data.get("data") or {}).get(key)
                    if val is not None:
                        apy = float(val)
                        # API returns percent string ("4.72") or decimal (0.0472)
                        if apy > 1:
                            apy /= 100.0
                        if 0.01 < apy < 0.30:   # 1–30% is a sane APY range
                            logger.info(f"[mETH] APY from {url}: {apy*100:.2f}%")
                            return apy
        except Exception:
            continue

    # 2. DeFiLlama yields — search for mETH pool
    try:
        r = _http.get("https://yields.llama.fi/pools", timeout=8)
        if r.status_code == 200:
            pools = r.json().get("data", [])
            for pool in pools:
                sym = (pool.get("symbol") or "").upper()
                proj = (pool.get("project") or "").lower()
                if "meth" in sym or "mantle" in proj:
                    apy = pool.get("apy")
                    if apy and 1 < apy < 30:
                        logger.info(f"[mETH] APY from DeFiLlama ({pool.get('project')}): {apy:.2f}%")
                        return float(apy) / 100.0
    except Exception:
        pass

    return None


def estimate_meth_apy(rate_now: Optional[float],
                      rate_7d_ago: Optional[float] = None) -> Optional[float]:
    """
    Estimate annualised mETH yield.
    If on-chain rate available: compute from rate growth.
    Otherwise: try API chain → hardcoded floor.
    """
    if rate_now is not None and rate_7d_ago is not None and rate_7d_ago > 0:
        weekly = (rate_now / rate_7d_ago) - 1
        return float(weekly * (365 / 7))

    # On-chain rate unavailable or no 7d reference — try API chain
    api_apy = _fetch_meth_apy_from_apis()
    if api_apy is not None:
        return api_apy

    logger.info("[mETH] On-chain + API sources unavailable — using Mantle published staking baseline (4.5%)")
    return 0.045


def get_meth_yield_signal(eth_funding_rate_8h: float) -> dict:
    """
    Compare ETH 8h funding rate (annualised) to mETH staking APY.
    Always returns a signal — degrades gracefully through fallback chain.
    """
    meth_rate = get_meth_exchange_rate()
    meth_apy  = estimate_meth_apy(meth_rate)

    funding_annualized = eth_funding_rate_8h * 3 * 365

    if meth_apy is None:
        return {
            "available": False,
            "reason":    "mETH APY unavailable (all sources failed)",
            "signal":    "NEUTRAL",
            "meth_apy":  None,
            "eth_funding_annualized": funding_annualized,
            "carry_edge_bps": None,
        }

    carry_edge = funding_annualized - meth_apy

    if carry_edge > 0.01:
        signal    = "BULLISH"
        reasoning = (f"ETH perp longs yield {carry_edge*100:.1f}% more than mETH staking "
                     f"({meth_apy*100:.1f}% APY) — carry favours longs")
    elif carry_edge < -0.01:
        signal    = "BEARISH"
        reasoning = (f"mETH staking ({meth_apy*100:.1f}% APY) yields {-carry_edge*100:.1f}% "
                     f"more than ETH perp longs — structural incentive to unwind longs")
    else:
        signal    = "NEUTRAL"
        reasoning = (f"ETH funding ({funding_annualized*100:.1f}%) ≈ mETH APY "
                     f"({meth_apy*100:.1f}%) — no carry trade edge")

    # Flag whether rate was on-chain (real) or API/hardcoded (approximate)
    data_source = "on-chain (mETHToETH)" if meth_rate is not None else "mantle-staking-baseline-4.5%"

    return {
        "available":                  True,
        "signal":                     signal,
        "reasoning":                  reasoning,
        "data_source":                data_source,
        "meth_exchange_rate":         meth_rate,
        "meth_apy_pct":               round(meth_apy * 100, 2),
        "eth_funding_8h":             eth_funding_rate_8h,
        "eth_funding_annualized_pct": round(funding_annualized * 100, 2),
        "carry_edge_bps":             round(carry_edge * 10000, 1),
        "mantle_asset":               "mETH",
        "mantle_protocol":            "Mantle Liquid Staking",
    }


# ── 2. DEX CVD (FusionX + Agni Finance fallback) ─────────────────────────────
#
# Uses w3.eth.get_logs directly instead of ContractEvent.get_logs.
# ContractEvent.get_logs changed its API between web3.py v5 and v6 — the raw
# eth_getLogs approach is stable across all versions.

# Swap event topic: keccak256("Swap(address,address,int256,int256,uint160,uint128,int24)")
SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

# Pool ABI — only what we need for log decoding
POOL_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "name": "sender",       "type": "address"},
            {"indexed": True,  "name": "recipient",    "type": "address"},
            {"indexed": False, "name": "amount0",      "type": "int256"},
            {"indexed": False, "name": "amount1",      "type": "int256"},
            {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
            {"indexed": False, "name": "liquidity",    "type": "uint128"},
            {"indexed": False, "name": "tick",         "type": "int24"},
        ],
        "name": "Swap", "type": "event",
    }
]

FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24",  "name": "fee",    "type": "uint24"},
        ],
        "name":   "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view", "type": "function",
    }
]

# Mantle mainnet token addresses
WETH  = "0xdEAddEaDdeadDEadDEADDEAddEADDEAddead1111"
USDT  = "0x201EBa5CC46D216Ce6DC03F6a759e8E766e956aE"
USDC  = "0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9"
WMNT  = "0x78c1b0C915c4FAA5FffA6CAbf0219DA63d7f4cb8"

FEE_TIERS = [500, 3000, 10000]

# DEX factories to try in order — FusionX first, Agni Finance as fallback
DEX_FACTORIES = [
    ("FusionX",  "0x530d2766D1988CC1c000C8b7d00334c14B69AD71"),
    ("Agni",     "0x25780dc8Fc3cfBD75F33bFDAB65e969b603b2035"),
]

# Token pairs to try in order — WMNT pairs are most liquid on Mantle
POOL_PAIRS = [
    (WMNT, USDT,  "WMNT/USDT",  WMNT),   # (tokenA, tokenB, label, token0_is_base)
    (WMNT, USDC,  "WMNT/USDC",  WMNT),
    (WETH, USDT,  "WETH/USDT",  WETH),
    (WETH, USDC,  "WETH/USDC",  WETH),
]


def _find_pool(w3: Web3) -> Optional[Tuple[str, str, str, str]]:
    """
    Search all DEX factories × token pairs × fee tiers for an active pool.
    Returns (pool_address, dex_name, pair_label, base_token_address) or None.
    """
    for dex_name, factory_addr in DEX_FACTORIES:
        try:
            factory = w3.eth.contract(
                address=Web3.to_checksum_address(factory_addr),
                abi=FACTORY_ABI,
            )
            for token_a, token_b, label, base_token in POOL_PAIRS:
                for fee in FEE_TIERS:
                    try:
                        pool_addr = factory.functions.getPool(
                            Web3.to_checksum_address(token_a),
                            Web3.to_checksum_address(token_b),
                            fee,
                        ).call()
                        if pool_addr != "0x0000000000000000000000000000000000000000":
                            logger.info(
                                f"[DEX CVD] {dex_name} {label} pool found: "
                                f"{pool_addr[:12]}... (fee={fee})"
                            )
                            return pool_addr, dex_name, label, base_token
                    except Exception:
                        continue
        except Exception as e:
            logger.debug(f"[DEX CVD] {dex_name} factory query failed: {e}")
            continue
    return None


def _decode_swap_amount0(log: dict, w3: Web3) -> Optional[int]:
    """
    Decode amount0 (int256) from a raw Swap event log.
    amount0 is the first non-indexed field, encoded as 32-byte two's complement.
    """
    try:
        data = log["data"]
        raw  = bytes(data) if isinstance(data, (bytearray, memoryview)) else bytes.fromhex(
            data.hex() if isinstance(data, bytes) else data[2:]
        )
        # First 32 bytes = amount0 (int256)
        amount0_bytes = raw[:32]
        # Two's complement to signed int
        val = int.from_bytes(amount0_bytes, "big", signed=True)
        return val
    except Exception as e:
        logger.debug(f"[DEX CVD] Swap log decode error: {e}")
        return None


def get_fusionx_dex_cvd(lookback_blocks: int = 5000) -> dict:
    """
    Compute on-chain CVD from Mantle DEX Swap events.
    Default lookback = 5000 blocks ≈ 2.8 hours at ~2s/block.

    Uses raw eth_getLogs (stable across web3.py v5/v6) and manual int256 decoding.
    Tries FusionX first, then Agni Finance.

    CVD sign: amount0 < 0 → base token left pool → taker BOUGHT → positive delta.
    """
    w3 = _mantle_w3()
    if w3 is None:
        return {"available": False, "reason": "Mantle mainnet RPC unavailable"}

    pool_info = _find_pool(w3)
    if not pool_info:
        return {"available": False, "reason": "No active pool found on FusionX or Agni Finance"}

    pool_addr, dex_name, pair_label, base_token = pool_info

    latest_block = w3.eth.block_number
    from_block   = max(0, latest_block - lookback_blocks)

    try:
        logs = w3.eth.get_logs({
            "address":   Web3.to_checksum_address(pool_addr),
            "fromBlock": from_block,
            "toBlock":   latest_block,
            "topics":    [SWAP_TOPIC],
        })
    except Exception as e:
        # Some RPCs cap the block range for getLogs — try a smaller window
        logger.debug(f"[DEX CVD] get_logs failed ({e}) — retrying with 1000 blocks")
        try:
            from_block = latest_block - 1000
            logs = w3.eth.get_logs({
                "address":   Web3.to_checksum_address(pool_addr),
                "fromBlock": from_block,
                "toBlock":   latest_block,
                "topics":    [SWAP_TOPIC],
            })
            lookback_blocks = 1000
        except Exception as e2:
            return {"available": False, "reason": f"eth_getLogs failed: {e2}"}

    swap_count = len(logs)

    if swap_count == 0:
        return {
            "available":      True,
            "cvd_delta":      0.0,
            "direction":      "flat",
            "swap_count":     0,
            "pool_address":   pool_addr,
            "pair":           pair_label,
            "dex":            dex_name,
            "blocks_scanned": lookback_blocks,
            "minutes_scanned": lookback_blocks * 2 // 60,
            "reason": f"No swaps in last {lookback_blocks} blocks (~{lookback_blocks*2//60} min)",
        }

    # Decode CVD: sum of signed base-token flows
    total_delta = 0.0
    decoded     = 0
    for log in logs:
        amount0 = _decode_swap_amount0(log, w3)
        if amount0 is not None:
            # amount0 < 0 → base token OUT of pool → taker BOUGHT → +delta
            total_delta += (-amount0 / 1e18)
            decoded += 1

    if decoded == 0:
        return {"available": False, "reason": "Log decode failed for all Swap events"}

    # Direction threshold: WMNT at ~$0.40 → 500 WMNT ≈ $200 meaningful move
    asset     = pair_label.split("/")[0]
    threshold = 500.0 if "WMNT" in asset else 0.5

    if total_delta > threshold:     direction = "rising"
    elif total_delta < -threshold:  direction = "falling"
    else:                           direction = "flat"

    if direction == "rising":
        interp = (f"{dex_name} {asset} buyers net +{total_delta:.1f} {asset} "
                  f"in {decoded} swaps — on-chain accumulation on Mantle")
    elif direction == "falling":
        interp = (f"{dex_name} {asset} sellers net {total_delta:.1f} {asset} "
                  f"in {decoded} swaps — on-chain distribution on Mantle")
    else:
        interp = (f"{dex_name} {asset} in equilibrium — {decoded} swaps, "
                  f"net {total_delta:+.2f} {asset}")

    return {
        "available":      True,
        "cvd_delta":      round(total_delta, 4),
        "direction":      direction,
        "swap_count":     decoded,
        "pool_address":   pool_addr,
        "pair":           pair_label,
        "dex":            dex_name,
        "blocks_scanned": lookback_blocks,
        "from_block":     from_block,
        "to_block":       latest_block,
        "minutes_scanned": lookback_blocks * 2 // 60,
        "interpretation": interp,
    }


# ── Combined signal ────────────────────────────────────────────────────────────

def get_mantle_signals(eth_funding_rate_8h: float = 0.0) -> dict:
    """Entry point for data_aggregator.py. Returns both Mantle signals bundled."""
    logger.info("[Mantle] Fetching mETH yield signal...")
    meth_signal = get_meth_yield_signal(eth_funding_rate_8h)

    logger.info("[Mantle] Fetching DEX CVD from Mantle on-chain...")
    dex_cvd = get_fusionx_dex_cvd()

    meth_dir = meth_signal.get("signal")
    dex_dir  = dex_cvd.get("direction")

    if meth_dir == "BULLISH" and dex_dir == "rising":
        combined_direction = "BULLISH"
        combined_note = ("mETH carry favours longs AND Mantle DEX shows on-chain accumulation "
                         "— both Mantle-native signals are bullish")
    elif meth_dir == "BEARISH" and dex_dir == "falling":
        combined_direction = "BEARISH"
        combined_note = ("mETH carry discourages longs AND Mantle DEX shows net selling "
                         "— both Mantle-native signals are bearish")
    elif meth_dir == "BULLISH" or dex_dir == "rising":
        combined_direction = "CAUTIOUS_BULLISH"
        combined_note = "Partial Mantle ecosystem support for bulls"
    elif meth_dir == "BEARISH" or dex_dir == "falling":
        combined_direction = "CAUTIOUS_BEARISH"
        combined_note = "Partial Mantle ecosystem pressure on bulls"
    else:
        combined_direction = "NEUTRAL"
        combined_note = "Mantle ecosystem signals are neutral — no carry or DEX edge"

    return {
        "meth_yield_signal":  meth_signal,
        "fusionx_dex_cvd":    dex_cvd,
        "combined_direction": combined_direction,
        "combined_note":      combined_note,
        "timestamp_utc":      datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing Mantle integrations...")
    result = get_mantle_signals(eth_funding_rate_8h=0.0001)
    print(json.dumps(result, indent=2, default=str))
