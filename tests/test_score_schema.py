"""Tests for the quality scoring tool.

Each test here corresponds to a way the scorer previously reported a schema as
better than it was. They are regression tests: the scoring rubric decides which
schemas the repository accepts and what tier a vendor may advertise, so a silent
change in what it credits is a correctness bug.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from score_schema import (  # noqa: E402
    CONFORMANCE_TOLERANCE,
    MIN_VECTORS_HIGH_TIER,
    analyze_branch_coverage,
    calculate_score,
    check_annotation_correctness,
    check_edge_cases,
    check_provenance,
    check_semantic_annotations,
    check_test_vectors_exist,
    detect_sensor,
    run_python_tests,
)


def temperature_schema(**field_extras):
    """A minimal one-field schema decoding 0x00E7 to 23.1 degrees."""
    field = {"name": "temperature", "type": "s16", "div": 10, "unit": "°C"}
    field.update(field_extras)
    return {
        "name": "unit_test_sensor",
        "version": 1,
        "endian": "big",
        "fields": [field],
        "test_vectors": [
            {"name": "basic", "payload": "00E7", "expected": {"temperature": 23.1}}
        ],
    }


def flagged_schema(vectors):
    """Two flagged groups, so branch coverage is measurable."""
    return {
        "name": "flagged_sensor",
        "version": 1,
        "endian": "big",
        "fields": [
            {"name": "flags", "type": "u8"},
            {
                "flagged": {
                    "field": "flags",
                    "groups": [
                        {"bit": 0, "fields": [{"name": "temperature", "type": "u8"}]},
                        {"bit": 1, "fields": [{"name": "voltage", "type": "u8"}]},
                    ],
                }
            },
        ],
        "test_vectors": vectors,
    }


class TestSensorDetection:
    """Detection matches whole tokens, not substrings."""

    @pytest.mark.parametrize(
        "field_name",
        [
            "photosynthetically_active_radiation",  # contains 'ph'
            "phase_angle",  # contains 'ph'
        ],
    )
    def test_substring_does_not_detect_a_sensor(self, field_name):
        assert detect_sensor({"name": field_name, "type": "u16"}) is None

    def test_substring_match_does_not_shadow_the_real_token(self):
        """'lightning_average_distance' is a distance, never a light sensor."""
        detected = detect_sensor({"name": "lightning_average_distance", "type": "u16"})
        assert detected == ("distance", 3330)

    @pytest.mark.parametrize(
        ("field_name", "expected_object"),
        [
            ("air_temperature", 3303),
            ("temperature", 3303),
            ("batteryVoltage", 3316),
            ("ph", 3326),
            ("co2_concentration", 3325),
        ],
    )
    def test_token_match_detects_a_sensor(self, field_name, expected_object):
        detected = detect_sensor({"name": field_name, "type": "u16"})
        assert detected is not None
        assert detected[1] == expected_object

    def test_annotation_disambiguates_a_name_matching_two_keywords(self):
        """'targetTemperature' is a Set Point (3308), not a temperature (3303)."""
        field = {"name": "targetTemperature", "type": "u8", "ipso": {"object": 3308}}
        assert detect_sensor(field) == ("target", 3308)
        ipso_ok, _senml_ok, problems = check_annotation_correctness(field, "target", 3308)
        assert ipso_ok
        assert not problems

    def test_unannotated_ambiguous_name_falls_back_to_the_longest_match(self):
        detected = detect_sensor({"name": "targetTemperature", "type": "u8"})
        assert detected == ("temperature", 3303)

    def test_field_can_declare_its_type(self):
        detected = detect_sensor({"name": "probe_a", "type": "u16", "sensor": "humidity"})
        assert detected == ("humidity", 3304)

    def test_field_can_opt_out(self):
        assert detect_sensor({"name": "temperature", "type": "u16", "sensor": "none"}) is None


class TestAnnotationCorrectness:
    """Presence of an annotation is not proof it is right."""

    def test_wrong_ipso_object_earns_nothing(self):
        field = {"name": "temperature", "unit": "°C", "ipso": {"object": 9999}}
        ipso_ok, _senml_ok, problems = check_annotation_correctness(field, "temperature", 3303)
        assert not ipso_ok
        assert problems

    def test_correct_ipso_object_is_credited(self):
        field = {"name": "temperature", "unit": "°C", "ipso": {"object": 3303}}
        ipso_ok, _senml_ok, problems = check_annotation_correctness(field, "temperature", 3303)
        assert ipso_ok
        assert not problems

    def test_senml_unit_must_match_the_sensor_type(self):
        field = {"name": "temperature", "unit": "°C", "senml": {"unit": "Pa"}}
        _ipso_ok, senml_ok, problems = check_annotation_correctness(field, "temperature", 3303)
        assert not senml_ok
        assert problems

    def test_scaled_unit_is_rejected_with_a_conversion_hint(self):
        """A field in kPa annotated as Pa misdeclares the value by 1000x."""
        field = {"name": "atmospheric_pressure", "unit": "kPa", "senml": {"unit": "Pa"}}
        _ipso_ok, senml_ok, problems = check_annotation_correctness(field, "pressure", 3323)
        assert not senml_ok
        assert any("convert" in p for p in problems)

    def test_equivalent_unit_spelling_is_accepted(self):
        field = {"name": "temperature", "unit": "°C", "senml": {"unit": "Cel"}}
        _ipso_ok, senml_ok, problems = check_annotation_correctness(field, "temperature", 3303)
        assert senml_ok
        assert not problems

    def test_annotations_on_undetectable_fields_earn_no_credit(self):
        """Otherwise semantic marks could be farmed from unrelated fields."""
        schema = temperature_schema(sensor="none")
        schema["fields"].append(
            {"name": "opaque_counter", "type": "u8", "ipso": {"object": 3303},
             "senml": {"unit": "Cel"}, "semantic": "nonsense"}
        )
        results, _recs = check_semantic_annotations(schema)
        assert results["detectable_sensors"] == 0
        assert results["ipso_mapped"] == 0
        assert results["senml_mapped"] == 0


class TestVectorComparison:
    """The scorer and validate_schema must agree on pass/fail."""

    def test_tolerance_matches_the_validator(self):
        assert CONFORMANCE_TOLERANCE == 0.001

    def test_error_larger_than_tolerance_fails(self):
        schema = temperature_schema()
        schema["test_vectors"][0]["expected"]["temperature"] = 23.105
        passed, _n_pass, n_fail, _errors, _decoded = run_python_tests(schema)
        assert not passed
        assert n_fail == 1

    def test_error_within_tolerance_passes(self):
        schema = temperature_schema()
        schema["test_vectors"][0]["expected"]["temperature"] = 23.1005
        passed, n_pass, _n_fail, _errors, _decoded = run_python_tests(schema)
        assert passed
        assert n_pass == 1

    def test_decoded_values_are_returned_for_edge_analysis(self):
        _passed, _n_pass, _n_fail, _errors, decoded = run_python_tests(temperature_schema())
        assert decoded and decoded[0][1]["temperature"] == pytest.approx(23.1)


class TestVectorCompleteness:
    def test_vector_without_payload_does_not_count(self):
        schema = temperature_schema()
        schema["test_vectors"].append({"name": "empty", "expected": {"temperature": 1}})
        has_vectors, count, issues = check_test_vectors_exist(schema)
        assert has_vectors
        assert count == 1
        assert issues


class TestEdgeCases:
    """Edge coverage comes from decoded values, never from a vector's name."""

    def test_name_alone_does_not_prove_coverage(self):
        schema = temperature_schema()
        schema["test_vectors"] = [
            {"name": "zero_and_negative_and_maximum", "payload": "00E7",
             "description": "zero negative maximum minimum",
             "expected": {"temperature": 23.1}}
        ]
        _p, _n, _f, _e, decoded = run_python_tests(schema)
        covered, missing = check_edge_cases(schema, decoded)
        assert "zero" not in covered
        assert "negative" not in covered
        assert missing

    def test_values_prove_coverage(self):
        schema = temperature_schema()
        schema["test_vectors"] = [
            {"name": "a", "payload": "0000", "expected": {"temperature": 0.0}},
            {"name": "b", "payload": "FFFF", "expected": {"temperature": -0.1}},
        ]
        _p, _n, _f, _e, decoded = run_python_tests(schema)
        covered, _missing = check_edge_cases(schema, decoded)
        assert "zero" in covered
        assert "negative" in covered
        assert "max" in covered

    def test_fixed_layout_does_not_demand_a_shorter_payload(self):
        _p, _n, _f, _e, decoded = run_python_tests(temperature_schema())
        covered, _missing = check_edge_cases(temperature_schema(), decoded)
        assert "min_payload" in covered


