#!/usr/bin/env python3
"""Write a per-vector corpus report for the Python interpreter or the generated TS013 codec.

The Go, Java and C# corpus runners write the same report when CORPUS_REPORT is set; this
produces it for the other two conformance paths, so tools/verdicts-gate.py can compare all
five vector by vector rather than by pass count. One entry per test vector:

    {"schema": "<path relative to the corpus root>", "index": <position in test_vectors>,
     "vector": "<name>", "status": "pass" | "fail" | "error" | "skip", "detail": "..."}

  pass   decoded, and every expected key (and `expected_warnings`, if given) matched
  fail   decoded, and a value or a warning differed - detail names the first
  error  the schema could not be loaded, or the decode (or codec) failed outright
  skip   nothing to decode: an encode vector, or no payload (PS-047). The language
         runners are decode-only, so every path skips the same vectors.

The Python path is the comparison `tests/test_corpus_conformance.py` makes (the same
`values_match` tolerance and `warnings_match`), and the TS013 path is
`tools/vector-verdicts.py`'s generated-codec runner, restricted to decode vectors.

Usage:
    python3 tools/corpus-report.py --impl python --out build/verdicts/python.json
    python3 tools/corpus-report.py --impl ts013 --out build/verdicts/ts013.json \\
        --only decentlab/dl-5tm.yaml,dragino/laq4.yaml
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from score_schema import CONFORMANCE_TOLERANCE  # noqa: E402
from validate_schema import is_encode_vector, values_match, warnings_match  # noqa: E402

CORPUS = REPO_ROOT / "schemas" / "devices"
DETAIL_LIMIT = 200
IMPLEMENTATIONS = ("python", "ts013")

Entry = Dict[str, Any]


def relative(path: str) -> str:
    """A schema path in the form every report uses."""
    path = path.replace("\\", "/")
    prefix = "schemas/devices/"
    return path[len(prefix) :] if path.startswith(prefix) else path


def parse_only(text: Optional[str]) -> Optional[Set[str]]:
    if not text:
        return None
    return {relative(item.strip()) for item in text.split(",") if item.strip()}


def entry(schema: str, index: int, vector: Any, status: str, detail: str = "") -> Entry:
    name = vector.get("name", "") if isinstance(vector, dict) else ""
    return {
        "schema": schema,
        "index": index,
        "vector": "" if name is None else str(name),
        "status": status,
        "detail": (detail or "")[:DETAIL_LIMIT],
    }


def is_decodable(vector: Any) -> bool:
    """Whether a vector is a decode vector with a payload, the only kind the runners run."""
    if not isinstance(vector, dict) or is_encode_vector(vector):
        return False
    payload = vector.get("payload")
    return payload is not None and str(payload).strip() != ""


def corpus_schemas(
    root: Path, only: Optional[Set[str]] = None
) -> Iterator[Tuple[str, Optional[dict], List[Any]]]:
    """Yield (relative path, schema, vectors) for every corpus schema that has vectors.

    A file that is not valid YAML yields no vectors: nothing can say what they were, so
    the gate reports whatever the other implementations recorded for it as missing here.
    """
    for path in sorted(root.rglob("*.yaml")):
        rel = relative(path.relative_to(root).as_posix())
        if only is not None and rel not in only:
            continue
        try:
            schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(schema, dict):
            continue
        vectors = schema.get("test_vectors") or []
        if vectors:
            yield rel, schema, list(vectors)


def python_verdict(schema: dict, vector: dict) -> Tuple[str, str]:
    """The verdict `tests/test_corpus_conformance.py` reaches for one decode vector."""
    try:
        payload = bytes.fromhex(str(vector.get("payload", "")).replace(" ", ""))
    except ValueError as exc:
        return "error", "payload: %s" % exc
    fport = vector.get("fPort") or vector.get("fport")
    try:
        result = SchemaInterpreter(schema).decode(payload, fPort=fport)
    except Exception as exc:  # a raising decoder is an error, not a crash of this tool
        return "error", "%s: %s" % (type(exc).__name__, exc)
    if not result.success:
        return "error", "; ".join(str(e) for e in result.errors[:2])
    for key, want in (vector.get("expected") or {}).items():
        if key not in result.data:
            return "fail", "%s missing" % key
        ok, message = values_match(want, result.data[key], CONFORMANCE_TOLERANCE)
        if not ok:
            return "fail", "%s: %s" % (key, message)
    ok, message = warnings_match(vector.get("expected_warnings"), result.warnings)
    if not ok:
        return "fail", message
    return "pass", ""


def python_report(root: Path = CORPUS, only: Optional[Set[str]] = None) -> List[Entry]:
    out = []  # type: List[Entry]
    for rel, schema, vectors in corpus_schemas(root, only):
        for index, vector in enumerate(vectors):
            if not is_decodable(vector):
                out.append(
                    entry(rel, index, vector, "skip", "no payload (encode vector)")
                )
                continue
            status, detail = python_verdict(schema, vector)
            out.append(entry(rel, index, vector, status, detail))
    return out


def _vector_verdicts():
    """tools/vector-verdicts.py, whose file name is not an importable module name."""
    spec = importlib.util.spec_from_file_location(
        "vector_verdicts", str(REPO_ROOT / "tools" / "vector-verdicts.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def ts013_report(root: Path = CORPUS, only: Optional[Set[str]] = None) -> List[Entry]:
    verdicts = _vector_verdicts()
    out = []  # type: List[Entry]
    for rel, schema, vectors in corpus_schemas(root, only):
        decodable = [(i, v) for i, v in enumerate(vectors) if is_decodable(v)]
        results = {}  # type: Dict[int, Tuple[str, str]]
        if decodable:
            ran = verdicts.run_generated(schema, [v for _, v in decodable])
            for (index, _), (verdict, detail) in zip(decodable, ran):
                if verdict == verdicts.PASS:
                    results[index] = ("pass", "")
                elif verdict == verdicts.UNSUPPORTED:
                    results[index] = ("error", detail)
                else:
                    results[index] = ("fail", detail)
        for index, vector in enumerate(vectors):
            if index in results:
                status, detail = results[index]
                out.append(entry(rel, index, vector, status, detail))
            else:
                out.append(
                    entry(rel, index, vector, "skip", "no payload (encode vector)")
                )
    return out


def build_report(
    impl: str, root: Path = CORPUS, only: Optional[Set[str]] = None
) -> List[Entry]:
    if impl == "python":
        return python_report(root, only)
    if impl == "ts013":
        return ts013_report(root, only)
    raise ValueError("unknown implementation %r" % impl)


def write_report(entries: Sequence[Entry], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(list(entries), indent=1) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--impl", required=True, choices=IMPLEMENTATIONS)
    parser.add_argument("--out", required=True, help="JSON file to write")
    parser.add_argument(
        "--only", help="Comma-separated schema paths (relative to the corpus root)"
    )
    parser.add_argument(
        "--root", default=str(CORPUS), help="Corpus root (default: schemas/devices)"
    )
    args = parser.parse_args(argv)

    entries = build_report(args.impl, Path(args.root), parse_only(args.only))
    write_report(entries, Path(args.out))
    counts = {}  # type: Dict[str, int]
    for item in entries:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    summary = ", ".join("%s %d" % (k, counts[k]) for k in sorted(counts))
    print("%s: %d vectors (%s) -> %s" % (args.impl, len(entries), summary, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
