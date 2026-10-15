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
# ...decode-only and without commentary (about a quarter of the size)
python3 tools/generate_ts013_codec.py <schema.yaml> -o <output-dir> --slim

# First-pass conversion from a vendor codec - a starting point, not a finished
# schema. See "Converting a vendor codec" below before trusting the output.
python3 tools/convert_decentlab.py <vendor-codec.js>
python3 tools/convert_milesight.py <vendor-codec.js>

# Full verification: use this before pushing, not the targets one by one
make verify        # test-languages once, then every corpus floor read from its logs

# Tests
make test          # C self-tests + pytest
make pytest        # Python only, on every core (pytest-xdist, -n auto)
make pytest PYTEST_ARGS=          # ...serially; PYTEST_ARGS takes any pytest options
make fuzz-quick    # 10-second fuzz, cheap enough for every commit

# The Go, Java and C# interpreters, in containers - no local toolchain needed.
# Each keeps its output in build/lang-logs/<lang>.log for check-floors --from-logs.
make test-go
make test-java
make test-dotnet
make test-languages   # every implementation: Python, C, Go, Java, C#
make check-floors     # runs Go, Java and C# again to read their counts
make check-floors-from-logs   # reads them from the logs above instead

# Regenerate the repository index after adding docs, tools or schemas
python3 tools/generate_docs_index.py
```

**Verify with `make verify`, once.** It runs `make test-languages` (pytest on every
core, the C self-tests, Go, Java and C#) and then `check-floors --from-logs`, which reads
the Go, Java and C# corpus counts from the logs those suites just wrote instead of
running each a second time. A log missing, unfinished, or older than any edit to a file
git sees (tracked or untracked-not-ignored) makes check-floors run that one suite itself,
and its report says where each implementation's numbers came from. So do not edit while
it runs, and do not follow it with `make pytest` and `make check-floors` - that repeats
work it has already done. Measured 2026-10-11 on a 32-core machine with warm container
caches: the suite took 17.5 minutes serially and under 2 on every core, and a whole pass
(`make verify`) took 3 minutes where `make pytest`, `make test-languages` and
`make check-floors` in turn had taken about 36 - pytest twice, Go, Java and C# twice.

Two rules keep the suite safe to parallelise, and a new test must follow both:

- **Never write into the working tree.** Use `tmp_path`. Two `test_floor_checker` tests
  used to rewrite `tests/test_encode_round_trip.py` and restore it in a `finally`; under
  xdist one could take the other's rewrite as its original and leave a wrong floor on
  disk. `check-floors --floor-root DIR` exists so those tests point at a copy.
- **A corpus-wide computation shared by several tests is computed once.** A module
  fixture is built once per *worker*, so eight workers rebuilt `test_corpus_invariants`'
  70-second decode eight times. Mark such a module or class
  `pytest.mark.xdist_group(...)`; `make pytest` passes `--dist loadgroup`, which keeps a
  group on one worker. Cache a repeated tool run with `functools.lru_cache` and group its
  callers, as `test_cr_2026_025_encode_parity_method.py` does.

Note: `make validate` only covers `examples/*.yaml`. Device schemas under
`schemas/devices/` are not part of that target — validate and score those
explicitly.

## Schema language cheat sheet

```
TYPES:        u8 u16 u24 u32 u64 | s8 s16 s24 s32 s64 | f16 bf16 f32 f64 | bool
              ascii hex bytes base64 | number integer string boolean | skip enum | udec sdec
              aliases uint8..uint64 int8..int64 i8..i64 - nothing else, case-sensitive
BYTES FORMAT: hex hex:upper base64 array | separator (hex formats only)
STRUCTURES:   type: object | repeat | byte_group | tlv
NAMING:       name | name_from ("region_${region_id}_dwell")
TAG KEYS:     "[1, 200]" exact | "[1, !0]" excluding | "[2, *]" any
MODIFIERS:    add mult div | lookup (sequence or sparse mapping; default "x${value}")
              | polynomial
              | compute | guard | transform
CONDITIONALS: match (value; "[1, 2]" list keys; field XOR length) | flagged | tlv
TRANSFORMS:   sqrt abs pow log10 log floor ceiling clamp | {op: round, ties: even|away}
COMPUTE OPS:  add sub mul div mod idiv pow
GUARD OPS:    gt gte lt lte eq ne
ENCODINGS:    sign_magnitude bcd gray (Python only)
REFERENCES:   $field_name | var: name | $ref: '#/definitions/name'
LITERALS:     {type: string, value: "x"} | {type: number, value: 3}
              | {type: boolean, value: true}  (read no bytes; boolean needs value)
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
python3 tools/crossvalidate_ttn.py --devices-repo ~/Workspace/lora/tools/lorawan-devices \
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

The 2768 payload vectors in `schemas/devices/` (measured 2026-10-11 with
`tools/check-floors.py`, after match-fractional-discriminator, the CR-2026-118 and CR-2026-124 fixtures, CR-2026-117's
guard-absent-operand, the `match-u32-*` and names-mangling-alike fixtures, CR-2026-127's
guard-boolean-operand, the CR-2026-121 and CR-2026-123 fixtures, CR-2026-110's,
CR-2026-125's and CR-2026-128's tlv-repeat-* fixtures, the mutation-gate vector in
round-large-value, CR-2026-114's ascii-latin1, CR-2026-131's byte-group-width,
CR-2026-109's lookup-reference-binding and CR-2026-132's
tag-component-reference-binding) are the shared cross-language test set. Every
implementation has a runner that reads the same YAML and the same vectors:

| Implementation | Runner | Decode floor | Re-encode floor |
|---|---|---|---|
| Python | `tests/test_corpus_conformance.py` | every vector | 2259 |
| Go | `go/schema/corpus_conformance_test.go` | 2870 | 2277 (plain API 2249) |
| C# | `dotnet/PayloadSchema.Tests/CorpusConformanceTests.cs` | 2870 | 2260 |
| Java | `bindings/java/.../CorpusConformanceTest.java` | 2870 | 2259 |
| C | `tools/c-corpus-harness.py` (builds each expressible schema through the struct API) | 575 of 575 attempted | n/a |

These figures move with every schema added. `make check-floors` prints each floor beside
its own implementation's actual; trust it over this table.

Every runner compares its pass count against a committed floor rather than requiring
the whole corpus, so any gap stays visible and a regression fails the build. All four
YAML implementations now decode the entire corpus, so **every decode floor is the full
corpus of payload vectors and any failure is a regression, not a known gap.** If you add a construct that one
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
- **A `bytes` field decodes to a lowercase hex string in all five, which is PS-080's
  default.** This note used to say Python returned a bytes object and Go a hex string,
  and called the disagreement a CR. Measured 2026-09-24 for `bytes` of length 2 over
  `0a0b`: Python, Go, Java, C# and the generated TS013 codec all give `"0a0b"`. Vectors
  write a hex string, and `values_match` still accepts hex or a list of octets against
  a byte sequence. `bytes` `format:` (`hex`, `hex:upper`, `base64`, `array`) and `separator:` (hex formats only, PS-391)
  are read by all five YAML paths: Python (`format_bytes`, CR-2026-058), the TS013 generator and its
  encoder runtime, Go, Java and C#. Measured 2026-10-14 on `bytes-format.yaml` and
  `bytes-format-forms.yaml` (`length: $n` and `remaining`, an empty value, a multi-character joiner,
  padded and unpadded base64): every path decodes them identically and the ten vectors of the second
  round-trip in Python. Encode accepts a formatted string back (PS-081), with or without the declared
  separator, as a bytes value or a list of octets. This note used to say Python and the generator
  ignored both keys; that stopped being true with CR-2026-058. C has no `format`/`separator`, and
  `tools/schema_binary.py` refuses both by name (PS-446). Encode is lenient where the spec prototype is:
  a short or long value is padded or cut to `length`, and a malformed base64 string or an octet above
  255 is not rejected (open, not a PS requirement).
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

Every decode floor is the full corpus. Do not re-add a withdrawn form to "support" an
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
kept `decentlab/dl-blg` on an unconverted vendor formula. The domain rules are part
of the contract, not an implementation detail: `sqrt` clamps its input at 0 (PS-116),
and the log of a non-positive number leaves the field **absent** (PS-117, CR-2026-057).
The logs used to clamp at 1e-10, reporting log10(0) as -10; NaN is never reported
(PS-282). `_language-conformance/transform-maths.yaml` and `log-of-non-positive.yaml`
hold all five to it.

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
  `skip`, the bracket bitfield range, `lookup`, `var`/`$var`, `consume`,
  per-field `endian`, and the three bare modifiers.
- **Lacks**: `object` (a `field_type_t` value, even parsed from its name, with no
  decode path; `byte_group` had none either until the C-interpreter wave), `compute`, `polynomial`, `guard`, `ref`, `formula`, the whole
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
unsafe.** `bindings/c/schema_ffi.c` has `schema_ffi_create_yaml()`, but its body is
`(void)yaml_str; return NULL;` — a declared stub. The FFI did not compile for a time
(its `schema_decode` collided with the interpreter header's, and it called a
`schema_decode_payload` that never existed); every export now carries a `schema_ffi_`
prefix (`schema_ffi_decode`, `schema_ffi_result_get_field_int`, ...) and its macros
`SCHEMA_FFI_*`, the Python/Go/Node consumers follow, and `make test-c` builds it with
`-Wall -Wextra -Werror` and decodes a payload through it (`src/test_ffi.c`).
`schema_ffi_result_to_json()` is real, and it
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

**That lesson recurred, worse, and CR-2026-021 found it.** `matches()` in the same tool
tested `if not values_match(...)`. `values_match` returns `(ok, detail)`, and a
two-element tuple is always truthy, so the condition could never hold: from the day the
second return value was added, the tool compared **key presence only**. Every value the
two paths disagreed on was reported as a pass, and the reassuring
`0 vectors where the two paths disagree` line meant only that both paths produced the same
*keys*. It is how the repeat `max` gap survived — the generated codec put four records
under a key where every interpreter put two, and both paths passed.

`test_cr_2026_021_repeat_limits.py` guards it two ways: it asserts the source no longer
contains `if not values_match(`, and it calls `matches()` on a known-unequal pair. **A
helper that returns a pair is a trap for `if not helper(...)`** — `warnings_match` has the
same shape and is unpacked correctly at all four of its call sites. Check any new one.

**A bare run of bit ranges is packed on encode by all four encoders.** CR-2026-023 found
`_encode_field` writing each bitfield as a whole byte holding its unshifted value,
ignoring the bit range and `consume: 0` alike — a LoRaWAN MHDR's three ranges came back as
three bytes, `40` encoding as `020000`. `byte_group` was given packing when its own
encoding was fixed; a bare run, the same thing without the wrapper, never was.
CR-2026-024 brought Java, C# and Go onto it. Do not add a fifth code path for this: each
encoder has one run packer, wired through its field list, its construct bodies and its
`flagged` path, and `test_cr_2026_024_encode_bitfield_parity.py` reads all three sources
so a removal shows up there rather than as a puzzling floor break.

**A `match` discriminator above 0x7FFFFFFF is compared as a 64-bit integer in Java and
C#.** Both narrowed it to a 32-bit int, and the private registry found the two ways
that goes wrong in one vendor family's "not available" sentinel - peek a word with
`u32[0:31]`, take case `2147483648` if it is 0x80000000, else read it as a value.
Java holds case keys as text (`expandNode` stringifies every mapping key while splicing
references) and parsed them with `Integer.parseInt`, which rejects 2147483648, so the
sentinel never matched. C# holds such a key as a double, and `(int)` of an out-of-range
double is `int.MinValue` on x64 - so the key, 0xFFFFFFFF and 0x80000001 all compared
equal and two ordinary values decoded as the sentinel. Both also cast an inline
four-byte discriminator to int and reported 0x80000001 under `name` as -2147483647.
A discriminator that is no integer at all (a u64 peek above 2^63-1) now matches no
case in either, rather than the 0 a default conversion would give.
`match-u32-peek-discriminator.yaml` and `match-u32-inline-discriminator.yaml` hold all
five to it. Still open: a fractional discriminator truncates to a key in Java and C#,
where Python compares it exactly.

**Read `make check-floors`, not any table here.** The per-implementation counts in this
file have gone stale three times; the corpus-table figures above were measured
2026-09-24.

**A field's own `endian:` is honoured by all five implementations, and was ignored by the
reference alone.** Go, Java, C# and C all parsed and applied it; `tools/schema_interpreter.py`
consulted only the schema-level `self.endian` at every read site, so a field declaring
`endian: little` under a big-endian schema decoded big-endian and reported success. Four-to-one,
silent, on the surface `td-tools` imports. `_decode_field`/`_encode_field` now wrap the
inner dispatch with the override; `_language-conformance/field-endian.yaml` holds all five
to it and `tests/test_field_endian.py` covers what a fixture cannot.

Three things that fell out of it, each worth knowing before touching byte order again:

- **The override does not reach the members of a nested construct.** Go and Java derive
  each field's effective order from the *context*, so a parent's declaration does not
  cascade. Python matches that deliberately (`_ENDIAN_OPAQUE_TYPES`); a cascade is
  defensible language design and would have made the reference the only one doing it.
- **`u32le16`/`s32le16` remain untouched by it** (PS-272). The type fixes both orders, and
  honouring `endian` there would make it a second spelling of little-endian `u32`.
- **The TS013 generator had the same gap and the fixture found it immediately.**
  `field_endian_override()` derived byte order from a `le_`/`be_` *type prefix* only, so a
  schema using the key generated a codec that disagreed with the interpreter. It now
  prefers the field's key. `tools/vector-verdicts.py` is back to 1254/1254 on both paths.
- **`bitfield_string` had the same gap, found by the registry's XE-10 batch (2026-10-13).**
  Its packed value was read and written in the document's order by the reference (the
  field-list path dispatches it without the override wrapper) and the generated codec, and
  written in it by the Java and C# encoders; Go honoured the key both ways, and C has no
  `bitfield_string`. All five now take the field's `endian` (PS-050a, PS-090).
  `bitfield-string-field-endian.yaml` and `-reverse.yaml` pin both directions; a round
  trip alone could not see the reference's defect, since it read and wrote the same
  wrong order. Decode floors 2751 -> 2755, re-encode and `plain fixed` +4 everywhere.

**A `_language-conformance` fixture's COMMENTS change its shape bucket in three languages.**
The Go, Java and C# encode harnesses classify a schema by scanning its raw YAML for a
construct name, and the iterating one is matched without a trailing colon - so a prose
mention of it in a comment filed `field-endian.yaml`, which has no construct at all, under
that shape in three languages while Python filed it under `plain fixed`. The per-shape
floors only mean anything if a schema's bucket reflects its fields. Keep construct names
out of fixture prose, or fix the classifiers.

**An unknown `type:` is an error in all four YAML implementations now; Java read it as one
byte.** `FieldType.fromString` returned `U8` for anything it did not list, so a typo, or a
type the binding had not implemented, read a single byte, returned success, and shifted
every following field. Measured before the fix: Go `unknown field type: totally_bogus`, C#
`Unknown field type`, Python `Unknown type: totally_bogus`, Java `{v=1, w=2}` and no error.
It is why Java appeared to support the `le_`/`be_` prefixes - it had simply never rejected
anything. Two constraints on the fix, both pinned by `UnknownFieldTypeTest`:

- **The bracket bit range must be matched before `fromString`**, which cannot parse it.
  Getting that order wrong turns every bitfield in the corpus into a parse failure.
- **An absent or empty `type:` must stay `U8`.** A field carrying a construct instead
  (`tlv:`, `match:`, `flagged:`, `byte_group:`, `$ref`) declares no type at all.

No corpus vector could catch either defect: the corpus contains only spellings Java already
knew, and `type: array` - the one unknown spelling in the tree - lives in `schemas/library/`,
which the runners never walk.

**`tools/c-corpus-harness.py` passed only the schema-level endian**, so `field-endian.yaml`
failed there and read as a C gap when the C interpreter decodes it correctly
(`field_def_t.endian`). Fixed in the harness; C was 492/492 attempted then, and is 487/487
since `dl-zn2`'s corrected decode needs `compute`, which the C interpreter lacks. This is the third time
a harness limit has been written down as an interpreter limit - grep the header first.

**`make check-floors` reads the floors so you do not have to.** There are 32 across four
languages in four syntaxes, and it prints each beside the actual *its own implementation*
reaches. Use it before claiming a floor is loose or tight. The rule below was broken a
second time without it - `tlv` reported loose at "900 against an actual of 910", which was
Python's floor next to Go's actual, both exactly at their own. `check-floors-python` is
instant; the full run drives four Docker toolchains, and `--from-logs` (what `make verify`
uses) reads their counts from the last `make test-go`/`test-java`/`test-dotnet` instead.

**Never compare implementations by their per-shape round-trip counts.** Those counts are
ratchets for one harness over time. Across implementations they are not comparable: each
harness buckets schemas its own way — `tests/test_encode_round_trip.py` serialises to JSON
and looks for a `"tlv"` key, the Go, Java and C# tests scan raw YAML for `tlv:` (and for
`repeat` with no colon) — and their denominators differ by a vector.

CR-2026-024 subtracted two of those numbers anyway and got a false result. It reported
that Go round-tripped ten `tlv` vectors the Python encoder did not, and concluded the
reference was the weaker of the two. CR-2026-025 compared the actual vector sets:
**every vector the Python encoder fails, Go fails too, and Go fails 28 more.** The
reference is ahead, not behind. Use `tools/encode-round-trip.py --list` and diff the
identifiers.

The measured gap, for whoever closes it: 14 of Go's 28 are `word_ordered_sensor_id` in the
`dl-*` schemas — the `u32le16` word-ordered type (PS-271/PS-272) — and one is
`match-default-fields.yaml`, which Go decodes and cannot re-encode.

**That `flagged` gap was `u32le16`, and CR-2026-026 closed it in Go.** The note here used
to say `flagged` was 14 behind Python in all three and that bit ranges were not the cause.
The first half was right and unhelpful; the cause was that **Go's encoder had no
`u32le16`/`s32le16` case at all**, and those members live in flagged groups. Go's `flagged`
now reads 135, the same as the reference's.

Two things worth keeping from it:

- **Go truncated its integer conversions where the reference rounds** — found only because
  the type fix let `dl-isf` get far enough to be wrong by one. It uses
  `math.RoundToEven` now. C# already had `MidpointRounding.ToEven` and Java `Math.rint`, so
  this was Go's alone; that was checked rather than assumed, because assuming a shared
  defect is how CR-2026-024 went wrong.
- **An unlisted type in Go's encode switch is a reported failure, not a silent zero-byte
  write.** That is why this was findable. Keep it that way.

`match-default-fields.yaml` was one of them, and CR-2026-027 fixed it in Go, Java and C#
together: **the `default:` key beside a match's `cases` was never read on encode**, so a
schema declaring a fallback wrote the discriminator and nothing else. Java and C# honoured
a `default` *case* and not the sibling key, and both ran the claimable-name heuristic ahead
of it — backwards where the discriminator is known and matched nothing, because the schema
already said what that means. The heuristic stays first only when the discriminator is
absent, which is the case it exists for.

**All four encoders handle `u32le16`/`s32le16` now** — CR-2026-026 in Go, CR-2026-028 in
Java and C#. They had failed differently and the Java form was the worse one: C# had no
case and threw `Cannot encode type`, while Java's `FieldType.isInteger()` *includes* both
types, so they fell through to the plain integer path and were written as an ordinary
four-byte value in the schema's byte order — right length, wrong order, no error, and bytes
the decoder would not read back. **A guard for these two must precede the `isInteger()`
path in Java**, and a test asserts that ordering.

One silent failure and one loud one from a single missing case is worth remembering: their
`flagged` buckets both read 121, one with 14 `bytes differ` and one with 14 `error`.

**`Encode` and `EncodeOrdered` are different contracts, and a comparison must say which one
it measures.** `EncodeOrdered` carries the TLV channel sequence a Go map cannot hold;
`Encode` assumes ascending tag order. `TestCorpusEncodeRoundTrip` uses the ordered pair,
`TestCorpusEncodePlainRoundTrip` the plain one, and they have separate floors.

The "Go is N behind the reference" figures in CR-2026-024, -026, -027 and -028 were measured
on the plain pair, which is not the API those CRs' tests exercise. Measured per path:

    reference fails 77
    Go ORDERED fails 68  — the reference passes 0 of them
    Go PLAIN   fails 76  — the reference passes 2 of them

**On the API its corpus test measures, Go has no gap against the reference** — it fails a
strict subset. The two on the plain path are `ws515` and `wt101`, whose devices lay channels
out non-ascending, which is the documented limitation rather than a defect. The fixes in
those CRs were real and moved the ordered floors; it was the comparison figures that were on
the wrong path.

CR-2026-029 also fixed a real defect the plain path was hiding: `encodeTLV`'s claiming pass
ran in tag order, and its spend rule skips a case only when every name it claims is already
taken — so a case claiming one name spent it before a case claiming that name *and another*
was considered, and both were emitted. `em400-mud`'s `8367000000` came back as
`036700008367000000`. The claiming pass is ordered by specificity now; emission is still by
ascending tag.

**Do not pin a running floor total in a CR-specific test.** CR-2026-024, CR-2026-026 and
CR-2026-027 each did, and each was broken by the next CR that raised the same floor —
CR-2026-027 while its own commit was fixing the other two. Pin the bucket the CR moved, and
read the total as a lower bound.

**Anchor a source-reading test on something that occurs once, and say where you searched
from.** Four of these tests have lied this session by matching the wrong place: a method
name matching its call site (CR-2026-024), `func encodeField` matching `encodeFields`
(CR-2026-026), `index("ncode")` matching an unrelated word, and a `case` line spelled
identically in decode and encode (both CR-2026-028). Every one failed on correct code, and
one of them was hiding a real omission behind the false failure.

**The encode round-trip residue is classified, not counted.** `tools/encode-round-trip.py`
gives a reason for every vector that does not re-encode to its payload, and
`test_cr_2026_023_encode_round_trip.py` asserts none is `unexplained`. That is the property
worth keeping: the count alone was 77 and read as an encoder full of holes, when all but
one entry is information the decode does not carry — undescribed bits, an internal `_`
field, an unknown tag's bytes, a `default` label (PS-269), an uninvertible `sqrt`. The one
recoverable entry is `name_from`, whose output key is built at run time while the encoders
look the value up by the declared name.

Do not re-add a hand-maintained residue table. `tests/test_encode_round_trip.py` carried
one; it said "1129 of 1191" with a four-point analysis, and every row was wrong by the time
the corpus reached 1237.

**The residue holds nothing recoverable now.** CR-2026-030 gave all four encoders
encode-side `name_from` resolution, which was the one entry the tool classified as a gap
rather than a loss: the value is reported under a templated key (`channel_3_reading`) and
every encoder looked it up under the declared name (`reading`), found nothing, and wrote a
zero. An unresolvable template is an error now, and the "missing field" warning names the
resolved key rather than one the schema never reports. Note there are two resolvers per
implementation, deliberately — decode resolves against the values its fields *reported*
(since CR-2026-109 a table kept beside the variables, which hold PS-523's binding), encode
against the data it was handed. CR-2026-031 added `name-from-var.yaml`, whose `var: idx` sits on a field
called `channel_id`, so the PS-267 fallback through the declaring field is exercised rather
than merely written; the corpus's other `name_from` names a field and a variable that agree,
which a resolver ignoring `var:` would have passed.

**A CR-specific test must bound every floor it names, never pin one.** Both the total and
the per-shape bucket are running values that later CRs raise. CR-2026-024, -026 and -027
pinned a total and were each broken by the next CR; CR-2026-028 fixed that by bounding the
total and pinning the *bucket*, which CR-2026-030 then broke. Bound both.

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

**Go's `round` transform was the outlier and is fixed**: `math.RoundToEven(v*scale)/scale`
inherited the multiply error above (2.36 where the others gave 2.35). It now uses
`roundHalfEvenDecimal` (`go/schema/schema.go`), and
`_language-conformance/round-half-to-even.yaml` holds all four to it.

**`score-baseline.json` is a live gate**: `make score-check` reports no regressions as of
2026-09-24. When a score drops for a good reason - a wrong annotation removed - refresh
that schema's entry alone and say why in the commit (PR #47 did this for vs370);
regenerating the whole file would hide any other drop.

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
  `crossvalidate_ttn.py` (milesight 64/84, decentlab 58/58), not an interpreter one.
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
| Python | `tests/test_encode_round_trip.py` | 2259 |
| Go | `go/schema/corpus_encode_test.go` | 2277 (plain 2249) |
| Java | `bindings/java/.../CorpusEncodeRoundTripTest.java` | 2259 |
| C# | `dotnet/.../CorpusEncodeRoundTripTests.cs` | 2260 |
| C | `src/test_encoder.c`, built by `make test-c` (unit tests, not a corpus round trip) | n/a |

- **A name may be an object under one tag and a scalar under another, and a tlv encoder
must not offer one's value to the other.** sensative/strips reports `temperature` as
`{value: ...}` from tag 2 and as a plain `s16` from tag 80. `fits` accepted any value for
a scalar field, because `_case_fidelity` checks only numbers, so a mapping reached
`round_to_integer` and the encode failed `Error encoding tlv: float() argument must be a
string or a real number, not 'dict'` (and a number offered to an object case wrote
`Missing required field`). A mapping now fits only an `object` or `repeat` field and an
object takes only a mapping (unless merged), in Python, the TS013 runtime, Go, Java and
C#; Go and the runtime already refused it by accident or crashed differently (runtime:
`cannot encode {"value":5} as an integer`; Java and C# round-tripped one of the two
fixture vectors only). C has no object. Fixture `tlv-name-object-and-scalar.yaml`: decode
floors +2 and re-encode +2 everywhere (Python, Java 2101 -> 2103, C# 2102 -> 2104, Go
2119 -> 2121, plain 2092 -> 2094; `tlv` +2). The registry's strips `vendor_codec_1_0002016402ff3803`
no longer crashes (it re-encodes to a different, valid layout that decodes back to the
same values); `..._0006160405fc` is a separate gap.

- **Which tlv case takes a value is CR-2026-148 (PS-527 amended, draft for 0.5.5).** Jason
  adopted this repository's proposal in full; the rule, as implemented in Python
  (`_case_takes`), the TS013 runtime (`encCaseTakes`), Go (`caseTakes`), Java and C#
  (`caseTakes`/`CaseTakes`):
  - *Fits (required):* undo the field's arithmetic - the lookup reversed, the `transform`
    stages and mult/div/add inverted - and, for an integer type or bit range, round per
    PS-552 (half to even on binary64); the raw value must lie within the type's range
    (PS-168): integers by width and sign, f16 at most 65504, f32 at most 3.4028235e38, f64
    any finite value; a nibble decimal by PS-556's n; a minifloat by whether it encodes.
    A value that does not fit is one the case "cannot represent" (am308's `tvoc` of 4369
    against a `div: 100` u16).
  - *Exact (a preference, never a rejection):* the forward arithmetic re-applied to that raw
    value, through the type's storage (cast to f32 or f16 for those), equals the given value
    under binary64 equality. A decoded value always reproduces exactly, so the case it came
    from is chosen again; 21.8 typed into an f32 fits but is not exact.
  - *Ranking:* the encoder sweeps the cases twice, first placing only exact values and then
    the rest, each sweep in the PS-527 order (widest claim, then declaration order, or Go's
    `TLVOrder`). No tolerance anywhere. It replaces "the reversed value is an integer within
    an absolute 1e-9", which refused every float field (wilsen, sparkfun: an f32 of 21.8 was
    taken by no case) and 0xfffffe through `div: 100` (elv: 16777214.000000002).
  - Cases: `tests/cr-2026-148-cases.yaml` (all five), including `div: 10` against `div: 100`
    for one name, where 23.45 goes to the `div: 100` case. Corpus: no device vector's chosen
    case changed (every vector's encoded bytes and errors compared before and after in
    Python; Go, Java and C# failing sets unchanged).
- **A nameless inline match in a tlv case, or in an object there, claims its branches'
  names.** `_case_claims` and its ports skipped a nameless field, and a `- match:` construct
  is one, so:
  - a case whose only content is such a match (thermokon's plans) claimed nothing, was
    never written, and **the encode returned an empty payload with no error** - in all five;
  - an object whose members come from such a match (Milesight wt303/wt304 `clear_plan`) was
    refused by every case: "no case takes the value of 'clear_plan' ... in full".
  `_match_claims` (Python), `encMatchClaims`, `matchClaims` (Go, Java), `MatchClaims` (C#):
  the match's `name` and every branch's fields (a `default` list included), each optional
  and each name once; the match's discriminator is then derived from the branch the input
  gives (PS-551). One side effect: `tag-component-reference-binding.yaml`'s two vectors,
  whose wildcard-tag case never encodes, used to encode to an empty payload with no error
  and now fail by name ("no case takes the value of 'hit'").
- **Fixtures:** `tlv-object-with-inner-branch`, `tlv-case-of-one-inline-branch`,
  `tlv-float-reading`, `tlv-scaled-wide-reading`, three vectors each; every one failed to
  re-encode in all five before (the inline-branch case as an empty payload). Decode floors
  2697 -> 2709 in Go, Java and C#; re-encode +12 everywhere (Python and Java 2103 -> 2115,
  C# 2104 -> 2116, Go 2121 -> 2133, plain 2094 -> 2106), all `tlv`. C encodes no tlv.
- **Registry (Python, read only), 5049 -> 5267 round trips, +218, none lost:** Milesight
  154 (wt303/wt304 `clear_plan` and kin), aquascope 33, thermokon 16, pepperl-fuchs
  wilsen 11, elv 2, sparkfun 2. Left, each a different shape:
  - wt303/wt304's alarm channels (84): an object whose only members are computed from an
    internal `_t` the encoder cannot derive (PS-434);
  - netvox's "Missing required field: match": a nameless top-level inline match whose
    taken branch is its `default`, the discriminator unreported. Where a nested match in
    the default reads it, CR-2026-151 now derives it (next entry); where none does, nothing
    in the input fixes it;
  - 207 vectors encoded to an empty payload with no error, under `internal-field`,
    `pattern-tag` or `undecoded-bytes`. Closed by "No silent empty encode" below.

- **CR-2026-156 (PS-567 to PS-569; applied on spec main for 0.5.5):
  `sampled` on a repeat.** A repeat, in its `element` or its `fields` form, may declare that
  its elements are samples of one measurement at a fixed interval: `interval` (required: a
  number of seconds above 0, a `$field` decoded earlier, or `{configured: name}`), `order`
  (`newest_first`, the default, or `oldest_first`) and `at` (`rx_time`, the default, or a
  `metadata.timestamps` entry of mode `unix_epoch` or `iso8601`). It is an annotation:
  decode and encode are untouched and no time is added to the output.
  - **`_meta`:** `_meta.fields.<repeat>.sampled` is `{interval, order, at}` with the defaults
    written, after `identity` (PS-568); a `$name` interval is `{"field": name}`, a number
    stays a number. Where it lives: Python `sampled_meta` (`field_meta`), Go `sampledMeta`
    (`go/schema/cr156.go`, from `fieldMeta`), Java `Sampled.meta`, C# `SchemaParser.SampledMeta`
    (`dotnet/PayloadSchema/Sampled.cs`). The TS013 codec carries no `_meta`, so it only loads
    and rejects.
  - **Load (PS-569), in the spec prototype's words** (`check_sampled`): `Field 'x': sampled
    needs an interval (PS-569)`, `... interval 0 is not a number of seconds above 0, a $
    reference or {configured: name} (PS-569)`, `... order 'sideways' is not newest_first or
    oldest_first ...`, `... at 'x' is neither rx_time nor a unix_epoch or iso8601 timestamps
    entry ...`, `... is defined only on a repeat, not on u8 ...`. The value in a message is
    Python's repr (`sampledRepr`, `Sampled.repr`, `PyRepr`). Python `sampled_errors`, called from
    `schema_iterator_errors`, so the interpreter, the validator and the TS013 generator
    refuse the same schemas; Go `checkSampled` (`schema.go`, after `checkFieldNames`), Java
    `Sampled.check` (`Schema.java`), C# `CheckSampled` (`SchemaParser.cs`). Each walks the
    header, fields, ports and definitions of the raw document, test_vectors skipped.
  - **Where the prototype and PS-569 differ, this repository follows PS-569.** The text says an
    interval that references a field the repeat cannot see is rejected (PS-539); the
    prototype's `REFERENCE_KEYS` has no `interval`, so it accepts `$nowhere`. `interval` is a
    reference key here in all four (`REFERENCE_KEYS`, `referenceKeys`, `REFERENCE_KEYS`,
    `ReferenceKeys`), so a `$` interval naming nothing, or a field declared after the repeat,
    gets PS-539's message (`interval` is the key it names). The walk reads `element` and
    `fields` before the repeat's other keys, so a field of the repeat's own elements counts
    as declared - the same looseness `identity` has.
  - **Vocabulary:** `sampled` is a field key and a context of its own (`interval`, `order`,
    `at`) in `tools/schema_vocabulary.py`, with the keys under `definitions.field` in
    `schemas/payload-schema.json` (the object is closed). `docs/SCHEMA-LANGUAGE-REFERENCE.md`
    carries a section; `tests/test_reference_vocabulary.py` requires it.
  - **C:** has no repeat, and `tools/schema_binary.py` refuses `sampled` by name.
  - **Cases:** `tests/cr-2026-156-cases.yaml` (the prototype's ten tests as four accepted
    `_meta` cases and eleven rejections, two of them PS-539's) is read by
    `test_cr_2026_156_sampled.py` (interpreter, validator and TS013 generator), Go
    `cr156_test.go`, Java `CR2026156Test`, C# `CR2026_156Tests`. The fixture
    `_language-conformance/repeat-sampled.yaml` carries `expected_meta` with all three
    interval forms. **The only floor moves are that fixture's one vector:** decode +1 in Go,
    Java and C#; re-encode +1 everywhere (Python, Java 2135 -> 2136, C# 2136 -> 2137, Go 2153
    -> 2154, plain 2125 -> 2126), `repeat` 25 -> 26. A timestamps `at` is not in the fixture:
    the enrichment is optional (see the rule on optional features), so that form is a case.
  - **Open, as the CR says:** a unit other than seconds (`{value: 10, unit: min}`) is not
    accepted; it can be added later without breaking a schema. The Integration Layer's half
    (stamping each sample) reads `_meta...sampled` and is not here.
- **CR-2026-151 (PS-551), applied on spec main for 0.5.5: a nested match fixes the
  discriminator it reads.** An inline match (`length:`) whose discriminator the input
  neither gives nor fixes through one of its own cases takes it from a nested match that
  reads it (`field: $report_type`), where the input fills exactly one of that match's
  cases, the case's key is a single value, and the value selects, in the enclosing match,
  the branch holding the nested match. The derived value goes through PS-557 and PS-168
  like a supplied one, and the nested match sees it as the variable. Otherwise the input
  is rejected naming the discriminator - its `name`, else its `var` - as `Missing required
  field: report_type`, where it said `match`. Python `_nested_discriminator`, runtime
  `encNestedDiscriminator`, Go `nestedDiscriminator` (`cr151.go`), Java and C#
  `nestedDiscriminator`/`NestedDiscriminator`. C writes no match, so it does not apply.
  - **Two readings of the CR text, flagged to the spec session:**
    - *"Fills a case"* is read as: the input gives every one of the case's fields, and
      nothing any other case of that nested match declares beyond them. Netvox r718n3's
      case 3 holds the fields of its cases 1 and 2 together, so an input with all of them
      is case 3's; read as "any field given", every r718n3 status vector is ambiguous.
    - *"Every enclosing match"* is read as every match between that reads the same
      discriminator; a match between that reads another is not consulted.
  - **Measured before, the example `{volt: 3, temp: 26}`:** Python and the runtime refused
    it (`Missing required field: match`); Go refused it (`match case "default" names no
    single discriminator value`); **Java and C# returned an empty payload with no error**,
    taking an unnamed inline match with no discriminator for the non-advancing peek before
    looking at its `default`. All five now write `011e0a28`. Java and C# take the `default`
    beside the cases as the reference does, and a `default` branch with no discriminator
    to write is the PS-551 error, where Java and C# wrote 0; Go said "match case
    "default" names no single discriminator value" there and now gives the same error.
    No corpus vector changed but the new fixture's (encoded bytes and errors compared in
    Python; Java and C# failing sets unchanged).
  - **Cases:** `tests/cr-2026-151-cases.yaml` (all five): the CR's example, a range key, two
    filled cases, a value selecting another branch, the named-discriminator error, a
    derived value past its byte (PS-168), and a case holding two others' fields. Fixture
    `match-discriminator-from-inner-case.yaml`: decode 2709 -> 2712 in Go, Java and C#;
    re-encode +3 everywhere (Python and Java 2115 -> 2118, C# 2116 -> 2119, Go 2133 ->
    2136, plain 2106 -> 2109), all `match`.
  - **Registry (Python, read only):** the CR expected the 153 Netvox vectors; measured,
    5267 -> 5274 round trips (+7, r718nl3; none lost). Of Netvox's 359 vectors failing
    `Missing required field: match`, 34 now derive the discriminator - 7 round-trip, 21
    (r718n3) then stop at its internal `_low_battery` (PS-434), 6 drop a trailing reserved
    byte - and 325 now say `Missing required field: report_type`: in those schemas no
    nested match reads the discriminator (rb02b's reads `$device_type`), so its `default`
    stands for every non-zero report type and nothing in the input fixes it.

**A bit range's base may be any whole number of bytes from 1 to 8, and a wide non-consuming one
binds its name as a selector** (`u40`, `u48`, `u56` as well as the plain widths): a byte several
positions ahead is the last byte of a base that wide, so `u56[2:6]` at offset 0 reads bits 2-6 of
byte 6 without moving, and a `match` after it dispatches on the name. The registry's dragino
lse01-121 and lsn50v2-d20-d22-d23 use it. Measured on `wide-peek-selector.yaml` (a signed `s40`
range too) and `wide-peek-selector-u64.yaml`: Python, Java and C# decoded both; **Go refused the
schema** (`unknown type: u40[0:1]`, its `bitRangePattern` listed 8/16/24/32/64 only); **the TS013
generator emitted a TODO comment for the read** - `type_size`, which knows plain types, returned
None for `u40`/`u48`/`u56` - so `$layout` was never bound and the codec threw `Match field
$layout has no value`. A u64 base worked. Go's pattern and the generator's new `slice_base_size`
fix both, and the generator now raises for a base it cannot size rather than emit a comment.
The peeks are named, not `_`-prefixed, on purpose: an internal one writes payload bytes and
declares no value, a PS-434 error on encode, whereas a named peek is derived from the field that
writes its bits (PS-551, CR-2026-141). Floors: decode +5 in Go, Java and C#; re-encode +4 and `match`
+4 everywhere (the skipped-four vector cannot round-trip).

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

**No silent empty encode (PS-442, PS-551).** An encoder never returns success having
written nothing for a construct whose decode reads bytes. The private registry had 307
vectors on master (207 after #138) that encoded to an empty payload with no error. Every
one was a construct that found nothing to write and returned quietly (measured against #138's
head, which master now is). All five encoders
(Python, the TS013 runtime, Go, Java, C#) now do the following, and C does its part.

- **A match chooses its case through every construct in it.** `_body_score` (Go
  `bodyScore`, Java `bodyScore`, C# `BodyScore`, runtime `encBodyScore`) counts the names
  the input gives through each of these:
  - a byte_group's or flagged group's members;
  - a nested match's best branch;
  - a tlv's claims.

  A literal the input gives with another value rules a branch out (PS-359, PS-513). So
  does a nested match all of whose branches are ruled out, unless its default is `skip`.
  A computed field is not counted: its name shows only that the branch reports it.
  `_best_case` takes the highest score, the first of equal cases winning. A `default` list
  wins only if it scores strictly higher. The same score feeds PS-551's discriminator
  derivation (`_sole_case_given`: unique, untied, one exact key). Only the top level of a
  case used to count. So imbuildings' and dingtek's `match: {remaining: true}`, whose case
  is a header byte and then a match on it, chose no case and wrote nothing.
- **A `remaining` match given no case's fields, and none of its default's,** takes the case
  matching 0 bytes. Writing nothing selects that case (tinovi's `0..1: []`).
- **A match that chooses nothing fails, unless its default is `skip`.** This is the guard
  at match level, and the narrowest one that holds. A chosen case body cannot write zero
  bytes without an error where it reads any, because every required field writes or
  fails. So the only silent path was the fall-through, which wrote nothing. Its outcomes
  are now:
  - an inline discriminator neither given nor peeked: `Missing required field: {name|match}`,
    whatever the default (this used to return nothing for a nameless or `skip` one);
  - a known discriminator with no case and no default: `No matching case for value {d}`,
    the decoder's own text (this wrote the discriminator alone);
  - anything else: `Missing required field: {name | field without $ | match}`.
- **A tlv case claims its literal and computed fields, optionally.** A literal takes only
  its own value. A computed field takes any value, so the case is chosen and what it is
  computed from is asked for (PS-434). A nested match's literals are claimed once per value
  (aquascope's `pc`: "ok" in one branch, "alarm" in another). Before, an entry reporting
  only constants (wt303's `device_request`) was never written. One computed from an
  internal value (`tlv-case-field-list`'s `internal_then_computed`, and the internal entry
  of `all_four`, which came back four entries long) was dropped.
- **A tlv `name_from` key no case can rebuild is named.** rakwireless's
  `digital_in_${ch}` builds its key from a tag component, which is not in the data. That
  key is now `tlv: no case takes the value of 'digital_in_3' ...`, where it was dropped.
  The check skips a key some field declares by name.
- **The top-level guard is `empty_payload_error`.** It applies where an encode wrote no
  bytes and the input gives a value that only a field reading payload bytes, or computing
  from them, reports. That input is refused: `Encoding wrote no bytes, though the input
  gives '{key}', which the schema reads from the payload (PS-442)`. Values it lets
  through:
  - a value a literal of that name gives;
  - an empty list, mapping or string;
  - an internal value or null.

  So a literal-only branch or a tlv of no entries still encodes to an empty payload. No
  path in any encoder reaches it today, so its unit tests call the helper directly (Go
  checks keys in sorted order). It is the last line against the next construct that finds
  a new way to write nothing.
- **C** used to pass over every internal field and every match. Now:
  - an internal field is written from its input, else PS-434's error;
  - a match writes the case its discriminator selects. A body placed above `field_count`
    is written in place, and the other cases' counted bodies are passed over.
    Otherwise it fails with `Missing required field: {var}` or `No matching case for
    value {d}`.

  C also has the empty-payload guard. Its test is `test_no_construct_is_encoded_as_nothing`.

**The registry's remaining 54 empty encodes are correct.** Each decoded nothing from its
bytes except constants: undescribed bytes, entries holding only internal fields, a pattern
tag's internal value. Decoding an empty payload gives back exactly the same data
(yobiiq meta, moko/restotracker/safora/te-connectivity's leftover bytes). The data does
not carry the bytes, which is a schema finding, not an encoder one. Registry, Python, read
only: 5267 -> 5410 round trips (+143, none lost).

- **24 of the 207** now round-trip (yabby-edge 7, dingtek 7, iq-rm200 4, aquascope 4,
  wt303/wt304 2).
- **129 fail by name:**
  - 56 PS-434 internal fields (imbuildings, dingtek, dragino ldds20/75, yobiiq sd-1001);
  - 25 non-empty payloads that differ only in skip padding;
  - 10 lossy lookup defaults;
  - 10 "Missing required field: match" (lht65n's computed-only branches);
  - 25 "no case takes the value" (rakwireless's tag-built keys, aquascope);
  - 3 "matches a range of tags".

**Closed by CR-2026-153 (below):** the `remaining` length check (PS-564), the order for
a known discriminator matching no case (PS-565), and Go's tie order (PS-563).

Fixtures: `length-selects-layout` (4 vectors), `literal-selects-branch` (2) and
`tag-entry-constants` (4). With `tlv-case-field-list`'s `literal_only`, all 11 round-trip
in all five and failed before. On top of CR-2026-151: decode floors 2712 -> 2722 in Go,
Java and C#; re-encode +11 (Python and Java 2118 -> 2129, C# 2119 -> 2130, Go 2136 ->
2147), `match` +6, `tlv` +5. Go plain is +10 (2109 -> 2119): `both_entries` is laid out
non-ascending.

**With CR-2026-151 (nested-match derivation).** An inline match's case is settled in this
order, in all five: the discriminator the input gives (or a peek); a case chosen by score
(`_best_case`) or, for `remaining`, the case matching 0 bytes; only then CR-2026-151's
`_nested_discriminator`, which therefore runs exactly where the input neither gives the
discriminator nor fixes it through one of the match's own cases; then the `default`. A
score-chosen case wins over a nested derivation: in `an_outer_case_still_its_own`
(`{version: 17}`) case 0 scores and is written as `0011`, and the nested match is never
consulted. Where both rejections could apply - an inline match with no discriminator
and nothing to derive - CR-2026-151's is taken, being the more specific: it names the
discriminator (`Missing required field: report_type`, its `name`, else its `var`), and
this note's fall-through says `match` only where the match has neither.

**CR-2026-153 (PS-562 to PS-565, la-payload-schema branch `cr/0.5.5-apply-152-154`, for
0.5.5) wrote #140 into the spec; three items were left for this repository.**

- **PS-564: what a `remaining` match writes selects the case it chose.** The bytes from
  the match to the end of the payload are counted per field list (`TailLevel` in Python,
  `tl` in the runtime's `encList`, and each port's equivalent). After each item of a list,
  the bytes it added go to every check an earlier item of the same list registered, so at
  the top each check holds its whole tail. A decoder's choice for that count is then
  compared with the encoder's - the first case in declaration order matching it, else the
  default, else none (`remaining_selection`). A mismatch is rejected, naming the match as
  PS-535 does: `Field an unnamed match: the 2 byte(s) from it to the end of the payload
  select case 2, not case 1, the case its input gives (PS-564)` ("its default", "no case";
  `'name'` for a named match). The spec prototype encodes no match, so this text is this
  repository's. **Not checked inside a tlv or a repeat** (`tail_unchecked`): the bytes
  after the match are not all in field lists there. No corpus, registry or fixture vector
  is rejected by it.
- **PS-563: a tie goes to the case declared first, in all five.** The runtime iterated a
  match's cases as a JS object, whose integer-like keys run ascending; the generator now
  records the order written as `__order` (`_annotate_on_repeat`) and `encCaseKeys` reads
  it, for the score, the discriminator's case, the 0-byte case and PS-564. Go keeps
  `Cases` sorted for decode and now reads the order written from the YAML node tree
  (`s.keyOrder`, as `_meta` and the reference walk do; a JSON-authored schema falls back
  to the sorted order), so `{2: [a], 1: [a]}` given `{a: 5}` writes `0205` where it wrote
  `0105`. Go's empty-payload guard checks names in declaration order, where it checked the
  input's keys sorted (`TestEmptyPayloadGuardDeclarationOrder`). Java and C# already
  walked their cases in declaration order. Go measures PS-564's tail as the payload length
  less the match's start, since it writes every field list into one buffer.
- **PS-565: a given discriminator matching no case takes the default, or is rejected.**
  Python and the runtime recovered a case by score first, so `{k: 9, a: 4, b: 3}` against
  `{1: [a]}` with `default: [b]` wrote `0904` - case 1's byte under a discriminator that
  decodes as the default, reading `b` = 4 - and an inline `kind: 9` with no default wrote
  `0904` with no error. Both now follow Go, Java and C#: the default, else `No matching
  case for value 9`; the score runs only where no discriminator is given. A `skip` default
  writes the discriminator alone, as a decoder reads it.
- **Cases:** `tests/cr-2026-153-cases.yaml` (11), read by `test_cr_2026_153_cases.py`
  (reference and runtime), Go `cr153_test.go`, Java `CR2026153Test`, C# `CR2026_153Tests`.
  Corpus and registry (Python, 5475 of 32336 round trips at f57faba) unchanged; no floor
  moves.
- **The match's own case wins** (CR-2026-155, editorial, PS-551 and PS-563): an inline
  match settles its case in the order given discriminator or peek, own case by score or
  the 0-byte case, nested derivation, default, reject - so `{0: [temp]}` beside a nested
  `{1: [temp]}` given `{temp: 26}` writes `000a28` (`own_case_wins_over_nested_derivation`).
Cases: `tests/silent-empty-cases.yaml` (14), read by the reference and the runtime
(`test_silent_empty_cases.py`), Go (`silent_empty_test.go`, both APIs), Java
(`SilentEmptyTest`) and C# (`SilentEmptyTests`).

**CR-2026-162 (PS-572 to PS-574; PS-433, PS-551, PS-563, PS-564 amended; la-payload-schema
branch `cr/0.5.6-apply-158-162`, for 0.5.6): the decode record.** The decoder records what it
chose, so an encoder need not infer it.
- **Decode (PS-572).** Asked for, the interpreter output carries `_decode` beside `_meta`:
  `{matches: {ptr: {case, value}}, internal: {ptr: value}, flagged: {ptr: flags}}`. decode()
  never carries it, and a TS013 codec never produces it (PS-467). The opt-ins:
  - Python `interpret(..., decode_record=True)`;
  - Go `InputContext{DecodeRecord: true}`;
  - Java `interpret(payload, ctx, true)`;
  - C# `SchemaDecoder.Interpret(..., decodeRecord: true)`.
  - C has no `_meta` and no record: not applicable.
- **What is recorded.** Where the prototype records it, and the same values:
  - every Option-B match evaluated: its case key (an integer key as a number, else its text;
    `"default"` for a default or `skip`) and the discriminator, recorded before its body
    decodes;
  - every flagged construct's flags;
  - every internal field decoded, computed ones included, with the value it would report;
  - an internal byte_group member: a `bool` its flag, any other member the number its name
    binds.
- **Locations.** An RFC 6901 pointer into the `$ref`-expanded document (`header`, `fields`,
  `ports`), `~0`/`~1` escaped. An integer key YAML resolves (`0x01:`) is written in
  decimal, as PyYAML loads it. The occurrence indices are:
  - `/repeat[i]` after a repeat field's pointer;
  - `tlv[k]`, the entry in payload order, unknown tags counted;
  - `records/n[0]`.

  One builder per language, the prototype's `pointer_map` and `DecodeTrace`: Python
  `pointer_map`/`DecodeTrace`, Go `cr162.go`, Java `DecodeRecord`, C# `DecodeRecord.cs`. The
  TS013 generator annotates each locatable node of the embedded plan with `__ptr`
  (`_annotate_pointers`), and the runtime's `encLoc` composes the indices.
- **Encode (PS-573).** The record arrives as `data['_decode']` or as a parameter: Python
  `encode(..., decode_record=)`, Go `EncodeWithRecord`/`EncodeOrderedWithRecord`, Java
  `encode(data, record)`/`encodeWithPort(..., record)`, C# `Encode(..., decodeRecord:)`, the
  runtime's encodeDownlink input. `_meta` and `_decode` are never input.
  - Each field list takes its recorded internal values first (`_recorded_inputs`).
  - Recorded flags choose a flagged block's groups where the input has no mask.
  - A match settles its case in this order: the discriminator the input gives, or a peek;
    then the record's value (its case, for `remaining`); then CR-2026-155's order below.
  - An inline discriminator's PS-168 check now runs before its case is looked for, as the
    prototype does: `kind: 300` over one byte is PS-168, where it said "No matching case".
- **Rejections, in the prototype's text.** Input contradicting the record:
  - `Field '_k' is given 2, but the decode record holds 1 (PS-573)`, the values as Python's
    repr;
  - `Field an unnamed match: the input gives 'b', which only another case declares, against
    the recorded case (PS-573)`.

  A match whose case nothing settles keeps CR-2026-151's and #140's texts, with ` (PS-574)`
  appended: `Missing required field: report_type (PS-574)`.
- **PS-574: this repository derives.** The prototype does not, and rejects
  `{volt: 3, temp: 26}` for r718n3. Here PS-551's nested-match rule, PS-563's score and
  PS-564's 0-byte case stay, as the optional fallback, so that input still writes
  `011e0a28`. Decoding `011e0a28` with the record and encoding it gives `011e0a28` back.
- **Where this differs from the prototype, and what CR-2026-164 settled.** The clash check
  reads a match's `default` list (`record_declared_fields`): the prototype's
  `declared_fields` did not, so a name a nested match's default reports (dingtek df702's
  `angle`) read as declared only by another case. Jason ruled on 2026-10-13 (CR-2026-164,
  la-payload-schema `cr/0.5.6-ps573-yaml`, submitted) that the `default` list is one of the
  match's cases, and that "only another case declares" is read **within the match alone**:
  a name something outside the match also declares (dragino lht65's `TempC_DS`, reported by
  a sibling match) and a tlv entry's scope (thermokon) are not refinements PS-573 has. The
  earlier `_declared_beside` is removed from Python, Go, Java, C# and the TS013 runtime, so
  the clash reads alike in all five.
- **CR-2026-165 (PS-573 amended; la-payload-schema `cr/0.5.6-apply-165`, for 0.5.6): a name
  declared outside the match, in the same reported object, is not another case's.** Jason
  adopted the refinement CR-2026-164 had left out, at a narrower scope than the removed
  `_declared_beside`. A field is a contradiction when another case of the recorded match
  declares it (the `default` list counts), the recorded case does not, and nothing outside
  the match declares it within the same reported object.
  - **Scope, as the prototype's `EncodeContext.enclosing`:** a stack of the field lists
    enclosing the entry being encoded, each with its current entry. The names of the other
    entries are walked through the merging constructs (`record_declared_fields`: match cases
    and default, tlv cases, flagged groups, byte_group, records) and subtracted. It is reset
    at an `object` and at each repeat element, so top-level names do not count for a match
    inside either; the removed `_declared_beside` also counted the message's whole top-level
    list. Where it differs, only a match inside an object or element.
  - **Where:** Python `_enclosing` (`_encode_field_list`, `_encode_object_members`,
    `_check_recorded_case`), the runtime's `ctx.encl` (`encList`, `encObject`), Go
    `EncodeContext.enclosing`, Java `Encoder.enclosing`, C# `SchemaEncoder._enclosing`.
  - **Cases:** `tests/cr-2026-162-cases.yaml`: the prototype's three
    `TestCR2026165OutsideNames` tests (the lht65 sibling shape, a real contradiction, an
    object boundary), and CR-2026-164's "name declared outside the match" case, which flips to
    accepted for a name in the same object.
  - **Measured** (read-only registry, Python): 13038 round trips with the record (12980
    under CR-2026-164), 5475 without it, unchanged; the proto corpus with the record 2402,
    unchanged. The set equals the earlier wider implementation's exactly: all 58 vectors it
    regained (dnt etrv 34, dragino lt22222-l 12, lht65/lht65n-pir/lsn50-v2 12) are recovered,
    since their matches sit at top level, and none is lost.
- **A number in a PS-573 message is JavaScript's String(n)** (CR-2026-164): `is given 2`,
  not `2.0`. `message_value` (Python, over `format_lookup_value`), `messageValue` (Go, over
  `givenText`), `messageValue` (Java, over `jsNumber`), `MessageValue` (C#, over `JsNumber`);
  the runtime's `encPyRepr` already wrote `String(n)`. Anything that is not a number keeps
  Python's repr. A float input is in `tests/cr-2026-162-cases.yaml`.
- **Measured.** Python, read-only registry (32,408 decoded vectors); the no-record numbers are unchanged
  in both trees.

  | | No record | With the record | Change |
  |---|---|---|---|
  | Registry | 5475 | 12980 | +7505, none lost |
  | Proto corpus | 2145 | 2402 | +257 (261 gained, 4 lost) |

  - The registry gains are almost all PS-434 internal fields (7485). The CR predicted +628
    from case and value alone; the recorded internal values are most of the difference.
  - The four proto losses are dnt-lw-wsci's (two schemas) `reset_ack_then_hall` and
    `set_ack_then_hall`. Each round-tripped by accident: the flagged block wrote a hall
    group the decode never took, and its byte ORed into the 0xFD peek. With the recorded
    flags (0) nothing is written, and the 0xFD entry, which reports nothing, has no value
    to be written from.
  - `tools/encode-round-trip.py --decode-record` measures the corpus.
  - Without the removed refinement, the registry loses 58 round trips against the earlier
    13038, each rejected by the strict clash check (dnt etrv 34, dragino lt22222-l 12,
    lht65/lht65n-pir/lsn50-v2 12). Of the 59 vectors a strict check rejects that round-trip
    unchecked, 3 still do with the `default` list counted and 56 are rejected; 199 vectors
    are rejected in all, against 220 with a fully strict check. Proto is unchanged at 2402.
- **Guards.**
  - `make gate-verdicts` rule 4: each runner's `CORPUS_META_REPORT` entry carries `decode`,
    and every corpus vector's record must equal Python's. 2744 of 2744 identical in Go, Java
    and C# on landing, 849 non-empty.
  - `tests/test_ts013_encode_parity.py`: the runtime given the reference's
    interpret-with-record output must write the reference's bytes on every corpus vector.
  - Cases: `tests/cr-2026-162-cases.yaml` (the prototype's 13 tests and six more), read by
    `test_cr_2026_162_cases.py` (reference and runtime), Go `cr162_test.go`, Java
    `CR2026162Test`, C# `CR2026_162Tests`.
- **Found on the way.** Go, Java and C# never matched a text discriminator (an `ascii`
  field) against a text case key; Go read it through `toInt` as 0. All three now compare
  text exactly, as Python does.
- **No floor moves.** The corpus runners decode and re-encode without the record.
- **PS-433:** `_decode` is reserved for interpreter metadata in all four loaders.

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
**CR-2026-134 (PS-551) reversed that ruling**: a missing value is now an error naming the
field, with no payload, in all five; see its entry under 0.5.2.

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

**Warning volume was measured, not assumed (superseded by PS-551, which made the warning an
error): 1 warning across all 1190 encodable corpus vectors**, and it is a true positive — `_language-conformance/name-from.yaml` reports
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

**`test_comprehensive.c` is the one C test file in no build target** - deliberately; see
below. `test_interpreter.c`, `test_binary_schema.c` and `test_encoder.c` used to be in none
too, which left C's encoder verified by nothing that ran; they are in `C_TESTS` now and
`make test-c` builds them. Six compiled executables are also tracked in
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

Milesight stands at 64 of 84 agreeing. `vs321` and `vs373` are the next region-mask
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

**The netvox/arwin/dnt conversions (2026-09-24) found seven defects, none visible to the
corpus before those schemas existed.** Each is fixed; the list is here so nobody re-derives it.

- **Python bound nothing for a top-level `_` field** — read the byte and discarded it, so
  `match: {field: $_kind}` failed with "Match has neither 'field' nor 'length'" (the key *was*
  there; the message now names the unbound reference). The computed-field path already bound
  `_` names, which 31 intermediates in 9 corpus schemas rely on, and Go, Java, C# and the TS013
  generator all bound them. `_bind_internal` now does it on every field-list path.
  **This is a prototype convention pending a CR, not spec behaviour**: PS-176 reserves `_` names
  for interpreter metadata, and the spec has no other spelling for "decoded and referenced, not
  reported". `_language-conformance/internal-discriminator.yaml` pins it.
- **Encoding that byte back.** An internal discriminator is not in the data, so it was written
  as zero while the match emitted the fields of the case the data fits: `0107` re-encoded as
  `0007`, silently. Python's encoder now infers it from that case when the case key is one exact
  value (`_internal_discriminators`, beside the `flagged` mask pre-pass). **Go, Java and C# do not
  yet**: they decode the fixture and fail its round trip, which is why their decode floors moved
  and their encode floors did not.
- **Go and C# `byte_group` members skipped their modifiers and lookup.** arwin's temperature,
  `u32[0:9]` with `div`/`add` in a group, read the raw 1023 against 72.3. C# had no reusable
  post-read step, so it was extracted into `ApplyPostRead`; Go's members now go through
  `applyLookupAndModifiers`.
- **Java's `bool` ignored `bit:` and `consume:`** — every bool read bit 0, and a flag group ending
  in `consume: 1` left the cursor on the byte. No corpus schema used `type: bool` until dnt.
- **The TS013 generator had no `bool` at all** (a TODO and no value), which is why the first dnt
  draft used `{0: false, 1: true}` lookups — outside what PS-106 then listed (numbers or
  strings), and **Go dropped a non-string lookup label silently**. CR-2026-104 since allows
  boolean labels and fixed Go's labels to hold any scalar (see below).
- **The generator renamed output keys** to JS identifiers — `pm1.0` came out as `pm1_0`. Keys are
  now restored at the entry points from a table built at generation time. Two names mangling to
  one spelling (adeunis `ER+P-1_s` and `ER-P-1_s`) used to be a generation error; now
  `unique_js_names` suffixes all but one of them (`ER_P_1_s`, `ER_P_1_s_2`, skipping any suffix
  that is another name's spelling), and `to_js_name` reads that mapping during `generate()`, so
  every staging and reference site agrees. A schema with no collision generates unchanged.
  `_language-conformance/names-mangling-alike.yaml` holds all five to it.
- **`generate_output_schema.py` kept the first branch's enum** when two branches report one key
  with the same type — r718x's `Status` is ON/OFF in one message and Success/Failure in another.
  Same-typed branches now union their enums and widen their bounds.

Still open from the same work: `lookup` is ignored on a `number` field; the generator drops string case keys in
a `match`; `score_schema.py` counts branch coverage only for discriminators present in the output
and never looks inside `match`/`byte_group` for semantic fields, which is most of why r718x and
dnt stop at Silver. r718x still reads its device and command bytes through a non-advancing peek
it adopted before the `_` fix; it can use `$_device_type` directly now.

**Every nested field list is decoded by `_decode_field_list` (2026-10-10).** Python's tlv
case body, a `type: object`'s members and a `flagged` group's fields were each their own loop
of plain reads (a group holding another `flagged` failed "declares no type" - mutelcor), so a number
literal there was "unknown type: number", a `match` or `byte_group` member "declares no type",
`var:`, internal references and `sentinel` were ignored, and a case's `flagged` read a phantom
byte. Go, Java and C# already decoded all of it. Both now go through `_decode_nested_list`,
which decodes into a fresh object, raises the list's first error, and hands its `_quality`
and warnings to the decode, as a match case body's are. A name bound inside an object stays
bound after it - the spec's "Combining Nested and Conditional" example relies on it, and Go,
Java and C# all do it. The TS013 generator skipped every nameless object member (a `match`
or a group of bit ranges decoded nothing and the group's byte was never consumed) and
reported internal members; both are fixed. `tlv-case-field-list.yaml`,
`object-member-field-list.yaml` and `flagged-group-field-list.yaml` hold all five to it. The validator now descends into an
object's members too; it never had, so a typeless member passed.

**A case body is a field list; a bare string is rejected at load (PS-441).** `cases: {5: skip}`
crashed `schema_iterator_errors` and the decoder, while Go, Java and C# parsed it as an empty
case - a match then decoded nothing for 5 with success, and a tlv dropped the case and skipped
the tag. `case_body_errors` (Python, the validator and the generator), Go's `checkCases`,
Java's `checkCaseBodies` and C#'s `CheckCaseBodies` all give `match.cases[5]: a case body is a
field list; write [] for a case that reads nothing (PS-441)`. `[]` is the empty case.

**`ascii` drops trailing NUL padding only, in all five.** Python, Go and C# did; the generated
codec kept every NUL and Java removed every NUL and trimmed whitespace at both ends. The spec
says nothing about NUL padding in `ascii` - this is agreement, not a requirement.

**A `skip` past the end of the payload is an error in all five.** Python and the generated codec
advanced past the end and reported success, so a truncated frame decoded with no error; Go, Java
and C# already failed the read. Measured on the private registry, the nested-list and skip fixes
together moved 487 vectors from failing to passing in Python and none the other way.

**Go's corpus runner compares nested values member by member (PS-043 to PS-045).** It compared a
nested mapping or list by its printed form, so a nested null - "this member is absent" - could
never pass, and a nested float had no tolerance. Python, Java and C# already recursed.
`object-member-field-list.yaml` asserts that padding and internal members of a group are absent,
which is what found it: Python used to report them as `unknown: null` and `_h: null`.

**A tlv `tag_fields` component's `var:` binds for the case body, in all five.** None of them
bound it, so `name_from: "temperature_${ch}"` with `ch` from the tag failed "references ch, which
has not been decoded" everywhere (the registry's rakwireless standardized payload).
`tlv-tag-var-name-from.yaml` pins it. Encoding such a case cannot rebuild the key: the tag's
value is not in the data.

**The Milesight wrong-decode pass (2026-09-24) left three things open.**

- **A peeked status beside the word it overlaps does not encode.** `em320-tilt`'s rewrite
  read threshold flags as bit-0 peeks of the angle words and exposed an encoder defect: the
  peek and the word are packed as separate bytes. The schema was left as it was, still
  reading three bytes the vendor does not, so it is known-wrong, not fixed.
- **`encode-round-trip.py` classifies a residue per schema, not per vector.** Once a schema
  carries any lookup `default`, every failure in it reads `lossy-value` - 5 became 148 in
  this pass - which hides real encoder defects in the same schema, such as a `type: object`
  inside a tlv case encoding as zeros (ct303 already failed that way).
- **Status codes inside a tlv case** (gs301 0xFFFE, ct 0xFFFD) need a `match` inside the
  case, which the Python interpreter cannot decode; they are hints in the schemas.

**Two corpus-wide guards, added 2026-09-24, and what they found on their first run.**

- **`tests/test_corpus_invariants.py`** holds every corpus vector to invariants no
  expected-value block can state: a successful decode consumes its whole payload; output
  is strict JSON with no stray `_` keys; integral values are integers; decoding is
  deterministic; the schema decodes the same when authored as JSON; and every key a corpus
  schema uses is read by some implementation. Violations sit in reasoned ratchet lists -
  a new one fails, and so does a listed one that is fixed, so the lists only shrink.
  **The JSON check found a real defect at once**: Python compared a match/tlv case key
  with `==`, and a JSON key is always a string, so the same schema as JSON decoded a
  different branch in 19 schemas - 116 vectors as *successful* decodes of the wrong data
  (netvox r718x its `default:` on every report; dnt, mla20 and elsys every tlv channel as
  unknown). `_match_case_pattern` now compares a numeric-string key as a number.
  The unread-tail list names real schema gaps - now only arwin port 12's byte 7, which
  waits on a vendor document (am30x's copied example, ws50x's and em310-tilt's padded
  vectors and r718x's documented reserved tail were settled on 2026-10-09; a padded
  harvested vector can hide from it, since an undescribed tag counts as consumed, so
  check a harvested vector's length against its case's width too); fixing one means removing its
  entry.
- **`tools/schema-mutation.py`** mutates each schema one step at a time (byte order,
  sign, width, scale, offset, bit range, lookup label, case key, flagged bit, dropped
  field) and asks whether the schema's own vectors notice. The first run: 91.3% of
  reachable mutants killed. The survivors are a list of missing vectors - byte order is
  unchecked on many Milesight multi-byte fields, the ct/em400 temperature channel's only
  vector is all zeros, and the 0xFFFF sensor-status path has no vector. **Vectors taken
  from our own decoder score as high as independent ones**, so a mutation score measures
  sensitivity, not correctness - the same warning as the quality score above.

  **The survivors were turned into vendor vectors on 2026-09-26.** For each surviving
  mutant a payload was crafted that separates it from the original schema, run through
  the vendor's own decoder, and added as `source: vendor-codec` only where our schema
  agreed with the vendor on every key. That added about 460 vectors and killed all but a
  handful of the 742 survivors; 140 of the 151 scored schemas are now at 100%. What is left
  cannot be killed by a vendor-codec vector:
  - ws302's `time_weight` labels: the vendor decoder reports "impulse" for every byte;
  - mutants that sit within the float tolerance (dl-gmm `div: 1`, vicki's battery `round`);
  - fields the vendor never emits (mla20 case 0x27, rbs30x `sensor_state` and the raw
    tilt bytes, qingping's frame header).

  The disagreements this found were deliberately **not** recorded as vectors. They are
  vicki's reason byte (the vendor reads hex digits as decimal), a 78.125 rounding tie
  (the vendor uses `toFixed`, which rounds half up), rbs30x's high-precision tilt
  temperature (the vendor uses sign-magnitude, we use `s8`), and qingping decoding frames
  that the vendor rejects.

  **Three new fixtures sat below the 0.80 floor for new schemas (fixed 2026-10-11).**
  Two needed a vector; one needed the harness to stop scoring what no payload reaches:
  - *A modifier behind a guard that never passes* - unkillable, now unscored. PS-444
    reports a failed guard's `else` as declared, skipping the modifiers, the stages and
    the lookup. A guard with a boolean operand fails on every payload (PS-549), so
    `guard-boolean-operand`'s inserted `add: 1` could not change any decode. The reach pass now records a guarded
    field's pipeline as reached only where its guard passed, and a SCALE/ADD/DROP-MOD/
    LOOKUP mutant of an unreached pipeline is `unreached`, not a survivor.
  - *A transform that changes nothing at that magnitude* - `round-large-value`'s
    `decimals: 6` on 3.4e38. It dies on an exact integer: 3F800001 rounds to exactly 1.
  - *Leftover bytes with no warning asserted* - `wide-bit-range`'s `consume: 8 -> 0`.
    `expected_warnings: []` kills it (PS-472).

  **Six vobo survivors are equivalent since PS-314/PS-429 (b05fc20, #93).** Each
  `_cfg_mask add N -> N+1` on port 71 subgroup 5 also sets the flagged mask's bit 0,
  whose `cycleTime` computes from `_ct_lo`/`_ct_hi` - bound only by subgroup 0, a
  different case of the same dispatch, so never in the same frame. They used to be
  killed because an unbound operand read 0 and the vectors assert `cycleTime: null`;
  an operand with no value now makes the field absent, so no payload separates them.
  The baseline accepts 20345 killed and 6 survived.

  Three lessons for the next pass:
  - **A tolerance survivor dies on an exact integer.** A +1 raw-unit mutant under a
    fine scale moves the value by less than 0.001, so no float expectation can see it.
    A payload whose vendor output is exactly 0 or 1 is compared exactly (PS-039) and
    catches it.
  - **A value inferred from the vendor code is not vendor output.** Values implied by the
    checks a vendor decoder runs before decoding are not `vendor-codec`, even when they
    are certain. The provenance gate flags changed expected values on an unchanged
    payload for exactly this reason.
  - `crossvalidate_ttn.py` reports `no-vendor-codec` for mla20, rbs30x and qingping,
    all of which have decoders in the TTN repository - a gap in how the tool finds
    them, not yet fixed.

**CI gates (`.github/workflows/gates.yml`, `make gates`, `make gate-verdicts`).** Every
schema bug fixed on 2026-09-24 had passed the existing checks, because those check a
schema against its own vectors. Five gates close that, each a ratchet against a committed
baseline - a regression fails, an improvement is reported and locked in with the gate's
`--update`/`-update` target, deliberately, in its own commit:

| Gate | Fails when | Baseline | On a pull request | On master and nightly |
|---|---|---|---|---|
| `gate-validator-strict` | a YAML/JSON mapping has a duplicate key (a colliding case key used to vanish silently), or a key no implementation reads (`sub:` in a transform, `count_field`, `match_value`, ...) | vocabulary in `tools/schema_vocabulary.py` | whole corpus | whole corpus |
| `gate-provenance` | a changed device schema has no independently sourced vector, or an added vector lacks `source:` | none: the diff against `BASE` | the PR's changed schemas | not run (no diff) |
| `gate-crossval` | a schema stops agreeing with its vendor's own decoder (TTN declared examples + vendor JS, Decentlab decoders), or a new one does not agree | `tools/crossval-baseline.json`, oracle commits pinned | whole corpus (seconds) | whole corpus |
| `gate-mutation` | a schema's vectors notice fewer one-step mutations than before (score or killed count), or a new schema scores below 0.80 | `tools/mutation-baseline.json` | the schemas the PR adds or modifies (`--only-changed`), or the full sweep if it touches the harness, the gate, the baseline, `requirements.txt` or a `tools/` module the harness loads | full sweep |
| `gate-verdicts` | a changed schema's vector fails in any of Python, Go, Java, C#, TS013, or a vector the reference passes fails elsewhere | `tools/verdicts-baseline.json` (empty: all five agree on every vector) | whole corpus, changed schemas strictly | whole corpus |

The nightly run is `gates.yml`'s `schedule` (03:30 UTC). Outside the gates, the plain
`pytest tests/` run (Quality's "Python tests") is the PR's test check; the same suite
under coverage (Fuzz Testing's "Test Coverage", about 2.5 times slower for an identical
test set) runs on master and nightly only, as does the full fuzz.

**Mutation on a PR is the PR's own schemas, not the corpus.** The full sweep cost about
84 minutes on one runner, so `--only-changed` scores only what the diff from the merge base
adds or modifies, under the full sweep's rules (a baselined schema against its entry, a new
one against the 0.80 floor, a deleted baselined one as missing). A change that can move every
schema's score - `FULL_SWEEP_FILES` in `tools/mutation-gate.py`, plus whatever `tools/`
module the harness imports at run time - runs the full sweep instead. If you make the
harness read a new file outside `tools/`, add it to `FULL_SWEEP_FILES`, or a PR changing it
will be judged on its schemas alone.

**One schema is nearly all of the sweep, so the sweep splits schemas, not just the corpus.**
`volley-boast/vobo` has about 25,000 mutants each replayed against 436 vectors; every other
schema together takes about one CPU-minute, so sharding by schema alone would leave one
shard holding vobo for the whole run. The gate plans *units* - a schema, or a contiguous
part of a heavy schema's mutant list (`plan_parts`) - and `schema-mutation.py merge_parts`
reassembles the parts into exactly the report one run gives, which a test holds it to. In
CI six `mutation-shard` jobs each run a slice (`--shard I/6 --parts-out`) and upload it
unjudged, and the `mutation` job reassembles and judges them (`--parts-in`), failing if any
schema in scope, or any part of one, is missing. Locally `make gate-mutation` does the same
in one process pool. Keep the matrix list and `MUTATION_SHARDS` in step.

The mutation gate ratchets the killed count as well as the score on purpose: deleting
vectors makes the mutants only they reached "unreached", which *raises* the score.
The crossval gate counts decode failures separately for the same kind of reason - one
failed decode replaces a mismatch per key, so a broken schema could otherwise look
improved. The Go, Java and C# corpus runners write per-vector reports only when
`CORPUS_REPORT` is set; their default behaviour and floors are unchanged.

### 0.5.2 (CR-2026-037 onward): where the new rules live

- **A null expectation asserts absence** (PS-043, CR-2026-075). Every runner compares
  through `validate_schema.expected_fields_match` (Python, vector-verdicts, the C harness,
  schema-mutation, corpus-report) or its own copy of the rule (Go, Java, C#). An omitted
  field - zero divisor, log of x <= 0, failed guard with no `else`, unmapped lookup - is
  now assertable in the shared corpus; `value-absent.yaml` and `log-of-non-positive.yaml`
  do. A runner that compares only the keys it lists blesses every wrongly reported field.
- **Schema-validity rules are checked when the schema is loaded** in Go, Java and C#:
  `go/schema/vocabulary.go` (`checkTypeVocabulary`, `checkFieldRules`), Java
  `Schema.parseFields`, C# `SchemaParser.ParseFields`. Put a new "MUST be rejected" rule
  there, on list members only - Java and C# also run `parseField` over construct bodies
  (an inline `tlv:` block arrives as a synthetic field typed `tlv`), which carry no type.
  Python rejects at decode time, which PS-328 permits; the validator and the TS013
  generator reject too, so a schema fails the same way on every path.
- **The type vocabulary is closed and case-sensitive** (CR-2026-037). Go canonicalises an
  alias in `parseFieldMap`, so the rest of the package sees one spelling per type; its
  capitalised `FieldType` constants remain only for the compact and binary formats.
- **`type: object` is the nested-group spelling and the `object:` key is rejected**
  (CR-2026-074, PS-466) - the corpus had 154 of the first and none of the second.
- **FPort (CR-2026-038/041/042).** A `ports` schema decoded with no FPort is an error in
  all five and never falls back to `default` (PS-459); `Decode()`/`decode()` without a
  port on a ports schema used to return `{}` with success in Go, Java and C#. Port keys are
  1-255 (PS-018). A top-level `fPort` states the port a document is about, is checked, and
  is never consulted when decoding (PS-335 to PS-338); the library command documents use it.
- **Byte order is never in a type name** (CR-2026-039, PS-053a): `le_`/`be_` are
  rejected everywhere; C# and the TS013 generator read them until 0.5.2.
- **References are spliced before parsing, everywhere** (CR-2026-045): Python
  `expand_refs`, Go `go/schema/refs.go`, Java `Schema.expandRawRefs`, C#
  `SchemaParser.ExpandRefs` - one pass over the raw document, so the typed parsers never
  see a `$ref`. A definition is `{fields: [...]}`; the library was migrated to that shape,
  and `use:` is withdrawn. The generator, validator and C harness call `expand_refs`.
- **`string` is only a literal** (CR-2026-051, PS-361): text read from the payload is
  `ascii`. `value` on a byte-reading field is the constant the encoder writes (PS-360).
- **Wave 4 types** live in one helper per language (`go/schema/wave4.go`, Java `Wave4`,
  C# `Wave4`, module functions in `schema_interpreter.py`): the word-ordered
  `f32le16`/`u32be16le`/`s32be16le`/`f32be16le`, the MCCI minifloats, and the encodings,
  which were Python-only before 0.5.2. A bit range's base is read in the field's effective
  byte order (PS-059) and `sN[...]` sign-extends from the range's width; a byte_group's
  bits refer to the group's assembled value, in the group's own `endian` (PS-364).
- **Wave 5 (CR-2026-050, -060, -061).** A mapping lookup's `default` may carry `${value}`
  (PS-406 to PS-409) in Python, Go, Java, C# and the TS013 decoder; one helper per language
  (`go/schema/wave5.go`, Java `Wave5`, C# `Wave5`, `format_lookup_value` in
  `schema_interpreter.py`). The value is written as **JavaScript's `String()`** writes it -
  fixed notation for 1e-6 <= |v| < 1e21, `1e-7` / `1.5e+21` outside - because the generated
  codec is one of the five paths; Python's `repr` and Go's `FormatFloat` differ from it at
  both ends. A lookup value with a fraction matches no key: Go's `toInt`, Java's
  `intValue()` and C#'s `ToInt` all truncated 2.5 to key 2, unseen because no corpus vector
  reaches it. The generated `encodeDownlink` reverses lookups, `${value}` defaults
  included, since its encoder became the runtime port described under "The generated
  codec's encoder" below. C has no lookup default of any kind.
  `metadata.timestamps` gains `epoch`, `iso8601`/`unix_epoch` reporting rules and the
  `calendar` mode (PS-354 to PS-356, PS-410 to PS-413) in Python only, which PS-310
  permits; `metadata-epoch-calendar.yaml` holds the other four to decoding a schema that
  carries them. `unix_epoch` now reports a number, not a string, and `format:` is refused.
- **Wave 6a (CR-2026-048, -053, -054 annotations, -055, -080, -081).** A repeat element
  is decoded like any field list - Python's `_decode_field_list` serves the top level and
  every element, Go, Java and C# each have one element function - so an element holds
  computed fields, internal fields and constructs, in a scope of its own (PS-368). An
  element's names are restored away after it; a reference to one from after the array,
  or to a field of the element not yet decoded, is an error. Every other unbound
  reference from a `ref` or a `compute` operand makes its field absent in all five
  (PS-314, PS-429; see the registry divergences below); a guard condition on one fails
  in all five (PS-540, CR-2026-117 below). `index`, `count_as` (after `present_if`, CR-2026-080), `present_if` and
  `carry` follow; `reserve` on a repeat and on a `tlv` is a buffer cut at the region's
  end with absolute positions, so nothing inside can read the trailer, and a repeat's
  `trailer` decodes first into the enclosing scope. Schema rules are rejected at load by
  all four and by the validator and generator (`iterator_errors` in Python, `wave6a.go`,
  Java `Wave6a`, C# `Wave6a`). PS-375 (`_meta`) waits for the `_meta` work and PS-377
  (per-element IPSO/SenML output) for CR-2026-078. A `present_if` repeat over byte-reading
  elements cannot be encoded (PS-387); `encode-round-trip.py` classifies it
  `dropped-element`. Open, found in the ports' review of the reference: a `present_if`
  whose `field` lacks `$` loads and keeps every element; `var:` names and `count: $name`
  are outside the PS-368 check; Java runs a zero-progress `until: end` loop to `max` where
  the others stop.
- **Wave 6b (CR-2026-056, -059, -062, -065, -067).** A `length` may name a preceding
  field; `optional` trailing fields; `match: {remaining: true}`; `sentinel` and
  `out_of_range: omit`; internal-field rules. All in Python, Go, Java, C# and the TS013
  decoder (`wave6b.go`, Java and C# `Wave6b`). A match case body now goes through the
  field-list decoder too, so it has computed fields and every per-field rule - and its
  `_quality` belongs to the decode, as Go and C# already kept it.
  **PS-434 changed encoding**: an internal field that reads bytes writes its `value`, else
  the input's, else it is an error naming the field. Encoders wrote its `default` or 0 -
  and Python alone inferred a match's internal discriminator from the case the data fit;
  that is gone pending the spec's answer (internal-discriminator.yaml no longer
  round-tripped; CR-2026-134's PS-551 answered, and all five derive it now). laq4's reserved bit and encode-padding's reserved byte declare `value: 0`.
  Two vobo vectors (`standard_zero`, `heartbeat2_zero`) round-tripped only because their
  internal byte halves were all zero, so the tlv floors dropped by 2 in every language,
  deliberately. A sentinel is compared on the bits before an `encoding` and written back
  as raw bits.
- **CR-2026-071/073: one arithmetic pipeline, one operation per stage.** Every numeric field,
  computed or read, is source -> mult/div/add -> `transform` -> lookup (PS-443); a failed
  guard's `else` is reported as declared, skipping all of it (PS-444). The load-time checks
  are one function per language - `arithmetic_schema_errors` in Python (it walks *every*
  field mapping, typed or not: a guard beside a `byte_group` is as invalid as one on a
  `u8`), `checkFieldRules` in Go, `checkArithmetic`/`parseField` in Java, `CheckGuard`/
  `CheckStage` in C# - and reject a guard off a computed field (PS-445) and a stage holding
  other than one operation (PS-452; `decimals`/`ties` belong to an `op:` stage). Fixtures:
  `arithmetic-order-computed`, `arithmetic-order-read`, `guard-else-as-declared`. What it
  found, beyond the CRs' own table: Go encode dropped a bare `div` beside a `transform`
  (`0041` for `028a`) and undid `log10` as the identity; Java reported no `_quality` for
  `valid_range` at all (now does, PS-131); Go, Java and C# all looked up a failed guard's
  `else`; C# still parsed and applied `sub:`; C applied the lookup before `add`, and its
  f16/f32/f64 paths applied no modifiers. A computed value reaching a *sequence* lookup
  with a fraction is an error (PS-105), not a truncated index. The dead `ModOrder` fields
  are gone from Go, Java and C#.
  **C and PS-446:** `tools/schema_binary.py` is the one host path that feeds
  `schema_load_binary`, and it now refuses every property C cannot apply - transform,
  polynomial, compute, guard, encoding, sentinel, `out_of_range`, a lookup `default`, scale
  factors and adds the format cannot reproduce, types it would have turned into `u8` -
  rather than emitting a blob that decodes without it. `binary_schema.py`, `_v2.py`,
  `generate-c.py` and `generate_firmware_codec.py` feed no C reader and still drop such
  properties silently; `generate_codec.py` crashes on every schema.
  Open: Python's `_decode_enum` ignores `mult`/`div`/`add` on an enum (C now applies them;
  no corpus vector has one); Go's `GuardDef.Else` is a `float64`, so a non-numeric `else`
  is treated as none; Go alone still applies a legacy `modifiers:` array, a key the
  specification does not define (left to the closed-vocabulary CR); Java reports a
  byte_group bit-range member as a Double.
- **CR-2026-085/086/087 (2026-10-09).** Bytes after the last field are reported with
  their offset and count - one warning, and none beside a PS-302 one for the same bytes
  (PS-472) - in all five and in the generated codec's warnings[] (PS-473); C gives the
  count as `bytes_unread`. A tlv entry with fewer bytes than its tag is an error naming
  the tlv in every implementation (PS-477): Python's plain `tag_size` path, Go and the
  generated codec stopped silently. CR-2026-093 extends it to the rest of the entry (see
  below). **`valid_range` compares before the lookup** (PS-475):
  compared after it, every looked-up value read "good" and `out_of_range: omit` never
  dropped one; omitted, a value is not looked up, so an index its table lacks is no error.
  A failed guard's `else` is not compared. Go, Java and C# read a `tag_fields` component
  as `length` bytes defaulting to 1, so a `u16` component was read as one byte and written
  as two; every implementation now takes the type's width. The output schema declares
  `_warnings` for every schema, since any payload can carry leftover bytes.
  SenML (PS-478/479) is produced by Python and C# only: one record per reported field
  wherever declared, `senml.name`/`senml.unit` else the reported name and `unit`, object
  members as `object/member`. Python had ignored `senml:` and walked top-level fields only;
  C# found no definition inside a port entry or a construct. Fixtures: `leftover-bytes`,
  `range-before-lookup` (its field was first named `flagged`, which three encode harnesses'
  text scan filed under that shape - keep construct names out of fixture field names too),
  and `match-default-skip` now expects the PS-472 warning. PS-118 is withdrawn (PS-102).
- **CR-2026-093 (PS-486): a tlv entry is never partly read.** Where an entry declares a
  length, a length cut short, or a length declaring more bytes than remain (less any
  `reserve`), is an error naming the tlv and the entry's offset, for a known tag and for an
  unknown one that would be skipped. All six implementations give the same text:
  `tlv entry at offset {p}: {n} byte(s) remain after its tag, fewer than its {w}-byte
  length (PS-486)` and `tlv entry at offset {p}: its length declares {n} byte(s), {r}
  remain (PS-486)`; C prefixes its tlv's name as it does for PS-477. Before it, a cut
  length rewound to the tag and warned under PS-472, and **no implementation checked a
  declared length against the payload on the known-tag path**: a case reading `length:
  remaining` took the two bytes present as the whole of five. An unknown tag split three
  ways: Python and TS013 stepped past the payload's end (10 bytes consumed of 7), Go read
  the tail as a new entry (a second "unknown tag (0xAA), 187 bytes" warning), and Java and
  C# failed with a bare underflow. No corpus vector reaches either cut (484 vectors over
  the 8 schemas with a `length_size`, probed), so no floor moves. C's `error_msg` grew from
  112 to 152 bytes to hold the length message's worst case, since the build treats a
  possible truncation as an error.
- **CR-2026-104: a lookup label may be a boolean** (PS-106), never with a `${value}` default
  (PS-407), and **a label matches only an input of its own type** (PS-513): `true` is not 1,
  and neither `"true"` nor `"5"` is a label of `true` or 5. One helper per language -
  `same_label` in `schema_interpreter.py`, `sameLabel` in `go/schema/cr104.go`,
  `Schema.sameLabel` in Java, `Helpers.SameLabel` in C#, and `===` in the generated
  codec's `encReverseLookup`. **Python's `True == 1` and `hash(True) == hash(1)` are the
  trap**: `list.index`, `==` and a dict keyed by label all conflate them, which is how the
  reference wrote a default-only `true` as 1. A boolean with no key of its own is refused on
  encode in all five (PS-409). Measured before: Go, Java and C# kept labels as strings -
  **Go dropped a number or boolean mapping silently** (its `map[int]string`; Netvox r718n3's
  `{1: 1, 2: 5, ...}` multiplier was blocked on it), Java and C# reported `"true"` and `"5"`
  as text - and Java, C# and TS013 wrote `false` through `[true, false]` as index 0. C holds
  a label as text, so `schema_binary.py` refuses a number or boolean label (PS-446) and the C
  harness skips them as a C gap. Fixtures: `lookup-boolean-labels`, `lookup-number-labels`.
- **CR-2026-094: SenML output takes primary units** (PS-478 amended, PS-487, PS-488).
  Python's `get_semantic_output(..., "senml")` and C#'s `SemanticFormatter.ToSenML` are
  the only SenML producers. A record's `u` is a registered SenML unit: a secondary unit is
  re-expressed in its primary unit by the registry's scale and offset, in decimal (3284 mV
  is 3.284 V, 90 /100 is 0.9 /); `%` is written `/`; an unregistered unit is left out with
  a warning, `"{path}: unit '{u}' is not a registered SenML unit, so its record has no 'u'
  (PS-488)"`. The return values are unchanged, because td-tools reads Python's: the
  warnings go to `interpreter.semantic_warnings` (reset per call) and to C#'s optional
  `warnings` list. The tables (`SENML_UNITS`/`SENML_SECONDARY_UNITS`, C# `SenmlUnits`) are
  the IANA registry as updated 2026-02-02 - 67 units, 39 secondary - checked against its
  XML, not copied by hand. Measured on the corpus: 70 device-schema and 39 library fields
  declare a unit the registry lacks (`d`, `kPa`, `mg/m³`, `°C`, `bar`, ...) and now carry no
  `u` in SenML; 534 are re-expressed, most often `/100`, `ms`, `ppm`, `mm` and `mV`.
- **`_meta` (CR-2026-096, with CR-2026-088's PS-480/481 and CR-2026-095's PS-489,
  2026-10-09).**
  - **Where:** Python, Go, Java and C# produce it through `interpret()` / `Interpret()`.
    `decode()` is unchanged and never carries it: td-tools reads `decode()`, and a TS013
    codec never carries `_meta` (PS-467). C will produce it on its gateway tier.
  - **Input context (PS-495):** `fPort`, plus `input_metadata` with `recvTime` and `devEUI`.
    A test vector supplies it the same way.
  - **One rule decides most of the code, PS-490:** an entry comes from the declaration that
    produced the value. Each language records it where a top-level field is decoded, last
    write wins, never inside an object or repeat (a depth counter), and under the resolved
    key for `name_from`. With nothing recorded, an entry falls back to the first declaration,
    else `{}`. That happens for a Python-only `metadata` block key and a raw tlv's
    `unknown_tags` (PS-499). Nested entries are declared, not produced (PS-500). A tlv with
    `merge: false` reports under the fixed key `channels`, whose entry is `tlv` (PS-493).
  - **Read the declaration as written.** Go, Java and C# all read the field's raw YAML
    mapping, not their typed model, because the typed model had already canonicalised the
    type and dropped `unit`, `ipso` and `senml`. Go also recovers YAML key order from the
    node tree, since a map has none and lookup labels are listed in schema order.
  - **Guard: `make gate-verdicts` rule 3.** Every runner writes `CORPUS_META_REPORT`, and
    every corpus vector's `_meta` must equal Python's exactly in Go, Java and C#. It
    measured 2549 of 2549 identical on landing. `expected_meta` on a vector is compared
    exactly by all four runners; the `meta-*.yaml` fixtures carry it.
  - **Taken from the spec prototype where the text was silent, then written into the
    spec by CR-2026-097:**
    - a malformed devEUI or recvTime is an error and produces no `_meta`;
    - `rx_time` keeps milliseconds, rounded half to even in decimal;
    - `version` only when declared;
    - PS-491 compares ipso with its defaults filled in.
  - **PS-491** (one name's declarations in one field list agree on unit, senml and ipso) is
    checked at load everywhere and rejects no corpus schema. Go, Java and C# report only the
    first violation; Python reports all.
  - **Known gaps, not fixed here:**
    - Java decodes a field with no `type` as `u8` when it carries a construct key whose
      value is malformed (`match: 3`), which slips past the PS-334 check.
    - Go reads a typeless tlv `tag_fields` member, and an enum with no `base`, as one byte.
    - PS-498 (enum `descriptions`, MAY) is not produced.
  - CR-2026-043 (`_meta.fPort`) and the `_meta` parts of CR-2026-054 land with this.
- **A `tlv` with `merge: false` (Clause 4) had three gaps, found by `meta-tlv-channels.yaml`.**
  No device schema uses the construct, so nothing had run it until `_meta` needed its
  `channels` entry.
  - **The generated TS013 codec did not decode it.** `channels` was missing from the codec's
    output, with no error. Each entry is now decoded into its own object and pushed to
    `channels`, and a raw-captured unknown tag goes there too (PS-303).
  - **All five encoders wrote an empty payload for it and reported success.** That was the
    silent write of nothing, in Python, Go, Java, C# and the generated codec alike. Each now
    refuses it by name: "a tlv with merge: false reports its entries under 'channels', and
    encoding them is not supported". Encoding the list is the subject of a CR being drafted;
    `encode-round-trip.py` files the refusal as `separate-tlv`, a recoverable gap.
  - **The output schema did not declare `channels`.** It is now an array of objects, each
    with `tag`, its case's fields, and `raw` under `unknown: raw`.
- **CR-2026-107: `type: boolean` is a literal** (PS-519, PS-520). It reads no bytes and
  writes none, beside the string and number literals (PS-357, PS-359), in Python, Go,
  Java, C# and the generated codec, and is reported as a JSON boolean (PS-279; the
  output schema declares `type: boolean`). It is not an alias of `bool`. A `boolean`
  with no `value` (PS-519), a non-boolean `value` such as `"true"`, `1` or `null`
  (PS-520), and a lookup or modifier on it (PS-358) are refused at load by all four, the
  validator and the TS013 generator. `tools/schema_binary.py` refuses it for C (PS-446)
  and the C harness files it as a C gap. **A YAML 1.1 `yes`/`on` was not detected
  here, and is now (CR-2026-164, PS-575, below)**: PyYAML and SnakeYAML hand the check a
  boolean for it, C# (YAML 1.2) a string it rejects, so the loader refuses the text first
  and no vector can use one. Fixture: `boolean-literal.yaml`.
- **CR-2026-102: `ipso` and `senml` are mappings** (PS-510). `ipso` must be a mapping
  carrying `object`, `senml` a mapping; `ipso: 3303` is not a shorthand and is refused at
  load, with the spec prototype's wording, identical everywhere: `The ipso of '{name}' is a
  mapping that carries ``object``, such as {object: 3303}; a bare object number is not a
  shorthand (PS-510)` / `The senml of '{name}' is a mapping, such as {unit: Cel} (PS-510)`.
  Python `annotation_schema_errors` (called by the interpreter, `validate_schema.py` on the
  expanded schema, and the TS013 generator), Go `checkAnnotations` from `checkFieldRules`,
  Java `checkAnnotations` from `parseFields` plus `checkDefinitionAnnotations`, C#
  `CheckAnnotations` from `ParseFields`. Every field list is covered, and **unreferenced
  `definitions` too**, because Go and C# already parse them. The legacy
  `semantic: {ipso: N}` is a different key and is not checked. No fixture: a rejection
  cannot be a corpus vector, and the corpus already uses all the accepted forms;
  `test_cr_2026_102_ipso_senml_mappings.py` also sweeps the corpus, library and examples.
- **CR-2026-112 (provisional PS-532): a `name_from` key writes what the field reports.**
  Its label where it has a lookup; a number as PS-406's `${value}` (one writer per language,
  `render_template_value` / `renderTemplateValue` / `Wave5.renderTemplateValue` /
  `Wave5.RenderTemplateValue`, over the existing `format_lookup_value`); a string as is; a
  boolean as `true`/`false`. Both resolvers in each language use it, and the generated
  codec needed none: concatenation is ECMAScript's own Number::toString. Measured before:
  Python wrote `True` and `1e-07`, Go `1e-07`, Java `1.0E-7`, C# `True` and **truncated a
  fraction** (`2.5` -> `2`, `1e-7` -> `0`, through `ToInt`). The Python and generated-codec
  encoders reversed the lookup of a field reached through its `var:`, so `reading_${at}`
  over a labelled `side` decoded as `reading_right` and was looked up as `reading_2` on
  encode. A reference to an `object` or `repeat` is refused at load in Go,
  Java and C# where the same field list declares it, and at decode/encode everywhere.
  No corpus schema's template referenced a lookup, boolean or fraction, so no device
  vector changed. `senml.name` templates are not rendered by either SenML producer (Python,
  C#): a record's `n` is the template as written, so PS-532's senml half is open. Fixture:
  `name-from-rendering.yaml`. When this landed every variable table held the label, so the
  template read it from there; CR-2026-109 changed what a reference binds, and the
  resolvers now read a separate table of reported values (next entry).
- **CR-2026-109 (PS-523, PS-524): a reference to a looked-up field binds a number.** A `$`
  reference and a `var` bind the number before the lookup where the lookup reports a text
  or boolean label, or has no entry (the field is omitted, PS-269, but the name stays bound);
  where it reports a number they bind that number, so `lookup: [0, 125, 250, 500]` feeds
  `125` to a `compute`. A sentinel or `out_of_range: omit` leaves the name unbound. A `match`
  compares its case keys with that binding, so a text key there matches nothing, and
  `validate_schema.case_key_type_warnings` warns about one (PS-524's SHOULD). A `name_from`
  template is not a reference: it writes the reported value (PS-532), and a template over a
  lookup miss is an error, `name_from for 'x' references code, which reported no value
  (PS-266, PS-532)`.
  - **CR-2026-132 extends it to an enum and a tag component.** An enum is a lookup by another
    spelling: a reference binds its code, its `default` name included, and a template writes
    its name. A tlv `tag_fields` component binds its name and `var` as any field does,
    through its modifiers and lookup, while the tag is still matched on the value read (so a
    component's sequence lookup out of range is now a PS-105 error). Measured before on 17
    probes: every implementation bound an enum's name (Go, Java and C# bound the raw value
    only where no name matched and none was a default) and bound a tag component's raw read,
    so a template over it wrote `reading_2` for `right`. The C interpreter binds an enum's
    code already; it binds every field's raw read, before modifiers and lookup, which is
    PS-523 only where there are neither. Counted before changing it: three schemas in the
    corpus, library, examples and private registry declare an enum (two fixtures and
    `library/sensors/user_input.yaml`) and none references one, so no vector outside the
    fixtures changed. Fixtures: ports 8 and 9 of `lookup-reference-binding.yaml`, and
    `tag-component-reference-binding.yaml`: decode floors +4 in Go, Java and C#, re-encode
    and `match` +1 everywhere (only the enum's mapped vector round-trips).
  - **PS-068 as CR-2026-132 amends it: an enum code with no name and no `default` is omitted**,
    as PS-269 omits a lookup miss, so the field keeps one type; a reference still binds the
    code. Before, Python reported `unknown(N)` and Go, Java, C# and the generated codec the
    raw integer - a string or a number by payload. Go and Java also never read `values`
    written as a list, so every such enum reported its integer; both read it now. Encoding
    an omitted enum takes the missing-field path, as a lookup miss does; `unknown(N)` input
    is still accepted. The output schema now enumerates an enum without a default. No corpus,
    example or registry vector reports an unnamed code, so only port 10 of
    `lookup-reference-binding.yaml` changed (decode floors +1). C already omitted it (see
    `src/test_comprehensive.c` above, whose stale `unknown(N)` assertions the spec now
    contradicts as well).
  - **Measured before, the CR's own table was wrong:** it said Go, Java and C# already bound
    the number. On 81 probes (6 lookup kinds x compute/match/var/template, at the top level,
    in a tlv case and in a match body) **all five bound the label**: a text label failed
    every `compute` (Python `could not convert string`, Go and C# `operand field not found`,
    Java read 0), no numeric case key matched, and a miss left the name unbound
    (`Variable not found`). Python's boolean probes passed only because `True == 1`.
  - **Where each language keeps the two values.** Python: `_bind` and the `_labels` /
    `_lookup_numbers` tables in `schema_interpreter.py`, the number recorded in
    `_apply_modifiers`. Go: `go/schema/cr109.go` (`bindReference`, `rebindReported`,
    `templateValue`). Java: `DecodeContext.bind` / `reported` / `bindMissed`. C#:
    `DecodeContext.Bind` / `Reported` / `BindOutput`. TS013: `_bind_js`, the `lv` and `lbl`
    objects and `refValue()`. Each scopes the reported table with a repeat element, as PS-368
    scopes the variables. **The copy loops that re-bind a construct's merged output** (Go,
    Java and C#, after a byte_group, flagged group, match or `$ref`) would overwrite the
    number with the label; each skips a name its own field already bound.
  - **Encoding.** Every encoder took a `match: {field: $x}` discriminator straight from the
    data, which is the label, and reversed it through the lookup only when reached via a
    `var` - turning a numeric label back into its index. All now reverse a text or boolean
    label and use a numeric one as it is (`_encode_reference`, `encReference`,
    `encodeDiscriminator`, `boundFromReported`, `ReferenceBinding`), and look for the field
    in the port being encoded before the rest of the schema, since two ports may declare one
    name with different tables. Go's encoder had no `$var` discriminator at all before.
  - Fixture `lookup-reference-binding.yaml`, one port per case: decode floors +7 in Go,
    Java and C#, re-encode +5 everywhere and `match` +5 in all four (its miss and plain
    `default` vectors carry no value to write back). No device schema and no
    vector of the private registry changed decode or encode output, and no schema in either
    keys a case by a label. A string case key is matched as nothing rather than refused at
    load, as the spec prototype does; PS-524 asks only for "matches nothing".
  - The generated codec took `default` for a `match` on an unbound name; it now throws, as
    every interpreter does.
- **Registry divergences (2026-10-10).** The private registry's 29,541 vectors, run through
  all five at cd874f1, found six defects the corpus could not see. Each has a fixture in
  `_language-conformance/`:
  - **A tag component's name is a reference** (`tag-component-ref.yaml`). A `ref: $rsn` in
    a case read the 0 every unbound name read in Python and Java, and was "not found" in
    Go and C#; only `var:` was bound. All five bind the name now, and Go binds a
    bit-range component's `var:`, which it skipped. dnt lw-ats's reason, 16 vectors, had
    failed in four implementations - Python "passed" five of them only because their
    expected value was the lookup's miss. CR-2026-116 covers `var:` alone.
  - **A reference with no value makes its field absent** (`reference-to-absent.yaml`,
    PS-314, PS-429). A `ref` or `compute` operand naming a field omitted upstream - a zero
    divisor, a log of 0, a sentinel - read 0 in Python and Java and failed the decode in
    Go and C#. The generated codec already omitted it, through NaN. A reference to a name
    the schema never declares now omits too, silently; no validator check catches that
    typo yet.
  - **`length: 0` reads nothing** (`zero-length.yaml`, PS-392). Go, Java and C# took 0
    for no length and read the type's one byte; each now records whether `length` was
    declared (`LengthDeclared` / `isLengthDeclared`). The C interpreter's skip did the
    same (`size ? size : 1`); the one-byte default for an absent length is the builder's.
  - **Java's and C#'s runners compared numeric-looking text as numbers**
    (`numeric-looking-text.yaml`): `"3E6388"` is Infinity and `"NaN"` NaN, and neither is
    within tolerance of itself. Equal strings now match first, and non-finite text is not
    a number.
  - **The generated codec ignored `name_from` on a `type: object`**, and Python's, Java's,
    C#'s and the codec's encoders read such an object back by its declared name
    (`name-from-object.yaml`). Go's general path already resolved it.
  - **Python's `round` raised InvalidOperation on a large value** (`round-large-value.yaml`):
    `quantize` needs every digit, and 3.4e38 to six places is 45, past the default 28.
  Left open: `elv/elv-bm-trx1` [164] (two tags report one name; four collect into a list,
  the codec keeps the last), Python's computed path ignoring `name_from`, and Python
  writing a huge integral float as a 39-digit integer where JavaScript writes `3.4e+38`.
- **CR-2026-111 (PS-528 to PS-531): `element` and `index_base` on a repeat.**
  - **What it does:** `element`, one field in place of `fields`, reports an array of values.
    Each element is decoded as a one-field record under its own `name`, or `element` where it
    has none, through the existing element function, so `present_if` and `name_from` work
    unchanged. The repeat then reports that record's value. A record with nothing reported
    gives `null` in its position, whatever the reason: a sentinel, a lookup miss, a log of
    x <= 0. **This is the one place a decoded value is `null`.**
  - **One helper per language reads the members:** Python `repeat_members`, Go
    `rawRepeatMembers`, Java `Wave6a.repeatMembers`, C# `SchemaParser.RepeatMembers`. The
    PS-369/372/373/374 checks, the PS-368 name walk and `_meta` all go through it. A check
    that still reads `fields` directly misses the element.
  - **Load-time rules, in the spec prototype's wording:** "Repeat '{name}' declares
    both|neither of \`fields\` and \`element\`; it declares exactly one (PS-083, PS-528)",
    "Repeat '{name}': \`element\` is a single field definition (PS-528)" and
    "Repeat '{name}': \`index_base\` is an integer (PS-530)". They live in Python
    `element_repeat_errors` (reached from `schema_iterator_errors`, so the validator and
    the TS013 generator get them too), Go `elementRepeatError`, Java
    `Wave6a.elementRepeatErrors` and C# `Wave6a`'s `ElementRepeatErrors`.
  - **Encoding:** each value is wrapped back into its record. What a `null` position
    encodes as was left open here and settled by CR-2026-121 (PS-544), below.
  - **`index`** binds the position plus `index_base` on decode and on encode. `count_as` is
    unaffected.
  - **`_meta`:** an element repeat's entry is `{type: repeat, element: {...}, identity}`,
    with the element's name left out. An index listing gains `index_base` after `count`
    when the base is not 0.
  - **Runners:** the Java and C# corpus runners compared a `null` inside an expected array
    by its printed form, so it never matched. Java's `normalizeOutput` also dropped null
    list items. All three are fixed.
  - **Where implementations differ:** Go accepts a whole float such as `index_base: 1.0`,
    because a JSON-authored schema gives it floats; the others reject it.
  - **C:** has no repeat. `schema_binary.py` refuses `element` and `index_base` by name.
  - **Fixtures:** `repeat-element-named`, `-unnamed`, `-null` and `-object`, and
    `repeat-index-base`.
- **CR-2026-118 (PS-541 to PS-543): `records`, and `in` / `not_in`.**
  - **What it does:** a `records` entry in a repeat's element, or in a match case within it,
    is a non-empty list of field lists. Each one decodes in order, in the element's scope,
    and yields one record. The repeat reports the records as consecutive elements in place
    of the element. The element's other reported fields go into each record first, so `_`
    fields are copied nowhere. A record field sharing a name with a field the element
    reports outside `records` is a load error (CR-2026-124). `present_if`
    drops all of an element's records. `index` counts source elements and `count_as`
    counts the records reported.
  - **Where it is checked:** load-time, in Python `records_errors` and `condition_errors`
    (reached from `schema_iterator_errors`, so the validator and the TS013 generator give
    the same errors), Go `cr118.go`, Java `Wave6a` and C# `Wave6a`. Each gives the same
    wording: "\`records\` belongs in a repeat's element, or a match case within it
    (PS-541)", "\`records\` is a non-empty list of field lists (PS-541)", "Repeat '{name}':
    an \`element\` cannot reach a \`records\` entry (PS-541)", "Repeat '{name}': ['a'] is
    reported both by a record and by the element outside \`records\` (PS-541)" (the
    names sorted, as a Python list), "\`in\` in the present_if of '{name}' is a list of
    numbers (PS-543)" and "a guard condition of '{name}' has 2 comparison operators; a
    condition has exactly one (PS-126)".
  - **CR-2026-124 settled the open questions.** Placement stays as above. An empty `in`
    never holds and an empty `not_in` always does. A boolean is not a number. `_meta` takes
    a name's first declaration. **PS-126 holds for every condition**, so a guard condition
    or `present_if` with no operator, or with two, is rejected at load. Measured before
    enforcing: no condition in `schemas/devices`, `schemas/library`, `examples` or the
    private registry had other than one operator. The clash rule reads the names the
    element reports at its own level: an object contributes its own name, not its
    members'. The spec prototype also counts an object member's name.
  - **Rejected:** `records` in an object's members, a tlv case, a flagged group, a
    byte_group, a trailer or another record, even inside an element. CR-2026-124 wrote
    this into PS-541.
  - **Encoding:** every encoder, the generated codec's included, refuses a repeat whose
    elements can reach `records` (directly or through a match case or default): "Repeat
    '{name}' has elements that yield several records, which cannot be encoded (PS-542)".
    `encode-round-trip.py` files it as `several-records`, which is inherent, like
    `dropped-element`.
  - **`_meta`:** the repeat's `elements` lists every record's fields, keyed by name, in
    declaration order. The PS-491 agreement check also walks record bodies.
  - **Generated codec:** a `rcs` stack holds the current element's records. It is emitted
    only where an element can reach `records`, so no other generated codec changes.
  - **C:** has no repeat. `schema_binary.py` refuses `records` by name, and the harness
    files all four fixtures as C gaps (repeat, guard).
  - **Fixtures:** `element-several-readings`, `element-case-several-readings`,
    `present-if-set` and `guard-set`, 12 vectors, one assertion each. Only `guard-set`
    round-trips, and it carries the empty `in` / `not_in` cases. Floors: decode 2618 ->
    2630 in Go, Java and C# (2632 with CR-2026-117's fixture); re-encode +4 everywhere
    (Python, Java, C# 1794 -> 1798; Go
    1811 -> 1815, plain 1788 -> 1792); `plain fixed` 133 -> 137 in all four.
- **CR-2026-117 (PS-539, PS-540, 2026-10-11).** PS-536's tag component name binding
  landed with PR #93; this adds the other two.
  - **PS-539: a `$` reference must name something declared, and declared first**
    (CR-2026-127 added the order). The two messages are the prototype's, identical in
    all four: "`$batery` (ref) names no field, var, tag component, index or count_as the
    schema declares (PS-539)" and "`$late` (ref) is referenced before the schema declares
    it (PS-539, PS-122)". One walk per language over the **`$ref`-spliced** document, the
    prototype's `check_references`: Python `reference_errors` (interpreter at decode per
    PS-328, validator, TS013 generator, `schema_binary.py` for C's blob), Go
    `references.go`, Java `References`, C# `References.cs`. A definition is checked where
    it is spliced, after the fields before the splice, and an unused one is never checked -
    walking definitions first, unspliced, falsely rejected 565 references in 62 registry
    schemas (watteco's `report_body` reads the `_clu` its port declares). Within a mapping,
    `tag_fields`, `trailer`, `fields` and `element` are walked first, so tag components
    precede cases and elements precede `present_if`/`identity`; the rest in source order,
    ports included, in all four. Go's maps keep no order, so `checkReferences` takes it
    from the YAML node tree (`buildKeyOrder`, as `_meta` does for lookup labels); it used
    to walk sorted keys, scalars before nested values, and ports sorted as strings, which
    accepted a match whose `default` read a name its later `cases` declared and refused
    one whose `cases` declared what a later `field:` read. `ps539_walk_order_test.go` and
    its Python, Java and C# mirrors pin it with pairs differing only in key order. **Each
    port is a decode of its own (CR-2026-130)**: a port's field list sees what the top level
    declares (metadata names, `header`/`fields`) and its own names, never another port's,
    in either source order; such a reference gets the "referenced before" message. All four
    used to carry one port's names into the next. Measured before the change: no corpus,
    library, example or registry schema (6 and 335 with `ports`) refers across ports. Declared: every `name`, `var`, `index`, `count_as` (a `byte_group`/`flagged`
    member and a tag component included) and `metadata` include/timestamp names. **A
    nameless element declares no name**: `$element` needs `element: {name: element, ...}`.
    A name declared earlier in a branch not taken is absent at decode (PS-314), not an
    error. Not checked: `lookup`, `values`, `else`, `value`, a non-match `default`, a
    `metadata.include` `source`. Measured: no device, library or example schema, and none
    of the 506 registry schemas, is rejected. Go, Java and C# report the first violation;
    Python all.
  - **PS-540: a guard or `present_if` condition on an absent value fails**, so `else`
    applies or the field is omitted. Python and Java read 0, so `gte: 0` passed; the
    generated codec compared `undefined !== 3` as true and `null >= 0` as true, and now
    tests `!= null` first, `not_in` (CR-2026-118) included. A repeat's `present_if` is evaluated with its element whole, so
    a name of the element with no value is absent there, not a PS-368 "not yet decoded"
    error. C has no guard. Fixture: `guard-absent-operand.yaml` (decode floors
    2628 -> 2630 in Go, Java and C#; re-encode unchanged, PS-387).
  - Tests that used an undeclared name to mean "absent" now declare it as a trailing
    `optional` field (Go `TestRefFieldNotFound`, Java `Wave6aTest`, C# `CR2026_071_073Tests`).
  - **PS-549 (CR-2026-127): a condition whose operand is a boolean fails**, as an absent
    one does; `true` is not 1 (PS-513). Python and Java compared it as 1/0; Go and C#
    already failed it; the generated codec's operand test is now `!= null && typeof !==
    'boolean'`. A schema testing a flag reads it as a number (a bit range). Fixture:
    `guard-boolean-operand.yaml`: decode floors 2630 -> 2632; both vectors round-trip, so
    re-encode +2 everywhere (Python, Java, C# 1796 -> 1798; Go 1813 -> 1815, plain 1790 ->
    1792) and `plain fixed` 135 -> 137 in all four.
- **CR-2026-121 (PS-544, PS-545): a null element, and what an element may be.**
  - **Encoding a `null`:** an element repeat writes the element's first `sentinel` as raw
    bits, through the existing absent-sentinel path, so modifiers are not reversed onto it.
    With no sentinel every encoder, the generated codec's included, says "Repeat '{name}'
    has null at position {p}, and its element declares no \`sentinel\` to write for it
    (PS-544)". `null-element` in `encode-round-trip.py` is now inherent: a null from a lookup
    miss or a second sentinel re-encodes as the first sentinel, a different byte.
  - **Shape:** "Repeat '{name}': \`element\` is a data field that declares \`type\`, not a
    construct (PS-545)" at load, for an element with no `type` or with `match`,
    `byte_group`, `flagged`, `tlv` or `records`. Go and C# check it before recursing into
    the element, so a typeless one is not reported as "declares no type" first. Go and C#
    report one error, so an element that is a `match` reaching `records` now reads PS-545
    there, where Python lists PS-541 too.
  - **An `_x` element** is decoded under `element` with `var: _x` (unless it declares a
    `var`), in each language's `repeat_members` helper, so its value is reported and `$_x`
    still reads it. Before, every position read `null`.
- **CR-2026-123 (PS-546, PS-547): identity from a sibling repeat.**
  - `identity: $codes[i]`, or an element's `unit` / `ipso.instance` written that way,
    names an element repeat earlier in the same field list, indexed by this repeat's own
    `index`; the sibling's element carries a lookup or is a literal (PS-374). Load-time, in
    Python `sibling_errors` (from `iterator_errors`), Go `cr121.go`, Java and C# `Wave6a`.
  - **Decode** is unchanged, two arrays, except that different lengths are an error naming
    both, also in the generated codec. **Encoding** each array on its own wrote a frame the
    decoder refused, so CR-2026-133 (PS-550) rejects it: "Repeat 'values' is given 1 values
    and 'codes', which identifies them, 2 (PS-550)" (`none` where the sibling is not a list).
    Checked against the data map the repeat is encoded from, before any element is written,
    in Python `_encode_repeat`, Go `encodeRepeat` (via `Field.Siblings`), Java
    `Encoder.encodeRepeat`, C# `SchemaEncoder.EncodeRepeat` (`SiblingNames`) and the
    runtime's `encRepeat` (`encSiblings` mirrors `sibling_references`). An absent repeat
    is rejected as a missing value since CR-2026-134 (PS-551).
    No vector can expect a rejection, so no floor moves; `test_cr_2026_133_*`, Go
    `cr133_test.go`, Java `CR2026133Test`, C# `CR2026_133Tests` hold the text.
  - **`_meta`:** `{"repeat": name, "values": [...], "open": true}` in that key order, the
    PS-375 listing of the sibling's element with `field` replaced by `repeat`. The sibling
    is found through `element_repeats` of the field list the repeat sits in, passed down
    `field_meta` as `siblings`.
  - **A computed count** (`count: $_n` with `_n` a `type: integer` compute) already worked in
    all four, and with `type: number` too; `repeat-count-computed` pins it.
- **CR-2026-126 (PS-548): an IPSO instance is an integer from 0 to 65534.** Checked at load
    through the listing `_meta` would carry: a literal, an index whose range from
    `index_base` fits, or a lookup (own-element or sibling) whose labels are all such
    integers with no `${value}` default. "Field '{name}': its ipso.instance can be other
    than an integer from 0 to 65534 (PS-548)", in Python `ipso_instance_errors` (from
    `schema_iterator_errors`, so the validator and generator too), Go `cr121.go`, Java and C#
    `Wave6a`. It rejects nothing in the corpus, library, examples or the private registry.
    Go reports the first violation in map order, so with several it may name a different
    field than Python; it also accepts an authored `1.0`, which Python rejects.
- **C** has no repeat and no semantic mappings; the harness files the four fixtures under
  "no 'repeat' field type".
- **Fixtures:** `repeat-element-null-sentinel`, `repeat-element-internal-name`,
  `repeat-identity-sibling` (two vectors, one with `expected_meta`) and
  `repeat-count-computed`, five vectors, all round-trip; `repeat-element-null`'s
  reserved-value vector now round-trips too. Floors: decode +5 in Go, Java and C#
  (2646 -> 2651 on top of master at c1b5dbf); re-encode +6 everywhere (Python, Java, C#
  1808 -> 1814; Go 1825 -> 1831, plain 1802 -> 1808); `repeat` shape 19 -> 25 in all four.
- **CR-2026-127 meets PS-546.** PS-539's order walk reads `$codes[i]` as two references,
  `codes` and `i`, in all four (`reference_errors`, `references.go`, Java and C#
  `References`). The index is declared by the repeat itself, so it is always in order; a
  sibling declared after the reference is both a PS-539 forward reference and a PS-546
  error. Python lists both; Go, Java and C# report the first they meet.
- **CR-2026-110 (PS-157 amended, PS-525 to PS-527): `on_repeat` on a tlv.**
  - **What it does:** with `merge: true`, a name more than one entry reports - same tag or
    not - is combined by its mode: `collect` (default, an array in payload order), `last`
    (the last value wins) or `merge` (objects combined member by member; a member reported
    twice is an error naming both, unless the member declares `on_repeat: last`). A field's
    own `on_repeat` beats the tlv's. **Two departures from the CR text, at Jason's
    decision, are implemented:** a tlv's `merge` default reaches its `type: object` fields
    only (the rest keep `collect`), and a merged member may declare `last`.
  - **One helper set per language, mirroring the spec prototype:** Python
    `reported_fields`/`tlv_repeat_modes`/`on_repeat_schema_errors`/`combine_repeated`,
    Go `go/schema/cr110.go`, Java `OnRepeat`, C# `OnRepeat.cs` plus `CombineRepeated` in
    the decoder. The generator calls the Python helpers at generation time and folds each
    entry through `tlvFold`, so the generated codec now collects as the interpreters do -
    it used to keep the last value for every repeated name.
  - **PS-526 is checked at load** in all four and by the validator and generator, in the
    prototype's wording, over every tlv in the document.
  - **A collected value that is itself a list stays one element.** All four appended a
    second report *into* a first value that was a list (a repeat), splicing the elements;
    the decoders now track which names they turned into arrays.
  - **Encoding (PS-527):** a merged object is written one entry per case declaring members
    the input supplies, in case declaration order; a `last` value once, under the first
    case declaring it. CR-2026-125 settled the rest (next entry). The reference used to
    fail inside `int()` on a collected scalar array and zero-fill an object's members, and
    the generated codec wrote one wrong entry. `encode-round-trip.py` files `last` losses
    as `last-value`.
  - **Found on the way:** Go and Java parsed no cases for an inline `tlv:` block declaring
    none of `tag_size`/`tag_fields`/`tag_key` (a one-byte tag is the default), so every
    tag was unknown and the decode succeeded empty; C#'s tlv candidate sort was unstable
    on ties, and now keeps declaration order as the reference does, which round-trips one
    more vobo vector (C#'s encode floor is +2, not +1).
  - **C:** no `on_repeat`; the harness skips such schemas with a named interpreter-gap
    reason, and a vector expecting a PS-157 array, which `decode_result_t` cannot hold.
    `schema_binary.py` refuses the key.
  - **Fixtures:** `tlv-repeat-collect`, `-last`, `-merge`, `-member-last`,
    `-object-collect`. Decode floors +6 everywhere; re-encode +1 (tlv-repeat-merge's
    `object_members_combine`).
- **CR-2026-125 and CR-2026-128 (PS-525, PS-526, PS-527): `on_repeat` encoding and
  placement.** CR-2026-125's "first case declaring it" could not be honoured - a tlv entry
  carries every field its case declares, so it zero-filled or dropped - and CR-2026-128
  replaced it, from this branch's report. In all five encoders:
  - **Each value goes under the first case whose declared fields it supplies in full**: a
    scalar, each element of an array PS-157 collected (`_reports_list` tells a field whose
    own value is a list), a `last` value, a merged object's members. An object element
    supplies exactly the case's members; a merged case is written once the input supplies
    all its members and one of them is still unwritten. A shared `last` member rides, with
    the input's value, in every case written. Nothing is zero-filled or dropped; a value no
    case takes is "tlv: no case takes the value of '{n}' ... in full (PS-502, PS-527)", and a
    supplied member no case declares "tlv: the merged object '{n}' is given ['{m}'], which no
    case declares (PS-527)". `{dr: 5}` gives `0311 05`, `[{adr: 1}, {dr: 5}]` gives
    `0310 01 0311 05`, a scalar follows the same rule.
  - **Two choices, both needed to keep the corpus, which CR-2026-129 adopted into PS-527.**
    A case requiring more fields is tried before declaration order - an object counting
    its members, so a merged {adr, dr} case outranks a {dr} one declared before it
    (`_claim_width`, Go `claimWidth`, Java `claimWidth`, C# `Width`, the generator's
    `width`): em400-mud declares
    [3, 103] (temperature) before [131, 103] (temperature, temperature_alarm), and strict
    declaration order hands temperature to the first and strands the alarm. And a case
    takes no value it cannot write (the old fidelity check, per field): am308's `tvoc` of
    4369 does not fit one case's `div: 100` u16. Without the two, 92 vectors in 20 schemas
    (Milesight, vobo, one fixture) went from round-tripping to an error when measured; with
    them the Python residue is unchanged but for tlv-repeat-object-collect, which now
    round-trips.
  - **The algorithm** is one queue per name and a pending set per merged object, swept
    case by case until nothing moves: `_case_claims` (a flagged group's fields and
    `optional: true` ones do not block a case), then the entries go out in payload order.
    Go has no payload order: it uses `TLVOrder` when given, else ascending tag, and tries
    the cases `TLVOrder` names first, so the tag that wrote a value gets it back.
  - **PS-526 as amended: merged cases may share a member only where it declares `last`**,
    else "tlv: cases share the member '{m}' of the merged object '{o}' without
    `on_repeat: last` (PS-526)" at load, in all four loaders, the validator and the
    generator (via `tlv_repeat_modes`). Measured 2026-10-11: nothing in the corpus or the
    registry is rejected.
  - **`on_repeat` where it has no effect is rejected at load** - anywhere but a tlv, a
    field a tlv case reports, or `last` on a merged object's member - by Python
    (`on_repeat_schema_errors`, so the validator and generator too), Go `checkOnRepeat`,
    Java `OnRepeat.checkPlacement` and C# `CheckOnRepeat`, in the prototype's wording.
    Declarations are compared by identity; `definitions` is skipped and held to the rule
    where `$ref` splices it. Measured 2026-10-10: no schema in the corpus (5 use
    `on_repeat`) or the private registry (none) is rejected.
  - **Fixtures:** `tlv-repeat-collect-first-case` (`3a 01 3a 02` round-trips),
    `tlv-repeat-merge-shared-member` (`dr` declares `last`; `0310 01 05` stays one entry)
    and `tlv-repeat-merge-shared-last` (the shared `last` member in both written cases).
    CR-2026-128 moved decode +1 and re-encode +2 in all four: shared-last, and
    tlv-repeat-object-collect, which now round-trips.
- **CR-2026-115 (PS-535): a read or skip past the end names the field**, in the spec
  prototype's words, identical in Python, Go, Java, C#, the generated codec and C (spec main
  c932961): `Field 'n': Buffer underflow: need 2 bytes at offset 0, but only 1 remaining
  (PS-535)`, `Field an unnamed skip: ...` where there is no name, and `Field 'b': Buffer
  underflow at peek offset 0 (PS-535)` for a bit range or bool (a `consume` past the end
  takes the need form). "Remaining" is to the end less any `reserve`.
  - **The innermost field is named, and nothing prefixes it.** Python's `Error decoding o:
    Error decoding x: Buffer too short...` is gone: the read raises an unnamed underflow,
    the field that made it names it once (`name_underflow`, Go `nameUnderflow`, Java
    `SchemaException.BufferUnderflow`, C# `BufferUnderflowException`, TS013 `_fld`), and
    every enclosing object, repeat, case body, flag group or `$ref` passes it through. An
    enum names itself, not its base; an inline match discriminator is named by the match's
    `name`, else `an unnamed match` (the prototype names nothing there).
  - **A byte_group is named by its members** (CR-2026-131's ruling): `Field byte_group of
    'f', 'w': Buffer underflow at peek offset 0 (PS-535)`, every named member in declaration
    order, `_` internal members included, each single-quoted and separated by `, ` - the
    spec prototype's form (spec main 06a5103) - else `Field an unnamed byte_group: ...`
    where no member has a name (the prototype writes `byte_group of :` there; the ruling
    takes the unnamed form). The group is one read of one value, so the read is the
    group's, always the peek form, and every member is lost - not only the first whose own
    base reaches past the end, which is what all five named until this change, with the
    need form where only the group's `size` did not fit. One helper per language:
    `byte_group_label` (Python; the TS013 generator calls it), Go `byteGroupLabel`, Java
    `BufferUnderflow.byteGroupLabel`, C# `BufferUnderflowException.ByteGroupLabel`, and C's
    `byte_group_underflow` since its byte_group decode path landed in the C-interpreter
    wave.
  - Unchanged, as PS-535 excepts them: `remaining`, PS-403's optional message, PS-477/486
    for a tlv entry, and PS-343's ragged tail for an `until: end` element.
  - Found on the way: Go's `bool` discarded its `consume` read's error, and Python and the
    generated codec never bounded `consume` on a bit range or bool, so both decoded
    successfully past the end.
  - C puts the same text in `error_msg` (`schema_underflow_message`); the worst case is 141
    characters, inside the 152-byte buffer. An unnamed C bit range reads `an unnamed bit
    range`, since the binary schema does not keep the type's spelling. C's `consume` is a
    flag advancing one byte, so a `consume: 2` past the end cannot arise there - and the
    harness turns any `consume >= 1` into that flag, which would build such a schema
    advancing one byte instead of two. No corpus vector reaches it; open.
  - No vector kind expects a failure, so the messages are pinned by unit tests in each
    language: `test_cr_2026_114_115.py`, `test_cr_2026_114_115_ts013.py`,
    `cr114_115_test.go`, `CR2026114115Test.java`, `CR2026_114_115Tests.cs`, and
    `selftest_schema.c`.
- **CR-2026-114 (PS-534, PS-537, PS-538): `ascii` is ISO 8859-1, padded with 0x00.**
  - Decode keeps every byte as the character of the same number and drops trailing 0x00
    only, warning `Field '<name>': a byte above 0x7F in an ascii value (PS-073, PS-538)`.
    Before it: U+FFFD in Python and Java, `?` in C#, raw bytes (invalid JSON text) in Go;
    the generated codec was already Latin-1 and only gained the warning.
  - Encode rejects `Field '<name>': an ascii value holds a character above U+00FF, which has
    no byte (PS-537)` and `Field '<name>': an ascii value of {n} bytes is longer than its
    length of {length} (PS-537)`, writes U+0080..U+00FF as its byte with `Field '<name>': a
    character above U+007F in an ascii value (PS-537)` in the encode warnings, and pads to
    a fixed `length`. A length that is not fixed (`remaining`, a reference) is neither padded
    nor bounded. All five wrote UTF-8 and truncated, so `"ABCé"` became `414243c3`. The
    rejections reach `errors` verbatim, with no `Error encoding x:` prefix.
  - One helper pair per language: `decode_ascii`/`encode_ascii` (Python), `decodeASCII`/
    `encodeASCII` (Go, `cr114_115.go`), Java `Encoder.encodeAscii` and the ascii case of
    `Schema.decodeFieldBody`, C# `Helpers.DecodeAscii`/`EncodeAscii`, TS013 `encField` in
    `ENCODER_RUNTIME_JS`.
  - **C has it too, since the C-interpreter wave** (see "The C interpreter is measured
    now" under Current gaps): `decoded_field_t.text_len` carries the length, so an interior
    0x00 is kept, and `non_ascii` is the PS-073 warning as a flag. The harness no longer
    skips `ascii-latin1.yaml`; it passes all four vectors, warnings included.
  - No corpus vector relied on U+FFFD, `?`, raw bytes or truncation: every implementation's
    counts rose by exactly the new fixture's four vectors. Fixture: `ascii-latin1.yaml`
    (high bytes, interior 0x00, all 0x00, each round-tripping).
- **CR-2026-131: a byte_group covers its widest member's base** (PS-070). With no `size`, a
  group is as wide as its widest member's base type, whichever bytes the bits fall in -
  `u16[0:3]` makes it two bytes. `size` widens it, and a `size` narrower than a member is
  **rejected at load**: `byte_group declares size 5, narrower than its widest member's 8
  bytes (PS-070)`, the spec prototype's text. A member is a bit range, a whole integer
  (`u16` is `u16[0:15]` of the group's value) or a `bool` with `bit` 0 to 7 (one bit of a
  `u8`); anything else is rejected, and every member reads from the group's start without
  moving it. All five read a sizeless group as one byte while the four encoders already
  widened a bit range's base, so decode and encode disagreed; Go and C# also gave a whole
  integer member the entire group's value, and Python and Java read it as a field of its
  own. Where the rules live: `byte_group_member_range`/`byte_group_width`/`byte_group_errors`
  (Python; the validator and the TS013 generator call them), `go/schema/cr131.go`
  (`checkByteGroup` from `checkFieldRules`), Java `Schema.checkByteGroup`, C#
  `SchemaParser.CheckByteGroup` and `Cr131`, and `encGroupWidth` in the generated codec's
  runtime. **Overlap is now counted in the group's assembled value** (PS-364) in all four;
  it was counted most-significant-first within each member's own base, which disagreed with
  every decoder for members of different widths, and C# skipped signed ranges entirely. C
  checks the same, in `byte_group_width`, since its byte_group path landed in the
  C-interpreter wave. **No corpus,
  library, example or registry schema is rejected or changes decode** (registry yabby-edge
  was rewritten as one 8-byte group, 69e1023). Fixture: `byte-group-width.yaml`.
- **A byte_group member's `var:` binds like any field's (PS-523, PS-539).** The Python
  reference (`_decode_byte_group_members` bound `[name]` only) and the generated TS013 codec
  (`_bind_js(..., False)`, and the bool member bound `vars.name` alone) never bound it, so a
  `ref`, a `compute` operand or a `match` selector reading it was absent or failed with "Match
  field $vb has no value" while the member's own name worked. Measured before on
  `byte-group-member-var.yaml` (a group at the top level, a flagged member, a group in a tlv
  case): Go, C# and C already bound it (C through `var_name`; `make selftest` covers it);
  Python and the generated codec failed 9 of 11 vectors. Java alone dropped a member with a
  `var` and no name (reserved bits a later field reads) before binding; it binds now, and that
  case has no round-trip vector (no value to write), so it is `ByteGroupMemberVarTest` and
  `tests/test_byte_group_member_var.py`. Encode was unaffected: the selector is derived from
  the member (PS-551) in all five. Floors: decode +11 in Go, Java and C#; re-encode +11
  everywhere (Python, Java 2153 -> 2164; C# 2154 -> 2165; Go 2171 -> 2182, plain 2143 -> 2154),
  all `tlv`.
- **CR-2026-142 (PS-555): the binary schema form carries its names.** `schema_load_binary`
  used to name each field from its IPSO object (`ipso_to_name`: 3303 was "temperature",
  anything unlisted `field_xxxx`), so a schema reported different keys from its blob than
  from its YAML. Format version 3 (`tools/schema_binary.py`, the header's "Binary Schema
  Loading" block): byte 2 is the *format* version and anything else is refused (1 held
  the schema's own `version`; 2 is `binary_schema_v2.py`'s unrelated layout under the same
  magic); count bit 7 announces a name table of one length byte plus UTF-8 per record,
  0 only for a skip; `skip` moved to type code 0x7, so it has a record at all. Without the
  table (`--no-names`, `encode_schema(names=False)`) the C loader sets `schema_t.nameless`,
  every name stays empty, values are reached with `result_get_index()` by schema index,
  and `schema_ffi_result_to_json()` refuses. `SCHEMA_MAX_NAME_LEN` (32, so 31 bytes) is a load error
  naming the field, never a truncation; the encoder refuses the same, and a count over
  127. `schema_load_binary_msg()` gives the reason for any refusal. `make test-c`'s
  harness runs either form with `--from-blob names|nameless` and requires the reference's
  keys exactly. `schema_interpreter.hpp` and `binary_schema_loader.py` lost their own IPSO
  name tables the same way; `binary_schema.py` and `binary_schema_v2.py` feed no C reader
  and were left as they were.
- Generators other than TS013 (`generate_js_decoder.py`, `generate_firmware_codec.py`,
  `binary_schema.py`, `schema_binary.py`) still carry pre-0.5.2 spellings such as
  `float`/`double`; they are not conformance paths and were not brought along.

- **CR-2026-138 (PS-553): a field has a name; padding is a skip.** A schema with a
  nameless field other than a `skip` is refused at load, in the spec prototype's text:
  `Schema 't': field 1, a u8, has no name; a field that reads or reports a value is named,
  and padding is a `skip` (PS-553)` - the position is 1-based within its list, the type
  is as written (`a field` where there is none), and an object's or repeat's members add
  `, in 'o'`; a port adds `, port N`. Exempt: constructs (`match`, `byte_group`, `flagged`, `tlv`, `records`), a byte_group member (reserved
  bits), a repeat's `element`, and the withdrawn `object:` key, which PS-466 names.
  - **Where:** Python `field_name_errors`, run by `schema_iterator_errors`, so the
    interpreter, the validator and the TS013 generator all refuse it; Go `cr138.go`,
    Java `FieldNames`, C# `FieldNames.cs`, each over the raw document after its `$ref`s
    are spliced. Go and Java visit ports and case keys in sorted order, Python and C# in
    source order; that only changes which error comes first when there are several.
  - **What it replaced, measured before:** Python decoded such a field under the key
    `unknown`, Go, Java, C# and the generated codec read its bytes
    and dropped them, every encoder wrote it as zero padding, and C's struct API decoded
    it as the others did and refused to encode it (`Missing required field: ` with an
    empty name). No decoder failed to read it, so nothing shifted. The `unknown` key
    and every zero-padding branch are gone, Python's nameless byte_group member with
    them: it too was reported as `unknown`.
  - **C:** the binary format carries no names - each field is a 2-byte IPSO object id
    and `schema_load_binary` invents its name with `ipso_to_name` - so C cannot check
    this. `tools/schema_binary.py` refuses the YAML before conversion. A recorded C gap.
  - Counted before: 0 such fields in the corpus, the library, the examples and the
    private registry, so no floor moves. Cases: `tests/cr-2026-138-cases.yaml`, read by
    all four.
- **CR-2026-139 (PS-554, PS-551 amended): a field's `default` is written where the input
  omits it.** A declared `default` is a defined absence. It is in the field's reported
  form (after modifiers, `transform` and lookup) and is encoded exactly as that value
  given as input: `{div: 10, default: 2.5}` writes `0x19`, `{lookup: {1: eco, 2: boost},
  default: boost}` writes `0x02`, and one that cannot be encoded is rejected as such input
  is (`Field 'x': 300 encodes as 300, outside 0 to 255 (PS-168)`). A decoder ignores it.
  An enum's `default` is its unmapped label (PS-068) and is never written, so an omitted
  enum is still `Missing required field`. This reverses #116, which rejected a declared default as a missing value.
  - **Where:** one predicate per language - Python `field_default`, Go `fieldDefault`
    (`cr134.go`), Java `Wave6b.hasFieldDefault`, C# `SchemaEncoder.HasFieldDefault`, the
    runtime's `encHasDefault` - consulted at every missing-input site: a field list, the
    top level, a byte_group member, a bit run, an internal field (before PS-434's error),
    and Python's `encode_command` and the generated `encodeCommand`. Go, Java and C# read
    the key from the declaration as written, since their typed models keep only an enum's.
    The default comes after `optional` (an omitted optional field stays omitted) and before
    a `sentinel`; a CR-2026-134 derivation, which runs first, wins over it.
  - **C** has no field default (`field_def_t` has no slot), and `tools/schema_binary.py`
    already refused every `default`; its reason now says so.
  - Counted before: 0 field-level non-enum defaults in the corpus, the library, the
    examples and the private registry, so no vector and no floor moves. Cases: the
    CR-2026-139 section of `tests/cr-2026-134-cases.yaml`, read by all five encoders.
- **CR-2026-134 (PS-551): an encoder rejects missing input and derives what the input
  fixes.** Every encoder used to write zeros for a required value its input omitted (a
  `Missing field: x` warning at the top level, silence inside a construct), and for a few
  shapes wrote nothing at all, so every later field shifted (PS-442). Now:
  - **Rejected, naming the field, with no payload.** The prototype's text, `Missing required
    field: {name}` (the resolved key for `name_from`), from Python, Go, Java, C#, the TS013
    runtime, the generated `encodeCommand`, Python's `encode_command`, and C's
    `encode_result_t.error_msg` (new; 160 bytes since CR-2026-135 also writes PS-168 there). It covers a scalar at any depth, `ascii`,
    `hex`, `bytes`, `bitfield_string`, a whole `type: object`, a member of a flagged group the
    input gives, a byte_group or bit-range member, and a repeat whose list is absent (a
    framed one wrote nothing; an `until: end` one with a trailer returned before the
    trailer). Python, Java and C# returned the bytes written before an error; every encoder
    now returns none, as Go always did. Not required: an optional field (PS-405), a field declaring `default` (PS-554, CR-2026-139, next entry above), a literal
    (PS-359), a field with `value` (PS-360), an internal field (PS-434, which keeps its own
    message), a skip, a nameless field (padding, as a skip is - since CR-2026-138 a schema with one is refused at load), and an absent value with a
    `sentinel` (written as the sentinel, PS-427).
  - **Derived** where the input omits it, by `_derive_inputs` and its port in each language
    (Go `cr134.go`, Java/C# `deriveInputs`/`DeriveInputs`, runtime `encDerive`), run on every
    field list:
    - a repeat's `count: $n` / `byte_length: $n` field, from the list (byte_length by
      encoding the elements) - the field named by `name:` or `var:`, also inside a
      byte_group. The prototype derives only these;
    - a `match: {field: $d}` discriminator, from the one case whose fields the input gives:
      not where two cases tie, and not for a list or range key. `internal-discriminator.yaml`
      round-trips again in all five, so the inference CR-2026-067 removed "pending the
      spec's answer" is back as PS-551's rule;
    - a named inline (`length:`) discriminator, from the same case, as before;
    - a flagged mask, from the groups given. This one always existed, but overwrote a
      supplied mask; a supplied one is now kept, undeclared bits included (`0x81` with one
      group at bit 0 stays `0x81`, where it was rewritten `0x01`).
    A derived value goes into a field with no arithmetic, lookup, `value` or `name_from`
    between its bytes and what a reference binds, and never into a bit range that no
    `consume` closes - see the peek below.
  - **A disagreement is rejected:** `Repeat '{name}' is given {n} values for a count of {c}
    (PS-551)` for a literal or supplied count (checked after PS-550/542/544/387); two texts
    the prototype states the rule for and gives none: `Repeat '{name}' encodes to {n} bytes for
    a byte_length of {b} (PS-551)` and `Flagged field '{mask}' is given {v}, and the groups
    given set {bits} (PS-551)` (compared on the groups' declared bits only). A supplied
    match discriminator naming a case whose fields are absent fails on those fields.
  - **With CR-2026-109/132's reference binding.** What is derived is the value a
    reference binds, the raw code: an enum takes it as a code, and a looked-up field's own
    reversal turns it into the code for a text or a numeric label alike. A supplied mask is
    checked as a reference binds it, through `_encode_reference` and its ports
    (`encodeDiscriminator`, `boundFromReported`, `ReferenceBinding`, `encReference`): read
    as reported, an enum name or a text label skipped the check, and `{"mask": "one"}` with
    both groups given wrote `01 01 02`. The cases are in `tests/cr-2026-134-cases.yaml`. The
    two together re-encode `lookup-reference-binding`'s `an_unnamed_enum_code_is_omitted_but_bound`,
    which neither did alone: the code reports no name, so the discriminator is derived.
    Decoding a flagged block whose mask is an enum or looked-up field failed with
    `Flagged field reference not found` - fixed since, and the lookup was not the cause
    (next entry).
  - **A flagged mask written `$mask` was never resolved.** Every decoder looked the
    `field:` text up as written, while names bind bare, so `field: $m` failed in Python,
    Go, Java and C# for any mask - plain, looked-up or enum - and the generated codec read
    `vars._m`, never bound, skipped every group and reported success. The corpus spells
    all 75 masks bare, which is why nothing saw it; the spec prototype strips the `$`, and
    every encoder already did. All five now strip it and read the PS-523 binding, so a
    text label's mask is its number, an enum's its code and a numeric label itself; the
    generated codec throws the same error where the mask is unbound. C stripped it
    already. Fixture: `mask-reference-binding.yaml` (decode floors +4 in Go, Java and C#,
    re-encode and `flagged` +4 everywhere).
  - **Also fixed on the way:** Python and the runtime refused a `flagged` block inside a
    match, tlv or repeat ("declares no type"), and every encoder passed over a construct
    nested in a flagged group as nameless, dropping its values silently
    (`flagged-group-field-list.yaml`'s `inner_groups` now round-trips). Python let a
    `bitfield_string` ValueError escape `encode()`.
  - **Left as it was: the non-advancing peek.** r718x, dnt and registry mutelcor, wt303 and wt304 read a
    byte with a bit range that no `consume` closes, then read it again with a nameless inline
    match (`length: 1`, `var:` only). The encoder writes the run's byte *and* would write the
    match's, so it has always written nothing for such a match, and still does when the
    discriminator is unknown and the match has no name or a `skip` default. A named inline
    match with no discriminator is rejected. Writing the peek correctly (the run writes
    nothing, or the match takes its value from it) is open; so is deriving a discriminator
    into a peek, which `_is_peek` declines for the same reason (`match-u32-peek-discriminator`
    and vobo would otherwise gain bytes). CR-2026-136 has since given the peek its layout:
    the run's bytes stay pending and an inline match with no discriminator takes it from
    them, so the byte is written once (see below).
  - **Corpus effect, all five identical:** round trips 1814 -> 1817 (Go 1831 -> 1834, plain
    1808 -> 1811), `match` +2 (internal-discriminator), `flagged` +1 (flagged-group-field-list
    `inner_groups`), no loss. Every other change is a vector that already failed now failing
    by name instead of silently: milesight ct10x/ct30x/ws515/gs301 `*_sensor_status` and dnt
    `value` (decoded as absent, so zero-filled), and the `_language-conformance` absent-value
    fixtures (`value-absent`, `out-of-range-omit`, `range-before-lookup`, `minifloat`,
    `log-of-non-positive`). No fixture expectation changed. `encode-round-trip.py` files an
    otherwise unexplained `Missing required field` under `absent-value`; there are none today.
  - **Registry (Python, read only):** 371 round trips gained (internal discriminators
    derived: radio-bridge's `_event` 180, arwin's `_sensor_type` 84, `_version` 26, ...) and
    25 lost, every one a value the decode omitted - a lookup miss on 0 (dragino
    `SENSOR_MODEL`, terabee's `{1: 1}` flag bits, nwave `reset_cause`, b-meters `vif`, dnt
    `device_orientation`) - that the zero it was filled with happened to reproduce.
  - **Tests:** `tests/cr-2026-134-cases.yaml` is one case list read by
    `test_cr_2026_134_missing_input.py` (reference and runtime), Go `cr134_test.go`, Java
    `CR2026134Test`, C# `CR2026_134Tests`; C's `test_a_missing_input_is_named`.

- **CR-2026-135 (PS-552, PS-262) and CR-2026-136 (PS-064, PS-062): how an encoder rounds,
  what it rejects, and how it lays out bit ranges.**
  - **Rounding.** An integer field's value - a bit range's included - is rounded after its
    arithmetic is inverted to the nearest integer, a tie to even, on the binary64 value
    (`div: 10` given 23.45 is 234.49999999999997, so 234), before any `encoding`; a float
    field is not rounded. Python, the runtime, Java and C# already did this on whole
    integers. Go rounded bit ranges half up (2.5 wrote 3). C rounded `(int64_t)(v ± 0.5)`,
    half away from zero and twice (0.49999999999999994 wrote 1, 23.45 wrote 235); it now
    uses `schema_round_half_even`, which needs no libm. Python's `int()` on the integer
    path truncated a fraction that reached it without modifiers.
  - **A value that rounds outside its type is a PS-168 error**, in the prototype's words:
    `Field 'x': 25.55 encodes as 256, outside 0 to 255 (PS-168)` - the value as given,
    before any arithmetic is undone, written as JavaScript's `String(number)` writes it. A
    bit range's type is its width (`uN`: 0 to 2^w - 1, `sN`: two's complement), and its
    message names it: `... outside 0 to 15 for u8[0:3] (PS-168)`. Before: Go and C wrapped
    silently (u8 256 wrote 00, u8 -1 ff, s8 -128.6 7f); Python and the runtime said `int too
    big to convert`; Java and C# `256 does not fit 1 unsigned bytes`; and all six masked a
    bit range into its width, so 16 in `u8[4:7]` wrote 0 and left the neighbour untouched -
    or, unmasked, spilled into it. The mask (PS-062) now only keeps a signed value's two's
    complement within the width, after the check. One helper per language holds the
    bit-range rule (Python `check_fits(..., bit_range)`, runtime `encFits`, Go
    `bitRangeFitError`, Java `checkBitRange`, C# `CheckBitRange`, C `schema_check_fits`).
  - **Layout: an encoder writes the bytes a decoder reads** (CR-2026-136). A bit range or a
    `bool` that does not `consume` leaves its bytes pending; the next field's bits or bytes
    are ORed into them; bytes still pending at the end of a field list are written. Python's
    `FrameBuffer`, the runtime's `encEmit`/`encPlaceBits`, and each port's equivalent; C ORs
    into its zeroed buffer and counts a non-consuming range's byte in the length. Before, a
    run was written whole and the next field after it, so a peek - a range read again by
    the field after it - came out two bytes where the decoder reads one, and a `bool` was
    written as a whole byte of 0 or 1, ignoring `bit` and `consume`. An inline match with no
    discriminator in the input now takes it from `data[var]` (dnt's presence mask, which the
    top level fills from the flagged groups) or from the bytes pending before it (the peek),
    and a known inline discriminator under `default: skip` writes its bytes.
  - **Frame carry.** A construct's field list (case body, object, flagged members, match
    body) hands back the bytes still pending at its end, and the enclosing list keeps them
    pending: Python's `Frame.pending`, the runtime's `encList` return value, Go's
    `endFieldList`, Java's `carriedTail`, C#'s `CarriedBy`. A tlv value and a repeat element
    carry nothing - their bytes are concatenated or measured whole. A flagged block's given
    groups are now one field list, so a bool, a bit range or an object in a group is laid out
    like any other member (vobo's `digital_*`, dnt's `*_ack_then_hall`).
  - **Corpus:** round trips 1814 -> 2079 in Python, Java and C# (+264 `tlv`: vobo 150,
    dnt-lw-wsci 75, milesight ct10x/ct30x/gs301/ws515 39 - each a peek that now shares its
    byte - and +1 `plain fixed` from the `encoder-rounding` fixture), no loss, the same set in
    all three (C# also counts `am102::encode_vendor_reference`, an encode vector, and still
    misses `vobo::modbus_generic_one_register_blocks`, a C#-only length defect that predates
    both CRs). Go ordered 1831 -> 2097, plain 1808 -> 2070, `tlv` 1316 -> 1581; a superset of
    the reference. Decode floors 2652 -> 2654 for the two fixtures.
    Rounding moved none: no corpus vector puts a tie or an out-of-range value into an
    integer field. `encode-round-trip.py` reports 0 unexplained.
  - **Registry (read only):** Python +333 round trips (Go ordered +362), -2 (truvami tag-s-l, enless: each
    vector carries a trailing byte the schema does not describe, which the old double write
    happened to reproduce). Rounding moved none; 14 vectors that already failed now fail
    with a PS-168 error naming a decoded value its field cannot hold (yabby-edge
    `initialBatV`, r718b2 `Channel`, nexelec `datalogMeasureRepetition`, loraboatmonitor
    `temperature`, teneo `distance`, tektelic `voltage`) - schema findings, not encoder ones.
    C's ties (fludia fm432ir x5, netvox r718ea_eb, nexelec origin-plus) are not run by any
    C corpus encode harness.
  - **PS-262** now warns only when `mult`, `div` and `add` are written out of the canonical
    order; `div: 10` then `add: -40`, the pattern Clause 3 recommends, no longer warns.
  - **Fixtures:** `arithmetic-worked-examples.yaml` (Clause 3's table, decoded) and
    `encoder-rounding.yaml` (its encode rows, as encode vectors, which only Python and the
    runtime check); the case list `tests/cr-2026-135-cases.yaml` (rounding, PS-168, and
    the prototype's TestCR2026136BitRangesOnEncode case for case) drives
    `test_cr_2026_135_encoder_rounding.py`, Go `cr135_test.go`, Java `CR2026135Test`, C#
    `CR2026_135Tests`; C has `test_an_encoder_rounds_ties_to_even`,
    `test_a_value_that_does_not_fit_is_named` and `test_bit_ranges_are_laid_out_as_decoded`.
  - **Open:** C's bit range is one byte, unsigned; an `sN[...]` or a multi-byte base is not
    built by its struct API. An enum given a number and `udec`/`sdec` keep their own range
    checks. A byte_group's whole-integer member (PS-070) is checked against its type's range
    like a bit range against its width, in Python, the runtime, Go, Java and C#.
  - **Merged with CR-2026-134 (PS-551).** Order of checks: a missing value is rejected
    first, then the value is rounded, then the PS-168 range check runs; a derived count,
    discriminator or mask goes through the same check. The pending-byte writer still
    rejects a missing bit-range, bool or byte_group member by name, and a flagged block's
    given groups - one field list since CR-2026-136 - pass through `_derive_inputs` and its
    ports like any other list, so the mask is derived, kept when supplied, and checked. A
    group holding a nameless construct (a nested flagged block, a byte_group) joins the list
    whole. Neither case file changed: every case in `tests/cr-2026-134-cases.yaml` and
    `tests/cr-2026-135-cases.yaml` passes in every language as written. **Corpus: -26 deliberately
    (Go -27, plain -25).** Of the 39 milesight vectors CR-2026-136 gained, 26 (ct10x 12,
    ct30x 6, gs301 6, ws515 2) re-encoded only because a status peek the decode omitted -
    a `u16[0:15]` whose lookup maps only 0xFFFF, so an ordinary reading omits it (PS-269) -
    was left as zero bits under the reading. PS-551 makes that status a required value the
    input does not give, and the case cannot be chosen (`no case takes the value of
    'current' 100 in full`). It is not derivable: a peek's bytes are written by the field
    after it (`_is_peek`). Go also loses gs301 `h2s_v12_u16_word`, which only its ordered
    API had recovered. Floors: Python and Java 2106 -> 2080 (`tlv` 1569 -> 1543), C# 2107 ->
    2081 (`tlv` 1570 -> 1544), Go 2124 -> 2097 (`tlv` 1587 -> 1560, plain 2097 -> 2072). The
    combination re-encodes nothing that neither side did alone. **CR-2026-141 reversed the
    "not derivable" half of this** (next item).

- **CR-2026-141 (PS-551 editorial): a peek whose bits other fields write is derived.** The
  spec now names a second derivable case beside a repeat's count: a bit range or `bool`
  that does not consume (PS-060, PS-066), whose bits the fields after it write. All five
  encoders, the TS013 runtime and C:
  - **Absent and covered:** derived, and writes no bits. Milesight's status `u16[0:15]`
    over its reading, absent wherever the lookup misses (PS-269), encodes as the reading:
    `{reading: 300}` is `012c` and decodes back to `{reading: 300}`.
  - **Absent and not covered:** `Missing required field: {name}`, where any of its bits
    is written by no other field (`[u8[0:3] a, u8[4:7] b consume 1]` given only `b`).
  - **Supplied and disagreeing:** `Field '{name}' is given {raw}, but the fields that
    share its bits write {found} (PS-551)`, `raw` and `found` the range's unsigned bits.
    Checked against the finished frame, and before the OR where a whole field would write
    over a peek's different bits; where no named peek explains the OR, `A field writes
    bits a peek before it set otherwise (PS-551)`. The OR used to change both values.
  - **How:** each frame carries, per byte, the bits some field wrote and every named bit
    range or bool placed (Python `Frame.covered`/`Frame.peeks`, `FrameBuffer.cover`,
    `check_peeks`; runtime `unc`/`pk`, `encCheckPeeks`; Go per absolute position in the
    encode context, with `beginFieldList` setting the enclosing list's bits aside; Java
    a side table beside `carriedTail`; C# beside `CarriedBy`; C per byte in
    `schema_encode_direction`, each field encoded into a scratch buffer first). The check
    runs where a frame closes: top-level encode, each tlv value, each repeat element and
    trailer. A nameless range is padding and is not checked.
  - **Two rules this repository adds where the prototype is silent, both raised with the
    spec session:**
    - *A skip is padding* (`padding()`): its zeros fix no peek's bits and contradict none.
      The prototype writes a skip like any field, so it would reject dnt-lw-wsci's status
      report (a `skip` moves past the `first_command` peek) and registry mutelcor,
      moko lw001-bg and nexelec sign-codec, which move past peeked bits the same way.
    - *A tlv case is chosen without its peek only where the case's own next field writes
      the peek's bits* (`covered_peeks`, read from the schema). Treating every
      non-consuming range as optional chose registry wt303's case 5 (status nibble beside
      a mode nibble) for a `mode` that case 0x68 reports alone, and the status was then
      missing.
  - **Changed case:** `tests/cr-2026-135-cases.yaml`'s `peeked_range_then_whole_byte`
    gave `whole: 5` and expected `35`, the OR - which reads back as `whole: 53`. It gives
    53 now; the old input is a rejection case in `tests/cr-2026-141-cases.yaml`.
  - **Corpus:** +26 (ct10x 12, ct30x 6, gs301 6, ws515 2; Go ordered also gs301
    `h2s_v12_u16_word` and `sensor_id_43_bytes`, Go plain 4 of the 6 gs301) and -10: vobo `mutant_p32_03..07` and `mutant_p112_03..07`, whose
    lookup gives two codes one label (109 and 45 are both `AnalogSensor45`), so the label
    reverses to 45 and the old OR with the peek's 109 happened to rebuild the byte. All
    `tlv`. Floors: Python and Java 2080 -> 2096 (`tlv` 1543 -> 1559), C# 2081 -> 2097
    (`tlv` 1544 -> 1560), Go ordered 2097 -> 2114 (`tlv` 1560 -> 1577), plain 2072 -> 2087. The
    failing sets of Python, Java and C# are identical (590); Go ordered fails a subset.
  - **Registry (Python, read only):** 4988 -> 4990 round trips: +8 (em2101 4,
    iq-opentherm-lrw 4, covered peeks now derived) and -6, each a frame the OR had
    rebuilt by accident. pm-io-5-sm (4): `valv` and `leak` peek the byte a derived
    `_flags` mask then writes whole as 0. mutelcor (2): `_di`, a whole-byte peek given its
    derived flagged mask 15, beside bools the groups place in bits 4-7 of the same byte.
    Both are schema findings: a mask derived from its groups' declared bits says nothing
    of the other bits. 172 that already failed fail with different text, mostly a missing
    peek named now that a derived one is checked.
  - **Tests:** `tests/cr-2026-141-cases.yaml` (the prototype's four
    `TestCR2026141CoveredPeek` cases and eleven more) drives
    `test_cr_2026_141_covered_peek.py` (reference and runtime, decoding back where a case
    gives `decoded`), Go `cr141_test.go`, Java `CR2026141Test`, C# `CR2026_141Tests`; C has
    `test_a_covered_peek_is_derived`, in the one-byte ranges its struct API builds.

- **CR-2026-149 (PS-558, PS-559; PS-055/055a amended): `bf16`.** The upper 16 bits of an
  IEEE 754 binary32 (spec main, applied for 0.5.5). Decode reads the word in the field's
  effective byte order and reports the binary32 whose low half is zero; an infinity or NaN
  is absent (PS-282), as for f32. Encode rounds the binary64 value to the nearest bf16, a tie
  to the even fraction, **once and never through a float32** - 1 + 2^-8 + 2^-40 is `3f81`,
  where a binary32 detour rounds to the tie first and gives `3f80` - and a finite value that
  rounds past 0x7F7F is a PS-168 error in the prototype's text, `Field 'x': 3.4e+38 is outside
  the range of bf16, -3.3895313892515355e+38 to 3.3895313892515355e+38 (PS-168)`; NaN and the
  infinities are written as `7fc0`, `7f80`, `ff80`.
  - **Where:** one helper pair per language - Python `decode_bf16`/`encode_bf16`
    (`schema_interpreter.py`), Go `go/schema/cr149.go`, Java `Bf16`, C# `Bf16`, and the TS013
    generator's `readBf16`/`decodeBf16` (a `DataView` `setUint16` into the upper half, then
    `getFloat32`) and `encBf16` in `ENCODER_RUNTIME_JS`. The type is in `validate_schema`'s
    vocabulary, Go's `canonicalTypes`, Java's `FieldType`, C#'s `FieldType` and the output
    schema (a number). `tools/schema_binary.py` refuses it for C through the same
    "no such type" refusal as the minifloats (it is not in `C_REPRESENTABLE_TYPES`); the C
    harness files the fixtures as C gaps.
  - **CR-2026-148's exact comparison:** "through the type's storage" is through PS-559's
    rounding, in `_case_takes` and its four ports; a value that rounds past the largest bf16
    does not fit. Exercised by the encode cases, not a corpus vector.
  - **Found on the way, in Java and C#:** both wrote a number of 1e21 or more in their own
    exponent forms (`3.4E38`, `3.4E+38`) where PS-168's text is JavaScript's `3.4e+38`; their
    `jsNumber`/`JsNumber` now write it as JavaScript does, and C# writes a number from 1e-6 up
    in fixed notation. No earlier message carried so large a value.
  - **The prototype's document-level `endian` gap is not ours:** its `encode()` wrote a field
    with no `endian` of its own big-endian under a little-endian document. All five encoders
    pass the plain case (`document_endian_on_encode` in `tests/cr-2026-149-cases.yaml`: Go's
    `Encode`, Java, C#, the reference and the generated codec); Go's `EncodeOrdered` and
    `EncodeWithPort` share its `encodeToContext`.
  - **Fixtures:** `_language-conformance/bf16.yaml` (Netvox r718e's three accelerations from
    the vendor decoder run under node - `B3 40` little-endian is 5.59375 - a negative, the
    smallest subnormal and the largest finite, each round-tripping) and
    three vectors of infinities and NaN (absent, so no round trip) in the same fixture.
    `tests/cr-2026-149-cases.yaml` is the shared encode case list (ties both ways, the
    double-rounding trap, the overflow tie and the largest value, subnormals, the document
    endian case), read by all five; NaN and the infinities cannot be JSON input, so each
    language's `cr149` test holds them.
  - **Floors:** decode +9 in Go, Java and C# (2709 -> 2718); re-encode +6 everywhere
    (Python, Java 2115 -> 2121; C# 2116 -> 2122; Go 2133 -> 2139, plain 2106 -> 2112), all
    `plain fixed` (148 -> 154).

- **CR-2026-150 (PS-560, PS-561): `ascii` with `numeral: decimal|hex`** (spec main 96e3638,
  for 0.5.5). An `ascii` field reports its digit text as an integer, so modifiers, lookups and
  references apply to it (PS-476, PS-523) and the output schema says `integer`. Decode is
  **strict**: after PS-534's trailing-0x00 removal the whole text must match `[+-]?[0-9]+`
  (decimal) or `[0-9A-Fa-f]+` (hex, no sign, no `0x`), else `Field 'v': '0x1A' is not a hex
  numeral (PS-560)` - the text in single quotes, an all-NUL value as `''`. No prefix is ever
  read, which is where JavaScript's `parseInt` (the vendor's) differs. Encode writes the
  integer, rounded as PS-552 requires after the modifiers are inverted, in uppercase hex or
  decimal, right-aligned and `0`-filled to `length` after any sign; a negative hex value or
  one that does not fit is `Field 'v': 65536 does not fit 4 hex digits (PS-168)` (the value
  as JavaScript writes it, the count the field's `length`). `1a` and `+65` decode and come
  back as `001A` and `0065`, so only canonical text round-trips.
  - **Load-time (PS-560):** `Field 'v': numeral is defined only on an ascii field, not on u8
    (PS-560)` and `Field 'v': numeral must be decimal or hex, not 'octal' (PS-560)`, the
    prototype's text, from Python `numeral_errors` (in `schema_iterator_errors`, so the
    validator and the TS013 generator too), Go `checkNumeral` (from `checkFieldRules`), Java
    `Numeral.check` (from `checkWave4`) and C# `Numeral.Check` (from `CheckWave4`). The key is
    in `tools/schema_vocabulary.py` (all five decoders) and `payload-schema.json`.
  - **Where:** Python `decode_numeral`/`encode_numeral`, Go `go/schema/cr150.go`, Java
    `Numeral`, C# `Numeral`, the generator's decode branch (a regex test before `parseInt`,
    then the field's modifiers like any number) and `encNumeral` in `ENCODER_RUNTIME_JS`;
    `generate_output_schema.py` maps it to `integer`. C: `schema_binary.py` refuses `numeral`
    (PS-446) and the harness files it as a C gap.
  - **Not done:** a tlv case holding a `numeral` field takes any number (CR-2026-148's
    "fits" does not test the digit count), so a value too wide for `length` is PS-168 from
    the case chosen rather than a reason to choose another case.
  - **Fixtures and cases:** `_language-conformance/ascii-numeral.yaml` (Dragino lbt1's mode 3
    `major`, `minor` and `rssi`, four vendor-codec vectors, all round-tripping) and
    `tests/cr-2026-150-cases.yaml` with three lists - `decode` (the prototype's pins and the
    strict rejections), `cases` (encode) and `load` - read by all five (`test_cr_2026_150_cases.py`
    for the reference and the generated codec, Go `cr150_test.go`, Java `CR2026150Test`, C#
    `CR2026_150Tests`). Floors: decode +4 in Go, Java and C# (2718 -> 2722), re-encode +4
    everywhere, all `plain fixed` (154 -> 158; C# 2126, Go 2143, plain 2116).

- **CR-2026-154 (PS-566; la-payload-schema main, for 0.5.5): negative zero is zero.** For every
  float type - `f16`, `bf16`, `f32`, `f64`, `f32le16`, `f32be16le` and the minifloats (PS-418
  cites it) - a decoder reports a negative zero as 0, and an encoder writes the positive-zero
  word for an input of -0.0, for a decoded -0 and for a -0 its own arithmetic makes (`mult: -1`
  over 0). Measured before, in both byte orders: Python and Java reported 0 and re-encoded a
  decoded -0 as the zero word, but wrote the sign word for an input of -0.0; Go and C# reported
  -0 (printed `-0` in JSON) and wrote the sign word for all three; the TS013 codec and C kept -0
  and wrote the sign word for all three. The minifloats already complied in all five. Every
  implementation now complies, normalised in one place per direction:
  - **Encode:** Python `positive_zero` (`-0.0 + 0.0` is `0.0`), applied at the f16/bf16/f32/f64
    and word-ordered writes; the runtime's `encWriteFloat`, the bf16 call and `writeWordOrdered`
    (`+ 0`); Go `positiveZero`, in `float16Bits`, `encodeFloat32`, `encodeFloat64`,
    `writeWordOrdered` and `encodeBF16`; Java once in the float branch of `encodeField`
    (`raw + 0.0`) and in `Wave4.writeWordOrdered`; C# in `EncodeFloat16/32/64`, `Bf16.Encode` and
    `Wave4.WriteWordOrdered`; C once in `encode_field`, after the modifiers are undone, so the
    `0 / -1` of a `mult: -1` is caught too. C still cannot encode an f16 (`SCHEMA_ERR_*`).
  - **Decode:** Python and Java already reported 0. The generated codec normalises once in
    `omitAbsent`, the pass every decode leaves through (`v + 0`); Go once in the decode tail that
    drops NaN and the infinities (and in `finishComputedValue`); C# beside the same NaN test;
    C in `finish_numeric`, after the modifiers. So a -0 from `mult: -1` over a `u8` is 0 too.
    **A normalisation must be a comparison or `+ 0`, never a bare assignment**: Go does not fold
    `v + 0` but a compiler may fold nothing else, and `-0 == 0` is true, so `if v == 0 { v = 0 }`
    is what the Go and C helpers say.
  - **Cases:** `tests/cr-2026-154-cases.yaml` has `encode_cases` (-0.0 for each type, in each
    byte order for f16, f32 and f64, `mult: -1` and `div: -4` over 0, and a negative value that
    keeps its sign) and `decode_cases` (the prototype's -0 words: f16/bf16/sflt16 `8000`, sflt24
    `800000`, f32 `80000000`, f64 `8000000000000000`, f32le16 `00008000`, f32be16le `00800000`;
    each must decode to a **positive** 0 and re-encode to the zero word), read by
    `test_cr_2026_154_cases.py` (reference and generated codec), Go `cr154_test.go`, Java
    `CR2026154Test` and C# `CR2026_154Tests`. C cannot read YAML: `src/selftest_schema.c`'s
    `test_a_negative_zero_is_zero` holds f32 and f64 by hand (`make selftest`; `make test-c`
    does not run it).
  - **Fixtures:** `_language-conformance/negative-zero.yaml` (two vectors; each -0 word is
    followed by the same type's 1.0 so a mutation of its width or byte order is noticed, mutation
    score 0.893) and bf16.yaml's `negative_zero` vector, which the first bf16 PR had to drop.
  - **A vector that decodes a sign-bit word cannot round-trip, and the convention is
    `tools/encode-round-trip.py`'s reasons, not the floors.** The encoder writes the zero word,
    so `encode(decode(payload))` differs by the sign bit; it is the same loss as the minifloats'
    `8000`. The tool files it as the inherent reason `negative-zero` (a float field of a vector
    that does not re-encode and reports 0; it is last in `classify`, so a vector another reason
    already explains keeps that reason), and `test_cr_2026_023_encode_round_trip.py` still finds
    nothing `unexplained`. Floors count passes only, so they move only by what round-trips:
    `negative-zero.yaml`'s second vector (positive zero words, `made_negative_zero` 0 over
    `mult: -1`) does, and fails to before this CR. Decode floors +3 in Go, Java and C# (2740 ->
    2743: the two fixture vectors and bf16's); re-encode +1 everywhere (Python 2143 -> 2144,
    Java 2143 -> 2144, C# 2144 -> 2145, Go 2161 -> 2162, plain 2133 -> 2134), `plain fixed` +1.
    C: 575 of 575, unchanged.
  - **CR-2026-157 (PS-566 editorial, for 0.5.5): the encoded word is tested, not the input.**
    A negative value the type's rounding turns into zero (-1e-50 as `f16`, `bf16`, `f32`,
    `f32le16`, `f32be16le`, `sflt16`, `sflt24`) rounds to the sign word, and every writer wrote it
    (`8000`, `80000000`, `800000`). Each float writer now writes the zero word when the finished
    word is the type's negative-zero pattern: Python `pack_float` (f16/f32/f64 and word-ordered)
    and a wrapper over `encode_minifloat` (the old `encode_minifloat` is `_minifloat_word`), plus
    `encode_bf16`; the runtime's `encWriteFloat`, `encBf16`, minifloat call and `writeWordOrdered`;
    Go `float16Bits`, `encodeFloat32`, `writeWordOrdered`, `encodeBF16`, `encodeMinifloat`;
    Java `Encoder.zeroWord` at the f16/f32 writes, `Bf16.encode`, `Wave4`; C# `EncodeFloat16/32`,
    `Bf16.Encode`, `Wave4`; C `encode_field`'s f32 (C has no f16, bf16 or minifloat encode).
    **#146's `+ 0.0` normalisation is kept**: it still handles -0.0 before the rounding and
    costs nothing, and the word test adds the rounded case. A subnormal is not zero and is not
    clamped: -2^-24 stays `8001` in f16 (`f16_smallest_subnormal_keeps_its_sign`; C pins
    -2^-149 as `80000001`). f64 never rounds -1e-50 to zero. Cases: the CR-2026-157 section of
    `tests/cr-2026-154-cases.yaml`, read by the same five runners, and C's selftest. No corpus
    vector can say "input -1e-50" (a vector decodes a payload), so no fixture or floor moved.

- **Per-definition tables are held by identity, never by address alone** (found auditing
  for the spec prototype's `id()`-keyed SIBLINGS defect, CR-2026-141). Python's decode
  tables `_guard_else`, `_pre_lookup` and `_lookup_numbers` and the PS-168 `_encode_given`
  were keyed by `id(field_def)`; a repeat's element is a dict built afresh for each decode
  of the repeat (`repeat_members`), so a later definition at the freed address inherited
  the entry - a failed guard switched off another field's `valid_range`. They are
  `IdentityTable`s now, whose entries hold the definition. Go's `keyOrder` was keyed by a
  map's address and kept on the schema after the raw document was freed; each entry now
  holds its map. The generated codec named a variable `x_outer{id(field) % 100000}`, so
  one schema generated different code on each run; it takes `_next_uid()`. Java's
  `IdentityHashMap`s and C#'s `ReferenceEqualityComparer` tables hold their keys and were
  never affected. `tests/test_identity_tables.py` reproduces each Python leak.

- **CR-2026-146 (PS-169 amended, la-payload-schema main, merge 65701a0, for 0.5.5): a label
  several codes share cannot be encoded.** Decoding is unaffected. An encoder given a label
  that two or more codes of a lookup (mapping or sequence) or an enum share rejects it:
  `Field '{name}': the label {label} is shared by codes [{a}, {b}], so it cannot be encoded
  (PS-169)`, codes ascending, a text label in single quotes, a boolean `true`/`false`, a
  number as JavaScript's String(number). A code given as a number still encodes. Every
  encoder wrote a guess - Python, Java, C# and the runtime the first code declared, Go the
  smallest. A caller that only ranks candidate cases (`_case_fidelity`, Go
  `reverseLookupAnyCode`/`rawForField`, Java and C# `ReverseLookup` without a name) takes
  the smallest code instead of rejecting; the field's own encoding rejects. The validator
  warns of a shared label on any field that is not computed (`shared_labels`), which in
  the corpus is vobo alone (104 warnings, through its YAML anchors). C's encoder takes
  native values only, so neither applies to it. `encode-round-trip.py` files the
  rejection as `shared-label`, read per vector from the encoder's error. Shared cases:
  `tests/cr-2026-146-cases.yaml`, read by `test_cr_2026_146_shared_labels.py` (reference
  and runtime), Go `cr146_test.go`, Java `CR2026146Test`, C# `CR2026_146Tests`.
  - **An enum encodes a name as its code and a code as given** (PS-067); a name with no
    code, the `default` label among them, is `Field '{name}': '{label}' is not a name of
    the enum (PS-067)`, the prototype's text. Measured before: Python named the base
    field `'None'` in its PS-168 text and still read `unknown(N)`; **Go wrote a code past
    the base wrapped and silently (300 as `2c`) and wrote every non-`u8` base as one
    byte** (`length` is 1 for an enum - 70000 under `u16` came out `70`); Java and C#
    said `300 does not fit 1 unsigned bytes`, naming no field. All five now give PS-168's
    text naming the enum and write the base's width; Go's decode shares `enumBaseWidth`,
    which adds `u24`/`s24`.
  - **Corpus:** -4 deliberately, all `tlv`: vobo `mutant_p32_01`, `mutant_p112_01`
    (`AnalogSensor36` is codes 36, 100, 164, 228), `two_analog_xp_all` and
    `two_analog_xx_ain3` (`%` is 57 and 170) round-tripped only because the first code
    found was the one in the payload. The ten `mutant_p*_03..07` already failed and now
    fail with the PS-169 text. Identical sets in Python and Go (diffed).
- **PS-168 for `udec`/`sdec`, and a fractional match discriminator.**
  - `udec`/`sdec` now reject in PS-168's form, `Field 'v': 123.5 encodes as 123.5,
    outside 0 to 15.9 (PS-168)`, the encoded value given to tenths and the range what one
    byte holds with tenths 0 to 9: 0 to 15.9, or -8 to 7.9 (`check_nibble_decimal`).
    Python, Go, Java, C# and the runtime said `does not fit udec` in four spellings; **C
    clamped silently** - udec 123.5 wrote `0x95`, and any udec of 10 or more wrote 9.x.
    Java rounded the tenths half up; all round half to even now. Cases in
    `tests/cr-2026-135-cases.yaml`, and C's `test_a_nibble_decimal_that_does_not_fit_is_named`.
  - A discriminator with a fraction (one taken from a field whose arithmetic leaves one)
    matches no single key and no list member; an inclusive range `lo..hi` still holds it,
    as the reference and the spec prototype read PS-398 (as CR-2026-147 rules). **Go, Java and C# decoded 2.5 as
    case 2** (`toInt`, `asLong`, `ToLong` truncate), and on encode Go truncated while Java
    and C# rounded half to even, so a schema whose `default` should apply wrote case 2's
    fields. Java and C#'s encoders also never matched an integral discriminator against a
    `lo..hi` key; they do now. Python and the runtime compared by value already.
    `_language-conformance/match-fractional-discriminator.yaml` (5 vectors) holds all five
    to it; decode floors 2690 -> 2695, `match` +5 everywhere.
  - **Floors:** Python and Java 2100 -> 2101 (`match` 142 -> 147, `tlv` 1559 -> 1555), C#
    2101 -> 2102 (`tlv` 1560 -> 1556), Go ordered 2118 -> 2119 (`tlv` 1577 -> 1573), plain
    2091 -> 2092; decode 2690 -> 2695 in Go, Java and C#.
- **CR-2026-147 (la-payload-schema main, merge 19bbc54, change 8db4385, for 0.5.5): Jason's
  rulings on the questions above.**
  - **PS-556: a nibble decimal is n = value x 10, rounded half to even on the binary64
    product (PS-552)**; the upper nibble is n div 10 and the lower n mod 10, both floored, so
    the tenths are 0 to 9 and -0.5 is `0xF5`. n outside 0..159 (udec) or -80..79 (sdec) is
    the PS-168 rejection above, unchanged. The first implementation split the whole part off
    and rounded the fraction, which differs on a tie of the product: 3.45 wrote `0x35` where
    PS-556 gives `0x34` (34.5 rounds to 34), and -3.45 `0xC5` where it gives `0xC6`. Fixed in
    Python, Go, Java, C#, the runtime and C (`schema_encode_nibble_decimal`). Decoders still
    accept a lower nibble of 10 to 15.
  - **PS-398 as amended:** a fractional discriminator matches a range containing it and no
    single or list case - what `match-fractional-discriminator.yaml` already pins.
  - **PS-169's label is written as JavaScript would** (the prototype's `js_text`), which
    `label_text` and its ports already did. The prototype lists the codes in declaration
    order; this repository lists them ascending, as the coordinator's brief for CR-2026-146
    asked - they differ only for a table declared out of order.
  - **PS-557: a fractional value for a discriminator the encoder writes** (a match with
    `length`) is rejected, naming the match as PS-535 does: `Field 'kind': the
    discriminator 2.5 is not an integer, so it cannot be written (PS-557)`, `Field an
    unnamed match: ...` with no `name`. Python's `int()` and Go's `toInt` truncated it, and
    Java and C# rounded it half to even. The prototype encodes no match, so the text is
    this repository's. `written_discriminator_error` (Python), `writtenDiscriminatorError`
    (Go), `checkWrittenDiscriminator`/`CheckWrittenDiscriminator`, and the runtime's
    `encMatch`. C's encoder writes no match, so PS-557 does not apply to it.
  - **PS-555's keying rule** is #134's (C's index-keyed inputs); the prototype has no
    nameless form, so there is no prototype text to compare.
  - **Cases:** `tests/cr-2026-147-cases.yaml`, read by `test_cr_2026_147_cases.py`
    (reference and runtime), Go `cr147_test.go`, Java `CR2026147Test`, C#
    `CR2026_147Tests`; C's nibble figures in `test_a_nibble_decimal_that_does_not_fit_is_named`.
    No corpus vector encodes a nibble decimal or a fractional written discriminator, so no
    floor moves.

- **CR-2026-159 (la-payload-schema `cr/0.5.6-apply-158-162`, for 0.5.6): four constructs and a
  misuse of `min`/`max` are withdrawn and rejected at load.** Each message is the spec
  prototype's (`key_vocabulary.WITHDRAWN`, `check_field`), word for word, with no prefix, so
  Python, Go, Java and C# give one string:
  - **`formula`** -> "`formula` is withdrawn; use `compute`, `polynomial`, `transform` or
    `guard` (CR-2026-159, PS-448)"; **`encode_formula`** -> "`encode_formula` is withdrawn with
    `formula` (CR-2026-159, PS-448)"; a **transform `clamp`** stage -> "`clamp` is withdrawn;
    use a `floor` stage and a `ceiling` stage (CR-2026-159, PS-115)"; a port's **`confirmed`**
    -> "`confirmed` is withdrawn: confirmed or unconfirmed is chosen per transmission, not by
    the schema (CR-2026-159, PS-448)".
  - **`min`/`max` on a field that is no repeat** -> "`min` on field 'v' is withdrawn: it bounds
    a repeat's element count only; declare a value range with `valid_range` (CR-2026-159,
    PS-395)" (`max` likewise; `<unnamed>` where the field has no name). On a `type: repeat`
    they stay valid, and `ties: even` stays valid.
  - **Where:** Python `withdrawn_errors` (from `schema_iterator_errors`, so the interpreter,
    the validator and the TS013 generator refuse it) and `transform_stage_errors` for
    `clamp`, with `WITHDRAWN_KEYS`; Go `go/schema/cr159.go` from `checkFieldRules`,
    `checkTransformStages` and `checkTypeVocabulary`; Java `Cr159`, from `parseFields`,
    `checkStage` and `parseRaw`; C# `Cr159.cs`, from `ParseFields`, `CheckStage` and `Parse`.
    C has none of the keys: `tools/schema_binary.py` (`schema_errors`) refuses them with the
    same text before a blob exists. A definition no `$ref` reaches is not walked by Python
    (every other walk skips it too); Go, Java and C# check one where they parse it.
  - **The encoder's min/max value check was already gone in this repository.** The prototype
    dropped the `min`/`max` range check from `encode_field`; none of the Python reference, the
    TS013 runtime, Go, Java, C# or C ever had one (measured: `u8` with `min: 10` encodes 5 and
    `max: 100` encodes 150). Nothing to remove; `valid_range` does not reject on encode, and a
    case pins it in all four.
  - **Dead code removed:** the formula evaluators (Python `_evaluate_formula` and
    `_evaluate_encode_formula`, Go `evaluateFormula` and its expression parser, Java
    `FormulaEvaluator`, the generator's `formula_to_js`), every `clamp` stage reader, the
    `Formula`/`Clamp` model fields, the validator's formula and clamp checks, and the
    vocabulary and `payload-schema.json` entries (the hint table names the replacements).
    `tools/generate_firmware_codec.py`, `generate_js_decoder.py` and `convert_decentlab.py`
    are not conformance paths and keep their old spellings.
  - **Measured before changing anything:** 0 `formula`, `encode_formula`, `confirmed` or non-repeat
    `min`/`max` in `schemas/devices`, `schemas/library` and `examples`, and none in the
    4065 files of the private registry; two `clamp` stages, both in the generated-and-orphaned
    `examples/env_sensor_resolved.yaml` and `multi_sensor_resolved.yaml`, now `floor: 0` and
    `ceiling: 100` (the same meaning). No device vector, so **no floor moves**.
  - **Tests:** the Python, Go, Java and C# formula and `clamp` tests are deleted with the
    construct (the prototype did the same); a few that used `formula` incidentally now use
    `div`/`add` or `ref` + `mult`. `tests/cr-2026-159-cases.yaml` is one case list read by
    `test_cr_2026_159_withdrawals.py` (interpreter, validator, generator, binary converter),
    Go `cr159_test.go`, Java `CR2026159Test`, C# `CR2026_159Tests`.
- **The legacy `type: match` is removed (cleanup, after CR-2026-159's pattern).** The spelling
  with `on:` for the discriminator and a `default` case for the fallback is not in the
  specification (Clause 4 replaced `on` with `field`; PS-448 rejects `on`, PS-575 an unquoted
  one). Every load path now refuses it with "`type: match` with `on:` is withdrawn; write
  `match: {field: $name, cases: ...}` (Clause 4)": Python `withdrawn_errors`
  (`WITHDRAWN_TYPE_MATCH`, so the interpreter, the validator, the TS013 generator and
  `schema_binary.py`), Go `withdrawnTypeMatch` from `checkTypeVocabulary` (before the
  unknown-type check, which would otherwise say `unknown type: match`), Java `Cr159.TYPE_MATCH`
  and C# `Cr159.TypeMatch`. The decode, encode, `_meta` and name-check paths for it, the
  list-form `cases` parse, `Case.Match`, Java's `on` read, `FieldType.fromString("match")` and
  `"match"` in the type vocabulary are gone; the internal `FieldType.Match` that an inline
  `match:` is parsed into stays. The meta-schema lost `definitions.field`'s `on` and
  `cases`, and the vocabulary's `on`, `cases` and `legacy_case` context (hints now name the
  replacement). Measured before removing: no schema in `schemas/devices`, `schemas/library`,
  `examples`, the docs or the private registry used it, so nothing was converted and no vector
  or floor moved. The only users were 11 Go tests, one C# test, three output-schema tests, one
  mutation-harness test and two binary-schema tests, all deleted or rewritten to the
  `match:` construct. Cases: three added to `tests/cr-2026-159-cases.yaml` (quote `on`
  in a case, or PS-575 refuses it first), read by all four loaders. The legacy binary
  encoders `binary_schema.py` and `binary_schema_v2.py` raise on it, and v2 now decodes a match
  record into `match:`.
- **CR-2026-160 (PS-059; la-payload-schema branch `cr/0.5.6-apply-158-162`, for 0.5.6): a bit
  range's base is any multiple of 8 from 8 to 64, and the wide widths are bases only.** `u40`,
  `u48`, `u56` and `s40`, `s48`, `s56` are valid as `uN[a:b]`/`sN[a:b]`, a non-consuming one
  included (Dragino lse01-121 reads bit 7 of its seventh byte with `u56[7:7]`, `consume: 0`),
  and are not field types of PS-052: a standalone `type: u56` is an unknown type.
  - **Measured before any change: every path already behaved so.** Python, Go (whose bit-range
    pattern gained 40/48/56 in #142 without its type table following), Java, C#, the TS013
    generator, the validator and C's `schema_binary.py` all rejected a standalone wide type and
    all read a wide range; this change is the cases and this entry. The error text is each
    implementation's existing unknown-type message, not one string: the shared cases match on
    the type's name, as the prototype's test does.
  - **Cases:** `tests/cr-2026-160-cases.yaml` (the prototype's `TestCR2026160WideBitRangeBases`
    case for case, plus a u40 and a signed s48 range), read by `test_cr_2026_160_cases.py`
    (reference, validator, generator and `encode_schema`), Go `cr160_test.go`, Java
    `CR2026160Test`, C# `CR2026_160Tests`. They are not corpus vectors, so no floor moves.

- **CR-2026-161 (PS-570, PS-571; la-payload-schema branch `cr/0.5.6-apply-158-162`, for 0.5.6):
  `_meta` carries `reports`, and a lookup's labels are of one type.**
  - **PS-570:** every `_meta` entry derived from a declaration, at any depth, carries `reports`,
    the JSON type PS-279 selects, right after `type` (key order is compared exactly): `integer`,
    `number`, `string`, `boolean`, `object` or `array`. A match discriminator is `integer`, a
    `merge: false` tlv's `channels` is `array`, `bytes` with `format: array` is `array`, a field
    with a lookup reports its labels' type whatever arithmetic precedes it (the lookup is the
    last step, PS-443; integer-only numeric labels `integer`, other numeric `number`, a
    `${value}` default `string`), and an undeclared PS-499 entry `{}` has none. One function per
    language, mirroring the prototype's `reports_of`: Python `reports_of`
    (`tools/schema_interpreter.py`), Go `reportsOf` (`go/schema/cr161.go`), Java `Reports.of`,
    C# `Meta.ReportsOf`. The discriminator and `channels` have no declaration of their own, so
    the first is given an internal `_reports` key beside its pseudo-declaration and the second
    is recognised by its `tlv`. C has no `_meta`.
  - **PS-571:** a lookup whose labels (a non-`${value}` `default` counts as one) mix JSON types
    is rejected at load, `Field 'f': its lookup mixes boolean, string labels; a lookup's labels
    are of one type (PS-571)`, the types sorted, integer and other numbers both `number`.
    Python `lookup_type_errors` from `schema_iterator_errors` (so the interpreter, the
    validator and the TS013 generator), Go `checkLookupTypes`, Java `Reports.check`, C#
    `Meta.CheckLookupTypes`. Measured before enforcing: no schema in the corpus, library or
    examples is rejected. Only one existing test tripped it, and it was a bug in the test:
    `lookup: {1: on, default: unknown}` is `true` under YAML 1.1, so the labels were a boolean
    and a string. **Quote `"on"`/`"off"`/`"yes"`/`"no"` as lookup labels**: PyYAML and SnakeYAML
    read them as booleans and C# (YAML 1.2) as strings, which is also why the shared cases
    quote them.
  - **`expected_meta` fixtures:** 12 `_language-conformance` fixtures changed, each entry
    gaining `reports` from the spec rule (they are compared exactly), and four Python tests that
    spell a `_meta` entry out. `make gate-verdicts` rule 3 (`_meta` identical to Python's in Go,
    Java and C#) is the check that the four producers agree.
  - **Cases:** `tests/cr-2026-161-cases.yaml` (the prototype's `TestCR2026161MetaReports`, 16),
    read by `test_cr_2026_161_cases.py`, Go `cr161_test.go`, Java `CR2026161Test`, C#
    `CR2026_161Tests`. No corpus vector changes, so no floor moves.

- **CR-2026-164 (PS-575; la-payload-schema `cr/0.5.6-apply-164`, for 0.5.6): a plain YAML 1.1
  boolean scalar is rejected where YAML text is loaded.** A plain (unquoted) scalar whose text
  is exactly `yes`, `no`, `on` or `off` in the spellings lower, Capitalised and UPPER is
  refused, since YAML 1.1 loaders read it as a boolean and YAML 1.2 ones as a string. `y`, `Y`,
  `n` and `N` are not in the list (fields called `y` and `n` stay valid), and a quoted value,
  `true`/`false`, a longer plain string containing one, a comment and a block scalar's text
  are all accepted. The text is the prototype's: `Line 4: the unquoted value 'on' is a boolean
  in YAML 1.1 and a string in YAML 1.2; quote it ("on") or write true or false (PS-575)`,
  1-based line, the first one in the document.
  - **Detected from the raw YAML before anything is constructed**, so the loaders agree:
    Python `check_yaml_booleans` in `tools/schema_interpreter.py` (`yaml.parse` events,
    `ScalarEvent.style is None`; raises `YamlBooleanError`), called by the interpreter CLI,
    `validate_schema.load_schema_text`, the TS013 generator's `load_schema` and
    `schema_binary.py`'s `encode`/`info`; Go `checkYAMLBooleans` in `go/schema/cr164.go`
    (`yaml.Node`, `Kind == ScalarNode`, `Style == 0`), the first thing `ParseSchema` does;
    Java `Schema.checkYamlBooleans` (SnakeYAML events, `ScalarStyle.PLAIN`), first in
    `fromYaml`; C# `SchemaParser.CheckYamlBooleans` in `Cr164.cs` (`YamlScalarNode.Style ==
    ScalarStyle.Plain`, over the loaded model, first violation by line), first in `Parse`.
    Mapping keys are scalars too and are checked. Text that is not well-formed YAML is left to
    the loader that follows.
  - **Not checked: a schema handed over as a built mapping** (`SchemaInterpreter(dict)`,
    `Schema({...})`, a YAML tree in Java or C# already parsed) - there is no text, and a
    YAML 1.1 loader has already turned `on` into `True`. **C has no YAML reader**, so the
    rule does not apply to it (`schema_binary.py`, which feeds C, refuses the YAML first).
  - **Measured before:** no file in `schemas/devices`, `schemas/library`, `examples` or
    `tests` (414 YAML files) holds one, so no vector or floor moves.
  - **Cases:** `tests/cr-2026-164-cases.yaml` (six rejections, six accepted including
    `name: y` / `name: n`), read by `tests/test_cr_2026_164_yaml_booleans.py` (reference,
    validator, generator, converter and the CLIs), Go `cr164_test.go`, Java `CR2026164Test`
    and C# `CR2026_164Tests`.

- **CR-2026-167 (PS-297 amended, PS-576, PS-577; merged on la-payload-schema main for
  0.5.7): a test vector may expect an error.**
  - **The key.** `expected_error: {requirement: PS-nnn | [PS-nnn, ...], at: <name or PS-572
    JSON Pointer>}` stands in place of `expected` / `expected_payload`, never beside them, on a
    decode vector (`payload`) or an encode vector (`input`). `expected_warnings` on such a vector
    lists the warnings reported before the failure. Load checks, in the prototype's text
    (`key_vocabulary.check_expected_error`): `expected_error needs a requirement (PS-576)`, the PS
    number format (`PS-\d+[a-z]?`), `at` a string, and `carries both expected_error and ...
    (PS-297, PS-576)`. They live in `validate_schema.expected_error_problems`, run by the
    validator; no interpreter reads vectors, so nothing else loads them. The vocabulary gate
    knows the key (`tools/schema_vocabulary.py`).
  - **The rule, one helper per runner.** A vector fails when the execution succeeds; when the
    message cites PS numbers and they do not include every listed `requirement` (checked **only
    where the message cites any**, so an error such as `Missing required field: x` is held to
    `at` alone); or when `at` is given and the message neither contains it quoted (`'humidity'`)
    nor, for a pointer, contains the pointer. Wording is never compared. Python
    `validate_schema.expected_error_mismatch` (also `expects_error`); Go `corpusErrorMismatch`,
    Java `errorMismatch`, C# `ErrorMismatch` in their corpus runners; the scorer's node runner
    has its own copy (`errorMismatch`). The prototype's `at` rule is the quoted name, so an error
    that names the field unquoted (the prototype's own `Missing required field: humidity`) cannot
    satisfy `at`: the fixture's PS-551 vector leaves `at` out for that reason.
  - **What each runner does.** Python's validator (`run_test_vector`), `tests/test_corpus_
    conformance.py`, `tools/corpus-report.py`, `tools/score_schema.py` (Python half and the node
    half; an error vector is a usable vector, it earns the vector points, and it is never a
    branch- or edge-case witness) and `tools/schema-mutation.py` run it (a mutant that lets the
    decode succeed, or moves the error to another rule or field, is killed). `tools/vector-verdicts.py`
    runs it on both paths, the generated codec's `errors[]` (decodeUplink, encodeDownlink) being
    the error messages. Go, Java and C# run it on decode vectors only, as they run only decode
    vectors; an encode vector is skipped by them as every encode vector is. `tools/c-corpus-
    harness.py` does not run them: it counts them (`expected_error_vectors_not_run`) and says why,
    since the fixture's rejections need ports, tlv or an encoder keyed by name, none of which the
    struct API builds. The round-trip suites (Python, Go, Java, C#, `encode-round-trip.py`),
    `test_corpus_invariants` and `test_ts013_encode_parity` skip them; a decode that fails is no
    round-trip candidate. `provenance-gate` reads `expected_error` as an outcome key, so changing
    one on an unchanged payload is flagged like a changed `expected`.
  - **Floors.** The decode floors are the corpus's decode vectors **that run**, and a decode
    vector expecting an error counts, passing when the decode fails as required (Go, Java, C#
    2744 -> 2751: the fixture's three rejections and four successful decodes; its two encode
    vectors are not decode vectors, so no decode floor moves for them). Python's decode floor is
    every vector, as before.
    The four successful decodes round-trip, so re-encode floors rise by 4, all `tlv` (Python and
    Java 2145 -> 2149, C# 2146 -> 2150, Go 2163 -> 2167, plain 2135 -> 2139); the rejections are
    no round-trip candidate.
  - **Fixture** `_language-conformance/expected-error.yaml` (ports 1-3): the CR's own
    `too_short` (PS-535 at `temperature`), a tlv entry cut short (PS-486), a match with no case
    and `default: error` (PS-565) and the CR's two encode examples (PS-168 at `humidity`; a
    missing input, PS-551). **Who cites what, measured:** the PS-535 underflow and the PS-486 tlv
    entry are cited by Python, Go, Java, C# and the generated codec (the C interpreter cannot run
    either, with no ports or tlv); PS-168 by Python and the codec (the encode vectors run only
    there); **PS-565 and PS-551 are cited by none**: `No matching case for value 9` and
    `Missing required field: humidity` carry no PS number in any implementation, so those two
    vectors are held to execution failure (and `at`, where given) alone, as PS-576 allows. Four
    successful decodes sit beside them so the schema is held as tightly as its rejections (a
    rejection says nothing of byte order or scale): mutation score 100%.
  - **PS-577 is the direction for the shared case files.** A certification suite includes a
    rejection vector for each requirement enforced by rejecting a payload or an input; a rejection
    that lives only in `tests/*-cases.yaml` is a rejection no other implementation's suite sees.
    **Not migrated in this change.** Follow-up inventory, counted from the case files
    (`error` cases, by kind): 109 encode and 7 decode rejections run at run time - a schema that
    loads - and could become vectors; 56 are load-time (a schema that is refused cannot carry
    a vector, so they stay):
    - encode, movable: `cr-2026-134` 34, `-135` 20, `-141` 8, `-146` 11, `-147` 11, `-148` 2,
      `-149` 3, `-150` 4, `-151` 5, `-153` 4, `silent-empty` 7;
    - decode, movable: `cr-2026-150` 7 (the `numeral` text that is not a numeral);
    - load-time, staying: `-138` 11, `-150` 4, `-156` 10, `-159` 15, `-160` 6, `-161` 3, `-164` 7.
    Two things a migration must settle first: Go, Java and C# run decode vectors only, so a moved
    encode rejection loses its coverage in those three until their corpus runners run encode
    vectors (the corpus has encode vectors that only Python and the codec run today); and each case
    carries its own schema, so 91 distinct run-time schemas would join `_language-conformance/`
    (or share one per CR with a port each, as the fixture does).

- **CR-2026-168 (PS-345, PS-346 amended, PS-578 to PS-580; la-payload-schema main, for
  0.5.7): field templates and shared lookup tables.** A definition carries exactly one of
  `fields` (a group), `field` (one field) or `lookup` (a table). A `$ref` to a `field`
  definition may carry field keys, each replacing the defined key whole (one level deep, so a
  `lookup` given there is not merged); the result must have a `name`. A `$ref` to a group
  carries nothing else, and a `$ref` in a field list never names a `lookup`. A field's
  `lookup` may be the pointer `#/definitions/<name>`. References resolve transitively; the
  definitions kept on the resolved document are resolved too, so no later check sees a
  pointer. An `enum`'s `values` cannot point to a table (not adopted).
  - **Where:** one resolver per language, the prototype's `resolve_references`, with its
    messages word for word: Python `expand_refs`/`definition_kind`; Go `go/schema/refs.go`,
    which now resolves on the **YAML node tree** before any value is built (`resolveRefNodes`;
    `ParseSchema` decodes `raw` and builds `keyOrder` from the resolved tree, so a template's
    copy keeps its source key order - a match's case order, PS-563, and `_meta` read it; a
    JSON-only document is turned into a node tree first; the decoded value is then given string keys throughout, `stringKeyedDocument`, as the map resolver left it - a case or port key otherwise stays `map[any]any` and the walks that read `map[string]any`, such as the PS-442 guard's `declaredSources`, miss it); Java `Schema.expandRawRefs`; C#
    `SchemaParser.ExpandRefs`. The validator now checks the resolved schema throughout
    (`validate_schema_structure`), the TS013 generator and `generate_output_schema.py` resolve
    first, and `tools/schema_binary.py` resolves before converting, so **C reads templates and
    shared tables** with no change to the interpreter: the fixture passes 3 of 3 through the
    struct API and both blob forms. The vocabulary's `definition` context gains `field` and
    `lookup`, and `referenced_definitions` follows lookup pointers; `payload-schema.json`
    describes the three kinds and the pointer.
  - **Measured before:** every implementation refused a `field` or `lookup` definition
    ("not a field group", PS-345). Python and the generated codec read `lookup:
    "#/definitions/x"` as a sequence of characters, so code 1 decoded as `'/'`, with success;
    a `$ref` to a group carrying `name` was accepted and the key silently ignored. The binary
    converter wrote a `$ref` entry as a field record of its own (`ref-header.yaml`'s blob had
    a nameless first record), which `test_cr_2026_142_binary_names.py` had pinned; it now
    compares against the resolved list.
  - **Messages changed:** the old PS-345 and PS-348 texts ("is not a field group with a
    `fields` array", "does not resolve to a field group") are the prototype's now
    ("must carry exactly one of `fields`, `field` or `lookup`, not none", "which `definitions`
    does not define"). The cycle text gained backquotes (`` `$ref` cycle: f -> f``).
  - **Cases:** `tests/cr-2026-168-cases.yaml` (the prototype's
    `TestCR2026168ReusableDefinitions`, 9 loaded and 9 rejected), read by
    `test_cr_2026_168_cases.py` (interpreter, validator, generator, binary converter, codec
    decode and encode, `_meta` against the written-out schema), Go `cr168_test.go`, Java
    `CR2026168Test`, C# `CR2026_168Tests`; each also pins that a template keeps its case order.
  - **Fixture** `definitions-field-and-lookup.yaml` (3 vectors, mutation 100%): decode floors
    +3 in Go, Java and C#; re-encode and `plain fixed` +3 everywhere.
  - **Corpus and registry:** every `definitions` entry in both carries `fields`, so nothing is
    rejected. Measured in Python against master over the corpus, library, examples and the
    private registry (919 schemas): validator output, and every vector's decode and re-encode,
    identical. No schema uses the new forms yet.

- **Range case keys take numeric bounds (PS-398); a malformed one is a load error.** `"12.1..35.4"`
  is inclusive over the reals, and a discriminator may be fractional (a computed field). The
  reference read each bound through `int()`, so such a key matched nothing and raised no error
  (a registry agent's AQI breakpoint table). Measured before on `match-fractional-range-keys.yaml`
  (13 vectors, bounds hit exactly through `s16` `div: 100`): **all five failed the same six** -
  Python, Go, Java, C# and the TS013 codec each parsed bounds as integers (Go `parseRangePattern`,
  Java `rangeBound`, C# `long.TryParse`, the codec `parseInt`/`int()`), the seven integral-bound and
  gap vectors passing by accident; the same keys over an integer discriminator failed too. Neither
  the encoders nor `_sole_case_given` needed anything: they reach the same matchers (Java and C#
  encoders already call `fractionInRange`), and a range key never fixes a discriminator.
  - **Bounds:** a decimal integer, a hexadecimal integer (`0x10..0x1F`, the spec's own example, which
    Python never matched) or a decimal with a fraction or exponent, either signed; an integer
    bound stays an integer, so a `u64` discriminator is compared exactly where both bounds are
    integers. One helper per language: Python `parse_range_case_key`, Go `go/schema/range_keys.go`,
    Java `RangeKey`, C# `RangeKey.cs`, the generator's `_match_condition` and runtime
    `encCasePattern`.
  - **Load (PS-398, PS-151):** a case key holding `..` that is not a quoted list must be two numbers
    with `start <= end`: `match.cases[a..b]: a range key is `start..end` with two numbers, each an
    integer or a decimal, such as 2..5 or 12.1..35.4 (PS-398)` and `... a range key has start <=
    end (PS-151, PS-398)`. Python `range_key_error` inside `case_body_errors` (so the interpreter,
    the validator and the TS013 generator), Go `rangeKeyLoadError` from `checkCases`, Java
    `RangeKey.error` from `checkCaseBodies`, C# `RangeKey.Error` from `CheckCaseBodies`. Applies to
    `tlv` cases too. Nothing in the corpus, library, examples or registry is rejected (measured).
  - **C:** `case_def_t.range_min/range_max` are integers, so a fractional bound is a C gap; the
    harness cannot build it. `tools/schema_binary.py` was not changed.
  - **Tests:** `tests/test_range_case_keys.py`, Go `range_keys_test.go`, Java `RangeKeyTest`, C#
    `RangeKeyTests`. `test_cr_2026_020_match_parity.py` used the malformed `"one.."` as its
    never-satisfied key and now uses `"[]"`.
  - **Floors:** decode 2755 -> 2768 in Go, Java and C#; re-encode +13 everywhere (Python, Java 2153
    -> 2166, C# 2154 -> 2167, Go 2171 -> 2184, plain 2143 -> 2156), `match` 160 -> 173, every vector
    round-tripping.

- **CR-2026-177 (PS-534 amended, PS-072 amended, PS-593 to PS-595; la-payload-schema main
  9e8957a, for 0.5.7): `nul` and `terminator` on `ascii`.**
  - **What it does.** An `ascii` field declares `nul: trailing|stop|drop` (default `trailing`,
    PS-534's old rule): `stop` ends the value before the first 0x00 and leaves the rest of the
    field unreported; `drop` removes every 0x00; whitespace is never trimmed. Or it declares
    `terminator`, a byte 0 to 255, in place of `length` (PS-593): the text runs to the first such
    byte, which is consumed and not reported; a payload that ends first is `Field 'v': no
    terminator byte 0x00 before the end of the payload (PS-593) (PS-535)`. `nul` and `terminator`
    apply before `numeral` (PS-594). Encode (PS-595): `stop` pads with 0x00 to `length` and
    rejects a value holding 0x00 (`Field 'v': a nul: stop value holds a 0x00 byte (PS-595)`);
    `drop` pads as PS-537; a terminated value is written followed by its terminator, no padding,
    and is rejected if it holds the terminator (`Field 'v': the value holds its terminator byte
    0x2C (PS-595)`).
  - **The Implementation Record's departure, binding:** a terminated `numeral` is written without
    fill (there is no `length` to fill to), and a negative `hex` numeral without a length is
    `Field 'v': -1 is negative, which a hex numeral cannot write (PS-168)` in all five encoders,
    where an unfixed length used to say `does not fit N hex digits`.
  - **Load (the prototype's text):** `an ascii field declares length or terminator, not both
    (PS-593)`, `nul and terminator are defined only on an ascii field, not on u8 (PS-534,
    PS-593)`, `nul must be trailing, stop or drop, not 'strip' (PS-534)`, `terminator must be a byte
    value 0 to 255, not 256 (PS-593)`, each prefixed `Field '<name>': `. **PS-072's "neither" is
    now a load error** (`ascii type requires 'length' field, or 'terminator' (PS-072)`); the
    prototype raises it at decode, and Python used to read one byte for a length-less `ascii`.
    Python `ascii_text_errors` (from `schema_iterator_errors`, so the validator and the TS013
    generator refuse it too), Go `checkAsciiText` (`go/schema/cr177.go`, from `checkFieldRules`),
    Java `AsciiText.check` (from `checkWave4`), C# `AsciiText.Check` (from `CheckWave4`).
  - **Where decode and encode live.** Python `apply_nul_mode`, `terminated_span`, `decode_ascii`
    and `encode_ascii` (`tools/schema_interpreter.py`); the generator's ascii branch and
    `ENCODER_RUNTIME_JS` string branch; Go `applyNulMode`/`terminatedSpan` and `decodeASCII`/
    `encodeASCII` (`cr114_115.go`); Java `AsciiText` and `Encoder.encodeAscii`; C#
    `AsciiText` and `Helpers.DecodeAscii`/`EncodeAscii`. Before: all five kept every 0x00 that
    was followed by another byte (`"AB\u0000XY"`), and none had a terminator.
  - **C:** has neither key. `tools/schema_binary.py` refuses `nul` and `terminator` by name
    (PS-446) and the C harness files them as C gaps.
  - **Round trip:** `stop` and `drop` lose what they drop, so `encode-round-trip.py` files a
    vector of such a field that does not re-encode as the inherent reason `nul-mode`.
  - **Cases and fixtures:** `tests/cr-2026-177-cases.yaml` (18 decode, 15 encode, 9 load; the
    prototype's `TestCR2026177NulAndTerminator` and more) read by `test_cr_2026_177_cases.py`
    (reference and generated codec), Go `cr177_test.go`, Java `CR2026177Test`, C#
    `CR2026_177Tests`. Fixtures `ascii-nul-stop`, `ascii-nul-drop`, `ascii-terminator`,
    `ascii-terminator-numeral` (15 vectors, `source: generated`): decode 2801 -> 2816 in Go,
    Java and C#; re-encode +9 everywhere (Python, Java 2199 -> 2208; C# 2200 -> 2209; Go 2217 ->
    2226, plain 2189 -> 2198), `plain fixed` 185 -> 194.
  - **Measured before:** no corpus, library or example schema, and none of the registry's
    schemas, declares `nul` or `terminator` or an `ascii` field without `length`, so none is
    rejected and no decode or encode output changes.

- **CR-2026-175 (PS-453 and PS-015 amended, PS-588, PS-589; la-payload-schema main 9e8957a, for
  0.5.7): a byte string that does not consume reports its span again.** A `bytes`, `hex` or
  `ascii` field declaring `consume: 0` reads and reports its span and leaves the read position,
  so the fields after it read the same bytes (a whole-frame `raw`, a command's bytes). Its
  `length` may be `remaining`; PS-535 applies past the end.
  - **Measured before:** every implementation advanced past the span, so the next field misread
    or underflowed. Go, Java and C# held `consume` as an int defaulting to 0 and could not tell
    `consume: 0` from none; on the shared cases Go failed 8 of 10 decodes and all 12 encodes (a
    given echo was written as data, `00d75a00d75a`, and an omitted one was "Missing required
    field"). The validator warned PS-453 with text describing behaviour nothing had.
  - **Where:** `is_echo` (Python, which the TS013 generator imports), Go `isEcho` and
    `Field.ConsumeDeclared` (`go/schema/cr175.go`), Java `Field.isEcho`/`consumeDeclared`, C#
    `Field.IsEcho`/`ConsumeDeclared`. Each decoder's bytes/hex/ascii read restores the position;
    each fixed element size counts an echo as 0 bytes.
  - **Encode (PS-589):** an echo writes nothing and is not required (a tlv case claims it
    optionally, as a literal). A value given for it is recorded beside the CR-2026-141 peeks -
    position (the decoder's, before any pending peek bytes), field, value, and its length read
    from the input (`remaining` reaches the frame's end) - and checked where the frame closes
    (top level, each tlv value, each repeat element and trailer): Python `FrameBuffer.echo`,
    `Frame.echoes`, `check_echoes` (called by `check_peeks`); the runtime's `fr.ec`,
    `encCheckEchoes` (by `encCheckPeeks`); Go, Java and C# beside their peek records. **The
    Implementation Record's departure is followed: the comparison is with what the field
    decodes**, its `format`/`separator`, or PS-534's trailing-0x00 removal (and `numeral`),
    applied first, so `00d7` given for a `hex:upper`, `:`-separated echo is rejected. Text, the
    prototype's: `Field 'raw' is given '00d75b', but the other fields write '00d75a' over its
    span (PS-589)`, or `... write too few bytes over its span (PS-589)`.
  - **Validator:** PS-015 no longer counts a byte string declaring `consume: 0`, and PS-453 no
    longer warns on one (it still warns on `u16` and the rest).
  - **C:** `field_def_t.consume` is a bit range's and a bool's; a byte string always advances.
    `tools/schema_binary.py` refuses `consume: 0` on one (PS-446) and the harness files it as an
    interpreter gap.
  - **Open:** a `$name` length naming a value the input does not carry (a computed length, as
    jooby's `message.bytes` would need) cannot size the span on encode, so a value given for
    such an echo is refused as `Missing required field: <name>`; omitting it encodes.
  - **Cases:** `tests/cr-2026-175-cases.yaml` (the prototype's eight tests and fourteen more: a
    length from an earlier field, format and separator, ascii padding, a repeat element) read
    by `test_cr_2026_175_cases.py` (reference and generated codec, plus the validator and C
    converter rules), Go `cr175_test.go`, Java `CR2026175Test`, C# `CR2026_175Tests`. Fixture
    `_language-conformance/byte-string-reread.yaml`, 9 vectors, all round-tripping.
  - **Floors:** decode 2801 -> 2810 in Go, Java and C#; re-encode +9 everywhere (Python and
    Java 2199 -> 2208, C# 2200 -> 2209, Go 2217 -> 2226, plain 2189 -> 2198), `plain fixed`
    185 -> 194.
  - **Corpus and registry:** no schema in the corpus, library, examples or the private registry
    declares `consume` on a byte string, so no decode, encode or load changes. Written into a
    copy of registry enginko decoder-environmental (`dataPayload` before `data`, two
    `remaining` fields at one level), its 96 vectors still pass and a decode re-encodes with
    the echo given.


- **CR-2026-172 (PS-119 amended, PS-584; la-payload-schema main 9e8957a, for 0.5.7): `compute`
  `pow`, a power whose exponent may be a field.** `{op: pow, a: 10, b: $x}` computes `a` to the
  power `b` in binary64; a result that is not a finite real number - a negative base with a
  fractional exponent, 0 to a negative power, an overflow - leaves the field absent, as a zero
  divisor does (PS-278), and the rest of the payload decodes. A computed field reading that
  absent value is absent too (PS-314).
  - **Where:** Python `_evaluate_compute` (`math.pow`; a `ValueError`, `OverflowError` or
    `ZeroDivisionError`, or a non-finite result, gives `OMITTED`), Go `evaluateCompute`'s `pow`
    case (`errComputeOmitted`), Java `evaluateCompute` and C# `EvaluateCompute` (`ComputeOmitted`),
    and the TS013 generator's `compute_to_js` (`Number.isFinite` else `undefined`, which
    `JSON.stringify` drops). The op is in `tools/schema_vocabulary.py`, `validate_schema.py`,
    `generate_jsonschema.py` and `docs/SCHEMA-LANGUAGE-REFERENCE.md`.
  - **Measured before:** all five rejected the op (`Unknown compute op: pow`, an error, so the
    decode failed). Encoding is untouched: a computed field is never written.
  - **Departure from the CR's comment, from the Implementation Record:** the Dingtek chain gives
    10.0000002 for `41 20 00 00` (the vendor divides by 0x7fffff), not 10.0.
  - **C:** has no `compute`; `tools/schema_binary.py` already refuses it, so `pow` is a recorded
    C gap, not a new refusal.
  - **Cases:** `tests/cr-2026-172-cases.yaml` (the prototype's five tests, the Dingtek chain, a
    literal base e, a negative base with an integer exponent and a reference to an absent power),
    read by `test_cr_2026_172_cases.py` (reference and generated codec), Go `cr172_test.go`, Java
    `CR2026172Test`, C# `CR2026_172Tests`. Fixture `_language-conformance/compute-pow.yaml`, seven
    vectors, all round-trip (computed fields are not written): decode +7 in Go, Java and C#
    (2790 -> 2797); re-encode +7 everywhere (Python and Java 2188 -> 2195, C# 2189 -> 2196, Go
    2206 -> 2213, plain 2178 -> 2185), `plain fixed` 185 -> 192.
  - **Impact:** no corpus device schema and no registry schema uses `pow` as a compute op, so no
    decode or encode output changed and none is rejected. Registry mcci codec-catena-generic's
    `r_gas = Math.pow(10, logGasR)` becomes writable: adding `{name: r_gas, type: number,
    compute: {op: pow, a: 10, b: $log_r_gas}}` after `log_r_gas` decodes `17 40 F8 00` to
    `log_r_gas` 8 and `r_gas` 100000000 (registry file not edited here).

- **CR-2026-178 (PS-178 amended, PS-596; la-payload-schema main, for 0.5.7): `_meta` carries
  `display_unit`.** Where a field declares `unit`, its `_meta.fields` entry carries
  `display_unit`, the text exactly as declared, right after `unit`; `unit` stays the SenML name
  where `senml.unit` is declared (now a MUST) and the field's `unit` otherwise. A field with no
  `unit` has no `display_unit` (a `senml.unit` alone gives `unit` only). Derived from the schema
  alone at every depth, in an object's members and a repeat's `element`, and a `$` reference in
  `unit` is carried through `meta_reference` as `unit` is. Where it lives: Python `field_meta`,
  Go `fieldMeta` (`go/schema/meta.go`), Java `Meta.fieldMeta`, C# `Meta.FieldMeta`; the TS013
  codec carries no `_meta`, and C has none, so no binary-form refusal is needed.
  - **Measured before:** all four wrote only `unit`, choosing `senml.unit` first, so the author's
    `"°C"` was lost wherever `Cel` was declared. No corpus schema changed decode or encode output.
  - **Fixtures:** the `expected_meta` of eight `_language-conformance` fixtures gained
    `display_unit` (meta-device-context's temperature is `"°C"` beside `unit: Cel`); no vector
    added, so **no floor moves**. `make gate-verdicts` rule 3 holds the four producers equal.
  - **Cases:** `tests/cr-2026-178-cases.yaml` (the prototype's three tests as eight), read by
    `test_cr_2026_178_cases.py`, Go `cr178_test.go`, Java `CR2026178Test`, C# `CR2026_178Tests`.

- **CR-2026-179 (PS-470 amended, PS-597 to PS-600; la-payload-schema main, for 0.5.7):
  `semantic` is a TTN normalized path, and a generated codec gains `normalizeUplink()`.**
  - **PS-470:** a `semantic` that is not a string is rejected at load, in the prototype's text:
    `The semantic of 't' is a string naming a normalized measurement type, such as
    air.temperature (PS-470)`. Python `annotation_errors` (interpreter, validator, TS013
    generator), Go `checkAnnotations`, Java `Schema.checkAnnotations`, C# `CheckAnnotations` (a
    plain scalar YAML 1.2 reads as a number, boolean or null is not a string). The legacy
    `semantic: {ipso: N}` is rejected with it: `get_field_metadata`/`get_semantic_output` read
    `ipso: {object}`, `schema_binary.py` no longer reads the old form, and the vocabulary's
    `semantic` context is gone. Measured before: no corpus, library, example or registry schema
    used a non-string `semantic`.
  - **PS-600:** `_meta.fields` entries carry `semantic` unchanged, after `description`, at every
    depth: Python `field_meta`, Go `fieldMeta`, Java `Meta.fieldMeta`, C# `Meta.FieldMeta`. Before,
    none of the four carried it. `make gate-verdicts` rule 3 holds them equal.
  - **PS-597:** `semantic_warnings()` (Python, the prototype's function and words) gives the
    validator's three warnings - a path TTN does not define, a unit neither the path's nor
    converted, a path two fields of one message declare outside a repeat; `validate_schema` adds
    them as warnings. Nothing is rejected.
  - **PS-598/599:** `normalize_plan()` computes, per message (port), each path's fields and how
    each reaches TTN's unit (`TTN_PATHS`, `TTN_UNIT_NAMES`, `TTN_CONVERSIONS`, copied from the
    prototype: °F/degF, mV, Pa/kPa/bar/inHg, km/h/mph/knots/kn/kt, `/`); `run_normalize_plan()`
    runs it, and the TS013 generator embeds the same plan as `NORMALIZE_PLAN` with
    `NORMALIZE_RUNTIME_JS` (slim codecs too), only where some field declares `semantic`. A value
    with no unit on a path that has one, an unconvertible unit, and every value on a duplicate
    path are left out with a warning. **One addition the prototype lacks:** TTN calls
    `normalizeUplink` with decodeUplink's output and no fPort, so for a `ports` schema the
    message is the one given by `input.fPort` if present, else the port entries whose
    reportable keys hold every declared key of the data; where those disagree on placement,
    nothing is placed, with a warning.
  - **Go, Java, C# and C** produce no normalized payload (the generated codec and the IL do).
    C has no `_meta`; `semantic` is an annotation C carries nothing for, so `schema_binary.py`
    does not refuse it.
  - **Cases:** `tests/cr-2026-179-cases.yaml` (the prototype's nine tests as 19), read by
    `test_cr_2026_179_cases.py` (reference, validator, and the generated normalizeUplink under
    node, full and slim, which must equal `normalize()`), Go `cr179_test.go`, Java
    `CR2026179Test`, C# `CR2026_179Tests` (the `meta` and `error` cases). Fixture
    `meta-semantic.yaml` (`expected_meta` at top level, object member, repeat element): decode
    +2 in Go, Java and C# (2842 -> 2844); re-encode +2 everywhere, `repeat` 26 -> 28 (Python and
    Java 2239 -> 2241, C# 2240 -> 2242, Go 2257 -> 2259, plain 2229 -> 2231).
  - **Measured** (Python, corpus at this branch, registry read only): no decode or encode output
    changes. With semantic: corpus 132 schemas, 127 normalize every placed path, 9 PS-597
    warnings; registry 256 schemas, 126 ready, 148 place something, 354 warnings. Nearly every
    registry warning is a field with no `unit` on a path that has one (relative humidity 141,
    latitude/longitude 91, co2 41, light 34, wind direction 13, motion count 5): PS-599 leaves
    them out, where the registry's `bin/normalized.py` counts them ready (its `NO_UNIT_OK`); the
    rest are `℃` and `%rH`, not in the prototype's spellings. Duplicate paths: 10 schemas (e.g.
    Milesight am102's `temperature` beside its history object's `temperature`).

- **CR-2026-170 (PS-430, PS-582 and PS-583 new, PS-096 amended; la-payload-schema main for
  0.5.7): padded and signed bitfield_string parts.** A part format is `decimal`, `sdecimal`,
  `hex` or `hex:upper`, optionally followed by `:N`, a minimum width from 1 to 32: a part with
  fewer digits is zero-filled on the left, one with more is written in full, never cut (so
  arwin's `slice(-2)` minor of 255 reads `255` here, not the vendor's `55`). `sdecimal` reads
  the part's bits as two's complement of its width and writes `-` before the padding (`-5` as
  `sdecimal:2` is `-05`).
  - **Load (PS-430), the prototype's text, unprefixed:** `bitfield_string part format must be
    decimal, sdecimal, hex or hex:upper, with an optional :N width from 1 to 32, not 'hex:0'
    (PS-430)` - a width of 0 or over 32, a zero-led width (`decimal:02`), another base or case
    (`HEX`, `octal`). Python `bitfield_part_errors` in `schema_iterator_errors` (so the
    interpreter, the validator - whose own `'decimal', 'hex' or 'hex:upper'` check is gone - and
    the TS013 generator); Go `parsePartFormat` from `checkFieldRules`; Java `BitfieldParts.parse`
    from `checkWave4`'s bitfield block; C# `BitfieldParts.Parse` from `SchemaParser`. Each used to
    reject anything but the three old formats with its own text and a field prefix.
  - **Encode (PS-096, PS-583):** a segment is parsed by its part's format - leading zeros
    accepted, hex in either case, a `-` only on `sdecimal`, surrounding whitespace stripped,
    nothing else (`+1` is refused) - and a value the width cannot hold is rejected, never masked:
    `Field 'v': segment '128' does not fit a 8-bit sdecimal part, -128 to 127 (PS-583)` and
    `Field 'v': segment 'x1' is not a sdecimal part (PS-096)`, the segment as Python's repr.
    **Measured before:** every encoder masked an out-of-range segment into its bits (`256` in an
    8-bit part wrote 0); Python took `int()`'s grammar (`+7`, `1_0`), Go wrote 0 for a segment
    it could not parse, and the codec took `parseInt`'s numeric prefix (`12ab` as 12); Java and
    C# refused an unparsable one with their own text.
  - **One helper set per language, and the formatting is one function** so CR-2026-176's
    template formats can reuse it: Python `parse_part_format` / `format_part_number` /
    `format_bitfield_part` / `parse_bitfield_segment`, Go `go/schema/cr170.go`
    (`formatPartNumber`), Java `BitfieldParts` (`formatNumber`), C# `BitfieldParts.cs`
    (`FormatNumber`), the codec's `partFormat` / `partText` / `bitfieldPart` and the runtime's
    `encSegmentBits` (exact at 64 bits through byte and bit arrays, no BigInt). Go, Java and C#
    parse a segment through a big integer, so a 64-bit range check is exact.
  - **Not changed:** a string with fewer segments than parts still fills the rest with `0`, and
    extra segments are ignored, in all five; the prototype rejects a count mismatch.
  - **C:** has no bitfield_string; `tools/schema_binary.py` already refuses the type by name.
  - **Vocabulary:** no new key. `schemas/payload-schema.json` now describes the third element of
    a part with a pattern; `docs/SCHEMA-LANGUAGE-REFERENCE.md`'s Bitfield String section gains
    the formats.
  - **Measured:** decode and encode output unchanged, and nothing rejected at load, for all 248
    corpus and private-registry schemas carrying a bitfield_string (Python, every vector
    decoded and re-encoded before and after).
  - **Cases:** `tests/cr-2026-170-cases.yaml` (decode, encode and load lists; the prototype's
    `TestCR2026170BitfieldStringParts` case for case, plus 64-bit, nibble and width-32 cases),
    read by `test_cr_2026_170_cases.py` (reference, validator and generated codec), Go
    `cr170_test.go`, Java `CR2026170Test`, C# `CR2026_170Tests`. Fixture
    `_language-conformance/bitfield-string-padded-signed.yaml` (five ports, 14 vectors, all
    round-tripping). **Floors:** decode 2801 -> 2815 in Go, Java and C#; re-encode +14
    everywhere (Python and Java 2199 -> 2213, C# 2200 -> 2214, Go 2217 -> 2231, plain 2189 ->
    2203), `plain fixed` 185 -> 199.

- **CR-2026-169 (PS-581; la-payload-schema main, for 0.5.7): which port entry an encoder
  uses.** An encoder of a schema with `ports` encodes one entry and reports the FPort it used:
  (1) a caller's FPort chooses its entry, or `default` where none lists it, and is rejected
  where neither exists, or where the entry is declared `uplink` **and a downlink is being
  encoded**; (2) else the one entry declaring `downlink`; (3) else the one downlink-capable
  entry (`downlink`, `both` or no declaration); (4) else rejected, naming the candidates.
  `default` is never chosen without an FPort. A schema without `ports` reports the caller's
  FPort, else the document's `fPort` (PS-458).
  - **Departures followed (Jason):** an uplink entry is refused only for a downlink - the
    `direction` the caller states (Python `direction=`, Go `EncodeToResultDirection`,
    Java `encodeWithPort(..., direction)`, C# `EncodeWithPort(direction:)`) or the generated
    `encodeDownlink`, which is always one; an encode with no direction still encodes an uplink
    entry the caller names, for round trips. A match keyed by a looked-up field encodes the
    code (PS-523): already true here since CR-2026-109, pinned by the worked example's case.
  - **Where:** Python `_choose_encode_port` (called first in `_encode`; `EncodeResult.fport`),
    Go `ChooseEncodePort` (`schema.go`; fPort 0 is none given, ports being 1-255;
    `EncodeResult.FPort`, `Schema.DocFPort`), Java `Schema.chooseEncodePort`
    (`EncodeResult.getFPort`), C# `SchemaEncoder.ChoosePort` (`EncodeResult.FPort`,
    `PayloadSchemaDefinition.DocFPort`), and the generator's `encodeDownlink`, whose choice is
    computed at generation time. Rejections are reported as errors (Go: the error return), in
    the spec prototype's text, with no payload.
  - **Before:** Python raised PS-459's ValueError (uncaught) with no FPort, and Go, Java and C#
    encoded the empty top-level `fields` of a ports schema (or its `default` entry under port 0)
    with success. The generated codec took the first declared `downlink` entry, never checked
    that it was unique, took any non-uplink entry for a given port, and did not take the
    document's `fPort` for a flat schema.
  - **Message changes:** an uplink entry refused for a downlink now reads `FPort 1 is declared
    direction:uplink and takes no downlink to encode (PS-581)` in place of PS-292's wording on
    the encode path (tests in `test_cr_2026_010_direction.py` and `test_ts013_encode_parity.py`
    updated); a downlink entry encoded as an uplink keeps PS-288's.
  - **Cases:** `tests/cr-2026-169-cases.yaml` (the prototype's `TestCR2026169EncodePort` plus
    several-declared, none-capable and both-entry cases), read by `test_cr_2026_169_cases.py`
    (reference and generated codec), Go `cr169_test.go`, Java `CR2026169Test`, C#
    `CR2026_169Tests`. No corpus fixture: the runners already encode with the vector's fPort, so
    no floor moves. C has no `ports`.

- **CR-2026-174 (PS-585 to PS-587; la-payload-schema main 4203c12, for 0.5.7): checksum
  fields.** A `uN` field may declare `checksum: {algo, seed?, from?, on_mismatch?}`: `sum8`,
  `xor8` (both seeded, default 0), `crc8/smbus` or `crc16/kermit`, over the frame's bytes from
  `from` to the byte before the field.
  - **Load (PS-585)**, the prototype's `check_checksum` text and order, first failure only:
    a mapping, its keys (PS-448, without the prototype's "did you mean" hint), an unsigned
    type (aliases accepted; a bit range is not one), a known `algo`, a wide enough type,
    `seed` only on sum8/xor8 and a byte, `from` >= 0, `on_mismatch` error|warn. Python
    `checksum_field_errors`/`checksum_errors` (from `schema_iterator_errors`, so the
    validator and the TS013 generator too), Go `checkChecksum` (`cr174.go`, from
    `checkFieldRules`), Java `Checksum.check` (from `parseRaw`), C# `Checksum.Check` (from
    `ParseRoot`), C `field_set_checksum`. Vocabulary: a field key and a `checksum` context;
    closed in `payload-schema.json`.
  - **Decode (PS-586):** compared on the raw value read, before any modifier; `from` is an
    offset into the payload being decoded, so one in a match case counts the discriminator
    before it. A mismatch is `Field '{name}': {algo} over bytes {from} to {p-1} is 0x{c:X},
    but the frame carries 0x{v:X} (PS-586)`, an error verbatim (Python re-raises it from a
    nested list as a FieldError) or with `warn` a warning; `from` past the field is
    `... the checksum range starts at byte {from}, after the field at byte {p} (PS-585)`.
    Python `checksum_mismatch` in `_decode_field_inner`, the codec's `checkChecksum`, Go
    `verifyChecksum`, Java and C# in the uN case of `decodeFieldBody`/`DecodeFieldBody`, C
    `schema_checksum_check` (warn mode is `decoded_field_t.checksum_mismatch` and
    `decode_result_t.checksum_warnings`, rendered by `schema_checksum_warning()`; C has no
    text warning list, so only the first is rendered).
  - **Encode (PS-587):** never taken from the input (an input value, internal or not, is
    ignored) and never required (a tlv case claims it optionally). Written as zeros and
    filled once the whole frame is known, earliest first, so an outer check covers an inner
    one: Python `_checksum_placeholder` + `fill_checksums` (placeholders ride `Frame` and
    `FrameBuffer` like peeks), the runtime's `encChecksumPlaceholder`/`encFillChecksums`
    (an array's `ck`), Go `placeChecksum`/`fillChecksums` (one buffer, absolute positions),
    Java and C# `FrameMeta` checksums with `fillChecksums`/`Fill`, C `schema_place_checksum`/
    `schema_fill_checksums`. Rejected as `Field '{name}': the bytes its checksum covers are
    not all written when it is reached (PS-587)` where peek bits are pending, where `from`
    is past the field, and **inside a tlv entry or a repeat element or trailer**, which are
    encoded apart from the frame (the prototype's sub-context departure, applied to
    encoding). Measured before: Python, Go, Java, C#, the codec and C all ignored the key -
    a bad check value decoded with success, and encode took the value from the input
    (`Missing required field`, or PS-434 for `_crc`); C's `schema_binary.py` dropped it.
  - **C:** implemented in the struct API in existing padding (`field_def_t` 1640 and
    `schema_t` 52536 bytes, unchanged); the binary form refuses `checksum` (PS-446). The
    fixture is port-based, which the harness skips; split per port it passes 3 of 7, the
    rest failing on C's uppercase, 15-byte-capped `hex` (a C gap, not this CR's).
  - **Not settled:** a checksum on a `byte_group` member is ignored by Python and refused by
    C; the spec does not place one there.
  - **Cases:** `tests/cr-2026-174-cases.yaml` (the prototype's `TestCR2026174Checksum` and
    the evidence's vendor frames: acrios sum8 from 6, emu crc8/smbus, jooby xor8 0x55, seeed
    crc16/kermit LE), read by `test_cr_2026_174_cases.py` (reference, validator, codec),
    Go `cr174_test.go`, Java `CR2026174Test`, C# `CR2026_174Tests`; C selftests in
    `selftest_schema.c`. `encode-round-trip.py` files a warned mismatch as
    `checksum-mismatch` (inherent).
  - **Floors:** fixture `checksum-fields.yaml` (8 vectors, one `expected_error`): decode
    2801 -> 2809 in Go, Java and C#; re-encode +6 everywhere (Python and Java 2199 -> 2205,
    C# 2200 -> 2206, Go 2217 -> 2223, plain 2189 -> 2195). Python files it `plain fixed`
    (185 -> 191); the Go, Java and C# text scans file it `match` (173 -> 179) because
    `on_mismatch:` contains `match:`. No corpus or registry schema declares the key, so no
    other vector changed (failing sets diffed in Go, Java and C#).

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
python3 tools/crossvalidate_ttn.py --devices-repo ~/Workspace/lora/tools/lorawan-devices \
    --vendor milesight-iot --schema-dir schemas/devices/milesight
```

Every payload the two agree on is a test vector you have earned: record it with
`source: vendor-codec`, which is independent provenance under PS-264 and the
honest way for one of these schemas to leave Rejected tier. A disagreement is a
finding to investigate, not a number to overwrite — and vendor data can be wrong
too, so check the decoder against the vendor's own documentation when they differ.

**Re-clone the Decentlab oracle when you need it**; it is not vendored here:
`git clone --depth 1 https://github.com/decentlab/decentlab-decoders`. The TTN
checkout at `~/Workspace/lora/tools/lorawan-devices` is permanent.

## Rules for this repository

**Test vectors must come from outside our own decoder.** A vector produced by
running our interpreter and recording what it printed proves nothing — it locks
in current behaviour, including bugs. Derive expected values from the vendor's
documentation or from the vendor's published TTN codec, and treat a disagreement
as a finding to investigate, not a number to overwrite. Regression vectors
generated from our own output are acceptable only when labelled as such.

**An OPTIONAL feature must not be asserted in the shared corpus.** A fixture's
`expected` is checked by all five implementations, so listing a key only some of them
produce fails the conformant ones. `metadata-enrichment.yaml` carries a `metadata`
block and lists only its two decoded fields; the enrichment those keys imply is
checked in `tests/test_cr_2026_036_metadata_enrichment.py`, against the one
implementation that has it. What the fixture is *for* is the other half of the
requirement - that a schema carrying an optional block still decodes its payload
identically everywhere, which all five now demonstrate including C.

**A capability in the meta-schema is not a capability in the specification.**
`metadata`, `encode_formula` and `input_metadata` sat in `schemas/payload-schema.json`
from the initial release with no clause defining any of them, and grepping the spec for
their key names was what hid it: `mode`, `source`, `field`, `name` and `format` all
appear in the spec for unrelated reasons, so a name-match said "documented" for keys
nothing described. Check whether the *construct* is described, not whether its key
names occur. CR-2026-036 reported five undocumented keys and the real answer was a
whole block of twelve.

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
    `match:` key. The older `type: match` with `on:` spelling is withdrawn, and
    its `on` and `cases` keys left `definitions.field`.
  - `byte_group` and `repeat` (CR-2026-019), seven field-level keys — `byte_group`,
    `size`, `count`, `byte_length`, `until`, `max`, `min`. `byte_group` has two
    spellings, an object carrying its own `size` and an array taking `size` from the
    field beside it; only the object form has corpus coverage, and a test says so
    rather than forbidding the other.

  `repeat`'s `max` and `min` were honoured by the four interpreters and not by the TS013
  generator until CR-2026-021, which closed the last such gap: `max` is now a ceiling on
  all five (defaulting to 1000, clamping a `count` and bounding the byte_length and
  until-end loops) and falling below `min` fails the decode on all five. `min` has no
  corpus fixture on purpose — it fails the decode, and no vector kind expects a failure.
- **A `byte_length` span must be consumed exactly, and CR-2026-022 made that true on all
  five paths.** PS-088 requires the members to divide the span; the Python, Go and Java
  interpreters enforced it and **the C# interpreter and the TS013 generator had no
  post-loop check at all**. Both ways of failing were accepted silently, and both leave
  the read position somewhere other than the span's end, so **every field after the
  repeat comes from the wrong offset with nothing reported**:

  - a ceiling stopping the loop early (`byte_length: 4` with `max: 2` over one-byte
    members leaves two bytes unread);
  - members that do not divide the span — a 2-byte member over a 5-byte span starts an
    iteration at offset 4, inside the span, and finishes at 6, past it. That one produced
    a third record holding the *following field's* byte, and the following field then read
    past the payload as zero.

  Both are errors now, everywhere. `max` and `byte_length` no longer report the conflict
  as a payload problem either: stopping at the ceiling names the ceiling and the bytes
  left, rather than the old `byte_length mismatch: expected end at 4, got 2`, which read
  as a short payload when the cause was the schema's own cap. The failing cases have no
  fixtures — no vector kind expects a failure — so
  `repeat-byte-length-span.yaml` pins the valid case with a field after the repeat, which
  is what makes a position left short visible at all.

  Every construct is now described. **Closing `definitions.field` itself is a different
  and much larger change** — it declares no required keys and types `type` as a bare
  string, so `s17` and a nameless field are still accepted — and nothing depends on it.
  `tools/validate_schema.py` remains the place for real structural checking.
- **`match` is now uniform across the five implementations — CR-2026-020 closed the
  gap CR-2026-018 found.** Every key (`field`, `length`, `name`, `var`, `cases`,
  `default`) is honoured everywhere. Do not reintroduce a partial reader: the six
  `match-*.yaml` fixtures in `_language-conformance` exercise each key on all five
  paths, and `test_cr_2026_018_match_meta_schema.py` now fails if the corpus *stops*
  exercising them, which is the opposite of what it originally asserted.

  What had gone wrong is worth knowing, because three of the four were parse-time
  drops that no test could see:

  - **Go** dropped `length`, `name`, `var` and `default` in the parser while
    `decodeMatch` already honoured `length` — and since it defaults to 1, a two-byte
    discriminator was read as one byte and every field after the construct came from
    the wrong offset. It also had no `"2..5"` case keys, made a `default` case key an
    ordinary case matching the string `"default"`, and built its case list by ranging
    over a Go map, so where two keys could match the same value the winner varied
    between runs. Cases are sorted now: integers ascending, then strings, default last.
  - **Java and C#** read `length` but never reported `name`, stored `var`, or read
    `default`.
  - **The TS013 generator** had no inline discriminator at all, and interpolated case
    keys straight into a comparison — a range key emitted `vars.kind === 2..5`, which
    is not valid JavaScript, so the whole codec failed to parse and the schema had *no*
    generated path rather than a wrong one.

  `default` defaults to `error`, so an unmatched value with no `default` now fails the
  decode on all five instead of silently skipping on four.
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
- **The C interpreter is measured now, and the gap is coverage rather than correctness
  (CR-2026-032).** `make test-c` builds the three previously-orphaned C test files and runs
  `tools/c-corpus-harness.py`, which generates C that builds each expressible corpus schema
  through the struct API, compiles it, and compares the decode against the vectors.
  **575 of 575 attempted vectors pass** (2026-10-13) since CR-2026-033 added `tlv` and CR-2026-034
  `flagged` (it was 50 of 50 before either). 751 of 1239 are still in schemas the struct API
  cannot build - 26 use a `transform` chain, 24 a `bitfield_string`, 15 more cases than
  `SCHEMA_MAX_CASES` allows, 3 `repeat`, 3 a `u32le16` **the interpreter has and the harness
  has no constructor for**. The report names which side each limit is on, and that
  distinction is load-bearing: `no constructor for type 'u32le16'` used to read as a C gap
  when C decodes it perfectly well.

  **The C-interpreter wave (2026-10-11)** closed these, one PR each; the figures are the
  harness's at each step (struct API / `--from-blob names` / `--from-blob nameless`):
  - **Multi-byte and signed bit ranges (CR-2026-136; PS-059, PS-352, PS-353, PS-365)**:
    554 -> 556 attempted, all passing (`wide-bit-range.yaml`); blob 64/64 unchanged. A
    range was read from one byte, unsigned, whatever its type said, and the harness built
    every `uN[...]` that way too. Now `field_def_t.size` is the base's bytes (0 is 1), read
    in the field's effective byte order; `bit_signed` (in what was padding, so
    `sizeof(field_def_t)` is unchanged) sign-extends from the range's width; and
    `consume` is a byte count (`consume: 2` advances two), which it already was in the
    YAML. `field_bit_range(name, base_bytes, signed, start, width, consume, endian)`
    builds one; `field_bits` keeps its one-byte meaning. A bit range now reports through
    the integer member (it was tagged `FIELD_TYPE_F64` though nothing scaled it). Encode
    ORs the range into its base in the same byte order, checks an `sN` range against
    -2^(w-1)..2^(w-1)-1 and names `s16[12:15]` in the PS-168 text, and the PS-551 peek
    bookkeeping in `schema_encode` holds a mask in the base's bit space, so a peek spans
    its base's bytes. A short base is PS-535's peek message, a short `consume` its need
    message, as the reference words them. A full `u64[0:63]` past 2^63 is
    `SCHEMA_ERR_UNSUPPORTED` (no int64 holds it). **The binary form refuses both**: its
    bitfield record carries a start and a width, its base is one unsigned byte, and its
    consume marker is one byte, so `tools/schema_binary.py` now names a wide or signed base
    and a `consume` above 1 rather than writing something that decodes differently; the
    loader sets a bitfield's `size` to its one-byte base (the record's size nibble is the
    width). `bit-range-order.yaml` still waits on `byte_group`.

  **The C-interpreter wave (2026-10-11)** closed these, one PR each; the figures are the
  harness's at each step (struct API / `--from-blob names` / `--from-blob nameless`):
  - **`byte_group` (CR-2026-131, CR-2026-137; PS-063, PS-070, PS-364, PS-365, PS-397,
    PS-535)**: 556 -> 565 attempted, all passing (`wide-group`, `byte-group-width`,
    `byte-group-bool`, `bit-range-order`); blob 64/64 unchanged. `FIELD_TYPE_BYTE_GROUP`
    was an enum value with no decode or encode path. Built with
    `field_byte_group(size, endian, member_start, member_count)`, its members placed above
    `field_count` (`nested_start`/`nested_count`, which nothing used). The group is one
    value of `size` bytes, or its widest member's base where 0, assembled in its own byte
    order; each member - a bit range of any base, a whole integer, a `bool` at bit 0-7 -
    is decoded as a bit range of that value through `decode_field`, so its sign,
    modifiers, lookup and variable apply as any field's. Load-time rules are checked at
    decode and encode, with the reference's text: a `size` narrower than a member, a `bool`
    past bit 7, an overlap (PS-397), and PS-535's "byte_group of 'a', 'b'" for a short
    read. Encode checks each member as the member it is (PS-168 names `s8`, not the
    group), ORs it into the group's value, writes the value once, zero where no member
    names bits; a member left out is "Missing required field", an internal one PS-434's
    error. The top-level loop, a tlv case and a flagged group all dispatch it; the harness
    builds a top-level group only (two schemas with one nested in a case body are a harness
    limit, said so). This branch carries the multi-byte/signed bit range header change
    verbatim, since members use its base helpers. **The binary form has no byte_group
    record** and still refuses one. `sizeof(schema_t)` unchanged at 52536.

  **The C-interpreter wave (2026-10-11)** closed these, one PR each; the figures are the
  harness's at each step (struct API / `--from-blob names` / `--from-blob nameless`):
  - **`ascii` (CR-2026-114, PS-534, PS-537, PS-538)**: 554 -> 558 attempted, all passing;
    blob 64/64 unchanged. Decode keeps an interior 0x00 (`decoded_field_t.text_len`; the
    C string in `value.str` stops early, the value does not), reports each byte as itself -
    ISO 8859-1, so a JSON writer maps 0x80-0xFF to U+0080-U+00FF - and raises
    `decoded_field_t.non_ascii` for PS-073's warning, which the harness renders. A value
    longer than 31 bytes after its padding is `SCHEMA_ERR_UNSUPPORTED` naming the field,
    never truncated as it was. Encode, which had no `ascii` case at all, takes text through
    `encode_inputs_add_text(inputs, name, bytes, len)` (or `_add_string`): Latin-1 bytes,
    the form decode reports, so a decoded value re-encodes to its bytes. It pads with 0x00,
    rejects a value longer than `length` with PS-537's message, and sets
    `encode_result_t.non_ascii` for its warning. A character above U+00FF cannot be put
    in that input, so PS-537's rejection of one belongs to whatever converts Unicode text
    to it (a gateway's JSON reader). Text for a numeric field, and a number for an `ascii`
    one, are `SCHEMA_ERR_TYPE`. **The binary form still refuses `ascii`**: its type code
    `bytes` is shared with `hex` and `bytes`, every code 0-7 is taken, and a new one would
    be a format version - not invented here.

  **The C-interpreter wave (2026-10-11)** closed these, one PR each; the figures are the
  harness's at each step (struct API / `--from-blob names` / `--from-blob nameless`):
  - **PS-523 binding (CR-2026-109, CR-2026-132)**: a `var_name` binds the value after
    `mult`/`div`/`add` and before the lookup, in `finish_numeric`, the one place both
    happen. Measured before: a text label and an enum already bound the number - C bound
    the raw read, which is the same thing until a modifier is present - but a scaled field
    bound its raw value (`div: 2` on raw 4 bound 4, not 2), a zero divisor (PS-100) left
    the raw value bound where PS-523 leaves the reference unbound, and an `f16`/`f32`/
    `f64`/`udec`/`sdec` never bound at all. `variable_t` now carries the binding as a
    `double` beside the exact `int64_t`, with `integral`; a `match` compares only an
    integral binding with its keys (a fraction reaches the default case alone, PS-524) and
    a `flagged` mask with a fraction is `SCHEMA_ERR_MATCH`. A lookup reporting a number
    binds that number under PS-523; C holds every label as text (PS-106, a known C gap),
    so it has none to bind. **The harness count does not move** (554/64/64 before and
    after): the corpus asks PS-523 of an implementation only through
    `lookup-reference-binding.yaml`, `mask-reference-binding.yaml` and
    `tag-component-reference-binding.yaml`, all port-based, and through inline `match`,
    which the harness does not build - and C's `match` has no builder, decodes its case
    bodies as counted fields (so the top-level loop walks them again), treats a zeroed
    `match_list` as matching 0, and skips an unmatched value where the default
    `default: error` (Match Default Handling) makes it an error. That construct is the next C item, not a harness tweak. Selftests cover the
    four rules through a `flagged` mask.

  **The C-interpreter wave (2026-10-11)** closed these, one PR each; the figures are the
  harness's at each step (struct API / `--from-blob names` / `--from-blob nameless`):
  - **Index-keyed encode (CR-2026-146, PS-555 amended)**: "an encoder working from such
    a form takes each value by the same index". A nameless schema (`schema_t.nameless`,
    a v3 blob without its name table) decoded by index but could not be encoded, its
    inputs being keyed by name. `encode_inputs_add_int_at(inputs, index, v)` and
    `_add_double_at` key a value by field index (stored under the key `#<index>`, with
    `by_index`); `schema_encode` looks a nameless schema's fields up by that key, so a
    message names the field `#3` ("Missing required field: #3", "Field '#2': 256 encodes
    as 256, ..."). **The two kinds are not mixed, a decision the CR leaves to the
    implementation**: a name-keyed input for a nameless schema, or an index-keyed one for
    a named schema, is `SCHEMA_ERR_PARSE` naming the input, rather than being ignored and
    the field reported missing. Decode counts unchanged (554 / 64 / 64); the harness's
    `--from-blob nameless` run now also re-encodes every decode from index-keyed inputs:
    **58 of 64 come back byte for byte**, 2 are lossy in the reference too (leftover bytes,
    non-zero skip padding), 2 carry a lookup label (C's encoder takes the code) and 2 a
    `bytes` value (no index-keyed input for one). `sizeof(schema_t)` and
    `sizeof(encode_inputs_t)` unchanged (52536, 2312).
    `encode_inputs_add_text_at` / `_add_string_at` key an `ascii` value the same way, and a
    byte_group's members are keyed `#<n>`, n their position in the field array
    (`nested_start + k`, the `field_index` decode reports them under); decode now reports a
    nameless schema's members, which it dropped. Both join the mixed-keying refusal.

  **A `flagged` construct's mask field must declare `var_name`.** This interpreter records a
  value in its variable table only where a field declares one, while a YAML `flagged` names a
  field - so the harness patches it in. `var_has()` exists so a missing reference is
  `SCHEMA_ERR_MATCH` rather than a mask of zero, which would decode nothing and report
  success. A `tlv`, `flagged` or `match` case body goes **above `field_count`**, reached only
  through `case_def_t.field_start`; adding it as a counted field makes the top-level loop
  decode it twice, which is what the existing `match` tests tolerate.

  **`tlv` in C, and its limits.** A tag is packed into `case_def_t.match_value` - one byte
  is its own value, two components are `(first << 8) | second`, which is exact because
  every tag component in the corpus is a `u8` and none has more than two. `schema_tlv_tag()`
  is the only place that encoding lives. **Case bodies are placed above `field_count`**
  through `schema_place_field()`: the `match` convention of adding them as counted fields
  makes the top-level loop decode each a second time, which its own test tolerates only
  because it asserts `field_count >= 2` rather than what was decoded. `merge: false` and
  `unknown: raw` are not represented - both need a channel `decode_result_t` does not
  have - and this interpreter has no text warning channel. Since CR-2026-085,
  `decode_result_t.bytes_unread` gives a caller the count PS-472 reports (from
  `bytes_consumed`, which an unknown tag with no length now rewinds to the tag), and the
  harness turns it into the warning; an unknown tag's own name has nowhere to go, so the
  six vectors expecting one are counted and listed separately rather than hidden.

  **The fixed-size limits are the real boundary, not an oversight.** `sizeof(schema_t)` is
  51 KB because every `field_def` carries `cases[16]` and `lookup[16]` unconditionally.
  Raising `SCHEMA_MAX_FIELDS` to fit mla20's 67 fields would put it past 110 KB, which
  defeats the point of a firmware-tier interpreter. Its largest *capability* gaps are
  `transform` (26 schemas, and with it `polynomial`, `sqrt`, `pow`, `log`, `floor`,
  `clamp`) and `bitfield_string`/`version_string` (24); `repeat` is the last construct with
  no field type at all, at 3. This said the first two were worth "widening the harness to
  cover", which was backwards - they are the interpreter's gaps, not the harness's, and
  CR-2026-035 corrected it here, in SESSION-NOTES.md and in the harness's own skip
  reasons, which had said "not built by the struct API" and were read as harness limits.

  Two things to keep straight when reading that report. **A skipped schema is not a passing
  one**, and **a harness limitation is not a C gap** - inline `match` and `$ref` are
  named separately because C supports them and the harness does not. `byte_group` and
  `object` used to be listed with them in error: they are enum values with no decode
  path, so they are C gaps. Ports are the other way round and were listed here in error: the header says
  outright that it has no port selection, so a port-based schema is a C gap. When in
  doubt, grep the header before writing down which side a limit sits on - every
  misattribution in this file and in SESSION-NOTES.md would have been caught by that.
  Adding a construct to C without extending the harness would produce a feature with no
  cross-check, which is why the TLV work is CR-2026-033 and not CR-2026-032.

  `src/test_comprehensive.c` is deliberately out of `make test-c`: 22 of its 160 assertions
  encode the pre-CR-2026-009 lookup and enum behaviour that PS-105/PS-269 changed, and its
  little-endian `s24`/`u64` cases fail while the interpreter decodes both correctly when
  driven directly. Stale test, not a defect - updating it is its own change.
- **CR-2026-004 is implemented in Python, Go, Java and C#, not in C.** `name_from`
  and `!`/`*` case keys do not apply to the C interpreter: its TLV support (CR-2026-033)
  packs a tag into one integer and has no wildcard keys, it has fixed-size name buffers, and it consumes a binary schema with no place for a
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
- **Many published schemas are still unverified.** 41 of 241 device schemas ship
  with no test vectors and are therefore Rejected (measured 2026-09-24). See the per-device table in
  [`docs/INDEX.md`](docs/INDEX.md). The decentlab and milesight families have been
  through a vendor cross-validation pass; the rest have not.
- **Decentlab: 58 of 58 agree with the vendor decoder.** `dl-zn2` read one word of a
  two-word value and `dl-iam` read a word the device does not send, so both mis-decoded
  the vendor's own examples; `dl-zn2`'s only vector was `source: generated` and pinned
  the bug. `dl-iam`'s `max(max(a,b),0)` is built from `compute` and `guard` alone.
- **`pow`, `sqrt`, `abs`, `log` and `log10` stages exist in all four YAML
  interpreters** (see "The unary maths transform stages" above), so a square can be a
  `pow: 2` stage. `floor`, `ceiling` and `clamp` are in all five since CR-2026-058; any
  stage outside the PS-115 table - `{round: n}`, `{op: floor}`, `{sub: n}` - is rejected,
  never skipped (PS-390).
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
- **No TTN declared example has been shown stale; the "stale" ones were a YAML
  reader.** This note used to say `ws50x`, `uc1114` and `uc1152` declared `true`
  where their decoders yield `"on"`. They declare `switch_1: on` *unquoted*, which is
  the string the vendor means and which PyYAML - a YAML 1.1 reader - turns into
  `True`. `crossvalidate_ttn.py` now reads codec files with YAML 1.2 booleans (only
  `true`/`false`), and milesight went 50 -> 53 agreeing: `ws50x`, `ws301` and `ws52x`
  had been blamed on the vendor's example rather than on our reader. `uc1114` and
  `uc1152` still disagree, but with both oracles *agreeing with each other* against
  us - schema gaps, not stale examples. **Any tool that reads TTN examples must use a
  1.2-boolean loader**; `crossvalidate_ttn._CoreBoolLoader` is the one to reuse.
  Still prefer the decoder when the two oracles genuinely differ; the tool reports
  them separately so that is visible.
- **Units come from TTN's payload schema, not from guesswork.** `lib/payload.json`
  in the device repository documents each normalized measurement and its unit, and
  the vendor's raw values are already in those units. Note that TTN's normalized
  `battery` is a *voltage* while the milesight raw field is a percentage 0-100.
  Those fields once declared `sensor: percentage` to stop the scorer detecting
  them as IPSO 3316 voltage. The scorer now follows IL-204 (below), so the word
  `battery` asks for no object at all; the `sensor:` key is gone from every schema
  and strict validation rejects it (PS-448). Quantities whose SenML unit is a different scale (pressure in
  hPa against SenML's Pa, distance in mm against m) carry a `unit:` but no
  annotation, because a mismatched annotation misdeclares the value.
- **Flags packed into a byte need `consume: 1` on the last field.** An explicit bit
  range (`u8[4:4]`) never advances the read position by itself, so a channel of bit
  ranges consumes nothing and the TLV loop reads the value byte as the next tag.
  `ws50x` and three others failed to decode the vendor's own payload for this
  reason. One case remains unsolved: `em320-tilt` packs a flag into bit 0 of bytes
  already consumed by its angle fields, which no current construct expresses.
- **Milesight units.** None of these schemas declare `unit:`, so they
  carry no semantic annotations; units have to be established per device before
  annotating.
- **The scorer's detectable sensor fields are IL-204's.** `tools/score_schema.py`
  expects an IPSO annotation only where the Integration Layer's TTN classification
  rule (IL-204, la-integration-layer `docs/guides/protocol-mappings/ttn-normalized.md`)
  places a field from its name: one of `temperature`, `humidity`, `pressure`,
  `precipitation` or `moisture`, with no qualifier word (`max`, `threshold`, `target`,
  `raw`, ...) and no medium that lacks a TTN path (`internal`, `leaf`, `board`, ...).
  The old name-to-object table, under which `battery`, `voltage`, `current` or
  `latitude` each demanded an object, is gone. A unit never selects an object (IL-203).
  A field whose `lookup` reports text (string labels, or only a `${value}` default) is a
  label, not a reading, so a quantity word in its name asks for nothing
  (`temperature_sensor_status`, `temperature_unit`); a lookup with numeric labels feeds
  arithmetic and its field stays a reading (`lookup_reports_text`). This replaced the
  `sensor: none` opt-out; the IL agreed the scorer may refine IL-204 this way.
  A generic object (3300, 3320, 3325, 3332) is never a mismatch (IL-199). A
  name-silent field's annotation is neither expected nor flagged. See
  `docs/IPSO-REFERENCE.md`. **A schema with no field IL-204 places gets half the
  semantic points** (`NO_DETECTABLE_SEMANTIC_CREDIT`, 10 of 20), by decision on
  PR #123. Giving none dropped 12 Platinum schemas whose only fields were battery,
  voltage or position to Silver. Giving all 20 lifted 257 registry schemas to
  Platinum without checking anything. The scan now descends into a field-level
  `match:` too. Before that, laq4's temperature and humidity were never seen.
- **The TS013 generator's shared-byte bug is fixed**: bare bit ranges, `consume` and
  `bool` with `bit:` decode correctly in generated JavaScript, and
  `tools/vector-verdicts.py` holds both paths to every corpus vector. What remains is
  the generator reading a bit range's base in the schema's byte order where PS-059 says
  big-endian (see SESSION-NOTES). A field named like a codec variable (`d`, `w`, `pos`,
  a helper's name or a reserved word) is staged as `name_` and reported under its own
  name; `d` used to replace the output object, so decodeUplink returned `data: 0`.
- **The TTN conversion found more generated-codec defects (2026-10-10), most now a
  `_language-conformance` fixture.** None is a language question; each made the codec
  disagree with the reference on a payload the reference decodes:
  - *Anything wider than 32 bits went through JavaScript's bit operators or a double.*
    A `bitfield_string` part above bit 31 read the wrong byte (`21.226.2.5602` for
    `3.5.2.5602`); a bit range of a `u64`/`s64` base, and a member of an eight-byte
    `byte_group`, divided a value `readU` had already rounded or turned into a decimal
    string past 2^53, and read 0 or lost low bits (nke-watteco celso). All three now
    move bits one at a time into a byte array (`bitBytes`, `bitRange`), exact at any
    width; the encoder places a segment past 2^53 from its digits. **Java and C# had
    the 64-bit `bitfield_string` part too**: both take a shift count mod 64, so the mask
    `(1 << 64) - 1` was 0, and Java parsed and printed segments signed.
  - *A composite tlv tag with a bit-range component* (comtac lpn-td1's `[u8, u8[0:3]]`,
    the count in the low nibble of the value's first byte) read the range as a whole
    byte and advanced. **Go, Java and C# had the same defect** and now read it as any
    bit range: in place, advancing by `consume` alone (PS-060).
  - *A list sharing its name with one of its members* declared one `var` for both;
    the list's variable is numbered now. *A counted list of elements reading no bytes*
    stopped after one, on a zero-progress guard meant for byte-bounded lists.
  - *An unnamed member of a nested `object`* (a `skip`, or a `match`) was filtered out
    and consumed nothing; an object with `fields: []` was an unknown type and no codec
    was generated at all (quandify).
  - *A tag component's `var:`* was never bound, so a case's `name_from: "voc_${ch}"`
    failed (rakwireless). The codec binds it now; no fixture yet, because the reference's
    tlv case loop does not apply `name_from` either - that is Python's own gap.
  - *A key that is not an identifier* - `4_20_mA`, `"1"`, `"[06] Button Totals"` - emitted
    `var 4_20_mA` (the codec did not load) or mangled to a leading `_`, which the codec
    then drops as internal. Such a name is staged with an `n` prefix and restored.
  - *`ascii` trailing NULs* are stripped, as the reference does, and nothing else;
    the specification says nothing about padding. **Java removed every NUL and trimmed
    spaces**, so `"HC "` read as `"HC"`; it now strips trailing NULs only, like Go and C#.
- **The generated codec never bound `var:` on a float field** (found by the registry's NU-02 batch,
  MCCI model4841: `{type: uflt16, var: va}` then `{type: number, ref: $va}` reported nothing).
  The decode paths for the minifloats, bf16, f16/f32/f64 and `udec`/`sdec` (and an `ascii`
  `numeral`) called `_bind_js(..., False)`, which skips the `var`; only the field's own name
  was bound, so `$name` worked and `var:` did not. Measured on 22 probes (11 types, each
  through `var` and through its own name): Python, Go, Java and C# bound all 22, the codec
  lost the 9 `var` ones (`f32le16`/`f32be16le` were fine, they take the integer path). C has
  no minifloat or bf16. The four calls now bind the var;
  `_language-conformance/minifloat-reference-binding.yaml` holds all five to it: decode +22
  in Go, Java and C#, re-encode and `plain fixed` +22 everywhere (all 22 round-trip). A
  byte_group member still binds no `var` in the codec (not measured here).
- **The generated codec's encoder is a runtime port of the reference's**, not code
  unrolled per field. `ENCODER_RUNTIME_JS` in `tools/generate_ts013_codec.py` mirrors
  `encode`/`_encode_field_list` function by function, each marked with the reference
  function it ports, and walks the field list embedded beside it as JSON. The unrolled
  encoder round-tripped 960 of the 1680 vectors the reference round-trips: it undid
  modifiers in key order, never undid a stage, a lookup, an `encoding` or a sentinel,
  wrote floats as integers and `s32` negatives wrong, wrote nothing for `match`,
  `object` and `repeat`, and ignored the fPort it was asked for.
  `tests/test_ts013_encode_parity.py` now holds `encodeDownlink(decodeUplink(p))` to
  the reference's `encode(decode(p))` on every corpus decode vector - 1999 identical,
  358 failing in both - so **a change to the reference's encoder needs the same change
  in the runtime**, or that test fails. The cost is size: about 31 KB of runtime in every
  codec, which took the median generated codec from 18 KB to roughly 50 KB. `--slim`
  (`TS013Generator(schema, slim=True)`) leaves the encoder and the commentary out - median
  about 12 KB - and its encodeDownlink reports that; a test holds its decode to the full
  codec's over the corpus. The generated codec is chiefly a way to test and verify a
  schema: the deployment target is the interpreted schema, which replaces a device's JS
  codec rather than generating another one.

- **CR-2026-171 (PS-390 amended; la-payload-schema main 9e8957a, for 0.5.7): `ties: up`.** A
  `round` stage's `ties` is `even` (default), `away` or `up`; `up` rounds an exact tie toward
  +infinity, as JavaScript's `Math.round` and `Math.floor(x + 0.5)` do, so -2.5 is -2 and -3.5 is
  -3 (`away` gives -3 and -4, `even` -2 and -4). Any other value is rejected at load. Encoding is
  untouched (a round stage is not inverted; PS-552).
  - **Where:** Python `round_decimal` (`ROUND_HALF_UP`, or `ROUND_HALF_DOWN` for a negative exact
    value) and a new load-time check in `transform_stage_errors` - Python used to fail only when
    the stage was applied, so a schema with `ties: down` passed the validator and the generator
    refused it separately; Go `roundHalfAwayDecimal(value, decimals, up)`; Java `applyTransform`
    (`HALF_DOWN` for a negative value); C# `RoundHalfAwayDecimal(..., up)`; the TS13 generator's
    `roundHalfUp` over `roundDecimal`, which now takes the rule by name (`'even'|'away'|'up'`).
    Go, Java and C# already rejected an unknown `ties` at load. C: `schema_binary.py` refuses
    every `transform`, so `ties` is a recorded C gap.
  - **The CR's own example does not hold on exact values.** Raw -999 over `div: 20` is -49.95,
    which binary64 stores at -49.9500000000000028, just beyond the tie; PS-390 rounds the stored
    value (2.355 is not a tie either), so every rule gives -50.0. The spec prototype passes it
    because it rounds `Decimal(repr(value))`, the shortest text, which this repository does not
    do. The cases use exact ties (`div: 2`, `div: 4`, `div: 1000` for -0.125) and pin the CR's
    example as `-50` (`the_cr_example_is_not_a_binary_tie`). Vendor JS rounds `x * 10`, where the
    multiply does land on the tie; a schema wanting that needs `mult: 10` before the round stage.
  - **Cases:** `tests/cr-2026-171-cases.yaml`, read by `test_cr_2026_171_ties_up.py` (reference,
    validator, TS13 generator and the generated codec under node), Go `cr171_test.go`, Java
    `CR2026171Test`, C# `CR2026_171Tests`. Fixture `_language-conformance/round-ties-up.yaml`
    (one vector, negative and positive ties beside `away` and `even`): decode floors 2790 -> 2791
    in Go, Java and C#; the vector cannot re-encode, so no re-encode floor moves.
