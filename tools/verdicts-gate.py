#!/usr/bin/env python3
"""Five-implementation verdicts, vector by vector: the gate a pass count cannot be.

Each corpus runner compares its pass COUNT against a floor, so a vector that decodes
correctly in Python and differently in Go, Java or C# is invisible whenever the total
still clears the floor - how Go came to drop boolean-labelled lookups, and Go and C# to
skip byte_group member modifiers, with every suite green. This reads one per-vector
report per implementation and compares them vector by vector (AGENTS.md: "diff the
failing sets, not the totals").

  python   tools/corpus-report.py --impl python   (the reference interpreter)
  ts013    tools/corpus-report.py --impl ts013    (the generated TS013 codec, under node)
  go       go/schema/corpus_conformance_test.go                     with CORPUS_REPORT
  java     bindings/java/.../CorpusConformanceTest.java             with CORPUS_REPORT
  csharp   dotnet/PayloadSchema.Tests/CorpusConformanceTests.cs     with CORPUS_REPORT

Two rules, both failing the gate:

1. CHANGED SCHEMAS (--base REF, or --changed PATH...): every vector of a schema the
   change touches must pass in all five. The baseline does not exempt them - a schema
   being edited is the moment to make it agree everywhere.
2. THE WHOLE CORPUS: a vector that passes in Python and does not pass in another
   implementation is a divergence, and every divergence must be listed, with a reason,
   in tools/verdicts-baseline.json. The list is a ratchet in both directions: a new
   divergence fails, and a listed one that no longer occurs fails too, so the list only
   shrinks. A vector Python fails is reported but not gated here: the Python corpus suite
   already fails on it.

A vector with no payload (an encode vector, PS-047) is `skip` in every runner and is never
a divergence.

Usage:
    python3 tools/verdicts-gate.py --run                     # drive all five, gate
    python3 tools/verdicts-gate.py --run --base origin/master --only-changed
    python3 tools/verdicts-gate.py --reports-dir build/verdicts   # gate existing reports
    python3 tools/verdicts-gate.py --run --write-baseline    # record the current set
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "schemas" / "devices"
DEFAULT_REPORTS = REPO_ROOT / "build" / "verdicts"
DEFAULT_BASELINE = REPO_ROOT / "tools" / "verdicts-baseline.json"

REFERENCE = "python"
IMPLEMENTATIONS = ("python", "go", "java", "csharp", "ts013")
DOCKER_IMPLS = ("go", "java", "csharp")
PASS, FAIL, ERROR, SKIP, MISSING = "pass", "fail", "error", "skip", "missing"
LETTER = {PASS: ".", FAIL: "F", ERROR: "E", SKIP: "s", MISSING: "-"}

Key = Tuple[str, int]  # (schema relative to the corpus root, index in test_vectors)
Report = Dict[Key, dict]
Reports = Dict[str, Report]


# --------------------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------------------


def index_report(entries: Iterable[dict]) -> Report:
    return {(e["schema"], int(e["index"])): e for e in entries}


def load_report(path: Path) -> Report:
    return index_report(json.loads(path.read_text(encoding="utf-8")))


def status_of(reports: Reports, impl: str, key: Key) -> str:
    entry = reports.get(impl, {}).get(key)
    return entry["status"] if entry else MISSING


def vector_name(reports: Reports, key: Key) -> str:
    for impl in IMPLEMENTATIONS:
        entry = reports.get(impl, {}).get(key)
        if entry and entry.get("vector"):
            return entry["vector"]
    return "#%d" % key[1]


def all_keys(reports: Reports) -> List[Key]:
    keys = set()  # type: Set[Key]
    for report in reports.values():
        keys.update(report)
    return sorted(keys)


# --------------------------------------------------------------------------------------
# The two rules
# --------------------------------------------------------------------------------------


def changed_problems(reports: Reports, changed: Iterable[str]) -> List[str]:
    """Rule 1: every vector of a changed schema passes in every implementation.

    A vector the reference skips (no payload) is skipped everywhere and asserts nothing.
    """
    problems = []
    changed = set(changed)
    keys = [k for k in all_keys(reports) if k[0] in changed]
    for key in keys:
        reference = status_of(reports, REFERENCE, key)
        if reference == SKIP:
            continue
        for impl in IMPLEMENTATIONS:
            if impl not in reports:
                continue
            status = status_of(reports, impl, key)
            if status != PASS:
                entry = reports[impl].get(key) or {}
                problems.append(
                    "%s [%d] %s: %s %s%s"
                    % (
                        key[0],
                        key[1],
                        vector_name(reports, key),
                        impl,
                        status,
                        (" - " + entry["detail"]) if entry.get("detail") else "",
                    )
                )
    return problems


def divergences(reports: Reports) -> List[dict]:
    """Vectors the reference passes and some other implementation does not."""
    out = []
    for key in all_keys(reports):
        if status_of(reports, REFERENCE, key) != PASS:
            continue
        for impl in IMPLEMENTATIONS:
            if impl == REFERENCE or impl not in reports:
                continue
            status = status_of(reports, impl, key)
            if status == PASS:
                continue
            entry = reports[impl].get(key) or {}
            out.append(
                {
                    "schema": key[0],
                    "index": key[1],
                    "vector": vector_name(reports, key),
                    "impl": impl,
                    "status": status,
                    "detail": entry.get("detail", ""),
                }
            )
    return out


def reverse_divergences(reports: Reports) -> List[Key]:
    """Vectors the reference does not pass while another implementation does."""
    out = []
    for key in all_keys(reports):
        if status_of(reports, REFERENCE, key) in (PASS, SKIP):
            continue
        if any(
            status_of(reports, impl, key) == PASS
            for impl in IMPLEMENTATIONS
            if impl != REFERENCE and impl in reports
        ):
            out.append(key)
    return out


def baseline_key(item: dict) -> Tuple[str, int, str, str]:
    return (item["schema"], int(item["index"]), item.get("vector", ""), item["impl"])


def ratchet(
    found: Sequence[dict],
    baseline: Sequence[dict],
    in_scope: Optional[Set[str]] = None,
    impls: Optional[Set[str]] = None,
) -> Tuple[List[dict], List[dict], List[dict]]:
    """(new, gone, unexplained) for rule 2.

    `in_scope` limits the comparison to the schemas a restricted run covered, and `impls`
    to the implementations that reported; a listed divergence outside either was not
    measured, so it can be neither confirmed nor declared gone.
    """

    def measured(item: dict) -> bool:
        if in_scope is not None and item["schema"] not in in_scope:
            return False
        return impls is None or item["impl"] in impls

    listed = {baseline_key(b): b for b in baseline if measured(b)}
    current = {baseline_key(f): f for f in found}
    new = [current[k] for k in sorted(current) if k not in listed]
    gone = [listed[k] for k in sorted(listed) if k not in current]
    unexplained = [
        listed[k]
        for k in sorted(listed)
        if k in current and not str(listed[k].get("reason", "")).strip()
    ]
    return new, gone, unexplained


# --------------------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------------------

BASELINE_COMMENT = (
    "Known cross-implementation divergences: corpus vectors the Python reference "
    "interpreter passes and another implementation does not. Written by "
    "tools/verdicts-gate.py --write-baseline; every entry needs a reason. A ratchet: a "
    "divergence not listed here fails the gate, and a listed one that no longer occurs "
    "fails it too, so remove it. See tools/verdicts-gate.py."
)


def load_baseline(path: Path) -> List[dict]:
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("divergences", []))


def write_baseline(path: Path, found: Sequence[dict], previous: Sequence[dict]) -> None:
    reasons = {baseline_key(p): p.get("reason", "") for p in previous}
    items = []
    for item in sorted(found, key=baseline_key):
        items.append(
            {
                "schema": item["schema"],
                "index": item["index"],
                "vector": item["vector"],
                "impl": item["impl"],
                "status": item["status"],
                "detail": item["detail"],
                "reason": reasons.get(baseline_key(item), ""),
            }
        )
    body = {"_comment": BASELINE_COMMENT, "divergences": items}
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", "utf-8")


# --------------------------------------------------------------------------------------
# Changed schemas
# --------------------------------------------------------------------------------------


def git(*args: str) -> str:
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def changed_schemas(base: str) -> List[str]:
    """Corpus schemas that differ from the merge base with `base`, working tree included.

    Committed, staged, unstaged and untracked changes all count, so the rule applies the
    same locally as it does to a pull request. A deleted schema has nothing to run.
    """
    merge_base = git("merge-base", base, "HEAD").strip()
    names = git("diff", "--name-only", merge_base).split()
    names += git("ls-files", "--others", "--exclude-standard").split()
    prefix = "schemas/devices/"
    out = set()
    for name in names:
        if (
            name.startswith(prefix)
            and name.endswith(".yaml")
            and (REPO_ROOT / name).exists()
        ):
            out.add(name[len(prefix) :])
    return sorted(out)


# --------------------------------------------------------------------------------------
# Running the five
# --------------------------------------------------------------------------------------


def _corpus_report_module():
    spec = importlib.util.spec_from_file_location(
        "corpus_report", str(REPO_ROOT / "tools" / "corpus-report.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def docker_command(
    impl: str, report: str, only: Optional[str], corpus_root: Optional[Path]
) -> List[str]:
    """The Makefile's test-go / test-java / test-dotnet invocation, for one test only."""
    cache = REPO_ROOT / ".cache"
    docker = os.environ.get("DOCKER") or "docker"  # as the Makefile's DOCKER ?= docker
    command = [docker, "run", "--rm", "-v", "%s:/work" % REPO_ROOT]
    env = {"CORPUS_REPORT": report}
    if only:
        env["CORPUS_ONLY"] = only
    if corpus_root is not None:
        command += ["-v", "%s:/scratch:ro" % corpus_root.resolve()]
        env["CORPUS_ROOT"] = "/scratch"
    if impl == "go":
        command += [
            "-w",
            "/work/go/schema",
            "-v",
            "%s/go:/tmp/gocache" % cache,
            "-e",
            "GOFLAGS=-mod=mod",
            "-e",
            "GOCACHE=/tmp/gocache",
        ]
        image = "golang:1.22"
        # -count=1: the report is a side effect a cached result would not reproduce.
        test = "go test -count=1 -run '^TestCorpusConformance$' ."
    elif impl == "java":
        command += ["-w", "/work/bindings/java", "-v", "%s/m2:/root/.m2" % cache]
        image = "maven:3.9-eclipse-temurin-21"
        test = "mvn -B -q test -Dtest=CorpusConformanceTest"
    elif impl == "csharp":
        command += ["-w", "/work/dotnet", "-v", "%s/nuget:/root/.nuget" % cache]
        image = "mcr.microsoft.com/dotnet/sdk:8.0"
        test = (
            "dotnet test --nologo "
            "--filter FullyQualifiedName~PayloadSchema.Tests.CorpusConformanceTests"
        )
    else:
        raise ValueError(impl)
    for name, value in env.items():
        command += ["-e", "%s=%s" % (name, value)]
    # The container runs as root; hand the report back to whoever ran the gate.
    owner = "%d:%d" % (os.getuid(), os.getgid()) if hasattr(os, "getuid") else "0:0"
    script = '%s; rc=$?; chown %s "%s" 2>/dev/null; exit $rc' % (test, owner, report)
    return command + [image, "sh", "-c", script]


