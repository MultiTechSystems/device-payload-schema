"""Reference implementation of CR-2026-004 (PS-265 .. PS-270).

Three constructs, each found by cross-validating the device schemas against the
vendors' own decoders:

* `name_from` -- an output key built from payload content, because the payload says
  which instance a reading belongs to ("region_3_avg_dwell").
* `lookup` as a mapping -- a device reporting 1=short, 2=long, 3=double has no
  entry for 0, which a zero-based list cannot express without inventing a label.
* negated and wildcard TLV case keys -- vendors dispatch on
  `channel_id === 1 && channel_type !== 0`.

The mapping form of `lookup` was previously accepted and mis-decoded: the guard was
written for a list, so the last entry of any mapping was unreachable and leaked
through as a raw integer. That regression is pinned below.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from schema_interpreter import (  # noqa: E402
    OMITTED,
    SchemaInterpreter,
    apply_lookup,
    reverse_lookup,
)


def decode(schema_yaml, payload_hex, fport=None):
    schema = yaml.safe_load(schema_yaml)
    result = SchemaInterpreter(schema).decode(bytes.fromhex(payload_hex), fPort=fport)
    return result


def decoded(schema_yaml, payload_hex, fport=None):
    result = decode(schema_yaml, payload_hex, fport)
    assert result.success, result.errors
    return {k: v for k, v in result.data.items() if not k.startswith("_")}


SPARSE = """
name: sparse
fields:
  - name: button
    type: u8
    lookup: {1: short, 2: long, 3: double}
"""


class TestSparseLookup:
    """PS-268, PS-269: a mapping's keys need not start at zero or be contiguous."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(1, "short"), (2, "long"), (3, "double")],
    )
    def test_every_entry_is_reachable(self, raw, expected):
        assert decoded(SPARSE, "%02x" % raw) == {"button": expected}

    def test_last_entry_is_reachable(self):
        """The old guard `0 <= value < len(lookup)` dropped exactly this case."""
        assert decoded(SPARSE, "03") == {"button": "double"}

    @pytest.mark.parametrize("raw", [0, 4, 255])
    def test_unmapped_value_omits_the_field(self, raw):
        """A raw integer is not a substitute for a label the device did not send."""
        assert decoded(SPARSE, "%02x" % raw) == {}

    def test_default_is_used_when_present(self):
        schema = """
name: with_default
fields:
  - name: state
    type: u8
    lookup: {1: on, default: unknown}
"""
        assert decoded(schema, "09") == {"state": "unknown"}

    def test_sequence_form_is_unchanged(self):
        schema = 'name: seq\nfields:\n  - {name: relay, type: u8, lookup: ["off", "on"]}\n'
        assert decoded(schema, "01") == {"relay": "on"}
        assert decoded(schema, "00") == {"relay": "off"}

    def test_sequence_out_of_range_is_an_error(self):
        """PS-105, which this CR left untouched while narrowing PS-104.

        This asserted `{"relay": 7}` while PS-105 was unimplemented - the raw index
        reported under a name that promises a label. The CR is unaffected: it says in
        as many words that no existing requirement changes behaviour, and the sequence
        form is unchanged. Only the previously-missing error is new.
        """
        schema = 'name: seq\nfields:\n  - {name: relay, type: u8, lookup: ["off", "on"]}\n'
        result = SchemaInterpreter(yaml.safe_load(schema)).decode(bytes.fromhex("07"))
        assert not result.success
        assert result.errors == [
            "Error decoding relay: lookup index 7 out of bounds for 2 entries"
        ]
        assert "relay" not in result.data


class TestLookupHelpers:
    def test_apply_lookup_mapping(self):
        assert apply_lookup(2, {1: "a", 2: "b"}) == "b"

    def test_apply_lookup_omits_unmapped(self):
        assert apply_lookup(9, {1: "a"}) is OMITTED

    def test_apply_lookup_leaves_non_integers(self):
        assert apply_lookup("already a label", {1: "a"}) == "already a label"

    def test_booleans_are_not_treated_as_indices(self):
        assert apply_lookup(True, ["off", "on"]) is True

    def test_reverse_lookup_mapping(self):
        assert reverse_lookup("double", {1: "short", 2: "long", 3: "double"}) == 3

    def test_reverse_lookup_sequence(self):
        assert reverse_lookup("on", ["off", "on"]) == 1

    def test_reverse_lookup_unknown_label_passes_through(self):
        assert reverse_lookup("missing", {1: "short"}) == "missing"


