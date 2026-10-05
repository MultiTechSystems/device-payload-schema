"""0.5.2 wave 4: CR-2026-046, -047, -049, -052, -063, -064 and -066.

The positive behaviour of each is held by a fixture in _language-conformance that all
five implementations decode (word-ordered, bit-range-order, minifloat, encoding,
bitfield-string-hex-case). This file holds what a vector cannot: the rejections, and the
minifloat encoder's choice of representation.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import (  # noqa: E402
    SchemaInterpreter, decode_minifloat, encode_minifloat)
from validate_schema import validate_schema_structure  # noqa: E402

REJECTED = {
    "encoding on a signed type (PS-422)": {"name": "v", "type": "s16", "encoding": "bcd"},
    "an unknown encoding (PS-422)": {"name": "v", "type": "u16", "encoding": "zigzag"},
    "match_value (PS-426)": {"name": "v", "type": "u8", "match_value": {"0..9": 1}},
    "a part format other than decimal, hex, hex:upper (PS-430)": {
        "name": "v", "type": "bitfield_string", "length": 1, "parts": [[0, 8, "octal"]]},
    "endian on a byte_group member (PS-364)": {"byte_group": {"size": 1, "fields": [
        {"name": "a", "type": "u8[0:3]", "endian": "little"}]}},
}


@pytest.mark.parametrize("label", sorted(REJECTED))
def test_rejected_by_the_reference_the_generator_and_the_validator(label):
    tag = label[label.index("(") + 1:-1]
    schema = {"name": "p", "fields": [REJECTED[label]]}
    errors = SchemaInterpreter(schema).decode(b"\x01\x02").errors
    assert any(tag in e for e in errors), errors
    assert any(tag in e for e in validate_schema_structure(schema))
    with pytest.raises(ValueError, match=tag):
        TS013Generator(schema).generate()


@pytest.mark.parametrize("ftype,value,word", [
    ("uflt16", 0.5, 0xF800),
    ("uflt16", 0.001, 0x6831),      # the normalised form, not f=4 at e=15
    ("sflt16", -0.25, 0xF400),          # sign, e=14, f=1024: -1024/2048 x 2^-1
    ("sflt24", 1.0, 0x3F0000),
])
def test_the_minifloat_encoder_takes_the_smallest_exponent(ftype, value, word):
    encoded = encode_minifloat(ftype, value)
    assert encoded == word
    assert decode_minifloat(ftype, encoded) == pytest.approx(value, rel=1e-3)


@pytest.mark.parametrize("ftype,value", [("uflt16", 1.0), ("uflt16", -0.1), ("sflt16", 1.5)])
def test_a_minifloat_out_of_range_is_an_error(ftype, value):
    with pytest.raises(ValueError, match="PS-420"):
        encode_minifloat(ftype, value)


@pytest.mark.parametrize("encoding,value", [("bcd", 10000), ("bcd", -1), ("sign_magnitude", 40000)])
def test_an_unrepresentable_encoded_value_is_rejected(encoding, value):
    schema = {"name": "p", "fields": [{"name": "v", "type": "u16", "encoding": encoding}]}
    result = SchemaInterpreter(schema).encode({"v": value})
    assert any("PS-463" in e for e in result.errors), result.errors


def test_a_bcd_digit_above_nine_is_an_error_not_a_number():
    schema = {"name": "p", "fields": [{"name": "v", "type": "u8", "encoding": "bcd"}]}
    assert not SchemaInterpreter(schema).decode(b"\x1A").success
