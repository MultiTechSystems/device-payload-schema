# Payload Schema Library

Reusable sensor type definitions for building LoRaWAN payload schemas.

## Usage

Use the preprocessor to resolve cross-file references:

```bash
python tools/schema_preprocessor.py my_sensor.yaml -o my_sensor_resolved.yaml
```

Then use the resolved schema with any interpreter.

## Library Structure

```
lib/
├── sensors/           # Individual sensor type definitions
│   ├── environmental.yaml   # Temperature, humidity, pressure, CO2, etc.
│   ├── power.yaml          # Battery, voltage, current, energy
│   ├── position.yaml       # GPS, accelerometer, gyroscope
│   ├── distance.yaml       # Ultrasonic, radar, level sensors
│   ├── digital.yaml        # Digital I/O, counters, presence
│   └── flow.yaml           # Water/gas meters, flow rates
│
├── profiles/          # Pre-built sensor combinations
│   ├── env-sensor.yaml     # Environmental sensor profiles
│   └── tracker.yaml        # GPS tracker profiles
│
└── common/            # Headers and utilities
    └── headers.yaml        # Message headers, timestamps, device info
```

## Example Schema

```yaml
name: my_env_sensor
version: 1
endian: big

fields:
  # Use library definitions
  - $ref: "lib/common/headers.yaml#/definitions/msg_type_header"
  - $ref: "lib/profiles/env-sensor.yaml#/definitions/temp_humidity"
  - $ref: "lib/sensors/power.yaml#/definitions/battery_mv"
  - $ref: "lib/sensors/power.yaml#/definitions/battery_pct_from_mv"
  
  # Add device-specific fields
  - name: custom_status
    type: u8

test_vectors:
  - name: normal_reading
    payload: "01 00E7 32 0BB8"
    expected:
      msg_type: 1
      temperature: 23.1
      humidity: 25.0
      battery_mv: 3000
      battery_percent: 83.3
      custom_status: 0
```

## Field Renaming

Use `rename:` to change field names when including definitions. Useful for multiple sensors of the same type:

```yaml
fields:
  # Two temperature sensors with different names
  - $ref: "lib/sensors/environmental.yaml#/definitions/temperature_c"
    rename:
      temperature: indoor_temp
      
  - $ref: "lib/sensors/environmental.yaml#/definitions/temperature_c"
    rename:
      temperature: outdoor_temp
```

Use `prefix:` to add a prefix to all field names:

```yaml
fields:
  # Add "zone1_" prefix to all fields
  - $ref: "lib/profiles/env-sensor.yaml#/definitions/temp_humidity"
    prefix: "zone1_"
    
  # Results in: zone1_temperature, zone1_humidity
  
  - $ref: "lib/profiles/env-sensor.yaml#/definitions/temp_humidity"
    prefix: "zone2_"
    
  # Results in: zone2_temperature, zone2_humidity
```

Combine both (prefix applied first, then renames):

```yaml
fields:
  - $ref: "lib/profiles/env-sensor.yaml#/definitions/temp_humidity"
    prefix: "room_"
    rename:
      room_temperature: room_temp  # Shorten after prefix
```

## Available Definitions

### Environmental (`lib/sensors/environmental.yaml`)

| Definition | Type | Description |
|------------|------|-------------|
| `temperature_c` | s16/10 | Temperature °C, 0.1° resolution |
| `temperature_c_hp` | s16/100 | Temperature °C, 0.01° resolution |
| `temperature_c_offset` | u16/10-40 | Temperature with -40° offset |
| `humidity_pct` | u8*0.5 | Humidity %, 0.5% resolution |
| `humidity_pct_hp` | u16/10 | Humidity %, 0.1% resolution |
| `pressure_hpa` | u16/10 | Pressure hPa |
| `co2_ppm` | u16 | CO2 concentration ppm |
| `tvoc_ppb` | u16 | TVOC ppb |
| `pm25` | u16 | PM2.5 μg/m³ |
| `illuminance_lux` | u16 | Light level lux |

### Power (`lib/sensors/power.yaml`)

| Definition | Type | Description |
|------------|------|-------------|
| `battery_mv` | u16 | Battery voltage mV |
| `battery_v` | u16/100 | Battery voltage V |
| `battery_pct` | u8 | Battery percentage |
| `battery_pct_from_mv` | computed | Battery % from mV (2.0-3.2V) |
| `power_w` | u16 | Power Watts |
| `energy_kwh` | u32/1000 | Energy kWh |
| `current_ma` | u16 | Current mA |
| `voltage_v` | u16/100 | Voltage V |

### Position (`lib/sensors/position.yaml`)

| Definition | Type | Description |
|------------|------|-------------|
| `latitude` | s32/10M | Latitude degrees |
| `longitude` | s32/10M | Longitude degrees |
| `altitude` | s32/100 | Altitude meters |
| `gps_position` | composite | Lat + Lon + Alt |
| `gps_position_compact` | s24/10k | Reduced precision GPS |
| `accelerometer_g` | s16/1000 | Accel XYZ in g |
| `gyroscope_dps` | s16/100 | Gyro XYZ °/s |

### Profiles (`lib/profiles/`)

| Definition | Contents |
|------------|----------|
| `temp_humidity` | Temperature + Humidity |
| `temp_humidity_pressure` | + Pressure |
| `indoor_air_quality` | + CO2 + TVOC |
| `gps_basic` | Lat + Lon + Alt |
| `full_tracker` | GPS + Speed + Heading + Battery |

## IPSO Mappings

All sensor definitions include IPSO Smart Object mappings where applicable:

| Sensor Type | IPSO Object |
|-------------|-------------|
| Temperature | 3303 |
| Humidity | 3304 |
| Pressure | 3315 |
| Illuminance | 3301 |
| Voltage/Battery | 3316 |
| Current | 3317 |
| Power | 3328 |
| Energy | 3331 |
| GPS Location | 3336 |
| Accelerometer | 3313 |
| Gyroscope | 3334 |
| Digital Input | 3200 |
| Digital Output | 3201 |
| Presence | 3302 |

## Extending the Library

Add new definitions to the appropriate category file:

```yaml
# lib/sensors/environmental.yaml
definitions:
  # ... existing definitions ...
  
  my_custom_sensor:
    - name: soil_moisture
      type: u16
      div: 10
      unit: "%"
      ipso: {object: 3323, resource: 5700}
```

Or create a new category file and reference it in your schemas.
