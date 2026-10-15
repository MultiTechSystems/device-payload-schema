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

Measured state, and where the breakdown now lives:

    python tools/encode-round-trip.py

CR-2026-023 stopped this docstring carrying a hand-maintained table. It listed
"1129 of 1191" with a four-point analysis of the residue, and both went stale as vectors
were added - by the time it was replaced the corpus was 1237 and the shape counts were
wrong in every row. The tool classifies every difference instead, and
`tests/test_cr_2026_023_encode_round_trip.py` asserts that none of them is unexplained,
which is the property worth holding: a new encoder defect appears as an unexplained row
rather than as a number that was already large.

The residue is dominated by information the decode does not carry - undescribed bits in a
`bitfield_string`, an internal `_` field, an unknown TLV tag's bytes, a `default` label
standing for every unmapped value (PS-269), a `sqrt` that cannot be inverted. One entry is
recoverable and nobody has done it: `name_from` builds its output key at run time, and the
encoders look the value up by the schema-declared name.

Raising the floors below is the measure of progress on encoding.
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
# +8: CR-2026-104 lookup label fixtures.
# +13: the TS013-defect fixtures - bitfield-string-wide, repeat-member-shares-name,
# ascii-nul-padding, digit-leading-name, wide-bit-range, wide-group,
# object-unnamed-skip, repeat-count-zero-width (tlv-tag-bit-range does not round-trip).
# +3: byte-group-bool.
# +3: tlv-case-field-list.
# +2: boolean-literal (CR-2026-107).
# +8: name-from-rendering (CR-2026-112), all plain fixed.
# +6: the registry divergence fixtures, all plain fixed (tag-component-ref does not
# round-trip: its cases match a range of tags).
# +5: repeat-element-named, -unnamed, -object, repeat-index-base and the null fixture's
# vector with no null (CR-2026-111), all repeat. Its two null vectors are refused.
# +2: guard-set (CR-2026-118), plain fixed. The other three CR-2026-118 fixtures are
# refused (PS-542, PS-387).
# +1: tlv-repeat-merge's object_members_combine (CR-2026-110), tlv. The other
# tlv-repeat-* vectors are lossy by design (last) or ambiguous (which tag carried an
# element is not in the output).
# +3: CR-2026-125 writes a collected array per element under the first declaring case
# and a shared merged member once: tlv-repeat-merge number_keeps_collecting,
# tlv-repeat-collect-first-case and tlv-repeat-merge-shared-member, all tlv.
# +2: CR-2026-128, tlv-repeat-merge-shared-last and tlv-repeat-object-collect (each
# collected object now finds the case whose members it supplies), both tlv.
# +2: guard-set's empty `in` and `not_in` vectors (CR-2026-124), plain fixed.
# +4: match-u32-inline-discriminator.
# +4: names-mangling-alike, plain fixed.
# +2: CR-2026-127 guard-boolean-operand, plain fixed.
# +6: repeat-element-null-sentinel, -internal-name, repeat-identity-sibling (2) and
# repeat-count-computed (CR-2026-121, CR-2026-123), plus the null fixture's
# reserved-value vector, whose null now writes the sentinel (PS-544); all repeat.
# +6: lookup-reference-binding (CR-2026-109, CR-2026-132), match; the miss, default
# and unnamed-enum vectors carry no value to write back.
# +4: lookup-reference-binding vectors added for the mutation gate, match.
# (merged with master's +6 tlv-repeat/mutation-gate vectors)
# +4: ascii-latin1 (CR-2026-114), all plain fixed: a byte above 0x7F re-encodes to itself.
# +3: byte-group-width (CR-2026-131), byte_group.
# +3: CR-2026-134 (PS-551) derives what the input fixes. internal-discriminator's two
# vectors (match) derive `_kind` from the case whose fields are given, and
# flagged-group-field-list's inner_groups (flagged) derives both masks and now encodes a
# flagged block nested in a group, which was passed over as nameless.
# +1: the merge of the two. lookup-reference-binding's an_unnamed_enum_code_is_omitted_
# but_bound (match) reports no discriminator, so neither side re-encoded it alone; with
# PS-551 the code is derived from the case whose fields are given.
# +1: CR-2026-135's encoder-rounding fixture, its decode vector (plain fixed). Its two
# encode vectors are no round trip, and arithmetic-worked-examples' omits a field.
# +264: CR-2026-136 lays bit ranges and bools out as a decoder reads them. A range that
# does not consume shares its byte with the next field - also across a construct's end,
# and inside a flagged group - instead of writing it twice, so the peeks in vobo (150),
# dnt-lw-wsci (75) and milesight ct10x/ct30x/gs301/ws515 (39) round-trip; all tlv.
# -26 deliberately, merging CR-2026-136 with CR-2026-134: 2132 combined (2079 + 1841 -
# 1814) less 26 tlv vectors of milesight ct10x (12), ct30x (6), gs301 (6) and ws515 (2)
# that CR-2026-136 round-tripped by leaving a status peek's bits zero. The peek is a
# `u16[0:15]` whose lookup misses on an ordinary reading, so the decode omits it
# (PS-269), and PS-551 makes it a required value the input does not give: the encode is
# rejected naming the status. Not derivable either - a peek's bytes are written by the
# field after it (_is_peek).
# +4: mask-reference-binding (flagged): a `$mask` reference resolves to its binding.
# +16, CR-2026-141 (PS-551 names a covered peek derivable): the 26 milesight vectors
# come back - an absent peek whose bits the reading writes is derived and writes none -
# less 10 that round-tripped only because ORing a supplied peek into the bytes after it
# happened to reproduce them, and are now rejected as a disagreement: vobo's
# mutant_p32_03..07 and mutant_p112_03..07, whose lookup gives one label to two codes,
# so the label reverses to the other. All tlv.
# +5: match-fractional-discriminator (match): a fraction matches no single key.
# -4 deliberately, PS-169 (CR-2026-146): a label several codes share is rejected. vobo's
# mutant_p32_01, mutant_p112_01, two_analog_xp_all and two_analog_xx_ain3 round-tripped
# only because the first code found was the right one. All tlv.
# +2: tlv-name-object-and-scalar (tlv): a mapping is no value for a scalar case, nor a
# number for an object one.
# +12: the four tlv encode-gap fixtures (tlv-object-with-inner-branch,
# tlv-case-of-one-inline-branch, tlv-float-reading, tlv-scaled-wide-reading), all tlv.
# +6: bf16 (CR-2026-149), all plain fixed; bf16-specials re-encodes nothing (an absent field).
# +4: ascii-numeral (CR-2026-150), all plain fixed.
# +4: wide-peek-selector, wide-peek-selector-u64 (match); the skipped-four vector does not round-trip.
# +6: bf16 (CR-2026-149), all plain fixed; its three infinity and NaN vectors re-encode nothing (an absent field).
# +3: match-discriminator-from-inner-case (CR-2026-151), all match.
# +1: negative-zero (CR-2026-154), plain fixed; its first vector and bf16's negative_zero re-encode
# as zero words and are filed `negative-zero` by tools/encode-round-trip.py.
# +11, no silent empty encode (PS-442): the fixtures length-selects-layout (4) and
# literal-selects-branch (2), match, and tag-entry-constants (4), tlv, and
# tlv-case-field-list's literal_only, tlv - an entry reporting only a constant, which
# encoded to no bytes with no error.
FLOOR_TOTAL = 2273  # +11: whole-number-read-in-place (CR-2026-182); +3: bitfield-string-width-split (CR-2026-183), plain fixed; +2: meta-semantic (CR-2026-179); +10: bytes-format-forms (all plain fixed; every vector round-trips); +11: byte-group-member-var (tlv); +4: bitfield-string-field-endian (two fixtures); +4: expected-error (CR-2026-167: its four successful decodes round-trip; the rejections are skipped); +1: repeat-sampled (CR-2026-156); earlier moves follow

