"""CR-2026-110: `on_repeat` combines a name reported by more than one tlv entry
(PS-157, PS-525 to PS-527).

The cases mirror the spec prototype's `TestCR2026110OnRepeat`, and the messages are its
wording. The decodes that succeed are fixtures in `_language-conformance/tlv-repeat-*`,
checked in every implementation; this file holds what a fixture cannot - the schema
rejections, the twice-reported member and the encoder's output where it is lossy.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402
from generate_ts013_codec import TS013Generator  # noqa: E402


def schema(tlv):
    return {"name": "t", "version": 1, "fields": [{"tlv": tlv}]}


LORAMAC = {
    "tag_size": 2,
    "on_repeat": "merge",
    "cases": {
        0x0310: [
            {
                "name": "loramac",
                "type": "object",
                "fields": [{"name": "adr", "type": "u8"}],
            }
        ],
        0x0311: [
            {
                "name": "loramac",
                "type": "object",
                "fields": [{"name": "dr", "type": "u8"}],
            }
        ],
        0x0001: [{"name": "battery", "type": "u8"}],
    },
}
EVENT = {
    "cases": {
        0x3A: [{"name": "event", "type": "u8"}],
        0x40: [{"name": "event", "type": "u8"}],
    }
}


def decode(tlv, hex_payload):
    return SchemaInterpreter(schema(tlv)).decode(bytes.fromhex(hex_payload))


def encode(tlv, data):
    return SchemaInterpreter(schema(tlv)).encode(data)


def test_merge_combines_objects_and_the_default_spares_other_fields():
    out = decode(LORAMAC, "031001031105000102000103")
    assert out.errors == []
    assert out.data == {"loramac": {"adr": 1, "dr": 5}, "battery": [2, 3]}


def test_a_member_reported_twice_is_an_error():
    out = decode(LORAMAC, "031001031002")
    message = "tlv: the merged object 'loramac' reports its member 'adr' twice (PS-525)"
    assert any(message in e for e in out.errors), out.errors
    assert out.data == {}


def test_last_keeps_the_last_value():
    assert decode(dict(EVENT, on_repeat="last"), "3a014002").data == {"event": 2}


def test_collect_is_the_default_across_different_tags():
    assert decode(EVENT, "3a014002").data == {"event": [1, 2]}


def test_an_object_collects_with_no_on_repeat():
    """Merge is never a default of its own: with no `on_repeat` an object collects."""
    tlv = {k: v for k, v in LORAMAC.items() if k != "on_repeat"}
    assert decode(tlv, "031001031105031002").data == {
        "loramac": [{"adr": 1}, {"dr": 5}, {"adr": 2}]
    }


def test_a_collected_list_value_is_an_element_not_spliced():
    """A value that is itself a list - a repeat - stays one element of the array. It
    used to be appended into, so the second report landed among the first's elements."""
    tlv = {
        "cases": {
            1: [
                {
                    "name": "vals",
                    "type": "repeat",
                    "count": 2,
                    "fields": [{"name": "v", "type": "u8"}],
                }
            ]
        }
    }
    assert decode(tlv, "010102010304").data == {
        "vals": [[{"v": 1}, {"v": 2}], [{"v": 3}, {"v": 4}]]
    }


def test_merge_encodes_one_entry_per_case():
    result = encode(LORAMAC, {"loramac": {"adr": 1, "dr": 5}})
    assert result.errors == []
    assert bytes(result.payload) == bytes.fromhex("031001031105")


def test_merge_encodes_in_case_declaration_order():
    """PS-527: the order the members arrive in does not decide the entries' order."""
    result = encode(LORAMAC, {"loramac": {"dr": 5, "adr": 1}})
    assert bytes(result.payload) == bytes.fromhex("031001031105")


def test_last_encodes_once_under_the_first_case():
    result = encode(dict(EVENT, on_repeat="last"), {"event": 2})
    assert result.errors == []
    assert bytes(result.payload) == bytes.fromhex("3a02")


