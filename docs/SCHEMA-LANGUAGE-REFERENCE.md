# Payload Schema Language Reference

Complete reference for the LoRa Alliance Payload Schema specification (v0.5.0).

Normative requirement ids (`PS-nnn`) are cited where a rule is easy to get wrong or where
implementations have disagreed. The specification is the authority; this is a working
reference for authoring schemas in this repository.

## Document Structure

```yaml
name: string              # REQUIRED: unique identifier
version: integer          # REQUIRED: schema version
endian: big|little        # Default: big
description: string       # Optional
direction: uplink|downlink|bidirectional  # Default: uplink
fields: [...]             # Field definitions (or use ports)
ports:                    # Port-based routing (or use fields)
  1: { fields: [...] }
  2: { fields: [...] }
definitions:              # Reusable field groups, pulled in with $ref
  common_header: { fields: [...] }
metadata:                 # Network metadata enrichment (Python only)
  include: [...]
  timestamps: [...]
test_vectors: [...]       # Test cases
downlink_commands: [...]  # Command definitions (for downlink)
```

## Field Types

### Integer Types

| Type | Bytes | Description |
|------|-------|-------------|
| `u8`, `u16`, `u24`, `u32`, `u64` | 1,2,3,4,8 | Unsigned integer |
| `s8`, `s16`, `s24`, `s32`, `s64` | 1,2,3,4,8 | Signed integer (two's complement) |

**Note:** 24-bit types (`u24`, `s24`) are commonly used for GPS coordinates in compact formats.

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
| `hex` | Lowercase hex string (requires `length:`) |
| `hex:upper` | Uppercase hex string (requires `length:`) |
| `bytes` | Raw bytes, reported as a lowercase hex string (requires `length:`) |
| `base64` | Base64 encoded output (requires `length:`) |

A `bytes` or `hex` field reports a **lowercase** hex string (PS-074, PS-281), not an array
of numbers. Choose the output representation with the type, not with a key:

```yaml
- name: device_eui
  type: hex:upper       # AB CD -> "ABCD"; type: hex -> "abcd"; type: base64 -> "q80="
  length: 8
```

`format:` and `separator:` keys on a `bytes` field are read by Go, Java and C# but ignored
by the Python reference interpreter, so neither is portable.

#### `length: remaining`

Consumes every byte from the read position to the end of the payload (PS-013):

```yaml
- name: message_type
  type: u8
- name: stored_downlink
  type: bytes
  length: remaining     # whatever is left after message_type
```

- `remaining` is the **only** spelling. A negative integer is an internal sentinel the
  parsers map it to, and `validate_schema.py` rejects it in a schema.
- At the end of a payload it yields an empty value, not an error and not a one-byte read
  (PS-014).
- At most one field per nesting level may use it (PS-015), and only on a variable-length
  type — `bytes`, `hex`, `ascii`, `string`. On a fixed-width type it is a validation error.
- When encoding it contributes no fixed count; the value supplies its own length.

### Special Types

| Type | Description |
|------|-------------|
| `bool` | Boolean (0=false, nonzero=true) |
| `number` | Computed field, or a constant with `value:` (no wire bytes) |
| `string` | Constant with `value:` (no wire bytes), or text read with `length:` |
| `skip` | Skip bytes (padding) |
| `enum` | Enumerated values |
| `bitfield_string` | Bit flags as string |

### Bool Type

Boolean fields extract a single bit and convert to true/false:

```yaml
- name: motion_detected
  type: bool
  bit: 0           # Bit position (0-7)
  consume: 1       # Advance past byte (optional)
```

By default, bool fields do not advance the position, allowing multiple bits
from the same byte. Add `consume: 1` to advance after reading.

### Bitfields

```yaml
- name: low_nibble
  type: u8[0:3]      # Bits 0-3 of the byte (4 bits), inclusive
- name: high_nibble
  type: u8[4:7]
  consume: 1         # A bit range never advances by itself
```

`uN[start:end]` (inclusive) is the only bitfield spelling; `u8:2`, `u8[3+:2]`,
`bits<3,2>` and `bits:2@3` were withdrawn (CR-2026-006) and are rejected. A bit range
reads its base value big-endian whatever the schema's `endian` (PS-059), and consumes
nothing unless `consume: N` is given, so the last range over a byte needs `consume: 1`.

### Byte Order

`endian:` at schema level sets the default; a field's own `endian:` overrides it for that
field:

```yaml
endian: big
fields:
  - name: counter
    type: u16
    endian: little   # 01 00 -> 1
```

The override does not cascade into the members of a nested construct (`object`, `repeat`,
`match` ...); set it on each member. `le_`/`be_` type prefixes (`le_u16`) are not part of
the language — the Python and Java interpreters reject them as unknown types.

### Byte Group (multiple values from shared bytes)

```yaml
- byte_group:
    size: 1
    fields:
      - name: value_a
        type: u8[0:3]
      - name: value_b
        type: u8[4:7]
```

Shorthand (size inferred from field types):

```yaml
- byte_group:
    - name: value_a
      type: u8[0:3]
    - name: value_b
      type: u8[4:7]
```

## Arithmetic Modifiers

Bare modifiers are applied in the canonical order **mult, then div, then add**, however
the keys are written (PS-101/PS-102):

```yaml
- name: temperature
  type: s16
  add: -40          # Written first, applied last
  div: 10
  # Result: (raw / 10) - 40; raw 1280 -> 88
```

| Modifier | Effect |
|----------|--------|
| `mult: n` | Multiply (applied first) |
| `div: n` | Divide |
| `add: n` | Add offset (applied last; use a negative value to subtract) |

For any other order, use a `transform` list, whose stages apply in list order:

```yaml
- name: soil_temperature
  type: u16
  transform:
    - add: -400
    - div: 10       # (raw - 400) / 10; raw 1280 -> 88
```

## Lookup Tables

Two forms, and they differ in more than syntax — a value the table does not cover behaves
differently in each.

**Sequence** — indexed from zero (PS-104):

```yaml
- name: status
  type: u8
  lookup: ["off", "on", "error", "unknown"]
```

An index past the last entry is an **error** (PS-105). The payload does not match the
schema's shape, so reporting the raw integer under a name that promises a label would be
worse than failing.

**Mapping** — keys need not start at zero or be contiguous (PS-268):

```yaml
- name: button
  type: u8
  lookup:
    1: short_press
    2: long_press
    10: held
```

A value with no entry **omits the field** (PS-269) rather than reporting the raw number.
Declare a `default` to report something instead:

```yaml
- name: mode
  type: u8
  lookup:
    0: idle
    1: active
    default: unknown
```

Values may be numbers as well as strings (PS-106).

**A `default` label cannot be encoded back.** It stands for every value the table does not
list, so there is no original to recover; encoders report the field rather than writing a
plausible byte. The same applies to an `enum` `default` (PS-068).

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
    op: div          # add, sub, mul, div, mod, idiv
    a: $field1       # Field reference or literal
    b: $field2
```

**Available Operations:**

| Op | Description | Example |
|----|-------------|---------|
| `add` | Addition | `a + b` |
| `sub` | Subtraction | `a - b` |
| `mul` | Multiplication | `a * b` |
| `div` | Division | `a / b` |
| `mod` | Modulo (remainder) | `int(a) % int(b)` |
| `idiv` | Integer division | `int(a) // int(b)` |

**Nibble Extraction Example:**

```yaml
- name: rawByte
  type: u8

- name: upperNibble
  type: number
  compute:
    op: idiv
    a: $rawByte
    b: 16

- name: lowerNibble
  type: number
  compute:
    op: mod
    a: $rawByte
    b: 16
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

### Formula (Deprecated)

Legacy string-expression syntax. **Do not use it in new schemas; use `ref` + `transform`
or `compute`.**

```yaml
- name: temp_c
  type: number
  formula: "($raw_temp - 4000) / 100"  # raw_temp 5000 -> 10
```

`formula` is evaluated by the Python, Go and Java interpreters and the TS013 generator.
C# parses the key but never evaluates it, and the C interpreter has no formula support,
so a schema relying on it does not decode the same everywhere. The same result portably:

```yaml
- name: temp_c
  type: number
  ref: $raw_temp
  transform:
    - add: -4000
    - div: 100
```

## Transform Operations

Stages apply in list order:

```yaml
transform:
  - mult: 2           # also div:, add: (there is no sub:; add a negative)
  - sqrt: true        # √x (input clamped at 0)
  - abs: true         # |x|
  - pow: 2            # x²
  - log10: true       # Base-10 logarithm (input clamped at 1e-10)
  - log: true         # Natural logarithm (input clamped at 1e-10)
  - {op: round, decimals: 2}   # Round half-to-even
```

`floor:`, `ceiling:` and `clamp:` stages exist only in the Python interpreter and the TS013
generator; Go, Java and C# ignore them, so avoid them where cross-language results matter.

## Conditional Parsing

### Match (by field value)

```yaml
- name: msg_type
  type: u8

- match:
    field: $msg_type
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

| Encoding | Description |
|----------|-------------|
| `sign_magnitude` | Bit 15 is sign, bits 0-14 are magnitude |
| `bcd` | Binary-coded decimal |
| `gray` | Gray code |

**`encoding:` is implemented in the Python interpreter only.** Go, Java, C#, C and the
TS013 generator ignore the key and report the raw value, so a schema using it decodes
differently outside Python.

`match_value` (value-range conditional transforms) appears in the specification's field
table but is implemented in no interpreter; do not use it.

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
    fPort: 1                   # Needed for a port-based schema (fport also accepted)
    source: vendor-doc         # vendor-doc | vendor-codec | field-capture | spec-example | generated
    expected:
      temperature: 23.1
      humidity: 50

  - name: encoding_test        # An encode vector: input + expected_payload
    source: vendor-doc
    input:
      temperature: 23.1
      humidity: 50
    expected_payload: "00E732"
```

A vector is either a decode vector (`payload` + `expected`) or an encode vector (`input` +
`expected_payload`), never both (PS-297). Declare `source:` on every vector: expected
values recorded from this repository's own decoder are `generated`, and a schema with no
independently sourced vector is capped at Silver (PS-264).

## Enum Type

```yaml
- name: status
  type: enum
  base: u8
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

# Count taken from an earlier field (note the $)
- name: num_readings
  type: u8
- name: readings
  type: repeat
  count: $num_readings
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

- match:
    field: $dev_type  # Reference variable
    cases:
      1: [...]
      2: [...]
```

A name beginning with `_` is internal: it becomes a variable that later fields can
reference, but it is not reported in the output. Use it for intermediates.

## Computed Output Keys (`name_from`)

The reported key comes from a template filled in from values decoded earlier in the same
payload (PS-265). The field's own `name` is then **not** a key in the output (PS-266) — it
stays available as a variable.

```yaml
- name: channel
  type: u8
  var: ch
- name: reading
  type: u16
  div: 10
  name_from: channel_${ch}_reading   # -> "channel_3_reading"
```

- `${...}` references a field's `name` or its `var` alias; both resolve.
- A reference the payload has not decoded yet is an error, not an empty substitution.
- An integral value renders without a fraction — `channel_3_`, not `channel_3.0_`.
- A `lookup` reference substitutes the label, so the key set is closed when every
  reference is.

This is the one construct whose output cannot be round-tripped: encoding looks a field up
by its schema name, and the decoded output holds the resolved key instead.

## TLV (Type-Length-Value)

Parse tag-based variable content. Supports single and multi-byte tags.

```yaml
- tlv:
    tag_size: 1           # Tag size in bytes (1, 2, or more)
    length_size: 1        # Length field size (0 = implicit/no length field)
    merge: true           # Merge results into parent (default)
    unknown: skip         # skip|error|raw for unknown tags
    cases:
      0x01:
        - name: temperature
          type: s16
      0x02:
        - name: humidity
          type: u8
```

### Multi-Byte Tags (Tektelic-style)

```yaml
- tlv:
    tag_size: 2           # 2-byte tags (big-endian)
    length_size: 0        # Implicit length from case definition
    cases:
      0x00BA:             # Battery status
        - name: battery_level
          type: u8
      0x0B67:             # Ambient temperature
        - name: temperature
          type: s16
          div: 10
```

### Composite Tags

For protocols with multi-field tag structures:

```yaml
- tlv:
    tag_fields:
      - name: channel
        type: u8
      - name: sensor_type
        type: u8
    tag_key: [channel, sensor_type]
    cases:
      "[1, 103]":         # Exact: channel 1, type 0x67
        - name: ch1_temperature
          type: s16
      "[2, !0]":          # Channel 2, any type except 0
        - name: ch2_value
          type: u8
      "[3, *]":           # Channel 3, any type
        - name: ch3_value
          type: u8
```

A composite key is a quoted string; a bare YAML list (`[1, 0x67]:`) is not a valid mapping
key and the file fails to load. The corpus writes the components in decimal.

## Match Patterns

```yaml
- match:
    field: $msg_type
    cases:
      1: [...]                    # Exact match
      "2..5": [...]               # Inclusive range (2,3,4,5)
      "16..31": [...]             # Range bounds are decimal
      default: [...]              # Fallback case
```

Range bounds must be decimal: `"0x10..0x1F"` matches nothing. The fallback key is
`default`; `_` is not recognised. With no `default`, an unmatched value fails the decode.

## Skip (Padding)

```yaml
- name: _reserved
  type: skip
  length: 2          # Skip 2 bytes
```

## Definitions (Reusable Groups)

```yaml
definitions:
  common_header:
    fields:
      - name: version
        type: u8
      - name: flags
        type: u8

fields:
  - $ref: "#/definitions/common_header"   # Splices version and flags in here
  - name: payload
    type: hex
    length: 2
```

A definition is an object with a `fields:` list, and `$ref` splices those fields into the
list where it appears — schema-level `fields:` or a port's. `01 02 AB CD` decodes to
`{version: 1, flags: 2, payload: "abcd"}`. The top-level `header:` block is gone; use a
definition for a common header.

`use:`, `rename:` and `prefix:` (including cross-file `use: file.yaml#name` and
`std/...` library paths) are resolved only by `tools/schema_preprocessor.py`, not by any
interpreter — given to an interpreter directly, a `use:` entry is misread as an ordinary
field. Their withdrawal is proposed in spec CR-2026-045; use `$ref`.

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

