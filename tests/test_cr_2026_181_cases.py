"""CR-2026-181 (PS-601, PS-602; PS-081, PS-527 and PS-551 amended): an encoder writes a
`bytes`, `hex` or `base64` value exactly as given, and rejects a malformed one or one whose
byte count is not its literal `length`, naming the field.

The cases live in `tests/cr-2026-181-cases.yaml`, read by every implementation.
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

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-181-cases.yaml").read_text())["cases"]


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


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
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


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_encode_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    out = run_codec(schema, "encodeDownlink({data: %s, fPort: 1})" % case["input"])
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
        assert not out.get("bytes"), out
    else:
        assert not out.get("errors"), out
        assert bytes(out["bytes"]).hex() == case["payload"]


def test_a_bytes_object_is_written_as_given():
    """Python callers may hand over a bytes object; it is held to the same length."""
    schema = {"name": "t", "fields": [{"name": "k", "type": "bytes", "length": 2}]}
    assert SchemaInterpreter(schema).encode({"k": b"\x01\x02"}).payload == b"\x01\x02"
    out = SchemaInterpreter(schema).encode({"k": b"\x01"})
    assert not out.success
    assert "Field 'k' is given 1 bytes for a length of 2 (PS-602)" in out.errors
