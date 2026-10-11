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

import functools
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "check-floors.py"


@functools.lru_cache(maxsize=None)
def run_python_floors():
    """`check-floors.py --python` on the tree as it is: (exit code, stdout, stderr).

    It re-encodes the whole corpus (about 45 seconds), and two tests ask for exactly this
    run, so it is made once per process and both are kept on one xdist worker.
    """
    done = subprocess.run([sys.executable, str(TOOL), "--python"],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    return done.returncode, done.stdout, done.stderr


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
        """`Map.of(... "repeat", N)` ends in ')' not ','; requiring a comma dropped it.

        Found, not pinned: the floor itself moves (6 -> 5 in 0.5.2 wave 1)."""
        assert mod.read_floors("java").get("shape repeat") is not None

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
    @pytest.mark.xdist_group("floor-checker-python-run")
    def test_the_output_says_so(self):
        code, out, err = run_python_floors()
        assert code == 0, out + err
        assert "never compared across implementations" in out

    def test_the_docstring_records_why(self):
        text = TOOL.read_text()
        assert "not comparable" in text
        assert "900" in text and "910" in text, "the concrete mistake should be named"


class TestTheTwoFailureDirections:
    """Each declares a floor in a copy of the Python floor file, read via --floor-root.

    These used to rewrite tests/test_encode_round_trip.py in the working tree and restore
    it in a `finally`. Under pytest-xdist two of them could interleave: one read the other's
    rewritten file as its "original" and restored that, leaving a wrong floor written to
    disk. A copy cannot race.
    """

    SOURCE = REPO_ROOT / "tests" / "test_encode_round_trip.py"

    def _run(self, *args):
        done = subprocess.run([sys.executable, str(TOOL), "--python", *args],
                              cwd=REPO_ROOT, capture_output=True, text=True)
        return done.returncode, done.stdout

    def _floor_root(self, tmp_path, rewrite):
        original = self.SOURCE.read_text()
        declared = int(re.search(r"FLOOR_TOTAL = (\d+)", original).group(1))
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_encode_round_trip.py").write_text(
            re.sub(r"FLOOR_TOTAL = \d+", f"FLOOR_TOTAL = {rewrite(declared)}", original))
        return str(tmp_path)

    @pytest.mark.xdist_group("floor-checker-python-run")
    def test_a_floor_at_its_actual_passes(self):
        code, out, _ = run_python_floors()
        assert code == 0, out
        assert "sits exactly at its own implementation's actual" in out

    def test_a_floor_below_actual_is_reported_but_does_not_fail(self, tmp_path):
        """Loose is a judgement, not a defect: leaving headroom can be deliberate.

        The lowered floor is derived from the declared one rather than written as a
        literal, so the assertion is about the gap being reported and not about a
        running total. Pinning both the floor and the "N unlocked" it implies made this
        test fail the moment the corpus grew by a vector, which is the trap AGENTS.md
        names for CR-specific tests and which applies here for the same reason.
        """
        gap = 6
        root = self._floor_root(tmp_path, lambda declared: declared - gap)
        code, out = self._run("--floor-root", root)
        assert code == 0, out
        assert "loose" in out and f"{gap} unlocked" in out

    def test_a_floor_below_actual_fails_under_loose(self, tmp_path):
        """--loose makes the same gap an exit 1, for a caller that wants floors exact.

        Separate from the test above only so the two corpus runs proceed in parallel.
        """
        root = self._floor_root(tmp_path, lambda declared: declared - 6)
        code_strict, out = self._run("--floor-root", root, "--loose")
        assert code_strict == 1, out

    def test_a_floor_above_actual_always_fails(self, tmp_path):
        """That is a regression: the ratchet is asserting something untrue."""
        # Above whatever the actual is: a fixed number stops being "above" once the
        # corpus grows past it, which is how this test broke at 1232.
        before = self.SOURCE.read_text()
        root = self._floor_root(tmp_path, lambda declared: declared + 10000)
        code, out = self._run("--floor-root", root)
        assert code == 1, out
        assert "REGRESSION" in out
        # The floor file in the tree is the one CI reads; a test must never edit it.
        assert self.SOURCE.read_text() == before


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


RUN_LOGGED = REPO_ROOT / "tools" / "run-logged.py"

#: Far in the future, so no file in the tree can be newer than a log claiming it.
FUTURE = 99999999999.0


