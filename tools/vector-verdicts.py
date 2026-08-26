#!/usr/bin/env python3
"""Execute every corpus test vector through both conformance paths and record a verdict.

A schema is conformant two ways, and the specification treats both as first class:

  interpreted   a schema-aware decoder reads the schema and the bytes (clause 1)
  generated     the schema is compiled to a TS013 JavaScript codec (clause 9)

The corpus runners exercise the interpreted path in five languages. Nothing exercised the
generated path over the corpus - it appeared only in the tests written for particular
change requests - so a construct the generator mishandled could pass every suite in the
repository while the codec a vendor actually ships returned the wrong value.

This produces the evidence a certification-style report needs: one verdict per vector per
path, with the disagreement between paths called out separately from an outright failure,
because the two mean different things. A vector that passes interpreted and fails
generated is a defect in the generator; one that fails both is a defect in the schema or
the language.

Usage:
    python3 tools/vector-verdicts.py                     # whole corpus, summary
    python3 tools/vector-verdicts.py --json verdicts.json
    python3 tools/vector-verdicts.py --schemas schemas/devices/dragino
"""

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import values_match, warnings_match  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
#: The path could not be exercised at all - no codec could be generated for the schema.
#: Distinct from FAIL: nothing was tested, so nothing is known.
UNSUPPORTED = "UNSUPPORTED"


#: Which schema construct exercises which specification clause. The clause headings are
#: the ones `extract-requirements.py` already records per requirement, so attribution
#: needs no second list of ids to keep in step - a requirement moved between clauses
#: follows its heading.
CONSTRUCT_CLAUSES = {
    "integer": ["Integer Type Requirements", "Decoded Value Types"],
    "word_ordered": ["Word-Ordered 32-bit Types"],
    "float": ["Floating Point Requirements", "Decoded Value Types"],
    "bitfield": ["Bitfield Requirements", "The `consume` Property"],
    "bool": ["Boolean Requirements", "Decoded Value Types"],
    "enum": ["Enum Requirements"],
    "string": ["String Type Requirements"],
    "bytes": ["Bytes Type Requirements"],
    "hex": ["Bytes Type Requirements"],
    "bitfield_string": ["bitfield_string Requirements"],
    "object": ["Object Requirements"],
    "repeat": ["Repeat Type Requirements"],
    "skip": ["Field Requirements"],
    "lookup": ["Lookup Table Requirements"],
    "modifier": ["Math Operation Requirements", "Transformation Requirements"],
    "transform": ["Transformation Requirements"],
    "polynomial": ["Polynomial Requirements"],
    "compute": ["Compute Requirements", "Computed Field Requirements"],
    "guard": ["Guard Requirements"],
    "valid_range": ["Valid Range"],
    "variable": ["Variable Requirements"],
    "match": ["Match Requirements", "Conditional Requirements"],
    "tlv": ["TLV Requirements"],
    # Attributed per vector rather than per schema: a `tlv` schema has unknown-tag
    # behaviour whether or not any vector asserts it, and 85 of 87 constructs in the
    # corpus went years without one. The clause is credited to the vectors that pin the
    # warning with `expected_warnings` (PS-305), which are the ones that would fail if the
    # behaviour changed.
    "unknown_tlv": ["Unknown Tag Handling"],
    "flagged": ["Flagged Requirements"],
    "ports": ["Ports Requirements", "Decoder Behavior"],
    "endian": ["Integer Type Requirements"],
    "unit": ["Output Format Requirements"],
    "semantic": ["Output Format Requirements"],
    "test_vector": ["Test Vector Requirements", "Expected Value Matching"],
    # Every schema that parses and decodes demonstrates the document-level rules: a
    # name, a positive version, and exactly one of `fields` or `ports`. These were
    # reported unexercised while all 162 schemas exercised them.
    "document": ["Document Structure", "Document Requirements"],
    "remaining": ["The `remaining` Keyword"],
    # An encode vector is the only evidence the downlink clauses can have from the corpus.
    "encode": ["Downlink Requirements", "Binary Conversion Requirements",
               "Encoding Test Vectors"],
    "integer_division": ["Integer Division and Modulo"],
    "name_from": ["Computed Field Names"],
}

#: Clauses the corpus cannot reach, whatever it contains, and why. A decode vector is
#: evidence about decoding; it says nothing about the encoder, about what a decoder does
#: with malformed input, or about the shape of generated code. Reported separately so
#: "not exercised" does not read as "untested" for clauses that need other evidence.
OUT_OF_SCOPE_CLAUSES = {
    "Decoder Safety": "negative input; needs malformed-payload cases",
    "Validator Safety": "negative input; needs invalid-schema cases",
    "Fuzz Testing Requirements": "covered by the fuzz targets, not the corpus",
    "TS013 Generation Requirements": "shape of generated code, not its output",
    "TS013 JavaScript Codecs": "shape of generated code, not its output",
    "AI-Generated Format Metadata": "authoring metadata; not observable from a decode",
    "Schema Versioning": "process requirement; not observable from a decode",
}

