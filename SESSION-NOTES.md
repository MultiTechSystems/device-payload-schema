# Session Notes

## Session: Aug 7, 2026 (later)

### Completed

**Decentlab: vendor agreement 21 -> 55 of 58**
- `convert_decentlab.py` translated a vendor `convert` with a chain of regexes and
  parked anything unmatched in a `# formula:` comment, emitting a bare u16. That is
  a deliberate handoff, not a bug - the script gets close and the remaining syntax
  is ours to write - but 80 fields across 36 schemas were still sitting on it.
- Added a general affine fitter: evaluate the vendor expression at two points, fit
  `a*x + b`, confirm at a third so a polynomial or logarithm is rejected rather than
  silently linearised. Exact to 8.3e-16 against a 1e-3 tolerance. 52 fields.
- Wrote the real syntax by hand for the rest: `compute` for two-word values,
  `polynomial` for cubics, chained `ref` for derived values. 13 + 12 more fields.
- Two shapes were *wrongly* matched rather than unmatched, so no hint warned anyone:
  52 offset-binary fields declared `s16` (for word 0x8a77 that is -300.89 where the
  device means 26.79), and `x[i] + x[j]*65536` emitted as a big-endian `u32`.
- Three schemas remain on hints: `dl-blg` (needs a natural log), `dl-iam` (`max()`
  of two linear combinations), `dl-zn2` (difference of two two-word values).

**All four YAML implementations reached the full device corpus, then it grew**
- Java 1052 -> full, C# 1082 -> full, Go 1101 -> full, via computed fields, bit
  ranges, `byte_group`, `s24`, inline `match`, hex case/lookup keys, `ne` guards,
  the `round` transform and simple-tag TLV.
- Two runner defects mimicked interpreter gaps: `fPort` vs `fport` (oyster decoded
  with no port at all), and `byte_group` assembling bytes little-endian in Go and C#.

**Bitfields crossing a byte boundary were silently wrong in three languages**
- `u24[0:11]` returned 11 where the device means 1320. Python, Java and C# read
  `buf[pos]` alone and discarded the declared base width; only Go used it.
- `rakwireless/qingping` depended on it, *and* declared its temperature as
  `u24[4:23]` where the vendor reads 12 bits at `[12:23]`, *and* had no consumer for
  either raw field - so it reported no temperature or humidity at all. Fixed, with a
  vector from the vendor decoder. Note that decoder is broken as published
  (`Decoder(bytes, port)` then reads `data[...]`); TTN's own live page returns
  `{"error": "data is not defined"}`, so nobody has ever run it.

**Corpus coverage sweep: 1116 -> 1166 vectors**
- Asked which constructs no vector exercises. Answer: repeat, name_from,
  bitfield_string, formula, until, byte_length, merge, unknown, default, skip,
  header, enum. Fixtures for each now live in
  `schemas/devices/_language-conformance/`.
