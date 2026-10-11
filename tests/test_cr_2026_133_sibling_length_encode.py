"""CR-2026-133 (PS-550): an encoder rejects sibling arrays of different lengths.

A repeat whose `identity`, or an element's `unit` or `ipso.instance`, names a sibling
element repeat (`$codes[i]`, PS-546) is paired with it position by position. A decoder
refuses arrays of different lengths (PS-546); an encoder given them must refuse too, naming
both, or it writes a frame its own decoder rejects. A rejection cannot be a corpus vector,
so the exact message is held here, for the reference and for the generated codec's runtime
encoder. Go, Java and C# hold the same text in their own tests.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402

CODES = {
    "name": "codes",
    "type": "repeat",
    "count": 2,
    "element": {"type": "u8", "lookup": {1: "1.8.1", 2: "2.8.1"}},
}


def values(**extra):
    return dict(
        {
            "name": "values",
            "type": "repeat",
            "count": 2,
            "index": "i",
            "identity": "$codes[i]",
            "element": {"type": "u32"},
        },
        **extra,
    )


def schema_of(fields):
    return {"name": "t", "version": 1, "endian": "big", "fields": fields}


LONGER = "Repeat 'values' is given 3 values and 'codes', which identifies them, 2 (PS-550)"
SHORTER = "Repeat 'values' is given 1 values and 'codes', which identifies them, 2 (PS-550)"


def encode_codec(schema, data):
    script = (
        TS013Generator(schema).generate()
        + "\nvar e; try { e = encodeDownlink({data: "
        + json.dumps(data)
        + ", fPort: 1}); } catch (x) { e = {errors: [String(x.message)]}; }"
        "\nprocess.stdout.write(JSON.stringify(e));"
    )
    done = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


CASES = [
    ([123, 456, 789], LONGER),
    ([123], SHORTER),
]


class TestReference:
    def test_equal_lengths_encode(self):
        out = SchemaInterpreter(schema_of([CODES, values()])).encode(
            {"codes": ["1.8.1", "2.8.1"], "values": [123, 456]}
        )
        assert out.success, out.errors
        assert out.payload.hex() == "01020000007b000001c8"

    @pytest.mark.parametrize("given, message", CASES)
    def test_different_lengths_are_rejected_naming_both(self, given, message):
        out = SchemaInterpreter(schema_of([CODES, values()])).encode(
            {"codes": ["1.8.1", "2.8.1"], "values": given}
        )
        assert not out.success
        assert any(message in e for e in out.errors), out.errors

    def test_a_sibling_unit_is_held_to_it(self):
        units = dict(CODES, name="units")
        reading = values(element={"type": "u32", "unit": "$units[i]"})
        del reading["identity"]
        out = SchemaInterpreter(schema_of([units, reading])).encode(
            {"units": ["1.8.1", "2.8.1"], "values": [1]}
        )
        assert any(
            "Repeat 'values' is given 1 values and 'units', which identifies them, 2 "
            "(PS-550)" in e
            for e in out.errors
        ), out.errors

    def test_a_repeat_without_a_sibling_reference_is_not_checked(self):
        # count 1, so the list agrees with its own count (PS-551, CR-2026-134) and only
        # the sibling's length differs.
        plain = values(count=1)
        del plain["identity"]
        out = SchemaInterpreter(schema_of([CODES, plain])).encode(
            {"codes": ["1.8.1", "2.8.1"], "values": [1]}
        )
        assert out.success, out.errors


class TestGeneratedCodec:
    def test_equal_lengths_encode(self):
        out = encode_codec(
            schema_of([CODES, values()]),
            {"codes": ["1.8.1", "2.8.1"], "values": [123, 456]},
        )
        assert not out.get("errors"), out
        assert bytes(out["bytes"]).hex() == "01020000007b000001c8"

    @pytest.mark.parametrize("given, message", CASES)
    def test_different_lengths_are_rejected_naming_both(self, given, message):
        out = encode_codec(
            schema_of([CODES, values()]),
            {"codes": ["1.8.1", "2.8.1"], "values": given},
        )
        assert any(message in e for e in out["errors"]), out