## Downlink Encoding

### Direction Property

```yaml
name: device_config
direction: downlink        # or: bidirectional

fields:
  - name: interval
    type: u16
    mult: 60              # Minutes to seconds
  - name: threshold
    type: u8
```

### Arithmetic Reversal

When encoding downlinks, arithmetic is reversed automatically:

| Decode (uplink) | Encode (downlink) |
|-----------------|-------------------|
| `mult: n` | `div: n` |
| `div: n` | `mult: n` |
| `add: n` | subtract `n` |

The reversal runs in the opposite order to decoding: subtract `add`, multiply by `div`,
divide by `mult`, then round to the wire integer.

### Command-Based Downlinks

`downlink_commands` is handled by the Python interpreter (`encode_command()`,
`decode_command()`) and the TS013 generator only; it is not in the specification, and
Go, Java, C# and C ignore it. A schema must still declare `fields` or `ports` (PS-004),
so commands sit beside a layout, as in the bidirectional example below.

```yaml
name: device_commands
version: 1
direction: downlink

fields:
  - name: ack
    type: u8

downlink_commands:
  set_interval:
    command_id: 0x01
    fields:
      - name: interval_minutes
        type: u16
  
  reboot:
    command_id: 0x02
    fields: []            # No payload
  
  set_threshold:
    command_id: 0x03
    fields:
      - name: low
        type: u8
      - name: high
        type: u8
```

