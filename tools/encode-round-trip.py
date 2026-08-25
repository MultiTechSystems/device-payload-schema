#!/usr/bin/env python3
"""Every corpus vector through `encode(decode(payload))`, with a reason where it differs.

`tests/test_encode_round_trip.py` and the Java, Go and C# round-trip tests report a
count - "1143 of 1237" - and a per-shape breakdown. Neither says *why* the rest differ,
so the residue has never been auditable: a number that large reads as an encoder full of
holes, and most of it is information the decode genuinely did not keep.

This classifies each difference instead. A vector is `inherent` where the payload cannot
be recovered from the decoded output no matter how good the encoder is, and `unexplained`
otherwise. The second list is the one worth working on, and it is meant to stay short:

    python tools/encode-round-trip.py             # summary and the unexplained list
    python tools/encode-round-trip.py --all       # every difference, with its reason
    python tools/encode-round-trip.py --json out.json

The `inherent` reasons, each of which is a decision recorded elsewhere:

  undecoded-bytes   The decode reported a warning about bytes it could not read - an
                    unknown TLV tag (PS-301/PS-302), a repeat stopped at its `max`. What
                    was never decoded cannot be re-encoded.
  internal-field    A field whose name begins with `_` is decoded as a variable and not
                    reported, so its bytes are not in the output. mla20's fourth header
                    byte is the corpus's example: it holds the TLV length, the vendor
                    decoder skips it, and so does the schema.
  skip-field        `type: skip` has no output by definition (PS-014).
  lossy-value       A `lookup`/`enum` `default` label stands for every unmapped value
                    (PS-269), and a rounding or `sqrt` stage discards precision. The
                    encoders say so rather than guessing.
  undescribed-bits  A `version_string` or `bitfield_string` whose `parts` do not cover
                    every bit of the field it reads: the uncovered bits are not in the
                    output. ws50x's `v11.1` leaves the low nibble undescribed.
  ambiguous-case    Several TLV or match cases carry the same field names, so the tag that
                    produced them is not recoverable from the names alone.

One reason is *not* inherent and is reported separately, because it is a gap rather than a
loss:

  templated-name    `name_from` reports a field under a templated key - `channel_3_reading`
                    for a schema-declared `reading` - and the encoders look the value up by
                    the declared name, so they find nothing. The template's inputs are in
                    the decoded output, so this one is recoverable and nobody has done it.
"""

import argparse
import collections
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import yaml  # noqa: E402

from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import is_encode_vector  # noqa: E402

CORPUS = REPO_ROOT / "schemas" / "devices"

INHERENT = (
    "undecoded-bytes",
    "internal-field",
    "skip-field",
    "lossy-value",
    "undescribed-bits",
    "ambiguous-case",
)

#: Recoverable in principle, so counted apart from the inherent set.
FIXABLE = ("templated-name",)

#: What a decode says when it could not read part of the payload. CR-2026-013 and
#: CR-2026-021 put these there; they are the evidence this tool reads.
UNREAD_MARKERS = ("undecoded", "unread", "discarded", "could not be delimited")


