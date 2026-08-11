"""`$ref` resolution in the generated output JSON Schema.

`process_fields` never resolved `$ref`, so every field behind a reference was missing
from the output schema. `ref-header.yaml` described one of the three properties its
decoder reports.

Only local `#/definitions/...` references resolve, and the target's `fields:` are
spliced into the list they appear in rather than nested - matching the interpreters and
the TS013 generator. Cross-file references are a pre-step
(tools/schema_preprocessor.py), not this tool's job.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import expand_refs, generate_output_schema  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402


def props(schema):
    return set(generate_output_schema(schema).get("properties", {}))


class TestExpandRefs:
    def test_splices_a_definitions_fields(self):
        definitions = {"head": {"fields": [{"name": "a", "type": "u8"},
                                           {"name": "b", "type": "u8"}]}}
        out = expand_refs([{"$ref": "#/definitions/head"},
                           {"name": "c", "type": "u8"}], definitions)
        assert [f["name"] for f in out] == ["a", "b", "c"]

    def test_resolves_a_ref_inside_a_definition(self):
        definitions = {
            "outer": {"fields": [{"$ref": "#/definitions/inner"},
                                 {"name": "b", "type": "u8"}]},
            "inner": {"fields": [{"name": "a", "type": "u8"}]},
        }
        out = expand_refs([{"$ref": "#/definitions/outer"}], definitions)
        assert [f["name"] for f in out] == ["a", "b"]

    def test_a_cycle_terminates(self):
        # A definition that refers to itself must not recurse forever.
        definitions = {"loop": {"fields": [{"$ref": "#/definitions/loop"},
                                           {"name": "a", "type": "u8"}]}}
        out = expand_refs([{"$ref": "#/definitions/loop"}], definitions)
        assert all(isinstance(f, dict) for f in out)

    @pytest.mark.parametrize(
        "ref",
        [
            "#/definitions/missing",       # no such definition
            "#/components/schemas/thing",  # not a local definitions pointer
            "other.yaml#/definitions/x",   # cross-file, handled by the preprocessor
        ],
    )
    def test_an_unresolvable_ref_is_dropped_without_crashing(self, ref):
        out = expand_refs([{"$ref": ref}, {"name": "a", "type": "u8"}], {})
        assert [f["name"] for f in out] == ["a"]

    def test_a_definition_without_fields_is_not_spliced(self):
        # The library's single-field definitions (name/type at the top level, no
        # `fields:`) are inlined by the preprocessor; the interpreters do not splice
        # them either, so neither does this.
        definitions = {"temperature_c_div10": {"name": "temperature", "type": "s16"}}
        out = expand_refs([{"$ref": "#/definitions/temperature_c_div10"}], definitions)
        assert out == []

    def test_no_definitions_at_all(self):
        assert expand_refs([{"$ref": "#/definitions/x"}], None) == []


class TestGeneratedProperties:
    def test_referenced_fields_become_properties(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\n"
            "definitions:\n"
            "  head:\n    fields:\n"
            "      - name: version\n        type: u8\n"
            "      - name: msg_type\n        type: u8\n"
            "fields:\n"
            "  - $ref: '#/definitions/head'\n"
            "  - name: reading\n    type: u16\n"
        )
        assert props(schema) == {"version", "msg_type", "reading"}

    def test_internal_referenced_fields_stay_unreported(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\n"
            "definitions:\n"
            "  head:\n    fields:\n"
            "      - name: _scratch\n        type: u8\n"
            "      - name: version\n        type: u8\n"
            "fields:\n  - $ref: '#/definitions/head'\n"
        )
        assert props(schema) == {"version"}

    def test_a_ref_inside_a_flagged_group_resolves(self):
        # The definitions have to reach the recursive calls, not just the top-level one.
        schema = yaml.safe_load(
            "name: t\nendian: big\n"
            "definitions:\n"
            "  block:\n    fields:\n      - name: inner\n        type: u8\n"
            "fields:\n"
            "  - name: flags\n    type: u8\n"
            "    flagged:\n"
            "      groups:\n"
            "        - bit: 0\n"
            "          fields:\n"
            "            - $ref: '#/definitions/block'\n"
        )
        assert "inner" in props(schema)

    def test_a_ref_in_a_port_resolves(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\n"
            "definitions:\n"
            "  block:\n    fields:\n      - name: inner\n        type: u8\n"
            "ports:\n"
            "  1:\n    fields:\n      - $ref: '#/definitions/block'\n"
        )
        assert "inner" in props(schema)


class TestAgainstTheCorpus:
    def test_ref_header_declares_what_its_decoder_reports(self):
        path = REPO_ROOT / "schemas/devices/_language-conformance/ref-header.yaml"
        schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        declared = props(schema)
        for vector in schema["test_vectors"]:
            result = SchemaInterpreter(schema).decode(
                bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            )
            reported = {k for k in result.data if k != "_quality"}
            assert reported <= declared, reported - declared
        # It described only `reading` before $ref resolution.
        assert declared == {"version", "msg_type", "reading"}

    def test_reported_keys_are_declared_across_the_corpus(self):
        """Every key a decoder reports should be a declared property.

        Ratcheted rather than absolute: three schemas still fall short, for reasons
        that are not `$ref`.

        - name-from.yaml: `name_from` builds the output key at run time, so it cannot
          be declared from the schema alone.
        - rbs30x.yaml, laq4.yaml: both use the `match` construct, which
          process_fields does not traverse at all - it handles `switch` and `tlv`.
          A separate gap, not fixed here.
        """
        known_short = {"name-from.yaml", "rbs30x.yaml", "laq4.yaml"}
        short, complete = set(), 0
        for path in sorted(REPO_ROOT.joinpath("schemas").rglob("*.yaml")):
            try:
                schema = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(schema, dict) or not schema.get("test_vectors"):
                continue
            try:
                declared = props(schema)
            except Exception:
                continue
            missing = set()
            for vector in schema["test_vectors"]:
                try:
                    result = SchemaInterpreter(schema).decode(
                        bytes.fromhex(str(vector["payload"]).replace(" ", "")),
                        fPort=vector.get("fPort", vector.get("fport")),
                    )
                except Exception:
                    continue
                missing |= {
                    k for k in result.data if k != "_quality" and k not in declared
                }
            if missing:
                short.add(path.name)
            else:
                complete += 1
        assert short <= known_short, f"new schemas with undeclared keys: {short - known_short}"
        # A floor, so the check cannot pass by finding nothing to look at.
        assert complete >= 173, complete
