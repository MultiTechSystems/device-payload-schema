"""PS-305 to PS-308: a test vector can assert the warnings a decode reports.

A vector could assert decoded values and encoded bytes, and nothing a decoder *said*. So
PS-301 and PS-302 - CR-2026-013's requirement that an unknown TLV tag be reported - were
implemented in five places and checked by no vector: the seven corpus vectors that produce
such a warning all passed exactly as they had before the requirement existed.

`expected_warnings` closes that. Three decisions are worth pinning, because they differ
from how `expected` works, and the tests below are grouped by them:

- absent asserts nothing, which is every vector written before the key existed;
- present, it is the complete list in order, so an unexpected warning fails too - that is
  what makes `expected_warnings: []` mean something;
- entries match as substrings, because the specification fixes what a warning must contain
  and not its wording.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from validate_schema import (  # noqa: E402
    validate_schema_structure,
    warnings_match,
)

CORPUS = REPO_ROOT / "schemas" / "devices"


class TestAbsenceAssertsNothing:
    """PS-308. Every vector in the corpus predates the key."""

    def test_no_key_passes_whatever_was_reported(self):
        assert warnings_match(None, ["anything at all", "and another"]) == (True, "")

    def test_no_key_passes_a_clean_decode(self):
        assert warnings_match(None, []) == (True, "")


class TestTheListIsComplete:
    """PS-305. Not a subset, which is the one way `expected_warnings` differs most from
    `expected` - and the reason the empty list means anything."""

    def test_the_empty_list_requires_a_clean_execution(self):
        assert warnings_match([], []) == (True, "")

    def test_the_empty_list_fails_an_unexpected_warning(self):
        ok, detail = warnings_match([], ["unknown TLV tag (0x09) skipped, 2 byte(s) discarded"])
        assert not ok
        assert "expected 0 warning(s), got 1" in detail

    def test_a_missing_warning_fails(self):
        ok, detail = warnings_match(["0x09"], [])
        assert not ok
        assert "expected 1 warning(s), got 0" in detail

    def test_an_extra_warning_fails(self):
        # The regression this exists to catch: a schema edit that starts discarding a tag
        # the schema used to describe, where every field in `expected` is still produced.
        ok, detail = warnings_match(["0x09"], ["about 0x09", "and something new"])
        assert not ok
        assert "expected 1 warning(s), got 2" in detail

    def test_order_is_positional(self):
        # Warnings are reported in the order the bytes appear, so two on one payload are
        # ordered by construction. Matching positionally keeps the rule simple to state.
        assert warnings_match(["first", "second"], ["first one", "second one"])[0]
        assert not warnings_match(["second", "first"], ["first one", "second one"])[0]


class TestEntriesAreSubstrings:
    """PS-306. The specification requires a warning to name the tag and state the byte
    count; it does not fix the sentence, so a vector must not depend on it."""

    def test_a_fragment_matches_anywhere_in_the_warning(self):
        reported = "unknown TLV tag (0x05, 0x6A) at offset 10: 4 of 14 byte(s) left undecoded"
        assert warnings_match(["0x05, 0x6A"], [reported]) == (True, "")

    def test_an_entry_may_be_several_fragments_of_one_warning(self):
        # The tag and the byte count are not contiguous in any implementation's text,
        # which is why an entry may be a list rather than only a string.
        reported = "unknown TLV tag (0x05, 0x6A) at offset 10: 4 of 14 byte(s) left undecoded"
        assert warnings_match([["0x05, 0x6A", "4 of 14 byte(s)"]], [reported]) == (True, "")

    def test_every_fragment_of_an_entry_must_appear(self):
        reported = "unknown TLV tag (0x05, 0x6A) at offset 10: 4 of 14 byte(s) left undecoded"
        ok, detail = warnings_match([["0x05, 0x6A", "9 of 99 byte(s)"]], [reported])
        assert not ok
        assert "'9 of 99 byte(s)' not found" in detail

    def test_a_fragment_list_still_counts_as_one_warning(self):
        ok, detail = warnings_match([["a", "b", "c"]], ["a b c", "second"])
        assert not ok
        assert "expected 1 warning(s), got 2" in detail

    def test_the_report_names_the_fragment_and_what_was_said(self):
        ok, detail = warnings_match(["expected text"], ["actual text"])
        assert not ok
        assert "'expected text' not found in 'actual text'" in detail


class TestTheKeyIsValidated:
    """A malformed key is a schema error, not a silently ignored one."""

    def base_schema(self, vector_extra):
        vector = {"name": "v", "payload": "003C", "expected": {"reading": 60}}
        vector.update(vector_extra)
        return {
            "name": "t", "endian": "big",
            "fields": [{"name": "reading", "type": "u16"}],
            "test_vectors": [vector],
        }

    def test_a_list_of_strings_is_accepted(self):
        errors = validate_schema_structure(self.base_schema({"expected_warnings": ["a"]}))
        assert not [e for e in errors if "expected_warnings" in e]

    def test_a_list_of_fragment_lists_is_accepted(self):
        errors = validate_schema_structure(self.base_schema({"expected_warnings": [["a", "b"]]}))
        assert not [e for e in errors if "expected_warnings" in e]

    def test_the_empty_list_is_accepted(self):
        errors = validate_schema_structure(self.base_schema({"expected_warnings": []}))
        assert not [e for e in errors if "expected_warnings" in e]

    def test_a_bare_string_is_rejected(self):
        # The likely slip, and one that would otherwise be read as a list of characters.
        errors = validate_schema_structure(self.base_schema({"expected_warnings": "a warning"}))
        assert [e for e in errors if "expected_warnings" in e]

    def test_a_number_entry_is_rejected(self):
        errors = validate_schema_structure(self.base_schema({"expected_warnings": [4]}))
        assert [e for e in errors if "expected_warnings" in e]

    def test_an_empty_fragment_list_is_rejected(self):
        # An entry that requires nothing would assert that a warning exists and say
        # nothing about it, which no vector should want to write by accident.
        errors = validate_schema_structure(self.base_schema({"expected_warnings": [[]]}))
        assert [e for e in errors if "expected_warnings" in e]


class TestTheCorpusUsesIt:
    """What the key was added for: the seven vectors CR-2026-013 measured, and the three
    probes for the modes no device schema sets."""

    def vectors_with_the_key(self):
        found = []
        for path in sorted(CORPUS.rglob("*.yaml")):
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for vector in document.get("test_vectors") or []:
                if "expected_warnings" in vector:
                    found.append((path, vector))
        return found

    def test_the_seven_vendor_vectors_carry_it(self):
        stopping_short = [
            (path, vector) for path, vector in self.vectors_with_the_key()
            if any("byte(s)" in str(entry) for entry in vector["expected_warnings"])
        ]
        assert len(stopping_short) >= 7

    def test_every_declared_expectation_names_a_tag(self):
        # A fragment weak enough to match any warning would assert nothing. Every entry
        # in the corpus names the tag, which is what PS-301 requires a warning to carry.
        for path, vector in self.vectors_with_the_key():
            for entry in vector["expected_warnings"]:
                fragments = entry if isinstance(entry, list) else [entry]
                assert any("0x" in fragment for fragment in fragments), (
                    "%s/%s asserts nothing identifying" % (path.name, vector.get("name"))
                )

    def test_a_probe_asserts_a_clean_decode(self):
        # `expected_warnings: []` is the form that catches an edit which begins
        # discarding data, and the corpus should contain at least one.
        assert any(vector["expected_warnings"] == []
                   for _, vector in self.vectors_with_the_key())

    def test_the_raw_mode_probe_reads_the_captured_entry(self):
        path = CORPUS / "_language-conformance" / "unknown-tlv-tag-raw.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        vector = document["test_vectors"][0]
        assert vector["expected"]["unknown_tags"] == [{"tag": [9], "raw": "0bb8"}]
