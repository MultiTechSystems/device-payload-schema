# Schema-Based Decoder Migration Strategy

## Overview

A migration path from hand-written/generated JS codecs to schema-driven interpreters, with A/B testing against production payloads to build confidence before switching.

## Current State

- **Production codecs**: Hand-written JS decoders, device-specific, deployed on TTN/ChirpStack
- **Schema library**: Growing collection of YAML/JSON schemas describing device payloads
- **Interpreters**: JSON and binary schema interpreters (JS, Python)

## Goal

Replace device-specific codecs with a single schema interpreter that reads schema definitions. Users can provide schemas in either JSON or binary format.

## Architecture

```
                    Production Environment
                              |
         +--------------------+--------------------+
         |                                         |
         v                                         v
   Existing JS Codec                    Schema Interpreter
   (current production)                 (JSON or Binary schema)
         |                                         |
         v                                         v
   Decoded Output A                      Decoded Output B
         |                                         |
         +-------------------+---------------------+
                             |
                             v
                      A/B Comparison
                      (log discrepancies)
```

## Migration Phases

### Phase 1: Shadow Mode
- Deploy schema interpreter alongside existing codecs
- Run both on every payload
- Log outputs but don't use interpreter results
- Collect discrepancy data

### Phase 2: Analysis
- Review discrepancies - are they bugs in:
  - Original codec?
  - Schema definition?
  - Interpreter?
- Fix issues, re-run comparison
- Build confidence metrics

### Phase 3: Gradual Cutover
- Start with devices where schema interpreter matches 100%
- Monitor for regressions
- Expand coverage

### Phase 4: Schema-First
- New devices get schema only (no hand-written codec)
- Deprecate old codecs as confidence grows

## A/B Comparison Implementation

```javascript
function decodeWithComparison(input) {
    // Run existing codec
    const existingResult = existingCodec.decodeUplink(input);
    
    // Run schema interpreter
    const schemaResult = schemaInterpreter.decodeUplink(input);
    
    // Compare
    const match = deepEqual(existingResult.data, schemaResult.data);
    
    if (!match) {
        logDiscrepancy({
            deviceId: input.deviceId,
            payload: input.bytes,
            existing: existingResult.data,
            schema: schemaResult.data,
            differences: findDiff(existingResult.data, schemaResult.data)
        });
    }
    
    // Return existing result (production path unchanged)
    return existingResult;
}
```

## Schema Format Choice

Either JSON or binary schema works for the interpreter:

| Format | Pros | Cons |
|--------|------|------|
| JSON | Human-readable, easy to debug | Larger size |
| Binary | Compact, faster parse | Harder to inspect |

**Recommendation**: Use JSON during development/debugging, binary for constrained environments (OTA, NFC, QR).

## Metrics to Track

- **Match rate**: % of payloads where outputs are identical
- **Discrepancy types**: Field missing, value mismatch, type difference
- **Coverage**: % of device types with schemas
- **Performance**: Decode latency comparison

## Success Criteria

Before switching a device to schema-only:
- 100% match rate over N payloads (e.g., 10,000)
- No new discrepancies in M days (e.g., 7 days)
- Performance within acceptable bounds

## Benefits

1. **Single codebase**: One interpreter vs hundreds of device codecs
2. **Easier updates**: Change schema, not code
3. **AI-friendly**: LLMs can generate/modify schemas
4. **Portable**: Same schema works in JS, Go, Python, C

## TTN Device Repository Conversion

Analysis of 803 JS codecs from `lorawan-devices` repo:

### Codec Categories

| Category | Count | % | Approach |
|----------|-------|---|----------|
| Simple (flat structure) | 212 | 30% | Auto-draft + 1 min review |
| Complex (conditionals) | 314 | 45% | Manual ~5 min each |
| TLV/variable-length | 173 | 25% | Schema extension needed |

### Field Statistics (12,529 fields total)

- With multiplier/divisor: 2,179 (17%)
- With offset: 1,919 (15%)
- With bitfield extraction: 1,299 (10%)
- Signed integers: 451 (4%)

### Estimated Conversion Time

- Simple codecs: 212 × 1 min = 3.5 hours
- Complex codecs: 314 × 5 min = 26 hours
- **Total: ~30 hours** (excluding TLV)

### Tooling

- `tools/analyze_ttn_codec.py` - Single codec analysis
- `tools/batch_analyze_codecs.py` - Batch analysis with stats

### TLV Support (NEW)

173 codecs (25%) use Type-Length-Value patterns. The new `tlv` schema type now supports these:

```yaml
fields:
  - type: tlv
    tag_fields:
      - name: channel_id
        type: u8
      - name: channel_type
        type: u8
    tag_key: [channel_id, channel_type]
    cases:
      [0x01, 0x75]:
        - name: battery
          type: u8
      [0x03, 0x67]:
        - name: temperature
          type: s16
          mult: 0.1
```

**Supported patterns:**
- Single-byte tags (Elsys style)
- Composite tags (Milesight channel_id + channel_type)
- Explicit length fields
- Repeated tags (auto-array)

### Updated Conversion Estimate

| Category | Count | Time | Total |
|----------|-------|------|-------|
| Simple (flat) | 212 | 1 min | 3.5 hrs |
| Complex (conditionals) | 314 | 5 min | 26 hrs |
| TLV (now supported) | 173 | 3 min | 8.5 hrs |
| **Total** | **699** | | **38 hrs** |

## TLV Benchmark Results

Comparison of hand-written Milesight AM307 vs TLV schema interpreter:

### File Size

| Component | Size |
|-----------|------|
| Hand-written decoder | 2,711 bytes per device |
| Schema JSON | 891 bytes per device |
| TLV Interpreter | 12,935 bytes (shared) |

**Per-device savings: 67%**
**Break-even point: 8 devices**

### Performance

| Metric | Hand-written | Schema |
|--------|--------------|--------|
| Cold start | 0.1 µs | 0.1 µs |
| Decode speed | 2,603,870 ops/sec | 201,816 ops/sec |
| Relative | 100% | 8% |

**Analysis**: While the schema interpreter is 12x slower, 200K ops/sec is still far above typical LoRaWAN throughput requirements (<1000 messages/sec even for large deployments).

### Recommendation

Schema-driven approach is suitable for production:
- Massive storage savings across device fleet
- Self-documenting schemas
- Easier maintenance and updates
- Performance is adequate for LoRaWAN workloads

## Related Work

- `reference-impl/js/tlv-decoder.js` - TLV schema interpreter
- `reference-impl/schemas/milesight-am307.yaml` - Milesight TLV example
- `reference-impl/schemas/elsys-ers.yaml` - Elsys TLV example
- `reference-impl/benchmarks/benchmark_tlv.js` - TLV benchmark
- `spec/sections/04-nested-conditional.md` - TLV spec section
- `docs/proposals/REPEAT-TLV-EXTENSION.md` - TLV design proposal
