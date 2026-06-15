"""
tests/test_hash.py

Tests for the on-chain hash computation — verifies determinism, format,
and that any change to the analysis output changes the hash.

These properties are critical: the hash is stored on Mantle Sepolia and
anyone can recompute it from the displayed analysis to verify integrity.

Run: pytest tests/ -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from on_chain.submit_audit import compute_hash, verify_hash, build_hashable_payload


BASE_RESULT = {
    "symbol":          "BTCUSDT",
    "timestamp_utc":   "2026-06-15T15:00:00+00:00",
    "scenario_number": 1,
    "scenario_name":   "HEALTHY_UPTREND",
    "cvd_matrix_state":"BOTH_RISING",
    "macro_regime":    {"macro_regime": "BULL"},
    "analysis": {
        "verdict":             "STRONG_LONG",
        "confidence_score":    88,
        "confluence_count":    10,
        "entry_trigger":       "Retest of $104,800 POC with Spot CVD bouncing",
        "stop_price":          103200.0,
        "target_1":            108500.0,
        "leverage_recommended": 10,
        "pre_trade_note_why":  "BOTH_RISING CVD + 10/12 confluence in S1",
        "pre_trade_note_wrong":"Spot CVD flips falling or price closes below 4H EMA",
        "pre_trade_note_add":  "Confirmed break of $108,500 with CVD new high",
        "failure_mode":        "Funding reset noise — wait 30min after 16:00 UTC",
        "playbook_rules_cited":["Part 0A: BULL regime", "Part 3: BOTH_RISING"],
    },
}


class TestHashDeterminism:
    def test_same_input_same_hash(self):
        h1, _ = compute_hash(BASE_RESULT)
        h2, _ = compute_hash(BASE_RESULT)
        assert h1 == h2, "Hash must be deterministic"

    def test_hash_is_hex_string(self):
        h, _ = compute_hash(BASE_RESULT)
        # Should be a 66-char hex string (0x + 64 chars)
        assert isinstance(h, str)
        assert len(h) == 66 or len(h) == 64  # with or without 0x prefix
        int(h.lstrip("0x"), 16)  # must be valid hex

    def test_payload_is_valid_json(self):
        import json
        _, payload = compute_hash(BASE_RESULT)
        parsed = json.loads(payload)
        assert isinstance(parsed, dict)

    def test_payload_has_required_fields(self):
        import json
        _, payload = compute_hash(BASE_RESULT)
        parsed = json.loads(payload)
        required = {
            "symbol", "verdict", "confidence_score", "confluence_count",
            "pre_trade_note_why", "pre_trade_note_wrong", "pre_trade_note_add",
            "playbook_rules_cited", "scenario", "cvd_matrix_state", "macro_regime"
        }
        for field in required:
            assert field in parsed, f"Missing required field in payload: {field}"


class TestHashSensitivity:
    def test_different_verdict_different_hash(self):
        import copy
        result2 = copy.deepcopy(BASE_RESULT)
        result2["analysis"]["verdict"] = "NO_TRADE"
        h1, _ = compute_hash(BASE_RESULT)
        h2, _ = compute_hash(result2)
        assert h1 != h2

    def test_different_symbol_different_hash(self):
        import copy
        result2 = copy.deepcopy(BASE_RESULT)
        result2["symbol"] = "ETHUSDT"
        h1, _ = compute_hash(BASE_RESULT)
        h2, _ = compute_hash(result2)
        assert h1 != h2

    def test_different_confluence_different_hash(self):
        import copy
        result2 = copy.deepcopy(BASE_RESULT)
        result2["analysis"]["confluence_count"] = 5
        h1, _ = compute_hash(BASE_RESULT)
        h2, _ = compute_hash(result2)
        assert h1 != h2

    def test_timestamp_rounded_to_minute(self):
        """Two analyses at the same minute should hash the same (for reproducibility)."""
        import copy
        r1 = copy.deepcopy(BASE_RESULT)
        r2 = copy.deepcopy(BASE_RESULT)
        r1["timestamp_utc"] = "2026-06-15T15:00:01+00:00"
        r2["timestamp_utc"] = "2026-06-15T15:00:59+00:00"
        h1, _ = compute_hash(r1)
        h2, _ = compute_hash(r2)
        assert h1 == h2, "Same minute should produce same hash (demo safety)"

    def test_different_minute_different_hash(self):
        import copy
        r1 = copy.deepcopy(BASE_RESULT)
        r2 = copy.deepcopy(BASE_RESULT)
        r1["timestamp_utc"] = "2026-06-15T15:00:00+00:00"
        r2["timestamp_utc"] = "2026-06-15T15:01:00+00:00"
        h1, _ = compute_hash(r1)
        h2, _ = compute_hash(r2)
        assert h1 != h2


class TestVerification:
    def test_verify_passes_on_correct_hash(self):
        h, _ = compute_hash(BASE_RESULT)
        assert verify_hash(BASE_RESULT, h) is True

    def test_verify_fails_on_wrong_hash(self):
        fake_hash = "0x" + "ab" * 32
        assert verify_hash(BASE_RESULT, fake_hash) is False

    def test_verify_case_insensitive(self):
        h, _ = compute_hash(BASE_RESULT)
        assert verify_hash(BASE_RESULT, h.upper()) is True
        assert verify_hash(BASE_RESULT, h.lower()) is True


class TestPayloadSorting:
    def test_playbook_rules_sorted(self):
        """Playbook rules must be sorted for cross-platform reproducibility."""
        import json, copy
        result = copy.deepcopy(BASE_RESULT)
        result["analysis"]["playbook_rules_cited"] = ["Part Z", "Part A", "Part M"]
        _, payload = compute_hash(result)
        parsed = json.loads(payload)
        assert parsed["playbook_rules_cited"] == sorted(["Part Z", "Part A", "Part M"])
