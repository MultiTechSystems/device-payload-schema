"""The scorer and the TTN cross-check must read nested output the way PS-043 to PS-045 do.

Three defects, each found by scoring schemas whose output nests, and each fixed:

- The JS half of the scorer compared a nested `null` expectation as a value, where
  PS-043 makes it an assertion of absence. A vector expecting `{bytes: {vol: null}}`
  failed although the generated codec correctly omitted `vol`.
- Branch coverage looked for a match's discriminator in the decoded output's top
  level, so a match inside a repeat element - or one keyed on an internal `_` field,
  which is never reported - read as entirely untested. Edge-case detection likewise
  looked only at top-level values, so a negative reading inside a repeat was missed.
- crossvalidate_ttn.compare() reported a vendor `null` against a key our decoder
  omits as a disagreement, where PS-043 makes it agreement.

All schemas here are synthetic.
"""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import crossvalidate_ttn as cv  # noqa: E402
from score_schema import (  # noqa: E402
    analyze_branch_coverage,
    check_edge_cases,
    run_js_tests,
    run_python_tests,
)

NESTED_NULL = {
    "name": "probe_nested_null",
    "endian": "big",
    "fields": [
        {
            "name": "bytes",
            "type": "object",
            "fields": [
                {"name": "_flag", "type": "u8"},
                {
                    "flagged": {
                        "field": "_flag",
                        "groups": [
                            {"bit": 0, "fields": [{"name": "vol", "type": "u8"}]}
                        ],
                    }
                },
                {"name": "temp", "type": "u8"},
            ],
        }
    ],
    "test_vectors": [
        {
            "name": "vol_absent",
            "payload": "00 05",
            "expected": {"bytes": {"vol": None, "temp": 5}},
        },
        {
            "name": "vol_present",
            "payload": "01 07 05",
            "expected": {"bytes": {"vol": 7, "temp": 5}},
        },
    ],
}

MATCH_IN_REPEAT = {
    "name": "probe_match_in_repeat",
    "endian": "big",
    "fields": [
        {
            "name": "items",
            "type": "repeat",
            "until": "end",
            "fields": [
                {"name": "kind", "type": "u8"},
                {
                    "match": {
                        "field": "$kind",
                        "cases": {
                            1: [{"name": "temp", "type": "s8"}],
                            2: [{"name": "hum", "type": "u8"}],
                        },
                    }
                },
            ],
        }
    ],
    "test_vectors": [
        {
            "name": "both_kinds",
            "payload": "01 F6 02 32",
            "expected": {"items": [{"kind": 1, "temp": -10}, {"kind": 2, "hum": 50}]},
        }
    ],
}

INTERNAL_DISCRIMINATOR = {
    "name": "probe_internal_discriminator",
    "endian": "big",
    "fields": [
        {"name": "_kind", "type": "u8"},
        {
            "match": {
                "field": "$_kind",
                "cases": {
                    1: [{"name": "temp", "type": "u8"}],
                    2: [{"name": "hum", "type": "u8"}],
                },
            }
        },
    ],
    "test_vectors": [
        {"name": "kind_1", "payload": "01 14", "expected": {"temp": 20}},
        {"name": "kind_2", "payload": "02 32", "expected": {"hum": 50}},
    ],
}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_js_nested_null_is_absence():
    status, errors = run_js_tests(NESTED_NULL, "probe.yaml")
    assert status == "pass", errors


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_js_nested_null_fails_when_the_key_is_reported():
    schema = dict(NESTED_NULL)
    schema["test_vectors"] = [
        {
            "name": "vol_reported",
            "payload": "01 07 05",
            "expected": {"bytes": {"vol": None}},
        }
    ]
    status, _errors = run_js_tests(schema, "probe.yaml")
    assert status == "fail"


def test_python_nested_null_is_absence():
    ok, passed, failed, errors, _decoded = run_python_tests(NESTED_NULL)
    assert ok and passed == 2 and failed == 0, errors


def test_match_inside_repeat_counts_each_branch():
    coverage, uncovered = analyze_branch_coverage(MATCH_IN_REPEAT)
    assert coverage == 1.0, uncovered


def test_match_inside_repeat_reports_an_unentered_branch():
    schema = dict(MATCH_IN_REPEAT)
    schema["test_vectors"] = [
        {
            "name": "kind_1_only",
            "payload": "01 F6",
            "expected": {"items": [{"kind": 1, "temp": -10}]},
        }
    ]
    coverage, uncovered = analyze_branch_coverage(schema)
    assert coverage == 0.5
    assert uncovered == ["match kind=2 untested"]


