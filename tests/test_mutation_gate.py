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


# ------------------------------------------------- --only-changed and --shard selection


def test_full_sweep_reasons_name_the_tooling_and_the_interpreter():
    sources = ["tools/schema_interpreter.py", "tools/schema_vocabulary.py"]
    for path in mg.FULL_SWEEP_FILES + ("tools/schema_vocabulary.py",):
        assert mg.full_sweep_reasons([path, "docs/FAQ.md"], sources) == [path]
    assert (
        mg.full_sweep_reasons(
            ["schemas/devices/milesight/am102.yaml", "AGENTS.md", "tools/other.py"],
            sources,
        )
        == []
    )


def test_the_harness_loads_the_interpreter_so_its_change_forces_a_full_sweep():
    mg.load_harness()
    sources = mg.harness_sources()
    assert "tools/schema_interpreter.py" in sources
    assert "tools/schema-mutation.py" in sources
    assert mg.full_sweep_reasons(["tools/validate_schema.py"], sources)


def test_schema_keys_keep_corpus_yaml_only_and_deleted_files():
    corpus = REPO_ROOT / "schemas" / "devices"
    keys = mg.schema_keys(
        [
            "schemas/devices/milesight/am102.yaml",
            "schemas/devices/acme/gone.yaml",  # deleted: still judged
            "schemas/library/common/headers.yaml",
            "schemas/devices/milesight/README.md",
            "tools/schema_interpreter.py",
        ],
        corpus,
    )
    assert keys == ["acme/gone.yaml", "milesight/am102.yaml"]


def test_shards_partition_the_corpus_deterministically_and_balance_cost():
    keys = ["s%02d.yaml" % i for i in range(40)]
    weights = {k: float(i + 1) for i, k in enumerate(keys[:30])}  # 10 unweighted
    shards = [mg.shard_of(keys, weights, i, 4) for i in range(1, 5)]
    assert set().union(*shards) == set(keys)
    assert sum(len(s) for s in shards) == len(keys)
    assert shards == [
        mg.shard_of(list(reversed(keys)), weights, i, 4) for i in range(1, 5)
    ]
    median = sorted(weights.values())[15]
    loads = [sum(weights.get(k, median) for k in s) for s in shards]
    assert max(loads) - min(loads) <= max(weights.values())
    assert mg.shard_of(keys, weights, 1, 1) == set(keys)


def _two_schema_tree(tmp_path):
    root = tmp_path / "devices"
    (root / "milesight").mkdir(parents=True)
    shutil.copy(SOURCE, root / "milesight" / "am102.yaml")
    shutil.copy(SOURCE, root / "milesight" / "twin.yaml")
    baseline = tmp_path / "baseline.json"
    args = ["--schemas-dir", str(root), "--baseline", str(baseline), "--workers", "1"]
    assert mg.main(args + ["--update"]) == 0
    return root, baseline, args


def test_only_changed_scores_just_the_changed_schema(tmp_path, monkeypatch, capsys):
    root, baseline, args = _two_schema_tree(tmp_path)
    data = json.loads(baseline.read_text())
    # Inflate the UNCHANGED schema's entry: judging it would fail the run.
    data["schemas"]["milesight/twin.yaml"]["score"] = 1.01
    baseline.write_text(json.dumps(data))
    changed = [str(root / "milesight" / "am102.yaml"), "docs/FAQ.md"]
    monkeypatch.setattr(mg, "changed_paths", lambda ref: changed)
    capsys.readouterr()
    assert mg.main(args + ["--changed-since", "BASE", "--only-changed"]) == 0
    out = capsys.readouterr().out
    assert "1 schemas scored" in out and "milesight/am102.yaml" in out
    # The full sweep judges the twin and fails on it.
    assert mg.main(args) == 1


def test_only_changed_judges_a_changed_schema_against_the_baseline(
    tmp_path, monkeypatch
):
    root, baseline, args = _two_schema_tree(tmp_path)
    data = json.loads(baseline.read_text())
    data["schemas"]["milesight/am102.yaml"]["score"] = 1.01
    baseline.write_text(json.dumps(data))
    changed = [str(root / "milesight" / "am102.yaml")]
    monkeypatch.setattr(mg, "changed_paths", lambda ref: changed)
    assert mg.main(args + ["--changed-since", "BASE", "--only-changed"]) == 1


def test_only_changed_fails_a_deleted_baselined_schema(tmp_path, monkeypatch):
    root, baseline, args = _two_schema_tree(tmp_path)
    (root / "milesight" / "twin.yaml").unlink()
    changed = [str(root / "milesight" / "twin.yaml")]
    monkeypatch.setattr(mg, "changed_paths", lambda ref: changed)
    assert mg.main(args + ["--changed-since", "BASE", "--only-changed"]) == 1


