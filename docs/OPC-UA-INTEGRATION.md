# OPC UA Integration Technical Overview

This document explores the technical aspects of integrating LoRaWAN device data with OPC UA industrial systems, as proposed for the LoRa Alliance / OPC Foundation Joint Working Group.

---

## Executive Summary

The LoRaWAN Payload Schema project provides a foundation for standardized LoRaWAN-to-OPC UA integration by:

1. **Semantic annotations** (`valid_range`, `resolution`, `unece`) that map directly to OPC UA information model concepts
2. **Declarative schemas** eliminating security risks of executable codec JavaScript
3. **Quality tracking** with `_quality` output flags compatible with OPC UA quality indicators

---

## Question 1: Information Model Depth

> Full device modeling (OPC UA DI companion spec) or just measurement semantics?

### Option A: Measurement Semantics Only (Recommended for Phase 1)

Map decoded values to OPC UA `AnalogItemType` nodes with:

| Schema Field | OPC UA Attribute |
|--------------|------------------|
| Field value | `Value` |
| `unit` | `EngineeringUnits.DisplayName` |
| `unece` | `EngineeringUnits.UnitId` |
| `valid_range` | `EURange` (Low/High) |
| `resolution` | Custom property or derived from `EURange` |
| `_quality` | `StatusCode` (Good, Bad_OutOfRange) |

**Advantages:**
- Simpler implementation
- Works with existing OPC UA servers
- No custom information model required

**Sample OPC UA NodeSet2.xml fragment:**

```xml
<UAVariable NodeId="ns=1;s=LoRaDevice1.Temperature" BrowseName="Temperature"
            DataType="Double" TypeDefinition="i=2368">
  <DisplayName>Temperature</DisplayName>
  <References>
    <Reference ReferenceType="HasProperty">ns=1;s=LoRaDevice1.Temperature.EURange</Reference>
    <Reference ReferenceType="HasProperty">ns=1;s=LoRaDevice1.Temperature.EngineeringUnits</Reference>
  </References>
</UAVariable>
<UAVariable NodeId="ns=1;s=LoRaDevice1.Temperature.EURange" BrowseName="EURange"
            DataType="Range" TypeDefinition="i=68">
  <Value>
    <uax:ExtensionObject>
      <uax:TypeId><uax:Identifier>i=884</uax:Identifier></uax:TypeId>
      <uax:Body>
        <uax:Range><uax:Low>-40</uax:Low><uax:High>85</uax:High></uax:Range>
      </uax:Body>
    </uax:ExtensionObject>
  </Value>
</UAVariable>
```

### Option B: Full Device Modeling (Phase 2+)

Create OPC UA companion specification defining:

- `LoRaDeviceType` extending `DeviceType` (OPC UA DI)
- `LoRaSensorType` with LoRaWAN-specific properties (DevEUI, AppEUI, fPort)
- Gateway modeling with `rxMetadata` (RSSI, SNR, timestamp)

**Additional complexity:**
- Requires OPC Foundation coordination for companion spec
- More implementation effort
- Provides richer integration for enterprise systems

### Recommendation

Start with **Option A** for initial deliverable. The payload schema's semantic fields (`valid_range`, `resolution`, `unece`) already provide the data needed. Full device modeling can be Phase 2 after proving value.

---

## Question 2: Transport Mechanisms

> OPC UA PubSub over MQTT from LNS, or traditional client-server from application layer?

### Option A: OPC UA PubSub over MQTT (Recommended)

```
[LoRa Device] → [Gateway] → [LNS] → [MQTT Broker] → [OPC UA PubSub Subscriber]
                                         ↓
                                  {Payload Schema}
                                    Decoder
```

**Data Flow:**

1. LNS publishes uplink to MQTT topic (e.g., `lorawan/devices/{DevEUI}/up`)
2. Schema interpreter decodes payload using device's YAML schema
3. OPC UA PubSub message constructed with:
   - `DataSetWriterId` mapped from DevEUI
   - Field values from decoded payload
   - Quality from `_quality` output

