"""The validator holds a definition's fields to the field rules where `$ref` splices it.

It used to check the raw field lists, so a definition's members were never seen: an object
with `fields: []` (PS-142) was rejected written inline and accepted inside a definition
(registry jooby/analog-gasi3).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from validate_schema import validate_schema_structure  # noqa: E402

EMPTY_OBJECT = {"name": "p", "type": "object", "fields": []}


def ps142(errors):
    return [e for e in errors if "PS-142" in e]


def test_inline_empty_object_is_rejected():
    schema = {"name": "t", "fields": [{"name": "a", "type": "u8"}, EMPTY_OBJECT]}
    assert ps142(validate_schema_structure(schema))


def test_empty_object_in_a_definition_is_rejected_where_spliced():
    schema = {
        "name": "t",
        "definitions": {"body": {"fields": [EMPTY_OBJECT]}},
        "fields": [{"name": "a", "type": "u8"}, {"$ref": "#/definitions/body"}],
    }
    assert ps142(validate_schema_structure(schema))


def test_empty_object_in_a_definition_spliced_into_a_port_is_rejected():
    schema = {
        "name": "t",
        "definitions": {"body": {"fields": [EMPTY_OBJECT]}},
        "ports": {
            1: {"fields": [{"name": "a", "type": "u8"}, {"$ref": "#/definitions/body"}]}
        },
    }
    assert ps142(validate_schema_structure(schema))


def test_a_valid_definition_still_validates():
    schema = {
        "name": "t",
        "definitions": {"body": {"fields": [{"name": "b", "type": "u8"}]}},
        "fields": [{"name": "a", "type": "u8"}, {"$ref": "#/definitions/body"}],
    }
    assert validate_schema_structure(schema) == []
