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

# Tests
make test          # C self-tests + pytest
make pytest        # Python only
make fuzz-quick    # 10-second fuzz, cheap enough for every commit

# Regenerate the repository index after adding docs, tools or schemas
python3 tools/generate_docs_index.py
```

Note: `make validate` only covers `examples/*.yaml`. Device schemas under
`schemas/devices/` are not part of that target — validate and score those
explicitly.

## Schema language cheat sheet

```
TYPES:        u8 u16 u24 u32 u64 | s8 s16 s24 s32 s64 | f16 f32 f64 | bool
              ascii hex bytes base64 | number string | skip enum
STRUCTURES:   object | repeat | byte_group | tlv
MODIFIERS:    add mult div | lookup | polynomial | compute | guard | transform
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

`tools/score_schema.py` scores each schema out of 100 and assigns a tier:
Bronze 50-69%, Silver 70-84%, Gold 85-94%, Platinum 95-100%.

| Points | Requirement |
|---|---|
| 12 | Passes structural validation |
| 8 | Has `test_vectors` |
| 20 | Python interpreter decodes all vectors correctly |
| 15 | Generated JS codec decodes all vectors correctly |
| 12 | All `match`/`flagged`/port branches covered by vectors |
| 8 | Edge cases covered (zero, max, negative, min payload) |
| 5 | At least 5 vectors |
| 20 | IPSO + SenML + semantic annotation of detectable sensor fields |

Two consequences worth internalising before doing schema work:

1. A schema with no `test_vectors` forfeits 68 of 100 points and cannot exceed
   Bronze, no matter how correct it is.
2. The test gates total 80 points, so perfect vector coverage *alone* reaches
   only Silver. **Platinum requires annotations as well** — at least 15 of the
   20 semantic points.

Use the existing platinum schemas as templates: `decentlab/dl-5tm`,
`decentlab/dl-alb`, `digital-matter/oyster`, `mclimate/vicki`, `dragino/laq4`.

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
- **CI runs fuzzing only.** `.github/workflows/` contains just `fuzz.yml`;
  nothing gates `tests/`, schema validation or scoring.
- **Most published schemas are unverified.** 151 of 158 device schemas ship with
  no test vectors (5 Platinum, 1 Silver, 152 Bronze; mean score 15.5%). See the
  per-device table in [`docs/INDEX.md`](docs/INDEX.md).
- **The JS TS013 generator has a shared-byte bug.** `generate_ts013_codec.py`
  can drop bare bit-range fields and fail to advance the cursor. The Python
  interpreter path is correct; only generated JavaScript is affected.