**PubSub JSON Message Example:**

```json
{
  "MessageId": "msg-12345",
  "MessageType": "ua-data",
  "PublisherId": "lorawan-gateway-001",
  "Messages": [{
    "DataSetWriterId": 1,
    "Payload": {
      "Temperature": {
        "Value": 23.45,
        "SourceTimestamp": "2026-02-19T10:30:00Z",
        "StatusCode": 0
      },
      "Humidity": {
        "Value": 65.0,
        "SourceTimestamp": "2026-02-19T10:30:00Z",
        "StatusCode": 0
      }
    }
  }]
}
```

**Advantages:**
- Natural fit for LoRaWAN's publish model
- Lower latency (no polling)
- Scales to many devices
- MQTT already used by most LNS platforms

### Option B: Client-Server from Application Layer

```
[LoRa Device] → [LNS] → [Application Server] ← OPC UA Client
                              ↓
                    {Schema Decoder + OPC UA Server}
```

**Advantages:**
- Familiar to traditional industrial systems
- Better for request/response patterns (downlinks)
- Supports OPC UA Browse, Read, Write, Call

### Option C: Hybrid (Recommended for Full Integration)

- **Uplinks:** PubSub over MQTT (real-time telemetry)
- **Downlinks:** Client-Server (command/control via OPC UA Write/Call)
- **Configuration:** Client-Server (device metadata, schema management)

### Sample Architecture

```
                    ┌─────────────────────────────────────────┐
                    │          OPC UA Server                   │
                    │  ┌─────────────┐  ┌─────────────┐       │
                    │  │ PubSub      │  │ Client/Svr  │       │
                    │  │ Subscriber  │  │ Interface   │       │
                    │  └──────┬──────┘  └──────┬──────┘       │
                    │         │                │              │
                    │    ┌────┴────────────────┴────┐         │
                    │    │   Payload Schema Engine  │         │
                    │    │   (decode/encode)        │         │
                    │    └────────────┬─────────────┘         │
                    └─────────────────┼───────────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
        ┌─────┴─────┐          ┌──────┴──────┐         ┌──────┴──────┐
        │   MQTT    │          │   HTTPS     │         │   gRPC      │
        │  Broker   │          │   Webhook   │         │   LNS API   │
        └─────┬─────┘          └──────┬──────┘         └──────┬──────┘
              │                       │                       │
              └───────────────────────┼───────────────────────┘
                                      │
                            ┌─────────┴─────────┐
                            │    LoRaWAN LNS    │
                            └─────────┬─────────┘
                                      │
                            ┌─────────┴─────────┐
                            │    LoRa Gateway   │
                            └─────────┬─────────┘
                                      │ RF
                            ┌─────────┴─────────┐
                            │   LoRa Devices    │
                            └───────────────────┘
```

---

## Question 3: Security Mapping

> How do LoRaWAN DevEUI/AppKey concepts map to OPC UA certificates/user tokens?

### Identity Mapping

| LoRaWAN Concept | OPC UA Concept |
|-----------------|----------------|
| DevEUI | Application Instance URI / NodeId |
| AppEUI/JoinEUI | Namespace URI |
| AppKey | (Not directly mapped - different layer) |
| Device activation (OTAA/ABP) | Session establishment |

### Authentication Approaches

#### Approach 1: Gateway-Level Trust (Recommended for Phase 1)

```
LoRaWAN Security Domain              OPC UA Security Domain
┌─────────────────────┐              ┌─────────────────────┐
│  Device ←→ LNS      │              │  OPC UA Server      │
│  (AES-128 CCM*)     │   Gateway    │  (X.509 + TLS)      │
│  AppKey, NwkSKey    │──────────────│  Server Certificate │
│  AppSKey            │   Trust      │  User Tokens        │
└─────────────────────┘   Boundary   └─────────────────────┘
```

