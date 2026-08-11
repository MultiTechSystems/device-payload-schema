"""`_quality` in the generated output JSON Schema.

PS-182 closes the key set: the object appears only when at least one field declares
`valid_range`, and only such fields appear in it. That makes it describable, so it is
declared rather than merely tolerated by `additionalProperties: true` - a consumer
reading the output schema could not otherwise learn the field exists.

The risk in declaring it is the opposite of the risk in omitting it: a key set that is
too small, combined with `additionalProperties: false`, would reject valid decoder
output. The corpus test below is what guards against that.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import generate_output_schema  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402

FLAGS = {"good", "out_of_range"}


def quality_of(schema):
    return generate_output_schema(schema).get("properties", {}).get("_quality")


def check_against(quality_schema, emitted):
    """Validate an emitted `_quality` object against its declaration.

    Hand-rolled rather than pulled in through jsonschema, which is not a dependency of
    this repo; only three things need checking and each is asserted explicitly.
    """
    problems = []
    declared = quality_schema.get("properties", {})
    extra = quality_schema.get("additionalProperties")
    for key, value in emitted.items():
        if key in declared:
            allowed = declared[key].get("enum", [])
        elif extra is False:
            problems.append(f"key '{key}' is not declared and extras are forbidden")
            continue
        elif isinstance(extra, dict):
            allowed = extra.get("enum", [])
        else:
            continue
        if value not in allowed:
            problems.append(f"'{key}' = {value!r} is not in {allowed}")
    return problems


class TestDeclaration:
    def test_absent_when_no_field_declares_a_range(self):
        # PS-182: the object only exists when a range does.
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n  - name: temperature\n    type: u8\n"
        )
        assert quality_of(schema) is None

    def test_present_when_a_field_declares_a_range(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: temperature\n    type: u8\n    valid_range: [5, 30]\n"
        )
        quality = quality_of(schema)
        assert quality["type"] == "object"
        assert set(quality["properties"]) == {"temperature"}
        assert set(quality["properties"]["temperature"]["enum"]) == FLAGS

    def test_only_fields_with_a_range_are_declared(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: temperature\n    type: u8\n    valid_range: [5, 30]\n"
            "  - name: counter\n    type: u8\n"
        )
        assert set(quality_of(schema)["properties"]) == {"temperature"}

    def test_internal_fields_are_not_declared(self):
        # A `_`-prefixed field is not reported, so it never carries a flag.
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: _raw\n    type: u8\n    valid_range: [5, 30]\n"
            "  - name: temperature\n    type: u8\n    valid_range: [5, 30]\n"
        )
        assert set(quality_of(schema)["properties"]) == {"temperature"}

    def test_key_set_is_closed_by_default(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: temperature\n    type: u8\n    valid_range: [5, 30]\n"
        )
        assert quality_of(schema)["additionalProperties"] is False

    def test_name_from_reopens_the_key_set(self):
        # The output key is decided at run time, so the set is not closed after all;
        # the values stay constrained but the key is accepted.
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: reading\n    type: u8\n    valid_range: [5, 30]\n"
            "    name_from: $label\n"
        )
        extra = quality_of(schema)["additionalProperties"]
        assert isinstance(extra, dict)
        assert set(extra["enum"]) == FLAGS

    def test_fields_behind_a_ref_are_declared(self):
        # 159 of the corpus's valid_range declarations sit in `definitions`, and
        # process_fields does not resolve `$ref` - so a walk of the field lists alone
        # would under-declare and `additionalProperties: false` would then reject
        # perfectly good output.
        schema = yaml.safe_load(
            "name: t\nendian: big\n"
            "definitions:\n"
            "  head:\n    fields:\n"
            "      - name: temperature\n        type: u8\n"
            "        valid_range: [5, 30]\n"
            "fields:\n  - $ref: '#/definitions/head'\n"
        )
        assert "temperature" in quality_of(schema)["properties"]

    def test_top_level_still_allows_other_metadata(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: temperature\n    type: u8\n    valid_range: [5, 30]\n"
        )
        assert generate_output_schema(schema)["additionalProperties"] is True


class TestAgainstTheCorpus:
    """Every `_quality` the interpreter emits must satisfy its own declaration."""

    def schemas_with_vectors(self):
        for path in sorted(REPO_ROOT.joinpath("schemas").rglob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            if "valid_range" not in text:
                continue
            try:
                schema = yaml.safe_load(text)
            except Exception:
                continue
            if isinstance(schema, dict) and schema.get("test_vectors"):
                yield path, schema

    def test_no_emitted_quality_violates_its_declaration(self):
        checked = 0
        problems = []
        for path, schema in self.schemas_with_vectors():
            quality_schema = quality_of(schema)
            for vector in schema["test_vectors"]:
                try:
                    payload = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
                    result = SchemaInterpreter(schema).decode(
                        payload, fPort=vector.get("fPort", vector.get("fport"))
                    )
                except Exception:
                    continue
                emitted = result.data.get("_quality")
                if emitted is None:
                    continue
                checked += 1
                if quality_schema is None:
                    problems.append(f"{path.name}: emits _quality but declares none")
                    continue
                problems += [
                    f"{path.name}/{vector['name']}: {p}"
                    for p in check_against(quality_schema, emitted)
                ]
        assert not problems, problems
        # The guard is only meaningful if it actually saw output.
        assert checked >= 12, f"expected the known quality vectors, saw {checked}"

    def test_the_two_device_schemas_declare_what_they_emit(self):
        for rel, expected in (
            (
                "schemas/devices/mclimate/vicki.yaml",
                {
                    "targetTemperature",
                    "sensorTemperature",
                    "relativeHumidity",
                    "batteryVoltage",
                    "valveOpenness",
                },
            ),
            (
                "schemas/devices/rakwireless/qingping.yaml",
                {"temperature", "humidity", "co2", "battery"},
            ),
        ):
            schema = yaml.safe_load(
                REPO_ROOT.joinpath(rel).read_text(encoding="utf-8")
            )
            declared = set(quality_of(schema)["properties"])
            assert expected <= declared, (rel, expected - declared)
