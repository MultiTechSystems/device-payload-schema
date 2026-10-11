"""CR-2026-114 and CR-2026-115 in the generated TS013 codec (la-payload-schema main c932961).

PS-535: a short read names the innermost field, in the reference's exact text, so a
cross-language diff compares equal strings. PS-538: a byte above 0x7F decodes to its ISO
8859-1 character with PS-073's warning. PS-537: the encoder pads, rejects a value too long
or holding a character above U+00FF, and writes U+0080..U+00FF as its own byte with a
warning. Each case is also held to the reference interpreter.
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


def schema_of(fields):
    return {"name": "probe", "version": 1, "endian": "big", "fields": fields}


def run_js(schema, call, slim=False):
    code = TS013Generator(schema, slim=slim).generate()
    out = subprocess.run(
        ["node"],
        input=code + "\nprocess.stdout.write(JSON.stringify(" + call + "));",
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def decode_js(schema, payload_hex, slim=False):
    raw = list(bytes.fromhex(payload_hex))
    return run_js(schema, "decodeUplink(%s)" % json.dumps({"bytes": raw, "fPort": 1}), slim)


def encode_js(schema, data):
    return run_js(schema, "encodeDownlink(%s)" % json.dumps({"data": data, "fPort": 1}))


def need(label, n, offset, remaining):
    return (
        f"Field {label}: Buffer underflow: need {n} bytes at offset {offset}, "
        f"but only {remaining} remaining (PS-535)"
    )


def peek(label, offset):
    return f"Field {label}: Buffer underflow at peek offset {offset} (PS-535)"


SHORT_READS = [
    ([{"name": "n", "type": "u16"}], "01", need("'n'", 2, 0, 1)),
    ([{"type": "skip", "length": 3}], "", need("an unnamed skip", 3, 0, 0)),
    ([{"name": "pad", "type": "skip", "length": 3}], "00", need("'pad'", 3, 0, 1)),
    ([{"name": "s", "type": "ascii", "length": 4}], "4142", need("'s'", 4, 0, 2)),
    ([{"name": "h", "type": "hex", "length": 4}], "41", need("'h'", 4, 0, 1)),
    ([{"name": "b", "type": "bytes", "length": 4}], "41", need("'b'", 4, 0, 1)),
    ([{"name": "f", "type": "f32"}], "0102", need("'f'", 4, 0, 2)),
    ([{"name": "w", "type": "u32le16"}], "0102", need("'w'", 4, 0, 2)),
    ([{"name": "ud", "type": "udec"}], "", need("'ud'", 1, 0, 0)),
    ([{"name": "_k", "type": "u16"}], "01", need("'_k'", 2, 0, 1)),
    (
        [{"name": "e", "type": "enum", "base": "u16", "values": {1: "one"}}],
        "00",
        need("'e'", 2, 0, 1),
    ),
    (
        [{"name": "o", "type": "object", "fields": [{"name": "x", "type": "u16"}]}],
        "01",
        need("'x'", 2, 0, 1),
    ),
    (
        [{"name": "r", "type": "repeat", "count": 2,
          "fields": [{"name": "v", "type": "u16"}]}],
        "000102",
        need("'v'", 2, 2, 1),
    ),
    (
        [{"name": "k", "type": "u8", "var": "k"},
         {"match": {"field": "$k", "cases": {1: [{"name": "m", "type": "u16"}]}}}],
        "0101",
        need("'m'", 2, 1, 1),
    ),
    (
        [{"match": {"length": 2, "name": "kind",
                    "cases": {1: [{"name": "m", "type": "u8"}]}}}],
        "00",
        need("'kind'", 2, 0, 1),
    ),
    (
        [{"match": {"length": 2, "cases": {1: [{"name": "m", "type": "u8"}]}}}],
        "00",
        need("an unnamed match", 2, 0, 1),
    ),
    (
        [{"tlv": {"tag_size": 1, "cases": {1: [{"name": "t", "type": "u16"}]}}}],
        "0101",
        need("'t'", 2, 1, 1),
    ),
    (
        [{"tlv": {"tag_size": 1, "reserve": 1,
                  "cases": {1: [{"name": "t", "type": "u16"}]}}}],
        "010102",
        need("'t'", 2, 1, 1),
    ),
    (
        [{"name": "fl", "type": "u8"},
         {"flagged": {"field": "fl", "groups": [
             {"bit": 0, "fields": [{"name": "g", "type": "u16"}]}]}}],
        "0101",
        need("'g'", 2, 1, 1),
    ),
    ([{"name": "b", "type": "u16[0:3]", "consume": 2}], "01", peek("'b'", 0)),
    ([{"name": "bo", "type": "bool", "bit": 1}], "", peek("'bo'", 0)),
    # The byte peeks fine; its `consume` asks for more than remain.
    ([{"name": "b", "type": "u8[0:3]", "consume": 2}], "01", need("'b'", 2, 0, 1)),
    ([{"name": "bo", "type": "bool", "consume": 2}], "01", need("'bo'", 2, 0, 1)),
    (
        [{"name": "lo", "type": "u8[0:3]"}, {"name": "hi", "type": "u8[4:7]", "consume": 2}],
        "01",
        need("'hi'", 2, 0, 1),
    ),
    (
        [{"byte_group": {"size": 2, "fields": [{"name": "a", "type": "u16[0:3]"},
                                               {"name": "c", "type": "u16[4:7]"}]}}],
        "01",
        peek("'a'", 0),
    ),
    (
        [{"name": "n", "type": "u8"}, {"name": "s", "type": "ascii", "length": "$n"}],
        "0541",
        need("'s'", 5, 1, 1),
    ),
]


@pytest.mark.parametrize("fields,payload,message", SHORT_READS)
def test_a_short_read_names_the_innermost_field(fields, payload, message):
    schema = schema_of(fields)
    js = decode_js(schema, payload)
    assert js["errors"] == [message], js
    assert SchemaInterpreter(schema).decode(bytes.fromhex(payload)).errors == [message]


def test_the_slim_codec_names_the_field_too():
    schema = schema_of([{"name": "n", "type": "u16"}])
    assert decode_js(schema, "01", slim=True)["errors"] == [need("'n'", 2, 0, 1)]


def test_remaining_at_the_end_is_not_an_error():
    schema = schema_of([{"name": "a", "type": "u8"},
                        {"name": "rest", "type": "hex", "length": "remaining"}])
    js = decode_js(schema, "01")
    assert js["errors"] == [] and js["data"] == {"a": 1, "rest": ""}


LABEL = schema_of([{"name": "label", "type": "ascii", "length": 6}])
HIGH = "Field 'label': a byte above 0x7F in an ascii value (PS-073, PS-538)"


@pytest.mark.parametrize("payload,text,warnings", [
    ("41E900000000", "Aé", [HIGH]),
    ("80A0FF410000", "\u0080 ÿA", [HIGH]),
    ("414200430000", "AB\u0000C", []),
    ("204120000000", " A ", []),
    ("000000000000", "", []),
])
def test_ascii_decodes_as_latin_1_and_drops_trailing_nuls_only(payload, text, warnings):
    for slim in (False, True):
        js = decode_js(LABEL, payload, slim=slim)
        assert js["data"] == {"label": text} and js["warnings"] == warnings, js


@pytest.mark.parametrize("value,payload,warnings,errors", [
    ("AB", [0x41, 0x42, 0, 0, 0, 0], [], []),
    ("Aé", [0x41, 0xE9, 0, 0, 0, 0],
     ["Field 'label': a character above U+007F in an ascii value (PS-537)"], []),
    ("ABCDEFG", [], [],
     ["Field 'label': an ascii value of 7 bytes is longer than its length of 6 (PS-537)"]),
    ("€", [], [],
     ["Field 'label': an ascii value holds a character above U+00FF, which has no byte "
      "(PS-537)"]),
])
def test_ascii_encodes_padded_or_not_at_all(value, payload, warnings, errors):
    js = encode_js(LABEL, {"label": value})
    assert (js["bytes"], js["warnings"], js["errors"]) == (payload, warnings, errors), js
    reference = SchemaInterpreter(LABEL).encode({"label": value})
    assert reference.warnings == warnings and reference.errors == errors
    if not errors:
        assert list(reference.payload) == payload


def test_a_latin_1_value_round_trips():
    js = decode_js(LABEL, "41E9FF000000")
    again = encode_js(LABEL, js["data"])
    assert again["bytes"] == list(bytes.fromhex("41E9FF000000")), again
