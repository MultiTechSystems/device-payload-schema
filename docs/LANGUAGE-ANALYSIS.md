# Schema Language Design Rationale

This document explains the design decisions behind the Payload Schema language.

## Design Goals

### 1. Declarative Over Imperative

**Decision**: Schema definitions describe *what* to decode, not *how*.

**Rationale**:
- **Security**: No code execution eliminates injection attacks (unlike `eval()`)
- **Portability**: Same schema works in Python, Go, Java, C#, C and generated JavaScript
- **Tooling**: Static analysis, validation, code generation all possible
- **Simplicity**: Device manufacturers don't need to write code

**Trade-off**: Cannot express arbitrary algorithms. Mitigated by:
- Comprehensive arithmetic modifiers (`add`, `mult`, `div`)
- Transform pipeline (`sqrt`, `pow`, `log`, `log10`, `abs`, `round`)
- Polynomial evaluation for calibration curves
- Lookup tables for enumeration

### 2. Wire-Format Agnostic Output

**Decision**: Schema defines binary→structured data mapping; output format is interpreter choice.

**Rationale**:
- Same schema produces JSON, SenML, LwM2M, or native structs
- Semantic annotations (`ipso`, `senml`, `semantic`) enable format translation
- Decouples parsing from presentation

### 3. Explicit Over Magic

**Decision**: All transformations visible in schema. Bare modifiers apply in one fixed
order, mult → div → add (CR-2026-002, PS-101); any other order is written as an explicit
`transform` list.

**Rationale**:
- Predictable: `div: 10` and `add: -40` always mean `(raw / 10) - 40`, in whichever
  order the keys are written
- Debuggable: Each step traceable
- No hidden conversions based on field names

**Example**:
```yaml
- name: temperature
  type: s16
  div: 10      # Step 1: divide
  add: -40     # Step 2: offset
```

### 4. Composition Over Inheritance

**Decision**: Use `definitions` for reuse, `$ref` for inclusion.

**Rationale**:
- Flat structure easier to parse in constrained environments
- No diamond inheritance problems
- Clear data flow

```yaml
definitions:
  battery:
    fields:
      - name: battery_mv
        type: u16

fields:
  - $ref: '#/definitions/battery'   # Splice the definition's fields inline
  - name: temperature
    type: s16
```

The `use:` key (with `rename:`/`prefix:`) is resolved only by
`tools/schema_preprocessor.py`, never by an interpreter, so it is not the reuse mechanism
of the language.

### 5. Progressive Complexity

**Decision**: Simple cases are simple; complexity available when needed.

| Complexity | Feature |
|------------|---------|
| Basic | `type: u16`, `div: 10` |
| Intermediate | `match`, `flagged`, `lookup` |
| Advanced | `polynomial`, `guard`, `tlv` |

Most sensors need only basic features. Complex industrial protocols can use full power.

## Type System Design

### Why Short Type Names?

```yaml
type: u16         # width in the name
type: s24         # unusual sizes have their own names
```

**Rationale**:
- Short forms (`u8`, `s16`, `f32`) match C conventions, reduce verbosity
- The width is part of the name, so no separate `length:` is needed for numbers;
  `u24`/`s24` cover the common odd size
- Long forms such as `type: UInt` with `length:` were considered and are not
  implemented: every interpreter rejects them as an unknown type

### Why One Bitfield Syntax?

```yaml
type: u8[0:3]     # Range syntax - bits 0-3, the only spelling
```

**Rationale**:
- Range syntax is natural for hardware engineers (it matches datasheets)
- One spelling means a schema reads the same way in every implementation

The language carried five spellings for a while - `u8[0:3]`, `u8[3+:2]`,
`bits<3,2>`, `bits:2@3` and the sequential `u8:4` - deliberately, to keep the
options open while the working group chose. CR-2026-006 settled on the range and
withdrew the rest.

The sequential form is why it mattered. "Four bits from the current position" needs
a bit cursor every implementation maintains identically, and only Python ever did:
Go and Java let the type fall through to a plain `u8` and read a whole byte, C#
reported an unknown field type, C set a sentinel no decode path read, and the
binary encoder and JS generator both resolved it to bit 0. A range says where the
bits are, so there is nothing to keep in step.

### Why Separate `udec`/`sdec` Types?

**Problem**: Some sensors encode decimals as BCD-like nibbles.

**Example**: 2.3 encoded as `0x23` (not `0x17`).

**Solution**: Dedicated one-byte types: the high nibble is the integer part, the low
nibble the tenths:
```yaml
type: udec       # 0x23 → 2.3
type: sdec       # 0xE5 → -1.5 (high nibble is a signed two's-complement nibble, -2 + 0.5)
```

**Rationale**: Common enough in LoRaWAN to warrant first-class support.

## Conditional Parsing Design

### Why Three Conditional Constructs?

| Construct | Use Case |
|-----------|----------|
| `match` | Dispatch on message type field |
| `flagged` | Bitmask presence indicators |
| `tlv` | Self-describing variable payloads |

**Rationale**: Each maps to common protocol patterns:

**`match`** - Fixed message types:
```yaml
- match:
    field: $msg_type
    cases:
      1:
        - name: temperature
          type: s16
      2:
        - name: latitude
          type: s32
```

