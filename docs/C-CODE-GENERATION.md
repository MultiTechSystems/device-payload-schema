# C Code Generation Guide

Generate standalone C codec headers from Payload Schema YAML files.

## Overview

The `generate-c.py` tool creates header-only C codecs that:

- Define a struct for decoded data
- Provide `decode_<name>()` function
- Provide `encode_<name>()` function
- Use no dynamic allocation (embedded-friendly)

## Usage

```bash
python tools/generate-c.py schema.yaml -o output.h
```

**Example:**
```bash
python tools/generate-c.py schemas/env_sensor.yaml -o include/env_sensor_codec.h
```

## Generated Code Structure

For a schema like:

```yaml
name: env_sensor
version: "1.0"
endian: little

fields:
  - name: temperature
    type: i16
    mult: 0.01
  - name: humidity
    type: u8
    mult: 0.5
  - name: battery_mv
    type: u16
  - name: status
    type: u8
```

The generator creates:

```c
typedef struct {
    s2_t temperature;
    u1_t humidity;
    u2_t battery_mv;
    u1_t status;
} env_sensor_t;

static inline int decode_env_sensor(const u1_t* buf, size_t len, env_sensor_t* out);
static inline int encode_env_sensor(const env_sensor_t* in, u1_t* buf, size_t max_len);
```

## Device Usage Example

### Decoding (Downlink Reception)

```c
#include "env_sensor_codec.h"

void handle_downlink(const uint8_t* payload, size_t len) {
    env_sensor_t config;
    
    int consumed = decode_env_sensor(payload, len, &config);
    if (consumed < 0) {
        // Error handling
        return;
    }
    
    // Apply multipliers (noted in generated code comments)
    float temperature = config.temperature * 0.01f;
    float humidity = config.humidity * 0.5f;
    
    // Use decoded values
    apply_config(temperature, humidity, config.battery_mv);
}
```

### Encoding (Uplink Transmission)

```c
#include "env_sensor_codec.h"

void send_uplink(void) {
    env_sensor_t data;
    uint8_t payload[16];
    
    // Fill struct (reverse the multipliers)
    data.temperature = (int16_t)(read_temp_sensor() / 0.01f);
    data.humidity = (uint8_t)(read_humidity() / 0.5f);
    data.battery_mv = read_battery_mv();
    data.status = get_status_flags();
    
    int len = encode_env_sensor(&data, payload, sizeof(payload));
    if (len > 0) {
        lorawan_send(FPORT, payload, len);
    }
}
```

## Return Values

| Value | Meaning |
|-------|---------|
| > 0 | Success: bytes consumed (decode) or written (encode) |
| -1 | Invalid parameters (NULL pointer) |
| -2 | Buffer too short |

## Multipliers

Multipliers are **not** applied automatically in the generated code. They appear as comments:

```c
out->temperature = read_u2_le(buf + pos);
/* Note: apply mult 0.01 in application */
```

Apply them in your application code:
```c
float temp_celsius = decoded.temperature * 0.01f;
```

For encoding, reverse the multiplier:
```c
data.temperature = (int16_t)(temp_celsius / 0.01f);
```

## Dependencies

The generated headers require `rt.h` which provides:

- Type aliases: `u1_t`, `u2_t`, `u4_t`, `s1_t`, `s2_t`, `s4_t`
- Read helpers: `read_u2_le()`, `read_u2_be()`, etc.
- Write helpers: `write_u2_le()`, `write_u2_be()`, etc.

## Generated vs Runtime Interpreter

| Aspect | Generated (`generate-c.py`) | Runtime (`schema_interpreter.h`) |
|--------|----------------------------|----------------------------------|
| Schema changes | Requires regenerate + recompile | Load new binary schema |
| Code size | Smaller per-schema | Fixed ~2KB + schema |
| Performance | Fastest | Fast |
| Flexibility | Fixed at compile time | Dynamic |
| Use case | Production devices | Development, OTA updates |

## Limitations

Current limitations of `generate-c.py`:

- Match/conditional fields: generates TODO comment
- Nested objects: not supported
- Bitfield syntax parsing: limited (`u8:4` only)
- Formula evaluation: not supported

For complex schemas, use the runtime interpreter instead.

## Example Workflow

```bash
# 1. Create/edit schema
vim schemas/my_sensor.yaml

# 2. Generate C header
python tools/generate-c.py schemas/my_sensor.yaml -o firmware/codecs/my_sensor_codec.h

# 3. Include in firmware
#include "codecs/my_sensor_codec.h"

# 4. Regenerate after schema changes
python tools/generate-c.py schemas/my_sensor.yaml -o firmware/codecs/my_sensor_codec.h
```
