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

The 1,193 test vectors across the 162 schemas in `schemas/devices/` that carry any (of
220 YAML files) are the shared cross-language test set. Every implementation has a
runner that reads the same YAML and the same vectors:

| Implementation | Runner | Decode | Re-encode |
|---|---|---|---|
| Python | `tests/test_corpus_conformance.py` | 1193 / 1193 | 1131 |
| Go | `go/schema/corpus_conformance_test.go` | 1193 | 1137 |
| C# | `dotnet/PayloadSchema.Tests/CorpusConformanceTests.cs` | 1193 | 1131 |
| Java | `bindings/java/.../CorpusConformanceTest.java` | 1193 | 1131 |
| C | none - consumes a binary schema, not YAML | n/a | n/a |

Every runner compares its pass count against a committed floor rather than requiring
the whole corpus, so any gap stays visible and a regression fails the build. All four
YAML implementations now decode the entire corpus, so **every decode floor is the full
1193 and any failure is a regression, not a known gap.** If you add a construct that one
implementation cannot express yet, lower its floor deliberately and say why here.

The re-encode column is the round-trip suite described under "Encoding" below, and its
floors are ratchets rather than a target: `encode(decode(payload)) == payload` cannot
hold for every vector. Update the number in this table when you move a floor.

A vector's port is written `fPort` or `fport`, and a runner that reads only one
spelling decodes a port-based schema with no port at all - every field of it then
reports as missing, which reads as an interpreter gap rather than a runner defect.
Read both, as all four runners now do.

`schemas/devices/_library-composed/` is generated by
`tools/compose_library_vectors.py` from `schemas/library/`. Those files are
definition catalogues, not schemas - no top-level `name:`/`fields:` - so nothing
could decode them and their vectors were verified by no implementation. The tool
composes each vector into a standalone schema, splicing the named definition's
`fields:` rather than nesting them, and **quarantines any vector that does not
decode** into `KNOWN-ISSUES.md` rather than adding it. Regenerate with the tool;
`--check` verifies it is current.

**Nothing is quarantined now** — `KNOWN-ISSUES.md` is gone. The seven that were
resolved as follows, and the causes are worth knowing because none was a wrong
expected value:

- **Two LoRaWAN files never declared `endian: little`.** Both
  `lorawan/lorawan_frames.yaml` and `lorawan/lorawan_mac_commands.yaml` say
  "little-endian byte order for multi-byte fields" in their header comments, and
  TS001 requires it, but neither set the key — so they composed as big-endian.
  `dev_nonce` read 0x1011 for a vector saying 0x1110, and LinkADRReq's `ch_mask`
  read 0xFF00 where TS001 says 0x00FF. Adding the key fixed both.
- **A `bytes` field has no agreed output representation.** Python decodes one to a
  bytes object, Go to a lowercase hex string. Vectors now write these as a hex
  string, the one form that survives YAML, JSON and every language, and
  `values_match` accepts hex or a list of octets against a byte sequence. **That the
  four implementations disagree on the decoded type is unresolved and unspecified** —
  worth a CR.
- **`type: array` never existed.** `repeat` with `until: end` expresses exactly the
  same thing and already worked in all four. Another "missing feature" that was not
  missing — try the current language first.
- **Three payloads had never been executed and did not match their own expected
  values under any scaling.** `gps_tracker`'s geofence read latitude 5.1018368
  against a vector saying 51.0 and a longitude of -1.3596416 against -0.12; two
  timestamps were a nibble or a byte-transposition out. In each the expected values
  carry the intent, so the payload was recomputed from them and the vector marked
  `source: generated` — the bytes now come from this schema, not a specification.

**`compose_library_vectors.py` force-quotes every string it emits, and must keep
doing so.** PyYAML left a numeric-looking string such as a hex EUI unquoted when its
own resolver would not read it back as a number, but Go, Java and C# use different
YAML implementations and theirs resolve `0102030405060708` to an integer. One EUI
arrived in Go as 1.02030405060708e+14 while the identically-shaped
`0001020304050607` stayed a string, so one vector passed and its neighbour failed
for a reason nothing in the schema explained.

**The four corpus runners are more permissive than the specification.** PS-039
requires integers to match *exactly* and PS-040 allows 0.001 for floats; every
runner instead applies a relative `max(0.001, |want| * 0.001)` to everything, which
on a GPS timestamp is about 20 days of slack — it is why one of the timestamp
vectors above looked fine to them. **Zero corpus vectors currently depend on that
laxity** (measured), so tightening the runners to PS-039 is free. Not yet done.

The Java and C# runners also did not descend into lists or maps, comparing their
printed forms instead — so a nested expectation could not be expressed. Java's
compared `{package_id=0.0}` against `{package_id=0}` and failed on the rendering
while every value in it was equal; C# read non-scalar expectations as null and
compared that against whatever was decoded. Both now recurse, per PS-044/PS-045.

**The bracket range `u8[5:7]` is the only bitfield spelling.** CR-2026-006 withdrew
the other four — `u8[3+:2]`, `bits<3,2>`, `bits:2@3` and the sequential `u8:2`. All
five were carried deliberately while the working group chose; the range won because
it says where the bits are instead of depending on a bit cursor each implementation
has to maintain identically.

