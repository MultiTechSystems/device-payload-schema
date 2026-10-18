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

Each integer type has fixed aliases, and no others (PS-049, PS-326); they are case-sensitive:

| Type | Aliases |
|------|---------|
| `u8`, `u16`, `u24`, `u32`, `u64` | `uint8`, `uint16`, `uint24`, `uint32`, `uint64` |
| `s8`, `s16`, `s24`, `s32`, `s64` | `i8`, `i16`, `i24`, `i32`, `i64` and `int8`, `int16`, `int24`, `int32`, `int64` |

### Checksum Fields

An unsigned integer field may hold a check value over the bytes before it (PS-585 to
PS-587). It declares `checksum`, a mapping:

| Key | Meaning |
|-----|---------|
| `algo` | `sum8` (sum of the bytes, modulo 256), `xor8` (the bytes XORed), `crc8/smbus` (poly 0x07, init 0, not reflected) or `crc16/kermit` (poly 0x1021 reflected, init 0) |
| `seed` | starting value of `sum8` or `xor8`, 0 to 255 (default 0); not allowed with a CRC |
| `from` | byte offset where the covered range starts, counted from the frame's first byte wherever the field sits (default 0) |
| `on_mismatch` | `error` (default): the decode fails; `warn`: a warning, and the rest is reported |

