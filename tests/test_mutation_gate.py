"""The mutation-score ratchet (tools/mutation-gate.py).

Rules are tested on inline maps; one integration test runs the harness over a small
temporary tree built from a real schema, so it is fast and independent of the corpus
baseline's contents.
"""

import json
import shutil
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "mutation-gate.py"


def _load():
    loader = SourceFileLoader("mutation_gate", str(TOOL))
    module = module_from_spec(spec_from_loader("mutation_gate", loader))
    loader.exec_module(module)
    return module


mg = _load()

K = "milesight/am102.yaml"


def e(score, killed, survived):
    return {"score": score, "killed": killed, "survived": survived}


def test_unchanged_passes():
    base = {K: e(0.9, 9, 1)}
    out = mg.compare(base, {K: e(0.9, 9, 1)})
    assert out == {"regressions": [], "improvements": [], "notes": []}


def test_score_drop_fails_exactly():
    base = {K: e(0.9, 9, 1)}
    out = mg.compare(base, {K: e(0.8999, 9, 1)})
    assert len(out["regressions"]) == 1 and "fell" in out["regressions"][0]


def test_score_rise_is_an_improvement():
    out = mg.compare({K: e(0.8, 8, 2)}, {K: e(0.9, 9, 1)})
    assert out["regressions"] == [] and "rose" in out["improvements"][0]


def g(score, killed, survived, generated):
    return dict(e(score, killed, survived), generated=generated)


def test_killed_drop_from_a_smaller_schema_is_a_note_not_a_failure():
    out = mg.compare({K: g(1.0, 10, 0, 12)}, {K: g(1.0, 8, 0, 10)})
    assert out["regressions"] == [] and out["notes"]


def test_removing_vectors_that_raises_the_score_still_fails():
    # Survivors became `unreached` once their vectors went: score up, coverage down.
    out = mg.compare({K: g(0.955, 42, 2, 60)}, {K: g(1.0, 30, 0, 60)})
    assert out["improvements"] == []
    assert len(out["regressions"]) == 1 and "killed fell" in out["regressions"][0]


def test_new_schema_floor():
    assert mg.compare({}, {"x/new.yaml": e(0.7, 7, 3)}, 0.8)["regressions"]
    assert mg.compare({}, {"x/new.yaml": e(0.8, 8, 2)}, 0.8)["regressions"] == []
    assert mg.compare({}, {"x/new.yaml": e(0.7, 7, 3)}, 0.6)["regressions"] == []
    unscored = mg.compare({}, {"x/new.yaml": e(None, 0, 0)}, 0.8)
    assert unscored["regressions"] == [] and unscored["notes"]


def test_existing_schema_below_new_floor_is_grandfathered():
    out = mg.compare({K: e(0.5, 5, 5)}, {K: e(0.5, 5, 5)}, 0.8)
    assert out["regressions"] == []


def test_removed_or_unscored_schema_fails():
    assert mg.compare({K: e(0.9, 9, 1)}, {})["regressions"]
    assert mg.compare({K: e(0.9, 9, 1)}, {K: e(None, 0, 0)})["regressions"]


def test_harness_error_fails():
    assert mg.compare({}, {K: {"error": "boom"}})["regressions"]


def test_distribution_bands():
    d = mg.distribution(
        {"a": e(1.0, 1, 0), "b": e(0.96, 1, 0), "c": e(0.5, 1, 1), "d": e(None, 0, 0)}
    )
    assert d["100%"] == 1 and d["95-99%"] == 1 and d["<70%"] == 1
    assert d["unscored"] == 1


SOURCE = REPO_ROOT / "schemas" / "devices" / "milesight" / "am102.yaml"


def _tree(tmp_path):
    root = tmp_path / "devices"
    (root / "milesight").mkdir(parents=True)
    shutil.copy(SOURCE, root / "milesight" / "am102.yaml")
    (root / "_library-composed").mkdir()
    shutil.copy(SOURCE, root / "_library-composed" / "copy.yaml")
    return root


def test_update_round_trip_and_library_exclusion(tmp_path):
    root = _tree(tmp_path)
    baseline = tmp_path / "baseline.json"
    args = ["--schemas-dir", str(root), "--baseline", str(baseline), "--workers", "1"]
    assert mg.main(args + ["--update"]) == 0
    data = json.loads(baseline.read_text())
    assert list(data["schemas"]) == ["milesight/am102.yaml"]
    assert data["min_score_new"] == mg.DEFAULT_MIN_SCORE
    assert mg.main(args) == 0
    # Weaken the recorded schema: an inflated baseline score must now fail.
    data["schemas"]["milesight/am102.yaml"]["score"] = 1.01
    baseline.write_text(json.dumps(data))
    assert mg.main(args) == 1


def test_vectors_failing_on_the_original_fail_the_gate():
    base = {K: dict(e(0.9, 9, 1), failing_original=0)}
    out = mg.compare(base, {K: dict(e(0.95, 19, 1), failing_original=2)})
    assert any("unmutated" in r for r in out["regressions"])
    new = mg.compare({}, {"x/new.yaml": dict(e(1.0, 5, 0), failing_original=1)})
    assert new["regressions"]
