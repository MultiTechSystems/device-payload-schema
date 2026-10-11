"""PS-539 (CR-2026-127) checks references in one decode order: within a mapping,
`tag_fields`, `trailer`, `fields` and `element` first, then the remaining keys in source
order. Each schema below differs from its pair only in the order two keys are written, and
that order alone decides whether the reference follows its declaration.

Mirrors go/schema/ps539_walk_order_test.go, Java's ReferenceWalkOrderTest and C#'s
ReferenceWalkOrderTests. Go walked sorted keys, scalars before nested values, and got
four of these wrong.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import expand_refs, reference_errors  # noqa: E402

ORDER = "is referenced before the schema declares it (PS-539, PS-122)"

CASES = [
    # A match's cases written before its discriminator declare the name it reads.
    (
        "fields:\n  - {name: k, type: u8}\n"
        "  - match: {cases: {1: [{name: x, type: u8}]}, default: skip, field: $x}\n",
        None,
    ),
    (
        "fields:\n  - {name: k, type: u8}\n"
        "  - match: {field: $x, default: skip, cases: {1: [{name: x, type: u8}]}}\n",
        "`$x` (field) " + ORDER,
    ),
    # A match's default field list against its cases.
    (
        "fields:\n  - {name: k, type: u8}\n"
        "  - match: {field: $k, default: [{name: x, type: u8}], "
        "cases: {1: [{name: y, type: number, ref: $x}]}}\n",
        None,
    ),
    (
        "fields:\n  - {name: k, type: u8}\n"
        "  - match: {field: $k, default: [{name: y, type: number, ref: $x}], "
        "cases: {1: [{name: x, type: u8}]}}\n",
        "`$x` (ref) " + ORDER,
    ),
    # Ports in source order, not sorted as strings ("10" < "9") or as numbers.
    (
        "ports:\n  9: {fields: [{name: x, type: u8}]}\n"
        "  10: {fields: [{name: y, type: number, ref: $x}]}\n",
        None,
    ),
    (
        "ports:\n  2: {fields: [{name: y, type: number, ref: $x}]}\n"
        "  1: {fields: [{name: x, type: u8}]}\n",
        "`$x` (ref) " + ORDER,
    ),
]


def errors_of(schema):
    expanded, ref_errors = expand_refs(schema)
    assert not ref_errors
    return reference_errors(expanded)


@pytest.mark.parametrize("doc,refused", CASES)
def test_reference_order_follows_the_source(doc, refused):
    errors = errors_of(yaml.safe_load("name: t\n" + doc))
    assert errors == ([refused] if refused else [])


@pytest.mark.parametrize("doc,refused", CASES)
def test_reference_order_follows_the_source_as_json(doc, refused):
    # The same schema authored as JSON keeps its key order.
    schema = json.loads(json.dumps(yaml.safe_load("name: t\n" + doc)))
    errors = errors_of(schema)
    assert errors == ([refused] if refused else [])