### Bidirectional Schema

```yaml
name: env_sensor
direction: bidirectional

fields:
  # Uplink: sensor readings
  - name: temperature
    type: s16
    div: 10
  - name: humidity
    type: u8

downlink_commands:
  # Downlink: configuration
  set_interval:
    command_id: 0x01
    fields:
      - name: interval
        type: u16
```

## Network Metadata Enrichment

Include TS013 input fields in decoder output. The `metadata` block is optional (PS-310)
and **only the Python interpreter implements it** (`decode(payload, fPort=...,
input_metadata={...})`); every other implementation decodes the payload and ignores the
block. A decoded field wins a name collision, and a value that cannot be resolved omits
its key with a warning.

```yaml
metadata:
  include:
    - name: received_at
      source: $recvTime
    - name: rssi
      source: $rxMetadata[0].rssi
    - name: snr
      source: $rxMetadata[0].snr
    - name: port
      source: $fPort
```

### Timestamp Modes

```yaml
metadata:
  timestamps:
    # Mode 1: Use network receive time
    - name: timestamp
      mode: rx_time

    # Mode 2: Compute from offset field
    - name: measurement_time
      mode: subtract
      offset_field: seconds_ago

    # Mode 3: Convert Unix epoch from payload
    - name: device_time
      mode: unix_epoch
      field: unix_timestamp
```

