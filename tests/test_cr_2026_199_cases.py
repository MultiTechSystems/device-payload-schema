"""CR-2026-199 (PS-641, PS-642): `trim: true` on an ascii field, on decode,
encode and load.

The cases live in `tests/cr-2026-199-cases.yaml`, read by every implementation: `decode`
(a payload and its data or error), `cases` (an encode input and its payload or error) and
`load` (a schema refused when it is loaded).
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
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema  # noqa: E402

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-199-cases.yaml").read_text())
DECODE, ENCODE, LOAD = DOC["decode"], DOC["cases"], DOC["load"]


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
    if "error" in case:
        assert any(case["error"] in e for e in out.errors), out.errors
        assert not out.data
    else:
        assert out.success, out.errors
        assert out.data == case["expected"]
        if "warnings" in case:
            assert out.warnings == case["warnings"]


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    out = run_codec(
        schema,
        "decodeUplink({bytes: %s, fPort: 1})" % json.dumps(list(bytes.fromhex(case["payload"]))),
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
    else:
        assert not out.get("errors"), out
        assert out["data"] == case["expected"]
        if "warnings" in case:
            assert (out.get("warnings") or []) == case["warnings"]


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).encode(json.loads(case["input"]))
    if "error" in case:
        assert any(case["error"] in e for e in out.errors), out.errors
    else:
        assert out.success, out.errors
        assert out.payload.hex() == case["payload"]
        if "warnings" in case:
            assert out.warnings == case["warnings"]


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    out = run_codec(
        schema, "encodeDownlink({data: %s, fPort: 1})" % case["input"]
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
        assert not out.get("bytes"), out
    else:
        assert not out.get("errors"), out
        assert bytes(out["bytes"]).hex() == case["payload"]
        if "warnings" in case:
            assert (out.get("warnings") or []) == case["warnings"]


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(b"\x01\x02\x03\x04")
    assert any(case["error"] in e for e in out.errors), out.errors


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_generator(case):
    with pytest.raises(Exception, match=r"PS-641"):
        TS013Generator(yaml.safe_load(case["schema"])).generate()


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_validator(case):
    result = validate_schema(yaml.safe_load(case["schema"]))
    assert not result.schema_valid
    assert any(case["error"] in str(m) for m in result.schema_errors), result.schema_errors


def test_an_accepted_trim_validates():
    schema = yaml.safe_load(DECODE[0]["schema"])
    assert validate_schema(schema).schema_valid


def test_the_binary_form_refuses_trim():
    """PS-446: the C struct API has `trim` (field_def_t.ascii_trim), but a field record of
    the binary schema form has no slot for it."""
    schema = yaml.safe_load(DECODE[0]["schema"])
    assert any("trim" in e and "PS-641" in e for e in schema_errors(schema))
