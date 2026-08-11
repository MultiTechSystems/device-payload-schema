# Implementation Status

Feature support matrix across reference implementations.

## Quick Summary

| Implementation | Decode | Encode | Binary Schema | Performance |
|----------------|--------|--------|---------------|-------------|
| **Python** | Full | Full | Full | 184K msg/s |
| **Java** | Core | - | Full | 3.7M msg/s |
| **Go** | Full | Partial | Full | 2.1M msg/s |
| **C** | Full | - | Full | 33M msg/s |
| **JavaScript** | Full | Partial | - | 638K msg/s |

## Detailed Feature Matrix

### Core Types

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| `u8`, `u16`, `u32`, `u64` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `s8`, `s16`, `s32`, `s64` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `u24`, `s24` | ✓ | - | ✓ | ✓ | ✓ |
| `f16` (half-precision) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `f32`, `f64` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `bool` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `ascii` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `hex` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `bytes` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `base64` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `skip` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `enum` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `udec` / `sdec` | ✓ | - | ✓ | ✓ | ✓ |
| `bitfield_string` | ✓ | ✓ | ✓ | - | ✓ |
| `length: remaining` (PS-013/014/015) | ✓ | ✓ | ✓ | - | ✓ |

`length: remaining` consumes to the end of the payload. C is short because the binary
schema format encodes `length` as an unsigned varint, which cannot represent it; that
needs a sentinel value in the format. The keyword is the only spelling - a negative
integer is the internal sentinel the parsers map it to, and the validator rejects one
written in a schema (PS-014).

### Bitfield Syntax

The bracket range is the only bitfield spelling; CR-2026-006 withdrew the other
four (`u8[3+:2]`, `bits<3,2>`, `bits:2@3`, `u8:4`). The `Width u8:4` row this table
used to carry claimed all five implementations supported it, which was never true:
only Python decoded it correctly, and the binary encoder, the JS generator and C
each resolved it to the wrong bits or set a sentinel nothing consumed.

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| Range `u8[0:3]` | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cross-byte `u16[4:11]` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `byte_group` | ✓ | ✓ | ✓ | ✓ | ✓ |
| Endian prefix `le_u16` | ✓ | ✓ | ✓ | ✓ | ✓ |

### Arithmetic Modifiers

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| `add` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `mult` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `div` | ✓ | ✓ | ✓ | ✓ | ✓ |
| YAML key ordering | ✓ | ✓ | ✓ | ✓ | ✓ |
| `lookup` (array) | ✓ | - | ✓ | ✓ | ✓ |
| `lookup` (map) | ✓ | ✓ | ✓ | ✓ | ✓ |

### Transform Pipeline

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| `sqrt` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `abs` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `pow` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `log` / `log10` | ✓ | - | ✓ | ✓ | ✓ |
| `floor` / `ceiling` | ✓ | - | ✓ | ✓ | ✓ |
| `clamp` | ✓ | - | ✓ | ✓ | ✓ |
| `round` | ✓ | - | ✓ | - | ✓ |

### Computed Fields

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| `type: number` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `ref: $field` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `polynomial` | ✓ | - | ✓ | ✓ | ✓ |
| `compute: {op, a, b}` | ✓ | - | ✓ | - | ✓ |
| `guard` conditions | ✓ | - | ✓ | - | ✓ |

### Conditional Parsing

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| `switch` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `switch` range `2..5` | ✓ | - | ✓ | - | ✓ |
| `switch` default `_` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `flagged` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `tlv` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `match_value` | ✓ | - | ✓ | - | ✓ |

### Structures

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| `type: object` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `type: repeat` (count) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `repeat` (count_field) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `repeat` (until: end) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `definitions` / `use` | ✓ | - | ✓ | - | ✓ |
| `ports` (fPort routing) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `var` (variables) | ✓ | ✓ | ✓ | - | ✓ |

### Encodings

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| `sign_magnitude` | ✓ | - | ✓ | ✓ | ✓ |
| `bcd` | ✓ | - | ✓ | ✓ | ✓ |
| `gray` | ✓ | - | - | - | ✓ |

