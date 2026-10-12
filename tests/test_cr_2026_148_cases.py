"""CR-2026-148 (PS-527 amended, draft for 0.5.5): which tlv case takes a value.

A case's field takes a value that, with its arithmetic undone and an integer rounded as
PS-552 requires, lies within the type's range (PS-168); among those, a case that
reproduces the value exactly under binary64 equality is preferred, with no tolerance.
The cases live in `tests/cr-2026-148-cases.yaml`, read by every implementation.
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

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-148-cases.yaml").read_text())["cases"]
IDS = [case["name"] for case in CASES]


def encode_codec(schema, data):
    script = (
        TS013Generator(schema).generate()
        + "\nvar e; try { e = encodeDownlink({data: "
        + json.dumps(data)
        + ", fPort: 1}); } catch (x) { e = {errors: [String(x.message)]}; }"
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
    else:
        assert out.success, out.errors
        assert out.payload.hex() == case["payload"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    out = encode_codec(schema, json.loads(case["input"]))
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
        assert not out.get("bytes"), out
    else:
        assert not out.get("errors"), out
        assert bytes(out["bytes"]).hex() == case["payload"]
