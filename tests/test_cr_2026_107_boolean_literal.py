"""CR-2026-107: a boolean literal (PS-357, PS-358 amended; PS-519, PS-520).

`{type: boolean, value: true|false}` reports the boolean and reads no bytes, beside the
string and number literals (PS-357). `boolean` is a literal type only, so `value` is
required (PS-519), and the value is a YAML `true` or `false`: `yes`, `on`, `"true"` and
`1` are refused (PS-520). Encoders write nothing for it and do not require its key
(PS-359). The C binary schema has no literal record, so it is refused there (PS-446).

PyYAML reads YAML 1.1, where `yes` and `on` are booleans, so the schema loaders read
booleans as YAML 1.2 does (`load_schema_yaml`, the validator's loader); a `yes` loaded
that way reaches the literal check as text.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import generate_output_schema  # noqa: E402
from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_binary import schema_errors  # noqa: E402
from schema_interpreter import SchemaInterpreter, load_schema_yaml  # noqa: E402
from validate_schema import load_schema_text, validate_schema_structure  # noqa: E402

SCHEMA = """\
name: p
fields:
  - {name: a, type: u8}
  - {name: flag, type: boolean%s}
  - {name: b, type: u8}
"""

REFUSED = {
    "missing value": ("", "PS-519"),
    "yes": (", value: yes", "PS-520"),
    "on": (", value: on", "PS-520"),
    "quoted true": (', value: "true"', "PS-520"),
    "one": (", value: 1", "PS-520"),
    "with a lookup": (", value: true, lookup: {1: x}", "PS-358"),
}


@pytest.mark.parametrize("label", sorted(REFUSED))
def test_a_malformed_boolean_literal_is_refused_on_every_path(label):
    fragment, tag = REFUSED[label]
    text = SCHEMA % fragment
    schema = load_schema_yaml(text)
    assert any(tag in e for e in SchemaInterpreter(schema).decode(b"\x01\x02").errors)
    assert any(tag in e for e in SchemaInterpreter(schema).encode({"a": 1}).errors)
    assert any(tag in e for e in validate_schema_structure(load_schema_text(text)))
    with pytest.raises(ValueError, match=tag):
        TS013Generator(schema).generate()


def test_the_loaders_read_yaml_1_2_booleans():
    """A YAML 1.1 reader would have handed PS-520 a True for `yes`."""
    for spelling in ("yes", "on", "no", "off", "y", "n"):
        assert load_schema_yaml(f"v: {spelling}") == {"v": spelling}
        assert load_schema_text(f"v: {spelling}") == {"v": spelling}
    for spelling, value in (("true", True), ("false", False), ("True", True)):
        assert load_schema_yaml(f"v: {spelling}") == {"v": value}


@pytest.mark.parametrize("value", [True, False])
def test_a_boolean_literal_reads_and_writes_no_bytes(value):
    schema = load_schema_yaml(SCHEMA % (", value: " + str(value).lower()))
    result = SchemaInterpreter(schema).decode(b"\x01\x02")
    assert result.success and result.data == {"a": 1, "flag": value, "b": 2}
    assert result.bytes_consumed == 2
    encoded = SchemaInterpreter(schema).encode({"a": 1, "b": 2})
    assert encoded.success and encoded.payload == b"\x01\x02" and not encoded.warnings


CONTEXTS = {
    "top level": (
        "  - {name: hw, type: boolean, value: true}\n  - {name: v, type: u8}\n",
        "05",
        {"hw": True, "v": 5},
    ),
    "match case": (
        "  - {name: kind, type: u8}\n  - match:\n      field: $kind\n"
        "      cases:\n        1:\n          - {name: hw, type: boolean, value: true}\n"
        "          - {name: v, type: u8}\n",
        "0105",
        {"kind": 1, "hw": True, "v": 5},
    ),
    "tlv case": (
        "  - tlv:\n      tag_size: 1\n      cases:\n        1:\n"
        "          - {name: hw, type: boolean, value: true}\n"
        "          - {name: v, type: u8}\n",
        "0105",
        {"hw": True, "v": 5},
    ),
    "object member": (
        "  - name: o\n    type: object\n    fields:\n"
        "      - {name: hw, type: boolean, value: false}\n"
        "      - {name: v, type: u8}\n",
        "05",
        {"o": {"hw": False, "v": 5}},
    ),
    "repeat element": (
        "  - name: r\n    type: repeat\n    count: 2\n    fields:\n"
        "      - {name: hw, type: boolean, value: true}\n"
        "      - {name: v, type: u8}\n",
        "0506",
        {"r": [{"hw": True, "v": 5}, {"hw": True, "v": 6}]},
    ),
}


def run_codec(schema, payload):
    code = TS013Generator(schema).generate()
    script = (
        code
        + "\nvar d = decodeUplink("
        + json.dumps({"bytes": list(payload), "fPort": 1})
        + ");\nvar e = encodeDownlink({data: d.data, fPort: 1});"
        "\nprocess.stdout.write(JSON.stringify({d: d, e: e}));"
    )
    return json.loads(
        subprocess.run(
            ["node"], input=script, capture_output=True, text=True, check=True
        ).stdout
    )


@pytest.mark.parametrize("context", sorted(CONTEXTS))
def test_a_boolean_literal_in_every_field_list(context):
    body, payload_hex, expected = CONTEXTS[context]
    schema = load_schema_yaml("name: p\nfields:\n" + body)
    payload = bytes.fromhex(payload_hex)
    result = SchemaInterpreter(schema).decode(payload)
    assert result.success and result.data == expected, (result.data, result.errors)
    encoded = SchemaInterpreter(schema).encode(result.data)
    assert encoded.success and encoded.payload == payload, encoded
    js = run_codec(schema, payload)
    assert js["d"]["data"] == expected, js
    assert js["e"]["bytes"] == list(payload), js


def test_the_output_schema_declares_a_boolean():
    schema = load_schema_yaml(
        (
            REPO_ROOT / "schemas/devices/_language-conformance/boolean-literal.yaml"
        ).read_text()
    )
    props = generate_output_schema(schema)["properties"]
    assert props["hardware_error"] == {"type": "boolean", "enum": [False]}
    # Two branches report `ok`, one true and one false: both values are declared.
    assert props["ok"]["type"] == "boolean" and sorted(props["ok"]["enum"]) == [
        False,
        True,
    ]


def test_the_c_binary_schema_refuses_it():
    schema = load_schema_yaml(SCHEMA % ", value: true")
    assert any("boolean" in e and "PS-446" in e for e in schema_errors(schema))