The covered range runs from `from` to the byte before the field. A decoder compares the
value read (in the field's type and byte order) with the checksum of that range; an encoder
computes it over the bytes it wrote and ignores any input value. A checksum field may sit at
the top level, in a port entry, in a match case, in a flagged group, and after a construct
that declares `reserve` - a repeat's `trailer` included; one that is a byte_group member, or
sits within a tlv entry, a `records` entry or a repeat element, is rejected at load
(PS-608). A range that covers nothing (`from` at or past the field) is a decode error and an
encode rejection (PS-608). A type other than `uN`, an unknown `algo`, a `seed` with a CRC,
or a type too narrow for the result (`u8` for `crc16/kermit`) is rejected at load.

```yaml
- {name: _counter, type: u8}
- {name: volume, type: u32, div: 1000}
- {name: _lrc, type: u8, checksum: {algo: xor8, seed: 0x55}}       # Jooby LRC
- {name: _crc, type: u16, endian: little, checksum: {algo: crc16/kermit}}   # Seeed E5
```

### Word-Ordered 32-Bit Types

Some devices send a 32-bit value as two 16-bit units in an order neither `endian`
setting describes. The type names the order, and a field's `endian:` does not change it
(PS-271, PS-272, PS-362, PS-363):

| Type | Units | Bytes within a unit | Interpreted as |
|------|-------|---------------------|----------------|
| `u32le16` | least significant first | big-endian | unsigned |
| `s32le16` | least significant first | big-endian | two's complement |
| `f32le16` | least significant first | big-endian | IEEE 754 binary32 |
| `u32be16le` | most significant first | little-endian | unsigned |
| `s32be16le` | most significant first | little-endian | two's complement |
| `f32be16le` | most significant first | little-endian | IEEE 754 binary32 |

```yaml
- name: counter
  type: u32le16      # 5678 1234 -> 0x12345678
```

### Floating Point Types

| Type | Bytes | Description |
|------|-------|-------------|
| `f16`, `f32`, `f64` | 2,4,8 | IEEE 754 float |
| `bf16` | 2 | The upper 16 bits of an IEEE 754 binary32 (1 sign, 8 exponent, 7 fraction bits), read in the field's effective byte order (PS-558) |

A `bf16` word is the binary32 whose low 16 bits are zero; an infinity or NaN is absent
(PS-282). An encoder rounds the binary64 value once to the nearest `bf16`, a tie to the even
fraction, never through a binary32, and rejects a finite value past 3.3895313892515355e38 as
PS-168 requires (PS-559). Netvox r718e sends it little-endian:

```yaml
- name: acceleration_x
  type: bf16
  endian: little
```

A negative zero is zero for every float type (`f16`, `bf16`, `f32`, `f64`, `f32le16`,
`f32be16le` and the minifloats, PS-566): the sign-bit word decodes to 0, and an encoder writes
the positive-zero word for -0.0, for a decoded -0 and for a -0 that arithmetic such as
`mult: -1` over 0 produces. A vector carrying a sign-bit zero word therefore does not
re-encode to itself.

### Compact Floats (MCCI)

Unsigned and signed minifloats used by MCCI sensors (PS-417 to PS-421). They are not
IEEE half precision:

| Type | Bytes | Layout | Value |
|------|-------|--------|-------|
| `uflt16` | 2 | exponent 4 bits, fraction 12 | `f / 4096 x 2^(e - 15)`, range [0, 1) |
| `sflt16` | 2 | sign, exponent 4, fraction 11 | `+-f / 2048 x 2^(e - 15)`; `-0` reports 0 |
| `sflt24` | 3 | sign, exponent 7, fraction 16 | `+-(1 + f / 65536) x 2^(e - 63)`; exponent 127 means no value, and the field is absent |

An encoder picks the smallest exponent whose fraction fits, ties to even; a value outside
the range is an error (PS-420).

### Decimal Types

| Type | Description |
|------|-------------|
| `udec` | Unsigned nibble-decimal (BCD-like) |
| `sdec` | Signed nibble-decimal |

### String/Byte Types

| Type | Description |
|------|-------------|
| `ascii` | Text of `length:` bytes; trailing 0x00 padding dropped, a byte above 0x7F reported as its ISO 8859-1 character with a warning (PS-534, PS-538). Encoding pads with 0x00 and rejects a value too long or above U+00FF (PS-537) |
| `hex` | Lowercase hex string (requires `length:`) |
| `bytes` | Raw bytes, reported as declared by `format:` (requires `length:`, PS-456) |
| `base64` | Base64 encoded output (requires `length:`) |

An `ascii` field that declares `numeral: decimal` or `numeral: hex` reports its digit text
as the integer it spells (PS-560), so modifiers, lookups and references apply to it as to
any integer. The text, after the trailing 0x00 padding is removed, must be wholly a numeral:
`decimal` is an optional `+` or `-` and one or more of `0`-`9`, `hex` is one or more hex
digits with no sign and no `0x`. Anything else (empty text, whitespace, a digit outside the
base) is an error naming the field, never a prefix read as JavaScript's `parseInt` would.
An encoder writes the integer in digits of the base, uppercase hex, `0`-filled to `length`
after any sign, and rejects a negative `hex` value or one that does not fit (PS-561, PS-168).
`numeral` on any other type, or with another value, is rejected at load.

```yaml
- {name: major, type: ascii, length: 4, numeral: hex}       # "001A" -> 26
- {name: rssi,  type: ascii, length: 4, numeral: decimal}   # "-065" -> -65
```

An `ascii` field may declare `nul` (PS-534): `trailing` (the default) removes trailing 0x00
bytes, `stop` ends the value before the first 0x00 and leaves the rest of the field unreported,
`drop` removes every 0x00. Whitespace is never trimmed. Instead of `length`, an `ascii` field may
declare `terminator`, a byte 0 to 255 (PS-593): the text runs to the first such byte, which is
consumed and not reported, and a payload that ends first is an error naming the field. Declaring
both `length` and `terminator`, or neither, is rejected at load. `nul` and `terminator` apply before
`numeral` (PS-594). An encoder writes `nul: stop` and `nul: drop` values padded to `length` (a value
holding 0x00 under `stop` is rejected; `drop` does not round-trip a dropped 0x00), and a terminated
value followed by its terminator, with no padding and no fill for a `numeral`; a value holding the
terminator is rejected (PS-595).

A field with `terminator` may also declare `consume_terminator: false`, which stops before the
terminator and leaves it for the next field to read, and an encoder then does not write it; and
`end_terminates: true`, with which the end of the data (the payload less any `reserve`, or a tlv
entry's declared length) ends the text too, an empty text being `""` (PS-619). Without
`end_terminates`, reaching the end is still PS-593's error. Unless `consume_terminator: false`,
an encoder writes the terminator even under `end_terminates`, so a frame that ended without one
re-encodes with it (PS-620). Either key without `terminator`, or with a value other than true or
false, is rejected at load.

```yaml
- {name: model, type: ascii, length: 8, nul: stop}   # 45 4D 33 31 30 00 58 59 -> "EM310"
- {name: version, type: ascii, terminator: 0x00}     # 31 2E 32 00 -> "1.2"
# Each element: a channel byte, then a name up to the next 0xFF channel byte or the end.
# FF 45 4D 34 33 30 31 FF 58 -> [{channel: 255, model: "EM4301"}, {channel: 255, model: "X"}]
- {name: model, type: ascii, terminator: 0xFF, consume_terminator: false, end_terminates: true}
```

Each byte of an `ascii` value is the ISO 8859-1 character of the same number, with a warning
for one above 0x7F (PS-538, PS-073). A device that writes UTF-8 text declares `charset: utf-8`
(PS-624): `length`, `nul` and `terminator` apply to the bytes first, and the bytes they leave
are decoded as UTF-8, with no warning. Bytes that are not well-formed UTF-8 are an error naming
the field and the position of the first bad byte within the text. An encoder writes the value
as UTF-8, and `length` counts bytes, not characters: a value whose UTF-8 is longer is rejected
(PS-625). `charset` with another value, or on another type, is rejected at load.

```yaml
- {name: condition, type: ascii, length: 8, nul: stop, charset: utf-8}   # C3 85 6E 67 00 ... -> "Ång"
```

A `bytes` field reports a **lowercase** hex string by default (PS-281). Its `format:` is
one of `hex`, `hex:upper`, `base64` or `array` (PS-079), and a `separator:` goes between
the bytes of either hex format (PS-391). `hex:upper` is a format, not a type: `type:
hex:upper` is rejected (CR-2026-037).

```yaml
- name: device_eui
  type: bytes
  length: 8
  format: hex:upper     # AB CD ... -> "ABCD..."
  separator: ":"        # -> "AB:CD:..."
```

The type vocabulary is closed and case-sensitive (CR-2026-037): the aliases are exactly
`uint8`-`uint64`, `int8`-`int64` and `i8`-`i64`, and anything else - `float`, `double`,
`UDec`, `U8`, `version_string` - is rejected when the schema is loaded, naming the field.
A field with no type and no construct is rejected too (PS-334).

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
  type — `bytes`, `hex`, `ascii`, `base64`. On a fixed-width type it is a validation error.

#### A length taken from an earlier field

`length` may name a field decoded before it, with or without `$` (PS-464). A length octet
followed by that many bytes is written directly:

```yaml
- name: n
  type: u8
- name: frame
  type: bytes
  length: $n            # or `length: n`
```

A name that no earlier field binds is an error naming it (PS-465).

#### Optional trailing fields

A field declaring `optional: true` is decoded where its bytes remain and is absent where
none do (PS-402). Devices that append fields in later firmware are described by one schema:

```yaml
- name: reset_cause
  type: u8
- name: firmware
  type: u16
  optional: true        # absent from a 1-byte frame
- name: ble_firmware
  type: u16
  optional: true        # absent unless 5 bytes arrive
```

- Some bytes but fewer than the field takes is an error; an optional field is never partly
  read (PS-403). A `type: object` is present or absent whole, sized as its fields sum.
- Every field after an optional one must be optional too (PS-404), so an absent field never
  leaves a later one reading the wrong bytes.
- An encoder writes an optional field only where the input has it, and rejects an input
  that supplies one after omitting an earlier one (PS-405).
- When encoding it contributes no fixed count; the value supplies its own length.

#### Reporting a span again (`consume: 0`)

A `bytes`, `hex` or `ascii` field declaring `consume: 0` reads and reports its span and
leaves the read position where it was, so the fields after it read the same bytes again
(PS-588). It reports the whole frame, or a command's bytes, beside the values decoded from
them. Its `length` may be `remaining`, and it does not count toward the one `remaining`
field a level may hold (PS-015).

```yaml
- name: raw              # the whole frame, reported first
  type: bytes
  length: remaining
  consume: 0
- {name: temperature, type: s16, div: 10}
- {name: humidity, type: u8}
# 00D7 5A -> {"raw": "00d75a", "temperature": 21.5, "humidity": 90}
```

- An encoder writes nothing for it and does not require it. A value given for it must
  equal what the field decodes from the bytes the other fields write over its span (its
  `format` and `separator`, or the trailing 0x00 removal, applied first), or the input is
  rejected naming the field (PS-589).
- On any other type than a bit range, a `bool`, a byte string or a whole number (below),
  `consume: 0` is NOT RECOMMENDED (PS-453).

#### Reading a whole number in place (`consume: 0`)

A whole numeric field - an integer `uN`/`sN`, a float `f16`/`bf16`/`f32`/`f64`, a minifloat,
`udec`/`sdec` or a word-ordered variant - declaring `consume: 0` reads its value from the bytes
at the read position as it otherwise would, and leaves the position where it was (PS-603). It
is reported, referenced and matched on like any field, as its full bit range is. A frame's kind
peeked before the cases that read it again:

```yaml
- {name: _kind, type: u8, consume: 0}
- match:
    field: $_kind
    cases:
      1: [{name: kind, type: u8}, {name: temperature, type: s16, div: 10}]
      2: [{name: kind, type: u8}, {name: battery, type: u8}]
# 01 00D7 -> {"kind": 1, "temperature": 21.5}
```

- An encoder writes nothing for it; the fields that consume its bytes write them. Where the
  input omits it, the bytes fix it, and it is not required. A value given for it must equal
  what those bytes decode to, through its own type, arithmetic and lookup, or the input is
  rejected naming the field (PS-604).

### Special Types

| Type | Description |
|------|-------------|
| `bool` | Boolean (0=false, nonzero=true) |
| `number` | Computed field, or a constant with `value:` (no wire bytes) |
| `string` | Constant with `value:` (no wire bytes) only; text read from the payload is `ascii` (PS-361) |
| `boolean` | Constant `true` or `false` with `value:` (no wire bytes) only; `value` is required, and `yes`, `on` or `"true"` is refused (PS-519, PS-520). Not a spelling of `bool`, which reads a bit |
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

A constant boolean that the payload does not carry - a flag the device has no bit
for - is a `boolean` literal, which reads no bytes and writes none (PS-357, PS-519):

```yaml
- name: hardware_error
  type: boolean
  value: false      # true or false; required
```

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
assembles its base value from N/8 bytes in the field's effective byte order - the schema's
`endian`, or the field's own (PS-059) - and consumes nothing unless `consume: N` is given,
so the last range over a byte needs `consume: 1`.

A signed base, `sN[start:end]`, sign-extends from the width of the range, not of the base
(PS-352, PS-353):

```yaml
- name: offset
  type: s8[0:3]      # 0x0F -> -1, not 15
  consume: 1
```

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
`match` ...); set it on each member. Byte order is never part of a type name: the `le_`
and `be_` prefixes (`le_u16`) are withdrawn and every implementation rejects them
(PS-053a, CR-2026-039).

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

A group assembles its `size` bytes into one value, and its members' bit positions refer to
that value (PS-364). The group may declare `endian`; its members may not:

```yaml
- byte_group:
    size: 2
    endian: little     # 34 12 -> 0x1234; bits 12-15 are the 1
    fields:
      - name: kind
        type: u16[12:15]
      - name: value
        type: u16[0:11]
```

Shorthand (size inferred from field types):

```yaml
- byte_group:
    - name: value_a
      type: u8[0:3]
    - name: value_b
      type: u8[4:7]
```

Without `size`, a group covers its widest member's base type, whichever bytes that
member's bits fall in: a `u16[0:3]` member makes the group two bytes, a `u32[...]` member
four (PS-070, CR-2026-131). `size` may widen the group but not narrow it: a `size: 5`
beside a `u64[...]` member is rejected when the schema is loaded, with `byte_group declares
size 5, narrower than its widest member's 8 bytes (PS-070)`. Every implementation used to
read a sizeless group as one byte.

A member is a bit range, a whole integer (`u16` is `u16[0:15]` of the group's value) or a
`bool` with `bit` (one bit of a `u8`, so `bit` is 0 to 7). Every member reads from the
group's start, none moves the position, and all of them take part in the width and
overlap checks; any other member is rejected.

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

A default may name the value it could not map by carrying `${value}` (PS-406):

```yaml
- name: sensor
  type: u8
  lookup:
    0: Battery Voltage
    1: AIN1
    default: "AnalogSensor${value}"     # 7 -> "AnalogSensor7"
```

The value is the one after arithmetic (PS-107), written in decimal with no fraction where
it is integral - as JavaScript's `String()` writes it, so the interpreters and the generated
codec agree. Such a lookup always reports a string, so its labels must be strings
(PS-407). `${value}` means this only in a mapping's `default` (PS-408). An encoder given
`AnalogSensor7` recovers the 7 (PS-409); a plain `default` label still cannot be encoded,
because it stands for every unmapped value at once.

Values may be numbers as well as strings (PS-106).

**A `default` label cannot be encoded back.** It stands for every value the table does not
list, so there is no original to recover; encoders report the field rather than writing a
plausible byte. The same applies to an `enum` `default` (PS-068).

### Set-Bit Labels (`bit_labels`)

A status, alarm or capability word is often reported as the names of its set bits. A
`lookup` names one value; `bit_labels` names each bit (0 the least significant), and the field
reports the labels of the bits that are set (PS-616):

```yaml
- name: sensor_type                  # 0x0F -> "T/H,CO2,PMx,Gas1"
  type: u8
  bit_labels: {0: T/H, 1: CO2, 2: PMx, 3: Gas1, 4: Gas2, 5: Light, 6: Noise, 7: Pressure}
  join: ","

- name: options                      # 0x00 -> ["None"]; 0x05 -> ["sensor", "led"]
  type: u8
  bit_labels: {0: sensor, 2: led, 3: buzzer}
  none: None
```

- It is declared on an unsigned integer (`u8` to `u64`) or an unsigned bit range, and not
  beside `lookup`, arithmetic, `transform`, `polynomial`, `time`, `compute`, `encoding` or
  `value`. `join`, `none` and `order` belong to it.
- The labels are a list, or one string separated by `join` where it is declared. `order` is
  `ascending` (the default) or `descending`, by bit number. Where no listed bit is set the
  field reports its `none` label (`["None"]`, or the text), else `[]` or `""`.
- A `$` reference to the field binds its number, as for a lookup (PS-523).
- A set bit with no label is not reported, and the decoder warns, naming the field and the
  bit (PS-617). A bit outside the width, two bits with one label, a `none` that is a bit's
  label, or a label containing a non-empty `join` is rejected at load.
- An empty `join` concatenates the labels; an encoder splits such text by the longest label
  first, so where one label is a prefix of another the schema decodes but is refused for
  encoding (PS-617).
- An encoder takes a list, or the joined text where `join` is declared, and writes the bits of
  those labels and no others; an unknown label is rejected, and `[]`, `""` or the `none` label
  writes 0. Order and repetition do not matter (PS-618).
- `_meta` reports `array`, or `string` where `join` is declared (PS-570).

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
    op: div          # add, sub, mul, div, mod, idiv, pow
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
| `pow` | Power; `b` may be a field (PS-584) | `a ** b` |

`pow` computes `a` raised to `b` in binary64, with `b` a literal or a field
(`{op: pow, a: 10, b: $scale}`); a literal base of 2.718281828459045 gives `e^x`. A result
that is not a finite real number - a negative `a` with a non-integer `b`, `0` with a negative
`b`, an overflow - leaves the field absent, as a zero divisor does (PS-278, PS-584), and the
rest of the payload still decodes.

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
        gt: 0          # gt, gte, lt, lte, eq, ne, in, not_in
    else: 0            # Fallback if condition fails
```

`in` and `not_in` take a list of numbers and hold where the value equals one of them, or
none of them (PS-543): `{field: $mode, in: [2, 4]}`. An empty `in` never holds, and an
empty `not_in` always does; a boolean is not a number. Every condition has exactly one
operator (PS-126), so a range of values is two conditions, not one, and a condition with
none or two is rejected. The same conditions serve
`present_if`.

### Formula (Withdrawn)

`formula` and `encode_formula` are withdrawn (CR-2026-159, PS-448): a schema declaring one is
rejected at load, in every implementation, the validator and the TS013 generator:
"`formula` is withdrawn; use `compute`, `polynomial`, `transform` or `guard` (CR-2026-159,
PS-448)". No registry, device or example schema used either. The same result portably:

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
  - log10: true       # Base-10 logarithm; x <= 0 leaves the field absent (PS-117)
  - log: true         # Natural logarithm; likewise
  - floor: 0          # Lower bound: max(x, 0)
  - ceiling: 100      # Upper bound: min(x, 100)
  - {op: round, decimals: 2}              # Half-to-even: `ties: even`, the default
  - {op: round, decimals: 2, ties: away}  # A tie rounds away from zero (toFixed)
```

All five implementations support every stage. `clamp` is withdrawn (CR-2026-159, PS-115):
write a `floor` stage and a `ceiling` stage. Any other stage - `{round: n}`, `{op:
floor}`, `{sub: n}` - is rejected, never skipped (PS-390). A zero divisor, like the log of
x <= 0, leaves the field absent rather than reporting NaN (PS-100, PS-282).

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

The older `type: match` spelling, with `on:` for the discriminator and a `default` case, is
withdrawn: a schema using it is rejected at load with "`type: match` with `on:` is
withdrawn; write `match: {field: $name, cases: ...}` (Clause 4)".

### Match on the bytes remaining

Some devices tell their layouts apart by length alone. `remaining: true` makes the
discriminator the number of bytes from here to the end, less any enclosing repeat's
`reserve` (PS-414); it reads nothing:

```yaml
- match:
    remaining: true
    cases:
      2:                  # two bytes left
        - name: short_reading
          type: u16
      4..255:             # four or more - a range is how "at least" is written
        - name: long_reading
          type: u32
```

A match declares exactly one of `field`, `length` and `remaining` (PS-416).

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

`encoding` applies only to an unsigned integer type, reading it unsigned and then decoding
the code (PS-422 to PS-425); Python, Go, Java, C# and the TS013 codec all support it. An
encoder rejects a value the code cannot represent (PS-463). C does not have it.

`match_value` is withdrawn and rejected (PS-426): write a signed type, a signed bit range,
`encoding`, `match` or `guard` instead.

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

A part's third element is its format (PS-430): `decimal` (the default), `sdecimal`, `hex`
(lower case) or `hex:upper`, optionally followed by `:N`, a minimum width from 1 to 32.
With `:N` a part is zero-filled on the left to `N` digits; a part already that wide or wider
is written in full, never cut. `sdecimal` reads the part's bits as two's complement of its
width and writes a `-` before any padding, so -5 as `sdecimal:2` is `"-05"` (PS-582). Quote
a format carrying `:N`.

```yaml
- name: mac_tail                   # 0x0A3F -> "0a:3f"
  type: bitfield_string
  length: 2
  delimiter: ":"
  parts:
    - [8, 8, "hex:2"]
    - [0, 8, "hex:2"]
- name: ai_offsets                 # 0xFB03 -> "-5,3"
  type: bitfield_string
  length: 2
  delimiter: ","
  parts:
    - [8, 8, sdecimal]
    - [0, 8, sdecimal]
```

Encoding parses each segment by its format: leading zeros are accepted, hex in either case,
and a `-` only on `sdecimal` (PS-096, PS-431). A segment the part's width cannot hold - 0 to
2^w - 1, or -2^(w-1) to 2^(w-1) - 1 for `sdecimal` - is rejected naming the field and the
segment, never masked (PS-583). A value without the declared `prefix`, or with a segment
count other than the part count, is rejected; a missing part is never filled with 0 and an
extra segment never ignored (PS-605).

With `delimiter: ""` the parts are joined with nothing between them, and an encoder splits
the value by each part's `:N` width (PS-606). That needs every part to declare a width that
holds its largest value (`hex:2` for 8 bits, `decimal:3`) and no part to be `sdecimal`; a
field that does not meet this still decodes, but is refused for encoding, and a value whose
length differs from the sum of the widths is rejected.

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

# A span of bytes taken from an earlier field; the elements must divide it exactly (PS-088)
- name: data_length
  type: u8
- name: samples
  type: repeat
  byte_length: $data_length
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

`min` and `max` bound a repeat's element count: at most `max` elements are read (default
1000), and fewer than `min` fails the decode. On any other field they are withdrawn
(CR-2026-159, PS-395): "`max` on field 'v' is withdrawn: it bounds a repeat's element count
only; declare a value range with `valid_range` (CR-2026-159, PS-395)". A `valid_range` is a
quality flag on decode; an encoder does not reject a value for being outside it.

Each element is decoded like a field list of its own: it may hold computed fields,
internal (`_`) fields and constructs. Its names exist only inside the element (PS-368):
a reference to one from after the array is an error, as is a reference inside an
element to one of its fields not yet decoded.

### Index, count and dropped elements

```yaml
- name: slots
  type: repeat
  count: 4
  index: i              # 0, 1, 2, 3 while each element decodes (PS-366)
  count_as: occupied    # after the array: elements reported (PS-367)
  present_if:           # one guard condition; a false element is dropped (PS-386)
    field: $id
    ne: 0
  fields:
    - name: id
      type: u8
    - name: position
      type: number
      ref: $i
- name: occupied_slots
  type: number
  ref: $occupied
```

`index` and `count_as` are never reported unless a field refers to them (PS-370). A
dropped element still advances `index` and consumes its bytes, and `count_as` counts the
elements reported, not read (CR-2026-080). A repeat with `present_if` over elements that
read bytes cannot be encoded - the dropped elements are not in the output (PS-387).

`index_base`, an integer (default 0), is the number `index` starts at: vendors that count
from 1 declare `index_base: 1`, so `name_from: "Device${i}ID"` gives `Device1ID` first.
`count_as` is unaffected, and anything but an integer is rejected (PS-530).

### Values rather than records (`element`)

```yaml
- name: increments
  type: repeat
  until: end
  element: {type: u16, div: 10}     # in place of fields (PS-528)
# 0078 0096 005A  ->  {"increments": [12, 15, 9]}
```

A repeat declares exactly one of `fields` and `element`. Each element is decoded as that
one field and the array holds its values. An omitted value - a sentinel, a lookup miss, a
value that cannot be computed - is `null` in its position, so later values keep their
index; this is the one place a decoded value is `null`. The element's `name` (default
`element`) is how `present_if` refers to the current value and is never reported, so a
leading `_` hides nothing: the value is still reported, and `$_x` still reads it (PS-545).
The element is one data field: it declares `type` and is not a `match`, `byte_group`,
`flagged`, `tlv` or `records` entry, which is rejected at load; a `type: object` element
reports one object per position. An encoder takes the array of values back (PS-529) and
writes the element's first `sentinel` for a `null` position, as the raw integer the field
reads, without inverting any modifier (a `div: 10` element with `sentinel: [0xFFFF]`
writes `FFFF`); with no sentinel it reports an error naming the repeat and the position
(PS-544).