`rx_time` copies `recvTime` as given. `subtract` and `unix_epoch` produce a UTC ISO 8601
string with milliseconds: `seconds_ago: 60` with `recvTime: "2024-01-01T00:01:00Z"`
gives `measurement_time: "2024-01-01T00:00:00.000Z"`, and `unix_timestamp: 1694498816`
gives `device_time: "2023-09-12T06:06:56.000Z"`.

### Available TS013 Input Fields

| Field | Description |
|-------|-------------|
| `$fPort` | LoRaWAN FPort |
| `$recvTime` | Server receive time (ISO 8601) |
| `$devEui` | Device EUI |
| `$rxMetadata[n].rssi` | RSSI from gateway n |
| `$rxMetadata[n].snr` | SNR from gateway n |
| `$rxMetadata[n].gatewayId` | Gateway identifier |

## Output Format Hints

```yaml
- name: temperature
  type: s16
  div: 10
  unit: "°C"
  ipso: {object: 3303, instance: 0, resource: 5700}   # IPSO object/instance/resource
  senml: {name: "temperature", unit: "Cel"}           # SenML record name and unit
  semantic: "air.temperature"                          # Dotted semantic name
```

All three are needed for the semantic points in `tools/score_schema.py`; see
`schemas/devices/decentlab/dl-5tm.yaml` for the canonical form. A bare `ipso: 3303` is
still read, but the older `senml_unit:` key is not, so it earns no SenML points.
Annotate only where the value's unit matches the IPSO/SenML definition — a percentage is
not IPSO 3316 (voltage), and a pressure in hPa is not SenML `Pa`.

