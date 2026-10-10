"""CR-2026-103 (PS-512) and CR-2026-105 (PS-514, PS-515): a vendor codec's string
rendering of a number is compared by value, and a vendor-codec vector may correct the
codec as long as it names each field it corrects."""

import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

from crossvalidate_ttn import as_number, compare, corrections  # noqa: E402
from score_schema import check_provenance as provenance_summary  # noqa: E402
from validate_schema import correction_errors, is_independent_vector, validate_schema  # noqa: E402

_spec = importlib.util.spec_from_file_location("provenance_gate", TOOLS / "provenance-gate.py")
provenance_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(provenance_gate)

INDEPENDENT = {"vendor-doc", "vendor-codec", "field-capture", "spec-example"}

SCHEMA = {
    "name": "t",
    "fields": [
        {"name": "temperature", "type": "u16", "add": -32768, "div": 100, "sentinel": [0]},
        {"name": "battery", "type": "u8", "div": 10},
    ],
}


def corrected(expected, names):
    return {
        "name": "v", "payload": "0000 1f", "source": "vendor-codec-corrected",
        "expected": expected,
        "correction": [{"field": n, "codec": -327.68, "reason": "0x0000 is no reading"}
                       for n in names],
    }


# --- PS-512 -----------------------------------------------------------------------

def test_a_formatted_number_is_compared_by_value():
    assert as_number("3.10", 3.1) == 3.1
    assert as_number("-0.50", -0.5) == -0.5
    assert compare(SCHEMA, bytes.fromhex("8a771f"), None, {"battery": "3.10"}) == []


def test_only_a_plain_decimal_against_a_number_is_converted():
    for text in ("0x1A", "1e3", "3.1(low battery)", "on", ""):
        assert as_number(text, 3.1) == text
    assert as_number("3.10", "3.10") == "3.10"     # string against string stays a string
    assert as_number("1", True) == "1"            # a boolean is not a number here


def test_a_formatted_number_that_differs_still_disagrees():
    assert compare(SCHEMA, bytes.fromhex("8a771f"), None, {"battery": "3.20"})


# --- PS-514 -----------------------------------------------------------------------

def test_corrected_source_requires_correction():
    vec = corrected({"temperature": None}, [])
    del vec["correction"]
    assert correction_errors(vec)


def test_correction_needs_field_codec_and_reason_and_names_an_asserted_field():
    vec = corrected({"temperature": None, "battery": 3.1}, ["temperature"])
    assert correction_errors(vec) == []
    vec["correction"][0].pop("reason")
    assert any("reason" in e for e in correction_errors(vec))
    assert correction_errors(corrected({"battery": 3.1}, ["temperature"]))


def test_correction_on_another_source_is_an_error():
    vec = corrected({"temperature": None}, ["temperature"])
    vec["source"] = "vendor-codec"
    assert correction_errors(vec)


def test_the_validator_reports_a_bad_correction():
    good = dict(SCHEMA, test_vectors=[corrected({"temperature": None, "battery": 3.1},
                                                ["temperature"])])
    assert validate_schema(good).schema_valid, validate_schema(good).schema_errors
    bad = dict(SCHEMA, test_vectors=[dict(corrected({"temperature": None}, []), correction=[])])
    assert not validate_schema(bad).schema_valid


def test_crossval_skips_exactly_the_corrected_fields():
    schema = dict(SCHEMA, test_vectors=[corrected({"temperature": None, "battery": 3.1},
                                                  ["temperature"])])
    assert corrections(schema) == {("00001f", None): {"temperature"}}
    vendor = {"temperature": -327.68, "battery": 3.1}
    assert compare(schema, bytes.fromhex("00001f"), None, vendor) == []
    # the uncorrected field is still compared
    assert compare(schema, bytes.fromhex("00001f"), None, dict(vendor, battery=9.9))
    # and so is the corrected one, on any other payload
    assert compare(SCHEMA, bytes.fromhex("00001f"), None, vendor)


# --- PS-515 -----------------------------------------------------------------------

def test_a_corrected_vector_is_independent_unless_it_corrects_everything():
    assert is_independent_vector(corrected({"temperature": None, "battery": 3.1},
                                           ["temperature"]), INDEPENDENT)
    assert not is_independent_vector(corrected({"temperature": None}, ["temperature"]),
                                     INDEPENDENT)


def test_the_scorer_counts_it_as_independent():
    schema = dict(SCHEMA, test_vectors=[corrected({"temperature": None, "battery": 3.1},
                                                  ["temperature"])])
    summary = provenance_summary(schema)
    assert summary["independent_vectors"] == 1 and not summary["issues"]


def test_the_provenance_gate_admits_it_and_refuses_one_without_correction(tmp_path):
    vec = corrected({"temperature": None, "battery": 3.1}, ["temperature"])
    text = lambda v: __import__("yaml").safe_dump(dict(SCHEMA, test_vectors=[v]))  # noqa: E731
    path = "schemas/devices/acme/t.yaml"
    report = provenance_gate.check_schema(path, text(vec), None)
    assert not report.failures, report.failures
    del vec["correction"]
    assert provenance_gate.check_schema(path, text(vec), None).failures
