# Device Payload Schema - Frequently Asked Questions

## Architecture and Layers

### What is the Device Payload Schema?

A declarative, YAML/JSON-based format for defining the structure of binary LoRaWAN device payloads. The device manufacturer describes the payload structure — field names, types, sizes, byte order, arithmetic transforms, units, and IPSO semantic metadata — in a machine-readable schema document. From that single schema, conforming tools can decode payloads in any language, generate TS013-compliant JavaScript codecs, produce test vectors, and emit semantic metadata (`_meta`) that drives downstream integration.

### Where does the Payload Schema sit in the architecture?

The Payload Schema is **Stage 1** of a two-stage pipeline:

```
Binary payload → [Payload Schema interpreter] → Decoded JSON + _meta
                                                        ↓
                                         [Integration Layer converters]
                                                        ↓
                                    BACnet / Modbus / Sparkplug / Matter / SenML / ...
```

**Stage 1 (this project):** The schema defines how to decode binary bytes into named, typed, unit-annotated fields. The interpreter produces decoded JSON with a `_meta` object carrying five protocol-independent attributes per field: name, type, unit (UCUM), IPSO object/resource ID, and device EUI.

**Stage 2 (companion — Integration Layer):** The Integration Layer consumes the decoded JSON + `_meta` and converts it to native protocol formats using declarative Integration Profiles. See [INTEGRATION-LAYER.md](INTEGRATION-LAYER.md) for details.

### What does the Payload Schema produce?

Two outputs from every decode:

1. **Decoded JSON** — the engineering values:
   ```json
   {"temperature": 23.5, "humidity": 65, "battery": 3.6}
   ```

2. **`_meta` object** — semantic metadata per field:
   ```json
   {
     "fields": {
       "temperature": {
         "type": "s16", "unit": "Cel",
         "ipso": {"object": 3303, "resource": 5700}
       }
     },
     "device_eui": "a1-00-00-27-05-00-00-77"
   }
   ```

The `_meta` carries enough information for any downstream system to classify, convert units, assign identity, and format the data — without knowing anything about the original binary encoding.

### What components make up the Payload Schema ecosystem?

| Component | What it does | Who uses it |
|-----------|-------------|-------------|
| **Schema document** | YAML/JSON describing binary payload structure | Device manufacturer authors it |
| **Interpreter** | Reads schema + binary bytes, produces decoded JSON + `_meta` | Network server, gateway, application |
| **TS013 codec generator** | Produces standalone JavaScript codec from schema | Network servers requiring TS013 API |
| **C code generator** | Produces C header with struct definitions and decode functions | Embedded firmware |
| **Test vectors** | Payload/expected-output pairs embedded in the schema | Automated validation |
| **Sensor definition library** | Pre-built schemas for common sensors with IPSO annotations | Profile generators, integrators |
| **Validation/scoring tools** | Schema syntax validation, completeness scoring (Bronze→Platinum) | Quality assurance |

### How does this relate to TS013?

TS013 defines the JavaScript codec API (`decodeUplink`, `encodeDownlink`, `decodeDownlink`) that network servers use. The Payload Schema can be:

1. **Compiled** — the TS013 generator produces a standalone JavaScript codec from the schema
2. **Interpreted** — a schema-aware decoder in any language (Python, Go, C, JS, Java, .NET) decodes directly

Both approaches produce TS013-compliant output. The schema is the single source of truth; TS013 codecs are one output.

### How does this relate to the Integration Layer?

The Integration Layer is a companion that consumes the Payload Schema's decoded output. The Payload Schema handles **what** the device sends (binary structure, field semantics). The Integration Layer handles **where** the data goes (BACnet objects, Modbus registers, Sparkplug metrics, Matter clusters).

The `_meta` object is the contract between them. The Integration Layer's seven universal operations (RENAME, CLASSIFY, ATTACH UNIT, CONVERT UNIT, TYPE COERCE, ATTACH IDENTITY, ATTACH TIMESTAMP) all operate on `_meta` attributes. See [INTEGRATION-LAYER.md](INTEGRATION-LAYER.md) for the full architecture.

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
| `generate-c.py` | Generate C header for firmware |
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
python tools/validate_schema.py my_schema.yaml -v
```

This checks:
- YAML syntax
- Field type validity
- Test vector results

### How do I check schema quality?

```bash
python tools/score_schema.py my_schema.yaml
```

Scores based on:
- Valid schema structure
- Test vector coverage
- IPSO/SenML annotations
- Edge case coverage

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
  - $ref: "schemas/library/sensors/environmental.yaml#/definitions/temperature_c"
  - $ref: "schemas/library/sensors/power.yaml#/definitions/battery_mv"
```

