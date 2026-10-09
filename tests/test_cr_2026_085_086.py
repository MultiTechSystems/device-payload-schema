"""CR-2026-085 (bytes after the last field, a cut-off tlv tag) and CR-2026-086 (the
order of a field's arithmetic, amended: valid_range before the lookup; encoding inverts
it in reverse).

The fixtures `_language-conformance/leftover-bytes.yaml`, `range-before-lookup.yaml`
and `match-default-skip.yaml` hold every implementation to the decodes. What a fixture
cannot state - an error, a warning not reported twice, an encoder's output - is here,
on both Python paths: the interpreter and the generated TS013 codec.
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


def run_js(schema, call):
    code = TS013Generator(schema).generate()
    out = subprocess.run(
        ["node"],
        input=code + "\nprocess.stdout.write(JSON.stringify(%s));" % call,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def decode_js(schema, payload):
    return run_js(schema, "decodeUplink({bytes: %s, fPort: 1})" % list(payload))


ONE = {"name": "probe", "fields": [{"name": "a", "type": "u8"}]}


def leftover(warnings):
    return [w for w in warnings if "PS-472" in w]


@pytest.mark.parametrize("payload,warned", [("2a", []), ("2a0102", ["offset 1"])])
def test_bytes_after_the_last_field_are_reported(payload, warned):
    # PS-472 on the interpreter, PS-473 in the generated codec's warnings[].
    raw = bytes.fromhex(payload)
    result = SchemaInterpreter(ONE).decode(raw)
    js = decode_js(ONE, raw)
    for warnings in (result.warnings, js["warnings"]):
        found = leftover(warnings)
        assert len(found) == len(warned), warnings
        for want, got in zip(warned, found):
            assert want in got and "2 byte" in got
    assert result.success and result.data == {"a": 42} and js["data"] == {"a": 42}


def test_a_ps_302_warning_is_the_leftover_warning():
    # The tag is consumed by bytes_consumed, but it is not decoded, so PS-302's warning
    # already says what was left. No second warning (PS-472).
    schema = {
        "name": "probe",
        "fields": [
            {
                "tlv": {
                    "tag_size": 1,
                    "cases": {1: [{"name": "a", "type": "u8"}]},
                }
            }
        ],
    }
    raw = bytes.fromhex("012a09ff")
    for warnings in (
        SchemaInterpreter(schema).decode(raw).warnings,
        decode_js(schema, raw)["warnings"],
    ):
        assert len(warnings) == 1 and "left undecoded" in warnings[0], warnings
        assert not leftover(warnings)


@pytest.mark.parametrize(
    "tlv,payload",
    [
        ({"tag_size": 2, "cases": {1: [{"name": "a", "type": "u8"}]}}, "00012a00"),
        (
            {
                "tag_fields": [
                    {"name": "ch", "type": "u8"},
                    {"name": "ty", "type": "u8"},
                ],
                "tag_key": ["ch", "ty"],
                "cases": {"[1, 2]": [{"name": "a", "type": "u8"}]},
            },
            "01022a01",
        ),
    ],
    ids=["tag_size", "tag_fields"],
)
def test_a_tag_cut_off_at_the_end_is_an_error(tlv, payload):
    # PS-477: the plain form stopped the loop silently in both paths.
    schema = {"name": "probe", "fields": [{"tlv": tlv}]}
    raw = bytes.fromhex(payload)
    result = SchemaInterpreter(schema).decode(raw)
    assert not result.success and any("PS-477" in e for e in result.errors)
    js = decode_js(schema, raw)
    assert js["data"] == {} and any("PS-477" in e for e in js["errors"]), js


def test_an_encoder_writes_nothing_after_the_last_field():
    # PS-474: the leftover bytes were never data, so they are not re-encoded.
    raw = bytes.fromhex("2a0102")
    interpreter = SchemaInterpreter(ONE)
    assert interpreter.encode(interpreter.decode(raw).data).payload == b"\x2a"
    js = decode_js(ONE, raw)
    assert run_js(ONE, "encodeDownlink({data: %s})" % json.dumps(js["data"]))[
        "bytes"
    ] == [0x2A]


GUARDED = {
    "name": "probe",
    "fields": [
        {"name": "x", "type": "u8"},
        {
            "name": "v",
            "type": "number",
            "ref": "$x",
            "guard": {"when": [{"field": "$x", "gt": 0}], "else": 99},
            "valid_range": [0, 10],
            "out_of_range": "omit",
        },
    ],
}


def test_a_guard_else_is_not_compared_with_the_range():
    # PS-443 as amended: a guard that reports its `else` ends the sequence, so 99 is
    # reported though the range stops at 10.
    raw = b"\x00"
    assert SchemaInterpreter(GUARDED).decode(raw).data["v"] == 99
    assert decode_js(GUARDED, raw)["data"]["v"] == 99


def test_a_value_the_guard_lets_through_is_compared():
    raw = b"\x20"
    result = SchemaInterpreter(GUARDED).decode(raw)
    assert "v" not in result.data and result.quality["v"] == "out_of_range"
    assert "v" not in decode_js(GUARDED, raw)["data"]
