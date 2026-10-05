"""CR-2026-050 and CR-2026-061: timestamps from another epoch, and as calendar parts.

`metadata.timestamps` is OPTIONAL (PS-310) and only the reference interpreter implements
it, so what it reports is held here rather than in the shared corpus. The fixture
metadata-epoch-calendar.yaml holds the other half: every implementation decodes a schema
carrying the new keys exactly as it would without them.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

FIXTURE = REPO_ROOT / "schemas/devices/_language-conformance/metadata-epoch-calendar.yaml"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def enrich(timestamps, payload_hex, fields=None):
    schema = {"name": "probe",
              "fields": fields or [{"name": "t", "type": "u32"}],
              "metadata": {"timestamps": timestamps}}
    return SchemaInterpreter(schema).decode(bytes.fromhex(payload_hex), input_metadata={})


def test_the_fixture_reports_the_specification_example():
    schema = yaml.safe_load(FIXTURE.read_text())
    result = SchemaInterpreter(schema).decode(bytes.fromhex("19D307151610A015"),
                                              input_metadata={})
    assert result.data["sampled_unix"] == 1790258709
    assert result.data["sampled_iso"] == "2026-09-24T14:05:09Z"
    assert result.data["timestamp"] == {"year": 2026, "month": "September", "day": 24,
                                        "hours": 14, "minutes": 5, "seconds": 9}
    assert result.warnings == []


def test_the_counts_stay_the_devices_own():
    """With a declared epoch the field is the device's count, not a converted copy."""
    schema = yaml.safe_load(FIXTURE.read_text())
    data = SchemaInterpreter(schema).decode(bytes.fromhex("19D307151610A015"),
                                            input_metadata={}).data
    assert data["since_2013"] == 433260309


# PS-354, PS-356 --------------------------------------------------------------------

def test_unix_epoch_reports_a_number_whatever_its_epoch():
    """PS-356. Before 0.5.2 it reported "2013-01-01T00:01:00.000Z", a string."""
    out = enrich([{"name": "u", "mode": "unix_epoch", "field": "t",
                   "epoch": "2013-01-01T00:00:00Z"}], "0000003C").data
    assert out["u"] == 1356998460 and isinstance(out["u"], int)


def test_no_epoch_means_1970():
    out = enrich([{"name": "u", "mode": "unix_epoch", "field": "t"},
                  {"name": "i", "mode": "iso8601", "field": "t"}], "0000003C").data
    assert out["u"] == 60
    assert out["i"] == "1970-01-01T00:01:00Z"


def test_iso8601_has_a_fraction_only_where_the_value_does():
    fields = [{"name": "t", "type": "u8", "div": 4}]
    out = enrich([{"name": "i", "mode": "iso8601", "field": "t"}], "06", fields).data
    assert out["i"] == "1970-01-01T00:00:01.5Z"
    out = enrich([{"name": "i", "mode": "iso8601", "field": "t"}], "08", fields).data
    assert out["i"] == "1970-01-01T00:00:02Z"


def test_an_internal_field_resolves():
    """PS-435: the spec's calendar example reads `_time_2015`, which is never reported."""
    fields = [{"name": "_t", "type": "u32"}]
    out = enrich([{"name": "c", "mode": "calendar", "field": "_t",
                   "epoch": "2015-01-01T00:00:00Z"}], "1610A015", fields).data
    assert "_t" not in out
    assert out["c"]["year"] == 2026


def test_without_runtime_input_nothing_is_derived():
    """PS-312, which CR-2026-050 records and leaves alone."""
    schema = yaml.safe_load(FIXTURE.read_text())
    data = SchemaInterpreter(schema).decode(bytes.fromhex("19D307151610A015")).data
    assert set(data) == {"since_2013", "since_2015"}


# PS-410 to PS-413 --------------------------------------------------------------------

def test_calendar_defaults_are_numbers_under_their_own_names():
    out = enrich([{"name": "c", "mode": "calendar", "field": "t"}], "00000000").data
    assert out["c"] == {"year": 1970, "month": 1, "day": 1, "hour": 0, "minute": 0,
                        "second": 0}


def test_calendar_labels_the_month():
    out = enrich([{"name": "c", "mode": "calendar", "field": "t",
                   "month_labels": MONTHS}], "00000000").data
    assert out["c"]["month"] == "January"


# Rejections: PS-355, PS-411, PS-412, the withdrawn format: ---------------------------

REJECTED = [
    ({"mode": "iso8601", "epoch": "2013-01-01T00:00:00+01:00"}, "PS-355"),
    ({"mode": "iso8601", "epoch": "2013-01-01"}, "PS-355"),
    ({"mode": "iso8601", "epoch": "2013-13-01T00:00:00Z"}, "PS-355"),
    ({"mode": "rx_time", "epoch": "2013-01-01T00:00:00Z"}, "PS-355"),
    ({"mode": "iso8601", "format": "%Y"}, "PS-356"),
    ({"mode": "calendar", "month_labels": ["Jan"]}, "PS-411"),
    ({"mode": "unix_epoch", "month_labels": MONTHS}, "PS-411"),
    ({"mode": "calendar", "keys": {"hour": "h", "minute": "h"}}, "PS-412"),
    ({"mode": "calendar", "keys": {"hour": "minute"}}, "PS-412"),
    ({"mode": "calendar", "keys": {"weekday": "w"}}, "PS-412"),
]


@pytest.mark.parametrize("entry,tag", REJECTED, ids=[f"{t}-{i}" for i, (_, t) in enumerate(REJECTED)])
def test_a_malformed_entry_is_rejected_by_interpreter_and_validator(entry, tag):
    ts = dict({"name": "x", "field": "t"}, **entry)
    schema = {"name": "probe", "fields": [{"name": "t", "type": "u32"}],
              "metadata": {"timestamps": [ts]}}
    result = SchemaInterpreter(schema).decode(b"\x00\x00\x00\x01")
    assert not result.success and any(tag in e for e in result.errors), result.errors
    assert any(tag in e for e in validate_schema_structure(schema))


def test_an_unquoted_epoch_names_the_yaml_trap():
    schema = yaml.safe_load("name: p\nfields: [{name: t, type: u32}]\nmetadata:\n"
                            "  timestamps: [{name: x, mode: iso8601, field: t, "
                            "epoch: 2013-01-01T00:00:00Z}]\n")
    errors = SchemaInterpreter(schema).decode(b"\x00\x00\x00\x01").errors
    assert any("quote it" in e for e in errors), errors
