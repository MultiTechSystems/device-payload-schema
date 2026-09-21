"""PS-004 and PS-018: a schema declares `fields` or `ports`, and a port is 1..223.

Both requirements are old, clear and were enforced by nothing. They were found while
gathering evidence for a change request against the specification prototype's port
selection (la-payload-schema CR-2026-038), which asks a different question - what a
decoder does when no FPort is supplied at all. These two need no specification change;
the clauses already say what to do and `tools/validate_schema.py` simply did not do it.

**PS-018's bound was wrong rather than missing**, which is the more interesting half.
The validator checked a range, reserved `default` and rejected a non-integer key - the
whole structure was right - but bounded at 1-255, the FPort *byte* range. PS-018
specifies the LoRaWAN *application* range: 0 is the MAC port and 224-255 are reserved,
so a schema could declare port 250 and validate clean. A wrong bound reads as a checked
one, which is why this outlived the absent check next to it.

**PS-004 was stated backwards.** The validator's own comment and error message both said
"either 'fields' or 'ports' (or both)" where PS-004 says "not both". The consequence is
not cosmetic: `tools/schema_interpreter.py` resolves the port entry and decodes its
fields alone, so a schema carrying both loses its top-level fields silently - the
familiar shape where the decode succeeds and reports fewer fields than the schema
declares.

No schema in the repository was affected by either fix: one schema uses `ports`
(`digital-matter/oyster`), none carries both, and no port key sits outside 1..223. That
is worth stating because it is also why neither defect was ever observed - the corpus
could not see them, so only a test written against the requirement can hold them.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from validate_schema import validate_schema_structure  # noqa: E402


def schema(**top):
    base = {"name": "t", "version": 1}
    base.update(top)
    return base


def port_schema(*keys):
    return schema(ports={k: {"fields": [{"name": "a", "type": "u8"}]} for k in keys})


FIELDS = [{"name": "a", "type": "u8"}]


class TestPS004FieldsOrPorts:
    """PS-004: a schema MUST contain either a `fields` array or a `ports` object, not both."""

    def test_fields_alone_is_valid(self):
        assert validate_schema_structure(schema(fields=FIELDS)) == []

    def test_ports_alone_is_valid(self):
        assert validate_schema_structure(port_schema(1)) == []

    def test_both_together_is_rejected(self):
        errors = validate_schema_structure(schema(fields=FIELDS, ports={1: {"fields": FIELDS}}))
        assert any("not both" in e for e in errors), errors

    def test_neither_is_rejected(self):
        errors = validate_schema_structure(schema())
        assert any("'fields' or 'ports'" in e for e in errors), errors

    def test_the_rejection_cites_the_requirement(self):
        # The message is the only place a schema author learns which rule they broke.
        errors = validate_schema_structure(schema(fields=FIELDS, ports={1: {"fields": FIELDS}}))
        assert any("PS-004" in e for e in errors), errors


class TestPS018PortRange:
    """PS-018: port numbers MUST be integers between 1 and 223."""

    @pytest.mark.parametrize("port", [1, 2, 100, 222, 223])
    def test_a_port_in_the_application_range_is_accepted(self, port):
        assert validate_schema_structure(port_schema(port)) == []

    @pytest.mark.parametrize("port", [0, 224, 250, 255, 256, -1])
    def test_a_port_outside_the_application_range_is_rejected(self, port):
        errors = validate_schema_structure(port_schema(port))
        assert any("1-223" in e for e in errors), (port, errors)

    def test_223_is_the_boundary_and_224_is_not(self):
        # The bound this test exists for: 224-255 is LoRaWAN reserved, and the FPort
        # byte range accepted it for as long as the check has existed.
        assert validate_schema_structure(port_schema(223)) == []
        assert validate_schema_structure(port_schema(224)) != []

    def test_default_is_a_reserved_key_not_a_port_number(self):
        assert validate_schema_structure(port_schema("default")) == []

    def test_a_key_that_is_neither_a_number_nor_default_is_rejected(self):
        errors = validate_schema_structure(port_schema("telemetry"))
        assert any("integer" in e for e in errors), errors

    def test_a_string_port_number_is_accepted(self):
        # JSON object keys are always strings, so "12" and 12 are the same port.
        assert validate_schema_structure(port_schema("12")) == []

    def test_a_string_port_out_of_range_is_still_rejected(self):
        assert validate_schema_structure(port_schema("250")) != []

    def test_the_rejection_cites_the_requirement(self):
        errors = validate_schema_structure(port_schema(250))
        assert any("PS-018" in e for e in errors), errors


class TestTheCorpusIsUnaffected:
    """Neither fix may change the verdict on a schema already in the repository."""

    def test_the_only_ports_schema_still_validates(self):
        import yaml

        oyster = REPO_ROOT / "schemas" / "devices" / "digital-matter" / "oyster.yaml"
        assert validate_schema_structure(yaml.safe_load(oyster.read_text())) == []
