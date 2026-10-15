"""CR-2026-192 (PS-567, PS-569, PS-615, PS-629): time fields and the Integration Layer.

The cases live in `tests/cr-2026-192-cases.yaml`, read by every implementation: `decode`
(a payload and its data), `cases` (an encode input and its payload), `load` (a schema
refused when it is loaded) and `meta` (`_meta.fields` keys). The generated codec carries
no `_meta`, so it reads the first three.
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

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-192-cases.yaml").read_text())
DECODE, ENCODE, LOAD, META = DOC["decode"], DOC["cases"], DOC["load"], DOC["meta"]


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


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_generated_codec(case):
    out = run_codec(
        yaml.safe_load(case["schema"]),
        "decodeUplink({bytes: %s, fPort: 1})"
        % json.dumps(list(bytes.fromhex(case["payload"]))),
    )
    assert not out.get("errors"), out
    assert out["data"] == case["expected"]


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
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(b"\x00" * 8)
    assert any(case["error"] in e for e in out.errors), out.errors


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_validator(case):
    errors = validate_schema_structure(yaml.safe_load(case["schema"]))
    assert any(case["error"] in e for e in errors), errors


@pytest.mark.parametrize("case", LOAD, ids=[c["name"] for c in LOAD])
def test_load_generator(case):
    with pytest.raises(Exception, match=r"PS-569"):
        TS013Generator(yaml.safe_load(case["schema"])).generate()


@pytest.mark.parametrize("case", META, ids=[c["name"] for c in META])
def test_meta(case):
    res = SchemaInterpreter(yaml.safe_load(case["schema"])).interpret(
        bytes.fromhex(case["payload"])
    )
    fields = res.data["_meta"]["fields"]
    for name, keys in case["expected"].items():
        for key, want in keys.items():
            if want is None:
                assert key not in fields[name], fields[name]
            else:
                assert fields[name][key] == want, fields[name]


def test_meta_time_follows_reports():
    """PS-629 in the prototype's key order: `time` right after `reports`, its own keys
    as declared."""
    res = SchemaInterpreter(yaml.safe_load(META[0]["schema"])).interpret(bytes(8))
    entry = res.data["_meta"]["fields"]["ts"]
    assert list(entry)[:3] == ["type", "reports", "time"]
    assert list(entry["time"]) == ["format", "epoch", "fraction"]
