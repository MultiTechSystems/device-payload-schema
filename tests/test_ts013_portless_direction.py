"""A declared schema-level `direction` is enforced by the generated TS013 codec too.

The generator emitted direction enforcement only for schemas with `ports`, so a portless
schema declared `direction: uplink` still decoded and encoded a downlink there while
Python, Go, Java, C# and C refused it (PS-288, PS-291, PS-292). The text is the
reference interpreter's. A schema declaring nothing accepts both directions (PS-287).
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402


def schema(direction=None):
    s = {"name": "p", "fields": [{"name": "a", "type": "u8"}]}
    if direction:
        s["direction"] = direction
    return s


def run(sch, call):
    code = TS013Generator(sch).generate()
    script = code + "\nprocess.stdout.write(JSON.stringify(" + call + "));"
    out = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


MESSAGE = "schema 'p' is declared direction:uplink; message direction is downlink"


def test_declared_uplink_rejected_by_decode_downlink():
    ref = SchemaInterpreter(schema("uplink")).decode(b"\x05", direction="downlink")
    assert ref.errors == [MESSAGE]
    js = run(schema("uplink"), "decodeDownlink({bytes: [5], fPort: 1})")
    assert js["errors"] == [MESSAGE] and js["data"] == {}


def test_declared_uplink_rejected_by_encode_downlink():
    js = run(schema("uplink"), "encodeDownlink({data: {a: 5}, fPort: 1})")
    assert js["errors"] == [MESSAGE] and js["bytes"] == []


def test_declared_uplink_still_decodes_uplink():
    js = run(schema("uplink"), "decodeUplink({bytes: [5], fPort: 1})")
    assert js["errors"] == [] and js["data"] == {"a": 5}


def test_declared_downlink_rejected_by_decode_uplink():
    js = run(schema("downlink"), "decodeUplink({bytes: [5], fPort: 1})")
    assert js["errors"] == [
        "schema 'p' is declared direction:downlink; message direction is uplink"
    ]


def test_undeclared_and_both_accept_both_directions():
    for direction in (None, "both"):
        sch = schema(direction)
        assert run(sch, "decodeUplink({bytes: [5], fPort: 1})")["data"] == {"a": 5}
        assert run(sch, "decodeDownlink({bytes: [5], fPort: 1})")["data"] == {"a": 5}
        enc = run(sch, "encodeDownlink({data: {a: 5}, fPort: 1})")
        assert enc["errors"] == [] and enc["bytes"] == [5]
