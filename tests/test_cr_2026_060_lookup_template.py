"""CR-2026-060: a lookup default may carry the value it could not map.

The fixture lookup-default-template.yaml holds the decode and round trip in all five
implementations. This file holds the reference interpreter, the generated codec and the
validator on what a vector cannot express: rejections, and the value's rendering.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import (SchemaInterpreter, format_lookup_value,  # noqa: E402
                                match_lookup_template)
from validate_schema import validate_schema_structure  # noqa: E402


def one(lookup, **extra):
    return {"name": "probe", "fields": [dict({"name": "x", "type": "u8", "lookup": lookup},
                                             **extra)]}


def decode_js(schema, payload_hex):
    code = TS013Generator(schema).generate()
    script = code + "\nprocess.stdout.write(JSON.stringify(decodeUplink(" + json.dumps(
        {"bytes": list(bytes.fromhex(payload_hex)), "fPort": 1}) + ").data));"
    out = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# PS-406 ------------------------------------------------------------------------------

@pytest.mark.parametrize("payload,want", [("01", "short"), ("07", "mode7"), ("00", "mode0")])
def test_the_spec_example_on_both_paths(payload, want):
    schema = one({1: "short", 2: "long", "default": "mode${value}"})
    assert SchemaInterpreter(schema).decode(bytes.fromhex(payload)).data["x"] == want
    assert decode_js(schema, payload)["x"] == want


def test_every_occurrence_is_substituted():
    schema = one({1: "a", "default": "${value}-${value}"})
    assert SchemaInterpreter(schema).decode(b"\x09").data["x"] == "9-9"


@pytest.mark.parametrize("value", [7, 7.0, -3, 2.5, 0.1, 1e-5, 1e-6, 1.5e-7, 1e-8,
                                   1.2345e-9, 1e20, 1e21, 1.5e22, -0.0, 123456789.125])
def test_rendering_matches_javascript_and_round_trips(value):
    """The interpreter and the generated codec must write the same string (PS-406)."""
    js = subprocess.run(["node", "-e", f"process.stdout.write(String({float(value)!r}))"],
                        capture_output=True, text=True, check=True).stdout
    assert format_lookup_value(value) == js
    assert match_lookup_template("x${value}", "x" + js) == pytest.approx(value)


# PS-407, PS-408 ----------------------------------------------------------------------

def test_numeric_labels_beside_a_template_are_rejected_everywhere():
    schema = one({1: 10, "default": "x${value}"})
    assert any("PS-407" in e for e in SchemaInterpreter(schema).decode(b"\x01").errors)
    assert any("PS-407" in e for e in validate_schema_structure(schema))
    with pytest.raises(ValueError, match="PS-407"):
        TS013Generator(schema).generate()


def test_a_label_is_not_a_template():
    schema = one({1: "a${value}"})
    assert SchemaInterpreter(schema).decode(b"\x01").data["x"] == "a${value}"
    assert decode_js(schema, "01")["x"] == "a${value}"


def test_a_plain_default_is_unchanged():
    schema = one({1: "a", "default": "unknown"})
    assert SchemaInterpreter(schema).decode(b"\x07").data["x"] == "unknown"


# PS-409 ------------------------------------------------------------------------------

def test_the_encoder_recovers_the_value():
    schema = one({1: "a", "default": "sensor${value}"}, div=2)
    interp = SchemaInterpreter(schema)
    assert interp.encode({"x": "sensor2.5"}).payload == b"\x05"


@pytest.mark.parametrize("text", ["sensorX", "sensor", "other7", "7"])
def test_a_string_that_is_neither_is_rejected(text):
    interp = SchemaInterpreter(one({1: "a", "default": "sensor${value}"}))
    result = interp.encode({"x": text})
    assert not result.success and any("PS-409" in e for e in result.errors), result.errors


def test_repeated_tokens_must_agree():
    interp = SchemaInterpreter(one({1: "a", "default": "${value}/${value}"}))
    assert interp.encode({"x": "4/4"}).payload == b"\x04"
    assert not interp.encode({"x": "4/5"}).success


def test_a_plain_default_label_still_cannot_be_encoded():
    result = SchemaInterpreter(one({1: "a", "default": "unknown"})).encode({"x": "unknown"})
    assert not result.success