def _log(started, body, exit_code=0):
    text = f"# run-logged started={started:.3f} cwd=/work\n# run-logged command: x\n{body}"
    if exit_code is not None:
        text += f"# run-logged exit={exit_code}\n"
    return text


GO_BODY = (
    "=== RUN   TestCorpusConformance\n"
    "    corpus_conformance_test.go:287: corpus vectors: 2555 total, 2555 passed, 0 failed\n"
    "    corpus_encode_test.go:192: tlv          round-trips=910   length=11\n"
    "    corpus_encode_test.go:195: total round-trips: 1763 of 2555 vectors decoded\n"
    "    corpus_encode_test.go:286: plain (unordered) round-trips: 1740 of 2555\n"
)


class TestItReadsTheLanguageLogs:
    """`--from-logs`: the counts come from what `make test-<lang>` already printed.

    A log stands in for running the suite only while it describes the tree as it is, so
    each refusal below is a case where using the log would report a stale number.
    """

    def test_a_complete_fresh_log_supplies_the_actuals(self, mod, tmp_path):
        path = tmp_path / "go.log"
        path.write_text(_log(FUTURE - 1, GO_BODY))
        actuals, why = mod.read_log(path, (FUTURE - 2, "schemas/x.yaml"))
        assert actuals == {"decode total": 2555, "encode total": 1763,
                           "encode plain": 1740, "shape tlv": 910}
        assert "exit 0" in why

    def test_a_missing_log_is_refused(self, mod, tmp_path):
        actuals, why = mod.read_log(tmp_path / "go.log", (0.0, ""))
        assert actuals is None and "no log" in why

    def test_an_unfinished_run_is_refused(self, mod, tmp_path):
        path = tmp_path / "go.log"
        path.write_text(_log(FUTURE, GO_BODY, exit_code=None))
        actuals, why = mod.read_log(path, (0.0, ""))
        assert actuals is None and "incomplete" in why

    def test_a_log_without_the_header_is_refused(self, mod, tmp_path):
        path = tmp_path / "go.log"
        path.write_text(GO_BODY + "# run-logged exit=0\n")
        actuals, why = mod.read_log(path, (0.0, ""))
        assert actuals is None and "header" in why

    def test_a_source_edited_after_the_run_began_makes_it_stale(self, mod, tmp_path):
        path = tmp_path / "go.log"
        path.write_text(_log(1000.0, GO_BODY))
        actuals, why = mod.read_log(path, (1001.0, "go/schema/schema.go"))
        assert actuals is None
        assert "stale" in why and "go/schema/schema.go" in why

    def test_a_log_with_no_counts_is_refused(self, mod, tmp_path):
        """A container that never started still exits and writes a trailer."""
        path = tmp_path / "go.log"
        path.write_text(_log(FUTURE, "docker: Cannot connect to the Docker daemon\n", 125))
        actuals, why = mod.read_log(path, (0.0, ""))
        assert actuals is None and "no corpus counts" in why

    def test_a_failed_run_still_supplies_its_counts(self, mod, tmp_path):
        """A floor regression fails the suite; check-floors must still report it."""
        path = tmp_path / "go.log"
        path.write_text(_log(FUTURE, GO_BODY, exit_code=1))
        actuals, why = mod.read_log(path, (0.0, ""))
        assert actuals["decode total"] == 2555 and "exit 1" in why

    def test_the_source_tree_excludes_ignored_output(self, mod):
        mtime, rel = mod.newest_source()
        assert 0 < mtime < float("inf")
        assert rel and not rel.startswith(("build/", ".cache/", ".venv/"))

    def test_the_tool_uses_a_fresh_log_instead_of_docker(self, mod, tmp_path):
        """End to end, on Go: floors at the logged actuals pass with no suite run."""
        floors = mod.read_floors("go")
        body = "corpus vectors: {0} total, {0} passed, 0 failed\n".format(
            floors["decode total"])
        body += "total round-trips: {} of 9999 vectors decoded\n".format(
            floors["encode total"])
        body += "plain (unordered) round-trips: {} of 9999\n".format(
            floors["encode plain"])
        for name, value in floors.items():
            if name.startswith("shape "):
                body += "{:<12} round-trips={:<5} length=0\n".format(name[6:], value)
        (tmp_path / "go.log").write_text(_log(FUTURE, body))
        done = subprocess.run(
            [sys.executable, str(TOOL), "--go", "--from-logs", "--log-dir", str(tmp_path)],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "go: actuals read from" in done.stdout
        assert "not measured" not in done.stdout
        assert "sits exactly at its own implementation's actual" in done.stdout


class TestRunLogged:
    def _run(self, tmp_path, *command):
        log = tmp_path / "sub" / "x.log"
        done = subprocess.run([sys.executable, str(RUN_LOGGED), str(log), "--", *command],
                              capture_output=True, text=True)
        return done, log

    def test_output_is_streamed_and_kept_with_header_and_trailer(self, tmp_path):
        done, log = self._run(tmp_path, sys.executable, "-c", "print('hello')")
        assert done.returncode == 0
        assert "hello" in done.stdout
        text = log.read_text()
        assert text.startswith("# run-logged started=")
        assert "hello\n" in text
        assert text.endswith("# run-logged exit=0\n")
        assert not (log.parent / "x.log.partial").exists()

    def test_the_exit_status_is_the_commands(self, tmp_path):
        done, log = self._run(tmp_path, sys.executable, "-c",
                              "import sys; print('boom', file=sys.stderr); sys.exit(3)")
        assert done.returncode == 3
        assert "boom" in log.read_text()
        assert log.read_text().endswith("# run-logged exit=3\n")


class TestTheLanguageTargetsKeepTheirLogs:
    """The counts check-floors parses only print under these exact options."""

    @pytest.fixture(scope="class")
    def makefile(self):
        return (REPO_ROOT / "Makefile").read_text()

    @staticmethod
    def _recipe(text, target):
        m = re.search(rf"^{target}:.*\n((?:\t.*\n)+)", text, re.M)
        assert m, target
        return m.group(1)

    @pytest.mark.parametrize("target,log", [("test-go", "go.log"),
                                            ("test-java", "java.log"),
                                            ("test-dotnet", "dotnet.log")])
    def test_each_suite_writes_its_log(self, makefile, target, log):
        assert f"$(RUN_LOGGED) $(LANG_LOG_DIR)/{log} --" in self._recipe(makefile, target)

    def test_go_prints_its_t_log_lines(self, makefile):
        assert "go test -v ./..." in self._recipe(makefile, "test-go")

    def test_dotnet_prints_its_console_output(self, makefile):
        assert "verbosity=detailed" in self._recipe(makefile, "test-dotnet")

    def test_verify_runs_each_suite_once_then_reads_the_logs(self, makefile):
        recipe = self._recipe(makefile, "verify")
        assert "$(MAKE) test-languages" in recipe
        assert "check-floors.py --from-logs" in recipe

    def test_the_log_dir_is_the_tools_default(self, mod, makefile):
        assert re.search(r"^LANG_LOG_DIR = build/lang-logs$", makefile, re.M)
        assert mod.LOG_DIR == REPO_ROOT / "build" / "lang-logs"


class TestWhatCountsAsASourceChange:
    """newest_source() on a scratch repository, so the mtimes are ours to set."""

    @pytest.fixture
    def repo(self, tmp_path):
        import os
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        (tmp_path / ".gitignore").write_text("bin/\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.txt").write_text("a")
        (tmp_path / "src" / "b.txt").write_text("b")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        for rel in (".gitignore", "src/a.txt", "src/b.txt", "src"):
            os.utime(tmp_path / rel, (1000, 1000))
        return tmp_path

    def test_the_newest_file_is_reported(self, mod, repo):
        import os
        os.utime(repo / "src" / "b.txt", (2000, 2000))
        assert mod.newest_source(repo) == (2000, "src/b.txt")

    def test_ignored_build_output_beside_the_sources_does_not_count(self, mod, repo):
        """dotnet test makes bin/ and obj/ next to its project: that is not an edit."""
        (repo / "src" / "bin").mkdir()
        (repo / "src" / "bin" / "out.dll").write_text("x")
        mtime, rel = mod.newest_source(repo)
        assert mtime == 1000 and rel != "src"

    def test_a_deleted_tracked_file_counts_through_its_directory(self, mod, repo):
        (repo / "src" / "b.txt").unlink()
        mtime, rel = mod.newest_source(repo)
        assert rel == "src" and mtime > 2000
