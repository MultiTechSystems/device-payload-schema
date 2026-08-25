"""CR-2026-019: `byte_group` and `repeat` described, and checked at all.

The last two constructs out of `definitions.field`'s `additionalProperties`, closing the
run begun by CR-2026-016. Both were undescribed *and* unchecked, so between them seven
field-level keys were invisible to the meta-schema and every malformed spelling was
reported valid:

    byte_group  size  count  byte_length  until  max  min

`repeat` is the better news of the two: all four interpreters read every key, and only
`max`/`min` are missing from the TS013 generator, which has its own no-progress guard.
After `match` in CR-2026-018 that is a relief - this is a construct the implementations
agree on.

What they did not have was any validation. A repeat with no bound, or with all three, or
with `until: banana`, or with no members, passed. The first, third and fourth raise at
decode in every interpreter. The second is worse than an error: the decode silently
follows `count` > `byte_length` > `until`, so the losing bounds describe something the
schema does not do, which is exactly what PS-083 exists to forbid.

`byte_group` has two spellings and nothing checked either. `byte_group: 7` was valid, and
a group with no members was valid and then decoded as nothing, because the construct
returns early on an empty field list.
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
FIELD_PROPS = META["definitions"]["field"]["properties"]
BYTE_GROUP = FIELD_PROPS["byte_group"]
CORPUS = REPO_ROOT / "schemas" / "devices"
EXAMPLES = REPO_ROOT / "examples"

#: The keys this CR describes, none of which the meta-schema mentioned before.
DESCRIBED = ("byte_group", "size", "count", "byte_length", "until", "max", "min")

#: Read by the four interpreters and not by the TS013 generator.
GENERATOR_IGNORES = ("max", "min")


def repeat_field(**overrides):
    field = {"name": "r", "type": "repeat", "fields": [{"name": "a", "type": "u8"}]}
    field.update(overrides)
    return field


def schema_with(field):
    return {"name": "probe", "endian": "big", "fields": [field]}


def constructs(key):
    """Each use of `key` in the corpus and examples, with the field and its file."""
    def walk(node, path):
        if isinstance(node, dict):
            if key in node:
                yield node, path
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


def repeats():
    for field, path in constructs("type"):
        if field.get("type") == "repeat":
            yield field, path


class TestTheKeysAreDescribed:
    @pytest.mark.parametrize("key", DESCRIBED)
    def test_it_exists_and_says_something(self, key):
        assert key in FIELD_PROPS, f"{key} is still undescribed"
        assert FIELD_PROPS[key].get("description"), key

    def test_field_itself_stays_permissive(self):
        """The whole run of five CRs never closed this, deliberately."""
        assert META["definitions"]["field"]["additionalProperties"] is True

    def test_every_construct_is_now_described(self):
        for key in ("tlv", "flagged", "match", "byte_group"):
            assert key in FIELD_PROPS, key

    def test_until_admits_only_end(self):
        """PS-086: any other value is refused, not treated as a bound."""
        assert FIELD_PROPS["until"] == {
            "const": "end",
            "description": FIELD_PROPS["until"]["description"],
        }

    @pytest.mark.parametrize("key", ["count", "byte_length"])
    def test_a_bound_may_be_a_variable(self, key):
        forms = FIELD_PROPS[key]["oneOf"]
        assert {"type": "integer", "minimum": 0} in forms
        assert any(f.get("type") == "string" and "$" in f.get("pattern", "")
                   for f in forms), forms

    @pytest.mark.parametrize("key", GENERATOR_IGNORES)
    def test_the_keys_the_generator_ignores_say_so(self, key):
        assert "TS013" in FIELD_PROPS[key]["description"], key

    def test_ps083_is_named_where_it_bites(self):
        assert "PS-083" in FIELD_PROPS["count"]["description"]


class TestByteGroupHasTwoSpellings:
    def test_both_are_described(self):
        assert len(BYTE_GROUP["oneOf"]) == 2
        forms = {branch.get("type") for branch in BYTE_GROUP["oneOf"]}
        assert forms == {"object", "array"}

    def test_the_object_form_requires_its_fields_and_closes(self):
        obj = next(b for b in BYTE_GROUP["oneOf"] if b.get("type") == "object")
        assert obj["required"] == ["fields"]
        assert obj["additionalProperties"] is False
        assert set(obj["properties"]) == {"size", "fields"}

    def test_the_object_form_defaults_size_to_one(self):
        obj = next(b for b in BYTE_GROUP["oneOf"] if b.get("type") == "object")
        assert obj["properties"]["size"]["default"] == 1

    def test_the_array_form_takes_size_from_beside_it(self):
        assert "array form" in FIELD_PROPS["size"]["description"]

    def test_neither_form_may_be_empty(self):
        for branch in BYTE_GROUP["oneOf"]:
            if branch.get("type") == "array":
                assert branch["minItems"] == 1
            else:
                assert branch["properties"]["fields"]["minItems"] == 1

    def test_the_consume_prohibition_is_recorded(self):
        assert "PS-017" in BYTE_GROUP["description"]


class TestTheValidatorNowChecksRepeat:
    """Each of these was accepted before this CR."""

    def test_a_working_repeat_passes(self):
        assert validate_schema_structure(schema_with(repeat_field(count=2))) == []

    @pytest.mark.parametrize("bound", [{"count": 2}, {"byte_length": 4},
                                       {"until": "end"}, {"count": "$n"}])
    def test_each_bound_alone_is_accepted(self, bound):
        assert validate_schema_structure(schema_with(repeat_field(**bound))) == []

    def test_no_bound_is_refused(self):
        errors = validate_schema_structure(schema_with(repeat_field()))
        assert any("one of" in e for e in errors), errors

    @pytest.mark.parametrize("bounds", [
        {"count": 2, "byte_length": 4},
        {"count": 2, "until": "end"},
        {"byte_length": 4, "until": "end"},
        {"count": 2, "byte_length": 4, "until": "end"},
    ])
    def test_more_than_one_bound_is_refused(self, bounds):
        errors = validate_schema_structure(schema_with(repeat_field(**bounds)))
        assert any("PS-083" in e for e in errors), errors

    @pytest.mark.parametrize("value", ["banana", "END", "", 1, True])
    def test_until_must_be_end(self, value):
        errors = validate_schema_structure(schema_with(repeat_field(until=value)))
        assert any("until" in e for e in errors), f"{value!r}: {errors}"

    def test_no_members_is_refused(self):
        errors = validate_schema_structure(
            schema_with({"name": "r", "type": "repeat", "count": 2}))
        assert any("fields" in e for e in errors), errors

    @pytest.mark.parametrize("key", ["count", "byte_length"])
    @pytest.mark.parametrize("value", [-1, 1.5, "two", True, None])
    def test_an_unusable_bound_is_refused(self, key, value):
        errors = validate_schema_structure(schema_with(repeat_field(**{key: value})))
        assert any(key in e for e in errors), f"{key}={value!r}: {errors}"

    @pytest.mark.parametrize("key", ["max", "min"])
    def test_a_negative_limit_is_refused(self, key):
        errors = validate_schema_structure(
            schema_with(repeat_field(count=2, **{key: -1})))
        assert any(key in e for e in errors), errors

    def test_a_min_above_max_is_refused(self):
        errors = validate_schema_structure(
            schema_with(repeat_field(count=2, min=5, max=2)))
        assert any("exceeds" in e for e in errors), errors

    def test_a_min_with_no_max_is_accepted(self):
        """`max` defaults differently across implementations, so it is not compared."""
        assert validate_schema_structure(
            schema_with(repeat_field(count=2, min=5))) == []

    def test_a_bad_member_is_reported(self):
        errors = validate_schema_structure(schema_with(
            repeat_field(count=2, fields=[{"name": "a", "type": "s17"}])))
        assert any("s17" in e for e in errors), errors


class TestTheValidatorNowChecksByteGroup:
    def test_both_spellings_pass(self):
        member = {"name": "a", "type": "u8[0:1]"}
        assert validate_schema_structure(
            schema_with({"byte_group": {"size": 1, "fields": [member]}})) == []
        assert validate_schema_structure(
            schema_with({"byte_group": [member], "size": 1})) == []

    @pytest.mark.parametrize("bad", [7, "fields", None, True])
    def test_a_byte_group_that_is_neither_is_refused(self, bad):
        errors = validate_schema_structure(schema_with({"byte_group": bad}))
        assert any("byte_group" in e for e in errors), f"{bad!r}: {errors}"

    def test_an_object_with_no_fields_is_refused(self):
        errors = validate_schema_structure(schema_with({"byte_group": {"size": 1}}))
        assert any("non-empty" in e for e in errors), errors

    def test_an_empty_array_is_refused(self):
        errors = validate_schema_structure(schema_with({"byte_group": []}))
        assert any("empty" in e for e in errors), errors

    @pytest.mark.parametrize("span", [0, -1, 1.5, "1", True])
    def test_an_unusable_size_is_refused(self, span):
        errors = validate_schema_structure(schema_with(
            {"byte_group": {"size": span, "fields": [{"name": "a", "type": "u8[0:1]"}]}}))
        assert any("size" in e for e in errors), f"{span!r}: {errors}"

    def test_the_array_form_size_is_checked_too(self):
        errors = validate_schema_structure(schema_with(
            {"byte_group": [{"name": "a", "type": "u8[0:1]"}], "size": 0}))
        assert any("size" in e for e in errors), errors

    def test_a_member_setting_consume_is_refused(self):
        """PS-017: the construct sets it, and a member advancing defeats the sharing."""
        errors = validate_schema_structure(schema_with({"byte_group": {
            "size": 1,
            "fields": [{"name": "a", "type": "u8[0:1]", "consume": 1}],
        }}))
        assert any("PS-017" in e for e in errors), errors

    def test_consume_zero_is_refused_as_well(self):
        """Harmless in effect, but it is the construct's business to set."""
        errors = validate_schema_structure(schema_with({"byte_group": {
            "size": 1,
            "fields": [{"name": "a", "type": "u8[0:1]", "consume": 0}],
        }}))
        assert any("PS-017" in e for e in errors), errors


