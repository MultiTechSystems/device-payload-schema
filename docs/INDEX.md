# Repository index

<!-- GENERATED FILE - do not edit by hand. Regenerate: python tools/generate_docs_index.py -->

Generated inventory of this repository: what lives where, what each document covers, what each tool does, and the state of every device schema. See [`../AGENTS.md`](../AGENTS.md) for how to work in this repository.

## Repository map

| Path | Contents |
|---|---|
| `bindings/` | Reference interpreters per language (c, go, java, node, python) |
| `build-system/` | Shared make fragments for the C/C++ builds |
| `docs/` | Documentation (this index is generated into it) |
| `dotnet/` | C# interpreter and its test project |
| `examples/` | Small illustrative schemas used by `make validate` and the C tests |
| `fuzz/` | Fuzzing harnesses (Python, Go, C libFuzzer) |
| `go/` | Go interpreter package |
| `include/` | C/C++ headers, including the generated codec headers |
| `output/` | Output-format converters (SenML, IPSO, TTN, WoT) |
| `proto/` | Protocol buffer definitions of the schema language |
| `schemas/` | Schema language JSON Schema, device schemas, shared library |
| `src/` | C/C++ interpreter, self-tests and benchmarks |
| `tests/` | Python test suite (pytest) |
| `tools/` | Python/JS tooling: interpreter, validators, generators, converters |

## Documents

18 documents in `docs/`. Read the one that matches the task; the sections list below tells you what is inside without opening it.

| Document | Purpose | Lines |
|---|---|---|
| [`AUDIT-REPORT.md`](AUDIT-REPORT.md) | Payload Codec Proto - Spec Completeness Audit | 183 |
| [`BIDIRECTIONAL-CODEC.md`](BIDIRECTIONAL-CODEC.md) | The Payload Schema codec is symmetric - both devices and networks use the same encode/decode... | 368 |
| [`C-CODE-GENERATION.md`](C-CODE-GENERATION.md) | Generate standalone C codec headers from Payload Schema YAML files. | 218 |
| [`CODEC-ANALYSIS-NOTES.md`](CODEC-ANALYSIS-NOTES.md) | Analysis of complex codecs from lorawan-devices repository to identify schema language gaps. | 607 |
| [`FAQ.md`](FAQ.md) | A declarative, YAML/JSON-based format for defining the structure of binary LoRaWAN device payloads.... | 448 |
| [`FORMULA-MIGRATION-TRACKING.md`](FORMULA-MIGRATION-TRACKING.md) | Tracking the migration from imperative formulas (JavaScript eval()) to declarative schema... | 466 |
| [`FUTURE-FEATURES.md`](FUTURE-FEATURES.md) | Status of semantic enhancements for the Payload Schema language. | 230 |
| [`GETTING-STARTED.md`](GETTING-STARTED.md) | Create one schema, generate codecs for all platforms. | 364 |
| [`INTEGRATION-LAYER.md`](INTEGRATION-LAYER.md) | How decoded payloads are transformed into WoT Thing Descriptions, SenML, IPSO, and other output... | 562 |
| [`IPSO-REFERENCE.md`](IPSO-REFERENCE.md) | Complete reference for IPSO Smart Objects (OMA LwM2M) used in LoRaWAN payload schemas. | 126 |
| [`LANGUAGE-ANALYSIS.md`](LANGUAGE-ANALYSIS.md) | This document explains the design decisions behind the Payload Schema language. | 333 |
| [`OUTPUT-FORMATS.md`](OUTPUT-FORMATS.md) | The Payload Schema decoder can output data in multiple formats for different platforms and... | 442 |
| [`SCHEMA-DEVELOPMENT-GUIDE.md`](SCHEMA-DEVELOPMENT-GUIDE.md) | Best practices for creating complete, validated payload schemas. | 194 |
| [`SCHEMA-LANGUAGE-REFERENCE.md`](SCHEMA-LANGUAGE-REFERENCE.md) | Complete reference for the LoRa Alliance Payload Schema specification (v0.3.2). | 1059 |
| [`SESSION-NOTES-2026-02-25.md`](SESSION-NOTES-2026-02-25.md) | The prototype tests were using a custom REQ-xxx-yyy numbering scheme that was inconsistent with the... | 78 |
| [`SPEC-IMPLEMENTATION-STATUS.md`](SPEC-IMPLEMENTATION-STATUS.md) | Feature support matrix across reference implementations. | 379 |
| [`TTN-CODEC-CONVERSION-GUIDE.md`](TTN-CODEC-CONVERSION-GUIDE.md) | Complete guide for AI-assisted conversion of The Things Network device repository codecs to Payload... | 548 |
| [`WOT-REFERENCE.md`](WOT-REFERENCE.md) | Reference for mapping LoRaWAN payload schema fields to W3C WoT Thing Descriptions and SAREF... | 316 |

