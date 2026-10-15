"""CR-2026-182 (PS-453, PS-603, PS-604): a whole numeric field declaring
`consume: 0` reads its value in place; an encoder writes nothing for it and checks a value
given for it against what the bytes the other fields write decode to.

The shared cases live in `tests/cr-2026-182-cases.yaml`, read by every implementation:
`decode` (a payload and its data or error) and `cases` (an encode input and its payload or
error). The validator's and the C converter's rules are held here alone.
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
from validate_schema import validate_schema  # noqa: E402

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-182-cases.yaml").read_text())
DECODE, ENCODE = DOC["decode"], DOC["cases"]


def run_codec(schema, call):
    script = (
        TS013Generator(schema).generate()
        + "\nvar e; try { e = "
        + call
        + "; } catch (x) { e = {errors: [String(x.message)]}; }"
        "\nprocess.stdout.write(JSON.stringify(e));"
    )
    done = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(
        bytes.fromhex(case["payload"])
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.errors), out.errors
    else:
        assert out.success, out.errors
        assert out.data == json.loads(case["expected"])
        assert not out.warnings, out.warnings


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_generated_codec(case):
    payload = list(bytes.fromhex(case["payload"]))
    out = run_codec(
        yaml.safe_load(case["schema"]),
        "decodeUplink({bytes: %s, fPort: 1})" % json.dumps(payload),
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
    else:
        assert not out.get("errors"), out
        assert out["data"] == json.loads(case["expected"])


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).encode(json.loads(case["input"]))
    if "error" in case:
        assert any(case["error"] in e for e in out.errors), out.errors
        assert out.payload == b"", out.payload
    else:
        assert out.success, out.errors
        assert out.payload.hex() == case["payload"]


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_generated_codec(case):
    out = run_codec(
        yaml.safe_load(case["schema"]), "encodeDownlink({data: %s, fPort: 1})" % case["input"]
    )
    if "error" in case:
        assert any(case["error"] in e for e in out.get("errors") or []), out
        assert not out.get("bytes"), out
    else:
        assert not out.get("errors"), out
        assert bytes(out["bytes"]).hex() == case["payload"]


@pytest.mark.parametrize("ftype", ["u8", "s16", "f32", "bf16", "sflt24", "udec", "u32le16"])
def test_a_whole_number_read_in_place_is_not_warned(ftype):
    # PS-453 as amended defers to PS-603 for a whole numeric field.
    result = validate_schema({"name": "t", "fields": [
        {"name": "w", "type": ftype, "consume": 0}, {"name": "a", "type": "u64"}]})
    assert not any("PS-453" in w for w in result.schema_warnings), result.schema_warnings


def test_consume_zero_elsewhere_is_still_warned():
    result = validate_schema({"name": "t", "fields": [
        {"name": "w", "type": "base64", "length": 2, "consume": 0},
        {"name": "a", "type": "u16"}]})
    assert any("PS-453" in w for w in result.schema_warnings), result.schema_warnings


def test_the_c_converter_refuses_it():
    errors = schema_errors({"name": "t", "fields": [
        {"name": "w", "type": "u16", "consume": 0}, {"name": "a", "type": "u16"}]})
    assert any("PS-603" in e for e in errors), errors


def test_a_decode_record_round_trips():
    # The prototype's last case: the record carries the internal discriminator, which the
    # encoder compares with the bytes rather than writing it.
    schema = yaml.safe_load(DECODE[0]["schema"])
    interp = SchemaInterpreter(schema)
    out = interp.interpret(bytes.fromhex("0100d7"), decode_record=True)
    assert out.success, out.errors
    data = {k: v for k, v in out.data.items() if not k.startswith("_")}
    enc = SchemaInterpreter(schema).encode(data, decode_record=out.data["_decode"])
    assert enc.success, enc.errors
    assert enc.payload.hex() == "0100d7"