```yaml
- {name: probes, type: repeat, count: 3, element: {type: u16, sentinel: [0xFFFF]}}
# 0001 FFFF 0003  ->  {"probes": [1, null, 3]}  ->  encodes back to 0001 FFFF 0003
```

### Values packed in bits (`packed_bits`, `bit_offset`)

```yaml
- {name: _count, type: "u8[2:7]"}
- {name: _repetition, type: "u24[4:9]", consume: 2}
- name: co2_history
  type: repeat
  count: $_count
  packed_bits: 10                   # each element is the next 10 bits (PS-621)
  bit_offset: 4                     # the first starts 4 bits into the byte
  element: {type: u16, sentinel: [0x3FF], mult: 5}
# 0000 0C 00 5143FF0040  ->  {"co2_history": [400, null, 5]}
```

A repeat with `element` may pack its elements end to end: one bit stream, most significant
bit first, the first element starting `bit_offset` bits (0 to 7, default 0) into the byte at
the read position. Each element is the next `packed_bits` bits (1 to 32), read unsigned or,
for a signed element type, sign-extended, and then decoded as the element type's integer, so
its `sentinel`, modifiers and `lookup` apply. The element is an integer type at least
`packed_bits` wide, and the repeat declares `count` or `byte_length`; with `byte_length` the
elements are as many as fit whole in `byte_length x 8 - bit_offset` bits. Afterwards the read
position moves to the next byte, or to the end of `byte_length` where that is further
(PS-622). The bits before `bit_offset` belong to the fields before the repeat, read as bit
ranges that consume nothing. Either key anywhere else is rejected at load. An encoder writes
each element through its modifiers inverted, rejects one that does not fit in `packed_bits`,
ORs the first byte into the bits the fields before it left, and pads with 0 bits (PS-623).

