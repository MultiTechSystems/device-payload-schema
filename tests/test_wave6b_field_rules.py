"""0.5.2 wave 6b: field-level rules.

CR-2026-059 (optional), -062 (match on bytes remaining), -065 (sentinel, out_of_range:
omit), -067 (internal fields) and -056 (a length naming a field). The _language-conformance
fixtures hold the decodes in all five implementations; this file holds the reference
interpreter, the generated codec and the validator on what a vector cannot express.
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


def decode(schema, payload_hex, **kw):
    return SchemaInterpreter(schema).decode(bytes.fromhex(payload_hex), **kw)


def decode_js(schema, payload_hex):
    code = TS013Generator(schema).generate()
    script = code + "\nprocess.stdout.write(JSON.stringify(decodeUplink(" + json.dumps(
        {"bytes": list(bytes.fromhex(payload_hex)), "fPort": 1}) + ")));"
    out = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def fields(*items):
    return {"name": "probe", "fields": list(items)}


# Schema rules: interpreter, generator and validator ---------------------------------

REJECTED = [
    ("PS-404", fields({"name": "a", "type": "u8", "optional": True}, {"name": "b", "type": "u8"})),
    ("PS-404", fields({"name": "r", "type": "repeat", "count": 1, "fields": [
        {"name": "a", "type": "u8", "optional": True}, {"name": "b", "type": "u8"}]})),
    ("PS-433", fields({"name": "_meta", "type": "u8"})),
    ("PS-433", fields({"name": "_quality", "type": "number", "value": 1})),
    ("PS-416", fields({"name": "k", "type": "u8", "var": "k"},
                      {"match": {"field": "$k", "remaining": True,
                                 "cases": {1: [{"name": "a", "type": "u8"}]}}})),
]


@pytest.mark.parametrize("tag,schema", REJECTED, ids=[f"{t}-{i}" for i, (t, _) in enumerate(REJECTED)])
def test_a_schema_rule_is_enforced_on_every_path(tag, schema):
    assert any(tag in e for e in decode(schema, "0102").errors)
    assert any(tag in e for e in validate_schema_structure(schema))
    with pytest.raises(ValueError, match=tag):
        TS013Generator(schema).generate()


@pytest.mark.parametrize("schema,why", [
    (fields({"name": "t", "type": "u8", "sentinel": 5}), "PS-427"),
    (fields({"name": "t", "type": "u8", "sentinel": ["x"]}), "PS-427"),
    (fields({"name": "t", "type": "u8", "valid_range": [0, 1], "out_of_range": "drop"}), "PS-428"),
    (fields({"name": "b", "type": "bytes", "length": "later"}, {"name": "later", "type": "u8"}),
     "PS-465"),
    (fields({"match": {"remaining": False, "cases": {1: [{"name": "a", "type": "u8"}]}}}), "PS-414"),
])
def test_the_validator_holds_the_field_rules(schema, why):
    assert any(why in e for e in validate_schema_structure(schema))


# PS-402, PS-403, PS-405 --------------------------------------------------------------

OPTIONAL = fields({"name": "a", "type": "u8"},
                  {"name": "fw", "type": "u16", "optional": True},
                  {"name": "ble", "type": "u16", "optional": True})


def test_some_bytes_but_too_few_is_an_error_on_both_paths():
    assert any("PS-403" in e for e in decode(OPTIONAL, "0102").errors)
    assert any("PS-403" in e for e in decode_js(OPTIONAL, "0102")["errors"])


def test_an_optional_object_is_present_or_absent_whole():
    schema = fields({"name": "a", "type": "u8"},
                    {"name": "gps", "type": "object", "optional": True,
                     "fields": [{"name": "lat", "type": "s16"}, {"name": "lon", "type": "s16"}]})
    assert decode(schema, "01").data == {"a": 1}
    assert decode(schema, "0100010002").data == {"a": 1, "gps": {"lat": 1, "lon": 2}}
    assert any("PS-403" in e for e in decode(schema, "010001").errors)


def test_an_optional_field_supplied_after_an_omitted_one_is_rejected():
    result = SchemaInterpreter(OPTIONAL).encode({"a": 1, "ble": 2})
    assert not result.success and any("PS-405" in e for e in result.errors)


def test_omitted_optional_fields_write_nothing():
    assert SchemaInterpreter(OPTIONAL).encode({"a": 1}).payload == b"\x01"


# PS-414: the reserve of an enclosing repeat is excluded -----------------------------

def test_remaining_excludes_an_enclosing_reserve():
    schema = fields({"name": "r", "type": "repeat", "until": "end", "reserve": 1, "max": 1,
                     "fields": [{"match": {"remaining": True, "cases": {
                         2: [{"name": "pair", "type": "u16"}]}}}]},
                    {"name": "tail", "type": "u8"})
    assert decode(schema, "0102FF").data == {"r": [{"pair": 258}], "tail": 255}
    assert decode_js(schema, "0102FF")["data"] == {"r": [{"pair": 258}], "tail": 255}


# PS-427, PS-428: absence and `_quality` ---------------------------------------------

def test_sentinel_compares_the_bits_before_an_encoding():
    schema = fields({"name": "v", "type": "u8", "encoding": "bcd", "sentinel": [255]})
    assert decode(schema, "FF").data == {}
    assert decode(schema, "42").data == {"v": 42}


def test_absent_readings_join_quality_only_where_it_is_produced():
    with_range = fields({"name": "t", "type": "s16", "sentinel": [-32768]},
                        {"name": "x", "type": "u8", "valid_range": [0, 10]})
    assert decode(with_range, "800005").data["_quality"] == {"x": "good", "t": "absent"}
    assert decode_js(with_range, "800005")["data"]["_quality"] == {"x": "good", "t": "absent"}
    alone = fields({"name": "t", "type": "s16", "sentinel": [-32768]})
    assert "_quality" not in decode(alone, "8000").data


def test_an_omitted_value_is_not_bound_for_later_references():
    """PS-429: a reference to it is a reference to an absent field."""
    schema = fields({"name": "t", "type": "u8", "sentinel": [255]},
                    {"name": "u", "type": "number", "ref": "$t", "mult": 2})
    assert "u" not in decode(schema, "05").data or decode(schema, "05").data["u"] == 10
    assert decode(schema, "FF").data.get("u", 0) == 0


# PS-434 ------------------------------------------------------------------------------

def test_an_internal_field_with_no_value_and_no_input_is_an_encode_error():
    schema = fields({"name": "_version", "type": "u8"}, {"name": "a", "type": "u8"})
    result = SchemaInterpreter(schema).encode({"a": 1})
    assert not result.success and any("PS-434" in e for e in result.errors)


def test_an_internal_field_takes_its_value_from_the_input_where_supplied():
    schema = fields({"name": "_version", "type": "u8"}, {"name": "a", "type": "u8"})
    assert SchemaInterpreter(schema).encode({"_version": 3, "a": 1}).payload == b"\x03\x01"


def test_an_internal_value_is_written_whatever_the_input_says():
    schema = fields({"name": "_version", "type": "u8", "value": 2}, {"name": "a", "type": "u8"})
    assert SchemaInterpreter(schema).encode({"_version": 9, "a": 1}).payload == b"\x02\x01"


def test_an_internal_bit_range_follows_the_same_rule():
    schema = fields({"name": "_r", "type": "u8[0:3]"}, {"name": "v", "type": "u8[4:7]",
                                                         "consume": 1})
    assert any("PS-434" in e for e in SchemaInterpreter(schema).encode({"v": 1}).errors)


def test_an_internal_computed_field_writes_nothing():
    schema = fields({"name": "a", "type": "u8"},
                    {"name": "_double", "type": "number", "ref": "$a", "mult": 2})
    assert SchemaInterpreter(schema).encode({"a": 4}).payload == b"\x04"


# PS-465 ------------------------------------------------------------------------------

def test_an_unresolved_length_names_the_field_on_both_paths():
    schema = fields({"name": "b", "type": "bytes", "length": "$n"})
    assert any("PS-465" in e and "'n'" in e for e in decode(schema, "01").errors)
    assert any("PS-465" in e for e in decode_js(schema, "01")["errors"])
