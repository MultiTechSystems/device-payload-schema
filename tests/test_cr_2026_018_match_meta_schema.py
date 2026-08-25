"""CR-2026-018: `match` described in the meta-schema, and checked at all.

The third construct out of `definitions.field`'s `additionalProperties`, after `tlv`
(CR-2026-016) and `flagged` (CR-2026-017). It is the least uniform of the three, and both
halves of that are worth pinning.

**Nothing validated it.** `match` was listed as an allowed field construct and never
looked inside, so a block with no discriminator - no `field` and no `length` - was
reported valid and then decoded as nothing. `flagged` had every key enforced; `match` had
none. This adds the checks that hold on every implementation.

**Support was uneven, and CR-2026-020 closed it.** Only `field` and `cases` used to be
honoured by all five: `length` and `name` were ignored by the Go interpreter and the TS013
generator, and `var` and `default` were honoured by the Python interpreter alone. The
tests below now assert the reverse of what they were written to assert - that the
description claims parity and does not warn about any key - and the corpus tripwire that
fired when the fixtures landed has become a test that the corpus exercises the keys.

The uneven keys were deliberately *not* enforced by the validator, and still are not:
refusing what only some implementations honour would have rejected schemas that work.
The validator refuses what nothing can use.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from validate_schema import validate_schema_structure  # noqa: E402

META = json.loads((REPO_ROOT / "schemas" / "payload-schema.json").read_text())
CORPUS = REPO_ROOT / "schemas" / "devices"
EXAMPLES = REPO_ROOT / "examples"

MATCH = META["definitions"]["match"]

#: Read out of each implementation: tools/schema_interpreter.py
#: `_decode_match_option_b`, go/schema/schema.go (the `fm["match"]` block),
#: bindings/java/.../Schema.java, dotnet/PayloadSchema/SchemaParser.cs, and
#: tools/generate_ts013_codec.py `_gen_decode_match`.
#: Honoured by all five since CR-2026-020. The split below is kept because it names what
#: each group used to be, and the tests assert the warnings for them are gone.
UNIVERSAL = {"field", "cases"}
WAS_THREE_OF_FIVE = {"length", "name"}
WAS_PYTHON_ONLY = {"var", "default"}
DESCRIBED = UNIVERSAL | WAS_THREE_OF_FIVE | WAS_PYTHON_ONLY


def every_match():
    """Each Option B `match` mapping in the corpus and the examples, with its file."""
    def walk(node, path):
        if isinstance(node, dict):
            match = node.get("match")
            if isinstance(match, dict):
                yield match, path
            for value in node.values():
                yield from walk(value, path)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item, path)

    for path in sorted(CORPUS.rglob("*.yaml")) + sorted(EXAMPLES.rglob("*.yaml")):
        try:
            schema = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        if isinstance(schema, dict):
            yield from walk(schema, path)


def match_schema(match):
    return {
        "name": "probe",
        "endian": "big",
        "fields": [{"name": "kind", "type": "u8"}, {"match": match}],
    }


def working_match(**overrides):
    match = {"field": "kind", "cases": {1: [{"name": "a", "type": "u8"}]}}
    match.update(overrides)
    return match


class TestTheDescriptionExists:
    def test_field_points_at_it(self):
        assert META["definitions"]["field"]["properties"]["match"] == {
            "$ref": "#/definitions/match"
        }

    def test_it_closes(self):
        assert MATCH["additionalProperties"] is False

    def test_field_itself_stays_permissive(self):
        assert META["definitions"]["field"]["additionalProperties"] is True

    def test_it_describes_every_key_an_implementation_reads(self):
        assert set(MATCH["properties"]) == DESCRIBED

    def test_a_discriminator_is_required_one_way_or_the_other(self):
        assert MATCH["anyOf"] == [{"required": ["field"]}, {"required": ["length"]}]

    def test_cases_is_not_required(self):
        """A block carrying only `default: [fields]` decodes those fields."""
        assert "required" not in MATCH
        assert all("cases" not in branch.get("required", []) for branch in MATCH["anyOf"])

    def test_every_key_carries_a_description(self):
        bare = [k for k, v in MATCH["properties"].items() if not v.get("description")]
        assert not bare, bare

    def test_the_earlier_constructs_are_undisturbed(self):
        props = META["definitions"]["field"]["properties"]
        assert props["tlv"] == {"$ref": "#/definitions/tlv"}
        assert props["flagged"] == {"$ref": "#/definitions/flagged"}


class TestTheDescriptionClaimsParity:
    """CR-2026-020 closed the gap, so no key may still warn about being ignored."""

    @pytest.mark.parametrize("key", sorted(DESCRIBED))
    def test_no_key_warns_that_it_is_ignored(self, key):
        text = MATCH["properties"][key]["description"]
        for weasel in ("ignored by", "alone", "TS013", "Go interpreter"):
            assert weasel not in text, f"{key} still warns: {text}"

    def test_the_construct_says_every_key_is_honoured(self):
        text = MATCH["description"]
        assert "honoured by all five implementations" in text

    def test_it_records_what_used_to_be_wrong(self):
        """The history is the reason the fixtures exist; losing it invites a regression."""
        assert "CR-2026-020" in MATCH["description"]

    def test_the_cases_key_no_longer_claims_a_list_spelling(self):
        """A YAML mapping key cannot be a list, so Option B never accepted one."""
        text = MATCH["properties"]["cases"]["description"]
        assert "cannot be a list" in text

    def test_the_default_default_is_recorded_as_error(self):
        """It differs from a tlv's `unknown`, which defaults to skip."""
        text = MATCH["properties"]["default"]["description"]
        assert "`error`" in text
        tlv_unknown = META["definitions"]["tlv"]["properties"]["unknown"]
        assert tlv_unknown["default"] == "skip"


