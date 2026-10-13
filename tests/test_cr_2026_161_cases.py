"""CR-2026-161 (PS-570, PS-571): `_meta` carries `reports`; a lookup's labels are of one type.

The cases live in `tests/cr-2026-161-cases.yaml`, read by every implementation that produces
`_meta`. A bad lookup is rejected on every load path: the interpreter, the validator and the
TS013 generator.
"""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter, schema_iterator_errors  # noqa: E402
from validate_schema import validate_schema  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-161-cases.yaml").read_text())["cases"]
REJECTED = [c for c in CASES if "error" in c]
ACCEPTED = [c for c in CASES if "error" not in c]


@pytest.mark.parametrize("case", ACCEPTED, ids=[c["name"] for c in ACCEPTED])
def test_the_reported_type(case):
    schema = yaml.safe_load(case["schema"])
    res = SchemaInterpreter(schema).interpret(bytes.fromhex(case["payload"]))
    assert res.success, res.errors
    entry = res.data["_meta"]["fields"]["f"]
    assert entry["reports"] == case["reports"]
    assert list(entry)[:2] == ["type", "reports"]
    if "element_reports" in case:
        assert entry["element"]["reports"] == case["element_reports"]
    assert validate_schema(schema).schema_valid


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_a_lookup_that_mixes_label_types_is_rejected(case):
    schema = yaml.safe_load(case["schema"])
    assert any(case["error"] in e for e in SchemaInterpreter(schema)._load_errors)
    assert any(case["error"] in e for e in schema_iterator_errors(schema))
    result = validate_schema(schema)
    assert not result.schema_valid
    assert any(case["error"] in e for e in result.schema_errors), result.schema_errors
    with pytest.raises(Exception, match="PS-571"):
        TS013Generator(schema).generate()


def test_a_discriminator_and_channels_report():
    schema = {"name": "t", "version": 1, "fields": [
        {"match": {"name": "kind", "length": 5, "cases": {1: []}, "default": []}},
        {"tlv": {"merge": False, "length_size": 1,
                 "cases": {1: [{"name": "b", "type": "u8"}]}}}]}
    fields = SchemaInterpreter(schema).interpret(bytes.fromhex("0000000001" "010109")).data["_meta"]["fields"]
    assert fields["kind"]["reports"] == "integer"
    assert fields["channels"]["reports"] == "array"