### Groups reported as one list (`flatten`)

```yaml
- name: counters
  type: repeat
  until: end
  flatten: _members                 # report every group's members as one list (PS-633)
  fields:
    - {name: _count, type: u8}
    - {name: _time, type: u16}
    - name: _members
      type: repeat
      count: $_count
      fields:
        - {name: id, type: u8}
        - {name: time, type: number, compute: {op: add, a: $_time, b: 0}}
# 02 0064 01 02  01 00C8 05  ->  {"counters": [{"id": 1, "time": 100},
#                                              {"id": 2, "time": 100}, {"id": 5, "time": 200}]}
```

A repeat with `fields` may name, in `flatten`, one of its element's fields that is a
`repeat`. It then reports, in place of its elements, the concatenation of that field's
arrays in payload order. The element's other fields must be internal; a value they hold,
such as a group's shared time, reaches the members through a reference. A `flatten` naming
no `repeat` among the element's fields, or an element reporting another field, is rejected
at load (PS-633). The flat list does not say which group each member came from, so an
encoder refuses the schema, which still decodes (PS-634).

### Several records from one element (`records`)

```yaml
- name: messages
  type: repeat
  until: end
  present_if: {field: $_dataId, not_in: [1, 2, 3, 4, 5, 6]}
  fields:
    - {name: _dataId, type: u8}
    - match:
        field: $_dataId
        cases:
          7:
            - records:
                - [{name: type, type: string, value: upload_battery},
                   {name: battery, type: u16}]
                - [{name: type, type: string, value: upload_interval},
                   {name: interval, type: u16, mult: 60}]
# 07 0064 003C  ->  {"messages": [{"type": "upload_battery", "battery": 100},
#                                  {"type": "upload_interval", "interval": 3600}]}
```

