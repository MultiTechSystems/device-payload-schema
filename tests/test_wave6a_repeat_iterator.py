"""0.5.2 wave 6a: the repeat iterator and reserved trailers.

CR-2026-048 (reserve, trailer), CR-2026-081 (tlv reserve), CR-2026-053 (index, count_as,
present_if), CR-2026-080 (count_as after present_if), CR-2026-055 (carry) and CR-2026-054's
identity and per-element annotations. The _language-conformance fixtures hold the decodes
in all five implementations; this file holds the reference interpreter, the generated
codec and the validator on what a vector cannot express - rejections, errors and encoding.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402


def decode(schema, payload_hex):
    return SchemaInterpreter(schema).decode(bytes.fromhex(payload_hex))


def decode_js(schema, payload_hex):
    code = TS013Generator(schema).generate()
    script = code + "\nvar r = decodeUplink(" + json.dumps(
        {"bytes": list(bytes.fromhex(payload_hex)), "fPort": 1}) + ");\n" \
        "process.stdout.write(JSON.stringify(r));"
    out = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def repeat(**extra):
    field = {"name": "r", "type": "repeat", "until": "end",
             "fields": [{"name": "v", "type": "u8"}]}
    field.update(extra)
    return {"name": "probe", "fields": [field]}


# Schema rules, rejected by the interpreter, the generator and the validator ---------

REJECTED = [
    ("PS-350", {"name": "p", "fields": [{"name": "r", "type": "repeat", "count": 1,
                                          "reserve": 1, "fields": [{"name": "v", "type": "u8"}]}]}),
    ("PS-350", repeat(reserve=-1)),
    ("PS-383", repeat(trailer=[{"name": "t", "type": "u8"}])),
    ("PS-384", repeat(reserve=2, trailer=[{"name": "t", "type": "u8"}])),
    ("PS-384", repeat(reserve=1, trailer=[{"name": "t", "type": "ascii", "length": "remaining"}])),
    ("PS-369", repeat(index="v")),
    ("PS-369", repeat(index="k", count_as="k")),
    ("PS-369", {"name": "p", "fields": [{"name": "outer", "type": "repeat", "count": 1, "index": "i",
                                          "fields": [{"name": "inner", "type": "repeat", "count": 1,
                                                      "index": "i",
                                                      "fields": [{"name": "v", "type": "u8"}]}]}]}),
    ("PS-386", repeat(present_if={"field": "$v"})),
    ("PS-372", repeat(identity="$nope")),
    ("PS-381", repeat(fields=[{"name": "v", "type": "u8", "carry": 0}])),
    ("PS-381", {"name": "p", "fields": [{"name": "x", "type": "number", "value": 1, "carry": 0}]}),
    ("PS-381", repeat(fields=[{"name": "v", "type": "u8"},
                              {"name": "t", "type": "number", "carry": "zero",
                               "compute": {"op": "add", "a": "$t", "b": "$v"}}])),
    ("PS-374", repeat(fields=[{"name": "code", "type": "u8"},
                              {"name": "v", "type": "u8", "unit": "$code"}])),
    ("PS-374", repeat(index="i", fields=[{"name": "v", "type": "u8",
                                          "ipso": {"object": 3303, "instance": "$i"}}])),
    ("PS-471", {"name": "p", "fields": [{"tlv": {"tag_size": 1, "reserve": -1,
                                                   "cases": {1: [{"name": "a", "type": "u8"}]}}}]}),
]


@pytest.mark.parametrize("tag,schema", REJECTED, ids=[f"{t}-{i}" for i, (t, _) in enumerate(REJECTED)])
def test_a_schema_rule_is_enforced_on_every_path(tag, schema):
    errors = decode(schema, "0102").errors
    assert any(tag in e for e in errors), errors
    assert any(tag in e for e in validate_schema_structure(schema))
    with pytest.raises(ValueError, match=tag):
        TS013Generator(schema).generate()


def test_a_valid_identity_schema_is_accepted():
    schema = repeat(index="i", max=4, identity="i", fields=[
        {"name": "code", "type": "u8", "lookup": {1: "Cel", 2: "%RH"}},
        {"name": "v", "type": "u8", "unit": "$code",
         "ipso": {"object": 3303, "instance": "$i"}, "senml": {"name": "t_${i}"}}])
    assert decode(schema, "0102").success
    assert not [e for e in validate_schema_structure(schema) if "PS-37" in e]


# PS-351, PS-471: too few bytes for the reserve -----------------------------------------

def test_too_few_bytes_for_a_repeat_reserve_is_an_error_on_both_paths():
    schema = repeat(reserve=2)
    assert any("PS-351" in e for e in decode(schema, "01").errors)
    assert any("PS-351" in e for e in decode_js(schema, "01")["errors"])


def test_too_few_bytes_for_a_tlv_reserve_is_an_error_on_both_paths():
    schema = {"name": "p", "fields": [
        {"tlv": {"tag_size": 1, "reserve": 2, "cases": {1: [{"name": "a", "type": "u8"}]}}}]}
    assert any("PS-471" in e for e in decode(schema, "01").errors)
    assert any("PS-471" in e for e in decode_js(schema, "01")["errors"])


def test_a_ragged_tail_is_measured_against_the_region():
    """PS-350: the ragged-tail rule applies to the payload less the reserved bytes."""
    schema = repeat(reserve=1, fields=[{"name": "v", "type": "u16"}])
    errors = decode(schema, "000102").errors   # 3 bytes: one u16, then one byte reserved
    assert not errors
    errors = decode(schema, "00010203").errors  # 3 in the region: a u16 and a ragged byte
    assert any("ragged" in e for e in errors), errors


# PS-368: element names are scoped --------------------------------------------------

def test_a_reference_from_outside_to_an_element_name_is_an_error():
    schema = {"name": "p", "fields": [
        {"name": "r", "type": "repeat", "count": 2, "fields": [{"name": "v", "type": "u8"}]},
        {"name": "last", "type": "number", "ref": "$v"}]}
    assert any("PS-368" in e for e in decode(schema, "0102").errors)


def test_a_reference_to_a_field_not_yet_decoded_is_an_error():
    schema = {"name": "p", "fields": [{"name": "r", "type": "repeat", "count": 1, "fields": [
        {"name": "early", "type": "number", "ref": "$v"}, {"name": "v", "type": "u8"}]}]}
    assert any("PS-368" in e for e in decode(schema, "05").errors)


def test_count_as_is_not_bound_inside_the_elements():
    """PS-367: count_as exists only once the repeat completes."""
    schema = {"name": "p", "fields": [{"name": "r", "type": "repeat", "count": 1,
                                        "count_as": "n", "fields": [
                                            {"name": "v", "type": "u8"},
                                            {"name": "seen", "type": "number", "ref": "$n"}]}]}
    result = decode(schema, "05")
    assert result.data["r"] == [{"v": 5, "seen": 0}]


def test_neither_index_nor_count_as_is_reported():
    """PS-370."""
    schema = repeat(index="i", count_as="n")
    assert decode(schema, "0102").data == {"r": [{"v": 1}, {"v": 2}]}


# Encoding: PS-370, PS-385, PS-387 --------------------------------------------------

def test_the_trailer_is_written_after_the_elements():
    schema = repeat(reserve=1, trailer=[{"name": "base", "type": "u8"}])
    interp = SchemaInterpreter(schema)
    assert interp.encode({"r": [{"v": 1}, {"v": 2}], "base": 9}).payload == b"\x01\x02\x09"


def test_the_index_is_bound_while_encoding():
    """PS-370: a name_from template resolves on encode as it did on decode."""
    schema = {"name": "p", "fields": [{"name": "r", "type": "repeat", "count": 2, "index": "i",
                                        "fields": [{"name": "v", "type": "u8",
                                                    "name_from": "ch_${i}"}]}]}
    interp = SchemaInterpreter(schema)
    decoded = interp.decode(b"\x07\x08").data
    assert decoded == {"r": [{"ch_0": 7}, {"ch_1": 8}]}
    assert interp.encode(decoded).payload == b"\x07\x08"


def test_present_if_over_byte_reading_elements_cannot_be_encoded():
    schema = repeat(present_if={"field": "$v", "ne": 0})
    result = SchemaInterpreter(schema).encode({"r": [{"v": 1}]})
    assert not result.success and any("PS-387" in e for e in result.errors)


def test_carry_from_a_field_decoded_before_the_repeat():
    """PS-378: carry may name a field decoded before the repeat."""
    schema = {"name": "p", "fields": [
        {"name": "start", "type": "u8"},
        {"name": "r", "type": "repeat", "count": 2, "fields": [
            {"name": "step", "type": "u8"},
            {"name": "level", "type": "number", "carry": "$start",
             "compute": {"op": "add", "a": "$level", "b": "$step"}}]}]}
    assert decode(schema, "640105").data["r"] == [{"step": 1, "level": 101},
                                                   {"step": 5, "level": 106}]
    assert decode_js(schema, "640105")["data"]["r"] == [{"step": 1, "level": 101},
                                                         {"step": 5, "level": 106}]
