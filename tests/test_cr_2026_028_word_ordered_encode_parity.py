"""CR-2026-028: Java and C# encode the word-ordered types too.

The port CR-2026-027 recorded. CR-2026-026 gave Go the `u32le16`/`s32le16` encode case;
Java and C# had the type in their enums, read it correctly on decode, and had no encode
case for either.

**They failed differently, and the Java form was the worse one.** C# had no case at all, so
both types fell to the throw at the end of its type switch and the encode reported
`Cannot encode type: U32LE16`. Java's `FieldType.isInteger()` *includes* both, so they fell
through to the plain integer path and were written as an ordinary four-byte value in the
schema's byte order - the right length, the wrong order, and no error. The decoder has
always read them word-ordered, so every such field re-encoded to bytes it would not decode
back.

That is why the two showed up as different symptoms in the same bucket: Java's `flagged`
read 121 with 14 `bytes differ`, C#'s 121 with 14 `error`. One silent, one loud, one cause.

    Java   1146 -> 1160     flagged 121 -> 135
    C#     1147 -> 1161     flagged 121 -> 135

Measured by identity (CR-2026-025's method), Go's remaining gap against the reference is
13, all of it TLV channel ordering in the `em400-*`/`ws203` schemas plus two vendor
references. The word-ordered cluster is closed in all four encoders.
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

CORPUS = REPO_ROOT / "schemas" / "devices"

ENCODERS = {
    "Encoder.java": (REPO_ROOT / "bindings" / "java" / "src" / "main" / "java" / "org"
                     / "lora" / "schema" / "Encoder.java"),
    "SchemaEncoder.cs": REPO_ROOT / "dotnet" / "PayloadSchema" / "SchemaEncoder.cs",
    "schema.go": REPO_ROOT / "go" / "schema" / "schema.go",
}

#: Where each encoder's word-ordered path starts: (search-from, anchor). The search-from
#: matters for schema.go, whose decode and encode cases are spelled identically - anchoring
#: on the case text alone found the decode one and the test failed on correct code. That is
#: the fourth loose anchor to lie in these tests this session, after `index("ncode")` here,
#: `func encodeField` matching `encodeFields` in CR-2026-026, and a method name matching a
#: call site in CR-2026-024. Anchor on something that occurs once, and say where from.
ANCHORS = {
    "Encoder.java": ("", "type == FieldType.U32LE16"),
    "SchemaEncoder.cs": ("", "case FieldType.U32LE16 or FieldType.S32LE16:"),
    "schema.go": ("func encodeField(", "case TypeU32LE16, TypeS32LE16:"),
}


def anchor_at(name):
    """The offset of the encode path in one encoder, searched from its own marker."""
    text = ENCODERS[name].read_text()
    since, anchor = ANCHORS[name]
    start = text.index(since) if since else 0
    return text, text.index(anchor, start)

FLOORS = {
    REPO_ROOT / "bindings" / "java" / "src" / "test" / "java" / "org" / "lora" / "schema"
    / "CorpusEncodeRoundTripTest.java": ("ENCODE_FLOOR_TOTAL", 1160, '"flagged", 135,'),
    REPO_ROOT / "dotnet" / "PayloadSchema.Tests" / "CorpusEncodeRoundTripTests.cs":
        ("EncodeFloorTotal", 1161, '["flagged"] = 135,'),
}


class TestAllFourEncodersHandleTheType:
    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_has_a_word_ordered_encode_path(self, name):
        assert "U32LE16" in ENCODERS[name].read_text(), (
            f"{name} lost its word-ordered encode case"
        )

    @pytest.mark.parametrize("name", ["Encoder.java", "SchemaEncoder.cs"])
    def test_the_port_is_attributed(self, name):
        assert "CR-2026-028" in ENCODERS[name].read_text(), name

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_the_anchor_is_present(self, name):
        """The other tests read from it, so its absence must fail loudly and first."""
        anchor_at(name)

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_writes_two_big_endian_units(self, name):
        """PS-272: `endian` plays no part, so both units are written big-endian."""
        text, start = anchor_at(name)
        window = text[start:start + 900]
        big = window.count('"big"') + window.count(", 2, false, false)")
        assert big >= 2, window[:400]

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_masks_to_thirty_two_bits(self, name):
        text = ENCODERS[name].read_text()
        assert "0xFFFFFFFF" in text, f"{name}: no 32-bit mask"

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_cites_the_requirements(self, name):
        text, start = anchor_at(name)
        near = text[max(0, start - 1400):start]
        assert "PS-271" in near and "PS-272" in near, name


class TestTheJavaFailureModeIsRecorded:
    """The silent one, which is the reason this was worth a test rather than a note."""

    def test_the_type_is_still_an_integer_type_in_java(self):
        """Which is *why* it fell through: the guard has to precede that path."""
        text = (REPO_ROOT / "bindings" / "java" / "src" / "main" / "java" / "org" / "lora"
                / "schema" / "FieldType.java").read_text()
        integer_block = text[text.index("boolean isInteger"):]
        integer_block = integer_block[:integer_block.index("}")]
        assert "U32LE16" in integer_block, (
            "if the type left isInteger(), this guard's placement no longer matters"
        )

    def test_the_guard_precedes_the_integer_path(self):
        text = ENCODERS["Encoder.java"].read_text()
        guard = text.index("type == FieldType.U32LE16")
        integer_path = text.index("if (type.isInteger())")
        assert guard < integer_path, (
            "the word-ordered guard must come first or the plain integer path wins"
        )


class TestTheWitnessesRoundTripOnTheReference:
    """The other three are held by their floors; this pins the fixtures."""

    def witnesses(self):
        found = []
        for path in sorted(CORPUS.rglob("*.yaml")):
            text = path.read_text()
            if "u32le16" not in text and "s32le16" not in text:
                continue
            try:
                schema = yaml.safe_load(text)
            except yaml.YAMLError:
                continue
            for vector in schema.get("test_vectors") or []:
                if vector.get("payload"):
                    found.append((path, schema, vector))
        return found

    def test_there_are_witnesses_at_all(self):
        assert len(self.witnesses()) >= 14, len(self.witnesses())

    def test_each_round_trips(self):
        for path, schema, vector in self.witnesses():
            raw = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            decoded = SchemaInterpreter(schema).decode(raw)
            if decoded.errors:
                continue
            encoded = SchemaInterpreter(schema).encode(decoded.data)
            assert not encoded.errors, (path.name, vector["name"], encoded.errors)
            assert bytes(encoded.payload) == raw, f"{path.name}::{vector['name']}"


class TestTheFloorsMovedWithTheFix:
    @pytest.mark.parametrize("path", sorted(FLOORS, key=str))
    def test_the_flagged_floor_is_raised(self, path):
        _, _, shape = FLOORS[path]
        assert shape in path.read_text(), f"{path.name}: expected {shape}"

    @pytest.mark.parametrize("path", sorted(FLOORS, key=str))
    def test_the_total_floor_is_at_least_what_this_cr_reached(self, path):
        """A lower bound. Three earlier CRs pinned an exact total and were broken by the
        next one; the pattern is in AGENTS.md now."""
        name, floor, _ = FLOORS[path]
        found = re.search(rf"{name}\s*=\s*(\d+)", path.read_text())
        assert found, f"{path.name}: no {name}"
        assert int(found.group(1)) >= floor, (path.name, found.group(1), floor)

    @pytest.mark.parametrize("path", sorted(FLOORS, key=str))
    def test_the_raise_is_explained(self, path):
        assert "CR-2026-028" in path.read_text(), path.name

    def test_the_reference_floor_did_not_move(self):
        """Nothing about the reference changed; it always encoded these."""
        text = (REPO_ROOT / "tests" / "test_encode_round_trip.py").read_text()
        assert "FLOOR_TOTAL = 1160" in text