Every decode floor is now the full 1193. Do not re-add a withdrawn form to "support" an
old schema: the Python interpreter, the binary encoders, the JS and TS013
generators and the C header all reject them, and the four tests that used to assert
they decode now assert they are refused.

Two of the four were never actually implemented outside Python, which is worth
knowing if anyone proposes reviving one:

- `binary_schema_v2.py` and `generate_js_decoder.py` resolved the sequential form
  to a start bit of 0, so a field the interpreter reads as bits 5-7 encoded as bits
  0-2.
- The C header set `bit_start = 255` as a sequential sentinel that no decode path
  ever read.

`schemas/library/lorawan/lorawan_frames.yaml` was the only file using the
sequential form, in **four** definitions — `mhdr`, `fctrl_uplink`,
`fctrl_downlink`, `dl_settings` — not three as an earlier note here said.

**When converting a sequential field, carry the byte consumption over too.** The
recipe `type: u8:3` + `bit_offset: 5` → `type: u8[5:7]` gets the value right and
the position wrong: the sequential form advanced the read position automatically on
reaching bit 0, and a bracket range never advances without `consume:`. So the last
field of each byte needs `consume: 1`. Without it the values still match and
`bytes_consumed` silently drops to 0, which misaligns every field after a spliced
`mhdr` or `fctrl`. Verified by decoding all 256 one-byte payloads through each of
the four definitions before and after: identical, `bytes_consumed` included.

`bit_offset:` keys were dropped in that rewrite. The interpreter never read them —
it derives the offset from the type string alone — so they were documentation that
could drift out of step with the field beside them.

`schemas/devices/_language-conformance/` holds fixtures rather than devices: one
schema per construct that no device schema uses, so the four interpreters are held
to the same behaviour for it. It sits in the device tree because that is the only
tree the runners walk — a fixture in `examples/` is read by nothing. Adding it
found `enum` unimplemented in Java (PS-067) and `default:` on an enum ignored by
Python, Go and C# (PS-068).

**The `header:` block is gone. Use `definitions` + `$ref`.** It was never in the
specification, so there was no authority for either behaviour, and the two
behaviours were measured rather than assumed: with a two-byte probe declaring one
header field and one ordinary field, Java reported both, while Python and Go
reported no header field at all *and* read the header's byte as the first field.
Nothing errored. Support is removed from Java and C#, and `validate_schema.py` now
reports a top-level `header:` — deleting it silently would have turned a
two-language divergence into four languages quietly losing a schema's first fields.

The replacement is what `schemas/library/common/headers.yaml` already used: a
`definitions:` entry pulled in with `$ref`. `schemas/devices/_language-conformance/
ref-header.yaml` holds it to the same standard as any other construct.

**That fixture immediately found `$ref` unimplemented in Java** — no `definitions`
parsing and no `$ref` resolution, in a binding whose users were being pointed at it
as the replacement. Now implemented, by splicing the referenced `fields:` into the
list at parse time, for both schema-level and per-port field lists. Note the
unrelated `ref:` key on a computed field, which Java did already support; they are
different constructs.

Run them all with `make test-languages`. This suite is why the Go and Java TLV
defects were found: those implementations returned an empty result for every
channel/type schema in the repository, and no test had ever read one.

**A `lookup` has two failure modes and they behave differently — CR-2026-009 settled
which is which.** A sequence is indexed from zero (PS-104) and an out-of-bounds index is
an **error** (PS-105/PS-285): the payload does not match the schema's shape at all. A
mapping's keys need not be contiguous (PS-268), so a value with no entry **omits the
field** (PS-269/PS-286) unless a `default` is declared. Storing the raw integer under a
name that promises a label is wrong in both cases. All five implementations honour the
distinction; C had both wrong on the ordinary-field path while its enum path a few lines
above was already correct, and the two sites disagreed with each other because C's
interpreter tests are in no build target. `src/selftest_schema.c` covers it now, and it
*is* in `SELFTEST_SRCS`.

The distinction also constrains encoding: a `default` label matches any unmapped value, so
there is no original to recover, and every encoder reports that rather than guessing.

**The unary maths transform stages — `sqrt`, `abs`, `pow`, `log10`, `log` — now
exist in all four.** Python always had them; Go, Java and C# had none, which is what
kept `decentlab/dl-blg` on an unconverted vendor formula. The domain clamps are part
of the contract, not an implementation detail: `sqrt` clamps its input at 0 and the
logs at 1e-10, because returning NaN instead poisons every later stage and every
field computed from it. `_language-conformance/transform-maths.yaml` holds all four
to it.

Two things to know before adding another transform key:

- **Go builds transform stages by hand from a map, not from its struct tags.** A
  field added to `Transform` without a matching line in that parse block is silently
  never populated: the stage parses to a no-op and the value passes through
  untouched, with nothing reported.
- **A plain typed field and a `ref`/`compute` field used to take different code
  paths in Go.** The plain-field branch was a second copy of the transform loop, and
  being a copy it had drifted — it applied `add` before `mult` where PS-101 fixes the
  order at mult, div, add, and it knew nothing of `{op: round}`. It now calls
  `applyTransformStages` like everything else.

**A compute field's transform stages were applied twice in Go and C#.** Both skipped
the re-application for a `type: number` with `ref` and forgot the `compute` case, so
`dl-blg`'s `voltage_ratio` came out as -0.4999999996 where the vendor decoder says
0.0064094 — its `div: 16777216` and `add: -0.5` each ran a second time. Fixed in
both. It survived because `dl-blg` had no test vectors at all; it now has two, taken
from the vendor decoder's own examples.