NAMED = """
name: computed
endian: little
fields:
  - name: region_id
    type: u8
  - name: avg_dwell
    name_from: "region_${region_id}_avg_dwell"
    type: u16
"""


class TestComputedFieldNames:
    """PS-265, PS-266, PS-267."""

    def test_key_is_built_from_the_payload(self):
        assert decoded(NAMED, "03 1000".replace(" ", "")) == {
            "region_id": 3,
            "region_3_avg_dwell": 16,
        }

    def test_same_schema_different_instance(self):
        assert "region_7_avg_dwell" in decoded(NAMED, "070100")

    def test_unresolved_reference_is_an_error(self):
        schema = 'name: bad\nfields:\n  - {name: v, type: u8, name_from: "x_${nope}"}\n'
        result = decode(schema, "01")
        assert not result.success
        assert "not been decoded" in result.errors[0]

    def test_schema_name_still_resolves_references(self):
        """PS-267: `name` remains the identity used by $ references."""
        schema = """
name: refs
endian: little
fields:
  - name: idx
    type: u8
  - name: reading
    name_from: "channel_${idx}_reading"
    type: u8
  - name: doubled
    type: number
    ref: $reading
    mult: 2
"""
        out = decoded(schema, "020a")
        assert out["channel_2_reading"] == 10
        assert out["doubled"] == 20


TAGGED = """
name: tags
endian: little
fields:
  - tlv:
      tag_fields: [{name: channel_id, type: u8}, {name: channel_type, type: u8}]
      tag_key: [channel_id, channel_type]
      cases:
        "[1, 200]":
          - {name: exact, type: u8}
        "[1, !0]":
          - {name: any_but_zero, type: u8, lookup: ["off", "on"]}
        "[2, *]":
          - {name: any_type, type: u8}
"""


class TestNegatedAndWildcardTags:
    """PS-270."""

    def test_negated_key_matches_other_values(self):
        assert decoded(TAGGED, "010501", fport=85) == {"any_but_zero": "on"}

    def test_negated_key_excludes_its_value(self):
        assert decoded(TAGGED, "010009", fport=85) == {}

    def test_exact_key_wins_over_negated(self):
        assert decoded(TAGGED, "01c807", fport=85) == {"exact": 7}

    def test_wildcard_matches_any_type(self):
        assert decoded(TAGGED, "02630a", fport=85) == {"any_type": 10}
        assert decoded(TAGGED, "02000b", fport=85) == {"any_type": 11}

    def test_exact_key_wins_over_wildcard(self):
        schema = """
name: precedence
endian: little
fields:
  - tlv:
      tag_fields: [{name: channel_id, type: u8}, {name: channel_type, type: u8}]
      tag_key: [channel_id, channel_type]
      cases:
        "[2, *]":
          - {name: fallback, type: u8}
        "[2, 99]":
          - {name: specific, type: u8}
"""
        # The wildcard is written first, so ordering must not decide the match.
        assert decoded(schema, "02630a", fport=85) == {"specific": 10}
        assert decoded(schema, "02010b", fport=85) == {"fallback": 11}

    def test_negated_key_wins_over_wildcard(self):
        schema = """
name: precedence2
endian: little
fields:
  - tlv:
      tag_fields: [{name: channel_id, type: u8}, {name: channel_type, type: u8}]
      tag_key: [channel_id, channel_type]
      cases:
        "[3, *]":
          - {name: fallback, type: u8}
        "[3, !0]":
          - {name: not_zero, type: u8}
"""
        assert decoded(schema, "030505", fport=85) == {"not_zero": 5}
        assert decoded(schema, "030006", fport=85) == {"fallback": 6}
