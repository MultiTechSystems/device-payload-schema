"""PS-021: a message decoded against a port declared for the other direction is an error.

An uplink arriving on a port declared `direction: downlink` was decoded against that
port's field definitions and reported as a successful decode. The device had not followed
its own declared protocol; nothing in the output said so, and the numbers reported were
indistinguishable from correct ones - in the reference schema, `reporting_interval: 60225`
for three bytes that were a temperature and a humidity.

Every interpreter parsed `direction` and never read it. The one component that read it,
the TS013 codec generator, refused the payload but reported "Unknown fPort", which
describes a port the schema does not define rather than one declared for the other
direction.

CR-2026-010 fixes the reading:

- The direction of the message is an input to the decoder (PS-290). Where the caller does
  not supply it, no check runs and behaviour is unchanged - a decoder in that position is
  not satisfying PS-021.
- A disagreement reports no field and is a decode error naming the FPort, the declared
  direction and the message direction (PS-288). Uplink bytes read through downlink field
  definitions produce numbers with no relationship to what the device measured, so there
  is nothing worth reporting alongside a warning.
- `both`, and an entry declaring nothing, accept either direction (PS-287). The check is
  opt-in per schema: an author who says nothing keeps today's behaviour.
- The check reads the entry actually selected, including a `default` entry (PS-289), and a
  schema-level declaration where the schema has no `ports` (PS-291).

`bidirectional` appeared in a clause 5 example and is withdrawn, so a schema carrying it
surfaces as an unknown value rather than being read as `both`.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402

# Port 1 carries telemetry, port 2 carries configuration. The payload is a well-formed
# port 1 uplink: 23.5 degrees and 65 %RH.
PORTED = """
name: demo_sensor
endian: big
ports:
  1:
    direction: uplink
    fields:
      - {name: temperature, type: s16, div: 10}
      - {name: humidity, type: u8}
  2:
    direction: downlink
    fields:
      - {name: command, type: u8}
      - {name: reporting_interval, type: u16}
"""
UPLINK_PAYLOAD = bytes.fromhex("00EB41")


def interpreter(body=PORTED):
    return SchemaInterpreter(yaml.safe_load(body))


def decode_js(body, octets, fPort, fn):
    js = TS013Generator(yaml.safe_load(body)).generate()
    driver = (
        js
        + f"\nvar _r = {fn}({{ bytes: {json.dumps(octets)}, fPort: {fPort} }});"
        + "\nconsole.log(JSON.stringify({data: _r.data, warnings: _r.warnings, errors: _r.errors}));"
    )
    out = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestPortDirection:
    def test_an_uplink_on_a_downlink_port_is_an_error(self):
        result = interpreter().decode(UPLINK_PAYLOAD, fPort=2, direction="uplink")
        assert not result.success
        assert result.errors == [
            "fPort 2 is declared direction:downlink; message direction is uplink"
        ]

    def test_no_field_is_reported(self):
        # The point of the requirement: no plausible-looking number reaches a consumer.
        result = interpreter().decode(UPLINK_PAYLOAD, fPort=2, direction="uplink")
        assert result.data == {}
        assert "reporting_interval" not in result.data

    def test_a_downlink_on_an_uplink_port_is_an_error(self):
        result = interpreter().decode(UPLINK_PAYLOAD, fPort=1, direction="downlink")
        assert not result.success
        assert result.errors == [
            "fPort 1 is declared direction:uplink; message direction is downlink"
        ]

    def test_the_declared_direction_still_decodes(self):
        result = interpreter().decode(UPLINK_PAYLOAD, fPort=1, direction="uplink")
        assert result.success
        assert result.data == {"temperature": 23.5, "humidity": 65}

    def test_a_downlink_on_a_downlink_port_still_decodes(self):
        result = interpreter().decode(UPLINK_PAYLOAD, fPort=2, direction="downlink")
        assert result.success
        assert result.data["reporting_interval"] == 60225


class TestWhenNoCheckApplies:
    """PS-287 and PS-290: the check is opt-in, on both sides."""

    def test_an_unsupplied_direction_decodes_as_before(self):
        result = interpreter().decode(UPLINK_PAYLOAD, fPort=2)
        assert result.success
        assert result.data == {"command": 0, "reporting_interval": 60225}

    def test_a_port_declaring_both_accepts_either_direction(self):
        body = """