**Common IPSO Smart Objects:**

| ID | Name | Use Case |
|----|------|----------|
| 3200 | Digital Input | Binary sensors |
| 3301 | Illuminance | Light (lux) |
| 3303 | Temperature | Temperature (°C) |
| 3304 | Humidity | Humidity (%) |
| 3308 | Set Point | Thermostat setpoints |
| 3316 | Voltage | Battery voltage |
| 3323 | Pressure | Pressure sensors |
| 3325 | Concentration | CO2/gas (ppm) |
| 3330 | Distance | Range/level sensors |
| 3337 | Positioner | Valve position (%) |

### M-Bus / Utility Format Hints

```yaml
- name: volume
  type: u32
  div: 1000
  unit: "m³"
  mbus_dif: 0x04        # 32-bit integer
  mbus_vif: 0x14        # Volume in 0.001 m³
```

## Semantic Fields

Fields for value quality tracking and IoT interoperability.

### Valid Range

Declares expected output value bounds. Out-of-range values produce a quality flag and a
warning but are not modified.

```yaml
- name: temperature
  type: s16
  div: 100
  unit: "°C"
  valid_range: [-40, 85]    # Expected operating range
```

**Interpreter Behavior** (Python, payload `0929` then `D8F1`):

```python
# Normal reading: result.data
{"temperature": 23.45, "_quality": {"temperature": "good"}}

# Out of range: result.data, and the message in result.warnings
{"temperature": -99.99, "_quality": {"temperature": "out_of_range"}}
# result.warnings == ["temperature: value -99.99 outside valid range [-40, 85]"]
```

`_quality` is added to the output by the Python, Go and C# interpreters and the TS013
codec, and only when a decoded field declares `valid_range`; Java does not emit it.
Python reports the warning in `result.warnings`; Go, Java and C# put it in the output
under `_warnings`.

### Resolution

Documents minimum detectable change. Useful for fixed-point scaling and code generation.

```yaml
- name: temperature
  type: s16
  div: 100
  unit: "°C"
  resolution: 0.01    # 0.01°C steps
```

**Interpreter Behavior:** Documentation only; the decoded value is not rounded to it.
The Python interpreter returns it from `get_field_metadata()` with `unit`, `valid_range`
and `unece`.

### UNECE Unit Codes

Standard unit identifiers per UNECE Recommendation 20.

```yaml
- name: temperature
  type: s16
  div: 100
  unit: "°C"
  unece: "CEL"        # UNECE code for Celsius
```

**Common UNECE Codes:**

| Measurement | Code | Display |
|-------------|------|---------|
| Temperature (C) | CEL | °C |
| Temperature (F) | FAH | °F |
| Humidity (%) | P1 | % |
| Pressure (Pa) | PAL | Pa |
| Pressure (bar) | BAR | bar |
| Voltage | VLT | V |
| Current | AMP | A |
| Power (W) | WTT | W |
| Distance (m) | MTR | m |
| Distance (mm) | MMT | mm |
| Mass (kg) | KGM | kg |
| Time (s) | SEC | s |

### Combined Example

