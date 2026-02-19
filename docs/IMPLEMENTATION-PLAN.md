# Implementation Plan

Notes from 2026-02-18 session for completing proto and spec.

## Order of Operations

1. **Proto implementation** — Update interpreters + add quality scoring tooling
2. **Scoring flow** — Self-assessable schema validation
3. **Spec finalization** — Document what's implemented, add conformance appendix

---

## Part 1: Schema Language Changes

Changes agreed for clarity and standard library alignment:

| Item | Old | New | Rationale |
|------|-----|-----|-----------|
| Clamp lower | `max: 0` | `floor: 0` | Clearer semantics |
| Clamp upper | `min: 100` | `ceiling: 100` | Clearer semantics |
| Base-10 log | `log: 10` | `log10: true` | Matches C/Python stdlib |
| Natural log | `ln: true` | `log: true` | Matches C/Python stdlib |
| Guard syntax | `["$x > 0"]` | `[{field: $x, gt: 0}]` | Machine-parseable |
| Polynomial order | descending | **unchanged** | Matches MATLAB/NumPy |

### Guard Condition Operators

```yaml
guard:
  when:
    - field: $incoming_radiation
      gt: 0      # greater than
    - field: $reflected_radiation
      gte: 0     # greater than or equal
  else: 0
```

Operators: `gt`, `gte`, `lt`, `lte`, `eq`, `ne`

---

## Part 2: Formula Migration

See `FORMULA-MIGRATION-TRACKING.md` for complete file list.

### Interpreter Updates Required

1. **Add new construct handlers:**
   - `_decode_polynomial(coefficients, input_value)` — Horner's method
   - `_decode_compute(op, a, b, context)` — Binary operations
   - `_evaluate_guard(conditions, context)` — Structured condition check
   - Transform math ops: `sqrt`, `abs`, `pow`, `floor`, `ceiling`, `clamp`, `log10`, `log`

2. **Update field processing:**
   - Handle `ref: $field_name` for computed fields
   - Handle `polynomial: [coeffs]` 
   - Handle `compute: {op:, a:, b:}`
   - Handle `guard: {when: [...], else:}`

3. **Backward compatibility:**
   - Keep `_evaluate_formula()` for deprecated `formula` field
   - Emit deprecation warning when `formula` is used

### Files to Update

```
tools/schema_interpreter.py    # Core interpreter
tools/validate_schema.py       # Add validation for new constructs
tools/generate_ts013_codec.py  # Generate JS for new constructs
tools/generate_firmware_codec.py # Generate C for new constructs
tests/test_schema_interpreter.py # Add tests for new constructs
```

---

## Part 3: Quality Scoring System

### Concept: Tiered Scoring

| Tier | Features | Coverage |
|------|----------|----------|
| **Bronze** | Core types, basic modifiers | ~60% devices |
| **Silver** | + bitfields, switch, flagged | ~90% devices |
| **Gold** | + polynomial, compute, guard, TLV | ~99% devices |
| **Platinum** | + encoding, compact mode, all edge cases | 100% |

### Two Scoring Targets

1. **Interpreter validation** — Does decoder software correctly implement the DSL?
2. **Schema scoring** — Does YAML schema correctly decode device payloads?

### Schema Scoring (Primary Focus)

Since everything is generated from schema, quality scoring = schema validation.

**Checklist:**
- [ ] Valid YAML syntax
- [ ] Valid against JSON Schema
- [ ] Has test_vectors (≥3 cases)
- [ ] Test vectors pass Python interpreter
- [ ] Test vectors pass generated JS codec
- [ ] All branches covered (switch/flagged)
- [ ] Edge cases (zero, max, negative, overflow)
- [ ] Round-trip encode/decode (where supported)
- [ ] Binary schema generates successfully

### New Tool: `score_schema.py`

```python
#!/usr/bin/env python3
"""
Quality scoring tool for payload schemas.

Usage:
    python tools/score_schema.py schemas/decentlab/dl-5tm.yaml
    python tools/score_schema.py schemas/ --all --report score-report.json
"""

def score_schema(schema_path) -> ScoringResult:
    """Run all quality scoring checks on a schema."""
    
    results = {}
    
    # 1. Schema validation
    results['schema_valid'] = validate_schema_structure(schema_path)
    
    # 2. JSON Schema compliance  
    results['jsonschema_valid'] = validate_against_jsonschema(schema_path)
    
    # 3. Test vectors present
    results['has_test_vectors'] = check_test_vectors_exist(schema_path)
    
    # 4. Python interpreter passes
    results['python_tests_pass'] = run_python_tests(schema_path)
    
    # 5. Generated JS passes (cross-validation)
    results['js_tests_pass'] = run_js_tests(schema_path)
    
    # 6. Branch coverage analysis
    results['branch_coverage'] = analyze_branch_coverage(schema_path)
    
    # 7. Edge case coverage
    results['edge_cases'] = check_edge_case_coverage(schema_path)
    
    # 8. Fuzz robustness
    results['fuzz_robust'] = fuzz_test(schema_path, iterations=100)
    
    # 9. Binary schema generation
    results['binary_generates'] = try_binary_generation(schema_path)
    
    # Calculate score and tier
    return calculate_score(results)


def calculate_score(results):
    """Calculate score and tier from results."""
    
    weights = {
        'schema_valid': 10,
        'jsonschema_valid': 5,
        'has_test_vectors': 10,
        'python_tests_pass': 20,
        'js_tests_pass': 15,
        'branch_coverage': 15,
        'edge_cases': 10,
        'fuzz_robust': 10,
        'binary_generates': 5,
    }
    
    score = sum(
        weights[k] * (v if isinstance(v, (int, float)) else (1 if v else 0))
        for k, v in results.items()
    )
    max_score = sum(weights.values())
    pct = score / max_score * 100
    
    if pct == 100:
        tier = 'PLATINUM'
    elif pct >= 90:
        tier = 'GOLD'
    elif pct >= 75:
        tier = 'SILVER'
    else:
        tier = 'BRONZE'
    
    return ScoringResult(
        score=pct,
        tier=tier,
        details=results
    )
```