### Document sections

- **AUDIT-REPORT.md** — Summary; Python Interpreter; Go Interpreter; Requirements Traceability; Verification; Changelog
- **BIDIRECTIONAL-CODEC.md** — Device vs Network Responsibilities; Overview; Link Directions; Schema Types; Binary Schema Benefits; C API Reference; Python API Reference; Performance; Modifier Handling; Schema Distribution; Example: Complete Device Implementation; See Also
- **C-CODE-GENERATION.md** — Overview; Design Decision: Raw Values Only; Usage; Generated Code Structure; Device Usage Example; Return Values; Dependencies; Generated vs Runtime Interpreter; Feature Support; Example Workflow; Batch Generation
- **CODEC-ANALYSIS-NOTES.md** — 1. Digital Matter Oyster (GPS Tracker); 2. Dragino LAQ4 (Air Quality Sensor); 3. Sensative Strips (Multi-Sensor); 4. Decentlab DL-5TM (Soil Sensor); 5. Tektelic Agriculture (Data-Driven TLV); Summary: Schema Language Gaps
- **FAQ.md** — Architecture and Layers; General; Schema Creation; Sensor Library; Interpreters; Code Generation; Data Types; Complex Structures; Migration; Troubleshooting; Performance
- **FORMULA-MIGRATION-TRACKING.md** — Why Migrate?; Migration Patterns; Migration Statistics; Constructs Coverage; Remaining Gaps; Best Practices for New Schemas; Tooling Support; Conclusion
- **FUTURE-FEATURES.md** — Implemented Features; Planned Features; Out of Scope: Device Profile Metadata; Implementation Notes; Status Summary; Related Standards
- **GETTING-STARTED.md** — What You Get; 5-Minute Example; Using the Sensor Library; Common Patterns; Adding Semantic Annotations; Test Vector Best Practices; Directory Structure; Next Steps; Getting Help
- **INTEGRATION-LAYER.md** — The Problem; Two-Layer Architecture; Decoder Output: The Integration Boundary; Integration Profile; Protocol Converters; Format Details; Context Layering; Where the Integration Layer Runs; Relationship to TS013; What Belongs Where; Version History
- **IPSO-REFERENCE.md** — Common Sensor Objects (3300-3350); Usage in Schema; Adding New IPSO Objects; Keyword Detection; Complex Codec Examples (from TTN lorawan-devices); Version History
- **LANGUAGE-ANALYSIS.md** — Design Goals; Type System Design; Conditional Parsing Design; Arithmetic Pipeline Design; Binary Format Design; Feature Exclusions; Compatibility Considerations; Future Considerations; Summary
- **OUTPUT-FORMATS.md** — Example; 1. Raw Format (Default); 2. IPSO Smart Objects Format; 3. SenML Format (RFC 8428); 4. TTN Normalized Format; Format Comparison; Schema Definition; API Usage; Output JSON Schema; Format-Specific JSON Schemas
- **SCHEMA-DEVELOPMENT-GUIDE.md** — Overview; Process: Converting an Existing Codec; Message Types; Edge Cases; Common Pitfalls; Tools; Checklist: Before Declaring "Complete"; Example: MClimate Vicki
- **SCHEMA-LANGUAGE-REFERENCE.md** — Document Structure; Field Types; Arithmetic Modifiers; Lookup Tables; Computed Fields; Transform Operations; Conditional Parsing; Named Encodings; Value-Range Matching; Bitfield String; Test Vectors; Enum Type ...
- **SESSION-NOTES-2026-02-25.md** — Requirement Numbering Alignment
- **SPEC-IMPLEMENTATION-STATUS.md** — Quick Summary; Detailed Feature Matrix; Implementation Notes; Test Coverage; Version Compatibility; Performance Benchmarks; Roadmap
- **TTN-CODEC-CONVERSION-GUIDE.md** — Overview; Prerequisites; Conversion Workflow; Test Vector Guidelines; Quality Tiers; Common Conversion Patterns; AI-Assisted Workflow; Troubleshooting; Output Directory Structure; Resources
- **WOT-REFERENCE.md** — Architecture; Thing Description Structure; SAREF Ontology Mapping; WoT Unit Codes; Integration Profile WoT Section; Mapping from IPSO to SAREF (Automated); Example: Full TD from Schema; Relationship to Other Output Formats; Version History