**Two Decentlab hints remain**, both in `schemas/devices/decentlab/`, and neither
needs a new language feature:

- `dl-iam` — `max(max(1.0*x0 - 1.64*x1, 0.59*x0 - 0.86*x1), 0) * 1.5504`. There is no
  `max` of two computed fields; `floor: 0` clamps a lower bound, so the outer
  `max(..., 0)` is expressible but the inner one is not.
- `dl-zn2` — a difference of two two-word values. Expressible today with two compute
  chains and a subtraction, exactly as `dl-blg`'s `voltage_ratio` assembles one.
  Mechanical; just not done.

Cross-validate either against the vendor decoder before trusting it:
`git clone --depth 1 https://github.com/decentlab/decentlab-decoders`, then run its
`DL-*.js` under node — `main()` in each carries the vendor's own example payloads,
which is where `dl-blg`'s two vectors came from.

**The C interpreter is a much smaller language than the other four.** It consumes a
binary schema for embedded use, and its `field_def_t` carries only `mult`, `div` and
`add` — there is no transform array and no compute struct. Audited against the header
rather than the comments, which claim more than the code does (`definitions`, `$ref`,
`ports`, `formula` and `byte_group` all appear only in prose):

- **Has**: `u8`-`u64`/`s8`-`s64` and their aliases, `f16`/`f32`/`f64`, `bool`,
  `bytes`, `string`/`ascii`, `hex`, `base64`, `udec`/`sdec`, `enum`, `match`,
  `object`, `skip`, the bracket bitfield range, `lookup`, `var`/`$var`, `consume`,
  per-field `endian`, and the three bare modifiers.
- **Lacks**: `compute`, `polynomial`, `guard`, `ref`, `formula`, the whole
  `transform` array (so no `round`, `sqrt`, `log`, `pow`, and no multi-stage),
  `name_from`, `tlv`, `flagged`, `repeat`/`until`/`count`/`byte_length`,
  `bitfield_string`, `number`, `version_string`, `definitions`/`$ref`, `ports`,
  `merge`, `valid_range`/`resolution`/`unece`, and the semantic mappings.

**Do not read the `Supports:` comment block at the top of
`include/schema_interpreter.h` as a feature list** — it is out of date.

**That list is a roadmap, not an accepted subset.** C serves two targets, and they
differ in their **I/O surface**, not in decode semantics — so the right structure is
one core with two front-ends, not two implementations. `schema_interpreter.hpp` is
already this pattern: a C++ RAII wrapper over the same core.

| | MCU (Zephyr) | Gateway (embedded Linux) |
|---|---|---|
| schema in | binary blob, compiled on the host | **YAML** |
| data out | native C values, no serialization | **JSON**, TS013 `{data, warnings, errors}` |
| direction | uplink packing, downlink commands | uplink decode |
| feature set | reduced on purpose | parity with the other four |
| missing today | v2 structural opcodes | **YAML reader and JSON writer** |

**The MCU tier never sees YAML or JSON** — `schema_load_binary` takes a blob,
`encode_inputs_add_int`/`add_double` take native values, and the header contains zero
occurrences of `json`. That is why its reduced feature set is correct rather than a
gap: packing an uplink and reading a downlink command need types, bitfields,
modifiers, `match` and `lookup`, not `compute` chains or semantic mappings. Every
output-representation question below is irrelevant to this tier.

**The gateway tier is the opposite: it exists to replace deployed JS codecs**, so its
JSON *is* the compatibility contract. Three pieces are missing, and the second decides
success:

1. **A YAML reader.** libyaml is fine here — an embedded-Linux gateway has megabytes
   and often already has it. Do not trade this away to protect the 18.6 KB figure;
   that number is an MCU concern and applying it here was a mistake once already.
2. **A JSON writer**, matching **JavaScript's** rendering, not Python's. See
   CR-2026-008: an integral value must serialize as `15`, not `15.0`, because that is
   what every deployed JS codec emits and a gateway swapping codecs must not appear to
   change its schema. This is the one rule that decides whether the C interpreter can
   actually replace a JS codec.
3. **The TS013 envelope** `{data, warnings, errors}`.

**There is no YAML->JSON C interpreter yet, and the JSON writer that exists is
unsafe.** `bindings/c/schema_ffi.c` has `schema_create_yaml()`, but its body is
`(void)yaml_str; return NULL;` — a declared stub. `result_to_json()` is real, and it
formats floats with `%g`, which silently destroys precision: 115020.68221552655 prints
as `115021`, 22.028848392450755 as `22.0288`, and the identifier 20228605 as
`2.02286e+07`. Do not build on it. Matching JavaScript needs the shortest
round-tripping decimal (a 1..17 `%.{p}g` search that stops when `strtod` returns the
input, or Ryu/Grisu) *plus* JavaScript's exponent thresholds — it uses fixed notation
between 1e-7 and 1e21 where `%g` switches far earlier, so `1000000.0` must print
`1000000`, not `1e+06`.