Then run the preprocessor:

```bash
python tools/schema_preprocessor.py my_schema.yaml -o my_schema_resolved.yaml
```

### How do I handle multiple sensors of the same type?

Use `rename:` or `prefix:`:

```yaml
fields:
  - $ref: "schemas/library/sensors/environmental.yaml#/definitions/temperature_c"
    rename:
      temperature: indoor_temp
      
  - $ref: "schemas/library/sensors/environmental.yaml#/definitions/temperature_c"
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

| Language | File | Tests | Notes |
|----------|------|-------|-------|
| Python | `tools/schema_interpreter.py` | 126 | Reference implementation, full feature support |
| JavaScript | `reference-impl/js/` | 92+ | TS013 codec generation |
| C | `reference-impl/c/` | — | Embedded-friendly, ARM benchmarks |
| Java | `reference-impl/java/` | — | Maven project |
| Go | `go/schema/schema.go` | — | Full feature support |

### How do I decode a payload in Python?

```python
from schema_interpreter import SchemaInterpreter

interp = SchemaInterpreter('my_schema.yaml')
result = interp.decode(bytes.fromhex('00E732'))
print(result.data)
# {'temperature': 23.1, 'humidity': 50}
```

### How do I decode in Go?

```go
import "payload-codec-proto/go/schema"

s, _ := schema.ParseSchema(schemaYAML)
result, _ := s.Decode(payload)
```

---

## Code Generation

### How do I generate a JavaScript codec?

```bash
python tools/generate_ts013_codec.py my_schema.yaml -o output/
```

Generates a TS013-compatible codec for TTN, ChirpStack, Helium.

### How do I generate C code for firmware?

```bash
python tools/generate-c.py my_schema.yaml -o include/codec.h
```

Generates struct definitions and encode/decode functions.

### Can I generate JSON Schema for API documentation?

```bash
python tools/generate_jsonschema.py my_schema.yaml -o output/
```

---

## Data Types

### What field types are supported?

| Type | Description | Example |
|------|-------------|---------|
| `u8`, `u16`, `u32` | Unsigned integers | Counters, battery |
| `s8`, `s16`, `s32` | Signed integers | Temperature, coordinates |
| `bool` | Boolean (1 byte) | Flags |
| `bits` | Bit field extraction | Status flags |
| `float16` | IEEE 754 half-precision | Sensor readings |
| `bytes` | Raw byte array | MAC address, EUI |

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
  add: -40       # With offset
```

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

---

## Complex Structures

### How do I parse TLV (Type-Length-Value) payloads?

```yaml
fields:
  - type: tlv
    tag_size: 1
    cases:
      1:
        - name: temperature
          type: s16
          div: 10
      2:
        - name: humidity
          type: u8
```

### How do I parse based on a message type header?

Use `match`:

```yaml
fields:
  - name: msg_type
    type: u8
    
  - type: match
    field: msg_type
    cases:
      1:
        - name: temperature
          type: s16
      2:
        - name: gps_lat
          type: s32
```

### How do I parse bit flags?

Use `bits`:

```yaml
- name: status_flags
  type: bits
  bits:
    - name: motion
      size: 1
    - name: tamper
      size: 1
    - name: low_battery
      size: 1
    - name: reserved
      size: 5
```

---

## Migration

### How do I convert an existing JavaScript codec?

1. Analyze the codec to identify payload structure
2. Create YAML schema matching the structure
3. Test with known payloads
4. Validate with `validate_schema.py`

Converter tools exist for some vendors:
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
- Field type spelling (`u16` not `uint16`)
- Test vector hex format (spaces optional)

### Test vectors don't match

Verify:
- Endianness (`endian: big` or `endian: little`)
- Scaling factors (`div`, `mult`, `add`)
- Signed vs unsigned types

### Preprocessor can't find library files

Add library paths:

```bash
python tools/schema_preprocessor.py my_schema.yaml -L ../lib -o output.yaml
```

Or check that `schemas/library/` is in the expected location relative to your schema.

---

## Performance

### How fast is the schema interpreter?

Benchmarks show 200,000-400,000 decodes/second in Python, sufficient for any LoRaWAN deployment.

### What's the schema size?

| Format | Size |
|--------|------|
| YAML | 500-2000 bytes |
| JSON | 400-1500 bytes |