#: Per-shape floors, so a regression in a shape that works cannot hide behind the 948
#: TLV vectors that do not. A shape absent here has no working round-trip to protect.
FLOOR_BY_SHAPE = {
    # 1283 -> 1281 deliberately (CR-2026-067, PS-434): two vobo vectors round-tripped
    # only because their internal byte halves were all zero, which the encoder wrote
    # for an internal field with no value. It now reports such a field instead.
    "tlv": 1589,  # +11: byte-group-member-var; +4: expected-error (CR-2026-167); +5: no silent empty (see FLOOR_TOTAL); +12: the tlv encode-gap fixtures; +2: tlv-name-object-and-scalar; -4: PS-169 (see FLOOR_TOTAL); +16: CR-2026-141 (see FLOOR_TOTAL); -26: CR-2026-136 with CR-2026-134 (see FLOOR_TOTAL); +264: CR-2026-136 (see FLOOR_TOTAL); +2: CR-2026-128; +3: CR-2026-125 fixtures; +1: tlv-repeat-merge (CR-2026-110)
    "flagged": 198,  # +1: CR-2026-134; +4: mask-reference-binding (see FLOOR_TOTAL)
    "plain fixed": 246,  # +3: definitions-field-and-lookup (CR-2026-168), plain fixed; +3: bitfield-string-width-split (CR-2026-183), plain fixed; +6: checksum-fields (CR-2026-174); +10: bytes-format-forms (all plain fixed; every vector round-trips); +22: minifloat-reference-binding (11 types x 2; plain fixed); +4: bitfield-string-field-endian (two fixtures); +1: negative-zero (CR-2026-154); +4: ascii-numeral (CR-2026-150); +6: bf16 (CR-2026-149); +1: CR-2026-135 (see FLOOR_TOTAL); +4: ascii-latin1 (CR-2026-114)
    # +1: CR-2026-132 enum port; +5: lookup-reference-binding (CR-2026-109);
    # +4: match-u32-inline-discriminator; +2: CR-2026-134; +1: both together (see
    # FLOOR_TOTAL); +5: match-fractional-discriminator
    # FLOOR_TOTAL); +5: match-fractional-discriminator; +3:
    # match-discriminator-from-inner-case (CR-2026-151); +6: no silent empty (see
    # FLOOR_TOTAL)
    "match": 184,  # +11: whole-number-read-in-place (CR-2026-182); +13: match-fractional-range-keys (match; every vector round-trips); +4: wide-peek-selector, wide-peek-selector-u64 (match); the skipped-four vector does not round-trip
    "byte_group": 28,  # +3: byte-group-width (CR-2026-131)
    # 6 -> 5 with plain fixed 66 -> 67: a bucket move. Composed library schemas now
    # carry only the definitions they reach, so one no longer has a `repeat` in an
    # unreferenced definition for the classifier to find.
    # 19 -> 25: CR-2026-121 and CR-2026-123 (see FLOOR_TOTAL).
    "repeat": 28,  # +2: meta-semantic (CR-2026-179); +1: repeat-sampled (CR-2026-156)
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
            if is_encode_vector(vector) or "expected_error" in vector:
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


# Under pytest-xdist a module fixture is built once per worker that runs one of its tests,
# so without this every worker would rebuild the corpus round trip (about 50 seconds). `make pytest` passes
# `--dist loadgroup`, which keeps the module on one worker and builds it once.
pytestmark = pytest.mark.xdist_group("encode-round-trip")


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
    # The power-on frames carry a constant label the vendor emits whatever the byte
    # says (`readDeviceStatus(1)`), modelled as a lookup `default`; a default stands
    # for every unlisted value, so the byte cannot be recovered (PS-269).
    lossy = {"ch255_type9_midscale", "ch255_type9_hex_letters",
             "power_on_reset_event", "power_on_device_status"}
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
