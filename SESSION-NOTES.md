# Session Notes

## Session: Aug 6, 2026

### Completed

**Cross-language conformance suite (the big one)**
- The corpus is now the shared test set: 1,116 vectors across 98 schemas, run by a
  runner in every implementation reading the same YAML.
  - `tests/test_corpus_conformance.py` 1116/1116
  - `go/schema/corpus_conformance_test.go` 1101
  - `dotnet/PayloadSchema.Tests/CorpusConformanceTests.cs` 1082
  - `bindings/java/.../CorpusConformanceTest.java` 1052
  - C has no runner: it consumes a binary schema, not YAML.
- Non-Python runners assert against a committed floor, so known gaps stay visible and
  a regression fails. **Raise the floor whenever a gap closes.**
- Before this existed, Go and C# each read two hardcoded schemas and Java none, which
  is how Go and Java came to return an empty result for *every* TLV schema in the
  repository without a test failing.

**Interpreter defects found by running the corpus**
- Go and Java: inline `- tlv:` blocks never got their cases (type assigned after
  parse), so no channel/type schema decoded. Fixed in both.
- Go and C#: composite case keys compared as strings against a tag rendered without a
  space, `[1,117]` vs the `"[1, 117]"` schemas are written with. Fixed in both.
- Go and Java: the sequence form `lookup: ["off", "on"]` was unparsed - hundreds of
  corpus fields reported raw integers. Fixed in both.
- Go: `DecodeContext.Read` bounds-checked only the upper end, so `length: -1` panicked
  and took the caller down. Now treats negative as the remainder.
- Go: guards were evaluated *after* compute, so a guarded division by zero aborted the
  whole payload (dl-alb, vicki). Conditions are checked first now.
- Go: no `hex` type (constant is `"Hex"`, no lowercase alias) and no `u8[lo:hi]` bit
  ranges - both added.
- C#: `ParseFieldType` strips the range so `u8[0:0]` read the whole byte. Routed to
  the Bits path, which now honours `consume`.
- Python: `hex` emitted uppercase, violating PS-074; `hex:upper` was unreachable
  because any type containing a colon parsed as a bit range.
- All four disagreed four ways on an unmatched `lookup`: raw value (Python, Go, Java)
  or the string `"unknown(42)"` (C). All now omit the field per PS-269.

**CR-2026-004 - implemented and applied**
- `name_from` computed keys, sparse mapping `lookup` with `default`, negated/wildcard
  TLV case keys (`"[1, !0]"`, `"[2, *]"`). PS-265..PS-270.
- Spec text applied to Clauses 2, 3 and 4; CR moved to `change-requests/implemented/`.
- Implemented in Python, Go, Java, C#. **Not C** - no TLV, fixed-size names, binary
  schema has no slot for a template or a default.

**Milesight family**
- 84 Rejected -> 18 Platinum, 33 Gold, 22 Silver, 11 Rejected (mean 14% -> 82.3%).
- Vendor agreement 0 -> 47 of 84, measured against the vendor's own JS decoder.
- Fixed: 13 conversions recorded in comments but never applied (humidity /2 etc.),
  version and serial channels (were `u8`, vendor reads 1-8 bytes), packed flag bytes,
  enum labels from vendor ternaries and status maps, units from TTN's payload schema.

### Next

1. **Java is the biggest conformance gap** (64 failures): computed `ref` fields are not
   evaluated and `u8[lo:hi]` bit ranges are unsupported. The same two fixes just made
   in Go and C# should close most of it. Then raise its floor.
2. **C#**: `rbs30x`'s `match` cases decode nothing; `vicki.sensorTemperature` differs
   by more than rounding (46.95 vs 14.76) - a real computed-field bug, not a tolerance
   issue. Both need the `round` transform op too (Go also lacks it).
3. **Go**: `byte_group` (oyster, ers) and `round`.
4. **`name_from` unlocks 27 milesight channels** that were blocked on computed keys -
   they can be added now that the construct exists in four implementations.
5. **Milesight leftovers**: 26 channels need per-device modelling (conditional members,
   loops over bit maps); 11 schemas are still Rejected for want of 5 verified vectors.
6. **Decentlab**: 37 of 58 still disagree with the vendor decoder - untouched since the
   offset-binary fix.
7. **CR-2026-002 and CR-2026-003** are in `change-requests/submitted/` awaiting WG
   review. 004 is implemented.

### Notes for whoever picks this up

- Re-clone the Decentlab oracle: `git clone --depth 1
  https://github.com/decentlab/decentlab-decoders` - the copy used today was in a
  session scratchpad and is not persistent. The TTN checkout at
  `~/Workspace/lora/tools/lorawan-devices` is permanent.
- Three TTN declared examples are stale and disagree with their own vendor decoder:
  `ws50x`, `uc1114`, `uc1152` all declare `true` where the decoder yields `"on"`.
  Prefer the decoder; `crossvalidate_ttn.py` reports the two oracles separately.
