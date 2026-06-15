# tests/: Automated Test Suite

```bash
pytest tests/ -v
```

## Files

### `test_decision_tree.py`

Tests the deterministic Q1-Q4 decision tree in `data_aggregator.py`. All 9 scenarios are covered with at least one positive and one boundary case each.

Key cases:

| Test | Scenario | Conditions |
|---|---|---|
| Healthy Uptrend | S1 | Above VWMA, spot rising, OI rising, funding positive, MACD positive |
| Uptrend Weakening | S2 | Above VWMA, spot not rising |
| Reversal from Top | S3 | Below VWMA, spot falling, OI falling |
| Healthy Downtrend | S4 | Below VWMA, spot falling, OI rising |
| Dead Cat Bounce | S5 | Above VWMA, spot rising, OI falling |
| Bottom Forming | S6 | Below VWMA, spot not falling |
| Reversal from Bottom | S7 | Above VWMA, spot rising, OI rising, funding or MACD not both OK |
| Ranging (shortcut) | S8 | Price within 3% of VWMA, spot flat, OI flat |
| MACD boundary | S7 vs S1 | Q4 distinguishes: MACD zero means S7, positive means S1 |

The range shortcut (S8) is tested before Q1 to confirm it correctly intercepts flat/flat/flat conditions.

### `test_hash.py`

Tests the hash computation in `on_chain/submit_audit.py`.

Cases covered:

- **Determinism**: Two calls with the same input produce the same hash.
- **Timestamp rounding**: Two analyses with timestamps differing by seconds within the same minute produce the same hash (timestamps are rounded to the minute before hashing).
- **Sensitivity**: Changing any single field in the payload produces a different hash.
- **Format**: Hash starts with `0x` and is 66 characters (32 bytes hex).
- **Required fields**: Payload contains all fields needed for independent verification.
- **Snapshot hash**: The `_build_snapshot_hash()` function in `run_pipeline.py` produces a consistent hash from raw indicator values.

## Running

```bash
# All tests
pytest tests/ -v

# Single file
pytest tests/test_decision_tree.py -v
pytest tests/test_hash.py -v

# Specific test
pytest tests/test_decision_tree.py::TestDecisionTree::test_s1_healthy_uptrend -v
```

No database connection is required. All tests use in-memory snapshot dictionaries. The hash tests import directly from `on_chain/submit_audit.py` using `sys.path.insert(0, project_root)`.
