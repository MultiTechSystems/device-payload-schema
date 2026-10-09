"""CR-2026-094: SenML output takes primary units (PS-478 amended, PS-487, PS-488).

A SenML record's `u` is a registered SenML unit. A secondary unit from the registry is
re-expressed in its primary unit by the registry's scale and offset, computed in decimal;
`%` is written `/`; a unit the registry does not have is left out of the record, its value
kept, with a warning naming the field and the unit. The decoded output and `_meta` keep
the author's unit, so only the SenML path changes.

SenML is produced by Python and C# only; CR2026_094Tests.cs holds C# to the same cases.
The expected values are the specification prototype's, from the CR.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import (  # noqa: E402
    SENML_SECONDARY_UNITS,
    SENML_UNITS,
    SchemaInterpreter,
    senml_unit,
)


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        (3284, "mV", (3.284, "V")),
        (412, "ppm", (0.000412, "/")),
        (1013.2, "hPa", (101320, "Pa")),
        (-97, "dBm", (-127, "dBW")),
        (90, "/100", (0.9, "/")),
        (35, "cm", (0.35, "m")),
        (50, "%", (50, "/")),
        (21.5, "Cel", (21.5, "Cel")),
        (36, "km/h", (10, "m/s")),
    ],
)
def test_a_record_takes_the_primary_unit(value, unit, expected):
    got_value, u, unregistered = senml_unit(value, unit)
    assert (got_value, u) == expected and unregistered is None
    # In decimal: the exact re-expression, not 3.2840000000000003.
    assert repr(got_value) == repr(expected[0])


def test_an_unregistered_unit_is_left_out():
    assert senml_unit(12.5, "kPa") == (12.5, None, "kPa")


def test_only_a_number_is_converted():
    assert senml_unit("low", "mV") == ("low", "V", None)
    assert senml_unit(True, "mV") == (True, "V", None)


def test_the_tables_are_the_registry():
    # IANA SenML Units registry as updated 2026-02-02: 67 units, 39 secondary units.
    assert len(SENML_UNITS) == 67 and len(SENML_SECONDARY_UNITS) == 39
    assert all(
        primary in SENML_UNITS for primary, _, _ in SENML_SECONDARY_UNITS.values()
    )


SCHEMA = {
    "name": "units",
    "fields": [
        {"name": "battery", "type": "u16", "unit": "mV"},
        {"name": "pressure", "type": "u16", "unit": "kPa"},
        {"name": "rh", "type": "u8", "unit": "%", "senml": {"unit": "/100"}},
        {
            "name": "probe",
            "type": "object",
            "fields": [{"name": "depth", "type": "u8", "unit": "inch"}],
        },
    ],
}


def test_the_records_and_the_warnings():
    interpreter = SchemaInterpreter(SCHEMA)
    result = interpreter.decode(bytes.fromhex("0CD4" "0064" "5A" "07"))
    records = interpreter.get_semantic_output(result.data, "senml")
    assert records == [
        {"n": "battery", "v": 3.284, "u": "V"},
        {"n": "pressure", "v": 100},
        {"n": "rh", "v": 0.9, "u": "/"},
        {"n": "probe/depth", "v": 7},
    ]
    assert interpreter.semantic_warnings == [
        "pressure: unit 'kPa' is not a registered SenML unit, so its record has no "
        "'u' (PS-488)",
        "probe/depth: unit 'inch' is not a registered SenML unit, so its record has "
        "no 'u' (PS-488)",
    ]
    # The decoded output keeps the author's values; only SenML is re-expressed.
    assert result.data["battery"] == 3284 and result.data["rh"] == 90


def test_the_warnings_are_reset_per_call():
    interpreter = SchemaInterpreter(SCHEMA)
    result = interpreter.decode(bytes.fromhex("0CD4" "0064" "5A" "07"))
    interpreter.get_semantic_output(result.data, "senml")
    interpreter.get_semantic_output(result.data, "ipso")
    assert interpreter.semantic_warnings == []
