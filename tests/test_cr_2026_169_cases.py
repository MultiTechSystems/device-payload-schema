"""CR-2026-169 (0.5.7): which port entry an encoder uses (PS-581).

A caller's FPort chooses the entry (or `default`), an uplink entry is refused only for a
downlink; without one, the single declared downlink entry, else the single downlink-capable
one, else a rejection. The cases live in `tests/cr-2026-169-cases.yaml`, read by every
implementation.
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

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-169-cases.yaml").read_text())["cases"]
IDS = [case["name"] for case in CASES]


def encode_codec(schema, data, fport):
    entry = {"data": data}
    if fport is not None:
        entry["fPort"] = fport
    script = (
        TS013Generator(schema).generate()
        + "\nvar e; try { e = encodeDownlink("
        + json.dumps(entry)
        + "); } catch (x) { e = {errors: [String(x.message)]}; }"
        "\nprocess.stdout.write(JSON.stringify(e));"
    )
    done = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_reference(case):
    schema = yaml.safe_load(case["schema"])
    direction = None if case.get("direction") == "none" else "downlink"
    out = SchemaInterpreter(schema).encode(
        json.loads(case["input"]), fPort=case.get("fport"), direction=direction
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.errors), out.errors
        assert out.payload == b""
    else:
        assert out.success, out.errors
        assert out.payload.hex() == case["payload"]
        assert out.fport == case["used"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_generated_codec(case):
    if case.get("direction") == "none":
        pytest.skip("encodeDownlink is always a downlink")
    schema = yaml.safe_load(case["schema"])
    out = encode_codec(schema, json.loads(case["input"]), case.get("fport"))
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
        assert not out.get("bytes"), out
    else:
        assert not out.get("errors"), out
        assert bytes(out["bytes"]).hex() == case["payload"]
        assert out["fPort"] == case["used"]


def test_the_reference_takes_no_fport_where_one_entry_is_chosen():
    """PS-459 is a decoder's rule: an encoder with no FPort no longer raises."""
    schema = {"name": "t", "ports": {2: {"direction": "downlink",
                                         "fields": [{"name": "a", "type": "u8"}]}}}
    assert SchemaInterpreter(schema).encode({"a": 3}).payload == b"\x03"
