# Device Payload Schema - Frequently Asked Questions

## Architecture and Layers

### What is the Device Payload Schema?

A declarative, YAML/JSON-based format for defining the structure of binary LoRaWAN device payloads. The device manufacturer describes the payload structure — field names, types, sizes, byte order, arithmetic transforms, units, and IPSO semantic metadata — in a machine-readable schema document. From that single schema, conforming tools can decode payloads in any language, generate TS013-compliant JavaScript codecs and C firmware headers, and check the schema against its embedded test vectors. The schema's annotations (`unit`, `ipso`, `senml`, `semantic`) are the semantic metadata that drives downstream integration.

### Where does the Payload Schema sit in the architecture?

The Payload Schema is **Stage 1** of a two-stage pipeline:

```
Binary payload → [Payload Schema interpreter] → Decoded JSON   (+ schema annotations)
                                                        ↓
                                         [Integration Layer converters]
                                                        ↓
                                    BACnet / Modbus / Sparkplug / Matter / SenML / ...
```

**Stage 1 (this project):** The schema defines how to decode binary bytes into named, typed, unit-annotated fields. The interpreter produces decoded JSON; the per-field unit, IPSO and SenML annotations stay in the schema, where a consumer reads them.

**Stage 2 (companion — Integration Layer):** The Integration Layer consumes the decoded JSON and the schema's annotations and converts it to native protocol formats using declarative Integration Profiles. See [INTEGRATION-LAYER.md](INTEGRATION-LAYER.md) for details.

### What does the Payload Schema produce?

Every decode returns the engineering values, in a TS013-style result with errors and
warnings (`python3 tools/schema_interpreter.py decode <schema> <hex>` prints it):

```json
{"success": true, "data": {"temperature": 23.1, "humidity": 50}, "errors": [], "warnings": []}
```

**No interpreter emits a `_meta` object.** The per-field `_meta` (type, UCUM unit, IPSO
object/resource, device EUI) described in the Integration Layer design is a proposed
Stage 2 contract, not something this repository produces. Today the semantic metadata
lives in the schema's own annotations:

```yaml
ipso: {object: 3303, instance: 0, resource: 5700}
senml: {name: "temperature", unit: "Cel"}
semantic: "air.temperature"
```

### What components make up the Payload Schema ecosystem?

| Component | What it does | Who uses it |
|-----------|-------------|-------------|
| **Schema document** | YAML/JSON describing binary payload structure | Device manufacturer authors it |
| **Interpreter** | Reads schema + binary bytes, produces decoded JSON | Network server, gateway, application |
| **TS013 codec generator** | Produces standalone JavaScript codec from schema | Network servers requiring TS013 API |
| **C code generator** | Produces C header with struct definitions and decode functions | Embedded firmware |
| **Test vectors** | Payload/expected-output pairs embedded in the schema | Automated validation |
| **Sensor definition library** | Pre-built schemas for common sensors with IPSO annotations | Profile generators, integrators |
| **Validation/scoring tools** | Schema syntax validation, completeness scoring (Rejected→Bronze→Platinum) | Quality assurance |

### How does this relate to TS013?

TS013 defines the JavaScript codec API (`decodeUplink`, `encodeDownlink`, `decodeDownlink`) that network servers use. The Payload Schema can be:

