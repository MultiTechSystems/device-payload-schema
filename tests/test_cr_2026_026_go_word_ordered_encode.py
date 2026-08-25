"""CR-2026-026: the Go encoder gains `u32le16`, and rounds instead of truncating.

Two defects, the second found by fixing the first.

**`u32le16`/`s32le16` had no encode case at all.** `decodeField` has handled the
word-ordered type since it was added - least significant 16-bit unit first, each unit
big-endian, `endian` deliberately not consulted (PS-271, PS-272) - and `encodeField` never
did, so every field of the type fell to the switch default and reported
`cannot encode type "u32le16"`. Fourteen vectors across the `dl-*` schemas could be
decoded and not re-encoded - thirteen named `word_ordered_sensor_id` and `dl-blg`'s
`blg_all_sensors`. Fifteen schemas carry the type; `dl-dlr2-009-2000` has no vectors.

That the default *reports* rather than writing nothing is why this was findable at all: an
earlier CR made an unlisted type a failure instead of a silent zero-byte write.

**Go truncated its integer conversions where the reference rounds.** With the type
encoding, `dl-isf` still came back wrong: four fields carrying
`transform: [{mult: 0.00032}, {add: -10}]` reverse 1.6 to 4999.999999999999, and
`uint64(numVal)` writes 4999. The reference does `int(round(value))`. Go now uses
`math.RoundToEven`, which is half-to-even as the repo's convention requires.

**This one was Go's alone.** C# already had `Math.Round(numeric, MidpointRounding.ToEven)`
and Java `Math.rint`, both half-to-even. Checked rather than assumed, because assuming the
other implementations shared a defect is how CR-2026-024 went wrong.

Measured by identity, which is the method CR-2026-025 established after per-shape counts
produced a false conclusion:

    before   Go fails 105, of which 28 the reference passes
    after    Go fails  91, of which 14 the reference passes

Every vector the reference fails, Go still fails - the subset relation holds - and Go's
`flagged` bucket now reads 135, the same as the reference's. **That last agreement is a
coincidence of bucketing, not evidence**, and this file does not test it: the buckets are
not comparable between harnesses.

The fourteen still failing are none of them word-ordered: seven are TLV channel ordering in
the `em400-*`/`ws203` schemas, four are `ch8_type230`, two are vendor references, and one is
`match-default-fields.yaml`, a CR-2026-020 fixture Go decodes and cannot re-encode.
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

GO = REPO_ROOT / "go" / "schema" / "schema.go"
GO_FLOORS = REPO_ROOT / "go" / "schema" / "corpus_encode_test.go"
CORPUS = REPO_ROOT / "schemas" / "devices"

#: The vectors this CR makes re-encodable, by name. Thirteen share one name; `dl-blg`
#: spells its differently, which is why the count and the name do not match.
WITNESS_VECTORS = ("word_ordered_sensor_id", "blg_all_sensors")


def witness_schemas():
    found = []
    for path in sorted(CORPUS.rglob("*.yaml")):
        try:
            schema = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        if not isinstance(schema, dict):
            continue
        if "u32le16" not in path.read_text() and "s32le16" not in path.read_text():
            continue
        for vector in schema.get("test_vectors") or []:
            if vector.get("name") in WITNESS_VECTORS:
                found.append((path, schema, vector))
    return found


class TestTheGoEncoderHandlesTheWordOrderedType:
    """Read from source: the case, and the properties that make it correct."""

    def test_the_encode_case_exists(self):
        text = GO.read_text()
        assert re.search(r"case TypeU32LE16, TypeS32LE16:[\s\S]{0,600}?encodeUint", text), (
            "encodeField has no word-ordered case"
        )

    def test_it_writes_the_low_unit_first(self):
        """PS-271: least significant 16-bit unit first."""
        text = GO.read_text()
        case = text[text.index("case TypeU32LE16, TypeS32LE16:",
                               text.index("func encodeField(")):]
        low = case.index("0xFFFF, 2")
        high = case.index(">>16, 2")
        assert low < high, "the high unit is written before the low one"

    def test_each_unit_is_big_endian_and_endian_is_not_consulted(self):
        """PS-272: honouring `endian` would make this a second spelling of u32."""
        text = GO.read_text()
        start = text.index("case TypeU32LE16, TypeS32LE16:",
                           text.index("func encodeField("))
        case = text[start:start + 700]
        assert case.count('"big"') == 2, case[:300]
        assert "endian)" not in case.split("case TypeByte")[0], (
            "the case passes the schema's endian, which PS-272 forbids"
        )

    def test_it_says_which_cr_and_which_requirements(self):
        text = GO.read_text()
        start = text.index("case TypeU32LE16, TypeS32LE16:",
                           text.index("func encodeField("))
        case = text[start - 900:start]
        assert "CR-2026-026" in case
        assert "PS-271" in case and "PS-272" in case


class TestTheGoEncoderRoundsRatherThanTruncates:
    def test_the_unsigned_case_rounds(self):
        text = GO.read_text()
        assert "encodeUint(uint64(math.RoundToEven(numVal))" in text

    def test_the_signed_case_rounds(self):
        text = GO.read_text()
        assert "encodeSint(int64(math.RoundToEven(numVal))" in text

    def test_no_truncating_conversion_is_left_in_the_integer_cases(self):
        text = GO.read_text()
        assert "encodeUint(uint64(numVal)" not in text
        assert "encodeSint(int64(numVal)" not in text

    def test_it_is_half_to_even_not_half_away_from_zero(self):
        """The repo's convention, and what the reference's `round()` does."""
        text = GO.read_text()
        # `func encodeField(` with the paren: without it this matched `encodeFields`
        # and read the wrong function, which is the same substring trap that made a
        # CR-2026-024 test fail on correct code.
        start = text.index("func encodeField(")
        body = text[start:start + 4000]
        assert "RoundToEven" in body
        assert "int64(math.Round(numVal))" not in body, (
            "half-away-from-zero would disagree with the reference at .5"
        )


class TestTheOtherImplementationsAlreadyRounded:
    """Checked, not assumed. Assuming a shared defect is how CR-2026-024 went wrong."""

    def test_dotnet_rounds_half_to_even(self):
        text = (REPO_ROOT / "dotnet" / "PayloadSchema" / "SchemaEncoder.cs").read_text()
        assert "MidpointRounding.ToEven" in text

    def test_java_rounds_half_to_even(self):
        text = (REPO_ROOT / "bindings" / "java" / "src" / "main" / "java" / "org" / "lora"
                / "schema" / "Encoder.java").read_text()
        assert "Math.rint" in text, "Java's asLong no longer rounds half-to-even"

    def test_the_reference_rounds(self):
        text = (REPO_ROOT / "tools" / "schema_interpreter.py").read_text()
        assert "return int(round(value))" in text


class TestTheWitnessesAreRealAndRoundTripOnTheReference:
    """The Go side is held by its floors; this pins the fixtures they rely on."""

    def test_there_are_fourteen_of_them(self):
        found = witness_schemas()
        assert len(found) == 14, [f"{p.name}::{v['name']}" for p, _, v in found]

    def test_thirteen_share_the_one_name(self):
        """The count and the name differ, which is worth pinning rather than rounding."""
        named = [v for _, _, v in witness_schemas()
                 if v["name"] == "word_ordered_sensor_id"]
        assert len(named) == 13, len(named)

    def test_each_carries_a_word_ordered_field(self):
        """Guaranteed by the filter in witness_schemas; asserted so it stays that way."""
        for path, _, _ in witness_schemas():
            text = path.read_text()
            assert "u32le16" in text or "s32le16" in text, path.name

    def test_each_round_trips_on_the_reference(self):
        for path, schema, vector in witness_schemas():
            raw = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            decoded = SchemaInterpreter(schema).decode(raw)
            encoded = SchemaInterpreter(schema).encode(decoded.data)
            assert not encoded.errors, (path.name, encoded.errors)
            assert bytes(encoded.payload) == raw, path.name


class TestTheFloorMovedWithTheFix:
    def test_the_go_total_and_flagged_floors_are_raised(self):
        text = GO_FLOORS.read_text()
        assert "encodeFloorTotal = 1169" in text
        assert '"flagged":     135,' in text

    def test_the_raise_is_explained(self):
        assert "CR-2026-026" in GO_FLOORS.read_text()

    def test_the_python_floor_did_not_move(self):
        """No reference behaviour changed, so its ratchet must not have been touched."""
        text = (REPO_ROOT / "tests" / "test_encode_round_trip.py").read_text()
        assert "FLOOR_TOTAL = 1160" in text
        assert "CR-2026-026" not in text, (
            "this CR changed no reference behaviour; its floor should not cite it"
        )