class TestTheCorpusConforms:
    def test_every_byte_group_matches_a_described_spelling(self):
        problems = []
        for field, path in constructs("byte_group"):
            bg = field["byte_group"]
            if isinstance(bg, dict):
                if set(bg) - {"size", "fields"}:
                    problems.append(f"{path.name}: {sorted(set(bg) - {'size', 'fields'})}")
                if not bg.get("fields"):
                    problems.append(f"{path.name}: no fields")
            elif isinstance(bg, list):
                if not bg:
                    problems.append(f"{path.name}: empty array form")
            else:
                problems.append(f"{path.name}: {type(bg).__name__}")
        assert not problems, problems

    def test_no_byte_group_member_sets_consume(self):
        offenders = [
            f"{path.name}: {member.get('name')}"
            for field, path in constructs("byte_group")
            for member in (field["byte_group"].get("fields", [])
                           if isinstance(field["byte_group"], dict)
                           else field["byte_group"] if isinstance(field["byte_group"], list)
                           else [])
            if isinstance(member, dict) and "consume" in member
        ]
        assert not offenders, offenders

    def test_every_repeat_declares_exactly_one_bound(self):
        problems = []
        for field, path in repeats():
            bounds = [k for k in ("count", "byte_length", "until") if k in field]
            if len(bounds) != 1:
                problems.append(f"{path.name}: {bounds}")
        assert not problems, problems

    def test_every_repeat_until_is_end(self):
        wrong = [
            f"{path.name}: {field['until']!r}"
            for field, path in repeats()
            if "until" in field and field["until"] != "end"
        ]
        assert not wrong, wrong

    def test_the_corpus_exercises_all_three_bounds(self):
        """Otherwise the sweeps above prove less than they appear to."""
        seen = set()
        for field, _ in repeats():
            seen |= {k for k in ("count", "byte_length", "until") if k in field}
        assert seen == {"count", "byte_length", "until"}, seen

    def test_the_corpus_actually_uses_byte_group(self):
        found = list(constructs("byte_group"))
        assert len(found) > 10, f"only {len(found)} byte_groups found"

    def test_the_corpus_uses_only_the_object_spelling(self):
        """Recorded rather than asserted as a rule: the array form is still described.

        If this ever fails it is not a defect - it means a schema started using the
        array form, and the `size`-beside-it path finally has corpus coverage.
        """
        arrays = [
            path.name for field, path in constructs("byte_group")
            if isinstance(field["byte_group"], list)
        ]
        assert not arrays, (
            "the array form now has corpus coverage, which it did not before: "
            + ", ".join(arrays)
        )


class TestTheGeneratorStillCannotClobberIt:
    def test_it_names_the_new_keys(self):
        import subprocess

        done = subprocess.run(
            [sys.executable, "tools/generate_jsonschema.py",
             "--output", "schemas/payload-schema.json"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert done.returncode != 0
        assert "definitions.field.properties.byte_group" in done.stderr

    def test_all_five_constructs_survived(self):
        current = json.loads((REPO_ROOT / "schemas" / "payload-schema.json").read_text())
        props = current["definitions"]["field"]["properties"]
        assert {"tlv", "flagged", "match", "byte_group"} <= set(props)
        assert {"tlv", "flagged", "flagged_group", "match"} <= set(current["definitions"])
