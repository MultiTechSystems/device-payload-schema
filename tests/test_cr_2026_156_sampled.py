"""CR-2026-156 (0.5.5, PS-567 to PS-569): `sampled` on a repeat.

The cases live in `tests/cr-2026-156-cases.yaml`, read by every implementation. Here they
run through the reference interpreter, and through the validator and the TS013 generator,
which must reject what the interpreter rejects.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter, reference_errors, schema_iterator_errors  # noqa: E402
from validate_schema import validate_schema  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-156-cases.yaml").read_text())["cases"]
IDS = [case["name"] for case in CASES]
REJECTED = [c for c in CASES if "error" in c]
ACCEPTED = [c for c in CASES if "error" not in c]


@pytest.mark.parametrize("case", ACCEPTED, ids=[c["name"] for c in ACCEPTED])
def test_meta_carries_sampled(case):
    schema = yaml.safe_load(case["schema"])
    interpreter = SchemaInterpreter(schema)
    assert not interpreter._load_errors
    res = interpreter.interpret(bytes.fromhex(case["payload"]))
    assert res.success, res.errors
    out = res.data
    entry = out["_meta"]["fields"]["pm25"]["sampled"]
    want = json.loads(case["sampled"])
    assert entry == want
    assert list(entry) == ["interval", "order", "at"]
    if "decoded" in case:
        assert out["pm25"] == json.loads(case["decoded"])        # decoding is unchanged


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_a_bad_declaration_is_rejected_at_load(case):
    schema = yaml.safe_load(case["schema"])
    assert any(case["error"] in e for e in SchemaInterpreter(schema)._load_errors)
    assert any(case["error"] in e for e in schema_iterator_errors(schema) + reference_errors(schema))
    result = validate_schema(schema)
    assert not result.schema_valid
    assert any(case["error"] in e for e in result.schema_errors), result.schema_errors
    with pytest.raises(Exception, match="PS-5[36]9"):  # PS-569, or PS-539 for a reference
        TS013Generator(schema).generate()