name: t
endian: big
ports:
  7:
    direction: both
    fields: [{name: x, type: u8}]
"""
        for direction in ("uplink", "downlink"):
            result = interpreter(body).decode(b"\x2a", fPort=7, direction=direction)
            assert result.success, direction
            assert result.data == {"x": 42}

    def test_an_undeclared_port_accepts_either_direction(self):
        body = """
name: t
endian: big
ports:
  7:
    fields: [{name: x, type: u8}]
"""
        for direction in ("uplink", "downlink"):
            result = interpreter(body).decode(b"\x2a", fPort=7, direction=direction)
            assert result.success, direction

    def test_a_schema_with_no_declaration_is_unaffected(self):
        body = "name: t\nendian: big\nfields: [{name: x, type: u8}]\n"
        assert interpreter(body).decode(b"\x2a", direction="uplink").success
        assert interpreter(body).decode(b"\x2a", direction="downlink").success


class TestSelectedEntry:
    """PS-289 and PS-291: the check reads the entry the decode actually used."""

    def test_the_default_entry_is_checked_and_named(self):
        body = """
name: t
endian: big
ports:
  1:
    direction: uplink
    fields: [{name: x, type: u8}]
  default:
    direction: downlink
    fields: [{name: raw, type: u8}]
"""
        result = interpreter(body).decode(b"\xab", fPort=42, direction="uplink")
        assert not result.success
        # Naming fPort 42 would describe a port the schema never defined.
        assert result.errors == [
            "the default port entry is declared direction:downlink; "
            "message direction is uplink"
        ]

    def test_an_unmatched_port_stays_an_unmatched_port(self):
        # PS-288 keeps the two faults apart. Running the check first must not turn "the
        # schema does not define this port" into a direction complaint.
        body = """
name: nd
endian: big
ports:
  1:
    direction: uplink
    fields: [{name: x, type: u8}]
"""
        for kwargs in ({}, {"direction": "uplink"}, {"direction": "downlink"}):
            with pytest.raises(ValueError) as caught:
                interpreter(body).decode(b"\x01", fPort=99, **kwargs)
            assert "No port definition for fPort 99" in str(caught.value)

    def test_a_schema_level_declaration_is_checked(self):
        body = """
name: cfg
endian: big
direction: downlink
fields: [{name: reporting_interval, type: u16}]
"""
        result = interpreter(body).decode(b"\x00\x3c", direction="uplink")
        assert not result.success
        assert result.errors == [
            "schema 'cfg' is declared direction:downlink; message direction is uplink"
        ]

    def test_a_schema_level_declaration_still_decodes_its_own_direction(self):
        body = """
name: cfg
endian: big
direction: downlink
fields: [{name: reporting_interval, type: u16}]
"""
        result = interpreter(body).decode(b"\x00\x3c", direction="downlink")
        assert result.success
        assert result.data == {"reporting_interval": 60}

    def test_the_reported_direction_property_keeps_its_default(self):
        # `self.direction` reports 'uplink' for a schema that declares nothing, and the
        # check deliberately does not read it: enforcing that default would narrow every
        # unannotated single-port schema already written.
        assert interpreter("name: t\nfields: [{name: x, type: u8}]\n").direction == "uplink"


class TestRejectedValues:
    def test_an_unknown_declared_direction_is_an_error(self):
        body = """
name: t
endian: big
ports:
  7:
    direction: bidirectional
    fields: [{name: x, type: u8}]
