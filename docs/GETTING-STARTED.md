# Getting Started for Sensor Vendors

Create one schema, generate codecs for all platforms.

## What You Get

```
Your Schema (YAML)
       │
       ├──► JavaScript codec (TTN, ChirpStack, Helium)
       ├──► Python codec (testing, data pipelines)
       ├──► C header (embedded firmware)
       └──► JSON Schema (API documentation)
```

## 5-Minute Example

### 1. Create Your Schema

Create `schemas/mycompany/temp-sensor.yaml`:

```yaml
name: mycompany_temp_sensor
version: 1
description: "MyCompany Temperature Sensor v1.0"
endian: big

fields:
  - name: temperature
    type: s16
    div: 100
    unit: "°C"
  
  - name: humidity
    type: u8
    unit: "%"
  
  - name: battery
    type: u8
    div: 10
    unit: "V"

test_vectors:
  - name: "normal_reading"
    payload: "09C44121"
    source: vendor-doc
    expected:
      temperature: 25.0
      humidity: 65
      battery: 3.3
  
  - name: "cold_reading"
    payload: "FF38281E"
    source: vendor-doc
    expected:
      temperature: -2.0
      humidity: 40
      battery: 3.0
```

**`source:` says where a vector's values came from**: `vendor-doc`, `vendor-codec`,
`field-capture`, `spec-example`, or `generated` (recorded from this toolkit's own
decoder). Take expected values from your datasheet or firmware, not from the
decoder's output. A schema with no independently sourced vector is capped at Silver
(PS-264), however well it scores otherwise.

**Payload breakdown:**
- `09C4` = 2500 → 2500/100 = 25.0°C
- `41` = 65 → 65%
- `21` = 33 → 33/10 = 3.3V

### 2. Validate Your Schema

```bash
python3 tools/validate_schema.py schemas/mycompany/temp-sensor.yaml -v
```

Output (abridged):
```
Schema: VALID
...
Test Vectors: 2/2 passed
--------------------------------------------------
✓ normal_reading: PASS
✓ cold_reading: PASS
--------------------------------------------------
PASSED: All 2 tests passed
```

### 3. Generate Codecs

**JavaScript (for TTN/ChirpStack):**
```bash
python3 tools/generate_ts013_codec.py schemas/mycompany/temp-sensor.yaml -o output/
```

**C Header (for firmware):**
```bash
python3 tools/generate_firmware_codec.py schemas/mycompany/temp-sensor.yaml -o include/temp_sensor_codec.h
```

### 4. Check Quality Score

```bash
python3 tools/score_schema.py schemas/mycompany/temp-sensor.yaml -v
```

Output for the schema above:
```
Test vectors: 2
Python tests: 2 passed, 0 failed
JS tests: pass
Branch coverage: 100%
Edge cases: ['max', 'negative', 'min_payload'], missing: ['zero values']
Semantic: 3 sensors detected, 0 IPSO mapped, 0 SenML mapped
Provenance: independent ({'vendor-doc': 2})

temp-sensor.yaml: SILVER (73.0%)
Recommendations:
  - Add more test vectors (recommend at least 3)
  - Add test vector for zero values
  - Add IPSO object mappings for standard sensors (3 fields)
  ...
```

Tiers: Platinum 95-100%, Gold 85-94%, Silver 70-84%, Bronze 60-69%, Rejected below
60%. Test coverage alone reaches only Silver; Gold and Platinum also need the
semantic annotations described below, at least five vectors including edge cases, and
an independently sourced vector.

## Using the Sensor Library

Build schemas faster using pre-defined sensor types with IPSO annotations already included.

### Quick Start with Library

```yaml
name: my_env_sensor
version: 1
endian: big

fields:
  - $ref: "schemas/library/common/headers.yaml#/definitions/msg_type_header"
  - $ref: "schemas/library/profiles/env-sensor.yaml#/definitions/temp_humidity"
  - $ref: "schemas/library/sensors/power.yaml#/definitions/battery_mv"
```

