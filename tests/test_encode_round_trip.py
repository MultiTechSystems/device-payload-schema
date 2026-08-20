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

| shape       | round-trips | length differs | bytes differ | errors |
|-------------|-------------|----------------|--------------|--------|
| tlv         |         899 |              7 |           43 |      0 |
| flagged     |         121 |              1 |            0 |      0 |
| plain fixed |          54 |              3 |            3 |      3 |
| match       |          34 |              0 |            0 |      0 |
| byte_group  |          17 |              0 |            2 |      0 |
| repeat      |           4 |              0 |            0 |      0 |

1129 of 1191, from 120 when this began. **Every construct now has an encoder**, so what
is left is information the decoded output does not carry, not a missing implementation:

1. **37 TLV, lossy by the vendor's design.** A `bitfield_string` hardware version keeps
   only the high nibble of byte 1, so bits 8-11 are discarded by the format itself.
2. **13 TLV**: a channel repeated within one payload collapses to a single dict key while
   decoding, so which occurrence a value came from is gone.
3. **3 errors, each a deliberate refusal** rather than a failure. Two are a `default`
   label, which stands for every value a table does not list (PS-269), so no original can
   be recovered; the third is `sqrt`, which cannot be inverted. Reported against the field
   rather than guessed at.
4. **9 in plain fixed, flagged and byte_group**: `skip` padding whose bytes the output
   does not carry, `name_from` whose key is built at run time, and two vicki vectors.

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
from validate_schema import is_encode_vector  # noqa: E402

DEVICES = REPO_ROOT / "schemas" / "devices"

#: Exact round-trips required overall. Raise as encoding improves.
FLOOR_TOTAL = 1131

