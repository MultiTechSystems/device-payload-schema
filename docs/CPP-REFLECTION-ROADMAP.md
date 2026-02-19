# C++ Reflection Codec Roadmap

Future direction for compile-time codec generation using C++ reflection.

## Current State

```
YAML Schema → generate-c.py → codec.h → compile → binary
```

Code generation works but requires a build step.

## Future: C++ Reflection

```
C++ Struct with Attributes → compile → binary (codec generated at compile-time)
```

No code generator needed - the struct IS the schema.

## Available Now: Boost.PFR (C++14/17/20)

[Boost.PFR](https://github.com/boostorg/pfr) provides reflection for aggregate types.

```cpp
#include <boost/pfr.hpp>
#include "rt.h"

struct EnvSensor {
    int16_t temperature;
    uint8_t humidity;
    uint16_t battery_mv;
    uint8_t status;
};

// Generic encoder using reflection
template<typename T>
int encode_struct(const T& data, uint8_t* buf, size_t max_len) {
    size_t pos = 0;
    
    boost::pfr::for_each_field(data, [&](const auto& field) {
        using FieldType = std::decay_t<decltype(field)>;
        
        if constexpr (sizeof(FieldType) == 1) {
            buf[pos++] = static_cast<uint8_t>(field);
        } else if constexpr (sizeof(FieldType) == 2) {
            write_u2_le(buf + pos, static_cast<uint16_t>(field));
            pos += 2;
        } else if constexpr (sizeof(FieldType) == 4) {
            write_u4_le(buf + pos, static_cast<uint32_t>(field));
            pos += 4;
        }
    });
    
    return pos;
}

// Usage
EnvSensor sensor{2500, 100, 3000, 0};
uint8_t payload[16];
int len = encode_struct(sensor, payload, sizeof(payload));
```

**Limitations:**
- No access to field names at runtime
- Can't add custom attributes (mult, unit)
- Requires Boost dependency

## Coming: C++26 Native Reflection

C++26 adds native reflection with the `^` operator and `std::meta` namespace.

```cpp
#include <meta>

// Custom attributes for codec metadata
struct Mult { double value; };
struct Unit { const char* name; };

struct EnvSensor {
    [[Mult{0.01}, Unit{"Cel"}]]
    int16_t temperature;
    
    [[Mult{0.5}, Unit{"%RH"}]]
    uint8_t humidity;
    
    [[Unit{"mV"}]]
    uint16_t battery_mv;
    
    uint8_t status;
};

// Compile-time codec generation
template<typename T>
constexpr auto generate_codec() {
    constexpr auto type_info = ^T;
    
    // Iterate over members at compile time
    template for (constexpr auto member : std::meta::members_of(type_info)) {
        // Access field name, type, and attributes
        constexpr auto name = std::meta::name_of(member);
        constexpr auto attrs = std::meta::attributes_of(member);
        
        // Generate encode/decode logic
    }
}

// Zero-overhead encode
template<typename T>
int encode(const T& data, uint8_t* buf, size_t max_len) {
    // All reflection happens at compile time
    // Generated code is as efficient as hand-written
}
```

**Benefits:**
- No code generator
- Struct IS the schema
- Custom attributes for metadata (mult, unit, semantic)
- Compile-time validation
- Zero runtime overhead
- IDE support (autocomplete, refactoring)

## Implementation Plan

### Phase 1: Boost.PFR Prototype (Now)

```cpp
// Simple struct reflection without metadata
// Demonstrates compile-time field iteration
```

### Phase 2: Attribute Macros (C++20)

```cpp
// Use macros to attach metadata until C++26
#define PS_FIELD(type, name, ...) type name; \
    static constexpr auto name##_meta = ps::FieldMeta{__VA_ARGS__};
    
struct EnvSensor {
    PS_FIELD(int16_t, temperature, .mult=0.01, .unit="Cel")
    PS_FIELD(uint8_t, humidity, .mult=0.5, .unit="%RH")
    PS_FIELD(uint16_t, battery_mv, .unit="mV")
    PS_FIELD(uint8_t, status)
};
```

### Phase 3: C++26 Native (Future)

```cpp
// Full reflection with custom attributes
// No macros needed
```

## Comparison

| Approach | Code Gen | Attributes | Runtime | Overhead |
|----------|----------|------------|---------|----------|
| generate-c.py | Required | In YAML | None | Zero |
| Boost.PFR | None | None | Minimal | Near-zero |
| C++20 Macros | None | Via macro | None | Zero |
| C++26 Native | None | Native | None | Zero |

## Files to Create

```
payload-codec-proto/
├── include/
│   ├── ps_reflect.hpp      # Reflection-based codec
│   └── ps_attributes.hpp   # C++20 attribute macros
├── examples/
│   └── env_sensor_reflect.cpp
└── tests/
    └── test_reflect.cpp
```

## References

- [P2996 - Reflection for C++26](https://wg21.link/p2996)
- [Boost.PFR](https://www.boost.org/doc/libs/release/doc/html/boost_pfr.html)
- [refl-cpp](https://github.com/veselink1/refl-cpp)
- [magic_enum](https://github.com/Neargye/magic_enum)
