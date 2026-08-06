"""Canonical modifier order (PS-101 / PS-102).

The bare `mult`, `div` and `add` modifiers apply in the fixed order mult, div,
add, whatever order the keys appear in the source. Before this was fixed, each
implementation did something different -- Python followed source key order, Go
carried a `ModOrder` field for YAML but fell back to add-mult-div for JSON and
used mult-div-add on its computed-field path, and Java and C# had the same split
-- so one schema could decode to different values depending on the language and
the serialisation it arrived in, silently.

`examples/canonical-modifier-order.yaml` is the shared fixture; every language's
test suite should decode it to the same values.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import (  # noqa: E402
    CANONICAL_MODIFIER_ORDER,
    SchemaInterpreter,
    apply_canonical_modifiers,
)
from validate_schema import validate_schema  # noqa: E402

FIXTURE = REPO_ROOT / "examples" / "canonical-modifier-order.yaml"


def decode(field_yaml, payload_hex):
    schema = yaml.safe_load(
        "name: t\nendian: big\nfields:\n  - name: v\n    type: u16\n" + field_yaml
    )
    result = SchemaInterpreter(schema).decode(bytes.fromhex(payload_hex))
    assert result.success, result.errors
    return result.data["v"]


def test_canonical_order_is_mult_div_add():
    assert CANONICAL_MODIFIER_ORDER == ("mult", "div", "add")


@pytest.mark.parametrize(
    "field_yaml",
    [
        "    div: 10\n    add: -40\n",
        "    add: -40\n    div: 10\n",
    ],
    ids=["div-first", "add-first"],
)
def test_key_order_does_not_change_the_result(field_yaml):
    """(1000 / 10) - 40 = 60 either way round."""
    assert decode(field_yaml, "03E8") == pytest.approx(60.0)


def test_all_three_modifiers_apply_in_canonical_order():
    """((100 * 3) / 2) + 1 = 151, not some other permutation."""
    assert decode("    add: 1\n    div: 2\n    mult: 3\n", "0064") == pytest.approx(151.0)


def test_absent_modifier_is_the_identity():
    assert decode("    mult: 2\n", "0064") == pytest.approx(200.0)
    assert decode("", "0064") == pytest.approx(100)


def test_transform_expresses_a_different_order():
    """Offset before division cannot be written with bare keys."""
    value = decode("    transform:\n      - add: -32768\n      - div: 100\n", "8009")
    assert value == pytest.approx(0.09)


def test_bare_keys_cannot_express_offset_first():
    """The same numbers as bare modifiers give the canonical result instead."""
    assert decode("    add: -32768\n    div: 100\n", "8009") != pytest.approx(0.09)


def test_multi_op_transform_stage_applies_every_operation():
    """A stage with several ops used to drop all but the first."""
    value = decode("    transform:\n      - {add: 10, mult: 2}\n", "0064")
    assert value == pytest.approx(210.0)


@pytest.mark.parametrize(
    "field_yaml",
    [
        "    div: 10\n    add: -40\n",
        "    add: -40\n    div: 10\n",
        "    mult: 3\n    div: 2\n    add: 1\n",
    ],
)
def test_encode_inverts_the_canonical_order(field_yaml):
    """Encoding must return the bytes the value was decoded from."""
    schema = yaml.safe_load(
        "name: rt\nendian: big\nfields:\n  - name: v\n    type: u16\n" + field_yaml
    )
    interpreter = SchemaInterpreter(schema)
    original = (1000).to_bytes(2, "big")
    decoded = interpreter.decode(original)
    assert decoded.success, decoded.errors
    encoded = interpreter.encode({"v": decoded.data["v"]})
    assert encoded.success, encoded.errors
    assert encoded.payload == original


def test_helper_ignores_zero_divisor():
    assert apply_canonical_modifiers(10.0, {"div": 0}) == pytest.approx(10.0)


def test_helper_applies_only_the_modifiers_present():
    assert apply_canonical_modifiers(10.0, {"add": 5}) == pytest.approx(15.0)
    assert apply_canonical_modifiers(10.0, {"mult": 2, "add": 5}) == pytest.approx(25.0)


class TestSharedFixture:
    """The cross-language fixture must pass, and must pin key-order independence."""

    def test_fixture_exists(self):
        assert FIXTURE.exists(), "shared conformance fixture is missing"

    def test_fixture_vectors_pass(self):
        schema = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
        result = validate_schema(schema)
        assert result.schema_valid, result.schema_errors
        assert result.tests_failed == 0, [
            test.errors for test in result.test_results if not test.passed
        ]

    def test_fixture_pins_key_order_independence(self):
        schema = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
        for vector in schema["test_vectors"]:
            expected = vector["expected"]
            assert expected["scaled_div_first"] == expected["scaled_add_first"], (
                "the fixture must assert that both key orders decode alike"
            )
