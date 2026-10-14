"""A range case key `start..end` has numeric bounds, not necessarily integers (PS-398), and a
malformed one is rejected at load instead of matching nothing.

`_language-conformance/match-fractional-range-keys.yaml` holds all five implementations to
the decoded values of fractional bounds. What a fixture cannot state is here: the load
errors, their text (identical in the interpreter, the validator and the generator), an
integer discriminator against fractional bounds, and a hexadecimal bound.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import (  # noqa: E402
    SchemaInterpreter, parse_range_case_key, range_key_error)
from validate_schema import validate_schema_structure  # noqa: E402

import yaml  # noqa: E402


def schema_with(key):
    return yaml.safe_load(
        "name: p\nfields:\n- name: v\n  type: u8\n- match:\n    field: $v\n    default: []\n    cases:\n"
        f"      '{key}':\n        - {{name: hit, type: u8}}\n")


@pytest.mark.parametrize("key", ["a..b", "1..", "..2", "1...5", "1.5.5..9", "5..2", "9..1.5"])
def test_a_malformed_range_key_is_rejected_at_load(key):
    problem = range_key_error(key)
    assert problem and "PS-" in problem and "PS-398" in problem
    want = f"match.cases[{key}]: {problem}"
    assert any(want in e for e in validate_schema_structure(schema_with(key)))
    assert any(want in e for e in SchemaInterpreter(schema_with(key))._load_errors)
    with pytest.raises(Exception, match="PS-398"):
        TS013Generator(schema_with(key)).generate()


@pytest.mark.parametrize("key,bounds", [
    ("2..5", (2, 5)), ("12.1..35.4", (12.1, 35.4)), ("-10.5..-0.5", (-10.5, -0.5)),
    ("0x10..0x1F", (16, 31)), ("1e2..2.5e2", (100.0, 250.0)), (" 3 .. 4 ", (3, 4))])
def test_a_range_key_reads_its_bounds_as_numbers(key, bounds):
    assert parse_range_case_key(key) == bounds
    assert range_key_error(key) is None
    assert not validate_schema_structure(schema_with(key))


def test_an_integer_discriminator_meets_fractional_bounds():
    s = SchemaInterpreter(schema_with("2.5..7.5"))
    got = {v: s.decode(bytes([v, 9])).data.get("hit") for v in (2, 3, 7, 8)}
    assert got == {2: None, 3: 9, 7: 9, 8: None}


def test_a_hexadecimal_bound_matches():
    s = SchemaInterpreter(schema_with("0x10..0x1F"))
    assert s.decode(bytes([0x10, 5])).data.get("hit") == 5
    assert s.decode(bytes([0x20, 5])).data.get("hit") is None
