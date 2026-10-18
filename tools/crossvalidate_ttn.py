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
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from score_schema import CONFORMANCE_TOLERANCE  # noqa: E402
from validate_schema import correction_path, values_match  # noqa: E402


class _CoreBoolLoader(yaml.SafeLoader):
    """A SafeLoader that reads only true/false as booleans, as YAML 1.2 does.

    PyYAML follows YAML 1.1, where an unquoted on/off/yes/no is a boolean. Vendor
    examples write enum labels that way - arwin's `statusLED: on` is the string its
    decoder emits - so a 1.1 reader turns a correct oracle value into `True` and
    reports a disagreement the schema does not have.
    """


_CoreBoolLoader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_CoreBoolLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def load_codec(codec_path):
    """Read a <device>-codec.yaml, or {} when it cannot be read."""
    try:
        return (
            yaml.load(codec_path.read_text(encoding="utf-8"), Loader=_CoreBoolLoader)
            or {}
        )
    except (OSError, yaml.YAMLError):
        return {}


def declared_examples(codec_path):
    """Read the uplink examples the vendor declares for a device."""
    return (load_codec(codec_path).get("uplinkDecoder") or {}).get("examples") or []


def find_codec(vendor_dir, stem):
    """Locate the codec file for a schema stem.

    Usually `<stem>-codec.yaml`, but some vendors put a firmware suffix after the
    word - dnt's `dnt-lw-wsci-codec-2-1-1.yaml` belongs to `dnt-lw-wsci-2-1-1`.
    """
    direct = vendor_dir / ("%s-codec.yaml" % stem)
    if direct.exists():
        return direct
    for candidate in sorted(vendor_dir.glob("*-codec-*.yaml")):
        if candidate.stem.replace("-codec-", "-", 1) == stem:
            return candidate
    # Some vendors name the file without the "-codec" infix: pallidus `codec-mr1.yaml`,
    # mcci `codec-catena4610.yaml`, ellenex `codec-single.yaml`, dragino `sw3l-lbcodec.yaml`.
    # Fall back to any codec file whose uplink decoder is <stem>.js, as the TTN device
    # repository itself links them.
    for candidate in sorted(vendor_dir.rglob("*codec*.yaml")):
        if candidate.stem == stem:
            return candidate
        name = (load_codec(candidate).get("uplinkDecoder") or {}).get("fileName") or ""
        if Path(name).stem == stem:
            return candidate
    return direct


