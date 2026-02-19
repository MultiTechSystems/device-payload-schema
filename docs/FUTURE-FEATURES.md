# Future Features Roadmap

Proposed enhancements for the Payload Schema language, based on OPC UA standards analysis and embedded code generation requirements.

---

## Phase 1: Value Range Validation (High Priority)

### Feature: `valid_range`

Declarative bounds for expected output values. Unlike `clamp` (which modifies values), this validates and flags out-of-range readings.

**Schema Syntax:**
```yaml
- name: temperature
  type: s16
  div: 100
  unit: "°C"
  valid_range: [-40, 85]    # Expected operating range
```

**Interpreter Behavior:**
```python
# Decode result includes quality flags
{
    "temperature": 23.45,
    "_warnings": [],           # Empty if all valid
    "_quality": {
        "temperature": "good"  # or "out_of_range"
    }
}

# Out-of-range example (sensor reads -999 on wire break)
{
    "temperature": -999.0,
    "_warnings": ["temperature out of valid range [-40, 85]"],
    "_quality": {
        "temperature": "out_of_range"
    }
}
```

**Embedded Code Generation:**
```c
// Generated constants
#define SENSOR_TEMP_MIN  (-40.0f)
#define SENSOR_TEMP_MAX  (85.0f)

typedef struct {
    float temperature;        /* °C, valid: -40 to 85 */
    uint8_t temperature_valid;  /* 1 if in range */
} sensor_t;

// Generated validation in pack()
static inline int pack_sensor(const sensor_t *data, uint8_t *buf) {
    if (data->temperature < SENSOR_TEMP_MIN || 
        data->temperature > SENSOR_TEMP_MAX) {
        return CODEC_ERR_OUT_OF_RANGE;
    }
    // ... encode
}

// Generated validation in unpack()
static inline int unpack_sensor(const uint8_t *buf, size_t len, sensor_t *data) {
    // ... decode
    data->temperature_valid = (data->temperature >= SENSOR_TEMP_MIN && 
                               data->temperature <= SENSOR_TEMP_MAX);
    return (int)pos;
}
```

**Use Cases:**
- Detect sensor failures (broken wire reads as extreme value)
- Quality indicators in cloud platforms
- Defensive firmware that rejects bad readings before transmission
- OPC UA `EURange` compatibility

---

## Phase 2: Resolution Metadata (Medium Priority)

### Feature: `resolution`

Documents the minimum detectable change. Useful for fixed-point scaling decisions and generated constants.

**Schema Syntax:**
```yaml
- name: temperature
  type: s16
  div: 100
  unit: "°C"
  resolution: 0.01    # 0.01°C steps
```

**Embedded Code Generation:**
```c
#define SENSOR_TEMP_RESOLUTION  0.01f
#define SENSOR_TEMP_SCALE       100     /* 1/resolution */

// Useful for rounding before encode
int16_t raw = (int16_t)roundf(temp_c / SENSOR_TEMP_RESOLUTION);
```

**Interpreter Behavior:**
- Included in schema metadata output
- Optional rounding to resolution in decode output
- Exposed in SenML/IPSO extended attributes

---

## Phase 3: Standard Unit Codes (Low Priority)

### Feature: `unece` 

UNECE Recommendation 20 unit codes for OPC UA interoperability.

**Schema Syntax:**
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
| Humidity | P1 | % |
| Pressure (Pa) | PAL | Pa |
| Pressure (bar) | BAR | bar |
| Voltage | VLT | V |
| Current | AMP | A |
| Distance (m) | MTR | m |
| Distance (mm) | MMT | mm |

**Use Cases:**
- OPC UA `EngineeringUnits.UnitId` mapping
- Automatic unit conversion between systems
- Standards compliance for industrial IoT

---

## Phase 4: Extended Metadata (Documentation Only)

These fields provide documentation value but don't affect runtime behavior.

### Feature: `accuracy`

Measurement accuracy specification.

```yaml
- name: temperature
  type: s16
  div: 100
  unit: "°C"
  accuracy: 0.5       # ±0.5°C
```

**Generated C (comments):**
```c
float temperature;  /* °C, accuracy: ±0.5°C */
```

### Feature: `instrument_range`

Physical sensor limits (vs operating range).

```yaml
- name: temperature
  type: s16
  div: 100
  valid_range: [-40, 85]        # Operating range
  instrument_range: [-55, 125]  # Physical limits
```

**Use Cases:**
- Datasheet documentation
- Hardware selection tools
- Extended device metadata APIs

---

## Implementation Notes

### Schema Validation

```yaml
# validate_schema.py additions
valid_range:
  type: array
  items: number
  minItems: 2
  maxItems: 2

resolution:
  type: number
  exclusiveMinimum: 0

accuracy:
  type: number
  exclusiveMinimum: 0

unece:
  type: string
  pattern: "^[A-Z0-9]{2,3}$"
```

### Backward Compatibility

All new fields are optional. Existing schemas continue to work unchanged.

### Output Format Extensions

**SenML (RFC 8428 extended):**
```json
[
  {
    "n": "temperature",
    "v": 23.45,
    "u": "Cel",
    "vmin": -40,
    "vmax": 85
  }
]
```

**IPSO/LwM2M:**
```json
{
  "3303": {
    "5700": 23.45,
    "5701": "°C",
    "5603": -40,
    "5604": 85
  }
}
```
(5603 = Min Range Value, 5604 = Max Range Value per OMA registry)

---

## Priority Summary

| Feature | Runtime Value | Code Gen Value | Effort | Priority |
|---------|--------------|----------------|--------|----------|
| `valid_range` | High (quality flags) | High (bounds check) | Medium | **P1** |
| `resolution` | Low | Medium (constants) | Low | P2 |
| `unece` | Medium (interop) | None | Low | P3 |
| `accuracy` | None | Low (comments) | Trivial | P4 |
| `instrument_range` | None | Low (comments) | Trivial | P4 |

---

## Related Standards

- **OPC UA Part 8** - Data Access (AnalogItemType, EUInformation, EURange)
- **UNECE Rec 20** - Codes for Units of Measure
- **OMA LwM2M** - Object 3303 (Temperature), resources 5603/5604 (Min/Max Range)
- **RFC 8428** - SenML (vmin, vmax extensions proposed)
- **IEEE 1451** - Smart Transducer Interface (TEDS metadata)
