# On-Chain: Mantle AI Trading Copilot

Hardhat project for the two Mantle Sepolia contracts that form the trust layer of the system.

## Deployed Contracts (Mantle Sepolia, chainId 5003)

| Contract | Address | Explorer |
|---|---|---|
| AuditLog (TradingSignalOracle v2) | `0xdc9AF27a1C764871b71B478A2D6D3FA2cB442Cd4` | [View](https://explorer.sepolia.mantle.xyz/address/0xdc9AF27a1C764871b71B478A2D6D3FA2cB442Cd4) |
| StrategyGate | `0x1150499F3D0E712a5a96FD4622656877E6700Ce3` | [View](https://explorer.sepolia.mantle.xyz/address/0x1150499F3D0E712a5a96FD4622656877E6700Ce3) |

Both contracts are deployed with the same wallet (`0x8C99f4a63218FFabce40c219d3f685703a5fC3ca`). Running `deploy.js` again with the same wallet will detect the existing deployment and skip redeployment.

## AuditLog.sol (TradingSignalOracle v2)

### Purpose

Provides two distinct on-chain capabilities:

1. **Audit trail**: Every analysis cycle calls `logAnalysis()` which emits an event containing the keccak256 hash of the full analysis output. The block timestamp proves the analysis existed at that moment, before any price action the user might observe.

2. **Live oracle**: `updateSignal()` writes the current regime, verdict, confidence, and confluence count into contract storage. Any other contract on Mantle can read this via `getLatestSignal(symbol)`.

### Key Functions

```solidity
// Called by submit_audit.py after every pipeline run
function logAnalysis(
    string calldata symbol,
    bytes32 dataHash,
    string calldata verdict,
    uint8 confidence,
    uint8 confluenceCount,
    string calldata scenarioName,
    string calldata snapshotHash
) external

// Also called after logAnalysis() to update the oracle state
function updateSignal(
    string calldata symbol,
    string calldata verdict,
    string calldata macroRegime,
    uint8 confidence,
    uint8 confluenceCount
) external

// Read by any contract or frontend
function getLatestSignal(string calldata symbol)
    external view returns (
        string memory verdict,
        string memory macroRegime,
        uint8 confidence,
        uint8 confluenceCount,
        uint256 updatedAt,
        bool exists
    )

// Convenience helper
function isBullish(string calldata symbol) external view returns (bool)
```

### Hash Verification

The `dataHash` stored on-chain is computed as:

```python
import json
from web3 import Web3

payload = {
    "symbol": "BTCUSDT",
    "analysis_timestamp_utc_minute": "2026-06-15T14:08:00Z",
    "verdict": "LONG",
    "confidence_score": 72,
    # ... full payload fields in submit_audit.py build_hashable_payload()
}
payload_json = json.dumps(payload, sort_keys=True, separators=(',', ':'))
data_hash = Web3.keccak(text=payload_json).hex()
```

Anyone can recompute this from the displayed analysis and compare to the on-chain event. The payload is stored in `analysis_results/{SYMBOL}_latest.json` as `hash_payload_json`.

## StrategyGate.sol

### Purpose

Makes the AI signal composable with DeFi. Any protocol on Mantle can call `checkPositionAllowedView()` to gate leveraged positions based on the current AI regime and verdict.

### Example Integration

A lending protocol that wants to reduce max LTV during BEAR regime:

```solidity
IStrategyGate gate = IStrategyGate(0x1150499F3D0E712a5a96FD4622656877E6700Ce3);
(bool allowed, uint8 maxLev, string memory reason) =
    gate.checkPositionAllowedView("BTCUSDT", requestedLeverage, 50);
require(allowed, reason);
```

### Leverage Caps (from the trading playbook)

| Regime | Confluence | Max Leverage |
|---|---|---|
| BEAR | any | 3x (absolute cap) |
| TRANSITION | any | 5x |
| BULL | below 7 | 5x |
| BULL | 7 or 8 | 7x |
| BULL | 9 or above | 10x |

Signals older than 4 hours are considered stale and all positions are rejected until a fresh analysis runs.

### Staleness

`STALENESS_LIMIT = 4 hours`. If the pipeline has not run in 4 hours, `checkPositionAllowedView()` returns `(false, 0, "Signal stale")`. This prevents protocols from acting on outdated regime data.

## Deployment

### First Deployment

```bash
cd on_chain
npx hardhat run scripts/deploy.js --network mantleSepolia
```

This deploys both contracts, writes `deployment.json`, and prints the addresses. The Python backend and frontend both read `deployment.json` to get the current addresses.

### Subsequent Runs

`deploy.js` checks whether `deployment.json` already contains a valid deployment from the same wallet on the same network. If the contracts still exist on-chain (verified via `getCode`), it skips deployment and prints the existing addresses. This ensures the audit trail is continuous across multiple runs.

To force a fresh deployment (not recommended unless contracts are broken):

```bash
rm on_chain/deployment.json
npx hardhat run scripts/deploy.js --network mantleSepolia
```

### Verification

```bash
npx hardhat verify --network mantleSepolia 0xdc9AF27a1C764871b71B478A2D6D3FA2cB442Cd4
npx hardhat verify --network mantleSepolia 0x1150499F3D0E712a5a96FD4622656877E6700Ce3 "0xdc9AF27a1C764871b71B478A2D6D3FA2cB442Cd4"
```

## `submit_audit.py`

Python module called by `run_pipeline.py` after every analysis cycle. Responsibilities:

1. Build the deterministic hashable payload from the analysis result
2. Compute `keccak256(payload)` as the `data_hash`
3. Compute `snapshot_hash` from raw indicator values (what the system observed)
4. Compute `playbook_prompt_hash` from the current prompt content (version fingerprint)
5. Submit `logAnalysis()` via EIP-1559 transaction with `estimate_gas()` plus 30% buffer
6. Submit `updateSignal()` to update the oracle state (non-fatal if it fails)
7. Return the transaction hash and explorer URL for inclusion in the analysis JSON

RPC fallback chain: `MANTLE_RPC` env var, then two public fallbacks for demo resilience.

## Gas Costs (approximate, Mantle Sepolia)

| Function | Gas Used | Cost at 0.002 gwei |
|---|---|---|
| logAnalysis() | ~80,000 | ~0.00016 MNT |
| updateSignal() | ~60,000 | ~0.00012 MNT |

With 6 symbols per run, each run costs approximately 0.002 MNT total.
