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
| Specification | The LoRa Alliance payload schema specification, in development (companion to TS013) | The normative language definition and conformance requirements |
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

Gold and Platinum additionally have **gates**, not just points. A
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
   vector a schema is capped at Silver.

```bash
python3 tools/score_schema.py schemas/devices --all --baseline score-baseline.json

# Score the rubric without the provenance gate.
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

The payload vectors in `schemas/devices/` are the shared cross-language test set. Every
implementation has a runner that reads the same YAML and the same vectors:

| Implementation | Decode runner | Re-encode runner |
|---|---|---|
| Python | `tests/test_corpus_conformance.py` | `tests/test_encode_round_trip.py` |
| Go | `go/schema/corpus_conformance_test.go` | `go/schema/corpus_encode_test.go` |
| Java | `bindings/java/.../CorpusConformanceTest.java` | `bindings/java/.../CorpusEncodeRoundTripTest.java` |
| C# | `dotnet/PayloadSchema.Tests/CorpusConformanceTests.cs` | `dotnet/.../CorpusEncodeRoundTripTests.cs` |
| C | `tools/c-corpus-harness.py` (each schema the struct API can build) | `src/test_encoder.c` (unit tests) |

`make check-floors` prints each floor beside its own implementation's actual count; trust
it over any figure written down anywhere.

- **Floors are ratchets.** Each runner compares its pass count against a committed floor,
  so a gap stays visible and a regression fails the build. Every YAML implementation
  decodes the whole corpus, so any decode failure is a regression. Raise a floor when you
  close a gap; never lower one without saying why.
- **Floors are per shape** (`tlv`, `flagged`, `match`, `byte_group`, `repeat`, plain
  fixed), so a regression in one cannot hide behind the mass of another.
- **Every vector is an encode assertion for free:** `encode(decode(payload)) == payload`.
  It cannot hold everywhere (a `skip`'s bytes, a rounding stage, a lookup `default`), so
  `tools/encode-round-trip.py` gives a reason for every vector that does not round-trip,
  and a test asserts none is unexplained.
- **Compare implementations by the vectors they fail, not by their totals.** Two
  implementations at one count can fail different vectors, and per-shape counts are not
  comparable across harnesses. Use `tools/encode-round-trip.py --list` and diff the sets.
- **The defect to watch for is a silent write of nothing**: a dispatch that does not list
  a type writes no bytes and returns success. Prefer reporting a value you cannot write
  over skipping it.
- **What a cross-check declines to compare, it silently blesses.** If you extend a
  comparison tool, make sure a new value shape is compared rather than skipped. A helper
  returning `(ok, detail)` is always truthy under `if not helper(...)`.
- **One assertion per vector when probing a divergence**: a runner stops at a vector's
  first mismatch, so several assertions hide all but one disagreement.
- **A feature-specific test bounds a floor; it never pins one**, since later
  changes raise the same floors.
- **Anchor a source-reading test on something that occurs once**, and say where the
  search starts.
- **A vector's port is written `fPort` or `fport`**; a runner must read both.
- `schemas/devices/_language-conformance/` holds one fixture per construct no device
  schema uses, so every implementation is held to it. Keep construct names out of fixture
  prose and field names: three encode harnesses classify a schema by scanning its text.
- A harness limit is not an interpreter limit. Grep the C header before writing down which
  side a C gap is on.

Maintainer notes that tie this implementation to the specification draft are kept outside
this repository.

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
`source: vendor-codec`, which is independent provenance and the
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
names occur. One audit reported five undocumented keys and the real answer was a
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

  - `definitions.tlv`, seven keys. The `unknown` enum is stated here and
    in `tools/validate_schema.py`, and `test_cr_2026_016_tlv_meta_schema.py` fails if
    they drift apart.
  - `definitions.flagged` and `definitions.flagged_group`, four keys.
    The validator already enforced all four, so there was no behaviour to change. It
    also refuses two groups claiming the same bit, which JSON Schema cannot express —
    `test_cr_2026_017_flagged_meta_schema.py` records that division of labour.
  - `definitions.match`, six keys, describing the Option B block under a
    `match:` key. The older `type: match` with `on:` spelling is withdrawn, and
    its `on` and `cases` keys left `definitions.field`.
  - `byte_group` and `repeat`, seven field-level keys — `byte_group`,
    `size`, `count`, `byte_length`, `until`, `max`, `min`. `byte_group` has two
    spellings, an object carrying its own `size` and an array taking `size` from the
    field beside it; only the object form has corpus coverage, and a test says so
    rather than forbidding the other.

  `repeat`'s `max` and `min` were honoured by the four interpreters and not by the TS013
  generator until a later fix closed the last such gap: `max` is now a ceiling on
  all five (defaulting to 1000, clamping a `count` and bounding the byte_length and
  until-end loops) and falling below `min` fails the decode on all five. `min` has no
  corpus fixture on purpose — it fails the decode, and no vector kind expects a failure.
- **A `byte_length` span must be consumed exactly, and that is now true on all
  five paths.** The language requires the members to divide the span; the Python, Go and Java
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
- **`match` is now uniform across the five implementations.** Every key (`field`, `length`, `name`, `var`, `cases`,
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
  would have deleted eleven keys — `source`, `expected_warnings`
 , `valid_range`, `resolution`, `unece`, the whole `tlv` description,
  and five `test_vectors` keys — silently, because JSON Schema treats an absent
  property as merely undescribed. The generator now refuses refuse to overwrite a
  file describing more than it does, and name what would be lost; `--force` still
  overwrites. **Edit the file by hand.** Teaching the generator the current file was
  the other option and was not taken: two definitions of the same thing to keep in
  step is the arrangement that produced the drift.
- **The C interpreter is measured now, and the gap is coverage rather than correctness
 .** `make test-c` builds the three previously-orphaned C test files and runs
  `tools/c-corpus-harness.py`, which generates C that builds each expressible corpus schema
  through the struct API, compiles it, and compares the decode against the vectors.
  **575 of 575 attempted vectors pass** (2026-10-13) since C gained `tlv` and
  `flagged` (it was 50 of 50 before either). 751 of 1239 are still in schemas the struct API
  cannot build - 26 use a `transform` chain, 24 a `bitfield_string`, 15 more cases than
  `SCHEMA_MAX_CASES` allows, 3 `repeat`, 3 a `u32le16` **the interpreter has and the harness
  has no constructor for**. The report names which side each limit is on, and that
  distinction is load-bearing: `no constructor for type 'u32le16'` used to read as a C gap
  when C decodes it perfectly well.

  **The C-interpreter wave (2026-10-11)** closed these, one PR each; the figures are the
  harness's at each step (struct API / `--from-blob names` / `--from-blob nameless`):
  - **Multi-byte and signed bit ranges**:
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
    -2^(w-1)..2^(w-1)-1 and names `s16[12:15]` in the out-of-range text, and the peek
    bookkeeping in `schema_encode` holds a mask in the base's bit space, so a peek spans
    its base's bytes. A short base gets the underflow peek message, a short `consume` its need
    message, as the reference words them. A full `u64[0:63]` past 2^63 is
    `SCHEMA_ERR_UNSUPPORTED` (no int64 holds it). **The binary form refuses both**: its
    bitfield record carries a start and a width, its base is one unsigned byte, and its
    consume marker is one byte, so `tools/schema_binary.py` now names a wide or signed base
    and a `consume` above 1 rather than writing something that decodes differently; the
    loader sets a bitfield's `size` to its one-byte base (the record's size nibble is the
    width). `bit-range-order.yaml` still waits on `byte_group`.

  **The C-interpreter wave (2026-10-11)** closed these, one PR each; the figures are the
  harness's at each step (struct API / `--from-blob names` / `--from-blob nameless`):
  - **`byte_group`**: 556 -> 565 attempted, all passing (`wide-group`, `byte-group-width`,
    `byte-group-bool`, `bit-range-order`); blob 64/64 unchanged. `FIELD_TYPE_BYTE_GROUP`
    was an enum value with no decode or encode path. Built with
    `field_byte_group(size, endian, member_start, member_count)`, its members placed above
    `field_count` (`nested_start`/`nested_count`, which nothing used). The group is one
    value of `size` bytes, or its widest member's base where 0, assembled in its own byte
    order; each member - a bit range of any base, a whole integer, a `bool` at bit 0-7 -
    is decoded as a bit range of that value through `decode_field`, so its sign,
    modifiers, lookup and variable apply as any field's. Load-time rules are checked at
    decode and encode, with the reference's text: a `size` narrower than a member, a `bool`
    past bit 7, an overlap, and the underflow message "byte_group of 'a', 'b'" for a short
    read. Encode checks each member as the member it is (the out-of-range message names `s8`, not the
    group), ORs it into the group's value, writes the value once, zero where no member
    names bits; a member left out is "Missing required field", an internal one the internal-field
    error. The top-level loop, a tlv case and a flagged group all dispatch it; the harness
    builds a top-level group only (two schemas with one nested in a case body are a harness
    limit, said so). This branch carries the multi-byte/signed bit range header change
    verbatim, since members use its base helpers. **The binary form has no byte_group
    record** and still refuses one. `sizeof(schema_t)` unchanged at 52536.

  **The C-interpreter wave (2026-10-11)** closed these, one PR each; the figures are the
  harness's at each step (struct API / `--from-blob names` / `--from-blob nameless`):
  - **`ascii`**: 554 -> 558 attempted, all passing;
    blob 64/64 unchanged. Decode keeps an interior 0x00 (`decoded_field_t.text_len`; the
    C string in `value.str` stops early, the value does not), reports each byte as itself -
    ISO 8859-1, so a JSON writer maps 0x80-0xFF to U+0080-U+00FF - and raises
    `decoded_field_t.non_ascii` for the non-ASCII warning, which the harness renders. A value
    longer than 31 bytes after its padding is `SCHEMA_ERR_UNSUPPORTED` naming the field,
    never truncated as it was. Encode, which had no `ascii` case at all, takes text through
    `encode_inputs_add_text(inputs, name, bytes, len)` (or `_add_string`): Latin-1 bytes,
    the form decode reports, so a decoded value re-encodes to its bytes. It pads with 0x00,
    rejects a value longer than `length` with the too-long message, and sets
    `encode_result_t.non_ascii` for its warning. A character above U+00FF cannot be put
    in that input, so rejecting one belongs to whatever converts Unicode text
    to it (a gateway's JSON reader). Text for a numeric field, and a number for an `ascii`
    one, are `SCHEMA_ERR_TYPE`. **The binary form still refuses `ascii`**: its type code
    `bytes` is shared with `hex` and `bytes`, every code 0-7 is taken, and a new one would
    be a format version - not invented here.

  **The C-interpreter wave (2026-10-11)** closed these, one PR each; the figures are the
  harness's at each step (struct API / `--from-blob names` / `--from-blob nameless`):
  - **Reference binding**: a `var_name` binds the value after
    `mult`/`div`/`add` and before the lookup, in `finish_numeric`, the one place both
    happen. Measured before: a text label and an enum already bound the number - C bound
    the raw read, which is the same thing until a modifier is present - but a scaled field
    bound its raw value (`div: 2` on raw 4 bound 4, not 2), a zero divisor left
    the raw value bound where the language leaves the reference unbound, and an `f16`/`f32`/
    `f64`/`udec`/`sdec` never bound at all. `variable_t` now carries the binding as a
    `double` beside the exact `int64_t`, with `integral`; a `match` compares only an
    integral binding with its keys (a fraction reaches the default case alone) and
    a `flagged` mask with a fraction is `SCHEMA_ERR_MATCH`. A lookup reporting a number
    binds that number; C holds every label as text (a known C gap),
    so it has none to bind. **The harness count does not move** (554/64/64 before and
    after): the corpus asks this binding of an implementation only through
    `lookup-reference-binding.yaml`, `mask-reference-binding.yaml` and
    `tag-component-reference-binding.yaml`, all port-based, and through inline `match`,
    which the harness does not build - and C's `match` has no builder, decodes its case
    bodies as counted fields (so the top-level loop walks them again), treats a zeroed
    `match_list` as matching 0, and skips an unmatched value where the default
    `default: error` (Match Default Handling) makes it an error. That construct is the next C item, not a harness tweak. Selftests cover the
    four rules through a `flagged` mask.

  **The C-interpreter wave (2026-10-11)** closed these, one PR each; the figures are the
  harness's at each step (struct API / `--from-blob names` / `--from-blob nameless`):
  - **Index-keyed encode**: an encoder working from a
    nameless form takes each value by the same index. A nameless schema (`schema_t.nameless`,
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
  have - and this interpreter has no text warning channel.
  `decode_result_t.bytes_unread` gives a caller the leftover-byte count (from
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
  that was corrected here, in SESSION-NOTES.md and in the harness's own skip
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
  cross-check, which is why C's TLV work came after the harness.

  `src/test_comprehensive.c` is deliberately out of `make test-c`: 22 of its 160 assertions
  encode the old lookup and enum behaviour that has since changed, and its
  little-endian `s24`/`u64` cases fail while the interpreter decodes both correctly when
  driven directly. Stale test, not a defect - updating it is its own change.
- **`name_from` and wildcard case keys are in Python, Go, Java and C#, not in C.** `name_from`
  and `!`/`*` case keys do not apply to the C interpreter: its TLV support
  packs a tag into one integer and has no wildcard keys, it has fixed-size name buffers, and it consumes a binary schema with no place for a
  template. It does omit a field whose lookup has no match. A `default`
  on a mapping lookup is Python, Go, Java and C# only, because the binary schema has
  no slot for it.
- **Modifier order is now canonical everywhere — do not reintroduce key order.**
  Bare `mult`/`div`/`add` apply in the order
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
  `pow: 2` stage. `floor`, `ceiling` and `clamp` are in all five; any
  stage outside the transform table - `{round: n}`, `{op: floor}`, `{sub: n}` - is rejected,
  never skipped.
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
    `history: [{timestamp, temperature, ...}]` exactly, because a tlv collects
    repeated tags into an array. Four are done; of the rest, 21 need the vendor's
    label maps applied as `lookup` tables, 11 need padding modelled with `skip`,
    and the remainder build their output key from payload content (below);
  - 27 build field *names* from payload content (`"region_" + n + "_avg_dwell"`,
    `"sdi12_" + n`), which needs computed keys (`name_from`);
  - 2 (`ws136`, `ws156` channel 255/52) are commented out in the vendor's own
    decoder, so it emits nothing there either and omitting them is correct.
  Those two blockers are now resolved: `ws101` uses a sparse mapping
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
  them as IPSO 3316 voltage. The scorer now follows the Integration Layer's TTN classification (below), so the word
  `battery` asks for no object at all; the `sensor:` key is gone from every schema
  and strict validation rejects it. Quantities whose SenML unit is a different scale (pressure in
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
- **The scorer's detectable sensor fields are the Integration Layer's.** `tools/score_schema.py`
  expects an IPSO annotation only where the Integration Layer's TTN classification
  rule places a field from its name: one of `temperature`, `humidity`, `pressure`,
  `precipitation` or `moisture`, with no qualifier word (`max`, `threshold`, `target`,
  `raw`, ...) and no medium that lacks a TTN path (`internal`, `leaf`, `board`, ...).
  The old name-to-object table, under which `battery`, `voltage`, `current` or
  `latitude` each demanded an object, is gone. A unit never selects an object.
  A field whose `lookup` reports text (string labels, or only a `${value}` default) is a
  label, not a reading, so a quantity word in its name asks for nothing
  (`temperature_sensor_status`, `temperature_unit`); a lookup with numeric labels feeds
  arithmetic and its field stays a reading (`lookup_reports_text`). This replaced the
  `sensor: none` opt-out; the Integration Layer agreed the scorer may refine its rule this way.
  A generic object (3300, 3320, 3325, 3332) is never a mismatch. A
  name-silent field's annotation is neither expected nor flagged. See
  `docs/IPSO-REFERENCE.md`. **A schema with no field that rule places gets half the
  semantic points** (`NO_DETECTABLE_SEMANTIC_CREDIT`, 10 of 20), by decision on
  PR #123. Giving none dropped 12 Platinum schemas whose only fields were battery,
  voltage or position to Silver. Giving all 20 lifted 257 other schemas to
  Platinum without checking anything. The scan now descends into a field-level
  `match:` too. Before that, laq4's temperature and humidity were never seen.
- **The TS013 generator's shared-byte bug is fixed**: bare bit ranges, `consume` and
  `bool` with `bit:` decode correctly in generated JavaScript, and
  `tools/vector-verdicts.py` holds both paths to every corpus vector. What remains is
  the generator reading a bit range's base in the schema's byte order where it should be
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
    bit range: in place, advancing by `consume` alone.
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

