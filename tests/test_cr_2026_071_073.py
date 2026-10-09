"""CR-2026-071 (a field's arithmetic pipeline) and CR-2026-073 (one operation per stage).

The decode order itself - source, modifiers, transform, lookup, with a failed guard's
`else` reported as declared - is held to every implementation by three corpus fixtures:
`_language-conformance/arithmetic-order-computed.yaml`, `arithmetic-order-read.yaml` and
`guard-else-as-declared.yaml`. What a fixture cannot state is a schema that must be
refused, so the rejections are here: each is checked on all three Python paths (the
interpreter, the validator and the TS013 generator), since a rule one path enforces and
another skips is how the two drifted before.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

GUARD = {"when": [{"field": "$x", "gt": 0}], "else": 0}


def schema_of(*fields):
    return {"name": "probe", "fields": [{"name": "x", "type": "u8"}] + list(fields)}


def assert_rejected(schema, tag):
    result = SchemaInterpreter(schema).decode(b"\x03\x04")
    assert not result.success and result.data == {}, result
    assert any(tag in e for e in result.errors), result.errors
    assert any(tag in e for e in validate_schema_structure(schema))
    with pytest.raises(ValueError, match=tag):
        TS013Generator(schema).generate()


@pytest.mark.parametrize(
    "field",
    [
        {"name": "v", "type": "u8", "guard": GUARD},
        {"name": "v", "type": "s16", "guard": GUARD},
        # A literal reads nothing, but it has no source a guard could precede either.
        {"name": "v", "type": "number", "value": 3, "guard": GUARD},
    ],
    ids=["u8", "s16", "literal"],
)
def test_a_guard_off_a_computed_field_is_rejected(field):
    # PS-445: ignored with success by every implementation before.
    assert_rejected(schema_of(field), "PS-445")


@pytest.mark.parametrize(
    "source", [{"ref": "$x"}, {"compute": {"op": "add", "a": "$x", "b": 1}}]
)
def test_a_guard_on_a_computed_field_is_accepted(source):
    field = dict({"name": "v", "type": "number", "guard": GUARD}, **source)
    assert SchemaInterpreter(schema_of(field)).decode(b"\x03").success


@pytest.mark.parametrize(
    "stage,held",
    [
        ({"add": 1, "mult": 2}, "holds add, mult"),
        ({"div": 2, "sqrt": True}, "holds div, sqrt"),
        ({"op": "round", "add": 1}, "holds op, add"),
        ({}, "holds none"),
        ({"decimals": 2}, "holds none"),
        ({"ties": "away"}, "holds none"),
    ],
)
def test_a_stage_without_exactly_one_operation_is_rejected(stage, held):
    # PS-452: every implementation decoded {add: 1, mult: 2}, in an order of its own.
    field = {"name": "v", "type": "u8", "transform": [stage]}
    assert_rejected({"name": "probe", "fields": [field]}, "PS-452")
    errors = (
        SchemaInterpreter({"name": "probe", "fields": [field]}).decode(b"\x03").errors
    )
    assert any(held in e for e in errors), errors


def test_the_parameters_of_an_op_stage_are_part_of_its_one_operation():
    field = {
        "name": "v",
        "type": "u8",
        "transform": [{"op": "round", "decimals": 1, "ties": "away"}, {"add": 1}],
    }
    result = SchemaInterpreter({"name": "probe", "fields": [field]}).decode(b"\x03")
    assert result.success and result.data == {"v": 4}
    assert not validate_schema_structure({"name": "probe", "fields": [field]})


def test_a_stage_is_checked_wherever_the_field_sits():
    nested = {
        "name": "probe",
        "fields": [
            {
                "byte_group": [
                    {
                        "name": "hi",
                        "type": "u8[4:7]",
                        "transform": [{"add": 1, "div": 2}],
                    },
                    {"name": "lo", "type": "u8[0:3]", "consume": 1},
                ]
            }
        ],
    }
    assert_rejected(nested, "PS-452")


def test_a_fractional_value_indexes_no_entry_of_a_sequence():
    # PS-443 puts the lookup after a computed field's arithmetic, so its value may be a
    # float. An integral one is its index; one with a fraction is no index (PS-105). The
    # reference passed both through unlooked-up, with success.
    labels = ["a", "b", "c", "d"]
    whole = {"name": "v", "type": "number", "ref": "$x", "div": 2, "lookup": labels}
    assert SchemaInterpreter(schema_of(whole)).decode(b"\x04").data["v"] == "c"
    result = SchemaInterpreter(schema_of(whole)).decode(b"\x03")
    assert not result.success and any("PS-105" in e for e in result.errors)


def test_a_guard_on_a_mapping_with_no_type_is_rejected():
    # PS-445 walked typed fields only, so a guard beside a byte_group passed.
    schema = {
        "name": "probe",
        "fields": [
            {
                "byte_group": [{"name": "hi", "type": "u8[4:7]", "consume": 1}],
                "guard": {"when": [{"field": "$hi", "gt": 0}], "else": 0},
            }
        ],
    }
    assert_rejected(schema, "PS-445")
