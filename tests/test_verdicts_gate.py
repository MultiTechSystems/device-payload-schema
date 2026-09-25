"""Tests for tools/verdicts-gate.py and tools/corpus-report.py (CI gate 3).

The gate's rules are tested on inline reports, so no docker is needed: the changed-schema
rule (every vector passes in all five), the whole-corpus divergence ratchet (a new
divergence fails, a listed one that went away fails), and the handling of vectors with no
payload, which every runner reports as `skip` and which must never read as a divergence.

The last tests check that tools/corpus-report.py reaches the same verdict as
tests/test_corpus_conformance.py on real schemas - the report is only worth gating if it
says what the Python corpus suite says.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "tools"))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, str(REPO_ROOT / "tools" / filename)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load("verdicts_gate", "verdicts-gate.py")
corpus_report = _load("corpus_report", "corpus-report.py")

ALL = ("python", "go", "java", "csharp", "ts013")


def entry(schema, index, status, vector=None, detail=""):
    return {
        "schema": schema,
        "index": index,
        "vector": vector or "v%d" % index,
        "status": status,
        "detail": detail,
    }


def reports(**overrides):
    """Five reports over two schemas, all passing unless overridden.

    An override is {impl: [(schema, index, status), ...]}.
    """
    base = [
        ("a.yaml", 0, "pass"),
        ("a.yaml", 1, "pass"),
        ("b.yaml", 0, "pass"),
        ("b.yaml", 1, "skip"),
    ]
    out = {}
    for impl in ALL:
        rows = {(s, i): st for s, i, st in base}
        for schema, index, status in overrides.get(impl, []):
            if status is None:
                rows.pop((schema, index), None)
            else:
                rows[(schema, index)] = status
        out[impl] = gate.index_report(entry(s, i, st) for (s, i), st in rows.items())
    return out


# ---------------------------------------------------------------------------------------
# Rule 1: changed schemas pass everywhere
# ---------------------------------------------------------------------------------------


def test_changed_schema_all_passing_is_clean():
    assert gate.changed_problems(reports(), ["a.yaml", "b.yaml"]) == []


def test_changed_schema_failing_in_one_implementation_fails():
    problems = gate.changed_problems(reports(go=[("a.yaml", 1, "fail")]), ["a.yaml"])
    assert len(problems) == 1
    assert "a.yaml [1]" in problems[0] and "go fail" in problems[0]


def test_changed_rule_ignores_unchanged_schemas():
    assert gate.changed_problems(reports(go=[("a.yaml", 1, "fail")]), ["b.yaml"]) == []


def test_changed_rule_is_not_exempted_by_the_baseline():
    # The rule takes no baseline at all: a schema being edited must agree everywhere.
    problems = gate.changed_problems(
        reports(ts013=[("a.yaml", 0, "error")]), ["a.yaml"]
    )
    assert problems and "ts013 error" in problems[0]


def test_changed_vector_missing_from_an_implementation_fails():
    # A runner that silently dropped a vector (say, it could not read the YAML) is not
    # a pass.
    problems = gate.changed_problems(reports(java=[("a.yaml", 0, None)]), ["a.yaml"])
    assert problems and "java missing" in problems[0]


def test_changed_rule_fails_when_the_reference_itself_fails():
    problems = gate.changed_problems(
        reports(python=[("a.yaml", 0, "fail")]), ["a.yaml"]
    )
    assert any("python fail" in p for p in problems)


# ---------------------------------------------------------------------------------------
# Skip handling
# ---------------------------------------------------------------------------------------


def test_a_vector_skipped_everywhere_is_neither_a_problem_nor_a_divergence():
    data = reports()
    assert gate.changed_problems(data, ["b.yaml"]) == []
    assert gate.divergences(data) == []
    assert gate.reverse_divergences(data) == []


def test_skip_in_another_implementation_where_the_reference_passes_diverges():
    # `skip` is only harmless where the reference skipped too; a runner skipping a vector
    # the reference decodes has not shown it agrees.
    found = gate.divergences(reports(csharp=[("a.yaml", 0, "skip")]))
    assert [(d["schema"], d["index"], d["impl"], d["status"]) for d in found] == [
        ("a.yaml", 0, "csharp", "skip")
    ]


def test_corpus_report_skips_encode_vectors_and_empty_payloads(tmp_path):
    schema = {
        "name": "s",
        "version": 1,
        "fields": [{"name": "a", "type": "u8"}],
        "test_vectors": [
            {"name": "decode", "payload": "05", "expected": {"a": 5}},
            {"name": "encode", "input": {"a": 5}, "expected_payload": "05"},
            {"name": "empty", "payload": "", "expected": {}},
            {"name": "wrong", "payload": "05", "expected": {"a": 6}},
        ],
    }
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(schema))
    got = [(e["vector"], e["status"]) for e in corpus_report.python_report(tmp_path)]
    assert got == [
        ("decode", "pass"),
        ("encode", "skip"),
        ("empty", "skip"),
        ("wrong", "fail"),
    ]


# ---------------------------------------------------------------------------------------
# Rule 2: the divergence ratchet
# ---------------------------------------------------------------------------------------


def listed(schema, index, impl, reason="because"):
    return {
        "schema": schema,
        "index": index,
        "vector": "v%d" % index,
        "impl": impl,
        "reason": reason,
    }


def test_no_divergence_and_empty_baseline_is_clean():
    assert gate.ratchet(gate.divergences(reports()), []) == ([], [], [])


def test_a_new_divergence_fails():
    found = gate.divergences(reports(go=[("a.yaml", 1, "fail")]))
    new, gone, unexplained = gate.ratchet(found, [])
    assert [(n["schema"], n["index"], n["impl"]) for n in new] == [("a.yaml", 1, "go")]
    assert gone == [] and unexplained == []


def test_a_listed_divergence_is_accepted():
    found = gate.divergences(reports(go=[("a.yaml", 1, "fail")]))
    assert gate.ratchet(found, [listed("a.yaml", 1, "go")]) == ([], [], [])


def test_a_listed_divergence_changing_from_fail_to_error_is_still_listed():
    found = gate.divergences(reports(go=[("a.yaml", 1, "error")]))
    assert gate.ratchet(found, [listed("a.yaml", 1, "go")]) == ([], [], [])


def test_a_listed_divergence_that_now_agrees_fails_so_the_list_shrinks():
    new, gone, _ = gate.ratchet(
        gate.divergences(reports()), [listed("a.yaml", 1, "go")]
    )
    assert new == [] and [(g["schema"], g["impl"]) for g in gone] == [("a.yaml", "go")]


def test_a_listed_divergence_needs_a_reason():
    found = gate.divergences(reports(go=[("a.yaml", 1, "fail")]))
    _, _, unexplained = gate.ratchet(found, [listed("a.yaml", 1, "go", reason=" ")])
    assert len(unexplained) == 1


def test_divergence_is_per_implementation():
    found = gate.divergences(
        reports(go=[("a.yaml", 1, "fail")], java=[("a.yaml", 1, "fail")])
    )
    new, _, _ = gate.ratchet(found, [listed("a.yaml", 1, "go")])
    assert [(n["impl"]) for n in new] == ["java"]


def test_a_restricted_run_cannot_declare_an_unmeasured_divergence_gone():
    data = reports()
    data = {
        impl: {k: v for k, v in r.items() if k[0] == "b.yaml"}
        for impl, r in data.items()
    }
    baseline = [listed("a.yaml", 1, "go")]
    assert gate.ratchet(gate.divergences(data), baseline, in_scope={"b.yaml"}) == (
        [],
        [],
        [],
    )
    # ...nor for an implementation that did not report.
    assert gate.ratchet([], baseline, impls={"python", "java"}) == ([], [], [])


def test_the_reference_failing_is_reported_but_is_not_a_divergence():
    data = reports(python=[("a.yaml", 0, "fail")])
    assert gate.divergences(data) == []
    assert gate.reverse_divergences(data) == [("a.yaml", 0)]


def test_write_baseline_keeps_reasons(tmp_path):
    path = tmp_path / "baseline.json"
    found = gate.divergences(
        reports(go=[("a.yaml", 1, "fail")], java=[("a.yaml", 0, "fail")])
    )
    gate.write_baseline(path, found, [listed("a.yaml", 1, "go", reason="known Go gap")])
    written = {(d["impl"], d["index"]): d["reason"] for d in gate.load_baseline(path)}
    assert written == {("go", 1): "known Go gap", ("java", 0): ""}


def test_committed_baseline_is_well_formed():
    data = json.loads((REPO_ROOT / "tools" / "verdicts-baseline.json").read_text())
    for item in data["divergences"]:
        assert item["impl"] in ALL and item["impl"] != "python"
        assert str(
            item.get("reason", "")
        ).strip(), "every listed divergence needs a reason"
        assert (REPO_ROOT / "schemas" / "devices" / item["schema"]).exists()


def test_docker_command_matches_the_makefile_images():
    for impl, image in (
        ("go", "golang:1.22"),
        ("java", "maven:3.9-eclipse-temurin-21"),
        ("csharp", "mcr.microsoft.com/dotnet/sdk:8.0"),
    ):
        command = gate.docker_command(
            impl, "/work/build/verdicts/x.json", "a.yaml", None
        )
        assert image in command
        assert "CORPUS_REPORT=/work/build/verdicts/x.json" in command
        assert "CORPUS_ONLY=a.yaml" in command


# ---------------------------------------------------------------------------------------
# corpus-report.py says what tests/test_corpus_conformance.py says
# ---------------------------------------------------------------------------------------

AGREEMENT_SCHEMAS = [
    "decentlab/dl-5tm.yaml",
    "dragino/laq4.yaml",
    "_language-conformance/field-endian.yaml",
]


@pytest.mark.parametrize("rel", AGREEMENT_SCHEMAS)
def test_corpus_report_agrees_with_the_python_corpus_suite(rel):
    import test_corpus_conformance as suite

    path = REPO_ROOT / "schemas" / "devices" / rel
    cases = [p.values for p in suite.CASES if p.values[0] == path]
    assert cases, "%s has no decode vectors in the corpus suite" % rel

    expected = {}
    for _, schema, vector in cases:
        try:
            suite.test_corpus_vector(path, schema, vector)
            expected[vector.get("name")] = "pass"
        except AssertionError:
            expected[vector.get("name")] = "not pass"

    report = corpus_report.python_report(only={rel})
    got = {
        e["vector"]: ("pass" if e["status"] == "pass" else "not pass")
        for e in report
        if e["status"] != "skip"
    }
    assert got == expected
    assert all(e["schema"] == rel for e in report)


def test_corpus_report_detects_a_wrong_expected_value(tmp_path):
    source = REPO_ROOT / "schemas" / "devices" / "decentlab" / "dl-5tm.yaml"
    schema = yaml.safe_load(source.read_text())
    vector = next(v for v in schema["test_vectors"] if v.get("expected"))
    key = next(iter(vector["expected"]))
    vector["expected"][key] = "definitely not this"
    schema["test_vectors"] = [vector]
    (tmp_path / "dl-5tm.yaml").write_text(yaml.safe_dump(schema))
    (only,) = corpus_report.python_report(tmp_path)
    assert only["status"] == "fail" and key in only["detail"]
