#!/usr/bin/env python3
"""
crossvalidate_decentlab.py - Check decentlab schemas against the vendor's decoders.

Our own test vectors cannot tell us whether a schema is *correct*: a vector
generated from our interpreter's output only records current behaviour, bugs
included. This tool compares each schema against an independent oracle -- the
decoder Decentlab publishes for the same sensor -- using the vendor's own
documented test payloads.

Requires a checkout of https://github.com/decentlab/decentlab-decoders:

    git clone --depth 1 https://github.com/decentlab/decentlab-decoders

Usage:
    python3 tools/crossvalidate_decentlab.py --vendor-dir ../decentlab-decoders
    python3 tools/crossvalidate_decentlab.py --vendor-dir DIR --schema dl-atm22
    python3 tools/crossvalidate_decentlab.py --vendor-dir DIR --quiet

Exit status is non-zero when any schema disagrees with its vendor decoder, so
this can gate a pull request once the family is clean.
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "schemas" / "devices" / "decentlab"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema_interpreter import SchemaInterpreter  # noqa: E402

#: Relative tolerance used when comparing against the vendor's floating point
#: results, plus an absolute floor for values near zero.
REL_TOLERANCE = 0.001
ABS_TOLERANCE = 0.02


def normalise(key):
    """Vendor decoders key on display names: 'Battery voltage' -> battery_voltage."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", key.lower())).strip("_")


def vendor_dirs(vendor_root):
    return sorted(p.name for p in vendor_root.glob("DL-*") if p.is_dir())


def match_vendor_dir(stem, available):
    """dl-pr36-8192 -> DL-PR36 (parameterised variants drop their suffixes)."""
    parts = stem.upper().split("-")
    for cut in range(len(parts), 0, -1):
        candidate = "-".join(parts[:cut])
        if candidate in available:
            return candidate
    return None


def load_vendor_decoder(vendor_root, name):
    """Import the vendor's python decoder for one sensor."""
    source = next((vendor_root / name).glob("*.py"), None)
    if source is None:
        return None
    spec = importlib.util.spec_from_file_location(
        "vendor_" + name.replace("-", "_"), source
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vendor_test_payloads(vendor_root, name):
    """Read the hex payloads the vendor documents in its ELEMENT-IoT decoder."""
    found = []
    for path in (vendor_root / name).glob("*.ex"):
        text = path.read_text(errors="replace")
        block = re.search(r"##\s*test payloads(.*?)(?:\n\s*\n|def )", text, re.S)
        if not block:
            continue
        for line in block.group(1).splitlines():
            m = re.match(r"\s*#\s*([0-9a-fA-F]{6,})\s*$", line)
            if m:
                found.append(m.group(1).lower())
    return sorted(set(found))


def vendor_values(module, payload_hex):
    raw = module.decode(payload_hex.encode(), hex=True)
    return {
        normalise(k): (v["value"] if isinstance(v, dict) else v)
        for k, v in raw.items()
    }


def our_values(schema, payload_hex):
    result = SchemaInterpreter(schema).decode(bytes.fromhex(payload_hex))
    if not result.success:
        return None, result.errors
    return {k: v for k, v in result.data.items() if not k.startswith("_")}, None


def agrees(ours, theirs):
    try:
        return abs(float(ours) - float(theirs)) <= max(
            ABS_TOLERANCE, abs(float(theirs)) * REL_TOLERANCE
        )
    except (TypeError, ValueError):
        return str(ours) == str(theirs)


def check_schema(path, vendor_root, available):
    """Return (status, details) for one schema."""
    stem = path.stem
    name = match_vendor_dir(stem, available)
    if name is None:
        return "no-vendor-decoder", []
    payloads = vendor_test_payloads(vendor_root, name)
    module = load_vendor_decoder(vendor_root, name)
    if not payloads or module is None:
        return "no-vendor-payloads", []
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems = []
    for payload in payloads:
        try:
            expected = vendor_values(module, payload)
        except Exception as exc:
            problems.append((payload, "vendor decoder failed: %s" % exc))
            continue
        actual, errors = our_values(schema, payload)
        if actual is None:
            problems.append((payload, "our decode failed: %s" % errors))
            continue
        for key, want in expected.items():
            if key not in actual:
                problems.append((payload, "%s missing from our output" % key))
            elif not agrees(actual[key], want):
                problems.append(
                    (payload, "%s: vendor %s, ours %s" % (key, want, actual[key]))
                )
    return ("agrees" if not problems else "disagrees"), problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--vendor-dir",
        required=True,
        help="path to a decentlab-decoders checkout",
    )
    parser.add_argument("--schema", help="check one schema by stem, e.g. dl-atm22")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args()

    vendor_root = Path(args.vendor_dir).expanduser().resolve()
    if not vendor_root.is_dir():
        print("not a directory: %s" % vendor_root, file=sys.stderr)
        return 2
    available = vendor_dirs(vendor_root)
    if not available:
        print("no DL-* sensor directories under %s" % vendor_root, file=sys.stderr)
        return 2

    paths = sorted(SCHEMA_DIR.glob("*.yaml"))
    if args.schema:
        paths = [p for p in paths if p.stem == args.schema]
        if not paths:
            print("no such schema: %s" % args.schema, file=sys.stderr)
            return 2

    tally = {}
    disagreeing = []
    for path in paths:
        status, problems = check_schema(path, vendor_root, available)
        tally[status] = tally.get(status, 0) + 1
        if status == "disagrees":
            disagreeing.append((path.stem, problems))
        if not args.quiet:
            print("%-26s %s" % (path.stem, status))
            for payload, detail in problems[:4]:
                print("    %s  %s" % (payload[:20], detail))

    print("\n%d schema(s) checked against vendor decoders" % len(paths))
    for status in sorted(tally):
        print("  %-20s %d" % (status, tally[status]))
    return 1 if disagreeing else 0


if __name__ == "__main__":
    sys.exit(main())
