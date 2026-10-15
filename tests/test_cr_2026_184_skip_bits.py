"""CR-2026-184 (PS-607, amended 2026-10-15): a validator may note, as information, where a
skip or a `consume` advances past a byte that bit ranges or bools consuming nothing read only
in part.

The cases live in `tests/cr-2026-184-cases.yaml`, the spec prototype's
TestCR2026184SkipOverPartlyReadBits. Each runs through `skip_bit_notes()` and
`validate_schema()`, which must report the notes at its INFO level, never as a warning or
an error, and never reject the schema.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from schema_interpreter import SchemaInterpreter, skip_bit_notes  # noqa: E402
from validate_schema import ValidationLevel, validate_schema  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-184-cases.yaml").read_text())["cases"]
DECODED = [c for c in CASES if "payload" in c]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_notes(case):
    assert skip_bit_notes(yaml.safe_load(case["schema"])) == case["notes"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_validator_notes_as_info_and_never_warns(case):
    result = validate_schema(yaml.safe_load(case["schema"]))
    assert result.schema_valid, result.schema_errors
    assert [n for n in result.schema_info if "PS-607" in n] == case["notes"]
    assert [w for w in result.schema_warnings if "PS-607" in w] == []
    levels = {m.level for m in result.messages if "PS-607" in m.message}
    assert levels <= {ValidationLevel.INFO}


@pytest.mark.parametrize("case", DECODED, ids=[c["name"] for c in DECODED])
def test_the_schema_still_loads_and_decodes(case):
    res = SchemaInterpreter(yaml.safe_load(case["schema"])).decode(
        bytes.fromhex(case["payload"]))
    assert res.success, res.errors
    assert res.data == json.loads(case["expected"])


def test_a_ref_is_spliced_before_the_walk():
    schema = {
        "name": "t", "version": 1,
        "definitions": {"hdr": {"fields": [{"name": "_s", "type": "u8[0:0]"},
                                           {"type": "skip", "length": 1}]}},
        "fields": [{"$ref": "#/definitions/hdr"}, {"name": "v", "type": "u8"}],
    }
    assert skip_bit_notes(schema) == [
        "byte 0 is read in part (bit 0) and a skip advances past it; bits 1-7 are read by "
        "no field. A decoder discards them and an encoder writes them as 0 (PS-607)"]