class TestTheValidatorNowLooksInside:
    """None of these were caught before; each is refused on every implementation."""

    def test_a_working_block_passes(self):
        assert validate_schema_structure(match_schema(working_match())) == []

    def test_a_block_with_no_discriminator_is_refused(self):
        match = working_match()
        del match["field"]
        errors = validate_schema_structure(match_schema(match))
        assert any("field" in e and "length" in e for e in errors), errors

    def test_length_alone_is_enough(self):
        match = working_match()
        del match["field"]
        match["length"] = 1
        assert validate_schema_structure(match_schema(match)) == []

    def test_a_non_object_block_is_refused(self):
        errors = validate_schema_structure(match_schema(["not", "an", "object"]))
        assert any("must be an object" in e for e in errors), errors

    @pytest.mark.parametrize("width", [0, 9, -1, 1.5, "1", True])
    def test_an_impossible_length_is_refused(self, width):
        match = working_match()
        del match["field"]
        match["length"] = width
        errors = validate_schema_structure(match_schema(match))
        assert any("length" in e for e in errors), f"{width!r}: {errors}"

    @pytest.mark.parametrize("width", [1, 2, 4, 8])
    def test_a_usable_length_is_accepted(self, width):
        match = working_match()
        del match["field"]
        match["length"] = width
        assert validate_schema_structure(match_schema(match)) == []

    def test_a_non_string_field_is_refused(self):
        errors = validate_schema_structure(match_schema(working_match(field=7)))
        assert any("field" in e for e in errors), errors

    def test_cases_must_map_to_field_lists(self):
        errors = validate_schema_structure(
            match_schema(working_match(cases={1: {"name": "a", "type": "u8"}}))
        )
        assert any("array of fields" in e for e in errors), errors

    def test_cases_must_be_a_mapping(self):
        errors = validate_schema_structure(
            match_schema(working_match(cases=[{"case": 1, "fields": []}]))
        )
        assert any("cases" in e for e in errors), errors

    @pytest.mark.parametrize("fallback", ["error", "skip"])
    def test_the_documented_defaults_are_accepted(self, fallback):
        assert validate_schema_structure(
            match_schema(working_match(default=fallback))) == []

    def test_a_field_list_default_is_accepted(self):
        assert validate_schema_structure(
            match_schema(working_match(default=[{"name": "z", "type": "u8"}]))) == []

    @pytest.mark.parametrize("fallback", ["ignore", 0, {}])
    def test_anything_else_as_default_is_refused(self, fallback):
        errors = validate_schema_structure(match_schema(working_match(default=fallback)))
        assert any("default" in e for e in errors), f"{fallback!r}: {errors}"

    def test_a_bad_field_inside_a_case_is_reported(self):
        """The block's cases are now descended into, which they were not before."""
        errors = validate_schema_structure(
            match_schema(working_match(cases={1: [{"name": "a", "type": "s17"}]}))
        )
        assert any("s17" in e for e in errors), errors

    def test_the_uneven_keys_are_not_refused(self):
        """Deliberate: the validator refuses what nothing honours, not what some do."""
        for key, value in (("var", "kept"), ("name", "kind_out"), ("length", 2)):
            match = working_match(**{key: value})
            assert validate_schema_structure(match_schema(match)) == [], key