def test_match_in_a_record_body_is_scanned():
    schema = {
        "name": "probe_match_in_record",
        "endian": "big",
        "fields": [
            {
                "name": "messages",
                "type": "repeat",
                "until": "end",
                "fields": [
                    {"name": "_id", "type": "u8"},
                    {
                        "match": {
                            "field": "$_id",
                            "cases": {
                                7: [
                                    {
                                        "records": [
                                            [
                                                {"name": "_sub", "type": "u8"},
                                                {
                                                    "match": {
                                                        "field": "$_sub",
                                                        "cases": {
                                                            0: [
                                                                {
                                                                    "name": "a",
                                                                    "type": "u8",
                                                                }
                                                            ],
                                                            1: [
                                                                {
                                                                    "name": "b",
                                                                    "type": "u8",
                                                                }
                                                            ],
                                                        },
                                                    }
                                                },
                                            ]
                                        ]
                                    }
                                ],
                                0: [{"name": "c", "type": "u8"}],
                            },
                        }
                    },
                ],
            }
        ],
        "test_vectors": [
            {"name": "sub_0", "payload": "07 00 05", "expected": {}},
            {"name": "plain", "payload": "00 05", "expected": {}},
        ],
    }
    coverage, uncovered = analyze_branch_coverage(schema)
    # _sub=1 is declared inside the record and never entered.
    assert uncovered == ["match _sub=1 untested"]
    assert coverage == 0.75


def test_internal_discriminator_branches_are_covered():
    coverage, uncovered = analyze_branch_coverage(INTERNAL_DISCRIMINATOR)
    assert coverage == 1.0, uncovered


def test_negative_value_inside_a_repeat_is_seen():
    ok, _p, _f, errors, decoded = run_python_tests(MATCH_IN_REPEAT)
    assert ok, errors
    covered, missing = check_edge_cases(MATCH_IN_REPEAT, decoded)
    assert "negative" in covered
    assert "negative values" not in missing


def test_negative_value_in_a_repeat_of_single_values_is_seen():
    schema = {
        "name": "probe_element_negative",
        "endian": "big",
        "fields": [
            {
                "name": "readings",
                "type": "repeat",
                "until": "end",
                "element": {"type": "s8"},
            }
        ],
        "test_vectors": [
            {"name": "two", "payload": "05 FB", "expected": {"readings": [5, -5]}}
        ],
    }
    ok, _p, _f, errors, decoded = run_python_tests(schema)
    assert ok, errors
    covered, _missing = check_edge_cases(schema, decoded)
    assert "negative" in covered
    # An unnamed element is still a field that can decode negative, so a vector set
    # with no negative reading is missing one rather than excused from it.
    schema["test_vectors"] = [
        {"name": "two", "payload": "05 06", "expected": {"readings": [5, 6]}}
    ]
    ok, _p, _f, errors, decoded = run_python_tests(schema)
    assert ok, errors
    _covered, missing = check_edge_cases(schema, decoded)
    assert "negative values" in missing


TTN_SCHEMA = {
    "name": "probe_ttn_null",
    "endian": "big",
    "fields": [
        {"name": "a", "type": "u8"},
        {"name": "_flag", "type": "u8"},
        {
            "flagged": {
                "field": "_flag",
                "groups": [{"bit": 0, "fields": [{"name": "b", "type": "u8"}]}],
            }
        },
    ],
}


def test_ttn_vendor_null_against_an_omitted_key_agrees():
    assert cv.compare(TTN_SCHEMA, bytes([1, 0]), 1, {"a": 1, "b": None}) == []


def test_ttn_vendor_null_against_a_reported_key_disagrees():
    problems = cv.compare(TTN_SCHEMA, bytes([1, 1, 2]), 1, {"a": 1, "b": None})
    assert len(problems) == 1 and problems[0].startswith("b:")


def test_ttn_nested_vendor_null_against_an_omitted_key_agrees():
    schema = {
        "name": "probe_ttn_nested_null",
        "endian": "big",
        "fields": [{"name": "o", "type": "object", "fields": TTN_SCHEMA["fields"]}],
    }
    assert cv.compare(schema, bytes([1, 0]), 1, {"o": {"a": 1, "b": None}}) == []
    assert cv.compare(schema, bytes([1, 1, 2]), 1, {"o": {"a": 1, "b": None}})
