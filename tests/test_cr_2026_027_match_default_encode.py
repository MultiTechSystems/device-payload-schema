"""CR-2026-027: encode a match's `default:` field list, in Go, Java and C#.

`match-default-fields.yaml` is a CR-2026-020 fixture. All five implementations decode it;
three could not re-encode it, so `097f` came back as `09` - the discriminator written and
the fallback's byte dropped, with no error.

Two things were missing, and they are not the same thing:

- **The `default:` key beside `cases` was never read on encode.** Java and C# already
  honoured a `default` *case* - the `cases: {default: [...]}` spelling - and neither read
  the sibling key. Go read neither.
- **The claimable-name heuristic ran first.** Java and C# tried "which case's field names
  does the data carry" before falling back to a default case. Where the discriminator is
  known and matched nothing, that is backwards: the schema said what an unmatched value
  means, so guessing a case from the names present contradicts it. The heuristic is still
  there, and still first, for an inline match whose discriminator is not in the data at
  all - which is the case it exists for.

Java's parser now converts the `default:` list to Fields once, at parse time, rather than
at decode time: the encoder is handed already-parsed Fields and has no `parseFields` of its
own. C# already parsed it that way; Go keeps the raw list and converts in both places,
which is what its own `parseFieldsRaw` is for.

    Go     1169 -> 1170     match 43 -> 44
    Java   1145 -> 1146     match 43 -> 44
    C#     1146 -> 1147     match 43 -> 44

Every implementation's `match` bucket now reads 44, the same as the reference's - and that
agreement is *not* evidence of anything, because the buckets are not comparable between
harnesses (CR-2026-025). The evidence is that the vector round-trips, which its floors now
hold.

**Found while doing this, not fixed here:** Java and C# have `u32le16`/`s32le16` in their
type enums and no encode case for either, which is the gap CR-2026-026 closed in Go. It is
worth about 14 vectors each and shows up as their `flagged` bucket reading 121 where Go's
and the reference's read 135. Checked from source rather than inferred, and left to its own
CR - the same fix ported twice is not this CR's subject.
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

FIXTURE = (REPO_ROOT / "schemas" / "devices" / "_language-conformance"
           / "match-default-fields.yaml")

ENCODERS = {
    "schema.go": REPO_ROOT / "go" / "schema" / "schema.go",
    "Encoder.java": (REPO_ROOT / "bindings" / "java" / "src" / "main" / "java" / "org"
                     / "lora" / "schema" / "Encoder.java"),
    "SchemaEncoder.cs": REPO_ROOT / "dotnet" / "PayloadSchema" / "SchemaEncoder.cs",
}

#: The bucket this CR moved, and the total it reached, per file.
#:
#: The total is read as a lower bound. This file was written in the same commit that fixed
#: CR-2026-024's and CR-2026-026's tests for pinning an exact total - and then pinned one
#: itself, which CR-2026-028 broke two files later. The lesson only counts if it is applied
#: to the test being written, not just the ones being repaired.
FLOORS = {
    REPO_ROOT / "go" / "schema" / "corpus_encode_test.go":
        ("encodeFloorTotal", 1170, '"match":       44,'),
    REPO_ROOT / "bindings" / "java" / "src" / "test" / "java" / "org" / "lora" / "schema"
    / "CorpusEncodeRoundTripTest.java": ("ENCODE_FLOOR_TOTAL", 1146, '"match", 44,'),
    REPO_ROOT / "dotnet" / "PayloadSchema.Tests" / "CorpusEncodeRoundTripTests.cs":
        ("EncodeFloorTotal", 1147, '["match"] = 44,'),
}


def schema():
    return yaml.safe_load(FIXTURE.read_text())


class TestTheFixtureIsWhatTheCrSaysItIs:
    def test_it_exists_and_uses_the_default_key(self):
        loaded = schema()
        match = next(f["match"] for f in loaded["fields"] if "match" in f)
        assert isinstance(match.get("default"), list), (
            "the fixture no longer uses the `default:` key form this CR is about"
        )
        assert "default" not in match.get("cases", {}), (
            "it must be the sibling key, not a `default` case - those are different paths"
        )

    def test_both_vectors_are_present(self):
        names = {v["name"] for v in schema()["test_vectors"]}
        assert names == {"an_unmatched_value_takes_the_default",
                         "a_matched_value_ignores_the_default"}

    def test_both_round_trip_on_the_reference(self):
        loaded = schema()
        for vector in loaded["test_vectors"]:
            raw = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            decoded = SchemaInterpreter(loaded).decode(raw)
            encoded = SchemaInterpreter(loaded).encode(decoded.data)
            assert not encoded.errors, (vector["name"], encoded.errors)
            assert bytes(encoded.payload) == raw, (
                f"{vector['name']}: {bytes(encoded.payload).hex()} != {raw.hex()}"
            )

    def test_the_unmatched_vector_is_the_one_that_was_dropped(self):
        """`097f` came back as `09`: the fallback's byte was never written."""
        loaded = schema()
        vector = next(v for v in loaded["test_vectors"]
                      if v["name"] == "an_unmatched_value_takes_the_default")
        assert vector["payload"].lower().replace(" ", "") == "097f"
        assert vector["expected"] == {"kind": 9, "fallback": 127}


