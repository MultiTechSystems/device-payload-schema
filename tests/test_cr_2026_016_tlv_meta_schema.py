"""CR-2026-016: `tlv` described in the meta-schema.

`definitions.field` in payload-schema.json takes `additionalProperties` and described no
construct, so `tlv` - seven keys read by five implementations - was invisible to it. That
is why CR-2026-015 had to put the `unknown` enum in tools/validate_schema.py instead: the
meta-schema had nowhere to hang it. It hangs here now, and the two must agree, which is
what this file mostly checks.

`definitions.field` stays permissive. Closing it is a much larger change (AGENTS.md
records it as a known gap: an unknown wire type such as `s17` is accepted, as is a field
with no `name`), and nothing here depends on it. A `tlv` is closable because its
vocabulary is fixed - a misspelt `tag_sze` is a mistake, not an extension.

`jsonschema` is not a dependency of this repo, so the conformance check below is
hand-rolled. Only two things need checking - the key set and the value shapes - and each
is asserted explicitly.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

META = json.loads((REPO_ROOT / "schemas" / "payload-schema.json").read_text())
CORPUS = REPO_ROOT / "schemas" / "devices"
EXAMPLES = REPO_ROOT / "examples"

TLV = META["definitions"]["tlv"]

#: Every key the five implementations read out of a `tlv`, gathered by reading each one:
#: tools/schema_interpreter.py `_decode_tlv`, go/schema/schema.go `Field`,
#: bindings/java/.../Schema.java, dotnet/PayloadSchema/SchemaParser.cs. The TS013
#: generator reads five of the seven - it derives the key from `tag_fields` and always
#: merges - which is a narrower set, not a different one.
IMPLEMENTED_KEYS = {
    "tag_size", "tag_fields", "tag_key", "length_size", "merge", "unknown", "cases",
}


def every_tlv():
    """Each `tlv` mapping in the corpus and the examples, with the file it came from."""
    def walk(node, path):
        if isinstance(node, dict):
            tlv = node.get("tlv")
            if isinstance(tlv, dict):
                yield tlv, path
            for value in node.values():
                yield from walk(value, path)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item, path)

    roots = sorted(CORPUS.rglob("*.yaml")) + sorted(EXAMPLES.rglob("*.yaml"))
    for path in roots:
        try:
            schema = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        if isinstance(schema, dict):
            yield from walk(schema, path)


class TestTheDescriptionExists:
    def test_field_points_at_it(self):
        assert META["definitions"]["field"]["properties"]["tlv"] == {
            "$ref": "#/definitions/tlv"
        }

    def test_it_closes(self):
        assert TLV["additionalProperties"] is False

    def test_field_itself_stays_permissive(self):
        """Deliberate: closing `definitions.field` is not this CR."""
        assert META["definitions"]["field"]["additionalProperties"] is True

    def test_it_describes_every_key_an_implementation_reads(self):
        assert set(TLV["properties"]) == IMPLEMENTED_KEYS

    def test_every_key_carries_a_description(self):
        undocumented = [k for k, v in TLV["properties"].items() if not v.get("description")]
        assert not undocumented


class TestTheUnknownEnumAgreesWithTheValidator:
    """The meta-schema and validate_schema.py must not drift apart."""

    def test_the_same_three_modes(self):
        from validate_schema import validate_schema_structure

        described = TLV["properties"]["unknown"]["enum"]
        accepted = [
            mode for mode in described + ["raws", "", "Skip"]
            if validate_schema_structure({
                "name": "p",
                "fields": [{"tlv": {"tag_size": 1, "unknown": mode,
                                    "cases": {1: [{"name": "k", "type": "u8"}]}}}],
            }) == []
        ]
        assert accepted == described

    def test_skip_is_the_documented_default(self):
        assert TLV["properties"]["unknown"]["default"] == "skip"

    def test_merge_defaults_to_true(self):
        assert TLV["properties"]["merge"]["default"] is True


class TestTheCorpusConforms:
    """Every `tlv` in the corpus satisfies the description, or the description is wrong."""

    def test_no_tlv_carries_an_undescribed_key(self):
        described = set(TLV["properties"])
        offenders = []
        for tlv, path in every_tlv():
            for key in tlv:
                if key not in described:
                    offenders.append(f"{path.name}: {key}")
        assert not offenders, (
            "these keys are used and not described, so `additionalProperties: false` "
            "would reject a valid schema: " + ", ".join(sorted(set(offenders)))
        )

    def test_the_scalar_shapes_hold(self):
        problems = []
        for tlv, path in every_tlv():
            for key, bound in (("tag_size", 4), ("length_size", 4)):
                if key in tlv:
                    value = tlv[key]
                    low = TLV["properties"][key]["minimum"]
                    if not isinstance(value, int) or not low <= value <= bound:
                        problems.append(f"{path.name}: {key}={value!r}")
            if "merge" in tlv and not isinstance(tlv["merge"], bool):
                problems.append(f"{path.name}: merge={tlv['merge']!r}")
            if "unknown" in tlv and tlv["unknown"] not in TLV["properties"]["unknown"]["enum"]:
                problems.append(f"{path.name}: unknown={tlv['unknown']!r}")
        assert not problems, problems

    def test_tag_key_is_a_list_of_names(self):
        problems = []
        for tlv, path in every_tlv():
            if "tag_key" not in tlv:
                continue
            value = tlv["tag_key"]
            if not (isinstance(value, list) and value
                    and all(isinstance(part, str) for part in value)):
                problems.append(f"{path.name}: {value!r}")
        assert not problems, problems

    def test_tag_fields_entries_are_fields(self):
        problems = []
        for tlv, path in every_tlv():
            for entry in tlv.get("tag_fields") or []:
                if not (isinstance(entry, dict) and "name" in entry):
                    problems.append(f"{path.name}: {entry!r}")
        assert not problems, problems

    def test_every_case_maps_to_a_field_list(self):
        problems = []
        for tlv, path in every_tlv():
            cases = tlv.get("cases")
            if not isinstance(cases, dict):
                problems.append(f"{path.name}: cases is {type(cases).__name__}")
                continue
            for key, value in cases.items():
                if not isinstance(value, list):
                    problems.append(f"{path.name}: case {key!r} -> "
                                    f"{type(value).__name__}")
        assert not problems, problems

    def test_the_corpus_uses_both_tag_forms(self):
        """Guards the test above from passing because it saw only one shape."""
        forms = set()
        for tlv, _ in every_tlv():
            if "tag_size" in tlv:
                forms.add("tag_size")
            if "tag_fields" in tlv:
                forms.add("tag_fields")
        assert forms == {"tag_size", "tag_fields"}

    def test_the_corpus_uses_both_case_key_forms(self):
        kinds = set()
        for tlv, _ in every_tlv():
            for key in (tlv.get("cases") or {}):
                kinds.add("int" if isinstance(key, int) else "composite")
        assert kinds == {"int", "composite"}


class TestTheGeneratorCannotClobberIt:
    """`generate_jsonschema.py` has not kept up with the file it claims to produce."""

    def test_it_refuses_and_names_what_would_be_lost(self):
        done = subprocess.run(
            [sys.executable, "tools/generate_jsonschema.py",
             "--output", "schemas/payload-schema.json"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert done.returncode != 0, "the generator overwrote a hand-maintained file"
        assert "definitions.tlv" in done.stderr
        assert "expected_warnings" in done.stderr

    def test_the_file_on_disk_survived(self):
        current = json.loads((REPO_ROOT / "schemas" / "payload-schema.json").read_text())
        assert "tlv" in current["definitions"]

    def test_stdout_still_works(self):
        """Refusing to overwrite is not refusing to run."""
        done = subprocess.run(
            [sys.executable, "tools/generate_jsonschema.py"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert done.returncode == 0
        assert json.loads(done.stdout)["definitions"]["field"]
