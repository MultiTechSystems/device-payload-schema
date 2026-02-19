# Payload Schema Language Reference

Condensed reference for AI assistants and quick lookup.

## Document Structure

```yaml
name: string              # REQUIRED: unique identifier
version: integer          # REQUIRED: schema version
endian: big|little        # Default: big
description: string       # Optional
fields: [...]             # Field definitions (or use ports)
ports:                    # Port-based routing (or use fields)
  1: { fields: [...] }
  2: { fields: [...] }
definitions:              # Reusable field groups
  common_header: [...]
test_vectors: [...]       # Test cases
```

## Field Types

### Integer Types

| Type | Bytes | Description |
|------|-------|-------------|
| `u8`, `u16`, `u32`, `u64` | 1,2,4,8 | Unsigned integer |
| `s8`, `s16`, `s32`, `s64` | 1,2,4,8 | Signed integer (two's complement) |

### Floating Point Types

| Type | Bytes | Description |
|------|-------|-------------|
| `f16`, `f32`, `f64` | 2,4,8 | IEEE 754 float |

### Decimal Types

| Type | Description |
|------|-------------|
| `udec` | Unsigned nibble-decimal (BCD-like) |
| `sdec` | Signed nibble-decimal |

### String/Byte Types

| Type | Description |
|------|-------------|
| `ascii` | ASCII string (requires `length:`) |
| `hex` | Hex string output (requires `length:`) |
| `bytes` | Raw bytes (requires `length:`) |
| `base64` | Base64 encoded output (requires `length:`) |

Bytes format options:
```yaml
- name: device_eui
  type: bytes
  length: 8
  format: hex           # "0011223344556677"
  format: hex:upper     # "0011223344556677" uppercase
  format: base64        # Base64 encoded
  format: array         # [0, 17, 34, 51, ...]
  separator: ":"        # "00:11:22:33:44:55"
```

### Special Types

| Type | Description |
|------|-------------|
| `bool` | Boolean (0=false, nonzero=true) |
| `number` | Computed field (no wire bytes) |
| `string` | Literal string constant |
| `skip` | Skip bytes (padding) |
| `enum` | Enumerated values |
| `bitfield_string` | Bit flags as string |

### Bitfields

```yaml
type: u8[0:3]      # Bits 0-3 of byte (4 bits)
type: u16[8:15]    # High byte of u16
```

### Endian Prefix

```yaml
type: le_u16       # Little-endian
type: be_u32       # Big-endian (explicit)
```

### Byte Group (multiple values from shared bytes)

```yaml
- byte_group:
    bytes: 3
    fields:
      - name: value_a
        type: u8[0:3]
      - name: value_b
        type: u8[4:7]
```

## Arithmetic Modifiers

Applied in YAML key order:

```yaml
- name: temperature
  type: s16
  div: 10           # Divide by 10
  add: -40          # Then subtract 40
  # Result: (raw / 10) - 40
```

| Modifier | Effect |
|----------|--------|
| `add: n` | Add offset |
| `mult: n` | Multiply |
| `div: n` | Divide |

## Lookup Tables

```yaml
- name: status
  type: u8
  lookup: ["off", "on", "error", "unknown"]
```

## Computed Fields

### Polynomial (calibration curves)

```yaml
- name: raw_value
  type: u16
  div: 50

- name: calibrated
  type: number
  ref: $raw_value
  polynomial: [0.0000043, -0.00055, 0.0292, -0.053]  # Descending powers
  # Result: ax³ + bx² + cx + d
```

### Cross-Field Computation

```yaml
- name: ratio
  type: number
  compute:
    op: div          # div, mul, add, sub
    a: $field1       # Field reference or literal
    b: $field2
```

### Guard Conditions

```yaml
- name: safe_ratio
  type: number
  compute:
    op: div
    a: $numerator
    b: $denominator
  guard:
    when:
      - field: $denominator
        gt: 0          # gt, gte, lt, lte, eq, ne
    else: 0            # Fallback if condition fails
```

## Transform Operations

```yaml
transform:
  - sqrt: true        # √x
  - abs: true         # |x|
  - pow: 2            # x²
  - floor: 0          # Clamp lower bound
  - ceiling: 100      # Clamp upper bound
  - clamp: [0, 100]   # Both bounds
  - log10: true       # Base-10 logarithm
  - log: true         # Natural logarithm
```

## Conditional Parsing

### Switch (by field value)

```yaml
- name: msg_type
  type: u8

- switch:
    field: msg_type
    cases:
      1:
        - name: temperature
          type: s16
      2:
        - name: humidity
          type: u8
```

### Flagged (bitmask presence)

```yaml
- name: flags
  type: u8

- flagged:
    field: flags
    groups:
      - bit: 0
        fields:
          - name: temperature
            type: s16
      - bit: 1
        fields:
          - name: humidity
            type: u8
```

## Named Encodings

```yaml
- name: signed_value
  type: u16
  encoding: sign_magnitude   # Also: bcd, gray
```

## Value-Range Matching

For value-dependent transformations:

```yaml
- name: signed_value
  type: u16
  match_value:
    - when: "< 32768"
      # No transform (value as-is)
    - when: ">= 32768"
      add: -65536
```

Prefer `encoding:` for standard patterns; use `match_value` for custom ranges.

## Bitfield String

Parse bits into formatted string (e.g., version numbers):

```yaml
- name: firmware_version
  type: bitfield_string
  length: 2              # Bytes to read
  delimiter: "."         # Separator between parts
  prefix: "v"            # Optional prefix
  parts:
    - [8, 8]             # [start_bit, width] → major
    - [0, 8]             # [start_bit, width] → minor
# Input: 0x0102 → Output: "v1.2"
```

## Test Vectors

```yaml
test_vectors:
  - name: basic_reading
    description: "Normal temperature reading"
    payload: "00 E7 32"        # Hex, spaces ignored
    expected:
      temperature: 23.1
      humidity: 50
```

## Complete Example

```yaml
name: environment_sensor
version: 1
endian: big
description: Temperature and humidity sensor with battery

fields:
  - name: temperature
    type: s16
    div: 10
    unit: "°C"
    
  - name: humidity
    type: u8
    unit: "%"
    
  - name: battery_mv
    type: u16
    unit: "mV"
    
  - name: battery_percent
    type: number
    ref: $battery_mv
    transform:
      - add: -2000        # 2000mV = 0%
      - div: 12           # 3200mV = 100%
      - clamp: [0, 100]
    unit: "%"

test_vectors:
  - name: normal
    payload: "00E7 32 0C80"
    expected:
      temperature: 23.1
      humidity: 50
      battery_mv: 3200
      battery_percent: 100
      
  - name: cold
    payload: "FF9C 5A 0BB8"
    expected:
      temperature: -10.0
      humidity: 90
      battery_mv: 3000
      battery_percent: 83.3
```

## Enum Type

```yaml
- name: status
  type: enum
  size: 1
  values:
    0: "off"
    1: "on"
    2: "error"
```

## Repeat (Arrays)

```yaml
# Count-based
- name: readings
  type: repeat
  count: 4
  fields:
    - name: value
      type: u16

# Field-based count
- name: readings
  type: repeat
  count_field: num_readings
  fields:
    - name: value
      type: u16

# Until end of payload
- name: entries
  type: repeat
  until: end
  fields:
    - name: value
      type: u16
```

## Nested Objects

```yaml
- name: gps
  type: object
  fields:
    - name: latitude
      type: s32
      div: 10000000
    - name: longitude
      type: s32
      div: 10000000
```

## Variables

Store values for later reference:

```yaml
- name: device_type
  type: u8
  var: dev_type      # Store as variable

- switch:
    field: $dev_type  # Reference variable
    cases:
      1: [...]
      2: [...]
```

## TLV (Type-Length-Value)

```yaml
- tlv:
    tag_type: u8
    length_type: u8
    cases:
      0x01:
        - name: temperature
          type: s16
      0x02:
        - name: humidity
          type: u8
```

## Match Patterns

```yaml
- switch:
    field: msg_type
    cases:
      1: [...]                    # Exact match
      2..5: [...]                 # Range (2,3,4,5)
      0x10..0x1F: [...]           # Hex range
      _: [...]                    # Default case
```

## Skip (Padding)

```yaml
- name: _reserved
  type: skip
  length: 2          # Skip 2 bytes
```

## Definitions (Reusable Groups)

```yaml
definitions:
  header:
    - name: version
      type: u8
    - name: flags
      type: u8

fields:
  - use: header      # Include definition
  - name: payload
    type: bytes
    length: 10
```

## Port-Based Routing

```yaml
ports:
  1:
    description: "Sensor data"
    fields:
      - name: temperature
        type: s16
  2:
    description: "Status"
    fields:
      - name: battery
        type: u8
```

## Output Format Hints

```yaml
- name: temperature
  type: s16
  div: 10
  unit: "°C"
  ipso: 3303          # IPSO Smart Object ID
  senml_unit: "Cel"   # SenML unit
```

## Compact Format (Alternative Syntax)

```yaml
# Verbose
fields:
  - name: temp
    type: s16
  - name: hum
    type: u8

# Compact equivalent
format: ">hB"         # struct-like format string
names: [temp, hum]
```

## Quick Reference Card

```
TYPES:        u8 u16 u32 u64 | s8 s16 s32 s64 | f16 f32 f64 | bool
              ascii hex bytes base64 | number string | skip enum
              udec sdec | bitfield_string
              
STRUCTURES:   object | repeat | byte_group | tlv

MODIFIERS:    add mult div | lookup | polynomial | compute | guard | transform | match_value

CONDITIONALS: switch (value match) | flagged (bitmask) | tlv (tag dispatch)

TRANSFORMS:   sqrt abs pow floor ceiling clamp log10 log

COMPUTE OPS:  add sub mul div

GUARD OPS:    gt gte lt lte eq ne

ENCODINGS:    sign_magnitude bcd gray

MATCH:        exact | range (n..m) | default (_)

REFERENCES:   $field_name | use: definition_name
```