### Report Output Format

```json
{
  "schema": "decentlab/dl-5tm.yaml",
  "timestamp": "2026-02-18T20:30:00Z",
  "score": 94.5,
  "tier": "GOLD",
  "details": {
    "schema_valid": true,
    "jsonschema_valid": true,
    "has_test_vectors": true,
    "python_tests_pass": true,
    "js_tests_pass": true,
    "branch_coverage": 0.85,
    "edge_cases": ["zero", "max"],
    "missing_edge_cases": ["overflow", "negative"],
    "fuzz_robust": true,
    "binary_generates": true
  },
  "recommendations": [
    "Add test vector for negative values",
    "Add test vector for overflow case"
  ]
}
```

---

## Part 4: Artifact Generation Pipeline

```
                         ┌─────────────────┐
                         │  YAML Schema    │ ← Source of truth
                         │  (human + AI)   │
                         └────────┬────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ JS Codec (TS013)│    │  JSON Schema    │    │  Binary Schema  │
│ decodeUplink()  │    │  (validation)   │    │  (base64/proto) │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                        │                        │
         │                        ▼                        │
         │              ┌─────────────────┐                │
         └─────────────▶│  Test Vectors   │◀───────────────┘
                        │  (AI-assisted)  │
                        └─────────────────┘
```

### TTN Device Library Output

```
vendor/decentlab/dl-5tm/
├── schema.yaml          # Source (human-maintained)
├── codec.js             # Generated (TS013)
├── schema.json          # Generated (validation)  
├── schema.bin           # Generated (binary, base64)
├── test-vectors.json    # AI-generated, human-reviewed
└── scoring.json         # Quality scoring report
```

---

## Part 5: Spec Finalization

After proto implementation is complete:

### Add to Spec

1. **Definitions section** — Key terms (decoder, encoder, field, modifier, computed field)

2. **Packet diagrams** — Visual byte layouts:
   ```
   +--------+--------+--------+--------+
   | Byte 0 | Byte 1 | Byte 2 | Byte 3 |
   +--------+--------+--------+--------+
   |  0x02  |  Device ID (BE) |  Flags |
   +--------+-----------------+--------+
   ```

3. **Conformance appendix** — Quality tiers, test requirements

4. **Requirements traceability** — REQ-xxx identifiers for key requirements

### Style (Keep Accessible)

- Don't add PhD fluff
- Keep practical examples
- Active voice where possible
- Explain the "why" not just "what"
- Target audience: device makers + integration developers

---

## Part 6: Task Checklist

### Phase A: Interpreter Updates (Proto) ✅ COMPLETE
- [x] Add `polynomial` support to `schema_interpreter.py`
- [x] Add `compute` support  
- [x] Add `guard` support (new structured syntax: `{field: $x, gt: 0}`)
- [x] Add transform ops: `floor`, `ceiling` (renamed from min/max)
- [x] Add transform ops: `log10`, `log` (renamed from log/ln)
- [x] Update `validate_schema.py` for new constructs
- [x] Add tests for all new constructs (16 new tests)
- [x] Deprecation warning for `formula`

### Phase B: Code Generators ✅ COMPLETE
- [x] Update `generate_ts013_codec.py` for new constructs
- [x] Update `generate_firmware_codec.py` for new constructs
- [x] Update `generate_jsonschema.py` with new properties

### Phase C: Scoring Tool + Requirements Analysis ✅ COMPLETE
- [x] Create `score_schema.py` wrapper
- [x] Add branch coverage analysis
- [x] Add JS cross-validation
- [x] Add edge case detection
- [x] JSON report output
- [x] Create `verify_spec_completeness.py`:
  - [x] Feature → Lang Reference mapping
  - [x] Feature → Interpreter function mapping
  - [x] Feature → Test coverage report
  - [x] Completeness scoring and status

### Phase D: Device Schemas + Reference Implementations
- [ ] Update Decentlab schemas with new constructs
- [ ] Ensure all schemas have test vectors
- [ ] Run quality scoring on all schemas
- [ ] Sync reference implementations in la-payload-schema:
  - [ ] `reference-impl/python/payload_schema.py`
  - [ ] `reference-impl/js/payload-schema.js`
  - [ ] `reference-impl/c/schema_decoder.c`

### Phase E: Spec Finalization
- [ ] Add definitions section (key terms)
- [ ] Add packet byte diagrams
- [ ] Add conformance appendix (Bronze/Silver/Gold/Platinum tiers)
- [ ] Add REQ-xxx traceability where missing
- [ ] Review for consistency
- [ ] Version bump

---

## Notes

- Schema is single source of truth
- Everything else generated
- Self-assessment with existing tools + small wrapper
- Tiered scoring (Bronze → Platinum)
- Keep spec accessible, not academic
- Spec-agnostic: no specific standards body references
- MIT license for open source publication
- Target: `github.com/lorawan-schema/payload-codec`
