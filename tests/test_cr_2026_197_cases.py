"""CR-2026-197 (PS-514 amended, PS-638): a corrected vendor outcome on a vector that
expects an error.

The cases live in `tests/cr-2026-197-cases.yaml` and mirror the spec prototype's
TestCR2026197CorrectedErrorVector. The rule is a test-vector one, so it is held on the
validator (`correction_errors`), which the provenance gate and the scorer share, and on
`is_independent_vector` and `crossvalidate_ttn.corrections`.
"""

import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from crossvalidate_ttn import compare, corrections  # noqa: E402
from validate_schema import (  # noqa: E402
    correction_errors,
    is_independent_vector,
    validate_schema,
)

DOC = yaml.safe_load((ROOT / "tests" / "cr-2026-197-cases.yaml").read_text())
CASES = DOC["cases"]
REJECTED = [c for c in CASES if "error" in c]
ACCEPTED = [c for c in CASES if "error" not in c]


def schema_with(case):
    schema = yaml.safe_load(DOC["schema"])
    vector = dict({"name": "v", "payload": "01"}, **copy.deepcopy(case["vector"]))
    schema["test_vectors"] = [vector]
    return schema, vector


@pytest.mark.parametrize("case", ACCEPTED, ids=[c["name"] for c in ACCEPTED])
def test_accepted(case):
    schema, vector = schema_with(case)
    assert correction_errors(vector) == []
    result = validate_schema(schema)
    assert not any("PS-514" in e or "PS-638" in e for e in result.schema_errors), (
        result.schema_errors
    )


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_rejected(case):
    schema, vector = schema_with(case)
    assert any(case["error"] in e for e in correction_errors(vector))
    result = validate_schema(schema)
    assert not result.schema_valid
    assert any(case["error"] in e for e in result.schema_errors), result.schema_errors


def test_a_whole_output_correction_is_not_independent():
    # PS-515: the entry corrects everything the codec produced.
    _, vector = schema_with(ACCEPTED[0])
    assert not is_independent_vector(vector, {"vendor-codec"})


def test_crossvalidation_takes_the_rejection_as_corrected():
    schema, _ = schema_with(ACCEPTED[0])
    schema["fields"].append({"name": "y", "type": "u8"})  # 01 is now one byte short
    assert corrections(schema) == {("01", None): {None}}
    assert compare(schema, bytes.fromhex("01"), None, {"valid": False}) == []
    assert compare(schema, bytes.fromhex("02"), None, {"valid": False})
