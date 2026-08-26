"""The floor checker reads what is declared and what is reached, per implementation.

Corpus floors are ratchets: each asserts a count never drops. There are 32 of them across
four languages in four syntaxes, and reading them by eye is how they go wrong. It happened
twice in one day - a claim that Go's TLV encode beat the reference, repeated across four
pull requests, and later a claim that the `tlv` floor was loose at "900 against an actual
of 910". Both were per-shape numbers taken from different implementations and treated as
one. AGENTS.md forbids that comparison at the point it was first made; this tool exists so
a program does the reading.

These tests cover the parsing rather than the numbers, because the numbers are the thing
the tool measures. Three of the four parsing bugs found while writing it were silent - a
floor read as "not measured" rather than an error - which is the failure mode worth pinning.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "check-floors.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("cf", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestItFindsEveryDeclaredFloor:
    @pytest.mark.parametrize("impl,count", [("python", 7), ("go", 9),
                                            ("java", 8), ("dotnet", 8)])
    def test_the_expected_number_of_floors_is_read(self, mod, impl, count):
        """A floor the tool cannot see is a floor nobody checks."""
        assert len(mod.read_floors(impl)) == count, mod.read_floors(impl)

    def test_every_implementation_declares_a_tlv_shape_floor(self, mod):
        for impl in ("python", "go", "java", "dotnet"):
            assert "shape tlv" in mod.read_floors(impl), impl

    def test_java_repeat_is_found_despite_the_closing_paren(self, mod):
        """`Map.of(... "repeat", 6)` ends in ')' not ','; requiring a comma dropped it."""
        assert mod.read_floors("java").get("shape repeat") == 6

    def test_the_declared_values_differ_between_implementations(self, mod):
        """The premise of the whole tool: these are not one number.

        Go's tlv floor is 910, Python's and Java's are 900, C#'s is 901 - each at its own
        actual, because each harness buckets schemas its own way.
        """
        tlv = {i: mod.read_floors(i)["shape tlv"] for i in
               ("python", "go", "java", "dotnet")}
        assert len(set(tlv.values())) > 1, tlv


class TestItParsesEachHarnessOutputShape:
    def test_a_go_style_line_with_a_file_prefix(self, mod):
        """Go's t.Log prefixes every line, which defeated an anchored regex silently."""
        out = mod.parse_log(
            "    corpus_encode_test.go:178: total round-trips: 1176 of 1243 vectors\n"
            "    corpus_encode_test.go:175: tlv          round-trips=910   length=11\n")
        assert out["encode total"] == 1176
        assert out["shape tlv"] == 910

    def test_a_bare_line_with_a_leading_space(self, mod):
        """C# prints without a prefix; an anchor tightened for Go then dropped this one."""
        out = mod.parse_log(" total round-trips: 1167 of 1243 vectors decoded\n"
                            " tlv          round-trips=901   length=11\n")
        assert out["encode total"] == 1167
        assert out["shape tlv"] == 901

    def test_the_decode_count_is_the_passed_column(self, mod):
        out = mod.parse_log("corpus vectors: 1253 total, 1242 passed, 11 failed\n")
        assert out["decode total"] == 1242

    def test_gos_plain_path_is_kept_separate_from_its_ordered_one(self, mod):
        out = mod.parse_log(
            "  corpus_encode_test.go:178: total round-trips: 1176 of 1243 vectors\n"
            "  corpus_encode_test.go:267: plain (unordered) round-trips: 1167 of 1242\n")
        assert out["encode total"] == 1176
        assert out["encode plain"] == 1167

    def test_an_unrecognised_shape_is_ignored(self, mod):
        assert mod.parse_log(" nonsense     round-trips=99\n") == {}


class TestItRefusesToCompareAcrossImplementations:
    def test_the_output_says_so(self):
        done = subprocess.run([sys.executable, str(TOOL), "--python"],
                              cwd=REPO_ROOT, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "never compared across implementations" in done.stdout

    def test_the_docstring_records_why(self):
        text = TOOL.read_text()
        assert "not comparable" in text
        assert "900" in text and "910" in text, "the concrete mistake should be named"


class TestTheTwoFailureDirections:
    def _run(self, *args):
        done = subprocess.run([sys.executable, str(TOOL), "--python", *args],
                              cwd=REPO_ROOT, capture_output=True, text=True)
        return done.returncode, done.stdout

    def test_a_floor_at_its_actual_passes(self):
        code, out = self._run()
        assert code == 0, out
        assert "sits exactly at its own implementation's actual" in out

    def test_a_floor_below_actual_is_reported_but_does_not_fail(self, tmp_path):
        """Loose is a judgement, not a defect: leaving headroom can be deliberate."""
        target = REPO_ROOT / "tests" / "test_encode_round_trip.py"
        original = target.read_text()
        try:
            target.write_text(re.sub(r"FLOOR_TOTAL = \d+", "FLOOR_TOTAL = 1160", original))
            code, out = self._run()
            assert code == 0, out
            assert "loose" in out and "6 unlocked" in out
            code_strict, _ = self._run("--loose")
            assert code_strict == 1
        finally:
            target.write_text(original)

    def test_a_floor_above_actual_always_fails(self):
        """That is a regression: the ratchet is asserting something untrue."""
        target = REPO_ROOT / "tests" / "test_encode_round_trip.py"
        original = target.read_text()
        try:
            target.write_text(re.sub(r"FLOOR_TOTAL = \d+", "FLOOR_TOTAL = 1200", original))
            code, out = self._run()
            assert code == 1, out
            assert "REGRESSION" in out
        finally:
            target.write_text(original)


class TestItIsWiredUp:
    def test_there_are_make_targets(self):
        text = (REPO_ROOT / "Makefile").read_text()
        assert re.search(r"^check-floors:", text, re.M)
        assert re.search(r"^check-floors-python:", text, re.M)
        assert re.search(r"^\.PHONY:.*\bcheck-floors\b", text, re.M)

    def test_it_is_not_in_ci(self):
        """The full run drives four Docker toolchains and takes minutes."""
        text = (REPO_ROOT / "Makefile").read_text()
        ci = next(l for l in text.splitlines() if l.startswith("ci:"))
        assert "check-floors" not in ci, ci
