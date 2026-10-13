"""CR-2026-167 (PS-297 amended, PS-576, PS-577): a test vector may expect an error.

`expected_error: {requirement, at}` stands in place of `expected` / `expected_payload`. A
runner fails the vector where the execution succeeds, where the error cites PS numbers that
leave out a listed requirement, or where `at` is given and the error does not name it. The
wording of an error is never compared. These mirror the spec prototype's
TestCR2026167ExpectedError and add this repository's runners.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import (  # noqa: E402
    expected_error_mismatch,
    run_test_vector,
    validate_schema_structure,
)

FIXTURE = REPO_ROOT / "schemas" / "devices" / "_language-conformance" / "expected-error.yaml"


def schema_with(vectors):
    return {
        "name": "t",
        "version": 1,
        "fields": [
            {"name": "temperature", "type": "s16", "div": 10},
            {"name": "humidity", "type": "u8"},
        ],
        "test_vectors": vectors,
    }


def run(vector):
    schema = schema_with([vector])
    return run_test_vector(SchemaInterpreter(schema), vector)


def test_the_cr_examples_pass():
    for vector in (
        {"name": "too_short", "payload": "00",
         "expected_error": {"requirement": "PS-535", "at": "temperature"}},
        {"name": "out_of_range", "input": {"temperature": 21.5, "humidity": 300},
         "expected_error": {"requirement": "PS-168", "at": "humidity"}},
    ):
        assert run(vector).passed, run(vector).errors


def test_a_vector_that_succeeds_fails():
    result = run({"name": "v", "payload": "00D741", "expected_error": {"requirement": "PS-535"}})
    assert not result.passed and "succeeded" in result.errors[0]


def test_another_requirement_or_field_fails():
    assert "cites" in run({"name": "v", "payload": "00",
                           "expected_error": {"requirement": "PS-168"}}).errors[0]
    assert "does not name" in run({"name": "v", "payload": "00",
                                   "expected_error": {"requirement": "PS-535",
                                                      "at": "humidity"}}).errors[0]


def test_a_list_must_all_be_cited():
    assert run({"name": "v", "payload": "00",
                "expected_error": {"requirement": ["PS-535"]}}).passed
    assert not run({"name": "v", "payload": "00",
                    "expected_error": {"requirement": ["PS-535", "PS-168"]}}).passed


def test_an_error_citing_no_requirement_is_checked_on_at_only():
    message = "Missing required field: humidity"
    assert expected_error_mismatch(message, {"requirement": "PS-551"}) is None
    assert expected_error_mismatch(message, {"requirement": "PS-551", "at": "humidity"})


def test_a_json_pointer_may_be_contained():
    assert expected_error_mismatch("bad value at /fields/2 (PS-168)",
                                   {"requirement": "PS-168", "at": "/fields/2"}) is None


@pytest.mark.parametrize("vector", [
    {"name": "v", "payload": "00", "expected": {}, "expected_error": {"requirement": "PS-535"}},
    {"name": "v", "payload": "00", "expected_error": {"at": "temperature"}},
    {"name": "v", "payload": "00", "expected_error": {"requirement": "535"}},
    {"name": "v", "payload": "00", "expected_error": {"requirement": "PS-535", "why": "x"}},
    {"name": "v", "input": {}, "expected_payload": "00",
     "expected_error": {"requirement": "PS-535"}},
])
def test_a_malformed_expected_error_is_rejected_at_load(vector):
    assert validate_schema_structure(schema_with([vector]))


def test_a_well_formed_one_validates():
    vector = {"name": "v", "payload": "00", "expected_error": {"requirement": ["PS-535"]}}
    assert validate_schema_structure(schema_with([vector])) == []


def test_the_fixture_passes_on_both_conformance_paths():
    spec = importlib.util.spec_from_file_location(
        "vector_verdicts", str(REPO_ROOT / "tools" / "vector-verdicts.py"))
    verdicts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verdicts)
    schema = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    vectors = schema["test_vectors"]
    assert [v for v, _ in verdicts.run_interpreted(schema, vectors)] == [verdicts.PASS] * len(vectors)
    assert [v for v, _ in verdicts.run_generated(schema, vectors)] == [verdicts.PASS] * len(vectors)


def test_a_success_fails_on_both_conformance_paths():
    spec = importlib.util.spec_from_file_location(
        "vector_verdicts", str(REPO_ROOT / "tools" / "vector-verdicts.py"))
    verdicts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verdicts)
    vector = {"name": "v", "payload": "00D741", "expected_error": {"requirement": "PS-535"}}
    schema = schema_with([vector])
    assert verdicts.run_interpreted(schema, [vector])[0][0] == verdicts.FAIL
    assert verdicts.run_generated(schema, [vector])[0][0] == verdicts.FAIL


def test_the_mutation_harness_kills_a_mutant_that_lets_the_decode_succeed():
    spec = importlib.util.spec_from_file_location(
        "schema_mutation", str(REPO_ROOT / "tools" / "schema-mutation.py"))
    mutation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mutation)
    vector = {"name": "v", "payload": "00", "expected_error": {"requirement": "PS-535"}}
    assert mutation.run_vector(SchemaInterpreter(schema_with([vector])), vector).passed
    shorter = schema_with([vector])
    shorter["fields"] = shorter["fields"][:0] + [{"name": "t", "type": "u8"}]
    assert not mutation.run_vector(SchemaInterpreter(shorter), vector).passed

