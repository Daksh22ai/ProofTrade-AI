// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title StrategyGate - Composable position-gating oracle for Mantle DeFi
 * @notice Any protocol on Mantle can call checkPositionAllowed() to gate
 *         leveraged trades based on the AI-determined market regime signal.
 *
 * EXAMPLE INTEGRATIONS:
 *   - Lending protocol: reduce max LTV when AI regime = BEAR
 *   - Perpetual DEX:    reject market orders above regime leverage cap
 *   - Vault strategy:   pause rebalancing when signal is NO_TRADE
 *
 * This contract makes TradingSignalOracle composable the AI signal becomes
 * a trustlessly-readable DeFi primitive on Mantle mainnet.
 *
 * Deployed alongside AuditLog.sol on Mantle Sepolia (chainId 5003).
 */

interface ITradingSignalOracle {
    function getLatestSignal(string calldata symbol)
        external view returns (
            string memory verdict,
            string memory macroRegime,
            uint8  confidence,
            uint8  confluenceCount,
            uint256 updatedAt,
            bool   exists
        );
}

contract StrategyGate {

    ITradingSignalOracle public immutable oracle;

    /// @dev Signals older than this are considered stale and reject all positions
    uint256 public constant STALENESS_LIMIT = 4 hours;

    struct PositionCheck {
        bool    allowed;
        uint8   maxLeverageAllowed;
        string  reason;
        string  regime;
        string  verdict;
        uint8   confidence;
        uint8   confluenceCount;
        uint256 signalAge;         // seconds since signal was last updated
    }

    event PositionChecked(
        address indexed caller,
        string  symbol,
        uint8   requestedLeverage,
        bool    allowed,
        uint8   maxLeverageAllowed,
        string  regime
    );

    constructor(address _oracle) {
        oracle = ITradingSignalOracle(_oracle);
    }

    /**
     * @notice Check whether a leveraged position is allowed given current AI signal.
     * @param symbol              Trading pair e.g. "BTCUSDT"
     * @param requestedLeverage   Leverage the user wants (1–125)
     * @param minConfidenceRequired Minimum confidence score required (0–100). Use 0 to skip.
     * @return check              Full position check result
     *
     * Leverage caps by regime (matches the trading playbook exactly):
     *   BULL   + confluence 9+  → 10x max
     *   BULL   + confluence 7+  → 7x max
     *   BULL   + below 7        → 5x max
     *   BEAR                    → 3x max (absolute override from playbook)
     *   TRANSITION              → 5x max
     */
    function checkPositionAllowed(
        string  calldata symbol,
        uint8   requestedLeverage,
        uint8   minConfidenceRequired
    ) external returns (PositionCheck memory check) {
        (
            string memory verdict,
            string memory regime,
            uint8  confidence,
            uint8  confluence,
            uint256 updatedAt,
            bool   exists
        ) = oracle.getLatestSignal(symbol);

        uint256 age = block.timestamp > updatedAt ? block.timestamp - updatedAt : 0;

        // ── Signal existence + freshness ──────────────────────────────────────
        if (!exists) {
            check = PositionCheck(false, 0, "No AI signal for symbol", "", "", 0, 0, 0);
            emit PositionChecked(msg.sender, symbol, requestedLeverage, false, 0, "");
            return check;
        }

        if (age > STALENESS_LIMIT) {
            check = PositionCheck(
                false, 0,
                string(abi.encodePacked("Signal stale (", _uint2str(age / 3600), "h old, max 4h)")),
                regime, verdict, confidence, confluence, age
            );
            emit PositionChecked(msg.sender, symbol, requestedLeverage, false, 0, regime);
            return check;
        }

        // ── Confidence threshold ──────────────────────────────────────────────
        if (minConfidenceRequired > 0 && confidence < minConfidenceRequired) {
            check = PositionCheck(
                false, 0,
                string(abi.encodePacked(
                    "Confidence ", _uint2str(confidence),
                    " below required ", _uint2str(minConfidenceRequired)
                )),
                regime, verdict, confidence, confluence, age
            );
            emit PositionChecked(msg.sender, symbol, requestedLeverage, false, 0, regime);
            return check;
        }

        // ── NO_TRADE verdict gate all positions ─────────────────────────────
        bytes32 verdictHash = keccak256(bytes(verdict));
        if (verdictHash == keccak256(bytes("NO_TRADE")) ||
            verdictHash == keccak256(bytes("NEUTRAL"))) {
            check = PositionCheck(
                false, 0,
                string(abi.encodePacked("AI verdict is ", verdict, " - no position recommended")),
                regime, verdict, confidence, confluence, age
            );
            emit PositionChecked(msg.sender, symbol, requestedLeverage, false, 0, regime);
            return check;
        }

        // ── Leverage cap by regime + confluence ───────────────────────────────
        uint8 maxLev = _maxLeverageForRegimeAndConfluence(regime, confluence);

        if (requestedLeverage > maxLev) {
            check = PositionCheck(
                false, maxLev,
                string(abi.encodePacked(
                    "Requested ", _uint2str(requestedLeverage), "x exceeds ",
                    regime, " regime cap of ", _uint2str(maxLev), "x"
                )),
                regime, verdict, confidence, confluence, age
            );
            emit PositionChecked(msg.sender, symbol, requestedLeverage, false, maxLev, regime);
            return check;
        }

        // ── Approved ──────────────────────────────────────────────────────────
        check = PositionCheck(
            true, maxLev,
            string(abi.encodePacked(
                "Position approved: ", verdict,
                " | ", regime, " regime",
                " | ", _uint2str(confluence), "/12 confluence",
                " | ", _uint2str(confidence), "% confidence"
            )),
            regime, verdict, confidence, confluence, age
        );
        emit PositionChecked(msg.sender, symbol, requestedLeverage, true, maxLev, regime);
        return check;
    }

    /**
     * @notice Read-only version no gas cost event, suitable for static calls.
     */
    function checkPositionAllowedView(
        string  calldata symbol,
        uint8   requestedLeverage,
        uint8   minConfidenceRequired
    ) external view returns (bool allowed, uint8 maxLev, string memory reason) {
        (
            string memory verdict,
            string memory regime,
            uint8  confidence,
            uint8  confluence,
            uint256 updatedAt,
            bool   exists
        ) = oracle.getLatestSignal(symbol);

        if (!exists)
            return (false, 0, "No signal");
        if (block.timestamp - updatedAt > STALENESS_LIMIT)
            return (false, 0, "Signal stale");
        if (minConfidenceRequired > 0 && confidence < minConfidenceRequired)
            return (false, 0, "Confidence too low");

        bytes32 vh = keccak256(bytes(verdict));
        if (vh == keccak256(bytes("NO_TRADE")) || vh == keccak256(bytes("NEUTRAL")))
            return (false, 0, string(abi.encodePacked("Verdict: ", verdict)));

        maxLev = _maxLeverageForRegimeAndConfluence(regime, confluence);
        if (requestedLeverage > maxLev)
            return (false, maxLev, string(abi.encodePacked(
                "Exceeds ", regime, " cap: ", _uint2str(maxLev), "x"
            )));

        return (true, maxLev, "Approved");
    }

    // ── Internal helpers ──────────────────────────────────────────────────────

    function _maxLeverageForRegimeAndConfluence(
        string memory regime,
        uint8 confluence
    ) internal pure returns (uint8) {
        bytes32 r = keccak256(bytes(regime));
        if (r == keccak256(bytes("BEAR")))       return 3;   // playbook absolute cap
        if (r == keccak256(bytes("TRANSITION"))) return 5;
        // BULL regime: confluence-scaled table from playbook Part 8
        if (confluence >= 9) return 10;
        if (confluence >= 7) return 7;
        return 5;
    }

    function _uint2str(uint256 v) internal pure returns (string memory) {
        if (v == 0) return "0";
        uint256 tmp = v;
        uint256 digits;
        while (tmp != 0) { digits++; tmp /= 10; }
        bytes memory buf = new bytes(digits);
        while (v != 0) {
            digits--;
            buf[digits] = bytes1(uint8(48 + (v % 10)));
            v /= 10;
        }
        return string(buf);
    }
}
