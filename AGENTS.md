# AGENTS.md

Working instructions for AI coding agents (and new contributors) in this
repository. Read this file first, then [`docs/INDEX.md`](docs/INDEX.md) for the
generated map of every document, tool and device schema.

## What this repository is

The reference implementation of the **LoRaWAN Payload Schema** language: a
declarative YAML/JSON format that describes how a device's binary uplink is laid
out, so one schema can produce decoders for many platforms.

It sits in the middle of three layers. Know which layer a change belongs to:

| Layer | Where | Contains |
|---|---|---|
| Specification | `la-payload-schema` (LoRa Alliance, companion to TS013) | The normative language definition and conformance requirements |
| **Reference implementation (this repo)** | `MultiTechSystems/device-payload-schema` | Interpreters (Python, JS, Java, Go, C, C#), generators, validators, device schemas |
| Consumers | e.g. `eclipse-thingweb/td-tools` | Pin this repo as a submodule and drive it |

Consequence: a language change is a specification change first. Do not add a
construct to the interpreter without a corresponding spec section, or the
implementations silently diverge from the normative text.

## Start here

| Task | Document |
|---|---|
| Find anything in the repo | [`docs/INDEX.md`](docs/INDEX.md) (generated) |
| Look up language syntax | [`docs/SCHEMA-LANGUAGE-REFERENCE.md`](docs/SCHEMA-LANGUAGE-REFERENCE.md) |
| Author a new device schema | [`docs/SCHEMA-DEVELOPMENT-GUIDE.md`](docs/SCHEMA-DEVELOPMENT-GUIDE.md), [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md) |
| Convert a vendor's TTN codec | [`docs/TTN-CODEC-CONVERSION-GUIDE.md`](docs/TTN-CODEC-CONVERSION-GUIDE.md) |
| Add semantic annotations | [`docs/IPSO-REFERENCE.md`](docs/IPSO-REFERENCE.md), [`docs/WOT-REFERENCE.md`](docs/WOT-REFERENCE.md), [`docs/OUTPUT-FORMATS.md`](docs/OUTPUT-FORMATS.md) |
| Understand a design decision | [`docs/LANGUAGE-ANALYSIS.md`](docs/LANGUAGE-ANALYSIS.md), [`docs/FAQ.md`](docs/FAQ.md) |
| Check what is implemented where | [`docs/SPEC-IMPLEMENTATION-STATUS.md`](docs/SPEC-IMPLEMENTATION-STATUS.md) |

## Commands

```bash
# Decode a payload with the Python reference interpreter
python3 tools/schema_interpreter.py decode \
    schemas/devices/decentlab/dl-5tm.yaml "02 1234 0003 01F4 0190 0C1C"

# Validate one schema (structure + its embedded test vectors)
python3 tools/validate_schema.py schemas/devices/<vendor>/<model>.yaml -v

# Score quality, one schema or the whole tree
python3 tools/score_schema.py schemas/devices/<vendor>/<model>.yaml
python3 tools/score_schema.py schemas/devices --all --report score-report.json

# Generate a TS013 JavaScript codec (ChirpStack / TTN)
python3 tools/generate_ts013_codec.py <schema.yaml> -o <output-dir>

# First-pass conversion from a vendor codec - a starting point, not a finished
# schema. See "Converting a vendor codec" below before trusting the output.
python3 tools/convert_decentlab.py <vendor-codec.js>
python3 tools/convert_milesight.py <vendor-codec.js>

# Tests
make test          # C self-tests + pytest
make pytest        # Python only
make fuzz-quick    # 10-second fuzz, cheap enough for every commit

# The Go, Java and C# interpreters, in containers - no local toolchain needed
make test-go
make test-java
make test-dotnet
make test-languages   # every implementation: Python, C, Go, Java, C#

# Regenerate the repository index after adding docs, tools or schemas
python3 tools/generate_docs_index.py
```

Note: `make validate` only covers `examples/*.yaml`. Device schemas under
`schemas/devices/` are not part of that target — validate and score those
explicitly.

## Schema language cheat sheet

```
TYPES:        u8 u16 u24 u32 u64 | s8 s16 s24 s32 s64 | f16 f32 f64 | bool
              ascii hex hex:upper bytes base64 | number string | skip enum
STRUCTURES:   object | repeat | byte_group | tlv
NAMING:       name | name_from ("region_${region_id}_dwell")
TAG KEYS:     "[1, 200]" exact | "[1, !0]" excluding | "[2, *]" any
MODIFIERS:    add mult div | lookup (sequence or sparse mapping) | polynomial
              | compute | guard | transform
CONDITIONALS: match (value) | flagged (bitmask) | tlv (tag dispatch)
TRANSFORMS:   sqrt abs pow floor ceiling clamp log10 log
COMPUTE OPS:  add sub mul div mod idiv
GUARD OPS:    gt gte lt lte eq ne
ENCODINGS:    sign_magnitude bcd gray
REFERENCES:   $field_name | var: name | use: definition
```

```yaml
name: sensor
version: 1
fields:
  - name: temperature
    type: s16
    div: 10
    unit: "°C"
    # All three annotations are needed for the semantic points; see
    # decentlab/dl-5tm.yaml for the canonical form.
    ipso: {object: 3303, instance: 0, resource: 5700}
    senml: {name: "temperature", unit: "Cel"}
    semantic: "air.temperature"
    valid_range: [-40, 85]
  - name: humidity
    type: u8
test_vectors:
  - name: basic
    payload: "00E7 32"
    expected:
      temperature: 23.1
      humidity: 50
```

## Quality tiers

`tools/score_schema.py` scores each schema out of 100 and assigns the tier
defined in the specification's Section 10: Platinum 95-100%, Gold 85-94%,
Silver 70-84%, Bronze 60-69%, **Rejected** below 60% ("insufficient coverage for
repository acceptance"). Most schemas in this repository are currently Rejected
because they ship no test vectors.

| Points | Requirement |
|---|---|
| 12 | Passes structural validation |
| 8 | Has usable `test_vectors` (payload *and* expected) |
| 20 | Python interpreter decodes all vectors correctly |
| 15 | Generated JS codec decodes all vectors correctly |
| 12 | All `match`/`flagged`/port branches entered by a vector |
| 8 | Edge cases covered (zero, max, negative, min payload) |
| 5 | At least 5 vectors |
| 20 | Correct IPSO + SenML + semantic annotation of detectable sensor fields |

Gold and Platinum additionally have **gates** (PS-239, PS-264), not just points. A
schema missing any of these is capped below Gold however high it scores: at least
5 vectors, vectors passing, every branch covered, edge case vectors present, no
incorrect annotations, and at least one vector with an independent `source:`.

Three things to internalise before doing schema work:

1. A schema with no `test_vectors` cannot exceed Rejected, however correct it is.
2. The test gates total 80 points, so perfect vector coverage *alone* reaches
   only Silver. Platinum requires correct annotations too.
3. **A high score is not proof of correctness.** The tool runs a schema against
   its own vectors, so it measures self-consistency. Vectors generated from our
   own decoder score perfectly while encoding whatever bug they captured — this
   is not hypothetical, it is how a schema that mis-decoded every real payload
   held a 100% Platinum score. Declare `source:` on every vector
   (`vendor-doc`, `vendor-codec`, `field-capture`, `spec-example`, or
   `generated`). Scoring enforces this by default: with no independently sourced
   vector a schema is capped at Silver (PS-264).

```bash
python3 tools/score_schema.py schemas/devices --all --baseline score-baseline.json

# Score the rubric without the PS-264 provenance gate.
python3 tools/score_schema.py <schema> --no-require-provenance

# Independent oracles. Use these before trusting a score.
python3 tools/crossvalidate_decentlab.py --vendor-dir ../decentlab-decoders
python3 tools/crossvalidate_ttn.py --devices-repo ../lorawan-devices \
    --vendor milesight-iot --schema-dir schemas/devices/milesight
```

The TTN device repository (`TheThingsNetwork/lorawan-devices`) carries, for most
vendors, both a declared set of input/output examples and the vendor's own
JavaScript decoder. Both are independent of this implementation, so they are the
right source for `test_vectors` on any of its 152 vendors — not our own output.

`score-baseline.json` is the committed baseline; CI fails on a *regression*
rather than on the backlog. Refresh it deliberately when scores legitimately
improve, and say so in the commit message.

Use the existing platinum schemas as templates: `decentlab/dl-5tm`,
`decentlab/dl-atm22`, `digital-matter/oyster`, `dragino/laq4`.

## The corpus is the conformance suite

The 1,116 test vectors in `schemas/devices/` are the shared cross-language test set.
Every implementation has a runner that reads the same YAML and the same vectors:

| Implementation | Runner | Vectors passing |
|---|---|---|
| Python | `tests/test_corpus_conformance.py` | 1116 / 1116 |
| Go | `go/schema/corpus_conformance_test.go` | 1116 / 1116 |
| C# | `dotnet/PayloadSchema.Tests/CorpusConformanceTests.cs` | 1116 / 1116 |
| Java | `bindings/java/.../CorpusConformanceTest.java` | 1116 / 1116 |
| C | none - consumes a binary schema, not YAML | n/a |

Every runner compares its pass count against a committed floor rather than requiring
the whole corpus, so any gap stays visible and a regression fails the build. All four
YAML implementations now decode the entire corpus, so **every floor is the full 1116
and any failure is a regression, not a known gap.** If you add a construct that one
implementation cannot express yet, lower its floor deliberately and say why here.

A vector's port is written `fPort` or `fport`, and a runner that reads only one
spelling decodes a port-based schema with no port at all - every field of it then
reports as missing, which reads as an interpreter gap rather than a runner defect.
Read both, as all four runners now do.

Run them all with `make test-languages`. This suite is why the Go and Java TLV
defects were found: those implementations returned an empty result for every
channel/type schema in the repository, and no test had ever read one.

## Converting a vendor codec

The converters in `tools/` (`convert_decentlab.py`, `convert_milesight.py`) do the
mechanical part: they read a vendor's JavaScript codec and emit a schema that is
*close*. Finishing it is your job, and the process is documented in
[`docs/SCHEMA-DEVELOPMENT-GUIDE.md`](docs/SCHEMA-DEVELOPMENT-GUIDE.md).

**A `# formula:` comment is a handoff, not a leftover.** Where a converter cannot
translate a vendor expression, it emits the field with a plain type and leaves the
expression in a comment beside it:

```yaml
- name: cumulative_precipitation
  type: u16
  # formula: (x[2] + x[3] * 65536) * this.PARAMETERS.resolution
```

That comment is the hint you finish. The field as it stands reports a raw sensor
word, so a schema still carrying hints is not done, whatever it scores. Write the
real syntax — `compute` for values spanning two words, `polynomial` for a fitted
curve, `ref` plus `transform` for anything affine — and remove the comment only
once the field is right. Do not delete a hint you have not implemented: it is the
record of what the device actually does.

The example above is not hypothetical. Reading only `x[2]` also left `x[3]`
unconsumed, so every group after it decoded from the wrong offset, and the battery
voltage in the next group read as 0 despite its own definition being correct. An
unfinished hint is not a contained problem.

**If an automatic conversion does not agree with the vendor, put the hint back.**
When you extend a converter and the field it now translates still disagrees on the
vendor's payloads, revert that field to its `# formula:` comment and let it go
through the next pass. Do not leave the failed attempt in place: a hint advertises
that the field is unfinished, whereas plausible-looking arithmetic that happens to
be wrong reads as finished work and will be trusted. The hint is the safe state,
so falling back to it is progress, not a retreat.

**The vendor's decoder is the oracle, and comparing against it is the loop.** Run
the vendor codec and the schema over the same payloads and diff the outputs:

```bash
python3 tools/crossvalidate_decentlab.py --vendor-dir ../decentlab-decoders
python3 tools/crossvalidate_ttn.py --devices-repo ../lorawan-devices \
    --vendor milesight-iot --schema-dir schemas/devices/milesight
```

Every payload the two agree on is a test vector you have earned: record it with
`source: vendor-codec`, which is independent provenance under PS-264 and the
honest way for one of these schemas to leave Rejected tier. A disagreement is a
finding to investigate, not a number to overwrite — and vendor data can be wrong
too, so check the decoder against the vendor's own documentation when they differ.

**Re-clone the Decentlab oracle when you need it**; it is not vendored here:
`git clone --depth 1 https://github.com/decentlab/decentlab-decoders`. The TTN
checkout at `../lorawan-devices` is permanent.

## Rules for this repository

**Test vectors must come from outside our own decoder.** A vector produced by
running our interpreter and recording what it printed proves nothing — it locks
in current behaviour, including bugs. Derive expected values from the vendor's
documentation or from the vendor's published TTN codec, and treat a disagreement
as a finding to investigate, not a number to overwrite. Regression vectors
generated from our own output are acceptable only when labelled as such.

**Do not hand-edit generated files.** `docs/INDEX.md` is generated by
`tools/generate_docs_index.py`; `generated/`, `output/` artifacts and generated
codec headers come from the tools. Change the generator, not its output.

**`tools/schema_interpreter.py` is a public API surface.** External consumers
(notably `td-tools`, which imports it by file path and calls
`SchemaInterpreter(schema).decode(payload, fPort=...)` and reads
`result.success` / `.errors` / `.data`) depend on its location and signatures.
Renaming or moving it breaks them silently — coordinate first.

**Every new or changed device schema** must validate, carry test vectors, and be
scored before commit. Record the tier you reached.

**Python style:** black with line length 88; tools must stay compatible with
Python 3.8 (`typing.List`, not `list[...]`; no `X | Y` annotations).

## Current gaps

Known weaknesses, so you neither trip over them nor assume they are intentional:

- **`schemas/payload-schema.json` is permissive.** `definitions.field` sets
  `additionalProperties: true`, declares no required keys, and types `type` as a
  bare string. It accepts an unknown wire type such as `s17`, a field with no
  `name`, and `mult: "0.1"`. For real structural checking use
  `tools/validate_schema.py`, which knows the type vocabulary.
- **CR-2026-004 is implemented in Python, Go, Java and C#, not in C.** `name_from`
  and `!`/`*` case keys do not apply to the C interpreter: it has no TLV support and
  fixed-size name buffers, and it consumes a binary schema with no place for a
  template. It does honour PS-269 (an unmatched lookup omits the field). A `default`
  on a mapping lookup is Python, Go, Java and C# only, because the binary schema has
  no slot for it.
- **Modifier order is now canonical everywhere — do not reintroduce key order.**
  Per CR-2026-002 (PS-101/PS-102), bare `mult`/`div`/`add` apply in the order
  *mult, div, add* regardless of how the keys were written, and `transform` is the
  only way to express another order. All implementations were brought onto this:
  Python, Go, Java, C#, the TS013 JS generator and the firmware C generator were
  applying source key order (or, in Go/Java/C#, three different orders depending
  on input format and code path); the C interpreter, its node/python/go FFI
  bindings, and the binary schema form were already canonical.
  `examples/canonical-modifier-order.yaml` is the cross-language fixture that pins
  this and is checked in CI — if you touch modifier handling in any language, that
  fixture must still decode identically. `validate_schema.py` warns when a field
  carries two or more bare modifiers, since the intent reads better as a
  `transform`.
- **Many published schemas are still unverified.** 62 of 158 device schemas ship
  with no test vectors and are therefore Rejected. See the per-device table in
  [`docs/INDEX.md`](docs/INDEX.md). The decentlab and milesight families have been
  through a vendor cross-validation pass; the rest have not.
- **Decentlab: 55 of 58 agree with the vendor decoder.** The three left are
  `dl-blg` (a thermistor needing a natural logarithm), `dl-iam` (a `max()` of two
  linear combinations) and `dl-zn2` (a difference of two two-word values). Only
  the logarithm needs a language feature: `log` exists as a transform stage in the
  Python interpreter but not in Go, Java or C#, so using it would break
  cross-language parity.
- **Squares are `compute`, not `pow`.** The Python interpreter accepts `pow`,
  `log` and `sqrt` transform stages; the other three do not. Where the vendor
  squares a value, multiply it by itself with `compute` — that decodes identically
  everywhere. Adding the missing ops to the other three would be the alternative,
  and is worth doing before any schema genuinely needs a logarithm.
- **Two wrongly matched shapes that leave no hint.** Both were pattern-matched by
  the converter, so unlike an untranslatable expression nothing marked them
  unfinished — and a wrongly matched field is more dangerous than an unmatched one:
  - *Offset-binary read as `s16`.* Decentlab sensors subtract 32768 from an
    unsigned word, so the value is `u16` with `add: -32768`, not a two's-complement
    read. For word `0x8a77` an s16 gives -300.89 where the device means 26.79. 52
    fields were declared this way. It is the same defect that let `dl-alb` hold a
    100% Platinum score while mis-decoding every payload.
  - *`x[i] + x[j] * 65536` read as a big-endian `u32`.* The vendor puts the low
    word first, so a u32 takes the halves the wrong way round: for words
    `a9fd 0134`, `dl-rhc` reported a sensor id of 2851930420 instead of 20228605.
    Read the two words and combine them with `compute`. `dl-isf` carried the same
    defect while still passing cross-validation, because the vendor's own test
    payload does not exercise that field — passing cross-validation means only
    that the payloads you have agree.
- **Milesight's remaining gaps are language features, not schema neglect.** 32 of
  the 84 still disagree with the vendor's decoder. The missing TLV channels were
  measured: of 790 channels the vendor decodes, 724 are covered and 66 are not, and
  **none of the 66 is a plain scalar channel that was simply forgotten**:
  - 54 are history records. These ARE expressible: a tlv case holding one
    `type: object` field reproduces the vendor's
    `history: [{timestamp, temperature, ...}]` exactly, because PS-157 collects
    repeated tags into an array. Four are done; of the rest, 21 need the vendor's
    label maps applied as `lookup` tables, 11 need padding modelled with `skip`,
    and the remainder build their output key from payload content (below);
  - 27 build field *names* from payload content (`"region_" + n + "_avg_dwell"`,
    `"sdi12_" + n`), which needs computed keys - see CR-2026-004;
  - 2 (`ws136`, `ws156` channel 255/52) are commented out in the vendor's own
    decoder, so it emits nothing there either and omitting them is correct.
  Those two blockers are now resolved by CR-2026-004: `ws101` uses a sparse mapping
  `lookup`, and `uc1114`/`uc1152` use `"[1, !0]"` and `"[9, *]"` case keys. Enum labels, version and serial channels, packed flag bytes, units and
  annotations are done.
- **Three TTN declared examples are stale, not two.** `ws50x`, `uc1114` and
  `uc1152` all declare values their own vendor decoder does not produce - `true`
  where the decoder yields `"on"`. Always prefer the decoder; `crossvalidate_ttn.py`
  reports the two oracles separately so the difference is visible.
- **Units come from TTN's payload schema, not from guesswork.** `lib/payload.json`
  in the device repository documents each normalized measurement and its unit, and
  the vendor's raw values are already in those units. Note that TTN's normalized
  `battery` is a *voltage* while the milesight raw field is a percentage 0-100, so
  those fields declare `sensor: percentage` to stop the scorer detecting them as
  IPSO 3316 voltage. Quantities whose SenML unit is a different scale (pressure in
  hPa against SenML's Pa, distance in mm against m) carry a `unit:` but no
  annotation, because a mismatched annotation misdeclares the value.
- **Flags packed into a byte need `consume: 1` on the last field.** An explicit bit
  range (`u8[4:4]`) never advances the read position by itself, so a channel of bit
  ranges consumes nothing and the TLV loop reads the value byte as the next tag.
  `ws50x` and three others failed to decode the vendor's own payload for this
  reason. One case remains unsolved: `em320-tilt` packs a flag into bit 0 of bytes
  already consumed by its angle fields, which no current construct expresses.
- **Some TTN declared examples are stale.** For `ws50x`, TTN's declared example
  says `switch_1: true` where the vendor's own decoder in the same repository says
  `"on"`. `crossvalidate_ttn.py` reports the two oracles separately for this
  reason; when they disagree, the vendor's decoder is the better authority. None of these schemas declare `unit:`, so they
  carry no semantic annotations and stop at Silver; the scorer's keyword heuristic
  would read milesight `battery` as IPSO 3316 voltage when it is a percentage, so
  units have to be established per device before annotating.
- **The JS TS013 generator has a shared-byte bug.** `generate_ts013_codec.py`
  can drop bare bit-range fields and fail to advance the cursor. The Python
  interpreter path is correct; only generated JavaScript is affected.
