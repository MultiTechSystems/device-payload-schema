#!/usr/bin/env python3
"""CI gate: vendor cross-validation must not regress.

AGENTS.md: "A high score is not proof of correctness." `score_schema.py` runs a schema
against its own vectors; only an oracle independent of this implementation says whether
the schema decodes what the device means. Two such oracles exist and already have
tools:

  ttn                 tools/crossvalidate_ttn.py - the vendor's declared examples in
                      TheThingsNetwork/lorawan-devices, re-decoded by the vendor's own
                      JavaScript under node.
  decentlab-decoders  tools/crossvalidate_decentlab.py - Decentlab's python decoders
                      over the test payloads the vendor documents.

This gate runs both over every schema directory that `tools/crossval-vendors.json` maps
to a vendor, and compares the per-schema result with the committed baseline
`tools/crossval-baseline.json`. It is a ratchet, like `score-baseline.json` and the
language floors: a regression fails, an improvement is reported and stays unlocked until
somebody refreshes the baseline deliberately with --update.

Fails when:

  * a schema that agreed with an oracle no longer does (or is no longer compared);
  * a schema that disagreed now has more problems, or more comparisons it could not
    make (a vendor decoder that stopped running is a lost check, not a pass);
  * a schema absent from the baseline is compared by an oracle and disagrees, unless
    the baseline's `exceptions` names it with a reason;
  * a baselined schema has disappeared (a rename is fine - refresh the baseline);
  * a directory under schemas/devices is in neither half of crossval-vendors.json;
  * an oracle checkout is not at the commit the baseline pins. The oracles are data,
    and a moved oracle changes the verdicts without any change here, so CI must clone
    exactly the pinned commit.

    python3 tools/crossval-gate.py
    python3 tools/crossval-gate.py --devices-repo ~/lorawan-devices \\
        --decentlab-dir ~/decentlab-decoders
    python3 tools/crossval-gate.py --update       # lock in improvements / new pins

Oracle locations: --devices-repo / --decentlab-dir, else $TTN_DEVICES_REPO /
$DECENTLAB_DECODERS, else ~/Workspace/lora/tools/{lorawan-devices,decentlab-decoders}.
node must be on PATH.
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

CORPUS = REPO_ROOT / "schemas" / "devices"
VENDORS_FILE = TOOLS / "crossval-vendors.json"
BASELINE_FILE = TOOLS / "crossval-baseline.json"

TTN = "ttn"
DECENTLAB = "decentlab-decoders"
ORACLE_URLS = {
    TTN: "https://github.com/TheThingsNetwork/lorawan-devices",
    DECENTLAB: "https://github.com/decentlab/decentlab-decoders",
}
DEFAULT_DIRS = {
    TTN: ("TTN_DEVICES_REPO", "~/Workspace/lora/tools/lorawan-devices"),
    DECENTLAB: ("DECENTLAB_DECODERS", "~/Workspace/lora/tools/decentlab-decoders"),
}

AGREES = "agrees"
DISAGREES = "disagrees"
#: Statuses where the oracle actually compared something.
COMPARED = (AGREES, DISAGREES)


# --------------------------------------------------------------------------- rules


def _entry(status_map, key, oracle):
    return (status_map.get(key) or {}).get(oracle)


def compare(
    baseline: Dict[str, Dict[str, Dict[str, Any]]],
    current: Dict[str, Dict[str, Dict[str, Any]]],
    exceptions: Optional[Dict[str, str]] = None,
) -> Dict[str, List[str]]:
    """Apply the ratchet. Returns lists of `regressions`, `improvements`, `notes`.

    Both maps are {schema_key: {oracle: {status, problems, notes}}}. Pure: no I/O.
    """
    exceptions = exceptions or {}
    regressions = []  # type: List[str]
    improvements = []  # type: List[str]
    notes = []  # type: List[str]

    for key in sorted(set(baseline) - set(current)):
        regressions.append(
            "%s: in the baseline but no longer present - if it was renamed or "
            "removed deliberately, refresh the baseline with --update" % key
        )

    for key in sorted(current):
        if key not in baseline:
            for oracle, now in sorted(current[key].items()):
                if now["status"] == DISAGREES:
                    reason = exceptions.get(key)
                    if reason:
                        notes.append(
                            "%s [%s]: new schema disagrees (%d problem(s)), allowed "
                            "by exception: %s" % (key, oracle, now["problems"], reason)
                        )
                    else:
                        regressions.append(
                            "%s [%s]: new schema disagrees with its vendor oracle "
                            "(%d problem(s)); a new schema must agree, or be listed "
                            "with a reason under `exceptions` in the baseline"
                            % (key, oracle, now["problems"])
                        )
                else:
                    improvements.append(
                        "%s [%s]: new schema, %s" % (key, oracle, now["status"])
                    )
            continue
        for oracle in sorted(set(baseline[key]) | set(current[key])):
            was = _entry(baseline, key, oracle)
            now = _entry(current, key, oracle)
            label = "%s [%s]" % (key, oracle)
            if now is None:
                regressions.append("%s: no longer checked by this oracle" % label)
                continue
            if was is None:
                if now["status"] == DISAGREES:
                    regressions.append(
                        "%s: newly compared and disagrees (%d problem(s))"
                        % (label, now["problems"])
                    )
                else:
                    improvements.append(
                        "%s: newly checked, %s" % (label, now["status"])
                    )
                continue
            regressions.extend(_compare_entry(label, was, now, improvements))
    return {
        "regressions": regressions,
        "improvements": improvements,
        "notes": notes,
    }


def _compare_entry(label, was, now, improvements) -> List[str]:
    out = []
    ws, ns = was["status"], now["status"]
    wn, nn = was.get("notes", 0), now.get("notes", 0)
    if ws == AGREES and ns != AGREES:
        out.append(
            "%s: agreed with the vendor, now %s (%d problem(s))"
            % (label, ns, now["problems"])
        )
    elif ws in COMPARED and ns not in COMPARED:
        out.append("%s: was %s, now %s - a comparison was lost" % (label, ws, ns))
    elif ws not in COMPARED and ns == DISAGREES:
        out.append(
            "%s: newly compared and disagrees (%d problem(s))"
            % (label, now["problems"])
        )
    elif now.get("decode_failures", 0) > was.get("decode_failures", 0):
        out.append(
            "%s: payloads our decoder cannot read rose %d -> %d"
            % (label, was.get("decode_failures", 0), now["decode_failures"])
        )
    elif ws == DISAGREES and ns == DISAGREES and now["problems"] > was["problems"]:
        out.append(
            "%s: disagreements rose %d -> %d"
            % (label, was["problems"], now["problems"])
        )
    elif ws != AGREES and ns == AGREES:
        improvements.append("%s: %s -> agrees" % (label, ws))
    elif ws == DISAGREES and now["problems"] < was["problems"]:
        improvements.append(
            "%s: disagreements fell %d -> %d"
            % (label, was["problems"], now["problems"])
        )
    elif ws != ns:
        improvements.append("%s: %s -> %s" % (label, ws, ns))
    if ns in COMPARED and nn > wn:
        out.append(
            "%s: %d comparison(s) could not be made, baseline %d - check node and "
            "the vendor decoder" % (label, nn, wn)
        )
    elif nn < wn:
        improvements.append("%s: comparisons not made fell %d -> %d" % (label, wn, nn))
    return out


def check_pins(
    pins: Dict[str, Dict[str, str]], commits: Dict[str, Optional[str]]
) -> List[str]:
    """One message per oracle whose checkout differs from the baseline's pin."""
    problems = []
    for oracle, pin in sorted(pins.items()):
        want = pin.get("commit")
        have = commits.get(oracle)
        if have is None:
            problems.append(
                "%s: checkout has no readable git commit; clone %s at %s"
                % (oracle, pin.get("url", ORACLE_URLS.get(oracle)), want)
            )
        elif have != want:
            problems.append(
                "%s: checkout is at %s but the baseline pins %s. The verdicts depend "
                "on the oracle's content, so they are only comparable at the pinned "
                "commit: `git -C <dir> fetch --depth 1 origin %s && git -C <dir> "
                "checkout %s`, or move the pin deliberately with --update."
                % (oracle, have[:12], want[:12], want, want)
            )
    return problems


