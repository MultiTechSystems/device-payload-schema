"""CR-2026-171 (PS-390): `ties: up` rounds an exact tie toward +infinity, as Math.round.

A negative tie goes toward zero (-2.5 is -2), where `away` gives -3 and `even` -2 or -4. Any
other `ties` is refused at load. The reference, the validator, the TS013 generator and the
generated codec (run under node) all read the case list in `tests/cr-2026-171-cases.yaml`,
which Go, Java and C# read too.
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
from schema_binary import schema_errors  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-171-cases.yaml").read_text())["cases"]
REJECTED = [c for c in CASES if "error" in c]
DECODED = [c for c in CASES if "error" not in c]


def payload_bytes(case):
    return bytes.fromhex(case["payload"].replace(" ", ""))


@pytest.mark.parametrize("case", DECODED, ids=[c["name"] for c in DECODED])
def test_the_reference_decodes_it(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(payload_bytes(case))
    assert out.success, out.errors
    assert out.data == json.loads(case["expected"])


@pytest.mark.parametrize("case", DECODED, ids=[c["name"] for c in DECODED])
def test_the_generated_codec_decodes_it(case):
    schema = yaml.safe_load(case["schema"])
    script = (TS013Generator(schema).generate() + "\nvar out = decodeUplink({bytes: %s, fPort: 1});"
              "\nprocess.stdout.write(JSON.stringify(out));" % json.dumps(list(payload_bytes(case))))
    done = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True)
    out = json.loads(done.stdout)
    assert not out.get("errors"), out
    assert out["data"] == json.loads(case["expected"])


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_every_path_refuses_an_unknown_rule(case):
    schema = yaml.safe_load(case["schema"])
    decoded = SchemaInterpreter(schema).decode(b"\x01")
    assert any(case["error"] in e for e in decoded.errors), decoded.errors
    assert any(case["error"] in e for e in validate_schema_structure(schema))
    with pytest.raises(ValueError, match=case["error"]):
        TS013Generator(schema).generate()
    assert schema_errors(schema)   # C has no transform at all, so the converter refuses it (PS-446)
