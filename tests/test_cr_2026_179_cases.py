"""CR-2026-179 (PS-470, PS-597 to PS-600): `semantic` names a TTN normalized path.

The cases live in `tests/cr-2026-179-cases.yaml`. Here every case runs through the reference
interpreter (`_meta`, the load error), the validator (PS-597's warnings), `normalize()` and
the generated codec's `normalizeUplink()` under node, which must agree with `normalize()`.
"""

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import (  # noqa: E402
    SchemaInterpreter,
    normalize,
    annotation_schema_errors,
    semantic_warnings,
)
from validate_schema import validate_schema  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-179-cases.yaml").read_text())["cases"]
META = [c for c in CASES if "meta" in c]
REJECTED = [c for c in CASES if "error" in c]
NORMALIZED = [c for c in CASES if "normalized" in c]
NODE = shutil.which("node")


def close(got, want):
    if isinstance(want, dict):
        return isinstance(got, dict) and set(got) == set(want) and all(
            close(got[k], want[k]) for k in want)
    if isinstance(want, list):
        return isinstance(got, list) and len(got) == len(want) and all(
            close(g, w) for g, w in zip(got, want))
    if isinstance(want, bool) or isinstance(got, bool):
        return got is want
    if isinstance(want, (int, float)):
        return isinstance(got, (int, float)) and math.isclose(got, want, rel_tol=1e-9)
    return got == want


def decoded(case):
    schema = yaml.safe_load(case["schema"])
    res = SchemaInterpreter(schema).decode(bytes.fromhex(case["payload"].replace(" ", "")),
                                           fPort=case.get("fport"))
    assert res.success, res.errors
    return schema, res.data


@pytest.mark.parametrize("case", META, ids=[c["name"] for c in META])
def test_meta_carries_semantic(case):
    schema = yaml.safe_load(case["schema"])
    res = SchemaInterpreter(schema).interpret(bytes.fromhex(case["payload"]))
    assert res.success, res.errors
    fields = res.data["_meta"]["fields"]
    for name, want in case["meta"].items():
        if want is None:
            assert "semantic" not in fields[name]
        else:
            assert fields[name]["semantic"] == want


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_a_semantic_that_is_not_a_string_is_rejected(case):
    schema = yaml.safe_load(case["schema"])
    assert any(case["error"] in e for e in annotation_schema_errors(schema))
    assert not SchemaInterpreter(schema).decode(b"\x01").success
    result = validate_schema(schema)
    assert not result.schema_valid
    assert any(case["error"] in e for e in result.schema_errors), result.schema_errors
    with pytest.raises(Exception, match="PS-470"):
        TS013Generator(schema).generate()


@pytest.mark.parametrize("case", NORMALIZED, ids=[c["name"] for c in NORMALIZED])
def test_normalize(case):
    schema, data = decoded(case)
    out = normalize(data, schema, case.get("normalize_fport"))
    assert close(out["data"], case["normalized"]), out
    assert len(out["warnings"]) == case.get("normalize_warnings", 0), out["warnings"]
    warnings = semantic_warnings(schema)
    assert len(warnings) == case.get("semantic_warnings", 0), warnings
    ps597 = [w for w in validate_schema(schema).schema_warnings if "PS-59" in w]
    assert len(ps597) == len(warnings), ps597


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("case", NORMALIZED, ids=[c["name"] for c in NORMALIZED])
@pytest.mark.parametrize("slim", [False, True], ids=["full", "slim"])
def test_generated_normalize_uplink_agrees(case, slim):
    schema, data = decoded(case)
    want = normalize(data, schema, case.get("normalize_fport"))
    code = TS013Generator(schema, slim=slim).generate()
    payload = list(bytes.fromhex(case["payload"].replace(" ", "")))
    call = {"bytes": payload, "fPort": case.get("fport") or 1}
    norm = {} if case.get("normalize_fport") is None else {"fPort": case["normalize_fport"]}
    script = code + "\nvar d = decodeUplink(%s);\nconsole.log(JSON.stringify(" \
        "normalizeUplink(Object.assign({data: d.data}, %s))));" % (json.dumps(call),
                                                                  json.dumps(norm))
    got = json.loads(subprocess.run([NODE], input=script, capture_output=True, text=True,
                                    check=True).stdout)
    assert got["data"] == want["data"]
    assert got["warnings"] == want["warnings"]


def test_no_normalize_uplink_without_semantic():
    schema = {"name": "t", "version": 1, "fields": [{"name": "v", "type": "u8"}]}
    assert "normalizeUplink" not in TS013Generator(schema).generate()


def test_the_validator_names_the_field_and_the_path():
    schema = yaml.safe_load(next(c for c in CASES if c["name"] ==
                                 "an_unknown_path_warns_and_still_loads")["schema"])
    warnings = semantic_warnings(schema)
    assert warnings == ["semantic 'temperature' of 't' is not a TTN normalized path (PS-597)"]
