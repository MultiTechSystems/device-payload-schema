"""CR-2026-036: the `metadata` enrichment block, specified and made honest.

The meta-schema had described a top-level `metadata` block, `encode_formula` on a field and
`input_metadata` on a test vector since the initial release. No clause of the specification
defined any of them, and the gap was invisible because clause 9 *did* describe resolving
`$recvTime` from a TS013 input object - the mechanism `metadata.include` consumes. Half the
feature was specified; the half that uses it was not.

PS-309 to PS-325 describe it as OPTIONAL, which is true in two senses. Only the Python
reference implements it - Go, Java, C#, the TS013 generator and the C interpreter have
nothing, and no corpus schema used it. And it is optional in kind rather than only in
practice: the block contributes no bytes, reads no payload and runs after decoding, so an
implementation that ignores it decodes every field identically.

**Three defects, all silent, found by probing the implementation in order to describe it:**

1. An enriched key overwrote a decoded payload field. An `include` entry named for a
   decoded field replaced it, so a `u16` decoding to 60 came back as an ISO timestamp
   string. PS-313 gives the decoded field precedence.
2. A missing runtime value emitted a null. `mode: rx_time` with no `recvTime` produced
   `measured_at: None`, which no consumer can tell from a device that reported nothing.
   PS-314 requires the key to be omitted.
3. Four swallowed exceptions produced neither a value nor a warning.

**Why the fixture asserts only the payload.** `metadata-enrichment.yaml` lists the two
decoded fields in `expected` and not the enriched keys. Asserting an OPTIONAL feature in
the shared corpus would fail four conformant implementations; the fixture's job is PS-310
and PS-311 - that a schema carrying the block still decodes its fields identically
everywhere - and the enrichment semantics are checked here, on the one implementation that
has them.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

FIXTURE = (REPO_ROOT / "schemas" / "devices" / "_language-conformance"
           / "metadata-enrichment.yaml")
RECV = {"recvTime": "2026-08-26T12:00:00Z"}


def schema(**metadata):
    base = {"name": "t", "version": 1, "fields": [{"name": "age", "type": "u16"}]}
    if metadata:
        base["metadata"] = metadata
    return base


def decode(sch, payload="003c", meta=RECV):
    return SchemaInterpreter(sch).decode(bytes.fromhex(payload), input_metadata=meta)


class TestADecodedFieldWinsACollision:
    """PS-313. This silently replaced the decoded value."""

    def test_the_decoded_value_survives(self):
        r = decode(schema(include=[{"name": "age", "source": "$recvTime"}]))
        assert r.data["age"] == 60, r.data

    def test_and_it_says_so(self):
        r = decode(schema(include=[{"name": "age", "source": "$recvTime"}]))
        assert any("age" in w and "decoded field" in w for w in r.warnings), r.warnings

    def test_a_timestamp_cannot_clobber_a_field_either(self):
        r = decode(schema(timestamps=[{"name": "age", "mode": "rx_time"}]))
        assert r.data["age"] == 60, r.data


class TestAnUnresolvedValueIsOmittedNotNulled:
    """PS-314. `measured_at: None` was indistinguishable from a silent device."""

    def test_a_missing_receive_time_omits_the_key(self):
        r = decode(schema(timestamps=[{"name": "t", "mode": "rx_time"}]), meta={})
        assert "t" not in r.data, r.data

    def test_no_null_reaches_the_output(self):
        r = decode(schema(timestamps=[{"name": "t", "mode": "rx_time"}]), meta={})
        assert None not in r.data.values(), r.data

    def test_a_malformed_receive_time_warns_rather_than_swallowing(self):
        r = decode(schema(timestamps=[{"name": "t", "mode": "subtract",
                                       "offset_field": "age"}]),
                   meta={"recvTime": "not-a-time"})
        assert "t" not in r.data
        assert r.warnings, "the exception was swallowed with no warning"

    def test_a_missing_source_field_warns(self):
        r = decode(schema(timestamps=[{"name": "t", "mode": "unix_epoch",
                                       "field": "nope"}]))
        assert "t" not in r.data
        assert any("nope" in w for w in r.warnings), r.warnings

    def test_an_unknown_mode_warns(self):
        r = decode(schema(timestamps=[{"name": "t", "mode": "elapsed_to_absolut"}]))
        assert "t" not in r.data
        assert any("mode" in w for w in r.warnings), r.warnings


class TestTheModesDeriveWhatTheSpecificationSays:
    def test_rx_time_is_the_receive_time(self):
        r = decode(schema(timestamps=[{"name": "t", "mode": "rx_time"}]))
        assert r.data["t"] == RECV["recvTime"]

    def test_elapsed_to_absolute_subtracts(self):
        r = decode(schema(timestamps=[{"name": "t", "mode": "elapsed_to_absolute",
                                       "elapsed_field": "age"}]))
        assert r.data["t"].startswith("2026-08-26T11:59:00"), r.data

    def test_offset_field_is_accepted_as_the_older_spelling(self):
        """PS-318."""
        r = decode(schema(timestamps=[{"name": "t", "mode": "elapsed_to_absolute",
                                       "offset_field": "age"}]))
        assert r.data["t"].startswith("2026-08-26T11:59:00"), r.data

    def test_time_base_defaults_to_rx_time(self):
        """PS-319: the entry above names no `time_base` and still resolves."""
        r = decode(schema(timestamps=[{"name": "t", "mode": "elapsed_to_absolute",
                                       "elapsed_field": "age"}]))
        assert "t" in r.data

    def test_an_unknown_time_base_omits_rather_than_guessing(self):
        r = decode(schema(timestamps=[{"name": "t", "mode": "elapsed_to_absolute",
                                       "elapsed_field": "age",
                                       "time_base": "gps_time"}]))
        assert "t" not in r.data
        assert r.warnings

    def test_derived_times_are_utc(self):
        """PS-320."""
        r = decode(schema(timestamps=[{"name": "t", "mode": "elapsed_to_absolute",
                                       "elapsed_field": "age"}]))
        assert r.data["t"].endswith("Z"), r.data


class TestNoRuntimeInputMeansNoEnrichment:
    """PS-312."""

    def test_the_block_is_ignored_entirely(self):
        sch = schema(include=[{"name": "received_at", "source": "$recvTime"}])
        r = SchemaInterpreter(sch).decode(bytes.fromhex("003c"))
        assert r.data == {"age": 60}, r.data
        assert r.warnings == [], r.warnings

    def test_the_payload_decodes_the_same_either_way(self):
        """PS-311: enrichment changes no decoded value."""
        sch = schema(timestamps=[{"name": "t", "mode": "rx_time"}])
        with_meta = SchemaInterpreter(sch).decode(bytes.fromhex("003c"),
                                                 input_metadata=RECV)
        without = SchemaInterpreter(schema()).decode(bytes.fromhex("003c"))
        assert with_meta.data["age"] == without.data["age"]
        assert with_meta.bytes_consumed == without.bytes_consumed


class TestAMalformedBlockIsRejected:
    """PS-315 to PS-317. Nothing validated the block at all before this CR."""

    @pytest.mark.parametrize("md,fragment", [
        ({"include": [{"name": "x"}]}, "source"),
        ({"include": [{"source": "$recvTime"}]}, "name"),
        ({"timestamps": [{"mode": "rx_time"}]}, "name"),
        ({"timestamps": [{"name": "t"}]}, "mode"),
        ({"timestamps": [{"name": "t", "mode": "nope"}]}, "unknown mode"),
        ({"timestamps": [{"name": "t", "mode": "subtract"}]}, "offset_field"),
        ({"timestamps": [{"name": "t", "mode": "unix_epoch"}]}, "field"),
        ({"timestamps": [{"name": "t", "mode": "elapsed_to_absolute"}]}, "elapsed_field"),
    ])
    def test_it_is_reported(self, md, fragment):
        errors = validate_schema_structure(schema(**md))
        assert any(fragment in e for e in errors), (errors, fragment)

    def test_a_well_formed_block_is_accepted(self):
        errors = validate_schema_structure(schema(
            include=[{"name": "r", "source": "$recvTime"}],
            timestamps=[{"name": "t", "mode": "elapsed_to_absolute",
                         "elapsed_field": "age"}]))
        assert [e for e in errors if "metadata" in e] == [], errors

    def test_a_schema_with_no_block_is_untouched(self):
        assert [e for e in validate_schema_structure(schema())
                if "metadata" in e] == []


class TestTheFixtureProvesPS310AcrossImplementations:
    def test_it_exists_and_carries_a_metadata_block(self):
        loaded = yaml.safe_load(FIXTURE.read_text())
        assert "metadata" in loaded
        assert loaded["metadata"]["timestamps"][0]["mode"] == "elapsed_to_absolute"

    def test_expected_lists_only_payload_fields(self):
        """Asserting the OPTIONAL enrichment here would fail four conformant runners."""
        loaded = yaml.safe_load(FIXTURE.read_text())
        payload_names = {f["name"] for f in loaded["fields"]}
        for vector in loaded["test_vectors"]:
            extra = set(vector["expected"]) - payload_names
            assert not extra, f"{vector['name']} asserts enriched keys: {extra}"

    def test_a_vector_carries_runtime_input(self):
        """PS-324: otherwise the block is never reached on any path."""
        loaded = yaml.safe_load(FIXTURE.read_text())
        assert any(v.get("input_metadata") for v in loaded["test_vectors"])

    def test_one_vector_deliberately_has_none(self):
        """PS-312 needs the negative case as much as the positive one."""
        loaded = yaml.safe_load(FIXTURE.read_text())
        assert any("input_metadata" not in v for v in loaded["test_vectors"])

    def test_every_vector_decodes_on_the_interpreted_path(self):
        loaded = yaml.safe_load(FIXTURE.read_text())
        for vector in loaded["test_vectors"]:
            result = SchemaInterpreter(loaded).decode(
                bytes.fromhex(vector["payload"]),
                input_metadata=vector.get("input_metadata"))
            assert not result.errors, (vector["name"], result.errors)
            assert result.warnings == [], (vector["name"], result.warnings)
            for key, want in vector["expected"].items():
                assert result.data[key] == want, (vector["name"], key, result.data)

    def test_the_conformance_runner_passes_the_runtime_input(self):
        """Wired in this CR; without it PS-324 is unreachable by any vector."""
        text = (REPO_ROOT / "tools" / "vector-verdicts.py").read_text()
        assert "input_metadata=vector.get" in text


class TestBothConformancePathsStillAgree:
    @pytest.mark.slow
    def test_the_corpus_verdicts_are_clean(self):
        done = subprocess.run([sys.executable, "tools/vector-verdicts.py"],
                              cwd=REPO_ROOT, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout[-2500:]
        assert "0 vectors where the two paths disagree" in done.stdout, done.stdout[-1500:]