class TestBranchCoverage:
    """Coverage is measured by entering branches, not by counting vectors."""

    def test_untested_branch_is_reported(self):
        schema = flagged_schema(
            [{"name": "only_bit0", "payload": "0105", "expected": {"flags": 1}}]
        )
        coverage, uncovered = analyze_branch_coverage(schema)
        assert coverage == 0.5
        assert any("[1]" in u for u in uncovered)

    def test_all_branches_covered(self):
        schema = flagged_schema(
            [
                {"name": "bit0", "payload": "0105", "expected": {"flags": 1}},
                {"name": "bit1", "payload": "0206", "expected": {"flags": 2}},
            ]
        )
        coverage, uncovered = analyze_branch_coverage(schema)
        assert coverage == 1.0
        assert uncovered == []

    def test_vector_count_alone_does_not_grant_coverage(self):
        schema = flagged_schema(
            [
                {"name": "n%d" % i, "payload": "0105", "expected": {"flags": 1}}
                for i in range(6)
            ]
        )
        coverage, _uncovered = analyze_branch_coverage(schema)
        assert coverage == 0.5


class TestProvenance:
    def test_missing_source_is_self_verified(self):
        assert check_provenance(temperature_schema())["verification"] == "self"

    def test_vendor_source_is_independent(self):
        schema = temperature_schema()
        schema["test_vectors"][0]["source"] = "vendor-codec"
        provenance = check_provenance(schema)
        assert provenance["verification"] == "independent"
        assert provenance["independent_vectors"] == 1

    def test_generated_source_is_not_independent(self):
        schema = temperature_schema()
        schema["test_vectors"][0]["source"] = "generated"
        assert check_provenance(schema)["verification"] == "self"

    def test_unrecognised_source_is_reported(self):
        schema = temperature_schema()
        schema["test_vectors"][0]["source"] = "vibes"
        assert check_provenance(schema)["issues"]


