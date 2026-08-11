#!/usr/bin/env python3
"""
compose_library_vectors.py - Make the schema library's test vectors runnable.

The files under `schemas/library/` are mostly definition catalogues rather than
schemas: they carry `definitions:` and a set of test vectors, but no top-level
`name:`/`fields:`, so an interpreter cannot decode them directly. Their vectors
are therefore verified by nothing - the corpus runners in all four languages walk
`schemas/devices/` only.

This composes each library vector into a standalone schema, so the whole library
is exercised in Python, Go, Java and C# by the runners that already exist.

Each vector names the definition it exercises one of three ways:

    command: set_alarm_req      the definition, by name (most files)
    (no key)                    inferred from the definition whose field names
                                match the vector's expected keys (lorawan_frames)
    hex: "02 1234 ..."          an alias for `payload:` (udp_packet_forwarder)

The definition's `fields:` list is spliced into the composed schema rather than
nested under it. Nesting - which is what `$ref` inlining produces - leaves a
container field with no `type: object`, so the interpreter never descends into it
and every field reports as missing.

Usage:
    python3 tools/compose_library_vectors.py            # write the composed corpus
    python3 tools/compose_library_vectors.py --check    # fail if out of date
"""

import argparse
import sys
from pathlib import Path

import yaml

class _QuotedStrDumper(yaml.SafeDumper):
    """Emits every string quoted.

    PyYAML leaves a numeric-looking string such as a hex EUI unquoted when its own
    resolver would not read it back as a number - but the runners in Go, Java and C#
    use different YAML implementations, and theirs resolve `0102030405060708` to an
    integer. A gateway EUI then arrived as 1.02030405060708e+14 in Go while the
    identically-shaped `0001020304050607` stayed a string, so one vector passed and
    its neighbour failed for a reason nothing in the schema explained.

    Quoting unconditionally removes the guesswork: a string stays a string in every
    parser that reads the composed corpus.
    """


def _quoted_str(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="'")


_QuotedStrDumper.add_representer(str, _quoted_str)


def _dump(schema) -> str:
    return yaml.dump(
        schema, Dumper=_QuotedStrDumper, sort_keys=False, allow_unicode=True
    )


REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "schemas" / "library"
#: Inside schemas/devices/ because that is the only tree the corpus runners walk.
OUTPUT = REPO_ROOT / "schemas" / "devices" / "_library-composed"

#: Vectors describing a JSON protocol message rather than a binary payload. The
#: payload schema language decodes bytes, so these are out of scope here.
JSON_ONLY = "json"

#: Not ours to publish: predates this work and is deliberately never committed.
SKIP_FILES = {"telemetry-v1.yaml"}


def field_names(fields):
    """Every output name a field list can produce, nested fields included."""
    names = set()
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        if field.get("name"):
            names.add(field["name"])
        for key in ("fields", "byte_group"):
            names |= field_names(field.get(key))
    return names


def infer_definition(definitions, expected):
    """Pick the definition whose fields best cover the vector's expected keys."""
    best, best_score = None, 0
    for name, defn in definitions.items():
        if not isinstance(defn, dict) or not defn.get("fields"):
            continue
        score = len(field_names(defn["fields"]) & set(expected))
        if score > best_score:
            best, best_score = name, score
    return best if best_score == len(expected) else None


def compose(source, document, vector, definitions):
    """Build a standalone schema for one library vector, or None if not possible."""
    if JSON_ONLY in vector:
        return None, "describes a JSON message, not a binary payload"

    payload = vector.get("payload") or vector.get("hex")
    if not payload:
        return None, "no payload"

    expected = vector.get("expected") or {}
    name = vector.get("command") or infer_definition(definitions, expected)
    if not name or name not in definitions:
        return None, "no definition named by `command` and none inferable"

    defn = definitions[name]
    if not isinstance(defn, dict) or not defn.get("fields"):
        return None, "definition %r has no fields" % name

    composed = {
        "name": "%s__%s" % (source.stem.replace("-", "_"), vector["name"]),
        "description": "Composed from %s#/definitions/%s so the library's test "
                       "vectors run in every implementation."
                       % (source.relative_to(REPO_ROOT), name),
        "endian": document.get("endian", "big"),
        # Spliced, not nested: a nested container without `type: object` is never
        # descended into and every field reports as missing.
        "fields": defn["fields"],
    }
    # Carried so a definition using a local `#/definitions/...` ref still resolves.
    if document.get("definitions"):
        composed["definitions"] = document["definitions"]

    tv = {"name": vector["name"], "payload": payload}
    if vector.get("description"):
        tv["description"] = vector["description"]
    # The expected values were written alongside the definitions in the library,
    # not produced by running this interpreter over them.
    tv["source"] = vector.get("source", "spec-example")
    tv["expected"] = expected
    composed["test_vectors"] = [tv]
    return composed, None


