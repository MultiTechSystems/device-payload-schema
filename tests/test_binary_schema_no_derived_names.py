"""PS-555: tools/binary_schema.py derives no field name from a record's position.

Its two layouts (version 1, flat 4-byte records; version 2, with structural records)
carry no field names. Its decoder filled the gap with `field_{index}`, and its callers
fed the result to the interpreter, which then reported the decoded object under those
invented keys. PS-555 forbids deriving a name from a field's position, and from a form
that carries no names forbids reporting the decoded object at all.

The decoder now describes each record by its position in the list, with no `name`,
and marks the dict `names: False`; `require_names()` refuses it wherever a decoded object
would be reported. tools/binary_schema_v2.py carries names in its string table and
leaves a field nameless where the table has none, so it needs no change; the last test
here holds it to that.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import binary_schema  # noqa: E402
import binary_schema_v2  # noqa: E402

SCHEMA = {
    "name": "env",
    "fields": [
        {"name": "temperature", "type": "s16", "mult": 0.01},
        {"name": "humidity", "type": "u8"},
        {"name": "battery_mv", "type": "u16"},
    ],
}

MATCH_SCHEMA = {
    "name": "cmds",
    "fields": [
        {"name": "cmd", "type": "u8", "var": "cmd"},
        {
            "match": {
                "field": "$cmd",
                "cases": {1: [{"name": "interval", "type": "u16"}]},
            }
        },
    ],
}


@pytest.mark.parametrize("schema", [SCHEMA, MATCH_SCHEMA], ids=["v1", "v2"])
def test_no_field_gets_a_positional_name(schema):
    decoded = binary_schema.decode_schema(binary_schema.encode_schema(schema))
    assert decoded["names"] is False

    def walk(fields):
        for f in fields:
            assert not str(f.get("name", "")).startswith("field_"), f
            assert "name" not in f, f
            for case in (f.get("match") or {}).get("cases", {}).values():
                walk(case)

    walk(decoded["fields"])


def test_the_records_are_still_described_in_order():
    decoded = binary_schema.decode_schema(binary_schema.encode_schema(SCHEMA))
    assert [f["type"] for f in decoded["fields"]] == ["s16", "u8", "u16"]
    assert decoded["fields"][0]["mult"] == pytest.approx(0.01)


def test_a_decoded_object_cannot_be_reported_from_it():
    decoded = binary_schema.decode_schema(binary_schema.encode_schema(SCHEMA))
    with pytest.raises(ValueError, match="PS-555"):
        binary_schema.require_names(decoded)
    assert binary_schema.require_names(SCHEMA) is SCHEMA


def test_the_v2_module_reads_names_from_its_string_table():
    decoded = binary_schema_v2.decode_schema(binary_schema_v2.encode_schema(SCHEMA))
    assert [f["name"] for f in decoded["fields"]] == [
        "temperature",
        "humidity",
        "battery_mv",
    ]


def test_no_positional_name_is_left_in_either_source():
    root = Path(__file__).resolve().parent.parent / "tools"
    for module in ("binary_schema.py", "binary_schema_v2.py"):
        text = (root / module).read_text()
        assert "'name': f'field_" not in text, module
        assert "f'field_{index}'" not in text.replace("`field_{index}`", ""), module