class TestTheCorpusConforms:
    def test_no_match_carries_an_undescribed_key(self):
        offenders = [
            f"{path.name}: {key}"
            for match, path in every_match()
            for key in match
            if key not in MATCH["properties"]
        ]
        assert not offenders, (
            "used and not described, so `additionalProperties: false` would reject a "
            "valid schema: " + ", ".join(sorted(set(offenders)))
        )

    def test_every_block_has_a_discriminator(self):
        missing = [
            path.name for match, path in every_match()
            if "field" not in match and "length" not in match
        ]
        assert not missing, missing

    def test_the_shapes_hold(self):
        problems = []
        for match, path in every_match():
            if "field" in match and not isinstance(match["field"], str):
                problems.append(f"{path.name}: field={match['field']!r}")
            cases = match.get("cases")
            if cases is not None:
                if not isinstance(cases, dict):
                    problems.append(f"{path.name}: cases is {type(cases).__name__}")
                else:
                    for key, value in cases.items():
                        if not isinstance(value, list):
                            problems.append(f"{path.name}: case {key!r}")
            if "default" in match:
                fallback = match["default"]
                if not (fallback in ("error", "skip") or isinstance(fallback, list)):
                    problems.append(f"{path.name}: default={fallback!r}")
        assert not problems, problems

    def test_the_corpus_now_exercises_the_keys_that_used_to_diverge(self):
        """The inverse of the tripwire this test was.

        It used to assert no schema reached for `length`, `name` or `var`, because those
        decoded differently on the Go interpreter and the generated codec, and it fired
        when CR-2026-020's fixtures landed. That was its purpose. Keeping it pointed the
        other way is what stops the gap reopening unobserved: parity with no vector
        exercising it is parity nobody is checking.
        """
        covered = {
            key
            for match, _ in every_match()
            for key in (WAS_THREE_OF_FIVE | WAS_PYTHON_ONLY)
            if key in match
        }
        assert covered == (WAS_THREE_OF_FIVE | WAS_PYTHON_ONLY), (
            "no corpus vector exercises: "
            + ", ".join(sorted((WAS_THREE_OF_FIVE | WAS_PYTHON_ONLY) - covered))
        )

    def test_the_corpus_actually_exercises_the_construct(self):
        found = list(every_match())
        assert found, "no match constructs found; every sweep above is vacuous"
        assert any(m.get("cases") for m, _ in found)


class TestTheGeneratorStillCannotClobberIt:
    def test_it_names_the_match_definition(self):
        import subprocess

        done = subprocess.run(
            [sys.executable, "tools/generate_jsonschema.py",
             "--output", "schemas/payload-schema.json"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert done.returncode != 0
        assert "definitions.match" in done.stderr

    def test_the_file_on_disk_survived(self):
        current = json.loads((REPO_ROOT / "schemas" / "payload-schema.json").read_text())
        assert {"tlv", "flagged", "flagged_group", "match"} <= set(current["definitions"])
