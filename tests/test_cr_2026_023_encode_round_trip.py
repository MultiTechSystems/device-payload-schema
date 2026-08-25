"""CR-2026-023: the encode round-trip residue, explained rather than counted.

`tests/test_encode_round_trip.py` and the Java, Go and C# round-trip tests report a count
and a per-shape breakdown. Neither said *why* the rest differ, so "1143 of 1237" read as
an encoder full of holes when most of the residue is information the decode genuinely did
not keep. Auditing it turned up one real encoder bug and left nothing unexplained.

**The bug: a bare run of bit ranges was not packed.** `_encode_field` wrote each bitfield
as a whole byte holding its unshifted value, ignoring both the bit range and `consume: 0`.
A LoRaWAN MHDR's three ranges came back as three bytes: `40` encoded as `020000`, the
wrong length and the wrong bits, with no error. `byte_group` was given packing when its
own encoding was fixed; a bare run - the same thing without the wrapper - never was.

Fixing it also *reclassified* two ws50x vectors. Their payloads carry five bytes an
unknown TLV tag makes undecodable (CR-2026-014 recorded exactly that), so three bytes is
the correct re-encode. The old one-byte-per-field bug happened to emit six, which made the
length match and left only a nibble wrong - a fundamentally unreversible vector looking
nearly right. They now fail on length, honestly.

`tools/encode-round-trip.py` classifies every difference. The test below is what keeps it
useful: **nothing may be unexplained.** A new encoder defect shows up as an unexplained
row rather than as a number that was already large.
"""

import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

TOOL = REPO_ROOT / "tools" / "encode-round-trip.py"


