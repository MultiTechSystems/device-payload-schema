"""CR-2026-058: the constructs the specification described and never required.

Held here for the reference interpreter, the generated codec and the validator; Go, Java
and C# carry their own CR-058 tests. The shared fixtures in _language-conformance cover
what a vector can express (round-ties-away, bytes-format, enum-description,
match-list-key, repeat-max*); this file covers the rest - every outcome that is a
rejection, an error, or an absent key, none of which a vector can assert.
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
    script = code + "\nprocess.stdout.write(JSON.stringify(decodeUplink(" + json.dumps(
        {"bytes": list(bytes.fromhex(payload_hex)), "fPort": 1}) + ")));"
    out = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def one(field, *after):
    return {"name": "probe", "fields": [field, *after]}


# PS-390 ------------------------------------------------------------------------------

REJECTED_STAGES = [
    ({"round": 1}, "{round: n}"),
    ({"op": "floor"}, "unknown operation"),
    ({"op": "ceil"}, "unknown operation"),
    ({"op": "round", "ties": "up"}, "ties"),
    ({"sub": 3}, "no operation"),
    # `{}` holds no operation at all, which CR-2026-073 made its own rule (PS-452);
    # tests/test_cr_2026_071_073.py has it.
]


@pytest.mark.parametrize("stage,why", REJECTED_STAGES, ids=[w for _, w in REJECTED_STAGES])
def test_a_stage_outside_the_table_is_rejected_by_the_reference(stage, why):
    result = decode(one({"name": "v", "type": "u8", "transform": [stage]}), "05")
    assert not result.success
    assert any("PS-390" in e for e in result.errors), result.errors


@pytest.mark.parametrize("stage,why", REJECTED_STAGES, ids=[w for _, w in REJECTED_STAGES])
def test_a_stage_outside_the_table_is_refused_by_the_generator(stage, why):
    with pytest.raises(ValueError, match="PS-390"):
        TS013Generator(one({"name": "v", "type": "u8", "transform": [stage]})).generate()


@pytest.mark.parametrize("ties,want", [("even", [78.12, -2]), ("away", [78.13, -3])])
def test_ties_on_both_paths(ties, want):
    schema = {"name": "probe", "fields": [
        {"name": "h", "type": "u8", "transform": [
            {"mult": 100}, {"div": 256}, {"op": "round", "decimals": 2, "ties": ties}]},
        {"name": "n", "type": "s8", "transform": [{"div": 2}, {"op": "round", "ties": ties}]},
    ]}
    expected = {"h": want[0], "n": want[1]}
    assert decode(schema, "C8FB").data == expected
    assert decode_js(schema, "C8FB")["data"] == expected


# PS-391 ------------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [{"format": "base64", "separator": ":"},
                                   {"format": "array", "separator": ":"},
                                   {"format": "hex:lower"}])
def test_an_invalid_bytes_rendering_is_rejected(extra):
    field = dict({"name": "b", "type": "bytes", "length": 2}, **extra)
    assert not decode(one(field), "0102").success
    assert validate_schema_structure(one(field))
    with pytest.raises(ValueError):
        TS013Generator(one(field)).generate()


# PS-394 ------------------------------------------------------------------------------

def test_the_description_form_round_trips_by_its_name():
    schema = one({"name": "m", "type": "enum", "values": {
        1: {"name": "standby", "description": "radio off"}}})
    interp = SchemaInterpreter(schema)
    assert interp.decode(b"\x01").data == {"m": "standby"}
    assert interp.encode({"m": "standby"}).payload == b"\x01"


# PS-396 ------------------------------------------------------------------------------

@pytest.mark.parametrize("bound,unparsed", [
    ({"count": "$n"}, "4 byte(s) at offset 1"),
    ({"until": "end"}, "2 byte(s) at offset 3"),
    ({"byte_length": 4}, "2 byte(s) at offset 3"),
])
def test_exceeding_max_is_an_error_naming_what_was_left(bound, unparsed):
    schema = {"name": "probe", "fields": [
        {"name": "n", "type": "u8", "var": "n"},
        dict({"name": "r", "type": "repeat", "max": 2,
              "fields": [{"name": "v", "type": "u8"}]}, **bound),
    ]}
    for errors in (decode(schema, "040A141E28").errors,
                   decode_js(schema, "040A141E28")["errors"]):
        assert any("'r'" in e and "max of 2" in e and unparsed in e for e in errors), errors


def test_no_field_after_an_exceeded_repeat_is_decoded():
    schema = {"name": "probe", "fields": [
        {"name": "r", "type": "repeat", "count": 3, "max": 2,
         "fields": [{"name": "v", "type": "u8"}]},
        {"name": "after", "type": "u8"},
    ]}
    result = decode(schema, "0A141E28")
    assert "after" not in result.data
    assert "after" not in decode_js(schema, "0A141E28")["data"]


# PS-397 ------------------------------------------------------------------------------

@pytest.mark.parametrize("members", [
    [{"name": "a", "type": "u8[0:3]"}, {"name": "b", "type": "u8[2:5]"}],
    [{"name": "a", "type": "u16[8:15]"}, {"name": "b", "type": "u8[0:3]"}],
    [{"name": "a", "type": "u8[0:0]"}, {"name": "b", "type": "bool", "bit": 0}],
])
def test_overlapping_members_are_rejected(members):
    schema = one({"byte_group": {"size": 2, "fields": members}})
    assert any("PS-397" in e for e in decode(schema, "0F00").errors)
    assert any("PS-397" in e for e in validate_schema_structure(schema))
    with pytest.raises(ValueError, match="PS-397"):
        TS013Generator(schema).generate()


def test_a_gap_between_members_is_permitted():
    schema = one({"byte_group": {"size": 1, "fields": [
        {"name": "a", "type": "u8[0:1]"}, {"name": "b", "type": "u8[6:7]"}]}})
    assert decode(schema, "C3").data == {"a": 3, "b": 3}


# PS-398, PS-399 ----------------------------------------------------------------------

@pytest.mark.parametrize("match", [
    {"field": "$k", "length": 1, "cases": {1: [{"name": "a", "type": "u8"}]}},
    {"cases": {1: [{"name": "a", "type": "u8"}]}},
])
def test_a_match_needs_exactly_one_discriminator_source(match):
    schema = one({"name": "k", "type": "u8", "var": "k"}, {"match": match})
    assert any("PS-399" in e for e in decode(schema, "0101").errors)
    assert validate_schema_structure(schema)
    with pytest.raises(ValueError, match="PS-399"):
        TS013Generator(schema).generate()


# PS-400 ------------------------------------------------------------------------------

def test_a_failed_guard_without_else_omits_the_field_on_both_paths():
    schema = {"name": "probe", "fields": [
        {"name": "raw", "type": "u8"},
        {"name": "scaled", "type": "number", "ref": "$raw", "mult": 2,
         "guard": {"when": [{"field": "$raw", "lt": 100}]}},
        {"name": "after", "type": "u8"},
    ]}
    assert decode(schema, "C805").data == {"raw": 200, "after": 5}
    assert decode_js(schema, "C805")["data"] == {"raw": 200, "after": 5}


# PS-389, PS-392, PS-393 (schema rules: the validator) ---------------------------------

@pytest.mark.parametrize("field,tag", [
    ({"name": "p", "type": "skip"}, "PS-392"),
    ({"name": "e", "type": "enum", "base": "s16", "values": {0: "a"}}, "PS-393"),
    ({"name": "f", "type": "bool", "bit": 8}, "PS-389"),
])
def test_the_validator_holds_the_schema_rules(field, tag):
    assert any(tag in e for e in validate_schema_structure(one(field)))