- LNS-to-OPC UA integration uses standard OPC UA security (X.509 certificates, TLS)
- LoRaWAN security (device-to-LNS) handled separately
- DevEUI used as identifier, not authentication credential
- OPC UA server trusts LNS application instance

**Advantages:**
- Clear separation of security domains
- No need to expose LoRaWAN keys to OPC UA
- Standard OPC UA security profiles apply

#### Approach 2: Device-Level Identity Propagation (Phase 2+)

For high-security applications, propagate device identity:

1. **DevEUI in OPC UA Certificate:** Generate per-device OPC UA certificates with DevEUI in Subject Alternative Name
2. **Audit Trail:** Include DevEUI in OPC UA audit events
3. **Access Control:** OPC UA Role-based access mapped to LoRaWAN device groups

### Data Integrity

| Layer | Mechanism |
|-------|-----------|
| LoRaWAN Payload | MIC (AES-CMAC) - verified by LNS |
| LoRaWAN Transport | AES-128 CCM* encryption |
| OPC UA Transport | TLS 1.2+ |
| OPC UA Message | Message signing (optional) |

### Recommendation

For the Joint Working Group's open-source implementation:

1. Implement **gateway-level trust** with OPC UA application authentication
2. Include DevEUI in OPC UA NodeIds for traceability
3. Document security considerations for production deployments
4. Provide guidance for environments requiring device-level identity propagation

---

## Implementation Roadmap

### Phase 1: Core Integration (This Project)

- [x] Semantic fields in payload schema (`valid_range`, `resolution`, `unece`)
- [x] Quality flags in decoder output (`_quality`)
- [ ] OPC UA NodeSet2.xml generator from schema
- [ ] Sample OPC UA PubSub publisher (Python)

### Phase 2: Full Integration

- [ ] OPC UA companion specification draft
- [ ] Downlink support (OPC UA Write → LoRaWAN downlink)
- [ ] Gateway metadata modeling
- [ ] Historical data access (OPC UA HA)

### Phase 3: Production Hardening

- [ ] Performance benchmarking at scale
- [ ] Security audit
- [ ] Certification test suite
- [ ] Reference deployment guide

---

## Related Tools

### Schema-to-NodeSet Generator

Generate OPC UA information model from payload schema:

```bash
python tools/generate_opcua_nodeset.py schemas/decentlab/dl-5tm.yaml \
  --namespace "http://example.com/lorawan/decentlab" \
  --output dl-5tm-nodeset.xml
```

### PubSub Publisher

Publish decoded payloads as OPC UA PubSub messages:

```bash
python tools/opcua_pubsub_publisher.py \
  --mqtt-broker mqtt://localhost:1883 \
  --mqtt-topic "lorawan/+/up" \
  --schema-dir schemas/ \
  --pubsub-config pubsub.json
```

---

## References

- OPC UA Part 8: Data Access
- OPC UA Part 14: PubSub
- OPC UA DI (Device Integration) Companion Spec
- UNECE Recommendation 20: Codes for Units of Measure
- LoRaWAN Specification 1.0.4
- TS013 Payload Codec API

---

## Appendix: UNECE Unit Code Reference

Common codes for LoRaWAN sensor data:

| Unit | UNECE Code | OPC UA UnitId |
|------|------------|---------------|
| Celsius | CEL | 4408652 |
| Fahrenheit | FAH | 4604232 |
| Kelvin | KEL | 4932940 |
| Percent | P1 | 20529 |
| Pascal | PAL | 5259596 |
| Bar | BAR | 4342098 |
| Volt | VLT | 5720148 |
| Ampere | AMP | 4279632 |
| Watt | WTT | 5723220 |
| Meter | MTR | 5067858 |
| Millimeter | MMT | 5066068 |
| Kilogram | KGM | 4933453 |
| Second | SEC | 5457219 |
| Hertz | HTZ | 4740186 |
| Decibel | 2N | 12878 |