## Tools

| Script | Purpose |
|---|---|
| `tools/analyze-proto.py` | Comprehensive protobuf file analysis tool |
| `tools/analyze_codec.js` | Load and execute codec in sandbox |
| `tools/analyze_ttn_codec.py` | TTN Codec Analyzer |
| `tools/batch_analyze_codecs.py` | Batch analyze TTN Device Repository codecs. |
| `tools/benchmark_all.py` | Comprehensive codec benchmark |
| `tools/benchmark_codecs.js` | Default test configuration |
| `tools/binary_schema.py` | Binary Schema Encoder/Decoder for OTA Schema Transfer |
| `tools/binary_schema_loader.py` | Load binary schemas for fast interpretation |
| `tools/binary_schema_v2.py` | Extended Binary Schema Encoder/Decoder |
| `tools/convert_decentlab.py` | Decentlab Protocol V2 Codec → Payload Schema Schema Converter |
| `tools/convert_milesight.py` | Milesight IoT Codec → Payload Schema Schema Converter |
| `tools/crossvalidate_decentlab.py` | Check decentlab schemas against the vendor's decoders. |
| `tools/crossvalidate_ttn.py` | Check schemas against The Things Network device repository. |
| `tools/fuzz_decoder.py` | Fuzz test the schema interpreter |
| `tools/generate-c.py` | Generate C codec from Payload Schema YAML |
| `tools/generate_codec.py` | Generate C codec AND unit tests from Payload Schema |
| `tools/generate_deliverables.py` | Generate all TS013 deliverables from Payload Schema YAML |
| `tools/generate_docs_index.py` | Generate docs/INDEX.md (this file) |
| `tools/generate_firmware_codec.py` | Generate firmware C codec from Payload Schema YAML. |
| `tools/generate_js_decoder.py` | Generate standalone TTN-compatible JavaScript decoders from Payload Schema YAML. |
| `tools/generate_jsonschema.py` | Generate JSON Schema for Payload Schema validation |
| `tools/generate_output_schema.py` | Generate JSON Schema for device codec output. |
| `tools/generate_ts013_codec.py` | Generate TS013-compliant JavaScript codec from Payload Schema YAML. |
| `tools/payload_size_calc.py` | Calculate payload sizes for all flag/port combinations. |
| `tools/qr_schema.py` | QR Code Schema Embedding Utilities |
| `tools/schema_base64.py` | Encode/decode Payload Schemas to/from base64 |
| `tools/schema_binary.py` | Compact Binary Schema Encoder/Decoder |
| `tools/schema_interpreter.py` | Runtime Schema Interpreter for Payload Decoding |
| `tools/schema_preprocessor.py` | Schema Preprocessor - Resolves cross-file $ref references. |
| `tools/score_schema.py` | Quality scoring tool for payload schemas. |
| `tools/validate_schema.py` | Validate schema and run test vectors |
| `tools/verify_spec_completeness.py` | Requirements traceability and spec completeness verification. |

## Device schemas

158 schemas under `schemas/devices/`.
Mean quality score 52.0% (PLATINUM 14, GOLD 8, SILVER 58, BRONZE 5, REJECTED 73). Tiers follow the specification's Section 10: Platinum 95-100%, Gold 85-94%, Silver 70-84%, Bronze 60-69%, Rejected below 60%. Gold and Platinum also have gates (PS-239) -- see `../AGENTS.md`. A high score shows self-consistency with a schema's own test vectors, not that the vectors are right.

