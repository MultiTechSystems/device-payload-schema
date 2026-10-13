# IPSO Smart Objects Reference

Complete reference for IPSO Smart Objects (OMA LwM2M) used in LoRaWAN payload schemas.

Source: [OMA LwM2M Registry](https://technical.openmobilealliance.org/OMNA/LwM2M/LwM2MRegistry.html)

## Common Sensor Objects (3300-3350)

| ID | Name | Description | Common Fields |
|----|------|-------------|---------------|
| 3300 | Generic Sensor | General-purpose sensor | value, unit, min, max |
| 3301 | Illuminance | Light level (lux) | value (lx) |
| 3302 | Presence | Motion/occupancy detection | value (boolean) |
| 3303 | Temperature | Temperature sensor | value (Cel) |
| 3304 | Humidity | Relative humidity | value (%RH) |
| 3305 | Power Measurement | Power monitoring | value (W) |
| 3306 | Actuation | On/off control, dimmer | on/off, dimmer (%) |
| 3308 | Set Point | Target/setpoint values | value |
| 3310 | Load Control | Load management | event |
| 3311 | Light Control | Light on/off/dimmer | on/off, dimmer |
| 3312 | Power Control | Power on/off | on/off |
| 3313 | Accelerometer | 3-axis acceleration | x, y, z (m/s²) |
| 3314 | Magnetometer | Magnetic field | x, y, z (T) |
| 3315 | Barometer | Atmospheric pressure | value (Pa) |
| 3316 | Voltage | Voltage measurement | value (V) |
| 3317 | Current | Current measurement | value (A) |
| 3318 | Frequency | Frequency measurement | value (Hz) |
| 3319 | Depth | Depth/level measurement | value (m) |
| 3320 | Percentage | Generic percentage | value (%) |
| 3321 | Altitude | Altitude measurement | value (m) |
| 3322 | Load | Weight/force sensor | value (kg) |
| 3323 | Pressure | Pressure sensor | value (Pa) |
| 3324 | Loudness | Sound level | value (dB) |
| 3325 | Concentration | Gas concentration (CO2, etc.) | value (ppm) |
| 3326 | Acidity | pH measurement | value (pH) |
| 3327 | Conductivity | Electrical conductivity | value (S/m) |
| 3328 | Power | Power measurement | value (W) |
| 3329 | Power Factor | Power factor | value |
| 3330 | Distance | Distance/range sensor | value (m) |
| 3331 | Energy | Energy consumption | value (Wh) |
| 3332 | Direction | Compass heading | value (deg) |
| 3333 | Time | Time value | value (s) |
| 3334 | Gyrometer | Angular velocity | x, y, z (deg/s) |
| 3335 | Colour | RGB color | r, g, b |
| 3336 | Location | GPS coordinates | lat, lon, alt |
| 3337 | Positioner | Position actuator | value (%) |
| 3338 | Buzzer | Audio alert | on/off |
| 3339 | Audio Clip | Audio playback | clip |
| 3340 | Timer | Timer/countdown | remaining (s) |
| 3341 | Addressable Text Display | Text display | text |
| 3342 | On/Off Switch | Binary switch | state |
| 3343 | Dimmer | Dimmer control | level (%) |
| 3344 | Up/Down Control | Increment/decrement | - |
| 3345 | Multiple Axis Joystick | Joystick input | x, y, z |
| 3346 | Rate | Speed/rate measurement | value (m/s, RPM) |
| 3347 | Push Button | Button press | count, state |
| 3348 | Multi-state Selector | Multi-position selector | state |

## Usage in Schema

```yaml
fields:
  - name: temperature
    type: s16
    div: 100
    unit: "°C"
    ipso: 3303
    senml: {unit: "Cel"}
    semantic: "temperature.ambient"
```

## Adding New IPSO Objects

When converting a TTN codec that uses an IPSO object not in this list:

1. Look up the object in the [OMA Registry](https://github.com/OpenMobileAlliance/lwm2m-registry)
2. Add to this reference document
3. Add a SenML unit mapping if applicable (`SENML_UNITS` in `tools/score_schema.py`)

## Which Fields the Scorer Expects to Be Annotated

`tools/score_schema.py` expects an IPSO annotation only on a field the
Integration Layer's TTN classification rule would place from its name
(IL-204, in la-integration-layer's `docs/guides/protocol-mappings/ttn-normalized.md`).
A name is split on `_` and at camelCase boundaries, ignoring case:

1. A **qualifier** word means the field is not a reading: `threshold`,
   `setpoint`, `target`, `min`, `max`, `minimum`, `maximum`, `avg`, `average`,
   `mean`, `median`, `percentile`, `change`, `delta`, `raw`, `limit`, `alarm`,
   `offset`. No annotation is expected, and none is checked.
2. A **TTN quantity** word makes it a reading. With no medium word, the path is
   the one below. `soil` gives `soil.temperature` and `soil.moisture`; `water`
   gives `water.temperature`.
3. A **medium with no TTN path** (`leaf`, `dew`, `surface`, `internal`, `board`,
   `pcb`, `electronics`, `head`, `barometer`, or `soil`/`water` with a quantity
   they have no path for) leaves a reading IL-204 cannot place. An annotation on
   it is right, but none is expected, and none is checked.
4. Any other name says nothing. Its annotation is the only evidence, so it is
   neither expected nor flagged, and it earns no credit.

| Quantity | TTN path (no medium) | IPSO expected | Also accepted | SenML Unit |
|----------|----------------------|---------------|---------------|------------|
| temperature | air.temperature | 3303 | | Cel |
| humidity | air.relativeHumidity | 3304 | | %RH |
| pressure | air.pressure | 3323 | 3315 | Pa |
| moisture | soil.moisture | 3320 | 3304 | % |
| precipitation | rain.cumulative | 3300 | | m |

A unit never selects an object (IL-203), so neither `V` nor the word `battery`
asks for 3316. A generic object (3300, 3320, 3325, 3332) selects no category on
its own (IL-199), so it is never reported as a mismatch. It earns credit only
where the table expects it.

An ipso annotation is reported wrong only where the name places the field and
the annotation names another quantity, as with `water_temperature` annotated
3304. A field whose `lookup` reports text - every label a string, or no label but a
`${value}` default - reports a label, not a measured value, so it is not a reading
whatever its name says (`temperature_sensor_status`). A lookup with numeric labels feeds
arithmetic (Elvaco's exponent table) and its field stays a reading. The
`sensor:` key is not part of the language and is rejected by strict validation.

A schema with fields but none that IL-204 places has nothing these checks can
verify. It earns half of the 20 semantic points.

## Complex Codec Examples (from TTN lorawan-devices)

These codecs demonstrate edge cases and features to consider:

| Device | Features | Schema Challenges |
|--------|----------|-------------------|
| Sensative Strips | 32 message types, door/flood/tamper | Large switch statement |
| Digital Matter Oyster | GPS, signed coords, speed/heading | 24-bit signed integers |
| Dragino LAQ4 | CO2, TVOC, mode-based | Conditional field parsing |
| Tektelic Agriculture | TLV, coefficients | Data-driven decoding |
| Decentlab DL-5TM | Flag-based, polynomial | Sensor presence bitmask |

## Version History

- v1.0: Initial 21 common objects
- v1.1: Added 27 additional objects (3318-3348)
- v1.2: Added complex codec examples from TTN
