"""CR-2026-141 (PS-551): a peek whose bits other fields write is derived.

A bit range or bool that does not consume shares its bits with the fields after it, which
fix its value. Absent from the input it is derived and writes no bits, and is missing
where a bit is left unwritten; supplied, it must agree with the bits the frame holds.
Milesight ct10x, ct30x, gs301 and ws515 peek their reading's word for a status whose
lookup maps only 0xFFFF, so an ordinary reading's status is absent from the input: read
as required (CR-2026-134) it failed 26 corpus round trips.

The cases live in `tests/cr-2026-141-cases.yaml`, read by every implementation.
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

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-141-cases.yaml").read_text())["cases"]
IDS = [case["name"] for case in CASES]


def run_codec(schema, data, payload=None):
    call = (
        "decodeUplink({bytes: " + json.dumps(list(payload)) + ", fPort: 1})"
        if payload is not None
        else "encodeDownlink({data: " + json.dumps(data) + ", fPort: 1})"
    )
    script = (
        TS013Generator(schema).generate()
        + "\nvar e; try { e = " + call + "; } catch (x) { e = {errors: [String(x.message)]}; }"
        "\nprocess.stdout.write(JSON.stringify(e));"
    )
    done = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_reference(case):
    schema = yaml.safe_load(case["schema"])
    out = SchemaInterpreter(schema).encode(json.loads(case["input"]))
    if "error" in case:
        assert any(case["error"] in e for e in out.errors), out.errors
        assert out.payload == b"", out.payload.hex()
        return
    assert out.success, out.errors
    assert out.payload.hex() == case["payload"]
    assert out.warnings == [], out.warnings
    if "decoded" in case:
        back = SchemaInterpreter(schema).decode(out.payload)
        assert back.success, back.errors
        assert back.data == json.loads(case["decoded"])


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    out = run_codec(schema, json.loads(case["input"]))
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
        assert not out.get("bytes"), out
        return
    assert not out.get("errors"), out
    assert bytes(out["bytes"]).hex() == case["payload"]
    assert not out.get("warnings"), out
    if "decoded" in case:
        back = run_codec(schema, None, bytes.fromhex(case["payload"]))
        assert not back.get("errors"), back
        assert back["data"] == json.loads(case["decoded"])