def run_all(
    out_dir: Path,
    impls: Sequence[str],
    only: Optional[List[str]],
    corpus_root: Optional[Path],
) -> Dict[str, float]:
    """Write every implementation's report into out_dir, in parallel. Returns timings."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        container_dir = "/work/" + out_dir.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        sys.exit("--reports-dir must be inside the repository to be visible to docker")
    only_text = ",".join(only) if only is not None else None
    timings = {}  # type: Dict[str, float]
    started = {}  # type: Dict[str, Tuple[float, subprocess.Popen, Path]]
    for impl in impls:
        if impl not in DOCKER_IMPLS:
            continue
        target = out_dir / ("%s.json" % impl)
        if target.exists():
            target.unlink()
        log = (out_dir / ("%s.log" % impl)).open("w")
        command = docker_command(
            impl, "%s/%s.json" % (container_dir, impl), only_text, corpus_root
        )
        started[impl] = (
            time.time(),
            subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT),
            out_dir / ("%s.log" % impl),
        )

    reporter = _corpus_report_module()
    root = corpus_root if corpus_root is not None else CORPUS
    only_set = set(only) if only is not None else None
    for impl in impls:
        if impl in DOCKER_IMPLS:
            continue
        began = time.time()
        entries = reporter.build_report(impl, root, only_set)
        reporter.write_report(entries, out_dir / ("%s.json" % impl))
        timings[impl] = time.time() - began

    for impl, (began, process, log) in started.items():
        code = process.wait()
        timings[impl] = time.time() - began
        if not (out_dir / ("%s.json" % impl)).exists():
            print(
                "%s: runner exited %d and wrote no report; see %s" % (impl, code, log)
            )
        elif code != 0:
            # A floor failure still writes the report, and the report is what is gated.
            print("%s: runner exited %d (report written; see %s)" % (impl, code, log))
    return timings


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------


def print_table(reports: Reports, keys: Sequence[Key], limit: int = 80) -> None:
    impls = [i for i in IMPLEMENTATIONS if i in reports]
    print("  %-58s %s" % ("schema [index] vector", " ".join("%-6s" % i for i in impls)))
    for key in keys[:limit]:
        label = "%s [%d] %s" % (key[0], key[1], vector_name(reports, key))
        cells = " ".join("%-6s" % LETTER[status_of(reports, i, key)] for i in impls)
        print("  %-58s %s" % (label[:58], cells))
    if len(keys) > limit:
        print("  ... and %d more" % (len(keys) - limit))
    print("  (. pass  F fail  E error  s skip  - missing)")


def describe(item: dict) -> str:
    return "%s [%d] %s: %s %s - %s" % (
        item["schema"],
        item["index"],
        item["vector"],
        item["impl"],
        item["status"],
        item.get("detail", ""),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--run",
        action="store_true",
        help="Produce the reports (docker for Go, Java and C#) first",
    )
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--base", help="Git ref; schemas changed since it must agree")
    parser.add_argument(
        "--changed",
        nargs="*",
        help="Schema paths to treat as changed, instead of --base",
    )
    parser.add_argument(
        "--only-changed",
        action="store_true",
        help="With --run, run only the changed schemas (CORPUS_ONLY)",
    )
    parser.add_argument(
        "--impls",
        default=",".join(IMPLEMENTATIONS),
        help="Comma-separated implementations (default: all five)",
    )
    parser.add_argument(
        "--corpus-root", help="Walk this directory instead of schemas/devices"
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Record the current divergences, keeping existing reasons",
    )
    args = parser.parse_args(argv)

    impls = [i.strip() for i in args.impls.split(",") if i.strip()]
    if REFERENCE not in impls:
        parser.error("the reference implementation (python) is required")
    reports_dir = Path(args.reports_dir)
    corpus_root = Path(args.corpus_root) if args.corpus_root else None

    changed = None  # type: Optional[List[str]]
    if args.changed is not None:
        changed = sorted(
            {p.replace("\\", "/").split("schemas/devices/")[-1] for p in args.changed}
        )
    elif args.base:
        changed = changed_schemas(args.base)
    if changed is not None:
        print("changed schemas: %d" % len(changed))
        for name in changed:
            print("  " + name)

    only = changed if args.only_changed else None
    if args.only_changed and changed is None:
        parser.error("--only-changed needs --base or --changed")

    if args.run:
        if only == []:
            print("no changed schemas: nothing to run")
            return 0
        began = time.time()
        timings = run_all(reports_dir, impls, only, corpus_root)
        print(
            "reports written in %.0fs (%s)"
            % (
                time.time() - began,
                ", ".join("%s %.0fs" % (i, timings[i]) for i in impls if i in timings),
            )
        )

    reports = {}  # type: Reports
    for impl in impls:
        path = reports_dir / ("%s.json" % impl)
        if not path.exists():
            print("FAIL: no report for %s at %s" % (impl, path))
            return 1
        reports[impl] = load_report(path)
        if only is not None:
            reports[impl] = {k: v for k, v in reports[impl].items() if k[0] in only}

    ok = True
    tally = []
    for impl in impls:
        counts = {}  # type: Dict[str, int]
        for entry in reports[impl].values():
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        tally.append(
            "%s %d/%d"
            % (
                impl,
                counts.get(PASS, 0),
                sum(v for s, v in counts.items() if s != SKIP),
            )
        )
    print("passing vectors: " + ", ".join(tally))

    # Rule 1
    if changed is not None:
        problems = changed_problems(reports, changed)
        if problems:
            ok = False
            print(
                "\nFAIL: %d vector(s) of changed schemas do not pass in all of %s:"
                % (len(problems), ", ".join(impls))
            )
            for line in problems:
                print("  " + line)
        else:
            print(
                "changed schemas: every vector passes in all of %s" % ", ".join(impls)
            )

    # Rule 2
    found = divergences(reports)
    diverging = sorted({(d["schema"], d["index"]) for d in found})
    print(
        "\n%d vector(s) the reference passes and another implementation does not:"
        % len(diverging)
    )
    if diverging:
        print_table(reports, diverging)
    reverse = reverse_divergences(reports)
    if reverse:
        print(
            "\n%d vector(s) the reference does not pass and another does (not gated here;"
            " the Python corpus suite fails on these):" % len(reverse)
        )
        print_table(reports, reverse)

    baseline_path = Path(args.baseline)
    previous = load_baseline(baseline_path)
    if args.write_baseline:
        if (
            only is not None
            or corpus_root is not None
            or set(impls) != set(IMPLEMENTATIONS)
        ):
            parser.error(
                "--write-baseline needs a full run of all five over the corpus"
            )
        write_baseline(baseline_path, found, previous)
        print(
            "\nbaseline written: %d divergence(s) -> %s" % (len(found), baseline_path)
        )
        missing = [d for d in load_baseline(baseline_path) if not d.get("reason")]
        if missing:
            print("  %d entr(ies) have no reason yet; add one to each" % len(missing))
        return 0

    scope = set(only) if only is not None else None
    if corpus_root is not None:
        # A scratch corpus shares no schemas with the baseline.
        scope = {k[0] for k in all_keys(reports)}
    new, gone, unexplained = ratchet(found, previous, scope, set(impls))
    if new:
        ok = False
        print(
            "\nFAIL: %d new divergence(s), not in %s:" % (len(new), baseline_path.name)
        )
        for item in new:
            print("  " + describe(item))
    if gone:
        ok = False
        print(
            "\nFAIL: %d listed divergence(s) no longer occur - remove them from %s:"
            % (len(gone), baseline_path.name)
        )
        for item in gone:
            print("  " + describe(item))
    if unexplained:
        ok = False
        print("\nFAIL: %d listed divergence(s) have no reason:" % len(unexplained))
        for item in unexplained:
            print("  " + describe(item))

    print("\nverdicts gate: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
