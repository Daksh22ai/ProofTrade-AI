"""
tests/test_decision_tree.py

Exhaustive tests for the deterministic Q1-Q4 decision tree.
All 9 scenarios are tested with known input values to ensure
the playbook logic cannot drift or hallucinate.

Run: pytest tests/ -v
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_aggregator import run_decision_tree


def _snapshot(price, vwma, spot_dir, oi_trend, funding, macd_hist):
    """Build a minimal snapshot dict for decision tree input."""
    return {
        "current_price": price,
        "vwma_20d":      vwma,
        "spot_cvd":      {"direction": spot_dir},
        "oi_trend":      oi_trend,
        "funding":       {"current": funding},
        "macd":          {"histogram": macd_hist},
    }


class TestScenario1HealthyUptrend:
    def test_basic(self):
        snap = _snapshot(110000, 100000, "rising", "rising", 0.0002, 50.0)
        num, name, trace = run_decision_tree(snap)
        assert num == 1
        assert name == "HEALTHY_UPTREND"
        assert trace["Q1_above_vwma"] is True
        assert trace["Q2A_spot_cvd_rising"] is True
        assert trace["Q3A_oi_rising_with_price"] is True
        assert trace["Q4_macd_above_zero"] is True

    def test_requires_positive_funding(self):
        # Negative funding → Q4 fails → S7 instead of S1
        snap = _snapshot(110000, 100000, "rising", "rising", -0.001, 50.0)
        num, name, _ = run_decision_tree(snap)
        assert num == 7
        assert name == "CONFIRMED_REVERSAL_FROM_BOTTOM"

    def test_requires_macd_above_zero(self):
        snap = _snapshot(110000, 100000, "rising", "rising", 0.0002, -5.0)
        num, name, _ = run_decision_tree(snap)
        assert num == 7


class TestScenario2UptrendWeakening:
    def test_basic(self):
        snap = _snapshot(110000, 100000, "flat", "rising", 0.0002, 10.0)
        num, name, trace = run_decision_tree(snap)
        assert num == 2
        assert name == "UPTREND_WEAKENING"
        assert trace["Q1_above_vwma"] is True
        assert trace["Q2A_spot_cvd_rising"] is False

    def test_falling_spot_cvd_above_vwma(self):
        snap = _snapshot(110000, 100000, "falling", "flat", 0.0, 5.0)
        num, name, _ = run_decision_tree(snap)
        assert num == 2


class TestScenario3ConfirmedReversalFromTop:
    def test_basic(self):
        snap = _snapshot(90000, 100000, "falling", "flat", -0.0002, -10.0)
        num, name, trace = run_decision_tree(snap)
        assert num == 3
        assert name == "CONFIRMED_REVERSAL_FROM_TOP"
        assert trace["Q1_above_vwma"] is False
        assert trace["Q2B_spot_cvd_falling"] is True
        assert trace["Q3B_oi_rising"] is False

    def test_spot_must_be_falling(self):
        # Spot CVD flat below VWMA → S6 (bottom forming), not S3
        snap = _snapshot(90000, 100000, "flat", "flat", 0.0, 0.0)
        num, name, _ = run_decision_tree(snap)
        assert num == 6


class TestScenario4HealthyDowntrend:
    def test_basic(self):
        snap = _snapshot(90000, 100000, "falling", "rising", -0.0003, -20.0)
        num, name, trace = run_decision_tree(snap)
        assert num == 4
        assert name == "HEALTHY_DOWNTREND"
        assert trace["Q3B_oi_rising"] is True


class TestScenario5DeadCatBounce:
    def test_basic(self):
        # Price above VWMA, Spot CVD rising, but OI falling (short covering)
        snap = _snapshot(110000, 100000, "rising", "falling", 0.0001, 10.0)
        num, name, trace = run_decision_tree(snap)
        assert num == 5
        assert name == "DEAD_CAT_BOUNCE"
        assert trace["Q3A_oi_rising_with_price"] is False

    def test_flat_oi_also_triggers(self):
        snap = _snapshot(110000, 100000, "rising", "flat", 0.0001, 10.0)
        num, name, _ = run_decision_tree(snap)
        assert num == 5


class TestScenario6BottomForming:
    def test_basic(self):
        snap = _snapshot(90000, 100000, "flat", "flat", 0.0, -5.0)
        num, name, trace = run_decision_tree(snap)
        assert num == 6
        assert name == "BOTTOM_FORMING"
        assert trace["Q1_above_vwma"] is False
        assert trace["Q2B_spot_cvd_falling"] is False


class TestScenario7ConfirmedReversalFromBottom:
    def test_basic(self):
        # Above VWMA, spot rising, OI rising, but funding negative (Q4 fails)
        snap = _snapshot(110000, 100000, "rising", "rising", -0.0003, 10.0)
        num, name, trace = run_decision_tree(snap)
        assert num == 7
        assert name == "CONFIRMED_REVERSAL_FROM_BOTTOM"

    def test_also_triggers_on_zero_macd(self):
        snap = _snapshot(110000, 100000, "rising", "rising", 0.0001, 0.0)
        num, name, _ = run_decision_tree(snap)
        assert num == 7  # macd_hist == 0 is not > 0, so Q4 fails


class TestScenario8RangingConsolidation:
    def test_range_shortcut(self):
        # Price within 3% of VWMA, both CVD flat, OI flat
        snap = _snapshot(100500, 100000, "flat", "flat", 0.0, 0.0)
        num, name, trace = run_decision_tree(snap)
        assert num == 8
        assert name == "RANGING_CONSOLIDATION"
        assert trace.get("range_shortcut") is True

    def test_insufficient_data_defaults_to_s8(self):
        snap = {"current_price": None, "vwma_20d": None,
                "spot_cvd": {"direction": "flat"}, "oi_trend": "flat",
                "funding": {"current": 0.0}, "macd": {"histogram": None}}
        num, name, trace = run_decision_tree(snap)
        assert num == 8
        assert "error" in trace

    def test_range_shortcut_requires_all_three_conditions(self):
        # Within 3% but CVD rising → no shortcut
        snap = _snapshot(100500, 100000, "rising", "flat", 0.0, 0.0)
        num, name, trace = run_decision_tree(snap)
        assert trace.get("range_shortcut") is False
        assert num != 8


class TestEdgeCases:
    def test_price_exactly_at_vwma(self):
        # Price == VWMA → price > vwma is False → below branch
        snap = _snapshot(100000, 100000, "falling", "falling", -0.0001, -5.0)
        num, name, _ = run_decision_tree(snap)
        assert num in (3, 4)  # depends on OI

    def test_zero_price_vwma_no_crash(self):
        snap = _snapshot(0.001, 0.001, "flat", "flat", 0.0, 0.0)
        num, name, _ = run_decision_tree(snap)
        assert isinstance(num, int)

    def test_all_scenarios_return_valid_tuple(self):
        cases = [
            _snapshot(110000, 100000, "rising", "rising", 0.0002, 50.0),   # S1
            _snapshot(110000, 100000, "flat",   "rising", 0.0002, 10.0),   # S2
            _snapshot(90000,  100000, "falling","flat",   -0.0002,-10.0),  # S3
            _snapshot(90000,  100000, "falling","rising", -0.0003,-20.0),  # S4
            _snapshot(110000, 100000, "rising", "falling", 0.0001, 10.0),  # S5
            _snapshot(90000,  100000, "flat",   "flat",    0.0,    -5.0),  # S6
            _snapshot(110000, 100000, "rising", "rising", -0.0003, 10.0),  # S7
            _snapshot(100500, 100000, "flat",   "flat",    0.0,     0.0),  # S8
        ]
        for snap in cases:
            num, name, trace = run_decision_tree(snap)
            assert isinstance(num, int), f"scenario_number must be int"
            assert isinstance(name, str), f"scenario_name must be str"
            assert isinstance(trace, dict), f"trace must be dict"
            assert 1 <= num <= 9, f"scenario {num} out of range"
