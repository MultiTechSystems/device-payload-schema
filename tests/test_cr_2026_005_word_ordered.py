"""PS-271 to PS-273: a 32-bit value carried as two 16-bit units, low unit first.

Four orderings exist for 32 bits spread over two 16-bit units. The specification could
express two of them - `u32` big-endian and `u32` little-endian - and the third, low unit
first with big-endian bytes inside each unit, had no type at all.

Its absence produced silently wrong schemas rather than errors. A converter reading the
vendor expression `x[0] + x[1] * 65536` has no correct type to emit and picks `u32`, which
reads the halves in the opposite order: `decentlab/dl-rhc` reported a sensor identifier of
2851930420 where the device reports 20228605, and `dl-isf` carried the same defect while
passing vendor cross-validation, because the vendor's own payload leaves those bytes zero,
where both readings agree.

The workaround was a chain of four fields and two `compute` steps to read four bytes, which
also made the result a real number: 20228605.0 where the codec the schema replaces reports
20228605.

CR-2026-005 adds `u32le16` and `s32le16`. The tests below pin the three things the
requirements settle: the value, that `endian` does not reach it (PS-272), and that the
result is an integer rather than the float the arithmetic workaround produced.
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

#: The CR's worked example: 43517 + 308 * 65536.
EXAMPLE_BYTES = "A9FD0134"
EXAMPLE_VALUE = 20228605
#: What `u32` big-endian reads from the same bytes, which is what schemas reported before.
U32_BIG_READING = 2851930420


def schema_for(declared, endian="big"):
    return {"name": "t", "endian": endian, "fields": [{"name": "v", "type": declared}]}


def decode(declared, payload_hex, endian="big"):
    return SchemaInterpreter(schema_for(declared, endian)).decode(
        bytes.fromhex(payload_hex)
    )


def decode_js(declared, payload_hex, endian="big"):
    js = TS013Generator(schema_for(declared, endian)).generate()
    octets = list(bytes.fromhex(payload_hex))
    driver = (
        js
        + f"\nvar _r = decodeUplink({{ bytes: {json.dumps(octets)}, fPort: 1 }});"
        + "\nconsole.log(JSON.stringify(_r.data));"
    )
    out = subprocess.run(["node", "-e", driver], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestTheValue:
    def test_the_worked_example(self):
        # PS-271: two big-endian 16-bit units, the first the less significant.
        assert decode("u32le16", EXAMPLE_BYTES).data["v"] == EXAMPLE_VALUE

    def test_it_differs_from_every_reading_u32_gives(self):
        # The reason the type is needed: no `u32` spelling produces this value.
        assert decode("u32", EXAMPLE_BYTES).data["v"] == U32_BIG_READING
        assert decode("u32", EXAMPLE_BYTES, endian="little").data["v"] != EXAMPLE_VALUE
        assert decode("u32le16", EXAMPLE_BYTES).data["v"] == EXAMPLE_VALUE

    @pytest.mark.parametrize(
        "payload,expected",
        [("00000000", 0), ("FFFFFFFF", 4294967295), ("FFFF0000", 65535),
         ("0000FFFF", 4294901760)],
    )
    def test_the_range_ends(self, payload, expected):
        assert decode("u32le16", payload).data["v"] == expected

    @pytest.mark.parametrize(
        "payload,expected",
        [("FFFFFFFF", -1), ("FFFEFFFF", -2), ("00008000", -2147483648),
         ("FFFF7FFF", 2147483647)],
    )
    def test_the_signed_form(self, payload, expected):
        assert decode("s32le16", payload).data["v"] == expected


class TestEndianDoesNotReachIt:
    """PS-272: the type fixes both orders."""

    @pytest.mark.parametrize("declared", ["u32le16", "s32le16"])
    def test_the_document_setting_is_ignored(self, declared):
        big = decode(declared, EXAMPLE_BYTES, endian="big").data["v"]
        little = decode(declared, EXAMPLE_BYTES, endian="little").data["v"]
        assert big == little == EXAMPLE_VALUE

    def test_otherwise_it_would_be_a_second_spelling_of_little_endian_u32(self):
        # The reading the CR rejected: suffix sets unit order, `endian` sets byte order.
        # Under it, u32le16 with endian little would equal little-endian u32.
        assert decode("u32le16", EXAMPLE_BYTES, endian="little").data["v"] != decode(
            "u32", EXAMPLE_BYTES, endian="little"
        ).data["v"]


class TestItReportsAnInteger:
    """The workaround reported 20228605.0; a field read reports 20228605 (PS-279)."""

    def test_the_value_is_an_integer(self):
        value = decode("u32le16", EXAMPLE_BYTES).data["v"]
        assert isinstance(value, int) and not isinstance(value, bool)

    def test_it_serializes_without_a_fraction(self):
        assert json.dumps(decode("u32le16", EXAMPLE_BYTES).data) == '{"v": 20228605}'


class TestRoundTrip:
    @pytest.mark.parametrize("declared,value", [("u32le16", EXAMPLE_VALUE),
                                                ("u32le16", 0),
                                                ("u32le16", 4294967295),
                                                ("s32le16", -1),
                                                ("s32le16", -2147483648)])
    def test_encode_inverts_decode(self, declared, value):
        interpreter = SchemaInterpreter(schema_for(declared))
        encoded = interpreter.encode({"v": value})
        assert encoded.success, encoded.errors
        assert interpreter.decode(encoded.payload).data["v"] == value

    def test_the_example_encodes_to_the_documented_bytes(self):
        encoded = SchemaInterpreter(schema_for("u32le16")).encode({"v": EXAMPLE_VALUE})
        assert encoded.payload.hex().upper() == EXAMPLE_BYTES


class TestGeneratedCodec:
    """The generated codec is the other conformance path (clause 9)."""

    def test_it_reads_the_worked_example(self):
        assert decode_js("u32le16", EXAMPLE_BYTES) == {"v": EXAMPLE_VALUE}

    def test_it_ignores_the_endian_setting_too(self):
        assert decode_js("u32le16", EXAMPLE_BYTES, endian="little") == {"v": EXAMPLE_VALUE}

    def test_the_signed_form(self):
        assert decode_js("s32le16", "FFFFFFFF") == {"v": -1}

    def test_it_agrees_with_the_interpreter_across_the_range(self):
        for payload in ("00000000", EXAMPLE_BYTES, "FFFF0000", "0000FFFF", "FFFFFFFF"):
            assert decode_js("u32le16", payload)["v"] == decode("u32le16", payload).data["v"]

    def test_it_round_trips(self):
        js = TS013Generator(schema_for("u32le16")).generate()
        driver = (
            js
            + f"\nvar e = encodeDownlink({{data: {{v: {EXAMPLE_VALUE}}}, fPort: 1}});"
            + "\nvar d = decodeUplink({bytes: e.bytes, fPort: 1});"
            + "\nconsole.log(JSON.stringify({bytes: e.bytes, back: d.data.v}));"
        )
        out = subprocess.run(["node", "-e", driver], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        result = json.loads(out.stdout)
        assert bytes(result["bytes"]).hex().upper() == EXAMPLE_BYTES
        assert result["back"] == EXAMPLE_VALUE
