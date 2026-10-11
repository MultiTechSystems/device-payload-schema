"""CR-2026-118: several records from one repeat element, and a condition on a set
(PS-541 to PS-543; PS-126 and PS-128 amended).

A `records` entry in a repeat's element, or in a match case within it, is a non-empty
list of field lists. Each yields one record, and the repeat reports the records as
consecutive elements in place of the element, with the element's other reported fields
first (PS-541). `index` counts source elements and `count_as` the records reported. An
encoder refuses a repeat whose elements can reach `records` (PS-542). A guard condition
or `present_if` may test `in` / `not_in` against a list of numbers (PS-543).

The cases mirror the spec prototype's TestCR2026118RecordsAndSets, and the messages are
the wording Go, Java, C# and the TS013 generator give identically.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_binary import schema_errors  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import load_schema_text, validate_schema_structure  # noqa: E402

MESSAGES = [
    {
        "name": "messages",
        "type": "repeat",
        "until": "end",
        "count_as": "n",
        "index": "i",
        "present_if": {"field": "$_dataId", "not_in": [1, 2, 3, 4, 5, 6]},
        "fields": [
            {"name": "_dataId", "type": "u8"},
            {"name": "pos", "type": "number", "ref": "$i"},
            {
                "match": {
                    "field": "$_dataId",
                    "cases": {
                        7: [
                            {
                                "records": [
                                    [
                                        {
                                            "name": "type",
                                            "type": "string",
                                            "value": "upload_battery",
                                        },
                                        {"name": "battery", "type": "u16"},
                                    ],
                                    [
                                        {
                                            "name": "type",
                                            "type": "string",
                                            "value": "upload_interval",
                                        },
                                        {"name": "interval", "type": "u16", "mult": 60},
                                    ],
                                ]
                            }
                        ],
                        3: [{"name": "x", "type": "u8"}],
                        0: [{"name": "hw", "type": "u8"}],
                    },
                }
            },
        ],
    },
    {"name": "count", "type": "number", "ref": "$n"},
]

COPIED = [
    {
        "name": "m",
        "type": "repeat",
        "count": 1,
        "fields": [
            {"name": "channel", "type": "u8"},
            {"name": "_spare", "type": "u8"},
            {"records": [[{"name": "a", "type": "u8"}], [{"name": "b", "type": "u8"}]]},
        ],
    }
]


def schema_of(fields):
    return {"name": "t", "version": 1, "endian": "big", "fields": fields}


def decode(fields, payload):
    result = SchemaInterpreter(schema_of(fields)).decode(bytes.fromhex(payload))
    assert result.success, result.errors
    return result.data


def run_codec(schema, payload, data=None):
    code = TS013Generator(schema).generate()
    script = (
        code
        + "\nvar d = decodeUplink("
        + json.dumps({"bytes": list(payload), "fPort": 1})
        + ");\nvar e; try { e = encodeDownlink({data: "
        + (json.dumps(data) if data is not None else "d.data")
        + ", fPort: 1}); } catch (x) { e = {errors: [String(x.message)]}; }"
        "\nprocess.stdout.write(JSON.stringify({d: d, e: e}));"
    )
    done = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


class TestDecode:
    def test_records_are_consecutive_elements_and_not_in_drops(self):
        assert decode(MESSAGES, "070064003C0305000A") == {
            "messages": [
                {"pos": 0, "type": "upload_battery", "battery": 100},
                {"pos": 0, "type": "upload_interval", "interval": 3600},
                {"pos": 2, "hw": 10},
            ],
            "count": 3,
        }

    def test_the_elements_other_fields_go_first_and_internal_ones_nowhere(self):
        data = decode(COPIED, "01FF0203")
        assert data == {"m": [{"channel": 1, "a": 2}, {"channel": 1, "b": 3}]}
        assert [list(r) for r in data["m"]] == [["channel", "a"], ["channel", "b"]]

    def test_a_field_after_the_entry_is_copied_too(self):
        fields = [
            {
                "name": "m",
                "type": "repeat",
                "count": 1,
                "fields": [
                    {"records": [[{"name": "a", "type": "u8"}]]},
                    {"name": "tail", "type": "u8"},
                ],
            }
        ]
        assert decode(fields, "0209") == {"m": [{"tail": 9, "a": 2}]}

    def test_an_element_that_reaches_no_records_is_itself(self):
        assert decode(MESSAGES, "000A")["messages"] == [{"pos": 0, "hw": 10}]

    def test_a_record_name_is_bound_for_the_next_record(self):
        fields = [
            {
                "name": "m",
                "type": "repeat",
                "count": 1,
                "fields": [
                    {
                        "records": [
                            [{"name": "a", "type": "u8"}],
                            [{"name": "b", "type": "number", "ref": "$a", "mult": 2}],
                        ]
                    }
                ],
            }
        ]
        assert decode(fields, "05") == {"m": [{"a": 5}, {"b": 10}]}

    @pytest.mark.parametrize(
        "cond,kept",
        [({"in": [1, 3]}, [{"x": 1}, {"x": 3}]), ({"not_in": [1, 3]}, [{"x": 2}])],
    )
    def test_present_if_in_and_not_in(self, cond, kept):
        fields = [
            {
                "name": "v",
                "type": "repeat",
                "count": 3,
                "present_if": dict({"field": "$x"}, **cond),
                "fields": [{"name": "x", "type": "u8"}],
            }
        ]
        assert decode(fields, "010203") == {"v": kept}

    @pytest.mark.parametrize("payload,expected", [("04", 40), ("05", -1)])
    def test_a_guard_with_in(self, payload, expected):
        fields = [
            {"name": "mode", "type": "u8"},
            {
                "name": "setting",
                "type": "number",
                "ref": "$mode",
                "mult": 10,
                "guard": {"when": [{"field": "$mode", "in": [2, 4]}], "else": -1},
            },
        ]
        assert decode(fields, payload)["setting"] == expected


class TestEncode:
    def test_a_repeat_reaching_records_is_refused(self):
        encoded = SchemaInterpreter(schema_of(COPIED)).encode(
            {"m": [{"channel": 1, "a": 2}]}
        )
        assert not encoded.success
        assert (
            "Repeat 'm' has elements that yield several records, which cannot be "
            "encoded (PS-542)"
        ) in encoded.errors[0]

    def test_through_a_match_case_too(self):
        encoded = SchemaInterpreter(schema_of(MESSAGES)).encode({"messages": []})
        assert not encoded.success and "PS-542" in encoded.errors[0]

    def test_a_guard_with_in_round_trips(self):
        fields = [
            {"name": "mode", "type": "u8"},
            {
                "name": "setting",
                "type": "number",
                "ref": "$mode",
                "guard": {"when": [{"field": "$mode", "in": [2, 4]}], "else": -1},
            },
        ]
        interp = SchemaInterpreter(schema_of(fields))
        assert interp.encode(interp.decode(b"\x04").data).payload == b"\x04"


IN_ELEMENT = (
    "`records` belongs in a repeat's element, or a match case within it (PS-541)"
)
NON_EMPTY = "`records` is a non-empty list of field lists (PS-541)"
REFUSED = {
    "top level": ([{"records": [[{"name": "a", "type": "u8"}]]}], IN_ELEMENT),
    "object member in an element": (
        [
            {
                "name": "m",
                "type": "repeat",
                "count": 1,
                "fields": [
                    {
                        "name": "o",
                        "type": "object",
                        "fields": [{"records": [[{"name": "a", "type": "u8"}]]}],
                    }
                ],
            }
        ],
        IN_ELEMENT,
    ),
    "tlv case in an element": (
        [
            {
                "name": "m",
                "type": "repeat",
                "count": 1,
                "fields": [
                    {
                        "tlv": {
                            "tag_size": 1,
                            "cases": {
                                1: [{"records": [[{"name": "a", "type": "u8"}]]}]
                            },
                        }
                    }
                ],
            }
        ],
        IN_ELEMENT,
    ),
    "inside a record": (
        [
            {
                "name": "m",
                "type": "repeat",
                "count": 1,
                "fields": [
                    {"records": [[{"records": [[{"name": "a", "type": "u8"}]]}]]}
                ],
            }
        ],
        IN_ELEMENT,
    ),
    "empty": (
        [{"name": "m", "type": "repeat", "count": 1, "fields": [{"records": []}]}],
        NON_EMPTY,
    ),
    "not a list of lists": (
        [
            {
                "name": "m",
                "type": "repeat",
                "count": 1,
                "fields": [{"records": [{"name": "a", "type": "u8"}]}],
            }
        ],
        NON_EMPTY,
    ),
    "under element": (
        [
            {
                "name": "m",
                "type": "repeat",
                "count": 1,
                "element": {
                    "match": {
                        "length": 1,
                        "cases": {1: [{"records": [[{"name": "a", "type": "u8"}]]}]},
                    }
                },
            }
        ],
        "Repeat 'm': an `element` cannot reach a `records` entry (PS-541)",
    ),
    "in not a list": (
        [
            {
                "name": "m",
                "type": "repeat",
                "count": 1,
                "present_if": {"field": "$a", "in": 3},
                "fields": [{"name": "a", "type": "u8"}],
            }
        ],
        "a condition's `in` is a list of numbers (PS-543)",
    ),
    "not_in holding text": (
        [
            {"name": "a", "type": "u8"},
            {
                "name": "b",
                "type": "number",
                "ref": "$a",
                "guard": {"when": [{"field": "$a", "not_in": ["1"]}], "else": 0},
            },
        ],
        "a condition's `not_in` is a list of numbers (PS-543)",
    ),
    "two operators": (
        [
            {"name": "a", "type": "u8"},
            {
                "name": "b",
                "type": "number",
                "ref": "$a",
                "guard": {"when": [{"field": "$a", "in": [1], "gt": 0}], "else": 0},
            },
        ],
        "a condition with `in` or `not_in` has no other comparison operator "
        "(PS-126, PS-543)",
    ),
}


@pytest.mark.parametrize("label", sorted(REFUSED))
def test_a_malformed_schema_is_refused_on_every_path(label):
    fields, message = REFUSED[label]
    schema = schema_of(fields)
    assert message in SchemaInterpreter(schema).decode(b"\x01\x01").errors
    assert message in validate_schema_structure(
        load_schema_text(yaml.safe_dump(schema))
    )
    # The generator raises the first error it finds; an element that is a construct is
    # refused under PS-545 (CR-2026-121) before PS-541 is reached.
    with pytest.raises(ValueError, match="PS-54[135]"):
        TS013Generator(schema).generate()


class TestMeta:
    def test_elements_describe_every_records_fields(self):
        meta = (
            SchemaInterpreter(schema_of(MESSAGES))
            .interpret(bytes.fromhex("070064003C"))
            .data["_meta"]
        )
        assert list(meta["fields"]["messages"]["elements"]) == [
            "pos",
            "type",
            "battery",
            "interval",
            "x",
            "hw",
        ]

    def test_disagreeing_declarations_across_records_are_refused(self):
        fields = [
            {
                "name": "m",
                "type": "repeat",
                "count": 1,
                "fields": [
                    {
                        "records": [
                            [{"name": "t", "type": "u8", "unit": "Cel"}],
                            [{"name": "t", "type": "u8", "unit": "K"}],
                        ]
                    }
                ],
            }
        ]
        errors = SchemaInterpreter(schema_of(fields)).decode(b"\x01\x02").errors
        assert any("PS-491" in e for e in errors)


class TestGeneratedCodec:
    @pytest.mark.parametrize(
        "fields,payload",
        [
            (MESSAGES, "070064003C0305000A"),
            (COPIED, "01FF0203"),
        ],
    )
    def test_it_decodes_as_the_interpreter_does(self, fields, payload):
        out = run_codec(schema_of(fields), bytes.fromhex(payload))
        assert out["d"]["data"] == decode(fields, payload)

    def test_its_encoder_refuses(self):
        out = run_codec(schema_of(COPIED), bytes.fromhex("01FF0203"))
        assert any(
            "Repeat 'm' has elements that yield several records, which cannot be "
            "encoded (PS-542)" in e
            for e in out["e"]["errors"]
        )

    @pytest.mark.parametrize("payload,expected", [("04", 40), ("05", -1)])
    def test_a_guard_with_in(self, payload, expected):
        fields = [
            {"name": "mode", "type": "u8"},
            {
                "name": "setting",
                "type": "number",
                "ref": "$mode",
                "mult": 10,
                "guard": {"when": [{"field": "$mode", "in": [2, 4]}], "else": -1},
            },
        ]
        out = run_codec(schema_of(fields), bytes.fromhex(payload))
        assert out["d"]["data"]["setting"] == expected


def test_the_binary_schema_refuses_rather_than_drops():
    errors = schema_errors(schema_of(COPIED))
    assert any("`records`" in e or "repeat" in e for e in errors)
