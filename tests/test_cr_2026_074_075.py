"""CR-2026-074 (`type: object`, the `object:` key withdrawn) and CR-2026-075 (a null
expectation asserts absence).

CR-074 made the corpus's spelling the specification's: a nested group is `type: object`
with `name` and `fields` (PS-139), and the `object:` key, which only this interpreter read,
is rejected naming the spelling to use (PS-466). CR-075 gave the vector format a way to
say "absent": a key expected as null must not be reported, with any value (PS-043); every
other listed key must be present and match (PS-044).
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import expected_fields_match, validate_schema_structure  # noqa: E402

OBJECT_KEY = {"name": "probe", "fields": [
    {"object": "reading", "fields": [{"name": "v", "type": "u8"}]}]}


def test_the_object_key_is_rejected_naming_type_object():
    errors = SchemaInterpreter(OBJECT_KEY).decode(b"\x01").errors
    assert any("PS-466" in e and "type: object" in e for e in errors), errors
    assert any("PS-466" in e for e in validate_schema_structure(OBJECT_KEY))
    with pytest.raises(ValueError, match="PS-466"):
        TS013Generator(OBJECT_KEY).generate()


def test_type_object_is_the_spelling():
    schema = {"name": "probe", "fields": [
        {"name": "reading", "type": "object", "fields": [{"name": "v", "type": "u8"}]}]}
    assert SchemaInterpreter(schema).decode(b"\x07").data == {"reading": {"v": 7}}


@pytest.mark.parametrize("actual,ok", [
    ({"after": 5}, True),
    ({"gone": 0, "after": 5}, False),
    ({"gone": None, "after": 5}, False),
    ({"gone": "", "after": 5}, False),
])
def test_null_asserts_absence_with_any_value(actual, ok):
    assert expected_fields_match({"gone": None, "after": 5}, actual)[0] is ok


def test_absence_holds_inside_a_nested_object():
    expected = {"reading": {"v": 7, "gone": None}}
    assert expected_fields_match(expected, {"reading": {"v": 7}})[0]
    assert not expected_fields_match(expected, {"reading": {"v": 7, "gone": 1}})[0]


def test_unlisted_keys_stay_unchecked():
    assert expected_fields_match({"a": 1}, {"a": 1, "b": 2})[0]