class TestTiers:
    """Tier boundaries and the PS-239 gates."""

    def _perfect_results(self, **overrides):
        results = {
            "schema_valid": True,
            "has_test_vectors": True,
            "test_count": 6,
            "python_tests_pass": True,
            "js_status": "pass",
            "branch_coverage": 1.0,
            "edge_cases_covered": ["zero", "max", "negative", "min_payload"],
            "edge_cases_missing": [],
            "semantic_annotations": {
                "total_fields": 1,
                "detectable_sensors": 1,
                "ipso_mapped": 1,
                "senml_mapped": 1,
                "semantic_mapped": 1,
                "annotation_errors": [],
            },
            "provenance": {"verification": "independent"},
        }
        results.update(overrides)
        return results

    def test_perfect_schema_is_platinum(self):
        score, tier = calculate_score(self._perfect_results())
        assert tier == "PLATINUM"
        assert score >= 95

    def test_no_vectors_is_rejected_not_bronze(self):
        """The specification reserves Bronze for 60-69%; below that is Rejected."""
        results = self._perfect_results(
            has_test_vectors=False,
            test_count=0,
            python_tests_pass=False,
            js_status="skipped",
            branch_coverage=0.0,
            edge_cases_covered=[],
            edge_cases_missing=["zero values"],
            semantic_annotations={
                "total_fields": 1, "detectable_sensors": 1, "ipso_mapped": 0,
                "senml_mapped": 0, "semantic_mapped": 0, "annotation_errors": [],
            },
        )
        _score, tier = calculate_score(results)
        assert tier == "REJECTED"

    def test_incomplete_branch_coverage_caps_below_gold(self):
        _score, tier = calculate_score(self._perfect_results(branch_coverage=0.5))
        assert tier not in ("GOLD", "PLATINUM")

    def test_too_few_vectors_caps_below_gold(self):
        results = self._perfect_results(test_count=MIN_VECTORS_HIGH_TIER - 1)
        _score, tier = calculate_score(results)
        assert tier not in ("GOLD", "PLATINUM")

    def test_wrong_annotations_cap_below_gold(self):
        semantic = dict(self._perfect_results()["semantic_annotations"])
        semantic["annotation_errors"] = ["temperature: ipso object 9999 is wrong"]
        _score, tier = calculate_score(self._perfect_results(semantic_annotations=semantic))
        assert tier not in ("GOLD", "PLATINUM")

    def test_skipped_js_does_not_penalise_the_score(self):
        """A score must not depend on whether Node.js is installed."""
        with_js = calculate_score(self._perfect_results())[0]
        without_js = calculate_score(self._perfect_results(js_status="skipped"))[0]
        assert without_js == pytest.approx(with_js)

    def test_generator_gap_does_not_penalise_the_score(self):
        gap = calculate_score(self._perfect_results(js_status="generator-gap"))[0]
        assert gap == pytest.approx(calculate_score(self._perfect_results())[0])

    def test_failing_js_does_cost_points(self):
        failing = calculate_score(self._perfect_results(js_status="fail"))[0]
        assert failing < calculate_score(self._perfect_results())[0]

    def test_self_sourced_vectors_cap_below_gold(self):
        """PS-264: a schema whose vectors all came from this implementation
        cannot be certified above Silver, however high its weighted score."""
        results = self._perfect_results(provenance={"verification": "self"})
        _score, tier = calculate_score(dict(results))
        assert tier == "SILVER"

    def test_provenance_gate_can_be_disabled(self):
        """--no-require-provenance scores the rubric without the PS-264 gate."""
        results = self._perfect_results(provenance={"verification": "self"})
        tier = calculate_score(dict(results), require_provenance=False)[1]
        assert tier == "PLATINUM"

    def test_provenance_gate_is_reported(self):
        results = self._perfect_results(provenance={"verification": "self"})
        calculate_score(results)
        assert any("PS-264" in gate for gate in results["tier_capped_by"])


