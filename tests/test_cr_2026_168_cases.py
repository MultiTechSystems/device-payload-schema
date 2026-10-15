"""CR-2026-168: reusable lookup tables and field templates.

A definition carries exactly one of `fields`, `field` or `lookup` (PS-345); a `$ref` to a
`field` definition may rename it and replace keys whole (PS-578); a field's `lookup` may be a
pointer to a `lookup` definition (PS-579); references resolve transitively (PS-580). The
interpreter, the validator, the TS013 generator and the binary converter for C read the same
case list as Go, Java and C#; the output and `_meta` are those of the written-out schema.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_binary import schema_errors  # noqa: E402
from schema_interpreter import SchemaInterpreter, expand_refs  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-168-cases.yaml").read_text())["cases"]
REJECTED = [c for c in CASES if "error" in c]
LOADED = [c for c in CASES if "error" not in c]


def _data(result):
    return {k: v for k, v in result.data.items() if k != "_quality"}


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_the_interpreter_refuses_it(case):
    schema = yaml.safe_load(case["schema"])
    decoded = SchemaInterpreter(schema).decode(b"\x01\x02")
    assert case["error"] in decoded.errors, decoded.errors
    assert decoded.data == {}
    encoded = SchemaInterpreter(schema).encode({"x": 1})
    assert case["error"] in encoded.errors, encoded.errors
    assert encoded.payload == b""


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_the_validator_the_generator_and_the_c_converter_refuse_it(case):
    schema = yaml.safe_load(case["schema"])
    assert case["error"] in validate_schema_structure(schema)
    with pytest.raises(ValueError) as raised:
        TS013Generator(schema).generate()
    assert case["error"] in str(raised.value)
    assert case["error"] in schema_errors(schema)


@pytest.mark.parametrize("case", LOADED, ids=[c["name"] for c in LOADED])
def test_it_decodes_and_encodes_as_written_out(case):
    schema = yaml.safe_load(case["schema"])
    assert validate_schema_structure(schema) == []
    interpreter = SchemaInterpreter(schema)
    port = case.get("fport")
    decoded = interpreter.decode(bytes.fromhex(case["payload"]), fPort=port)
    assert decoded.success, decoded.errors
    assert _data(decoded) == json.loads(case["expected"])
    if case.get("round_trip", True):
        encoded = interpreter.encode(decoded.data, fPort=port)
        assert encoded.success, encoded.errors
        assert encoded.payload.hex() == case["payload"]
    if "encode" in case:
        encoded = interpreter.encode(json.loads(case["encode"]), fPort=port)
        assert encoded.success, encoded.errors
        assert encoded.payload.hex() == case["encoded"]


def run_codec(schema, call):
    script = (
        TS013Generator(schema).generate()
        + "\nvar e; try { e = "
        + call
        + "; } catch (x) { e = {errors: [String(x.message)]}; }"
        "\nprocess.stdout.write(JSON.stringify(e));"
    )
    done = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize("case", LOADED, ids=[c["name"] for c in LOADED])
def test_the_generated_codec_decodes_and_encodes_it(case):
    schema = yaml.safe_load(case["schema"])
    port = case.get("fport", 1)
    payload = list(bytes.fromhex(case["payload"]))
    out = run_codec(schema, "decodeUplink({bytes: %s, fPort: %d})" % (json.dumps(payload), port))
    assert not out.get("errors"), out
    assert {k: v for k, v in out["data"].items() if k != "_quality"} == json.loads(
        case["expected"])
    if case.get("round_trip", True):
        out = run_codec(schema, "encodeDownlink({data: %s, fPort: %d})"
                        % (json.dumps(out["data"]), port))
        assert not out.get("errors"), out
        assert bytes(out["bytes"]).hex() == case["payload"]


def _written_out(case):
    """The schema with every reference resolved and its definitions dropped."""
    schema, errors = expand_refs(yaml.safe_load(case["schema"]))
    assert errors == []
    schema.pop("definitions")
    return schema


@pytest.mark.parametrize("case", LOADED, ids=[c["name"] for c in LOADED])
def test_the_output_and_meta_are_those_of_the_written_out_schema(case):
    payload = bytes.fromhex(case["payload"])
    port = case.get("fport", 1)
    ours = SchemaInterpreter(yaml.safe_load(case["schema"])).interpret(payload, fPort=port)
    written = SchemaInterpreter(_written_out(case)).interpret(payload, fPort=port)
    assert ours == written


def test_no_pointer_or_reference_survives_resolution():
    case = next(c for c in CASES if c["name"] == "a_field_definition_may_use_a_lookup_pointer")
    schema, errors = expand_refs(yaml.safe_load(case["schema"]))
    assert errors == []
    assert schema["fields"] == [{"type": "u8", "lookup": ["off", "on"], "name": "s"}]
    assert schema["definitions"]["state"]["field"]["lookup"] == ["off", "on"]


def test_the_definitions_are_not_shared_between_uses():
    case = next(c for c in CASES if c["name"] == "the_cr_example")
    schema, _ = expand_refs(yaml.safe_load(case["schema"]))
    first, second = schema["fields"][3]["lookup"], schema["fields"][4]["lookup"]
    assert first == second and first is not second