A `records` entry, in a repeat's element or a `match` case within it, is a non-empty list
of field lists. Each is decoded in order from the read position and yields one record, and
the repeat reports the records as consecutive elements in place of the element (PS-541).
The element's other reported fields are copied into each record, before the record's own;
`_` fields are not reported, so they are not copied. A record field may not share a name
with a field the element reports outside `records` (CR-2026-124). `index` counts source elements, and
`count_as` counts the records reported. `records` anywhere else - the top level, an
object's members, a tlv case, a flagged group, a byte_group, a trailer, another record -
and under `element` is rejected at load. An encoder refuses
a repeat whose elements can reach one, since a record does not say which element it came
from (PS-542). `_meta`'s `elements` describes every record's fields, keyed by name.

### Running values (`carry`)

```yaml
- name: hours
  type: repeat
  count: 24
  fields:
    - name: rain
      type: u8
    - name: so_far
      type: number
      carry: 0                        # or a $ref decoded before the repeat
      compute: {op: add, a: $so_far, b: $rain}
```

Inside its own computation, a carried field's name is its value in the previous
element - the `carry` value in the first (PS-378, PS-379). It restarts each time the
repeat begins, including inside each element of an enclosing repeat (PS-380).

### Bytes reserved at the end

```yaml
- name: registers
  type: repeat
  until: end
  reserve: 1            # the last byte is not an element (PS-350)
  index: i
  trailer:              # decoded first, from the reserved bytes (PS-383)
    - name: first_slot
      type: u8
  fields:
    - name: value
      type: u16
    - name: slot
      type: number
      compute: {op: add, a: $first_slot, b: $i}
```

`reserve` keeps `until: end` off the last bytes, and the fields declared after the
repeat decode them. A `trailer` reads them before the first element instead, so the
elements can use its values; it is reported beside the array, and its fields' sizes
must add up to `reserve` (PS-384). The encoder writes the elements, then the trailer.
Fewer bytes than `reserve` at the start of the repeat is an error (PS-351).

### What each element measures

```yaml
- name: probes
  type: repeat
  count: 2
  index: i
  identity: i                         # or a $ref to a field of the element (PS-372)
  fields:
    - name: unit_code
      type: u8
      lookup: {1: Cel, 2: "%RH"}
    - name: reading
      type: s16
      div: 10
      unit: $unit_code                # per element (PS-373)
      ipso: {object: 3303, instance: $i, resource: 5700}
      senml: {name: "probe_${i}"}
```

These are annotations: they change no decoded value (PS-376). A field they name must
carry a `lookup` or be a literal, so its values are known from the schema, and the index
needs a literal `count` or a `max` (PS-374). An `ipso.instance` must be an integer from 0
to 65534, or a reference every value of which is one: an index whose range from
`index_base` fits, a literal, or a lookup whose labels are all such integers with no
`${value}` default; anything else is rejected at load (PS-548).

One array can identify another, position by position. Where a device sends a list of
codes and then one reading per code, `$name[i]` names an earlier sibling repeat declaring
`element`, indexed by this repeat's own `index` (PS-546):

