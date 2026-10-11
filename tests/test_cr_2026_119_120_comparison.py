"""CR-2026-119 (PS-512) and CR-2026-120 (PS-514, PS-515) in the vendor comparison tools.

CR-2026-119: a unit symbol begins with a letter, ℃, °, % or /, and may continue with those,
superscript digits and spaces, so dragino uv254-lb's "3.073mw/cm ²" renders 3.073. A unit
still never begins with a digit.

CR-2026-120: a correction's `field` may be a `/`-joined path to a nested member, with
zero-based array indices. The validator resolves it into `expected`, PS-515 counts the
asserted fields at the depth of the deepest path, and the comparison skips exactly that
member rather than the whole top-level key.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from crossvalidate_ttn import as_number, compare, skip_corrected  # noqa: E402
from validate_schema import (  # noqa: E402
    asserted_paths,
    correction_errors,
    is_independent_vector,
    resolve_member,
)

INDEPENDENT = {"vendor-doc", "vendor-codec", "field-capture", "spec-example"}


# --- CR-2026-119: PS-512's unit symbol ----------------------------------------------


def test_a_unit_with_a_space_and_a_superscript_renders_the_number():
    assert as_number("3.073mw/cm ²", 3.073) == 3.073
    assert as_number("12 m³/h", 12) == 12.0
    assert as_number("4 m²", 4) == 4.0


def test_the_existing_unit_forms_still_render():
    for text, want in (("257mm", 257.0), ("18℃", 18.0), ("-70dBm", -70.0)):
        assert as_number(text, want) == want
    assert as_number("80 %", 80) == 80.0


def test_a_unit_never_begins_with_a_digit_or_superscript():
    assert as_number("3.1 2.5", 3.1) == "3.1 2.5"
    assert as_number("3 ²m", 3) == "3 ²m"
    assert as_number("3.1(low battery)", 3.1) == "3.1(low battery)"


def test_the_hex_forms_take_the_widened_unit_and_are_tried_first():
    assert as_number("0x0a 10 m³/h", 10) == 10.0
    assert as_number("0xff", 255) == 255.0
    assert as_number("0x0a 10 Min", 10) == 10.0


# --- CR-2026-120: PS-514 paths ------------------------------------------------------

NESTED = {"payload": {"device": {"batteryLevel": None, "temperature": 21.5}}}
ARRAY = {"readings": [{"value": None, "unit": "C"}, {"value": 3, "unit": "C"}]}


def corrected(expected, fields):
    return {
        "payload": "00",
        "source": "vendor-codec-corrected",
        "expected": expected,
        "correction": [
            {"field": f, "codec": "external", "reason": "placeholder"} for f in fields
        ],
    }


def test_a_path_resolves_into_expected():
    assert resolve_member(NESTED, "payload/device/batteryLevel") == (True, None)
    assert resolve_member(ARRAY, "readings/1/value") == (True, 3)
    assert resolve_member(NESTED, "temperature")[0] is False
    assert resolve_member(NESTED, "payload/device/missing")[0] is False
    assert resolve_member(ARRAY, "readings/2/value")[0] is False
    assert resolve_member(ARRAY, "readings/x/value")[0] is False


def test_a_corrected_member_must_be_asserted():
    assert correction_errors(corrected(NESTED, ["payload/device/batteryLevel"])) == []
    assert correction_errors(corrected(ARRAY, ["readings/0/value"])) == []
    errors = correction_errors(corrected(NESTED, ["payload/device/humidity"]))
    assert errors and "is not in 'expected'" in errors[0]


def test_a_plain_name_still_works():
    vec = corrected({"temperature": None, "battery": 3.1}, ["temperature"])
    assert correction_errors(vec) == []
    assert is_independent_vector(vec, INDEPENDENT)
    assert not is_independent_vector(
        corrected({"temperature": None}, ["temperature"]), INDEPENDENT
    )


def test_ps515_counts_at_the_depth_of_the_deepest_path():
    assert asserted_paths(NESTED, 3) == [
        ["payload", "device", "batteryLevel"],
        ["payload", "device", "temperature"],
    ]
    # One leaf corrected: its sibling is still the codec's.
    vec = corrected(NESTED, ["payload/device/batteryLevel"])
    assert is_independent_vector(vec, INDEPENDENT)
    # Every leaf corrected: nothing is the codec's.
    both = corrected(
        NESTED, ["payload/device/batteryLevel", "payload/device/temperature"]
    )
    assert not is_independent_vector(both, INDEPENDENT)
    # The whole object corrected by its name is still every field.
    assert not is_independent_vector(corrected(NESTED, ["payload"]), INDEPENDENT)


def test_ps515_with_array_indices_and_a_shallower_sibling():
    vec = corrected(ARRAY, ["readings/0/value"])
    assert is_independent_vector(vec, INDEPENDENT)
    # A leaf above the deepest path is one asserted field of its own.
    flat = dict(NESTED, battery=3.1)
    assert is_independent_vector(
        corrected(flat, ["payload/device/batteryLevel", "payload/device/temperature"]),
        INDEPENDENT,
    )
    covered = corrected(flat, ["payload", "battery"])
    assert not is_independent_vector(covered, INDEPENDENT)


def test_skip_corrected_drops_exactly_the_member():
    want = {"payload": {"device": {"batteryLevel": "external", "temperature": 21.5}}}
    got = {"payload": {"device": {"temperature": 21.5}}}
    w, g = skip_corrected(want, got, {"payload/device/batteryLevel"})
    assert w == {"payload": {"device": {"temperature": 21.5}}}
    assert g == got
    # The originals are not modified.
    assert want["payload"]["device"]["batteryLevel"] == "external"


def test_skip_corrected_keeps_array_indices_aligned():
    want = {"readings": [{"value": "NULL"}, {"value": 3}]}
    got = {"readings": [{}, {"value": 3}]}
    w, g = skip_corrected(want, got, {"readings/0/value"})
    assert w == g == {"readings": [{}, {"value": 3}]}
    w, g = skip_corrected(want, got, {"readings/0"})
    assert w == g == {"readings": [None, {"value": 3}]}


SCHEMA = {
    "name": "nested",
    "fields": [
        {
            "name": "payload",
            "type": "object",
            "fields": [
                {
                    "name": "device",
                    "type": "object",
                    "fields": [{"name": "temperature", "type": "u8"}],
                }
            ],
        }
    ],
    "test_vectors": [
        {
            "name": "placeholder",
            "payload": "15",
            "source": "vendor-codec-corrected",
            "expected": {
                "payload": {"device": {"temperature": 21, "batteryLevel": None}}
            },
            "correction": [
                {
                    "field": "payload/device/batteryLevel",
                    "codec": "external",
                    "reason": "placeholder for an external supply",
                }
            ],
        }
    ],
}


def test_compare_skips_the_member_and_still_compares_its_siblings():
    vendor = {"payload": {"device": {"temperature": 21, "batteryLevel": "external"}}}
    assert compare(SCHEMA, bytes.fromhex("15"), None, vendor) == []
    wrong = {"payload": {"device": {"temperature": 22, "batteryLevel": "external"}}}
    problems = compare(SCHEMA, bytes.fromhex("15"), None, wrong)
    assert problems and problems[0].startswith("payload:")
    # Another payload carries no correction, so the member is compared.
    assert compare(SCHEMA, bytes.fromhex("16"), None, vendor)
