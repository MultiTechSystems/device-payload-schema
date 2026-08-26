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
| [`BIDIRECTIONAL-CODEC.md`](BIDIRECTIONAL-CODEC.md) | The Payload Schema codec is symmetric - both devices and networks use the same encode/decode... | 369 |
| [`C-CODE-GENERATION.md`](C-CODE-GENERATION.md) | Generate standalone C codec headers from Payload Schema YAML files. | 218 |
| [`CODEC-ANALYSIS-NOTES.md`](CODEC-ANALYSIS-NOTES.md) | Analysis of complex codecs from lorawan-devices repository to identify schema language gaps. | 607 |
| [`FAQ.md`](FAQ.md) | A declarative, YAML/JSON-based format for defining the structure of binary LoRaWAN device payloads.... | 448 |
| [`FORMULA-MIGRATION-TRACKING.md`](FORMULA-MIGRATION-TRACKING.md) | Tracking the migration from imperative formulas (JavaScript eval()) to declarative schema... | 466 |
| [`FUTURE-FEATURES.md`](FUTURE-FEATURES.md) | Status of semantic enhancements for the Payload Schema language. | 230 |
| [`GETTING-STARTED.md`](GETTING-STARTED.md) | Create one schema, generate codecs for all platforms. | 364 |
| [`INTEGRATION-LAYER.md`](INTEGRATION-LAYER.md) | How decoded payloads are transformed into WoT Thing Descriptions, SenML, IPSO, and other output... | 562 |
| [`IPSO-REFERENCE.md`](IPSO-REFERENCE.md) | Complete reference for IPSO Smart Objects (OMA LwM2M) used in LoRaWAN payload schemas. | 126 |
| [`LANGUAGE-ANALYSIS.md`](LANGUAGE-ANALYSIS.md) | This document explains the design decisions behind the Payload Schema language. | 340 |
| [`OUTPUT-FORMATS.md`](OUTPUT-FORMATS.md) | The Payload Schema decoder can output data in multiple formats for different platforms and... | 442 |
| [`SCHEMA-DEVELOPMENT-GUIDE.md`](SCHEMA-DEVELOPMENT-GUIDE.md) | Best practices for creating complete, validated payload schemas. | 194 |
| [`SCHEMA-LANGUAGE-REFERENCE.md`](SCHEMA-LANGUAGE-REFERENCE.md) | Complete reference for the LoRa Alliance Payload Schema specification (v0.5.0). | 1152 |
| [`SESSION-NOTES-2026-02-25.md`](SESSION-NOTES-2026-02-25.md) | The prototype tests were using a custom REQ-xxx-yyy numbering scheme that was inconsistent with the... | 78 |
| [`SPEC-IMPLEMENTATION-STATUS.md`](SPEC-IMPLEMENTATION-STATUS.md) | Feature support matrix across reference implementations. | 457 |
| [`TTN-CODEC-CONVERSION-GUIDE.md`](TTN-CODEC-CONVERSION-GUIDE.md) | Complete guide for AI-assisted conversion of The Things Network device repository codecs to Payload... | 549 |
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
| `tools/benchmark-c-interpreter.py` | Benchmark the C interpreter against the Python reference on one corpus schema. |
| `tools/benchmark_all.py` | Comprehensive codec benchmark |
| `tools/benchmark_codecs.js` | Default test configuration |
| `tools/binary_schema.py` | Binary Schema Encoder/Decoder for OTA Schema Transfer |
| `tools/binary_schema_loader.py` | Load binary schemas for fast interpretation |
| `tools/binary_schema_v2.py` | Extended Binary Schema Encoder/Decoder |
| `tools/c-corpus-harness.py` | Run corpus vectors through the C interpreter, and say what it cannot reach. |
| `tools/compose_library_vectors.py` | Make the schema library's test vectors runnable. |
| `tools/convert_decentlab.py` | Decentlab Protocol V2 Codec → Payload Schema Schema Converter |
| `tools/convert_milesight.py` | Milesight IoT Codec → Payload Schema Schema Converter |
| `tools/crossvalidate_decentlab.py` | Check decentlab schemas against the vendor's decoders. |
| `tools/crossvalidate_js_json.py` | diff interpreter JSON against the generated TS013 codec. |
| `tools/crossvalidate_ttn.py` | Check schemas against The Things Network device repository. |
| `tools/encode-round-trip.py` | Every corpus vector through `encode(decode(payload))`, with a reason where it differs. |
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
| `tools/vector-verdicts.py` | Execute every corpus test vector through both conformance paths and record a verdict. |
| `tools/verify_spec_completeness.py` | Requirements traceability and spec completeness verification. |