### By vendor

| Vendor | Schemas | Vectors | Tiers |
|---|---|---|---|
| milesight | 84 | 677 | SILVER 57, BRONZE 4, REJECTED 23 |
| decentlab | 58 | 120 | PLATINUM 12, GOLD 7, SILVER 1, REJECTED 38 |
| makerfabs | 6 | 0 | REJECTED 6 |
| mclimate | 3 | 11 | GOLD 1, REJECTED 2 |
| digital-matter | 1 | 7 | PLATINUM 1 |
| dragino | 1 | 5 | PLATINUM 1 |
| elsys | 1 | 2 | REJECTED 1 |
| hbi | 1 | 0 | REJECTED 1 |
| radio-bridge | 1 | 28 | BRONZE 1 |
| radionode | 1 | 0 | REJECTED 1 |
| rakwireless | 1 | 0 | REJECTED 1 |

### By device

`vectors` is the number of `test_vectors` entries: 0 means the schema has never been verified against a known payload.

| Schema | Fields | Vectors | Constructs | Score | Tier |
|---|---|---|---|---|---|
| `decentlab/dl-5tm` | 10 | 7 | fields | 100% | PLATINUM |
| `decentlab/dl-alb` | 11 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-atm22` | 15 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-atm41` | 27 | 6 | fields | 92% | GOLD |
| `decentlab/dl-atm41g2` | 20 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-blg` | 7 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-ctd10` | 10 | 6 | fields | 89% | GOLD |
| `decentlab/dl-cws` | 10 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-cws2` | 12 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-dlr2-002` | 7 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-dlr2-003` | 6 | 5 | fields | 100% | PLATINUM |
| `decentlab/dl-dlr2-004-10` | 5 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-dlr2-005` | 5 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-dlr2-006` | 5 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-dlr2-008-2000` | 5 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-dlr2-009-2000` | 6 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-dlr2-010` | 10 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-dlr2-011` | 7 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-dlr2-012` | 5 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-ds18` | 7 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-dws-232263168-0000302459-1370` | 6 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-gmm` | 15 | 6 | fields | 92% | GOLD |
| `decentlab/dl-iam` | 15 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-ifd` | 6 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-ilt` | 7 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-isd` | 6 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-isf` | 19 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-itst` | 6 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-kl66-1538372-464859` | 10 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-lid` | 16 | 6 | fields | 80% | SILVER |
| `decentlab/dl-lp8p` | 16 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-lpw` | 5 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-lws` | 5 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-mbx` | 7 | 6 | fields | 89% | GOLD |
| `decentlab/dl-mes5` | 11 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-ntu` | 11 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-optod` | 12 | 6 | fields | 94% | GOLD |
| `decentlab/dl-par` | 5 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-pheht` | 11 | 6 | fields | 89% | GOLD |
| `decentlab/dl-pm` | 17 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-pr21-1-10` | 6 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-pr26-0-1` | 6 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-pr36-8192` | 6 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-pr36ctd-8192-1024` | 8 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-pyr` | 5 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-rad` | 10 | 6 | fields | 92% | GOLD |
| `decentlab/dl-rhc` | 7 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-sdd` | 40 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-sht35` | 6 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-smtp` | 20 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-tbrg-01` | 7 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-tp` | 37 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-trs11` | 7 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-trs12` | 8 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-trs21` | 6 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-wrm` | 8 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-zn1` | 5 | 0 | fields | 16% | REJECTED |
| `decentlab/dl-zn2` | 6 | 0 | fields | 16% | REJECTED |
| `digital-matter/oyster` | 24 | 7 | ports | 100% | PLATINUM |
| `dragino/laq4` | 14 | 5 | fields | 100% | PLATINUM |
| `elsys/ers` | 32 | 2 | fields | 56% | REJECTED |
| `hbi/mla20` | 18 | 0 | fields | 16% | REJECTED |
| `makerfabs/4-channel-adc` | 8 | 0 | fields | 16% | REJECTED |
| `makerfabs/ath20` | 4 | 0 | fields | 14% | REJECTED |
| `makerfabs/gps-tracker` | 16 | 0 | fields | 16% | REJECTED |
| `makerfabs/leaf-moisture-sn-3001` | 6 | 0 | fields | 14% | REJECTED |
| `makerfabs/pipe-pressure` | 3 | 0 | fields | 16% | REJECTED |
| `makerfabs/soil-monitor` | 8 | 0 | fields | 14% | REJECTED |
| `mclimate/flood-sensor` | 5 | 0 | fields | 16% | REJECTED |
| `mclimate/t-valve` | 11 | 0 | fields | 16% | REJECTED |
| `mclimate/vicki` | 31 | 11 | fields | 85% | GOLD |
| `milesight/am102` | 13 | 10 | fields | 80% | SILVER |
| `milesight/am102l` | 13 | 10 | fields | 80% | SILVER |
| `milesight/am103` | 12 | 15 | fields | 76% | SILVER |
| `milesight/am103l` | 12 | 15 | fields | 76% | SILVER |
| `milesight/am104` | 9 | 15 | fields | 80% | SILVER |
| `milesight/am107` | 12 | 15 | fields | 76% | SILVER |
| `milesight/am307` | 15 | 15 | fields | 76% | SILVER |
| `milesight/am307l` | 15 | 15 | fields | 76% | SILVER |
| `milesight/am308` | 16 | 15 | fields | 76% | SILVER |
| `milesight/am308l` | 16 | 15 | fields | 76% | SILVER |
| `milesight/am319` | 22 | 15 | fields | 76% | SILVER |
| `milesight/am319l` | 22 | 15 | fields | 76% | SILVER |
| `milesight/at101-fh` | 22 | 8 | fields | 80% | SILVER |
| `milesight/at101` | 16 | 6 | fields | 80% | SILVER |
| `milesight/ct101` | 19 | 0 | fields | 14% | REJECTED |
| `milesight/ct103` | 19 | 0 | fields | 14% | REJECTED |
| `milesight/ct105` | 19 | 0 | fields | 14% | REJECTED |
| `milesight/ct303` | 13 | 0 | fields | 14% | REJECTED |
| `milesight/ct305` | 13 | 0 | fields | 14% | REJECTED |
| `milesight/ct310` | 13 | 0 | fields | 14% | REJECTED |
| `milesight/em300-di` | 7 | 15 | fields | 80% | SILVER |
| `milesight/em300-mcs` | 14 | 9 | fields | 80% | SILVER |
| `milesight/em300-mld` | 4 | 6 | fields | 80% | SILVER |
| `milesight/em300-sld` | 6 | 9 | fields | 80% | SILVER |
| `milesight/em300-th` | 5 | 10 | fields | 80% | SILVER |
| `milesight/em300-zld` | 6 | 9 | fields | 80% | SILVER |
| `milesight/em310-tilt` | 17 | 0 | fields | 14% | REJECTED |
| `milesight/em310-udl` | 13 | 6 | fields | 80% | SILVER |
| `milesight/em320-th` | 5 | 10 | fields | 80% | SILVER |
| `milesight/em320-tilt` | 9 | 0 | fields | 14% | REJECTED |
| `milesight/em400-mud` | 10 | 13 | fields | 65% | BRONZE |
| `milesight/em400-tld` | 10 | 13 | fields | 65% | BRONZE |
| `milesight/em400-udl` | 10 | 13 | fields | 65% | BRONZE |
| `milesight/em410-rdl` | 15 | 12 | fields | 80% | SILVER |
| `milesight/em500-co2` | 7 | 0 | fields | 14% | REJECTED |
| `milesight/em500-lgt` | 4 | 7 | fields | 80% | SILVER |
| `milesight/em500-pp` | 4 | 8 | fields | 80% | SILVER |
| `milesight/em500-pt100` | 4 | 7 | fields | 80% | SILVER |
| `milesight/em500-smt` | 5 | 9 | fields | 80% | SILVER |
| `milesight/em500-smtc` | 7 | 15 | fields | 80% | SILVER |
| `milesight/em500-swl` | 4 | 7 | fields | 80% | SILVER |
| `milesight/em500-udl` | 4 | 0 | fields | 14% | REJECTED |
| `milesight/gs101` | 7 | 0 | fields | 16% | REJECTED |
| `milesight/gs301` | 20 | 12 | fields | 80% | SILVER |
| `milesight/ts101` | 10 | 7 | fields | 80% | SILVER |
| `milesight/ts201` | 12 | 6 | fields | 80% | SILVER |
| `milesight/ts201v2` | 14 | 9 | fields | 80% | SILVER |
| `milesight/ts30x` | 25 | 10 | fields | 80% | SILVER |
| `milesight/uc100` | 7 | 0 | fields | 16% | REJECTED |
| `milesight/uc1114` | 4 | 0 | fields | 16% | REJECTED |
| `milesight/uc1152` | 3 | 0 | fields | 16% | REJECTED |
| `milesight/uc300` | 19 | 15 | fields | 76% | SILVER |
| `milesight/uc50x` | 11 | 0 | fields | 16% | REJECTED |
| `milesight/uc51x` | 20 | 12 | fields | 80% | SILVER |
| `milesight/vs121` | 5 | 0 | fields | 16% | REJECTED |
| `milesight/vs135` | 15 | 6 | fields | 80% | SILVER |
| `milesight/vs321` | 21 | 9 | fields | 78% | SILVER |
| `milesight/vs330` | 6 | 9 | fields | 80% | SILVER |
| `milesight/vs340` | 4 | 5 | fields | 80% | SILVER |
| `milesight/vs341` | 4 | 5 | fields | 80% | SILVER |
| `milesight/vs350` | 25 | 15 | fields | 80% | SILVER |
| `milesight/vs351` | 26 | 15 | fields | 80% | SILVER |
| `milesight/vs360` | 23 | 15 | fields | 80% | SILVER |
| `milesight/vs370` | 13 | 0 | fields | 16% | REJECTED |
| `milesight/vs373` | 33 | 5 | fields | 80% | SILVER |
| `milesight/ws101` | 4 | 0 | fields | 16% | REJECTED |
| `milesight/ws136` | 3 | 0 | fields | 16% | REJECTED |
| `milesight/ws156` | 3 | 0 | fields | 16% | REJECTED |
| `milesight/ws201` | 5 | 10 | fields | 80% | SILVER |
| `milesight/ws202` | 5 | 6 | fields | 80% | SILVER |
| `milesight/ws203` | 8 | 12 | fields | 80% | SILVER |
| `milesight/ws301` | 5 | 6 | fields | 80% | SILVER |
| `milesight/ws302` | 9 | 0 | fields | 16% | REJECTED |
| `milesight/ws303` | 4 | 0 | fields | 16% | REJECTED |
| `milesight/ws50x` | 17 | 0 | fields | 16% | REJECTED |
| `milesight/ws515` | 22 | 15 | fields | 78% | SILVER |
| `milesight/ws52x` | 16 | 15 | fields | 65% | BRONZE |
| `milesight/ws558` | 7 | 15 | fields | 80% | SILVER |
| `milesight/wt101` | 22 | 15 | fields | 80% | SILVER |
| `milesight/wt201v1` | 26 | 9 | fields | 80% | SILVER |
| `milesight/wt201v2` | 27 | 12 | fields | 80% | SILVER |
| `milesight/wts305` | 10 | 15 | fields | 76% | SILVER |
| `milesight/wts505` | 10 | 15 | fields | 76% | SILVER |
| `milesight/wts506` | 10 | 15 | fields | 76% | SILVER |
| `radio-bridge/rbs30x` | 52 | 28 | fields | 68% | BRONZE |
| `radionode/rn320bth` | 13 | 0 | fields | 14% | REJECTED |
| `rakwireless/qingping` | 9 | 0 | fields | 16% | REJECTED |

## Other schema files

| Path | Role |
|---|---|
| `schemas/ipso-output.schema.json` | JSON Schema for IPSO-shaped decoder output |
| `schemas/payload-schema.json` | JSON Schema for the payload schema language itself |
| `schemas/senml-output.schema.json` | JSON Schema for SenML-shaped decoder output |
| `schemas/ttn-output.schema.json` | JSON Schema for TTN-shaped decoder output |
| `schemas/library/` | 34 reusable schema fragments (`use:` targets) |