```yaml
- name: temperature
  type: s16
  div: 100
  unit: "°C"
  valid_range: [-40, 85]
  resolution: 0.01
  unece: "CEL"
  ipso: {object: 3303, instance: 0, resource: 5700}
  senml: {name: "temperature", unit: "Cel"}
  semantic: "air.temperature"
```

## Compact Format (Alternative Syntax)

A struct-like string in place of the `fields:` list. **Python interpreter only** (Go has a
separate `DecodeCompact()` function; Java, C# and C have none), and `validate_schema.py`
rejects it (`'fields' must be an array`), so a repository schema cannot use it.

```yaml
# Single-line format with names
fields: ">B:version H:length I:timestamp"

# With padding (2x = skip 2 bytes)
fields: ">B:type 2x H:value I:timestamp"
# 01 FFFF 00E7 00000064 -> {type: 1, value: 231, timestamp: 100}
```

There is no `format:` + `names:` form: a schema written that way decodes to `{}`.

### Format Characters

| Char | Type | Bytes |
|------|------|-------|
| `b/B` | s8/u8 | 1 |
| `h/H` | s16/u16 | 2 |
| `i/I` | s32/u32 | 4 |
| `q/Q` | s64/u64 | 8 |
| `e` | f16 | 2 |
| `f` | f32 | 4 |
| `d` | f64 | 8 |
| `?` | bool | 1 |
| `x` | skip (`Nx` skips N) | 1 |
| `>` | big-endian | - |
| `<` | little-endian | - |

## OTA Schema Transfer

Schemas can be transmitted over-the-air from device to network.

### Binary Schema Encoding

Schemas compile to compact binary for transmission:

```yaml
# ~5 bytes for simple field
- name: temperature
  type: s16
  div: 10

# Compiles to: 0x11 0x0A 0x00 [name...]
```

### QR Code Embedding

```
LW:1:DevEUI:AppEUI:AppKey:SCHEMA:Base64EncodedSchema
```

## Schema Validation

### Validation Levels

| Level | Description |
|-------|-------------|
| ERROR | Must fix before use |
| WARNING | Should review |
| INFO | Best practice suggestion |

### Common Validations

```yaml
# WARNING: Missing IPSO for known sensor type
- name: temperature          # "Consider adding ipso: 3303 for standard sensor type"
  type: s16
  div: 10
```

Other messages include an ERROR for an unknown type, a withdrawn bitfield spelling or a
top-level `header:`, a WARNING for no test vectors, and INFO suggestions for fewer than 3
vectors or no zero/maximum vector. A reference to a variable nothing binds is **not**
caught by the validator — `match: {field: $undefined_var}` validates and fails at decode
("no earlier field or var: binds it"), which is one reason every branch needs a vector.

## Quality Scoring

`tools/score_schema.py` scores a schema out of 100 and assigns the specification's
Section 10 tier: **Platinum** 95-100%, **Gold** 85-94%, **Silver** 70-84%, **Bronze**
60-69%, **Rejected** below 60%.

| Points | Requirement |
|---|---|
| 12 | Passes structural validation |
| 8 | Has usable `test_vectors` (payload *and* expected) |
| 20 | Python interpreter decodes all vectors correctly |
| 15 | Generated JS codec decodes all vectors correctly |
| 12 | All `match`/`flagged`/port branches entered by a vector |
| 8 | Edge cases covered (zero, max, negative, min payload) |
| 5 | At least 5 vectors |
| 20 | Correct IPSO (7) + SenML (7) + semantic (6) annotation of detectable sensor fields |

Gold and Platinum also have **gates** (PS-239, PS-264): a schema is capped at Silver,
whatever it scores, unless it has at least 5 vectors, all vectors pass, every branch is
covered, edge-case vectors are present, no annotation is incorrect, and at least one
vector declares an independent `source:` (`vendor-doc`, `vendor-codec`, `field-capture`
or `spec-example`; `generated` does not count). `--no-require-provenance` scores without
the provenance gate. A schema with no test vectors cannot leave Rejected.

## TS013 Code Generation

Schemas generate TS013-compliant JavaScript decoders:

```javascript
// Generated from schema
function decodeUplink(input) {
  // ... generated decoder logic
  return {
    data: { temperature: 23.1, humidity: 50 },
    warnings: [],
    errors: []
  };
}

function encodeDownlink(input) {
  // ... generated encoder logic
  return {
    fPort: 1,
    bytes: [0x00, 0xE7, 0x32]
  };
}
```

## Complete Example

A fixed three-field uplink with canonical annotations. It validates, passes all five
vectors in the Python interpreter and the generated JS codec, and scores 100%. Its
vectors are marked `source: generated` because the device is invented, so
`score_schema.py` caps it at **Silver** (PS-264); with
`--no-require-provenance` it is **Platinum**. A real schema reaches Gold or Platinum
by taking at least one vector from the vendor.

```yaml
name: environment_sensor
version: 1
endian: big
description: Temperature, humidity and battery voltage sensor

fields:
  - name: temperature
    type: s16
    div: 10
    unit: "°C"
    ipso: {object: 3303, instance: 0, resource: 5700}
    senml: {name: "temperature", unit: "Cel"}
    semantic: "air.temperature"
    valid_range: [-40, 85]

  - name: humidity
    type: u8
    unit: "%"
    ipso: {object: 3304, instance: 0, resource: 5700}
    senml: {name: "humidity", unit: "%RH"}
    semantic: "air.humidity"
    valid_range: [0, 100]

  - name: battery_voltage
    type: u16
    div: 1000
    unit: "V"
    ipso: {object: 3316, instance: 0, resource: 5700}
    senml: {name: "vbat", unit: "V"}
    semantic: "battery.voltage"

# Illustrative: this device is invented, so its vectors are computed by hand from
# the layout above and declared `source: generated`. A real schema takes them from
# the vendor's documentation or codec (PS-264).
test_vectors:
  - name: normal
    payload: "00E7 32 0C80"
    source: generated
    expected:
      temperature: 23.1
      humidity: 50
      battery_voltage: 3.2

  - name: cold
    payload: "FF9C 5A 0BB8"
    source: generated
    expected:
      temperature: -10.0
      humidity: 90
      battery_voltage: 3.0

  - name: zero
    payload: "0000 00 0000"
    source: generated
    expected:
      temperature: 0.0
      humidity: 0
      battery_voltage: 0.0

  - name: extremes
    payload: "8000 FF FFFF"
    source: generated
    expected:
      temperature: -3276.8
      humidity: 255
      battery_voltage: 65.535

  - name: warm_dry
    payload: "0190 0A 0E10"
    source: generated
    expected:
      temperature: 40.0
      humidity: 10
      battery_voltage: 3.6
```

## Quick Reference Card

```
TYPES:        u8 u16 u24 u32 u64 | s8 s16 s24 s32 s64 | f16 f32 f64 | bool
              ascii hex hex:upper bytes base64 | number string | skip enum
              udec sdec | bitfield_string
              
STRUCTURES:   object | repeat | byte_group | tlv

BITFIELDS:    uN[start:end] (inclusive; consume: N to advance) | bool + bit: N

BYTE ORDER:   endian: big|little (schema level, or per field; no cascade)

TAG KEYS:     "[1, 103]" exact | "[1, !0]" excluding | "[2, *]" any

MODIFIERS:    mult div add (canonical order) | lookup | polynomial | compute | guard
              | transform

CONDITIONALS: match (value dispatch) | flagged (bitmask) | tlv (tag dispatch)

TRANSFORMS:   mult div add | sqrt abs pow log10 log | {op: round}
              (floor ceiling clamp: Python and TS013 only)

COMPUTE OPS:  add sub mul div mod idiv

GUARD OPS:    gt gte lt lte eq ne

ENCODINGS:    sign_magnitude bcd gray (Python only)

MATCH:        exact | range ("n..m", decimal) | default

REFERENCES:   $field_name | var: name | $ref: '#/definitions/name'

SEMANTICS:    unit | ipso {object, instance, resource} | senml {name, unit}
              | semantic | valid_range | resolution | unece

DIRECTIONS:   uplink | downlink | bidirectional

METADATA:     include | timestamps (rx_time, subtract, unix_epoch) (Python only)
```

## See Also

- [SCHEMA-DEVELOPMENT-GUIDE.md](SCHEMA-DEVELOPMENT-GUIDE.md) - Tutorial and best practices
- [OUTPUT-FORMATS.md](OUTPUT-FORMATS.md) - Output format specifications (IPSO, SenML)
- [C-CODE-GENERATION.md](C-CODE-GENERATION.md) - Embedded firmware codec generation
- [BIDIRECTIONAL-CODEC.md](BIDIRECTIONAL-CODEC.md) - Downlink encoding details
