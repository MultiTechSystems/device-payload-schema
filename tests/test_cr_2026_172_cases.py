"""CR-2026-172 (PS-119, PS-584): `compute` `pow`.

A result that is not a finite real number omits the field and decoding continues. The cases
live in `tests/cr-2026-172-cases.yaml`, read by every implementation.
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

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-172-cases.yaml").read_text())["decode_cases"]


def same(got, want):
    assert sorted(got) == sorted(want), (got, want)
    for key, value in want.items():
        assert got[key] == pytest.approx(value, rel=1e-9), (key, got, want)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(bytes.fromhex(case["payload"]))
    assert out.success, out.errors
    same(out.data, json.loads(case["expected"]))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    script = (TS013Generator(schema).generate() + "\nvar out = decodeUplink({bytes: %s, fPort: 1});"
              "\nprocess.stdout.write(JSON.stringify(out));"
              % json.dumps(list(bytes.fromhex(case["payload"]))))
    done = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True)
    out = json.loads(done.stdout)
    assert not out.get("errors"), out
    same(out["data"], json.loads(case["expected"]))
