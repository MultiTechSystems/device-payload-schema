"""`name_from` in the generated output JSON Schema.

PS-265/PS-266: the reported key comes from a template filled in from values decoded
earlier in the same payload, so the field's own name is never a key. The output schema
described nothing for such a field, which made it the last construct whose output the
schema could not account for.

Two forms, depending on the template's references:

- Every reference has a closed set of values -> the whole key set is finite, so the keys
  are declared outright. A `lookup` counts as closed because PS-269 drops a field whose
  value is not in the mapping rather than reporting the raw number, so an unmapped value
  never reaches a key.
- Otherwise -> the template becomes an anchored `patternProperties` entry carrying the
  field's value schema.

The substitution behaviour each pattern has to accommodate was measured, not assumed: a
lookup reference substitutes the label, a signed field can contribute a minus sign, and a
scaled field renders as a decimal while an integral one loses its trailing zero.
"""

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import (  # noqa: E402
    MAX_ENUMERATED_KEYS,
    generate_output_schema,
    name_from_targets,
    variable_sources,
)
from schema_interpreter import SchemaInterpreter  # noqa: E402


def generated(body):
    return generate_output_schema(yaml.safe_load(body))


def patterns_of(doc):
    return doc.get("patternProperties") or {}


def matches(doc, key):
    return any(re.search(p, key) for p in patterns_of(doc))


NUMERIC = (
    "name: t\nendian: big\nfields:\n"
    "  - name: idx\n    type: u8\n    var: idx\n"
    "  - name: reading\n    type: u8\n    name_from: channel_${idx}_reading\n"
)

CLOSED = (
    "name: t\nendian: big\nfields:\n"
    "  - name: kind\n    type: u8\n    lookup: {0: alpha, 1: beta}\n"
    "  - name: reading\n    type: u8\n    name_from: ${kind}_reading\n"
)


class TestPatternForm:
    def test_a_numeric_reference_becomes_a_pattern(self):
        doc = generated(NUMERIC)
        assert list(patterns_of(doc)) == [r"^channel_\d+_reading$"]

    def test_the_pattern_matches_the_key_the_decoder_actually_reports(self):
        schema = yaml.safe_load(NUMERIC)
        doc = generate_output_schema(schema)
        data = SchemaInterpreter(schema).decode(bytes.fromhex("032A")).data
        assert "channel_3_reading" in data
        assert matches(doc, "channel_3_reading")

    def test_the_pattern_carries_the_fields_value_schema(self):
        doc = generated(NUMERIC)
        subschema = patterns_of(doc)[r"^channel_\d+_reading$"]
        assert subschema["type"] == "integer"
        assert subschema["maximum"] == 255

    def test_the_fields_own_name_is_not_declared(self):
        doc = generated(NUMERIC)
        assert "reading" not in doc["properties"]

    def test_a_signed_reference_allows_a_minus_sign(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: offset\n    type: s8\n"
            "  - name: reading\n    type: u8\n    name_from: at_${offset}_reading\n"
        )
        doc = generate_output_schema(schema)
        key = next(
            k for k in SchemaInterpreter(schema).decode(bytes.fromhex("FE2A")).data
            if k != "offset"
        )
        assert key == "at_-2_reading"
        assert matches(doc, key)

    def test_a_scaled_reference_allows_a_decimal(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: scaled\n    type: u8\n    div: 10\n"
            "  - name: reading\n    type: u8\n    name_from: v_${scaled}_reading\n"
        )
        doc = generate_output_schema(schema)
        # 0x19 -> 2.5 renders with its decimal; an integral value would not.
        assert matches(doc, "v_2.5_reading")
        assert matches(doc, "v_2_reading")

    def test_a_text_reference_is_permissive(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: tag\n    type: ascii\n    length: 3\n"
            "  - name: reading\n    type: u8\n    name_from: ${tag}_reading\n"
        )
        doc = generate_output_schema(schema)
        key = next(
            k for k in SchemaInterpreter(schema).decode(bytes.fromhex("4142432A")).data
            if k != "tag"
        )
        assert key == "ABC_reading"
        assert matches(doc, key)

    def test_the_pattern_is_anchored(self):
        doc = generated(NUMERIC)
        pattern = list(patterns_of(doc))[0]
        assert pattern.startswith("^") and pattern.endswith("$")
        assert not re.search(pattern, "prefix_channel_3_reading_suffix")


