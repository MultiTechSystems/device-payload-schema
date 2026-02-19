# TS013-1.0.0 Compliance Analysis: lorawan-devices Repository

## Overview

This document analyzes the compliance of devices in The Things Network's `lorawan-devices` repository against the LoRaWAN Payload Codec API Specification (TS013-1.0.0).

## TS013 Required Deliverables

Per TS013-1.0.0, a compliant payload codec **must** include:

| Deliverable | Description | Required |
|-------------|-------------|----------|
| `index.js` | JavaScript codec with `decodeUplink()` function | **Yes** |
| `metadata.json` | Metadata about the codec (name, version, etc.) | **Yes** |
| `examples.json` | Test examples with input/output pairs | **Yes** |

## TS013 Recommended (Optional) Deliverables

| Deliverable | Description |
|-------------|-------------|
| `encodeDownlink()` | Function to encode downlink commands |
| `decodeDownlink()` | Function to decode downlink commands |
| `uplink.schema.json` | JSON Schema for uplink output validation |
| `downlink.schema.json` | JSON Schema for downlink input/output validation |
| `normalizedOutput` | Normalized payload data in examples (standard measurement types) |

## Repository Structure vs TS013 Specification

The `lorawan-devices` repository uses a **different structure** than what TS013 specifies:

| TS013 Spec | lorawan-devices Repo |
|------------|---------------------|
| `index.js` | `<device>.js` or `<device>-codec.js` |
| `metadata.json` | Embedded in `*-codec.yaml` |
| `examples.json` | Embedded in `*-codec.yaml` under `examples:` |
| Separate JSON files | Single YAML file per device |

**Key Differences:**
- Uses **YAML** codec files (`*-codec.yaml`) instead of separate JSON files
- Examples are embedded inline in the YAML, not in separate `examples.json` files
- No separate `metadata.json` files exist in vendor directories
- No JSON Schema files (`*.schema.json`) in vendor directories

## Compliance Statistics

| Feature | Approximate Count | Notes |
|---------|-------------------|-------|
| `decodeUplink` | 800+ devices | Most codecs have this (REQUIRED function) |
| `encodeDownlink` | ~200+ devices | Recommended function |
| `decodeDownlink` | ~200+ devices | Recommended function |
| `normalizedOutput` | ~35 devices | Very few implement normalized output |
| JSON Schemas | 0 | No vendor-specific schemas found |

## Devices with All Three Codec Functions

The following device categories have **decodeUplink + encodeDownlink + decodeDownlink** with examples:

### Complete Implementations

| Vendor | Device | Notes |
|--------|--------|-------|
| the-things-products | the-things-uno-quickstart | Reference implementation, all 3 functions |
| netvox | rp02 (and many others) | Extensive examples for all functions |
| tektelic | Multiple models (t0007xxx, etc.) | Comprehensive codec support |
| strega | smart-valve, motorized-valve, etc. | Industrial valve controllers |
| nexelec | Multiple models | Air quality sensors |
| nke-watteco | Multiple models | Industrial sensors |
| milesight-iot | Multiple models | IoT sensors |
| pepperl-fuchs | wilsen-* series | Industrial sensors |

## Devices with normalizedOutput (Closest to Full TS013)

Only ~35 devices implement `normalizedOutput` in their examples, which provides standardized measurement types. These are the closest to full TS013 compliance:

| Vendor | Device | Normalized Data Types |
|--------|--------|----------------------|
| the-things-industries | generic-node-sensor-edition | temperature, humidity, battery |
| tektelic | t00048xx, t00061xx | Multiple sensor types |
| dragino | lht65, lht52, lsn50-v2, lse01, lwl03a | Temperature, humidity, soil |
| elsys | ers, ers-co2, ems-desk | Environmental sensors |
| sensative | strips | Door/window, temperature |
| quandify | cubicmeter-1-1-* | Water metering |
| mclimate | vicki | HVAC/thermostatic valve |
| makerfabs | ath20 | Temperature, humidity |
| laird/ezurio | rs1xx-temp-rh-sensor | Temperature, humidity |
| koidra | sdi-12-* | Agricultural sensors |
| fludia | fm432* series | Energy metering |
| browan | tbms100, tbdw100 | Motion, door/window |
| moko | lw001-bgpro | GPS tracker |
| milesight-iot | ws301, em300-th | Door sensor, temp/humidity |
| example | windsensor | Reference example |

## Compliance Summary

### No Devices Fully Meet TS013 As Written

The repository does not strictly follow TS013 file structure because:

1. **No separate `metadata.json`** - Metadata is embedded in YAML
2. **No separate `examples.json`** - Examples are embedded in YAML
3. **No JSON Schemas** - `uplink.schema.json` / `downlink.schema.json` are absent
4. **Repository predates TS013** - Uses TTN-specific structure

### Compliance Levels

| Level | Criteria | Device Count |
|-------|----------|--------------|
| **Minimal** | `decodeUplink` only | ~600+ |
| **Basic** | `decodeUplink` + examples | ~800+ |
| **Good** | All 3 functions + examples | ~200+ |
| **Best Available** | All 3 functions + `normalizedOutput` | ~35 |
| **Full TS013** | Separate files + schemas | **0** |

## Recommendations

For new device submissions targeting TS013 compliance:

1. **Implement all three codec functions**: `decodeUplink`, `encodeDownlink`, `decodeDownlink`
2. **Include `normalizedOutput`** in examples using standard measurement types from `lib/payload.json`
3. **Provide comprehensive examples** covering all message types and edge cases
4. **Consider JSON Schema** validation for complex payloads

## Reference Files

- Repository schema: `lorawan-devices/schema.json`
- Normalized payload schema: `lorawan-devices/lib/payload.json`
- Example reference: `lorawan-devices/vendor/example/windsensor-codec.yaml`

---

*Analysis performed: February 2026*
*Based on: lorawan-devices repository and TS013-1.0.0 specification*
