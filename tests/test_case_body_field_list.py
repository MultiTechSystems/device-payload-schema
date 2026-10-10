"""A nested field list - a tlv case body, a `type: object`'s members - is decoded as the
top level is, and a case body that is not a field list is a schema error.

`_language-conformance/tlv-case-field-list.yaml` and `object-member-field-list.yaml`
hold all five implementations to the decoded values. What a fixture cannot state is
here: the schema errors, their text, and that the validator no longer crashes.

Before this, Python's tlv case body and object members were each their own loop of plain
reads, so a number literal there was "unknown type: number", a `match` or `byte_group`
member "declares no type", and a `sentinel` in a tlv case was ignored. Go, Java and C#
already decoded all of it.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter, schema_iterator_errors  # noqa: E402
from validate_schema import validate_schema  # noqa: E402

WANT = "a case body is a field list; write [] for a case that reads nothing (PS-441)"

STRING_BODIES = [
    (
        "name: p\nfields:\n- match:\n    length: 1\n    cases:\n"
        "      5: skip\n      6: [{name: a, type: u8}]\n",
        "fields[0].match.cases[5]",
    ),
    (
        "name: p\nfields:\n- tlv:\n    tag_size: 1\n    cases:\n      1: skip\n",
        "fields[0].tlv.cases[1]",
    ),
    (
        "name: p\nfields:\n- name: g\n  type: object\n  fields:\n  - match:\n"
        "      length: 1\n      cases:\n        5: skip\n",
        "fields[0].fields[0].match.cases[5]",
    ),
]


@pytest.mark.parametrize("src,where", STRING_BODIES)
def test_the_validator_rejects_a_string_case_body(src, where):
    result = validate_schema(yaml.safe_load(src))
    assert not result.schema_valid
    assert f"{where}: {WANT}" in result.schema_errors


@pytest.mark.parametrize("src,where", STRING_BODIES)
def test_the_interpreter_rejects_it_at_load(src, where):
    result = SchemaInterpreter(yaml.safe_load(src)).decode(bytes([5]))
    assert not result.success
    assert f"{where}: {WANT}" in result.errors


@pytest.mark.parametrize("src,where", STRING_BODIES)
def test_the_generator_rejects_it(src, where):
    with pytest.raises(ValueError, match=r"a case body is a field list"):
        TS013Generator(yaml.safe_load(src))


def test_the_iterator_walk_does_not_crash_on_a_string_body():
    # It raised AttributeError: 'str' object has no attribute 'get'.
    assert schema_iterator_errors(yaml.safe_load(STRING_BODIES[0][0])) == []


def test_an_empty_case_body_is_a_case_that_reads_nothing():
    src = (
        "name: p\nfields:\n- match:\n    length: 1\n    cases:\n"
        "      5: []\n      6: [{name: a, type: u8}]\n"
    )
    assert validate_schema(yaml.safe_load(src)).schema_valid
    result = SchemaInterpreter(yaml.safe_load(src)).decode(bytes([5]))
    assert result.success and result.data == {}


def test_an_error_inside_a_group_member_fails_the_decode():
    src = (
        "name: p\nfields:\n- name: g\n  type: object\n  fields:\n"
        "  - {name: a, type: u8}\n  - {name: b, type: u16}\n"
    )
    result = SchemaInterpreter(yaml.safe_load(src)).decode(bytes([1, 2]))
    assert not result.success
    assert result.errors and result.errors[0].startswith("Error decoding g:")


def test_a_group_member_range_is_flagged_and_warned():
    # A member's valid_range is checked, as a match case body's is; group members had
    # no range check at all before.
    src = (
        "name: p\nfields:\n- name: g\n  type: object\n  fields:\n"
        "  - {name: t, type: u8, valid_range: [0, 10]}\n"
    )
    result = SchemaInterpreter(yaml.safe_load(src)).decode(bytes([20]))
    assert result.success
    assert result.data["g"] == {"t": 20}
    assert result.data["_quality"] == {"t": "out_of_range"}
    assert result.warnings == ["t: value 20 outside valid range [0, 10]"]


def test_an_object_member_is_validated_as_a_field_list():
    # The validator never descended into an object's members, so a typeless one passed.
    src = "name: p\nfields:\n- name: g\n  type: object\n  fields:\n  - {name: a}\n"
    result = validate_schema(yaml.safe_load(src))
    assert not result.schema_valid
    assert any("declares no 'type'" in e for e in result.schema_errors)


def test_an_empty_object_is_rejected():
    src = "name: p\nfields:\n- name: g\n  type: object\n  fields: []\n"
    result = validate_schema(yaml.safe_load(src))
    assert any("PS-142" in e for e in result.schema_errors)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_generated_codec_drops_trailing_nul_padding_from_ascii():
    # Python, Go and C# report "HC"; the codec reported "HC\u0000\u0000", and Java
    # removed the interior NUL too. An interior NUL is kept everywhere now.
    src = "name: p\nfields:\n- {name: label, type: ascii, length: 5}\n"
    schema = yaml.safe_load(src)
    script = TS013Generator(schema, slim=True).generate() + (
        "\nprocess.stdout.write(JSON.stringify(decodeUplink("
        "{bytes: [72, 0, 67, 0, 0], fPort: 1}).data));"
    )
    out = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    assert json.loads(out.stdout) == {"label": "H\u0000C"}
    assert SchemaInterpreter(schema).decode(bytes([72, 0, 67, 0, 0])).data == {
        "label": "H\u0000C"
    }
