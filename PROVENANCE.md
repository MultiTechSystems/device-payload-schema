# Provenance and attribution

This repository contains two kinds of material with different origins, and the
distinction matters when reusing either.

## The code

The schema language, the reference interpreters, the code generators, the tools
and the tests are original work of Multi-Tech Systems, Inc., published under the
MIT License in [LICENSE](LICENSE).

## The device schemas

`schemas/devices/` describes **third-party devices**. Each schema was written by
reading its manufacturer's own material — a published JavaScript codec, a
datasheet, an integration guide — and it exists to say the same thing
declaratively.

A test vector records where it came from in a `source:` field — most of them, not
all. `schemas/devices/` holds 241 files: 162 device schemas (121 of them with test
vectors) and 79 that are not devices, the 30 `_language-conformance` fixtures and 49
`_library-composed` files. Their 1535 vectors break down as follows (measured
2026-09-24):

| `source:` | Device schemas | Fixtures and composed | Meaning |
|---|---|---|---|
| `vendor-codec` | 1300 | 0 | Derived from the vendor's published JavaScript codec |
| `vendor-doc` | 63 | 0 | Derived from the vendor's datasheet or integration guide |
| `generated` | 15 | 55 | Produced by the tooling in this repository |
| `spec-example` | 0 | 47 | From a specification's own example |
| *(none)* | 52 | 3 | Not recorded |

The 52 device vectors with no `source:` are in radio-bridge (27), mclimate (11),
digital-matter (7), dragino (5) and elsys (2). Schemas carry no schema-level
`source:` key; 63 device schemas cite the page or document they were read from in a
header comment. Fourteen manufacturers are represented: Arwin, Decentlab, Digital
Matter, DNT, Dragino, Elsys, HBI, Makerfabs, MClimate, Milesight, Netvox, Radio
Bridge, RadioNode and RAKwireless.

### What that means for reuse

A schema states a device's field names, engineering units, value ranges, byte
layout and scaling. That descriptive content originates with the device's
manufacturer, not with Multi-Tech Systems, and the MIT License on this repository
covers the schema language, the interpreters and the tools — not a grant over
material that was never ours to license.

Manufacturer and product names are used **nominatively**: to identify which
device a schema describes. No affiliation, sponsorship or endorsement by any
named manufacturer is claimed or implied, and no schema here is a
vendor-supplied or vendor-approved description of their product. Each
manufacturer's own documentation is authoritative for their device; where a
schema disagrees with it, the manufacturer is right and the schema has a bug —
which is worth an issue.

If you represent a manufacturer named here and want a schema corrected or
removed, please open an issue. Corrections are welcome from anyone, and a test
vector taken from the vendor's own documentation is the most useful form one can
take.

### Test vectors

A vector marked `vendor-codec` is an input/output pair produced by running the
vendor's codec, and one marked `vendor-doc` is an example the vendor published.
They exist to hold this implementation to the same answer the vendor's own
implementation gives, which is what makes a schema checkable rather than merely
plausible. They are not a claim that the vendor endorses this schema.

## Downstream

Artifacts generated from these schemas carry the same distinction. The
[Integration Layer](https://github.com/MultiTechSystems/device-integration-layer)
publishes Thing Models, Modbus maps and BACnet object maps derived from this
corpus, and records the same provenance for them.

## Specifications

The schema language targets LoRaWAN® payload decoding and its code generators
emit TS013-conformant JavaScript. LoRaWAN® and TS013 are LoRa Alliance®
deliverables; no specification text is reproduced here. LoRaWAN® is a mark of the
LoRa Alliance®, used to identify the technology this tooling targets.