def find_decoder(vendor_dir, codec_path, stem):
    """Locate the vendor JavaScript the codec file names, falling back to <stem>.js.

    The codec's `uplinkDecoder.fileName` is authoritative, and may sit in a
    subdirectory (netvox keeps every decoder under `payload/`).
    """
    name = (load_codec(codec_path).get("uplinkDecoder") or {}).get("fileName")
    if name and (vendor_dir / name).exists():
        return vendor_dir / name
    return vendor_dir / ("%s.js" % stem)


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
    code = js_path.read_text(encoding="utf-8", errors="replace")
    if "\n" not in code and code.count("\\n") > 3:
        # Some TTN files hold the whole decoder on one line with literal `\n` escapes
        # (dragino's -LB codecs), which no engine loads as written. The intent is plain, so
        # the escapes are undone, as the registry's oracle does; otherwise the vendor
        # decoder silently never ran for these units.
        code = (
            code.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
        )
    script = "%s\nconst REQUESTS = %s;\n%s" % (
        code,
        json.dumps(requests),
        driver,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(script)
        temp = handle.name
    try:
        proc = subprocess.run(
            ["node", temp], capture_output=True, text=True, timeout=60
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    finally:
        Path(temp).unlink(missing_ok=True)


# PS-512: a vendor codec's string rendering of a number - `(x / 10).toFixed(2)` - is
# compared by value. "1e3" and "3.1(low battery)" stay strings, and so does every string
# compared against a string.
_DECIMAL = re.compile(r"-?\d+(\.\d+)?\Z")
# PS-512 as amended by CR-2026-119: a unit symbol begins with a letter, ℃, °, % or /, and
# may continue with those, superscript digits (² ³) and spaces ("3.073mw/cm ²"). It never
# begins with a digit, so a second number is never read as part of a unit, and
# "3.1(low battery)" stays a string.
_UNIT = r"[A-Za-z°℃%/][A-Za-z°℃%/²³ ]*"
# A decimal followed only by a unit symbol ("257mm", "18℃", "-70dBm", "80 %").
_DECIMAL_UNIT = re.compile(r"(-?\d+(?:\.\d+)?)\s*" + _UNIT + r"\Z")
# PS-512 as amended by CR-2026-108: "0x" and hex digits followed by a decimal, optionally
# with a unit symbol ("0x0a 10 Min", "0x07 7"), renders the decimal; a bare "0x01" renders
# its integer value.
_HEX_DECIMAL = re.compile(
    r"0[xX][0-9A-Fa-f]+\s+(-?\d+(?:\.\d+)?)(?:\s*" + _UNIT + r")?\Z"
)
_HEX_BARE = re.compile(r"0[xX]([0-9A-Fa-f]+)\Z")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def as_number(want, got):
    """`want` as the number it denotes where it is a PS-512 rendering and `got` a number.

    Descends into objects and lists, so a rendering nested inside a reported object
    (`{"unit": "W", "value": "1.70"}`) is compared by value too.
    """
    if isinstance(want, dict) and isinstance(got, dict):
        return {k: as_number(v, got[k]) if k in got else v for k, v in want.items()}
    if isinstance(want, list) and isinstance(got, list) and len(want) == len(got):
        return [as_number(w, g) for w, g in zip(want, got)]
    if _is_number(got) and isinstance(want, str):
        text = want.strip()
        # The 0x forms first: "0xff" would otherwise read as 0 with the unit "xff".
        h = _HEX_BARE.match(text)
        if h:
            return float(int(h.group(1), 16))
        if text[:2].lower() == "0x":
            m = _HEX_DECIMAL.match(text)
            return float(m.group(1)) if m else want
        if _DECIMAL.match(text):
            return float(text)
        m = _DECIMAL_UNIT.match(text)
        if m:
            return float(m.group(1))
    return want


def _hex(payload):
    return re.sub(r"[^0-9a-f]", "", str(payload).lower())


def corrections(schema):
    """{(payload hex, fPort): {field, ...}} for the schema's `vendor-codec-corrected` vectors.

    PS-514: such a vector says which fields the vendor codec gets wrong for its payload, so a
    comparison against that codec on the same payload skips exactly those fields. A field
    may be a `/`-joined path to a nested member (CR-2026-120); `skip_corrected` drops
    exactly that member. A vector expecting an error carries one entry without `field`
    (PS-638), recorded as None: the codec's whole output for that payload is corrected.
    """
    out = {}
    for vec in (schema or {}).get("test_vectors") or []:
        if not isinstance(vec, dict) or vec.get("source") != "vendor-codec-corrected":
            continue
        port = vec.get("fPort", vec.get("fport", vec.get("port")))
        names = {
            c.get("field") for c in vec.get("correction") or [] if isinstance(c, dict)
        }
        out.setdefault((_hex(vec.get("payload", "")), port), set()).update(names)
    return out


def _drop_member(value, segments):
    """`value` with the member at `segments` removed: popped from an object, and set to
    None in an array so that the indices after it still line up."""
    if not segments:
        return value
    head, rest = segments[0], segments[1:]
    if isinstance(value, dict) and head in value:
        copy = dict(value)
        if rest:
            copy[head] = _drop_member(value[head], rest)
        else:
            del copy[head]
        return copy
    if isinstance(value, list) and head.isdigit() and int(head) < len(value):
        copy = list(value)
        copy[int(head)] = _drop_member(value[int(head)], rest) if rest else None
        return copy
    return value


def skip_corrected(expected, got, fields):
    """(expected, got) with the members a PS-514 correction names left out of both.

    A plain name skips that top-level key; a path (`payload/device/batteryLevel`,
    `readings/0/value`) skips exactly that member and compares its siblings
    (CR-2026-120).
    """
    for field in fields or ():
        if not isinstance(field, str):
            continue
        if field in expected or field in got:
            segments = [field]
        else:
            segments = correction_path(field)
        expected = _drop_member(expected, segments)
        got = _drop_member(got, segments)
    return expected, got


def compare(schema, payload, fport, expected):
    """Return a list of disagreements between our decode and `expected`."""
    skip = corrections(schema).get((payload.hex(), fport), set())
    try:
        result = SchemaInterpreter(schema).decode(payload, fPort=fport)
    except Exception as exc:  # noqa: BLE001 - report, do not abort the sweep
        return ["our decoder raised %s: %s" % (type(exc).__name__, exc)]
    if not result.success:
        if None in skip:
            # PS-514/PS-638 (CR-2026-197): a vector expecting this error records the
            # codec's whole output as corrected, so the failed decode is the schema's ruling.
            return []
        return ["our decode failed: %s" % result.errors[:2]]
    got = {k: v for k, v in result.data.items() if not k.startswith("_")}
    expected, got = skip_corrected(expected or {}, got, skip)
    problems = []
    for key, want in expected.items():
        if want is None and key not in got:
            # PS-043: a null is absence, so a vendor null against a key we omit is
            # agreement. Nested nulls reach expected_fields_match through values_match,
            # which applies the same rule; a null against a value we report is not.
            continue
        if key not in got:
            problems.append("%s: vendor %r, ours <missing>" % (key, want))
            continue
        match, _message = values_match(
            as_number(want, got[key]), got[key], CONFORMANCE_TOLERANCE
        )
        if not match:
            problems.append("%s: vendor %r, ours %r" % (key, want, got[key]))
    return problems


def check_schema(path, vendor_dir, use_decoder):
    """Return (status, problems) for one schema."""
    codec = find_codec(vendor_dir, path.stem)
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
        declared = example.get("output") or {}
        if declared.get("errors") and not declared.get("data"):
            # The vendor declares that this payload fails, typically on an fPort the
            # codec does not handle. That is an expectation too: our decode must fail. It
            # was compared as data instead, so every port-based schema "disagreed" with
            # the vendor's own unknown-FPort example.
            try:
                succeeded = (
                    SchemaInterpreter(schema).decode(payload, fPort=fport).success
                )
            except (
                Exception
            ):  # noqa: BLE001 - an undeclared fPort raises; that is a failure
                succeeded = False
            if succeeded:
                problems.append(
                    "%s (declared example): the vendor declares an error (%s), and our "
                    "decode succeeded" % (payload.hex(), declared["errors"][0])
                )
            continue
        expected = declared.get("data") or {}
        problems += [
            "%s (declared example): %s" % (payload.hex(), issue)
            for issue in compare(schema, payload, fport, expected)
        ]

    # Optionally re-decode the same payloads with the vendor's own decoder, which
    # catches cases where TTN's declared output has drifted from its codec.
    js = find_decoder(vendor_dir, codec, path.stem)
    if use_decoder and not js.exists():
        problems.append("note: vendor decoder %s not found, not compared" % js.name)
    elif use_decoder:
        requests = [
            {
                "hex": bytes(e["input"]["bytes"]).hex(),
                "fPort": e["input"].get("fPort", 85),
            }
            for e in examples
        ]
        results = run_vendor_decoder(js, requests)
        if not results:
            problems.append(
                "note: vendor decoder %s did not run, not compared" % js.name
            )
        else:
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

    # A note records a comparison that could not be made; it is printed, so the
    # skip is visible, but it is not a disagreement.
    real = [p for p in problems if not p.startswith("note: ")]
    return ("agrees" if not real else "disagrees"), problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--devices-repo", required=True, help="lorawan-devices checkout"
    )
    parser.add_argument(
        "--vendor", required=True, help="vendor directory, e.g. milesight-iot"
    )
    parser.add_argument(
        "--schema-dir", required=True, help="our schema directory for that vendor"
    )
    parser.add_argument("--schema", help="check a single schema by stem")
    parser.add_argument(
        "--no-vendor-decoder",
        action="store_true",
        help="compare only against declared examples, skipping node",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="list every disagreement"
    )
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