- That found two spec violations: PS-067 (Java had no `enum` type at all, reporting
  the raw integer) and PS-068 (`default:` on an enum ignored by Python, Go *and* C#).
- `tools/compose_library_vectors.py` makes `schemas/library/` runnable. Those files
  are definition catalogues, not schemas, so nothing could decode them and their
  vectors were verified by nothing. 42 composed in; 7 quarantined into
  KNOWN-ISSUES.md because they do not decode and had never been executed.

**CR-2026-005 submitted** (in la-payload-schema): `u32le16`/`s32le16` for a 32-bit
value carried as two 16-bit units, low unit first. Not an endianness variant - the
payload is a word stream with no 32-bit field on the wire - but the bytes do land
in a recognisable CDAB order, and 30 occurrences across 16 vendor decoders make it
worth a type. 17 values currently spend 14 lines of `compute` each.

### Next

1. **Settle the bitfield form.** The specification lists five spellings and the
   direction under discussion is to keep only the bracket form `u8[3:4]`. If that is
   settled, the work is to drop the other four from the spec and the Python
   interpreter, and rewrite `lorawan/lorawan_frames.yaml`, the only file using the
   sequential form. That rewrite is mechanical and verified equivalent, and it
   returns Go, Java and C# to the full 1166 without touching an interpreter. **Do
   not close that gap by implementing `u8:N` three more times.**
2. **Work through `_library-composed/KNOWN-ISSUES.md`** - 7 vectors. Two are
   representation mismatches (a vector says `[1,2,...]` where the field decodes to
   bytes, so probably a vector-format question rather than a decode bug), one wants
   a `type: array` that does not exist, and four disagree numerically.
   `lorawan_mac_commands__device_time_ans` matches neither endianness, so its
   expected value is simply wrong.
3. **Decide `header:`.** Undocumented, unused by any schema, honoured by Java and C#
   and ignored by Python and Go. The library's own `common/headers.yaml` implements
   headers as `definitions:`, and `definitions` + `$ref` covers the use case more
   generally, so the likely answer is to delete it - or define it as sugar.
4. **The three remaining Decentlab hints.** Only `dl-blg` needs a language feature:
   `log` exists as a transform stage in Python but not in Go, Java or C#. Adding
   `pow`/`log`/`sqrt` to those three would also let squares stop going through
   `compute`.
5. **Wire the preprocessor into validate and score.** A library-composed schema
   cannot be scored today; `validate_schema` rejects an unflattened `$ref` schema
   (correctly, and loudly - the one tool that behaved well all session).
6. **Push.** 16 commits in payload-codec-proto, 1 in la-payload-schema, and the
   three td-tools branches are on the fork with no PRs opened.

### Notes for whoever picks this up

- **The requirements extractor misses 21 of the 270 PS ids** in the spec - a second
  id on a line, a line ending in a colon, or no modal verb - so the traceability
  appendix under-reports by 7.8% and any audit built on it inherits the blind spot.
- **Corpus runners walk `schemas/devices/` only.** A fixture anywhere else is read
  by nothing and looks like coverage while proving nothing. That is why both
  `_language-conformance/` and `_library-composed/` sit inside the device tree.
- **Cross-file `$ref` is a pre-step, by design.** The interpreters resolve only
  local `#/definitions/...`; `schema_preprocessor.py` inlines the rest so they stay
  free of a loader. It is wired into `fuzz.yml` and nothing else.
- When inlining a definition, **splice its `fields:` rather than nesting them** - a
  nested container without `type: object` is never descended into and every field
  reports as missing.
- Vendor data is not automatically the better authority: the qingping datasheet
  contradicts itself on every sensor range, and its published decoder cannot run.

## Session: Aug 7, 2026

### Completed

**All four YAML implementations now decode the whole corpus**
- Python 1116, Go 1101 -> 1116, C# 1082 -> 1116, Java 1052 -> 1116. Every floor is
  now the full 1116, so any failure anywhere is a regression, not a known gap.
- Java gained the most: computed fields (`ref`, `polynomial`, `compute`, `guard`) were
  not parsed at all, `u8[lo:hi]` fell through to U8 and read the whole byte, `consume`
  was unmodelled, `byte_group` was absent, `s24` resolved to U8 and misaligned
  everything after it, and there was no inline `- match:` block or map-shaped cases.
- Go: TLV cases were parsed only when the block had `tag_fields`/`tag_key`, so a plain
  `tag_size` block (elsys/ers) had no cases and decoded nothing. `ne` in a guard was
  neither parsed nor evaluated. `{op: round}` was dropped.
- C#: match case keys and lookup keys went through `int.TryParse`, which does not read
  `0x01`, so hex-written cases and lookups never matched - rbs30x reported nothing
  past its header. Same `ne` and `round` defects as Go.

**Two bugs worth remembering because they mimic interpreter gaps**
- The Java and Go *runners* read a vector's port as `fPort` only, while oyster writes
  `fport`. Its seven vectors decoded with no port at all, so every field reported
  missing and it read as an interpreter gap. Python and C# already read both.
- A guard's fallback must be reported exactly as declared. Java briefly applied the
  field's modifiers to it, which turned vicki's `else: 0` into `(0 - 28.33333) /
  5.66666` and put sensorTemperature 5 degrees low.

**Modifier/transform ordering unified**
- All four now apply bare modifiers *then* transform stages, both when both are
  present. Go, C# and Java each had an either/or chain that silently dropped the
  modifier on a field that scales and then rounds. Rounding is half-to-even
  everywhere, matching the interpreter; half-up disagrees on exact halves the vectors
  contain.

**CR-2026-002 and CR-2026-003 accepted**
- Both moved to `change-requests/implemented/` in la-payload-schema; their spec text
  was already applied in 24c819e. `CR-tracking.md` rewritten - its summary counts,
  tables and statistics had all drifted apart.
- PS-264 is now enforced by default in `score_schema.py`: the gate existed behind
  `--require-provenance`, which was right while the CR was a proposal and wrong once
  it was normative. Flag inverted to `--no-require-provenance`.
- Three schemas capped at Silver as a result - `digital-matter/oyster`,
  `dragino/laq4`, `mclimate/vicki` - all because no vector declares a `source`. No
  provenance was invented for them; that is the point of the requirement.
- `payload-schema.json` gained `source` with the five PS-263 values.

**Everything pushed**
- `payload-codec-proto` master and `la-payload-schema` main are pushed; the three
  td-tools branches are on the MultiTech fork with conventional-commit messages and
  ECA sign-off, no PRs opened yet.

### Next

1. **Decentlab is the largest correctness debt**: 37 of 58 still disagree with the
   vendor decoder, untouched since the offset-binary fix. Re-clone the oracle first.
2. **Provenance for the three capped schemas** - cross-check oyster, laq4 and vicki
   against vendor docs or decoders and declare the real `source`. Do not guess one.
3. **`name_from` unlocks 27 milesight channels** blocked on computed keys.
4. **Milesight leftovers**: 26 channels need per-device modelling; 11 schemas are
   still Rejected for want of 5 verified vectors.
5. **Open the three td-tools PRs**, catalog fix first - without it the conformance
   suite runs on 6 conversions instead of 163 and still passes.

### Notes for whoever picks this up

- The repo `.venv` was dead (built June 3 for Python 3.12 at an old path, no pip), so
  `make test-languages` failed at the pytest step. Rebuilt from requirements.txt.
  `hypothesis` was missing from the user environment too; test_hypothesis.py could not
  even be collected.
- The C# test `YamlKeyOrderMatters` was renamed to `ModifiersApplyInCanonicalOrder`.
  Its assertion held either way, but the name asserted the contract CR-2026-002
  removed.
- Everything in the Aug 6 "Next" list is now done except the schema-coverage items,
  which are items 1-4 above.

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
   *(Aug 7: both accepted and implemented - see the session above.)*

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
