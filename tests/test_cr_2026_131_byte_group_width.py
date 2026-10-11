"""CR-2026-131: the bytes a byte_group covers, and the members it admits (PS-070).

A group that declares no `size` covers its widest member's base type, whichever bytes
the member's bits fall in: a `u16[0:3]` member makes it two bytes. `size` may widen it;
a `size` narrower than a member's base is rejected, naming the group. A member is a bit
range, a whole integer (`u16` is `u16[0:15]`) or a `bool` with `bit` (one bit of a
`u8`); each reads from the group's start, and all of them take part in the width and
overlap checks.

All five implementations read a sizeless group as one byte, while the four encoders
already widened a bit range's base, so decode and encode disagreed. The cross-language
fixture is `_language-conformance/byte-group-width.yaml`; this file pins what a fixture
cannot: the rejections and their messages (the spec prototype's), the generated codec in
both directions, and PS-343's bare ragged-tail message, tidied in the same change.

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

NARROW = "byte_group declares size 5, narrower than its widest member's 8 bytes (PS-070)"
OVERLAP = "byte_group members 'w' and 'f' overlap (PS-397)"
HIGH_BIT = (
    "byte_group member 'f' is a bool at bit 9; a bool member is one bit of a u8, "
    "0 to 7 (PS-070)"
)
OTHER_KIND = (
    "byte_group member 'f' is not a bit range, a whole integer or a bool with `bit` "
    "(PS-364)"
)


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

#: The prototype's case: a whole u8 beside a range of the other byte, little-endian.
WHOLE_AND_RANGE = [
    {
        "byte_group": {
            "endian": "little",
            "fields": [{"name": "lo", "type": "u8"}, {"name": "hi", "type": "u16[8:11]"}],
        }
    },
    {"name": "n", "type": "u8"},
]

#: Each rejected group, beside the message it is rejected with.
REJECTED = [
    ({"byte_group": {"size": 5, "fields": [{"name": "a", "type": "u64[0:3]"}]}}, NARROW),
    ({"byte_group": [{"name": "a", "type": "u64[0:3]"}], "size": 5}, NARROW),
    (
        {
            "byte_group": [
                {"name": "w", "type": "u8"},
                {"name": "f", "type": "bool", "bit": 0},
            ]
        },
        OVERLAP,
    ),
    ({"byte_group": [{"name": "f", "type": "bool", "bit": 9}]}, HIGH_BIT),
    ({"byte_group": [{"name": "f", "type": "bool"}]}, OTHER_KIND),
    ({"byte_group": [{"name": "f", "type": "ascii", "length": 1}]}, OTHER_KIND),
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
            ({"byte_group": [{"name": "a", "type": "int16"}]}, 2),
            ({"byte_group": [{"name": "a", "type": "bool", "bit": 3}]}, 1),
            # `size` widens, on either spelling.
            ({"byte_group": {"size": 3, "fields": [{"name": "a", "type": "u16[0:3]"}]}}, 3),
            ({"byte_group": [{"name": "a", "type": "u8[0:3]"}], "size": 2}, 2),
            ({"byte_group": [{"name": "a", "type": "u16"}], "size": 2}, 2),
        ],
    )
    def test_the_widest_base_widened_by_size(self, field, width):
        assert byte_group_width(field) == width

    @pytest.mark.parametrize("field, message", REJECTED)
    def test_a_rejected_group_names_why(self, field, message):
        with pytest.raises(ValueError) as caught:
            byte_group_width(field)
        assert str(caught.value) == message


class TestRejection:
    """Python rejects at decode time, which PS-328 permits; the validator and the
    generator reject at once."""

    @pytest.mark.parametrize("field, message", REJECTED)
    def test_the_interpreter(self, field, message):
        result = SchemaInterpreter(schema_of([field])).decode(bytes(8))
        assert not result.success
        assert any(error.endswith(message) for error in result.errors), result.errors

    @pytest.mark.parametrize("field, message", REJECTED)
    def test_the_encoder(self, field, message):
        result = SchemaInterpreter(schema_of([field])).encode({})
        assert any(error.endswith(message) for error in result.errors), result.errors

    @pytest.mark.parametrize("field, message", REJECTED)
    def test_the_validator(self, field, message):
        errors = validate_schema(schema_of([field])).schema_errors
        assert any(error.endswith(": " + message) for error in errors), errors

    @pytest.mark.parametrize("field, message", REJECTED)
    def test_the_generator(self, field, message):
        with pytest.raises(ValueError) as caught:
            TS013Generator(schema_of([field])).generate()
        assert str(caught.value) == message

    def test_a_group_inside_a_tlv_case_is_checked_by_the_validator(self):
        schema = schema_of(
            [
                {
                    "tlv": {
                        "tag_size": 1,
                        "cases": {1: [REJECTED[0][0]]},
                    }
                }
            ]
        )
        errors = validate_schema(schema).schema_errors
        assert any(error.endswith(NARROW) for error in errors), errors


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

    def test_a_whole_integer_reads_from_the_start_without_moving_it(self):
        assert decode(schema_of(WHOLE_AND_RANGE), "050307") == {"lo": 5, "hi": 3, "n": 7}

    def test_a_whole_integer_is_its_full_range_of_the_group(self):
        # u8 is u8[0:7] of the assembled value: big-endian, that is the second byte. It
        # was read as a field of its own, from the first.
        fields = [
            {"byte_group": [{"name": "b", "type": "u8"}], "size": 2},
            {"name": "n", "type": "u8"},
        ]
        assert decode(schema_of(fields), "120309") == {"b": 3, "n": 9}

    def test_a_bool_is_one_bit_of_the_low_byte(self):
        # A bool is one bit of a u8: bit 0 of the assembled value, the second byte
        # big-endian, beside a signed range of the first.
        fields = [
            {
                "byte_group": [
                    {"name": "t", "type": "s16[8:15]"},
                    {"name": "flag", "type": "bool", "bit": 0},
                ]
            }
        ]
        assert decode(schema_of(fields), "FE01") == {"t": -2, "flag": True}


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

    def test_a_whole_integer_is_written_into_its_range(self):
        schema = schema_of(WHOLE_AND_RANGE)
        assert encode(schema, {"lo": 5, "hi": 3, "n": 7}) == "050307"

    def test_a_negative_whole_integer_writes_its_own_bits_only(self):
        fields = [
            {
                "byte_group": {
                    "endian": "little",
                    "fields": [
                        {"name": "t", "type": "s8"},
                        {"name": "hi", "type": "u16[8:11]"},
                    ],
                }
            }
        ]
        schema = schema_of(fields)
        assert decode(schema, "FE03") == {"t": -2, "hi": 3}
        assert encode(schema, {"t": -2, "hi": 3}) == "fe03"


class TestGeneratedCodec:
    @pytest.mark.parametrize(
        "fields, payload",
        [
            (LOW_NIBBLE, "000507"),
            # The array spelling's `size` beside the group was ignored by the generator.
            (
                [
                    {"byte_group": [{"name": "lo", "type": "u8[0:3]"}], "size": 2},
                    {"name": "n", "type": "u8"},
                ],
                "000507",
            ),
            (WHOLE_AND_RANGE, "050307"),
        ],
    )
    def test_decode_and_encode_agree_with_the_interpreter(self, fields, payload):
        schema = schema_of(fields)
        code = TS013Generator(schema).generate()
        decoded = node(
            code, "decodeUplink(" + json.dumps({"bytes": list(bytes.fromhex(payload))}) + ")"
        )
        assert decoded["data"] == decode(schema, payload)
        encoded = node(code, "encodeDownlink(" + json.dumps({"data": decoded["data"]}) + ")")
        assert bytes(encoded["bytes"]).hex() == payload


class TestValidator:
    def test_a_sizeless_group_is_valid(self):
        assert validate_schema(schema_of(LOW_NIBBLE)).schema_errors == []


class TestRaggedTailIsBare:
    """PS-343's message names the repeat already; Python alone prefixed it again with
    `Error decoding r:`, which the other four do not."""

    def test_the_message_is_the_other_implementations_text(self):
        schema = schema_of(
            [
                {
                    "name": "r",
                    "type": "repeat",
                    "until": "end",
                    "fields": [{"name": "v", "type": "u16"}],
                }
            ]
        )
        result = SchemaInterpreter(schema).decode(bytes.fromhex("000102"))
        assert result.errors == [
            "repeat 'r' ends in a ragged tail: 1 byte(s) at offset 2, fewer than the 2 "
            "an element takes (PS-343)"
        ]
