# Manually Written Schemas

These schemas were manually converted from TTN Device Repository JavaScript codecs.

| Schema | Vendor | Pattern | Status |
|--------|--------|---------|--------|
| elsys.yaml | Elsys | TLV (single-byte tag) | Complete |
| mclimate-flood-sensor.yaml | MClimate | Fixed bitfield | Complete |
| mclimate-t-valve.yaml | MClimate | Fixed bitfield | Complete |
| makerfabs-gps-tracker.yaml | Makerfabs | Fixed position | Complete |
| hbi-mla20.yaml | HBI | Version header + TLV | Partial (status packet only) |

## Not Convertible (Remaining)

| Codec | Vendor | Reason |
|-------|--------|--------|
| nexelec/carbon-codec-v1.js | Nexelec | Nibble-level bitstream with message type dispatch (464 lines) |
| nexelec/sign-codec.js | Nexelec | Similar nibble parser, multiple message types (601 lines) |
| sensecap/sensecapt2000-tracker-abc-decoder.js | SenseCap | Custom hex frame protocol with deserialize functions (704 lines) |
| sensecap/sensecapt1000-tracker-ab-decoder.js | SenseCap | Similar custom frame protocol (813 lines) |
| mclimate/vicki.js | MClimate | Complex thermostatic valve state machine (410 lines) |
| tip/sinus85.js | TIP | Hex string preprocessing + multi-function decode (126 lines) |

These codecs use patterns beyond what a declarative schema can express and should
remain as handwritten JavaScript.
