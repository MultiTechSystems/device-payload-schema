"""CR-2026-187 (PS-616 to PS-618): `bit_labels` reports the labels of a bitmask's set bits,
as a list or joined text, and an encoder sets the bits of the labels it is given.

The cases live in `tests/cr-2026-187-cases.yaml`, read by every implementation: `decode`
(a payload, its data and its warnings), `cases` (an encode input and its payload or error),
`meta` (what `_meta` reports) and `load` (a schema refused when it is loaded).
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
from schema_binary import encode_schema  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema  # noqa: E402

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-187-cases.yaml").read_text())
DECODE, ENCODE, META, LOAD = DOC["decode"], DOC["cases"], DOC["meta"], DOC["load"]


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


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(
        bytes.fromhex(case["payload"])
    )
    assert out.success, out.errors
    assert out.data == case["expected"]
    for warning in case.get("warnings", []):
        assert warning in out.warnings, out.warnings
    if "warnings" not in case:
        assert not out.warnings, out.warnings


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    out = run_codec(
        schema,
        "decodeUplink({bytes: %s, fPort: 1})"
        % json.dumps(list(bytes.fromhex(case["payload"]))),
    )
    assert not out.get("errors"), out
    assert out["data"] == case["expected"]
    for warning in case.get("warnings", []):
        assert warning in (out.get("warnings") or []), out


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).encode(
        json.loads(case["input"])
    )
    if "error" in case:
        assert case["error"] in out.errors, out.errors
        assert not out.payload
    else:
        assert out.success, out.errors
        assert out.payload.hex() == case["payload"]


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    out = run_codec(schema, "encodeDownlink({data: %s, fPort: 1})" % case["input"])
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
        assert not out.get("bytes"), out
    else:
        assert not out.get("errors"), out
        assert bytes(out["bytes"]).hex() == case["payload"]


@pytest.mark.parametrize("case", META, ids=[c["name"] for c in META])
def test_meta_reports(case):
    res = SchemaInterpreter(yaml.safe_load(case["schema"])).interpret(
        bytes.fromhex(case["payload"])
    )
    assert res.data["_meta"]["fields"]["f"]["reports"] == case["reports"]


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(b"\x01\x02")
    assert any(case["error"] in e for e in out.errors), out.errors


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_validator(case):
    errors = validate_schema(yaml.safe_load(case["schema"])).schema_errors
    assert any(case["error"] in str(e) for e in errors), errors


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_generator(case):
    with pytest.raises(Exception, match=r"PS-61[67]"):
        TS013Generator(yaml.safe_load(case["schema"])).generate()


def test_c_binary_form_refuses_bit_labels():
    """PS-446: the C interpreter has no bit_labels, so its binary form refuses the key."""
    schema = yaml.safe_load(DECODE[0]["schema"])
    with pytest.raises(Exception, match="bit_labels"):
        encode_schema(schema)
