"""CR-2026-146 (PS-169, 0.5.5): a label two or more codes share cannot be encoded.

Decoding is unaffected. An encoder given a label that several codes of a lookup (mapping or
sequence) or an enum share rejects it, naming the field and the codes, and writes none of
them; the code given as a number still encodes. Every implementation wrote the first code it
found, which is a guess. An enum encodes a name back to its code and a code as given; a name
with no code is rejected (PS-067).

The cases live in `tests/cr-2026-146-cases.yaml`, read by every implementation.
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

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-146-cases.yaml").read_text())["cases"]
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


def test_decoding_a_shared_label_is_unaffected():
    schema = {
        "name": "t",
        "fields": [
            {
                "name": "x",
                "type": "u8",
                "lookup": {45: "AnalogSensor45", 109: "AnalogSensor45"},
            }
        ],
    }
    out = SchemaInterpreter(schema).decode(bytes([109]))
    assert out.success and out.data == {"x": "AnalogSensor45"}


def test_the_validator_warns_of_a_shared_label():
    """PS-169's SHOULD: a label several codes share is warned about where the field can be
    encoded, and not on a computed field, which is never written (PS-214)."""
    from validate_schema import validate_schema

    shared = {
        "name": "t",
        "fields": [
            {"name": "x", "type": "u8", "lookup": {1: "a", 2: "b", 3: "b"}},
            {"name": "e", "type": "enum", "base": "u8", "values": {4: "on", 5: "on"}},
            {"name": "c", "type": "number", "ref": "$x", "lookup": {1: "z", 2: "z"}},
        ],
    }
    warnings = [w for w in validate_schema(shared).schema_warnings if "PS-169" in w]
    assert any(
        w.startswith("x:") and "'b' is shared by codes [2, 3]" in w for w in warnings
    )
    assert any(
        w.startswith("e:") and "'on' is shared by codes [4, 5]" in w for w in warnings
    )
    assert not any(w.startswith("c:") for w in warnings), warnings
    unshared = {
        "name": "t",
        "fields": [{"name": "x", "type": "u8", "lookup": ["a", "b"]}],
    }
    assert not [w for w in validate_schema(unshared).schema_warnings if "PS-169" in w]
