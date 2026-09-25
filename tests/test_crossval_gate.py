"""The vendor cross-validation ratchet (tools/crossval-gate.py).

The rules are tested on small inline status maps so they run in milliseconds; one
integration test drives the real oracles and skips when the checkouts are absent.
"""

import json
import os
import shutil
import subprocess
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "crossval-gate.py"


def _load():
    loader = SourceFileLoader("crossval_gate", str(TOOL))
    module = module_from_spec(spec_from_loader("crossval_gate", loader))
    loader.exec_module(module)
    return module


cg = _load()


def entry(status, problems=0, notes=0, decode_failures=0):
    return {
        "status": status,
        "problems": problems,
        "notes": notes,
        "decode_failures": decode_failures,
    }


def one(key, status, **kw):
    return {key: {"ttn": entry(status, **kw)}}


K = "milesight/am102.yaml"


def test_unchanged_passes():
    base = one(K, "agrees")
    out = cg.compare(base, one(K, "agrees"))
    assert out["regressions"] == [] and out["improvements"] == []


def test_agrees_to_disagrees_fails():
    out = cg.compare(one(K, "agrees"), one(K, "disagrees", problems=2))
    assert len(out["regressions"]) == 1
    assert "agreed with the vendor" in out["regressions"][0]


def test_agrees_to_not_compared_fails():
    out = cg.compare(one(K, "agrees"), one(K, "no-vendor-codec"))
    assert out["regressions"]


def test_disagreement_count_rise_fails_and_fall_improves():
    base = one(K, "disagrees", problems=3)
    assert cg.compare(base, one(K, "disagrees", problems=4))["regressions"]
    out = cg.compare(base, one(K, "disagrees", problems=2))
    assert out["regressions"] == []
    assert "fell 3 -> 2" in out["improvements"][0]


def test_decode_failure_lowering_the_count_still_fails():
    # A payload that stops decoding collapses many per-key problems into one.
    base = one(K, "disagrees", problems=5)
    out = cg.compare(base, one(K, "disagrees", problems=1, decode_failures=1))
    assert out["regressions"] and out["improvements"] == []


def test_lost_vendor_decoder_run_fails():
    out = cg.compare(one(K, "agrees"), one(K, "agrees", notes=1))
    assert "could not be made" in out["regressions"][0]


def test_disagrees_to_agrees_is_an_improvement():
    out = cg.compare(one(K, "disagrees", problems=2), one(K, "agrees"))
    assert out["regressions"] == []
    assert out["improvements"] == ["%s [ttn]: disagrees -> agrees" % K]


def test_new_schema_must_agree_unless_excepted():
    new = one("milesight/new.yaml", "disagrees", problems=1)
    assert cg.compare({}, new)["regressions"]
    out = cg.compare({}, new, {"milesight/new.yaml": "vendor example is stale"})
    assert out["regressions"] == []
    assert "stale" in out["notes"][0]
    ok = cg.compare({}, one("milesight/new.yaml", "agrees"))
    assert ok["regressions"] == [] and ok["improvements"]
    neutral = cg.compare({}, one("milesight/new.yaml", "no-vendor-codec"))
    assert neutral["regressions"] == []


def test_exception_never_excuses_a_regression():
    out = cg.compare(
        one(K, "agrees"), one(K, "disagrees", problems=1), {K: "any reason"}
    )
    assert out["regressions"]


def test_removed_schema_fails():
    assert cg.compare(one(K, "agrees"), {})["regressions"]


def test_second_oracle_is_ratcheted_independently():
    base = {K: {"ttn": entry("agrees"), "decentlab-decoders": entry("agrees")}}
    now = {K: {"ttn": entry("agrees"), "decentlab-decoders": entry("disagrees", 1)}}
    out = cg.compare(base, now)
    assert (
        len(out["regressions"]) == 1 and "decentlab-decoders" in out["regressions"][0]
    )


def test_pinned_commit_mismatch_fails():
    pins = {"ttn": {"url": "u", "commit": "a" * 40}}
    assert cg.check_pins(pins, {"ttn": "a" * 40}) == []
    msg = cg.check_pins(pins, {"ttn": "b" * 40})
    assert len(msg) == 1 and "pins aaaaaaaaaaaa" in msg[0]
    assert cg.check_pins(pins, {"ttn": None})


def test_unmapped_schema_dir_is_reported(tmp_path):
    (tmp_path / "milesight").mkdir()
    (tmp_path / "newvendor").mkdir()
    vendors = {"ttn": {"milesight": "milesight-iot"}, "not_cross_validated": {}}
    assert cg.unmapped_dirs(tmp_path, vendors) == ["newvendor"]


def test_every_corpus_dir_is_mapped():
    vendors = json.loads((REPO_ROOT / "tools" / "crossval-vendors.json").read_text())
    assert cg.unmapped_dirs(REPO_ROOT / "schemas" / "devices", vendors) == []


def test_update_round_trip(tmp_path):
    current = {K: {"ttn": dict(entry("agrees"), detail=["x"])}}
    commits = {"ttn": "a" * 40, "decentlab-decoders": "b" * 40}
    data = cg.build_baseline(current, commits, {"exceptions": {"x.yaml": "why"}})
    path = tmp_path / "b.json"
    path.write_text(json.dumps(data))
    back = json.loads(path.read_text())
    assert "detail" not in back["schemas"][K]["ttn"]
    assert back["exceptions"] == {"x.yaml": "why"}
    assert back["oracles"]["ttn"]["commit"] == "a" * 40
    assert back["summary"]["ttn"]["milesight"] == {"agrees": 1}
    assert cg.compare(back["schemas"], current)["regressions"] == []


def _oracle(env, default):
    return Path(os.environ.get(env) or default).expanduser()


TTN_REPO = _oracle("TTN_DEVICES_REPO", "~/Workspace/lora/tools/lorawan-devices")
DL_DIR = _oracle("DECENTLAB_DECODERS", "~/Workspace/lora/tools/decentlab-decoders")


def _head(path):
    out = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return out.stdout.strip()


@pytest.mark.skipif(
    not (TTN_REPO.is_dir() and DL_DIR.is_dir() and shutil.which("node")),
    reason="needs the lorawan-devices and decentlab-decoders checkouts and node",
)
def test_gate_passes_on_the_tree_at_the_pinned_commits():
    baseline = json.loads((REPO_ROOT / "tools" / "crossval-baseline.json").read_text())
    pins = baseline["oracles"]
    if _head(TTN_REPO) != pins["ttn"]["commit"] or _head(DL_DIR) != (
        pins["decentlab-decoders"]["commit"]
    ):
        pytest.skip("oracle checkouts are not at the pinned commits")
    code = cg.main(["--devices-repo", str(TTN_REPO), "--decentlab-dir", str(DL_DIR)])
    assert code == 0
