# Formula Migration Tracking

This document tracks all `formula` occurrences that need to be migrated to the new
declarative constructs (`polynomial`, `compute`, `guard`, transform math ops).

**Target:** Replace `formula` with declarative constructs for TTN device library conversion.

**Date:** 2026-02-18

## Schema Language Changes (Finalized)

Before implementing, these spec changes were made in `la-payload-schema`:

| Item | Old | New | Rationale |
|------|-----|-----|-----------|
| Clamp lower | `max: 0` | `floor: 0` | Clearer semantics |
| Clamp upper | `min: 100` | `ceiling: 100` | Clearer semantics |
| Base-10 log | `log: 10` | `log10: true` | Matches C/Python stdlib |
| Natural log | `ln: true` | `log: true` | Matches C/Python stdlib |
| Guard syntax | `["$x > 0"]` | `[{field: $x, gt: 0}]` | Machine-parseable |
| Polynomial order | descending | **unchanged** | Matches MATLAB/NumPy |

## Migration Status Key

- [ ] Not started
- [~] In progress
- [x] Complete

---

## 1. Python Interpreter (`tools/schema_interpreter.py`)

### Current Formula Implementation

| Line | Function | Purpose |
|------|----------|---------|
| 768-787 | `_evaluate_encode_formula()` | Phase 3 encode formula evaluation |
| 789-826 | `_evaluate_formula()` | Formula evaluation with $field substitution |
| 1056-1062 | Field processing | Apply formula after reading value |
| 1207-1214 | Computed field | `type: number` with formula containing `$` |
| 1239-1240 | Flagged field | Formula in flagged groups |
| 1398-1399 | Encode skip | Skip formula fields during encoding |
| 1461 | Encode flagged | Skip formula in flagged encode |
| 1519-1522 | Encode formula | Use encode_formula for reverse |
| 701-702 | TLV decode | Formula in TLV cases |

### Migration Tasks

- [ ] Add `_decode_polynomial(coefficients, ref_value)` function
- [ ] Add `_decode_compute(op, a, b, ctx)` function  
- [ ] Add `_evaluate_guard(conditions, ctx)` function
- [ ] Add transform math ops to `_apply_modifiers()`: sqrt, abs, pow, floor, ceiling, clamp, log10, log
- [ ] Update field processing to handle `polynomial`, `compute`, `guard`, `ref`
- [ ] Keep `_evaluate_formula()` for backward compatibility (deprecated)
- [ ] Add deprecation warning when `formula` is used

---

## 2. Schema Validator (`tools/validate_schema.py`)

| Line | Context |
|------|---------|
| 251-261 | Validate computed field (type: number with formula) |
| 265-270 | Validate formula $field references |

### Migration Tasks

- [ ] Add validation for `polynomial` (array of numbers, min 2 coefficients)
- [ ] Add validation for `compute` (op in [add,sub,mul,div], a/b are $ref or literal)
- [ ] Add validation for `guard` (when array, else value)
- [ ] Add validation for `ref` (must reference existing field)
- [ ] Keep formula validation for backward compatibility
- [ ] Emit warning when `formula` is used in new schemas

---

## 3. Code Generators

### 3a. TS013 Codec Generator (`tools/generate_ts013_codec.py`)

| Line | Context |
|------|---------|
| 10 | Doc mentions formula support |
| 64-66 | `formula_to_js()` conversion function |
| 245-248 | Generate JS for formula computed field |
| 340-341 | Formula in field processing |
| 508 | Skip formula in encode |
| 619-621 | Encode comment for formula |

#### Migration Tasks

- [ ] Add `polynomial_to_js()` — generate Horner's method evaluation
- [ ] Add `compute_to_js()` — generate binary operation
- [ ] Add `guard_to_js()` — generate condition check
- [ ] Keep `formula_to_js()` for backward compat
- [ ] Update computed field generation for new constructs

### 3b. Firmware Codec Generator (`tools/generate_firmware_codec.py`)

| Line | Context |
|------|---------|
| 141 | Skip formula number fields |
| 399 | Check for formula modifier |
| 549-552 | Formula field comment generation |
| 592-594 | Skip formula in encode reversal |
| 735 | Formula in field check |

#### Migration Tasks