### Process Schema

```bash
# Resolve library references
python3 tools/schema_preprocessor.py schemas/mycompany/my_env_sensor.yaml -o schemas/mycompany/my_env_sensor_resolved.yaml

# Validate the resolved schema
python3 tools/validate_schema.py schemas/mycompany/my_env_sensor_resolved.yaml -v
```

The preprocessing step is required: the interpreters resolve only a local
`$ref: '#/definitions/<name>'`, so they reject a `$ref` into another file.

### Available Sensor Types

| Category | Library File | Sensors |
|----------|--------------|---------|
| Environmental | `schemas/library/sensors/environmental.yaml` | temperature, humidity, pressure, CO2, TVOC, illuminance |
| Power | `schemas/library/sensors/power.yaml` | battery_mv, battery_pct, voltage, current, power, energy |
| Position | `schemas/library/sensors/position.yaml` | latitude, longitude, altitude, accelerometer, gyroscope |
| Digital | `schemas/library/sensors/digital.yaml` | digital_input, digital_output, counter, presence |
| Distance | `schemas/library/sensors/distance.yaml` | distance_mm, level_pct, radar |
| Flow | `schemas/library/sensors/flow.yaml` | flow_rate, total_volume |

### Sensor Profiles (Pre-built Combinations)

| Profile | File | Contents |
|---------|------|----------|
| `temp_humidity` | `schemas/library/profiles/env-sensor.yaml` | Temperature + Humidity |
| `temp_humidity_pressure` | `schemas/library/profiles/env-sensor.yaml` | + Pressure |
| `indoor_air_quality` | `schemas/library/profiles/env-sensor.yaml` | + CO2 + TVOC |
| `gps_basic` | `schemas/library/profiles/tracker.yaml` | Lat + Lon + Alt |
| `full_tracker` | `schemas/library/profiles/tracker.yaml` | GPS + Speed + Heading + Battery |

### Multiple Sensors of Same Type

Use `rename:` or `prefix:` when you have multiple sensors. Like cross-file `$ref`, these
are resolved only by `tools/schema_preprocessor.py`; no interpreter reads them, so
always decode the resolved file:

```yaml
fields:
  # Two temperature sensors
  - $ref: "schemas/library/sensors/environmental.yaml#/definitions/temperature_c_div10"
    rename:
      temperature: indoor_temp
      
  - $ref: "schemas/library/sensors/environmental.yaml#/definitions/temperature_c_div10"
    rename:
      temperature: outdoor_temp

  # Or use prefix for groups
  - $ref: "schemas/library/profiles/env-sensor.yaml#/definitions/temp_humidity"
    prefix: "zone1_"
    
  - $ref: "schemas/library/profiles/env-sensor.yaml#/definitions/temp_humidity"
    prefix: "zone2_"
```

### Benefits of Using the Library

1. **IPSO object numbers included** - as `semantic: {ipso: N}`; the scorer counts only the
   canonical `ipso:`/`senml:`/`semantic:` form shown under "Adding Semantic Annotations"
2. **Consistent scaling** - Standard units and precision across devices
3. **Less typing** - Common patterns ready to use
4. **SenML units** - RFC 8428 compliance built-in

See `schemas/library/README.md` for the complete library reference.

## Common Patterns

### Signed vs Unsigned

```yaml
# Unsigned (0 to 65535)
- name: distance
  type: u16
  unit: "mm"

# Signed (-32768 to 32767)
- name: temperature
  type: s16
  div: 100
  unit: "°C"
```

### Scaling Values

```yaml
# Device sends 2500, decoder outputs 25.00
- name: temperature
  type: s16
  div: 100

# Device sends 33, decoder outputs 3.3
- name: battery
  type: u8
  div: 10
```

### Boolean Flags

```yaml
# Two flags in one byte: bit 0 and bit 1
- name: motion_detected
  type: bool
  bit: 0

- name: door_open
  type: bool
  bit: 1
  consume: 1    # last flag in the byte advances past it
```