def unmapped_dirs(schemas_dir: Path, vendors: Dict[str, Any]) -> List[str]:
    known = set(vendors.get(TTN, {})) | set(vendors.get("not_cross_validated", {}))
    return sorted(
        p.name for p in schemas_dir.iterdir() if p.is_dir() and p.name not in known
    )


# --------------------------------------------------------------------------- running


def _load_tool(name):
    path = TOOLS / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_commit(path: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


#: crossvalidate_ttn.compare's wording for a payload our decoder could not read at all.
#: One such problem replaces a per-key disagreement for every key of that payload, so
#: a decode that breaks can LOWER the problem count; it is counted separately.
_DECODE_FAILURE = "our decode failed"
_DECODER_RAISED = "our decoder raised"


def _summarise(status: str, problems: List[Any]) -> Dict[str, Any]:
    texts = [p if isinstance(p, str) else "%s  %s" % p for p in problems]
    real = [t for t in texts if not t.startswith("note: ")]
    failures = [t for t in real if _DECODE_FAILURE in t or _DECODER_RAISED in t]
    return {
        "status": status,
        "problems": len(real),
        "decode_failures": len(failures),
        "notes": len(texts) - len(real),
        "detail": texts,
    }


def run_oracles(
    schemas_dir: Path,
    vendors: Dict[str, Any],
    ttn_repo: Path,
    decentlab_dir: Path,
    workers: int = 8,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{schema_key: {oracle: {status, problems, notes, detail}}} for the tree."""
    ttn_tool = _load_tool("crossvalidate_ttn")
    dl_tool = _load_tool("crossvalidate_decentlab")

    jobs = []  # type: List[Tuple[str, Path, Path]]
    for our_dir, ttn_vendor in sorted(vendors.get(TTN, {}).items()):
        vendor_dir = ttn_repo / "vendor" / ttn_vendor
        if not vendor_dir.is_dir():
            raise SystemExit(
                "crossval-vendors.json maps %s to %s, which is not a vendor directory "
                "in %s" % (our_dir, ttn_vendor, ttn_repo)
            )
        for path in sorted((schemas_dir / our_dir).glob("*.yaml")):
            jobs.append((path.relative_to(schemas_dir).as_posix(), path, vendor_dir))

    result = {}  # type: Dict[str, Dict[str, Dict[str, Any]]]

    def ttn_job(job):
        key, path, vendor_dir = job
        status, problems = ttn_tool.check_schema(path, vendor_dir, use_decoder=True)
        return key, _summarise(status, problems)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for key, entry in pool.map(ttn_job, jobs):
            result.setdefault(key, {})[TTN] = entry

    available = dl_tool.vendor_dirs(decentlab_dir)
    if not available:
        raise SystemExit("no DL-* sensor directories under %s" % decentlab_dir)
    for our_dir in vendors.get(DECENTLAB, []):
        for path in sorted((schemas_dir / our_dir).glob("*.yaml")):
            key = path.relative_to(schemas_dir).as_posix()
            status, problems = dl_tool.check_schema(path, decentlab_dir, available)
            result.setdefault(key, {})[DECENTLAB] = _summarise(status, problems)
    return result


def tally(status_map) -> Dict[str, Dict[str, Dict[str, int]]]:
    """{oracle: {vendor_dir: {status: n}}}."""
    out = {}  # type: Dict[str, Dict[str, Dict[str, int]]]
    for key, oracles in status_map.items():
        vendor = key.split("/", 1)[0]
        for oracle, entry in oracles.items():
            bucket = out.setdefault(oracle, {}).setdefault(vendor, {})
            bucket[entry["status"]] = bucket.get(entry["status"], 0) + 1
    return out


def strip_detail(status_map):
    return {
        key: {
            oracle: {k: v for k, v in entry.items() if k != "detail"}
            for oracle, entry in sorted(oracles.items())
        }
        for key, oracles in sorted(status_map.items())
    }


def build_baseline(status_map, commits, previous=None) -> Dict[str, Any]:
    previous = previous or {}
    return {
        "about": (
            "Committed ratchet for tools/crossval-gate.py. Regenerate only "
            "deliberately (--update) and say why in the commit message. "
            "`exceptions` maps a schema key to the reason a NEW schema may disagree "
            "with its vendor oracle; it never excuses a regression."
        ),
        "oracles": {
            oracle: {"url": ORACLE_URLS[oracle], "commit": commits[oracle]}
            for oracle in sorted(commits)
        },
        "summary": tally(status_map),
        "exceptions": previous.get("exceptions", {}),
        "schemas": strip_detail(status_map),
    }


def _oracle_dir(cli_value, oracle) -> Path:
    env, default = DEFAULT_DIRS[oracle]
    return Path(cli_value or os.environ.get(env) or default).expanduser().resolve()


def print_tally(status_map):
    for oracle, vendors in sorted(tally(status_map).items()):
        print("%s:" % oracle)
        for vendor, counts in sorted(vendors.items()):
            total = sum(counts.values())
            cells = ", ".join("%s %d" % (s, n) for s, n in sorted(counts.items()))
            print(
                "  %-16s %d/%d agree  (%s)"
                % (vendor, counts.get(AGREES, 0), total, cells)
            )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--devices-repo", help="lorawan-devices checkout")
    parser.add_argument("--decentlab-dir", help="decentlab-decoders checkout")
    parser.add_argument("--schemas-dir", default=str(CORPUS))
    parser.add_argument("--baseline", default=str(BASELINE_FILE))
    parser.add_argument("--vendors", default=str(VENDORS_FILE))
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument(
        "--update", action="store_true", help="rewrite the baseline from this run"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="print problems for regressions"
    )
    args = parser.parse_args(argv)

    if shutil.which("node") is None:
        print("FAIL: node is not on PATH; the vendor JavaScript cannot run")
        return 2
    ttn_repo = _oracle_dir(args.devices_repo, TTN)
    decentlab_dir = _oracle_dir(args.decentlab_dir, DECENTLAB)
    for oracle, path in ((TTN, ttn_repo), (DECENTLAB, decentlab_dir)):
        if not path.is_dir():
            print(
                "FAIL: %s checkout not found at %s (clone %s)"
                % (oracle, path, ORACLE_URLS[oracle])
            )
            return 2
    schemas_dir = Path(args.schemas_dir).resolve()
    vendors = json.loads(Path(args.vendors).read_text(encoding="utf-8"))
    baseline_path = Path(args.baseline)
    baseline = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path.exists()
        else None
    )
    commits = {TTN: git_commit(ttn_repo), DECENTLAB: git_commit(decentlab_dir)}

    failures = []  # type: List[str]
    stray = unmapped_dirs(schemas_dir, vendors)
    if stray:
        failures.append(
            "schema directories in neither 'ttn' nor 'not_cross_validated' of %s: %s"
            % (Path(args.vendors).name, ", ".join(stray))
        )
    if baseline is not None and not args.update:
        failures.extend(check_pins(baseline.get("oracles", {}), commits))
    if failures:
        for f in failures:
            print("FAIL: %s" % f)
        return 1

    started = time.time()
    current = run_oracles(schemas_dir, vendors, ttn_repo, decentlab_dir, args.workers)
    elapsed = time.time() - started
    print_tally(current)
    print("(%d schemas, %.1fs)" % (len(current), elapsed))

    if args.update:
        if None in commits.values():
            print("FAIL: cannot pin an oracle without a git commit: %s" % commits)
            return 1
        data = build_baseline(current, commits, baseline)
        baseline_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print("wrote %s" % baseline_path)
        return 0
    if baseline is None:
        print("FAIL: no baseline at %s; create one with --update" % baseline_path)
        return 1

    outcome = compare(
        baseline.get("schemas", {}), current, baseline.get("exceptions", {})
    )
    for line in outcome["improvements"]:
        print("IMPROVED: %s" % line)
    for line in outcome["notes"]:
        print("NOTE: %s" % line)
    if outcome["improvements"]:
        print(
            "%d improvement(s) not yet locked in - refresh the baseline with "
            "--update" % len(outcome["improvements"])
        )
    for line in outcome["regressions"]:
        print("REGRESSION: %s" % line)
        key = line.split(" ", 1)[0].rstrip(":")
        if args.verbose or len(outcome["regressions"]) <= 5:
            for oracle, entry in sorted((current.get(key) or {}).items()):
                for detail in entry.get("detail", [])[:8]:
                    print("      [%s] %s" % (oracle, detail))
    if outcome["regressions"]:
        print("FAIL: %d cross-validation regression(s)" % len(outcome["regressions"]))
        return 1
    print("PASS: no cross-validation regressions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
