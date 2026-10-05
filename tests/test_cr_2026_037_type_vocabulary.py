"""CR-2026-037: the type vocabulary is closed.

An unknown or absent type is rejected naming the field and the spelling (PS-327, PS-328,
PS-334); the PS-049 alias table is exhaustive (PS-326) and case-sensitive (PS-333); udec
and sdec are required (PS-329, PS-330). Held for the reference interpreter, the TS013
generator and the validator; the other four languages carry their own CR-037 tests.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

FIXTURE = REPO_ROOT / "schemas/devices/_language-conformance/type-vocabulary.yaml"

UNDEFINED = ["float", "double", "UDec", "SDec", "U8", "Byte", "uint", "float16",
             "boolean", "switch", "version_string", "hex:upper", "tlv"]


def one(type_name):
    return {"name": "probe", "fields": [{"name": "v", "type": type_name}]}


@pytest.mark.parametrize("spelling", UNDEFINED)
def test_the_interpreter_rejects_an_undefined_spelling(spelling):
    result = SchemaInterpreter(one(spelling)).decode(b"\x01\x02\x03\x04\x05\x06\x07\x08")
    assert not result.success
    assert any("'v'" in e and spelling in e for e in result.errors), result.errors


@pytest.mark.parametrize("spelling", UNDEFINED)
def test_the_generator_refuses_an_undefined_spelling(spelling):
    with pytest.raises(ValueError, match="unknown type"):
        TS013Generator(one(spelling)).generate()


@pytest.mark.parametrize("spelling", UNDEFINED)
def test_the_validator_reports_an_undefined_spelling(spelling):
    errors = validate_schema_structure(one(spelling))
    assert any("unknown type" in e for e in errors), errors


def test_a_field_with_no_type_is_rejected_everywhere():
    schema = {"name": "probe", "fields": [{"name": "v"}]}
    assert any("declares no type" in e
               for e in SchemaInterpreter(schema).decode(b"\x01").errors)
    errors = validate_schema_structure(schema)
    assert any("PS-334" in e for e in errors), errors


@pytest.mark.parametrize("spelling,payload,want", [
    ("uint8", "07", 7), ("uint64", "0000000000000009", 9), ("int64", "FFFFFFFFFFFFFFF6", -10),
    ("i24", "FFFFFB", -5), ("udec", "25", 2.5), ("sdec", "E5", -1.5), ("sdec", "79", 7.9),
])
def test_aliases_and_nibble_decimals_decode_and_encode(spelling, payload, want):
    interp = SchemaInterpreter(one(spelling))
    result = interp.decode(bytes.fromhex(payload))
    assert result.data["v"] == pytest.approx(want)
    assert interp.encode(result.data).payload == bytes.fromhex(payload)


def test_integer_reports_an_integer_and_rejects_a_fraction():
    schema = {"name": "probe", "fields": [
        {"name": "a", "type": "u8"},
        {"name": "half", "type": "integer", "compute": {"op": "div", "a": "$a", "b": 2}},
    ]}
    interp = SchemaInterpreter(schema)
    assert interp.decode(b"\x08").data["half"] == 4
    assert isinstance(interp.decode(b"\x08").data["half"], int)
    assert any("type integer" in e for e in interp.decode(b"\x07").errors)


def test_the_fixture_round_trips_in_the_reference():
    schema = yaml.safe_load(FIXTURE.read_text())
    payload = bytes.fromhex(schema["test_vectors"][0]["payload"].replace(" ", ""))
    interp = SchemaInterpreter(schema)
    encoded = interp.encode(interp.decode(payload).data)
    assert encoded.success, encoded.errors
    assert encoded.payload == payload
