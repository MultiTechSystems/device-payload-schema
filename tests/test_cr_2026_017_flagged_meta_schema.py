"""CR-2026-017: `flagged` described in the meta-schema.

The second construct out of `definitions.field`'s `additionalProperties`, after `tlv` in
CR-2026-016 and following the same shape: describe what the five implementations read,
close the object, and pin it against the corpus so `additionalProperties: false` cannot
start rejecting a valid schema.

`flagged` is the easier of the two. Its vocabulary is four keys, all five
implementations agree on them, and `tools/validate_schema.py` already enforced every one
- unlike `tlv`, whose `unknown` parameter had no validation anywhere until CR-2026-015.
So this describes a contract that was already being kept, which is why there is no
behaviour change to make: the value is that a schema author reading the meta-schema, or
an editor completing against it, can now see the construct at all.

One rule the description cannot carry: two groups claiming the same bit. JSON Schema
cannot express uniqueness of a member across array items, and the validator refuses it.
Asserted below so the division of labour is recorded rather than assumed.
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

FLAGGED = META["definitions"]["flagged"]
GROUP = META["definitions"]["flagged_group"]

#: Read out of each implementation in turn: tools/schema_interpreter.py
#: `_decode_flagged`, go/schema/schema.go `FlaggedDef`/`FlaggedGroup`,
#: bindings/java/.../Schema.java, dotnet/PayloadSchema/SchemaParser.cs, and
#: tools/generate_ts013_codec.py - which subscripts `fg['field']` and `group['bit']`
#: rather than defaulting them, and so is the strictest reader of the five.
IMPLEMENTED = {"field", "groups"}
IMPLEMENTED_GROUP = {"bit", "fields"}


def every_flagged():
    """Each `flagged` mapping in the corpus and the examples, with its file."""
    def walk(node, path):
        if isinstance(node, dict):
            flagged = node.get("flagged")
            if isinstance(flagged, dict):
                yield flagged, path
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


def flagged_schema(flagged):
    """A minimal schema whose one construct is the `flagged` given."""
    return {
        "name": "probe",
        "endian": "big",
        "fields": [{"name": "flags", "type": "u8"}, {"flagged": flagged}],
    }


def working_flagged(**overrides):
    flagged = {
        "field": "flags",
        "groups": [{"bit": 0, "fields": [{"name": "a", "type": "u8"}]}],
    }
    flagged.update(overrides)
    return flagged


class TestTheDescriptionExists:
    def test_field_points_at_it(self):
        assert META["definitions"]["field"]["properties"]["flagged"] == {
            "$ref": "#/definitions/flagged"
        }

    def test_groups_point_at_the_group_definition(self):
        assert FLAGGED["properties"]["groups"]["items"] == {
            "$ref": "#/definitions/flagged_group"
        }

    def test_both_objects_close(self):
        assert FLAGGED["additionalProperties"] is False
        assert GROUP["additionalProperties"] is False

    def test_field_itself_stays_permissive(self):
        """As in CR-2026-016: closing `definitions.field` is not this CR."""
        assert META["definitions"]["field"]["additionalProperties"] is True

    def test_it_describes_every_key_an_implementation_reads(self):
        assert set(FLAGGED["properties"]) == IMPLEMENTED
        assert set(GROUP["properties"]) == IMPLEMENTED_GROUP

    def test_the_keys_the_generator_subscripts_are_required(self):
        assert set(FLAGGED["required"]) == IMPLEMENTED
        assert set(GROUP["required"]) == IMPLEMENTED_GROUP

    def test_every_key_carries_a_description(self):
        for obj in (FLAGGED, GROUP):
            bare = [k for k, v in obj["properties"].items() if not v.get("description")]
            assert not bare, bare

    def test_tlv_is_still_described(self):
        """CR-2026-016 is not disturbed by this one."""
        assert META["definitions"]["field"]["properties"]["tlv"] == {
            "$ref": "#/definitions/tlv"
        }


class TestTheDescriptionAgreesWithTheValidator:
    """Where both speak, they must say the same thing."""

    def test_a_working_construct_satisfies_both(self):
        assert validate_schema_structure(flagged_schema(working_flagged())) == []

    @pytest.mark.parametrize("missing", ["field", "groups"])
    def test_the_required_keys_are_required_by_the_validator_too(self, missing):
        flagged = working_flagged()
        del flagged[missing]
        errors = validate_schema_structure(flagged_schema(flagged))
        assert any(missing in e for e in errors), errors

    @pytest.mark.parametrize("missing", ["bit", "fields"])
    def test_the_required_group_keys_likewise(self, missing):
        group = {"bit": 0, "fields": [{"name": "a", "type": "u8"}]}
        del group[missing]
        errors = validate_schema_structure(flagged_schema(working_flagged(groups=[group])))
        assert any(missing in e for e in errors), errors

    def test_a_negative_bit_is_refused(self):
        below = GROUP["properties"]["bit"]["minimum"] - 1
        errors = validate_schema_structure(
            flagged_schema(working_flagged(
                groups=[{"bit": below, "fields": [{"name": "a", "type": "u8"}]}]))
        )
        assert any("bit" in e for e in errors), errors

    def test_an_empty_group_field_list_is_refused(self):
        assert GROUP["properties"]["fields"]["minItems"] == 1
        errors = validate_schema_structure(
            flagged_schema(working_flagged(groups=[{"bit": 0, "fields": []}]))
        )
        assert any("fields" in e for e in errors), errors

    def test_a_duplicate_bit_is_refused_only_by_the_validator(self):
        """The one rule JSON Schema cannot carry, so the description says who does."""
        duplicated = [
            {"bit": 1, "fields": [{"name": "a", "type": "u8"}]},
            {"bit": 1, "fields": [{"name": "b", "type": "u8"}]},
        ]
        errors = validate_schema_structure(
            flagged_schema(working_flagged(groups=duplicated))
        )
        assert any("duplicate bit" in e for e in errors), errors
        assert "uniqueItems" not in json.dumps(FLAGGED["properties"]["groups"])
        assert "validate_schema.py" in GROUP["properties"]["bit"]["description"]

    def test_the_flags_field_must_come_first(self):
        """PS-159, and the description says a bare name rather than a $-reference."""
        errors = validate_schema_structure({
            "name": "probe",
            "fields": [{"flagged": working_flagged()}, {"name": "flags", "type": "u8"}],
        })
        assert any("flagged" in e for e in errors), errors
        assert "$flags" in FLAGGED["properties"]["field"]["description"]


class TestTheCorpusConforms:
    def test_no_flagged_carries_an_undescribed_key(self):
        offenders = []
        for flagged, path in every_flagged():
            for key in flagged:
                if key not in FLAGGED["properties"]:
                    offenders.append(f"{path.name}: {key}")
            for group in flagged.get("groups") or []:
                if not isinstance(group, dict):
                    continue
                for key in group:
                    if key not in GROUP["properties"]:
                        offenders.append(f"{path.name}: groups[].{key}")
        assert not offenders, (
            "used and not described, so `additionalProperties: false` would reject a "
            "valid schema: " + ", ".join(sorted(set(offenders)))
        )

    def test_every_required_key_is_present_throughout(self):
        problems = []
        for flagged, path in every_flagged():
            for key in FLAGGED["required"]:
                if key not in flagged:
                    problems.append(f"{path.name}: flagged.{key}")
            for group in flagged.get("groups") or []:
                for key in GROUP["required"]:
                    if isinstance(group, dict) and key not in group:
                        problems.append(f"{path.name}: groups[].{key}")
        assert not problems, problems

    def test_the_shapes_hold(self):
        problems = []
        low = GROUP["properties"]["bit"]["minimum"]
        high = GROUP["properties"]["bit"]["maximum"]
        for flagged, path in every_flagged():
            if not isinstance(flagged.get("field"), str):
                problems.append(f"{path.name}: field={flagged.get('field')!r}")
            if not isinstance(flagged.get("groups"), list):
                problems.append(f"{path.name}: groups is not a list")
                continue
            for group in flagged["groups"]:
                bit = group.get("bit")
                if not isinstance(bit, int) or isinstance(bit, bool) or not low <= bit <= high:
                    problems.append(f"{path.name}: bit={bit!r}")
                if not isinstance(group.get("fields"), list) or not group["fields"]:
                    problems.append(f"{path.name}: bit {bit} has no fields")
        assert not problems, problems

    def test_the_flags_field_is_never_dollar_prefixed(self):
        """The description claims a bare name; the corpus is the evidence."""
        prefixed = [
            f"{path.name}: {flagged['field']}"
            for flagged, path in every_flagged()
            if isinstance(flagged.get("field"), str) and flagged["field"].startswith("$")
        ]
        assert not prefixed, prefixed

    def test_the_corpus_actually_exercises_the_construct(self):
        """Guards every sweep above from passing on an empty set."""
        found = list(every_flagged())
        assert len(found) > 20, f"only {len(found)} flagged constructs found"
        bits = {g.get("bit") for f, _ in found for g in f.get("groups") or []}
        assert len(bits) > 1, "every group claims the same bit; shapes untested"


class TestTheGeneratorStillCannotClobberIt:
    """CR-2026-016's guard has to cover the new keys too."""

    def test_it_names_the_flagged_definitions(self):
        import subprocess

        done = subprocess.run(
            [sys.executable, "tools/generate_jsonschema.py",
             "--output", "schemas/payload-schema.json"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert done.returncode != 0
        assert "definitions.flagged" in done.stderr
        assert "definitions.flagged_group" in done.stderr

    def test_the_file_on_disk_survived(self):
        current = json.loads((REPO_ROOT / "schemas" / "payload-schema.json").read_text())
        assert {"tlv", "flagged", "flagged_group"} <= set(current["definitions"])