**Matching the JS codecs is a solved problem on the Python side.** Normalizing integral
values to integers is sufficient — measured with `tools/crossvalidate_js_json.py`,
which diffs serialized JSON token by token: **1142 byte-identical, zero divergences.**
No precision or exponent divergence at all, because Python's float repr and JavaScript's
number-to-string both emit the shortest round-tripping decimal. That tool is the
acceptance test for the gateway work; run it before and after any change to output
representation.

It reached zero in two steps, and the second is the transferable lesson. The 14 numeric
differences were CR-2026-007's `idiv`/`mod` convention and closed when that CR landed.
The remaining key-set differences were **TS013 generator** gaps — `name_from`, dropped
`repeat` items, missed TLV channels, unemitted members of a nested `object` — but the
tool had also been skipping lists and dicts entirely rather than comparing them, so a
`bytes` field the generator emitted as a JS array read as identical. **What a cross-check
declines to compare, it silently blesses.** Both the generator and the comparison were
fixed; if you extend the tool, make sure a new value shape is compared rather than
skipped.

**Rounding is half-to-even everywhere, and the generator now emits a helper for it.**
`Math.round` is half-up and asymmetric for negatives, which put the generated codec at
78.13 against the interpreters' 78.12 for `vicki.relativeHumidity`. Two traps are baked
into `roundHalfEven`, and any reimplementation needs both:

- Rounding `v * 10^d` is not rounding `v` at `d` decimals. 2.355 is stored as
  2.35499999999999998, but `2.355 * 100` is exactly 235.5, so the multiply invents a
  tie. Use a decimal-correct path (`toFixed`) for the ordinary case.
- A genuine tie needs the long expansion to detect. `Number("2.345") === 2.345` is
  true - that is the shortest *representation* - while the stored value sits above the
  half. Read `toFixed(d+19)` and require a 5 at the cut with only zeros after it.

**Go's `round` transform is the remaining outlier**: `math.RoundToEven(v*scale)/scale`
inherits exactly the multiply error above, so Go gives 2.36 and 2.68 where Python, Java
and C# give 2.35 and 2.67. Three of four are decimal-correct. No corpus vector catches
it yet, so add one when fixing it.

**`score-baseline.json` is stale.** It reports 26 regressions, of which 23 are
decentlab schemas that score identically at HEAD in a clean worktree - the baseline
predates an edge-case scoring fix in the unpushed commits. Regenerate it before using
it as a gate, or it will keep hiding the real regressions among false ones.

**A more correct schema can score lower**, which is the mirror of the warning above
that a high score is not proof of correctness. `ct303`/`ct305`/`ct310` dropped SILVER
98% to BRONZE 66% when they gained their vendor-verified current channels: the JS gate
fails because the generator cannot emit nested `object` members (15 points), and 27 new
fields diluted the semantic component. Only the six genuine current readings were
annotated (IPSO 3317); `_max`, `_min`, `_total` and the alarm bits were deliberately
left alone, because inventing mappings to lift a score is what PS-264 exists to
prevent. Python passes 16/16, branch coverage is 100%, and crossvalidate reports
`agrees`.

With YAML in C, the corpus runner walks `schemas/devices/` exactly like the other four
— no host-side fixture generation needed. **Build it before building features**: C is
invisible to the corpus today, which is precisely why its dead sequential-bitfield
sentinel and its unbuilt test files survived so long. For this target the acceptance
test is stronger than the expected-value blocks: **diff the serialized JSON against
the generated TS013 codec's output.** That comparison catches `15` vs `15.0` on the
first run, which no value-level check does.

Fidelity has two different targets, worth not confusing:

- Replacing **our own generated** codecs is achievable now — both come from the same
  YAML, so the key sets already agree.
- Replacing a **vendor** codec is a schema-fidelity question, tracked by
  `crossvalidate_ttn.py` (milesight 50/84, decentlab 55/58), not an interpreter one.
  Our `dl-5tm` emits `flags` and `soil_temperature_raw` where the vendor's emits
  neither — and `soil_temperature_raw` is a pure intermediate that should have been
  `_`-prefixed, so it is a schema bug that would surface as an extra JSON key.

Priority order for the gateway target, by what the device corpus actually uses:
`tlv` and `flagged` first (most device schemas open with one), then `transform`
stages, then `compute`/`ref`/`polynomial`, then `repeat`/`until`. `name_from`,
`merge`, `bitfield_string` and the semantic mappings are rarer and can wait.

### Encoding

**Every corpus vector is an encode assertion, for free.** A vector carries a payload and
the values it decodes to, so `encode(decode(payload)) == payload` needs no new fixtures.
That is now a suite in four languages, and it is the reason encoding went from barely
exercised to the best-covered part of the project:

| | Runner | Round-trips |
|---|---|---|
| Python | `tests/test_encode_round_trip.py` | 1131 / 1193 |
| Go | `go/schema/corpus_encode_test.go` | 1137 |
| Java | `bindings/java/.../CorpusEncodeRoundTripTest.java` | 1131 |
| C# | `dotnet/.../CorpusEncodeRoundTripTests.cs` | 1131 |
| C | none — `src/test_encoder.c` is in no build target | unverified |

All five implementations have an encoder; Java's and C#'s were built from nothing, ported
from `tools/schema_interpreter.py`. Read that one first when changing any of them.

**The floors are per shape, not just per total, and that is what makes them work.** Each
runner scores `tlv`, `flagged`, `match`, `byte_group`, `repeat` and plain-fixed layouts
separately, so a regression in one cannot hide behind the mass of another. This has paid
off literally: adding a `default` case to Go's encode type switch raised the total and
dropped `flagged` by 9 in the same run, which the total alone would have absorbed.

