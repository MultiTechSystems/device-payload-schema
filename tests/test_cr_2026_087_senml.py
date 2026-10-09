"""CR-2026-087: SenML record names and units (PS-478, PS-479).

One record per field the decoded output reports, wherever it is declared, named by its
`senml.name` or else its reported name, with `senml.unit` or else `unit`; a member of an
object with no `senml.name` is named by the object's reported name and its own, joined
by `/`. The reference walked the top-level field list only and ignored `senml:`
entirely, so Clause 7's own example came out as `temperature` in `°C`.

SenML output is produced by Python and C# only; Go, Java, the generated codec and C have
none, so this is not a corpus fixture (an OPTIONAL output, AGENTS.md).
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

ENVIRONMENTAL = """name: environmental_sensor
version: 1
fields:
  - {name: temperature, type: s16, div: 10, unit: "°C", senml: {name: temp, unit: Cel}}
  - {name: humidity, type: u8, mult: 0.5, unit: "%RH", senml: {name: rh, unit: "%RH"}}
  - {name: battery_voltage, type: u16, div: 1000, unit: V, senml: {name: vbat, unit: V}}
"""

PLACEMENTS = """name: senml_placements
ports:
  1:
    fields:
    - {name: kind, type: u8}
    - byte_group:
      - {name: hi, type: "u8[4:7]", unit: V, senml: {name: high, unit: V}}
      - {name: lo, type: "u8[0:3]", consume: 1, unit: A}
    - {name: _internal, type: u8}
    - match:
        field: $kind
        cases:
          1:
          - {name: case_temp, type: u8, unit: Cel}
    - {name: flags, type: u8}
    - flagged:
        field: flags
        groups:
        - bit: 0
          fields:
          - {name: fv, type: u8, unit: lx}
    - name: env
      type: object
      fields:
      - {name: t, type: u8, unit: Cel}
      - name: probe
        type: object
        fields:
        - {name: depth, type: u8, unit: m}
      - {name: p, type: u8, senml: {name: pressure, unit: Pa}}
    - {name: missing, type: u8, lookup: {9: nine}}
    - tlv:
        tag_size: 1
        cases:
          3:
          - {name: tlv_rh, type: u8, unit: "%RH", senml: {name: rh}}
"""

PLACEMENTS_PAYLOAD = "0152ff14010715026401032a"


def senml(schema_text, payload, fport=None, drop_senml=False, decode_port=None):
    """`fport` is passed to the SenML formatter; `decode_port` (default: the same) to the
    decode, so a port's output can be formatted without naming the port."""
    schema = yaml.safe_load(schema_text)
    if drop_senml:
        for field in schema["fields"]:
            field.pop("senml", None)
    interpreter = SchemaInterpreter(schema)
    port = decode_port if decode_port is not None else fport
    result = interpreter.decode(bytes.fromhex(payload), fPort=port)
    assert result.success, result.errors
    return interpreter.get_semantic_output(result.data, "senml", fPort=fport)


def test_clause_7_example_is_named_by_its_senml_block():
    records = senml(ENVIRONMENTAL, "00FF820CCC")
    assert [(r["n"], r["u"], r["v"]) for r in records] == [
        ("temp", "Cel", 25.5),
        ("rh", "%RH", 65),
        ("vbat", "V", 3.276),
    ]


def test_without_the_block_the_reported_name_and_unit_are_used():
    records = senml(ENVIRONMENTAL, "00FF820CCC", drop_senml=True)
    # `°C` is not a registered SenML unit, so that record has no `u` (PS-488,
    # CR-2026-094); test_cr_2026_094_senml_units.py covers the warning.
    assert [(r["n"], r.get("u")) for r in records] == [
        ("temperature", None),
        ("humidity", "%RH"),
        ("battery_voltage", "V"),
    ]


@pytest.mark.parametrize("fport", [1, None])
def test_every_reported_field_has_a_record_wherever_declared(fport):
    # Without the port, every entry's fields are searched.
    records = senml(PLACEMENTS, PLACEMENTS_PAYLOAD, fport=fport, decode_port=1)
    assert [(r["n"], r.get("u"), r["v"]) for r in records] == [
        ("kind", None, 1),
        ("high", "V", 5),  # byte_group, senml.name and senml.unit
        ("lo", "A", 2),  # byte_group, `unit`
        ("case_temp", "Cel", 20),  # match case
        ("flags", None, 1),
        ("fv", "lx", 7),  # flagged group
        ("env/t", "Cel", 21),  # object member (PS-479)
        ("env/probe/depth", "m", 2),  # at every level
        ("pressure", "Pa", 100),  # an object member naming itself
        ("rh", "%RH", 42),  # tlv case, merged; senml.name, `unit`
    ]


def test_unreported_fields_have_no_record():
    names = [r["n"] for r in senml(PLACEMENTS, PLACEMENTS_PAYLOAD, fport=1)]
    assert "_internal" not in names  # internal (PS-432)
    assert "missing" not in names  # omitted by an unmapped lookup (PS-269)
