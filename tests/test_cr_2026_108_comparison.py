"""CR-2026-108 in the vendor comparison: PS-512's 0x-prefixed renderings, compared by value.

"0x0a 10 Min" and "0x07 7" render the decimal after the hex token; a bare "0x01" renders its
integer value. A rendering nested inside a reported object is compared by value as well,
which the comparison used to skip (found by the private registry's re-audit: a vendor
`{"unit": "W", "value": "1.70"}` against our `{"unit": "W", "value": 1.7}`).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from crossvalidate_ttn import as_number  # noqa: E402


def test_a_hex_token_followed_by_a_decimal_renders_the_decimal():
    for text, want in (
        ("0x0a 10 Min", 10.0),
        ("0x07 7", 7.0),
        ("0x800c 12 h", 12.0),
        ("0x85 5 Min", 5.0),
        ("0X1e 30 s", 30.0),
    ):
        assert as_number(text, want) == want


def test_a_bare_hex_token_renders_its_integer_value():
    assert as_number("0x01", 1) == 1.0
    assert as_number("0x1A", 26) == 26.0
    assert as_number("0xff", 255) == 255.0


def test_a_hex_rendering_that_differs_still_disagrees():
    assert as_number("0x0a 10 Min", 11) == 10.0
    assert as_number("0x0a 10 Min", 11) != 11


def test_a_hex_rendering_against_a_string_stays_a_string():
    assert as_number("0x01", "0x01") == "0x01"
    assert as_number("0x01", True) == "0x01"


def test_a_rendering_nested_in_an_object_is_compared_by_value():
    want = {"unit": "W", "value": "1.70"}
    assert as_number(want, {"unit": "W", "value": 1.7}) == {"unit": "W", "value": 1.7}


def test_a_rendering_nested_in_a_list_is_compared_by_value():
    assert as_number([{"v": "0x02"}, "3.0V"], [{"v": 2}, 3]) == [{"v": 2.0}, 3.0]


def test_a_nested_shape_that_differs_is_left_alone():
    assert as_number(["1", "2"], [1]) == ["1", "2"]
    assert as_number({"a": "1"}, {"b": 1}) == {"a": "1"}
