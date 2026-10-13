"""CR-2026-160 (PS-059): u40/u48/u56 and s40/s48/s56 are bit-range bases only.

A standalone `type: u56` is an unknown type, on every load path (the interpreter, the
validator, the TS013 generator and C's binary form), while a bit range over such a base is
read. The cases live in `tests/cr-2026-160-cases.yaml`, read by every implementation.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_binary import encode_schema  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-160-cases.yaml").read_text())["cases"]
REJECTED = [c for c in CASES if "error" in c]
ACCEPTED = [c for c in CASES if "error" not in c]


@pytest.mark.parametrize("case", ACCEPTED, ids=[c["name"] for c in ACCEPTED])
def test_a_wide_base_is_read(case):
    schema = yaml.safe_load(case["schema"])
    res = SchemaInterpreter(schema).decode(bytes.fromhex(case["payload"]))
    assert res.success, res.errors
    assert res.data == json.loads(case["decoded"])
    assert validate_schema(schema).schema_valid


@pytest.mark.parametrize("case", ACCEPTED, ids=[c["name"] for c in ACCEPTED])
def test_a_wide_base_generates_a_codec(case):
    assert TS013Generator(yaml.safe_load(case["schema"])).generate()


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_a_standalone_wide_type_is_rejected(case):
    schema = yaml.safe_load(case["schema"])
    interpreter = SchemaInterpreter(schema)
    res = interpreter.decode(bytes(8))
    assert not res.success
    assert any(case["error"] in e for e in res.errors + list(interpreter._load_errors))
    result = validate_schema(schema)
    assert not result.schema_valid
    with pytest.raises(Exception, match=case["error"]):
        TS013Generator(schema).generate()
    with pytest.raises(Exception, match=case["error"]):
        encode_schema(schema)
