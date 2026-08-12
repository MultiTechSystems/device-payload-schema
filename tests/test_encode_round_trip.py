"""Encoding, measured against the decode corpus.

Every one of the 1190 corpus vectors tests decoding. Nothing tested encoding, which is
the MCU tier's entire job - packing an uplink, building a downlink command - so an
encoder could be wrong in any way at all and the suite stayed green. Java and C# have no
encoder to test; `src/test_encoder.c` is in no build target. This is the Python
reference encoder held to the one assertion the corpus can make for free:

    encode(decode(payload)) == payload

That does not hold everywhere, and cannot. A `skip` field's bytes are not recoverable
from output that omits them; a rounding stage discards precision; an unmapped `lookup`
value is dropped by design (PS-269). So the floors below are ratchets, not a target of
1190: they pin what round-trips today, per schema shape, so a regression in a working
shape cannot hide behind the mass of a broken one.

Measured state when this was written, and the reason for the shape breakdown:

| shape       | round-trips | length differs | bytes differ |
|-------------|-------------|----------------|--------------|
| tlv         |           0 |            948 |            0 |
| flagged     |         121 |              1 |            0 |
| plain fixed |          53 |              7 |            3 |
| match       |           0 |             23 |           11 |
| byte_group  |           2 |              1 |           16 |
| repeat      |           1 |              3 |            0 |

Three gaps, in order of size:

1. **TLV encoding is unimplemented.** 948 vectors, none round-tripping: the encoder
   emits the channel values without rebuilding the tag/length framing around them.
2. **`match` encoding.** 34 vectors; the discriminator and the selected case's bytes are
   not reassembled.
3. **`byte_group` bit packing.** 16 vectors produce the right length and the wrong bits.

Raising any of those floors is the measure of progress on encoding.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

DEVICES = REPO_ROOT / "schemas" / "devices"

#: Exact round-trips required overall. Raise as encoding improves.
FLOOR_TOTAL = 177

#: Per-shape floors, so a regression in a shape that works cannot hide behind the 948
#: TLV vectors that do not. A shape absent here has no working round-trip to protect.
FLOOR_BY_SHAPE = {
    "flagged": 121,
    "plain fixed": 53,
}

#: Encoding must never raise. It used to, 26 times: "Cannot encode type: number" for any
#: schema with a computed field, and "can't convert negative int to unsigned" wherever a
#: transform-bearing wire field decoded below zero - a `u16` with
#: `transform: [{add: -32768}, {div: 10}]` decodes to -3276.8, and nothing reversed it.
ALLOWED_RAISES = 0


def schema_shape(schema: dict) -> str:
    """The construct that dominates a schema's layout, for the breakdown above."""
    text = json.dumps(schema)
    for key in ("tlv", "match", "repeat", "flagged", "byte_group"):
        if f'"{key}"' in text:
            return key
    return "plain fixed"


def round_trip_results():
    """Decode then re-encode every corpus vector, tallied by shape and verdict."""
    tally: collections.Counter = collections.Counter()
    raises: list[str] = []
    for path in sorted(DEVICES.rglob("*.yaml")):
        try:
            schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(schema, dict):
            continue
        shape = schema_shape(schema)
        for vector in schema.get("test_vectors") or []:
            raw = str(vector.get("payload", "")).replace(" ", "")
            if not raw:
                continue
            try:
                payload = bytes.fromhex(raw)
            except ValueError:
                continue
            fport = vector.get("fPort", vector.get("fport"))
            try:
                decoded = SchemaInterpreter(schema).decode(payload, fPort=fport)
            except Exception:
                continue
            if decoded.errors:
                continue
            try:
                encoded = SchemaInterpreter(schema).encode(dict(decoded.data), fPort=fport)
            except Exception as exc:
                raises.append(f"{path.name}/{vector.get('name')}: {type(exc).__name__}: {exc}")
                continue
            out = bytes(encoded.payload or b"")
            if encoded.errors:
                verdict = "reported errors"
            elif out == payload:
                verdict = "round-trips"
            elif len(out) != len(payload):
                verdict = "length differs"
            else:
                verdict = "bytes differ"
            tally[(shape, verdict)] += 1
    return tally, raises


@pytest.fixture(scope="module")
def results():
    return round_trip_results()


def test_encoding_never_raises(results):
    """A field the encoder cannot express belongs in `result.errors`, not in a traceback.

    A flagged group used to propagate: one unencodable field killed the whole call,
    where the per-field path beside it recorded the problem and carried on.
    """
    _, raises = results
    assert len(raises) <= ALLOWED_RAISES, raises[:6]


def test_total_round_trips_do_not_regress(results):
    tally, _ = results
    exact = sum(n for (_, verdict), n in tally.items() if verdict == "round-trips")
    assert exact >= FLOOR_TOTAL, (
        f"{exact} vectors round-trip, floor is {FLOOR_TOTAL}. "
        "Encoding got worse, or a schema that used to round-trip stopped."
    )


@pytest.mark.parametrize("shape,floor", sorted(FLOOR_BY_SHAPE.items()))
def test_per_shape_round_trips_do_not_regress(results, shape, floor):
    tally, _ = results
    exact = tally[(shape, "round-trips")]
    assert exact >= floor, (
        f"{shape}: {exact} round-trip, floor is {floor}. A working layout regressed."
    )


def test_transform_bearing_field_round_trips():
    """The defect that motivated this file, pinned as a unit.

    `u16` with `transform: [{add: -32768}, {div: 10}]`: decoding gives -3276.8, and
    encoding has to run the chain backwards to recover the original bytes.
    """
    schema = yaml.safe_load(
        "name: t\nendian: big\nfields:\n"
        "  - name: radiation\n    type: u16\n"
        "    transform:\n      - add: -32768\n      - div: 10\n"
    )
    for raw in ("0000", "8000", "ffff"):
        payload = bytes.fromhex(raw)
        decoded = SchemaInterpreter(schema).decode(payload)
        assert not decoded.errors, decoded.errors
        encoded = SchemaInterpreter(schema).encode(dict(decoded.data))
        assert not encoded.errors, encoded.errors
        assert bytes(encoded.payload) == payload, (
            f"{raw}: decoded {decoded.data['radiation']} re-encoded to "
            f"{bytes(encoded.payload).hex()}"
        )


def test_derived_field_contributes_no_bytes():
    """A computed field is not on the wire, so encoding must skip it.

    It used to reach "Cannot encode type: number" unless it used the deprecated
    `formula` spelling, so every schema with `ref`/`compute`/`polynomial` failed here.
    """
    schema = yaml.safe_load(
        "name: t\nendian: big\nfields:\n"
        "  - name: raw\n    type: u8\n    var: raw\n"
        "  - name: doubled\n    type: number\n"
        "    compute:\n      op: mul\n      a: $raw\n      b: 2\n"
    )
    decoded = SchemaInterpreter(schema).decode(bytes.fromhex("05"))
    assert decoded.data["doubled"] == 10
    encoded = SchemaInterpreter(schema).encode(dict(decoded.data))
    assert not encoded.errors, encoded.errors
    assert bytes(encoded.payload) == bytes.fromhex("05")


def test_non_invertible_transform_is_reported_not_guessed():
    """`sqrt` cannot be undone, so the field is reported rather than mis-encoded."""
    schema = yaml.safe_load(
        "name: t\nendian: big\nfields:\n"
        "  - name: v\n    type: u16\n    transform:\n      - sqrt: true\n"
    )
    encoded = SchemaInterpreter(schema).encode({"v": 4})
    assert encoded.errors, "expected an error for an irreversible transform"
    assert "sqrt" in encoded.errors[0]
