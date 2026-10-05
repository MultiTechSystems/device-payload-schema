"""CR-2026-038, -041 and -042: the FPort rules.

- 038: a `ports` schema decoded with no FPort is an error, selects no entry - not even
  `default` - and says so in words distinct from an unmatched FPort (PS-459, PS-460).
- 041: a port key is 1 to 255; 0 is not one (PS-018).
- 042: a top-level `fPort` (or `fport`) is an integer from 1 to 255, every `ports` key
  beside it must equal it, and it is never consulted when decoding (PS-335 to PS-338).
  A document of `definitions` alone is a library, not a schema (PS-339).

Go, Java and C# hold the same in their own tests.
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

PORTED = {"name": "probe", "ports": {
    1: {"fields": [{"name": "a", "type": "u8"}]},
    "default": {"fields": [{"name": "b", "type": "u8"}]},
}}


def decode_js(schema, payload, fport):
    code = TS013Generator(schema).generate()
    entry = {"bytes": list(payload)}
    if fport is not None:
        entry["fPort"] = fport
    script = code + "\nprocess.stdout.write(JSON.stringify(decodeUplink(" + json.dumps(entry) + ")));"
    out = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_no_fport_is_an_error_and_the_default_is_not_used():
    with pytest.raises(ValueError, match="no FPort was supplied") as caught:
        SchemaInterpreter(PORTED).decode(b"\x07")
    assert "PS-459" in str(caught.value)
    # The generator cannot express a `default` entry, so its half uses one port alone.
    js = decode_js({"name": "probe", "ports": {1: PORTED["ports"][1]}}, b"\x07", None)
    assert js["data"] == {} and any("no FPort was supplied" in e for e in js["errors"])


def test_an_unmatched_fport_still_uses_the_default():
    assert SchemaInterpreter(PORTED).decode(b"\x07", fPort=9).data == {"b": 7}


@pytest.mark.parametrize("port", [0, 256])
def test_a_port_key_outside_1_to_255_is_invalid(port):
    schema = {"name": "p", "ports": {port: {"fields": [{"name": "a", "type": "u8"}]}}}
    assert any("1-255" in e for e in validate_schema_structure(schema))


@pytest.mark.parametrize("declared", [0, 256, "225", True])
def test_a_top_level_fport_must_be_a_port(declared):
    schema = {"name": "p", "fPort": declared, "fields": [{"name": "a", "type": "u8"}]}
    assert any("PS-335" in e for e in validate_schema_structure(schema))
    assert any("PS-335" in e for e in SchemaInterpreter(schema).decode(b"\x01").errors)


def test_beside_ports_every_key_equals_it():
    schema = {"name": "p", "fPort": 20, "ports": {
        20: {"fields": [{"name": "a", "type": "u8"}]},
        21: {"fields": [{"name": "b", "type": "u8"}]}}}
    assert any("PS-337" in e for e in validate_schema_structure(schema))


def test_the_top_level_fport_is_never_consulted():
    """It states which port the document is about; decoding without one still works."""
    for key in ("fPort", "fport"):
        schema = {"name": "p", key: 225, "fields": [{"name": "a", "type": "u8"}]}
        assert SchemaInterpreter(schema).decode(b"\x05").data == {"a": 5}
        assert SchemaInterpreter(schema).decode(b"\x05", fPort=9).data == {"a": 5}


def test_a_definition_library_is_not_a_malformed_schema():
    library = {"name": "lib", "fPort": 225, "definitions": {
        "cmd": {"fields": [{"name": "cid", "type": "u8"}]}}}
    assert not any("PS-004" in e for e in validate_schema_structure(library))
