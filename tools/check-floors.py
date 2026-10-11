#!/usr/bin/env python3
"""Print every corpus floor beside the actual its own implementation reaches.

The floors are ratchets: each asserts that a count never drops. They live in seven places
across four languages, in four syntaxes, and reading them by eye is how they go wrong.

**A floor is only ever compared with an actual from the same implementation.** This is not
tidiness. Each harness buckets schemas its own way - the Python tool serialises to JSON and
looks for a `tlv` key while the Go, Java and C# tests scan raw YAML - so the same shape name
counts different vectors in different implementations, and the numbers are not comparable.
AGENTS.md records the rule at the point where it was first broken: a claim that Go's TLV
encode beat the reference, repeated across four pull requests, was per-shape counts
subtracted across harnesses. It was made again after that: `tlv` was reported loose at "900
against an actual of 910", which was Python's floor read next to Go's actual, both of which
were exactly at their own actuals. This tool exists so that reading is done by a program.

The output is grouped by implementation and never totals or differences anything across
them. Ask for one implementation, or all four.

    python3 tools/check-floors.py                    # every implementation
    python3 tools/check-floors.py --python           # just the fast one
    python3 tools/check-floors.py --loose            # exit 1 if any floor sits below actual
    python3 tools/check-floors.py --json PATH        # machine-readable
    python3 tools/check-floors.py --from-logs        # reuse `make test-languages` logs

Running Go, Java and C# means running their toolchains in Docker, which takes a couple of
minutes. `--python` needs nothing but this repository.

`--from-logs` reads the output `make test-go`, `make test-java` and `make test-dotnet`
already keep under build/lang-logs/ (see tools/run-logged.py), so a verification pass runs
each suite once rather than twice; `make verify` does exactly that. A log is used only if
it is complete and no file in the source tree changed after its run began. Otherwise that
one implementation falls back to running its suite, as it would without the flag, and the
report says where each implementation's actuals came from.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Where `make test-go`, `make test-java` and `make test-dotnet` keep their output.
LOG_DIR = REPO / "build" / "lang-logs"

#: The floor declarations are read from here. Only tests change it (--floor-root), so they
#: can declare a floor in a copy rather than rewriting a file in the working tree - which,
#: with the suite running in parallel, another worker could read or restore mid-change.
FLOOR_ROOT = REPO

#: Where each floor is declared, and the pattern that reads its value.
#: (implementation, floor name, file, regex with one numeric group)
FLOOR_SITES = [
    ("python", "encode total", "tests/test_encode_round_trip.py",
     r"FLOOR_TOTAL\s*=\s*(\d+)"),
    ("go", "decode total", "go/schema/corpus_conformance_test.go",
     r"corpusFloor\s*=\s*(\d+)"),
    ("go", "encode total", "go/schema/corpus_encode_test.go",
     r"encodeFloorTotal\s*=\s*(\d+)"),
    ("go", "encode plain", "go/schema/corpus_encode_test.go",
     r"encodePlainFloorTotal\s*=\s*(\d+)"),
    ("java", "decode total",
     "bindings/java/src/test/java/org/lora/schema/CorpusConformanceTest.java",
     r"CORPUS_FLOOR\s*=\s*(\d+)"),
    ("java", "encode total",
     "bindings/java/src/test/java/org/lora/schema/CorpusEncodeRoundTripTest.java",
     r"ENCODE_FLOOR_TOTAL\s*=\s*(\d+)"),
    ("dotnet", "decode total", "dotnet/PayloadSchema.Tests/CorpusConformanceTests.cs",
     r"CorpusFloor\s*=\s*(\d+)"),
    ("dotnet", "encode total", "dotnet/PayloadSchema.Tests/CorpusEncodeRoundTripTests.cs",
     r"EncodeFloorTotal\s*=\s*(\d+)"),
]

#: Per-shape floor tables, one syntax each.
SHAPE_SITES = [
    ("python", "tests/test_encode_round_trip.py", r'"([a-z_ ]+)":\s*(\d+),'),
    ("go", "go/schema/corpus_encode_test.go", r'"([a-z_ ]+)":\s*(\d+),'),
    # The last entry of Java's Map.of ends in ')' rather than ',', so requiring a comma
    # silently dropped "repeat" - present in the table and absent from the report.
    ("java", "bindings/java/src/test/java/org/lora/schema/CorpusEncodeRoundTripTest.java",
     r'"([a-z_ ]+)",\s*(\d+)\s*[,)]'),
    ("dotnet", "dotnet/PayloadSchema.Tests/CorpusEncodeRoundTripTests.cs",
     r'\["([a-z_ ]+)"\]\s*=\s*(\d+),'),
]

SHAPES = ("tlv", "flagged", "plain fixed", "match", "byte_group", "repeat")


def read_floors(impl):
    """{floor name: value} declared for one implementation."""
    out = {}
    for who, name, rel, pattern in FLOOR_SITES:
        if who != impl:
            continue
        m = re.search(pattern, (FLOOR_ROOT / rel).read_text())
        if m:
            out[name] = int(m.group(1))
    for who, rel, pattern in SHAPE_SITES:
        if who != impl:
            continue
        text = (FLOOR_ROOT / rel).read_text()
        # Only the shape table's own entries: a shape name plus a number.
        for shape, value in re.findall(pattern, text):
            if shape in SHAPES:
                out.setdefault(f"shape {shape}", int(value))
    return out


def python_actuals():
    spec = importlib.util.spec_from_file_location(
        "ert", REPO / "tests" / "test_encode_round_trip.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tally, _ = module.round_trip_results()
    out = {"encode total": sum(v for (shape, verdict), v in tally.items()
                               if verdict == "round-trips")}
    for shape in SHAPES:
        out[f"shape {shape}"] = tally[(shape, "round-trips")]
    return out


def parse_log(text):
    """Actuals from the lines the Go, Java and C# harnesses print."""
    out = {}
    m = re.search(r"corpus vectors:\s*\d+\s*total,\s*(\d+)\s*passed", text)
    if m:
        out["decode total"] = int(m.group(1))
    # Go's t.Log prefixes every line with "corpus_encode_test.go:178: ", so the anchor
    # allows an optional file:line. Without it Go's totals and per-shape rows read as
    # "not measured" while Java and C#, which print bare lines, parsed fine - a silent
    # half-answer rather than an error.
    m = re.search(r"(?:^\s*|:\s*)total round-trips:\s*(\d+) of", text, re.M)
    if m:
        out["encode total"] = int(m.group(1))
    m = re.search(r"plain \(unordered\) round-trips:\s*(\d+) of", text)
    if m:
        out["encode plain"] = int(m.group(1))
    for shape, value in re.findall(
            r"(?:^|:\s)\s*([a-z_ ]{3,12}?)\s+round-trips=(\d+)", text, re.M):
        if shape.strip() in SHAPES:
            out[f"shape {shape.strip()}"] = int(value)
    return out


