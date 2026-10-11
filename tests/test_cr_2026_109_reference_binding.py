"""CR-2026-109: what a reference to a looked-up field binds (PS-523, PS-524).

A `$` reference and a `var` bind the number before the lookup where the lookup reports
a text or boolean label, or has no entry for the value; where it reports a number, they
bind that number, so a table of sizes feeds arithmetic. A `name_from` template is not a
reference: it writes the value reported (PS-532). A miss omits the field from the output
and leaves the reference bound to the number.

Every implementation bound the label before this, measured on 27 probes at the top
level, in a tlv case and in a match body: `$code` in a `compute` failed on "report_rsp",
a case keyed 129 never matched it, and a miss left the name unbound. The fixture
lookup-reference-binding.yaml holds all five to one vector per case. This file holds
what a vector cannot: the errors, the unbound paths, the encoder, and the validator.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import case_key_type_warnings  # noqa: E402

CMD = {"name": "Cmd", "type": "u8", "lookup": {1: "Report", 129: "ReportRsp"}}


def schema(*fields):
    return {"name": "probe", "endian": "big", "fields": list(fields)}


def decode(doc, payload):
    result = SchemaInterpreter(doc).decode(bytes.fromhex(payload))
    return result.data, result.errors


def run_js(doc, call):
    code = TS013Generator(doc).generate()
    script = code + "\nprocess.stdout.write(JSON.stringify(%s));" % call
    out = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def decode_js(doc, payload):
    out = run_js(
        doc, "decodeUplink({bytes: %s, fPort: 1})" % list(bytes.fromhex(payload))
    )
    return out.get("data", {}), out.get("errors", [])


BOTH = pytest.mark.parametrize("run", [decode, decode_js], ids=["python", "ts013"])


# The spec prototype's own tests (TestCR2026109ReferenceBinding) -------------------


@BOTH
def test_a_match_selects_on_the_number_under_a_label(run):
    doc = schema(
        CMD,
        {
            "match": {
                "field": "$Cmd",
                "cases": {
                    129: [{"name": "ok", "type": "u8"}],
                    1: [{"name": "rep", "type": "u8"}],
                },
            }
        },
    )
    assert run(doc, "8107") == ({"Cmd": "ReportRsp", "ok": 7}, [])


@BOTH
def test_an_unmapped_value_still_selects(run):
    doc = schema(
        CMD,
        {"match": {"field": "$Cmd", "cases": {2: [{"name": "other", "type": "u8"}]}}},
    )
    assert run(doc, "0207") == ({"other": 7}, [])


@BOTH
def test_a_var_binds_the_same_way(run):
    doc = schema(dict(CMD, var="c"), {"name": "n", "type": "number", "ref": "$c"})
    assert run(doc, "81") == ({"Cmd": "ReportRsp", "n": 129}, [])


@BOTH
def test_a_numeric_table_feeds_arithmetic(run):
    doc = schema(
        {"name": "size", "type": "u8", "lookup": [0, 125, 250, 500]},
        {
            "name": "twice",
            "type": "number",
            "compute": {"op": "mul", "a": "$size", "b": 2},
        },
    )
    assert run(doc, "01") == ({"size": 125, "twice": 250}, [])


@BOTH
def test_a_template_still_reads_the_label(run):
    doc = schema(CMD, {"name": "x", "name_from": "r_${Cmd}", "type": "u8"})
    assert run(doc, "8105") == ({"Cmd": "ReportRsp", "r_ReportRsp": 5}, [])


@BOTH
def test_a_lookup_miss_omits_the_field_but_binds_its_number(run):
    doc = schema(
        {"name": "a", "type": "u8", "lookup": {1: "one"}},
        {"name": "c", "type": "number", "compute": {"op": "mul", "a": "$a", "b": 2}},
    )
    assert run(doc, "05") == ({"c": 10}, [])


# What a vector cannot express ------------------------------------------------------


@BOTH
def test_a_template_over_a_miss_is_an_error(run):
    doc = schema(CMD, {"name": "x", "name_from": "r_${Cmd}", "type": "u8"})
    _, errors = run(doc, "0505")
    assert errors and "reported no value (PS-266, PS-532)" in errors[0]


@BOTH
def test_a_boolean_label_binds_its_number(run):
    doc = schema(
        {"name": "door", "type": "u8", "lookup": {0: False, 1: True}},
        {"name": "n", "type": "number", "ref": "$door"},
    )
    data, errors = run(doc, "01")
    assert (data, errors) == ({"door": True, "n": 1}, [])
    # Python's True == 1 would have passed that; the type is the assertion.
    assert type(data["n"]) is int


@BOTH
def test_a_numeric_default_binds_itself(run):
    doc = schema(
        {"name": "scale", "type": "u8", "lookup": {1: 10, "default": 0}},
        {"name": "n", "type": "number", "ref": "$scale"},
    )
    assert run(doc, "07") == ({"scale": 0, "n": 0}, [])


@BOTH
def test_a_sentinel_leaves_the_reference_unbound(run):
    doc = schema(
        {
            "name": "code",
            "type": "u8",
            "sentinel": [255],
            "lookup": {1: "one", 255: "none"},
        },
        {
            "match": {
                "field": "$code",
                "cases": {1: [{"name": "x", "type": "u8"}]},
                "default": [{"name": "y", "type": "u8"}],
            }
        },
    )
    data, errors = run(doc, "ff07")
    assert "code" not in data and errors


@BOTH
def test_the_number_is_taken_after_the_arithmetic(run):
    # PS-443: the lookup is the last step, so the number is the one it was keyed by.
    doc = schema(
        {
            "name": "code",
            "type": "u8",
            "add": -100,
            "lookup": {1: "first", 2: "second"},
        },
        {"name": "n", "type": "number", "ref": "$code"},
    )
    assert run(doc, "66") == ({"code": "second", "n": 2}, [])


def test_a_byte_group_member_binds_its_number():
    doc = schema(
        {
            "byte_group": {
                "size": 1,
                "fields": [
                    {
                        "name": "mode",
                        "type": "u8[4:7]",
                        "lookup": {1: "eco", 2: "boost"},
                    },
                    {"name": "lvl", "type": "u8[0:3]"},
                ],
            }
        },
        {"name": "n", "type": "number", "ref": "$mode"},
    )
    assert decode(doc, "23") == ({"mode": "boost", "lvl": 3, "n": 2}, [])


def test_an_internal_field_binds_its_number():
    doc = schema(
        {"name": "_kind", "type": "u8", "lookup": {1: "a", 2: "b"}},
        {"match": {"field": "$_kind", "cases": {2: [{"name": "x", "type": "u8"}]}}},
    )
    assert decode(doc, "0207") == ({"x": 7}, [])


def test_a_computed_field_with_a_lookup_binds_its_number():
    doc = schema(
        {"name": "raw", "type": "u8"},
        {
            "name": "kind",
            "type": "number",
            "ref": "$raw",
            "lookup": {1: "one", 2: "two"},
        },
        {
            "name": "n",
            "type": "number",
            "compute": {"op": "add", "a": "$kind", "b": 10},
        },
    )
    assert decode(doc, "02") == ({"raw": 2, "kind": "two", "n": 12}, [])


def test_a_repeat_element_does_not_leak_its_label():
    # PS-368: an element's names are restored after it, its reported labels too, so a
    # template after the array writes the outer field's label.
    doc = schema(
        dict(CMD),
        {
            "name": "items",
            "type": "repeat",
            "count": 1,
            "fields": [
                {"name": "Cmd", "type": "u8", "lookup": {1: "Inner", 2: "Other"}}
            ],
        },
        {"name": "x", "name_from": "r_${Cmd}", "type": "u8"},
    )
    data, errors = decode(doc, "810105")
    assert errors == [] and data["r_ReportRsp"] == 5


# Encode -----------------------------------------------------------------------------


def test_encode_selects_the_case_from_a_label():
    doc = schema(
        CMD,
        {
            "match": {
                "field": "$Cmd",
                "cases": {
                    129: [{"name": "ok", "type": "u8"}],
                    1: [{"name": "rep", "type": "u8"}],
                },
            }
        },
    )
    result = SchemaInterpreter(doc).encode({"Cmd": "ReportRsp", "ok": 7})
    assert result.payload.hex() == "8107"


def test_encode_uses_a_numeric_label_as_it_is():
    # The binding is the label 125, not the index 1 it is reversed to for writing.
    doc = schema(
        dict({"name": "size", "type": "u8", "lookup": [0, 125, 250]}, var="s"),
        {
            "match": {
                "field": "$s",
                "cases": {
                    125: [{"name": "big", "type": "u8"}],
                    1: [{"name": "one", "type": "u8"}],
                },
            }
        },
    )
    result = SchemaInterpreter(doc).encode({"size": 125, "big": 7})
    assert result.payload.hex() == "0107"


def test_encode_finds_a_reference_in_its_own_port():
    doc = {
        "name": "probe",
        "ports": {
            1: {"fields": [{"name": "code", "type": "u8", "lookup": {1: "a", 2: "b"}}]},
            2: {
                "fields": [
                    {"name": "code", "type": "u8", "lookup": {1: 10, 2: 20}},
                    {
                        "match": {
                            "field": "$code",
                            "cases": {
                                20: [{"name": "x", "type": "u8"}],
                                2: [{"name": "y", "type": "u8"}],
                            },
                        }
                    },
                ]
            },
        },
    }
    result = SchemaInterpreter(doc).encode({"code": 20, "x": 7}, fPort=2)
    assert result.payload.hex() == "0207"


# PS-524: the validator --------------------------------------------------------------


def test_a_text_case_key_on_a_looked_up_field_warns():
    doc = schema(
        CMD,
        {
            "match": {
                "field": "$Cmd",
                "cases": {
                    "ReportRsp": [{"name": "ok", "type": "u8"}],
                    1: [{"name": "rep", "type": "u8"}],
                },
            }
        },
    )
    warnings = case_key_type_warnings(doc)
    assert (
        len(warnings) == 1 and "'ReportRsp'" in warnings[0] and "PS-524" in warnings[0]
    )
    # ...and it matches nothing.
    assert decode(doc, "8107")[1]


@pytest.mark.parametrize("key", [129, "0x81", "128..130", "[129, 130]", "default"])
def test_numeric_case_keys_do_not_warn(key):
    doc = schema(
        CMD,
        {"match": {"field": "$Cmd", "cases": {key: [{"name": "ok", "type": "u8"}]}}},
    )
    assert case_key_type_warnings(doc) == []


def test_a_text_key_on_a_text_field_does_not_warn():
    doc = schema(
        {"name": "tag", "type": "ascii", "length": 2},
        {"match": {"field": "$tag", "cases": {"OK": [{"name": "x", "type": "u8"}]}}},
    )
    assert case_key_type_warnings(doc) == []


# CR-2026-132: an enum binds its code; a tag component binds as any field ---------

ENUM = {"name": "mode", "type": "enum", "base": "u8", "values": {1: "on", 2: "off"}}


@BOTH
def test_an_enum_binds_its_code_and_a_template_reads_its_name(run):
    # The spec prototype's TestCR2026132EnumBindsItsCode.
    doc = schema(
        ENUM,
        {"name": "c", "type": "number", "ref": "$mode"},
        {"match": {"field": "$mode", "cases": {2: [{"name": "x", "type": "u8"}]}}},
        {"name": "k", "type": "u8", "name_from": "m_${mode}"},
    )
    assert run(doc, "020507") == ({"mode": "off", "c": 2, "x": 5, "m_off": 7}, [])


@BOTH
def test_an_enum_default_binds_the_code(run):
    doc = schema(
        dict(ENUM, default="spare", var="m"),
        {"name": "c", "type": "number", "ref": "$m"},
    )
    assert run(doc, "09") == ({"mode": "spare", "c": 9}, [])


def test_encode_selects_the_case_from_an_enum_name():
    doc = schema(
        ENUM,
        {
            "match": {
                "field": "$mode",
                "cases": {
                    2: [{"name": "x", "type": "u8"}],
                    1: [{"name": "y", "type": "u8"}],
                },
            }
        },
    )
    result = SchemaInterpreter(doc).encode({"mode": "off", "x": 5})
    assert result.payload.hex() == "0205"


TAGGED = {
    "tlv": {
        "tag_fields": [
            {
                "name": "side",
                "type": "u8",
                "var": "s",
                "lookup": {1: "left", 2: "right"},
            },
            {"name": "kind", "type": "u8"},
        ],
        "tag_key": ["side", "kind"],
        "cases": {
            "[*, 0x10]": [
                {"name": "reading", "type": "u8", "name_from": "reading_${s}"},
                {"name": "n", "type": "number", "ref": "$side"},
            ]
        },
    }
}


@BOTH
def test_a_tag_component_binds_the_number_under_its_label(run):
    assert run(schema(TAGGED), "021007") == ({"reading_right": 7, "n": 2}, [])


@BOTH
def test_a_numeric_tag_component_label_binds_itself(run):
    tlv = dict(
        TAGGED["tlv"],
        tag_fields=[
            {"name": "side", "type": "u8", "var": "s", "lookup": [0, 125, 250]},
            {"name": "kind", "type": "u8"},
        ],
    )
    assert run(schema({"tlv": tlv}), "011007") == ({"reading_125": 7, "n": 125}, [])


# PS-068 as CR-2026-132 amends it: an unmatched code with no default is omitted ----


@BOTH
@pytest.mark.parametrize(
    "values", [{1: "on", 2: "off"}, ["idle", "on", "off"]], ids=["mapping", "sequence"]
)
def test_an_unnamed_enum_code_is_omitted_but_bound(run, values):
    # Python reported "unknown(5)", the others the code 5: a string or a number by payload.
    doc = schema(
        {"name": "mode", "type": "enum", "base": "u8", "values": values},
        {"name": "c", "type": "number", "ref": "$mode"},
    )
    assert run(doc, "05") == ({"c": 5}, [])


@BOTH
def test_a_template_over_an_unnamed_enum_code_is_an_error(run):
    doc = schema(ENUM, {"name": "k", "type": "u8", "name_from": "m_${mode}"})
    _, errors = run(doc, "0507")
    assert errors and "reported no value (PS-266, PS-532)" in errors[0]