#: Schema keys that name a construct directly.
KEY_CONSTRUCTS = {
    "lookup": "lookup", "transform": "transform", "polynomial": "polynomial",
    "compute": "compute", "guard": "guard", "valid_range": "valid_range",
    "var": "variable", "match": "match", "tlv": "tlv", "flagged": "flagged",
    "ports": "ports", "endian": "endian", "unit": "unit", "semantic": "semantic",
    "ipso": "semantic", "senml": "semantic", "repeat": "repeat", "byte_group": "bitfield",
}

INTEGER_TYPES = {"u8", "u16", "u24", "u32", "u64", "s8", "s16", "s24", "s32", "s64",
                 "i8", "i16", "i32", "i64", "byte", "uint", "sint"}


def constructs_used(schema: dict) -> set:
    """The construct vocabulary a schema draws on, for attributing requirements.

    Read from the schema rather than from the decoded output: a field declaring `div`
    exercises the modifier requirements whether or not that particular vector's value
    happens to change.
    """
    found = {"document"}
    vectors = schema.get("test_vectors") or []
    if vectors:
        found.add("test_vector")
    if any("input" in v or "expected_payload" in v for v in vectors if isinstance(v, dict)):
        found.add("encode")

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in KEY_CONSTRUCTS:
                    found.add(KEY_CONSTRUCTS[key])
                if key in ("mult", "div", "add", "idiv", "mod"):
                    found.add("modifier")
                if key in ("idiv", "mod"):
                    found.add("integer_division")
                if key == "name_from":
                    found.add("name_from")
                if isinstance(value, str) and "remaining" in value:
                    found.add("remaining")
                if key == "type" and isinstance(value, str):
                    base = value.split("[")[0]
                    if "[" in value:
                        found.add("bitfield")
                    if base in ("u32le16", "s32le16"):
                        # An integer type, and the only exercise the word-ordered clause
                        # can get (PS-271).
                        found.add("integer")
                        found.add("word_ordered")
                    elif base in INTEGER_TYPES:
                        found.add("integer")
                    elif base in ("f16", "f32", "f64", "number"):
                        found.add("float")
                    elif base in ("bool", "enum", "string", "ascii", "hex", "bytes",
                                  "bitfield_string", "object", "repeat", "array", "skip"):
                        found.add("array" if base == "array" else base)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return found


@dataclass
class VectorVerdict:
    schema: str
    schema_file: str
    vector: str
    fport: int | None
    interpreted: str
    generated: str
    detail: str = ""
    constructs: list = field(default_factory=list)

    @property
    def agree(self) -> bool:
        return self.interpreted == self.generated


@dataclass
class Suite:
    verdicts: list = field(default_factory=list)

    def tally(self, path: str) -> dict:
        counts = {PASS: 0, FAIL: 0, UNSUPPORTED: 0}
        for verdict in self.verdicts:
            counts[getattr(verdict, path)] += 1
        return counts

    @property
    def disagreements(self) -> list:
        return [v for v in self.verdicts if not v.agree]


def vector_bytes(vector: dict) -> bytes:
    payload = vector.get("payload")
    if isinstance(payload, list):
        return bytes(payload)
    text = str(payload).replace(" ", "").replace("0x", "")
    return bytes.fromhex(text)


def vector_fport(vector: dict):
    for key in ("fport", "fPort", "port"):
        if vector.get(key) is not None:
            return vector[key]
    return None


def matches(expected: dict, actual: dict) -> tuple[bool, str]:
    """Whether every expected field is present and equal, and what differs if not.

    `values_match` returns `(ok, detail)`. This tested `not values_match(...)` on the
    tuple, and a two-element tuple is always truthy, so the condition could never hold:
    from the day the second return value was added until CR-2026-021 this function
    compared *key presence only*, and every value the two paths disagreed on was reported
    as a pass. The `0 vectors where the two paths disagree` line was that much weaker than
    it read, and it is how `max` on a repeat stayed invisible - the generated codec
    produced four records where every interpreter produced two, and both paths passed.
    """
    for name, want in (expected or {}).items():
        if name not in actual:
            return False, f"{name} missing"
        ok, detail = values_match(want, actual[name])
        if not ok:
            return False, f"{name}: {detail}"
    return True, ""


