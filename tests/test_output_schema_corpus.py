"""Corpus-wide guarantees for the generated output JSON Schema.

Two properties, both checked against real decoder output rather than against a reading
of the generator:

1. Every key a decoder reports is a declared property.
2. Every reported value satisfies the type its property declares.

The first is the one that kept finding gaps - `$ref` was unresolved and the `match`
construct was not traversed at all, each of which silently omitted whole groups of
fields. Both are ratcheted with a floor so the checks cannot pass by finding nothing to
look at.
"""

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import generate_output_schema  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402

# `name_from` builds its output key at run time from a decoded value, so it cannot be
# declared from the schema alone. This is inherent, not a gap to close.
KNOWN_UNDECLARED = {"name-from.yaml"}


def json_types(value):
    """The JSON Schema type names a Python value satisfies."""
    if isinstance(value, bool):
        return {"boolean"}
    if isinstance(value, int):
        return {"integer", "number"}
    if isinstance(value, float):
        return {"number"}
    if isinstance(value, str):
        return {"string"}
    if isinstance(value, list):
        return {"array"}
    if isinstance(value, dict):
        return {"object"}
    if value is None:
        return {"null"}
    return set()


def decoded_corpus():
    """Yield (path, declared properties, [decoded data, ...]) per schema.

    Aggregated per schema rather than per vector: a schema whose first vector reports
    every declared key and whose second reports an extra one is a schema with a gap, and
    counting vectors would have let it land in both the "complete" and "short" tallies.
    """
    for path in sorted(REPO_ROOT.joinpath("schemas").rglob("*.yaml")):
        try:
            schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(schema, dict) or not schema.get("test_vectors"):
            continue
        try:
            declared = generate_output_schema(schema).get("properties", {})
        except Exception:
            continue
        decoded = []
        for vector in schema["test_vectors"]:
            try:
                result = SchemaInterpreter(schema).decode(
                    bytes.fromhex(str(vector["payload"]).replace(" ", "")),
                    fPort=vector.get("fPort", vector.get("fport")),
                )
            except Exception:
                continue
            decoded.append(result.data)
        if decoded:
            yield path, declared, decoded


def undeclared_by_schema():
    out = {}
    for path, declared, decoded in decoded_corpus():
        missing = set()
        for data in decoded:
            missing |= {k for k in data if k != "_quality" and k not in declared}
        if missing:
            out[path.name] = missing
    return out


def test_every_reported_key_is_declared():
    short = undeclared_by_schema()
    unexpected = set(short) - KNOWN_UNDECLARED
    assert not unexpected, {k: sorted(short[k])[:6] for k in unexpected}


def test_enough_schemas_are_fully_declared():
    """A floor, so the check above cannot pass by looking at nothing.

    173 of 174 as of the `match` fix - every schema with a vector that actually decodes,
    bar name-from.yaml. Schemas whose vectors all fail to decode are excluded rather than
    counted as complete: they prove nothing about whether their properties are declared,
    and counting them inflated this figure by two the first time I measured it.
    """
    total = sum(1 for _ in decoded_corpus())
    complete = total - len(undeclared_by_schema())
    assert complete >= 173, f"{complete} of {total}"


def test_reported_values_satisfy_their_declared_type():
    problems = {}
    checked = 0
    for path, declared, decoded in decoded_corpus():
        for data in decoded:
            for key, value in data.items():
                if key == "_quality" or key not in declared:
                    continue
                declared_type = declared[key].get("type")
                if declared_type is None:
                    continue
                allowed = (
                    set(declared_type)
                    if isinstance(declared_type, list)
                    else {declared_type}
                )
                checked += 1
                if not (json_types(value) & allowed):
                    problems.setdefault(path.name, set()).add(
                        f"{key}: {type(value).__name__} not in {sorted(allowed)}"
                    )
    assert not problems, {k: sorted(v)[:4] for k, v in problems.items()}
    assert checked >= 2832, checked
