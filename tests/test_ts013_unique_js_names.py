"""Two field names that mangle to one JavaScript identifier are both generated.

The generated codec stages each field under `to_js_name(name)` and restores the
schema's own name at its entry points. Adeunis reports `ER+P-1_s` and `ER-P-1_s`, which
both mangle to `ER_P_1_s`, so the generator refused the schema. `unique_js_names` now
gives each name its own spelling, and every site that stages or references a field
reads that spelling through `to_js_name`, so a reference cannot land on the wrong one.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import generate_ts013_codec  # noqa: E402
from generate_ts013_codec import (  # noqa: E402
    TS013Generator,
    to_js_name,
    unique_js_names,
)
from schema_interpreter import SchemaInterpreter  # noqa: E402


def test_names_that_do_not_collide_keep_their_spelling():
    names = ["pm1.0", "4_20_mA", "temperature", "[06] Button Totals"]
    assert unique_js_names(names) == {name: to_js_name(name) for name in names}


def test_colliding_names_are_suffixed_in_sorted_order():
    assert unique_js_names(["ER-P-1_s", "ER+P-1_s"]) == {
        "ER+P-1_s": "ER_P_1_s",
        "ER-P-1_s": "ER_P_1_s_2",
    }


def test_a_name_that_is_its_own_spelling_keeps_it():
    assert unique_js_names(["a+b", "a-b", "a_b"]) == {
        "a_b": "a_b",
        "a+b": "a_b_2",
        "a-b": "a_b_3",
    }


def test_a_suffix_that_is_another_name_is_skipped():
    mapping = unique_js_names(["a+b", "a-b", "a_b", "a_b_2"])
    assert mapping == {"a_b": "a_b", "a_b_2": "a_b_2", "a+b": "a_b_3", "a-b": "a_b_4"}
    # Nor may it be a name that only mangles to that spelling.
    mapping = unique_js_names(["x+y", "x-y", "x.y.2"])
    assert mapping == {"x+y": "x_y", "x-y": "x_y_3", "x.y.2": "x_y_2"}
    assert len(set(mapping.values())) == len(mapping)


def test_overrides_are_scoped_to_one_generation():
    TS013Generator(
        {
            "name": "t",
            "fields": [{"name": "a+b", "type": "u8"}, {"name": "a-b", "type": "u8"}],
        }
    ).generate()
    assert generate_ts013_codec._JS_NAME_OVERRIDES == {}
    assert to_js_name("a-b") == "a_b"


REFS = {
    "name": "refs",
    "fields": [
        {"name": "a+b", "type": "u8"},
        {"name": "a-b", "type": "u8"},
        {"name": "a_b", "type": "u8"},
        {"name": "twice", "type": "number", "ref": "$a-b", "mult": 2},
        {
            "name": "diff",
            "type": "number",
            "compute": {"op": "sub", "a": "$a_b", "b": "$a+b"},
        },
        {
            "match": {
                "field": "$a-b",
                "cases": {2: [{"name": "kind", "type": "u8"}]},
                "default": "skip",
            }
        },
    ],
}


def run_js(schema, payload):
    code = TS013Generator(schema).generate()
    script = (
        code
        + "\nvar r = decodeUplink("
        + json.dumps({"bytes": list(payload), "fPort": 1})
        + ");\nvar e = encodeDownlink({data: r.data, fPort: 1});"
        + "\nprocess.stdout.write(JSON.stringify([r, e]));"
    )
    out = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize("payload", ["01020309", "05020307", "0A0B0C"])
def test_references_read_the_field_they_name(payload):
    raw = bytes.fromhex(payload)
    reference = SchemaInterpreter(REFS).decode(raw)
    assert reference.success, reference.errors
    decoded, encoded = run_js(REFS, raw)
    assert decoded["errors"] == [] and decoded["data"] == reference.data
    assert encoded["errors"] == [] and bytes(encoded["bytes"]) == raw