**It cannot be lossless everywhere, so the floors are ratchets, not a target of 1193.** A
`skip` field's bytes are not recoverable from output that omits them, a rounding stage
discards precision, a `lookup` or enum `default` label stands for every value the table
does not list (PS-269, PS-068), `sqrt`/`log`/`pow` do not invert, an unknown TLV channel
was never decoded, and a `name_from` key is not the field's name. Those are reported
through the result type rather than papered over with plausible bytes. Raise a floor when
you close a gap; never lower one without saying why here.

**The defect shape to watch for is a silent write of nothing.** Every encode gap found so
far was a type spelling some dispatch did not list, which wrote no bytes and returned
success — a sequence `lookup` label (67 Go vectors emitted a TLV tag followed by nothing),
a `u8[0:0]` bit range, `u24`/`s24`, a `type: number` inside a `flagged` group. Each read
as a pass until a cross-implementation diff. Go's switch now has a `default` that reports
an unhandled type; keep it, and prefer reporting a value you cannot write over skipping it.

**Three more of that shape were in Go's `encodeFields`, and all three are fixed.** They
were found from outside, by a consumer building LoRaWAN downlink on the Go encoder, which
is worth noting: the corpus could not see any of them, because a round-trip that fails on
length looks the same as any other near miss.

- **A missing field was skipped, not zero-filled.** Python, Java and C# all warn
  `Missing field: {name}` and write zero; Go alone wrote nothing, so the payload came out
  *short* and every field after the gap landed at the wrong offset. A partial map
  therefore produced a corrupt frame rather than one with a zero placeholder. Go now
  matches the other three. `encodeFieldList` — the tlv/match/repeat path — was already
  correct, which is why only fixed layouts were affected.
- **`EncodeWithPort` discarded the error from `ResolveFields`.** An undeclared fPort
  resolved to nil fields, encoding to an empty payload and returning success. Python's
  `_resolve_fields` raises and its `encode` does not catch it. Both Go entry points had
  this, because `EncodeWithPort` and `EncodeOrderedWithPort` were copies; they now share
  one `encodeToContext`. Note the limit: a schema declaring a `default` port resolves
  *any* undeclared port to it, so encoding against the wrong port still succeeds with
  plausible bytes for the wrong message. A caller needing a specific port must check
  `Ports` itself.

- **An unnamed or `_`-prefixed field contributed no bytes.** `encodeFields` returned early
  on `field.Name == ""` or a `_` prefix, so a top-level `skip` or a `_reserved` byte
  shifted every field after it — the same defect as the missing field above, one branch
  up. Both now go through `encodeField`, which writes `length` zeros for a `skip` and the
  zero value otherwise, as `encodeFieldList` and Python do. Neither is warned about:
  padding and internals are not the caller's to supply, and Python does not warn either.

  **The `number` check had to move above the name check to do this.** A computed field is
  very often the `_`-prefixed kind — `mclimate/vicki` has six — and with the old order
  they would now reach `encodeField`, whose switch has no `number` case, so a schema that
  encoded fine would start reporting "cannot encode type".

None of the three changed a single round-trip count — total stays 1135 and every shape
floor holds. The only visible movement is `plain fixed` going `length=5 → 3` and
`bytes=2 → 4`: two vectors went from a silently truncated payload to a correctly shaped
one. **That is why the per-shape table records length and bytes separately from
round-trips** — a pure correctness win that moves no total would otherwise be invisible.

**The third fix moved nothing at all, and that was the finding.** Of the five schemas with
an unnamed or `_`-prefixed field at top level, four ship no test vectors
(`makerfabs/soil-monitor`, `makerfabs/ath20`, `makerfabs/leaf-moisture-sn-3001`,
`hbi/mla20`) and the fifth, `_language-conformance/skip-type.yaml`, *names* its skip field
so the missing-field path already covered it. The corpus could observe neither the bug nor
its repair.

**`_language-conformance/encode-padding.yaml` closes that hole**, and it is the first
fixture there aimed at the encode direction. `[u8 a, skip 2, _reserved u8, u8 b]` over
`01 00 00 00 02`. The padding is **zero on purpose**, unlike `skip-type.yaml`'s `FF FF`:
zero padding is reconstructible, so the vector round-trips and
`encode(decode(payload)) == payload` fails the moment such a field stops contributing its
bytes. A fixture whose padding cannot be reconstructed can only ever test decoding, which
is why the existing skip fixture never caught this.

All four implementations decode *and* round-trip it — confirmed with `make test-java` and
`make test-dotnet`, not assumed — so every floor was raised together: decode 1191 → 1192 in
all four, re-encode totals 1129 → 1130 for Python, Java and C# and 1135 → 1136 for Go, and
the `plain fixed` shape floor 54 → 55 in all four. Exact counts were read out of each
runner rather than inferred; Python's and C#'s runners print no table, so their totals came
from over-raising the floor and reading the assertion message.

Parity was checked against the reference implementation rather than assumed. For
`[u8 a, skip 2, _reserved u8, u8 b]` Python and Go now both emit `01 00 00 00 02` with no
warnings; for a three-field layout missing its middle field both emit `11 11 00 33 33`
with `Missing field: second`; and for a `_`-prefixed `number` both emit two bytes. Three of
the four implementations already agreed before these fixes and Go was the outlier, which
is what settled "should this error?" — it should not; it should zero-fill and say so.

