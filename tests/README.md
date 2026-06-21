# JIDRA Regression Tests

Comprehensive pytest-based test suite for JIDRA public APIs.

## Running Tests

### All tests
```bash
python -m pytest tests/ -v
```

### Specific test file
```bash
python -m pytest tests/test_engine.py -v
```

### Specific test class
```bash
python -m pytest tests/test_engine.py::TestGetFlow -v
```

### Specific test
```bash
python -m pytest tests/test_engine.py::TestGetFlow::test_get_flow_basic -v
```

### With coverage
```bash
python -m pytest tests/ --cov=jidra --cov-report=html
```

## Test Structure

### `conftest.py`
Pytest configuration and shared fixtures:
- `simple_test_graph`: In-memory Graph object with sample data
- `test_graph_file`: Temporary JSONL graph file
- `loaded_test_graph`: Loaded Graph from JSONL file

### `test_engine.py`
Regression tests for `JidraEngine` public API:
- `TestEngineInit`: Graph loading and initialization
- `TestGetMethodContext`: Method context extraction
- `TestGetFlow`: Stitched flow generation
- `TestGetAgentFlow`: Compact agent flow view
- `TestGetMethodSource`: Method source retrieval
- `TestGetCallChain`: Call chain discovery

### `test_flow_stitcher.py`
Regression tests for flow stitching algorithm:
- `TestStitchFlow`: Core stitching, depth control, business-only filtering
- Flow structure validation (nodes, edges, uncertain_edges, stopped_paths)
- Tiering and ranking validation
- Agent view structure

### `test_cost_calculator.py`
Tests for the cost/ROI calculator — all numbers derived from the real graph, no hardcoded estimates:
- `TestPricing`: Model pricing lookup and validation
- `TestAnalyzeGraph`: Real graph measurement (skips if `graph_validated.jsonl` absent)
- `TestCostBreakdown`: Per-query cost comparison using synthetic fixtures
- `TestROIAnalysis`: Annual ROI, payback period, year-1 ROI
- `TestEndToEnd`: Full pipeline against real graph

Run only the real-graph tests:
```bash
python -m pytest tests/test_cost_calculator.py -v -k "real"
```

Run only unit tests (no graph file needed):
```bash
python -m pytest tests/test_cost_calculator.py -v -k "not real and not missing"
```

## Design

Tests use a minimal 3-method call chain fixture:
```
TestController.handleRequest()
  → TestService.process()
    → TestRepository.fetch()
```

All tests verify:
1. **API Contracts**: Expected fields and structure present
2. **Graceful Error Handling**: Missing/invalid inputs produce errors, not exceptions
3. **Parameter Effects**: Parameters affect output as documented
4. **Backward Compatibility**: Old field names still work if supported

## Adding New Tests

When adding new features, add tests to ensure:
1. Happy path works
2. Error cases are handled
3. Edge cases (empty results, max limits, etc.) are tested
4. API contract is documented

Example:
```python
def test_new_feature(test_graph_file):
    engine = JidraEngine(test_graph_file)
    result = engine.new_method("param")
    
    assert "error" not in result
    assert "expected_field" in result
```
