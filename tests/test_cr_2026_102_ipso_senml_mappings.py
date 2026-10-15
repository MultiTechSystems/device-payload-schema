"""CR-2026-102: `ipso` and `senml` are mappings (PS-510).

A field's `ipso` is a mapping carrying `object`, and its `senml` a mapping. Any other
value is refused when the schema is loaded, naming the field and the key. `ipso: 3303`
used to load, and every implementation dropped it: the author believed the field
annotated and no consumer saw an annotation. A bare object number is not a shorthand.

The rule holds for every field mapping, wherever it sits. The legacy
`semantic: {ipso: N}` is a different key and is outside PS-510.

A rejection cannot be a shared corpus fixture, since no vector kind expects a failure;
the accepted forms are exercised throughout the corpus. Go, Java and C# carry the same
cases in their own tests (cr102_test.go, CR2026102Test, CR2026_102Tests).
"""

import glob
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import yaml  # noqa: E402
from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import (  # noqa: E402
    SchemaInterpreter,
    annotation_schema_errors,
    expand_refs,
)
from validate_schema import load_schema_text, validate_schema_structure  # noqa: E402

IPSO_MESSAGE = (
    "The ipso of 't' is a mapping that carries `object`, such as {object: 3303}; "
    "a bare object number is not a shorthand (PS-510)"
)
SENML_MESSAGE = "The senml of 't' is a mapping, such as {unit: Cel} (PS-510)"

REFUSED = {
    "ipso integer": ("ipso: 3303", IPSO_MESSAGE),
    "ipso string": ("ipso: '3303'", IPSO_MESSAGE),
    "ipso null": ("ipso: null", IPSO_MESSAGE),
    "ipso list": ("ipso: [3303]", IPSO_MESSAGE),
    "ipso without object": ("ipso: {resource: 5700}", IPSO_MESSAGE),
    "senml string": ("senml: 'Cel'", SENML_MESSAGE),
    "senml null": ("senml: null", SENML_MESSAGE),
    "senml list": ("senml: ['Cel']", SENML_MESSAGE),
}

ACCEPTED = {
    "ipso object": "ipso: {object: 3303}",
    "ipso full": "ipso: {object: 3303, instance: 1, resource: 5700}",
    "senml unit": "senml: {unit: Cel}",
    "senml name and unit": "senml: {name: x, unit: Cel}",
}

#: Every place a field mapping sits. FIELD is replaced by the annotated field.
CONTEXTS = {
    "top level": "fields:\n  - FIELD\n",
    "port": "ports:\n  1:\n    fields:\n      - FIELD\n",
    "match case": (
        "fields:\n  - {name: kind, type: u8}\n  - match:\n      field: $kind\n"
        "      cases:\n        1:\n          - FIELD\n"
    ),
    "tlv case": "fields:\n  - tlv:\n      tag_size: 1\n      cases:\n        1:\n          - FIELD\n",
    "object member": "fields:\n  - name: o\n    type: object\n    fields:\n      - FIELD\n",
    "repeat element": (
        "fields:\n  - name: r\n    type: repeat\n    count: 2\n    fields:\n      - FIELD\n"
    ),
    "byte_group member": "fields:\n  - byte_group:\n      size: 1\n      fields:\n        - BITS\n",
    "flagged group": (
        "fields:\n  - {name: mask, type: u8}\n  - flagged:\n      field: mask\n"
        "      groups:\n        - bit: 0\n          fields:\n            - FIELD\n"
    ),
    "definition": (
        "definitions:\n  d:\n    fields:\n      - FIELD\n"
        "fields:\n  - $ref: '#/definitions/d'\n"
    ),
}


def schema_text(context, annotation):
    body = CONTEXTS[context]
    body = body.replace("FIELD", "{name: t, type: u8, %s}" % annotation)
    body = body.replace("BITS", "{name: t, type: 'u8[0:7]', %s}" % annotation)
    return "name: p\n" + body


@pytest.mark.parametrize("context", sorted(CONTEXTS))
@pytest.mark.parametrize("label", sorted(REFUSED))
def test_a_non_mapping_annotation_is_refused_on_every_path(label, context):
    annotation, message = REFUSED[label]
    text = schema_text(context, annotation)
    schema = yaml.safe_load(text)
    assert message in SchemaInterpreter(schema).decode(b"\x01\x01\x01", fPort=1).errors
    assert message in SchemaInterpreter(schema).encode({}, fPort=1).errors
    assert message in validate_schema_structure(load_schema_text(text))
    with pytest.raises(ValueError) as raised:
        TS013Generator(schema).generate()
    assert str(raised.value) == message


@pytest.mark.parametrize("context", sorted(CONTEXTS))
@pytest.mark.parametrize("label", sorted(ACCEPTED))
def test_a_mapping_annotation_is_accepted_on_every_path(label, context):
    text = schema_text(context, ACCEPTED[label])
    schema = yaml.safe_load(text)
    assert not any("PS-510" in e for e in validate_schema_structure(load_schema_text(text)))
    assert not any(
        "PS-510" in e for e in SchemaInterpreter(schema).decode(b"\x01\x01\x01", fPort=1).errors
    )
    TS013Generator(schema).generate()


def test_an_unreferenced_definition_is_held_to_it_too():
    """Go and C# parse every definition, so a definition no field references is checked."""
    schema = yaml.safe_load(
        "name: p\ndefinitions:\n  d:\n    fields:\n      - {name: t, type: u8, ipso: 3303}\n"
        "fields:\n  - {name: a, type: u8}\n"
    )
    assert IPSO_MESSAGE in SchemaInterpreter(schema).decode(b"\x01").errors


def test_the_legacy_semantic_ipso_is_withdrawn():
    """CR-2026-179 (PS-470): `semantic` is a TTN normalized path, a string; the legacy
    `semantic: {ipso: N}` is rejected, `ipso:` being the IPSO annotation."""
    schema = {
        "name": "p",
        "fields": [{"name": "t", "type": "u8", "semantic": {"ipso": 3303}}],
    }
    assert any("PS-470" in e for e in annotation_schema_errors(schema))


CORPUS = ["schemas/devices/**/*.yaml", "schemas/library/**/*.yaml", "examples/**/*.yaml"]


def test_no_corpus_schema_trips_ps_510():
    """#85 converted the last bare `ipso`; this keeps it so, definitions included."""
    tripped = []
    for pattern in CORPUS:
        for path in glob.glob(str(REPO_ROOT / pattern), recursive=True):
            with open(path, encoding="utf-8") as handle:
                document = yaml.safe_load(handle)
            if not isinstance(document, dict):
                continue
            expanded, _ = expand_refs(document)
            if annotation_schema_errors(expanded):
                tripped.append(path)
    assert tripped == []
