"""CR-2026-154 (PS-566): a negative zero is reported as 0 and written as the positive-zero word.

It covers every floating-point type. A decoder reports a negative zero as 0; an encoder writes
the zero word for an input of -0.0, for a decoded -0 and for a -0 its own arithmetic produces.
Python already reported 0 (the output makes an integral value an integer) and wrote the sign
word for an input of -0.0; the generated codec kept -0 and wrote the sign word for all three.

The cases live in `tests/cr-2026-154-cases.yaml`, read by every implementation.
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-154-cases.yaml").read_text())
ENCODE = DOC["encode_cases"]
DECODE = DOC["decode_cases"]


def run_codec(schema, call):
    """Run `call` (JavaScript) against the generated codec; its value is JSON on stdout."""
    script = TS013Generator(schema).generate() + "\nvar out = " + call + ";\n" \
        "process.stdout.write(JSON.stringify(out));"
    done = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def is_positive_zero(value):
    return value == 0 and math.copysign(1, value) == 1


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_reference(case):
    out = SchemaInterpreter(yaml.safe_load(case["schema"])).encode(json.loads(case["input"]))
    assert out.success, out.errors
    assert out.payload.hex() == case["payload"]


@pytest.mark.parametrize("case", ENCODE, ids=[c["name"] for c in ENCODE])
def test_encode_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    out = run_codec(schema, "encodeDownlink({data: %s, fPort: 1})" % case["input"])
    assert not out.get("errors"), out
    assert bytes(out["bytes"]).hex() == case["payload"]


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_and_reencode_reference(case):
    interpreter = SchemaInterpreter(yaml.safe_load(case["schema"]))
    decoded = interpreter.decode(bytes.fromhex(case["payload"]))
    assert decoded.success, decoded.errors
    assert is_positive_zero(decoded.data["x"]), repr(decoded.data["x"])
    out = interpreter.encode(decoded.data)
    assert out.success, out.errors
    assert out.payload.hex() == case["reencode"]


@pytest.mark.parametrize("case", DECODE, ids=[c["name"] for c in DECODE])
def test_decode_and_reencode_generated_codec(case):
    schema = yaml.safe_load(case["schema"])
    # JSON.stringify writes -0 as 0, so the sign is read off the value itself.
    out = run_codec(
        schema,
        "(function () { var d = decodeUplink({bytes: %s, fPort: 1}); var x = d.data.x;"
        " return {x: x, negative: Object.is(x, -0), errors: d.errors,"
        " back: encodeDownlink({data: d.data, fPort: 1})}; })()"
        % json.dumps(list(bytes.fromhex(case["payload"]))),
    )
    assert not out["errors"], out
    assert out["x"] == 0 and not out["negative"], out
    assert not out["back"].get("errors"), out
    assert bytes(out["back"]["bytes"]).hex() == case["reencode"]