### Downlink Support (v0.3.2)

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| `direction` property | ✓ | - | - | - | ✓ |
| `downlink_commands` | ✓ | - | - | - | ✓ |
| `encode_command()` | ✓ | - | - | - | ✓ |
| `decode_command()` | ✓ | - | - | - | ✓ |
| Bidirectional schemas | ✓ | - | - | - | ✓ |

### Schema Composition (v0.3.2)

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| `$ref:` cross-file | ✓ | - | - | - | - |
| `use:` shorthand | ✓ | - | - | - | - |
| `rename:` fields | ✓ | - | - | - | - |
| `prefix:` fields | ✓ | - | - | - | - |
| Compact format strings | ✓ | - | - | - | - |

### Validation (v0.3.2)

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| ERROR level | ✓ | - | - | - | - |
| WARNING level | ✓ | - | - | - | - |
| INFO level | ✓ | - | - | - | - |
| Best practice checks | ✓ | - | - | - | - |
| Quality scoring | ✓ | - | - | - | - |
| `valid_range` -> `_quality` (PS-131/PS-182) | ✓ | - | - | - | ✓ |

`valid_range` is checked after all arithmetic, on the reported value, and the value is
passed through unchanged whatever the verdict (PS-132). `_quality` appears only when a
field actually carried a range (PS-182). Go, Java and C# decode these schemas correctly
but report no quality object; the interpreters and the generated JS agree on it, which
`tools/crossvalidate_js_json.py` now checks as a first-class value.

### Semantic Hints

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| `unit` | ✓ | - | ✓ | - | ✓ |
| `ipso` | ✓ | - | ✓ | - | ✓ |
| `senml_unit` | ✓ | - | ✓ | - | ✓ |
| `description` | ✓ | - | ✓ | - | ✓ |

### Binary Schema Format

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| Parse v1 | ✓ | - | ✓ | ✓ | - |
| Parse v2 | ✓ | ✓ | ✓ | ✓ | - |
| Encode v1 | ✓ | - | ✓ | - | - |
| Encode v2 | ✓ | - | ✓ | - | - |
| QR encoding | ✓ | - | - | - | - |

### Encoding (Struct→Binary)

| Feature | Python | Java | Go | C | JS |
|---------|--------|------|-----|---|-----|
| Basic types | ✓ | - | ✓ | - | ✓ |
| Bitfields | ✓ | - | Partial | - | ✓ |
| Nested objects | ✓ | - | - | - | ✓ |
| Repeat | ✓ | - | - | - | ✓ |
| Conditionals | ✓ | - | - | - | - |

## Implementation Notes

### Python (`tools/schema_interpreter.py`)

**Reference implementation** - most complete and tested.

- Full decode and encode support
- All schema features implemented (v0.3.2 spec)
- Extensive test coverage (477+ tests)
- Binary schema encode/decode
- Used for validation and code generation
- **v0.3.2 additions**: downlink_commands, direction, encodings, compact format

### Java (`bindings/java/`)

**High-performance JVM implementation** - best pure-language schema performance.

- Core decode support (no encode)
- YAML and binary schema v2 parsing
- Formula evaluation with field references
- Optimized for throughput (3.7M msg/s with 3x overhead)
- JIT-friendly interpreter loop
- Missing: polynomial, guard, definitions, some transform functions
- No encode support (decode-only)

### Go (`go/schema/`)

**Production quality** for high-throughput servers.

- Full decode support
- YAML and binary schema parsing
- Optimized for performance (2.1M msg/s with binary)
- Missing: `definitions`, `guard`, some encodings
- Encode support partial (basic types only)

### C (`include/schema_interpreter.h`)

**Embedded-optimized** - no dynamic allocation required.

- Full decode support
- Binary schema loading (no YAML)
- Programmatic schema building
- 32M msg/s throughput
- Missing: complex computed fields, definitions
- No encode support (decode-only)

### JavaScript (`tools/generate_ts013_codec.py` output)

**Generated codecs** for TTN/ChirpStack.

