"""CR-2026-159: constructs withdrawn for want of any use or of any test.

`formula`, `encode_formula`, a transform's `clamp` and a port's `confirmed` are refused at
load, and so is `min` or `max` on any field that is no repeat, each in the spec prototype's
text, naming what replaces it. The interpreter, the validator, the TS013 generator and the
binary converter for C refuse the same schemas from one case list, which Go, Java and C#
read too. A `valid_range` does not reject on encode, and nothing else in the encoders ever
checked a field's `min`/`max`.
"""

import json
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

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-159-cases.yaml").read_text())["cases"]
REJECTED = [c for c in CASES if "error" in c]
LOADED = [c for c in CASES if "error" not in c]


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_the_interpreter_refuses_it_on_decode_and_encode(case):
    schema = yaml.safe_load(case["schema"])
    port = case.get("fport")
    decoded = SchemaInterpreter(schema).decode(b"\x01\x02\x03\x04", fPort=port)
    assert case["error"] in decoded.errors, decoded.errors
    assert decoded.data == {}
    encoded = SchemaInterpreter(schema).encode({"v": 1}, fPort=port)
    assert case["error"] in encoded.errors, encoded.errors
    assert encoded.payload == b""


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_the_validator_and_the_generator_refuse_it(case):
    schema = yaml.safe_load(case["schema"])
    assert case["error"] in validate_schema_structure(schema)
    with pytest.raises(ValueError, match="CR-2026-159"):
        TS013Generator(schema).generate()


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_the_binary_converter_refuses_it_for_c(case):
    assert case["error"] in schema_errors(yaml.safe_load(case["schema"]))


@pytest.mark.parametrize("case", LOADED, ids=[c["name"] for c in LOADED])
def test_the_valid_ones_load_decode_and_re_encode(case):
    schema = yaml.safe_load(case["schema"])
    assert not any("CR-2026-159" in e for e in validate_schema_structure(schema))
    interpreter = SchemaInterpreter(schema)
    decoded = interpreter.decode(bytes.fromhex(case["payload"]))
    assert decoded.success, decoded.errors
    # `valid_range` reports a `_quality` beside the values, which the cases do not list
    assert {k: v for k, v in decoded.data.items() if k != "_quality"} == json.loads(
        case["expected"])
    encoded = interpreter.encode(decoded.data)
    assert encoded.success, encoded.errors
    assert encoded.payload.hex() == case["payload"]
    if "encode" in case:
        encoded = interpreter.encode(json.loads(case["encode"]))
        assert encoded.success, encoded.errors
        assert encoded.payload.hex() == case["encoded"]


def test_a_valid_range_does_not_reject_a_value_on_encode():
    schema = {"name": "t", "version": 1,
              "fields": [{"name": "v", "type": "u8", "valid_range": [10, 100]}]}
    for value in (5, 150):
        encoded = SchemaInterpreter(schema).encode({"v": value})
        assert encoded.success and encoded.payload == bytes([value]), encoded.errors


def test_the_repeat_limits_still_work():
    schema = {"name": "t", "version": 1, "fields": [
        {"name": "r", "type": "repeat", "until": "end", "max": 2, "min": 1,
         "fields": [{"name": "x", "type": "u8"}]}]}
    assert SchemaInterpreter(schema).decode(b"\x01\x02\x03").success is False
    assert SchemaInterpreter(schema).decode(b"\x01\x02").success is True


def test_the_dead_evaluators_are_gone():
    source = (ROOT / "tools" / "schema_interpreter.py").read_text()
    assert "_evaluate_formula" not in source and "_evaluate_encode_formula" not in source
    generator = (ROOT / "tools" / "generate_ts013_codec.py").read_text()
    assert "formula_to_js" not in generator and '"clamp" in st' not in generator
