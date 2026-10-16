"""CR-2026-194 (PS-436, PS-437, PS-438, PS-440, PS-270): a one-field tlv tag's "[*]" and
"[!n]" keys rank below exact keys, and a tlv has no `default` case.

The cases live in `tests/cr-2026-194-cases.yaml`, read by every implementation. Each is
held here on the reference, the validator, the TS013 generator and the generated codec.
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
from validate_schema import validate_schema  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-194-cases.yaml").read_text())["cases"]
REJECTED = [c for c in CASES if "error" in c]
ACCEPTED = [c for c in CASES if "error" not in c]


def run_codec(schema, payloads):
    calls = ", ".join(
        "decodeUplink({bytes: %s, fPort: 1})" % json.dumps(list(bytes.fromhex(p)))
        for p in payloads
    )
    script = (
        TS013Generator(schema).generate()
        + "\nprocess.stdout.write(JSON.stringify(["
        + calls
        + "]));"
    )
    done = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize("case", ACCEPTED, ids=[c["name"] for c in ACCEPTED])
def test_reference_ranks_the_keys(case):
    schema = yaml.safe_load(case["schema"])
    interpreter = SchemaInterpreter(schema)
    for d in case["decodes"]:
        res = interpreter.decode(bytes.fromhex(d["payload"]))
        assert res.success, res.errors
        assert res.data == json.loads(d["decoded"])
    assert validate_schema(schema).schema_valid


@pytest.mark.parametrize("case", ACCEPTED, ids=[c["name"] for c in ACCEPTED])
def test_generated_codec_ranks_the_keys(case):
    schema = yaml.safe_load(case["schema"])
    outs = run_codec(schema, [d["payload"] for d in case["decodes"]])
    for d, out in zip(case["decodes"], outs):
        assert out["errors"] == [], out
        assert out["data"] == json.loads(d["decoded"])


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_rejected_at_load(case):
    schema = yaml.safe_load(case["schema"])
    interpreter = SchemaInterpreter(schema)
    assert case["error"] in interpreter._load_errors
    res = interpreter.decode(bytes(3))
    assert not res.success
    assert any(case["error"] in e for e in res.errors)
    result = validate_schema(schema)
    assert not result.schema_valid
    assert any(case["error"] in e for e in result.schema_errors)
    with pytest.raises(Exception, match=r"PS-4[34][0-9]"):
        TS013Generator(schema).generate()


def test_composite_tag_with_a_default_case_is_rejected():
    """PS-438 rejects a `default` key in any tlv's cases, not only a one-field tag's."""
    schema = {
        "name": "t",
        "fields": [
            {
                "tlv": {
                    "tag_fields": [
                        {"name": "ch", "type": "u8"},
                        {"name": "ty", "type": "u8"},
                    ],
                    "tag_key": ["ch", "ty"],
                    "cases": {
                        "[1, 2]": [{"name": "a", "type": "u8"}],
                        "default": [{"name": "x", "type": "u8"}],
                    },
                }
            }
        ],
    }
    assert any("PS-436, PS-438" in e for e in SchemaInterpreter(schema)._load_errors)
