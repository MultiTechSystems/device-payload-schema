"""CR-2026-170 (PS-430, PS-582, PS-096, PS-583): padded and signed bitfield_string parts.

The cases live in `tests/cr-2026-170-cases.yaml`, read by every implementation: `decode`
(a payload and its value), `cases` (an encode input and its payload or error) and `load`
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
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-170-cases.yaml").read_text())
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
        assert out.data == {"v": case["expected"]}


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    out = run_codec(
        schema,
        "decodeUplink({bytes: %s, fPort: 1})"
        % json.dumps(list(bytes.fromhex(case["payload"]))),
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
    else:
        assert not out.get("errors"), out
        assert out["data"] == {"v": case["expected"]}


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).encode(
        json.loads(case["input"])
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.errors), out.errors
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
    with pytest.raises(Exception, match=r"PS-430"):
        TS013Generator(yaml.safe_load(case["schema"])).generate()