def verify(schema):
    """Decode the composed schema's own vector. Returns None on success, else why.

    These vectors had never been executed by anything, so some of them encode
    mistakes rather than describing the device. One that does not decode is
    quarantined into KNOWN-ISSUES.md rather than added to the corpus, so the build
    stays honest about what is actually verified.
    """
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from schema_interpreter import SchemaInterpreter  # noqa: E402
    # The same comparison the corpus runners use, so this cannot disagree with
    # them about whether a vector passes.
    from validate_schema import values_match  # noqa: E402

    vector = schema["test_vectors"][0]
    try:
        payload = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
        result = SchemaInterpreter(schema).decode(payload)
    except Exception as exc:                                    # noqa: BLE001
        return "decode raised: %s" % str(exc)[:80]
    if result.errors:
        return "decode errors: %s" % str(result.errors[0])[:80]
    for key, want in (vector.get("expected") or {}).items():
        if key not in result.data:
            return "%s missing from output" % key
        got = result.data[key]
        matched = values_match(want, got)
        if isinstance(matched, tuple):
            matched = matched[0]
        if not matched:
            return "%s: vector says %r, decodes to %r" % (key, want, got)
    return None


def build():
    composed, skipped = {}, []
    for source in sorted(LIBRARY.rglob("*.yaml")):
        if source.name in SKIP_FILES:
            continue
        try:
            document = yaml.safe_load(source.read_text()) or {}
        except yaml.YAMLError as exc:
            skipped.append((source.name, "-", "unparseable: %s" % exc))
            continue
        vectors = document.get("test_vectors") or []
        if not vectors:
            continue
        definitions = document.get("definitions") or {}
        for vector in vectors:
            if not isinstance(vector, dict) or not vector.get("name"):
                continue
            if not definitions and document.get("fields"):
                # Already a standalone schema; the runners still never see it.
                schema = dict(document)
                schema["test_vectors"] = [vector]
                schema["name"] = "%s__%s" % (source.stem.replace("-", "_"), vector["name"])
                composed[schema["name"]] = schema
                continue
            schema, why = compose(source, document, vector, definitions)
            if schema is None:
                skipped.append((source.name, vector["name"], why))
            else:
                composed[schema["name"]] = schema
    return composed, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the composed corpus is out of date")
    args = parser.parse_args()

    composed, skipped = build()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    stale = False

    # Quarantine anything that does not decode: these vectors had never been run,
    # so a mismatch is as likely to be a wrong vector as a wrong schema, and both
    # need a human. Adding them to the corpus would just paint the build red.
    quarantined = []
    for name in sorted(composed):
        why = verify(composed[name])
        if why:
            quarantined.append((name, why))
            del composed[name]

    wanted = {"%s.yaml" % name: _dump(schema) for name, schema in composed.items()}
    if quarantined:
        lines = ["# Library vectors that do not decode", "",
                 "Generated by `tools/compose_library_vectors.py` - do not edit by hand.",
                 "",
                 "These vectors are in `schemas/library/` but are not part of the corpus,",
                 "because they do not decode against the definition they name. Nothing had",
                 "ever executed them, so each is either a wrong expected value or a wrong",
                 "schema, and telling those apart needs a source for the device.", ""]
        for name, why in quarantined:
            lines.append("- **%s** - %s" % (name, why))
        lines.append("")
        wanted["KNOWN-ISSUES.md"] = "\n".join(lines)
    wanted["README.md"] = (
        "# Composed library vectors\n\n"
        "Generated by `tools/compose_library_vectors.py` - do not edit by hand.\n\n"
        "The files under `schemas/library/` are definition catalogues, not schemas:\n"
        "they have no top-level `name:`/`fields:`, so nothing can decode them and\n"
        "their test vectors were verified by no implementation. This directory is\n"
        "each of those vectors composed into a standalone schema, placed inside\n"
        "`schemas/devices/` because that is the only tree the corpus runners walk.\n\n"
        "Regenerate with `python3 tools/compose_library_vectors.py`; CI checks it is\n"
        "current with `--check`.\n")

    existing = {p.name: p.read_text() for p in OUTPUT.iterdir() if p.is_file()}
    if existing != wanted:
        stale = True
        if not args.check:
            for path in OUTPUT.iterdir():
                if path.is_file():
                    path.unlink()
            for filename, text in wanted.items():
                (OUTPUT / filename).write_text(text)

    print("composed %d library vectors into %s"
          % (len(composed), OUTPUT.relative_to(REPO_ROOT)))
    if quarantined:
        print("quarantined %d that do not decode (see KNOWN-ISSUES.md):" % len(quarantined))
        for name, why in quarantined:
            print("  %-52s %s" % (name, why))
    if skipped:
        print("skipped %d:" % len(skipped))
        for source, vector, why in skipped:
            print("  %-32s %-24s %s" % (source, vector, why))

    if args.check and stale:
        print("\nOUT OF DATE - run: python3 tools/compose_library_vectors.py", file=sys.stderr)
        return 1
    if args.check:
        print("composed corpus is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
