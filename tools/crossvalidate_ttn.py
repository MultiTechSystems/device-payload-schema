#!/usr/bin/env python3
"""
crossvalidate_ttn.py - Check schemas against The Things Network device repository.

Our own test vectors only show that a schema agrees with itself. This compares a
schema against two oracles that are independent of this implementation, both
published by the device vendor in TheThingsNetwork/lorawan-devices:

  * the `examples` declared in <device>-codec.yaml (vendor-authored input/output
    pairs, validated by TTN's CI), and
  * optionally the vendor's own JavaScript decoder <device>.js, run under node.

Requires a checkout of the device repository:

    git clone --depth 1 https://github.com/TheThingsNetwork/lorawan-devices

Usage:
    python3 tools/crossvalidate_ttn.py --devices-repo ../lorawan-devices \\
        --vendor milesight-iot --schema-dir schemas/devices/milesight
    python3 tools/crossvalidate_ttn.py --devices-repo DIR --vendor milesight-iot \\
        --schema-dir schemas/devices/milesight --schema am102 --verbose

Exit status is non-zero when any schema disagrees with its vendor examples, so
this can gate a pull request once a vendor family is clean.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from score_schema import CONFORMANCE_TOLERANCE  # noqa: E402
from validate_schema import values_match  # noqa: E402


def declared_examples(codec_path):
    """Read the uplink examples the vendor declares for a device."""
    try:
        codec = yaml.safe_load(codec_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    return (codec.get("uplinkDecoder") or {}).get("examples") or []


def run_vendor_decoder(js_path, requests):
    """Decode payloads with the vendor's own JavaScript, in one node process."""
    driver = """
const out = [];
for (const req of REQUESTS) {
  const bytes = [];
  for (let i = 0; i < req.hex.length; i += 2) bytes.push(parseInt(req.hex.substr(i, 2), 16));
  try {
    const r = decodeUplink({ bytes: bytes, fPort: req.fPort });
    out.push(r && r.data ? { data: r.data } : { errors: (r && r.errors) || ['no data'] });
  } catch (e) { out.push({ errors: [String((e && e.message) || e)] }); }
}
console.log(JSON.stringify(out));
"""
    script = "%s\nconst REQUESTS = %s;\n%s" % (
        js_path.read_text(encoding="utf-8", errors="replace"),
        json.dumps(requests),
        driver,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(script)
        temp = handle.name
    try:
        proc = subprocess.run(["node", temp], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    finally:
        Path(temp).unlink(missing_ok=True)


def compare(schema, payload, fport, expected):
    """Return a list of disagreements between our decode and `expected`."""
    try:
        result = SchemaInterpreter(schema).decode(payload, fPort=fport)
    except Exception as exc:  # noqa: BLE001 - report, do not abort the sweep
        return ["our decoder raised %s: %s" % (type(exc).__name__, exc)]
    if not result.success:
        return ["our decode failed: %s" % result.errors[:2]]
    got = {k: v for k, v in result.data.items() if not k.startswith("_")}
    problems = []
    for key, want in (expected or {}).items():
        if key not in got:
            problems.append("%s: vendor %r, ours <missing>" % (key, want))
            continue
        match, _message = values_match(want, got[key], CONFORMANCE_TOLERANCE)
        if not match:
            problems.append("%s: vendor %r, ours %r" % (key, want, got[key]))
    return problems


def check_schema(path, vendor_dir, use_decoder):
    """Return (status, problems) for one schema."""
    codec = vendor_dir / ("%s-codec.yaml" % path.stem)
    if not codec.exists():
        return "no-vendor-codec", []
    examples = declared_examples(codec)
    if not examples:
        return "no-vendor-examples", []
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))

    problems = []
    for example in examples:
        payload = bytes(example["input"]["bytes"])
        fport = example["input"].get("fPort")
        expected = example["output"].get("data") or {}
        problems += [
            "%s (declared example): %s" % (payload.hex(), issue)
            for issue in compare(schema, payload, fport, expected)
        ]

    # Optionally re-decode the same payloads with the vendor's own decoder, which
    # catches cases where TTN's declared output has drifted from its codec.
    js = vendor_dir / ("%s.js" % path.stem)
    if use_decoder and js.exists():
        requests = [
            {
                "hex": bytes(e["input"]["bytes"]).hex(),
                "fPort": e["input"].get("fPort", 85),
            }
            for e in examples
        ]
        results = run_vendor_decoder(js, requests)
        if results:
            for example, produced in zip(examples, results):
                data = produced.get("data") if isinstance(produced, dict) else None
                if not data:
                    continue
                payload = bytes(example["input"]["bytes"])
                problems += [
                    "%s (vendor decoder): %s" % (payload.hex(), issue)
                    for issue in compare(
                        schema, payload, example["input"].get("fPort"), data
                    )
                ]

    return ("agrees" if not problems else "disagrees"), problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--devices-repo", required=True, help="lorawan-devices checkout")
    parser.add_argument("--vendor", required=True, help="vendor directory, e.g. milesight-iot")
    parser.add_argument(
        "--schema-dir", required=True, help="our schema directory for that vendor"
    )
    parser.add_argument("--schema", help="check a single schema by stem")
    parser.add_argument(
        "--no-vendor-decoder",
        action="store_true",
        help="compare only against declared examples, skipping node",
    )
    parser.add_argument("--verbose", action="store_true", help="list every disagreement")
    args = parser.parse_args()

    vendor_dir = Path(args.devices_repo).expanduser().resolve() / "vendor" / args.vendor
    if not vendor_dir.is_dir():
        print("no such vendor directory: %s" % vendor_dir, file=sys.stderr)
        return 2
    schema_dir = Path(args.schema_dir)
    if not schema_dir.is_absolute():
        schema_dir = REPO_ROOT / schema_dir
    paths = sorted(schema_dir.glob("*.yaml"))
    if args.schema:
        paths = [p for p in paths if p.stem == args.schema]
        if not paths:
            print("no such schema: %s" % args.schema, file=sys.stderr)
            return 2

    tally, disagreeing = {}, 0
    for path in paths:
        status, problems = check_schema(
            path, vendor_dir, use_decoder=not args.no_vendor_decoder
        )
        tally[status] = tally.get(status, 0) + 1
        if status == "disagrees":
            disagreeing += 1
        print("%-24s %s" % (path.stem, status))
        limit = None if args.verbose else 3
        for problem in problems[:limit]:
            print("    %s" % problem)

    print("\n%d schema(s) checked against %s" % (len(paths), args.vendor))
    for status in sorted(tally):
        print("  %-20s %d" % (status, tally[status]))
    return 1 if disagreeing else 0


if __name__ == "__main__":
    sys.exit(main())