**Go had nowhere to put a warning, which is why it was the silent one.** The other four
return a result type carrying `warnings`; Go returned bare bytes. `EncodeContext` now has
`Warnings`, and `Schema.EncodeToResult` returns an `EncodeResult{Payload, Warnings}` —
additive, so `Encode`/`EncodeWithPort` keep their signatures and simply drop the warnings
as before. Wording matches the other three exactly (`Missing field: {name}`) so a
cross-language diff compares equal strings.

**`encodeFields` and `encodeFieldList` were two hand-maintained copies of the same
dispatch, and are now one.** That duplication is why all three defects above were in
`encodeFields` and none in `encodeFieldList`: the corpus is overwhelmingly tlv, so the
copy it exercises least is the one that rotted. `encodeFields` is the survivor and now
serves every context — top level, header, tlv case, match branch, repeat record, `$ref`d
definition, nested object.

The merge is byte-for-byte inert on the corpus: 1135 round-trips, every shape identical
including `tlv` at 905/7/37/0, decode 1191/1191. What it changed:

- **The tlv/match/repeat path gained the constructs only the top-level copy dispatched
  on.** `flagged` and `repeat` were the two missing. Bytes for a well-formed `repeat` were
  already right — it fell through to `encodeField`, whose `TypeRepeat` case walks records
  the same way — but a value that is *not* a list of records wrote nothing and returned
  success there, where `encodeRepeat` reports it. The strict path is now used everywhere.
- **A `skip` is dispatched before the name check, and on both spellings.**
  `encodeFieldList` matched only lowercase `skip`, so `type: Skip` in a tlv case wrote
  nothing. Handling it before the name check also stops a *named* skip — `skip-type.yaml`
  calls one `pad` — being reported as an omitted value.
- **Warnings now escape a tlv case.** A case is encoded into its own buffer so its length
  prefix can be measured first, and warnings raised in there were dropped on the floor.
  `EncodeContext.branch()` / `mergeWarnings` carry them out; without that the tlv path
  would have looked clean by construction. `branch()` deliberately does *not* carry
  `TLVOrder` — a nested tlv has no recovered order of its own, and passing the outer one
  down would reorder its channels to match a sequence that was never about them.

**Warning volume was measured, not assumed: 1 warning across all 1190 encodable corpus
vectors**, and it is a true positive — `_language-conformance/name-from.yaml` reports
`Missing field: reading`, because a `name_from` key is not the field's name, which is
already listed above as an unrecoverable round-trip case. The tlv path, whose newly
enabled warnings were the thing to worry about, produces none.

**A tlv case built from a nameless construct claimed nothing, and is fixed.** `encodeTLV`
collected a case's claimable names from the top level of the case only, and a `byte_group`
or `flagged` field has no name there — its names live in its group's fields, which
`encodeByteGroup` and `encodeFlagged` read out of the same flat map. So such a case found
nothing to claim, never became a candidate, and its channel encoded to **no bytes and no
error**: the silent shape again, this time in candidate selection rather than in a
dispatch.

`claimableFields` now flattens a case for both the claiming loop and `caseFidelity`, which
had the identical top-level-only scan and would otherwise have ranked such a case at zero
matches. What it deliberately does *not* descend into matters as much as what it does:

- **An object's or repeat's `fields` are not descended into.** Their values live in a
  nested map under the field's own name, not in the case's map, so claiming their members
  would claim names that are not there. The field's own name is claimed instead, which is
  what the 17 nested objects in the corpus rely on — `TestNestedObjectInATLVCaseIsClaimedByItsOwnName`
  pins that boundary from the other side.
- **A nested `match` or `tlv` is not descended into**, because which branch supplies a name
  depends on the data, so every branch's names would over-claim. No corpus schema nests
  either inside a case.

`hbi/mla20`'s case 32 is two byte_groups of exactly this shape and now encodes: tag `20`
then the packed byte `51` for `device_status: 5` / `charger_status: 1`. It ships no test
vectors, which is why the corpus never saw this and does not see the repair either. The
same silence covered the `flagged` variant, which had been failing loudly before the
dispatch merge (`cannot encode type ""`) and briefly degraded to silent-empty in between.

**Trying to write a conformance fixture for it found a matching *decode* defect, and that
one was Python's.** Under a one-byte tag and length, `20 01 51` decoded in Go to
`{charger_status: 1, device_status: 5}` and in Python to `{unknown: 81}` — the reference
implementation read the group's shared byte as a `u8` called `unknown` and never descended
into the bit ranges, because `_decode_tlv`'s case loop dispatched only `bitfield_string`
before falling through to the generic field path. So `hbi/mla20`'s case `0x20` had never
worked in Python either, in *either* direction, and no vector had ever asked it to.

Both halves are now fixed in Python and the fixture is committed as
`_language-conformance/tlv-nameless-case.yaml`:

- `_decode_tlv` dispatches `byte_group`, decoding it into a scratch `DecodeResult` whose
  data folds into `tag_result` — not straight into the payload-level output, which would
  bypass the merge/channels handling. It takes an optional `outer` result so the group's
  errors have somewhere to go; the parameter is **not** called `result`, because the method
  already binds a local `result` dict for its channel output and the collision made the
  first attempt fail with `'dict' object has no attribute 'errors'`.
