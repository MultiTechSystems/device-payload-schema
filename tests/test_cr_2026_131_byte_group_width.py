"""CR-2026-131: the bytes a byte_group covers (PS-070).

A group that declares no `size` covers its widest member's base type, whichever bytes
the member's bits fall in: a `u16[0:3]` member makes it two bytes. `size` may widen it,
never narrow it. All five implementations read a sizeless group as one byte, while
the four encoders already widened a bit range's base, so decode and encode disagreed.
The cross-language fixture is `_language-conformance/byte-group-width.yaml`; this file
pins what a fixture cannot: the generated codec in both directions, the validator's
warning on a `size` the members override, and PS-343's bare ragged-tail message, tidied
in the same change.

The cases mirror the spec prototype's TestCR2026131ByteGroupWidth.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter, byte_group_width  # noqa: E402
from validate_schema import validate_schema  # noqa: E402


def schema_of(fields, endian="big"):
    return {"name": "probe", "version": 1, "endian": endian, "fields": fields}


def decode(schema, payload):
    result = SchemaInterpreter(schema).decode(bytes.fromhex(payload))
    assert result.success, result.errors
    return result.data


def encode(schema, data):
    result = SchemaInterpreter(schema).encode(data)
    assert result.success, result.errors
    return result.payload.hex()


def node(code, call):
    done = subprocess.run(
        ["node"],
        input=code + "\nprocess.stdout.write(JSON.stringify(" + call + "));",
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


LOW_NIBBLE = [
    {"byte_group": [{"name": "lo", "type": "u16[0:3]"}]},
    {"name": "n", "type": "u8"},
]


class TestWidth:
    @pytest.mark.parametrize(
        "field, width",
        [
            ({"byte_group": [{"name": "a", "type": "u8[0:3]"}]}, 1),
            ({"byte_group": [{"name": "a", "type": "u16[0:3]"}]}, 2),
            ({"byte_group": [{"name": "a", "type": "s24[4:9]"}]}, 3),
            ({"byte_group": {"fields": [{"name": "a", "type": "u32[0:7]"}]}}, 4),
            ({"byte_group": [{"name": "a", "type": "u64[0:1]"}]}, 8),
            ({"byte_group": [{"name": "a", "type": "u16"}]}, 2),
            ({"byte_group": [{"name": "a", "type": "bool", "bit": 3}]}, 1),
            # `size` widens, on either spelling...
            ({"byte_group": {"size": 3, "fields": [{"name": "a", "type": "u16[0:3]"}]}}, 3),
            ({"byte_group": [{"name": "a", "type": "u8[0:3]"}], "size": 2}, 2),
            # ...and never narrows.
            ({"byte_group": {"size": 1, "fields": [{"name": "a", "type": "u32[0:3]"}]}}, 4),
        ],
    )
    def test_the_widest_base_widened_by_size(self, field, width):
        assert byte_group_width(field) == width


class TestDecode:
    def test_a_u16_member_makes_the_group_two_bytes(self):
        # The prototype's case: little-endian, the member's bits all in the first byte.
        assert decode(schema_of(LOW_NIBBLE, "little"), "0F0007") == {"lo": 15, "n": 7}

    def test_big_endian_bits_in_the_second_byte(self):
        # One byte read, bits 0-3 were the first byte's: 0xA, and n read 0x05.
        assert decode(schema_of(LOW_NIBBLE), "A00507") == {"lo": 5, "n": 7}

    def test_a_short_payload_is_an_error(self):
        result = SchemaInterpreter(schema_of(LOW_NIBBLE)).decode(bytes.fromhex("0F"))
        assert not result.success

    def test_a_narrow_size_is_overridden(self):
        fields = [
            {"byte_group": {"size": 1, "fields": [{"name": "w", "type": "u16[0:15]"}]}},
            {"name": "n", "type": "u8"},
        ]
        assert decode(schema_of(fields), "123409") == {"w": 0x1234, "n": 9}


class TestEncode:
    def test_the_widened_group_is_written(self):
        assert encode(schema_of(LOW_NIBBLE), {"lo": 5, "n": 7}) == "000507"

    def test_a_size_with_only_narrow_members_is_written_in_full(self):
        fields = [
            {"byte_group": [{"name": "a", "type": "u8[0:3]"}], "size": 2},
            {"name": "n", "type": "u8"},
        ]
        schema = schema_of(fields)
        assert decode(schema, "000307") == {"a": 3, "n": 7}
        assert encode(schema, {"a": 3, "n": 7}) == "000307"


class TestGeneratedCodec:
    @pytest.mark.parametrize(
        "fields",
        [
            LOW_NIBBLE,
            # The array spelling's `size` beside the group was ignored by the generator.
            [
                {"byte_group": [{"name": "lo", "type": "u8[0:3]"}], "size": 2},
                {"name": "n", "type": "u8"},
            ],
        ],
    )
    def test_decode_and_encode_agree_with_the_interpreter(self, fields):
        schema = schema_of(fields)
        code = TS013Generator(schema).generate()
        payload = "000507"
        decoded = node(
            code, "decodeUplink(" + json.dumps({"bytes": list(bytes.fromhex(payload))}) + ")"
        )
        assert decoded["data"] == decode(schema, payload)
        encoded = node(
            code, "encodeDownlink(" + json.dumps({"data": decoded["data"]}) + ")"
        )
        assert bytes(encoded["bytes"]).hex() == payload


class TestValidator:
    def test_a_size_narrower_than_a_member_is_warned(self):
        fields = [
            {"byte_group": {"size": 5, "fields": [{"name": "w", "type": "u64[0:39]"}]}}
        ]
        warnings = validate_schema(schema_of(fields)).schema_warnings
        assert any("covers 8 bytes (PS-070)" in w for w in warnings), warnings

    def test_a_sizeless_group_is_not_warned(self):
        warnings = validate_schema(schema_of(LOW_NIBBLE)).schema_warnings
        assert not any("PS-070" in w for w in warnings)


class TestRaggedTailIsBare:
    """PS-343's message names the repeat already; Python alone prefixed it again with
    `Error decoding r:`, which the other four do not."""

    def test_the_message_is_the_other_implementations_text(self):
        schema = schema_of(
            [{"name": "r", "type": "repeat", "until": "end",
              "fields": [{"name": "v", "type": "u16"}]}]
        )
        result = SchemaInterpreter(schema).decode(bytes.fromhex("000102"))
        assert result.errors == [
            "repeat 'r' ends in a ragged tail: 1 byte(s) at offset 2, fewer than the 2 "
            "an element takes (PS-343)"
        ]
