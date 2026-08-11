"""`length: remaining` - the only spelling for "consume to the end of the payload".

PS-013 fixes how it is evaluated, PS-014 fixes the empty result at the end of a
payload, and PS-015 allows it on at most one field per nesting level.

These exist because the keyword was specified but implemented nowhere: the one
schema that needed it (radio-bridge rbs30x) wrote `length: -1` instead, which in
Python sliced `buf[pos:pos - 1]` - an empty value *and* a read cursor rewound by a
byte - while Go's Read already resolved a negative count to the remainder. The two
interpreters disagreed on that field and no vector asserted it.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema  # noqa: E402


def decode(payload_hex, length="remaining", ftype="bytes"):
    schema = yaml.safe_load(
        "name: t\nendian: big\nfields:\n"
        "  - name: head\n    type: u8\n"
        f"  - name: tail\n    type: {ftype}\n    length: {length}\n"
    )
    result = SchemaInterpreter(schema).decode(bytes.fromhex(payload_hex))
    assert result.success, result.errors
    return result.data


class TestEvaluation:
    def test_consumes_every_byte_after_the_read_position(self):
        # PS-013: total_payload_length - current_read_position, at extraction.
        assert decode("10010009")["tail"] == "010009"

    def test_is_empty_at_the_end_of_the_payload(self):
        # PS-014: not an error, and not a one-byte read.
        assert decode("10")["tail"] == ""

    def test_does_not_rewind_the_read_cursor(self):
        # The `length: -1` defect: a field after the remainder would have re-read
        # bytes the remainder had already consumed.
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: tail\n    type: bytes\n    length: remaining\n"
            "  - name: after\n    type: u8\n"
        )
        result = SchemaInterpreter(schema).decode(bytes.fromhex("0102"))
        assert result.data["tail"] == "0102"
        assert "after" not in result.data or result.data.get("after") is None

    @pytest.mark.parametrize(
        "ftype,expected",
        [("bytes", ""), ("hex", ""), ("ascii", ""), ("string", "")],
    )
    def test_empty_result_per_type(self, ftype, expected):
        assert decode("10", ftype=ftype)["tail"] == expected

    def test_ascii_reads_the_remainder_as_text(self):
        assert decode("10414243", ftype="ascii")["tail"] == "ABC"

    def test_hex_and_bytes_agree(self):
        # CR-2026-008/PS-281: both report a lowercase hex string.
        assert decode("10ab", ftype="hex")["tail"] == decode("10ab", ftype="bytes")["tail"]
        assert decode("10AB", ftype="bytes")["tail"] == "ab"


class TestValidation:
    def base(self, fields):
        return yaml.safe_load("name: t\nendian: big\nfields:\n" + fields)

    def test_a_negative_length_is_rejected(self):
        # `remaining` is the only spelling; -1 is an internal sentinel.
        result = validate_schema(
            self.base("  - name: tail\n    type: bytes\n    length: -1\n")
        )
        assert not result.schema_valid
        assert any("remaining" in e for e in result.schema_errors), result.schema_errors

    def test_remaining_is_accepted_on_a_variable_length_type(self):
        result = validate_schema(
            self.base("  - name: tail\n    type: bytes\n    length: remaining\n")
        )
        assert result.schema_valid, result.schema_errors

    def test_remaining_is_rejected_on_a_fixed_width_type(self):
        result = validate_schema(
            self.base("  - name: tail\n    type: u16\n    length: remaining\n")
        )
        assert not result.schema_valid
        assert any("not valid on type" in e for e in result.schema_errors), (
            result.schema_errors
        )

    def test_two_remainders_at_one_level_are_rejected(self):
        # PS-015: only one field can consume the remainder.
        result = validate_schema(
            self.base(
                "  - name: a\n    type: bytes\n    length: remaining\n"
                "  - name: b\n    type: bytes\n    length: remaining\n"
            )
        )
        assert not result.schema_valid
        assert any("more than one field" in e for e in result.schema_errors), (
            result.schema_errors
        )


class TestTheSchemaThatNeededIt:
    def test_rbs30x_reports_its_stored_downlink(self):
        schema = yaml.safe_load(
            (REPO_ROOT / "schemas" / "devices" / "radio-bridge" / "rbs30x.yaml")
            .read_text(encoding="utf-8")
        )
        result = validate_schema(schema)
        assert result.schema_valid, result.schema_errors
        assert result.tests_failed == 0, [
            test.errors for test in result.test_results if not test.passed
        ]
        vectors = {v["name"]: v for v in schema["test_vectors"]}
        # Pinned so the field cannot silently go back to being unasserted.
        assert vectors["Device info packet"]["expected"]["stored_downlink"] == "010009"
        assert (
            vectors["Device info packet with no stored downlink"]["expected"][
                "stored_downlink"
            ]
            == ""
        )
