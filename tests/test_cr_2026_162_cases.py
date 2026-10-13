"""CR-2026-162 (PS-572 to PS-574): the decode record. `interpret(..., decode_record=True)`
adds `_decode`; an encoder given it follows it and rejects input contradicting it; where
neither the input nor the record gives a match's case, this repository derives it.

The cases live in `tests/cr-2026-162-cases.yaml`, read by every implementation. The
generated codec's runtime encoder takes the record in encodeDownlink's input as `_decode`;
its decoder never produces one (PS-467), so the record it is given is the reference's.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-162-cases.yaml").read_text())["cases"]
IDS = [case["name"] for case in CASES]


def same(want, got):
    """JSON values equal, numbers by value (1 and 1.0 alike, a boolean only a boolean)."""
    if isinstance(want, bool) or isinstance(got, bool):
        return want is got
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return want == got
    if isinstance(want, dict) and isinstance(got, dict):
        return want.keys() == got.keys() and all(same(want[k], got[k]) for k in want)
    if isinstance(want, list) and isinstance(got, list):
        return len(want) == len(got) and all(same(a, b) for a, b in zip(want, got))
    return want == got


def interpret(case):
    schema = yaml.safe_load(case["schema"])
    out = SchemaInterpreter(schema).interpret(
        bytes.fromhex(case["decode"]["payload"]), case.get("fport"), decode_record=True)
    assert out.success, out.errors
    return schema, out.data


def step_record(step, decoded):
    record = step.get("record")
    if record is None:
        return None
    if record == "decoded":
        return decoded["_decode"]
    return json.loads(record)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_reference(case):
    schema = yaml.safe_load(case["schema"])
    decoded = None
    if "decode" in case:
        schema, decoded = interpret(case)
        record = decoded["_decode"]
        want = case["decode"]
        if "record" in want:
            assert same(json.loads(want["record"]), record), record
        if "matches" in want:
            assert set(record["matches"]) == set(json.loads(want["matches"]))
        # decode() never carries it (PS-572).
        plain = SchemaInterpreter(schema).decode(
            bytes.fromhex(want["payload"]), case.get("fport"))
        assert "_decode" not in plain.data
        if want.get("round_trip"):
            out = SchemaInterpreter(schema).encode(decoded, case.get("fport"))
            assert out.success, out.errors
            assert out.payload.hex() == want["payload"]
    for step in case.get("encode") or []:
        out = SchemaInterpreter(schema).encode(
            json.loads(step["input"]), case.get("fport"),
            decode_record=step_record(step, decoded))
        if "error" in step:
            assert any(step["error"] in e for e in out.errors), out.errors
            assert out.payload == b"", out.payload
        else:
            assert out.success, out.errors
            assert out.payload.hex() == step["payload"]


def encode_codec(schema, data, fport):
    script = (
        TS013Generator(schema).generate()
        + "\nvar e; try { e = encodeDownlink({data: "
        + json.dumps(data)
        + ", fPort: %d}); } catch (x) { e = {errors: [String(x.message)]}; }" % fport
        + "\nprocess.stdout.write(JSON.stringify(e));"
    )
    done = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_generated_codec(case):
    """The runtime encoder, given the reference's record as `_decode` in its input."""
    schema = yaml.safe_load(case["schema"])
    fport = case.get("fport") or 1
    decoded = None
    if "decode" in case:
        schema, decoded = interpret(case)
        if case["decode"].get("round_trip"):
            out = encode_codec(schema, decoded, fport)
            assert not out.get("errors"), out
            assert bytes(out["bytes"]).hex() == case["decode"]["payload"]
    for step in case.get("encode") or []:
        data = json.loads(step["input"])
        record = step_record(step, decoded)
        if record is not None:
            data["_decode"] = record
        out = encode_codec(schema, data, fport)
        if "error" in step:
            assert any(step["error"] in e for e in out.get("errors") or []), out
            assert not out.get("bytes"), out
        else:
            assert not out.get("errors"), out
            assert bytes(out["bytes"]).hex() == step["payload"]


def test_a_generated_codec_never_reports_the_record():
    """PS-467, PS-572: decodeUplink's data carries neither `_meta` nor `_decode`."""
    case = CASES[0]
    schema = yaml.safe_load(case["schema"])
    script = (
        TS013Generator(schema).generate()
        + "\nprocess.stdout.write(JSON.stringify(decodeUplink({bytes: %s, fPort: 1})));"
        % json.dumps(list(bytes.fromhex(case["decode"]["payload"])))
    )
    done = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True)
    data = json.loads(done.stdout)["data"]
    assert "_decode" not in data and "_meta" not in data
