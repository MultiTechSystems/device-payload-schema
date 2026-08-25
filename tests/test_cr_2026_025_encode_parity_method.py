"""CR-2026-025: how to compare encoders, and the claim that got it wrong.

Opened to bring the Python encoder's TLV round-trip up to Go's. **There was nothing to
bring up.** The gap did not exist, and this records why it looked like it did, because the
mistake is easy to repeat and cost a wrong line in AGENTS.md.

CR-2026-024 read two per-shape round-trip counts - Go's `tlv` at 910, Python's at 900 -
subtracted them, and concluded Go round-tripped ten vectors the reference could not, so the
reference's TLV case selection was the weaker of the two. Comparing the actual vector sets
says the opposite:

    Python fails 77.  Go fails 105.  Vectors Python fails and Go passes: 0.

Python's failures are a strict subset of Go's. The reference is ahead by 28.

The counts were never comparable. Each harness buckets a schema its own way -
`tests/test_encode_round_trip.py` serialises to JSON and looks for a `"tlv"` key; the Go,
Java and C# tests scan raw YAML for `tlv:`, and for `repeat` with no colon at all - and
their denominators differ by a vector. They are ratchets for one harness over time and
nothing else.

So this CR ships no encoder change. It ships `--list`, which emits `schema::vector` per
line so two implementations can be diffed by identity rather than by bucket size, the
correction to the record, and the tests below pinning the method.

The real gap is the reverse and is left to its own CR: of Go's 28, fourteen are
`word_ordered_sensor_id` in the `dl-*` schemas - the `u32le16` word-ordered type
(PS-271/PS-272) - and one is `match-default-fields.yaml`, a CR-2026-020 fixture Go decodes
and cannot re-encode.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "encode-round-trip.py"
AGENTS = REPO_ROOT / "AGENTS.md"
PY_HARNESS = REPO_ROOT / "tests" / "test_encode_round_trip.py"
GO_HARNESS = REPO_ROOT / "go" / "schema" / "corpus_encode_test.go"


def tool(*args):
    done = subprocess.run([sys.executable, str(TOOL), *args],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[:400]
    return done.stdout


class TestTheListModeExists:
    """The means of comparing by identity rather than by bucket size."""

    def test_it_prints_one_identifier_per_line(self):
        lines = [l for l in tool("--list").splitlines() if l.strip()]
        assert lines
        for line in lines:
            assert line.count("::") == 1, line
            schema, vector = line.split("::")
            assert schema.endswith(".yaml"), line
            assert vector, line

    def test_it_is_sorted_so_a_diff_is_readable(self):
        lines = [l for l in tool("--list").splitlines() if l.strip()]
        assert lines == sorted(lines)

    def test_it_lists_exactly_the_vectors_that_differ(self):
        listed = len([l for l in tool("--list").splitlines() if l.strip()])
        summary = tool()
        counts = {}
        for kind in ("length", "bytes", "error"):
            for line in summary.splitlines():
                if line.strip().endswith(f"{kind} differs"):
                    counts[kind] = int(line.split()[0])
        assert listed == sum(counts.values()), (listed, counts)

    def test_it_prints_nothing_else(self):
        """So the output can be diffed without filtering."""
        out = tool("--list")
        assert "ENCODE ROUND-TRIP" not in out
        assert "Reasons:" not in out


class TestTheMethodIsWrittenDown:
    """A warning nobody can find is a warning nobody reads."""

    def test_the_tool_says_not_to_subtract_shape_counts(self):
        doc = TOOL.read_text()
        assert "never the per-shape counts" in doc
        assert "--list" in doc

    def test_the_tool_records_the_false_conclusion(self):
        """The specific error, so the next reader recognises the shape of it."""
        doc = TOOL.read_text()
        assert "CR-2026-024" in doc
        assert "28" in doc

    def test_agents_md_carries_the_correction(self):
        text = AGENTS.read_text()
        assert "Never compare implementations by their per-shape round-trip counts" in text
        assert "CR-2026-025" in text

    def test_agents_md_no_longer_claims_go_is_ahead(self):
        # Whitespace-normalised: the correction wraps across lines in the document.
        text = " ".join(AGENTS.read_text().split())
        assert "so Go is *ahead*" not in text
        assert "The reference is ahead, not behind." in text


class TestTheHarnessesReallyDoBucketDifferently:
    """The premise of the warning, read from the source rather than asserted."""

    def test_python_matches_a_json_key(self):
        text = PY_HARNESS.read_text()
        assert "json.dumps(schema)" in text
        assert 'f\'"{key}"\' in text' in text, (
            "the JSON-key match is what makes this classifier differ from the others"
        )

    def test_go_matches_raw_yaml_with_colons(self):
        text = GO_HARNESS.read_text()
        assert '"tlv:"' in text

    def test_go_matches_repeat_without_a_colon(self):
        """The inconsistency inside Go's own list, which is part of why they diverge."""
        text = GO_HARNESS.read_text()
        assert '"repeat"' in text, "Go's shape list no longer has the colon-less entry"

    def test_the_two_classifiers_are_not_the_same_expression(self):
        """If they are ever unified, this test should be the one that fails."""
        assert "json.dumps" not in GO_HARNESS.read_text()


class TestNoEncoderChangeWasMade:
    """Stated so the absence reads as the finding, not an unfinished job."""

    def test_the_reference_encoder_is_untouched_by_this_cr(self):
        """`git diff` is the real check; this pins the claim the CR rests on."""
        interpreter = (REPO_ROOT / "tools" / "schema_interpreter.py").read_text()
        assert "CR-2026-025" not in interpreter, (
            "this CR claims no encoder change; the interpreter now cites it"
        )

    @pytest.mark.parametrize("path", [
        REPO_ROOT / "go" / "schema" / "schema.go",
        REPO_ROOT / "dotnet" / "PayloadSchema" / "SchemaEncoder.cs",
        REPO_ROOT / "bindings" / "java" / "src" / "main" / "java" / "org" / "lora"
        / "schema" / "Encoder.java",
    ])
    def test_no_other_encoder_was_touched_either(self, path):
        assert "CR-2026-025" not in path.read_text(), path.name