class TestEnumeratedForm:
    def test_closed_references_become_exact_properties(self):
        doc = generated(CLOSED)
        assert {"alpha_reading", "beta_reading"} <= set(doc["properties"])
        assert not patterns_of(doc)

    def test_the_enumerated_key_is_the_one_reported(self):
        schema = yaml.safe_load(CLOSED)
        doc = generate_output_schema(schema)
        data = SchemaInterpreter(schema).decode(bytes.fromhex("012A")).data
        assert "beta_reading" in data
        assert "beta_reading" in doc["properties"]

    def test_a_lookup_default_joins_the_closed_set(self):
        doc = generated(
            "name: t\nendian: big\nfields:\n"
            "  - name: kind\n    type: u8\n"
            "    lookup: {0: alpha, default: other}\n"
            "  - name: reading\n    type: u8\n    name_from: ${kind}_reading\n"
        )
        assert {"alpha_reading", "other_reading"} <= set(doc["properties"])

    def test_two_closed_references_produce_the_cross_product(self):
        doc = generated(
            "name: t\nendian: big\nfields:\n"
            "  - name: a\n    type: u8\n    lookup: {0: x, 1: y}\n"
            "  - name: b\n    type: u8\n    lookup: {0: p, 1: q}\n"
            "  - name: reading\n    type: u8\n    name_from: ${a}_${b}_reading\n"
        )
        assert {"x_p_reading", "x_q_reading", "y_p_reading", "y_q_reading"} <= set(
            doc["properties"]
        )

    def test_a_large_cross_product_falls_back_to_a_pattern(self):
        # Enumerating hundreds of properties for one field documents nothing.
        labels_a = "\n".join(f"      {i}: a{i}" for i in range(9))
        labels_b = "\n".join(f"      {i}: b{i}" for i in range(9))
        doc = generated(
            "name: t\nendian: big\nfields:\n"
            "  - name: a\n    type: u8\n    lookup:\n" + labels_a + "\n"
            "  - name: b\n    type: u8\n    lookup:\n" + labels_b + "\n"
            "  - name: reading\n    type: u8\n    name_from: ${a}_${b}_reading\n"
        )
        assert 9 * 9 > MAX_ENUMERATED_KEYS
        assert patterns_of(doc), "expected the pattern form for 81 combinations"

    def test_a_template_with_no_references_is_a_literal_key(self):
        doc = generated(
            "name: t\nendian: big\nfields:\n"
            "  - name: reading\n    type: u8\n    name_from: fixed_key\n"
        )
        assert "fixed_key" in doc["properties"]
        assert "reading" not in doc["properties"]


class TestReferenceResolution:
    def test_a_var_alias_resolves(self):
        # The interpreters store a value under the field's name and under `var:`.
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: idx\n    type: u8\n    var: ch\n"
            "  - name: reading\n    type: u8\n    name_from: channel_${ch}_reading\n"
        )
        doc = generate_output_schema(schema)
        key = next(
            k for k in SchemaInterpreter(schema).decode(bytes.fromhex("032A")).data
            if k != "idx"
        )
        assert key == "channel_3_reading"
        assert matches(doc, key)

    def test_an_unknown_reference_stays_permissive(self):
        # Better a loose pattern than one that rejects a key the decoder produces.
        keys, pattern = name_from_targets({"name_from": "x_${nope}_y"}, {})
        assert keys == []
        assert re.search(pattern, "x_anything_y")

    def test_variable_sources_indexes_names_and_aliases(self):
        sources = variable_sources(
            yaml.safe_load(
                "name: t\nfields:\n  - name: idx\n    type: u8\n    var: ch\n"
            )
        )
        assert sources["idx"] is sources["ch"]


class TestScope:
    def test_a_pattern_inside_a_nested_object_belongs_to_that_object(self):
        doc = generated(
            "name: t\nendian: big\nfields:\n"
            "  - object: inner\n"
            "    fields:\n"
            "      - name: idx\n        type: u8\n        var: idx\n"
            "      - name: reading\n        type: u8\n"
            "        name_from: channel_${idx}_reading\n"
        )
        nested = doc["properties"]["inner"]
        assert list(nested.get("patternProperties") or {}) == [
            r"^channel_\d+_reading$"
        ]
        assert not patterns_of(doc)

    def test_a_pattern_inside_a_match_case_reaches_the_top_level(self):
        doc = generated(
            "name: t\nendian: big\nfields:\n"
            "  - name: idx\n    type: u8\n    var: idx\n"
            "  - match:\n"
            "      field: $idx\n"
            "      cases:\n"
            "        3:\n"
            "          - name: reading\n            type: u8\n"
            "            name_from: channel_${idx}_reading\n"
        )
        assert list(patterns_of(doc)) == [r"^channel_\d+_reading$"]


class TestTheFixture:
    def test_name_from_yaml_is_fully_described(self):
        path = REPO_ROOT / "schemas/devices/_language-conformance/name-from.yaml"
        schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        doc = generate_output_schema(schema)
        for vector in schema["test_vectors"]:
            data = SchemaInterpreter(schema).decode(
                bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            ).data
            for key in data:
                if key == "_quality":
                    continue
                assert key in doc["properties"] or matches(doc, key), key