# --- encode vectors -------------------------------------------------------------
#
# CR-2026-012 settled the encode vector's spelling as `input` + `expected_payload`
# (+ `direction`), and schemas started carrying them. The scorer read `payload` from
# every vector regardless, so an encode vector decoded empty bytes and scored as a
# failure — costing the whole 20-point Python component. Ten schemas dropped a tier
# or two, one of them from PLATINUM to SILVER, while validate_schema and
# vector-verdicts passed the same vectors.

ENCODE_SCHEMA = {
    "name": "encode_only",
    "version": 1,
    "endian": "big",
    "fields": [
        {"name": "command", "type": "u8"},
        {"name": "interval", "type": "u16"},
    ],
}


def _encode_vector(**over):
    v = {
        "name": "set_interval",
        "direction": "downlink",
        "input": {"command": 1, "interval": 600},
        "expected_payload": "010258",
        "source": "generated",
    }
    v.update(over)
    return v


def test_encode_vector_is_run_as_an_encode_not_a_decode():
    schema = dict(ENCODE_SCHEMA, test_vectors=[_encode_vector()])
    ok, passed, failed, errors, _ = run_python_tests(schema)
    assert ok, errors
    assert (passed, failed) == (1, 0)


def test_encode_vector_that_produces_the_wrong_bytes_fails():
    """The credit has to be earned: a wrong expectation must still fail."""
    schema = dict(ENCODE_SCHEMA,
                  test_vectors=[_encode_vector(expected_payload="01FFFF")])
    ok, passed, failed, errors, _ = run_python_tests(schema)
    assert not ok
    assert (passed, failed) == (0, 1)
    assert any("expected" in e for e in errors)


def test_expected_payload_may_be_a_byte_array():
    """Clause 5 wrote the bytes as an array; CR-2026-012 keeps both readable."""
    schema = dict(ENCODE_SCHEMA,
                  test_vectors=[_encode_vector(expected_payload=[0x01, 0x02, 0x58])])
    ok, passed, failed, _, _ = run_python_tests(schema)
    assert ok and (passed, failed) == (1, 0)


def test_decode_and_encode_vectors_score_together():
    """A schema carrying both kinds gets credit for both."""
    schema = dict(ENCODE_SCHEMA, test_vectors=[
        {"name": "decode_it", "payload": "010258",
         "expected": {"command": 1, "interval": 600}},
        _encode_vector(),
    ])
    ok, passed, failed, errors, _ = run_python_tests(schema)
    assert ok, errors
    assert (passed, failed) == (2, 0)
