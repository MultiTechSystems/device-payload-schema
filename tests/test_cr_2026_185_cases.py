"""CR-2026-185 (PS-585 amended, PS-608, PS-609): a checksum field's `from` counts from the
frame, so one within a byte_group, a tlv entry, a records entry or a repeat element is
rejected at load, and an empty covered range is an error; normalizeUplink() without an FPort
places values only where the candidate port entries agree.

The cases live in `tests/cr-2026-185-cases.yaml`, read by every implementation: `decode`,
`cases` (encode) and `load`; `normalize` here only, by normalize() and the generated codec's
normalizeUplink().
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter, normalize  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-185-cases.yaml").read_text())
DECODE, ENCODE, LOAD = DOC["decode"], DOC["cases"], DOC["load"]
NORMALIZE = DOC["normalize"]
NODE = shutil.which("node")


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
    with pytest.raises(Exception, match=r"PS-608"):
        TS013Generator(yaml.safe_load(case["schema"])).generate()


@pytest.mark.parametrize("case", NORMALIZE, ids=[c["name"] for c in NORMALIZE])
def test_normalize_reference(case):
    out = normalize(case["data"], yaml.safe_load(case["schema"]), case.get("fport"))
    assert out == {"data": case["normalized"], "warnings": case["warnings"]}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("case", NORMALIZE, ids=[c["name"] for c in NORMALIZE])
@pytest.mark.parametrize("slim", [False, True], ids=["full", "slim"])
def test_normalize_generated_codec(case, slim):
    code = TS013Generator(yaml.safe_load(case["schema"]), slim=slim).generate()
    given = {"data": case["data"]}
    if case.get("fport") is not None:
        given["fPort"] = case["fport"]
    script = code + "\nconsole.log(JSON.stringify(normalizeUplink(%s)));" % json.dumps(given)
    got = json.loads(subprocess.run([NODE], input=script, capture_output=True, text=True,
                                    check=True).stdout)
    assert got == {"data": case["normalized"], "warnings": case["warnings"]}