def walk(node):
    """Every mapping in a schema, so a construct can be looked for anywhere."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def schema_traits(schema):
    """What a schema carries that can make a payload unrecoverable."""
    traits = set()
    for node in walk(schema):
        name = node.get("name")
        if isinstance(name, str) and name.startswith("_") and node.get("type") != "number":
            traits.add("internal-field")
        if node.get("type") == "skip":
            traits.add("skip-field")
        if node.get("type") in ("version_string", "bitfield_string"):
            covered = 0
            for part in node.get("parts") or []:
                if isinstance(part, list) and len(part) >= 2:
                    covered += int(part[1])
            width = int(node.get("length", 1)) * 8
            if covered < width:
                traits.add("undescribed-bits")
        # A `default` beside a lookup or enum, or inside the lookup table itself, stands
        # for every value the table does not list (PS-269), so the original is gone.
        if node.get("default") is not None and (
                node.get("lookup") is not None or node.get("values") is not None
                or node.get("enum") is not None):
            traits.add("lossy-value")
        for key in ("lookup", "enum", "values"):
            table = node.get(key)
            if isinstance(table, dict) and any(
                    str(k).lower() == "default" for k in table):
                traits.add("lossy-value")
        # A stage that discards precision: `round` as much as `sqrt`.
        for stage in node.get("transform") or []:
            if not isinstance(stage, dict):
                continue
            if stage.get("op") in ("round", "sqrt", "log", "log10", "floor", "ceiling"):
                traits.add("lossy-value")
            if any(k in stage for k in ("sqrt", "log", "log10", "round")):
                traits.add("lossy-value")
        if node.get("name_from"):
            traits.add("templated-name")
        # A repeat with a ceiling can stop before the payload does, and a match that
        # skips an unmatched value reports nothing for the bytes it passed over. Neither
        # emits a warning, so the trait is the only evidence.
        if node.get("type") == "repeat" and node.get("max") is not None:
            traits.add("undecoded-bytes")
        match = node.get("match")
        if isinstance(match, dict) and match.get("default") == "skip":
            traits.add("undecoded-bytes")
        if isinstance(tlv := node.get("tlv"), dict) and tlv.get("unknown") == "raw":
            # `unknown_tags` holds the captured bytes, and no encoder writes them back.
            traits.add("undecoded-bytes")
        tlv = node.get("tlv")
        if isinstance(tlv, dict):
            signatures = collections.Counter()
            for body in (tlv.get("cases") or {}).values():
                if isinstance(body, list):
                    names = tuple(sorted(
                        f.get("name") for f in body
                        if isinstance(f, dict) and f.get("name")))
                    if names:
                        signatures[names] += 1
            if any(count > 1 for count in signatures.values()):
                traits.add("ambiguous-case")
    return traits


def classify(decoded, traits):
    """Why this vector cannot round-trip, or None where nothing explains it."""
    if decoded.warnings:
        # CR-2026-013 and CR-2026-021 made the decode say what it could not read. That
        # statement is exactly the evidence needed here.
        for warning in decoded.warnings:
            if any(marker in warning for marker in UNREAD_MARKERS):
                return "undecoded-bytes"
    for trait in INHERENT + FIXABLE:
        if trait in traits:
            return trait
    return None


def run():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true",
                        help="list every difference, not only the unexplained ones")
    parser.add_argument("--json", metavar="PATH", help="write the full result as JSON")
    args = parser.parse_args()

    rows = []
    counts = collections.Counter()

    for path in sorted(CORPUS.rglob("*.yaml")):
        try:
            schema = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        if not isinstance(schema, dict) or not schema.get("test_vectors"):
            continue
        traits = schema_traits(schema)
        for vector in schema["test_vectors"]:
            if is_encode_vector(vector) or not vector.get("payload"):
                continue
            want = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            port = vector.get("fport", vector.get("fPort"))
            name = vector.get("name", "?")
            try:
                decoder = SchemaInterpreter(schema)
                decoded = decoder.decode(want, port) if port else decoder.decode(want)
                if decoded.errors:
                    counts["decode-error"] += 1
                    continue
                encoder = SchemaInterpreter(schema)
                result = (encoder.encode(decoded.data, port) if port
                          else encoder.encode(decoded.data))
            except Exception as exc:
                counts["exception"] += 1
                rows.append({"schema": path.name, "vector": name,
                             "kind": "exception", "reason": "unexplained",
                             "detail": f"{type(exc).__name__}: {exc}"[:120]})
                continue

            got = bytes(result.payload)
            if not result.errors and got == want:
                counts["round-trip"] += 1
                continue

            kind = ("error" if result.errors
                    else "length" if len(got) != len(want) else "bytes")
            counts[kind] += 1
            reason = classify(decoded, traits) or "unexplained"
            counts[reason] += 1
            detail = (result.errors[0][:120] if result.errors
                      else f"want {want.hex()} got {got.hex()}")
            rows.append({"schema": path.name, "vector": name, "kind": kind,
                         "reason": reason, "detail": detail})

    total = counts["round-trip"] + counts["length"] + counts["bytes"] + counts["error"]
    print("=" * 74)
    print("ENCODE ROUND-TRIP")
    print("=" * 74)
    print(f"  {counts['round-trip']:>5} of {total} vectors re-encode to their payload")
    for kind in ("length", "bytes", "error"):
        if counts[kind]:
            print(f"  {counts[kind]:>5} {kind} differs")
    print()
    print("  Reasons:")
    for reason in INHERENT:
        if counts[reason]:
            print(f"  {counts[reason]:>5} {reason}")
    for reason in FIXABLE:
        if counts[reason]:
            print(f"  {counts[reason]:>5} {reason}  (recoverable, nobody has done it)")
    unexplained = [r for r in rows if r["reason"] == "unexplained"]
    print(f"  {len(unexplained):>5} unexplained")

    listing = rows if args.all else [
        r for r in rows if r["reason"] == "unexplained" or r["reason"] in FIXABLE]
    if listing:
        print()
        print(f"  {'schema':<38} {'vector':<30} kind")
        print("  " + "-" * 72)
        for row in listing:
            print(f"  {row['schema']:<38} {row['vector'][:30]:<30} {row['kind']}")
            print(f"      {row['reason']}: {row['detail']}")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(
            {"counts": dict(counts), "rows": rows}, indent=2) + "\n")
        print(f"\n  Wrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(run())