def test_only_changed_holds_a_new_schema_to_the_floor(tmp_path, monkeypatch, capsys):
    root, baseline, args = _two_schema_tree(tmp_path)
    shutil.copy(SOURCE, root / "milesight" / "new.yaml")
    changed = [str(root / "milesight" / "new.yaml")]
    monkeypatch.setattr(mg, "changed_paths", lambda ref: changed)
    capsys.readouterr()
    floor = ["--min-score", "1.01"]
    assert mg.main(args + ["--changed-since", "BASE", "--only-changed"] + floor) == 1
    assert "new schema scores" in capsys.readouterr().out


def test_only_changed_runs_the_full_sweep_when_the_interpreter_changes(
    tmp_path, monkeypatch, capsys
):
    root, baseline, args = _two_schema_tree(tmp_path)
    changed = ["tools/schema_interpreter.py"]
    monkeypatch.setattr(mg, "changed_paths", lambda ref: changed)
    capsys.readouterr()
    assert mg.main(args + ["--changed-since", "BASE", "--only-changed"]) == 0
    out = capsys.readouterr().out
    assert "full sweep" in out and "2 schemas scored" in out


def _run_shards(args, tmp_path, count, skip=()):
    out = tmp_path / "parts"
    for i in range(1, count + 1):
        if i in skip:
            continue
        shard = ["--shard", "%d/%d" % (i, count)]
        assert mg.main(args + shard + ["--parts-out", str(out / ("%d.json" % i))]) == 0
    return out


def test_shards_split_a_schema_and_judge_like_one_run(tmp_path, monkeypatch, capsys):
    root, baseline, args = _two_schema_tree(tmp_path)
    monkeypatch.setattr(mg, "MIN_PART_WEIGHT", 0.0)  # split even these small ones
    out = _run_shards(args, tmp_path, 3)
    units = set()
    for name in ("1.json", "2.json", "3.json"):
        units.update(json.loads((out / name).read_text())["units"])
    assert any("#" in u for u in units), units  # parts really crossed shards
    harness = mg.load_harness()
    assert mg.load_shards(out, harness) == mg.run_harness(root, workers=1)
    capsys.readouterr()
    assert mg.main(args + ["--parts-in", str(out)]) == 0
    assert "2 schemas scored" in capsys.readouterr().out
    data = json.loads(baseline.read_text())
    data["schemas"]["milesight/twin.yaml"]["score"] = 1.01
    baseline.write_text(json.dumps(data))
    assert mg.main(args + ["--parts-in", str(out)]) == 1


def test_a_missing_shard_fails_the_judgement(tmp_path, monkeypatch, capsys):
    _root, _baseline, args = _two_schema_tree(tmp_path)
    monkeypatch.setattr(mg, "MIN_PART_WEIGHT", 0.0)
    out = _run_shards(args, tmp_path, 3, skip=(2,))
    capsys.readouterr()
    assert mg.main(args + ["--parts-in", str(out)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_update_refuses_a_partial_run(tmp_path):
    _root, _baseline, args = _two_schema_tree(tmp_path)
    for extra in (
        ["--changed-since", "BASE", "--only-changed"],
        ["--shard", "1/2", "--parts-out", str(tmp_path / "p.json")],
        ["--parts-in", str(tmp_path)],
    ):
        try:
            mg.main(args + ["--update"] + extra)
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("--update ran a partial sweep")


# ------------------------------------------------- splitting one schema's mutants


def test_merged_parts_equal_the_unsplit_report():
    import yaml

    harness = mg.load_harness()
    schema = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    whole = harness.mutate_schema(schema, "am102")
    for count in (1, 2, 5):
        parts = [
            harness.mutate_schema(schema, "am102", part=(i, count))
            for i in reversed(range(count))  # merge_parts orders them itself
        ]
        assert harness.merge_parts(parts) == whole
    try:
        harness.merge_parts([harness.mutate_schema(schema, "am102", part=(0, 2))])
    except ValueError:
        pass
    else:
        raise AssertionError("a missing part was merged")


def test_part_bounds_cover_the_list_exactly():
    harness = mg.load_harness()
    for total in (0, 1, 7, 100):
        for count in (1, 3, 8):
            spans = [harness.part_bounds(total, i, count) for i in range(count)]
            assert spans[0][0] == 0 and spans[-1][1] == total
            assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))


def test_only_a_dominant_schema_is_split():
    weights = {"big.yaml": 10000000.0, "small.yaml": 30000.0, "tiny.yaml": 10.0}
    parts = mg.plan_parts(weights, list(weights) + ["new.yaml"], workers=4)
    assert parts["big.yaml"] == 16
    assert parts["small.yaml"] == parts["tiny.yaml"] == parts["new.yaml"] == 1
    assert mg.plan_parts(weights, weights, workers=1)["big.yaml"] == 1
    # Alone, a light schema is still not split: each part repeats its setup.
    assert mg.plan_parts(weights, ["small.yaml"], workers=4) == {"small.yaml": 1}


def test_a_split_run_judges_like_an_unsplit_one(tmp_path):
    root, _baseline, _args = _two_schema_tree(tmp_path)
    whole = mg.run_harness(root, workers=1)
    heavy = {"milesight/am102.yaml": 1e9, "milesight/twin.yaml": 1.0}
    split = mg.run_harness(root, workers=2, weights=heavy)
    assert split == whole