- `_claimable_fields` mirrors Go's `claimableFields` and feeds both `_encode_tlv`'s
  claiming and `_case_fidelity`, which had the same top-level-only scan.

**All four implementations now have it, encode and decode.** `claimableFields` /
`ClaimableFields` are the same helper in `Encoder.java` and `SchemaEncoder.cs`, feeding
both the claiming loop and `caseFidelity`/`CaseFidelity`, which had the same
top-level-only scan in every language. Neither port needed anything else: unlike Go, both
already funnel every field-list context through one dispatch (`encodeOne`, `EncodeOne`), so
a byte_group inside a case encoded correctly the moment the case was selected.

Every floor moved together: decode 1192 → 1193 in all four; re-encode 1130 → 1131 for
Python, Java and C# and 1136 → 1137 for Go; tlv shape 899 → 900 for Python, Java and C# and
905 → 906 for Go. Java prints its per-shape table, Go does too; Python's and C#'s counts
came from over-raising the floor and reading the assertion message.

`mla20` still ships no vectors of its own. It can have them now that the construct works in
all five implementations, and that is the better long-term guard than a fixture standing in
for it — but its payloads have to come from the vendor rather than from our own decoder
(PS-264), so it is not a mechanical job.

Note that `tools/schema_interpreter.py` is a public API surface with external consumers, so
its decode output changing is not a local matter — but `{unknown: 81}` cannot have been
depended on, since the field names the schema declares were absent entirely.

Go's re-encode figure is the highest, and not because its encoder is better:
`DecodeOrdered`/`EncodeOrdered` carry the TLV channel sequence a Go map cannot hold, so
where two cases define the same field name under different tags — em500-smt has `humidity`
under both `[4, 104]` and `[4, 202]` with identical arithmetic — the tag that actually
wrote those bytes goes back. Python, Java and C# recover order from their output keys,
which cannot tell those two cases apart, and pick the first. Java and C# need no such API
because a `LinkedHashMap` and a .NET `Dictionary` already preserve insertion order.

Cross-language method of record: **diff the failing sets, not the totals.** Two
implementations at the same count can be failing different vectors. Every Go fix above came
from listing what Go failed that Python passed; the set is now empty in that direction.

**Four of the nine C test files are in no build target**: `test_comprehensive.c`,
`test_interpreter.c`, `test_binary_schema.c`, `test_encoder.c`. Only `test_codec.c`
and the `selftest_*` files are wired in — so C's encoder (`schema_encode`, `encode_field`,
reverse modifiers) is verified by nothing that runs, and PS-061..PS-064 and the reverse of
canonical modifier order are normative. Six compiled executables are also tracked in
git (`src/test_binary_schema`, `test_comprehensive`, `test_interpreter`,
`test_encoder`, and the two `_cpp` ones).

**`idiv` and `mod` are floored everywhere — CR-2026-007 settled it, and it is
implemented.** The convention is floored division and a remainder taking the divisor's
sign, so `a == idiv(a,b)*b + mod(a,b)` holds in every implementation, and a zero divisor
omits the field rather than emitting `NaN` (PS-278).

Worth keeping because it is the clearest example of four implementations disagreeing four
ways on one operator. Measured for `a = -7, b = 3` before the CR: Python gave `idiv -3,
mod 2` (floored both), Go and C# gave `-2, -1` (truncated both), and Java gave `-3, -1` —
`Math.floorDiv` for `idiv` beside a truncated `%`, so its own two operators used different
conventions and the identity failed in Java alone. A zero divisor diverged a fourth way:
Python and Java emitted `NaN`, **which is not valid JSON**, so one zero divisor made the
whole decode unparseable; Go errored and C# threw.

`_language-conformance/compute-negative-idiv-mod.yaml` is the acceptance test and now
passes everywhere, which is why every decode floor is the full 1193.

Note that no schema in the repository uses `idiv` or `mod` at all; those eight
vectors are the only exercise of either operator in the corpus.

**One assertion per vector when probing a divergence.** A runner stops at a vector's
first mismatch, so an earlier draft of that fixture — four computed fields in one
vector — hid three of the four disagreements behind whichever field was compared
first. Go looked like it had an `idiv` bug and a correct `mod`.

**The conformance comparison is exact for integers.** PS-039 requires an integer
expectation to match exactly; PS-040's 0.001 is for floats and is absolute. All four
runners used a relative `max(0.001, |want| * 0.001)` on everything, roughly 20 days
of slack on a GPS timestamp, which is how a ts003 vector wrong by 2048 seconds passed
in three languages while the composed-corpus tool rejected it. Integerness is decided
from **how the vector wrote the value**, not from the decoded type — every
interpreter widens integers on the way out, so `5` against `5.0` still matches.

**`name_from` is not the answer to a per-channel key.** It exists for a key that
varies with a decoded *value*. A milesight channel whose key is `current_chn1` or
`region_3` looks like a candidate and is not: the index is fixed per channel id — the
vendor builds it from `current_chns.indexOf(channel_id)` — so a per-(channel, type)
TLV case names its fields outright. Check the vendor decoder before reaching for the
construct; an earlier note here claimed 27 channels were blocked on it and none of
the ones examined were.

Cross-validate a vendor family with:

