"""CR-2026-057: the behaviour the errata make normative.

A zero divisor omits the field (PS-100, PS-278), and so does the log of a non-positive
number (PS-117); neither is reported as NaN (PS-282). The later fields still decode.
`type: integer` is admitted on a computed field (PS-112, PS-123) and a fractional
result is an error (PS-388) - held in test_cr_2026_037_type_vocabulary.py.
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

ABSENT = [
    ("bare div 0", {"type": "u8", "div": 0}, "07"),
    ("stage div 0", {"type": "u8", "transform": [{"div": 0}]}, "07"),
    ("log10 of 0", {"type": "u8", "transform": [{"log10": True}]}, "00"),
    ("log of -1", {"type": "s8", "transform": [{"log": True}]}, "FF"),
    ("log then lookup", {"type": "u8", "transform": [{"log": True}],
                         "lookup": {0: "zero"}}, "00"),
]


def schema_for(field):
    return {"name": "probe", "fields": [dict(field, name="v"), {"name": "w", "type": "u8"}]}


@pytest.mark.parametrize("label,field,raw", ABSENT, ids=[a[0] for a in ABSENT])
def test_the_reference_omits_the_field_and_carries_on(label, field, raw):
    result = SchemaInterpreter(schema_for(field)).decode(bytes.fromhex(raw + "05"))
    assert result.success, result.errors
    assert result.data == {"w": 5}


def run_codec(schema, payload):
    code = TS013Generator(schema).generate()
    script = code + "\nprocess.stdout.write(JSON.stringify(decodeUplink(" + json.dumps(
        {"bytes": list(payload), "fPort": 1}) + ")));"
    out = subprocess.run(["node"], input=script, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("label,field,raw", ABSENT, ids=[a[0] for a in ABSENT])
def test_the_generated_codec_omits_the_field_and_carries_on(label, field, raw):
    out = run_codec(schema_for(field), bytes.fromhex(raw + "05"))
    assert out["errors"] == []
    assert out["data"] == {"w": 5}


def test_a_positive_log_is_still_reported():
    field = {"type": "u8", "transform": [{"log10": True}]}
    assert SchemaInterpreter(schema_for(field)).decode(b"\x64\x05").data == {"v": 2, "w": 5}