def is_encode_vector(vector: dict) -> bool:
    """An encode vector carries the values to encode, not the bytes to decode (PS-047)."""
    return "input" in vector or "expected_payload" in vector


def expected_bytes(vector: dict) -> bytes:
    want = vector.get("expected_payload")
    if isinstance(want, list):
        return bytes(want)
    return bytes.fromhex(str(want).replace(" ", "").replace("0x", ""))


def run_interpreted_encode(schema: dict, vector: dict) -> tuple:
    """Verdict for one encode vector from the interpreter's encoder."""
    try:
        result = SchemaInterpreter(schema).encode(
            vector.get("input") or {}, fPort=vector_fport(vector)
        )
    except Exception as exc:
        return FAIL, f"{type(exc).__name__}: {exc}"[:120]
    if not result.success:
        return FAIL, "; ".join(result.errors)[:120]
    want = expected_bytes(vector)
    if result.payload != want:
        return FAIL, f"want {want.hex()}, got {result.payload.hex()}"
    ok, detail = warnings_match(vector.get("expected_warnings"), result.warnings)
    return (PASS, "") if ok else (FAIL, detail)


def run_interpreted(schema: dict, vectors: list) -> list:
    """Verdict per vector from a schema-aware decoder."""
    out = []
    for vector in vectors:
        if is_encode_vector(vector):
            out.append(run_interpreted_encode(schema, vector))
            continue
        try:
            result = SchemaInterpreter(schema).decode(
                vector_bytes(vector), fPort=vector_fport(vector),
                # PS-324: a schema with a `metadata` block reads runtime context no
                # payload can supply. Absent, PS-312 says the block is ignored, which is
                # every vector in the corpus.
                input_metadata=vector.get("input_metadata"),
            )
            if not result.success:
                out.append((FAIL, "; ".join(result.errors)[:120]))
                continue
            ok, detail = matches(vector.get("expected"), result.data)
            if ok:
                # PS-307: the key is checked on this path and on the generated one, so a
                # warning only one of them reports is a disagreement rather than a pass.
                ok, detail = warnings_match(
                    vector.get("expected_warnings"), result.warnings
                )
            out.append((PASS if ok else FAIL, detail))
        except Exception as exc:  # a raising decoder is a failure, not a crash of this tool
            out.append((FAIL, f"{type(exc).__name__}: {exc}"[:120]))
    return out


