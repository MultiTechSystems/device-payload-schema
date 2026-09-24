# Device Payload Schema

Declarative payload schema definitions for IoT device codecs.

**New to this toolkit?** Start with the [Getting Started Guide](docs/GETTING-STARTED.md).

## Overview

This repository provides:

- **YAML Schema Language**: Declarative payload definitions for binary-to-JSON decoding
- **Reference Interpreters**: Python, Go, Java, C# and C implementations
- **Code Generators**: Generate TS013-compliant codec code from schemas
- **Device Schemas**: Ready-to-use schemas for common devices (Decentlab, Milesight, etc.)

The schema language enables device manufacturers and integrators to define payload
structures once, then automatically generate decoders for multiple platforms.

## Quick Start

### Prerequisites

```bash
# Python development
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Decode a Payload

```bash
# Using the Python interpreter
python3 tools/schema_interpreter.py decode \
  schemas/devices/decentlab/dl-5tm.yaml \
  "02 1234 0003 01F4 0190 0C1C"
```

### Generate a Codec

```bash
# Generate JavaScript decoder
python3 tools/generate_ts013_codec.py schemas/devices/decentlab/dl-5tm.yaml -o dl_5tm_codec.js

# Generate C header
python3 tools/generate_firmware_codec.py schemas/devices/decentlab/dl-5tm.yaml -o dl_5tm_codec.h
```

### Run Tests

```bash
make pytest        # or: pytest tests/ -v
make test-languages   # Python, C, Go, Java and C# (Go/Java/C# run in Docker)
```

### Score a Schema

```bash
# Check schema quality and scoring tier
python3 tools/score_schema.py schemas/devices/decentlab/dl-5tm.yaml --verbose

# Output: PLATINUM (100.0%) with recommendations
```

Quality tiers: Rejected (below 60%), Bronze (60-69%), Silver (70-84%), Gold (85-94%), Platinum (95-100%).
A schema with no test vector declaring an independent `source:` (`vendor-doc`,
`vendor-codec`, `field-capture`, `spec-example`) is capped at Silver (PS-264).

Scoring includes: schema validity, test vectors, Python/JS cross-validation, branch coverage, edge cases, and semantic annotations (IPSO, SenML, TTN normalized).

## Repository Structure

```
payload-codec-proto/
├── schemas/                     # Device payload schemas
│   ├── devices/                # Device-specific schemas (YAML)
│   ├── payload-schema.json     # Meta-schema for YAML validation
│   ├── ipso-output.schema.json # IPSO format output validation
│   ├── senml-output.schema.json # SenML format output validation
│   └── ttn-output.schema.json  # TTN normalized output validation
│
├── tools/                       # Interpreters and generators
│   ├── schema_interpreter.py   # Reference Python decoder
│   ├── generate_ts013_codec.py # TS013 JavaScript generator
│   ├── generate_output_schema.py # Output JSON Schema generator
│   ├── generate_firmware_codec.py # C code generator
│   ├── validate_schema.py      # Schema validator
│   ├── score_schema.py         # Quality scoring tool
│   └── convert_*.py            # Converter utilities
│
├── include/                     # C reference implementation
│   └── schema_interpreter.h    # Header-only C decoder
├── go/schema/                   # Go interpreter
├── bindings/java/               # Java interpreter
├── dotnet/                      # C# interpreter
│
├── tests/                       # Test suite
│   └── test_schema_interpreter.py
│
├── docs/                        # Documentation
│   ├── LANGUAGE-ANALYSIS.md    # Schema language design
│   └── SPEC-IMPLEMENTATION-STATUS.md
│
└── examples/                    # Usage examples
```

## Schema Language

### Basic Example

```yaml
name: temperature_sensor
version: 1
endian: big

fields:
  - name: temperature
    type: s16
    div: 10
    unit: "°C"
    
  - name: humidity
    type: u8
    unit: "%"
