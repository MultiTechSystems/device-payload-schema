"""PS-105: an out-of-bounds index into a sequence `lookup` is an error.

Every implementation silently reported the raw index instead, so a payload that did not
match its schema decoded as though it did - the index appearing under a name that
promises a label. No corpus vector exercised it, and the tests that touched it were
written to pass whatever the code did ("out of range should either return raw value or
'unknown'").

The two lookup failures are deliberately different, and the specification says so:

- A **mapping** gap is a known unknown: the field is omitted, no error (PS-269).
- A **sequence** index out of bounds is a shape mismatch: an error (PS-105).

PS-105 does not say whether the error aborts the payload. It is read as a decode error
because PS-278 shows the specification saying "report the field as absent and MUST NOT
abort" where that is what it means, and because PS-269 already provides the omit-quietly
behaviour for the case that deserves it.

Go, Java and C# assert the same message in their own test suites (cr004_test.go,
CR2026004Test.java, CR2026_004Tests.cs); the C interpreter has no sequence form at all -
its lookups are key/value pairs and the binary schema format cannot express a sequence -
so the requirement does not apply there.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import LookupIndexError, SchemaInterpreter, apply_lookup  # noqa: E402

SEQUENCE = (
    "name: t\nendian: big\nfields:\n"
    "  - name: relay\n    type: u8\n    lookup: [off_state, on_state]\n"
)
EXPECTED_MESSAGE = "lookup index 7 out of bounds for 2 entries"


def decode(body, payload_hex):
    return SchemaInterpreter(yaml.safe_load(body)).decode(
        bytes.fromhex(payload_hex)
    )


def decode_js(body, octets):
    js = TS013Generator(yaml.safe_load(body)).generate()
    driver = (
        js
        + f"\nvar _r = decodeUplink({{ bytes: {json.dumps(octets)}, fPort: 1 }});"
        + "\nconsole.log(JSON.stringify({data: _r.data, errors: _r.errors}));"
    )
    out = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestInterpreter:
    def test_an_in_range_index_still_maps(self):
        result = decode(SEQUENCE, "01")
        assert result.success
        assert result.data["relay"] == "on_state"

    def test_an_out_of_bounds_index_is_an_error(self):
        result = decode(SEQUENCE, "07")
        assert not result.success
        assert result.errors == [f"Error decoding relay: {EXPECTED_MESSAGE}"]

    def test_the_field_is_not_reported(self):
        # The point of the requirement: no raw index under a name promising a label.
        assert "relay" not in decode(SEQUENCE, "07").data

    def test_a_negative_index_is_also_out_of_bounds(self):
        body = (
            "name: t\nendian: big\nfields:\n"
            "  - name: relay\n    type: s8\n    lookup: [off_state, on_state]\n"
        )
        result = decode(body, "FF")   # -1
        assert not result.success
        assert "lookup index -1 out of bounds for 2 entries" in result.errors[0]

    def test_apply_lookup_raises_directly(self):
        with pytest.raises(LookupIndexError) as caught:
            apply_lookup(7, ["off_state", "on_state"])
        assert str(caught.value) == EXPECTED_MESSAGE

    def test_the_index_at_the_boundary_is_valid(self):
        body = (
            "name: t\nendian: big\nfields:\n"
            "  - name: relay\n    type: u8\n    lookup: [a, b, c]\n"
        )
        assert decode(body, "02").data["relay"] == "c"
        assert not decode(body, "03").success


class TestMappingIsUnaffected:
    """PS-269 keeps its own behaviour: a gap omits the field and is not an error."""

    MAPPING = (
        "name: t\nendian: big\nfields:\n"
        "  - name: button\n    type: u8\n    lookup: {1: short, 2: long}\n"
    )

    def test_a_mapping_gap_omits_quietly(self):
        result = decode(self.MAPPING, "09")
        assert result.success
        assert result.errors == []
        assert "button" not in result.data

    def test_a_mapping_gap_does_not_stop_later_fields(self):
        body = (
            "name: t\nendian: big\nfields:\n"
            "  - name: button\n    type: u8\n    lookup: {1: short}\n"
            "  - name: after\n    type: u8\n"
        )
        result = decode(body, "0905")
        assert result.success
        assert result.data["after"] == 5

    def test_a_mapping_default_still_applies(self):
        body = (
            "name: t\nendian: big\nfields:\n"
            "  - name: button\n    type: u8\n    lookup: {1: short, default: other}\n"
        )
        assert decode(body, "09").data["button"] == "other"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
class TestGeneratedCodec:
    def test_an_in_range_index_still_maps(self):
        assert decode_js(SEQUENCE, [1])["data"]["relay"] == "on_state"

    def test_an_out_of_bounds_index_is_reported_as_an_error(self):
        result = decode_js(SEQUENCE, [7])
        assert result["errors"] == [EXPECTED_MESSAGE]
        assert "relay" not in result["data"]

    def test_the_message_matches_the_interpreter(self):
        # The wording is the parity contract across implementations; Go, Java and C#
        # assert the same string in their own suites.
        js_message = decode_js(SEQUENCE, [7])["errors"][0]
        py_message = decode(SEQUENCE, "07").errors[0]
        assert py_message.endswith(js_message)

    def test_a_mapping_gap_still_omits_quietly_in_js(self):
        body = (
            "name: t\nendian: big\nfields:\n"
            "  - name: button\n    type: u8\n    lookup: {1: short}\n"
        )
        result = decode_js(body, [9])
        assert result["errors"] == []
        assert "button" not in result["data"]