def run_generated(schema: dict, vectors: list) -> list:
    """Verdict per vector from the schema's generated TS013 codec.

    One node process per schema rather than per vector: the corpus has over a thousand
    vectors and process start-up dominated everything else.
    """
    try:
        js = TS013Generator(schema).generate()
    except Exception as exc:
        return [(UNSUPPORTED, f"no codec: {type(exc).__name__}: {exc}"[:120])] * len(vectors)

    calls = []
    for vector in vectors:
        if is_encode_vector(vector):
            calls.append({
                "encode": True,
                "data": vector.get("input") or {},
                "fPort": vector_fport(vector) or 1,
            })
        else:
            calls.append({
                "encode": False,
                "bytes": list(vector_bytes(vector)),
                "fPort": vector_fport(vector) or 1,
            })
    driver = (
        js
        + "\nvar _out = [];\n"
        + f"var _calls = {json.dumps(calls)};\n"
        + "for (var i = 0; i < _calls.length; i++) {\n"
        + "  var c = _calls[i];\n"
        + "  try {\n"
        + "    if (c.encode) {\n"
        + "      var e = encodeDownlink({data: c.data, fPort: c.fPort});\n"
        + "      _out.push({bytes: e.bytes, errors: e.errors || [], warnings: e.warnings || []});\n"
        + "    } else {\n"
        + "      var r = decodeUplink(c);\n"
        + "      _out.push({data: r.data, errors: r.errors || [], "
        + "warnings: r.warnings || []});\n"
        + "    }\n"
        + "  } catch (e) { _out.push({data: null, bytes: null, errors: [String(e && e.message || e)]}); }\n"
        + "}\nconsole.log(JSON.stringify(_out));"
    )
    try:
        completed = subprocess.run(
            ["node", "-e", driver], capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        return [(FAIL, "codec timed out")] * len(vectors)
    if completed.returncode != 0:
        return [(FAIL, f"codec threw: {completed.stderr.strip()[:100]}")] * len(vectors)

    try:
        results = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return [(FAIL, "codec produced no JSON")] * len(vectors)

    out = []
    for vector, result in zip(vectors, results):
        if result.get("errors"):
            out.append((FAIL, "; ".join(result["errors"])[:120]))
            continue

        if is_encode_vector(vector):
            produced = result.get("bytes")
            if produced is None:
                warnings = "; ".join(result.get("warnings") or []) or "no bytes"
                out.append((UNSUPPORTED, f"codec cannot encode: {warnings}"[:120]))
                continue
            want = expected_bytes(vector)
            got = bytes(produced)
            if got != want:
                out.append((FAIL, f"want {want.hex()}, got {got.hex()}"))
                continue
            ok, detail = warnings_match(
                vector.get("expected_warnings"), result.get("warnings") or []
            )
            out.append((PASS if ok else FAIL, detail))
            continue

        ok, detail = matches(vector.get("expected"), result.get("data") or {})
        if ok:
            ok, detail = warnings_match(
                vector.get("expected_warnings"), result.get("warnings") or []
            )
        out.append((PASS if ok else FAIL, detail))
    return out


def collect(schema_root: Path) -> Suite:
    suite = Suite()
    for schema_file in sorted(schema_root.rglob("*.yaml")):
        try:
            schema = yaml.safe_load(schema_file.read_text())
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(schema, dict):
            continue
        vectors = schema.get("test_vectors") or []
        if not vectors:
            continue

        interpreted = run_interpreted(schema, vectors)
        generated = run_generated(schema, vectors)
        constructs = sorted(constructs_used(schema))
        has_tlv = "tlv" in constructs
        for vector, (i_verdict, i_detail), (g_verdict, g_detail) in zip(
            vectors, interpreted, generated
        ):
            vector_constructs = constructs
            if has_tlv and vector.get("expected_warnings") is not None:
                # This vector asserts what the decode said, not only what it read, so it
                # is evidence about unknown-tag handling in a way its siblings are not.
                vector_constructs = sorted(set(constructs) | {"unknown_tlv"})
            suite.verdicts.append(
                VectorVerdict(
                    schema=schema.get("name", schema_file.stem),
                    schema_file=str(schema_file.relative_to(REPO_ROOT)),
                    vector=vector.get("name", "unnamed"),
                    fport=vector_fport(vector),
                    interpreted=i_verdict,
                    generated=g_verdict,
                    detail=i_detail or g_detail,
                    constructs=vector_constructs,
                )
            )
    return suite


def print_summary(suite: Suite) -> None:
    interpreted = suite.tally("interpreted")
    generated = suite.tally("generated")
    total = len(suite.verdicts)

    print("=" * 66)
    print("CONFORMANCE VERDICTS BY PATH")
    print("=" * 66)
    print(f"\n{'Path':<14}{'Pass':>8}{'Fail':>8}{'Unsupported':>14}{'Rate':>8}")
    print("-" * 66)
    for label, counts in (("interpreted", interpreted), ("generated", generated)):
        tested = counts[PASS] + counts[FAIL]
        rate = f"{100 * counts[PASS] / tested:.1f}%" if tested else "n/a"
        print(
            f"{label:<14}{counts[PASS]:>8}{counts[FAIL]:>8}"
            f"{counts[UNSUPPORTED]:>14}{rate:>8}"
        )
    print(f"\n{total} vectors across {len({v.schema_file for v in suite.verdicts})} schemas")

    disagreements = suite.disagreements
    print(f"{len(disagreements)} vectors where the two paths disagree")
    for verdict in disagreements[:15]:
        print(
            f"  {verdict.schema:<28} {verdict.vector[:26]:<28}"
            f" interpreted={verdict.interpreted} generated={verdict.generated}"
        )
        if verdict.detail:
            print(f"      {verdict.detail[:96]}")
    if len(disagreements) > 15:
        print(f"  ... and {len(disagreements) - 15} more")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--schemas", default="schemas/devices",
                        help="Schema root to walk (default: schemas/devices)")
    parser.add_argument("--json", help="Write the full verdict list to this file")
    args = parser.parse_args()

    suite = collect(REPO_ROOT / args.schemas)
    if not suite.verdicts:
        print(f"No test vectors found under {args.schemas}")
        return 1

    print_summary(suite)

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "verdicts": [asdict(v) for v in suite.verdicts],
                    "interpreted": suite.tally("interpreted"),
                    "generated": suite.tally("generated"),
                    "construct_clauses": CONSTRUCT_CLAUSES,
                    "out_of_scope_clauses": OUT_OF_SCOPE_CLAUSES,
                },
                indent=2,
            )
        )
        print(f"\nVerdicts: {args.json}")

    # A path that cannot run is not a failure of this tool; a disagreement is a finding
    # to report, not an error either. Only exit non-zero where a vector failed outright.
    failures = suite.tally("interpreted")[FAIL] + suite.tally("generated")[FAIL]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