"""
        result = interpreter(body).decode(b"\x2a", fPort=7, direction="uplink")
        assert not result.success
        assert result.errors == [
            "fPort 7 declares unknown direction 'bidirectional'; "
            "expected both, downlink, uplink"
        ]

    def test_an_unknown_message_direction_is_a_caller_error(self):
        # A bad argument is a programming error, not a payload problem.
        with pytest.raises(ValueError) as caught:
            interpreter().decode(UPLINK_PAYLOAD, fPort=1, direction="sideways")
        assert "unknown message direction 'sideways'" in str(caught.value)


class TestGeneratedCodec:
    """The generated codec names the fault instead of reporting "Unknown fPort"."""

    def test_decode_uplink_refuses_a_downlink_port_by_name(self):
        result = decode_js(PORTED, list(UPLINK_PAYLOAD), 2, "decodeUplink")
        assert result["data"] == {}
        assert result["errors"] == [
            "fPort 2 is declared direction:downlink; message direction is uplink"
        ]
        assert result["warnings"] == []

    def test_decode_downlink_refuses_an_uplink_port_by_name(self):
        result = decode_js(PORTED, list(UPLINK_PAYLOAD), 1, "decodeDownlink")
        assert result["data"] == {}
        assert result["errors"] == [
            "fPort 1 is declared direction:uplink; message direction is downlink"
        ]

    def test_an_undefined_port_is_still_an_unknown_fport(self):
        # The two faults stay distinguishable: this port is genuinely not in the schema.
        result = decode_js(PORTED, list(UPLINK_PAYLOAD), 9, "decodeUplink")
        assert result["errors"] == []
        assert result["warnings"] == ["Unknown fPort: 9"]

    def test_each_entry_point_still_decodes_its_own_direction(self):
        up = decode_js(PORTED, list(UPLINK_PAYLOAD), 1, "decodeUplink")
        assert up["data"] == {"temperature": 23.5, "humidity": 65}
        down = decode_js(PORTED, list(UPLINK_PAYLOAD), 2, "decodeDownlink")
        assert down["data"]["reporting_interval"] == 60225

    def test_an_undeclared_port_is_reachable_from_both_entry_points(self):
        body = """
name: t
endian: big
ports:
  7:
    fields: [{name: x, type: u8}]
"""
        for fn in ("decodeUplink", "decodeDownlink"):
            result = decode_js(body, [42], 7, fn)
            assert result["data"] == {"x": 42}, fn
            assert result["errors"] == [], fn


class TestEncodeDirection:
    """PS-292: the mirror of the decode check, so a frame is not built for a port that
    disclaims the direction it would travel."""

    ENCODABLE = """
name: demo_sensor
endian: big
ports:
  1:
    direction: uplink
    fields: [{name: temperature, type: s16, div: 10}]
  2:
    direction: downlink
    fields: [{name: reporting_interval, type: u16}]
"""

    def test_a_downlink_for_an_uplink_port_is_an_error(self):
        result = interpreter(self.ENCODABLE).encode(
            {"temperature": 23.5}, fPort=1, direction="downlink"
        )
        assert not result.success
        assert result.errors == [
            "fPort 1 is declared direction:uplink; message direction is downlink"
        ]

    def test_no_payload_is_produced(self):
        # Emitting the bytes anyway would put a malformed frame on the air.
        result = interpreter(self.ENCODABLE).encode(
            {"temperature": 23.5}, fPort=1, direction="downlink"
        )
        assert result.payload == b""

    def test_an_uplink_for_a_downlink_port_is_an_error(self):
        result = interpreter(self.ENCODABLE).encode(
            {"reporting_interval": 60}, fPort=2, direction="uplink"
        )
        assert not result.success
        assert result.errors == [
            "fPort 2 is declared direction:downlink; message direction is uplink"
        ]

    def test_the_declared_direction_still_encodes(self):
        result = interpreter(self.ENCODABLE).encode(
            {"reporting_interval": 60}, fPort=2, direction="downlink"
        )
        assert result.success
        assert result.payload == b"\x00\x3c"

    def test_an_unsupplied_direction_encodes_as_before(self):
        result = interpreter(self.ENCODABLE).encode({"reporting_interval": 60}, fPort=2)
        assert result.success
        assert result.payload == b"\x00\x3c"

    def test_a_schema_level_declaration_is_checked(self):
        body = """
name: cfg
endian: big
direction: downlink
fields: [{name: reporting_interval, type: u16}]
"""
        result = interpreter(body).encode({"reporting_interval": 60}, direction="uplink")
        assert not result.success
        assert result.errors == [
            "schema 'cfg' is declared direction:downlink; message direction is uplink"
        ]
