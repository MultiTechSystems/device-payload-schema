"""CR-2026-175 (PS-453, PS-015, PS-588, PS-589): a `bytes`, `hex` or `ascii` field declaring
`consume: 0` reports its span and leaves the read position; an encoder writes nothing for it
and checks a value given for it against the bytes the other fields write.

The shared cases live in `tests/cr-2026-175-cases.yaml`, read by every implementation:
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

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-175-cases.yaml").read_text())
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


ECHO = {
    "name": "t",
    "fields": [
        {"name": "raw", "type": "bytes", "length": "remaining", "consume": 0},
        {"name": "rest", "type": "bytes", "length": "remaining"},
    ],
}


def test_an_echo_does_not_count_towards_one_remaining_field():
    # PS-015 as amended: a field declaring `consume: 0` consumes nothing.
    result = validate_schema(ECHO)
    assert not any("PS-015" in e for e in result.schema_errors), result.schema_errors


def test_two_consuming_remainders_are_still_rejected():
    schema = {"name": "t", "fields": [dict(f, consume=1) for f in ECHO["fields"]]}
    result = validate_schema(schema)
    assert any("PS-015" in e for e in result.schema_errors), result.schema_errors


@pytest.mark.parametrize("ftype", ["bytes", "hex", "ascii"])
def test_a_byte_string_echo_is_not_warned(ftype):
    # PS-453 defers to PS-588 for a byte string.
    result = validate_schema({"name": "t", "fields": [
        {"name": "raw", "type": ftype, "length": 1, "consume": 0},
        {"name": "a", "type": "u8"}]})
    assert not any("PS-453" in w for w in result.schema_warnings), result.schema_warnings


def test_consume_zero_elsewhere_is_still_warned():
    result = validate_schema({"name": "t", "fields": [
        # A whole number reads in place since CR-2026-182 (PS-603); a base64 does not.
        {"name": "w", "type": "base64", "length": 2, "consume": 0},
        {"name": "a", "type": "u8"}]})
    assert any("PS-453" in w for w in result.schema_warnings), result.schema_warnings


def test_the_c_converter_refuses_it():
    errors = schema_errors({"name": "t", "fields": [
        {"name": "raw", "type": "hex", "length": 2, "consume": 0},
        {"name": "a", "type": "u16"}]})
    assert any("PS-588" in e for e in errors), errors
