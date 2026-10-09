"""CR-2026-093 (PS-486): a tlv entry is never partly read.

Where an entry declares a length, a length cut short or a length declaring more bytes than
remain is an error naming the tlv and the entry's offset - for a known tag, and for an
unknown one that would be skipped. The bytes are not left over for PS-472.

The cases are the CR's own: before it, a cut length rewound to the tag and warned under
PS-472; a cut value on a known tag decoded what was there and reported success; and an
unknown tag behaved three ways (Python and TS013 stepped past the payload's end, Go read
the tail as a new entry, Java and C# failed with a bare underflow). An error cannot be a
corpus vector, so this holds both Python paths to it; Go, Java, C# and C have the same
cases in cr093_test.go, CR2026093Test, CR2026_093Tests and selftest_schema.c.
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


def schema(length_size=1, reserve=0):
    tlv = {
        "tag_size": 1,
        "length_size": length_size,
        "cases": {
            1: [{"name": "a", "type": "u8"}],
            2: [{"name": "b", "type": "bytes", "length": "remaining"}],
        },
    }
    if reserve:
        tlv["reserve"] = reserve
    return {"name": "probe", "fields": [{"tlv": tlv}]}


def decode_js(sch, payload):
    code = TS013Generator(sch).generate()
    call = "decodeUplink({bytes: %s, fPort: 1})" % list(payload)
    out = subprocess.run(
        ["node"],
        input=code + "\nprocess.stdout.write(JSON.stringify(%s));" % call,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


CUT = "tlv entry at offset {}: {} byte(s) remain after its tag, fewer than its {}-byte length (PS-486)"
OVER = "tlv entry at offset {}: its length declares {} byte(s), {} remain (PS-486)"


@pytest.mark.parametrize(
    "length_size,reserve,payload,message",
    [
        (1, 0, "01010702", CUT.format(3, 0, 1)),
        (1, 0, "0101070205aabb", OVER.format(3, 5, 2)),
        (1, 0, "0101070905aabb", OVER.format(3, 5, 2)),
        (2, 0, "010001070200", CUT.format(4, 1, 2)),
        (1, 1, "0101070205aabb", OVER.format(3, 5, 1)),
    ],
    ids=[
        "length byte missing",
        "value cut short",
        "unknown tag skipped past the end",
        "one of two length bytes",
        "value cut short by a reserve",
    ],
)
def test_a_cut_short_entry_is_an_error(length_size, reserve, payload, message):
    sch = schema(length_size, reserve)
    raw = bytes.fromhex(payload)
    result = SchemaInterpreter(sch).decode(raw)
    assert not result.success
    assert any(message in e for e in result.errors), result.errors
    assert not result.warnings
    js = decode_js(sch, raw)
    assert js["data"] == {} and js["errors"] == [message] and js["warnings"] == [], js


@pytest.mark.parametrize(
    "payload,data",
    [("010107", {"a": 7}), ("0101070202aabb", {"a": 7, "b": "aabb"})],
)
def test_an_entry_that_fits_decodes(payload, data):
    raw = bytes.fromhex(payload)
    result = SchemaInterpreter(schema()).decode(raw)
    assert result.success and result.data == data and not result.warnings
    js = decode_js(schema(), raw)
    assert js == {"data": data, "warnings": [], "errors": []}
