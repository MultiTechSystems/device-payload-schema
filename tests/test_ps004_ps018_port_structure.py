"""PS-004 and PS-018: a schema declares `fields` or `ports`, and what may name a port.

Both requirements are old and were enforced by nothing. They were found while gathering
evidence for a change request against the specification prototype's port selection
(la-payload-schema CR-2026-038), which asks a different question - what a decoder does
when no FPort is supplied at all.

PS-004 needs no specification change: the clause is clear and the validator contradicted
it. PS-018 is the interesting one, because enforcing it literally turned out to be the
wrong thing to do, and the reasoning is below.

**PS-018 is reported, not enforced, and that is deliberate.** The validator bounded a
port at 1-255, the FPort *byte* range, where PS-018 confines it to 1-223, the LoRaWAN
*application* range. The obvious repair - move the ceiling to 223 - is wrong, because the
other octet values are ASSIGNED rather than invalid: TS001 gives 0 to MAC commands and
224 to the MAC-layer certification test protocol, and reserves 225-255 for future
standardised applications. This repository already ships a MAC command library
(`schemas/library/lorawan/lorawan_mac_commands.yaml`), so a schema describing traffic on
one of those ports is out of PS-018's scope but is not malformed, and a tool that
refuses it refuses payloads that exist.

So the split is by what the value can possibly be. An FPort is one octet: outside 0-255
it names no port at all and is an error. Inside 0-255 but outside 1-223 it is a warning
naming what that port is assigned to, which keeps the departure from PS-018 visible and
deliberate while leaving the schema usable.

**PS-004 was stated backwards.** The validator's own comment and error message both said
"either 'fields' or 'ports' (or both)" where PS-004 says "not both". The consequence is
not cosmetic: `tools/schema_interpreter.py` resolves the port entry and decodes its
fields alone, so a schema carrying both loses its top-level fields silently - the
familiar shape where the decode succeeds and reports fewer fields than the schema
declares.

No schema in the repository is affected: one schema uses `ports`
(`digital-matter/oyster`), none carries both, and no port key sits outside 1..223. That
is worth stating because it is also why neither defect was ever observed - the corpus
could not see them, so only a test written against the requirement can hold them.

Whether PS-018 itself should be widened, so that a schema on port 0 or 224 is in scope
rather than merely tolerated, is a specification question and is not settled here.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from validate_schema import (  # noqa: E402
    ValidationResult,
    check_best_practices,
    validate_schema_structure,
)


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


def warnings_for(*keys):
    """Only the port-range warnings. `check_best_practices` reports other things too -
    a bare fixture has no test vectors and no description - and asserting on the whole
    list would make these tests fail whenever an unrelated best practice is added."""
    result = ValidationResult(schema_valid=True)
    check_best_practices(port_schema(*keys), result)
    return [w for w in result.schema_warnings if "PS-018" in w]


class TestAnFPortMustFitInAnOctet:
    """Structural: only 0-255 can name an FPort at all. Everything else is an error."""

    @pytest.mark.parametrize("port", [0, 1, 100, 223, 224, 255])
    def test_any_octet_value_is_structurally_valid(self, port):
        assert validate_schema_structure(port_schema(port)) == []

    @pytest.mark.parametrize("port", [256, -1, 1000])
    def test_a_value_outside_the_octet_is_rejected(self, port):
        errors = validate_schema_structure(port_schema(port))
        assert any("0-255" in e for e in errors), (port, errors)

    def test_default_is_a_reserved_key_not_a_port_number(self):
        assert validate_schema_structure(port_schema("default")) == []

    def test_a_key_that_is_neither_a_number_nor_default_is_rejected(self):
        errors = validate_schema_structure(port_schema("telemetry"))
        assert any("integer" in e for e in errors), errors

    def test_a_string_port_number_is_accepted(self):
        # JSON object keys are always strings, so "12" and 12 are the same port.
        assert validate_schema_structure(port_schema("12")) == []


class TestPS018IsReportedNotEnforced:
    """PS-018: 1-223 is the application range. Outside it is a warning, not a rejection."""

    @pytest.mark.parametrize("port", [1, 2, 100, 222, 223])
    def test_a_port_in_the_application_range_warns_about_nothing(self, port):
        assert warnings_for(port) == []

    @pytest.mark.parametrize("port", [0, 224, 225, 250, 255])
    def test_a_port_outside_it_warns_but_still_validates(self, port):
        # Both halves matter: the departure is reported, and the schema stays usable.
        assert validate_schema_structure(port_schema(port)) == []
        assert any("PS-018" in w for w in warnings_for(port)), port

    def test_a_reserved_port_is_usable_because_it_is_assigned_not_invalid(self):
        # The point of the whole change: an application on the certification test port
        # or on the MAC port has a real payload, and this tool must not refuse it.
        for port in (0, 224):
            assert validate_schema_structure(port_schema(port)) == []

    def test_the_warning_names_what_the_port_is_for(self):
        assert any("MAC commands" in w for w in warnings_for(0))
        assert any("certification" in w for w in warnings_for(224))
        assert any("future standardised" in w for w in warnings_for(240))

    def test_default_is_not_warned_about(self):
        assert warnings_for("default") == []

    def test_a_string_port_out_of_range_warns_the_same_as_an_int(self):
        assert any("PS-018" in w for w in warnings_for("250"))


class TestTheCorpusIsUnaffected:
    """Neither fix may change the verdict on a schema already in the repository."""

    def test_the_only_ports_schema_still_validates(self):
        import yaml

        oyster = REPO_ROOT / "schemas" / "devices" / "digital-matter" / "oyster.yaml"
        assert validate_schema_structure(yaml.safe_load(oyster.read_text())) == []