```yaml
- {name: _n, type: u8}
- {name: _regs, type: integer, compute: {op: sub, a: $_n, b: 2}}   # a computed count
- name: codes
  type: repeat
  count: $_regs
  element: {type: u8, lookup: {1: "1.8.1", 2: "2.8.1", default: "${value}"}}
- name: values
  type: repeat
  count: $_regs
  index: i
  identity: $codes[i]
  element: {type: u32}
# 04 01 02 0000007B 000001C8  ->  {"codes": ["1.8.1", "2.8.1"], "values": [123, 456]}
```

The decoded output is two arrays; `_meta` lists the identity as `{"repeat": "codes",
"values": [...]}` (PS-547): the identity at each position is taken from the sibling's
value at the same position. The sibling's element must carry a `lookup` or be a literal, and
two arrays of different lengths are a decode error naming both. A per-element `unit` or
`ipso.instance` may name a sibling the same way.

### A history: `sampled`

A device that buffers readings sends them as a history: one frame, the last N samples of a
measurement, taken at a fixed interval. A repeat says so with `sampled` (PS-567), in either
its `element` or its `fields` form:

```yaml
- name: pm25
  type: repeat
  until: end
  element: {type: u16, unit: ug/m3}
  sampled: {interval: {configured: history_period}, order: newest_first}
```

- `interval` (required) is a number of seconds above 0, a `$field` decoded before the repeat
  (its value after modifiers is the seconds), or `{configured: name}`, a device setting the
  frame does not carry and the integration supplies.
- `order` is `newest_first` (the default) or `oldest_first`.
- `at` is the time of the newest sample: `rx_time` (the default), the name of a
  `metadata.timestamps` entry whose mode is `unix_epoch` or `iso8601`, or the name of a field
  decoded before the repeat that declares `time` with format `unix` or `iso8601` (PS-610),
  whose instant is the anchor (CR-2026-192).

Element k of N was sampled at `at` - k x `interval` for `newest_first`, and at `at` -
(N - 1 - k) x `interval` for `oldest_first`. `sampled` is an annotation: decoding and
encoding ignore it, and no time is added to the output. `_meta.fields.<repeat>.sampled`
carries it with the defaults written (PS-568): `{"interval": {"configured": "history_period"},
"order": "newest_first", "at": "rx_time"}`, a reference as `{"field": "period"}`. A schema is
rejected at load where `sampled` is on a field that is not a repeat, the interval is absent,
not above 0, a bare string without `$` or a mapping other than `configured`, the order is
neither value, or `at` names neither a `unix_epoch`/`iso8601` entry nor a `unix`/`iso8601`
time field decoded before the repeat (PS-569); a `calendar` or `pattern` time field is no anchor. Python, Go, Java and
C# implement it; the C binary form has no repeat.

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

A name beginning with `_` is internal: it is decoded and can be referenced like any
field, and it is never reported (PS-432). Use it for protocol bytes, masks, reserved bits
and the steps of a computation.

- `_meta`, `_quality` and `_warnings` are reserved for interpreter output and cannot name a
  field (PS-433).
- An encoder never needs an internal field in its input. One that reads bytes writes its
  `value`; without one it takes the input's value under its name, and otherwise reports an
  error naming it (PS-434). So a reserved field states what it carries:

```yaml
- name: _reserved
  type: u8[1:1]
  value: 0              # what an encoder writes
```

- A `metadata` timestamp may read an internal field (PS-435).

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

## Template Strings (`template`)

A `type: string` field declares either `value`, a literal, or `template`, text joined from
values decoded earlier (PS-361, PS-590). It reads and writes no bytes, and an encoder ignores
a value given for it.

```yaml
- {name: _major, type: u8}
- {name: _minor, type: u8}
- {name: _build, type: u16}
- name: firmware_version            # 01 02 0003 -> "1.02.003"
  type: string
  template: "${_major}.${_minor:decimal:2}.${_build:decimal:3}"
```

- `${name}` writes the value as `name_from` does (PS-532): a number as JavaScript's
  `String(n)`, a looked-up field's label, a string as it is, a boolean as `true`/`false`.
- `${name:format}` writes the number the reference binds - a looked-up field's code - in a
  bitfield_string part format: `decimal`, `sdecimal`, `hex` or `hex:upper`, with an optional
  `:N` minimum width (zero-filled, never cut; a `-` before the padding).
- `$$` writes a `$`.
- A reference with no value makes the field absent (PS-592); a format given a fraction, or
  `hex` given a negative number, makes it absent with a warning (PS-591).
- A reference to an undeclared name, an object or a repeat, a format that is not a part
  format, and `template` beside `value` or off `type: string` are rejected at load.

## Time Fields (`time`)

A numeric field - an integer, float, nibble-decimal or minifloat type, a bit range, or a
computed `number` or `integer` - may declare `time` (PS-610). Its value after the modifiers
is a count of seconds since `epoch` (an RFC 3339 UTC instant, 1970-01-01T00:00:00Z where
absent), and the field is reported as that instant, in UTC, in its `format`. A reference to
the field still binds the number.

```yaml
- name: timestamp                    # GPS seconds, as toISOString writes them
  type: u32
  add: -17
  time: {format: iso8601, epoch: "1980-01-06T00:00:00Z", fraction: 3}
- name: Timestamp                    # as toUTCString writes it
  type: u32
  time: {format: pattern, pattern: "%a, %d %b %Y %H:%M:%S GMT"}
- name: time_stamp                   # from calendar parts decoded earlier
  type: number
  time:
    format: unix
    milliseconds: true
    parts: {year: $_year, month: $_month, day: $_day, hour: $_hour}
```

- `format: unix` reports seconds since 1970, or since `since:`; `milliseconds: true` reports
  an integer count of milliseconds (PS-611).
- `format: iso8601` reports `YYYY-MM-DDTHH:MM:SSZ`; `fraction: N` (1 to 9) writes exactly N
  fractional digits, and without it digits appear only where the value has them (PS-611).
- `format: calendar` reports `{year, month, day, hour, minute, second}`, with
  `month_labels:` (twelve names) and `keys:` (part to member name) as PS-411 and PS-412
  define them (PS-612).
- `format: pattern` writes `pattern:` with `%Y`, `%m`, `%d`, `%H`, `%M`, `%S`, `%L`
  (milliseconds), `%b` (`Jan`), `%a` (`Mon`) and `%%` replaced; names are English (PS-613).
- `parts:` on a `type: number` field maps `year`, `month`, `day` and optionally `hour`,
  `minute`, `second` to fields decoded earlier; a part that is not whole, or a date that
  does not exist (an all-zero clock), omits the field with a warning naming it, and
  decoding continues; a rolled-over date is never reported. `epoch` does not apply
  (PS-614).
- An instant outside the years 1 to 9999 likewise omits the field with a warning (PS-610).
- Milliseconds, `fraction` and `%L` truncate, as JavaScript's `Date` does.
- An encoder parses the value back in its format (PS-615). A pattern lacking `%Y`, `%m` or
  `%b`, `%d`, `%H`, `%M` or `%S`, and `parts` naming a field that reads no bytes, decode
  but are refused for encoding; a `%a` that is not the date's weekday is rejected.
