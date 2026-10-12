"""No silent empty encode (PS-442, PS-551).

An encoder never returns success having written nothing for a construct whose decode
reads bytes - a match with no case chosen, a tlv entry no case claimed - nor an empty
payload where the input gives a value only payload bytes carry. The cases live in
`tests/silent-empty-cases.yaml`, read by every implementation.
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
from schema_interpreter import (  # noqa: E402
    SchemaInterpreter,
    declared_sources,
    empty_payload_error,
)

CASES = yaml.safe_load((ROOT / "tests" / "silent-empty-cases.yaml").read_text())["cases"]
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
        assert out.payload == b"", out.payload
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


FIELDS = yaml.safe_load(
    """
- {name: battery, type: u8}
- match:
    remaining: true
    cases:
      2:
        - {name: _w, type: u16}
        - {name: distance, type: number, ref: $_w}
    default:
      - {name: distance, type: string, value: none}
      - {name: kind, type: number, value: 4}
"""
)


class TestTheEmptyPayloadGuard:
    """The last line, which no path in the reference reaches today: each construct now
    fails by name first. Held directly, as each port holds its own copy."""

    def test_declared_sources_tell_reads_from_constants(self):
        sources = declared_sources(FIELDS)
        assert sources["battery"] == {"reads": True, "literals": []}
        assert sources["distance"] == {"reads": True, "literals": ["none"]}
        assert sources["kind"] == {"reads": False, "literals": [4]}

    def test_a_value_only_bytes_carry_is_named(self):
        assert empty_payload_error(FIELDS, {"battery": 3}) == (
            "Encoding wrote no bytes, though the input gives 'battery', which the "
            "schema reads from the payload (PS-442)"
        )

    def test_a_value_a_constant_gives_is_not(self):
        assert empty_payload_error(FIELDS, {"distance": "none", "kind": 4}) is None

    def test_another_value_of_a_name_bytes_also_carry_is_named(self):
        assert "'distance'" in empty_payload_error(FIELDS, {"distance": 12})

    def test_values_written_in_no_bytes_are_not(self):
        data = {"battery": [], "distance": "", "_w": 5, "other": 1, "x": None}
        assert empty_payload_error(FIELDS, data) is None