- Full decode support
- Partial encode support  
- No binary schema (uses generated code)
- Eval-free generated code
- TS013 format compliant
- `valid_range` quality flags and their warnings
- **v0.3.2 additions**: downlink_commands (encodeCommand/decodeCommand)

### Output JSON Schema (`tools/generate_output_schema.py`)

**Validation schemas** for decoder output.

- `_quality` is declared when any field carries a `valid_range` (PS-182), with a closed
  key set unless a `name_from` makes the output key dynamic
- `$ref` into `definitions` is **not** resolved for the properties themselves, so fields
  behind a reference are missing from the output schema

- Describes structure of decoded payload data
- JSON Schema draft-07 compliant
- Includes type constraints, ranges, and descriptions
- Enables standard JSON Schema validation of codec output

### Schema Validator (`tools/validate_schema.py`)

**Schema validation and testing tool** (v0.3.2 enhanced).

- Validates schema syntax and structure
- Runs embedded test vectors
- **v0.3.2**: Three-level validation (ERROR/WARNING/INFO)
- Best practice checks for IPSO annotations
- Test coverage recommendations
- JSON output for CI integration

## Test Coverage

| Test Suite | Python | Java | Go | C |
|------------|--------|------|-----|---|
| Unit tests | ✓ | - | ✓ | ✓ |
| Test vectors | ✓ | - | ✓ | ✓ |
| Fuzz testing | ✓ | - | ✓ | ✓ |
| Property tests | ✓ | - | - | - |
| Round-trip | ✓ | - | Partial | - |

## Version Compatibility

| Schema Version | Python | Java | Go | C | JS |
|----------------|--------|------|-----|---|-----|
| v1 (baseline) | ✓ | - | ✓ | ✓ | ✓ |
| v2 (extended) | ✓ | ✓ | ✓ | ✓ | ✓ |

All implementations MUST support v1 schemas. V2 adds optional features.

## Performance Benchmarks

Tested with DL-5TM schema (8 fields, flagged construct, polynomial transform).

### Hardware Comparison

| Hardware | Year | Python Interpreter | Go Binary Schema |
|----------|------|-------------------|------------------|
| AMD Ryzen 9 7950X3D | 2023 | 81K ops/s (12 µs) | 1.87M ops/s (0.5 µs) |
| Intel i5-2400 | 2011 | 17K ops/s (58 µs) | 555K ops/s (1.8 µs) |
| **Ratio** | | **4.7x** | **3.4x** |

### C Interpreter (AMD Ryzen 9 7950X3D)

**Not comparable to the DL-5TM rows above** — the C interpreter has no `flagged` or
`polynomial` support, so this uses a simpler 5-field frame (u8 protocol, u16 device
id, s16 temperature with `div`, u8 humidity with `div`, u16 battery). The Python
figure was measured on the same machine with the same schema and payload, so the two
rows here are comparable to each other and to nothing else in this document.

| Implementation | Throughput | Latency |
|----------------|------------|---------|
| C interpreter (`include/schema_interpreter.h`) | 20.5M ops/s | 0.05 µs |
| Python interpreter | 141K ops/s | 7.1 µs |
| **Ratio** | **145x** | |

Stripped executable including the whole interpreter and the schema: **18.6 KB**
(`gcc -O2 -Os`, header-only so everything inlines). That size and throughput are why
the C interpreter is a candidate for a full-featured embedded-Linux gateway decoder
and not only for the MCU binary-schema path — see AGENTS.md for what it still lacks.

### Java Implementation (AMD Ryzen 9 7950X3D)

| Implementation | Throughput | Latency | vs Traditional |
|----------------|------------|---------|----------------|
| Traditional (hand-coded) | 11.2M ops/s | 89 ns | 1x |
| Schema Interpreter (YAML) | 3.7M ops/s | 270 ns | 3.0x |
| Cold Parse + Decode | 21.6K ops/s | 46 µs | 519x |

Java has the **lowest schema overhead (3x)** among high-level languages due to JIT optimization of the interpreter loop.

### Go Implementation (Intel i5-2400)

