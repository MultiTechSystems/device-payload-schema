"""CR-2026-030: resolve a `name_from` template on encode, in all four implementations.

The last entry in `tools/encode-round-trip.py`'s residue that was recoverable rather than
lost. `name_from` reports a field under a templated key - `channel_3_reading` for a
schema-declared `reading` (PS-265, PS-266) - and every encoder looked the value up under
the declared name, found nothing, and wrote a zero. `name-from.yaml` re-encoded `032a` as
`0300`.

Not silent, but misleading: the warning read `Missing field: reading`, naming a key the
schema never reports. It names the resolved key now.

Decoding resolves the template against its variables. Encoding is handed the decoded
output, keyed by field name, and has none - so a `${ref}` is looked for in the data first,
and failing that through a field declaring `var: ref`, whose name is often not the
variable's (PS-267). The reference searches the whole schema for that field, as its decode
side does; Go, Java and C# search the field list they already hold, which covers a
template referencing a sibling and needs no new plumbing. No corpus schema declares a
`name_from` whose reference resolves any other way - there is one `name_from` in the
corpus, referencing `${idx}`, whose `var` and field name agree.

An unresolvable template is now an error rather than a zero byte, on all four.

    reference  1160 -> 1161     plain fixed 58 -> 59
    Go         1170 -> 1171     plain fixed 58 -> 59  (plain path 1161 -> 1162)
    Java       1160 -> 1161     plain fixed 58 -> 59
    C#         1161 -> 1162     plain fixed 58 -> 59

With this the residue is 76, and `tools/encode-round-trip.py` classifies every one of them
as information the decode does not carry. Nothing recoverable is left in it.
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

FIXTURE = next((REPO_ROOT / "schemas").rglob("name-from.yaml"))

ENCODERS = {
    "schema_interpreter.py": REPO_ROOT / "tools" / "schema_interpreter.py",
    "schema.go": REPO_ROOT / "go" / "schema" / "schema.go",
    "Encoder.java": (REPO_ROOT / "bindings" / "java" / "src" / "main" / "java" / "org"
                     / "lora" / "schema" / "Encoder.java"),
    "SchemaEncoder.cs": REPO_ROOT / "dotnet" / "PayloadSchema" / "SchemaEncoder.cs",
}


def schema():
    return yaml.safe_load(FIXTURE.read_text())


class TestTheFixtureRoundTripsOnTheReference:
    def test_it_decodes_under_the_templated_key(self):
        decoded = SchemaInterpreter(schema()).decode(bytes.fromhex("032a"))
        assert decoded.data == {"idx": 3, "channel_3_reading": 42}

    def test_it_re_encodes_exactly(self):
        loaded = schema()
        decoded = SchemaInterpreter(loaded).decode(bytes.fromhex("032a"))
        encoded = SchemaInterpreter(loaded).encode(decoded.data)
        assert not encoded.errors, encoded.errors
        assert bytes(encoded.payload) == bytes.fromhex("032a"), bytes(encoded.payload).hex()

    def test_it_warns_about_nothing(self):
        """The old warning named `reading`, a key the schema never reports."""
        loaded = schema()
        decoded = SchemaInterpreter(loaded).decode(bytes.fromhex("032a"))
        encoded = SchemaInterpreter(loaded).encode(decoded.data)
        assert encoded.warnings == [], encoded.warnings

    def test_a_missing_reference_is_an_error_not_a_zero_byte(self):
        loaded = schema()
        # `idx` absent, so `channel_${idx}_reading` cannot be rebuilt.
        result = SchemaInterpreter(loaded).encode({"channel_3_reading": 42})
        assert result.errors, "an unresolvable template must not encode as zero"
        assert "name_from" in result.errors[0], result.errors

    def test_a_missing_value_names_the_resolved_key(self):
        """The warning has to name the key a caller would actually supply."""
        loaded = schema()
        result = SchemaInterpreter(loaded).encode({"idx": 7})
        assert any("channel_7_reading" in w for w in result.warnings), result.warnings


class TestEveryEncoderResolvesTheTemplate:
    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_has_an_encode_side_resolver(self, name):
        text = ENCODERS[name].read_text()
        assert re.search(r"(_resolve_encode_name|resolveEncodeName|ResolveEncodeName)", text), (
            f"{name} has no encode-side name_from resolution"
        )

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_the_fix_is_attributed(self, name):
        assert "CR-2026-030" in ENCODERS[name].read_text(), name

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_falls_back_to_the_declaring_var(self, name):
        """PS-267: a `var`'s name is often not its field's."""
        text = ENCODERS[name].read_text()
        assert re.search(r"(_field_declaring_var|sibling\.Var|sibling\.getVar|sibling\.Var ==)",
                         text), f"{name} resolves only a direct data key"

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_it_cites_the_requirement(self, name):
        text = ENCODERS[name].read_text()
        assert "PS-267" in text, name

    @pytest.mark.parametrize("name", sorted(ENCODERS))
    def test_the_decode_side_resolver_is_untouched(self, name):
        """Two resolvers, deliberately: they resolve against different sources."""
        text = ENCODERS[name].read_text()
        if name == "schema_interpreter.py":
            assert "_resolve_field_name" in text
        elif name == "schema.go":
            assert "func resolveFieldName(" in text


class TestTheResidueHasNothingRecoverableLeft:
    """The point of the CR: the classified residue is now all genuine loss."""

    def test_the_tool_still_explains_everything(self):
        import subprocess
        done = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "encode-round-trip.py")],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr[:400]
        assert re.search(r"0\s+unexplained", done.stdout), done.stdout[-600:]

    def test_no_templated_name_is_left_in_the_residue(self):
        import subprocess
        done = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "encode-round-trip.py")],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert "templated-name" not in done.stdout, (
            "a name_from vector is still failing to re-encode"
        )

    def test_the_tool_still_knows_the_reason_exists(self):
        """Removed from the residue, not from the vocabulary - it can recur."""
        text = (REPO_ROOT / "tools" / "encode-round-trip.py").read_text()
        assert "templated-name" in text


class TestTheFloorsMovedWithTheFix:
    #: (file, floor-name, value this CR reached, shape prefix, shape value)
    FLOORS = [
        (REPO_ROOT / "tests" / "test_encode_round_trip.py",
         "FLOOR_TOTAL", 1161, '"plain fixed": ', 59),
        (REPO_ROOT / "go" / "schema" / "corpus_encode_test.go",
         "encodeFloorTotal", 1171, '"plain fixed": ', 59),
        (REPO_ROOT / "bindings" / "java" / "src" / "test" / "java" / "org" / "lora"
         / "schema" / "CorpusEncodeRoundTripTest.java",
         "ENCODE_FLOOR_TOTAL", 1161, '"plain fixed", ', 59),
        (REPO_ROOT / "dotnet" / "PayloadSchema.Tests" / "CorpusEncodeRoundTripTests.cs",
         "EncodeFloorTotal", 1162, '["plain fixed"] = ', 59),
    ]

    @pytest.mark.parametrize("entry", FLOORS, ids=lambda e: e[0].name)
    def test_the_total_is_at_least_what_this_cr_reached(self, entry):
        path, name, floor, _, _ = entry
        found = re.search(rf"{name}\s*=\s*(\d+)", path.read_text())
        assert found, f"{path.name}: no {name}"
        assert int(found.group(1)) >= floor, (path.name, found.group(1), floor)

    @pytest.mark.parametrize("entry", FLOORS, ids=lambda e: e[0].name)
    def test_the_shape_is_at_least_what_this_cr_reached(self, entry):
        """A bound on the bucket too. Pinning it exactly is what CR-2026-024's test did,
        and this CR broke it - the bucket is as much a running value as the total."""
        path, _, _, prefix, floor = entry
        found = re.search(re.escape(prefix) + r"(\d+)", path.read_text())
        assert found, f"{path.name}: no {prefix!r}"
        assert int(found.group(1)) >= floor, (path.name, found.group(1), floor)

    def test_the_go_plain_path_floor_moved_too(self):
        text = (REPO_ROOT / "go" / "schema" / "corpus_encode_test.go").read_text()
        found = re.search(r"encodePlainFloorTotal\s*=\s*(\d+)", text)
        assert found and int(found.group(1)) >= 1162, found and found.group(1)
