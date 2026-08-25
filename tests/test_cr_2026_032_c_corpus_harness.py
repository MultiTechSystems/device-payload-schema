"""CR-2026-032: measure the C interpreter against the corpus.

Opened to add TLV to the C interpreter. **The scope was wrong and this is the CR that had
to come first**, because nothing measured that interpreter at all: its four dedicated test
files were in no build target, `src/selftest_schema.c` exercises hand-built schemas rather
than corpus vectors, and `grep corpus src/*.c` returned nothing. Adding a large construct
to an implementation with no cross-check would have produced a feature I could only verify
against tests I also wrote.

`tools/c-corpus-harness.py` generates C that builds each expressible corpus schema through
the struct API (`field_u8("x")`, `schema_add_field()` - there is no YAML reader and the
header parses no binary format), compiles it once, runs it, and compares the output against
the vectors' `expected` with the same `values_match` the other runners use.

**The result: 50 of 50 attempted vectors decode exactly as the corpus expects.** The C
interpreter is not wrong about what it supports. It supports 4% of the corpus, and that is
the finding - 1189 of 1239 vectors are in schemas the struct API cannot build:

    79  no tlv field type       <- closed by CR-2026-033
    34  no flagged field type
     3  no constructor for type 'repeat'
     1  no enum/lookup default in the struct API (a known C gap)

plus schemas this *harness* cannot build though C could - inline `match`, `byte_group`,
`object`, `$ref`, ports. Those are named separately on purpose: a harness limitation
reported as a C gap would be a lie in the direction that flatters the harness.

**Five things looked like C defects and none was.** The first run reported five failures.
Three were bugs in this harness - `bytes` printed through the integer branch (the union
member matters), a `skip` built and never added to the schema, so every field after it read
from the padding's offset - one was the known `default` representational gap, and two more
were `test_comprehensive.c`'s stale little-endian assertions, which I probed directly and
found the interpreter decodes correctly. Every one was caught by checking rather than
reporting. That is the whole reason this CR exists in this order.

`make test-c` now builds and runs `test_interpreter.c`, `test_binary_schema.c` and
`test_encoder.c` - all previously orphaned, all passing - and then the harness.
`test_comprehensive.c` stays out with its reason recorded: 22 of its 160 assertions encode
the pre-CR-2026-009 lookup and enum behaviour that PS-105/PS-269 deliberately changed.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tools" / "c-corpus-harness.py"
MAKEFILE = REPO_ROOT / "Makefile"

#: What the harness reached when this CR landed. A floor, not an equality - adding corpus
#: vectors or widening the expressible subset raises it, and must not break this.
ATTEMPTED_FLOOR = 50

#: Constructs the C interpreter has no field type for. Distinct from what the harness
#: cannot build, and the distinction is the point.
#: `no tlv field type` was here until CR-2026-033 added one; the interpreter now decodes
#: the construct and the harness builds it, so it is no longer a reason at all.
C_GAPS = ("no flagged field type", "no constructor for type 'repeat'")


def harness(*args):
    done = subprocess.run([sys.executable, str(HARNESS), *args],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    return done.stdout


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("c") / "report.json"
    harness("--json", str(out))
    return json.loads(out.read_text())


class TestTheHarnessRunsAndTheCInterpreterIsCorrect:
    def test_it_compiles_and_runs(self, report):
        assert report["attempted"] > 0, "nothing was attempted, so nothing was measured"

    def test_every_attempted_vector_passes(self, report):
        assert report["failures"] == [], report["failures"][:6]

    def test_it_attempted_at_least_what_this_cr_reached(self, report):
        assert report["attempted"] >= ATTEMPTED_FLOOR, report["attempted"]

    def test_passed_equals_attempted(self, report):
        assert report["passed"] == report["attempted"], (
            report["passed"], report["attempted"])


class TestTheGapIsCoverageNotCorrectness:
    """The finding: C is right about what it supports and supports very little."""

    def test_some_of_the_corpus_is_still_unreachable(self, report):
        """That there is a gap, not how wide it is.

        This asserted `skipped > attempted * 10` when CR-2026-032 landed - true then, at
        1189 against 50, and false as soon as CR-2026-033 added tlv and took it to 786
        against 453. A ratio measured on the day is no more an invariant than a floor
        total is; the accounting test below is the durable one.
        """
        assert report["skipped_vectors"] > 0
        assert report["skips"], "unreachable vectors with no reason recorded"

    def test_the_corpus_total_is_accounted_for(self, report):
        assert report["attempted"] + report["skipped_vectors"] == report["corpus_vectors"]

    @pytest.mark.parametrize("gap", C_GAPS)
    def test_each_missing_field_type_is_reported(self, gap, report):
        assert gap in report["skips"], sorted(report["skips"])

    def test_tlv_is_no_longer_a_reason_at_all(self):
        """It was the largest when this CR landed; CR-2026-033 removed it."""
        out = harness()
        assert "no tlv field type" not in out, out

    def test_harness_limits_are_named_apart_from_c_gaps(self, report):
        """A harness limitation reported as a C gap would flatter the harness."""
        harness_limited = [r for r in report["skips"] if "this harness" in r]
        assert harness_limited, sorted(report["skips"])
        for reason in harness_limited:
            assert reason not in C_GAPS


class TestTheKnownGapsAreNotReportedAsDefects:
    def test_the_lookup_default_is_a_skip_not_a_failure(self, report):
        """AGENTS.md records it: no slot for it in the struct."""
        assert any("default" in r for r in report["skips"]), sorted(report["skips"])
        assert not any("default" in f["detail"] for f in report["failures"])

    def test_the_harness_says_why_it_skips_rather_than_approximating(self):
        doc = HARNESS.read_text()
        assert "would report a pass for something the interpreter" in doc

    def test_the_harness_records_its_own_early_bugs(self):
        """So the next reader knows the failure modes it already had."""
        doc = HARNESS.read_text()
        assert "this harness's own" in doc
        assert "schema_add_field" in doc


class TestTheOrphanedCTestsAreBuiltNow:
    def test_there_is_a_make_target(self):
        assert re.search(r"^test-c:", MAKEFILE.read_text(), re.M)

    def test_it_is_in_ci(self):
        text = MAKEFILE.read_text()
        ci = next(l for l in text.splitlines() if l.startswith("ci:"))
        assert "test-c" in ci, ci

    def test_it_is_phony(self):
        assert re.search(r"^\.PHONY:.*\btest-c\b", MAKEFILE.read_text(), re.M)

    @pytest.mark.parametrize("name", ["test_interpreter", "test_binary_schema",
                                      "test_encoder"])
    def test_the_passing_tests_are_listed(self, name):
        assert name in MAKEFILE.read_text()

    def test_the_failing_one_is_excluded_with_a_reason(self):
        text = MAKEFILE.read_text()
        assert "test_comprehensive" in text, "the exclusion should be recorded, not silent"
        assert "C_TESTS = test_interpreter test_binary_schema test_encoder" in text
        assert "stale expectations" in text

    def test_the_target_runs_the_harness_too(self):
        text = MAKEFILE.read_text()
        start = text.index("test-c:")
        assert "c-corpus-harness.py" in text[start:start + 900]