| Implementation | Throughput | Latency |
|----------------|------------|---------|
| Native Go | 1.45M ops/s | 690 ns |
| Binary Schema (pre-parsed) | 555K ops/s | 1.8 µs |
| YAML Schema (pre-parsed) | 121K ops/s | 8.3 µs |
| Binary Parse | 393K ops/s | 2.5 µs |
| YAML Parse | 2.2K ops/s | 446 µs |

### Python Implementation (Intel i5-2400)

| Implementation | Throughput | Latency |
|----------------|------------|---------|
| Native Python | 514K ops/s | 1.9 µs |
| Binary Schema (w/ parse) | 28K ops/s | 36 µs |
| Interpreter (pre-parsed) | 17K ops/s | 58 µs |
| Interpreter (w/ parse) | 179 ops/s | 5.6 ms |

### Estimated Cloud Performance

| Cloud Instance | Python Interpreter | Go Binary Schema |
|----------------|-------------------|------------------|
| AWS t3.micro ($7/mo) | 17K ops/s | 555K ops/s |
| AWS t3.small ($14/mo) | 20K ops/s | 650K ops/s |
| AWS c6i.large ($62/mo) | 50K ops/s | 1.2M ops/s |
| AWS c7g.large (Graviton3) | 55K ops/s | 1.4M ops/s |

### LoRaWAN Scale Analysis

| Devices | Messages/day | t3.micro Python | t3.micro Go |
|---------|-------------|-----------------|-------------|
| 100 | 14K | <1 sec | trivial |
| 1,000 | 144K | 8 sec | <1 sec |
| 10,000 | 1.4M | 84 sec | 3 sec |
| 100,000 | 14M | 14 min | 26 sec |
| 1,000,000 | 144M | 2.4 hours | 4.3 min |

### Recommendations

- **<10K devices**: Python on t3.micro ($7/mo) is sufficient
- **10K-100K devices**: Go or Java on t3.small ($14/mo) recommended
- **>100K devices**: Java (3.7M ops/s) or Go on c6i.medium ($31/mo) for headroom
- **Latency-sensitive**: Java Schema (270 ns) or Go Binary Schema (<500 ns)
- **JVM ecosystem**: Java provides best schema performance at 3.7M ops/s

## Roadmap

### Recently Completed (v0.3.2)

| Feature | Implementation | Notes |
|---------|---------------|-------|
| **Java interpreter** | Java | Core decode, binary v2, 3.7M msg/s |
| `encoding:` property | Python, JS | sign_magnitude, bcd, gray |
| `downlink_commands:` | Python, JS | Command-based encoding |
| `direction:` property | Python, JS | uplink/downlink/bidirectional |
| `use:` shorthand | Python | Simplified schema composition |
| Compact format strings | Python | struct-like syntax |
| Validation levels | Python | ERROR/WARNING/INFO |
| Quality scoring | Python | Bronze/Silver/Gold/Platinum |

### Planned Additions

| Feature | Target | Priority |
|---------|--------|----------|
| Java polynomial/guard | Q2 | Medium |
| Java transform functions | Q2 | Medium |
| Go encode (full) | Q2 | Medium |
| Go downlink_commands | Q2 | Medium |
| C definitions | Q2 | Low |
| JS binary schema | Q3 | Medium |
| Rust implementation | Q3 | High |
| WASM build | Q4 | Medium |

### Schema Language Enhancements

See [FUTURE-FEATURES.md](FUTURE-FEATURES.md) for detailed specifications.

| Feature | Value | Status |
|---------|-------|--------|
| `valid_range` | Quality flags, bounds checking | ✓ Implemented |
| `resolution` | Metadata annotation | ✓ Implemented |
| `unece` | Standard unit identifiers | ✓ Implemented |
| `downlink_commands` | Command-based downlinks | ✓ Implemented |
| `direction` | Schema direction hint | ✓ Implemented |
| Validation levels | ERROR/WARNING/INFO | ✓ Implemented |
| Compact format | Struct-like syntax | ✓ Implemented |

### Not Planned

- Formula expressions (security concern)
- Dynamic schema modification
- Encryption/compression (out of scope)
