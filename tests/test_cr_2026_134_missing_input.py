"""CR-2026-134 (PS-551): an encoder rejects missing input and derives what the input fixes.

An encoder wrote zeros for a required value its input omitted - with a warning at the top
level, silently inside a construct - and for a framed repeat whose list was absent, or a few
other shapes, wrote nothing at all, so every later field shifted (PS-442). Each now rejects
the input, naming the field, and derives what the rest of the input fixes: a repeat's
`count`/`byte_length` field from its list, a match's discriminator from the one case whose
fields are given, a flagged mask from the groups given. A supplied value that disagrees with
the derived one is rejected.

The cases live in `tests/cr-2026-134-cases.yaml`, read by every implementation, so the
reference, the generated codec's runtime encoder, Go, Java and C# are held to the same
bytes and the same text. A rejection cannot be a corpus vector, which is why they are here.
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

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-134-cases.yaml").read_text())["cases"]
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
        # A rejected input produces no payload, not the bytes written before the error.
        assert out.payload == b"", out.payload.hex()
    else:
        assert out.success, out.errors
        assert out.payload.hex() == case["payload"]
        assert out.warnings == [], out.warnings


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
        assert not out.get("warnings"), out


class TestTheOldBehaviourIsGone:
    """The text that announced a zero-fill is not produced anywhere it used to be."""

    def test_no_missing_field_warning(self):
        schema = yaml.safe_load(CASES[0]["schema"])
        out = SchemaInterpreter(schema).encode({"b": 1})
        assert not any("Missing field:" in w for w in out.warnings), out.warnings

    def test_a_bitfield_string_that_is_not_a_number_is_an_error_not_an_exception(self):
        schema = yaml.safe_load(
            "name: t\nfields:\n  - {name: v, type: bitfield_string, length: 1, "
            "parts: [[4, 4], [0, 4]]}\n"
        )
        out = SchemaInterpreter(schema).encode({"v": "x.y"})
        assert out.errors and out.payload == b""

    def test_encode_command_rejects_a_missing_field(self):
        interp = SchemaInterpreter(
            {
                "name": "t",
                "fields": [{"name": "a", "type": "u8"}],
                "downlink_commands": {
                    "set": {
                        "command_id": 1,
                        "fields": [{"name": "interval", "type": "u16"}],
                    }
                },
            }
        )
        out = interp.encode_command("set", {})
        assert "Missing required field: interval" in out.errors, out.errors
        assert out.payload == b""