def run(cmd, cwd=REPO):
    done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=True)
    return done.stdout + done.stderr


# As the Makefile's GO_IMAGE / DOTNET_IMAGE: CI points these at a mirror.
GO_IMAGE = os.environ.get("GO_IMAGE") or "golang:1.22"
DOTNET_IMAGE = os.environ.get("DOTNET_IMAGE") or "mcr.microsoft.com/dotnet/sdk:8.0"


def go_actuals():
    # -count=1 is required. Without it `go test` answers `ok (cached)` and runs nothing,
    # which silently makes any check against it meaningless.
    return parse_log(run(
        'docker run --rm -v "$PWD":/work -w /work/go/schema -v "$PWD/.cache/go":/tmp/gocache '
        '-e GOFLAGS=-mod=mod -e GOCACHE=/tmp/gocache ' + GO_IMAGE + ' '
        'sh -c "go test ./... -count=1 -v"'))


def java_actuals():
    return parse_log(run("make test-java"))


def dotnet_actuals():
    # `make test-dotnet` suppresses the console output that carries these numbers; it
    # reports only "Passed! 92", which invites a guess.
    return parse_log(run(
        'docker run --rm -v "$PWD":/work -w /work/dotnet -v "$PWD/.cache/nuget":/root/.nuget '
        + DOTNET_IMAGE + ' dotnet test --nologo '
        '--logger "console;verbosity=detailed"'))


ACTUALS = {"python": python_actuals, "go": go_actuals,
           "java": java_actuals, "dotnet": dotnet_actuals}

#: The implementations whose actuals a `make test-<lang>` log can supply.
LOGGED = ("go", "java", "dotnet")

# Written by tools/run-logged.py around the command's own output.
LOG_HEADER = re.compile(r"^# run-logged started=([0-9.]+)", re.M)
LOG_TRAILER = re.compile(r"^# run-logged exit=(-?\d+)\s*$", re.M)


def newest_source(repo=REPO):
    """(mtime, path) of the most recently modified file or directory in the source tree.

    The tree is what git sees: tracked files plus untracked ones that are not ignored, so
    build output, caches and the logs themselves never count. A tracked file deleted from
    the working tree is represented by its nearest surviving directory, whose mtime is the
    only record of when it went. Directories are not counted otherwise: the suites
    themselves create ignored output beside the sources (dotnet's bin/ and obj/, Maven's
    target/), which touches those directories and would make every log stale at once.
    If git cannot answer, the tree is treated as newer than any log, which only costs a run.
    """
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(repo), capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return float("inf"), "(git ls-files failed)"
    newest = (0.0, "")
    for rel in set(listing.decode("utf-8", errors="replace").split("\0")):
        if not rel:
            continue
        probe = rel
        while True:
            try:
                mtime = os.stat(os.path.join(str(repo), probe)).st_mtime
                break
            except OSError:
                probe = os.path.dirname(probe)  # deleted: ask the directory it was in
                if not probe:
                    mtime = 0.0
                    break
        if mtime > newest[0]:
            newest = (mtime, probe)
    return newest