## Device schemas

234 schemas under `schemas/devices/`.
Mean quality score 69.2% (PLATINUM 30, GOLD 40, SILVER 100, BRONZE 18, REJECTED 46). Tiers follow the specification's Section 10: Platinum 95-100%, Gold 85-94%, Silver 70-84%, Bronze 60-69%, Rejected below 60%. Gold and Platinum also have gates (PS-239) -- see `../AGENTS.md`. A high score shows self-consistency with a schema's own test vectors, not that the vectors are right.

### By vendor

| Vendor | Schemas | Vectors | Tiers |
|---|---|---|---|
| milesight | 84 | 948 | PLATINUM 18, GOLD 33, SILVER 22, REJECTED 11 |
| decentlab | 58 | 136 | PLATINUM 12, GOLD 7, SILVER 1, BRONZE 14, REJECTED 24 |
| _library-composed | 49 | 57 | SILVER 48, BRONZE 1 |
| _language-conformance | 27 | 44 | SILVER 23, BRONZE 2, REJECTED 2 |
| makerfabs | 6 | 0 | REJECTED 6 |
| mclimate | 3 | 12 | SILVER 1, REJECTED 2 |
| digital-matter | 1 | 7 | SILVER 1 |
| dragino | 1 | 5 | SILVER 1 |
| elsys | 1 | 2 | SILVER 1 |
| hbi | 1 | 12 | SILVER 1 |
| radio-bridge | 1 | 29 | BRONZE 1 |
| radionode | 1 | 0 | REJECTED 1 |
| rakwireless | 1 | 1 | SILVER 1 |

### By device

`vectors` is the number of `test_vectors` entries: 0 means the schema has never been verified against a known payload.

