"""Run every test vector in the device corpus through the Python interpreter.

The corpus is the conformance suite: 1,116 vectors across 98 schemas, most of them
verified against a vendor's own decoder. Until this file existed, no language's test
suite ran them - Go and C# each read two hardcoded schemas, Java and C none - which
is how both Go and Java came to return an empty result for every TLV schema in the
repository without a single test failing.

The equivalent runner exists for each implementation:

    go/schema/corpus_conformance_test.go
    bindings/java/src/test/java/org/lora/schema/CorpusConformanceTest.java
    dotnet/PayloadSchema.Tests/CorpusConformanceTests.cs

Each reads the same YAML and the same vectors, so a construct one implementation
mishandles shows up as a failure there and nowhere else.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from score_schema import CONFORMANCE_TOLERANCE  # noqa: E402
from validate_schema import is_encode_vector, values_match  # noqa: E402

CORPUS = REPO_ROOT / "schemas" / "devices"


def corpus_vectors():
    """Yield (schema_path, schema, vector) for every vector in the corpus."""
    for path in sorted(CORPUS.rglob("*.yaml")):
        try:
            schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - a broken file is a failure
            yield pytest.param(path, None, None, id="%s-unparseable" % path.stem)
            continue
        for index, vector in enumerate(schema.get("test_vectors") or []):
            # An encode vector has no payload to decode; tools/vector-verdicts.py runs
            # those on both conformance paths (PS-047).
            if is_encode_vector(vector):
                continue
            name = vector.get("name", "vector%d" % index)
            yield pytest.param(
                path, schema, vector,
                id="%s/%s-%s" % (path.parent.name, path.stem, name),
            )


CASES = list(corpus_vectors())


def test_corpus_is_not_empty():
    assert len(CASES) > 500, "the corpus conformance suite should cover the whole corpus"


@pytest.mark.parametrize(("path", "schema", "vector"), CASES)
def test_corpus_vector(path, schema, vector):
    assert schema is not None, "%s does not parse" % path
    payload = str(vector.get("payload", "")).replace(" ", "")
    expected = vector.get("expected") or {}
    fport = vector.get("fPort") or vector.get("fport")

    result = SchemaInterpreter(schema).decode(bytes.fromhex(payload), fPort=fport)
    assert result.success, "%s: %s" % (path.name, result.errors[:2])

    for key, want in expected.items():
        assert key in result.data, "%s: %s missing from output" % (path.name, key)
        match, message = values_match(want, result.data[key], CONFORMANCE_TOLERANCE)
        assert match, "%s: %s: %s" % (path.name, key, message)