- `time` on another type or beside `lookup`, an unknown format, or a key of another format
  is rejected at load.
- `valid_range` on a time field compares the seconds after the modifiers (PS-615). No
  encoder here range-checks on write, so an instant outside the range is written as any
  other value is.
- `_meta.fields.<name>.time` carries `format`, and `epoch`, `since`, `milliseconds`,
  `fraction` and `pattern` where declared; never `parts`, `month_labels` or `keys` (PS-629).

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

### A trailer after the entries

```yaml
- tlv:
    tag_size: 1
    reserve: 9            # the last nine bytes are not entries (PS-471)
    cases: { ... }
- name: keepalive
  type: bytes
  length: 9
```

The loop stops where the reserved bytes begin, and the fields after it read them. Fewer
bytes than `reserve` at the start is an error.

### A name reported more than once (`on_repeat`)

With `merge: true`, a name two entries report - whether they share a tag or not - is
combined as its `on_repeat` says (PS-157, PS-525): `collect` (the default) gathers the
values into an array in payload order, `last` keeps the value reported last, `merge`
combines `type: object` values member by member, and `concat` always reports one flat
array (PS-632).

```yaml
- tlv:
    tag_size: 2
    on_repeat: merge      # default for this tlv's object fields; others keep collect
    cases:
      0x0310: [{name: loramac, type: object, fields: [{name: adr, type: u8}]}]
      0x0311: [{name: loramac, type: object, fields: [{name: dr, type: u8}]}]
# 0310 01 0311 05  ->  {"loramac": {"adr": 1, "dr": 5}}
```

A field's own `on_repeat` takes precedence over the tlv's. Only a `type: object` field may
declare `merge`, and every field reporting one name must resolve to the same mode, or the
schema is rejected (PS-526). A merged object reporting one member twice is an error,
unless that member declares `on_repeat: last`. `on_repeat` anywhere else - outside a tlv,
or on a member of an object that is not merged - is rejected (PS-525).

Merged cases may share a member only where it declares `on_repeat: last` (PS-526).

`concat` reports the name as one array however many entries report it, one included; a
value that is itself an array has its elements appended, and a name no entry reports is
absent (PS-632). An encoder writes one entry per element of the array where the case
reports a scalar or an object; where the case reports an array, the flat list cannot be
split back into entries and the schema is refused for encoding (PS-634).

```yaml
- tlv:
    tag_size: 1
    cases:
      0xFE: [{name: request_result, type: object, on_repeat: concat, fields: [{name: cmd, type: u8}]}]
# FE 01  ->  {"request_result": [{"cmd": 1}]}
```

The encoder writes each value - a scalar, each element of an array PS-157 collected, a
`last` value, a merged object's members - under the first case whose declared fields it
supplies in full, and never zero-fills or drops one (PS-527). A shared `last` member is
written in every case written for its object. A value no case takes in full, or a member
no case declares, is an error naming it.

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

A one-field tag may use the one-element keys `"[*]"` (any value) and `"[!n]"` (any value but
n). They rank below every exact key, whatever order they are written in (PS-270, PS-438):

```yaml
- tlv:
    tag_size: 1
    cases:
      1: [{name: temperature, type: s16, div: 10}]   # tag 1
      "[!0x02]": [{name: other, type: u8}]          # any tag but 1 and 2
      "[*]": [{name: raw, type: u8}]                # tag 2
```

A tlv has no `default` case; its catch-all is `"[*]"` (`"[*, *]"` for a two-field tag), and
a `default` key is rejected at load (PS-436, PS-438). One key written twice (`"[*]"` and
`"[ * ]"`) is rejected (PS-437), and so are two negated keys, which can match one tag
(PS-440).

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

A definition is an object with a `fields:` list (PS-345), and `$ref` splices those fields
into the list where it appears - any field list: a port's, an object's, a repeat's, a
case's (PS-347). `01 02 AB CD` decodes to `{version: 1, flags: 2, payload: "abcd"}`. The
top-level `header:` block is gone; use a definition for a common header.

A reference that does not resolve, a cycle, or a pointer not of the form
`#/definitions/<name>` makes the schema invalid. A reference into another file
(`lib.yaml#/definitions/x`) is resolved only by `tools/schema_preprocessor.py`, which also
applies `rename:` and `prefix:`; an interpreter given one rejects it (PS-462). The `use:`
shorthand is withdrawn (CR-2026-045): write `$ref`.

### Field templates and shared lookup tables

A definition carries exactly one of `fields` (a group, as above), `field` (one field) or
`lookup` (a table) (PS-345, CR-2026-168):

```yaml
definitions:
  alarm_codes:
    lookup: {0: none, 1: low, 2: high, 3: sensor_fault, default: "alarm${value}"}
  probe:
    field: {type: s16, div: 10, unit: Cel, valid_range: [-40, 125]}

fields:
  - {$ref: "#/definitions/probe", name: temp_1}
  - {$ref: "#/definitions/probe", name: temp_2}
  - {$ref: "#/definitions/probe", name: temp_ext, div: 100}   # div replaced
  - {name: alarm_1, type: u8, lookup: "#/definitions/alarm_codes"}
```

A `$ref` to a `field` definition may carry field keys beside it: each replaces the defined
key whole, one level deep, so a `lookup` or `transform` given there is not merged with the
definition's (PS-578). The resolved field must have a `name`, from the definition or the
reference. A `$ref` to a group carries nothing else, and a `$ref` in a field list never
names a `lookup` definition (PS-348). A field's `lookup` may be the pointer
`#/definitions/<name>` to a `lookup` definition, and decodes and encodes exactly as the
table written in its place (PS-579). A `field` definition may use a pointer, and a group may
reference `field` definitions; references resolve transitively, and a cycle is invalid
(PS-580, PS-349). The decoded output and `_meta` are those of the schema written out. An
`enum`'s `values` cannot point to a table.

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

A port declares `fields`, `direction` and a `description`. `confirmed` is withdrawn
(CR-2026-159, PS-448): whether a downlink is confirmed is chosen per transmission, not by the
schema, and a port declaring it is rejected at load.

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
    # The network receive time
    - name: timestamp
      mode: rx_time

    # The receive time minus a field, in seconds
    - name: measurement_time
      mode: subtract
      offset_field: seconds_ago

    # A base time minus an elapsed count from a field (PS-318, PS-319)
    - name: sampled_at
      mode: elapsed_to_absolute
      elapsed_field: seconds_ago        # `offset_field` is the older spelling
      time_base: rx_time                # the default

    # A device count of seconds since an epoch, as Unix seconds (a number)
    - name: device_time
      mode: unix_epoch
      field: counter
      epoch: "2013-01-01T00:00:00Z"     # optional; 1970 where absent (PS-354, PS-355)

    # The same instant as an RFC 3339 UTC string
    - name: device_time_iso
      mode: iso8601
      field: counter
      epoch: "2013-01-01T00:00:00Z"

    # The same instant as its UTC calendar parts (PS-410 to PS-413)
    - name: stamp
      mode: calendar
      field: _since_2015                 # an internal field resolves (PS-435)
      epoch: "2015-01-01T00:00:00Z"
      month_labels: [January, February, March, April, May, June,
                     July, August, September, October, November, December]
      keys: {hour: hours, minute: minutes, second: seconds}
