"""CR-2026-024: Java, C# and Go pack a bare bitfield run on encode too.

CR-2026-023 fixed the Python reference encoder and left this recorded as the follow-up:
three implementations packed `byte_group` and wrote every other bit range as a whole byte
holding its unshifted value. A LoRaWAN MHDR's three ranges came back as three bytes,
`40` encoding as `020000`.

The witnesses are the three composed LoRaWAN header schemas in the corpus, which each
decode one byte into bit ranges and could not re-encode it on any of the three:

    lorawan_frames__mhdr_unconfirmed_up      40
    lorawan_frames__mhdr_confirmed_down      a0
    lorawan_frames__fctrl_uplink_adr_ack     a0

Their round-trip counts move as follows, and the floors move with them:

    Java   1143 -> 1145     plain fixed 55 -> 58
    C#     1144 -> 1146     plain fixed 55 -> 58
    Go     1144 -> 1155     plain fixed 55 -> 58, tlv 906 -> 910

Two things the numbers say that are worth reading rather than skipping:

**Go gained nine tlv vectors and the others none.** Its TLV cases built from bit ranges
were being written a byte apiece; the same cases in Java and C# fail for a different
reason, which is the `ambiguous-case` class CR-2026-023 catalogued - several cases sharing
field names, so the tag is not recoverable. Different defects behind the same count.

**The claim that Go round-trips ten tlv vectors the reference does not was wrong**, and
CR-2026-025 withdrew it. It came from subtracting two per-shape counts built by harnesses
that bucket schemas differently and count different denominators. Comparing the vector sets
instead: every vector the Python encoder fails, Go fails too, and Go fails 28 more. The
shape counts below are still the right ratchets for each harness over time; they are not
comparable between harnesses.

`flagged` moves on none of them. No `flagged` group in the corpus holds a bit range -
they are `u16`, `u32le16` and computed members - so the 14 that differ there are a
separate Java/C#/Go-versus-Python gap this CR does not touch. The run packing is wired
through the flagged path anyway, so a group that grows one will pack rather than silently
not.
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

#: The shape floor each round-trip test now holds, and the file that holds it.
#:
#: Only the shape, and deliberately. This started out pinning the running total too, and
#: every later CR that raised a floor broke it - CR-2026-026 and CR-2026-027 each did. The
#: total is not this CR's to own; `plain fixed` is the bucket it moved, and the pattern
#: below reads the total as a lower bound instead.
#: (total-name, total-floor, shape-name, shape-floor) per file. Both are read as lower
#: bounds. Bounding only the total was CR-2026-028's half-fix: the bucket is a running
#: value too, and CR-2026-030 raised `plain fixed` from 58 to 59 and broke this.
FLOORS = {
    REPO_ROOT / "bindings" / "java" / "src" / "test" / "java" / "org" / "lora" / "schema"
    / "CorpusEncodeRoundTripTest.java": ("ENCODE_FLOOR_TOTAL", 1145, '"plain fixed", ', 58),
    REPO_ROOT / "dotnet" / "PayloadSchema.Tests" / "CorpusEncodeRoundTripTests.cs":
        ("EncodeFloorTotal", 1146, '["plain fixed"] = ', 58),
    REPO_ROOT / "go" / "schema" / "corpus_encode_test.go":
        ("encodeFloorTotal", 1155, '"plain fixed": ', 58),
}


def floor_at_least(path, name, minimum, pattern=None):
    """Assert a floor in `path` is at least `minimum`, whatever later CRs raised it to."""
    text = path.read_text()
    probe = pattern if pattern else rf"{name}\s*=\s*(\d+)"
    found = re.search(probe if pattern else probe, text)
    assert found, f"{path.name}: no {name}"
    assert int(found.group(1)) >= minimum, (path.name, found.group(1), minimum)

#: The corpus vectors this CR exists to make round-trip.
WITNESSES = (
    "lorawan_frames__mhdr_unconfirmed_up.yaml",
    "lorawan_frames__mhdr_confirmed_down.yaml",
    "lorawan_frames__fctrl_uplink_adr_ack.yaml",
)


class TestEveryEncoderPacksABareRun:
    """Read from source: a removal shows up here rather than as a puzzling floor break."""

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_has_a_bare_run_packer(self, name):
        text = ENCODERS[name].read_text()
        assert "itfieldRun" in text, f"{name} has no bare-run packer"

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_says_where_the_fix_came_from(self, name):
        assert "CR-2026-024" in ENCODERS[name].read_text(), name

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_still_packs_byte_group_separately(self, name):
        """The construct that always worked must not have been folded away."""
        text = ENCODERS[name].read_text()
        assert re.search(r"[eE]ncodeByteGroup", text), name

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_resolves_a_label_rather_than_writing_zero(self, name):
        """The reference raises on an unmappable label; so must these."""
        text = ENCODERS[name].read_text()
        assert re.search(r"[bB]itfieldLabelValue", text), (
            f"{name} has no label resolution, so an enum on a bit range writes zero"
        )
        assert "declared values" in text, name


class TestTheFloorsMovedWithTheFix:
    @pytest.mark.parametrize("path", sorted(FLOORS, key=str))
    def test_the_shape_floor_is_at_least_what_this_cr_reached(self, path):
        _, _, shape_prefix, shape_floor = FLOORS[path]
        floor_at_least(path, shape_prefix, shape_floor,
                       pattern=re.escape(shape_prefix) + r"(\d+)")

    @pytest.mark.parametrize("path", sorted(FLOORS, key=str))
    def test_the_total_floor_is_at_least_what_this_cr_reached(self, path):
        name, floor, _, _ = FLOORS[path]
        floor_at_least(path, name, floor)

    @pytest.mark.parametrize("path", sorted(FLOORS, key=str))
    def test_the_raise_is_explained(self, path):
        assert "CR-2026-024" in path.read_text(), path.name


class TestTheWitnessesRoundTripOnTheReference:
    """The Python side, which CR-2026-023 fixed. The other three are held by their floors.

    Asserted here as well so the fixtures cannot quietly stop being the witnesses: if one
    of these schemas changes shape, this fails rather than the floors drifting.
    """

    @pytest.mark.parametrize("filename", WITNESSES)
    def test_the_witness_exists(self, filename):
        matches = list(CORPUS.rglob(filename))
        assert matches, f"{filename} is no longer in the corpus"

    @pytest.mark.parametrize("filename", WITNESSES)
    def test_it_is_one_byte_of_bit_ranges(self, filename):
        schema = yaml.safe_load(next(CORPUS.rglob(filename)).read_text())
        ranges = [f for f in schema["fields"] if "[" in str(f.get("type", ""))]
        assert len(ranges) >= 3, f"{filename} is no longer a bare run"
        assert sum(int(f.get("consume", 0) or 0) for f in ranges) == 1, (
            "exactly one member closes the span"
        )

    @pytest.mark.parametrize("filename", WITNESSES)
    def test_it_round_trips(self, filename):
        schema = yaml.safe_load(next(CORPUS.rglob(filename)).read_text())
        for vector in schema["test_vectors"]:
            raw = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            decoded = SchemaInterpreter(schema).decode(raw)
            encoded = SchemaInterpreter(schema).encode(decoded.data)
            assert not encoded.errors, encoded.errors
            assert bytes(encoded.payload) == raw, (
                f"{filename}::{vector['name']}: {bytes(encoded.payload).hex()} != {raw.hex()}"
            )


class TestWhatThisCrDoesNotClaim:
    """Stated as tests so the limits are not read as oversights."""

    def test_no_flagged_group_holds_a_bit_range(self):
        """Why `flagged` moves on none of the three."""
        found = []
        for path in CORPUS.rglob("*.yaml"):
            try:
                schema = yaml.safe_load(path.read_text())
            except yaml.YAMLError:
                continue

            def walk(node):
                if isinstance(node, dict):
                    flagged = node.get("flagged")
                    if isinstance(flagged, dict):
                        for group in flagged.get("groups") or []:
                            for member in group.get("fields") or []:
                                if "[" in str(member.get("type", "")):
                                    found.append(path.name)
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for item in node:
                        walk(item)

            walk(schema)
        assert not found, (
            "a flagged group now holds a bit range, so the flagged floors can rise: "
            + ", ".join(sorted(set(found)))
        )

    def test_the_run_packing_is_wired_through_the_flagged_path_anyway(self):
        """So the day one appears, it packs rather than silently not."""
        for name, path in ENCODERS.items():
            text = path.read_text()
            # The definition, not the call site in the dispatch: searching for the
            # bare name found the latter and this test failed on correct code.
            definition = re.search(r"(byte\[\] [eE]ncodeFlagged|func encodeFlagged)", text)
            assert definition, name
            window = text[definition.start():definition.start() + 2400]
            assert "itfieldRun" in window, (
                f"{name}: encodeFlagged does not route through the run packing"
            )
