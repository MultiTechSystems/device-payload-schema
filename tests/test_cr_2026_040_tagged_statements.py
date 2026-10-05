"""CR-2026-040: the five statements it tagged, as far as tooling can hold them.

PS-453 and PS-456 are schema rules, so the validator enforces them. PS-454 and PS-455
(stop on a direction disagreement, and report it differently from an unmatched FPort)
were already implemented everywhere by CR-2026-010; tests/test_cr_2026_010_direction.py
and each language's CR-010 tests hold them. PS-457 is the C binary schema recording the
lookup spelling, held by src/selftest_schema.c (test_sequence_flag_survives_binary).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from validate_schema import validate_schema, validate_schema_structure  # noqa: E402


def test_a_bytes_field_without_length_is_an_error():
    errors = validate_schema_structure({"name": "p", "fields": [{"name": "b", "type": "bytes"}]})
    assert any("PS-456" in e for e in errors), errors


def test_a_bytes_field_with_length_is_fine():
    schema = {"name": "p", "fields": [{"name": "b", "type": "bytes", "length": 2}]}
    assert not any("PS-456" in e for e in validate_schema_structure(schema))


def test_consume_zero_off_a_bit_range_is_warned():
    result = validate_schema({"name": "p", "fields": [
        {"name": "b", "type": "u8", "consume": 0}, {"name": "c", "type": "u8"}]})
    assert any("PS-453" in w for w in result.schema_warnings), result.schema_warnings


def test_consume_zero_on_a_bit_range_or_bool_is_not_warned():
    result = validate_schema({"name": "p", "fields": [
        {"name": "a", "type": "u8[0:3]", "consume": 0},
        {"name": "b", "type": "bool", "consume": 0},
        {"name": "c", "type": "u8[4:7]", "consume": 1}]})
    assert not any("PS-453" in w for w in result.schema_warnings)
