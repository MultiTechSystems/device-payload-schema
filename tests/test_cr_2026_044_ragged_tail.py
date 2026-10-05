"""CR-2026-044: a ragged tail under `until: end` is an error (PS-343, PS-344, PS-344a).

Where too few bytes remain for a whole element, the decode fails naming the repeat and
calling it a ragged tail, and no partial result is reported as a success. The generated
codec read past the end as zeros, so a short tail decoded as a last element of zeros.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402

FIXED = {"name": "p", "fields": [{"name": "r", "type": "repeat", "until": "end",
                                  "fields": [{"name": "a", "type": "u8"},
                                             {"name": "b", "type": "u16"}]}]}


def decode_js(schema, payload):
    code = TS013Generator(schema).generate()
    script = code + "\nprocess.stdout.write(JSON.stringify(decodeUplink(" + json.dumps(
        {"bytes": list(payload), "fPort": 1}) + ")));"
    return json.loads(subprocess.run(["node"], input=script, capture_output=True,
                                     text=True, check=True).stdout)


@pytest.mark.parametrize("payload", ["01000202", "0100020200"])
def test_a_ragged_tail_is_an_error_on_both_paths(payload):
    raw = bytes.fromhex(payload)
    result = SchemaInterpreter(FIXED).decode(raw)
    assert not result.success and result.data == {}
    assert any("'r'" in e and "ragged tail" in e for e in result.errors), result.errors
    js = decode_js(FIXED, raw)
    assert js["data"] == {} and any("ragged tail" in e for e in js["errors"]), js


def test_the_check_comes_before_the_element():
    errors = SchemaInterpreter(FIXED).decode(bytes.fromhex("01000202")).errors
    assert any("fewer than the 3 an element takes" in e for e in errors), errors


def test_whole_elements_still_decode():
    assert SchemaInterpreter(FIXED).decode(bytes.fromhex("010002020003")).data == {
        "r": [{"a": 1, "b": 2}, {"a": 2, "b": 3}]}