| Schema | Fields | Vectors | Constructs | Score | Tier |
|---|---|---|---|---|---|
| `_language-conformance/bitfield-string` | 1 | 1 | fields | 71% | SILVER |
| `_language-conformance/compute-negative-idiv-mod` | 5 | 8 | fields | 76% | SILVER |
| `_language-conformance/encode-padding` | 3 | 1 | fields | 71% | SILVER |
| `_language-conformance/enum-spec-default` | 2 | 1 | fields | 71% | SILVER |
| `_language-conformance/lookup-default` | 1 | 1 | fields | 71% | SILVER |
| `_language-conformance/match-case-range` | 3 | 3 | fields | 67% | BRONZE |
| `_language-conformance/match-cases-default-key` | 3 | 2 | fields | 65% | BRONZE |
| `_language-conformance/match-default-fields` | 3 | 2 | fields | 71% | SILVER |
| `_language-conformance/match-default-skip` | 2 | 2 | fields | 71% | SILVER |
| `_language-conformance/match-inline-discriminator` | 2 | 1 | fields | 59% | REJECTED |
| `_language-conformance/match-var` | 2 | 1 | fields | 57% | REJECTED |
| `_language-conformance/metadata-enrichment` | 2 | 3 | fields | 75% | SILVER |
| `_language-conformance/name-from-var` | 2 | 2 | fields | 71% | SILVER |
| `_language-conformance/name-from` | 2 | 1 | fields | 71% | SILVER |
| `_language-conformance/ref-header` | 1 | 1 | fields | 71% | SILVER |
| `_language-conformance/repeat-byte-length-span` | 4 | 1 | fields | 73% | SILVER |
| `_language-conformance/repeat-byte-length` | 4 | 1 | fields | 73% | SILVER |
| `_language-conformance/repeat-count` | 3 | 1 | fields | 71% | SILVER |
| `_language-conformance/repeat-max-count` | 3 | 1 | fields | 71% | SILVER |
| `_language-conformance/repeat-max` | 2 | 2 | fields | 71% | SILVER |
| `_language-conformance/round-half-to-even` | 5 | 1 | fields | 71% | SILVER |
| `_language-conformance/skip-type` | 3 | 1 | fields | 71% | SILVER |
| `_language-conformance/tlv-nameless-case` | 2 | 1 | fields | 71% | SILVER |
| `_language-conformance/transform-maths` | 6 | 1 | fields | 71% | SILVER |
| `_language-conformance/unknown-tlv-tag-raw` | 1 | 1 | fields | 71% | SILVER |
| `_language-conformance/unknown-tlv-tag-skip-delimited` | 2 | 1 | fields | 73% | SILVER |
| `_language-conformance/unknown-tlv-tag-skip` | 2 | 2 | fields | 71% | SILVER |
| `_library-composed/alarm_config__set_delta_threshold` | 4 | 2 | fields | 71% | SILVER |
| `_library-composed/alarm_config__set_temp_alarm` | 5 | 2 | fields | 71% | SILVER |
| `_library-composed/data_logging__clear_log` | 3 | 2 | fields | 71% | SILVER |
| `_library-composed/data_logging__enable_logging` | 4 | 2 | fields | 73% | SILVER |
| `_library-composed/data_logging__fetch_all` | 4 | 2 | fields | 73% | SILVER |
| `_library-composed/device_management__factory_reset` | 3 | 2 | fields | 73% | SILVER |
| `_library-composed/device_management__identify_10sec` | 3 | 2 | fields | 71% | SILVER |
| `_library-composed/device_management__reboot_immediate` | 3 | 2 | fields | 73% | SILVER |
| `_library-composed/gps_tracker__clear_all_geofences` | 3 | 1 | fields | 73% | SILVER |
| `_library-composed/gps_tracker__request_position` | 3 | 1 | fields | 71% | SILVER |
| `_library-composed/gps_tracker__set_geofence` | 6 | 1 | fields | 75% | SILVER |
| `_library-composed/lorawan_frames__fctrl_uplink_adr_ack` | 5 | 1 | fields | 73% | SILVER |
| `_library-composed/lorawan_frames__join_request` | 3 | 1 | fields | 71% | SILVER |
| `_library-composed/lorawan_frames__mhdr_confirmed_down` | 3 | 1 | fields | 73% | SILVER |
| `_library-composed/lorawan_frames__mhdr_unconfirmed_up` | 3 | 1 | fields | 73% | SILVER |
| `_library-composed/lorawan_mac_commands__dev_status_ans` | 3 | 1 | fields | 71% | SILVER |
| `_library-composed/lorawan_mac_commands__device_time_ans` | 3 | 1 | fields | 71% | SILVER |
| `_library-composed/lorawan_mac_commands__link_adr_req_dr5_pwr2` | 4 | 1 | fields | 73% | SILVER |
| `_library-composed/lorawan_mac_commands__link_check_ans` | 3 | 1 | fields | 71% | SILVER |
| `_library-composed/lorawan_mac_commands__link_check_req` | 1 | 1 | fields | 71% | SILVER |
| `_library-composed/sensor_config__read_all_sensors` | 3 | 1 | fields | 73% | SILVER |
| `_library-composed/sensor_config__set_interval_5min` | 3 | 1 | fields | 73% | SILVER |
| `_library-composed/sensor_config__set_temp_calibration` | 4 | 1 | fields | 73% | SILVER |
| `_library-composed/ts003_clock_sync__app_time_ans_negative_correction` | 3 | 1 | fields | 73% | SILVER |
| `_library-composed/ts003_clock_sync__app_time_ans_positive_correction` | 3 | 1 | fields | 69% | BRONZE |
| `_library-composed/ts003_clock_sync__app_time_req_with_ans_required` | 3 | 1 | fields | 71% | SILVER |
| `_library-composed/ts003_clock_sync__force_resync` | 1 | 1 | fields | 71% | SILVER |
| `_library-composed/ts003_clock_sync__package_version_req` | 1 | 1 | fields | 73% | SILVER |
| `_library-composed/ts003_clock_sync__set_periodicity_daily` | 2 | 1 | fields | 71% | SILVER |
| `_library-composed/ts004_fragmentation__frag_session_delete` | 2 | 1 | fields | 73% | SILVER |
| `_library-composed/ts004_fragmentation__frag_session_status_query` | 2 | 1 | fields | 73% | SILVER |
| `_library-composed/ts004_fragmentation__package_version_req` | 1 | 1 | fields | 73% | SILVER |
| `_library-composed/ts005_multicast__mc_group_delete_0` | 2 | 1 | fields | 73% | SILVER |
| `_library-composed/ts005_multicast__mc_group_status_all` | 2 | 1 | fields | 71% | SILVER |
| `_library-composed/ts005_multicast__package_version_req` | 1 | 1 | fields | 73% | SILVER |
| `_library-composed/ts006_firmware_mgmt__delete_any_image` | 2 | 1 | fields | 73% | SILVER |
| `_library-composed/ts006_firmware_mgmt__dev_version_req` | 1 | 1 | fields | 71% | SILVER |
| `_library-composed/ts006_firmware_mgmt__package_version_req` | 1 | 1 | fields | 73% | SILVER |
| `_library-composed/ts006_firmware_mgmt__reboot_cancel` | 2 | 1 | fields | 73% | SILVER |
| `_library-composed/ts006_firmware_mgmt__reboot_countdown_5min` | 2 | 1 | fields | 71% | SILVER |
| `_library-composed/ts006_firmware_mgmt__reboot_immediate` | 2 | 1 | fields | 73% | SILVER |
| `_library-composed/ts006_firmware_mgmt__upgrade_image_query` | 1 | 1 | fields | 71% | SILVER |
| `_library-composed/ts007_multi_package__package_version_ans_full_stack` | 4 | 1 | fields | 73% | SILVER |
| `_library-composed/ts007_multi_package__package_version_req` | 1 | 1 | fields | 73% | SILVER |
| `_library-composed/udp_packet_forwarder__push_ack` | 3 | 1 | fields | 71% | SILVER |
| `_library-composed/udp_packet_forwarder__push_data_header` | 4 | 1 | fields | 73% | SILVER |
| `_library-composed/utility_meter__reset_all_counters` | 3 | 1 | fields | 73% | SILVER |
| `_library-composed/utility_meter__set_ct_100_1` | 3 | 1 | fields | 71% | SILVER |
| `_library-composed/utility_meter__set_tariff` | 3 | 1 | fields | 73% | SILVER |
| `decentlab/dl-5tm` | 10 | 8 | fields | 100% | PLATINUM |
| `decentlab/dl-alb` | 11 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-atm22` | 15 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-atm41` | 27 | 6 | fields | 92% | GOLD |
| `decentlab/dl-atm41g2` | 20 | 1 | fields | 65% | BRONZE |
| `decentlab/dl-blg` | 9 | 2 | fields | 69% | BRONZE |
| `decentlab/dl-ctd10` | 10 | 6 | fields | 89% | GOLD |
| `decentlab/dl-cws` | 10 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-cws2` | 12 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-dlr2-002` | 7 | 1 | fields | 65% | BRONZE |
| `decentlab/dl-dlr2-003` | 6 | 5 | fields | 100% | PLATINUM |
| `decentlab/dl-dlr2-004-10` | 5 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-dlr2-005` | 5 | 1 | fields | 63% | BRONZE |
| `decentlab/dl-dlr2-006` | 5 | 1 | fields | 63% | BRONZE |
| `decentlab/dl-dlr2-008-2000` | 12 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-dlr2-009-2000` | 9 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-dlr2-010` | 10 | 1 | fields | 63% | BRONZE |
| `decentlab/dl-dlr2-011` | 7 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-dlr2-012` | 5 | 1 | fields | 63% | BRONZE |
| `decentlab/dl-ds18` | 7 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-dws-232263168-0000302459-1370` | 10 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-gmm` | 15 | 6 | fields | 92% | GOLD |
| `decentlab/dl-iam` | 15 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-ifd` | 6 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-ilt` | 7 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-isd` | 6 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-isf` | 19 | 1 | fields | 65% | BRONZE |
| `decentlab/dl-itst` | 6 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-kl66-1538372-464859` | 13 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-lid` | 16 | 6 | fields | 80% | SILVER |
| `decentlab/dl-lp8p` | 16 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-lpw` | 5 | 1 | fields | 63% | BRONZE |
| `decentlab/dl-lws` | 5 | 1 | fields | 63% | BRONZE |
| `decentlab/dl-mbx` | 7 | 6 | fields | 89% | GOLD |
| `decentlab/dl-mes5` | 11 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-ntu` | 11 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-optod` | 12 | 6 | fields | 94% | GOLD |
| `decentlab/dl-par` | 5 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-pheht` | 11 | 6 | fields | 89% | GOLD |
| `decentlab/dl-pm` | 17 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-pr21-1-10` | 6 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-pr26-0-1` | 6 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-pr36-8192` | 6 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-pr36ctd-8192-1024` | 8 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-pyr` | 5 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-rad` | 10 | 6 | fields | 92% | GOLD |
| `decentlab/dl-rhc` | 7 | 1 | fields | 65% | BRONZE |
| `decentlab/dl-sdd` | 40 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-sht35` | 6 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-smtp` | 20 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-tbrg-01` | 7 | 1 | fields | 65% | BRONZE |
| `decentlab/dl-tp` | 37 | 6 | fields | 100% | PLATINUM |
| `decentlab/dl-trs11` | 10 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-trs12` | 11 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-trs21` | 6 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-wrm` | 8 | 0 | fields | 14% | REJECTED |
| `decentlab/dl-zn1` | 5 | 1 | fields | 63% | BRONZE |
| `decentlab/dl-zn2` | 6 | 1 | fields | 63% | BRONZE |
| `digital-matter/oyster` | 24 | 7 | ports | 100% | SILVER |
| `dragino/laq4` | 14 | 5 | fields | 100% | SILVER |
| `elsys/ers` | 32 | 2 | fields | 71% | SILVER |
| `hbi/mla20` | 108 | 12 | fields | 80% | SILVER |
| `makerfabs/4-channel-adc` | 8 | 0 | fields | 16% | REJECTED |
| `makerfabs/ath20` | 4 | 0 | fields | 14% | REJECTED |
| `makerfabs/gps-tracker` | 16 | 0 | fields | 16% | REJECTED |
| `makerfabs/leaf-moisture-sn-3001` | 6 | 0 | fields | 14% | REJECTED |
| `makerfabs/pipe-pressure` | 3 | 0 | fields | 16% | REJECTED |
| `makerfabs/soil-monitor` | 8 | 0 | fields | 14% | REJECTED |
| `mclimate/flood-sensor` | 5 | 0 | fields | 16% | REJECTED |
| `mclimate/t-valve` | 11 | 0 | fields | 16% | REJECTED |
| `mclimate/vicki` | 31 | 12 | fields | 100% | SILVER |
| `milesight/am102` | 22 | 17 | fields | 100% | PLATINUM |
| `milesight/am102l` | 22 | 15 | fields | 100% | PLATINUM |
| `milesight/am103` | 16 | 15 | fields | 94% | GOLD |
| `milesight/am103l` | 16 | 15 | fields | 94% | GOLD |
| `milesight/am104` | 12 | 15 | fields | 100% | PLATINUM |
| `milesight/am107` | 16 | 15 | fields | 94% | GOLD |
| `milesight/am307` | 19 | 15 | fields | 91% | GOLD |
| `milesight/am307l` | 19 | 15 | fields | 91% | GOLD |
| `milesight/am308` | 20 | 15 | fields | 91% | GOLD |
| `milesight/am308l` | 20 | 15 | fields | 91% | GOLD |
| `milesight/am319` | 26 | 15 | fields | 91% | GOLD |
| `milesight/am319l` | 26 | 15 | fields | 91% | GOLD |
| `milesight/at101-fh` | 35 | 15 | fields | 91% | GOLD |
| `milesight/at101` | 19 | 15 | fields | 100% | PLATINUM |
| `milesight/ct101` | 20 | 15 | fields | 81% | SILVER |
| `milesight/ct103` | 20 | 15 | fields | 81% | SILVER |
| `milesight/ct105` | 20 | 15 | fields | 81% | SILVER |
| `milesight/ct303` | 50 | 16 | fields | 81% | SILVER |
| `milesight/ct305` | 50 | 16 | fields | 81% | SILVER |
| `milesight/ct310` | 50 | 16 | fields | 81% | SILVER |
| `milesight/em300-di` | 10 | 15 | fields | 100% | PLATINUM |
| `milesight/em300-mcs` | 17 | 15 | fields | 100% | PLATINUM |
| `milesight/em300-mld` | 5 | 6 | fields | 100% | PLATINUM |
| `milesight/em300-sld` | 9 | 11 | fields | 100% | PLATINUM |
| `milesight/em300-th` | 8 | 10 | fields | 100% | PLATINUM |
| `milesight/em300-zld` | 9 | 11 | fields | 100% | PLATINUM |
| `milesight/em310-tilt` | 18 | 15 | fields | 98% | SILVER |
| `milesight/em310-udl` | 14 | 15 | fields | 89% | GOLD |
| `milesight/em320-th` | 8 | 10 | fields | 100% | PLATINUM |
| `milesight/em320-tilt` | 10 | 0 | fields | 38% | REJECTED |
| `milesight/em400-mud` | 13 | 13 | fields | 73% | SILVER |
| `milesight/em400-tld` | 13 | 13 | fields | 73% | SILVER |
| `milesight/em400-udl` | 13 | 13 | fields | 73% | SILVER |
| `milesight/em410-rdl` | 17 | 15 | fields | 92% | GOLD |
| `milesight/em500-co2` | 11 | 0 | fields | 31% | REJECTED |
| `milesight/em500-lgt` | 5 | 7 | fields | 89% | GOLD |
| `milesight/em500-pp` | 5 | 8 | fields | 89% | GOLD |
| `milesight/em500-pt100` | 6 | 7 | fields | 100% | PLATINUM |
| `milesight/em500-smt` | 8 | 9 | fields | 100% | PLATINUM |
| `milesight/em500-smtc` | 11 | 15 | fields | 94% | GOLD |
| `milesight/em500-swl` | 5 | 7 | fields | 89% | GOLD |
| `milesight/em500-udl` | 5 | 0 | fields | 25% | REJECTED |
| `milesight/gs101` | 7 | 0 | fields | 16% | REJECTED |
| `milesight/gs301` | 23 | 15 | fields | 100% | PLATINUM |
| `milesight/ts101` | 14 | 7 | fields | 91% | GOLD |
| `milesight/ts201` | 16 | 15 | fields | 100% | PLATINUM |
| `milesight/ts201v2` | 25 | 15 | fields | 94% | GOLD |
| `milesight/ts30x` | 26 | 15 | fields | 80% | SILVER |
| `milesight/uc100` | 7 | 7 | fields | 78% | SILVER |
| `milesight/uc1114` | 6 | 0 | fields | 16% | REJECTED |
| `milesight/uc1152` | 4 | 0 | fields | 16% | REJECTED |
| `milesight/uc300` | 19 | 15 | fields | 80% | SILVER |
| `milesight/uc50x` | 12 | 15 | fields | 100% | PLATINUM |
| `milesight/uc51x` | 24 | 15 | fields | 83% | SILVER |
| `milesight/vs121` | 5 | 0 | fields | 16% | REJECTED |
| `milesight/vs135` | 15 | 13 | fields | 80% | SILVER |
| `milesight/vs321` | 24 | 15 | fields | 98% | SILVER |
| `milesight/vs330` | 7 | 9 | fields | 86% | GOLD |
| `milesight/vs340` | 5 | 5 | fields | 89% | GOLD |
| `milesight/vs341` | 5 | 5 | fields | 89% | GOLD |
| `milesight/vs350` | 28 | 15 | fields | 94% | GOLD |
| `milesight/vs351` | 29 | 15 | fields | 94% | GOLD |
| `milesight/vs360` | 23 | 15 | fields | 80% | SILVER |
| `milesight/vs370` | 15 | 15 | fields | 92% | GOLD |
| `milesight/vs373` | 33 | 15 | fields | 80% | SILVER |
| `milesight/ws101` | 5 | 0 | fields | 27% | REJECTED |
| `milesight/ws136` | 4 | 0 | fields | 40% | REJECTED |
| `milesight/ws156` | 4 | 0 | fields | 40% | REJECTED |
| `milesight/ws201` | 6 | 10 | fields | 89% | GOLD |
| `milesight/ws202` | 6 | 6 | fields | 86% | GOLD |
| `milesight/ws203` | 12 | 12 | fields | 94% | GOLD |
| `milesight/ws301` | 6 | 6 | fields | 100% | PLATINUM |
| `milesight/ws302` | 10 | 0 | fields | 24% | REJECTED |
| `milesight/ws303` | 5 | 5 | fields | 100% | PLATINUM |
| `milesight/ws50x` | 17 | 13 | fields | 78% | SILVER |
| `milesight/ws515` | 24 | 15 | fields | 81% | SILVER |
| `milesight/ws52x` | 16 | 15 | fields | 80% | SILVER |
| `milesight/ws558` | 7 | 15 | fields | 80% | SILVER |
| `milesight/wt101` | 24 | 15 | fields | 89% | GOLD |
| `milesight/wt201v1` | 29 | 15 | fields | 86% | GOLD |
| `milesight/wt201v2` | 30 | 15 | fields | 85% | GOLD |
| `milesight/wts305` | 13 | 15 | fields | 91% | GOLD |
| `milesight/wts505` | 13 | 15 | fields | 91% | GOLD |
| `milesight/wts506` | 13 | 15 | fields | 91% | GOLD |
| `radio-bridge/rbs30x` | 52 | 29 | fields | 68% | BRONZE |
| `radionode/rn320bth` | 13 | 0 | fields | 14% | REJECTED |
| `rakwireless/qingping` | 11 | 1 | fields | 81% | SILVER |

## Other schema files

| Path | Role |
|---|---|
| `schemas/ipso-output.schema.json` | JSON Schema for IPSO-shaped decoder output |
| `schemas/payload-schema.json` | JSON Schema for the payload schema language itself |
| `schemas/senml-output.schema.json` | JSON Schema for SenML-shaped decoder output |
| `schemas/ttn-output.schema.json` | JSON Schema for TTN-shaped decoder output |
| `schemas/library/` | 34 reusable schema fragments (`use:` targets) |