```

### Features

- **Field Types**: u8, u16, u24, u32, u64, s8, s16, s24, s32, s64, f16, f32, f64, bool, ascii, hex, bytes, base64
- **Bit Fields**: `u8[0:3]` for bit extraction (inclusive range; add `consume: 1` on the last field of the byte)
- **Arithmetic**: `add`, `mult`, `div` modifiers
- **Polynomial**: Calibration curves with `polynomial: [a, b, c, d]`
- **Computed Fields**: Cross-field arithmetic with `compute: {op: div, a: $x, b: $y}`
- **Guards**: Conditional evaluation with `guard: {when: [...], else: 0}`
- **Conditional Parsing**: `match` and `flagged` for dynamic structures
- **TLV/LTV**: Tag-Length-Value parsing with `tlv`

See [the schema language reference](docs/SCHEMA-LANGUAGE-REFERENCE.md) for the complete syntax.

## Performance & Security

### Why Declarative Schemas?

Traditional LoRaWAN(R) codecs use JavaScript with `eval()` or `new Function()`, creating security risks on shared infrastructure. Declarative schemas eliminate this attack surface entirely.

### Performance

Measured by `make bench-c` (`tools/benchmark-c-interpreter.py`) on one corpus frame
(`decentlab/dl-lid`, 29 bytes, 15 fields), decode only, schema built once:

| Implementation | Throughput | Latency |
|----------------|------------|---------|
| C interpreter (`include/schema_interpreter.h`) | ~8.5M ops/s | 0.12 µs |
| Python interpreter (`tools/schema_interpreter.py`) | ~40K ops/s | 25 µs |

Figures depend on the machine; re-run the target rather than quoting these. See
[SPEC-IMPLEMENTATION-STATUS.md](docs/SPEC-IMPLEMENTATION-STATUS.md) for how they were
measured and for older per-language figures that are not comparable to these.

## Code Generation

### TS013-Compliant JavaScript

```bash
python3 tools/generate_ts013_codec.py schemas/devices/decentlab/dl-5tm.yaml -o dl_5tm_codec.js
```

Generates decoders compatible with The Things Network, ChirpStack, and other
LoRaWAN network servers that support the TS013 Payload Codec API.

### Output JSON Schema

```bash
python3 tools/generate_output_schema.py schemas/devices/decentlab/dl-5tm.yaml -o dl_5tm_output.schema.json
```

Generates a JSON Schema describing the decoded payload structure. This schema
enables validation of decoder output with standard JSON Schema tools.

### Embedded C

```bash
python3 tools/generate_firmware_codec.py schemas/devices/decentlab/dl-5tm.yaml -o dl_5tm_codec.h
```

Generates header-only C decoders for embedded systems (Arduino, ESP32, STM32, etc.).

## Compatibility

### TS013 Payload Codec API

Generated codecs implement the TS013 interface:

```javascript
function decodeUplink(input) {
  return {
    data: { /* decoded fields */ },
    warnings: [],
    errors: []
  };
}
```

### TTN Device Repository

`tools/convert_milesight.py` and `tools/convert_decentlab.py` produce a first-pass
schema from a vendor's JavaScript codec (a starting point, not a finished schema), and
`tools/crossvalidate_ttn.py` compares a schema against the vendor decoder and declared
examples in a TTN device repository checkout. See the
[TTN codec conversion guide](docs/TTN-CODEC-CONVERSION-GUIDE.md).

## Contributing

Contributions welcome! Please:

1. Add test vectors for new schemas
2. Ensure `pytest` passes
3. Run `python3 tools/validate_schema.py <schema> -v` and `python3 tools/score_schema.py <schema>` on new schemas

## License

MIT — see [LICENSE](LICENSE). Author: Jason Reiss, Multi-Tech Systems, Inc.

The device schemas in `schemas/devices/` describe third-party devices and were
written from each manufacturer's own material. That descriptive content
originates with the manufacturer rather than with this project:
[PROVENANCE.md](PROVENANCE.md) records where every schema came from and what it
means for reuse.

MIT License

Copyright (c) 2024-2026 Multitech Systems, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Documentation

- [Language Analysis](docs/LANGUAGE-ANALYSIS.md) - Schema language design rationale
- [Implementation Status](docs/SPEC-IMPLEMENTATION-STATUS.md) - Feature support matrix
- [Formula Migration](docs/FORMULA-MIGRATION-TRACKING.md) - Migration to declarative constructs
