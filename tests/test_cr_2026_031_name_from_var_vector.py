"""CR-2026-031: a witness for the PS-267 fallback CR-2026-030 added.

CR-2026-030 gave all four encoders a `name_from` resolver, with a fallback through the
field declaring `var: ref` because PS-267 says a variable's name and its field's are
distinct - rbs30x has `name: event_type` with `var: evt`. **Nothing exercised that
fallback.** The corpus's one `name_from` referenced `${idx}` on a field also called `idx`,
so a resolver that ignored `var:` entirely and treated the reference as a field name would
have passed it.

`name-from-var.yaml` closes that: `name: channel_id` with `var: idx`, and a template
reading `channel_${idx}_reading`. Decoding resolves against variables, where `idx` exists;
encoding is handed output whose only key is `channel_id`, so it has to get from the
variable name to the declaring field or write a zero.

**It passed on all five paths first time.** The fallback was correct in every
implementation, so this vector confirms rather than exposes - worth saying plainly rather
than dressing a passing test as a find. What it buys is that the fallback can no longer be
deleted, or quietly reduced to a direct data lookup, without something failing.

    decode   1237 -> 1239 everywhere
    encode   reference 1161 -> 1163   plain fixed 59 -> 61
             Go        1171 -> 1173   (plain path 1162 -> 1164)
             Java      1161 -> 1163
             C#        1162 -> 1164

No floor assertion in any earlier CR's tests broke when those rose, which is the first time
that has been true this session - CR-2026-030 converted the last of them from equalities to
bounds.
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402

FIXTURE = (REPO_ROOT / "schemas" / "devices" / "_language-conformance"
           / "name-from-var.yaml")


def schema():
    return yaml.safe_load(FIXTURE.read_text())


class TestTheFixtureIsTheWitnessItClaimsToBe:
    """If these drift, the fallback silently stops being covered again."""

    def test_it_exists(self):
        assert FIXTURE.is_file()

    def test_the_var_name_differs_from_the_field_name(self):
        """The whole point: `idx` is not a field name anywhere in the schema."""
        loaded = schema()
        source = next(f for f in loaded["fields"] if f.get("var"))
        assert source["var"] == "idx"
        assert source["name"] == "channel_id"
        assert source["name"] != source["var"], (
            "the names agree again, so a resolver ignoring `var:` would pass"
        )

    def test_no_field_is_called_idx(self):
        names = {f.get("name") for f in schema()["fields"]}
        assert "idx" not in names

    def test_the_template_references_the_var(self):
        loaded = schema()
        templated = next(f for f in loaded["fields"] if f.get("name_from"))
        assert templated["name_from"] == "channel_${idx}_reading"

    def test_the_decoded_output_has_no_key_the_template_names(self):
        """Which is why encoding cannot resolve it by a direct data lookup."""
        decoded = SchemaInterpreter(schema()).decode(bytes.fromhex("032a"))
        assert "idx" not in decoded.data
        assert set(decoded.data) == {"channel_id", "channel_3_reading"}

    def test_two_vectors_with_different_channels(self):
        """One channel would let a hard-coded key pass."""
        vectors = schema()["test_vectors"]
        keys = set()
        for vector in vectors:
            keys |= {k for k in vector["expected"] if k.startswith("channel_")
                     and k != "channel_id"}
        assert keys == {"channel_3_reading", "channel_7_reading"}, keys


class TestItRoundTripsOnBothPythonSidePaths:
    @pytest.mark.parametrize("payload,key,value", [("032a", "channel_3_reading", 42),
                                                   ("072a", "channel_7_reading", 42)])
    def test_the_interpreter_decodes_and_re_encodes(self, payload, key, value):
        loaded = schema()
        decoded = SchemaInterpreter(loaded).decode(bytes.fromhex(payload))
        assert decoded.data[key] == value, decoded.data
        encoded = SchemaInterpreter(loaded).encode(decoded.data)
        assert not encoded.errors, encoded.errors
        assert encoded.warnings == [], encoded.warnings
        assert bytes(encoded.payload) == bytes.fromhex(payload)

    @pytest.mark.parametrize("payload,key", [("032a", "channel_3_reading"),
                                             ("072a", "channel_7_reading")])
    def test_the_generated_codec_decodes_the_same_key(self, payload, key):
        import json
        import subprocess
        import tempfile

        js = TS013Generator(schema()).generate()
        body = ",".join(str(b) for b in bytes.fromhex(payload))
        harness = (js + f"\nvar _r = decodeUplink({{fPort: 1, bytes: [{body}]}});"
                   + "\nconsole.log(JSON.stringify(_r.data));")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(harness)
            path = fh.name
        try:
            done = subprocess.run(["node", path], capture_output=True, text=True)
        finally:
            Path(path).unlink(missing_ok=True)
        assert done.returncode == 0, done.stderr[:300]
        assert key in json.loads(done.stdout), done.stdout


class TestTheFallbackCannotBeQuietlyRemoved:
    """A resolver reduced to a direct data lookup must fail, not pass."""

    def test_a_direct_lookup_alone_would_not_resolve_this(self):
        """Demonstrates what the fallback is for, without asserting on source text."""
        decoded = SchemaInterpreter(schema()).decode(bytes.fromhex("032a"))
        template = "channel_${idx}_reading"
        references = re.findall(r"\$\{(\w+)}", template)
        assert references == ["idx"]
        assert all(ref not in decoded.data for ref in references), (
            "a direct data lookup would now succeed, so this fixture proves nothing"
        )

    @pytest.mark.parametrize("name,path", [
        ("schema_interpreter.py", REPO_ROOT / "tools" / "schema_interpreter.py"),
        ("schema.go", REPO_ROOT / "go" / "schema" / "schema.go"),
        ("Encoder.java", REPO_ROOT / "bindings" / "java" / "src" / "main" / "java" / "org"
         / "lora" / "schema" / "Encoder.java"),
        ("SchemaEncoder.cs", REPO_ROOT / "dotnet" / "PayloadSchema" / "SchemaEncoder.cs"),
    ])
    def test_each_encoder_still_has_the_fallback(self, name, path):
        text = path.read_text()
        assert re.search(r"(_field_declaring_var|sibling\.Var|sibling\.getVar)", text), name


class TestTheFloorsRose:
    #: (file, floor name, the value this CR reached). Bounds, per AGENTS.md.
    FLOORS = [
        (REPO_ROOT / "tests" / "test_encode_round_trip.py", "FLOOR_TOTAL", 1163),
        (REPO_ROOT / "go" / "schema" / "corpus_encode_test.go", "encodeFloorTotal", 1173),
        (REPO_ROOT / "go" / "schema" / "corpus_encode_test.go",
         "encodePlainFloorTotal", 1164),
        (REPO_ROOT / "go" / "schema" / "corpus_conformance_test.go", "corpusFloor", 1239),
        (REPO_ROOT / "bindings" / "java" / "src" / "test" / "java" / "org" / "lora"
         / "schema" / "CorpusEncodeRoundTripTest.java", "ENCODE_FLOOR_TOTAL", 1163),
        (REPO_ROOT / "bindings" / "java" / "src" / "test" / "java" / "org" / "lora"
         / "schema" / "CorpusConformanceTest.java", "CORPUS_FLOOR", 1239),
        (REPO_ROOT / "dotnet" / "PayloadSchema.Tests" / "CorpusEncodeRoundTripTests.cs",
         "EncodeFloorTotal", 1164),
        (REPO_ROOT / "dotnet" / "PayloadSchema.Tests" / "CorpusConformanceTests.cs",
         "CorpusFloor", 1239),
    ]

    @pytest.mark.parametrize("entry", FLOORS, ids=lambda e: f"{e[0].name}:{e[1]}")
    def test_the_floor_is_at_least_what_this_cr_reached(self, entry):
        path, name, floor = entry
        found = re.search(rf"{name}\s*=\s*(\d+)", path.read_text())
        assert found, f"{path.name}: no {name}"
        assert int(found.group(1)) >= floor, (path.name, name, found.group(1), floor)

    def test_the_shape_floors_rose_together(self):
        for path, prefix in [
            (REPO_ROOT / "tests" / "test_encode_round_trip.py", '"plain fixed": '),
            (REPO_ROOT / "go" / "schema" / "corpus_encode_test.go", '"plain fixed": '),
            (REPO_ROOT / "bindings" / "java" / "src" / "test" / "java" / "org" / "lora"
             / "schema" / "CorpusEncodeRoundTripTest.java", '"plain fixed", '),
            (REPO_ROOT / "dotnet" / "PayloadSchema.Tests" / "CorpusEncodeRoundTripTests.cs",
             '["plain fixed"] = '),
        ]:
            found = re.search(re.escape(prefix) + r"(\d+)", path.read_text())
            assert found, f"{path.name}: no {prefix!r}"
            assert int(found.group(1)) >= 61, (path.name, found.group(1))
