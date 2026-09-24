"""A field-level `endian:` overrides the schema's, for that field's own read.

Go, Java, C# and the C interpreter all honoured this key. `tools/schema_interpreter.py`
parsed nothing: every read site consults the schema-level ``self.endian``, so a field
declaring `endian: little` under a big-endian schema decoded big-endian and reported
success. The reference implementation was the only one of the five that got it wrong,
and it is the surface `td-tools` imports by file path.

`_language-conformance/field-endian.yaml` holds all five to the scalar case. What that
fixture cannot assert is here: the boundary the override does NOT cross, the types it
does not apply to, and the values it refuses.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema  # noqa: E402


def decode(src, payload):
    return SchemaInterpreter(yaml.safe_load(src)).decode(bytes.fromhex(payload))


class TestTheOverrideApplies:
    def test_a_little_endian_field_under_a_big_endian_schema(self):
        result = decode(
            "name: p\nendian: big\nfields:\n"
            "- {name: v, type: u16, endian: little}\n"
            "- {name: w, type: u16}\n",
            "01020102",
        )
        assert result.errors == []
        # 0x0201 for the override, 0x0102 for the field that takes the schema's order.
        assert result.data == {"v": 513, "w": 258}

    def test_a_big_endian_field_under_a_little_endian_schema(self):
        result = decode(
            "name: p\nendian: little\nfields:\n"
            "- {name: v, type: u16, endian: big}\n"
            "- {name: w, type: u16}\n",
            "01020102",
        )
        assert result.data == {"v": 258, "w": 513}

    def test_the_override_survives_a_round_trip(self):
        """Honouring it on the way in and not on the way out would corrupt the payload."""
        src = (
            "name: p\nendian: big\nfields:\n"
            "- {name: v, type: u16, endian: little}\n"
            "- {name: w, type: u16}\n"
        )
        interp = SchemaInterpreter(yaml.safe_load(src))
        decoded = interp.decode(bytes.fromhex("01020102"))
        assert interp.encode(decoded.data).payload.hex() == "01020102"


class TestTheBoundary:
    def test_it_does_not_reach_the_members_of_a_nested_object(self):
        """Go and Java derive each member's order from the CONTEXT, not from its parent.

        A cascade would be defensible language design and is not what any other
        implementation does, so Python must not be the one that invents it.
        """
        result = decode(
            "name: p\nendian: big\nfields:\n"
            "- name: grp\n  type: object\n  endian: little\n  fields:\n"
            "  - {name: inner, type: u16}\n",
            "0102",
        )
        assert result.data == {
            "grp": {"inner": 258}
        }, "the member kept the schema's order"

    @pytest.mark.parametrize("declared", ["little", "big"])
    def test_a_word_ordered_type_is_unaffected(self, declared):
        """PS-272: the type fixes both orders, so `endian` plays no part either way."""
        result = decode(
            f"name: p\nendian: big\nfields:\n"
            f"- {{name: v, type: u32le16, endian: {declared}}}\n",
            "3f800000",
        )
        assert result.data == {"v": 16256}


class TestAnUnusableValueIsRefused:
    def test_the_decoder_reports_it(self):
        result = decode(
            "name: p\nendian: big\nfields:\n- {name: v, type: u16, endian: sideways}\n",
            "0102",
        )
        assert result.data == {}
        assert any("'big' or 'little'" in e for e in result.errors)

    def test_the_validator_reports_it_without_needing_a_vector(self):
        """A schema shipping no vectors would otherwise carry it to production silently.

        62 of the device schemas ship no test_vectors at all, so a check that only fires
        when a vector happens to exercise the field is not a check.
        """
        schema = yaml.safe_load(
            "name: p\nendian: big\nfields:\n- {name: v, type: u16, endian: sideways}\n"
        )
        result = validate_schema(schema)
        assert any(
            "endian" in e and "sideways" in e for e in result.schema_errors
        ), result.schema_errors

    def test_a_usable_value_validates_clean(self):
        schema = yaml.safe_load(
            "name: p\nendian: big\nfields:\n- {name: v, type: u16, endian: little}\n"
        )
        result = validate_schema(schema)
        assert result.schema_errors == []