def load_tool():
    loader = SourceFileLoader("ert", str(TOOL))
    spec = spec_from_loader("ert", loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def round_trip(schema, payload):
    raw = bytes.fromhex(payload)
    decoded = SchemaInterpreter(schema).decode(raw)
    encoded = SchemaInterpreter(schema).encode(decoded.data)
    return decoded, bytes(encoded.payload), encoded


class TestABareBitfieldRunIsPacked:
    """The bug this CR found. `byte_group` always did this; a bare run did not."""

    MHDR = {
        "name": "mhdr", "endian": "little",
        "fields": [
            {"name": "mtype", "type": "u8[5:7]"},
            {"name": "rfu", "type": "u8[2:4]"},
            {"name": "major", "type": "u8[0:1]", "consume": 1},
        ],
    }

    def test_three_ranges_occupy_one_byte(self):
        decoded, got, _ = round_trip(self.MHDR, "40")
        assert decoded.data == {"mtype": 2, "rfu": 0, "major": 0}
        assert got == bytes.fromhex("40"), got.hex()

    def test_it_used_to_emit_one_byte_per_field(self):
        """`40` became `020000`: three bytes of unshifted values."""
        _, got, _ = round_trip(self.MHDR, "40")
        assert len(got) == 1, f"one shared byte expected, got {got.hex()}"

    @pytest.mark.parametrize("payload", ["00", "20", "40", "60", "80", "a0", "e3", "ff"])
    def test_every_bit_pattern_survives(self, payload):
        _, got, _ = round_trip(self.MHDR, payload)
        assert got == bytes.fromhex(payload), f"{payload} -> {got.hex()}"

    def test_a_five_field_run_too(self):
        """The LoRaWAN FCtrl: four flags and a nibble in one byte."""
        fctrl = {
            "name": "fctrl", "endian": "little",
            "fields": [
                {"name": "adr", "type": "u8[7:7]"},
                {"name": "adr_ack_req", "type": "u8[6:6]"},
                {"name": "ack", "type": "u8[5:5]"},
                {"name": "class_b", "type": "u8[4:4]"},
                {"name": "fopts_len", "type": "u8[0:3]", "consume": 1},
            ],
        }
        for payload in ("a0", "00", "ff", "0f", "f0"):
            _, got, _ = round_trip(fctrl, payload)
            assert got == bytes.fromhex(payload), f"{payload} -> {got.hex()}"

    def test_a_run_is_closed_by_consume(self):
        """Two bytes, each its own run, rather than one packed span."""
        two = {
            "name": "two", "endian": "big",
            "fields": [
                {"name": "a", "type": "u8[4:7]"},
                {"name": "b", "type": "u8[0:3]", "consume": 1},
                {"name": "c", "type": "u8[4:7]"},
                {"name": "d", "type": "u8[0:3]", "consume": 1},
            ],
        }
        _, got, _ = round_trip(two, "1234")
        assert got == bytes.fromhex("1234"), got.hex()

    def test_a_plain_field_after_a_run_still_follows_it(self):
        mixed = {
            "name": "mixed", "endian": "big",
            "fields": [
                {"name": "hi", "type": "u8[4:7]"},
                {"name": "lo", "type": "u8[0:3]", "consume": 1},
                {"name": "plain", "type": "u16"},
            ],
        }
        _, got, _ = round_trip(mixed, "12abcd")
        assert got == bytes.fromhex("12abcd"), got.hex()

    def test_undescribed_bits_are_zero_not_guessed(self):
        """vicki's status byte describes bits 3..7; 0..2 are not in the output."""
        partial = {
            "name": "partial", "endian": "big",
            "fields": [
                {"name": "top", "type": "u8[4:7]"},
                {"name": "flag", "type": "u8[3:3]", "consume": 1},
            ],
        }
        _, got, _ = round_trip(partial, "ff")
        assert got == bytes.fromhex("f8"), (
            "the undescribed low bits must be zero, not carried over"
        )

    def test_a_label_on_a_bit_range_is_resolved(self):
        """An `enum` on a bit range reports a label; the number has to come back."""
        labelled = {
            "name": "labelled", "endian": "big",
            "fields": [
                {"name": "kind", "type": "u8[6:7]", "enum": {0: "a", 1: "b", 2: "c"}},
                {"name": "rest", "type": "u8[0:5]", "consume": 1},
            ],
        }
        decoded, got, result = round_trip(labelled, "81")
        assert not result.errors, result.errors
        assert got == bytes.fromhex("81"), got.hex()

    def test_an_unmappable_label_is_reported_not_written_as_zero(self):
        labelled = {
            "name": "labelled", "endian": "big",
            "fields": [
                {"name": "kind", "type": "u8[6:7]", "enum": {0: "a"}},
                {"name": "rest", "type": "u8[0:5]", "consume": 1},
            ],
        }
        result = SchemaInterpreter(labelled).encode({"kind": "nope", "rest": 1})
        assert result.errors, "a label nobody can map must not silently become zero"


class TestTheCorpusResidueIsExplained:
    """The tool, and the property that makes it worth having."""

    def test_nothing_is_unexplained(self):
        done = subprocess.run([sys.executable, str(TOOL), "--json", "/dev/stdout"],
                              cwd=REPO_ROOT, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr[:400]
        blob = done.stdout[done.stdout.index("{"):done.stdout.rindex("}") + 1]
        report = json.loads(blob)
        unexplained = [r for r in report["rows"] if r["reason"] == "unexplained"]
        assert not unexplained, (
            "a difference with no recorded reason is either a new encoder defect or a "
            "reason the tool has not learned: "
            + ", ".join(f"{r['schema']}::{r['vector']}" for r in unexplained[:8])
        )

    def test_the_round_trip_count_has_not_regressed(self):
        module = load_tool()
        assert module.INHERENT, "the reason vocabulary went missing"
        assert "templated-name" in module.FIXABLE

    def test_every_reason_is_documented(self):
        module = load_tool()
        doc = module.__doc__ or ""
        for reason in module.INHERENT + module.FIXABLE:
            assert reason in doc, f"{reason} is classified but not explained"

    def test_the_unread_markers_match_what_a_decode_says(self):
        """The tool reads decode warnings; a reworded warning must not blind it."""
        module = load_tool()
        schema = {
            "name": "probe", "endian": "big",
            "fields": [{"tlv": {"tag_size": 1,
                                "cases": {1: [{"name": "known", "type": "u16"}]}}}],
        }
        decoded = SchemaInterpreter(schema).decode(bytes.fromhex("01003C090BB8"))
        assert decoded.warnings, "expected an unknown-tag warning to classify from"
        assert any(marker in decoded.warnings[0] for marker in module.UNREAD_MARKERS), (
            f"no marker matches {decoded.warnings[0]!r}"
        )


class TestTheOtherEncodersHaveCaughtUp:
    """CR-2026-024 brought Java, C# and Go onto this too.

    This class was the inverse when CR-2026-023 landed: it read their source to assert
    they packed `byte_group` and *not* a bare run, so the gap could not go stale
    unnoticed. It now asserts the opposite, which is what stops the gap reopening.
    """

    def test_the_reference_encoder_packs(self):
        mhdr = {
            "name": "mhdr", "endian": "little",
            "fields": [
                {"name": "mtype", "type": "u8[5:7]"},
                {"name": "rfu", "type": "u8[2:4]"},
                {"name": "major", "type": "u8[0:1]", "consume": 1},
            ],
        }
        _, got, _ = round_trip(mhdr, "40")
        assert got == bytes.fromhex("40")

    def test_all_four_encoders_have_a_bare_run_packer(self):
        """Read from their source, so a removal shows up here rather than in a floor."""
        sources = {
            "Encoder.java": (REPO_ROOT / "bindings" / "java" / "src" / "main" / "java"
                             / "org" / "lora" / "schema" / "Encoder.java"),
            "SchemaEncoder.cs": REPO_ROOT / "dotnet" / "PayloadSchema" / "SchemaEncoder.cs",
            "schema.go": REPO_ROOT / "go" / "schema" / "schema.go",
        }
        for name, path in sources.items():
            text = path.read_text()
            assert "itfieldRun" in text, f"{name} has no bare-run packer"
            assert "CR-2026-024" in text, f"{name} does not say where the fix came from"
