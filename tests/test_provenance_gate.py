"""tools/provenance-gate.py: independent provenance for changed schemas (gate 2).

Each test builds a throwaway git repository in tmp_path with a `master` commit, then a
change on HEAD, and runs the gate with `--repo` pointing at it.
"""

import importlib.util
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "provenance_gate", str(REPO_ROOT / "tools" / "provenance-gate.py")
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

DEVICE = "schemas/devices/acme/sensor.yaml"
FIXTURE = "schemas/devices/_language-conformance/probe.yaml"
COMPOSED = "schemas/devices/_library-composed/lib__vector.yaml"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(repo),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    ).stdout


def _schema(*vectors: str) -> str:
    body = "name: sensor\nfields:\n  - {name: t, type: u8}\ntest_vectors:\n"
    for v in vectors:
        body += textwrap.indent(textwrap.dedent(v).strip("\n"), "  ") + "\n"
    return body


VENDOR = """
- name: vendor_example
  payload: "17"
  expected: {t: 23}
  source: vendor-doc
"""
GENERATED = """
- name: regression
  payload: "00"
  expected: {t: 0}
  source: generated
"""
UNSOURCED = """
- name: new_one
  payload: "05"
  expected: {t: 5}
"""


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    _write(root, DEVICE, _schema(VENDOR))
    _write(root, "README", "base\n")
    _commit(root, "base")
    _git(root, "checkout", "-q", "-b", "change")
    return root


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


def _run(root: Path, capsys, *extra: str):
    code = gate.main(["--repo", str(root), "--base", "master"] + list(extra))
    return code, capsys.readouterr().out


def test_unchanged_repository_passes(repo, capsys):
    code, out = _run(repo, capsys)
    assert code == 0
    assert "0 changed schema(s)" in out


def test_added_vector_without_source_fails(repo, capsys):
    _write(repo, DEVICE, _schema(VENDOR, UNSOURCED))
    _commit(repo, "add a vector")
    code, out = _run(repo, capsys)
    assert code == 1
    assert "FAIL schemas/devices/acme/sensor.yaml" in out
    assert "'new_one' was added without a `source:`" in out


def test_existing_unsourced_vector_is_not_blamed_on_the_change(repo, capsys):
    _write(repo, DEVICE, _schema(VENDOR, UNSOURCED))
    _commit(repo, "old debt")
    _git(repo, "branch", "-f", "master", "HEAD")
    _write(repo, DEVICE, _schema(VENDOR, UNSOURCED).replace("name: sensor", "name: s2"))
    _commit(repo, "unrelated edit")
    code, out = _run(repo, capsys)
    assert code == 0, out


def test_changed_schema_with_only_generated_vectors_fails(repo, capsys):
    _write(repo, DEVICE, _schema(GENERATED))
    _commit(repo, "replace the vendor vector")
    code, out = _run(repo, capsys)
    assert code == 1
    assert "no vector has an independent source" in out


def test_new_schema_with_no_vectors_fails(repo, capsys):
    _write(
        repo,
        "schemas/devices/acme/other.yaml",
        "name: o\nfields:\n  - {name: t, type: u8}\n",
    )
    _commit(repo, "new schema")
    code, out = _run(repo, capsys)
    assert code == 1
    assert "no test vectors" in out


def test_unknown_source_value_fails(repo, capsys):
    _write(repo, DEVICE, _schema(VENDOR, GENERATED.replace("generated", "vendor_doc")))
    _commit(repo, "typo")
    code, out = _run(repo, capsys)
    assert code == 1
    assert "unknown source 'vendor_doc'" in out


def test_fixture_may_be_generated_but_must_declare_it(repo, capsys):
    _write(repo, FIXTURE, _schema(GENERATED))
    _commit(repo, "fixture")
    code, out = _run(repo, capsys)
    assert code == 0, out

    _write(repo, FIXTURE, _schema(GENERATED, UNSOURCED))
    _commit(repo, "fixture without source")
    code, out = _run(repo, capsys)
    assert code == 1
    assert "declares no source" in out


def test_fixture_rule_covers_every_vector_not_only_added_ones(repo, capsys):
    _write(repo, FIXTURE, _schema(UNSOURCED))
    _commit(repo, "fixture debt")
    _git(repo, "branch", "-f", "master", "HEAD")
    _write(repo, FIXTURE, _schema(UNSOURCED).replace("name: sensor", "name: p2"))
    _commit(repo, "touch it")
    code, out = _run(repo, capsys)
    assert code == 1
    assert "'new_one' declares no source" in out


def test_generated_library_composed_is_skipped(repo, capsys):
    _write(repo, COMPOSED, _schema(UNSOURCED))
    _commit(repo, "composed")
    code, out = _run(repo, capsys)
    assert code == 0
    assert "skipped 1 generated schema(s)" in out


def test_expected_changed_under_same_payload_and_source_warns(repo, capsys):
    _write(repo, DEVICE, _schema(VENDOR.replace("{t: 23}", "{t: 24}")))
    _commit(repo, "re-record")
    code, out = _run(repo, capsys)
    assert code == 0
    assert "WARN schemas/devices/acme/sensor.yaml" in out
    assert "'vendor_example': expected values changed" in out


def test_expected_changed_with_payload_does_not_warn(repo, capsys):
    changed = VENDOR.replace("{t: 23}", "{t: 24}").replace('"17"', '"18"')
    _write(repo, DEVICE, _schema(changed))
    _commit(repo, "new payload")
    code, out = _run(repo, capsys)
    assert code == 0
    assert "WARN" not in out


def test_worktree_changes_are_checked_only_on_request(repo, capsys):
    _write(repo, DEVICE, _schema(VENDOR, UNSOURCED))
    code, _ = _run(repo, capsys)
    assert code == 0
    code, out = _run(repo, capsys, "--include-worktree")
    assert code == 1
    assert "new_one" in out


def test_untracked_schema_is_seen_with_worktree(repo, capsys):
    _write(repo, "schemas/devices/acme/new.yaml", _schema(GENERATED))
    code, out = _run(repo, capsys, "--include-worktree")
    assert code == 1
    assert "acme/new.yaml" in out


def test_all_mode_reports_and_never_fails(repo, capsys):
    _write(repo, "schemas/devices/acme/other.yaml", _schema(GENERATED, UNSOURCED))
    _write(repo, FIXTURE, _schema(UNSOURCED))
    code = gate.main(["--repo", str(repo), "--all"])
    out = capsys.readouterr().out
    assert code == 0
    assert "2 device schemas (fixtures excluded)" in out
    assert "1 have vectors but none independently sourced" in out
    assert "1 device vectors declare no source at all" in out
    assert "1 fixture vectors declare no source" in out


def test_all_mode_runs_on_this_repository(capsys):
    assert gate.main(["--all"]) == 0
    out = capsys.readouterr().out
    assert "device schemas (fixtures excluded)" in out