def test_a_last_member_round_trips_once_per_case():
    tlv = {
        "on_repeat": "merge",
        "cases": {
            1: [
                {
                    "name": "s",
                    "type": "object",
                    "fields": [
                        {"name": "mode", "type": "u8"},
                        {"name": "seq", "type": "u8", "on_repeat": "last"},
                    ],
                }
            ],
            2: [
                {
                    "name": "s",
                    "type": "object",
                    "fields": [
                        {"name": "level", "type": "u8"},
                        {"name": "seq", "type": "u8", "on_repeat": "last"},
                    ],
                }
            ],
        },
    }
    out = decode(tlv, "010702020902")
    assert out.data == {"s": {"mode": 7, "seq": 2, "level": 9}}
    assert bytes(encode(tlv, out.data).payload) == bytes.fromhex("010702020902")


MERGE_ON_SCALAR = {"cases": {1: [{"name": "e", "type": "u8", "on_repeat": "merge"}]}}
DISAGREE = {
    "cases": {
        1: [{"name": "e", "type": "u8", "on_repeat": "last"}],
        2: [{"name": "e", "type": "u8"}],
    }
}
BAD_VALUE = {"on_repeat": "first", "cases": {1: [{"name": "e", "type": "u8"}]}}
BAD_FIELD_VALUE = {"cases": {1: [{"name": "e", "type": "u8", "on_repeat": "keep"}]}}

REJECTIONS = [
    (
        MERGE_ON_SCALAR,
        "Field 'e': only a `type: object` field may declare `on_repeat: merge` (PS-526)",
    ),
    (
        DISAGREE,
        "tlv: the fields reporting 'e' declare different `on_repeat`, last and collect (PS-526)",
    ),
    (BAD_VALUE, "tlv `on_repeat` is one of collect, merge, last, not 'first' (PS-525)"),
    (
        BAD_FIELD_VALUE,
        "Field 'e': `on_repeat` is one of collect, merge, last, not 'keep' (PS-525)",
    ),
]


@pytest.mark.parametrize("tlv,message", REJECTIONS)
def test_rejected_by_the_interpreter(tlv, message):
    assert message in decode(tlv, "0101").errors
    assert message in encode(tlv, {"e": 1}).errors


@pytest.mark.parametrize("tlv,message", REJECTIONS)
def test_rejected_by_the_validator(tlv, message):
    assert message in validate_schema_structure(schema(tlv))


@pytest.mark.parametrize("tlv,message", REJECTIONS)
def test_rejected_by_the_generator(tlv, message):
    with pytest.raises(ValueError, match="PS-52[56]"):
        TS013Generator(schema(tlv))


def test_merge_on_a_tlv_spares_a_scalar():
    """A tlv's `merge` reaches its object fields only, so a scalar under it is valid."""
    assert validate_schema_structure(schema(LORAMAC)) == []


def test_rejected_inside_a_nested_construct():
    """The check walks the whole schema: a tlv inside a port's fields is held to it."""
    s = {"name": "t", "version": 1, "ports": {"1": {"fields": [{"tlv": DISAGREE}]}}}
    errors = SchemaInterpreter(s).decode(bytes.fromhex("0101"), fPort=1).errors
    assert any("PS-526" in e for e in errors)


COLLECTED = "was collected into an array (PS-157), and writing one entry per element"


@pytest.mark.parametrize(
    "tlv,data",
    [
        (EVENT, {"event": [1, 2]}),
        (
            {k: v for k, v in LORAMAC.items() if k != "on_repeat"},
            {"loramac": [{"adr": 1}, {"dr": 5}]},
        ),
    ],
)
def test_a_collected_array_is_refused_by_name_not_mis_written(tlv, data):
    """PS-502's one entry per element is not implemented. The reference failed inside
    int() for a scalar and wrote `03 10 00` for an object - the members read as absent
    and zero-filled - and the generated codec wrote `3a 02`. Both now refuse it."""
    result = encode(tlv, data)
    assert any(COLLECTED in e for e in result.errors), result.errors
    code = TS013Generator(schema(tlv)).generate()
    script = code + "\nprocess.stdout.write(JSON.stringify(encodeDownlink(%s)));" % (
        json.dumps({"data": data})
    )
    out = json.loads(
        subprocess.run(
            ["node"], input=script, capture_output=True, text=True, check=True
        ).stdout
    )
    assert any(COLLECTED in e for e in out.get("errors", [])), out
