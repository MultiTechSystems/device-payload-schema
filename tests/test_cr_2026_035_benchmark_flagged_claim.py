"""CR-2026-035: the stale `flagged` claim in the C benchmark, and what it was hiding.

`docs/SPEC-IMPLEMENTATION-STATUS.md` justified a simplified C benchmark frame by saying
the interpreter "has no `flagged` or `polynomial` support". CR-2026-034 gave it `flagged`,
so half that sentence was false. Fixing the sentence turned up three larger problems.

**The numbers were not reproducible.** No committed thing produced the 20.5M ops/s in that
table. `src/benchmark.cpp` looks like the source and is not: it times a small interpreter
defined inline in itself, and never includes `schema_interpreter.h`. A third figure, 32M
msg/s, sat in the same document's C entry. `tools/benchmark-c-interpreter.py` and
`make bench-c` now regenerate both rows, and the C schema is built from **the same YAML the
Python reference reads**, by reusing the corpus harness's `schema_source()` - a
hand-transcribed C copy can drift from the YAML, and then the two rows are not measuring
the same work. Measured: C 8.5M ops/s, Python 40K, ~210x, on a 15-field `flagged` frame.

**Ten cells of the feature matrix were wrong about C.** It claimed `polynomial`, `sqrt`,
`abs`, `pow`, `log`, `floor`/`ceiling`, `clamp`, all three `repeat` rows and `ports` - each
of which is zero occurrences in the header - and denied `var`, which the interpreter has
had all along and which `flagged` depends on. The test below re-derives the C column from
the header instead of hardcoding the corrections.

**And the harness's own wording had misled me.** Its skip reasons said `transform` and
`bitfield_string` were "not built by the struct API", which reads as a limit on the
harness. SESSION-NOTES.md and AGENTS.md both concluded from that "the next work on C is
widening the harness, not the interpreter", naming those two as harness limits whose status
was "unknown because the harness cannot build them". Backwards: they are the interpreter's
two largest gaps, 26 schemas and 24, and `grep -cw transform include/schema_interpreter.h`
was 0 the whole time. The reasons now say "the interpreter has no transform pipeline", and
both documents are corrected.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "SPEC-IMPLEMENTATION-STATUS.md"
HEADER = REPO_ROOT / "include" / "schema_interpreter.h"
BENCH = REPO_ROOT / "tools" / "benchmark-c-interpreter.py"
HARNESS = REPO_ROOT / "tools" / "c-corpus-harness.py"
NOTES = REPO_ROOT / "SESSION-NOTES.md"
AGENTS = REPO_ROOT / "AGENTS.md"
FRAME = REPO_ROOT / "schemas" / "devices" / "decentlab" / "dl-lid.yaml"

C_COLUMN = 4  # | Feature | Python | Java | Go | C | JS |


def doc_rows():
    """{row label: [cells]} for every 6-column matrix row in the document."""
    rows = {}
    for line in DOC.read_text().splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) == 6:
            rows.setdefault(cells[0], cells)
    return rows


class TestTheStaleClaimIsGone:
    def test_the_benchmark_no_longer_says_c_has_no_flagged(self):
        text = DOC.read_text()
        assert "the C interpreter has no `flagged`" not in text
        assert "has no `flagged` or" not in text

    def test_it_still_explains_why_dl_5tm_is_out_of_reach(self):
        """The frame is still simplified - for one reason now, not two."""
        section = DOC.read_text().split("### C Interpreter")[1].split("### Java")[0]
        assert "transform" in section and "polynomial" in section
        assert "CR-2026-034" in section, "the reason it changed should be traceable"

    def test_the_unreproducible_figures_are_gone_or_marked_historical(self):
        text = DOC.read_text()
        assert "32M msg/s" not in text, "a third, unsourced throughput figure"
        # 20.5M may still appear, but only as an explicitly superseded number.
        for match in re.finditer(r"20\.5M", text):
            window = text[max(0, match.start() - 260):match.start() + 120]
            assert "earlier numbers" in window or "not comparable" in window, window


class TestTheBenchmarkIsReproducible:
    def test_the_tool_exists_and_is_executable(self):
        assert BENCH.is_file()
        assert BENCH.stat().st_mode & 0o111, "not executable"

    def test_the_doc_names_the_command_that_regenerates_it(self):
        assert "make bench-c" in DOC.read_text()

    def test_there_is_a_make_target(self):
        text = (REPO_ROOT / "Makefile").read_text()
        assert re.search(r"^bench-c:", text, re.M)
        assert re.search(r"^\.PHONY:.*\bbench-c\b", text, re.M)

    def test_it_is_not_in_ci(self):
        """A measurement whose numbers depend on the machine does not belong in CI."""
        text = (REPO_ROOT / "Makefile").read_text()
        ci = next(l for l in text.splitlines() if l.startswith("ci:"))
        assert "bench-c" not in ci, ci

    def test_it_builds_the_c_schema_from_the_yaml_rather_than_a_copy(self):
        """A transcribed C schema can drift; then the two rows measure different work."""
        text = BENCH.read_text()
        assert "c-corpus-harness.py" in text
        assert "schema_source" in text

    def test_the_old_cpp_benchmark_is_not_the_source_of_these_numbers(self):
        """It never includes the header, which is why it could not have been."""
        cpp = (REPO_ROOT / "src" / "benchmark.cpp").read_text()
        assert "schema_interpreter.h" not in cpp
        assert "src/benchmark.cpp" in (REPO_ROOT / "Makefile").read_text()

    def test_the_loop_cannot_be_optimised_away(self):
        """Without a consumer for the results, -O2 may delete the loop entirely."""
        text = BENCH.read_text()
        assert "volatile" in text
        assert "sink" in text

    @pytest.mark.slow
    def test_it_runs_and_both_sides_decode_the_same_field_count(self, tmp_path):
        out = tmp_path / "b.json"
        done = subprocess.run(
            [sys.executable, str(BENCH), "--iterations", "20000",
             "--python-iterations", "300", "--rounds", "2", "--json", str(out)],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
        report = json.loads(out.read_text())
        assert report["flagged"] is True, "the frame must exercise flagged"
        assert report["c_fields_decoded"] == report["python_fields_decoded"], report
        assert report["c_fields_decoded"] >= 15, report
        assert report["c"]["ops_per_sec"] > report["python"]["ops_per_sec"]


class TestTheFrameActuallyExercisesFlagged:
    def test_the_default_frame_has_a_flagged_construct(self):
        schema = yaml.safe_load(FRAME.read_text())
        assert any(isinstance(f, dict) and "flagged" in f for f in schema["fields"])

    def test_it_has_no_transform_which_c_could_not_run(self):
        text = FRAME.read_text()
        assert "transform:" not in text and "polynomial:" not in text

    def test_the_doc_names_this_frame(self):
        section = DOC.read_text().split("### C Interpreter")[1].split("### Java")[0]
        assert "dl-lid" in section


class TestTheMatrixAgreesWithTheHeader:
    """Re-derived from the header, so a future capability change breaks this, not prose."""

    #: (matrix row label, keyword whose presence in the header means C supports it)
    DERIVED = [
        ("`polynomial`", "polynomial"),
        ("`sqrt`", "sqrt"),
        ("`abs`", "abs"),
        ("`pow`", "pow"),
        ("`clamp`", "clamp"),
        ("`type: repeat` (count)", "FIELD_TYPE_REPEAT"),
        ("`repeat` (count_field)", "FIELD_TYPE_REPEAT"),
        ("`repeat` (until: end)", "FIELD_TYPE_REPEAT"),
        ("`type: object`", "FIELD_TYPE_OBJECT"),
        ("`flagged`", "FIELD_TYPE_FLAGGED"),
        ("`tlv`", "FIELD_TYPE_TLV"),
    ]

    @pytest.mark.parametrize("label,keyword", DERIVED, ids=[d[0] for d in DERIVED])
    def test_the_c_cell_matches_the_header(self, label, keyword):
        rows = doc_rows()
        assert label in rows, f"no matrix row {label!r}; ids may have drifted"
        supported = re.search(rf"\b{re.escape(keyword)}\b", HEADER.read_text()) is not None
        cell = rows[label][C_COLUMN]
        claims = cell == "✓"
        assert claims == supported, (
            f"{label}: doc says C={cell!r} but the header "
            f"{'has' if supported else 'does not have'} {keyword!r}"
        )

    def test_variables_are_credited_now(self):
        """C has var_get/var_set/var_has, and `flagged` reads a mask through them."""
        assert "static inline bool var_has(" in HEADER.read_text()
        assert doc_rows()["`var` (variables)"][C_COLUMN] == "✓"

    def test_ports_are_not_credited(self):
        """The header says outright that it has no port selection."""
        assert "no port selection" in HEADER.read_text()
        assert doc_rows()["`ports` (fPort routing)"][C_COLUMN] == "-"


class TestTheSkipReasonsNameTheRightSide:
    """The wording that produced a backwards conclusion in two documents."""

    @pytest.fixture(scope="class")
    def report(self, tmp_path_factory):
        out = tmp_path_factory.mktemp("c35") / "r.json"
        done = subprocess.run([sys.executable, str(HARNESS), "--json", str(out)],
                              cwd=REPO_ROOT, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout[-2000:]
        return json.loads(out.read_text())

    def test_the_old_wording_is_gone(self):
        """Checked on the reason strings, not the file.

        The file still contains the phrase - in the comment explaining why it was
        wrong. Reading the source text flagged that comment, which is the fourth
        loose-anchor slip of this kind recorded in SESSION-NOTES.md, so this imports
        the table and inspects its values.
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("h35", HARNESS)
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)

        offenders = [(k, v) for k, v in harness.UNREACHABLE_KEYS.items()
                     if "not built by the struct API" in v]
        assert offenders == [], offenders
        # The one remaining "struct API" reason is explicit about being a C gap.
        for key, reason in harness.UNREACHABLE_KEYS.items():
            if "struct API" in reason:
                assert "C gap" in reason, (key, reason)

    def test_transform_is_reported_as_an_interpreter_gap(self, report):
        reasons = [r for r in report["skips"] if "transform" in r]
        assert reasons, sorted(report["skips"])
        assert all("interpreter" in r for r in reasons), reasons

    def test_it_is_the_largest_gap_and_that_is_visible(self, report):
        transform = max(v for k, v in report["skips"].items() if "transform" in k)
        assert transform >= 20, report["skips"]

    def test_the_measurement_did_not_regress(self, report):
        assert report["failures"] == [], report["failures"][:6]
        assert report["attempted"] >= 488, report["attempted"]
        assert report["attempted"] + report["skipped_vectors"] == report["corpus_vectors"]


class TestTheDocumentsNoLongerSayTheOpposite:
    def test_the_notes_do_not_call_transform_a_harness_limit(self):
        text = NOTES.read_text()
        assert "widening the harness, not the interpreter" not in text
        assert "the interpreter, and the two biggest items" in text

    def test_the_notes_record_that_it_was_knowable_by_grep(self):
        assert "grep -cw transform" in NOTES.read_text()

    def test_agents_no_longer_calls_them_harness_work(self):
        text = AGENTS.read_text()
        assert 'worth less than widening the harness to cover' not in text
        assert "largest *capability* gaps" in text

    def test_agents_no_longer_lists_ports_as_supported(self):
        """Normalised: the claim spans a wrapped line."""
        text = " ".join(AGENTS.read_text().split())
        assert "`$ref` and ports are named separately because C supports them" not in text
        assert "Ports are the other way round" in text
