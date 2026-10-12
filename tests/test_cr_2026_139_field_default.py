"""CR-2026-139 (PS-554, PS-551 amended): a field's `default` is written where the input
omits it.

The encode cases - written, in reported form, input winning, an enum's default never
written, inside each construct, and the PS-168 rejection of a default that cannot be
encoded - are in `tests/cr-2026-134-cases.yaml`, so the reference, the generated codec's
runtime encoder, Go, Java and C# are held to the same bytes and the same text. This file
holds what that list cannot: a decoder ignores the key, the generated `encodeCommand`
honours it, and C, whose binary format has no slot for it, refuses it before conversion.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_binary import schema_errors  # noqa: E402
from schema_interpreter import SchemaInterpreter, field_default  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

FIELDS = [{"name": "interval", "type": "u16", "default": 300},
          {"name": "enabled", "type": "bool", "default": True, "consume": 1}]
SCHEMA = {"name": "t", "version": 1, "fields": FIELDS}


def test_a_decoder_ignores_it():
    """The spec prototype's test_a_decoder_ignores_it."""
    decoded = SchemaInterpreter(SCHEMA).decode(bytes.fromhex("000a00"))
    assert decoded.success, decoded.errors
    assert decoded.data == {"interval": 10, "enabled": False}


def test_the_generated_codec_decoder_ignores_it():
    script = (TS013Generator(SCHEMA).generate()
              + "\nprocess.stdout.write(JSON.stringify(decodeUplink({bytes: [0, 10, 0], "
              "fPort: 1})));")
    out = json.loads(subprocess.run(["node"], input=script, capture_output=True, text=True,
                                    check=True).stdout)
    assert out["data"] == {"interval": 10, "enabled": False}, out


def test_the_validator_accepts_it():
    assert validate_schema_structure(SCHEMA) == []


@pytest.mark.parametrize("field,expected", [
    ({"name": "a", "type": "u8", "default": 3}, (True, 3)),
    ({"name": "a", "type": "u8", "default": None}, (True, None)),
    ({"name": "a", "type": "u8"}, (False, None)),
    # PS-068: an enum's default is its unmapped label, not a value to write.
    ({"name": "e", "type": "enum", "base": "u8", "values": {1: "a"}, "default": "x"},
     (False, None)),
    # A legacy `type: match`'s default is its fallback case.
    ({"type": "match", "on": "k", "cases": [], "default": "skip"}, (False, None)),
])
def test_which_defaults_are_written(field, expected):
    assert field_default(field) == expected


def test_c_refuses_it_before_conversion():
    """field_def_t and the binary format have no slot for a field default, so the C
    encoder would reject the input every other implementation encodes (PS-446). The
    converter already refused every `default`, an enum's included; it now says why."""
    errors = schema_errors({"name": "t", "fields": [{"name": "a", "type": "u8",
                                                     "default": 3}]})
    assert any("`default`" in e and "PS-554" in e and "PS-446" in e for e in errors), errors


def test_the_generated_command_encoder_honours_it():
    schema = {"name": "t", "version": 1, "fields": [{"name": "x", "type": "u8"}],
              "downlink_commands": {"set": {"command_id": 1, "fields": [
                  {"name": "interval", "type": "u16", "default": 300}]}}}
    code = TS013Generator(schema).generate()
    assert "data.interval = 300;" in code