```
.venv/bin/python tools/crossvalidate_ttn.py \
  --devices-repo ~/Workspace/lora/tools/lorawan-devices \
  --vendor milesight-iot --schema-dir schemas/devices/milesight [--verbose]
```

Milesight stands at 50 of 84 agreeing. `vs321` and `vs373` are the next region-mask
pair; both decode nothing for their declared example, so more than the regions is
missing. Note that a bitfield range reads its base value **big-endian regardless of
the schema's `endian`**, so a little-endian 16-bit mask must be taken as two `u8`
fields rather than one `u16`.

**`src/test_comprehensive.c` is in no build target.** No Makefile rule references
it, so `make test` and `make selftest` never compile it and its 160 checks run
nowhere. Build it by hand:

```
gcc -I include -o /tmp/tc src/test_comprehensive.c -lm && /tmp/tc
```

Two checks fail there and did before this session: both expect an unmatched enum to
report `unknown(N)`, which PS-269 replaced with omitting the field. Fix the
assertions or wire the file into the build — but know the failures are stale
expectations, not regressions. Being unbuilt is also how C's dead sequential
sentinel went unnoticed.

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

  Two constructs are exceptions, each described and closed with
  `additionalProperties: false` because its vocabulary is fixed at the keys the five
  implementations read — a misspelt `tag_sze` is a mistake, not an extension nobody has
  described yet:

  - `definitions.tlv` (CR-2026-016), seven keys. The `unknown` enum is stated here and
    in `tools/validate_schema.py`, and `test_cr_2026_016_tlv_meta_schema.py` fails if
    they drift apart.
  - `definitions.flagged` and `definitions.flagged_group` (CR-2026-017), four keys.
    The validator already enforced all four, so there was no behaviour to change. It
    also refuses two groups claiming the same bit, which JSON Schema cannot express —
    `test_cr_2026_017_flagged_meta_schema.py` records that division of labour.
  - `definitions.match` (CR-2026-018), six keys, describing the Option B block under a
    `match:` key. The older `type: match` with `on:` spelling keeps its keys on
    `definitions.field`.
  - `byte_group` and `repeat` (CR-2026-019), seven field-level keys — `byte_group`,
    `size`, `count`, `byte_length`, `until`, `max`, `min`. `byte_group` has two
    spellings, an object carrying its own `size` and an array taking `size` from the
    field beside it; only the object form has corpus coverage, and a test says so
    rather than forbidding the other.

  Every construct is now described. **Closing `definitions.field` itself is a different
  and much larger change** — it declares no required keys and types `type` as a bare
  string, so `s17` and a nameless field are still accepted — and nothing depends on it.
  `tools/validate_schema.py` remains the place for real structural checking.
- **`match` is the least uniformly implemented construct.** Only `field` and `cases`
  are honoured everywhere. `length` and `name` are honoured by the Python, Java and C#
  interpreters and **ignored by the Go interpreter and the TS013 generator**, so a
  match reading its own discriminator from the payload decodes differently on those
  two. `var` and `default` are honoured by the **Python interpreter alone** — on the
  other four, a value matching no case is silently skipped whatever `default` says.

  The five paths agree today only because nothing in the corpus uses the uneven keys,
  bar one `default: skip` whose vectors never reach it.
  `test_cr_2026_018_match_meta_schema.py` has a tripwire that fails if a schema starts
  using them, and the gaps are recorded in each key's description in the meta-schema.
  Closing them is behaviour work in four implementations and wants its own CR, with
  `_language-conformance` vectors for the inline-discriminator and `default` paths.
- **`tools/generate_jsonschema.py` does not produce
  `schemas/payload-schema.json` any more.** Nothing in the Makefile or CI runs it, so
  the drift went unnoticed while CR after CR edited the file directly. Regenerating
  would have deleted eleven keys — `source` (PS-263), `expected_warnings`
  (CR-2026-014), `valid_range`, `resolution`, `unece`, the whole `tlv` description,
  and five `test_vectors` keys — silently, because JSON Schema treats an absent
  property as merely undescribed. CR-2026-016 made the generator refuse to overwrite a
  file describing more than it does, and name what would be lost; `--force` still
  overwrites. **Edit the file by hand.** Teaching the generator the current file was
  the other option and was not taken: two definitions of the same thing to keep in
  step is the arrangement that produced the drift.
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
  - *`x[i] + x[j] * 65536` emitted as a `u32`.* This is not an endianness
    question. The Decentlab payload is a stream of big-endian 16-bit words on
    2-byte boundaries, and there is no 32-bit field on the wire to have a byte
    order: a wide value is arithmetic over two independent words, with the low
    word first, which contradicts the big-endian word stream it sits on. (The
    bytes do happen to form a recognisable CDAB order, so a mixed-endian 32-bit
    read would reproduce them; that is a coincidence of this case, not the format's
    grammar, and modelling it that way invents a 32-bit field the protocol does not
    have. Why the convention is inverted is not documented anywhere we can see.)
    No integer type can read it — express
    it with `compute`, which states plainly that two words were read and combined.
    Emitting `u32` gave `dl-rhc` a sensor id of 2851930420 instead of 20228605 for
    words `a9fd 0134`. `dl-isf` carried the same defect while still passing
    cross-validation, because the vendor's own test payload does not exercise that
    field — agreement covers only the payloads you have.
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
