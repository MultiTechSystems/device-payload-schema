"""CR-2026-104: a lookup label may be a boolean (PS-106), matched only as one (PS-513).

The fixtures lookup-boolean-labels.yaml and lookup-number-labels.yaml hold the decode and
the round trip in every implementation. This file holds what a vector cannot express, on
the reference interpreter, the generated codec, the validator and the C binary form:

- PS-407: a `${value}` default beside a boolean label is a schema error;
- PS-513: `true` is not 1, `1` is not `true`, and "true" is not `true`, on encode;
- PS-409: a boolean only the `default` carries has no key to write.

Python's `True == 1` and `hash(True) == hash(1)` are why the reference wrote a
default-only `true` as 1 and found `1` through `[true, false]` as index 0.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import lookup_json_schema  # noqa: E402
from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_binary import schema_errors  # noqa: E402
from schema_interpreter import SchemaInterpreter, same_label  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

PROBE = {
    "name": "probe",
    "fields": [
        {"name": "m", "type": "u8[4:7]", "lookup": {0: False, "default": True}},
        {"name": "q", "type": "u8[0:0]", "consume": 1, "lookup": [True, False]},
    ],
}


def one(lookup):
    return {"name": "probe", "fields": [{"name": "x", "type": "u8", "lookup": lookup}]}


def encode(schema, data):
    result = SchemaInterpreter(schema).encode(data)
    return result.payload.hex(), result.errors


def run_js(schema, call):
    code = TS013Generator(schema).generate()
    script = code + "\nprocess.stdout.write(JSON.stringify(%s));" % call
    out = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def encode_js(schema, data):
    out = run_js(schema, "encodeDownlink(%s)" % json.dumps({"data": data}))
    return bytes(out.get("bytes") or []).hex(), out["errors"]


# PS-106: decoding -------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,want", [("31", {"m": True, "q": False}), ("00", {"m": False, "q": True})]
)
def test_decode_reports_json_booleans(payload, want):
    data = SchemaInterpreter(PROBE).decode(bytes.fromhex(payload)).data
    assert data == want
    assert all(type(data[k]) is bool for k in want)
    js = run_js(
        PROBE,
        "decodeUplink({bytes: %s, fPort: 1}).data"
        % json.dumps(list(bytes.fromhex(payload))),
    )
    assert js == want


# PS-513: encoding -------------------------------------------------------------------


@pytest.mark.parametrize("encoder", [encode, encode_js])
def test_the_sequence_form_encodes_by_index(encoder):
    schema = one([True, False])
    assert encoder(schema, {"x": False}) == ("01", [])
    assert encoder(schema, {"x": True}) == ("00", [])


@pytest.mark.parametrize("encoder", [encode, encode_js])
def test_a_default_only_boolean_has_no_key_to_write(encoder):
    schema = one({0: False, "default": True})
    assert encoder(schema, {"x": False}) == ("00", [])
    payload, errors = encoder(schema, {"x": True})
    assert errors and "PS-513" in errors[0], (payload, errors)


@pytest.mark.parametrize("encoder", [encode, encode_js])
def test_true_is_not_one(encoder):
    # A boolean input never matches a number label ...
    payload, errors = encoder(one({1: 1, 2: 2}), {"x": True})
    assert errors, payload
    # ... and a number input never matches a boolean label: 1 is not `true`, so it is
    # the value itself, not the index of `true`.
    assert encoder(one([True, False]), {"x": 1}) == ("01", [])
    assert encoder(one([False, True]), {"x": 0}) == ("00", [])


@pytest.mark.parametrize("encoder", [encode, encode_js])
def test_a_string_is_not_a_boolean_or_a_number(encoder):
    for schema, value in ((one({0: False, 1: True}), "true"), (one({1: 1, 2: 5}), "5")):
        payload, errors = encoder(schema, {"x": value})
        assert errors, (value, payload)


@pytest.mark.parametrize("encoder", [encode, encode_js])
def test_number_labels_reverse_to_their_key(encoder):
    schema = one({1: 1, 2: 5, 3: 10, 4: 100})
    assert encoder(schema, {"x": 5}) == ("02", [])
    assert encoder(schema, {"x": 100}) == ("04", [])


def test_the_bit_ranges_of_the_probe():
    assert encode(PROBE, {"m": False, "q": False}) == ("01", [])
    payload, errors = encode(PROBE, {"m": True, "q": False})
    assert errors and "PS-513" in errors[0]


def test_same_label():
    assert same_label(True, True) and same_label(False, False)
    assert not same_label(True, 1) and not same_label(1, True)
    assert not same_label(False, 0) and not same_label(0.0, False)
    assert not same_label("true", True) and not same_label(True, "true")
    assert same_label(1, 1.0) and same_label("a", "a")


# PS-407: the schema -----------------------------------------------------------------


def test_a_template_beside_a_boolean_label_is_rejected():
    schema = one({0: False, "default": "v${value}"})
    assert any("PS-407" in e for e in validate_schema_structure(schema))
    with pytest.raises(Exception, match="PS-407"):
        TS013Generator(schema).generate()
    result = SchemaInterpreter(schema).decode(b"\x00")
    assert not result.success and any("PS-407" in e for e in result.errors)


def test_boolean_labels_are_a_valid_schema():
    assert not validate_schema_structure(PROBE)


# Tools ------------------------------------------------------------------------------


def test_the_output_schema_types_a_boolean_lookup():
    assert lookup_json_schema({0: False, "default": True}) == {
        "type": "boolean",
        "enum": [False, True],
    }
    # `true` and 1 are two members, not one (PS-513).
    assert lookup_json_schema([1, True])["enum"] == [1, True]


def test_the_c_binary_form_refuses_a_boolean_label():
    # PS-446: the format holds a label as text, so C would report the string "True".
    errors = schema_errors(one([True, False]))
    assert any("PS-106" in e for e in errors), errors
