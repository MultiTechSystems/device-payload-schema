# Protocol Prototype Template - Implementation Plan

This document outlines the phased approach to completing the `la-proto-template` project.

## Project Goal

Create a reusable template for building protocol prototypes that validate LoRaWAN specifications. The template should support:

- Portable C code (Linux, Zephyr, FreeRTOS)
- Python simulation and testing
- Multiple message encoding formats (JSON/REST, Protocol Buffers)
- AI-assisted development with consistent code generation
- Integration with sibling specification projects (`la-spec-template`)

## Current Status

### Completed
- [x] Basic project structure created
- [x] `.cursorrules` for AI assistance
- [x] `rt.h` - Portability types and byte-order utilities
- [x] `sys.h` - System abstraction interface
- [x] `selftests.h/c` - Self-test framework
- [x] `sys_linux.c` - Linux platform implementation
- [x] `selftest_codec.c` - Example codec tests
- [x] `selftest_protocol.c` - Example protocol tests
- [x] `requirements.txt` - Python dependencies
- [x] `Makefile` - Build system
- [x] `README.md` - Project overview
- [x] `tests/conftest.py` - pytest fixtures
- [x] `PROTOTYPE-DEVELOPMENT-GUIDE.md` - Comprehensive guide (needs update)

### Pending
- [ ] Phase 1: Message Encoding Support
- [ ] Phase 2: Python Codec and Simulation
- [ ] Phase 3: Mock Server Templates
- [ ] Phase 4: Documentation Updates
- [ ] Phase 5: Example Protocol Implementation

---

## Phase 1: Message Encoding Support

**Goal:** Support both JSON/REST (like TS002 Backend Interfaces) and Protocol Buffers (like NS-GW Interface) encoding formats.

### 1.1 JSON Codec (C)

Create lightweight JSON encoding/decoding for C:

```
include/
├── json.h          # JSON builder/parser interface
src/
├── json.c          # Minimal JSON implementation
├── selftest_json.c # JSON codec tests
```

Features:
- Static memory allocation (no malloc)
- Build JSON objects incrementally
- Parse JSON with callback pattern
- Hex string encoding for binary data (per TS002)

### 1.2 Protocol Buffer Support (C)

Already using nanopb pattern. Add:

```
proto/
├── messages.proto    # Example message definitions
├── messages.options  # nanopb static allocation options
src/
├── messages.pb.c     # Generated (from nanopb)
├── messages.pb.h     # Generated (from nanopb)
```

### 1.3 Encoding Abstraction

Create common interface for both formats:

```c
// codec.h
typedef enum {
    CODEC_JSON,
    CODEC_PROTOBUF
} codec_type_t;

int encode_message(codec_type_t type, void* msg, u1_t* buf, size_t bufsize);
int decode_message(codec_type_t type, void* msg, const u1_t* buf, size_t len);
```

### Deliverables
- [ ] `include/json.h` - JSON codec interface
- [ ] `src/json.c` - JSON implementation
- [ ] `src/selftest_json.c` - JSON tests
- [ ] `include/codec.h` - Unified codec interface
- [ ] `proto/messages.proto` - Example protobuf
- [ ] `proto/messages.options` - nanopb options

---

## Phase 2: Python Codec and Simulation

**Goal:** Python implementations for rapid prototyping and testing.

### 2.1 Python Package Structure

```
python/
├── __init__.py
├── codec/
│   ├── __init__.py
│   ├── json_codec.py    # JSON encoding (matches C)
│   ├── proto_codec.py   # Protobuf encoding
│   └── base.py          # Common interfaces
├── messages/
│   ├── __init__.py
│   └── types.py         # Message dataclasses
└── utils/
    ├── __init__.py
    └── hex.py           # Hex string utilities
```

### 2.2 Message Types (dataclasses)

```python
# python/messages/types.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class UplinkMetadata:
    dev_eui: str          # Hex string (16 chars)
    frequency: int        # Hz
    rssi: int            # dBm
    snr: float           # dB
    
@dataclass 
class BackendMessage:
    protocol_version: str
    sender_id: str
    receiver_id: str
    transaction_id: int
    message_type: str
```

### Deliverables
- [ ] `python/codec/json_codec.py`
- [ ] `python/codec/proto_codec.py`
- [ ] `python/messages/types.py`
- [ ] `python/utils/hex.py`
- [ ] Unit tests for Python codec

---

## Phase 3: Mock Server Templates

**Goal:** Reusable mock servers for protocol simulation.

### 3.1 HTTP/REST Mock (JSON)

For TS002-style backend interfaces:

```
simulation/mocks/
├── __init__.py
├── rest_server.py      # HTTP/REST mock server
├── handlers.py         # Message handler registration
└── backend_mock.py     # TS002-style NS/JS/AS mocks
```

Features:
- aiohttp-based async server
- JSON message encoding
- Transaction ID tracking
- Request/response logging

### 3.2 WebSocket Mock (Protobuf/JSON)

For NS-GW style interfaces:

```
simulation/mocks/
├── ws_server.py        # WebSocket mock server
├── lns_mock.py         # LNS mock implementation
├── cups_mock.py        # CUPS mock implementation
└── gateway_sim.py      # Gateway simulator
```

Features:
- websockets-based async server
- Support both JSON and Protobuf encoding
- Radio simulation hooks
- Timing simulation

### Deliverables
- [ ] `simulation/mocks/rest_server.py`
- [ ] `simulation/mocks/ws_server.py`
- [ ] `simulation/mocks/lns_mock.py`
- [ ] `simulation/mocks/gateway_sim.py`
- [ ] `simulation/scenarios/basic_flow.py` - Example scenario

---

## Phase 4: Documentation Updates

**Goal:** Clear, actionable documentation.

### 4.1 Update PROTOTYPE-DEVELOPMENT-GUIDE.md

Add sections for:
- Message encoding options (JSON vs Protobuf)
- When to use each format
- TS002 Backend Interface patterns
- NS-GW Interface patterns

### 4.2 Create Focused Guides

```
docs/
├── PLAN.md                          # This file
├── PROTOTYPE-DEVELOPMENT-GUIDE.md   # Main guide (update)
├── JSON-ENCODING.md                 # JSON patterns for LoRaWAN
├── PROTOBUF-ENCODING.md             # Protobuf patterns
├── SIMULATION-GUIDE.md              # Running simulations
└── PORTING-GUIDE.md                 # Porting to Zephyr/FreeRTOS
```

### 4.3 Update .cursorrules

Add rules for:
- JSON object naming (camelCase per TS002)
- Hex string encoding conventions
- REST endpoint patterns
- WebSocket message patterns

### Deliverables
- [ ] Update `PROTOTYPE-DEVELOPMENT-GUIDE.md`
- [ ] Create `JSON-ENCODING.md`
- [ ] Create `SIMULATION-GUIDE.md`
- [ ] Update `.cursorrules`

---

## Phase 5: Example Protocol Implementation

**Goal:** Complete working example demonstrating all features.

### 5.1 Example: Simplified Join Flow

Implement a simplified version of the TS002 Join procedure:

```
examples/join-flow/
├── README.md
├── messages.proto       # JoinReq/JoinAns messages
├── messages.json        # JSON schema
├── c/
│   ├── join_handler.c
│   └── join_handler.h
├── python/
│   └── join_sim.py
└── tests/
    ├── test_join_c.py   # Test C implementation
    └── test_join_py.py  # Test Python implementation
```

### 5.2 Integration Test

End-to-end test using:
- C codec for message encoding
- Python mock servers
- Automated compliance checking

### Deliverables
- [ ] `examples/join-flow/` complete example
- [ ] Integration test passing
- [ ] Documentation for running example

---

## Execution Order

### Immediate (Phase 1.1)
Start with JSON codec since it's simpler and matches TS002:
1. Create `include/json.h`
2. Create `src/json.c`
3. Create `src/selftest_json.c`
4. Verify with `make selftest`

### Next Steps
After JSON codec works:
1. Phase 2.1 - Python codec
2. Phase 3.1 - REST mock server
3. Phase 4 - Documentation
4. Phase 1.2-1.3 - Protobuf support
5. Phase 3.2 - WebSocket mock
6. Phase 5 - Example

---

## Decision Log

| Decision | Rationale |
|----------|-----------|
| JSON first, Protobuf optional | TS002 uses JSON; more accessible; Protobuf adds complexity |
| Static allocation in C | Embedded portability (Zephyr, FreeRTOS) |
| Python for simulation | Rapid development, rich async libraries |
| Separate from spec template | Reduce code churn, clearer separation of concerns |
| dataclasses for Python messages | Type hints, immutability, easy serialization |

---

## Open Questions

1. **Protobuf priority** - Should we defer Protobuf support until after JSON is complete and tested?
   - *Recommendation:* Yes, JSON first

2. **C JSON library** - Use existing library (cJSON, jsmn) or minimal custom implementation?
   - *Recommendation:* Minimal custom for embedded portability, option to swap

3. **WebSocket library for C** - Not needed for prototype testing (Python handles simulation)
   - *Recommendation:* C code focuses on codec, Python handles transport

---

## Next Action

**Start Phase 1.1:** Create the JSON codec for C.

Ready to proceed?