def read_log(path, tree):
    """(actuals, why) from one run-logged log, or (None, why) when it cannot be used.

    `tree` is newest_source()'s answer. A log is refused when it is missing, when its run
    did not finish, when a source file changed after its run began, or when it holds no
    corpus counts at all (a container that failed to start, say). In each case the caller
    runs the suite instead, which is what plain check-floors always does.
    """
    path = Path(path)
    shown = path
    try:
        shown = path.resolve().relative_to(REPO)
    except ValueError:
        pass
    if not path.exists():
        return None, f"there is no log at {shown}"
    text = path.read_text(errors="replace")
    head = LOG_HEADER.search(text)
    if not head:
        return None, f"{shown} has no run-logged header"
    tail = LOG_TRAILER.search(text)
    if not tail:
        return None, f"{shown} is incomplete (its run did not finish)"
    newest_mtime, newest_path = tree
    if newest_mtime > float(head.group(1)):
        return None, f"{shown} is stale: {newest_path} changed after its run began"
    actuals = parse_log(text)
    if not actuals:
        return None, f"{shown} holds no corpus counts"
    return actuals, f"{shown} (exit {tail.group(1)})"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for impl in ACTUALS:
        parser.add_argument(f"--{impl}", action="store_true")
    parser.add_argument("--loose", action="store_true",
                        help="exit 1 if any floor sits below its own actual")
    parser.add_argument("--json", metavar="PATH")
    parser.add_argument("--from-logs", action="store_true",
                        help="read Go, Java and C# actuals from the logs `make test-go`, "
                             "`test-java` and `test-dotnet` keep, running a suite only "
                             "when its log is missing, incomplete or stale")
    parser.add_argument("--log-dir", metavar="DIR", default=str(LOG_DIR),
                        help="where --from-logs looks (default: build/lang-logs)")
    parser.add_argument("--floor-root", metavar="DIR", help=argparse.SUPPRESS)
    args = parser.parse_args()

    global FLOOR_ROOT
    if args.floor_root:
        FLOOR_ROOT = Path(args.floor_root)

    chosen = [i for i in ACTUALS if getattr(args, i)] or list(ACTUALS)
    report, loose, below = {}, [], []
    tree = None

    for impl in chosen:
        floors = read_floors(impl)
        actuals = None
        if args.from_logs and impl in LOGGED:
            if tree is None:
                tree = newest_source()
            actuals, why = read_log(Path(args.log_dir) / f"{impl}.log", tree)
            if actuals is None:
                print(f"\n  {impl}: running its suite, because {why}")
            else:
                print(f"\n  {impl}: actuals read from {why}")
        if actuals is None:
            actuals = ACTUALS[impl]()
        rows = []
        for name in sorted(floors):
            floor = floors[name]
            actual = actuals.get(name)
            gap = None if actual is None else actual - floor
            rows.append({"floor_name": name, "floor": floor,
                         "actual": actual, "headroom": gap})
            if gap is not None and gap > 0:
                loose.append((impl, name, floor, actual))
            if gap is not None and gap < 0:
                below.append((impl, name, floor, actual))
        report[impl] = rows

        print(f"\n  {impl}")
        print(f"    {'floor':<18} {'declared':>9} {'actual':>8} {'headroom':>9}")
        for r in rows:
            a = "-" if r["actual"] is None else r["actual"]
            h = "not measured" if r["headroom"] is None else f"{r['headroom']:+d}"
            print(f"    {r['floor_name']:<18} {r['floor']:>9} {a:>8} {h:>9}")

    print()
    if below:
        for impl, name, floor, actual in below:
            print(f"  REGRESSION {impl} {name}: floor {floor} is above actual {actual}")
    if loose:
        for impl, name, floor, actual in loose:
            print(f"  loose  {impl} {name}: floor {floor}, actual {actual} "
                  f"({actual - floor} unlocked)")
    else:
        print("  Every measured floor sits exactly at its own implementation's actual.")
    print("  Floors are never compared across implementations; each harness buckets "
          "schemas its own way.")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")

    if below:
        return 1
    return 1 if (args.loose and loose) else 0


if __name__ == "__main__":
    sys.exit(main())
