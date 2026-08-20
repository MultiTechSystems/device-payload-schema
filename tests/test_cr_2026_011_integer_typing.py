"""PS-279, PS-293 to PS-296: what a decoder reports for an integer it cannot hold.

A `u64` used to decode eight ways across the implementations. Two reported a negative
number for a field the specification maps to uint64, one reported 2^64 - a value larger
than the declared type can hold - and only Python was exact. Every answer was a defensible
reading, because PS-279's table is written in JSON types and says nothing about precision,
and PS-039's exact-match rule has no counterpart for the decoder itself.

CR-2026-011 fixes the reading:

- An integer-typed field is reported through an integer channel (PS-293) with its exact
  decoded value (PS-294).
- Where the implementation cannot represent it exactly, the value is a decimal string or
  the field is absent with an error - never rounded, wrapped or sign-changed (PS-295).
- Implementations document their exact-integer limit; a generated TS013 codec reports
  through a JSON number, so its limit is 2^53-1 (PS-296).

The interpreter here needs none of PS-295's escape: Python integers are arbitrary
precision, so the exactness requirement is met directly and the test's job is to keep it
that way. The generated codec does need it, and its half of this file is where the string
form is exercised.
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

TWO_53 = 2 ** 53
U64_MAX = 2 ** 64 - 1
S64_MIN = -(2 ** 63)


def decode(field, payload_hex):
    schema = {"name": "t", "endian": "big", "fields": [dict(field, name="v")]}
    return SchemaInterpreter(schema).decode(bytes.fromhex(payload_hex)).data["v"]


def decode_js(field, octets):
    schema = {"name": "t", "endian": "big", "fields": [dict(field, name="v")]}
    js = TS013Generator(schema).generate()
    driver = (
        js
        + f"\nvar _r = decodeUplink({{ bytes: {json.dumps(octets)}, fPort: 1 }});"
        + "\nconsole.log(JSON.stringify({v: _r.data.v, t: typeof _r.data.v}));"
    )
    out = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestInterpreterExactness:
    """PS-294: the exact decoded value, at every width."""

    def test_a_u64_at_the_top_of_its_range_is_exact(self):
        assert decode({"type": "u64"}, "FF" * 8) == U64_MAX

    def test_a_u64_just_above_the_double_range_is_exact(self):
        # 2^53+1 is the first integer a double cannot hold, and the value six
        # implementations rounded to 2^53.
        assert decode({"type": "u64"}, "0020000000000001") == TWO_53 + 1

    def test_an_s64_at_the_bottom_of_its_range_is_exact(self):
        # Two implementations reported this as -1 and two as s64 maximum, from a shift
        # count masked to the operand's width.
        assert decode({"type": "s64"}, "8000000000000000") == S64_MIN

    def test_a_u64_is_never_negative(self):
        # The specification maps u64 to uint64; a negative reading is not a precision
        # limitation but the wrong answer, and indistinguishable from a signed field's.
        assert decode({"type": "u64"}, "FF" * 8) > 0

    def test_a_u64_never_exceeds_its_own_range(self):
        assert decode({"type": "u64"}, "FF" * 8) <= U64_MAX


class TestInterpreterTyping:
    """PS-279, PS-293: the reported type follows the declaration, not the value."""

    @pytest.mark.parametrize("declared", ["u8", "u16", "u32", "u64", "s8", "s16", "s32"])
    def test_an_integer_width_reports_an_integer(self, declared):
        value = decode({"type": declared}, "01" * 8)
        assert isinstance(value, int) and not isinstance(value, bool)

    def test_a_modifier_makes_the_field_a_number(self):
        assert decode({"type": "s16", "div": 10}, "00EB") == 23.5

    def test_an_integral_scaled_reading_still_serializes_without_a_fraction(self):
        # PS-280 is unchanged by this CR: a `number` whose reading is whole prints as 20.
        schema = {"name": "t", "endian": "big",
                  "fields": [{"name": "v", "type": "s16", "div": 10}]}
        assert json.dumps(SchemaInterpreter(schema).decode(bytes.fromhex("00C8")).data) \
            == '{"v": 20}'


class TestGeneratedCodec:
    """PS-295, PS-296: a JavaScript codec reports the string form above 2^53-1."""

    def test_a_small_u64_is_still_a_number(self):
        assert decode_js({"type": "u64"}, [0, 0, 0, 0, 0, 0, 0, 60]) == {"v": 60, "t": "number"}

    def test_a_u64_above_the_safe_range_is_an_exact_decimal_string(self):
        result = decode_js({"type": "u64"}, [255] * 8)
        assert result == {"v": "18446744073709551615", "t": "string"}
        # The value it used to report, 18446744073709552000, is 385 too high.
        assert int(result["v"]) == U64_MAX

    def test_the_first_unrepresentable_integer_is_a_string(self):
        result = decode_js({"type": "u64"}, [0, 0x20, 0, 0, 0, 0, 0, 1])
        assert int(result["v"]) == TWO_53 + 1
        assert result["t"] == "string"

    def test_an_s64_minimum_is_exact_and_signed(self):
        result = decode_js({"type": "s64"}, [0x80, 0, 0, 0, 0, 0, 0, 0])
        assert int(result["v"]) == S64_MIN

    def test_a_small_negative_s64_is_still_a_number(self):
        assert decode_js({"type": "s64"}, [255] * 8) == {"v": -1, "t": "number"}

    def test_a_scaled_field_stays_a_number(self):
        # PS-279: a modifier makes it a `number`, where the platform's precision limit is
        # inherent rather than a violation.
        result = decode_js({"type": "u64", "div": 1000}, [255] * 8)
        assert result["t"] == "number"

    def test_the_interpreter_and_the_codec_agree_below_the_limit(self):
        octets = [0, 0, 0, 0, 0, 0, 0x01, 0x2C]
        assert decode_js({"type": "u64"}, octets)["v"] == decode({"type": "u64"}, "000000000000012C")
