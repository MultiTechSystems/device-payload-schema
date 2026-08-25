"""CR-2026-015: describe `_warnings`, and refuse an `unknown` mode nobody implements.

Two loose ends from CR-2026-013 and CR-2026-014.

`_warnings` became part of the decoder's contract in five implementations and was
described nowhere - it survived on `additionalProperties: true`, which is exactly the
state PS-182 rejected for `_quality`: a key a decoder can report and no schema describes
is a key nobody reading the schema can learn about.

The `unknown` parameter had no validation anywhere. All five implementations fall back to
`skip` on a value they do not recognise, so `unknown: raws` silently selected the mode
that abandons the rest of the payload - the failure CR-2026-013 exists to make visible,
reintroduced by a typo. `definitions.field` in payload-schema.json does not describe
`tlv` at all and takes `additionalProperties`, so the validator is the only place it can
be caught.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import generate_output_schema  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

CORPUS = REPO_ROOT / "schemas" / "devices"


def warnings_of(schema):
    return generate_output_schema(schema).get("properties", {}).get("_warnings")


#: Distinguishes "no `unknown` key" from "`unknown:` with nothing after it", which YAML
#: reads as None and which the interpreters degrade to `skip` like any other value they
#: do not recognise.
ABSENT = object()


def tlv_schema(unknown=ABSENT):
    tlv = {"tag_size": 1, "cases": {1: [{"name": "known", "type": "u16"}]}}
    if unknown is not ABSENT:
        tlv["unknown"] = unknown
    return {"name": "probe", "endian": "big", "fields": [{"tlv": tlv}]}


def range_schema():
    return {
        "name": "probe",
        "endian": "big",
        "fields": [{"name": "t", "type": "u8", "valid_range": [0, 10]}],
    }


class TestWarningsIsDescribed:
    """The key is declared where a decode can produce one."""

    def test_a_valid_range_declares_it(self):
        # The same condition that gates `_quality`: a value can fall outside the range.
        assert warnings_of(range_schema()) is not None

    def test_a_tlv_declares_it(self):
        # An undescribed tag warns under every mode but `error`, and `skip` is default.
        assert warnings_of(tlv_schema()) is not None

    @pytest.mark.parametrize("mode", ["skip", "raw"])
    def test_the_reporting_modes_declare_it(self, mode):
        assert warnings_of(tlv_schema(mode)) is not None

    def test_error_mode_alone_does_not(self):
        # It fails the decode rather than reporting, so there is no warning to describe.
        assert warnings_of(tlv_schema("error")) is None

    def test_a_schema_that_cannot_warn_does_not_declare_it(self):
        plain = {"name": "p", "fields": [{"name": "t", "type": "u8"}]}
        assert warnings_of(plain) is None

    def test_it_is_an_array_of_strings(self):
        declared = warnings_of(tlv_schema())
        assert declared["type"] == "array"
        assert declared["items"] == {"type": "string"}

    def test_what_a_decode_reports_satisfies_the_declaration(self):
        """The declaration has to admit the real thing, not merely exist."""
        schema = tlv_schema()
        declared = warnings_of(schema)
        result = SchemaInterpreter(schema).decode(bytes.fromhex("01003C090BB8"))
        assert result.warnings, "expected the unknown tag to be reported"
        assert declared["type"] == "array"
        assert all(isinstance(w, str) for w in result.warnings)


class TestTheCorpusStillDescribesWhatItReports:
    """No corpus schema reports a warning its output schema does not admit."""

    def test_every_schema_that_warns_declares_the_key(self):
        undeclared = []
        for path in sorted(CORPUS.rglob("*.yaml")):
            schema = yaml.safe_load(path.read_text())
            if not isinstance(schema, dict) or "test_vectors" not in schema:
                continue
            declared = warnings_of(schema) is not None
            for vector in schema["test_vectors"]:
                if vector.get("expected_warnings"):
                    if not declared:
                        undeclared.append(f"{path.name}: {vector.get('name')}")
                    break
        assert not undeclared, (
            "these schemas report a warning their output schema does not describe: "
            + ", ".join(undeclared)
        )


class TestTheUnknownModeIsValidated:
    """A mode no implementation honours is refused rather than degraded to `skip`."""

    @pytest.mark.parametrize("mode", ["skip", "raw", "error"])
    def test_the_three_modes_are_accepted(self, mode):
        assert validate_schema_structure(tlv_schema(mode)) == []

    def test_absent_is_accepted(self):
        assert validate_schema_structure(tlv_schema()) == []

    @pytest.mark.parametrize("mode", ["raws", "Skip", "ignore", "", 1, True])
    def test_anything_else_is_refused(self, mode):
        errors = validate_schema_structure(tlv_schema(mode))
        assert errors, f"{mode!r} should not have been accepted"
        assert any("unknown" in e for e in errors), errors

    def test_an_empty_value_is_refused(self):
        """`unknown:` with nothing after it, which YAML reads as None.

        Worth its own case because it is the one bad value that does not look like a
        typo, and it degrades the same way: not one of the three, so `skip`.
        """
        errors = validate_schema_structure(tlv_schema(None))
        assert any("unknown" in e for e in errors), errors

    def test_the_report_names_the_modes_and_the_value(self):
        (error,) = [
            e for e in validate_schema_structure(tlv_schema("raws"))
            if "unknown" in e
        ]
        assert "skip/raw/error" in error
        assert "'raws'" in error

    def test_the_typo_would_otherwise_have_been_silent(self):
        """Why this check exists: the interpreter degrades rather than complaining.

        A misspelling selects `skip`, which for a tag-only tlv abandons the remainder of
        the payload - so without the validator the schema decodes, loses bytes, and says
        only what a correctly spelled `skip` would have said.
        """
        payload = bytes.fromhex("01003C090BB8")
        typo = SchemaInterpreter(tlv_schema("raws")).decode(payload)
        spelled = SchemaInterpreter(tlv_schema("skip")).decode(payload)
        assert typo.data == spelled.data
        assert typo.warnings == spelled.warnings
        assert validate_schema_structure(tlv_schema("raws")) != []


class TestTheCorpusModesAreSpelled:
    """Every `unknown` in the corpus is one of the three."""

    def test_no_corpus_schema_carries_a_bad_mode(self):
        bad = []
        for path in sorted(CORPUS.rglob("*.yaml")):
            schema = yaml.safe_load(path.read_text())
            if not isinstance(schema, dict):
                continue
            for error in validate_schema_structure(schema):
                if "unknown' must be" in error:
                    bad.append(f"{path.name}: {error}")
        assert not bad, bad