#: Per-shape floors, so a regression in a shape that works cannot hide behind the 948
#: TLV vectors that do not. A shape absent here has no working round-trip to protect.
FLOOR_BY_SHAPE = {
    "tlv": 900,
    "flagged": 121,
    "plain fixed": 55,
    "match": 34,
    "byte_group": 17,
    "repeat": 4,
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
            if is_encode_vector(vector):
                continue
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

def test_tlv_payload_round_trips():
    """A TLV payload must come back byte-identical, channels in their original order.

    Decoding flattens every channel into one dict, so encoding recovers the channels
    from which field names are present and orders them by where those names appear in
    the dict - which for output straight from `decode` is the order they were read.
    Without that the channels come back rearranged and the payload differs while every
    value in it is right.
    """
    schema = yaml.safe_load(
        (REPO_ROOT / "schemas/devices/milesight/am102.yaml").read_text(encoding="utf-8")
    )
    # `ch255_type9_midscale` carries a `version_string`, which is lossy in the decoder
    # rather than the encoder: bytes 11 11 decode to "v11.1", a digit short, so nothing
    # can reconstruct the second byte. That is a separate defect from TLV framing.
    # Both hardware-version vectors: the vendor's format keeps only the high nibble of
    # byte 1, so bits 8-11 are discarded and no encoder can put them back.
    lossy = {"ch255_type9_midscale", "ch255_type9_hex_letters"}
    checked = 0
    for vector in schema["test_vectors"]:
        if is_encode_vector(vector):
            continue
        if vector["name"] in lossy:
            continue
        payload = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
        decoded = SchemaInterpreter(schema).decode(payload)
        assert not decoded.errors, decoded.errors
        encoded = SchemaInterpreter(schema).encode(dict(decoded.data))
        assert not encoded.errors, encoded.errors
        assert bytes(encoded.payload) == payload, (
            f"{vector['name']}: {bytes(encoded.payload).hex()} != {payload.hex()}"
        )
        checked += 1
    assert checked >= 14, checked

    # The multi-channel vector is the one that tests framing and ordering together:
    # three channels, each with its own composite tag, in payload order.
    multi = next(v for v in schema["test_vectors"] if v["name"] == "vendor_reference")
    payload = bytes.fromhex(str(multi["payload"]).replace(" ", ""))
    decoded = SchemaInterpreter(schema).decode(payload)
    assert bytes(SchemaInterpreter(schema).encode(dict(decoded.data)).payload) == payload


def test_lookup_label_encodes_back_to_its_integer():
    """A label must map back through the lookup, not reach int() as a string.

    `_reverse_modifiers` guarded on the value being numeric *before* reversing the
    lookup, so reverse_lookup was dead code for every label a lookup had produced. The
    label then hit int() and 69 corpus vectors failed to encode with
    "invalid literal for int() with base 10: 'Class A'".
    """
    schema = yaml.safe_load(
        "name: t\nendian: big\nfields:\n"
        "  - name: lorawan_class\n    type: u8\n"
        "    lookup: [\"Class A\", \"Class B\", \"Class C\"]\n"
    )
    for raw, label in (("00", "Class A"), ("01", "Class B"), ("02", "Class C")):
        decoded = SchemaInterpreter(schema).decode(bytes.fromhex(raw))
        assert decoded.data["lorawan_class"] == label
        encoded = SchemaInterpreter(schema).encode(dict(decoded.data))
        assert not encoded.errors, encoded.errors
        assert bytes(encoded.payload) == bytes.fromhex(raw)


def test_unencodable_tlv_tag_is_reported():
    """A wildcard case matches a range of tags, so encoding cannot choose one (PS-270)."""
    schema = yaml.safe_load(
        "name: t\nendian: big\nfields:\n"
        "  - tlv:\n"
        "      tag_fields:\n"
        "        - {name: ch, type: u8}\n"
        "        - {name: kind, type: u8}\n"
        "      tag_key: [ch, kind]\n"
        "      cases:\n"
        "        \"[1, *]\":\n"
        "          - {name: reading, type: u8}\n"
    )
    encoded = SchemaInterpreter(schema).encode({"reading": 7})
    assert encoded.errors, "expected an error rather than an invented tag"
    assert "cannot choose one" in encoded.errors[0]

def test_match_construct_round_trips():
    """rbs30x: a `byte_group` header, an event type, and a `match` on it.

    Two things had to be right together. The discriminator comes from `field: $evt`,
    whose variable name is not the field's name (`name: event_type, var: evt`), so
    encoding has to find the field that declared the variable to read its value. And
    because that field is encoded by the main loop, the match must *not* write the
    discriminator again - only an inline `length:` match owns those bytes.
    """
    schema = yaml.safe_load(
        (REPO_ROOT / "schemas/devices/radio-bridge/rbs30x.yaml").read_text(encoding="utf-8")
    )
    exact = 0
    for vector in schema["test_vectors"]:
        if is_encode_vector(vector):
            continue
        payload = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
        decoded = SchemaInterpreter(schema).decode(payload)
        if decoded.errors:
            continue
        encoded = SchemaInterpreter(schema).encode(dict(decoded.data))
        assert not encoded.errors, f"{vector['name']}: {encoded.errors}"
        assert bytes(encoded.payload) == payload, (
            f"{vector['name']}: {bytes(encoded.payload).hex()} != {payload.hex()}"
        )
        exact += 1
    assert exact >= 29, exact


def test_byte_group_bits_are_packed():
    """A shared byte's ranges pack back into it.

    Encoding had no byte_group case at all: the construct fell through to the plain
    field path, which found no name, encoded a default of 0, and emitted one zero byte -
    right length, wrong bits, no error. rbs30x's first byte is `u8[4:7]` over `u8[0:3]`.
    """
    schema = yaml.safe_load(
        "name: t\nendian: big\nfields:\n"
        "  - byte_group:\n      size: 1\n      fields:\n"
        "        - {name: high, type: 'u8[4:7]'}\n"
        "        - {name: low, type: 'u8[0:3]'}\n"
    )
    for raw in ("10", "a5", "0f", "f0"):
        payload = bytes.fromhex(raw)
        decoded = SchemaInterpreter(schema).decode(payload)
        assert not decoded.errors, decoded.errors
        encoded = SchemaInterpreter(schema).encode(dict(decoded.data))
        assert not encoded.errors, encoded.errors
        assert bytes(encoded.payload) == payload, (
            f"{raw}: {decoded.data} re-encoded to {bytes(encoded.payload).hex()}"
        )


def test_remaining_length_encodes_the_value_it_has():
    """`length: remaining` has no fixed count when encoding (PS-014).

    Slicing with the word itself raised "slice indices must be integers", which is how
    radio-bridge's stored downlink failed to re-encode.
    """
    schema = yaml.safe_load(
        "name: t\nendian: big\nfields:\n"
        "  - name: head\n    type: u8\n"
        "  - name: tail\n    type: bytes\n    length: remaining\n"
    )
    for raw in ("10010009", "10", "1042"):
        payload = bytes.fromhex(raw)
        decoded = SchemaInterpreter(schema).decode(payload)
        encoded = SchemaInterpreter(schema).encode(dict(decoded.data))
        assert not encoded.errors, encoded.errors
        assert bytes(encoded.payload) == payload

def test_ambiguous_tlv_case_is_resolved_by_the_arithmetic():
    """Two cases can define one field name; only one of them wrote the bytes.

    am308 has `tvoc` under [8, 125] with `div: 100` and under [8, 230] raw. Emitting
    every case whose fields are present produced both channels, so the payload grew.
    The value says which: 43.69 came from 4369 through the divide exactly, while the raw
    case would need it rounded - and 4369 raw cannot have come from the divide case,
    because 436900 does not fit the u16 it would have to be written to.
    """
    schema = yaml.safe_load(
        (REPO_ROOT / "schemas/devices/milesight/am308.yaml").read_text(encoding="utf-8")
    )
    for name in ("ch8_type125_midscale", "ch8_type230_midscale"):
        vector = next(v for v in schema["test_vectors"] if v["name"] == name)
        payload = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
        decoded = SchemaInterpreter(schema).decode(payload, fPort=85)
        encoded = SchemaInterpreter(schema).encode(dict(decoded.data), fPort=85)
        assert not encoded.errors, f"{name}: {encoded.errors}"
        assert bytes(encoded.payload) == payload, (
            f"{name}: {bytes(encoded.payload).hex()} != {payload.hex()}"
        )


def test_ref_is_spliced_when_encoding():
    """Decoding splices a `$ref` definition's fields in place; encoding must too.

    It did not, so the whole referenced header collapsed to one zero byte and
    ref-header.yaml re-encoded 01020304 as 000304.
    """
    schema = yaml.safe_load(
        (REPO_ROOT / "schemas/devices/_language-conformance/ref-header.yaml")
        .read_text(encoding="utf-8")
    )
    for vector in schema["test_vectors"]:
        if is_encode_vector(vector):
            continue
        payload = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
        decoded = SchemaInterpreter(schema).decode(payload)
        encoded = SchemaInterpreter(schema).encode(dict(decoded.data))
        assert not encoded.errors, encoded.errors
        assert bytes(encoded.payload) == payload

def test_repeat_records_round_trip():
    """A `repeat` writes its records back to back, and nothing else.

    The framing costs no bytes here: `count: $n` and `byte_length: $len` name a field
    earlier in the list that the main loop encodes on its own. Encoding reached
    "Cannot encode type: repeat" before this, so every record was dropped -
    repeat-count.yaml re-encoded 020a14 as 02, keeping only the count.
    """
    for name in ("repeat-count", "repeat-byte-length"):
        schema = yaml.safe_load(
            (REPO_ROOT / f"schemas/devices/_language-conformance/{name}.yaml")
            .read_text(encoding="utf-8")
        )
        for vector in schema["test_vectors"]:
            if is_encode_vector(vector):
                continue
            payload = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            decoded = SchemaInterpreter(schema).decode(payload)
            assert isinstance(decoded.data["items"], list), decoded.data
            encoded = SchemaInterpreter(schema).encode(dict(decoded.data))
            assert not encoded.errors, f"{name}: {encoded.errors}"
            assert bytes(encoded.payload) == payload, (
                f"{name}: {bytes(encoded.payload).hex()} != {payload.hex()}"
            )


def test_unrecoverable_default_label_is_refused_clearly():
    """A `default` label matches any unmapped value, so there is no original to write.

    Reported as that, rather than as int()'s "invalid literal for int() with base 10:
    'unknown'", which said nothing about why the value could not be encoded.
    """
    schema = yaml.safe_load(
        "name: t\nendian: big\nfields:\n"
        "  - name: mode\n    type: u8\n"
        # `on` must be quoted: YAML parses it as the boolean True, which is why the
        # corpus writes ["off", "on"] rather than [off, on].
        "    lookup: {1: \"on\", default: unknown}\n"
    )
    encoded = SchemaInterpreter(schema).encode({"mode": "unknown"})
    assert encoded.errors
    assert "default" in encoded.errors[0] and "cannot be recovered" in encoded.errors[0]
    # A label the table does list still encodes.
    assert bytes(SchemaInterpreter(schema).encode({"mode": "on"}).payload) == b"\x01"