class TestEveryEncoderReadsTheDefaultKey:
    """Read from source, so a removal shows here rather than as a floor break."""

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_consults_the_match_default(self, name):
        text = ENCODERS[name].read_text()
        assert re.search(r"(MatchDefault|getMatchDefault|match\.MatchDefault)", text), (
            f"{name} never reads the `default:` key"
        )

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_says_where_the_fix_came_from(self, name):
        assert "CR-2026-027" in ENCODERS[name].read_text(), name

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_the_default_is_only_taken_when_the_discriminator_is_known(self, name):
        """Otherwise a default claiming no names would beat a case that does."""
        text = ENCODERS[name].read_text()
        assert re.search(r"discriminator != nil|discriminator != null", text), name

    def test_the_claimable_heuristic_is_still_there(self):
        """It is what an inline match with no reported discriminator relies on."""
        assert "casePresent" in ENCODERS["Encoder.java"].read_text()
        assert "CasePresent" in ENCODERS["SchemaEncoder.cs"].read_text()


class TestJavaParsesTheDefaultOnce:
    """The encoder is handed Fields, so the conversion moved to the parser."""

    def test_the_parser_converts_the_list(self):
        text = (REPO_ROOT / "bindings" / "java" / "src" / "main" / "java" / "org" / "lora"
                / "schema" / "Schema.java").read_text()
        assert "setMatchDefault(\n                            parseFields(" in text \
            or "setMatchDefault(parseFields(" in text \
            or re.search(r"parseFields\(\(List<Map<String, Object>>\) declaredFields\)", text), (
                "Schema.java no longer parses the `default:` list at parse time"
            )

    def test_the_decoder_no_longer_parses_it_again(self):
        text = (REPO_ROOT / "bindings" / "java" / "src" / "main" / "java" / "org" / "lora"
                / "schema" / "Schema.java").read_text()
        assert "decodeFields((List<Field>) fallbackFields, ctx)" in text, (
            "decodeMatch should consume the already-parsed list"
        )


class TestTheFloorsMovedWithTheFix:
    @pytest.mark.parametrize("path", sorted(FLOORS, key=str))
    def test_the_match_floor_is_raised(self, path):
        _, _, shape = FLOORS[path]
        assert shape in path.read_text(), f"{path.name}: expected {shape}"

    @pytest.mark.parametrize("path", sorted(FLOORS, key=str))
    def test_the_total_floor_is_at_least_what_this_cr_reached(self, path):
        name, floor, _ = FLOORS[path]
        found = re.search(rf"{name}\s*=\s*(\d+)", path.read_text())
        assert found, f"{path.name}: no {name}"
        assert int(found.group(1)) >= floor, (path.name, found.group(1), floor)

    @pytest.mark.parametrize("path", sorted(FLOORS, key=str))
    def test_the_raise_is_explained(self, path):
        assert "CR-2026-027" in path.read_text(), path.name

    def test_the_python_floor_was_not_lowered(self):
        """The reference already encoded this; nothing about it changed here. A bound
        rather than an equality - CR-2026-030 raised it legitimately."""
        text = (REPO_ROOT / "tests" / "test_encode_round_trip.py").read_text()
        found = re.search(r"FLOOR_TOTAL = (\d+)", text)
        assert found and int(found.group(1)) >= 1160, found and found.group(1)


class TestWhatWasFoundHereAndFixedNext:
    """This class was the inverse when CR-2026-027 landed.

    It asserted Java and C# declared `u32le16` and had no encode case for it, so the gap
    could not go stale unnoticed. CR-2026-028 closed it; the assertions are turned round.
    """

    @pytest.mark.parametrize("name", ["Encoder.java", "SchemaEncoder.cs", "schema.go"])
    def test_every_encoder_handles_the_word_ordered_type(self, name):
        encoder = ENCODERS[name].read_text()
        assert re.search(r"U32LE16", encoder), (
            f"{name} lost its word-ordered encode case"
        )

    @pytest.mark.parametrize("name", ["Encoder.java", "SchemaEncoder.cs"])
    def test_the_fix_is_attributed(self, name):
        assert "CR-2026-028" in ENCODERS[name].read_text(), name
