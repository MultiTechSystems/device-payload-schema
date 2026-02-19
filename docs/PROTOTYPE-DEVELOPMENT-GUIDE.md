# Protocol Prototype Development Guide

This guide provides standards and best practices for developing prototype implementations of LoRaWAN protocol specifications. Prototypes serve to validate specifications, prove interoperability, and provide reference implementations.

## Table of Contents

- [Overview](#overview)
- [Language Selection](#language-selection)
- [Portable C Development](#portable-c-development)
- [Python Simulation Framework](#python-simulation-framework)
- [Testing Philosophy](#testing-philosophy)
- [Project Structure](#project-structure)
- [Code Generation from Specifications](#code-generation-from-specifications)
- [AI-Assisted Development](#ai-assisted-development)
- [Debugging Methodology](#debugging-methodology)
- [Integration with Specifications](#integration-with-specifications)

## Overview

### Purpose of Prototypes

Protocol prototypes serve several purposes:

1. **Specification Validation** - Prove the protocol design works as intended
2. **Interoperability Testing** - Verify different implementations can communicate
3. **Compliance Verification** - Test conformance to specification requirements
4. **Reference Implementation** - Provide example code for implementers
5. **Edge Case Discovery** - Find ambiguities or gaps in the specification

### Relationship to Specification Documents

```
┌─────────────────────┐     ┌─────────────────────┐
│   Specification     │     │     Prototype       │
│   (Normative)       │────▶│   (Informative)     │
│                     │     │                     │
│ - Requirements      │     │ - Reference impl    │
│ - Message formats   │     │ - Test harness      │
│ - State machines    │     │ - Simulation        │
└─────────────────────┘     └─────────────────────┘
         │                           │
         │                           │
         ▼                           ▼
┌─────────────────────┐     ┌─────────────────────┐
│  Production Code    │◀────│   Test Results      │
│  (Various vendors)  │     │   (Compliance)      │
└─────────────────────┘     └─────────────────────┘
```

Prototypes are **informative** - they demonstrate one way to implement the specification but are not the only valid implementation.

## Language Selection

### Decision Matrix

| Use Case | Language | Rationale |
|----------|----------|-----------|
| **Embedded firmware** | C (C11) | Memory constraints, real-time, RTOS support |
| **Protocol simulation** | Python | Rapid development, asyncio, rich libraries |
| **Message codec reference** | C + Python bindings | Portable, verifiable against spec |
| **Network server components** | Go or Python | Concurrency, networking |
| **Test harnesses** | Python (pytest) | Fixtures, assertions, parameterization |
| **Performance-critical tools** | Rust | Memory safety, no runtime, C interop |

### Target Platforms

Production code SHOULD be portable across:

| Platform | OS/RTOS | Constraints |
|----------|---------|-------------|
| Linux gateway | Linux (glibc) | Full POSIX, abundant resources |
| Embedded gateway | Zephyr RTOS | Limited heap, no dynamic alloc |
| Constrained device | FreeRTOS | Minimal footprint, static alloc |
| Bare metal | None | No OS services |

## Portable C Development

### Type Definitions

Use fixed-width integer types for protocol structures. Define a portability header:

```c
/* rt.h - Runtime portability header */
#ifndef _rt_h_
#define _rt_h_

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* Fixed-width types for protocol structures */
typedef uint8_t   u1_t;
typedef int8_t    s1_t;
typedef uint16_t  u2_t;
typedef int16_t   s2_t;
typedef uint32_t  u4_t;
typedef int32_t   s4_t;
typedef uint64_t  u8_t;
typedef int64_t   s8_t;

typedef const char* str_t;

/* Timestamp type (microseconds) */
typedef s8_t ustime_t;

#define SIZE_ARRAY(a) (sizeof(a)/sizeof((a)[0]))

#endif /* _rt_h_ */
```

### Platform Abstraction Layer

Isolate OS-specific code behind a platform abstraction:

```c
/* sys.h - System abstraction interface */
#ifndef _sys_h_
#define _sys_h_

#include "rt.h"

/* Time functions */
ustime_t sys_time(void);        /* Monotonic microseconds */
ustime_t sys_utc(void);         /* UTC time, 0 if unavailable */
void     sys_usleep(ustime_t us);

/* Logging */
void sys_log(int level, const char* fmt, ...);

/* Random number generation */
int sys_random(u1_t* buf, int len);

/* Platform initialization */
void sys_init(void);

#endif /* _sys_h_ */
```

Provide separate implementations:

```
src/
├── sys.h              # Interface (portable)
├── sys_linux.c        # Linux implementation
├── sys_zephyr.c       # Zephyr RTOS implementation
├── sys_freertos.c     # FreeRTOS implementation
└── sys_stub.c         # Stub for unit tests
```

### Memory Management Strategy

For embedded portability, prefer static allocation:

```c
/* Static allocation pattern */
#define MAX_MESSAGES 16
#define MAX_MESSAGE_SIZE 256

typedef struct {
    u1_t data[MAX_MESSAGE_SIZE];
    u2_t len;
    bool in_use;
} message_slot_t;

static message_slot_t message_pool[MAX_MESSAGES];

message_slot_t* message_alloc(void) {
    for (int i = 0; i < MAX_MESSAGES; i++) {
        if (!message_pool[i].in_use) {
            message_pool[i].in_use = true;
            message_pool[i].len = 0;
            return &message_pool[i];
        }
    }
    return NULL;  /* Pool exhausted */
}

void message_free(message_slot_t* msg) {
    msg->in_use = false;
}
```

### Byte Order Handling

LoRaWAN uses little-endian byte order. Provide portable macros:

```c
/* Byte order utilities */
static inline u2_t read_u2_le(const u1_t* buf) {
    return (u2_t)buf[0] | ((u2_t)buf[1] << 8);
}

static inline u4_t read_u4_le(const u1_t* buf) {
    return (u4_t)buf[0] | ((u4_t)buf[1] << 8) |
           ((u4_t)buf[2] << 16) | ((u4_t)buf[3] << 24);
}

static inline void write_u2_le(u1_t* buf, u2_t val) {
    buf[0] = (u1_t)(val);
    buf[1] = (u1_t)(val >> 8);
}

static inline void write_u4_le(u1_t* buf, u4_t val) {
    buf[0] = (u1_t)(val);
    buf[1] = (u1_t)(val >> 8);
    buf[2] = (u1_t)(val >> 16);
    buf[3] = (u1_t)(val >> 24);
}
```

### Protocol Buffer Integration

For structured message encoding, use nanopb (embedded-friendly protobuf):

```protobuf
// messages.proto
syntax = "proto3";

message UplinkMessage {
    bytes phy_payload = 1;
    uint32 frequency = 2;
    int32 rssi = 3;
    float snr = 4;
}
```

Generate C code:

```bash
# Install nanopb
pip install nanopb

# Generate C files
nanopb_generator -I . -D . messages.proto
# Outputs: messages.pb.c, messages.pb.h
```

### Build System

Use a portable Makefile structure:

```makefile
# Platform selection
PLATFORM ?= linux
VARIANT ?= debug

# Platform-specific settings
ifeq ($(PLATFORM),linux)
    CC = gcc
    CFLAGS += -DPLATFORM_LINUX
    SYS_SRC = sys_linux.c
endif

ifeq ($(PLATFORM),zephyr)
    # Zephyr uses its own build system
    include $(ZEPHYR_BASE)/Makefile.inc
    CFLAGS += -DPLATFORM_ZEPHYR
    SYS_SRC = sys_zephyr.c
endif

# Common sources
SRCS = main.c codec.c protocol.c $(SYS_SRC)

# Build rules
$(BUILD_DIR)/%.o: src/%.c
	$(CC) $(CFLAGS) -c $< -o $@
```

## Python Simulation Framework

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Test Orchestrator                     │
│                      (pytest/asyncio)                    │
└─────────────────────┬───────────────────┬───────────────┘
                      │                   │
         ┌────────────▼────────┐ ┌────────▼────────────┐
         │   Mock LNS/CUPS     │ │   Mock Gateway      │
         │   (WebSocket)       │ │   (Radio Sim)       │
         └────────────┬────────┘ └────────┬────────────┘
                      │                   │
                      └─────────┬─────────┘
                                │
                      ┌─────────▼─────────┐
                      │  Device Under     │
                      │  Test (DUT)       │
                      └───────────────────┘
```

### Mock Server Template

```python
# mocks/lns_mock.py
import asyncio
import websockets
import json
from dataclasses import dataclass
from typing import Callable, Optional

@dataclass
class RouterConfig:
    """LNS router configuration (from spec section X.X)"""
    region: str
    freq_range: tuple[int, int]
    data_rates: list[tuple[int, int, int]]  # (SF, BW, 0)
    max_eirp: float

class MockLNS:
    """Mock LoRaWAN Network Server for testing."""
    
    def __init__(self, host: str = "localhost", port: int = 6090):
        self.host = host
        self.port = port
        self.connections: dict[str, websockets.WebSocketServerProtocol] = {}
        self.message_handlers: dict[str, Callable] = {}
        self.received_messages: list[dict] = []
        
    async def start(self):
        """Start the mock LNS server."""
        self.server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port
        )
        
    async def stop(self):
        """Stop the mock LNS server."""
        self.server.close()
        await self.server.wait_closed()
        
    async def _handle_connection(self, ws, path):
        """Handle incoming WebSocket connection."""
        gw_id = path.strip('/')
        self.connections[gw_id] = ws
        
        try:
            async for message in ws:
                msg = json.loads(message)
                self.received_messages.append(msg)
                await self._dispatch_message(ws, msg)
        finally:
            del self.connections[gw_id]
            
    async def _dispatch_message(self, ws, msg: dict):
        """Dispatch message to appropriate handler."""
        msgtype = msg.get('msgtype')
        if msgtype in self.message_handlers:
            response = await self.message_handlers[msgtype](msg)
            if response:
                await ws.send(json.dumps(response))
                
    def on_message(self, msgtype: str):
        """Decorator to register message handler."""
        def decorator(func):
            self.message_handlers[msgtype] = func
            return func
        return decorator
        
    async def send_downlink(self, gw_id: str, downlink: dict):
        """Send a downlink to a specific gateway."""
        if gw_id in self.connections:
            await self.connections[gw_id].send(json.dumps(downlink))
```

### Test Harness Template

```python
# tests/conftest.py
import pytest
import asyncio
from mocks.lns_mock import MockLNS
from mocks.gateway_sim import GatewaySimulator

@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def mock_lns():
    """Provide a mock LNS server."""
    lns = MockLNS(port=6090)
    await lns.start()
    yield lns
    await lns.stop()

@pytest.fixture
async def gateway_sim():
    """Provide a gateway simulator."""
    gw = GatewaySimulator()
    await gw.start()
    yield gw
    await gw.stop()

# tests/test_uplink.py
import pytest

@pytest.mark.asyncio
async def test_uplink_message_format(mock_lns, gateway_sim):
    """
    Test: Uplink message format per spec section 5.5.2
    
    Given: A gateway connected to LNS
    When: Gateway receives a LoRa uplink
    Then: LNS receives properly formatted uplink message
    """
    # Arrange
    uplink_received = asyncio.Event()
    received_msg = None
    
    @mock_lns.on_message('updf')
    async def handle_uplink(msg):
        nonlocal received_msg
        received_msg = msg
        uplink_received.set()
        return None
    
    # Act
    await gateway_sim.connect("ws://localhost:6090/test-gw")
    await gateway_sim.simulate_uplink(
        freq=868100000,
        sf=7,
        payload=bytes.fromhex("40F17DBE4900020001954378762B11FF0D")
    )
    
    # Assert
    await asyncio.wait_for(uplink_received.wait(), timeout=5.0)
    
    assert received_msg is not None
    assert received_msg['msgtype'] == 'updf'
    assert 'DR' in received_msg  # Data rate MUST be present
    assert 'Freq' in received_msg  # Frequency MUST be present
    assert 'upinfo' in received_msg  # Uplink info MUST be present
```

## Testing Philosophy

### Tests First, Not Code Archaeology

**RULE: Write tests to find and prove bugs rather than searching through code.**

When debugging issues:

1. **Hypothesis from symptoms** - Form a hypothesis based on observable behavior
2. **Write a minimal test** that exercises that specific code path
3. **Test proves/disproves the hypothesis** - If it fails as expected, you've found the bug
4. **Fix is verified by the test** - The same test validates the fix

### C Self-Test Pattern

```c
/* selftests.h */
#ifndef _selftests_h_
#define _selftests_h_

/* Test assertion macro */
#define TCHECK(cond) do {                                     \
    if (!(cond)) {                                            \
        selftest_fail(#cond, __FILE__, __LINE__);             \
    }                                                         \
} while (0)

#define TFAIL(msg) selftest_fail(msg, __FILE__, __LINE__)

/* Test registration */
extern void selftest_codec(void);
extern void selftest_protocol(void);
extern void selftest_state_machine(void);

void selftest_fail(const char* expr, const char* file, int line);
int  selftests_run(void);

#endif /* _selftests_h_ */
```

```c
/* selftest_codec.c */
#include "selftests.h"
#include "codec.h"

static void test_encode_uplink_header(void) {
    u1_t buf[16];
    int len;
    
    /* Test case from spec Table 5 */
    len = encode_uplink_header(buf, sizeof(buf),
        .mtype = MTYPE_UNCONFIRMED_UP,
        .devaddr = 0x01020304,
        .fcnt = 1
    );
    
    TCHECK(len == 8);
    TCHECK(buf[0] == 0x40);  /* Unconfirmed Data Up */
    TCHECK(read_u4_le(&buf[1]) == 0x01020304);
}

static void test_decode_uplink_header(void) {
    /* Test with known-good packet */
    u1_t packet[] = {0x40, 0x04, 0x03, 0x02, 0x01, 0x00, 0x01, 0x00};
    uplink_header_t hdr;
    
    int result = decode_uplink_header(&hdr, packet, sizeof(packet));
    
    TCHECK(result == 0);
    TCHECK(hdr.mtype == MTYPE_UNCONFIRMED_UP);
    TCHECK(hdr.devaddr == 0x01020304);
    TCHECK(hdr.fcnt == 1);
}

void selftest_codec(void) {
    test_encode_uplink_header();
    test_decode_uplink_header();
}
```

### Running Self-Tests

```c
/* selftests.c */
#include "selftests.h"
#include <stdio.h>

static int test_failures = 0;

void selftest_fail(const char* expr, const char* file, int line) {
    fprintf(stderr, "FAIL: %s at %s:%d\n", expr, file, line);
    test_failures++;
}

typedef void (*selftest_fn)(void);

static const selftest_fn all_tests[] = {
    selftest_codec,
    selftest_protocol,
    selftest_state_machine,
};

int selftests_run(void) {
    test_failures = 0;
    
    for (int i = 0; i < SIZE_ARRAY(all_tests); i++) {
        all_tests[i]();
    }
    
    if (test_failures == 0) {
        printf("ALL %d SELFTESTS PASSED\n", (int)SIZE_ARRAY(all_tests));
        return 0;
    } else {
        printf("%d SELFTEST(S) FAILED\n", test_failures);
        return 1;
    }
}
```

### Test Categories

| Category | Tool | Purpose |
|----------|------|---------|
| **Unit tests** | C selftests | Test individual functions in isolation |
| **Integration tests** | Python pytest | Test component interactions |
| **Simulation tests** | Python asyncio | Test full protocol flows with mocks |
| **Compliance tests** | Gherkin/pytest | Verify spec requirements |
| **Regression tests** | CI/CD | Prevent re-introduction of bugs |

## Project Structure

### Recommended Layout

```
protocol-prototype/
├── .cursorrules              # AI assistant rules
├── README.md                 # Project overview
├── Makefile                  # Build system
├── requirements.txt          # Python dependencies
│
├── spec/                     # Link to specification
│   └── README.md             # Points to spec repo
│
├── proto/                    # Protocol Buffers
│   ├── messages.proto
│   └── messages.options      # nanopb options
│
├── include/                  # Public headers
│   ├── rt.h                  # Portability types
│   ├── sys.h                 # System abstraction
│   ├── codec.h               # Message encoding
│   └── protocol.h            # Protocol logic
│
├── src/                      # Implementation
│   ├── codec.c
│   ├── protocol.c
│   ├── sys_linux.c
│   ├── sys_zephyr.c
│   ├── selftests.h
│   ├── selftests.c
│   └── selftest_*.c
│
├── python/                   # Python package
│   ├── __init__.py
│   ├── codec.py              # Python codec (for tests)
│   └── simulator.py
│
├── tests/                    # Test suite
│   ├── conftest.py           # pytest fixtures
│   ├── test_codec.py
│   ├── test_protocol.py
│   └── compliance/           # Spec compliance tests
│       ├── test_section_5.py
│       └── requirements.md   # Requirement coverage
│
├── simulation/               # Simulation framework
│   ├── mocks/
│   │   ├── lns_mock.py
│   │   └── gateway_sim.py
│   ├── scenarios/
│   │   └── basic_updown.py
│   └── run_simulation.py
│
├── tools/                    # Development tools
│   ├── generate_from_spec.py
│   └── coverage_report.py
│
└── docs/
    ├── ARCHITECTURE.md
    ├── COVERAGE.md           # Spec coverage matrix
    └── PORTING.md            # Platform porting guide
```

## Code Generation from Specifications

### Mapping Spec Tables to Code

Specification tables can be converted to code structures:

**Spec Table (Section 4.2):**

| Bits | [7:5] | [4:2] | [1:0] |
|------|-------|-------|-------|
| MHDR | MType | RFU | Major |

**Generated C Code:**

```c
/* Generated from spec section 4.2 */
typedef struct {
    u1_t mtype : 3;   /* Bits [7:5] */
    u1_t rfu   : 3;   /* Bits [4:2] - Reserved */
    u1_t major : 2;   /* Bits [1:0] */
} mhdr_t;

/* MType values from spec Table 4 */
typedef enum {
    MTYPE_JOIN_REQUEST        = 0b000,
    MTYPE_JOIN_ACCEPT         = 0b001,
    MTYPE_UNCONFIRMED_UP      = 0b010,
    MTYPE_UNCONFIRMED_DOWN    = 0b011,
    MTYPE_CONFIRMED_UP        = 0b100,
    MTYPE_CONFIRMED_DOWN      = 0b101,
    MTYPE_RFU                 = 0b110,
    MTYPE_PROPRIETARY         = 0b111,
} mtype_t;
```

### AI Prompt for Code Generation

```
Generate C code from this specification table:

[Paste table from spec]

Requirements:
- Use fixed-width types (u1_t, u2_t, u4_t from rt.h)
- Use bit fields for sub-byte fields
- Generate encode/decode functions
- Add comments referencing spec section numbers
- Follow the naming convention: lowercase_with_underscores
- RFU fields MUST be set to 0 on encode, ignored on decode
```

## AI-Assisted Development

### Prototype Project .cursorrules

Create `.cursorrules` in the prototype project root:

```
# Protocol Prototype Development Rules

## Code Style

### C Code
- Use C11 with GNU extensions where needed for portability
- Fixed-width types: u1_t, u2_t, u4_t, s1_t, s2_t, s4_t (from rt.h)
- Functions: lowercase_with_underscores
- Types: lowercase_t suffix
- Constants: UPPERCASE_WITH_UNDERSCORES
- Use static for file-local functions

### Python Code
- Python 3.8+ for asyncio improvements
- Type hints for function signatures
- Async/await for network operations
- dataclasses for message structures

## Testing

### Self-Tests (C)
- Use TCHECK() macro for assertions
- One test function per behavior
- Register in selftests.c array
- Test both success and failure paths

### pytest (Python)
- Use fixtures for setup/teardown
- Mark async tests with @pytest.mark.asyncio
- Reference spec sections in docstrings

## Specification Compliance

- Comment code with spec section references: /* Per spec section 5.2 */
- Map normative keywords to test assertions:
  - MUST → test failure if not satisfied
  - SHOULD → warning if not satisfied
  - MAY → optional test

## Platform Portability

- No dynamic memory allocation in embedded builds
- Use sys.h abstraction for OS services
- Avoid GNU-specific extensions in core protocol code
- Test on Linux before targeting embedded

## Protocol Buffers

- Use nanopb for C code generation
- Define .options file for static allocation
- Regenerate .pb.c/.pb.h after .proto changes
```

### AI Prompts for Common Tasks

**Generate Message Handler:**
```
Generate a C handler for the [MessageName] message defined in spec section X.X.

The handler should:
1. Decode the message using the codec functions
2. Validate all fields per spec requirements
3. Update protocol state as specified
4. Return appropriate response or error code

Include:
- TCHECK assertions for invariants
- Logging with LOG() macro
- Error handling for malformed messages
```

**Generate Test Case:**
```
Generate a pytest test case for spec requirement:

"The gateway SHALL transmit the downlink within RECEIVE_DELAY1 seconds"

The test should:
1. Set up mock LNS and gateway simulator
2. Trigger the condition (send uplink)
3. Assert the requirement is met (timing check)
4. Reference the spec section in the docstring
```

## Debugging Methodology

### Hypothesis-Driven Debugging

1. **Observe** - What is the actual behavior?
2. **Hypothesize** - What could cause this?
3. **Test** - Write a minimal test that would fail if hypothesis is correct
4. **Verify** - Run test, refine hypothesis if needed
5. **Fix** - Implement fix, test passes
6. **Regress** - Keep test for regression prevention

### Logging Conventions

```c
/* Log levels */
enum { LOG_DEBUG, LOG_INFO, LOG_WARN, LOG_ERROR };

/* Module tags */
#define MOD_CODEC  "CODEC"
#define MOD_PROTO  "PROTO"
#define MOD_SYS    "SYS"

/* Usage */
LOG(LOG_DEBUG, MOD_CODEC, "Decoding uplink: len=%d", len);
LOG(LOG_ERROR, MOD_PROTO, "Invalid MType: 0x%02x", mtype);
```

### Protocol Tracing

```python
# simulation/tracing.py
import json
from datetime import datetime

class ProtocolTracer:
    def __init__(self, output_file: str):
        self.output = open(output_file, 'w')
        
    def trace(self, direction: str, endpoint: str, message: dict):
        entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'direction': direction,  # 'TX' or 'RX'
            'endpoint': endpoint,
            'message': message
        }
        self.output.write(json.dumps(entry) + '\n')
        self.output.flush()
```

## Integration with Specifications

### Requirement Traceability

Maintain a coverage matrix in `docs/COVERAGE.md`:

```markdown
# Specification Coverage Matrix

## Section 5: Message Formats

| Requirement | Spec Section | Test | Status |
|-------------|--------------|------|--------|
| Uplink header format | 5.1.1 | test_codec.py::test_uplink_header | ✅ Pass |
| Downlink timing | 5.2.3 | test_timing.py::test_rx1_window | ✅ Pass |
| MIC calculation | 5.3 | selftest_codec::test_mic | ✅ Pass |

## Section 6: Protocol Behavior

| Requirement | Spec Section | Test | Status |
|-------------|--------------|------|--------|
| Join procedure | 6.2.4 | test_join.py::test_otaa_join | 🔄 In Progress |
```

### Updating Prototypes for Spec Changes

When the specification changes:

1. **Review the change** - Identify affected prototype code
2. **Update coverage matrix** - Mark affected tests as needing update
3. **Update tests first** - Modify tests to match new spec
4. **Update implementation** - Fix code to pass updated tests
5. **Document** - Update COVERAGE.md with new status

### Linking to Spec Repository

```markdown
<!-- spec/README.md -->
# Specification Reference

This prototype implements:

**Specification:** LoRaWAN NS-GW Interface
**Version:** 1.0.0
**Repository:** https://github.com/lorawan-schema/payload-codec
**Local Copy:** ../la-spec-template (for development)

## Tracked Sections

- Section 4: Protocol Stack ✅
- Section 5: Message Exchange ✅
- Section 6: State Management 🔄

## Syncing with Spec Updates

```bash
# Check for spec updates
cd ../la-spec-template && git pull

# Compare versions
diff spec-info.yaml ../la-spec-template/spec-info.yaml
```
```

## Quick Start Checklist

- [ ] Clone prototype template
- [ ] Set up Python virtual environment
- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Build C code: `make platform=linux variant=debug`
- [ ] Run C self-tests: `./build/bin/selftest`
- [ ] Run Python tests: `pytest tests/`
- [ ] Run simulation: `python simulation/run_simulation.py`
- [ ] Review coverage: `python tools/coverage_report.py`

## References

- [Basic Station](https://github.com/lorabasics/basicstation) - Reference gateway implementation
- [nanopb](https://github.com/nanopb/nanopb) - Protocol Buffers for embedded
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) - Async test support
- [Zephyr RTOS](https://docs.zephyrproject.org/) - Embedded RTOS documentation
- [FreeRTOS](https://www.freertos.org/) - Real-time OS documentation