- `uc1114`'s vendor decoder reads past the end of the payload (its loop has no
  `else break`), so it reports a field for a channel type its own condition excludes.
  We deliberately do not reproduce that, so cross-validation will keep flagging it.
- A field-block scan that jumps to the end of each block never sees fields nested
  inside an `object`. This bit twice today - when annotating history records and when
  marking label fields. Advance by one line instead.
- `schemas/library/gateway/telemetry-v1.yaml` is untracked and was left alone all
  session: it predates this work and is not mine to commit.

## Session: Feb 24, 2026

### Completed

**Output JSON Schema Generation**
- Added `tools/generate_output_schema.py` for generating JSON Schema describing decoder output
- Generates draft-07 compliant schemas with type constraints, ranges, descriptions
- Enables standard JSON Schema validation of codec output
- Updated README, SPEC-IMPLEMENTATION-STATUS.md, OUTPUT-FORMATS.md documentation

**MClimate Vicki Schema**
- Created `schemas/devices/manual/mclimate-vicki.yaml` for Vicki Smart Radiator Thermostat
- Added `mod` and `idiv` compute operators across Python, Go, and JS interpreters
- Fixed `byte_group` field referencing in interpreter and validator
- Fixed `ref` field modifier application (`mult`, `div`, `add`)
- Generated JS codec and output schema

**Schema Language Extensions**
- `compute` now supports: `add`, `sub`, `mul`, `div`, `mod`, `idiv`
- `transform` now supports: `{op: round, decimals: N}` syntax
- Updated JSON meta-schema to include new operators
- Updated documentation (SCHEMA-LANGUAGE-REFERENCE.md, .cursorrules)

**Schema Development Process**
- Created `tools/analyze_codec.js` for extracting test vectors from existing JS codecs
- Created `docs/SCHEMA-DEVELOPMENT-GUIDE.md` with best practices for codec conversion
- Added schema development section to `.cursorrules`
- Process: Analyze original → Generate test vectors → Write schema → Validate → Document deviations

**Interpreter/Generator Fixes**
- Added `round` transform to Python interpreter (`{op: round, decimals: N}` syntax)
- Added `round` transform to JS generator (generates `Math.round()` calls)
- Vicki schema now produces identical output to original TTN codec for keepalive messages

---

## Session: Feb 19, 2026

### Completed

**Testing & Coverage**
- Python: 448 tests, 81.0% coverage
- Go: 331 tests, 80.1% coverage
- Added edge case tests for formula injection, recursion limits, buffer boundaries
- Added compact format edge cases (Go-specific)
- Added binary schema and base64 edge cases

**Fuzzing Infrastructure**
- Verified Python random fuzzing (`fuzz_decoder.py`)
- Verified Python Hypothesis property-based tests
- Verified Go native fuzzing (`fuzz/go/decoder_test.go`)
- Coverage analysis at intervals (core paths saturate quickly)

**Benchmarks**
- Ran on Ryzen 9 7950X3D (local) and Intel i5-2400 (skidoosh)
- Documented in `SPEC-IMPLEMENTATION-STATUS.md`
- Go Binary Schema: ~600K ops/sec (Ryzen), ~280K ops/sec (i5)
- Python Interpreter: ~21K ops/sec (Ryzen), ~8K ops/sec (i5)
- Cloud estimates added (t3.micro to c7g.large)

**Documentation**
- Updated `AUDIT-REPORT.md` with current test counts
- Updated `SPEC-IMPLEMENTATION-STATUS.md` with benchmarks
- Created `FUTURE-FEATURES.md` roadmap

**Semantic Fields (Implemented)**
- `valid_range: [min, max]` - bounds checking with `_quality` flags
- `resolution` - minimum detectable change metadata
- `unece` - standard unit codes (UNECE Recommendation 20)
- Schema validation in `validate_schema.py`
- Python interpreter support with quality output
- Go interpreter support with quality output
- Test coverage in both Python and Go

---

## Future TODO

See [FUTURE-FEATURES.md](docs/FUTURE-FEATURES.md) for detailed roadmap.

**Planned:**
- Embedded codegen: bounds constants from `valid_range`, scale constants from `resolution`
- Output format extensions: SenML vmin/vmax, IPSO 5603/5604
- Batch generation of output schemas for all device schemas

**Per-Device Deliverables (Required):**
1. YAML Schema (`device.yaml`) - Source payload definition
2. JS Codec (`device-codec.js`) - TS013-compliant decoder
3. Output Schema (`device-output.schema.json`) - JSON Schema for decoded payload

**Out of Scope (device profile, not schema):**
- `accuracy`, `instrument_range` - static sensor characteristics belong in device registries

### References

- [FUTURE-FEATURES.md](docs/FUTURE-FEATURES.md)
- UNECE Recommendation 20
