# Schema Development Guide

Best practices for creating complete, validated payload schemas.

## Overview

When converting an existing codec (e.g., TTN JavaScript) to a declarative schema, the goal is **functional equivalence**: the generated codec must produce identical output for all input payloads.

## Process: Converting an Existing Codec

### 1. Analyze the Original Codec

**Before writing any YAML**, thoroughly analyze the source codec:

```bash
# Use the codec analyzer to extract test vectors
node tools/analyze_codec.js vendor/device/codec.js --output test-vectors.yaml
```

Key questions to answer:
- What message types exist? (keepalive, command responses, configurations)
- What conditional logic exists? (switch on first byte, TLV structures)
- What mathematical transformations are applied? (rounding, scaling, formulas)
- What edge cases exist? (division by zero, max values, error conditions)

### 2. Document All Code Paths

Create a checklist of ALL code paths in the original:

```markdown
## Message Types
- [ ] Keepalive (reason=0x01)
- [ ] Keepalive alternate (reason=0x51) - different temp formula
- [ ] Extended keepalive (reason=0x81)
- [ ] Command response 0x04 - device versions
- [ ] Command response 0x12 - keepalive time
- [ ] ... (list ALL commands)

## Edge Cases
- [ ] Division by zero (motorRange=0)
- [ ] Maximum values (0xFF bytes)
- [ ] Minimum values (0x00 bytes)
- [ ] Negative temperatures
- [ ] All flags set
```

### 3. Generate Comprehensive Test Vectors

**Test vectors are the source of truth.** Generate them by running the original codec:

```javascript
// For each message type and edge case, run through original codec
const result = decodeUplink({ bytes: [...], fPort: 2 });
// Record exact output including all fields
```

Record each such vector with `source: vendor-codec` (or `vendor-doc` when the values come
from the datasheet). Never record what this toolkit's own decoder printed as if it were
independent: label such vectors `source: generated`. A schema with no independently
sourced vector is capped at Silver (PS-264) however high it scores.

Minimum test vector coverage:
- **One test per message type** (keepalive, each command, etc.)
- **Boundary tests**: all zeros, all 0xFF, single bit set
- **Edge case tests**: division by zero, negative values, overflow
- **Flag combination tests**: all flags on, individual flags

### 4. Write Schema Incrementally

Start with the simplest message type and verify before adding complexity:

```bash
# After each change, validate
python3 tools/validate_schema.py schemas/device.yaml

# Compare the schema against the vendor's decoder (TTN device repository checkout)
python3 tools/crossvalidate_ttn.py --devices-repo ../lorawan-devices \
    --vendor milesight-iot --schema-dir schemas/devices/milesight --schema ws301

# Decentlab: against the vendor's own decoders
python3 tools/crossvalidate_decentlab.py --vendor-dir ../decentlab-decoders
```

### 5. Document Deviations

If the original codec has bugs or undefined behavior, document it:

```yaml
# NOTE: The original TTN codec has a bug where reason=0x51 (alternate temp formula)
# never actually applies the formula due to incorrect comparison.
# We replicate this bug for compatibility.
# The intended formula was: (raw - 28.33) / 5.67
```

### 6. Validate Output Format

Ensure generated codec output matches original:
- **Numeric precision**: Use `transform: [{op: round, decimals: 2}]`
- **Boolean format**: `type: bool` decodes to `true`/`false`, as JavaScript codecs do; a
  codec that emits `0`/`1` needs an integer field (e.g. `u8[3:3]`) instead
- **Field names**: Must match exactly
- **Nested objects**: Command responses often use nested structures

## Common Pitfalls

### Missing Message Types

**Problem**: Schema only handles one message type when codec handles multiple.

**Solution**: Use `analyze_codec.js` to extract ALL message types, create test vectors for each.

### Incorrect Rounding

**Problem**: Values like `21.875` should be `21.88` in output.

**Solution**: Add transform with rounding:
```yaml
transform:
  - op: round
    decimals: 2
```

### Missing Edge Cases

**Problem**: Schema works for normal values but fails at boundaries.

**Solution**: Always include boundary test vectors:
- All bytes = 0x00
- All bytes = 0xFF  
- Division-by-zero conditions
- Negative value handling

### Boolean vs Integer

**Problem**: The original emits a flag as `true`/`false` in one place and `0`/`1` in another.

**Solution**: Match the original per field. `type: bool` (with `bit:`) decodes to
`true`/`false` in every implementation and the generated codec; a bit range such as
`u8[3:3]` decodes to the integer `0`/`1`.

### Incomplete Command Support

**Problem**: TLV/command response support is complex and often incomplete.

**Solution**: 
1. Document what's supported vs unsupported
2. Create separate schemas for complex command structures
3. Use `match` or `tlv` constructs for command dispatch

## Tools

### analyze_codec.js

Extracts test vectors from existing JS codec:

```bash
node tools/analyze_codec.js <codec.js> --output vectors.yaml
```

### validate_schema.py

Validates schema and runs test vectors:

```bash
python3 tools/validate_schema.py schema.yaml -v
```

### score_schema.py

Evaluates schema quality and coverage:

```bash
python3 tools/score_schema.py schema.yaml --verbose
```

### crossvalidate_ttn.py / crossvalidate_decentlab.py

Compare a schema's decode against the vendor's own decoder and declared examples:

```bash
python3 tools/crossvalidate_ttn.py --devices-repo ../lorawan-devices \
    --vendor milesight-iot --schema-dir schemas/devices/milesight [--verbose]
python3 tools/crossvalidate_decentlab.py --vendor-dir ../decentlab-decoders
```

`tools/crossvalidate_js_json.py` diffs the interpreter's JSON against the generated
TS013 codec's, token by token.

## Checklist: Before Declaring "Complete"

- [ ] All message types from original codec are implemented OR documented as out-of-scope
- [ ] Test vectors cover all code paths (use code coverage tools on original)
- [ ] Edge cases tested: zeros, max values, division-by-zero
- [ ] Numeric precision matches original (toFixed, Math.round)
- [ ] Generated codec passes all test vectors
- [ ] Differences from original are documented
- [ ] Schema passes validation (`validate_schema.py`)
- [ ] Schema scores at target tier (`score_schema.py`)
- [ ] Every test vector declares `source:`, and at least one is independent of this toolkit

## Example: MClimate Vicki

The Vicki schema demonstrates these principles:

1. **Analyzed original**: Found 25+ command types, 3 keepalive variants
2. **Scoped to keepalive only**: Command responses documented as future work
3. **Test vectors**: 12 vectors covering all keepalive edge cases. They were validated
   against the interpreter's output and carry no independent `source:`, so the schema
   scores 100% yet is capped at Silver (PS-264) until vectors from the vendor codec are
   recorded
4. **Added rounding transforms**: Match original's `toFixed(2)` and `Math.round()`
5. **Documented bugs**: Original's broken reason=0x51 formula
6. **Validated output**: Generated codec matches original for all test vectors

Result: Keepalive messages are 100% compatible; command responses are explicitly out of scope.
