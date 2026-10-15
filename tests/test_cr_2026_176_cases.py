"""CR-2026-176 (PS-361 amended, PS-590 to PS-592): template string fields.

The cases live in `tests/cr-2026-176-cases.yaml`, read by every implementation: `decode`
(a payload, its data and a warning), `cases` (an encode input and its payload) and `load`
(a schema refused when it is loaded).
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
from validate_schema import validate_schema_structure  # noqa: E402

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-176-cases.yaml").read_text())
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


def check_warning(case, warnings):
    if "warning" in case:
        assert any(case["warning"] in w for w in warnings), warnings
    else:
        assert not [w for w in warnings if "PS-591" in w], warnings


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(
        bytes.fromhex(case["payload"])
    )
    assert out.success, out.errors
    assert out.data == case["expected"]
    check_warning(case, out.warnings)


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_generated_codec(case):
    out = run_codec(
        yaml.safe_load(case["schema"]),
        "decodeUplink({bytes: %s, fPort: 1})"
        % json.dumps(list(bytes.fromhex(case["payload"]))),
    )
    assert not out.get("errors"), out
    assert out["data"] == case["expected"]
    check_warning(case, out.get("warnings") or [])


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).encode(
        json.loads(case["input"])
    )
    assert out.success, out.errors
    assert out.payload.hex() == case["payload"]


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_generated_codec(case):
    out = run_codec(
        yaml.safe_load(case["schema"]),
        "encodeDownlink({data: %s, fPort: 1})" % case["input"],
    )
    assert not out.get("errors"), out
    assert bytes(out["bytes"]).hex() == case["payload"]


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(b"\x01")
    assert any(case["error"] in e for e in out.errors), out.errors


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_validator(case):
    errors = validate_schema_structure(yaml.safe_load(case["schema"]))
    assert any(case["error"] in e for e in errors), errors


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_generator(case):
    with pytest.raises(Exception, match=r"PS-(361|430|539|590)"):
        TS013Generator(yaml.safe_load(case["schema"])).generate()


def test_the_binary_form_refuses_a_template():
    """C has no template string (PS-446): the converter names the key, never drops it."""
    schema = yaml.safe_load(DECODE[0]["schema"])
    with pytest.raises(Exception, match="template"):
        encode_schema(schema)
