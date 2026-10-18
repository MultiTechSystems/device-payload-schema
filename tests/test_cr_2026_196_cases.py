"""CR-2026-196 (PS-635 to PS-637): a `type: number` field declaring `source` reports the
FPort, the payload length or the bytes remaining, on decode, encode and load.

The cases live in `tests/cr-2026-196-cases.yaml`, read by every implementation: `decode`
(a payload, an FPort where one is given, and its data), `cases` (an encode input and its
payload or error) and `load` (a schema refused when it is loaded).
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

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-196-cases.yaml").read_text())
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


def codec_input(key, value, fport):
    entry = {key: value}
    if fport is not None:
        entry["fPort"] = fport
    return json.dumps(entry)


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(
        bytes.fromhex(case["payload"]), fPort=case.get("fport")
    )
    assert out.success, out.errors
    assert out.data == case["expected"]
    if "warnings" in case:
        assert out.warnings == case["warnings"]


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    payload = list(bytes.fromhex(case["payload"]))
    out = run_codec(
        schema, "decodeUplink(%s)" % codec_input("bytes", payload, case.get("fport"))
    )
    assert not out.get("errors"), out
    assert out["data"] == case["expected"]
    if "warnings" in case:
        assert (out.get("warnings") or []) == case["warnings"]


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).encode(
        json.loads(case["input"]), fPort=case.get("fport")
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
    out = run_codec(
        schema,
        "encodeDownlink(%s)"
        % codec_input("data", json.loads(case["input"]), case.get("fport")),
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
        assert not out.get("bytes"), out
    else:
        assert not out.get("errors"), out
        assert bytes(out["bytes"]).hex() == case["payload"]


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(b"\x01\x02\x03\x04")
    assert any(case["error"] in e for e in out.errors), out.errors


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_generator(case):
    with pytest.raises(Exception, match=r"PS-635"):
        TS013Generator(yaml.safe_load(case["schema"])).generate()


def test_a_flat_schema_reports_the_callers_fport():
    """PS-581: the reference reported the document's FPort alone, dropping the caller's."""
    schema = {"name": "t", "fields": [{"name": "a", "type": "u8"}]}
    assert SchemaInterpreter(schema).encode({"a": 1}, fPort=3).fport == 3
