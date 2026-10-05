"""CR-2026-045 (definitions and references) and CR-2026-051 (literal constants).

045: a definition is a field group with `fields` (PS-345); a reference splices those
fields wherever a field list appears (PS-346, PS-347); a dangling reference, a file
reference this implementation does not support, a malformed pointer and a cycle are
rejected (PS-348, PS-349, PS-461, PS-462). The `use:` shorthand is withdrawn.

051: `string`/`number` with `value` is a literal read from no bytes (PS-357), whose value
matches its type and which carries no arithmetic (PS-358); encoders write nothing for it
and do not require its key (PS-359); `value` on a field that reads bytes is the constant
an encoder writes (PS-360); `string` with no `value` is an error, not a byte read (PS-361).
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from schema_preprocessor import SchemaPreprocessor  # noqa: E402
from validate_schema import validate_schema, validate_schema_structure  # noqa: E402

GROUP = {"fields": [{"name": "v", "type": "u8"}]}

INVALID_REFERENCES = {
    "bare list definition": ({"d": [{"name": "v", "type": "u8"}]}, "#/definitions/d", "PS-345"),
    "dangling": ({"d": GROUP}, "#/definitions/missing", "PS-348"),
    "file part": ({"d": GROUP}, "lib.yaml#/definitions/d", "PS-462"),
    "pointer form": ({"d": GROUP}, "#/d", "PS-461"),
    "cycle": ({"a": {"fields": [{"$ref": "#/definitions/b"}]},
               "b": {"fields": [{"$ref": "#/definitions/a"}]}}, "#/definitions/a", "PS-349"),
}


@pytest.mark.parametrize("label", sorted(INVALID_REFERENCES))
def test_an_invalid_reference_is_rejected_everywhere(label):
    definitions, ref, tag = INVALID_REFERENCES[label]
    schema = {"name": "p", "definitions": definitions, "fields": [{"$ref": ref}]}
    assert any(tag in e for e in SchemaInterpreter(schema).decode(b"\x01").errors)
    assert any(tag in e for e in validate_schema_structure(schema))
    with pytest.raises(ValueError, match=tag):
        TS013Generator(schema).generate()


def test_the_use_shorthand_is_withdrawn(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("name: p\ndefinitions:\n  d: {fields: [{name: v, type: u8}]}\n"
                    "fields:\n  - use: d\n")
    with pytest.raises(ValueError, match="withdrawn"):
        SchemaPreprocessor().process(path)


def literal(**field):
    return {"name": "p", "fields": [dict({"name": "k"}, **field), {"name": "x", "type": "u8"}]}


@pytest.mark.parametrize("field", [
    {"type": "string", "value": 3},
    {"type": "number", "value": "3"},
    {"type": "number", "value": 3, "mult": 2},
    {"type": "string", "value": "ppm", "lookup": {0: "a"}},
])
def test_a_malformed_literal_is_rejected(field):
    schema = literal(**field)
    assert any("PS-358" in e for e in SchemaInterpreter(schema).decode(b"\x01").errors)
    assert any("PS-358" in e for e in validate_schema_structure(schema))


def test_a_literal_is_written_as_nothing_and_its_key_is_not_required():
    result = SchemaInterpreter(literal(type="string", value="ppm")).encode({"x": 5})
    assert result.payload == b"\x05" and result.warnings == []


def test_a_constant_is_written_whatever_the_input_says():
    schema = {"name": "p", "fields": [{"name": "cid", "type": "u8", "value": 0x10},
                                      {"name": "x", "type": "u8"}]}
    assert SchemaInterpreter(schema).decode(b"\x22\x05").data == {"cid": 0x22, "x": 5}
    assert SchemaInterpreter(schema).encode({"cid": 0x99, "x": 5}).payload == b"\x10\x05"


def test_a_string_with_no_value_is_an_error_not_a_read():
    schema = {"name": "p", "fields": [{"name": "s", "type": "string", "length": 2}]}
    assert any("PS-361" in e for e in SchemaInterpreter(schema).decode(b"ab").errors)
    assert any("PS-361" in w for w in validate_schema(schema).schema_warnings)
    with pytest.raises(ValueError, match="PS-361"):
        TS013Generator(schema).generate()
