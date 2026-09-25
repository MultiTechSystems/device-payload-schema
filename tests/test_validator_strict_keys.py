"""validate_schema.py rejects duplicate mapping keys and keys nothing reads (gate 4).

PyYAML and json.load both keep the last of two equal keys silently, so a colliding
match/tlv case or lookup entry vanished without a word. And a key outside the
language vocabulary - misspelt, invented, or copied from another format - validated,
while every implementation ignored it.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import schema_vocabulary  # noqa: E402
from validate_schema import (  # noqa: E402
    DuplicateKeyError,
    load_schema_text,
    validate_schema,
)

VALIDATOR = REPO_ROOT / "tools" / "validate_schema.py"


def _yaml(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


def _errors(schema: dict):
    result = validate_schema(schema)
    return result.schema_valid, result.schema_errors, result.schema_warnings


# --------------------------------------------------------------------------------------
# Duplicate keys
# --------------------------------------------------------------------------------------

MATCH_DUP = _yaml("""
    name: dup
    fields:
      - name: kind
        type: u8
        var: kind
      - match:
          field: $kind
          cases:
            1:
              - name: a
                type: u8
            1:
              - name: b
                type: u16
    """)


def test_duplicate_match_case_key_fails_with_line_info():
    with pytest.raises(DuplicateKeyError) as info:
        load_schema_text(MATCH_DUP, "dup.yaml")
    err = info.value
    assert err.key == 1
    assert err.line == 12 and err.first_line == 9
    assert err.column == 9
    assert err.path == "fields[1].match.cases"
    assert "line 12" in str(err) and "fields[1].match.cases" in str(err)


def test_numeric_string_and_integer_case_keys_collide():
    # The interpreters compare a numeric-string case key as a number, and a JSON
    # rendering turns every integer key into a string: `1` and "1" are one case.
    text = MATCH_DUP.replace(
        "            1:\n              - name: b",
        '            "1":\n              - name: b',
    )
    with pytest.raises(DuplicateKeyError):
        load_schema_text(text, "dup.yaml")


def test_duplicate_lookup_key_fails():
    text = _yaml("""
        name: dup
        fields:
          - name: mode
            type: u8
            lookup:
              0: off
              1: on
              0x00: idle
        """)
    with pytest.raises(DuplicateKeyError) as info:
        load_schema_text(text, "dup.yaml")
    assert info.value.path == "fields[0].lookup"
    assert info.value.line == 8 and info.value.first_line == 6


def test_duplicate_schema_key_fails():
    text = "name: a\nname: b\nfields:\n  - {name: x, type: u8}\n"
    with pytest.raises(DuplicateKeyError) as info:
        load_schema_text(text, "dup.yaml")
    assert info.value.path == ""
    assert info.value.line == 2


def test_merge_keys_are_not_duplicates():
    text = _yaml("""
        base: &b {type: u8, mult: 2}
        name: merged
        fields:
          - <<: *b
            name: x
            mult: 3
        """)
    assert load_schema_text(text, "m.yaml")["fields"][0]["mult"] == 3


def test_json_duplicate_key_fails_with_line_info():
    text = (
        '{\n  "name": "dup",\n  "fields": [\n'
        '    {"name": "m", "type": "u8", "lookup": {"0": "off", "0": "on"}}\n  ]\n}\n'
    )
    with pytest.raises(DuplicateKeyError) as info:
        load_schema_text(text, "dup.json")
    assert info.value.key == "0"
    assert info.value.line == 4
    assert info.value.path == "fields[0].lookup"


def test_json_without_duplicates_loads():
    doc = {"name": "ok", "fields": [{"name": "x", "type": "u8"}]}
    assert load_schema_text(json.dumps(doc), "ok.json") == doc


def test_cli_reports_a_duplicate_as_an_invalid_schema(tmp_path):
    path = tmp_path / "dup.yaml"
    path.write_text(MATCH_DUP)
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    assert proc.returncode == 1
    # `make validate-devices` greps for this line.
    assert "Schema: INVALID" in proc.stdout
    assert "duplicate key 1 at line 12" in proc.stdout


# --------------------------------------------------------------------------------------
# Unknown keys
# --------------------------------------------------------------------------------------


def _schema(**field_extra):
    fld = {"name": "t", "type": "s16"}
    fld.update(field_extra)
    return {"name": "probe", "fields": [fld]}


def test_unknown_transform_stage_fails_with_suggestion():
    valid, errors, _ = _errors(_schema(transform=[{"sub": 40}]))
    assert not valid
    [msg] = [e for e in errors if "Unknown key" in e]
    assert "Unknown key 'sub'" in msg and "fields[0].transform[0]" in msg
    assert "`add:` with a negative value" in msg


def test_near_miss_gets_a_did_you_mean():
    valid, errors, _ = _errors(_schema(mulr=0.1))
    assert not valid
    assert "did you mean `mult`?" in errors[0]


def test_count_field_points_at_count_reference():
    schema = {
        "name": "probe",
        "fields": [
            {"name": "n", "type": "u8", "var": "n"},
            {
                "name": "r",
                "type": "repeat",
                "count_field": "n",
                "fields": [{"name": "v", "type": "u8"}],
            },
        ],
    }
    valid, errors, _ = _errors(schema)
    assert not valid
    assert any("count_field" in e and "count: $x" in e for e in errors)


def test_unknown_key_in_a_tlv_block_is_reported_with_its_path():
    schema = {
        "name": "probe",
        "fields": [
            {
                "tlv": {
                    "tag_sze": 1,
                    "cases": {1: [{"name": "a", "type": "u8"}]},
                }
            }
        ],
    }
    valid, errors, _ = _errors(schema)
    assert not valid
    assert "fields[0].tlv.tag_sze" in errors[0] and "tag_size" in errors[0]


def test_conditional_on_a_decoded_field_is_an_error():
    valid, errors, _ = _errors(_schema(conditional="flags & 1"))
    assert not valid
    assert "always decodes" in errors[0]


def test_unread_key_is_accepted_only_in_an_unreferenced_definition():
    inert = {
        "name": "probe",
        "fields": [{"name": "x", "type": "u8"}],
        "definitions": {
            "unused": {
                "cid": 3,
                "fields": [{"name": "y", "type": "u8", "conditional": "x == 0"}],
            }
        },
    }
    assert _errors(inert)[0]
    referenced = json.loads(json.dumps(inert))
    referenced["fields"].append({"$ref": "#/definitions/unused"})
    valid, errors, _ = _errors(referenced)
    assert not valid
    assert {e.split("'")[1] for e in errors} == {"cid", "conditional"}


def test_annotation_and_documentation_keys_pass():
    schema = _schema(
        description="air temperature",
        comment="prose",
        unit="°C",
        div=10,
        valid_range=[-40, 85],
        ipso={"object": 3303, "instance": 0, "resource": 5700},
        senml={"name": "temperature", "unit": "Cel"},
        semantic="air.temperature",
    )
    schema["description"] = "probe"
    schema["test_vectors"] = [
        {"name": "v", "payload": "00E7", "expected": {"t": 23.1}, "source": "generated"}
    ]
    valid, errors, _ = _errors(schema)
    assert valid, errors


def test_lookup_and_case_keys_are_data_not_vocabulary():
    schema = {
        "name": "probe",
        "fields": [
            {"name": "k", "type": "u8", "var": "k", "lookup": {0: "zero", 7: "seven"}},
            {
                "match": {
                    "field": "$k",
                    "cases": {"anything": [{"name": "a", "type": "u8"}]},
                }
            },
        ],
    }
    assert _errors(schema)[0]


def test_divergent_key_is_a_warning_not_an_error():
    valid, errors, warnings = _errors(
        {
            "name": "p",
            "fields": [{"name": "b", "type": "bytes", "length": 2, "format": "hex"}],
        }
    )
    assert valid, errors
    assert any("'format'" in w and "Python" in w for w in warnings)


def test_allow_unknown_keys_downgrades_to_warnings():
    result = validate_schema(_schema(mulr=0.1), strict_keys=False)
    assert result.schema_valid
    assert any("mulr" in w for w in result.schema_warnings)


def test_every_corpus_schema_and_example_has_only_known_keys():
    """The corpus is what makes default-reject safe: nothing in it trips the check."""
    import yaml

    paths = sorted((REPO_ROOT / "schemas" / "devices").rglob("*.yaml"))
    paths += sorted((REPO_ROOT / "examples").glob("*_resolved.yaml"))
    bad = []
    for path in paths:
        schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        for where, _, key, _ in schema_vocabulary.unknown_keys(schema):
            bad.append("%s: %s (%s)" % (path.relative_to(REPO_ROOT), where, key))
    assert not bad, "\n".join(bad)


def test_every_hint_names_a_key_the_vocabulary_rejects():
    """A hint for a key that is in fact accepted would never be shown."""
    for ctx, key in schema_vocabulary.HINTS:
        assert not schema_vocabulary.is_known(ctx, key), (ctx, key)
