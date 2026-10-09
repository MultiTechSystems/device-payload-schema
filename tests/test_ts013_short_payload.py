"""A payload shorter than its fields is an error in the generated TS013 codec too.

The generated readers substituted 0 for every byte past the end, so a truncated payload
decoded to plausible values with `errors: []`: the MClimate flood sensor's schema
reported `battery: 0` for the one-byte payload `42`, where every interpreter fails the
decode. Fixed-length `ascii`, `hex` and `bytes` stopped at the end and reported the
bytes that were there.

Each case is held to the reference interpreter: whichever way it decides, the codec
must decide the same way.
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

FLOOD = {
    "name": "flood",
    "fields": [
        {
            "byte_group": [
                {
                    "name": "reason",
                    "type": "u8[5:7]",
                    "lookup": [
                        "keepalive",
                        "testButtonPressed",
                        "floodDetected",
                        "fraudDetected",
                    ],
                },
                {"name": "boxTamper", "type": "u8[3:3]"},
                {"name": "flood", "type": "u8[1:1]"},
            ]
        },
        {"name": "battery", "type": "u8", "mult": 0.016},
        {"name": "temperature", "type": "u8", "optional": True},
    ],
}

READ_TYPES = [
    "u8",
    "u16",
    "s16",
    "u24",
    "s32",
    "u64",
    "s64",
    "f16",
    "f32",
    "f64",
    "u32le16",
    "f32le16",
    "uflt16",
    "u16[0:3]",
]
LENGTH_TYPES = ["ascii", "hex", "bytes", "base64"]


def decode_js(schema, payload):
    code = TS013Generator(schema).generate()
    script = (
        code
        + "\nprocess.stdout.write(JSON.stringify(decodeUplink("
        + json.dumps({"bytes": list(payload), "fPort": 1})
        + ")));"
    )
    return json.loads(
        subprocess.run(
            ["node"], input=script, capture_output=True, text=True, check=True
        ).stdout
    )


def one_field(field):
    return {
        "name": "t",
        "fields": [{"name": "a", "type": "u8"}, dict({"name": "v"}, **field)],
    }


def assert_same_verdict(schema, payload):
    raw = bytes.fromhex(payload)
    reference = SchemaInterpreter(schema).decode(raw)
    js = decode_js(schema, raw)
    if reference.success:
        assert not js["errors"], js
    else:
        assert js["errors"] and js["data"] == {}, (reference.errors, js)


def test_flood_sensor_one_byte_is_an_error():
    js = decode_js(FLOOD, b"\x42")
    assert js["data"] == {} and js["errors"] == [
        "Buffer too short: need 1 bytes at pos 1"
    ]


@pytest.mark.parametrize(
    "payload,expected",
    [
        (
            "42BE",
            {"reason": "floodDetected", "boxTamper": 0, "flood": 1, "battery": 3.04},
        ),
        (
            "4ABE17",
            {
                "reason": "floodDetected",
                "boxTamper": 1,
                "flood": 1,
                "battery": 3.04,
                "temperature": 23,
            },
        ),
    ],
)
def test_flood_sensor_whole_frames_still_decode(payload, expected):
    js = decode_js(FLOOD, bytes.fromhex(payload))
    assert js["errors"] == [] and js["data"] == expected, js


@pytest.mark.parametrize("ftype", READ_TYPES)
@pytest.mark.parametrize("payload", ["01", "0102", "010203"])
def test_a_short_read_matches_the_reference(ftype, payload):
    assert_same_verdict(one_field({"type": ftype}), payload)


@pytest.mark.parametrize("ftype", LENGTH_TYPES)
@pytest.mark.parametrize("payload", ["01", "0102", "010203", "0102030405"])
def test_a_short_declared_length_matches_the_reference(ftype, payload):
    assert_same_verdict(one_field({"type": ftype, "length": 4}), payload)
