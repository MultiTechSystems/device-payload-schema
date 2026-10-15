"""CR-2026-174 (PS-585 to PS-587): a `checksum` field is verified on decode and computed on
encode.

The cases live in `tests/cr-2026-174-cases.yaml`, read by every implementation: `decode`
(a payload and its data, warning or error), `cases` (an encode input and its payload or
error) and `load` (a schema refused when it is loaded).
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
from schema_interpreter import SchemaInterpreter, compute_checksum  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-174-cases.yaml").read_text())
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


@pytest.mark.parametrize(
    "algo,expected",
    [("crc8/smbus", 0xF4), ("crc16/kermit", 0x2189), ("sum8", 0xDD), ("xor8", 0x31)],
)
def test_the_check_values(algo, expected):
    assert compute_checksum(algo, b"123456789") == expected


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(
        bytes.fromhex(case["payload"])
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.errors), out.errors
        assert not out.success
    else:
        assert out.success, out.errors
        assert out.data == case["expected"]
    if "warning" in case:
        assert any(case["warning"] in w for w in out.warnings), out.warnings


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
        assert out["data"] == case["expected"]
    if "warning" in case:
        assert any(case["warning"] in w for w in out.get("warnings") or []), out


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).encode(
        json.loads(case["input"])
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.errors), out.errors
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


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encoded_frames_decode_back(case):
    """Every frame an encoder writes passes its own checksums when decoded (PS-586)."""
    if "error" in case:
        pytest.skip("no frame")
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(
        bytes.fromhex(case["payload"])
    )
    assert out.success and not out.warnings, (out.errors, out.warnings)


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(b"\x01\x02\x03\x04", fPort=2)
    assert any(case["error"] in e for e in out.errors), out.errors


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_validator(case):
    errors = validate_schema_structure(yaml.safe_load(case["schema"]))
    assert any(case["error"] in e for e in errors), errors


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_generator(case):
    with pytest.raises(Exception, match=r"PS-58[56]|PS-448"):
        TS013Generator(yaml.safe_load(case["schema"])).generate()