A `bool` with `bit:` reads one bit and outputs `true`/`false`; it does not move the read
position, so the last flag of a byte needs `consume: 1`.

### Enumerations

```yaml
- name: status
  type: u8
  lookup:
    0: "ok"
    1: "low_battery"
    2: "sensor_error"
    3: "tamper"
```

A value with no entry (here, 4 and above) omits the field unless the lookup declares a
`default:`.

### Optional Sensor Groups (Flagged)

When your device has optional sensors controlled by a flags byte:

```yaml
fields:
  - name: flags
    type: u8
  
  - flagged:
      field: flags
      groups:
        - bit: 0
          fields:
            - name: temperature
              type: s16
              div: 100
        - bit: 1
          fields:
            - name: humidity
              type: u8
```

Payload with both: `03 09C4 41` (flags=0x03, temp, humidity)
Payload temp only: `01 09C4` (flags=0x01, temp only)

### Computed Fields

When one value derives from others:

```yaml
- name: temp_raw
  type: u16

- name: temperature
  type: number
  ref: $temp_raw
  transform:
    - add: -4000     # there is no `sub:` stage; add a negative
    - div: 100
  unit: "°C"
```

`transform` stages apply in list order: raw `1A0A` (6666) gives (6666 - 4000) / 100 =
26.66. Bare `mult`/`div`/`add` keys always apply as mult, then div, then add, whatever
order they are written in, so use `transform` when you need another order.

## Adding Semantic Annotations

For interoperability with IoT platforms, add standard annotations:

```yaml
- name: temperature
  type: s16
  div: 100
  unit: "°C"
  ipso: {object: 3303, instance: 0, resource: 5700}
  senml: {name: "temperature", unit: "Cel"}
  semantic: "air.temperature"
```

This enables automatic conversion to IPSO Smart Objects, SenML (RFC 8428), and TTN normalized format.

## Test Vector Best Practices

Include test vectors for:

1. **Normal operation** - Typical readings
2. **Boundary values** - Min/max sensor range
3. **Negative values** - For signed types
4. **Edge cases** - Zero, maximum, minimum payload
5. **All flag combinations** - For flagged schemas

```yaml
test_vectors:
  - name: "normal"
    payload: "09C44121"
    source: vendor-doc
    expected: {temperature: 25.0, humidity: 65, battery: 3.3}
  
  - name: "max_temp"
    payload: "7FFF6428"
    source: vendor-doc
    expected: {temperature: 327.67, humidity: 100, battery: 4.0}
  
  - name: "negative_temp"
    payload: "FF38281E"
    source: vendor-doc
    expected: {temperature: -2.0, humidity: 40, battery: 3.0}
  
  - name: "zero_values"
    payload: "00000000"
    source: vendor-doc
    expected: {temperature: 0.0, humidity: 0, battery: 0.0}
```

## Directory Structure

```
schemas/
└── mycompany/
    ├── temp-sensor.yaml
    ├── door-sensor.yaml
    └── multi-sensor.yaml

output/
└── temp-sensor_codec.js  # generate_ts013_codec.py -o output/
```

## Next Steps

1. **[Sensor Library](../schemas/library/README.md)** - Pre-built sensor definitions with IPSO mappings
2. **[Schema Language Reference](SCHEMA-LANGUAGE-REFERENCE.md)** - Full field type and modifier documentation
3. **[Output Formats](OUTPUT-FORMATS.md)** - IPSO, SenML, TTN normalized output
4. **[Bidirectional Codec](BIDIRECTIONAL-CODEC.md)** - Downlink encoding for device configuration
5. **[C Code Generation](C-CODE-GENERATION.md)** - Embedded firmware integration

## Getting Help

- Check existing schemas in `schemas/` for examples
- Run `python3 tools/validate_schema.py --help` for options
- Quality scoring tool explains what's missing
