# Implementation Status

Feature support matrix across the reference implementations.

**Measured 2026-09-24 at commit `5fdd850`**, by decoding a one-construct probe schema
per row in each implementation (Python directly, Go, Java and C# in their `make test-*`
containers, C through `tools/c-corpus-harness.py`'s struct-API code generator, and the
TS013 generator's output under node), plus the 30 `_language-conformance` fixtures.
Where a cell could not be probed — mostly C constructs the harness cannot build — it
was read from the source and says so. Re-measure rather than trust this table after
any interpreter change; every row here was wrong at least once in the previous
revision.

| Column | Source |
|---|---|
| Python | `tools/schema_interpreter.py` (reference) |
| Go | `go/schema/schema.go` |
| Java | `bindings/java/src/main/java/org/lora/schema/` |
| C# | `dotnet/PayloadSchema/` |
| C | `include/schema_interpreter.h` — struct/binary API, **no YAML reader** |
| TS013 | JavaScript emitted by `tools/generate_ts013_codec.py` |

Legend: ✓ implemented · ✗ refused (parse or decode error, or no representation in C) ·
**∅ accepted and silently ignored** (decodes without error as if the key were absent) ·
⚠ partial or silently wrong (see the footnote) — the last two are the dangerous ones,
because nothing reports them.

## Quick Summary

| | Python | Go | Java | C# | C | TS013 |
|---|---|---|---|---|---|---|
| Decode, corpus | 1524/1524 | full floor | full floor | full floor | 487/487 attempted | 1524/1524 |
| Encode | ✓ | ✓ | ✓ | ✓ | ✓ (MCU scope) | ✓ `encodeDownlink` |
| Binary schema | read/write | read/write | read (v2) | — | read | — |

The corpus is 1535 vectors in 200 schemas (`tools/vector-verdicts.py`: 1535/1535 on
both the interpreted and the generated path; 11 of them are encode vectors). C attempts
487 vectors and passes all 487; 1037 more are in the 104 schemas its struct API cannot
build (`make test-c` prints why). Go, Java and C# decode floors are the full corpus;
`make check-floors` prints each beside its actual.

## Feature Matrix

### Types

| Feature | Python | Go | Java | C# | C | TS013 |
|---|---|---|---|---|---|---|
| `u8`–`u64`, `s8`–`s64`, `u24`, `s24` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `i8` `i16` `i32` `i64` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `uintN`, `intN`, `i24` aliases | ✓ | ✗ | ⚠¹ | ✗ | ✓ | ⚠¹ |
| `float`, `double` | ✓ | ✗ | ✗ | ✗ | ✓ | ⚠¹ |
| `u32le16` (PS-271) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `f16` | ✓ | ✓ | ✓ | ✓ | ✓ | ⚠² |
| `f32`, `f64` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `bool` + `bit:` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| bit range `u8[a:b]` + `consume` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| cross-byte range `u16[4:11]` | ✓ | ✓ | ✓ | ✓ | ⚠³ | ✓ |
| range base read big-endian (PS-059) | ✓ | ⚠⁴ | ✓ | ✓ | ⚠³ | ⚠⁴ |
| `ascii` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `string` with `length` | ✓ | ✓ | ⚠⁵ | ✓ | ✓ | ⚠⁵ |
| literal `{type: string\|number, value:}` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `bytes` (lowercase hex string) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `hex` (lowercase, PS-074) | ✓ | ✓ | ✓ | ✓ | ⚠⁶ | ✓ |
| `hex:upper` | ✓ | ✓ | ✗ | ✗ | ✗ | ⚠⁷ |
| `base64` | ✓ | ✗ | ✗ | ✗ | ✓ | ⚠⁷ |
| `format:` / `separator:` on `bytes` | ∅ | ✓ | ✓ | ✓ | ✗ | ∅ |
| `length: remaining` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `skip` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `udec` / `sdec` | ✓ | ✗ | ✗ | ✗ | ✓ | ⚠⁷ |
| `enum` (+ `default`) | ✓ | ✓ | ✓ | ✓ | ⚠⁸ | ✓ |
| `bitfield_string` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `version_string` | ✓ | ✗ | ✗ | ⚠⁹ | ✗ | ⚠⁷ |
| `_`-prefixed fields hidden from output | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `encoding: sign_magnitude\|bcd\|gray` | ✓ | ∅ | ∅ | ∅ | ✗ | ∅ |
| `le_`/`be_` type prefixes | ✗ | ✗ | ✗ | ✓ | ✗ | ✓ |
| field-level `endian:` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

1. Java accepts `uint16`, `uint24` and `i24` and refuses the other spellings; the
   generated codec emits nothing, with no error, for `uint24` and `double`.
2. The generator reads an `f16` with its `f64` reader over two bytes: `3C00` decodes to
   1.08e-19 instead of 1.0.
3. C's bit range reads one byte whatever the base type, so `u16[4:11]` over `0FF0`
   gives 0 and `consume` advances one byte.
4. Go and the generated codec take the base in the schema's byte order: under
   `endian: little`, `u16[0:7]` over `12 34` gives 18 where PS-059 and the other three
   give 52.
5. Java and the generated codec treat `type: string` without `value:` as a literal: no
   bytes are read, the field is dropped, and every following field is misaligned.
6. C emits uppercase hex.
7. The generated codec emits nothing for the field and reports no error.
8. C has `enum` and `lookup` but no `default` for either.
9. C# returns the prefix alone (`"v"` for `01 02 03`).

### Lookup, modifiers and computed values

| Feature | Python | Go | Java | C# | C | TS013 |
|---|---|---|---|---|---|---|
| `lookup` sequence | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| sequence out of range is an error (PS-105) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `lookup` mapping; a miss omits the field (PS-269) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `lookup` `default` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `mult` `div` `add`, canonical order (PS-101) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `transform:` arithmetic stages | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `sqrt` `abs` `pow` `log` `log10` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `{op: round, decimals: N}` half-to-even | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `floor:` `ceiling:` `clamp:` bounds | ✓ | ∅ | ∅ | ∅ | ✗ | ✓ |
| `{op: floor}` / `{op: ceiling}` | ✓ | ∅ | ∅ | ∅ | ✗ | ✓ |
| `polynomial` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `type: number` + `ref:` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `compute` `add` `sub` `mul` `div` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `compute` `mod` `idiv`, floored (CR-2026-007) | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `guard` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `match_value` | ∅ | ∅ | ∅ | ∅ | ✗ | ∅ |

`match_value` is documented in the language reference and implemented nowhere.
`sub:` is not a language key, but Go and C# honour it as a transform stage while
Python, Java and the generator ignore it; write `add:` with a negative value.

### Conditionals

| Feature | Python | Go | Java | C# | C | TS013 |
|---|---|---|---|---|---|---|
| `match` on `field:` | ✓ | ✓ | ✓ | ✓ | ✓¹⁰ | ✓ |
| `match` inline `length:` discriminator | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| range case keys `"2..5"` | ✓ | ✓ | ✓ | ✓ | ✓¹⁰ | ✓ |
| `default` case / key; unmatched with none is an error | ✓ | ✓ | ✓ | ✓ | ✓¹⁰ | ✓ |
| `flagged` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `tlv` (`tag_size`, `length_size`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| composite tags (`tag_fields` + `tag_key`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| wildcard keys `"[1, !0]"`, `"[2, *]"` | ✓ | ✓ | ✓ | ✓ | ✗ | ✗¹¹ |
| `unknown: skip` / `raw` | ✓ | ✓ | ✓ | ✓ | ⚠¹² | ✓ |

The fallback key is `default`; `_` is an ordinary key everywhere and matches nothing.
Hex range keys (`"0x10..0x1F"`) match nothing in the four interpreters and do match in
the generated codec — write ranges in decimal.

10. From the header: C's `match` reads a variable, with integer, list, range and
    default cases. The harness cannot build inline `match`, so no C vector exercises it.
11. Generation raises (`JSONDecodeError` in the TLV encode path), so no codec is
    produced — this is why `milesight/uc1114` and `uc1152` have none.
12. C has `skip` only; `raw` needs somewhere to put the captured bytes.

### Structures and composition

| Feature | Python | Go | Java | C# | C | TS013 |
|---|---|---|---|---|---|---|
| `repeat` `count: $field` | ✓¹³ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `repeat` `byte_length`, `until: end` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `repeat` `max` / `min` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `byte_group` | ✓ | ✓ | ⚠¹⁴ | ✓ | ✗ | ✓ |
| `type: object` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `var:` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `name_from` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `ports` (fPort routing) | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `definitions` + `$ref`, top-level list | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `$ref` in a port's field list | ✓ | ✗ | ✓ | ✓ | ✗ | ✓ |
| `$ref` inside a `match` case or `object` | ⚠¹⁵ | ✗ | ⚠¹⁵ | ⚠¹⁵ | ✗ | ⚠¹⁵ |

13. Python requires the `$`; a bare `count: n` fails there and is accepted by the others.
14. Java reports byte_group members as floats (`10.0`).
15. Silently wrong: Python decodes the `$ref` entry as a `u8` named `unknown`; Java and
    the generated codec drop it; C# resolves it inside an `object` and errors inside a
    `match` case. Go errors in both. `use:`/`rename:`/`prefix:` are resolved only by
    `tools/schema_preprocessor.py`, never by an interpreter.

### Output and metadata

| Feature | Python | Go | Java | C# | C | TS013 |
|---|---|---|---|---|---|---|
| `valid_range` → `_quality` (PS-131/PS-182) | ✓ | ⚠¹⁶ | ∅ | ⚠¹⁶ | ✗ | ✓ |
| `metadata.timestamps` | ✓¹⁷ | ∅ | ∅ | ∅ | ✗ | ∅ |
| `direction:` (uplink/downlink/both, by grep) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `downlink_commands` | ✓ | — | — | — | — | ✓ |

16. Go and C# report `_quality` and also put the warning list into the decoded data as
    a `_warnings` key.
17. Only when the caller passes `input_metadata`; with none the block is ignored
    (PS-312). `unix_epoch` emits an ISO string with milliseconds.

### Encoding (struct → bytes)

All five interpreters have an encoder. The corpus round-trip suites
(`encode(decode(payload)) == payload`) run in Python, Go, Java and C#, with per-shape
floors; Python currently round-trips 1232 vectors (`make check-floors-python`). C's
encoder (`schema_encode`) covers the MCU surface and is tested by `src/test_encoder.c`
under `make test-c`. The generated codec's `encodeDownlink` passes all 11 encode
vectors in the corpus.

## Implementation Notes

### Python (`tools/schema_interpreter.py`)

The reference: the widest construct set, and the only home of `encoding:`,
`metadata.timestamps`, `version_string` and the bound stages among the interpreters.
Public API surface (`td-tools` imports it by path). Gaps: `$ref` inside a nested
construct, `separator:`.

### Go (`go/schema/`)

Full corpus decode and the ordered-TLV encode API (`DecodeOrdered`/`EncodeOrdered`).
Gaps: most type aliases, `base64`, `udec`/`sdec`, `version_string`, `$ref` outside the
top-level list, the bound stages; bit-range base follows the schema's byte order.

### Java (`bindings/java/`)

Decode and encode (`Encoder.java`), transforms, `polynomial`, `compute`, `guard` and
`definitions` all present. Gaps: `string` without `value:` (silently reads nothing),
`hex:upper`, `base64`, `udec`/`sdec`, `version_string`, most aliases, `valid_range`
quality, nested `$ref`.

### C# (`dotnet/PayloadSchema/`)

Decode and encode (`SchemaEncoder.cs`). The only interpreter that accepts `le_`/`be_`
prefixes. Gaps: aliases other than `iN`, `hex:upper`, `base64`, `udec`/`sdec`,
`version_string` (silently wrong), `$ref` inside a `match` case.

### C (`include/schema_interpreter.h`)

Embedded-oriented: schemas are built through the struct API or loaded from a binary
blob, never from YAML, and there is no transform array or compute struct. Has integer,
float, `bool`, bit ranges (one byte), `ascii`/`string`, `hex`, `base64`, `bytes`,
`udec`/`sdec`, `u32le16`, `enum`, `lookup`, `match`, `flagged`, `tlv` (tag-only and
two-component tags, `unknown: skip`), `var`, field `endian`, the three bare modifiers
and an encoder. Lacks everything in the `transform`/`compute` family, `repeat`,
`object`, `byte_group`, `definitions`/`$ref`, `ports`, `name_from`, `valid_range`,
defaults on `enum`/`lookup`, and emits uppercase `hex`.

`make test-c` (2026-09-24): **487 of 487 attempted vectors pass; 1037 of 1524 are in
104 schemas the struct API cannot build** — chiefly `transform` (26 schemas),
`bitfield_string`/`version_string` (23) and more cases than `SCHEMA_MAX_CASES` (15).
The harness names which side each limit is on; a harness limit is not a C gap.

### JavaScript (`tools/generate_ts013_codec.py` output)

TS013 `decodeUplink`/`encodeDownlink`, eval-free, half-to-even rounding helper, and
`valid_range` quality flags. Passes all 1535 corpus vectors. Its failure mode is
silence: an unsupported type emits no field and no error (see footnote 7). A field
named after a generated local overwrites it: `d` replaces the whole `data` with the
field's value, `w` replaces `warnings`, and `pos` or `buf` corrupt every following
field.

### Output JSON Schema (`tools/generate_output_schema.py`)

**Validation schemas** for decoder output.

- `_quality` is declared when any field carries a `valid_range` (PS-182), with a closed
  key set unless a `name_from` makes the output key dynamic
- A `lookup` is typed from its values (PS-106) and declared as an `enum`, since both the
  mapping and sequence forms are closed (PS-269, PS-105); `type: enum` is enumerated only
  where a `default` closes it (PS-068)
- `$ref` into local `definitions` is resolved, so fields behind a reference are declared
- `match` is traversed in both syntaxes (nested `match:` and legacy `type: match`/`on:`),
  including `default` branches; types widen where cases report a name differently
- `name_from` keys are declared: as exact properties where every `${...}` reference
  has a closed set of values, otherwise as an anchored `patternProperties` entry
  carrying the field's value schema

- Describes structure of decoded payload data
- JSON Schema draft-07 compliant
- Includes type constraints, ranges, and descriptions
- Enables standard JSON Schema validation of codec output

### Schema Validator (`tools/validate_schema.py`)

- Validates schema syntax and structure against the type vocabulary
- Runs embedded test vectors
- Three-level validation (ERROR/WARNING/INFO) and best-practice checks
- `-v` for detail, `--json` for CI integration

## Test Coverage

| Test Suite | Python | Go | Java | C# | C |
|---|---|---|---|---|---|
| Unit tests | ✓ | ✓ | ✓ | ✓ | ✓ |
| Corpus decode | ✓ | ✓ | ✓ | ✓ | ✓ (attempted subset) |
| Corpus encode round-trip | ✓ | ✓ | ✓ | ✓ | - |
| Fuzz (`tools/fuzz_decoder.py`) | ✓ | - | - | - | - |
| Property tests (`tests/test_hypothesis.py`) | ✓ | - | - | - | - |

## Performance Benchmarks

### C interpreter against Python (reproducible)

Regenerate with `make bench-c` (`tools/benchmark-c-interpreter.py`). Frame:
`schemas/devices/decentlab/dl-lid.yaml`, vector `vendor_reference_2` — a 29-byte
payload decoding to **15 fields**: three plain, then a two-group `flagged` covering
twelve more, two of them scaled with `div`. It is `dl-lid` rather than the richer
`dl-5tm` because `dl-5tm` needs `transform` and `polynomial`, which C lacks; the other
reason once given, that C had no `flagged`, stopped being true with CR-2026-034.

| Implementation | Throughput | Latency |
|----------------|------------|---------|
| C interpreter (`include/schema_interpreter.h`) | ~8.5M ops/s | 0.12 µs |
| Python interpreter (`tools/schema_interpreter.py`) | ~40K ops/s | 25 µs |
| **Ratio** | **~210x** | |

Both figures time `decode` alone, with the schema built once outside the loop. Measured
on an AMD Ryzen 9 7950X3D; a re-run on 2026-09-24 on a heavily loaded host gave 7.9M
and 38K (208x). Stripped executable including the whole interpreter and the schema:
**18.2 KB** (`cc -O2 -Os`, header-only so everything inlines).

These rows are comparable to each other and to nothing else in this document.

### Historical figures (not reproducible from this repository)

The tables below come from earlier revisions. No make target regenerates them, their
schemas and code have changed since, and the C figures once quoted beside them (33M
and 20.5M ops/s) were measured on a 5-field frame with no `flagged` and have been
withdrawn: those earlier numbers are not comparable to the table above. Treat the rest as
orders of magnitude only.

| Hardware | Python Interpreter | Go Binary Schema |
|----------|-------------------|------------------|
| AMD Ryzen 9 7950X3D | 81K ops/s (12 µs) | 1.87M ops/s (0.5 µs) |
| Intel i5-2400 | 17K ops/s (58 µs) | 555K ops/s (1.8 µs) |

DL-5TM schema (8 fields, `flagged`, `polynomial`).

| Java (AMD Ryzen 9 7950X3D) | Throughput | Latency |
|----------------|------------|---------|
| Traditional (hand-coded) | 11.2M ops/s | 89 ns |
| Schema Interpreter (YAML) | 3.7M ops/s | 270 ns |
| Cold Parse + Decode | 21.6K ops/s | 46 µs |

| Go (Intel i5-2400) | Throughput | Latency |
|----------------|------------|---------|
| Native Go | 1.45M ops/s | 690 ns |
| Binary Schema (pre-parsed) | 555K ops/s | 1.8 µs |
| YAML Schema (pre-parsed) | 121K ops/s | 8.3 µs |
| YAML Parse | 2.2K ops/s | 446 µs |

| Python (Intel i5-2400) | Throughput | Latency |
|----------------|------------|---------|
| Native Python | 514K ops/s | 1.9 µs |
| Interpreter (pre-parsed) | 17K ops/s | 58 µs |
| Interpreter (w/ parse) | 179 ops/s | 5.6 ms |

## Known Gaps

The cells above marked ∅ or ⚠ are the work list; the ones that matter most:

- `floor`/`ceiling`/`clamp` stages are silently ignored by Go, Java and C#.
- Bit-range base byte order: Go and the generator disagree with PS-059.
- `$ref` inside a nested construct works only in C#, and only inside an `object`.
- The generator's silent omissions (`f16`, `hex:upper`, `base64`, `udec`,
  `version_string`, `string` without `value:`) and its local-name collisions.
- `encoding:` is Python-only; `match_value` is implemented nowhere.

### Not Planned

- Formula expressions (security concern)
- Dynamic schema modification
- Encryption/compression (out of scope)