- [ ] Add C code generation for `polynomial` (Horner's method)
- [ ] Add C code generation for `compute` (simple binary op)
- [ ] Add C code generation for `guard` (if/else)
- [ ] Skip `formula` in C generation (not implementable without eval)

### 3c. JS Decoder Generator (`tools/generate_js_decoder.py`)

| Line | Context |
|------|---------|
| 262-264 | Eval formula in generated JS |

#### Migration Tasks

- [ ] Replace eval-based formula with declarative construct code

### 3d. JSON Schema Generator (`tools/generate_jsonschema.py`)

| Line | Context |
|------|---------|
| 76-82 | Schema for formula and encode_formula properties |
| 90 | Variable storage for formula references |

#### Migration Tasks

- [ ] Add schema definitions for `polynomial`, `compute`, `guard`, `ref`
- [ ] Mark `formula` as deprecated in schema

---

## 4. Documentation

### 4a. C Interpreter Status (`docs/C-INTERPRETER-STATUS.md`)

| Line | Context |
|------|---------|
| 61 | formula feature row (Python ✅, others ❌) |
| 93 | encode_formula feature row |
| 112-113 | Summary noting formula missing in C/C++ |

#### Migration Tasks

- [ ] Update to show polynomial/compute/guard status
- [ ] Note that formula is deprecated, new constructs are C-implementable

### 4b. Spec Implementation Status (`docs/SPEC-IMPLEMENTATION-STATUS.md`)

| Line | Context |
|------|---------|
| 90-91 | formula/encode_formula status rows |
| 121 | encode_formula status |
| 170 | encode_formula checklist item |
| 187 | Formula sandboxing note |

#### Migration Tasks

- [ ] Add rows for polynomial, compute, guard
- [ ] Mark formula as deprecated
- [ ] Update implementation notes

### 4c. Language Analysis (`docs/LANGUAGE-ANALYSIS.md`)

| Line | Context |
|------|---------|
| 176-179 | Formula example |
| 505-509 | EBNF grammar with formula-prop |

#### Migration Tasks

- [ ] Update examples to use polynomial/compute/guard
- [ ] Update EBNF grammar

---

## 5. C Interpreter (`include/schema_interpreter.h`)

| Line | Context |
|------|---------|
| 987 | Comment about encode_formula |

#### Migration Tasks

- [ ] Add polynomial evaluation (Horner's method)
- [ ] Add compute evaluation (switch on op)
- [ ] Add guard evaluation (condition check)
- [ ] Document that formula is not supported in C (use declarative)

---

## 6. Converter Tools

### 6a. Decentlab Converter (`tools/convert_decentlab.py`)

| Line | Context |
|------|---------|
| 91 | Comment about formula for complex expressions |
| 171-176 | Store formula in `_formula` field |
| 238-242 | Emit formula as comment |
| 273-297 | Count schemas with formulas |

#### Migration Tasks

- [ ] Update to emit `polynomial` for polynomial expressions
- [ ] Update to emit `compute` for cross-field ratios
- [ ] Keep formula comment for complex expressions needing manual review

---

## 7. Device Schemas (`schemas/decentlab/`)

All Decentlab schemas have formula comments (commented out, not active).
These need conversion to declarative constructs.

### Schemas with Formula Comments (40 files)

| Schema | Formula Type | Recommended Replacement |
|--------|--------------|------------------------|
| `dl-5tm.yaml` | Topp polynomial, linear | `polynomial`, `add`/`div` |
| `dl-alb.yaml` | Ratio with guard | `compute` + `guard` |
| `dl-atm41g2.yaml` | 32-bit assembly | `compute` chain |
| `dl-blg.yaml` | Steinhart-Hart | `polynomial` |
| `dl-cws.yaml` | Offset binary | `add` |
| `dl-cws2.yaml` | SHT scaling | `mult`/`add` |
| `dl-dlr2-*.yaml` | Various polynomials | `polynomial` |
| `dl-dws-*.yaml` | Frequency to value | `polynomial` |
| `dl-iam.yaml` | SHT + AQI formula | `mult`/`add`, `compute` |
| `dl-isf.yaml` | Linear scaling | `mult`/`add` |
| `dl-itst.yaml` | Linear offset | `add`/`div` |
| `dl-kl66-*.yaml` | Vibration formula | `polynomial` |
| `dl-lp8p.yaml` | SHT scaling | `mult`/`add` |
| `dl-lpw.yaml` | Percent scaling | `mult` |
| `dl-lws.yaml` | Linear | `mult` |
| `dl-par.yaml` | Radiation | `mult`/`add` |
| `dl-pm.yaml` | SHT scaling | `mult`/`add` |
| `dl-pr21-*.yaml` | Pressure linear | `mult`/`add` |
| `dl-pr26-*.yaml` | Pressure linear | `mult`/`add` |
| `dl-pr36*.yaml` | Pressure with param | `mult`/`add` |
| `dl-pyr.yaml` | Radiation | `mult`/`add` |
| `dl-sht35.yaml` | SHT scaling | `mult`/`add` |
| `dl-smtp.yaml` | Soil moisture | `add`/`div` |
| `dl-tbrg-01.yaml` | Rain gauge | `mult` |
| `dl-trs11.yaml` | Polynomial | `polynomial` |
| `dl-trs12.yaml` | Polynomial | `polynomial` |
| `dl-trs21.yaml` | Negation | `mult: -1` |
| `dl-wrm.yaml` | SHT + thermocouple | `mult`/`add` |
| `dl-zn1.yaml` | (check) | TBD |
| `dl-zn2.yaml` | (check) | TBD |

#### Migration Priority

1. **High** - Schemas already using `add`/`div`/`mult` (formula is comment only)
2. **Medium** - Linear formulas convertible to `add`/`div`/`mult`
3. **Low** - Complex polynomials needing `polynomial` construct
4. **Manual** - Cross-field with parameters (may need `compute` chain)

---

## 8. Test Files

### 8a. Python Tests (`tests/test_schema_interpreter.py`)

| Line | Context |
|------|---------|
| 868-920 | Formula test class and methods |
| 2902-2903 | Encode formula test data |

#### Migration Tasks

- [ ] Add tests for `polynomial` construct
- [ ] Add tests for `compute` construct
- [ ] Add tests for `guard` construct
- [ ] Add tests for transform math ops
- [ ] Keep formula tests for backward compatibility

---

## 9. Other Files

### 9a. Test Requirements Map (`test-requirements-map.yaml`)

Contains formula-related requirement mappings.

#### Migration Tasks

- [ ] Add requirement mappings for new constructs

### 9b. Generated Files (`generated/`)

Auto-generated, will be updated when generators are updated.

---

## Summary

### Formula Migration

| Category | Files | Status |
|----------|-------|--------|
| Python Interpreter | 1 | [ ] |
| Schema Validator | 1 | [ ] |
| Code Generators | 4 | [ ] |
| Documentation | 3 | [ ] |
| C Interpreter | 1 | [ ] |
| Converter Tools | 1 | [ ] |
| Device Schemas | 40 | [ ] |
| Test Files | 1 | [ ] |
| Other | 2 | [ ] |
| **Total** | **54** | **0%** |

### Organization Reference Removal

| Category | Files | Status |
|----------|-------|--------|
| JSON Schema | 2 | [x] |
| Build/Config | 4 | [x] |
| Documentation | 4 | [x] |
| Proto/Go | 3 | [x] |
| **Total** | **13** | **100%** |

---

## Migration Order

1. **Python interpreter** — Core implementation
2. **Schema validator** — Validate new constructs
3. **Tests** — Verify implementation
4. **Documentation** — Update status docs
5. **Code generators** — Generate code for new constructs
6. **Device schemas** — Convert Decentlab schemas
7. **Converter tools** — Auto-convert new schemas

---

## Notes

- This project is intended for TTN device library conversion
- Repository is organization-agnostic for standalone open source publication
- `formula` remains for backward compatibility but is deprecated
- New schemas MUST use declarative constructs

---

## 10. Reference Cleanup (COMPLETED)

All organization-specific and placeholder spec references have been removed.

### Completed Tasks

- [x] Chose new GitHub org/repo name: `lorawan-schema/payload-codec`
- [x] Updated all organization URL references
- [x] Updated all organization domain URLs
- [x] Replaced all organization-specific text with generic/project-specific text
- [x] Updated copyright notice in README (MIT License)
- [x] Deleted `.hypothesis/constants/` directory
- [x] Removed placeholder spec numbers from tools and documentation
- [x] Updated build-system files

### Note on TS013 References

References to TS013 (Payload Codec API) are intentional and should be kept, as this
is the standard API format that generated codecs target.