1. **Compiled** — the TS013 generator produces a standalone JavaScript codec from the schema
2. **Interpreted** — a schema-aware decoder (Python, Go, Java, C#, C) decodes directly

Both approaches produce TS013-compliant output. The schema is the single source of truth; TS013 codecs are one output.

### How does this relate to the Integration Layer?

The Integration Layer is a companion that consumes the Payload Schema's decoded output. The Payload Schema handles **what** the device sends (binary structure, field semantics). The Integration Layer handles **where** the data goes (BACnet objects, Modbus registers, Sparkplug metrics, Matter clusters).

The Integration Layer design proposes a `_meta` object as the contract between them, on which its seven universal operations (RENAME, CLASSIFY, ATTACH UNIT, CONVERT UNIT, TYPE COERCE, ATTACH IDENTITY, ATTACH TIMESTAMP) operate. No interpreter here produces `_meta`; it would be built from the schema's annotations. See [INTEGRATION-LAYER.md](INTEGRATION-LAYER.md) for the full architecture.

---

## General

### Why use schemas instead of JavaScript codecs?

| Aspect | JavaScript Codecs | Schema-Driven |
|--------|-------------------|---------------|
| File size | 2-50 KB per device | 200-500 bytes |
| Security | Arbitrary code execution | Data only |
| Portability | JS runtime required | Any language |
| Validation | Manual testing | Automatic with test vectors |
| Maintenance | Edit code | Edit data |

### What tools are included?

| Tool | Purpose |
|------|---------|
| `validate_schema.py` | Validate schema syntax and test vectors |
| `score_schema.py` | Rate schema completeness |
| `schema_preprocessor.py` | Resolve cross-file references |
| `generate_ts013_codec.py` | Generate JavaScript codec |
| `generate_firmware_codec.py` | Generate C header for firmware |
| `generate_output_schema.py` | Generate JSON Schema for decoded output |
| `crossvalidate_ttn.py` | Compare a schema against the vendor's TTN decoder |
| `schema_interpreter.py` | Python decoder |

---

## Schema Creation

### How do I create a schema from scratch?

Start with this template:

```yaml
name: my_sensor
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

test_vectors:
  - name: normal
    payload: "00E7 32"
    source: vendor-doc     # where the expected values came from
    expected:
      temperature: 23.1
      humidity: 50
```

### Can I generate a schema from a datasheet?

Use an LLM-assisted workflow:

1. Feed the datasheet payload format to an LLM (Claude, ChatGPT)
2. Reference the sensor library examples
3. Validate output with `validate_schema.py`
4. Score with `score_schema.py`
5. Iterate until passing

### How do I validate my schema?

```bash
python3 tools/validate_schema.py my_schema.yaml -v
```

This checks:
- YAML syntax
- Field type validity
- Test vector results

### How do I check schema quality?

```bash
python3 tools/score_schema.py my_schema.yaml -v
```

Scores based on:
- Valid schema structure
- Test vectors, and whether the Python interpreter and the generated JS codec pass them
- Branch and edge case coverage
- IPSO/SenML/semantic annotations

Tiers: Platinum 95-100%, Gold 85-94%, Silver 70-84%, Bronze 60-69%, Rejected below 60%.
A schema whose vectors all lack an independent `source:` (`vendor-doc`, `vendor-codec`,
`field-capture`, `spec-example`) is capped at Silver (PS-264).

---

## Sensor Library

### Is there a library of pre-built sensor definitions?

Yes. The `schemas/library/` directory contains common sensors with scaling and IPSO mappings:

| Category | File | Sensors |
|----------|------|---------|
| Environmental | `schemas/library/sensors/environmental.yaml` | temperature, humidity, pressure, CO2, TVOC |
| Power | `schemas/library/sensors/power.yaml` | battery_mv, battery_pct, voltage, current |
| Position | `schemas/library/sensors/position.yaml` | GPS, accelerometer, gyroscope |
| Digital | `schemas/library/sensors/digital.yaml` | digital I/O, counter, presence |

### How do I use library definitions?

Reference them with `$ref`:

```yaml
fields:
  - $ref: "schemas/library/sensors/environmental.yaml#/definitions/temperature_c_div10"
  - $ref: "schemas/library/sensors/power.yaml#/definitions/battery_mv"
```

Then run the preprocessor, and decode the resolved file. The interpreters resolve only a
local `$ref: '#/definitions/<name>'` and reject a reference into another file:

```bash
python3 tools/schema_preprocessor.py my_schema.yaml -o my_schema_resolved.yaml
```

### How do I handle multiple sensors of the same type?

Use `rename:` or `prefix:`. Both are resolved by `schema_preprocessor.py` only; no
interpreter reads them:

```yaml
fields:
  - $ref: "schemas/library/sensors/environmental.yaml#/definitions/temperature_c_div10"
    rename:
      temperature: indoor_temp
      
  - $ref: "schemas/library/sensors/environmental.yaml#/definitions/temperature_c_div10"
    rename:
      temperature: outdoor_temp
```

Or with prefix for groups:

```yaml
fields:
  - $ref: "schemas/library/profiles/env-sensor.yaml#/definitions/temp_humidity"
    prefix: "zone1_"
```

---

## Interpreters

### What interpreters are available?

| Language | Location | Notes |
|----------|----------|-------|
| Python | `tools/schema_interpreter.py` | Reference implementation, decode and encode |
| Go | `go/schema/` | Decode and encode |
| Java | `bindings/java/` | Maven project; decode and encode |
| C# | `dotnet/PayloadSchema/` | Decode and encode |
| C | `include/schema_interpreter.h`, `src/` | Embedded subset: no `transform`, `compute`, `repeat`, ports or `$ref` |
| JavaScript | `tools/generate_ts013_codec.py` | A generator, not an interpreter: emits a standalone TS013 codec |

Python, Go, Java and C# decode the whole shared test-vector corpus (`make test-languages`).

### How do I decode a payload in Python?

```python
from schema_interpreter import SchemaInterpreter

import yaml

with open('my_schema.yaml') as f:
    schema = yaml.safe_load(f)       # SchemaInterpreter takes the loaded dict, not a path

interp = SchemaInterpreter(schema)
result = interp.decode(bytes.fromhex('00E732'))   # fPort=N for a port-based schema
print(result.success, result.errors)
print(result.data)
# {'temperature': 23.1, 'humidity': 50}
```

### How do I decode in Go?

```go
import "github.com/MultiTechSystems/lorawan-payload-schema/go/schema"

s, err := schema.ParseSchema(schemaYAML)   // YAML text as a string
result, err := s.Decode(payload)          // map[string]any
```

---

## Code Generation

### How do I generate a JavaScript codec?

```bash
python3 tools/generate_ts013_codec.py my_schema.yaml -o output/
```

Generates a TS013-compatible codec for TTN, ChirpStack, Helium.

### How do I generate C code for firmware?

```bash
python3 tools/generate_firmware_codec.py my_schema.yaml -o include/codec.h
```

Generates struct definitions and encode/decode functions.

### Can I generate JSON Schema for API documentation?

```bash
python3 tools/generate_output_schema.py my_schema.yaml -o my_schema.output.json
```

---

## Data Types

### What field types are supported?

| Type | Description | Example |
|------|-------------|---------|
| `u8`, `u16`, `u24`, `u32`, `u64` | Unsigned integers | Counters, battery |
| `s8`, `s16`, `s24`, `s32`, `s64` | Signed integers | Temperature, coordinates |
| `f16`, `f32`, `f64` | IEEE 754 floats | Sensor readings |
| `bool` | One bit (`bit: 0`-`7`), `true`/`false`; does not advance without `consume:` | Flags |
| `u8[3:4]` | Bit range (inclusive), the only bitfield spelling | Status flags |
| `bytes` | Raw bytes (`length:`), output as a lowercase hex string | MAC address, EUI |
| `hex`, `hex:upper`, `base64`, `ascii` | Byte strings in a chosen text form | Serial numbers, EUIs |

See [SCHEMA-LANGUAGE-REFERENCE.md](SCHEMA-LANGUAGE-REFERENCE.md) for the full list.

### How do I handle scaling?

Use `mult`, `div`, or `add`:

```yaml
- name: temperature
  type: s16
  div: 10        # Raw 231 → 23.1

- name: humidity
  type: u8
  mult: 0.5      # Raw 100 → 50.0

- name: temp_offset
  type: u16
  div: 10
  add: -40       # raw / 10 - 40
```

Bare modifiers always apply as mult, then div, then add, whatever order the keys are
written in. For another order, such as (raw - 400) / 10, use a `transform` list:
`transform: [{add: -400}, {div: 10}]`.

### How do I handle enumerations?

Use `lookup`:

```yaml
- name: status
  type: u8
  lookup:
    0: "ok"
    1: "low_battery"
    2: "error"
```

A value with no entry omits the field unless the lookup declares `default:`.

---

## Complex Structures

### How do I parse TLV (Type-Length-Value) payloads?

```yaml
fields:
  - tlv:
      tag_size: 1
      length_size: 1
      cases:
        1:
          - name: temperature
            type: s16
            div: 10
        2:
          - name: humidity
            type: u8
```

`01 02 00E7 02 01 32` decodes to `{temperature: 23.1, humidity: 50}`.

### How do I parse based on a message type header?

Use `match`:

```yaml
fields:
  - name: msg_type
    type: u8
    
  - match:
      field: $msg_type
      cases:
        1:
          - name: temperature
            type: s16
        2:
          - name: gps_lat
            type: s32
```

An unmatched value fails the decode unless the match declares `default:`.

### How do I parse bit flags?

Use a bit range, `uN[start:end]` (inclusive, bit 0 the least significant):

```yaml
- name: motion
  type: u8[7:7]
- name: tamper
  type: u8[6:6]
- name: low_battery
  type: u8[5:5]
  consume: 1       # a bit range does not advance by itself; the last one in the byte does
```

`A0` decodes to `{motion: 1, tamper: 0, low_battery: 1}`. Use `type: bool` with `bit:`
for `true`/`false` instead of 0/1.

---

## Migration

### How do I convert an existing JavaScript codec?

1. Analyze the codec to identify payload structure
2. Create YAML schema matching the structure
3. Record the vendor codec's output for known payloads as test vectors (`source: vendor-codec`)
4. Validate with `validate_schema.py` and compare with `crossvalidate_ttn.py`

Converter tools exist for some vendors. Their output is a first pass, not a finished
schema; a `# formula:` comment marks a field still to be finished:
- `convert_milesight.py` - Milesight devices
- `convert_decentlab.py` - Decentlab devices

### What codecs can't be converted?

Codecs with these features may not convert:
- Compression (Huffman, delta)
- Encryption
- CRC validation within payload
- Complex state-dependent logic

---

## Troubleshooting

### Schema validation fails

Check:
- YAML syntax (indentation, colons)
- Field type spelling (`u16` not `uint16`, `u8[0:3]` not `bits`)
- Test vector hex format (spaces optional)

### Test vectors don't match

Verify:
- Endianness (`endian: big` or `endian: little`)
- Scaling factors (`div`, `mult`, `add`), which apply in that fixed order
- Signed vs unsigned types

### Preprocessor can't find library files

Add library paths:

```bash
python3 tools/schema_preprocessor.py my_schema.yaml -L ../lib -o output.yaml
```

Or check that `schemas/library/` is in the expected location relative to your schema.

---

## Performance

### How fast is the schema interpreter?

`make bench-c` measures about 40,000 decodes/second in Python and about 8 million in the C
interpreter, on a 29-byte, 15-field Decentlab frame (figures depend on the machine). Even
the Python figure is far above a single gateway's traffic.

### What's the schema size?

| Format | Size |
|--------|------|
| YAML | 500-2000 bytes |
| JSON | 400-1500 bytes |