```

`rx_time` copies `recvTime` as given, and `subtract` gives a UTC string with milliseconds:
`seconds_ago: 60` with `recvTime: "2024-01-01T00:01:00Z"` gives
`"2024-01-01T00:00:00.000Z"`.

The three epoch modes read their field as seconds since `epoch` - an RFC 3339 UTC string
with a `Z`, quoted, or YAML reads it as a date - and keep the field itself as the device's
own count (PS-356). For 2026-09-24T14:05:09Z, 433260309 seconds after 2013:

| Mode | Reports |
|------|---------|
| `unix_epoch` | `1790258709`, a number - whatever the declared epoch |
| `iso8601` | `"2026-09-24T14:05:09Z"`, with a fraction only where the value has one |
| `calendar` | `{year: 2026, month: September, day: 24, hours: 14, minutes: 5, seconds: 9}` with the labels and keys above; numbers and the six default names without them |

Before 0.5.2 `unix_epoch` reported a string, and `iso8601` took a `format:` strftime
pattern; that key is withdrawn and rejected. A device counting minutes converts with
`mult: 60`. A GPS epoch is an approximation: GPS time does not insert leap seconds.

Enrichment runs only when the decoder is given runtime input (PS-312), and only the Python
reference implements it; the others may decline `metadata` (PS-310), but must decode the
payload as though the block were absent.

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
  semantic: "air.temperature"                          # TTN normalized path (PS-470)
```

**`semantic` is a TTN normalized measurement path** (CR-2026-179, PS-470): a string from
the `measurement` definition of TTN's `lib/payload.json` (`air.temperature`,
`air.relativeHumidity`, `soil.moisture`, `position.latitude`, `battery`, ...). Anything
else that is not a string is rejected. `_meta.fields` carries it unchanged (PS-600). The
validator warns of a path TTN does not define, of a unit that is neither the path's nor one
normalizeUplink converts, and of two fields of one message on one path (PS-597). A
generated TS013 codec gains `normalizeUplink()`, which places each value at its path in
TTN's unit (PS-598, PS-599): it converts °F, mV, Pa/kPa/bar/inHg, km/h/mph/knots and a
fraction (`/`), and otherwise leaves the value out with a warning - a field with no `unit`
on a path that has one included, and every value on a path two fields declare. So declare
`unit` beside `semantic`, and do not put a battery in % on `battery`, which is in V.
TTN calls `normalizeUplink()` with no FPort (PS-609, CR-2026-185): for a schema with
`ports`, an `fPort` in its input selects the entry; otherwise the candidates are the uplink
entries reporting every key of the data, and their placement is used where they all place
the same values, else nothing is placed, with a warning naming the FPorts or saying none
fits.

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

#### No reading: `sentinel` and `out_of_range: omit`

A device that reports "no reading" with a reserved value - SenseCAP's `0x8000`, a humidity
of 200 % - should not have it decoded as a quantity. Both forms leave the field absent
(PS-427 to PS-429):

```yaml
- name: temperature
  type: s16
  div: 10
  sentinel: [-32768]     # the raw integer, before div and before any encoding
- name: humidity
  type: u8
  valid_range: [0, 100]
  out_of_range: omit     # outside the range is no reading, not a flagged one
```

An absent reading is neither reported nor bound for later references, and `_quality`
records it as `absent` or `out_of_range` wherever `_quality` is produced. Without
`out_of_range` (or with `flag`), a value outside `valid_range` is reported and flagged as
before. An encoder writes the first sentinel back for a field the input omits.
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
    semantic: "air.relativeHumidity"
    valid_range: [0, 100]

  - name: battery_voltage
    type: u16
    div: 1000
    unit: "V"
    ipso: {object: 3316, instance: 0, resource: 5700}
    senml: {name: "vbat", unit: "V"}
    semantic: "battery"

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
TYPES:        u8 u16 u24 u32 u64 | s8 s16 s24 s32 s64 | f16 bf16 f32 f64 | bool
              ascii hex bytes base64 | number integer string | skip enum
              udec sdec | bitfield_string | object
              aliases: uint8..uint64, int8..int64, i8..i64 (nothing else; case-sensitive)
BYTES FORMAT: hex (default) hex:upper base64 array | separator: (hex formats only)
              
STRUCTURES:   object | repeat | byte_group | tlv

BITFIELDS:    uN[start:end] (inclusive; consume: N to advance) | bool + bit: N

BYTE ORDER:   endian: big|little (schema level, or per field; no cascade)

TAG KEYS:     "[1, 103]" exact | "[1, !0]" excluding | "[2, *]" any

MODIFIERS:    mult div add (canonical order) | lookup | polynomial | compute | guard
              | transform

CONDITIONALS: match (value dispatch) | flagged (bitmask) | tlv (tag dispatch)

TRANSFORMS:   mult div add | sqrt abs pow log10 log | floor ceiling
              | {op: round, decimals: n, ties: even|away} - nothing else (PS-390)
              div 0 or log of x <= 0: the field is absent (PS-100, PS-117)

COMPUTE OPS:  add sub mul div mod idiv pow

GUARD OPS:    gt gte lt lte eq ne

ENCODINGS:    sign_magnitude bcd gray (on uN only)

MATCH:        exact | range ("n..m", decimal) | list ("[1, 2, 3]", quoted) | default
              exactly one of field: or length: (PS-399)

REFERENCES:   $field_name | var: name | $ref: '#/definitions/name'

SEMANTICS:    unit | ipso {object, instance, resource} | senml {name, unit}
              | semantic | valid_range | resolution | unece

DIRECTIONS:   uplink | downlink | bidirectional

METADATA:     include | timestamps (rx_time, subtract, unix_epoch, iso8601, calendar,
              elapsed_to_absolute; epoch) (Python only)
```

## See Also

- [SCHEMA-DEVELOPMENT-GUIDE.md](SCHEMA-DEVELOPMENT-GUIDE.md) - Tutorial and best practices
- [OUTPUT-FORMATS.md](OUTPUT-FORMATS.md) - Output format specifications (IPSO, SenML)
- [C-CODE-GENERATION.md](C-CODE-GENERATION.md) - Embedded firmware codec generation
- [BIDIRECTIONAL-CODEC.md](BIDIRECTIONAL-CODEC.md) - Downlink encoding details
