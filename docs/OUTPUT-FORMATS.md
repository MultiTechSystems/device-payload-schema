# Output Format Comparison

The Payload Schema decoder can output data in multiple formats for different platforms and standards.
The raw format is what every interpreter returns; the IPSO, SenML and TTN views are
produced by the Python interpreter's `get_semantic_output()` alone (see
[API Usage](#api-usage) for its current limits).

> **Note:** Output formats (IPSO, SenML, TTN) are a **network-side concern only**. 
> This applies to BOTH directions:
> - **Uplink:** Device encodes values→bytes, Network decodes bytes→JSON→IPSO/SenML
> - **Downlink:** Network encodes config→bytes, Device decodes bytes→values
>
> The device never sees semantic formats - it only converts between values and bytes.
> This keeps device firmware simple and small.

## Example

**Schema:** Environmental sensor with temperature, humidity, pressure, CO2, battery
(the schema is under [Schema Definition](#schema-definition))  
**Payload:** `0929822794035221` (8 bytes)

---

## 1. Raw Format (Default)

Simple flat dictionary - easiest to use in applications.

```json
{
  "temperature": 23.45,
  "humidity": 65,
  "pressure": 1013.2,
  "co2": 850,
  "battery": 3.3
}
```

An integral value is reported as an integer (`65`, not `65.0`), as a JavaScript codec
would (CR-2026-008). Keys beginning with `_` are the interpreter's own: `_quality` appears
when a decoded field declares `valid_range` (Python, Go, C# and the TS013 codec; not Java),
and Go, Java and C# add `_warnings` when the decode had something to report (Python returns
those in `result.warnings` instead). `decode()` never adds `_meta`; `interpret()` does
(section 1a).

**Use case:** Direct application use, custom backends, simple integrations.

---

## 1a. Interpreter Output: decoded JSON and `_meta`

`_meta` is the normative interface to the Integration Layer (PS-175, PS-180). It is the
schema's semantic annotations and the uplink's context, beside the decoded values, so a
converter downstream can name, type and route each value without reading the schema.

`decode()` is unchanged and returns the decoded fields alone, because existing callers such
as td-tools read it. `interpret()` returns the same decoded fields with `_meta` added last.
A generated TS013 codec never carries `_meta` (PS-467), and the C interpreter does not
produce it yet.

```json
{
  "temperature": 23.1,
  "humidity": 50,
  "_meta": {
    "schema": "meta_device_context",
    "version": 2,
    "device_eui": "00112233445566aa",
    "rx_time": 1787745600.123,
    "fPort": 7,
    "fields": {
      "temperature": {"type": "s16", "unit": "Cel",
                      "ipso": {"object": 3303, "instance": 0, "resource": 5700},
                      "senml": {"name": "temp"}, "description": "Air temperature"},
      "humidity": {"type": "u8", "unit": "%RH"}
    }
  }
}
```

**The input context (PS-495)** is the TS013 uplink input plus the device's EUI:
- `fPort`;
- `input_metadata`, carrying `recvTime` (ISO 8601 or seconds) and `devEUI`.

A test vector supplies it the same way, through its `fPort` and `input_metadata`.

| Language | Call |
|---|---|
| Python | `SchemaInterpreter(schema).interpret(payload, fPort=7, input_metadata={"recvTime": ..., "devEUI": ...})` |
| Go | `s.Interpret(payload, schema.InputContext{FPort: &port, RecvTime: ..., DevEUI: ...})` |
| Java | `schema.interpret(payload, new InputContext(7, recvTime, devEui))` |
| C# | `SchemaDecoder.Interpret(schema, payload, new InputContext(7, recvTime, devEui))` |

**Device level:**
- `schema` and `version` come from the schema.
- `device_eui` is 16 lower-case hex digits, with separators removed (PS-496).
- `rx_time` is Unix seconds in UTC, keeping milliseconds (PS-177).
- `fPort` is reported as supplied, even where selection fell through to `default` (PS-342).

A key whose source is absent is omitted. A devEUI or recvTime that is present but malformed
is an error, and a failed decode produces no `_meta` (PS-497).

**`fields`** has exactly one entry per top-level reported key (PS-181). Each entry comes from
the declaration that produced the value (PS-490), so a name declared in two cases is typed by
the case that was taken. A `name_from` key gets its declaration's entry (PS-492). The entry's
attributes are:
- `type`: the declared type, an alias written canonically and a bit range as written (PS-493);
- `unit`: `senml.unit`, else `unit`, never converted (PS-178);
- `ipso`: with its defaults filled in;
- `senml.name` (PS-480);
- `description`.

An object's `fields` and a repeat's `elements` list what is declared (PS-481, PS-371). No
internal field has an entry at any depth (PS-494). A repeat element's per-element `unit`,
`ipso.instance` and `identity` are listings, marked `open` where the field's lookup default
carries `${value}` (PS-375, PS-489).

**Schema rule (PS-491):** the declarations of one reported name in one field list must agree
on `unit`, `senml` and `ipso`, or the schema is rejected at load. No corpus schema breaks it.

`make gate-verdicts` compares every corpus vector's `_meta` across Python, Go, Java and C#
(rule 3). A test vector may pin it with `expected_meta`, which is compared exactly.

---

## 2. IPSO Smart Objects Format

OMA LwM2M standard object IDs - interoperable with LwM2M servers.

```json
{
  "3303": {
    "value": 23.45,
    "unit": "°C"
  },
  "3304": {
    "value": 65,
    "unit": "%RH"
  },
  "pressure": 1013.2,
  "3325": {
    "value": 850,
    "unit": "ppm"
  },
  "3316": {
    "value": 3.3,
    "unit": "V"
  }
}
```

Each annotated field is keyed by its object id, with the field's `unit:` string; a field
with no IPSO object (here `pressure`, whose hPa is not IPSO 3315's unit) keeps its own
name and raw value. Two fields sharing an object id collide, and the later one wins.

> **Known defect:** `get_semantic_output(data, 'ipso')` reads the object id from the
> legacy nested `semantic: {ipso: 3303}` form only. With the canonical annotations shown
> under [Schema Definition](#schema-definition), where `semantic:` is a string, it raises
> `AttributeError`. The output above is what it produces for the legacy form.

**IPSO Object Reference:**
| Object ID | Name | Typical Use |
|-----------|------|-------------|
| 3200 | Digital Input | Binary sensors, switches |
| 3202 | Analog Input | Generic analog readings |
| 3301 | Illuminance | Light sensors (lux) |
| 3302 | Presence | Motion/occupancy sensors |
| 3303 | Temperature | Temperature sensors (°C) |
| 3304 | Humidity | Relative humidity (%) |
| 3308 | Set Point | Thermostat targets, setpoints |
| 3314 | Magnetometer | Compass, magnetic field |
| 3315 | Barometer | Atmospheric pressure |
| 3316 | Voltage | Battery voltage, power supply |
| 3317 | Current | Electrical current (A) |
| 3322 | Load | Weight, force sensors |
| 3323 | Pressure | Pressure sensors (Pa, bar) |
| 3325 | Concentration | CO2, gas sensors (ppm) |
| 3328 | Power | Power measurement (W) |
| 3330 | Distance | Range, level sensors (m) |
| 3331 | Energy | Energy consumption (Wh, kWh) |
| 3334 | Gyrometer | Angular velocity |
| 3336 | Location | GPS coordinates |
| 3337 | Positioner | Valve position, actuators (%) |
| 3347 | Push Button | Button press events |

**Use case:** LwM2M platforms (Leshan, Wakaama), cloud IoT services, OMA-compliant systems.

---

## 3. SenML Format (RFC 8428)

IETF Sensor Measurement Lists standard.

```json
[
  {"n": "temperature", "v": 23.45, "u": "Cel"},
  {"n": "humidity", "v": 65, "u": "%RH"},
  {"n": "pressure", "v": 101320, "u": "Pa"},
  {"n": "co2", "v": 0.00085, "u": "/"},
  {"n": "battery", "v": 3.3, "u": "V"}
]
```

One record per field the decoded output reports, wherever it is declared - a port
entry, a `match` or `tlv` case, a `byte_group`, a `flagged` group - and an object's
members as `object/member` (PS-478, PS-479). A record is named by the field's
`senml.name`, else its reported name.

Its unit comes from `senml.unit`, else `unit`, and is always a unit of the IANA SenML
Units registry (CR-2026-094):

- **A registered unit** is used as it is: `Cel`, `%RH`, `V`.
- **A secondary unit** is re-expressed in its primary unit by the registry's scale and
  offset, computed in decimal (PS-487): 1013.2 `hPa` is 101320 `Pa`, 850 `ppm` is
  0.00085 `/`, 3284 `mV` is 3.284 `V` (not 3.2840000000000003), -97 `dBm` is -127 `dBW`.
  `%` is written by its preferred name `/`, with no change of value.
- **A unit the registry does not have** (`°C`, `kPa`, `bar`) is left out of the record,
  the value kept, with a warning naming the field and the unit (PS-488). Python reports
  it in `interpreter.semantic_warnings` after `get_semantic_output()`; C# adds it to the
  optional `warnings` list passed to `SemanticFormatter.ToSenML()`.

The decoded output and `_meta` keep the author's unit; only the SenML view converts.
The registry tables are `SENML_UNITS` and `SENML_SECONDARY_UNITS` in
`tools/schema_interpreter.py` and `SenmlUnits` in C#.

**SenML Fields:**
- `n` - name
- `v` - value (numeric)
- `vs` - value (string)
- `vb` - value (boolean)
- `vd` - value (bytes, hex string)
- `u` - unit
- `t` - time (optional)
- `bt` - base time (optional)

**Use case:** CoAP integration, CBOR encoding, RFC-compliant systems.

---

## 4. TTN Normalized Format

A `decoded_payload` / `normalized_payload` pair modelled on The Things Network v3. The
`normalized_payload` shape here is this interpreter's own (`{measurement: {<field name>:
{value, unit}}}`); it does not follow TTN's normalized payload schema (`lib/payload.json`
in the device repository, e.g. `{"air": {"temperature": 23.45}}`), so it is not accepted
where TTN expects that schema. `decoded_payload` is the raw output, `_quality` included.

```json
{
  "decoded_payload": {
    "temperature": 23.45,
    "humidity": 65,
    "pressure": 1013.2,
    "co2": 850,
    "battery": 3.3
  },
  "normalized_payload": [
    {
      "measurement": {
        "temperature": {
          "value": 23.45,
          "unit": "°C"
        }
      }
    },
    {
      "measurement": {
        "humidity": {
          "value": 65,
          "unit": "%RH"
        }
      }
    },
    {
      "measurement": {
        "pressure": {
          "value": 1013.2,
          "unit": "hPa"
        }
      }
    },
    {
      "measurement": {
        "co2": {
          "value": 850,
          "unit": "ppm"
        }
      }
    },
    {
      "measurement": {
        "battery": {
          "value": 3.3,
          "unit": "V"
        }
      }
    }
  ]
}
```

**Use case:** The Things Network, TTN Console, TTN integrations, Cayenne LPP compatibility.

---

## Format Comparison

| Format | Structure | Size | Interoperability | Best For |
|--------|-----------|------|------------------|----------|
| **Raw** | Flat dict | Smallest | Application-specific | Custom backends |
| **IPSO** | Object-based | Medium | High (OMA/LwM2M) | LwM2M platforms |
| **SenML** | Record array | Medium | High (IETF/CoAP) | RFC-compliant systems |
| **TTN** | Normalized | Largest | TTN ecosystem | TTN integrations |

---

## Schema Definition

Semantic annotations use the canonical form of `schemas/devices/decentlab/dl-5tm.yaml`:
`ipso: {object, instance, resource}`, `senml: {name, unit}` and a dotted `semantic:` name.
This is the schema behind the example above:

```yaml
name: env_sensor
version: 1
fields:
  - name: temperature
    type: s16
    div: 100
    unit: "°C"
    ipso: {object: 3303, instance: 0, resource: 5700}
    senml: {name: "temperature", unit: "Cel"}
    semantic: "air.temperature"
  - name: humidity
    type: u8
    div: 2
    unit: "%RH"
    ipso: {object: 3304, instance: 0, resource: 5700}
    senml: {name: "humidity", unit: "%RH"}
    semantic: "air.humidity"
  - name: pressure        # hPa: SenML's unit is Pa, so no annotation
    type: u16
    div: 10
    unit: "hPa"
  - name: co2
    type: u16
    unit: "ppm"
    ipso: {object: 3325, instance: 0, resource: 5700}
    senml: {name: "co2", unit: "ppm"}
    semantic: "air.co2"
  - name: battery
    type: u8
    div: 10
    unit: "V"
    ipso: {object: 3316, instance: 0, resource: 5700}
    senml: {name: "vbat", unit: "V"}
    semantic: "battery.voltage"
test_vectors:
  - name: example
    payload: "0929 82 2794 0352 21"
    source: generated     # illustrative device; values computed from the layout
    expected:
      temperature: 23.45
      humidity: 65
      pressure: 1013.2
      co2: 850
      battery: 3.3
```

These annotations are what `tools/score_schema.py` scores. The nested
`semantic: {ipso: 3303, senml: ...}` form is legacy; see the IPSO defect note above for
the one place that still reads it.

---

## API Usage

### Python

```python
from schema_interpreter import SchemaInterpreter

interpreter = SchemaInterpreter(schema)
result = interpreter.decode(payload)

# Raw format (default)
raw = result.data

# IPSO format (legacy nested annotations only; see the defect note above)
ipso = interpreter.get_semantic_output(result.data, 'ipso')

# SenML format
senml = interpreter.get_semantic_output(result.data, 'senml')

# TTN format
ttn = interpreter.get_semantic_output(result.data, 'ttn')
```

`get_semantic_output()` walks the schema's top-level `fields:` only, so fields inside
`ports`, `flagged`, `match` or `tlv` are left out of the IPSO, SenML and TTN views. An
unknown format name returns the raw data unchanged.

### Network Server Integration

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  LoRaWAN Device │────▶│  Network Server │────▶│  Application    │
│                 │     │                 │     │                 │
│  Uplink Payload │     │  Decode + Format│     │  IPSO/SenML/TTN │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

The network server can:
1. Decode payload using schema
2. Transform to target format based on application requirements
3. Forward to appropriate backend (LwM2M, MQTT, HTTP, etc.)

---

## Output JSON Schema

Each device schema can generate a corresponding **Output JSON Schema** that describes
the structure of the decoded payload. This enables validation of decoder output with
standard JSON Schema tools.

### Generation

```bash
python tools/generate_output_schema.py schemas/my-device.yaml -o my-device-output.schema.json
```

### Example Output Schema

For a sensor with `temperature` (`s16`, `div: 10`, `unit: "°C"`,
`valid_range: [-40, 85]`) and `humidity` (`u8`, `unit: "%"`):

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://lorawan-schema.org/devices/temp_humidity/v1/output",
  "title": "temp_humidity Decoded Payload",
  "description": "Temperature and humidity sensor",
  "type": "object",
  "properties": {
    "temperature": {
      "type": "number",
      "description": "Unit: \u00b0C. Valid range: [-40, 85]"
    },
    "humidity": {
      "type": "integer",
      "minimum": 0,
      "maximum": 255,
      "description": "Unit: %"
    },
    "_quality": {
      "type": "object",
      "description": "Per-field quality flags for fields declaring valid_range (PS-131/PS-182). Present only when at least one such field is decoded; absent otherwise.",
      "properties": {
        "temperature": {
          "type": "string",
          "enum": ["good", "out_of_range"],
          "description": "good = within valid_range; out_of_range = outside it"
        }
      },
      "additionalProperties": false
    },
    "_warnings": {
      "type": "array",
      "description": "What the decode had to say that did not stop it - a value outside its `valid_range`, or a TLV tag the schema does not describe (PS-301, PS-302). Present only when something was reported.",
      "items": {"type": "string"}
    }
  },
  "additionalProperties": true
}
```

A field with no modifier is an `integer` bounded by its wire type; a scaled field is a
`number`. `_quality` and `_warnings` are described only where the schema can produce
them.

### Validation Usage

```javascript
const Ajv = require('ajv');
const ajv = new Ajv();

const outputSchema = require('./my-device-output.schema.json');
const validate = ajv.compile(outputSchema);

const decoded = decodeUplink(input);
const valid = validate(decoded.data);

if (!valid) {
  console.error('Decoder output validation failed:', validate.errors);
}
```

### Benefits

| Benefit | Description |
|---------|-------------|
| **Type Safety** | Ensures decoder output matches expected types |
| **Documentation** | Schema serves as API documentation |
| **Integration** | Standard format for API contracts |
| **Testing** | Automated validation in CI/CD pipelines |
| **Tooling** | IDE autocomplete from schema |

### Deliverables per Device

For each device schema, the following artifacts SHOULD be generated:

1. **YAML Schema** (`device.yaml`) - Source payload definition
2. **JS Codec** (`device-codec.js`) - TS013-compliant decoder/encoder
3. **Output Schema** (`device-output.schema.json`) - JSON Schema for decoded payload

---

## Format-Specific JSON Schemas

Different output formats have different validation schemas. The examples below use
SenML unit symbols (`Cel`), as SenML output does (section 3).

| Format | Schema | Scope |
|--------|--------|-------|
| **Raw** | Per-device generated | Device-specific fields |
| **IPSO** | `schemas/ipso-output.schema.json` | Generic structure |
| **SenML** | `schemas/senml-output.schema.json` | RFC 8428 compliant |
| **TTN** | `schemas/ttn-output.schema.json` | TTN normalized format |

### Raw Format Schema (Per-Device)

Generated by `generate_output_schema.py` - validates device-specific field names and types.

```bash
python tools/generate_output_schema.py device.yaml -o device-output.schema.json
```

### IPSO Format Schema (Generic)

Validates the IPSO object structure (object IDs as keys, value/unit properties):

```json
{
  "3303": {"value": 23.45, "unit": "Cel"},
  "3304": {"value": 65, "unit": "%RH"}
}
```

Location: `schemas/ipso-output.schema.json`

### SenML Format Schema (RFC 8428)

Validates SenML record arrays per IETF RFC 8428:

```json
[
  {"n": "temperature", "v": 23.45, "u": "Cel"},
  {"n": "humidity", "v": 65, "u": "%RH"}
]
```

Location: `schemas/senml-output.schema.json`

### TTN Normalized Format Schema

Validates The Things Network v3 format with `decoded_payload` and `normalized_payload`:

```json
{
  "decoded_payload": {"temperature": 23.45},
  "normalized_payload": [
    {"measurement": {"temperature": {"value": 23.45, "unit": "Cel"}}}
  ]
}
```

Location: `schemas/ttn-output.schema.json`

### Multi-Format Validation Example

```javascript
const Ajv = require('ajv');
const ajv = new Ajv();

// Load schemas
const rawSchema = require('./device-output.schema.json');
const ipsoSchema = require('./schemas/ipso-output.schema.json');
const senmlSchema = require('./schemas/senml-output.schema.json');
const ttnSchema = require('./schemas/ttn-output.schema.json');

// Validate based on output format
function validateOutput(data, format) {
  const schemas = {
    'raw': rawSchema,
    'ipso': ipsoSchema,
    'senml': senmlSchema,
    'ttn': ttnSchema
  };
  
  const validate = ajv.compile(schemas[format]);
  return validate(data) ? null : validate.errors;
}
```