**`flagged`** - Optional sensor groups:
```yaml
- flagged:
    field: sensors_present
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

**`tlv`** - Extensible protocols:
```yaml
- tlv:
    tag_size: 1
    length_size: 1
    cases:
      103:              # 0x67
        - name: temperature
          type: s16
      104:              # 0x68
        - name: humidity
          type: u8
```

## Arithmetic Pipeline Design

### Why a Fixed Modifier Order?

**Original decision (reversed)**: `add`, `mult`, `div` applied in YAML key order.

**Current decision**: CR-2026-002 (PS-101/PS-102) replaced it with a fixed canonical
order, **mult → div → add**, regardless of how the keys are written. An order other
than the canonical one must be an explicit `transform` list.

**Why it was reversed**:
- Implementations diverged: Python, the TS013 generator and the C firmware generator
  followed key order, while Go, Java and C# applied different orders depending on
  input format and code path, so one schema decoded differently across them
- A reader cannot see from the keys alone which order was intended
- `transform` already expressed ordered arithmetic explicitly

```yaml
# Pattern 1: Scale then offset - the canonical order, so bare keys work
- name: temp_c
  type: u16
  div: 100
  add: -40

# Pattern 2: Offset then scale - needs a transform
- name: pressure
  type: u16
  transform:
    - add: -32768
    - mult: 0.1
```

`examples/canonical-modifier-order.yaml` pins this across every implementation, and
`validate_schema.py` warns when a field carries two or more bare modifiers.

### Why Separate `transform` Array?

**Problem**: Some operations don't fit mult/div/add model.

**Solution**: Transform pipeline for mathematical operations:
```yaml
transform:
  - sqrt: true
  - mult: 2
  - {op: round, decimals: 1}
```

(`floor`, `ceiling` and `clamp` stages exist in the Python interpreter and the TS013
generator only; Go, Java and C# ignore them, so they are not portable.)

**Rationale**:
- Clear ordering (array = sequential)
- Extensible (add new transforms without breaking existing)
- Composable with arithmetic modifiers

## Binary Format Design

### Why a Binary Schema Format?

**Use cases**:
- OTA schema transfer (save airtime)
- Embedded systems (no YAML parser)
- QR code encoding (limited space)

**Design**: 4-6 bytes per field:
```
Header: 'P' 'S' version flags field_count
Field:  type_byte mult_exp semantic_id[2] [options]
```

**Rationale**:
- 10-20x smaller than YAML
- O(1) field access
- No string parsing at runtime

### Why Not Use Protobuf/MessagePack?

**Considered**: Using existing binary formats.

**Rejected because**:
- Protobuf requires schema compilation
- MessagePack still needs schema definition
- Custom format optimized for this specific use case
- Simpler implementation in constrained C

## Feature Exclusions

### Why No Formula Field?

**Considered**: `formula: "sqrt(x * 0.01) + offset"`

**Rejected because**:
- Requires expression parser (complex, security risk)
- Platform-dependent floating point
- Debugging difficulty
- Covered by `polynomial` + `transform` + `compute`

### Why No Conditional Arithmetic?

**Considered**: `mult: { if: "$range == 1", then: 0.1, else: 0.01 }`

**Current approach**: Dispatch on the controlling field with `match` and declare the
scaled field once per case; range keys such as `"0..99"` select a band of values:
```yaml
- match:
    field: $range
    cases:
      1:
        - name: reading
          type: u16
          mult: 0.1
      default:
        - name: reading
          type: u16
          mult: 0.01
```

A `match_value` construct (a value-dependent modifier list) was proposed and is **not
implemented in any interpreter**.

**Rationale**: Keeps arithmetic simple; complex cases use explicit matching.

### Why No Encryption/Compression?

**Decision**: Schema describes payload structure only.

**Rationale**:
- Encryption is transport concern (handled by LoRaWAN)
- Compression varies by implementation
- Schema remains focused and portable

## Compatibility Considerations

### TTN Codec Compatibility

**Goal**: Convert existing TTN JavaScript codecs to schemas.

**Mapping**:
| TTN Pattern | Schema Equivalent |
|-------------|-------------------|
| `bytes[i]` | Sequential field reads |
| `<< / >>` | Bitfields or multi-byte types |
| Lookup object | `lookup:` sequence or mapping |
| Math expressions | `add`/`mult`/`div` + `transform` |

**Limitation**: Complex algorithms require manual conversion or hybrid approach.

### Milesight TLV Compatibility

**Design decision**: First-class TLV support because Milesight (major vendor) uses it extensively.

```yaml
- tlv:
    tag_fields:       # Milesight: a channel id and a channel type byte, no length
      - name: channel_id
        type: u8
      - name: channel_type
        type: u8
    tag_key: [channel_id, channel_type]
    cases:
      "[1, 117]":
        - name: battery
          type: u8
```

## Future Considerations

### Reserved Keywords

These are reserved for future use:
- `compress` - Payload compression hints
- `encrypt` - Field-level encryption markers
- `validate` - Runtime validation rules
- `encode` - Bidirectional encoding hints

### Extension Points

Schema can include vendor extensions:
```yaml
x-vendor:
  custom_property: value
```

Interpreters MUST ignore unknown `x-` prefixed keys.

## Summary

The Payload Schema language prioritizes:

1. **Security** - Declarative, no code execution
2. **Portability** - Works across languages and platforms  
3. **Simplicity** - Common cases are concise
4. **Predictability** - Explicit ordering, no magic
5. **Extensibility** - Future features won't break existing schemas
