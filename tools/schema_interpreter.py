#!/usr/bin/env python3
"""
schema_interpreter.py - Runtime Schema Interpreter for Payload Decoding

Decodes LoRaWAN payloads using Payload Schema definitions at runtime.
This is the reference implementation of the Payload Schema decoder.

Usage:
    from schema_interpreter import SchemaInterpreter
    
    interpreter = SchemaInterpreter(schema)
    result = interpreter.decode(payload_bytes)
    
    # Or encode
    payload = interpreter.encode(data_dict)
"""

import calendar
import struct
import re
import json
import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Dict, Any, List, Optional, Tuple, Union
from enum import Enum

class Endian(Enum):
    BIG = 'big'
    LITTLE = 'little'


@dataclass
class DecodeResult:
    """Result of decoding a payload."""
    data: Dict[str, Any]
    bytes_consumed: int
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    quality: Dict[str, str] = field(default_factory=dict)
    
    @property
    def success(self) -> bool:
        return len(self.errors) == 0


@dataclass
class EncodeResult:
    """Result of encoding data to payload."""
    payload: bytes
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    #: The FPort the encode used (PS-581): the port entry chosen for a schema with
    #: `ports`, else the caller's, else the document's `fPort`; None where there is none.
    fport: Optional[int] = None
    
    @property
    def success(self) -> bool:
        return len(self.errors) == 0


#: String types whose name contains a colon, so that the bitfield parser does not
#: mistake them for a bit range such as ``u8:3``.
_COLON_STRING_TYPES = frozenset({'hex:upper'})

#: Sentinel meaning "this field produced no value and is omitted from the output".
OMITTED = object()


#: Directions a message can be travelling, as supplied to ``decode`` (PS-290).
MESSAGE_DIRECTIONS = frozenset({'uplink', 'downlink'})

#: Values `direction` may take on a schema or a port entry (PS-287). An entry declaring
#: `both`, or declaring nothing, accepts either direction. `bidirectional` appeared in a
#: clause 5 example and is not one of them; CR-2026-010 withdrew that spelling so that a
#: schema carrying it surfaces rather than being read as `both`.
DECLARED_DIRECTIONS = frozenset({'uplink', 'downlink', 'both'})

#: The computed field types: they read no bytes and write none. `integer` is `number`
#: declaring an integer result (PS-283); the encoder knew only `number`, so a schema
#: using `integer` decoded and then failed with "Cannot encode type: integer".
COMPUTED_TYPES = ('number', 'integer')


#: Byte width and signedness of every integer type spelling, including aliases. Lifted
#: out of _encode_field so the TLV case ranking can ask whether a value fits a field
#: before believing that field wrote it.
INTEGER_TYPE_INFO: Dict[str, Tuple[int, bool]] = {
    'u8': (1, False), 'uint8': (1, False),
    'u16': (2, False), 'uint16': (2, False),
    'u24': (3, False), 'uint24': (3, False),
    'u32': (4, False), 'uint32': (4, False),
    'u64': (8, False), 'uint64': (8, False),
    # Word-ordered 32-bit (PS-271): four bytes wide, and the bytes are laid out by the
    # encoder rather than by the width alone.
    'u32le16': (4, False), 's32le16': (4, True),
    'u32be16le': (4, False), 's32be16le': (4, True),
    's8': (1, True), 'i8': (1, True), 'int8': (1, True),
    's16': (2, True), 'i16': (2, True), 'int16': (2, True),
    's24': (3, True), 'i24': (3, True), 'int24': (3, True),
    's32': (4, True), 'i32': (4, True), 'int32': (4, True),
    's64': (8, True), 'i64': (8, True), 'int64': (8, True),
}


#: The word-ordered 32-bit types: two 16-bit units, in an order the type fixes and no
#: `endian` setting varies (PS-272). `le16` is the low unit first, each unit big-endian
#: (PS-271, PS-362); `be16le` the high unit first, each unit little-endian (PS-363, the
#: fourth ordering, CR-2026-047).
WORD_ORDERED_TYPES = {
    'u32le16': ('le16', 'u'), 's32le16': ('le16', 's'), 'f32le16': ('le16', 'f'),
    'u32be16le': ('be16le', 'u'), 's32be16le': ('be16le', 's'), 'f32be16le': ('be16le', 'f'),
}


def read_word_ordered(field_type, data):
    """The value of a word-ordered type over four bytes (PS-271, PS-362, PS-363)."""
    layout, kind = WORD_ORDERED_TYPES[field_type]
    if layout == 'le16':
        word = int.from_bytes(data[0:2], 'big') | (int.from_bytes(data[2:4], 'big') << 16)
    else:
        word = (int.from_bytes(data[0:2], 'little') << 16) | int.from_bytes(data[2:4], 'little')
    if kind == 'f':
        return struct.unpack('>f', word.to_bytes(4, 'big'))[0]
    if kind == 's' and word >= 0x80000000:
        word -= 0x100000000
    return word


class IdentityTable:
    """Values by field definition, matched by identity and holding the definition.

    Keyed by `id()` alone, an entry outlived its definition: a repeat's element is a dict
    built for each decode of the repeat (`repeat_members`), and a later definition given
    the freed address inherited the entry - a failed guard's `else` could switch off
    another field's `valid_range`. The spec prototype's SIBLINGS table had the same defect
    (CR-2026-141). Each entry keeps its definition alive, so no address is reused while
    the entry stands, and is matched by identity besides."""

    def __init__(self):
        self._entries = {}

    def __setitem__(self, obj, value):
        self._entries[id(obj)] = (obj, value)

    def add(self, obj):
        self[obj] = True

    def __contains__(self, obj):
        entry = self._entries.get(id(obj))
        return entry is not None and entry[0] is obj

    def get(self, obj, default=None):
        entry = self._entries.get(id(obj))
        return entry[1] if entry is not None and entry[0] is obj else default

    def pop(self, obj, default=None):
        value = self.get(obj, default)
        if obj in self:
            del self._entries[id(obj)]
        return value

    def discard(self, obj):
        self.pop(obj)


def pointer_segment(key: Any) -> str:
    """One RFC 6901 reference token: `~` as `~0`, `/` as `~1` (PS-572)."""
    return str(key).replace('~', '~0').replace('/', '~1')


def pointer_map(document: Dict[str, Any]) -> IdentityTable:
    """PS-572: the RFC 6901 JSON Pointer of every mapping and list in the `$ref`-expanded
    schema document, by identity. The spec prototype's `pointer_map`; held as an
    IdentityTable, so an entry keeps its node alive and is matched by identity."""
    found = IdentityTable()

    def walk(node: Any, pointer: str) -> None:
        if isinstance(node, (dict, list)):
            if node not in found:
                found[node] = pointer
            items = node.items() if isinstance(node, dict) else enumerate(node)
            for key, value in items:
                walk(value, f"{pointer}/{pointer_segment(key)}")

    for key, value in document.items():
        if value is not None:
            walk(value, f"/{key}")
    return found


class DecodeTrace:
    """The decode record (PS-572), kept while decoding and followed while encoding (PS-573).

    The spec prototype's DecodeTrace. A location is the construct's JSON Pointer into the
    expanded schema with an occurrence index after each node that repeats at run time:
    `/repeat[i]` after a repeat field's own pointer, `tlv[k]` for a tlv entry in payload
    order, and `records/n[k]` for a record."""

    def __init__(self, pointers: IdentityTable, record: Optional[Dict[str, Any]] = None):
        self.pointers = pointers
        self.frames: List[tuple] = []           # (anchor pointer, kind, index), outermost first
        record = record or {}
        self.matches: Dict[str, Any] = dict(record.get('matches') or {})
        self.internal: Dict[str, Any] = dict(record.get('internal') or {})
        self.flagged: Dict[str, Any] = dict(record.get('flagged') or {})

    def push(self, node: Any, kind: str, index: int, suffix: str = '') -> bool:
        anchor = self.pointers.get(node)
        if anchor is None:
            return False
        self.frames.append((anchor + suffix, kind, index))
        return True

    def pop(self, pushed: bool) -> None:
        if pushed:
            self.frames.pop()

    def loc(self, node: Any) -> Optional[str]:
        pointer = self.pointers.get(node)
        if pointer is None:
            return None
        for anchor, kind, index in sorted(self.frames, key=lambda f: -len(f[0])):
            if pointer == anchor or pointer.startswith(anchor + '/'):
                rest = pointer[len(anchor):]
                pointer = (f"{anchor}/repeat[{index}]{rest}" if kind == 'repeat'
                           else f"{anchor}[{index}]{rest}")
        return pointer

    def lookup(self, table: str, node: Any) -> Any:
        """The record's entry for a construct at its current location, or _UNRECORDED."""
        where = self.loc(node)
        if where is None:
            return _UNRECORDED
        return getattr(self, table).get(where, _UNRECORDED)

    def as_dict(self) -> Dict[str, Any]:
        return {'matches': self.matches, 'internal': self.internal, 'flagged': self.flagged}


#: What `DecodeTrace.lookup` gives for a construct the record says nothing of.
_UNRECORDED = object()


def record_declared_fields(fields: Any, skip: Any = None) -> List[str]:
    """Every data field a field list can report, by name, looking through the constructs
    whose fields merge into the enclosing object (match, tlv, flagged, byte_group,
    records): the spec prototype's `declared_fields`, which PS-573's "a field that only
    another case declares" is checked against. A match's `default` list beside its cases is
    looked into too, where the prototype's is not: a name a default branch reports is
    declared by that branch (dingtek df702's `angle`, in a nested match's default, read as
    declared only by another case)."""
    found: List[str] = []

    def walk(items):
        for item in items or []:
            if not isinstance(item, dict) or item is skip:
                continue
            if 'type' in item:
                if item.get('name') and item['name'] not in found:
                    found.append(item['name'])
                walk(item.get('trailer'))
                continue
            spec = next((item[k] for k in ('match', 'tlv', 'flagged', 'byte_group',
                                           'records') if k in item), None)
            if isinstance(spec, list):
                for part in spec:
                    walk(part if isinstance(part, list) else [part])
            elif isinstance(spec, dict):
                walk(spec.get('fields'))
                for body in (spec.get('cases') or {}).values():
                    walk(body if isinstance(body, list) else None)
                walk(spec.get('default') if isinstance(spec.get('default'), list) else None)
                for group in spec.get('groups') or []:
                    walk(group.get('fields') if isinstance(group, dict) else None)
    walk(fields if isinstance(fields, list) else None)
    return found


class Frame(bytes):
    """A field list's bytes, with how many at the end are still pending - peeked by a bit
    range or bool and consumed by nothing in the list. The enclosing list keeps them
    pending, so the field after the construct shares them, as a decoder reads it: vobo's
    digital inputs peek a word inside a flagged block that the field after it reads."""

    def __new__(cls, data=b'', pending=0, covered=None, peeks=None, echoes=None):
        frame = super().__new__(cls, data)
        frame.pending = pending
        # CR-2026-141 (PS-551): per byte, the bits some field wrote (None: all of them),
        # and each bit range or bool placed, for the check where the frame closes.
        frame.covered = covered
        frame.peeks = peeks or []
        # CR-2026-175 (PS-589): each non-consuming byte string given a value, as
        # (position, field, given, length or None for `remaining`).
        frame.echoes = echoes or []
        return frame


def padding(length: int) -> 'Frame':
    """A skip's zeros (CR-2026-141). Padding carries no value, so its bits fix no peek and
    contradict none: a decoder skips them, whatever the peek before set there."""
    return Frame(bytes(length), 0, bytes(length), [])


def frame_covered(data) -> bytes:
    """The bits of `data` some field wrote: all of them for bytes a whole field wrote."""
    covered = getattr(data, 'covered', None)
    return bytes(covered) if covered is not None else b'\xff' * len(data)


def frame_peeks(data) -> list:
    """The bit ranges and bools placed in `data`: (position, size, byteorder, start,
    width, raw or None where derived, name)."""
    return list(getattr(data, 'peeks', None) or [])


def prefixed_frame(prefix: bytes, body) -> 'Frame':
    """`prefix` written whole, then `body`, keeping the body's pending tail and peeks."""
    shift = len(prefix)
    return Frame(bytes(prefix) + bytes(body), getattr(body, 'pending', 0),
                 b'\xff' * shift + frame_covered(body),
                 [(p[0] + shift,) + tuple(p[1:]) for p in frame_peeks(body)],
                 [(e[0] + shift,) + tuple(e[1:]) for e in frame_echoes(body)])


#: The byte strings a `consume: 0` re-reads (PS-588, CR-2026-175).
NON_CONSUMING_TYPES = ('bytes', 'hex', 'ascii')


def is_echo(field_def) -> bool:
    """A `bytes`, `hex` or `ascii` field declaring `consume: 0` (PS-588): it reads and
    reports its span and leaves the read position, so later fields read the bytes again."""
    return (isinstance(field_def, dict) and field_def.get('type') in NON_CONSUMING_TYPES
            and 'consume' in field_def and field_def.get('consume') == 0
            and not isinstance(field_def.get('consume'), bool))


def frame_echoes(data) -> list:
    """The non-consuming byte strings given a value in `data` (Frame.echoes)."""
    return list(getattr(data, 'echoes', None) or [])


def echo_length(field_def, data) -> Optional[int]:
    """The byte count of a non-consuming byte string, read from the input it is encoded
    with: None for `remaining`, which reaches the end of the frame it is checked in."""
    raw = field_def.get('length', 1)
    if isinstance(raw, str):
        text = raw.strip()
        if text.lower() == 'remaining':
            return None
        if not re.fullmatch(r'-?\d+', text):
            ref = text[1:] if text.startswith('$') else text
            value = data.get(ref)
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or value < 0 or int(value) != value:
                raise ValueError(missing_field_message(ref))
            return int(value)
        raw = int(text)
    return None if raw < 0 else int(raw)


def echo_value(field_def, span: bytes) -> Any:
    """What a non-consuming byte string decodes from `span`: its `format` and
    `separator`, or PS-534's trailing 0x00 removal, applied as a decoder applies them."""
    ftype = field_def.get('type')
    if ftype == 'bytes':
        return format_bytes(field_def, span)
    if ftype == 'hex':
        return span.hex()
    value = decode_ascii(field_def.get('name'), span, [])
    if field_def.get('numeral'):
        value = decode_numeral(field_def.get('name'), value, field_def['numeral'])
    return value


def echo_text(value) -> str:
    """A value as a PS-589 message writes it: text in single quotes, a number as
    JavaScript's String(number), a list of octets as `[1, 2]` in quotes."""
    if isinstance(value, list):
        return "'[" + ", ".join(echo_text(v).strip("'") for v in value) + "]'"
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return format_lookup_value(value)
    return "'" + str(value) + "'"


def check_echoes(frame) -> None:
    """PS-589 (CR-2026-175), where a frame closes: a value given for a non-consuming byte
    string equals what the field decodes from the bytes the other fields wrote over its
    span, or the input is rejected naming the field."""
    data = bytes(frame)
    for position, field_def, given, length in frame_echoes(frame):
        if length is None:
            length = max(0, len(data) - position)
        if position + length > len(data):
            written = 'too few bytes'
        else:
            try:
                found = echo_value(field_def, data[position:position + length])
            except ValueError:
                found = None
            if found is not None and normalize_output(found) == normalize_output(given):
                continue
            written = echo_text(found) if found is not None else 'too few bytes'
        raise ValueError(f"Field '{field_def.get('name')}' is given {echo_text(given)}, but "
                         f"the other fields write {written} over its span (PS-589)")


def peek_disagreement(peeks, frame: bytes) -> str:
    """PS-551 (CR-2026-141): a supplied bit range or bool whose bits read back from `frame`
    differently from what it was given. ORing the two would change both values."""
    for position, size, byteorder, start, width, raw, name in peeks:
        if raw is None or position + size > len(frame):
            continue
        mask = ((1 << width) - 1) << start
        found = int.from_bytes(frame[position:position + size], byteorder) & mask
        if found != raw << start:
            return (f"Field '{name}' is given {raw}, but the fields that share its bits "
                    f"write {found >> start} (PS-551)")
    return "A field writes bits a peek before it set otherwise (PS-551)"


def check_peeks(frame) -> None:
    """PS-551 (CR-2026-141), where a frame closes: every bit range and bool reads back what
    it was given, and an absent one - derived, writing no bits - has every bit written by
    another field, or it is missing. Then PS-589's check of each non-consuming byte string
    given a value (check_echoes)."""
    _check_bit_peeks(frame)
    check_echoes(frame)


def _check_bit_peeks(frame) -> None:
    covered = frame_covered(frame)
    for position, size, byteorder, start, width, raw, name in frame_peeks(frame):
        mask = ((1 << width) - 1) << start
        if raw is None:
            have = int.from_bytes(covered[position:position + size].ljust(size, b'\0'),
                                  byteorder)
            if have & mask != mask:
                raise ValueError(missing_field_message(name))
            continue
        found = int.from_bytes(bytes(frame[position:position + size]).ljust(size, b'\0'),
                               byteorder) & mask
        if found != raw << start:
            raise ValueError(peek_disagreement([(position, size, byteorder, start, width,
                                                 raw, name)], bytes(frame)))


class FrameBuffer:
    """The bytes a field list writes, laid out as a decoder reads them (CR-2026-136).

    A bit range or a bool that does not `consume` leaves the bytes it filled *pending*: a
    decoder peeks them (PS-059, PS-066), so the next field reads the same bytes, and its
    own bits or bytes are ORed into them. Bytes still pending at the end are written too,
    since a decoder reads them. The encoder used to write a run's bytes and then the next
    field's after them - two bytes where the decoder reads one.
    """

    def __init__(self):
        self.buf = bytearray()
        self.pending = bytearray()
        # CR-2026-141: the bits written, by position in this list's frame, and each bit
        # range or bool placed (see Frame).
        self.covered = bytearray()
        self.peeks = []
        self.echoes = []        # CR-2026-175 (PS-589): see Frame.echoes

    def echo(self, field_def, data) -> None:
        """PS-589: a non-consuming byte string writes nothing and is not required; a value
        given for it is checked where the frame closes. It reads from the position a
        decoder is at, which is the start of any bytes still pending."""
        name = field_def.get('name')
        if name and data.get(name) is not None:
            self.echoes.append((len(self.buf), field_def, data[name],
                                echo_length(field_def, data)))

    def cover(self, position: int, mask: bytes) -> None:
        if len(self.covered) < position + len(mask):
            self.covered.extend(bytes(position + len(mask) - len(self.covered)))
        for i, m in enumerate(mask):
            self.covered[position + i] |= m

    def extend(self, data) -> None:
        carried = min(getattr(data, 'pending', 0), len(data))
        offset = len(self.buf)
        covered = frame_covered(data)
        peeks = frame_peeks(data)
        self.echoes.extend((e[0] + offset,) + tuple(e[1:]) for e in frame_echoes(data))
        data = bytes(data)
        if self.pending and data and any(
                self.pending[i] & ~data[i] & covered[i]
                for i in range(min(len(self.pending), len(data)))):
            # PS-551: a field would OR over a peek's different bits, changing both.
            raise ValueError(peek_disagreement(self.peeks, bytes(self.buf) + data))
        self.cover(offset, covered)
        self.peeks.extend((p[0] + offset,) + tuple(p[1:]) for p in peeks)
        if self.pending and data:
            shared = min(len(self.pending), len(data))
            data = bytes(b | self.pending[i] if i < shared else b
                         for i, b in enumerate(data))
            del self.pending[:shared]
        if carried:
            # A nested list's unconsumed tail stays pending here (Frame).
            self.buf.extend(data[:len(data) - carried])
            tail = bytearray(data[len(data) - carried:])
            for i, b in enumerate(self.pending):
                if i < len(tail):
                    tail[i] |= b
                else:
                    tail.append(b)
            self.pending = tail
            return
        self.buf.extend(data)

    def place_bits(self, base_size: int, byteorder: str, start: int, bits: int,
                   consume: int, width: int = 0, name: Any = None,
                   derived: bool = False) -> None:
        """OR `bits` at `start` into the pending base; then consume as declared (PS-016).

        CR-2026-141 (PS-551): a range of `width` bits is recorded for the check where the
        frame closes. A derived one - absent from the input, and not consuming - writes no
        bits: the fields that write them fix its value."""
        if width and name:          # a nameless range is padding, and is not checked
            self.peeks.append((len(self.buf), base_size, byteorder, start, width,
                               None if derived else bits, name))
            if not derived:
                self.cover(len(self.buf),
                           (((1 << width) - 1) << start).to_bytes(base_size, byteorder))
        if len(self.pending) < base_size:
            self.pending.extend(bytes(base_size - len(self.pending)))
        base = int.from_bytes(self.pending[:base_size], byteorder) | (bits << start)
        self.pending[:base_size] = base.to_bytes(base_size, byteorder)
        if consume > 0:
            if len(self.pending) < consume:
                self.pending.extend(bytes(consume - len(self.pending)))
            self.buf.extend(self.pending[:consume])
            del self.pending[:consume]

    def frame(self) -> 'Frame':
        size = len(self.buf) + len(self.pending)
        covered = bytes(self.covered[:size]).ljust(size, b'\0')
        return Frame(bytes(self.buf + self.pending), len(self.pending), covered,
                     list(self.peeks), list(self.echoes))


def is_peek_field(field_def) -> bool:
    """A named bit range or bool that does not consume (PS-060, PS-066)."""
    if not isinstance(field_def, dict) or not field_def.get('name') \
            or int(field_def.get('consume', 0) or 0):
        return False
    ftype = str(field_def.get('type', ''))
    if ftype == 'bool':
        return True
    return ('byte_group' not in field_def and ftype != 'bitfield_string'
            and any(marker in ftype for marker in ('[', ':', '<')))


def covered_peeks(fields) -> list:
    """The peeks in `fields` a whole field after them writes over - read from the schema,
    so a tlv case can be chosen without them (PS-551, CR-2026-141). A peek is counted only
    where the next field that reads bytes is a plain whole field: a range that consumes,
    a skip or a construct ends its span first, and its bits may be left unwritten
    (wt303's status nibble beside a mode nibble). Whether a peek is derived when encoding
    is decided by the bits actually written, not by this."""
    out, pending = [], []
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        ftype = f.get('type')
        if is_peek_field(f):
            pending.append(f)
            continue
        if ftype in COMPUTED_TYPES or is_literal(f):
            continue
        construct = any(k in f for k in ('match', 'tlv', 'byte_group', 'flagged', '$ref'))
        bits = isinstance(ftype, str) and any(m in ftype for m in ('[', ':', '<'))
        if not construct and not bits and ftype not in ('skip', 'object', 'repeat', 'bool'):
            out.extend(pending)
        pending = []
    return out


def integer_bounds(size_bytes=None, signed=False, bits=None):
    """The range a whole integer of `size_bytes` bytes, or of `bits` bits, holds."""
    bits = bits if bits is not None else size_bytes * 8
    if signed:
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


def check_nibble_decimal(tenths, signed, name, given):
    """PS-168 for `udec`/`sdec` (PS-330): one byte holds a whole part in a nibble and
    tenths 0 to 9, so 0 to 15.9, or -8 to 7.9 signed. `tenths` is the value as encoded,
    in tenths; the message gives it rounded to tenths, in the form check_fits uses. Both
    types said "does not fit udec", in words of their own in every implementation."""
    low, high = (-80, 79) if signed else (0, 159)
    if not low <= tenths <= high:
        sign = '-' if tenths < 0 else ''
        whole, part = divmod(abs(tenths), 10)
        encoded = f"{sign}{whole}" + (f".{part}" if part else '')
        shown = (format_lookup_value(given)
                 if isinstance(given, (int, float)) and not isinstance(given, bool)
                 else str(given))
        raise ValueError(f"Field '{name}': {shown} encodes as {encoded}, outside "
                         f"{'-8' if signed else '0'} to {'7.9' if signed else '15.9'} "
                         f"(PS-168)")


def written_discriminator_error(value, name):
    """PS-557 (CR-2026-147): the message for a fractional value given for a discriminator
    an encoder writes, or None where the value is no fraction. The match is named as
    PS-535 names it."""
    if isinstance(value, bool) or not isinstance(value, float):
        return None
    if value != value or value in (float('inf'), float('-inf')) or value.is_integer():
        return None
    label = f"'{name}'" if name else 'an unnamed match'
    return (f"Field {label}: the discriminator {format_lookup_value(value)} is not an "
            f"integer, so it cannot be written (PS-557)")


def tail_unchecked(method):
    """PS-564 is checked only where the bytes after a `remaining` match are all in the
    field lists enclosing it; inside a tlv or a repeat they are not, so a `remaining`
    match there is not checked (`_ps564_depth`)."""
    def wrapper(self, *args, **kwargs):
        self._ps564_depth = getattr(self, '_ps564_depth', 1) + 1
        try:
            return method(self, *args, **kwargs)
        finally:
            self._ps564_depth -= 1
    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class TailLevel:
    """PS-564: one field list's share of the bytes written after a `remaining` match.

    A `remaining` match is decoded on the count of bytes from it to the end of the
    payload, so what an encoder writes there must select the case it chose. Each field
    list is a level: once an item of it is written, the bytes it added are added to the
    tail of every check an earlier item of the same list registered - its own `remaining`
    match, or one nested inside it - so at the top each check holds the bytes from its
    match to the end. `start()` before each item, `close()` after the list."""

    def __init__(self, encoder: Any, out: 'FrameBuffer'):
        self.encoder, self.out, self.checks = encoder, out, []
        self.size, self.mark = None, 0

    def _size(self) -> int:
        return len(self.out.buf) + len(self.out.pending)

    def start(self) -> None:
        self.close()
        self.size, self.mark = self._size(), len(self.encoder._ps564)

    def close(self) -> None:
        if self.size is None:
            return
        grown = self._size() - self.size
        for check in self.checks:
            check['tail'] += grown
        self.checks.extend(self.encoder._ps564[self.mark:])
        self.size = None


def remaining_selection(match_def: Dict[str, Any], count: int, pattern) -> Any:
    """The case key a decoder selects for a `remaining` match with `count` bytes left,
    'default' where no case lists it and the match declares a default (a list or
    `skip`), else None (PS-414, PS-415)."""
    cases = match_def.get('cases') or {}
    for key in cases:
        if key != 'default' and pattern(count, key):
            return key
    default = match_def.get('default', 'error')
    if isinstance(default, list) or default == 'skip' or isinstance(cases.get('default'), list):
        return 'default'
    return None


def remaining_mismatch_message(match_def: Dict[str, Any], count: int, selected: Any,
                               chosen: Any) -> str:
    """PS-564's rejection, naming the match as PS-535 and PS-557 name one."""
    name = match_def.get('name')
    label = f"'{name}'" if name else 'an unnamed match'

    def describe(key):
        if key is None:
            return 'no case'
        if key == 'default':
            return 'its default'
        return f"case {key}"
    return (f"Field {label}: the {count} byte(s) from it to the end of the payload select "
            f"{describe(selected)}, not {describe(chosen)}, the case its input gives "
            f"(PS-564)")


def check_written_discriminator(value, name):
    """PS-557: a written discriminator is read back as an integer, so a fraction is
    rejected rather than rounded or truncated. `int()` truncated 2.5 to 2."""
    message = written_discriminator_error(value, name)
    if message:
        raise ValueError(message)


def round_to_integer(value):
    """PS-552: the nearest integer to the binary64 value, a tie to even. Python's round()
    on a float is exactly that; an int is already one."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return int(round(float(value)))


def check_fits(raw, low, high, name, given, bit_range=None):
    """PS-168, PS-552: the rounded integer fits the field's type, a bit range's being its
    width, or the encoder rejects the input naming the field and the value given. The spec
    prototype's text; the value is written as JavaScript's String(number) writes it, so a
    cross-language diff compares equal strings. Masking or wrapping it wrote another
    number than the one given, silently.

    A bit range (PS-064, CR-2026-136) names its type after the range, as the prototype
    does: "... outside 0 to 15 for u8[0:3] (PS-168)"."""
    if not low <= raw <= high:
        shown = (format_lookup_value(given)
                 if isinstance(given, (int, float)) and not isinstance(given, bool)
                 else str(given))
        where = f" for {bit_range}" if bit_range else ""
        raise ValueError(f"Field '{name}': {shown} encodes as {raw}, outside {low} to "
                         f"{high}{where} (PS-168)")
    return raw


def positive_zero(value):
    """A float to write: a zero of either sign is the positive zero (PS-566).

    `-0.0 + 0.0` is `0.0` in IEEE 754, so this turns a negative zero - an input's, a decoded
    one's, or one the field's own arithmetic produced - into the positive-zero word, and
    changes no other value.
    """
    return float(value) + 0.0


def pack_float(code, value, little=False):
    """struct.pack of a binary float whose word is the positive zero when it would be a
    negative zero (PS-566, CR-2026-157): a negative value the type's rounding turns into
    zero (-1e-50 as an f32) packs the sign word, so the packed word is tested, not the
    input. A subnormal is not zero and keeps its sign."""
    packed = struct.pack('>' + code, positive_zero(value))
    if packed[0] == 0x80 and not any(packed[1:]):
        packed = bytes(len(packed))
    return packed[::-1] if little else packed


def write_word_ordered(field_type, value):
    """The inverse of read_word_ordered."""
    layout, kind = WORD_ORDERED_TYPES[field_type]
    if kind == 'f':
        word = int.from_bytes(pack_float('f', value), 'big')
    else:
        word = int(value)
        if word < 0:
            word += 0x100000000
        word &= 0xFFFFFFFF   # the caller has checked the value fits (PS-168)
    high, low = word >> 16, word & 0xFFFF
    if layout == 'le16':
        return low.to_bytes(2, 'big') + high.to_bytes(2, 'big')
    return high.to_bytes(2, 'little') + low.to_bytes(2, 'little')


#: The MCCI minifloats (CR-2026-063): width in bytes. Each is a word read in the field's
#: effective byte order (PS-420) and decoded by its formula, never as IEEE half precision
#: (PS-421).
MINIFLOAT_SIZES = {'uflt16': 2, 'sflt16': 2, 'sflt24': 3}


def decode_minifloat(field_type, word):
    """The value of a minifloat word (PS-417 to PS-419); None where it has none (e = 127)."""
    if field_type == 'uflt16':
        e, f = word >> 12, word & 0x0FFF
        return f / 4096 * 2.0 ** (e - 15)
    if field_type == 'sflt16':
        sign = -1 if word & 0x8000 else 1
        e, f = (word >> 11) & 0x0F, word & 0x07FF
        value = sign * f / 2048 * 2.0 ** (e - 15)
        return 0 if value == 0 else value          # 0x8000 is -0, reported as 0
    sign = -1 if word & 0x800000 else 1
    e, f = (word >> 16) & 0x7F, word & 0xFFFF
    if e == 127:
        return None
    if e == 0:
        value = sign * f / 65536 * 2.0 ** -62
    else:
        value = sign * (1 + f / 65536) * 2.0 ** (e - 63)
    return 0 if value == 0 else value


def _round_half_even(fraction):
    from fractions import Fraction
    floor = fraction.numerator // fraction.denominator
    rest = fraction - floor
    if rest > Fraction(1, 2) or (rest == Fraction(1, 2) and floor % 2):
        floor += 1
    return floor


def encode_minifloat(field_type, value):
    """The word for a value, with a sign-only word (a negative value the type rounds to zero)
    written as the zero word (PS-566, CR-2026-157); see `_minifloat_word`."""
    word = _minifloat_word(field_type, value)
    sign_only = {'sflt16': 0x8000, 'sflt24': 0x800000}.get(field_type)
    return 0 if word == sign_only else word


def _minifloat_word(field_type, value):
    """The word for a value: the smallest exponent whose fraction fits, ties to even (PS-420).

    A value outside the type's range is an error, not a saturation.
    """
    from fractions import Fraction
    exact = Fraction(float(value))
    if field_type in ('uflt16', 'sflt16'):
        signed = field_type == 'sflt16'
        if exact < 0 and not signed:
            raise ValueError(f"{value} is negative; uflt16 holds [0, 1) (PS-420)")
        magnitude = abs(exact)
        bits = 11 if signed else 12
        for e in range(16):
            f = _round_half_even(magnitude * (1 << bits) * Fraction(2) ** (15 - e))
            if f < 1 << bits:
                word = (e << bits) | f
                return word | (0x8000 if signed and exact < 0 else 0)
        raise ValueError(f"{value} is outside the range of {field_type} (PS-420)")
    magnitude = abs(exact)
    sign = 0x800000 if exact < 0 else 0
    if magnitude == 0:
        return sign
    for e in range(1, 127):
        if magnitude < Fraction(2) ** (e - 62):
            f = _round_half_even((magnitude / Fraction(2) ** (e - 63) - 1) * 65536)
            if f == 65536:
                continue
            if f < 0:
                break
            return sign | (e << 16) | f
    else:
        raise ValueError(f"{value} is outside the range of sflt24 (PS-420)")
    f = _round_half_even(magnitude * 65536 * Fraction(2) ** 62)
    if f >= 65536:
        return sign | (1 << 16)
    return sign | f


#: The largest finite bf16, 0x7F7F: (2 - 2**-7) * 2**127 (CR-2026-149).
BF16_MAX = (2 - 2.0 ** -7) * 2.0 ** 127


def decode_bf16(word):
    """PS-558: the binary32 whose upper half is the word and whose lower half is zero."""
    return struct.unpack('>f', (word << 16).to_bytes(4, 'big'))[0]


def encode_bf16(value, name=None):
    """PS-559: the nearest bf16 to the binary64 value, a tie to the even fraction, rounded
    once (never through a binary32); a finite value beyond the largest bf16 is a PS-168
    error, in the spec prototype's text. NaN and the infinities are their words."""
    value = float(value)
    if math.isnan(value):
        return 0x7FC0
    sign = 0x8000 if math.copysign(1.0, value) < 0 else 0
    magnitude = abs(value)
    if math.isinf(magnitude):
        return sign | 0x7F80
    mantissa, exponent = math.frexp(magnitude)       # magnitude = mantissa * 2**exponent
    if magnitude == 0 or exponent - 1 < -126:
        word = round(magnitude * 2.0 ** 133)         # subnormal: fraction * 2**-133
    else:
        fraction = round(mantissa * 256)             # 128..256, the leading bit included
        if fraction == 256:
            fraction, exponent = 128, exponent + 1
        word = (exponent + 126) << 7 | (fraction - 128)
    if word >= 0x7F80:
        raise ValueError(
            f"Field '{name}': {format_lookup_value(value)} is outside the range of bf16, "
            f"{format_lookup_value(-BF16_MAX)} to {format_lookup_value(BF16_MAX)} (PS-168)")
    return sign | word if word else 0            # PS-566: a negative value rounding to zero is 0


#: The largest finite magnitude each binary float type holds (CR-2026-148, PS-168): a
#: value past it is no value the field can write. f64 holds any finite value.
FLOAT_TYPE_MAX = {'f16': 65504.0, 'f32': 3.4028234663852886e38,
                  'f32le16': 3.4028234663852886e38, 'f32be16le': 3.4028234663852886e38,
                  'f64': float('inf')}


def integer_range(field_type: str):
    """Inclusive (low, high) a field of this type can hold, or None if not an integer."""
    info = INTEGER_TYPE_INFO.get(str(field_type))
    if info is None:
        return None
    size, signed = info
    if signed:
        return -(1 << (8 * size - 1)), (1 << (8 * size - 1)) - 1
    return 0, (1 << (8 * size)) - 1


def resolve_length(field_def, buf, pos, default=1, variables=None):
    """Resolve a field's byte count, honouring `length: remaining` (PS-014).

    `remaining` consumes every byte from the read position to the end of the
    payload. It is the only spelling the specification defines for that; a
    negative integer is the shared internal sentinel the parsers map it to, so
    all five implementations agree without needing a string in their field
    structs. `buf[pos:pos + -1]` used to yield an empty slice *and* rewind the
    cursor by a byte, which is how radio-bridge's stored downlink decoded as
    empty.
    """
    raw = field_def.get('length', default)
    if isinstance(raw, str):
        text = raw.strip()
        if text.lower() == 'remaining':
            return max(0, len(buf) - pos)
        if not re.fullmatch(r'-?\d+', text):
            # PS-464, PS-465: the name of a preceding field, with or without `$`, whose
            # decoded value is the count - resolved as `repeat`'s byte_length is. An
            # unresolved name is an error naming it.
            name = text[1:] if text.startswith('$') else text
            if variables is None or name not in variables:
                raise ValueError(f"length names {name!r}, which is not a field decoded "
                                 f"before this one (PS-465)")
            value = variables[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or value < 0 or int(value) != value:
                raise ValueError(f"length names {name!r}, whose value {value!r} is not a "
                                 f"byte count (PS-464)")
            return int(value)
        raw = int(text)
    if raw < 0:
        return max(0, len(buf) - pos)
    return int(raw)


def normalize_output(value):
    """Bring one decoded value to its reported JSON representation (CR-2026-008).

    Three rules, applied recursively so `object` and `repeat` members are covered:

    - A byte sequence reports as a lowercase hex string (PS-281). Python decoded these
      to a bytes object and Go to a hex string, so no single expected value satisfied
      both and two library vectors had to be quarantined over it.
    - An integral numeric value reports without a fraction (PS-280): 15, not 15.0.
      `JSON.stringify` omits a zero fraction and `json.dumps` preserves it, so the
      interpreter disagreed with the codec generated from the same schema on 304 of
      2850 corpus fields. A gateway replacing a deployed JS codec must not appear to
      change its schema, so JavaScript's rendering is the conformant one.
    - NaN and the infinities are not JSON values, so a field holding one is omitted
      (PS-282). Emitting them produced output no conforming parser would read.

    Returns OMITTED for a value that must not be reported.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return OMITTED
        if value.is_integer():
            return int(value)
        return value
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            normalized = normalize_output(item)
            if normalized is not OMITTED:
                out[key] = normalized
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            normalized = normalize_output(item)
            if normalized is not OMITTED:
                out.append(normalized)
        return out
    return value


#: The output formats of a `bytes` field (PS-079), and the two a `separator` applies to
#: (PS-391).
BYTES_FORMATS = ('hex', 'hex:upper', 'base64', 'array')
SEPARATED_BYTES_FORMATS = ('hex', 'hex:upper')


def check_bytes_format(field_def):
    """Reject a `format` outside PS-079 and a `separator` beside a format it cannot apply to."""
    fmt = field_def.get('format', 'hex')
    if fmt not in BYTES_FORMATS:
        raise ValueError(
            f"Field '{field_def.get('name', '?')}': bytes format {fmt!r} is not one of "
            f"{', '.join(BYTES_FORMATS)} (PS-079)")
    if 'separator' in field_def and fmt not in SEPARATED_BYTES_FORMATS:
        raise ValueError(
            f"Field '{field_def.get('name', '?')}': `separator` applies only to the hex "
            f"formats, not {fmt!r} (PS-391)")


def format_bytes(field_def, data: bytes):
    """A `bytes` field's value in its declared format (PS-079, PS-391).

    Both keys were ignored here and by the generated codec, so `format: hex:upper` with
    `separator: ":"` reported `aabbcc` where Go, Java and C# report `AA:BB:CC`.
    """
    check_bytes_format(field_def)
    fmt = field_def.get('format', 'hex')
    if fmt == 'base64':
        import base64 as b64
        return b64.b64encode(data).decode('ascii')
    if fmt == 'array':
        return list(data)
    digits = '%02X' if fmt == 'hex:upper' else '%02x'
    return str(field_def.get('separator', '')).join(digits % b for b in data)


#: The types whose value is a byte string an encoder writes as given (PS-081, PS-071).
BYTE_STRING_TYPES = ('bytes', 'hex', 'base64')
_HEX_TEXT = re.compile(r'(?:[0-9A-Fa-f]{2})*')
_BASE64_TEXT = re.compile(r'[A-Za-z0-9+/]*={0,2}')


def byte_string_input(field_def, value) -> bytes:
    """CR-2026-181 (PS-081, PS-601): a `bytes`, `hex` or `base64` field's input as bytes.

    A string is read in the field's own format, never guessed: base64 (padded or not) for
    `format: base64` and the `base64` type, otherwise hex in either case, with the field's
    declared `separator` or without; any of them takes an array of integers 0 to 255.
    Anything malformed is rejected naming the field, in the spec prototype's words
    (`byte_string_value`): this wrapped array elements with `& 0xFF`, decoded base64
    leniently, and stripped spaces from hex.
    """
    name = field_def.get('name')
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, (list, tuple)):
        if not all(isinstance(b, (int, float)) and not isinstance(b, bool)
                   and math.isfinite(b) and b == int(b) and 0 <= b <= 255 for b in value):
            raise FieldError(f"Field '{name}': an array element is not an integer from 0 "
                             f"to 255 (PS-601)")
        return bytes(int(b) for b in value)
    if not isinstance(value, str):
        raise FieldError(f"Field '{name}': a {json_type_name(value)} is not a byte string "
                         f"value (PS-081)")
    ftype = field_def.get('type')
    if ftype == 'base64' or (ftype == 'bytes' and field_def.get('format') == 'base64'):
        text = value.strip()
        padded = text + '=' * (-len(text) % 4)
        if not _BASE64_TEXT.fullmatch(padded) or len(padded.rstrip('=')) % 4 == 1:
            raise FieldError(f"Field '{name}': '{value}' is not valid base64 (PS-601)")
        import base64 as b64
        return b64.b64decode(padded)
    separator = field_def.get('separator') if ftype == 'bytes' else None
    text = value.replace(str(separator), '') if separator else value
    if not _HEX_TEXT.fullmatch(text):
        with_separator = " with separator '%s'" % separator if separator else ''
        raise FieldError(f"Field '{name}': '{value}' is not well-formed hex{with_separator} "
                         f"(PS-601)")
    return bytes.fromhex(text)


def json_type_name(value) -> str:
    """The JSON type of an input value, as a message names it."""
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float)):
        return 'number'
    if isinstance(value, dict):
        return 'object'
    return 'null' if value is None else type(value).__name__


def byte_string_named_length(field_def) -> Optional[str]:
    """The field a byte string's `length` names (PS-464), or None for a literal length,
    `remaining` or none."""
    if field_def.get('type') not in BYTE_STRING_TYPES:
        return None
    length = field_def.get('length')
    if not isinstance(length, str) or length.strip().lower() == 'remaining':
        return None
    return length.strip().lstrip('$') or None


def check_byte_string_length(field_def, raw: bytes) -> bytes:
    """PS-602: a literal `length` is the value's byte count; nothing is cut or padded."""
    length = field_def.get('length')
    if isinstance(length, int) and not isinstance(length, bool) and len(raw) != length:
        raise FieldError(f"Field '{field_def.get('name')}' is given {len(raw)} bytes for a "
                         f"length of {length} (PS-602)")
    return raw


_FIXED_TYPE_SIZES = {
    'u8': 1, 'u16': 2, 'u24': 3, 'u32': 4, 'u64': 8,
    's8': 1, 's16': 2, 's24': 3, 's32': 4, 's64': 8,
    'u32le16': 4, 's32le16': 4, 'f32le16': 4,
    'u32be16le': 4, 's32be16le': 4, 'f32be16le': 4, 'f16': 2, 'bf16': 2,
    'uflt16': 2, 'sflt16': 2, 'sflt24': 3, 'f32': 4, 'f64': 8, 'udec': 1, 'sdec': 1,
}


def fixed_element_size(fields):
    """The bytes one element of these fields always takes, or None if it varies.

    PS-344a asks that a whole element be known to remain before one is begun; that can be
    known only where the size is fixed. A bit range or bool advances by its `consume`,
    a computed or literal field by nothing.
    """
    total = 0
    for field in fields or []:
        if not isinstance(field, dict):
            return None
        ftype = str(field.get('type', ''))
        base = INTEGER_TYPE_INFO.get(ftype)
        if ftype in _FIXED_TYPE_SIZES or base:
            total += _FIXED_TYPE_SIZES.get(ftype) or base[0]
        elif ftype in COMPUTED_TYPES or is_literal(field) or is_echo(field):
            continue                    # an echo re-reads, advancing nothing (PS-588)
        elif '[' in ftype or ftype == 'bool':
            consume = field.get('consume', 0)
            if not isinstance(consume, int):
                return None
            total += consume
        elif ftype in ('bytes', 'ascii', 'hex', 'base64', 'skip') and isinstance(
                field.get('length'), int):
            total += field['length']
        else:
            return None
    return total or None


#: Names clause 7 reserves for interpreter metadata (PS-176, PS-433).
RESERVED_OUTPUT_NAMES = ('_meta', '_quality', '_warnings', '_decode')    # _decode: CR-2026-162


def internal_name_errors(schema):
    """PS-433: no field may take a name reserved for interpreter metadata."""
    errors = []

    def walk(node, path):
        if isinstance(node, dict):
            name = node.get('name')
            if name in RESERVED_OUTPUT_NAMES and ('type' in node or 'fields' in node):
                errors.append(f"{path}: {name!r} is reserved for interpreter metadata and "
                              f"cannot name a field (PS-433)")
            for key, value in node.items():
                if key not in ('test_vectors', 'definitions'):
                    walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(schema, '')
    return errors


def declared_sources(fields: Any) -> Dict[str, Dict[str, Any]]:
    """Every name a field list declares, anywhere in it, with whether some declaration
    reads payload bytes or computes from them (`reads`) and the values its literals give
    (`literals`). A declaration is a mapping with `name` and `type`, or a match's `name`.

    For the empty-payload check in every encoder (PS-442, no-silent-empty): a payload of
    no bytes is a faithful encoding only of input that no field reads from bytes.
    """
    found = {}  # type: Dict[str, Dict[str, Any]]

    def note(name, literal, value=None):
        entry = found.setdefault(name, {'reads': False, 'literals': []})
        if literal:
            entry['literals'].append(value)
        else:
            entry['reads'] = True

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        name = node.get('name')
        if isinstance(name, str) and name and 'type' in node:
            if is_literal(node):
                note(name, True, node['value'])
            else:
                note(name, False)
        match = node.get('match')
        if isinstance(match, dict) and isinstance(match.get('name'), str):
            note(match['name'], False)
        for value in node.values():
            if isinstance(value, (list, dict)):
                walk(value)

    walk(fields)
    return found


def empty_payload_error(fields: Any, data: Dict[str, Any]) -> Optional[str]:
    """PS-442: an encoder that wrote no bytes, where the input gives a value that only a
    field reading payload bytes (or computing from them) reports, dropped that field's
    bytes without a word. The first such name is reported, in the input's order.

    A value that can be written in no bytes is not one: an empty list, mapping or string
    (a repeat over nothing, an empty `remaining` text), a value a literal of that name
    gives, and an internal or absent one. So a schema that legitimately reads nothing
    for the input - a literal-only branch, a tlv of no entries - still encodes to an
    empty payload.
    """
    sources = None
    for key, value in data.items():
        if not isinstance(key, str) or key.startswith('_') or value is None:
            continue
        if isinstance(value, (list, dict, str, bytes)) and len(value) == 0:
            continue
        if sources is None:
            sources = declared_sources(fields)
        entry = sources.get(key)
        if not entry or not entry['reads']:
            continue
        if any(same_label(literal, value) for literal in entry['literals']):
            continue
        return (f"Encoding wrote no bytes, though the input gives '{key}', which the "
                f"schema reads from the payload (PS-442)")
    return None


def match_label(spec: Dict[str, Any]) -> str:
    """How a message names a match (PS-573): its `name`, else its `var`, quoted, else
    'an unnamed match'. The spec prototype's `match_label`."""
    name = spec.get('name') or spec.get('var')
    return f"'{name}'" if name else 'an unnamed match'


def undetermined_case_message(name):
    """PS-574: a match whose case neither the input nor a decode record gives, and which
    this encoder could not derive either, named by its discriminator."""
    return f"Missing required field: {name} (PS-574)"


def missing_field_message(name):
    """PS-551: what every encoder says for a required value the input omits. The spec
    prototype's text, so a cross-language diff compares equal strings."""
    return f"Missing required field: {name}"


def field_default(field_def):
    """PS-554 (CR-2026-139): `(True, default)` where an omitted input takes the field's
    declared `default`, else `(False, None)`.

    The default is in the field's reported form - after its modifiers, transform and
    lookup - and the caller encodes it exactly as that value given as input, so one that
    cannot be encoded is rejected as such input is (PS-168). A declared default is a
    defined absence (PS-551 as amended). An enum's `default` is its unmapped label
    (PS-068), so it is not a value to write. Since CR-2026-134 a declared default was rejected as a missing value.
    """
    if isinstance(field_def, dict) and 'default' in field_def \
            and field_def.get('type') != 'enum':
        return True, field_def['default']
    return False, None


def raw_bits_field(field_def):
    """The field without its `encoding`: a sentinel is written as the bits it is (PS-427)."""
    return {k: v for k, v in field_def.items() if k != 'encoding'}


def optional_field_size(field_def):
    """The bytes an optional field takes, or None where its size depends on the payload.

    An `object` is sized as its fields sum (CR-2026-059). A field whose length depends on
    the payload is present where at least one byte remains.
    """
    if field_def.get('type') == 'object':
        return fixed_element_size(field_def.get('fields'))
    return fixed_element_size([field_def])


def optional_errors(fields, where='fields'):
    """PS-404: in any field list, every field after an optional field is optional too."""
    errors = []
    seen = None
    for i, f in enumerate(fields or []):
        if not isinstance(f, dict):
            continue
        if f.get('optional') is True:
            seen = seen or f.get('name', '?')
        elif seen is not None:
            errors.append(f"{where}[{i}] ({f.get('name', '?')}): follows optional field "
                          f"{seen!r}, so it must be optional too (PS-404)")
        for key, value in f.items():
            if key in ('fields', 'trailer', 'default') and isinstance(value, list):
                errors.extend(optional_errors(value, f"{where}[{i}].{key}"))
            elif key in ('match', 'tlv') and isinstance(value, dict):
                cases = value.get('cases')
                for ck, body in (cases.items() if isinstance(cases, dict) else []):
                    if isinstance(body, list):
                        errors.extend(optional_errors(body, f"{where}[{i}].{key}[{ck}]"))
                if isinstance(value.get('default'), list):
                    errors.extend(optional_errors(value['default'],
                                                  f"{where}[{i}].{key}.default"))
            elif key == 'flagged' and isinstance(value, dict):
                for g, group in enumerate(value.get('groups') or []):
                    if isinstance(group, dict):
                        errors.extend(optional_errors(group.get('fields'),
                                                      f"{where}[{i}].flagged[{g}]"))
    return errors


class RaggedTailError(ValueError):
    """PS-343: a repeat's ragged tail. Reported as its own message, which already names
    the repeat; the field-list handler's `Error decoding {name}:` prefix stated the name
    a second time and made the text differ from the other four implementations'."""


def ragged_tail_message(field_def, remaining, element_size, offset):
    """The PS-344 error: the repeat, and the tail as a ragged tail, not an underrun."""
    need = f", fewer than the {element_size} an element takes" if element_size else ""
    return (f"repeat '{field_def.get('name', '?')}' ends in a ragged tail: {remaining} "
            f"byte(s) at offset {offset}{need} (PS-343)")


def repeat_limit_message(field_def, limit, mode, offset, end):
    """The PS-396 error: the repeat, its limit, and the payload left unparsed."""
    return (f"repeat '{field_def.get('name', '?')}' exceeds its max of {limit} element(s) "
            f"({mode}); {end - offset} byte(s) at offset {offset} left unparsed (PS-396)")


_BIT_RANGE = re.compile(r'^[us](\d+)\[(\d+):(\d+)\]$')


#: The whole-integer types a byte_group member may name (PS-070): `u16` is `u16[0:15]`.
#: The word-ordered types are not among them; their bytes are not one value.
WHOLE_INTEGER_TYPES: Dict[str, Tuple[int, bool]] = {
    name: info for name, info in INTEGER_TYPE_INFO.items() if name not in WORD_ORDERED_TYPES
}


def byte_group_member_range(member):
    """(base bytes, low bit, high bit, signed) of a byte_group member (PS-070, CR-2026-131).

    A member is a bit range; a whole integer, which is its full range (`u16` is
    `u16[0:15]`); or a `bool` with `bit`, one bit of a `u8`. Its bits are numbered in the
    group's assembled value (PS-364), and it reads from the group's start: no member
    moves the position, the group consumes its width once. Anything else is rejected.
    """
    name = member.get('name') if isinstance(member, dict) else None
    ftype = str(member.get('type', '')) if isinstance(member, dict) else ''
    match = _BIT_RANGE.match(ftype)
    if match:
        low, high = sorted((int(match.group(2)), int(match.group(3))))
        return max(1, int(match.group(1)) // 8), low, high, ftype.startswith('s')
    if ftype in WHOLE_INTEGER_TYPES:
        size, signed = WHOLE_INTEGER_TYPES[ftype]
        return size, 0, size * 8 - 1, signed
    bit = member.get('bit') if isinstance(member, dict) else None
    if ftype == 'bool' and isinstance(bit, int) and not isinstance(bit, bool):
        if not 0 <= bit <= 7:
            raise ValueError(
                f"byte_group member {name!r} is a bool at bit {bit}; a bool member is one "
                f"bit of a u8, 0 to 7 (PS-070)")
        return 1, bit, bit, False
    raise ValueError(
        f"byte_group member {name!r} is not a bit range, a whole integer or a bool with "
        f"`bit` (PS-364)")


def byte_group_member_bits(member):
    """The bits of the group's assembled value a member covers (PS-364).

    Every member's bits refer to that one value, so they compare directly. They used to
    be mapped most-significant-first within each member's own base, which disagreed with
    the decoders for members of different widths: `u8[4:7]` beside `u16[4:7]` read the
    same bits and passed the check.
    """
    _, low, high, _ = byte_group_member_range(member)
    return set(range(low, high + 1))


def byte_group_endian(field_def, context_endian):
    """A byte_group's effective byte order (PS-364), rejecting an `endian` on a member.

    The group assembles its bytes into one value, so the order is the group's: declared
    in the mapping form, else the context's. A member's own `endian` would split the
    group into values read two ways, and is rejected.
    """
    group = field_def.get('byte_group')
    members = group.get('fields', []) if isinstance(group, dict) else group
    for member in members if isinstance(members, list) else []:
        if isinstance(member, dict) and 'endian' in member:
            raise ValueError(
                f"byte_group member '{member.get('name', '?')}' declares endian; the "
                f"group's byte order is declared on the group (PS-364)")
    declared = group.get('endian') if isinstance(group, dict) else None
    return Endian(declared) if declared else context_endian


def check_byte_group_overlap(members):
    """PS-397: bit ranges within one byte_group must not overlap.

    Overlapping members were accepted by all five implementations, each reporting both
    from the same bits; encoding then OR-ed two values into them. Since CR-2026-131 a
    whole-integer or bool member takes part too, and a member of any other kind is
    rejected here.
    """
    seen = []
    for member in members or []:
        if not isinstance(member, dict):
            continue
        bits = byte_group_member_bits(member)
        for name, other in seen:
            if bits & other:
                raise ValueError(
                    f"byte_group members '{name}' and '{member.get('name', '?')}' "
                    f"overlap (PS-397)")
        seen.append((member.get('name', '?'), bits))


def byte_group_declared_size(field_def):
    """The `size` a byte_group declares - on the mapping form, or beside the array form -
    or None."""
    group = field_def.get('byte_group')
    size = group.get('size') if isinstance(group, dict) else field_def.get('size')
    return None if size is None else int(size)


def byte_group_width(field_def):
    """The bytes a byte_group covers (PS-070, CR-2026-131), checking its members.

    A group that declares no `size` covers its widest member's base type, whichever
    bytes the member's bits fall in: a `u16[0:3]` member makes it two bytes. `size` may
    widen it; a `size` narrower than a member's base is a schema error naming the group.
    Every implementation read a sizeless group as one byte, so a `u16[8:15]` member
    decoded from the wrong byte with nothing reported - while the encoders already
    widened to the base, so the two directions disagreed.
    """
    group = field_def.get('byte_group')
    members = group.get('fields', []) if isinstance(group, dict) else group
    members = [m for m in members or [] if isinstance(m, dict)]
    check_byte_group_overlap(members)
    widest = max((byte_group_member_range(m)[0] for m in members), default=1)
    declared = byte_group_declared_size(field_def)
    if declared is not None and declared < widest:
        raise ValueError(
            f"byte_group declares size {declared}, narrower than its widest member's "
            f"{widest} bytes (PS-070)")
    return max(widest, declared or 1)


def byte_group_errors(node, path=""):
    """Every byte_group in a schema held to PS-070 and PS-397, wherever it sits - top
    level, a port, a definition, a tlv case, a match branch - as `{path}: {message}`.
    A group whose `size` is not an integer is left to the structural check."""
    errors = []
    if isinstance(node, dict):
        group = node.get('byte_group')
        if isinstance(group, (dict, list)):
            size = group.get('size') if isinstance(group, dict) else node.get('size')
            if size is None or (isinstance(size, int) and not isinstance(size, bool)):
                try:
                    byte_group_width(node)
                except ValueError as exc:
                    errors.append(f"{path or 'byte_group'}: {exc}")
        for key, value in node.items():
            if key != 'test_vectors':
                errors.extend(byte_group_errors(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            errors.extend(byte_group_errors(value, f"{path}[{index}]"))
    return errors


_RANGE_NUMBER = r'[+-]?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
_RANGE_KEY = re.compile(r'^\s*(%s)\s*\.\.\s*(%s)\s*$' % (_RANGE_NUMBER, _RANGE_NUMBER))


def _range_bound(text):
    """A range bound as the number it spells: an integer stays one, exactly."""
    text = text.strip()
    if re.match(r'^[+-]?0[xX]', text):
        return int(text, 16)
    try:
        return int(text)
    except ValueError:
        return float(text)


def parse_range_case_key(text):
    """The bounds of a range case key, "2..5" or "12.1..35.4" (PS-398), or None.

    Each bound is a number - a decimal integer, a hexadecimal integer or a decimal with a
    fraction or exponent - and may be negative. An integer bound is kept an integer.
    """
    m = _RANGE_KEY.match(str(text))
    if not m:
        return None
    return _range_bound(m.group(1)), _range_bound(m.group(2))


def parse_list_case_key(text):
    """The values of a quoted list case key, "[1, 2, 0x10]", or None if it is not one."""
    text = str(text).strip()
    if not (text.startswith('[') and text.endswith(']')):
        return None
    values = []
    for part in text[1:-1].split(','):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part, 16) if part.lower().startswith('0x') else int(part))
        except ValueError:
            return None
    return values


def object_key_withdrawn(field_def):
    """The PS-466 error for a field written with the withdrawn `object:` key."""
    return (f"the `object:` key is withdrawn; write `type: object` with `name: "
            f"{field_def.get('object')}` and `fields` (PS-466)")


REF_PREFIX = '#/definitions/'


def expand_refs(schema):
    """Splice every `{$ref: '#/definitions/name'}` into the field list it sits in.

    Returns (expanded schema, errors). The errors are the reasons the schema is invalid
    and must be rejected rather than decoded:

    - a definition that is not a field group `{fields: [...]}` (PS-345);
    - a reference that does not resolve (PS-348), or names a file (PS-462: support is
      optional, and this implementation rejects it rather than guess);
    - a pointer not of the form `#/definitions/<name>` (PS-461);
    - a cycle, direct or transitive (PS-349).

    `definitions` and `test_vectors` are carried over as they are.
    """
    if not isinstance(schema, dict):
        return schema, []
    definitions = schema.get('definitions') or {}
    errors = []
    if not isinstance(definitions, dict):
        return schema, ["'definitions' must map names to field groups (PS-345)"]
    for name, group in definitions.items():
        if not (isinstance(group, dict) and isinstance(group.get('fields'), list)):
            errors.append(
                f"definition '{name}' is not a field group with a `fields` array (PS-345)")

    def resolve(ref, stack):
        if not isinstance(ref, str) or not ref.startswith(REF_PREFIX):
            if isinstance(ref, str) and '#' in ref and not ref.startswith('#'):
                raise ValueError(f"$ref {ref!r} names another document; this implementation "
                                 f"resolves only #/definitions/<name> (PS-462)")
            raise ValueError(f"$ref {ref!r} is not of the form #/definitions/<name> (PS-461)")
        name = ref[len(REF_PREFIX):]
        if name in stack:
            raise ValueError(f"$ref cycle: {' -> '.join(stack + [name])} (PS-349)")
        group = definitions.get(name)
        if not (isinstance(group, dict) and isinstance(group.get('fields'), list)):
            raise ValueError(f"$ref {ref!r} does not resolve to a field group (PS-348)")
        return expand(group['fields'], stack + [name])

    def expand(node, stack):
        if isinstance(node, dict):
            return {k: expand(v, stack) for k, v in node.items()}
        if isinstance(node, list):
            out = []
            for item in node:
                if isinstance(item, dict) and '$ref' in item:
                    out.extend(resolve(item['$ref'], stack))
                else:
                    out.append(expand(item, stack))
            return out
        return node

    expanded = {}
    for key, value in schema.items():
        if key in ('definitions', 'test_vectors'):
            expanded[key] = value
            continue
        try:
            expanded[key] = expand(value, [])
        except ValueError as exc:
            errors.append(str(exc))
            expanded[key] = value
    # A definition no field list reaches is still checked for cycles and dangling refs.
    for name, group in definitions.items():
        if isinstance(group, dict) and isinstance(group.get('fields'), list):
            try:
                expand(group['fields'], [name])
            except ValueError as exc:
                if str(exc) not in errors:
                    errors.append(str(exc))
    return expanded, errors


def fport_declaration_errors(schema):
    """PS-335 and PS-337: what is wrong with a document's top-level `fPort`, if anything.

    The key states which port a document is about (PS-458). It is an integer in the range
    a port key may take (PS-018: 1 to 255), and beside `ports` every key must equal it.
    `fport` is accepted as the same key (PS-338). It selects nothing (PS-336).
    """
    if not isinstance(schema, dict):
        return []
    key = 'fPort' if 'fPort' in schema else 'fport' if 'fport' in schema else None
    if key is None:
        return []
    declared = schema[key]
    if isinstance(declared, bool) or not isinstance(declared, int) or not 1 <= declared <= 255:
        return [f"top-level {key} must be an integer from 1 to 255, got {declared!r} (PS-335)"]
    ports = schema.get('ports') or {}
    others = [k for k in ports if str(k) != str(declared)]
    if others:
        return [f"top-level {key} is {declared} but ports also declares "
                f"{', '.join(str(k) for k in others)}; every ports key must equal it (PS-337)"]
    return []


def typed_field_dicts(node, _top=True):
    """Every mapping carrying a `type` in a schema's field lists (not its vectors)."""
    if isinstance(node, dict):
        if not _top and 'type' in node:
            yield node
        for key, value in node.items():
            if _top and key in ('test_vectors', 'definitions'):
                continue
            yield from typed_field_dicts(value, False)
    elif isinstance(node, list):
        for item in node:
            yield from typed_field_dicts(item, False)


#: The operations a `transform` stage may hold: PS-098's arithmetic, the PS-115 table, and
#: an `op:` stage (PS-390). `decimals` and `ties` are parameters of an `op:` stage and are
#: part of that one operation, not operations of their own (PS-452).
TRANSFORM_OPERATIONS = ('add', 'mult', 'div', 'sqrt', 'abs', 'pow', 'floor', 'ceiling',
                        'log10', 'log', 'op')
TRANSFORM_OP_PARAMETERS = ('decimals', 'ties')


#: CR-2026-159: keys withdrawn from the language, with what replaces them - the spec
#: prototype's key_vocabulary.WITHDRAWN, word for word. Used by no registry, device or
#: example schema when it was withdrawn.
WITHDRAWN_KEYS = {
    'formula': "`formula` is withdrawn; use `compute`, `polynomial`, `transform` or `guard` "
               "(CR-2026-159, PS-448)",
    'encode_formula': "`encode_formula` is withdrawn with `formula` (CR-2026-159, PS-448)",
    'clamp': "`clamp` is withdrawn; use a `floor` stage and a `ceiling` stage "
             "(CR-2026-159, PS-115)",
    'confirmed': "`confirmed` is withdrawn: confirmed or unconfirmed is chosen per "
                 "transmission, not by the schema (CR-2026-159, PS-448)",
}


#: The legacy `type: match` spelling (`on:` for the discriminator, a `default` case for the
#: fallback) is gone; Clause 4's `match:` construct replaces it.
WITHDRAWN_TYPE_MATCH = ("`type: match` with `on:` is withdrawn; write `match: {field: $name, "
                        "cases: ...}` (Clause 4)")


def withdrawn_errors(schema):
    """CR-2026-159: `formula`, `encode_formula` and a port's `confirmed` are rejected at
    load, and so are `min` and `max` on any field that is not a repeat (they bound a
    repeat's element count; a value range is `valid_range`, PS-395). A transform's `clamp`
    stage is transform_stage_errors'. Every message is the prototype's.
    """
    errors = []

    def check_field(node):
        name = node.get('name', '<unnamed>')
        if node.get('type') == 'match':
            errors.append(WITHDRAWN_TYPE_MATCH)
        for key in ('formula', 'encode_formula'):
            if key in node:
                errors.append(WITHDRAWN_KEYS[key])
        if node.get('type') != 'repeat':
            for bound in ('min', 'max'):
                if bound in node:
                    errors.append(
                        f"`{bound}` on field '{name}' is withdrawn: it bounds a repeat's "
                        f"element count only; declare a value range with `valid_range` "
                        f"(CR-2026-159, PS-395)")

    def walk(node, top):
        if isinstance(node, dict):
            if not top and ('type' in node or 'name' in node):
                check_field(node)
            for key, value in node.items():
                if top and key in ('test_vectors', 'definitions'):
                    continue
                walk(value, False)
        elif isinstance(node, list):
            for item in node:
                walk(item, False)

    if isinstance(schema, dict):
        for port, entry in (schema.get('ports') or {}).items():
            if isinstance(entry, dict) and 'confirmed' in entry:
                errors.append(WITHDRAWN_KEYS['confirmed'])
        walk(schema, True)
    return errors


def transform_stage_errors(stages, where):
    """PS-390 and PS-452: each stage holds exactly one operation the language defines.

    A stage holding two - `{add: 1, mult: 2}` - was decoded by every implementation, in
    whichever order each chose: Python applied mult then add, the generated codec only
    one of them. Rejected when the schema is loaded, never applied in part.
    """
    if not isinstance(stages, list):
        return [f"{where}: `transform` must be a list of stages (PS-102)"]
    errors = []
    for index, stage in enumerate(stages):
        at = f"{where}: transform stage {index}"
        if not isinstance(stage, dict):
            errors.append(f"{at} is {stage!r}, not a mapping holding one operation (PS-452)")
            continue
        if 'round' in stage:
            errors.append(f"{at}: `{{round: n}}` is not a transform stage; write "
                          f"{{op: round, decimals: n}} (PS-390)")
            continue
        if 'clamp' in stage:
            errors.append(WITHDRAWN_KEYS['clamp'])
            continue
        unknown = [k for k in stage
                   if k not in TRANSFORM_OPERATIONS and k not in TRANSFORM_OP_PARAMETERS]
        if unknown:
            errors.append(f"{at} names no operation of the PS-115 table: "
                          f"{', '.join(map(str, unknown))} (PS-390)")
            continue
        operations = [k for k in stage if k in TRANSFORM_OPERATIONS]
        if len(operations) != 1:
            held = ', '.join(operations) if operations else 'none'
            errors.append(f"{at} must hold exactly one operation, and holds {held} (PS-452)")
            continue
        if 'op' not in stage and any(k in stage for k in TRANSFORM_OP_PARAMETERS):
            errors.append(f"{at}: `decimals` and `ties` belong to an `op:` stage (PS-452)")
        elif stage.get('op') == 'round' and stage.get('ties', 'even') not in (
                'even', 'away', 'up'):
            errors.append(f"{at}: a round stage's `ties` is `even`, `away` or `up`, "
                          f"not {stage['ties']!r} (PS-390)")
    return errors


def arithmetic_errors(field_def):
    """CR-2026-071/073: the arithmetic a field declares is one it can carry.

    PS-445: a `guard` belongs to a computed field. On a field read from the payload it was
    ignored by every implementation, so a schema meaning "no reading below this" decoded
    every value; `sentinel` and `out_of_range: omit` say that for a read value.
    PS-452: one operation per transform stage.
    """
    if not isinstance(field_def, dict):
        return []
    name = field_def.get('name', '?')
    errors = []
    if 'guard' in field_def and not (
            field_def.get('type') in COMPUTED_TYPES
            and ('ref' in field_def or 'compute' in field_def)):
        errors.append(f"Field '{name}': a guard is declared only on a computed field (ref "
                      f"or compute); for a value read from the payload use sentinel or "
                      f"out_of_range: omit (PS-445)")
    if 'transform' in field_def:
        errors.extend(transform_stage_errors(field_def['transform'], f"Field '{name}'"))
    return errors


def arithmetic_schema_errors(node, _top=True):
    """arithmetic_errors() for every field mapping declaring a guard or a transform.

    Walked separately from typed_field_dicts(): a guard on a mapping with no `type` - a
    byte_group entry, a match - is as invalid as one on a `u8` (PS-445), and a walk that
    only visits typed fields let it through with success.
    """
    errors = []
    if isinstance(node, dict):
        if not _top and ('guard' in node or 'transform' in node):
            errors.extend(arithmetic_errors(node))
        for key, value in node.items():
            if _top and key in ('test_vectors', 'definitions'):
                continue
            if key in ('guard', 'transform'):
                continue        # their own contents are not fields
            errors.extend(arithmetic_schema_errors(value, False))
    elif isinstance(node, list):
        for item in node:
            errors.extend(arithmetic_schema_errors(item, False))
    return errors


#: CR-2026-110 (PS-525): how a name reported by more than one tlv entry combines.
ON_REPEAT = ('collect', 'merge', 'last')

#: The constructs whose fields report into the enclosing object, so a tlv case reports
#: their names as its own (the spec prototype's STRUCTURAL_KEYS).
_REPORTING_CONSTRUCTS = ('match', 'byte_group', 'flagged', 'tlv')


def reported_fields(fields):
    """Every named field a field list reports, by name, first declaration first, looking
    through the constructs whose fields merge into the enclosing object. An object's or
    repeat's members are not descended into: they report under the field's own name.
    The spec prototype's `declared_fields`."""
    found = {}  # type: Dict[str, Dict[str, Any]]

    def walk(items):
        for item in items or []:
            if not isinstance(item, dict):
                continue
            if 'type' in item:
                if item.get('name'):
                    found.setdefault(item['name'], item)
                walk(item.get('trailer'))
                continue
            spec = next((item[k] for k in _REPORTING_CONSTRUCTS if k in item), None)
            if isinstance(spec, list):
                walk(spec)
            elif isinstance(spec, dict):
                walk(spec.get('fields'))
                for body in (spec.get('cases') or {}).values():
                    walk(body if isinstance(body, list) else None)
                for group in spec.get('groups') or []:
                    walk(group.get('fields') if isinstance(group, dict) else None)
    walk(fields)
    return found


def tlv_repeat_modes(tlv_def):
    """How each name a tlv reports combines when more than one entry reports it (PS-157,
    PS-525, PS-526): (mode by name, the members of each merged object that declare
    `last`, the schema errors).

    A field's own `on_repeat` takes precedence over the tlv's. A tlv's `merge` reaches
    its `type: object` fields only; any other field keeps `collect`, since only an
    object can merge. With no `on_repeat` anywhere every field collects, as PS-157
    always has.
    Every declaration of one name must agree. The wording is the spec prototype's.
    """
    errors = []  # type: List[str]
    modes = {}  # type: Dict[str, str]
    last_members = {}  # type: Dict[str, set]
    shared = {}  # type: Dict[tuple, list]
    if not isinstance(tlv_def, dict):
        return modes, last_members, errors
    default = tlv_def.get('on_repeat', 'collect')
    if default not in ON_REPEAT:
        errors.append(f"tlv `on_repeat` is one of {', '.join(ON_REPEAT)}, not "
                      f"{default!r} (PS-525)")
        default = 'collect'
    for body in (tlv_def.get('cases') or {}).values():
        if not isinstance(body, list):
            continue
        for name, field_def in reported_fields(body).items():
            own = field_def.get('on_repeat')
            if own is not None and own not in ON_REPEAT:
                errors.append(f"Field '{name}': `on_repeat` is one of "
                              f"{', '.join(ON_REPEAT)}, not {own!r} (PS-525)")
                continue
            is_object = field_def.get('type') == 'object'
            if own == 'merge' and not is_object:
                errors.append(f"Field '{name}': only a `type: object` field may "
                              f"declare `on_repeat: merge` (PS-526)")
                continue
            mode = own or (default if default != 'merge' or is_object else 'collect')
            if modes.setdefault(name, mode) != mode:
                errors.append(f"tlv: the fields reporting '{name}' declare different "
                              f"`on_repeat`, {modes[name]} and {mode} (PS-526)")
                continue
            if mode == 'merge':
                last_members.setdefault(name, set()).update(
                    member['name'] for member in field_def.get('fields') or []
                    if isinstance(member, dict) and member.get('on_repeat') == 'last'
                    and member.get('name'))
                for member in field_def.get('fields') or []:
                    if isinstance(member, dict) and member.get('name'):
                        shared.setdefault((name, member['name']), []).append(member)
    for (name, member), declarations in shared.items():
        # CR-2026-128: merged cases share a member only where it declares `last`.
        if len(declarations) > 1 and any(d.get('on_repeat') != 'last'
                                         for d in declarations):
            errors.append(f"tlv: cases share the member '{member}' of the merged object "
                          f"'{name}' without `on_repeat: last` (PS-526)")
    return modes, last_members, errors


#: Mappings whose keys are data, not schema, so nothing under them declares a field -
#: and `definitions`, which is held to the rule where a `$ref` splices it.
_ON_REPEAT_OPAQUE = ('lookup', 'values', 'test_vectors', 'metadata', 'definitions')


def on_repeat_schema_errors(node):
    """tlv_repeat_modes()'s errors for every tlv in a schema, wherever it sits (PS-526),
    and every `on_repeat` declared where it has no effect (PS-525, CR-2026-125): it
    belongs on a tlv, on a field a tlv case reports, and as `last` on a member of a
    merged object. A schema breaking either is invalid whether or not a payload
    reaches it. The placement wording is the spec prototype's."""
    errors = []  # type: List[str]
    allowed = set()

    def tlvs(item):
        if isinstance(item, list):
            for child in item:
                tlvs(child)
        elif isinstance(item, dict):
            tlv = item.get('tlv')
            if isinstance(tlv, dict):
                allowed.add(id(tlv))
                modes, _, tlv_errors = tlv_repeat_modes(tlv)
                errors.extend(tlv_errors)
                for body in (tlv.get('cases') or {}).values():
                    if not isinstance(body, list):
                        continue
                    for name, field_def in reported_fields(body).items():
                        allowed.add(id(field_def))
                        if modes.get(name) != 'merge':
                            continue
                        for member in field_def.get('fields') or []:
                            if isinstance(member, dict) \
                                    and member.get('on_repeat') == 'last':
                                allowed.add(id(member))
            for key, value in item.items():
                if key not in _ON_REPEAT_OPAQUE:
                    tlvs(value)

    def strays(item):
        if isinstance(item, list):
            for child in item:
                strays(child)
        elif isinstance(item, dict):
            if 'on_repeat' in item and id(item) not in allowed:
                errors.append(
                    f"`on_repeat` on '{item.get('name', 'an entry')}' has no effect "
                    f"there: it belongs on a tlv, a field of a tlv case, or a merged "
                    f"object's member (PS-525)")
            for key, value in item.items():
                if key not in _ON_REPEAT_OPAQUE:
                    strays(value)

    tlvs(node)
    strays(node)
    return errors


def _reports_list(field_def):
    """Whether a field's own value is a list - a repeat, or bytes rendered as an array -
    so that a list under its name is not PS-157's collection of several entries."""
    kind = field_def.get('type')
    return kind == 'repeat' or (kind == 'bytes' and field_def.get('format') == 'array')


def tlv_encode_plan(tlv_def, modes, last_members):
    """What PS-527 needs of a tlv's cases: `members`, for each merged object, every
    member some case declares - a supplied member outside it is an error. Shared
    members are `last` ones, as PS-526 requires, so each written case carries them.
    `last_members` is accepted for the callers' symmetry with tlv_repeat_modes."""
    members = {}  # type: Dict[str, set]
    for case_key, body in (tlv_def.get('cases') or {}).items():
        if case_key == 'default' or not isinstance(body, list):
            continue
        for name, field_def in reported_fields(body).items():
            if modes.get(name) != 'merge':
                continue
            for member in field_def.get('fields') or []:
                if isinstance(member, dict) and member.get('name'):
                    members.setdefault(name, set()).add(member['name'])
    return {'members': members}


def combine_repeated(name, earlier, value, mode, last_members):
    """`last` keeps the later value; `merge` combines two objects member by member,
    and a member reported twice is an error unless it declares `on_repeat: last`
    (PS-525)."""
    if mode == 'last':
        return value
    combined = dict(earlier)
    for member, member_value in value.items():
        if member in combined and member not in last_members:
            raise ValueError(f"tlv: the merged object '{name}' reports its member "
                             f"'{member}' twice (PS-525)")
        combined[member] = member_value
    return combined


def annotation_errors(field_def):
    """PS-510 (CR-2026-102): a field's `ipso` is a mapping carrying `object`, and its
    `senml` a mapping. `ipso: 3303` loaded and every implementation dropped it, so the
    author believed the field annotated and no consumer saw an annotation. The wording
    is the spec prototype's, and Go, Java and C# give it identically."""
    if not isinstance(field_def, dict):
        return []
    name = field_def.get('name', '?')
    errors = []
    if 'ipso' in field_def and not (isinstance(field_def['ipso'], dict)
                                    and 'object' in field_def['ipso']):
        errors.append(f"The ipso of '{name}' is a mapping that carries `object`, such as "
                      f"{{object: 3303}}; a bare object number is not a shorthand (PS-510)")
    if 'senml' in field_def and not isinstance(field_def['senml'], dict):
        errors.append(f"The senml of '{name}' is a mapping, such as {{unit: Cel}} (PS-510)")
    return errors


def annotation_schema_errors(node, _top=True):
    """annotation_errors() for every field mapping, wherever it sits: a port list, a case
    body, an object's or repeat's members, a byte_group or flagged group. Walked like
    arithmetic_schema_errors(), over the schema after its `$ref`s are spliced, and over
    `definitions` too, referenced or not, as Go and C# parse them. The legacy
    `semantic: {ipso: N}` is a different key, outside PS-510, and is not descended into."""
    errors = []
    if isinstance(node, dict):
        if not _top:
            errors.extend(annotation_errors(node))
        for key, value in node.items():
            if _top and key == 'test_vectors':
                continue
            if key in ('ipso', 'senml', 'semantic'):
                continue        # their own contents are not fields
            errors.extend(annotation_schema_errors(value, False))
    elif isinstance(node, list):
        for item in node:
            errors.extend(annotation_schema_errors(item, False))
    return errors


#: Keys whose string value may be a `$` reference (PS-539), as the spec prototype's
#: key_vocabulary.REFERENCE_KEYS lists them.
REFERENCE_KEYS = ('ref', 'a', 'b', 'field', 'count', 'byte_length', 'carry', 'instance',
                  'unit', 'identity', 'interval')
#: Keys whose string value is a template of `${name}` references (PS-265, PS-373).
TEMPLATE_KEYS = ('name_from',)
#: Keys under which a field tree holds data, not declarations or references. A
#: `default` is data too, except a match's, which is a field list.
REFERENCE_DATA_KEYS = ('lookup', 'values', 'else', 'value')
TEMPLATE_REFERENCE = re.compile(r'\$\{(\w+)\}')
#: Keys walked before the rest of their mapping, because what the rest reads comes after
#: them: a tlv's tag components before its cases, a repeat's trailer (read first, PS-383)
#: and its elements before its `present_if` and `identity` (PS-539, CR-2026-127).
REFERENCE_FIRST_KEYS = ('tag_fields', 'trailer', 'fields', 'element')


def reference_errors(schema):
    """PS-539 (CR-2026-117, CR-2026-127): every `$` reference names something the schema
    declares - a field, a `var`, a tag component, a repeat's `index` or `count_as` - and
    the declaration comes before the reference in schema order (PS-122, PS-147): a tlv's
    tag components before its cases, a repeat's `trailer` and elements before its
    `present_if` and `identity`. The schema is walked with every `$ref` already spliced,
    so a definition's references are checked where it is used, after the fields declared
    before the splice, and an unused definition is never checked. Each port's field list
    is a decode of its own (CR-2026-130): it sees what the top level declares and its own
    names, never another port's; such a reference reads as declared too late. Within a
    mapping, `tag_fields`, `trailer`, `fields` and `element` come first, then the rest in
    source order (CR-2026-127). A nameless repeat
    element declares no name (PS-528). `metadata` include and timestamp names count as
    declared first; a `metadata.include` `source` names runtime input (PS-315) and is not
    checked. Whether a name declared earlier is bound when it is read is a decode matter
    (PS-314), so a name in a branch not taken is absent, not an error.

    The walk is the spec prototype's `key_vocabulary.check_references`; Go, Java and C#
    walk the spliced document the same way and give the messages identically."""
    if not isinstance(schema, dict):
        return []
    declared, ahead = set(), []

    def walk(node, key=None, in_senml=False):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if not in_senml:
            for name_key in ('name', 'var', 'index', 'count_as'):
                if isinstance(node.get(name_key), str):
                    declared.add(node[name_key])
        # What the rest of an entry reads comes first (REFERENCE_FIRST_KEYS).
        first = [k for k in REFERENCE_FIRST_KEYS if k in node]
        for k in first + [k for k in node if k not in first]:
            v = node[k]
            if k in REFERENCE_DATA_KEYS:
                continue
            if isinstance(v, str):
                names = []
                if k in REFERENCE_KEYS and v.startswith('$'):
                    # PS-546: `$name[i]` names a sibling repeat and this one's index.
                    indexed = re.fullmatch(r'(\w+)\[(\w+)\]', v[1:])
                    names = list(indexed.groups()) if indexed else [v[1:]]
                elif k in TEMPLATE_KEYS or (in_senml and k == 'name'):
                    names = TEMPLATE_REFERENCE.findall(v)
                ahead.extend((n, k) for n in names if n not in declared)
            elif k == 'default':
                if key == 'match' and isinstance(v, (dict, list)):
                    walk(v)                     # a match's default is a field list
            else:
                walk(v, k, in_senml=(k == 'senml'))

    metadata = schema.get('metadata')
    if isinstance(metadata, dict):
        for entry in (metadata.get('include') or []) + (metadata.get('timestamps') or []):
            if isinstance(entry, dict) and isinstance(entry.get('name'), str):
                declared.add(entry['name'])
    for part in ('header', 'fields'):
        walk(schema.get(part))
    # Each port is a decode of its own (CR-2026-130): what one port declares is never bound
    # while another is decoded, so each starts from what the top level declares.
    shared, seen = set(declared), set(declared)
    if isinstance(schema.get('ports'), dict):
        for group in schema['ports'].values():
            declared.clear()
            declared.update(shared)
            walk(group)
            seen |= declared
    declared.clear()
    declared.update(seen)       # to tell "never declared" from "declared too late"
    errors = []
    for name, key in ahead:
        message = (reference_error_message(name, key) if name not in declared
                   else reference_order_message(name, key))
        if message not in errors:
            errors.append(message)
    return errors


def reference_error_message(name, key):
    """PS-539's wording, the spec prototype's, identical in Go, Java and C#."""
    return (f"`${name}` ({key}) names no field, var, tag component, index or count_as "
            f"the schema declares (PS-539)")


def reference_order_message(name, key):
    """PS-539's ordering rule (CR-2026-127), in the spec prototype's wording."""
    return f"`${name}` ({key}) is referenced before the schema declares it (PS-539, PS-122)"


#: The literal types (PS-357). `boolean` is a literal type only (PS-519): it is not
#: an alias of `bool`, which reads a bit (PS-065).
LITERAL_TYPES = ('string', 'number', 'boolean')


def is_literal(field_def):
    """A `string`, `number` or `boolean` field declaring `value`: a constant read from
    no bytes (PS-357)."""
    return (isinstance(field_def, dict) and field_def.get('type') in LITERAL_TYPES
            and 'value' in field_def)


LITERAL_FORBIDDEN = ('ref', 'polynomial', 'compute', 'lookup', 'transform', 'mult', 'div', 'add')


def literal_errors(field_def):
    """PS-358: a literal's value matches its type, and it carries no arithmetic.

    PS-519: `boolean` is a literal type only, so it must declare `value`. PS-520: its
    value is a boolean; `"true"`, `1` or `null` is refused. A YAML 1.1 reader such as
    PyYAML hands this `yes` as True already, and PS-520 does not ask a decoder to
    detect that: authors write `true` or `false`.
    """
    if (isinstance(field_def, dict) and field_def.get('type') == 'boolean'
            and 'value' not in field_def):
        return [f"Field '{field_def.get('name', '?')}': type boolean is a literal and "
                f"declares no value (PS-519)"]
    if not is_literal(field_def):
        return []
    name, value, ftype = field_def.get('name', '?'), field_def['value'], field_def['type']
    errors = []
    if ftype == 'string' and not isinstance(value, str):
        errors.append(f"Field '{name}': a string literal's value must be a string (PS-358)")
    if ftype == 'number' and (isinstance(value, bool) or not isinstance(value, (int, float))):
        errors.append(f"Field '{name}': a number literal's value must be a number (PS-358)")
    if ftype == 'boolean' and not isinstance(value, bool):
        errors.append(f"Field '{name}': a boolean literal's value must be true or false, "
                      f"not {value!r} (PS-520)")
    extra = [k for k in LITERAL_FORBIDDEN if k in field_def]
    if extra:
        errors.append(f"Field '{name}': a literal must not declare {', '.join(extra)} (PS-358)")
    return errors


ENCODINGS = ('sign_magnitude', 'bcd', 'gray')
_UNSIGNED_INTEGER_TYPES = ('u8', 'u16', 'u24', 'u32', 'u64',
                           'uint8', 'uint16', 'uint24', 'uint32', 'uint64')


def encoding_errors(field_def):
    """PS-422 and PS-426: `encoding` only on uN and only a named code; no `match_value`."""
    errors = []
    name = field_def.get('name', '?') if isinstance(field_def, dict) else '?'
    if not isinstance(field_def, dict):
        return errors
    if 'match_value' in field_def:
        errors.append(
            f"Field '{name}': match_value is withdrawn; write a signed type (sN), a signed "
            f"bit range (sN[start:end]), encoding, match or guard instead (PS-426)")
    if 'encoding' in field_def:
        if field_def['encoding'] not in ENCODINGS:
            errors.append(f"Field '{name}': encoding {field_def['encoding']!r} is not one of "
                          f"{', '.join(ENCODINGS)} (PS-422)")
        elif field_def.get('type') not in _UNSIGNED_INTEGER_TYPES:
            errors.append(f"Field '{name}': encoding applies only to an unsigned integer "
                          f"type uN, not {field_def.get('type')!r} (PS-422)")
    return errors


def reverse_enum(value, field_def):
    """The code an enum reported `value` for, or `value` where none is found
    (CR-2026-132: a reference to an enum binds its code)."""
    values = field_def.get('values') or {}
    if isinstance(values, dict):
        codes = [lookup_key_code(key) for key, entry in values.items()
                 if enum_label(entry) == value]
    elif isinstance(values, list):
        codes = [i for i, entry in enumerate(values) if enum_label(entry) == value]
    else:
        codes = []
    if None in codes:
        return value
    if len(codes) > 1:
        raise shared_label_error(field_def.get('name', '?'), value, codes)  # PS-169
    if codes:
        return codes[0]
    if isinstance(value, str) and value.startswith('unknown(') and value.endswith(')'):
        try:
            return int(value[8:-1])
        except ValueError:
            return value
    return value


def enum_label(entry):
    """What an enum value reports: its `name` where it is the description form (PS-394).

    The `description` is metadata and never the value. This returned the whole mapping,
    so a decoded enum reported {name: standby, description: ...} as its value.
    """
    if isinstance(entry, dict) and 'name' in entry:
        return entry['name']
    return entry


def round_decimal(value, decimals=0, ties='even'):
    """Round to `decimals` places on the value itself (PS-390).

    `ties` is `even` (the default), `away` from zero, or `up` toward +infinity (a negative
    tie rounds toward zero, as JavaScript's Math.round does). Decimal(float) is the exact
    binary value, so a value stored just below a tie rounds down as it should, and an
    exact tie such as 78.125 is recognised as one - rounding value * 10**d instead would
    invent ties. `away` exists for vendor decoders that use JavaScript's toFixed, `up` for Math.round.
    """
    from decimal import (Decimal, ROUND_HALF_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP,
                         localcontext)
    if ties not in ('even', 'away', 'up'):
        raise ValueError(
            f"round `ties` must be 'even', 'away' or 'up', got {ties!r} (PS-390)")
    if isinstance(decimals, bool) or not isinstance(decimals, int) or decimals < 0:
        raise ValueError(
            f"round `decimals` must be a non-negative integer, got {decimals!r} (PS-390)")
    if value is OMITTED or isinstance(value, bool):
        return value
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return value
    exact = Decimal(value)
    if ties == 'even':
        mode = ROUND_HALF_EVEN
    elif ties == 'up' and exact < 0:
        mode = ROUND_HALF_DOWN     # a negative tie goes toward +infinity: toward zero
    else:
        mode = ROUND_HALF_UP
    # quantize needs every digit of the result, and the default context holds 28: an f32
    # near its maximum, 3.4e38, to 6 places is 45 digits, so it raised InvalidOperation
    # and failed the decode where every other implementation reported the value.
    with localcontext() as context:
        context.prec = max(context.prec, exact.adjusted() + decimals + 2)
        rounded = float(exact.quantize(Decimal(1).scaleb(-decimals), rounding=mode))
    return 0.0 if rounded == 0 else rounded


class LookupIndexError(ValueError):
    """An out-of-bounds index into a sequence ``lookup`` (PS-105).

    Every implementation used to report the raw index instead, silently, so a payload
    that did not match its schema decoded as though it did.
    """


class FieldError(ValueError):
    """An error whose text already names its field in full, reported verbatim.

    An enclosing construct must not prefix it ("Error in tlv: ...") or name another
    field: the message is compared as an exact string across the five implementations.
    """
    named = True


class BufferUnderflow(FieldError):
    """A read or skip past the end of the payload, less any `reserve` (PS-535).

    Raised unnamed by the read; the innermost field that made the read restates it as
    `Field '<name>': ... (PS-535)` (`_name_underflow`), and every enclosing field then
    passes it through unchanged. The text is the spec prototype's.
    """
    named = False

    @classmethod
    def need(cls, length: int, pos: int, end: int) -> 'BufferUnderflow':
        return cls(f"Buffer underflow: need {length} bytes at offset {pos}, "
                   f"but only {max(0, end - pos)} remaining")

    @classmethod
    def peek(cls, pos: int) -> 'BufferUnderflow':
        # A bit range or a bool reads its byte(s) without consuming them.
        return cls(f"Buffer underflow at peek offset {pos}")


def byte_group_label(members: Any) -> str:
    """How PS-535 identifies a byte_group: by its members, as the spec prototype does -
    `byte_group of 'f', 'w'`, every named member in declaration order, internal `_` members
    included - else `an unnamed byte_group` (CR-2026-131)."""
    names = [m.get('name') for m in members or [] if isinstance(m, dict) and m.get('name')]
    if not names:
        return 'an unnamed byte_group'
    return 'byte_group of ' + ', '.join(f"'{n}'" for n in names)


def underflow_label(name: Any, kind: Any) -> str:
    """How a PS-535 error identifies a field: its name, else what it is."""
    return f"'{name}'" if name else f"an unnamed {kind or 'field'}"


def name_underflow(exc: 'BufferUnderflow', name: Any, kind: Any) -> FieldError:
    """PS-535: restate an unnamed short read as the error of the field that made it."""
    if exc.named:
        return exc
    named = BufferUnderflow(f"Field {underflow_label(name, kind)}: {exc} (PS-535)")
    named.named = True
    named.detail = str(exc)
    return named


def error_text(prefix: str, exc: BaseException) -> str:
    """An error for `result.errors`: a FieldError verbatim, anything else prefixed."""
    if getattr(exc, 'named', False):
        return str(exc)
    return f"{prefix}{exc}"


_NAMED_UNDERFLOW = re.compile(r"^Field .*\(PS-535\)$", re.S)


def reraise_text(text: str) -> ValueError:
    """The exception for a nested field list's first error, keeping a PS-535 message
    verbatim so the enclosing field does not prefix it."""
    if _NAMED_UNDERFLOW.match(text):
        exc = BufferUnderflow(text)
        exc.named = True
        return exc
    return ValueError(text)


ASCII_HIGH_BYTE_WARNING = "Field '{}': a byte above 0x7F in an ascii value (PS-073, PS-538)"


def decode_ascii(name: Any, raw: bytes, warnings: List[str]) -> str:
    """PS-534, PS-538: each byte is the ISO 8859-1 character of the same number, so the
    value is lossless and re-encodes to the same bytes (PS-537); trailing 0x00 padding
    only is dropped. A byte above 0x7F is reported with PS-073's warning."""
    if any(byte > 0x7F for byte in raw):
        warnings.append(ASCII_HIGH_BYTE_WARNING.format(name))
    return raw.decode('latin-1').rstrip('\x00')


#: PS-560: the whole text of a `numeral` field, by base.
NUMERAL_PATTERNS = {'decimal': re.compile(r'[+-]?[0-9]+'), 'hex': re.compile(r'[0-9A-Fa-f]+')}


def decode_numeral(name: Any, text: str, numeral: str) -> int:
    """PS-560: the whole text is a numeral of the declared base, or an error naming the
    field; never a prefix, as JavaScript's parseInt would take."""
    if not NUMERAL_PATTERNS[numeral].fullmatch(text):
        raise FieldError(f"Field '{name}': '{text}' is not a {numeral} numeral (PS-560)")
    return int(text, 10 if numeral == 'decimal' else 16)


def encode_numeral(name: Any, value: Any, length: Optional[int], numeral: str) -> str:
    """PS-561: digits in the declared base, uppercase hex, `0`-filled to `length` after any
    sign; a negative hex value, or one that does not fit, is a PS-168 error."""
    n = round_to_integer(value)                                        # PS-552
    digits = format(abs(n), 'd' if numeral == 'decimal' else 'X')
    sign = '-' if n < 0 else ''
    width = len(sign) + len(digits) if length is None else length
    if (n < 0 and numeral == 'hex') or len(sign) + len(digits) > width:
        raise FieldError(f"Field '{name}': {format_lookup_value(value)} does not fit "
                         f"{length if length is not None else len(digits)} {numeral} digits (PS-168)")
    return sign + digits.rjust(width - len(sign), '0')


# PS-430, PS-582, PS-583 (CR-2026-170): a bitfield_string part format is decimal, sdecimal, hex
# or hex:upper, optionally followed by `:N`, a minimum width from 1 to 32 that never cuts.
# One helper per job, so the template formats of CR-2026-176 can reuse format_part_number.
PART_BASES = ('decimal', 'sdecimal', 'hex', 'hex:upper')


def part_format_error(fmt: Any) -> Optional[str]:
    """The PS-430 rejection of a part format, in the spec prototype's text, or None."""
    try:
        parse_part_format(fmt)
    except ValueError as exc:
        return str(exc)
    return None


def parse_part_format(fmt: Any) -> Tuple[str, Optional[int]]:
    """A part format's base and its minimum width (None where it declares none). Any other
    format - a width of 0 or over 32, a zero-led width, another base - is a ValueError
    (PS-430)."""
    text = fmt if isinstance(fmt, str) else None
    if text is not None:
        for base in sorted(PART_BASES, key=len, reverse=True):
            if text == base:
                return base, None
            if text.startswith(base + ':'):
                width = text[len(base) + 1:]
                if re.fullmatch(r'[1-9][0-9]?', width) and int(width) <= 32:
                    return base, int(width)
    raise ValueError(f"bitfield_string part format must be decimal, sdecimal, hex or "
                     f"hex:upper, with an optional :N width from 1 to 32, not {fmt!r} (PS-430)")


def format_part_number(number: int, base: str, width: Optional[int]) -> str:
    """An integer in a part format's base (PS-430): lower- or upper-case hex, else decimal; a
    negative one with a leading `-` before the padding (PS-582); zero-filled on the left to
    `width` digits, never cut."""
    digits = (format(abs(number), 'x') if base == 'hex' else
              format(abs(number), 'X') if base == 'hex:upper' else str(abs(number)))
    if width is not None:
        digits = digits.rjust(width, '0')
    return ('-' if number < 0 else '') + digits


def format_bitfield_part(raw: int, bits: int, fmt: Any) -> str:
    """One part of a bitfield_string from its unsigned bits: `sdecimal` reads them as two's
    complement of the part's width (PS-582)."""
    base, width = parse_part_format(fmt)
    if base == 'sdecimal' and bits > 0 and raw >= 1 << (bits - 1):
        raw -= 1 << bits
    return format_part_number(raw, base, width)


_SEGMENT_PATTERNS = {'decimal': re.compile(r'[0-9]+'), 'sdecimal': re.compile(r'-?[0-9]+'),
                     'hex': re.compile(r'[0-9A-Fa-f]+'), 'hex:upper': re.compile(r'[0-9A-Fa-f]+')}


def parse_bitfield_segment(segment: str, bits: int, fmt: Any, name: Any) -> int:
    """One segment back to its part's unsigned bits (PS-096, PS-431, PS-583): leading zeros
    are accepted, a hex segment without regard to case, a `-` only on `sdecimal`. A value the
    part's width cannot hold is rejected, never masked."""
    base, _ = parse_part_format(fmt)
    text = segment.strip()
    if not _SEGMENT_PATTERNS[base].fullmatch(text):
        raise FieldError(f"Field '{name}': segment {segment!r} is not a {base} part (PS-096)")
    number = int(text, 16 if base.startswith('hex') else 10)
    low, high = ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if base == 'sdecimal'
                 else (0, (1 << bits) - 1))
    if not low <= number <= high:
        raise FieldError(f"Field '{name}': segment {segment!r} does not fit a {bits}-bit "
                         f"{base} part, {low} to {high} (PS-583)")
    return number & ((1 << bits) - 1)


def bitfield_part_errors(node, _top=True):
    """PS-430: every bitfield_string part's format, walked over the whole schema."""
    errors = []
    if isinstance(node, list):
        for item in node:
            errors.extend(bitfield_part_errors(item, False))
    elif isinstance(node, dict):
        if node.get('type') == 'bitfield_string' and isinstance(node.get('parts'), list):
            for part in node['parts']:
                if isinstance(part, list) and len(part) > 2:
                    problem = part_format_error(part[2])
                    if problem:
                        errors.append(problem)
        for key, value in node.items():
            if _top and key == 'test_vectors':
                continue
            errors.extend(bitfield_part_errors(value, False))
    return errors


def encode_ascii(name: Any, value: Any, length: Optional[int],
                 warnings: List[str]) -> bytes:
    """PS-537: a character above U+00FF, or a value longer than a fixed `length`, is
    rejected naming the field; U+0080..U+00FF is written as the byte of the same number
    with a warning; a short value is padded with 0x00. `length` is None where the field's
    length is not fixed (`remaining`, a reference), and the value then supplies it."""
    text = str(value)
    if any(ord(ch) > 0xFF for ch in text):
        raise FieldError(f"Field '{name}': an ascii value holds a character above "
                         f"U+00FF, which has no byte (PS-537)")
    encoded = text.encode('latin-1')
    if length is not None and len(encoded) > length:
        raise FieldError(f"Field '{name}': an ascii value of {len(encoded)} bytes is "
                         f"longer than its length of {length} (PS-537)")
    if any(byte > 0x7F for byte in encoded):
        warnings.append(f"Field '{name}': a character above U+007F in an ascii value "
                        f"(PS-537)")
    if length is None:
        return encoded
    return encoded + b'\x00' * (length - len(encoded))


#: The token a mapping's `default` substitutes with the unmapped value (PS-406).
LOOKUP_VALUE_TOKEN = '${value}'

#: A decimal number as format_lookup_value writes one, for recovering it on encode.
_LOOKUP_NUMBER = r'(-?\d+(?:\.\d+)?(?:e[-+]?\d+)?)'


def lookup_template(lookup):
    """The mapping's `default` where it carries `${value}` (PS-406), else None.

    Only a mapping's default is a template (PS-408): a label, or a sequence, is never one.
    """
    if not isinstance(lookup, dict):
        return None
    default = lookup.get('default')
    if isinstance(default, str) and LOOKUP_VALUE_TOKEN in default:
        return default
    return None


def format_lookup_value(value):
    """The value written into a `${value}` default: decimal, no fraction where integral.

    PS-406 and PS-280. Written as JavaScript's String(number) writes it, which is what
    the generated codec produces: the shortest round-tripping digits, in fixed notation
    for 1e-6 <= |v| < 1e21 and as 1e-7 / 1.5e+21 outside that range.
    """
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        value = int(value)
    if isinstance(value, int) and abs(value) < 10 ** 21:
        return str(value)
    value = float(value)
    text = repr(value)
    if 1e-6 <= abs(value) < 1e21:
        if 'e' in text:
            from decimal import Decimal
            text = format(Decimal(text), 'f')
        return text
    from decimal import Decimal
    digits, exponent = format(Decimal(text).normalize(), 'e').split('e')
    if digits.endswith('.0'):
        digits = digits[:-2]
    sign = '-' if exponent.startswith('-') else '+'
    return f"{digits}e{sign}{exponent.lstrip('+-').lstrip('0') or '0'}"


def render_template_value(value, reference, template):
    """The text a key template writes for a referenced value (PS-532, CR-2026-112).

    A number as a `${value}` default writes it (PS-406: JavaScript's String(number), so
    `2` not `2.0` and `1e-7` not `1e-07`), a string as it is, a boolean as `true` or
    `false`. Python's str() wrote `True` and `1e-07`, so one schema reported a different
    key here than in the generated codec. An object or array has no text: it is an error
    naming the reference and the template.
    """
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return format_lookup_value(value)
    if isinstance(value, str):
        return value
    raise ValueError(
        "template %r references %r, which is not a number, string or boolean "
        "and cannot be written into a key (PS-532)" % (template, reference))


def lookup_template_pattern(template):
    """A regular expression for every string a `${value}` default can produce."""
    parts = template.split(LOOKUP_VALUE_TOKEN)
    body = re.escape(parts[0])
    for part in parts[1:]:
        body += _LOOKUP_NUMBER + re.escape(part)
    return '^' + body + '$'


def match_lookup_template(template, text):
    """The number a `${value}` default wrote into `text`, or None if it does not match.

    Every `${value}` in the template must hold the same number (PS-409).
    """
    parts = template.split(LOOKUP_VALUE_TOKEN)
    pattern = re.escape(parts[0]) + _LOOKUP_NUMBER
    for part in parts[1:-1]:
        pattern += re.escape(part) + r'\1'
    pattern += re.escape(parts[-1])
    match = re.fullmatch(pattern, text)
    if not match:
        return None
    digits = match.group(1)
    number = float(digits)
    return int(number) if number.is_integer() and re.fullmatch(r'-?\d+', digits) else number


def lookup_template_errors(field_def):
    """PS-407: a `${value}` default reports a string, so every label must be one."""
    if not isinstance(field_def, dict):
        return []
    lookup = field_def.get('lookup')
    if lookup_template(lookup) is None:
        return []
    numeric = [k for k, label in lookup.items()
               if k != 'default' and not isinstance(label, str)]
    if not numeric:
        return []
    return [f"Field '{field_def.get('name', '?')}': a lookup whose default carries "
            f"${{value}} reports a string, so its labels must be strings; "
            f"{len(numeric)} are not (PS-407)"]


_ITERATOR_KEYS = ('index', 'count_as')

#: The name an `element` (PS-528) is decoded under where it declares none. It is never
#: reported: the repeat reports the element's value, not a record holding it.
ELEMENT_NAME = 'element'


#: What an `element` may not be (PS-545): it is one data field, not a construct entry.
ELEMENT_CONSTRUCTS = ('match', 'byte_group', 'flagged', 'tlv', 'records')

#: `$name[i]`: the value a sibling repeat of single values reported at this position
#: (PS-546), in a repeat's `identity` or an element's `unit` or `ipso.instance`.
SIBLING_REFERENCE = re.compile(r'^\$(\w+)\[(\w+)\]$')


def sibling_references(repeat):
    """The `$name[i]` references a repeat declares (PS-546): its identity, and its
    elements' `unit` and `ipso.instance`, as (key, sibling name, index name) triples."""
    refs = [('identity', repeat.get('identity'))]
    for member in repeat_members(repeat)[0]:
        if isinstance(member, dict):
            ipso = member.get('ipso') if isinstance(member.get('ipso'), dict) else {}
            refs += [('unit', member.get('unit')), ('ipso.instance', ipso.get('instance'))]
    found = []
    for key, ref in refs:
        m = SIBLING_REFERENCE.match(ref) if isinstance(ref, str) else None
        if m:
            found.append((key, m.group(1), m.group(2)))
    return found


def sibling_errors(repeat, earlier, at):
    """PS-546: each `$name[i]` names a repeat declaring `element`, decoded earlier in the
    same field list (`earlier`, by name), and `i` is this repeat's `index`; the sibling's
    element carries a lookup or is a literal (PS-374)."""
    errors = []
    for key, name, index in sibling_references(repeat):
        label = f"{at}: {key} `${name}[{index}]`"
        if index != repeat.get('index'):
            errors.append(f"{label} must index by the repeat's own `index` (PS-546)")
            continue
        sibling = earlier.get(name)
        if sibling is None:
            errors.append(f"{label} names no repeat declaring `element` earlier in the "
                          f"same field list (PS-546)")
            continue
        element = sibling['element']
        if not ('lookup' in element or is_literal(element)):
            errors.append(f"{label}: the elements of '{name}' identify values but carry no "
                          f"lookup and are not a literal (PS-374, PS-546)")
    return errors


def repeat_members(field_def):
    """A repeat's element fields and, for a repeat of single values, the element's name.

    A repeat declares exactly one of `fields` and `element` (PS-083, PS-528), which
    iterator_errors() checks at load. An `element` is decoded as a one-field record under
    its own name, or ELEMENT_NAME, so `present_if` and `name_from` in it can refer to the
    current value; the repeat then reports the value (element_value). The spec prototype's
    `repeat_members`, without its raising: here the rules are load-time errors.
    """
    element = field_def.get('element')
    if isinstance(element, dict) and 'fields' not in field_def:
        name = element.get('name') or ELEMENT_NAME
        if name.startswith('_'):
            # PS-545: the element's name is never reported, so an internal name hides
            # nothing. It is decoded under ELEMENT_NAME, and `var` keeps `$_x` bound.
            return [dict(element, name=ELEMENT_NAME, var=element.get('var', name))], \
                ELEMENT_NAME
        return [dict(element, name=name)], name
    fields = field_def.get('fields')
    return (fields if isinstance(fields, list) else []), None


def element_value(record):
    """The value a repeat of single values reports for one element: the record's one
    reported value, or None where it was omitted (PS-528), so positions keep their
    index. Interpreter metadata beside it (`_quality`) is not the value."""
    return next((v for k, v in record.items() if k not in RESERVED_OUTPUT_NAMES), None)


def index_base(field_def):
    """A repeat's `index_base` (PS-530), 0 where it declares none."""
    base = field_def.get('index_base', 0)
    return base if isinstance(base, int) and not isinstance(base, bool) else 0


def element_repeat_errors(field_def):
    """PS-083, PS-528, PS-530: a repeat declares exactly one of `fields` and `element`,
    an `element` is one field definition, and `index_base` is an integer. The wording is
    the spec prototype's, and Go, Java, C# and the TS013 generator give it identically."""
    label = f"Repeat '{field_def.get('name', '<unnamed>')}'"
    errors = []
    if ('fields' in field_def) == ('element' in field_def):
        which = 'both' if 'fields' in field_def else 'neither'
        errors.append(f"{label} declares {which} of `fields` and `element`; it declares "
                      f"exactly one (PS-083, PS-528)")
    elif not isinstance(field_def.get('element', {}), dict):
        errors.append(f"{label}: `element` is a single field definition (PS-528)")
    elif 'element' in field_def and ('type' not in field_def['element'] or any(
            key in field_def['element'] for key in ELEMENT_CONSTRUCTS)):
        errors.append(f"{label}: `element` is a data field that declares `type`, not a "
                      f"construct (PS-545)")
    base = field_def.get('index_base', 0)
    if isinstance(base, bool) or not isinstance(base, int):
        errors.append(f"{label}: `index_base` is an integer (PS-530)")
    return errors


def _repeat_member_ref_errors(repeat, member, where):
    """PS-373, PS-374: a per-element unit, IPSO instance or SenML name, and what it names."""
    errors = []
    by_name = {f.get('name'): f for f in repeat_members(repeat)[0] if isinstance(f, dict)}
    index = repeat.get('index')
    refs = []
    # A `$name[i]` names a sibling repeat (PS-546), which sibling_errors() checks.
    if isinstance(member.get('unit'), str) and member['unit'].startswith('$') \
            and not SIBLING_REFERENCE.match(member['unit']):
        refs.append(('unit', member['unit'][1:]))
    ipso = member.get('ipso')
    if isinstance(ipso, dict) and isinstance(ipso.get('instance'), str):
        if ipso['instance'].startswith('$') and not SIBLING_REFERENCE.match(ipso['instance']):
            refs.append(('ipso.instance', ipso['instance'][1:]))
    senml = member.get('senml')
    if isinstance(senml, dict) and isinstance(senml.get('name'), str):
        refs.extend(('senml.name', name) for name in re.findall(r'\$\{([^}]+)\}', senml['name']))
    for key, name in refs:
        if name == index:
            count = repeat.get('count')
            if not ((isinstance(count, int) and not isinstance(count, bool))
                    or 'max' in repeat):
                errors.append(f"{where}: {key} uses the index {name!r}, so the repeat needs a "
                              f"literal count or a max (PS-374)")
            continue
        target = by_name.get(name)
        if target is None:
            errors.append(f"{where}: {key} names {name!r}, which is neither a field of the "
                          f"element nor the repeat's index (PS-373)")
        elif not ('lookup' in target or is_literal(target)):
            errors.append(f"{where}: {key} names {name!r}, which must carry a lookup or be a "
                          f"literal so its values are known from the schema (PS-374)")
    return errors


def iterator_errors(fields, enclosing=(), in_elements=False, where='fields'):
    """The repeat iterator's schema rules, checked at load (CR-2026-048, -053, -054, -055).

    PS-350/PS-383/PS-384 (reserve and trailer), PS-369 (index and count_as names), PS-372
    to PS-374 (identity and per-element references), PS-381 (carry), and `present_if`'s
    form (PS-386). Walks nested field lists so an enclosing repeat's names are known.
    """
    errors = []
    earlier = {}                     # PS-546: the element repeats decoded before this field
    for i, f in enumerate(fields or []):
        if not isinstance(f, dict):
            continue
        at = f"{where}[{i}] ({f.get('name', '?')})"
        if 'carry' in f:
            carry = f['carry']
            if not in_elements or f.get('type') not in COMPUTED_TYPES:
                errors.append(f"{at}: carry applies only to a computed field declared in a "
                              f"repeat's elements (PS-381)")
            elif isinstance(carry, bool) or not (
                    isinstance(carry, (int, float))
                    or (isinstance(carry, str) and carry.startswith('$'))):
                errors.append(f"{at}: carry must be a numeric literal or a $ reference to a "
                              f"field decoded before the repeat (PS-381)")
        if f.get('type') == 'repeat':
            errors.extend(element_repeat_errors(f))                  # PS-528, PS-530
            members = repeat_members(f)[0]
            names = {m.get('name') for m in members if isinstance(m, dict)}
            own = []
            for key in _ITERATOR_KEYS:
                value = f.get(key)
                if value is None:
                    continue
                if not isinstance(value, str) or not value:
                    errors.append(f"{at}: {key} must be a name (PS-366, PS-367)")
                    continue
                if value in names or value in enclosing or value in own:
                    errors.append(f"{at}: {key} {value!r} names a field of the element, or an "
                                  f"index or count_as already in scope (PS-369)")
                own.append(value)
            if 'reserve' in f:
                reserve = f['reserve']
                if f.get('until') != 'end':
                    errors.append(f"{at}: reserve applies only to a repeat with until: end "
                                  f"(PS-350)")
                elif isinstance(reserve, bool) or not isinstance(reserve, int) or reserve < 0:
                    errors.append(f"{at}: reserve must be a non-negative integer (PS-350)")
            if 'trailer' in f:
                trailer = f['trailer']
                size = fixed_element_size(trailer) if isinstance(trailer, list) else None
                if 'reserve' not in f:
                    errors.append(f"{at}: trailer needs reserve (PS-383)")
                elif size is None:
                    errors.append(f"{at}: every trailer field needs a size known from the "
                                  f"schema (PS-384)")
                elif size != f.get('reserve'):
                    errors.append(f"{at}: the trailer's fields take {size} byte(s), and "
                                  f"reserve is {f.get('reserve')}; they must be equal (PS-384)")
            present_if = f.get('present_if')
            if present_if is not None and not (
                    isinstance(present_if, dict)
                    and isinstance(present_if.get('field'), str)
                    and any(op in present_if for op in CONDITION_OPS)):
                errors.append(f"{at}: present_if must be one guard condition, "
                              f"{{field: $name, <op>: value}} (PS-386)")
            errors.extend(sibling_errors(f, earlier, at))           # PS-546
            identity = f.get('identity')
            if identity is not None and not (
                    identity == f.get('index')
                    or (isinstance(identity, str) and SIBLING_REFERENCE.match(identity))
                    or (isinstance(identity, str) and identity.startswith('$')
                        and identity[1:] in names)):
                errors.append(f"{at}: identity must be the repeat's index or a $ reference "
                              f"to a field of its elements (PS-372)")
            key = 'element' if repeat_members(f)[1] is not None else 'fields'
            for j, member in enumerate(members):
                if isinstance(member, dict):
                    errors.extend(_repeat_member_ref_errors(
                        f, member, f"{at}.{key}[{j}] ({member.get('name', '?')})"))
            errors.extend(iterator_errors(members, tuple(enclosing) + tuple(own), True,
                                          f"{at}.{key}"))
            if isinstance(f.get('trailer'), list):
                errors.extend(iterator_errors(f['trailer'], enclosing, False,
                                              f"{at}.trailer"))
            if isinstance(f.get('element'), dict) and isinstance(f.get('name'), str):
                earlier[f['name']] = f
            continue
        for key in ('fields',):
            if isinstance(f.get(key), list):
                errors.extend(iterator_errors(f[key], enclosing, False, f"{at}.{key}"))
        for construct in ('match', 'tlv', 'flagged', 'byte_group'):
            body = f.get(construct)
            if isinstance(body, dict):
                cases = body.get('cases')
                case_lists = (cases.values() if isinstance(cases, dict)
                              else cases if isinstance(cases, list) else [])
                for case in case_lists:
                    # A non-list case body is case_body_errors' to report.
                    group = case if isinstance(case, list) else (
                        case.get('fields') if isinstance(case, dict) else None)
                    errors.extend(iterator_errors(group, enclosing, False, f"{at}.{construct}"))
                if isinstance(body.get('fields'), list):
                    errors.extend(iterator_errors(body['fields'], enclosing, False,
                                                  f"{at}.{construct}"))
                for group in body.get('groups') or []:
                    if isinstance(group, dict):
                        errors.extend(iterator_errors(group.get('fields'), enclosing, False,
                                                      f"{at}.{construct}"))
    return errors


def repeat_only_names(schema):
    """Names declared in some repeat's elements and nowhere outside one (PS-368)."""
    inside, outside = set(), set()

    def walk(node, in_elements):
        if isinstance(node, dict):
            name = node.get('name')
            if isinstance(name, str) and ('type' in node or 'fields' in node):
                (inside if in_elements else outside).add(name)
            for key, value in node.items():
                if key in ('test_vectors', 'definitions'):
                    continue
                walk(value, in_elements or (key in ('fields', 'element')
                                            and node.get('type') == 'repeat'))
        elif isinstance(node, list):
            for item in node:
                walk(item, in_elements)

    walk(schema, False)
    return inside - outside


def tlv_reserve_errors(field_def):
    """PS-471: a tlv's reserve is a non-negative integer."""
    body = field_def.get('tlv') if isinstance(field_def, dict) else None
    if not isinstance(body, dict) or 'reserve' not in body:
        return []
    reserve = body['reserve']
    if isinstance(reserve, bool) or not isinstance(reserve, int) or reserve < 0:
        return [f"tlv reserve must be a non-negative integer, got {reserve!r} (PS-471)"]
    return []


def range_key_error(key):
    """Why a case key spelled like a range is not one, else None (PS-398, PS-151).

    A key is a range where it holds `..` and is not a quoted list. Both bounds must be
    numbers and `start <= end`. A malformed one, `"a..b"`, matched nothing and raised no
    error, so every value fell through to the default.
    """
    if not isinstance(key, str) or '..' not in key or key.strip().startswith('['):
        return None
    bounds = parse_range_case_key(key)
    if bounds is None:
        return ("a range key is `start..end` with two numbers, each an integer or a "
                "decimal, such as 2..5 or 12.1..35.4 (PS-398)")
    if bounds[0] > bounds[1]:
        return "a range key has start <= end (PS-151, PS-398)"
    return None


def case_body_errors(schema):
    """A `match` or `tlv` case body is a field list (PS-347, PS-441), `[]` for a case
    that reads nothing.

    A bare string such as `cases: {5: skip}` was taken for one: the validator crashed
    walking it and the decoder failed with `'str' object has no attribute 'get'` only
    when a payload reached that case. Rejected at load, naming the case, as Go, Java
    and C# do.
    """
    errors = []

    def walk(node, where):
        if isinstance(node, dict):
            for construct in ('match', 'tlv'):
                body = node.get(construct)
                if not isinstance(body, dict) or not isinstance(body.get('cases'), dict):
                    continue
                for key, case in body['cases'].items():
                    if not isinstance(case, list):
                        errors.append(
                            f"{where}.{construct}.cases[{key}]: a case body is a field "
                            f"list; write [] for a case that reads nothing (PS-441)")
                    problem = range_key_error(key)
                    if problem:
                        errors.append(f"{where}.{construct}.cases[{key}]: {problem}")
            for key, value in node.items():
                if key not in ('test_vectors', 'definitions'):
                    walk(value, f"{where}.{key}" if where else key)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{where}[{i}]")

    walk(schema, '')
    return errors


#: The comparison operators of a guard condition and `present_if` (PS-126, PS-543).
CONDITION_OPS = ('gt', 'gte', 'lt', 'lte', 'eq', 'ne', 'in', 'not_in')

#: The set operators (PS-543): each takes a list of numbers.
SET_OPS = ('in', 'not_in')


def records_reachable(fields):
    """Whether a field list reaches a `records` entry (PS-541): directly, or through a
    match case or default, which merge into the list. Nothing else is descended into: a
    `records` entry anywhere else is rejected at load (records_errors)."""
    for item in fields or []:
        if not isinstance(item, dict):
            continue
        if 'records' in item and not item.get('type'):
            return True
        match = item.get('match')
        if isinstance(match, dict) and not item.get('type'):
            cases = match.get('cases') if isinstance(match.get('cases'), dict) else {}
            bodies = list(cases.values()) + [match.get('default')]
            if any(isinstance(b, list) and records_reachable(b) for b in bodies):
                return True
    return False


def level_names(fields):
    """The names a field list reports at its own level: its named fields, and those of
    the constructs that merge into it (match cases and default, tlv cases, flagged groups,
    byte_group members). An object or repeat contributes its own name, not its members'.
    Internal and `name_from` fields report under no fixed name, and a `records` entry's
    bodies are records of their own (PS-541)."""
    found = set()
    for item in fields if isinstance(fields, list) else []:
        if not isinstance(item, dict):
            continue
        if 'records' in item and not item.get('type'):
            continue
        if item.get('type'):
            name = item.get('name')
            if (isinstance(name, str) and not name.startswith('_')
                    and not item.get('name_from')):
                found.add(name)
            continue
        for key in ('match', 'tlv'):
            spec = item.get(key)
            if isinstance(spec, dict):
                cases = spec.get('cases') if isinstance(spec.get('cases'), dict) else {}
                for body in list(cases.values()) + [spec.get('default')]:
                    found |= level_names(body)
        flagged = item.get('flagged')
        if isinstance(flagged, dict):
            for group in flagged.get('groups') or []:
                if isinstance(group, dict):
                    found |= level_names(group.get('fields'))
        group = item.get('byte_group')
        found |= level_names(group.get('fields') if isinstance(group, dict) else group)
    return found


def records_clash(fields):
    """PS-541 (CR-2026-124): the names a record shares with a field the element reports
    outside `records`, sorted. A schema with any is invalid."""
    outside = level_names(fields)
    inside = set()

    def collect(items):
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if 'records' in item and not item.get('type'):
                for body in item['records'] if isinstance(item['records'], list) else []:
                    inside.update(level_names(body))
            match = item.get('match')
            if isinstance(match, dict) and not item.get('type'):
                cases = match.get('cases') if isinstance(match.get('cases'), dict) else {}
                for body in list(cases.values()) + [match.get('default')]:
                    collect(body)

    collect(fields)
    return sorted(inside & outside)


def records_errors(schema):
    """PS-541: a `records` entry is a non-empty list of field lists, in a repeat's
    element or a match case within it, and never reached from an `element` (PS-528).

    The wording is shared by Go, Java, C# and the TS013 generator."""
    errors = []

    def walk(fields, in_element):
        for item in fields if isinstance(fields, list) else []:
            if not isinstance(item, dict):
                continue
            if 'records' in item and not item.get('type'):
                if not in_element:
                    errors.append("`records` belongs in a repeat's element, or a match "
                                  "case within it (PS-541)")
                bodies = item['records']
                if not (isinstance(bodies, list) and bodies
                        and all(isinstance(b, list) for b in bodies)):
                    errors.append(
                        "`records` is a non-empty list of field lists (PS-541)")
                    continue
                for body in bodies:
                    walk(body, False)
                continue
            if item.get('type') == 'repeat':
                element = item.get('element')
                if isinstance(element, dict):
                    if records_reachable([element]):
                        label = item.get('name', '<unnamed>')
                        errors.append(f"Repeat '{label}': an `element` cannot reach a "
                                      f"`records` entry (PS-541)")
                    else:
                        walk([element], False)
                walk(item.get('fields'), True)
                walk(item.get('trailer'), False)
                clash = records_clash(item.get('fields'))
                if clash:
                    label = item.get('name', '<unnamed>')
                    errors.append(f"Repeat '{label}': {clash} is reported both by a "
                                  f"record and by the element outside `records` (PS-541)")
                continue
            match = item.get('match')
            if isinstance(match, dict) and not item.get('type'):
                cases = match.get('cases')
                cases = cases if isinstance(cases, dict) else {}
                for body in list(cases.values()) + [match.get('default')]:
                    walk(body, in_element)
            if isinstance(item.get('cases'), dict):
                for body in item['cases'].values():
                    walk(body, False)
            walk(item.get('fields'), False)
            tlv = item.get('tlv')
            if isinstance(tlv, dict) and isinstance(tlv.get('cases'), dict):
                for body in tlv['cases'].values():
                    walk(body, False)
            flagged = item.get('flagged')
            if isinstance(flagged, dict):
                for group in flagged.get('groups') or []:
                    if isinstance(group, dict):
                        walk(group.get('fields'), False)
            group = item.get('byte_group')
            walk(group.get('fields') if isinstance(group, dict) else group, False)

    walk(schema.get('fields'), False)
    for entry in (schema.get('ports') or {}).values():
        walk(entry.get('fields') if isinstance(entry, dict) else entry, False)
    return errors


def condition_errors(schema):
    """PS-126: every guard condition and `present_if` has exactly one comparison
    operator. PS-543: `in` and `not_in` take a list of numbers, never a boolean. The
    wording is the spec prototype's, and Go, Java, C# and the TS013 generator give it."""
    errors = []

    def check(cond, where):
        if not isinstance(cond, dict):
            return
        used = sum(op in cond for op in CONDITION_OPS)
        if used != 1:
            errors.append(f"{where} has {used} comparison operators; a condition has "
                          f"exactly one (PS-126)")
        for op in SET_OPS:
            values = cond.get(op)
            if op in cond and not (isinstance(values, list) and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in values)):
                errors.append(f"`{op}` in {where} is a list of numbers (PS-543)")

    def walk(node):
        if isinstance(node, dict):
            name = node.get('name', '?')
            check(node.get('present_if'), f"the present_if of '{name}'")
            guard = node.get('guard')
            if isinstance(guard, dict) and isinstance(guard.get('when'), list):
                for cond in guard['when']:
                    check(cond, f"a guard condition of '{name}'")
            for key, value in node.items():
                if key != 'test_vectors':
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return errors


#: The largest LwM2M object instance ID; 65535 is reserved (PS-548).
IPSO_INSTANCE_MAX = 65534


def ipso_instance_errors(schema):
    """PS-548: an `ipso.instance` is an integer from 0 to 65534, or a reference every value
    of which is one - a repeat's index whose range from `index_base` fits, a literal such
    integer, or a lookup whose labels are all such integers with no `${value}` default.
    Checked through the listing `_meta` would carry (PS-375, PS-547), so an own-element
    reference (PS-373) and a sibling's `$name[i]` (PS-546) are held to the same rule."""
    def valid(v):
        return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= IPSO_INSTANCE_MAX

    errors = []

    def check(field, repeat, siblings):
        listing = meta_reference(field['ipso']['instance'], repeat, siblings=siblings)
        if isinstance(listing, dict) and 'index' in listing:
            count = listing.get('count')
            if not isinstance(count, int) or isinstance(count, bool):
                return              # PS-374 reports an index with no literal count or max
            low = listing.get('index_base', 0)
            ok = valid(low) and valid(low + max(count, 1) - 1)
        elif isinstance(listing, dict):
            ok = not listing.get('open') and all(valid(v) for v in listing.get('values', []))
        else:
            ok = valid(listing)
        if not ok:
            errors.append(f"Field '{field.get('name', '<unnamed>')}': its ipso.instance can "
                          f"be other than an integer from 0 to {IPSO_INSTANCE_MAX} (PS-548)")

    def walk(node, repeat, siblings):
        if isinstance(node, list):
            here = element_repeats(node)
            for item in node:
                walk_item(item, repeat, siblings, here)

    def walk_item(item, repeat, siblings, here):
        if not isinstance(item, dict):
            walk(item, repeat, siblings)
            return
        ipso = item.get('ipso')
        if isinstance(ipso, dict) and 'instance' in ipso:
            check(item, repeat, siblings)
        for key, value in item.items():
            if key in ('test_vectors', 'definitions', 'ipso', 'senml', 'lookup'):
                continue
            if item.get('type') == 'repeat' and key in ('fields', 'element'):
                # A member's references name this repeat's index and fields, and the
                # element repeats beside it (PS-546).
                walk(value if isinstance(value, list) else [value], item, here)
            elif isinstance(value, dict):
                walk_item(value, repeat, siblings, here)
            else:
                walk(value, repeat, siblings)

    walk_item(schema, None, {}, {})
    return errors


#: The constructs PS-553 exempts: an entry carrying one and no `type` is not a field.
#: `records` is handled first, as the spec prototype's check_field_names does.
NAMELESS_CONSTRUCTS = ('match', 'byte_group', 'flagged', 'tlv')


def field_name_errors(schema):
    """PS-553 (CR-2026-138): every field that reads or reports a value has a `name`.

    A `skip` is the one nameless field. A construct (`match`, `byte_group`, `flagged`,
    `tlv`, `records`) is not a field; a byte_group member may leave reserved bits
    unnamed, and a repeat's `element` is nameless (PS-528). Any other nameless field
    rejects the schema, identified by its 1-based position in its list, in the spec
    prototype's text. The walk is the prototype's `check_field_names`: the header, the
    fields and each port's fields, through an object's or a repeat's `fields` and
    `trailer` (which add `, in '<name>'`), and through match cases and default, flagged
    groups, tlv cases and `records` bodies (which add nothing).

    Before, Python decoded a nameless field under the key `unknown`, Go, Java, C# and
    the generated codec read it and dropped it, and every encoder wrote it as zero
    padding. The references are spliced first, so a definition is held to it where used.
    """
    if not isinstance(schema, dict):
        return []
    schema = expand_refs(schema)[0]
    label = f"Schema {schema.get('name')!r}"
    errors = []

    def walk(items, where):
        if not isinstance(items, list):
            return
        for position, item in enumerate(items, 1):
            if not isinstance(item, dict):
                continue
            if 'records' in item:
                for body in item['records'] if isinstance(item['records'], list) else []:
                    walk(body, where)
                continue
            if any(key in item for key in NAMELESS_CONSTRUCTS) and 'type' not in item:
                spec = item.get('match')
                if isinstance(spec, dict):
                    cases = spec.get('cases')
                    for body in (list(cases.values()) if isinstance(cases, dict) else []) \
                            + [spec.get('default')]:
                        walk(body, where)
                for key in ('tlv', 'flagged'):
                    spec = item.get(key)
                    if isinstance(spec, list):
                        walk(spec, where)
                    elif isinstance(spec, dict):
                        walk(spec.get('fields'), where)
                        cases = spec.get('cases')
                        for body in (cases.values() if isinstance(cases, dict) else []):
                            walk(body, where)
                        for group in spec.get('groups') or []:
                            walk(group.get('fields') if isinstance(group, dict) else None,
                                 where)
                continue
            if set(item) <= {'$ref'} or item.get('type') == 'skip':
                continue
            if 'object' in item and 'type' not in item:
                continue            # the withdrawn `object:` key, which PS-466 names
            if item.get('type') == 'match':
                continue            # the withdrawn `type: match`, which withdrawn_errors names
            name = item.get('name')
            if not (isinstance(name, str) and name):
                kind = item.get('type', 'field')
                errors.append(f"{where}: field {position}, a {kind}, has no name; a field "
                              f"that reads or reports a value is named, and padding is a "
                              f"`skip` (PS-553)")
                continue
            walk(item.get('fields'), f"{where}, in '{name}'")
            walk(item.get('trailer'), f"{where}, in '{name}'")

    walk(schema.get('header'), f"{label}, header")
    walk(schema.get('fields'), label)
    ports = schema.get('ports')
    for port, entry in (ports.items() if isinstance(ports, dict) else []):
        group = entry.get('fields') if isinstance(entry, dict) else entry
        walk(group, f"{label}, port {port}")
    return errors


def sampled_meta(sampled):
    """PS-568: a repeat's `sampled`, from the schema alone, with `order` and `at` filled
    in; a `$name` interval is written {"field": name}."""
    interval = sampled['interval']
    if isinstance(interval, str):
        interval = {'field': interval[1:]}
    return {'interval': interval, 'order': sampled.get('order', 'newest_first'),
            'at': sampled.get('at', 'rx_time')}


def sampled_errors(schema):
    """PS-569 (CR-2026-156): `sampled` only on a repeat; an interval above 0, a `$`
    reference or {configured: name}; a known order; `at` is rx_time or a unix_epoch or
    iso8601 timestamps entry. The wording is the spec prototype's check_sampled. A `$`
    reference must name a field the repeat can see, which reference_errors holds to
    PS-539 (`interval` is one of its reference keys)."""
    metadata = schema.get('metadata') if isinstance(schema.get('metadata'), dict) else {}
    times = {t.get('name') for t in metadata.get('timestamps') or []
             if isinstance(t, dict) and t.get('mode') in ('unix_epoch', 'iso8601')}
    errors = []

    def bad(field, why):
        errors.append(f"Field '{field.get('name')}': sampled {why} (PS-569)")

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if 'sampled' in node and 'type' in node:
                check(node, node['sampled'])
            for key, value in node.items():
                if key != 'test_vectors' and isinstance(value, (list, dict)):
                    walk(value)

    def check(field, sampled):
        if field.get('type') != 'repeat':
            return bad(field, f"is defined only on a repeat, not on {field.get('type')}")
        if not isinstance(sampled, dict) or 'interval' not in sampled:
            return bad(field, "needs an interval")
        interval = sampled['interval']
        if isinstance(interval, bool) or not (
                (isinstance(interval, (int, float)) and interval > 0)
                or (isinstance(interval, str) and interval.startswith('$'))
                or (isinstance(interval, dict) and set(interval) == {'configured'}
                    and isinstance(interval['configured'], str))):
            return bad(field, f"interval {interval!r} is not a number of seconds above 0, "
                              f"a $ reference or {{configured: name}}")
        if sampled.get('order', 'newest_first') not in ('newest_first', 'oldest_first'):
            return bad(field, f"order {sampled.get('order')!r} is not newest_first or "
                              f"oldest_first")
        at = sampled.get('at', 'rx_time')
        if at != 'rx_time' and at not in times:
            bad(field, f"at {at!r} is neither rx_time nor a unix_epoch or iso8601 "
                       f"timestamps entry")

    walk({k: schema.get(k) for k in ('header', 'fields', 'ports', 'definitions')})
    return errors




def numeral_errors(node, _top=True):
    """PS-560: `numeral` is `decimal` or `hex`, and only on an `ascii` field. Walked over the
    whole schema, in the spec prototype's text."""
    errors = []
    if isinstance(node, list):
        for item in node:
            errors.extend(numeral_errors(item, False))
    elif isinstance(node, dict):
        if 'numeral' in node and 'type' in node:
            if str(node.get('type')).lower() != 'ascii':
                errors.append(f"Field '{node.get('name')}': numeral is defined only on an "
                              f"ascii field, not on {node.get('type')} (PS-560)")
            elif node['numeral'] not in NUMERAL_PATTERNS:
                errors.append(f"Field '{node.get('name')}': numeral must be decimal or hex, "
                              f"not {node['numeral']!r} (PS-560)")
        for key, value in node.items():
            if _top and key == 'test_vectors':
                continue
            errors.extend(numeral_errors(value, False))
    return errors


def schema_iterator_errors(schema):
    """iterator_errors over the top-level and port field lists, and every tlv's reserve.

    PS-553's nameless-field check rides here too, since every loader - the interpreter,
    the validator and the TS013 generator - already calls this one.
    """
    errors = field_name_errors(schema)
    errors.extend(withdrawn_errors(schema))                          # CR-2026-159
    errors.extend(numeral_errors(schema))                            # PS-560
    errors.extend(bitfield_part_errors(schema))                      # PS-430
    errors.extend(lookup_type_errors(schema))                        # PS-571
    errors.extend(iterator_errors(schema.get('fields')))
    errors.extend(ipso_instance_errors(schema))                      # PS-548
    for port, entry in (schema.get('ports') or {}).items():
        group = entry.get('fields') if isinstance(entry, dict) else entry
        errors.extend(iterator_errors(group, where=f"ports[{port}].fields"))

    def tlvs(node):
        if isinstance(node, dict):
            if 'tlv' in node and not node.get('type'):
                yield node
            for key, value in node.items():
                if key not in ('test_vectors', 'definitions'):
                    yield from tlvs(value)
        elif isinstance(node, list):
            for item in node:
                yield from tlvs(item)

    for node in tlvs(schema):
        errors.extend(tlv_reserve_errors(node))
    errors.extend(records_errors(schema))                            # PS-541
    errors.extend(sampled_errors(schema))                            # PS-569
    errors.extend(condition_errors(schema))                          # PS-543
    return errors


#: The timestamp modes that read a field as seconds since an epoch (PS-354, PS-410).
EPOCH_MODES = ('unix_epoch', 'iso8601', 'calendar')

#: A `calendar` entry's members, in the order it reports them (PS-410).
CALENDAR_PARTS = ('year', 'month', 'day', 'hour', 'minute', 'second')

_RFC3339_UTC = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z')


def parse_epoch(text):
    """An `epoch` as seconds since 1970-01-01T00:00:00Z, or None if it is not valid.

    PS-355: an RFC 3339 date-time in UTC with a `Z` designator, and nothing else - a
    numeric offset is refused, because a local-time epoch is exactly the mistake a
    declared epoch exists to make visible.
    """
    from datetime import datetime, timezone
    match = _RFC3339_UTC.fullmatch(text) if isinstance(text, str) else None
    if not match:
        return None
    try:
        instant = datetime.strptime(text[:19], '%Y-%m-%dT%H:%M:%S').replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None
    seconds = int((instant - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds())
    fraction = match.group(1)
    return seconds + float(fraction) if fraction and float(fraction) else seconds


def timestamp_errors(metadata):
    """PS-355, PS-411 and PS-412: what a timestamp entry may declare, checked at load.

    Also refuses `format:` on an `iso8601` entry, which CR-2026-050 withdrew: it was a
    Python strftime pattern no clause described, and PS-356 now fixes the output.
    """
    errors = []
    if not isinstance(metadata, dict):
        return errors
    for i, ts in enumerate(metadata.get('timestamps') or []):
        if not isinstance(ts, dict):
            continue
        where = f"metadata.timestamps[{i}] ({ts.get('name', '?')})"
        mode = ts.get('mode')
        if 'epoch' in ts:
            if mode not in EPOCH_MODES:
                errors.append(f"{where}: epoch applies only to the "
                              f"{', '.join(EPOCH_MODES)} modes, not {mode!r} (PS-355)")
            elif parse_epoch(ts['epoch']) is None:
                hint = (" - quote it, or YAML reads it as a date"
                        if not isinstance(ts['epoch'], str) else "")
                errors.append(f"{where}: epoch {ts['epoch']!r} is not an RFC 3339 "
                              f"date-time string in UTC with a Z designator{hint} (PS-355)")
        if 'format' in ts:
            errors.append(f"{where}: format is withdrawn; an iso8601 entry reports "
                          f"YYYY-MM-DDTHH:MM:SSZ (PS-356, CR-2026-050)")
        for key in ('month_labels', 'keys'):
            if key in ts and mode != 'calendar':
                errors.append(f"{where}: {key} applies only to the calendar mode "
                              f"(PS-{411 if key == 'month_labels' else 412})")
        labels = ts.get('month_labels')
        if mode == 'calendar' and labels is not None and not (
                isinstance(labels, list) and len(labels) == 12
                and all(isinstance(label, str) for label in labels)):
            errors.append(f"{where}: month_labels must be exactly twelve strings (PS-411)")
        keys = ts.get('keys')
        if mode == 'calendar' and keys is not None:
            if not isinstance(keys, dict) or not all(
                    isinstance(v, str) and v for v in keys.values()):
                errors.append(f"{where}: keys must map part names to member names (PS-412)")
            else:
                unknown = [k for k in keys if k not in CALENDAR_PARTS]
                if unknown:
                    errors.append(f"{where}: keys names {', '.join(map(str, unknown))}, "
                                  f"which are not among {', '.join(CALENDAR_PARTS)} (PS-412)")
                names = [keys.get(part, part) for part in CALENDAR_PARTS]
                clashes = sorted({n for n in names if names.count(n) > 1})
                if clashes:
                    errors.append(f"{where}: keys maps two parts to "
                                  f"{', '.join(clashes)} (PS-412)")
    return errors


def apply_lookup(value, lookup):
    """Map a decoded integer through a ``lookup`` table (PS-103..PS-107, PS-268).

    A sequence is indexed from zero. A mapping is matched against its keys, which
    need be neither contiguous nor start at zero -- a device reporting 1=short,
    2=long, 3=double has no entry for 0, and a zero-based list cannot express that
    without inventing a label. Where a mapping has no entry and declares no
    ``default``, the field is omitted rather than reported as its raw value, since
    the device did not report a value the schema can name.

    The two failure cases are deliberately different, following the specification:
    a mapping gap is a known unknown and omits the field (PS-269), while an
    out-of-bounds sequence index is an error (PS-105) -- the payload does not match
    the schema's shape at all. PS-278 shows the specification saying "report the
    field as absent and MUST NOT abort" where that is what it wants, so PS-105's
    bare "MUST emit an error" is read as a decode error.

    The mapping form was previously accepted and mis-decoded: the guard
    ``0 <= value < len(lookup)`` was written for a list, so the last entry of any
    mapping was unreachable and leaked through as a raw integer.
    """
    if not lookup or isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if isinstance(lookup, dict):
        # Arithmetic before the lookup (PS-107) can leave an integral value as a float:
        # 7.0 is the key 7. A value with a fraction matches no key.
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, int):
            for key, label in lookup.items():
                try:
                    if int(key) == value:
                        return label
                except (TypeError, ValueError):
                    continue
        if 'default' in lookup:
            template = lookup_template(lookup)
            if template is not None:
                return template.replace(LOOKUP_VALUE_TOKEN, format_lookup_value(value))
            return lookup['default']
        return OMITTED
    # A computed field's value arrives as a float (PS-443 puts the lookup after its
    # arithmetic). An integral one is its index; one with a fraction indexes no entry,
    # which PS-105 makes an error. Both used to pass through unlooked-up, with success.
    if isinstance(value, float):
        if not value.is_integer():
            raise LookupIndexError(
                f"lookup index {value} is not an index of a {len(lookup)}-entry sequence "
                f"(PS-105)")
        value = int(value)
    if 0 <= value < len(lookup):
        return lookup[value]
    raise LookupIndexError(
        f"lookup index {value} out of bounds for {len(lookup)} entries")


def _match_composite_key(case_key: str, tag_tuple):
    """Match a composite TLV case key against a tag, supporting `!` and `*`.

    Returns (matched, specificity) where specificity is 0 for an exact key, 1 when
    any element is negated, and 2 when any element is a wildcard. Vendors dispatch
    on one tag field while excluding or ignoring another -- "channel 1, any type but
    0" - which an exact key cannot express and enumerating 256 type values would not
    sensibly cover (PS-270).
    """
    body = case_key.strip()[1:-1] if case_key.strip().endswith(']') else case_key.strip()[1:]
    parts = [part.strip().strip('"\'') for part in body.split(',')]
    if len(parts) != len(tag_tuple):
        return False, 0
    specificity = 0
    for part, actual in zip(parts, tag_tuple):
        if part == '*':
            specificity = max(specificity, 2)
            continue
        negated = part.startswith('!')
        text = part[1:].strip() if negated else part
        try:
            expected = int(text, 0)
        except (TypeError, ValueError):
            return False, 0
        if negated:
            specificity = max(specificity, 1)
            if actual == expected:
                return False, 0
        elif actual != expected:
            return False, 0
    return True, specificity


def same_label(label, value):
    """PS-513: a boolean label matches only a boolean input, and a boolean input only
    a boolean label.

    Python's ``True == 1`` (and ``hash(True) == hash(1)``) would otherwise match
    ``true`` to a label of 1 and ``1`` to a label of ``true``, so the types are
    compared before the values. ``"true"`` is a string and never equals ``True``.
    """
    return isinstance(label, bool) == isinstance(value, bool) and label == value


def message_value(value) -> str:
    """A value quoted in an error message (PS-573, CR-2026-164): a number as JavaScript's
    String(number) writes it (`2`, not `2.0`), anything else as Python's repr."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format_lookup_value(value)
    return repr(value)


def label_text(label) -> str:
    """A label as a PS-169 or PS-067 message writes it: text in single quotes, a boolean
    as JSON writes it, a number as JavaScript's String(number) does (format_lookup_value),
    so every implementation's text compares equal."""
    if isinstance(label, bool):
        return 'true' if label else 'false'
    if isinstance(label, (int, float)):
        return format_lookup_value(label)
    return f"'{label}'"


def shared_label_error(name, label, codes) -> ValueError:
    """PS-169 (CR-2026-146, 0.5.5): a label several codes share names no one code, so
    encoding it would be a guess. The codes are listed ascending."""
    listed = ', '.join(str(code) for code in sorted(codes))
    return ValueError(f"Field '{name}': the label {label_text(label)} is shared by codes "
                      f"[{listed}], so it cannot be encoded (PS-169)")


def lookup_key_code(key):
    """A mapping key as the integer code it stands for, or None where it is not one."""
    if isinstance(key, bool):
        return None
    if isinstance(key, int):
        return key
    try:
        return int(str(key), 0)
    except ValueError:
        return None


def reverse_lookup(value, lookup, name=None):
    """Map a label back to its integer for encoding.

    A string that is no label but matches the mapping's `${value}` default yields the
    value written into it (PS-409); the caller rejects any other string. Labels are
    compared with `same_label` (PS-513), so a boolean is never found as a number. A
    boolean with no key of its own is returned unchanged, and the caller rejects it.

    A label two or more codes share is rejected, naming the field `name` and the codes
    (PS-169, CR-2026-146); it used to encode as whichever code came first. With no `name`
    - a caller only ranking candidate cases - the smallest code is returned instead.
    """
    if not lookup:
        return value
    if isinstance(lookup, dict):
        codes = [lookup_key_code(key) for key, label in lookup.items()
                 if key != 'default' and same_label(label, value)]
        if codes:
            if None in codes:
                return value
            if len(codes) > 1 and name is not None:
                raise shared_label_error(name, value, codes)
            return min(codes)
        template = lookup_template(lookup)
        if template is not None and isinstance(value, str):
            recovered = match_lookup_template(template, value)
            if recovered is not None:
                return recovered
        return value
    if isinstance(lookup, (list, tuple)):
        codes = [index for index, label in enumerate(lookup) if same_label(label, value)]
        if len(codes) > 1 and name is not None:
            raise shared_label_error(name, value, codes)
        if codes:
            return codes[0]
    return value

#: Order in which the bare `mult`, `div` and `add` modifiers are applied,
#: irrespective of the order the keys appear in the source document (PS-101).
#: Order-dependent arithmetic uses the `transform` array instead (PS-102).
CANONICAL_MODIFIER_ORDER = ('mult', 'div', 'add')


def apply_canonical_modifiers(value, field_def: Dict[str, Any]):
    """Apply `mult`, `div` and `add` to a value in the canonical order.

    Previously each call site iterated the field dict, so the arithmetic followed
    the order the keys happened to appear in the YAML. That made a schema's
    meaning depend on key order, which a decoder targeting a struct (Go, Java,
    C#, C), Protocol Buffers or the binary schema form cannot preserve -- and the
    implementations of this specification consequently disagreed with each other.
    An absent modifier is the identity operation.

    A zero divisor returns OMITTED: the field is absent (PS-100, PS-278). This skipped
    the division instead, so `div: 0` reported the undivided value as though it were
    the scaled one.
    """
    if value is OMITTED:
        return OMITTED
    for key in CANONICAL_MODIFIER_ORDER:
        operand = field_def.get(key)
        if operand is None:
            continue
        if key == 'mult':
            value = value * operand
        elif key == 'div':
            if operand == 0:
                return OMITTED
            value = value / operand
        else:
            value = value + operand
    return value


class TransformNotInvertible(ValueError):
    """A ``transform`` stage that encoding cannot undo (``sqrt``, ``log``, ``pow``...)."""


def reverse_transform_stages(value, stages):
    """Undo a ``transform`` chain for encoding, innermost stage last.

    Decoding runs the stages in order, so encoding runs their inverses in reverse
    order. Nothing did this before: a `u16` carrying
    ``transform: [{add: -32768}, {div: 10}]`` decoded to -3276.8 and encoding wrote
    that straight back into an unsigned field, which raised
    "can't convert negative int to unsigned" where it happened to be out of range and
    silently produced the wrong bytes where it did not.

    Rounding and clamping stages are identity in reverse: the value they discarded
    cannot be recovered, and for a value that was in range they changed nothing.
    Genuinely irreversible arithmetic raises, so a caller reports the field rather
    than writing a wrong byte.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    for stage in reversed(list(stages or [])):
        if not isinstance(stage, dict):
            continue
        if 'add' in stage:
            value = value - float(stage['add'])
        elif 'mult' in stage:
            factor = float(stage['mult'])
            if factor == 0:
                raise TransformNotInvertible("cannot undo 'mult: 0'")
            value = value / factor
        elif 'div' in stage:
            value = value * float(stage['div'])
        elif 'round' in stage or 'op' in stage:
            # The discarded precision is gone; the value itself is unchanged.
            continue
        elif 'floor' in stage or 'ceiling' in stage:
            # Bound stages: identity for anything that was inside the bound.
            continue
        else:
            unknown = ", ".join(sorted(stage)) or "empty stage"
            raise TransformNotInvertible(f"cannot undo transform stage: {unknown}")
    return value


def reverse_canonical_modifiers(value, field_def: Dict[str, Any]):
    """Invert :func:`apply_canonical_modifiers` for encoding.

    Decoding computes ``((raw * mult) / div) + add``, so encoding subtracts
    ``add``, multiplies by ``div``, then divides by ``mult``.
    """
    for key in reversed(CANONICAL_MODIFIER_ORDER):
        operand = field_def.get(key)
        if operand is None:
            continue
        if key == 'add':
            value = value - operand
        elif key == 'div':
            value = value * operand
        elif operand != 0:
            value = value / operand
    return value



#: The IANA SenML Units registry (RFC 8428 section 12.1, RFC 8798), as updated
#: 2026-02-02: every unit a SenML record's `u` may carry (CR-2026-094).
SENML_UNITS = frozenset([
    'm', 'kg', 'g', 's', 'A', 'K', 'cd', 'mol', 'Hz', 'rad', 'sr', 'N', 'Pa', 'J', 'W',
    'C', 'V', 'F', 'Ohm', 'S', 'Wb', 'T', 'H', 'Cel', 'lm', 'lx', 'Bq', 'Gy', 'Sv',
    'kat', 'm2', 'm3', 'l', 'm/s', 'm/s2', 'm3/s', 'l/s', 'W/m2', 'cd/m2', 'bit',
    'bit/s', 'lat', 'lon', 'pH', 'dB', 'dBW', 'Bspl', 'count', '/', '%', '%RH', '%EL',
    'EL', '1/s', '1/min', 'beat/min', 'beats', 'S/m', 'B', 'VA', 'VAs', 'var', 'vars',
    'J/m', 'kg/m3', 'deg', 'NTU',
])

#: Its Secondary Units: name -> (primary unit, scale, offset), as the registry writes
#: them. A record gives the primary unit and the value re-expressed in it (PS-487).
SENML_SECONDARY_UNITS = {
    'ms': ('s', '1/1000', '0'),
    'min': ('s', '60', '0'),
    'h': ('s', '3600', '0'),
    'MHz': ('Hz', '1000000', '0'),
    'kW': ('W', '1000', '0'),
    'kVA': ('VA', '1000', '0'),
    'kvar': ('var', '1000', '0'),
    'Ah': ('C', '3600', '0'),
    'Wh': ('J', '3600', '0'),
    'kWh': ('J', '3600000', '0'),
    'varh': ('vars', '3600', '0'),
    'kvarh': ('vars', '3600000', '0'),
    'kVAh': ('VAs', '3600000', '0'),
    'Wh/km': ('J/m', '3.6', '0'),
    'KiB': ('B', '1024', '0'),
    'GB': ('B', '1e9', '0'),
    'Mbit/s': ('bit/s', '1000000', '0'),
    'B/s': ('bit/s', '8', '0'),
    'MB/s': ('bit/s', '8000000', '0'),
    'mV': ('V', '1/1000', '0'),
    'mA': ('A', '1/1000', '0'),
    'dBm': ('dBW', '1', '-30'),
    'ug/m3': ('kg/m3', '1e-9', '0'),
    'mm/h': ('m/s', '1/3600000', '0'),
    'm/h': ('m/s', '1/3600', '0'),
    'ppm': ('/', '1e-6', '0'),
    '/100': ('/', '1/100', '0'),
    '/1000': ('/', '1/1000', '0'),
    'hPa': ('Pa', '100', '0'),
    'mm': ('m', '1/1000', '0'),
    'cm': ('m', '1/100', '0'),
    'km': ('m', '1000', '0'),
    'km/h': ('m/s', '1/3.6', '0'),
    'ppb': ('/', '1e-9', '0'),
    'ppt': ('/', '1e-12', '0'),
    'VAh': ('VAs', '3600', '0'),
    'mg/l': ('kg/m3', '1/1000', '0'),
    'ug/l': ('kg/m3', '1e-6', '0'),
    'g/l': ('kg/m3', '1', '0'),
}


def _registry_number(text):
    """A scale or offset as the registry writes it: `1/1000`, `1e9`, `3.6`, `1/3.6`."""
    if "/" in text:
        num, den = text.split("/")
        return Fraction(Decimal(num)) / Fraction(Decimal(den))
    return Fraction(Decimal(text))


def senml_unit(value, unit):
    """The value and `u` of a SenML record for a field declared in `unit` (PS-487, PS-488).

    Returns (value, u, unregistered). `u` is None where the record carries no unit, and
    `unregistered` names a unit the registry does not have, for the warning PS-488
    requires. A secondary unit is re-expressed in its primary unit by the registry's
    scale and offset, computed exactly on the value's decimal form, so 3284 mV is 3.284 V
    and not 3.2840000000000003. `%` is written by its preferred name `/`, which changes
    no value. Only a number is converted; a string or boolean keeps its value.
    """
    if not unit:
        return value, None, None
    if unit == "%":
        return value, "/", None
    if unit in SENML_UNITS:
        return value, unit, None
    if unit in SENML_SECONDARY_UNITS:
        primary, scale, offset = SENML_SECONDARY_UNITS[unit]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                return value, primary, None
            exact = Fraction(Decimal(repr(value))) * _registry_number(scale)
            exact += _registry_number(offset)
            value = int(exact) if exact.denominator == 1 else float(exact)
        return value, primary, None
    return value, None, unit



# --- _meta (Clause 7: PS-175, PS-177, PS-178, PS-180, PS-181, PS-340 to PS-342, PS-371 to
# PS-376; CR-2026-088 PS-480/481; CR-2026-095 PS-489; CR-2026-096 PS-490 to PS-497) ---

#: Alias type names written as their canonical name in `_meta` (PS-493).
_META_TYPE_ALIASES = dict(
    [('uint%d' % n, 'u%d' % n) for n in (8, 16, 24, 32, 64)]
    + [('int%d' % n, 's%d' % n) for n in (8, 16, 24, 32, 64)]
    + [('i%d' % n, 's%d' % n) for n in (8, 16, 24, 32, 64)]
)

_DEV_EUI = re.compile(r'[0-9a-f]{16}')
_RECV_TIME = re.compile(
    r'(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?'
    r'(Z|z|[+-]\d{2}:?\d{2})?')


def meta_type(field_def):
    """A `_meta` entry's `type` (PS-493): the declared type, an alias by its canonical
    name, a bit range as written. A `tlv` with `merge: false` reports its entries under
    `channels`, and that entry is `tlv` (PS-493). A field with no type is never reported - PS-011 and PS-441 make it a
    schema error - so no type is invented for one; None here leaves `type` out."""
    declared = field_def.get('type')
    if isinstance(declared, str):
        return _META_TYPE_ALIASES.get(declared, declared)
    if isinstance(field_def.get('tlv'), dict):
        return 'tlv'
    return None


def meta_declarations(fields):
    """Reported name -> declaration for one level of decoded output, first declaration
    first (PS-371, PS-481 as amended by CR-2026-096: what is declared, not what is
    present). A construct that merges into its parent (`match` cases and `default`,
    `tlv` cases, `flagged` groups, `byte_group`) contributes its fields at this level; an
    object or repeat contributes its own name. Internal fields (PS-494) and `name_from`
    fields, whose key is known only at run time (PS-492), have no entry here."""
    found = {}

    def visit(items):
        if not isinstance(items, list):
            return
        for f in items:
            if not isinstance(f, dict):
                continue
            group = f.get('byte_group')
            if group is not None:
                visit(group.get('fields') if isinstance(group, dict) else group)
            if isinstance(f.get('flagged'), dict):
                for g in f['flagged'].get('groups') or []:
                    if isinstance(g, dict):
                        visit(g.get('fields'))
            if isinstance(f.get('records'), list) and not f.get('type'):
                for body in f['records']:                            # PS-541
                    visit(body)
                continue
            match = f.get('match')
            if isinstance(match, dict):
                for body in (match.get('cases') if isinstance(match.get('cases'), dict) else {}).values():
                    if isinstance(body, list):
                        visit(body)
                if isinstance(match.get('default'), list):
                    visit(match['default'])
                label = match.get('name')
                if isinstance(label, str) and not label.startswith('_'):
                    found.setdefault(label, {'name': label,
                                             'type': 'u%d' % (8 * int(match.get('length', 1))),
                                             '_reports': 'integer'})         # PS-570
            tlv = f.get('tlv')
            if isinstance(tlv, dict):
                if tlv.get('merge', True) is False:
                    # Its entries are reported as a list under the fixed key `channels`
                    # (Clause 4), whose entry is `tlv` (PS-493). A tlv has no name of its
                    # own (PS-448).
                    found.setdefault('channels', f)
                    continue
                for body in (tlv.get('cases') if isinstance(tlv.get('cases'), dict) else {}).values():
                    if isinstance(body, list):
                        visit(body)
                continue
            name = f.get('name')
            if not isinstance(name, str) or name.startswith('_') or f.get('name_from'):
                continue
            if 'match' in f and 'type' not in f:
                continue
            found.setdefault(name, f)

    visit(fields)
    return found


def element_repeats(fields):
    """Name -> declaration of each repeat declaring `element` in one field list, the
    sibling a `$name[i]` reference can name (PS-546). A construct that merges into its
    parent (`match`, `tlv`, `flagged`) is part of the list; an object or repeat is not."""
    found = {}

    def visit(items):
        for f in items if isinstance(items, list) else []:
            if not isinstance(f, dict):
                continue
            if f.get('type') == 'repeat':
                if isinstance(f.get('element'), dict) and isinstance(f.get('name'), str):
                    found.setdefault(f['name'], f)
                continue
            for construct in ('match', 'tlv'):
                body = f.get(construct)
                if isinstance(body, dict):
                    cases = body.get('cases')
                    for case in (cases.values() if isinstance(cases, dict) else []):
                        visit(case)
                    visit(body.get('default'))
            if isinstance(f.get('flagged'), dict):
                for g in f['flagged'].get('groups') or []:
                    if isinstance(g, dict):
                        visit(g.get('fields'))

    visit(fields)
    return found


def meta_reference(value, repeat=None, bare=False, siblings=None):
    """A per-element `unit`, `ipso.instance` or `identity` as PS-375 lists it: the repeat's
    index as {index, count}, or an element field as {field, values} with a lookup's
    `default` last, marked `open` where that default carries `${value}` (PS-489). A
    literal is itself. PS-374, checked at load, restricts what a reference may name.
    A `$name[i]` names the sibling repeat `name` in `siblings` (PS-546), and is listed
    as {repeat, values} from that repeat's element (PS-547)."""
    if repeat is None or not isinstance(value, str):
        return value
    if not (bare or value.startswith('$')):
        return value
    sibling = SIBLING_REFERENCE.match(value)
    if sibling:
        target = (siblings or {}).get(sibling.group(1))
        element = dict(target['element'], name=ELEMENT_NAME) if target else {}
        listing = meta_reference('$' + ELEMENT_NAME, {'element': element})
        listing.pop('field', None)
        return {'repeat': sibling.group(1), **listing}               # PS-547
    name = value[1:] if value.startswith('$') else value
    if name == repeat.get('index'):
        count = repeat.get('count')
        if not (isinstance(count, int) and not isinstance(count, bool)):
            count = repeat.get('max')
        listing = {'index': name, 'count': count}
        if index_base(repeat):
            listing['index_base'] = index_base(repeat)               # PS-375, PS-530
        return listing
    target = next((f for f in repeat_members(repeat)[0] if isinstance(f, dict)
                   and f.get('name') == name), {})
    lookup = target.get('lookup')
    if isinstance(lookup, dict):
        values = [label for key, label in lookup.items() if key != 'default']
        if 'default' in lookup:
            values.append(lookup['default'])
    elif isinstance(lookup, list):
        values = list(lookup)
    elif 'value' in target:
        values = [target['value']]
    else:
        values = []
    listing = {'field': name, 'values': values}
    if lookup_template(lookup) is not None:
        listing['open'] = True                                  # PS-489
    return listing


def lookup_labels(lookup):
    """A lookup's labels, and its `default` where that is not a `${value}` template."""
    if isinstance(lookup, (list, tuple)):
        return list(lookup)
    if isinstance(lookup, dict):
        return [v for k, v in lookup.items()
                if not (k == 'default' and isinstance(v, str) and LOOKUP_VALUE_TOKEN in v)]
    return []


def json_kind(value):
    """The JSON type of a lookup label: boolean, integer, number or string."""
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, float):
        return 'integer' if value.is_integer() else 'number'
    return 'string'


_REPORTS_FLOATS = ('number', 'f16', 'bf16', 'f32', 'f64', 'f32le16', 'f32be16le', 'uflt16',
                   'sflt16', 'sflt24', 'udec', 'sdec')


def reports_of(field_def):
    """PS-570: the JSON type PS-279 selects for a field's reported value, from the schema
    alone (the spec prototype's reports_of). A lookup is the last step (PS-443), so a field
    with one reports its labels' type whatever precedes it; a `${value}` default reports a
    string. None where nothing declares one."""
    if '_reports' in field_def:                       # a match discriminator or `channels`
        return field_def['_reports']
    lookup = field_def.get('lookup')
    if lookup is not None:
        if lookup_template(lookup) is not None:
            return 'string'
        kinds = {json_kind(v) for v in lookup_labels(lookup)}
        if kinds == {'integer', 'number'}:
            return 'number'
        return next(iter(kinds)) if len(kinds) == 1 else None
    if isinstance(field_def.get('tlv'), dict) and not field_def.get('type'):
        return 'array'                                # `channels` of a merge: false tlv
    t = str(field_def.get('type', '')).lower()
    if t == 'repeat':
        return 'array'
    if t == 'object':
        return 'object'
    if t in ('string', 'number', 'boolean') and 'value' in field_def:          # a literal
        return t
    if t == 'bytes' and field_def.get('format') == 'array':
        return 'array'
    if t in ('bool', 'boolean'):
        return 'boolean'
    if t == 'ascii':
        return 'integer' if field_def.get('numeral') else 'string'
    if t in ('string', 'bitfield_string', 'enum', 'bytes', 'hex', 'base64'):
        return 'string'
    if any(k in field_def for k in ('mult', 'div', 'add', 'polynomial', 'transform')):
        return 'number'
    if t == 'integer':
        return 'integer'
    if t in _REPORTS_FLOATS:
        return 'number'
    if _BIT_RANGE.match(t) or t in INTEGER_TYPE_INFO:
        return 'integer'
    return None


def lookup_type_errors(node, _top=True):
    """PS-571: every label of one lookup, and a `default` that is not a `${value}` template,
    is of one JSON type (integers and other numbers are one type, number)."""
    errors = []
    if isinstance(node, list):
        for item in node:
            errors.extend(lookup_type_errors(item, False))
    elif isinstance(node, dict):
        if 'lookup' in node and 'type' in node:
            kinds = {('number' if k == 'integer' else k)
                     for k in map(json_kind, lookup_labels(node['lookup']))}
            if len(kinds) > 1:
                errors.append(f"Field '{node.get('name')}': its lookup mixes "
                              f"{', '.join(sorted(kinds))} labels; a lookup's labels are of "
                              f"one type (PS-571)")
        for key, value in node.items():
            if _top and key == 'test_vectors':
                continue
            if isinstance(value, (list, dict)):
                errors.extend(lookup_type_errors(value, False))
    return errors


def field_meta(field_def, repeat=None, siblings=None):
    """One `_meta.fields` entry, derived from one declaration (PS-178, PS-493, PS-480,
    PS-371, PS-481). `repeat` is the repeat whose elements the field belongs to, for the
    per-element references of PS-373 and PS-375. `siblings` are the element repeats of
    the field list the field is declared in, which a `$name[i]` names (PS-546)."""
    entry = {}
    declared_type = meta_type(field_def)
    if declared_type is not None:
        entry['type'] = declared_type
    reports = reports_of(field_def)
    if reports is not None:
        entry['reports'] = reports                                       # PS-570
    senml = field_def.get('senml') if isinstance(field_def.get('senml'), dict) else {}
    unit = senml.get('unit') or field_def.get('unit')          # PS-178, no conversion
    if unit is not None:
        entry['unit'] = meta_reference(unit, repeat, siblings=siblings)
    if field_def.get('unit') is not None:
        # PS-596: the author's text as declared, whether or not senml.unit replaced it above
        entry['display_unit'] = meta_reference(field_def['unit'], repeat, siblings=siblings)
    ipso = field_def.get('ipso')
    if isinstance(ipso, dict) and 'object' in ipso:
        entry['ipso'] = {'object': ipso['object'],
                         'instance': meta_reference(ipso.get('instance', 0), repeat,
                                                    siblings=siblings),
                         'resource': ipso.get('resource', 5700)}
    if isinstance(senml.get('name'), str):
        entry['senml'] = {'name': senml['name']}               # PS-480; a template, PS-373
    if field_def.get('description'):
        entry['description'] = field_def['description']
    if declared_type == 'repeat' and repeat_members(field_def)[1] is not None:
        # PS-531: the element's own entry, nested, without its name; the repeat keeps
        # `type: repeat` and its identity.
        element = {k: v for k, v in field_def['element'].items() if k != 'name'}
        entry['element'] = field_meta(element, field_def, siblings)
        if 'identity' in field_def:
            entry['identity'] = meta_reference(field_def['identity'], field_def, bare=True,
                                               siblings=siblings)
        if 'sampled' in field_def:
            entry['sampled'] = sampled_meta(field_def['sampled'])      # PS-568
    elif declared_type == 'repeat':
        inner = element_repeats(field_def.get('fields'))
        entry['elements'] = {name: field_meta(member, field_def, inner)     # PS-371
                             for name, member in meta_declarations(
                                 field_def.get('fields')).items()}
        if 'identity' in field_def:
            entry['identity'] = meta_reference(field_def['identity'], field_def, bare=True,
                                               siblings=siblings)
        if 'sampled' in field_def:
            entry['sampled'] = sampled_meta(field_def['sampled'])      # PS-568
    elif declared_type == 'object':
        inner = element_repeats(field_def.get('fields'))
        entry['fields'] = {name: field_meta(member, siblings=inner)          # PS-481
                           for name, member in meta_declarations(
                               field_def.get('fields')).items()}
    return entry


def normalise_dev_eui(text):
    """PS-496: 16 lower-case hexadecimal digits, `-`, `:` and spaces removed. None where
    the input is not a device EUI once normalised."""
    if not isinstance(text, str):
        return None
    eui = re.sub(r'[-: ]', '', text).lower()
    return eui if _DEV_EUI.fullmatch(eui) else None


def rx_time_seconds(recv_time):
    """PS-177, PS-495: `recvTime` as numeric Unix seconds in UTC, keeping its fraction.

    TS013 gives an ISO 8601 string; a number is taken as seconds already. The fraction is
    carried as written - `<seconds>.<digits>` read as one decimal - so every implementation
    lands on the same double. None where the input is neither."""
    if isinstance(recv_time, bool):
        return None
    if isinstance(recv_time, (int, float)):
        return recv_time
    if not isinstance(recv_time, str):
        return None
    m = _RECV_TIME.fullmatch(recv_time.strip())
    if not m:
        return None
    year, month, day, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
    try:
        seconds = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))
    except (ValueError, OverflowError):
        return None
    zone = m.group(8)
    if zone and zone not in ('Z', 'z'):
        sign = -1 if zone[0] == '-' else 1
        digits = zone[1:].replace(':', '')
        seconds -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    # Milliseconds are kept (PS-177): the fraction is rounded half to even at three
    # digits, in decimal, so every implementation lands on the same double.
    exact = Decimal(seconds) + Decimal('0.' + (m.group(7) or '0')).quantize(
        Decimal('0.001'), rounding=ROUND_HALF_EVEN)
    if exact == exact.to_integral_value():
        return int(exact)
    return float(exact)


def _meta_agreement_key(field_def):
    """What PS-491 requires one reported name's declarations to agree on."""
    ipso = field_def.get('ipso')
    if isinstance(ipso, dict):
        ipso = (ipso.get('object'), ipso.get('instance', 0), ipso.get('resource', 5700))
    senml = field_def.get('senml')
    if isinstance(senml, dict):
        senml = tuple(sorted((str(k), str(v)) for k, v in senml.items()))
    return {'unit': field_def.get('unit'), 'senml': senml, 'ipso': ipso}


def meta_declaration_errors(schema):
    """PS-491: the declarations of one reported name in one field list must agree on
    `unit`, `senml` and `ipso`. A field list is the top level, each port's, and each
    object's or repeat's members; `match` and `tlv` cases, `flagged` groups and
    `byte_group`s merge into theirs. `type`, `lookup` and `description` may differ."""
    errors = []

    def check(fields, where):
        seen = {}

        def visit(items):
            if not isinstance(items, list):
                return
            for f in items:
                if not isinstance(f, dict):
                    continue
                group = f.get('byte_group')
                if group is not None:
                    visit(group.get('fields') if isinstance(group, dict) else group)
                if isinstance(f.get('flagged'), dict):
                    for g in f['flagged'].get('groups') or []:
                        if isinstance(g, dict):
                            visit(g.get('fields'))
                if isinstance(f.get('records'), list) and not f.get('type'):
                    for body in f['records']:                        # PS-541
                        visit(body)
                    continue
                match = f.get('match')
                if isinstance(match, dict):
                    for body in (match.get('cases') if isinstance(match.get('cases'), dict) else {}).values():
                        if isinstance(body, list):
                            visit(body)
                    if isinstance(match.get('default'), list):
                        visit(match['default'])
                tlv = f.get('tlv')
                if isinstance(tlv, dict):
                    bodies = [b for b in (tlv.get('cases') if isinstance(tlv.get('cases'), dict) else {}).values()
                              if isinstance(b, list)]
                    if tlv.get('merge', True) is False:
                        check([m for b in bodies for m in b],
                              '%s/%s' % (where, f.get('name', 'tlv')))
                    else:
                        for body in bodies:
                            visit(body)
                    continue
                if f.get('type') in ('object', 'repeat') and isinstance(f.get('fields'), list):
                    check(f['fields'], '%s/%s' % (where, f.get('name', '?')))
                name = f.get('name')
                if (isinstance(name, str) and not name.startswith('_')
                        and not f.get('name_from')):
                    seen.setdefault(name, []).append(f)

        visit(fields)
        for name, decls in seen.items():
            keys = [_meta_agreement_key(d) for d in decls]
            differing = [k for k in ('unit', 'senml', 'ipso')
                         if len({repr(x[k]) for x in keys}) > 1]
            if differing:
                errors.append(
                    "Field '%s' is declared %d times in %s and its declarations differ in "
                    "%s; the declarations of one reported name must agree on unit, senml "
                    "and ipso (PS-491)" % (name, len(decls), where, ', '.join(differing)))

    if not isinstance(schema, dict):
        return errors
    if isinstance(schema.get('fields'), list):
        check(schema['fields'], 'fields')
    for port, entry in (schema.get('ports') or {}).items():
        group = entry.get('fields') if isinstance(entry, dict) else entry
        if isinstance(group, list):
            check(group, 'port %s' % port)
    return errors


class SchemaInterpreter:
    """
    Runtime interpreter for Payload Schema definitions.
    
    Supports:
    - All integer types (u8, u16, u24, u32, i8, i16, etc.)
    - Floating point (f32, f64)
    - Bitfields (u8[3:4] - the bracket range is the only spelling)
    - Boolean
    - Bytes/strings
    - Arithmetic modifiers (mult, add, div)
    - Lookup tables
    - Nested objects
    - Conditional/match fields
    - Semantic mappings (IPSO, SenML)
    """
    
    #: PS-541: the records the current repeat element yields; None outside an element.
    _pending_records = None

    _ps564 = ()             # PS-564's checks, set per encode
    _record_supplied = frozenset()  # PS-573: names the decode record supplied, per encode
    _encode_input_names = frozenset()  # the names the encode's input gives, per encode
    _ps564_depth = 1        # not checked outside encode()

    def __init__(self, schema: Dict[str, Any]):
        # Every `$ref` is spliced before anything reads the schema (PS-346, PS-347), so
        # a reference works in any field list - a port entry, an object, a repeat, a
        # case - and not only at the top level, which was all this handled. Problems
        # with the references are reported by decode and encode (PS-348).
        self.source_schema = schema
        schema, self._load_errors = expand_refs(schema)
        self._load_errors.extend(case_body_errors(schema))      # PS-441
        # PS-358: a malformed literal is a schema error, reported as the references are;
        # so are an `encoding` off an unsigned integer and `match_value` (PS-422, PS-426).
        for field_def in typed_field_dicts(schema):
            self._load_errors.extend(literal_errors(field_def))
            self._load_errors.extend(encoding_errors(field_def))
            self._load_errors.extend(lookup_template_errors(field_def))
        self._load_errors.extend(arithmetic_schema_errors(schema))    # PS-445, PS-452
        self._load_errors.extend(annotation_schema_errors(schema))    # PS-510
        self._load_errors.extend(on_repeat_schema_errors(schema))     # PS-525, PS-526
        self._load_errors.extend(reference_errors(schema))            # PS-539
        self._load_errors.extend(meta_declaration_errors(schema))      # PS-491
        self._load_errors.extend(timestamp_errors(schema.get('metadata')))
        self._load_errors.extend(schema_iterator_errors(schema))
        self._repeat_only_names = repeat_only_names(schema)
        self._load_errors.extend(optional_errors(schema.get('fields')))
        for port, entry in (schema.get('ports') or {}).items():
            group = entry.get('fields') if isinstance(entry, dict) else entry
            self._load_errors.extend(optional_errors(group, f"ports[{port}].fields"))
        self._load_errors.extend(internal_name_errors(schema))
        self.schema = schema
        # PS-572: each construct's location in the expanded document, for the decode
        # record; built once, as the prototype's Schema.pointers is.
        self._pointers = pointer_map({'header': schema.get('header'),
                                      'fields': schema.get('fields'),
                                      'ports': schema.get('ports')})
        #: The decode record being kept or followed (PS-572, PS-573); None when not asked.
        self._trace = None
        self._pre_lookup = IdentityTable()   # PS-475, reset by decode
        self._guard_else = IdentityTable()
        self._leftover_reported = False
        self._case_quality: Dict[str, str] = {}     # reset by decode
        self._nested_warnings: List[str] = []       # reset by decode
        self._encode_warnings: List[str] = []       # PS-537, reset by encode
        #: What the last get_semantic_output call could not express, such as a unit
        #: the SenML registry does not have (PS-488). Its return value stays the
        #: records alone, as callers already read it.
        self.semantic_warnings: List[str] = []
        # PS-490: the declaration that wrote each top-level reported value, by key, and
        # how deep inside an object or repeat the decode is (only depth 0 records).
        self._producers: Dict[str, Dict[str, Any]] = {}
        self._meta_depth = 0
        self.endian = Endian(schema.get('endian', 'big'))
        self.name = schema.get('name', 'unknown')
        self.version = schema.get('version', 1)
        self.definitions = schema.get('definitions', {})
        self.direction = schema.get('direction', 'uplink')  # uplink|downlink|bidirectional
        self.downlink_commands = schema.get('downlink_commands', {})

    def _parse_compact_format(self, format_str: str) -> tuple:
        """
        Parse compact format string to field definitions.
        
        Format: ">B:version H:length I:timestamp"
        - >/<: big/little endian prefix
        - b/B: s8/u8, h/H: s16/u16, i/I: s32/u32, q/Q: s64/u64
        - f: f32, d: f64, x: skip 1 byte, Nx: skip N bytes
        - :name suffix assigns field name
        
        Returns:
            Tuple of (field list, endian override or None)
        """
        FORMAT_CHARS = {
            'b': ('s8', 1), 'B': ('u8', 1),
            'h': ('s16', 2), 'H': ('u16', 2),
            'i': ('s32', 4), 'I': ('u32', 4),
            'q': ('s64', 8), 'Q': ('u64', 8),
            'f': ('f32', 4), 'd': ('f64', 8),
            'e': ('f16', 2),
            'x': ('skip', 1),
            '?': ('bool', 1),
        }
        
        fields = []
        parts = format_str.split()
        endian_override = None
        
        for part in parts:
            # Handle endian prefix
            if part in ('>', '<'):
                endian_override = Endian.BIG if part == '>' else Endian.LITTLE
                continue
            
            # Check for endian prefix at start of token
            if part.startswith('>') or part.startswith('<'):
                endian_override = Endian.BIG if part[0] == '>' else Endian.LITTLE
                part = part[1:]
            
            # Parse count prefix (e.g., "2x" for 2 skip bytes)
            count = 1
            if part and part[0].isdigit():
                count_str = ''
                while part and part[0].isdigit():
                    count_str += part[0]
                    part = part[1:]
                count = int(count_str) if count_str else 1
            
            if not part:
                continue
            
            # Extract format char and optional name
            if ':' in part:
                fmt_char = part[0]
                name = part[2:]  # Skip format char and colon
            else:
                fmt_char = part[0]
                name = f'_field{len(fields)}'
            
            if fmt_char not in FORMAT_CHARS:
                raise ValueError(f"Unknown format character: {fmt_char}")
            
            type_name, size = FORMAT_CHARS[fmt_char]
            
            # Handle skip with count
            if type_name == 'skip':
                fields.append({
                    'name': f'_skip{len(fields)}',
                    'type': 'skip',
                    'length': count
                })
            else:
                field_def = {'name': name, 'type': type_name}
                fields.append(field_def)
        
        return fields, endian_override
    
    def _select_port_entry(self, fPort: int = None) -> Tuple[Optional[Dict[str, Any]], str]:
        """The `ports` entry a decode of `fPort` uses, with a label naming it.

        Returns ``(None, label)`` where the schema has no `ports` and the top-level
        `fields` apply. The label names the entry for diagnostics, and distinguishes a
        matched port from the `default` entry standing in for one, because saying
        "fPort 42" of a payload the default entry accepted describes the wrong thing.

        The direction check (PS-289) reads the entry this returns, so that the direction
        verified and the fields decoded always come from the same entry.
        """
        ports = self.schema.get('ports')
        if not ports:
            return None, f"schema '{self.name}'"

        if fPort is None:
            # PS-459, PS-460: with no FPort there is nothing to select by, and the
            # `default` entry is not a stand-in for "unknown". This used it, so a
            # caller that forgot the port decoded the payload as whatever the default
            # describes; and with no default the error said "fPort None", which reads
            # as the unmatched-port fault of PS-025.
            raise ValueError(
                f"no FPort was supplied, and schema '{self.name}' selects its fields "
                f"by port (PS-459)")

        port_key = str(fPort)
        if port_key in ports:
            return ports[port_key], f'fPort {fPort}'
        # Try int key (YAML may parse as int)
        if fPort in ports:
            return ports[fPort], f'fPort {fPort}'

        if 'default' in ports:
            return ports['default'], 'the default port entry'

        raise ValueError(f"No port definition for fPort {fPort} and no default in schema '{self.name}'")

    def _choose_encode_port(self, fPort: Optional[int] = None,
                            direction: Optional[str] = None) -> Optional[int]:
        """The FPort whose entry an encode uses, by PS-581 (CR-2026-169).

        A caller's FPort chooses its entry, or the `default` entry where none lists it;
        an entry declared `uplink` is refused only where a downlink is being encoded.
        Without one: the single entry declaring `downlink`, else the single
        downlink-capable entry (`downlink`, `both` or no declaration, PS-287), else the
        input is rejected naming the candidates. `default` is never chosen without an
        FPort, having no number to report. Raises ValueError to reject. Returns None for
        a schema without `ports`.
        """
        ports = self.schema.get('ports')
        if not ports:
            return None
        if fPort is not None:
            entry = ports.get(fPort, ports.get(str(fPort), ports.get('default')))
            if not isinstance(entry, dict):
                known = ', '.join(str(k) for k in sorted(
                    (k for k in ports if k != 'default'), key=lambda k: int(k)))
                raise ValueError(
                    f"FPort {fPort} matches no port entry and schema {self.name!r} "
                    f"declares no default; it defines {known or 'nothing'} (PS-581)")
            if entry.get('direction') == 'uplink' and direction == 'downlink':
                raise ValueError(
                    f"FPort {fPort} is declared direction:uplink and takes no "
                    f"downlink to encode (PS-581)")
            return fPort
        numbered = {int(k): e for k, e in ports.items()
                    if k != 'default' and isinstance(e, dict)}
        for tier, wanted in (('declared downlink', ('downlink',)),
                             ('downlink-capable', ('downlink', 'both', None))):
            candidates = sorted(k for k, e in numbered.items()
                                if e.get('direction') in wanted)
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise ValueError(
                    f"no FPort was given, and schema {self.name!r} has "
                    f"{len(candidates)} {tier} port entries "
                    f"({', '.join(map(str, candidates))}); give the FPort (PS-581)")
        raise ValueError(
            f"no FPort was given, and schema {self.name!r} has no port entry that "
            f"takes a downlink (PS-581)")

    def _resolve_fields(self, fPort: int = None) -> list:
        """Resolve fields for a given fPort (port-based schema selection)."""
        entry, _ = self._select_port_entry(fPort)
        fields = self.schema.get('fields', []) if entry is None else entry.get('fields', [])

        # Handle compact format string
        if isinstance(fields, str):
            parsed_fields, endian_override = self._parse_compact_format(fields)
            if endian_override:
                self.endian = endian_override
            return parsed_fields
        return fields

    def _direction_error(self, fPort: int = None, direction: str = None) -> Optional[str]:
        """The PS-288 error for decoding a `direction` message here, or None to proceed.

        None means no check applies: the caller did not state the direction (PS-290), or
        the selected entry declares `both`, or it declares nothing, which PS-287 reads as
        `both`. A declaration is a statement about which way traffic on that entry runs,
        so a message travelling the other way does not match the schema.

        The check reads the raw declaration rather than ``self.direction``, which defaults
        to 'uplink' for reporting. Enforcing that default would make every unannotated
        single-port schema reject downlinks, narrowing schemas already written -
        CR-2026-010 rejects that reading and keeps the annotation opt-in.
        """
        if direction is None:
            return None
        if direction not in MESSAGE_DIRECTIONS:
            raise ValueError(
                f"unknown message direction {direction!r}; "
                f"expected one of {', '.join(sorted(MESSAGE_DIRECTIONS))}"
            )

        entry, label = self._select_port_entry(fPort)
        declared = self.schema.get('direction') if entry is None else entry.get('direction')

        if declared is None:
            return None
        if declared not in DECLARED_DIRECTIONS:
            return (
                f"{label} declares unknown direction {declared!r}; "
                f"expected {', '.join(sorted(DECLARED_DIRECTIONS))}"
            )
        if declared in ('both', direction):
            return None
        return f'{label} is declared direction:{declared}; message direction is {direction}'
    
    def _resolve_ref(self, ref: str) -> Dict[str, Any]:
        """
        Resolve a $ref reference to its definition.
        
        Supports: #/definitions/name format (local references)
        """
        if not ref.startswith('#/definitions/'):
            raise ValueError(f"Unsupported $ref format: {ref}")
        
        def_name = ref.split('/')[-1]
        if def_name not in self.definitions:
            raise ValueError(f"Definition not found: {def_name}")
        
        return self.definitions[def_name]
    
    def _read_int(self, buf: bytes, pos: int, size: int, signed: bool) -> Tuple[int, int]:
        """Read integer from buffer."""
        if pos + size > len(buf):
            raise BufferUnderflow.need(size, pos, len(buf))
        
        data = buf[pos:pos + size]
        
        if self.endian == Endian.LITTLE:
            value = int.from_bytes(data, 'little', signed=signed)
        else:
            value = int.from_bytes(data, 'big', signed=signed)
        
        return value, pos + size
    
    def _write_int(self, value: int, size: int, signed: bool) -> bytes:
        """Write integer to bytes."""
        byteorder = 'little' if self.endian == Endian.LITTLE else 'big'
        return value.to_bytes(size, byteorder, signed=signed)
    
    def _read_float(self, buf: bytes, pos: int, size: int) -> Tuple[float, int]:
        """Read float from buffer."""
        if pos + size > len(buf):
            raise BufferUnderflow.need(size, pos, len(buf))
        
        data = buf[pos:pos + size]
        fmt = '<f' if self.endian == Endian.LITTLE else '>f'
        if size == 8:
            fmt = '<d' if self.endian == Endian.LITTLE else '>d'
        
        value = struct.unpack(fmt, data)[0]
        return value, pos + size
    
    def _read_float16(self, buf: bytes, pos: int) -> Tuple[float, int]:
        """Read IEEE 754 half-precision float (2 bytes)."""
        if pos + 2 > len(buf):
            raise BufferUnderflow.need(2, pos, len(buf))
        
        data = buf[pos:pos + 2]
        # Use struct 'e' format for half-precision (Python 3.6+)
        fmt = '<e' if self.endian == Endian.LITTLE else '>e'
        try:
            value = struct.unpack(fmt, data)[0]
        except struct.error:
            # Fallback: manual conversion for older Python
            value = self._float16_to_float(data)
        return value, pos + 2
    
    def _float16_to_float(self, data: bytes) -> float:
        """Manual IEEE 754 half-precision to float conversion."""
        if self.endian == Endian.LITTLE:
            h = data[0] | (data[1] << 8)
        else:
            h = (data[0] << 8) | data[1]
        
        sign = (h >> 15) & 1
        exp = (h >> 10) & 0x1F
        frac = h & 0x3FF
        
        if exp == 0:
            if frac == 0:
                return -0.0 if sign else 0.0
            # Subnormal
            return ((-1) ** sign) * (frac / 1024) * (2 ** -14)
        elif exp == 31:
            if frac == 0:
                return float('-inf') if sign else float('inf')
            return float('nan')
        
        return ((-1) ** sign) * (1 + frac / 1024) * (2 ** (exp - 15))
    
    def _decode_encoding(self, value: int, encoding: str, size: int) -> int:
        """
        Decode value from special encoding format.
        
        Supports:
        - sign_magnitude: MSB is sign bit, remaining bits are magnitude
        - bcd: Binary-coded decimal (each nibble is 0-9)
        - gray: Gray code (adjacent values differ by one bit)
        """
        if encoding == 'sign_magnitude':
            # MSB is sign, rest is magnitude
            sign_bit = 1 << (size * 8 - 1)
            if value & sign_bit:
                return -(value & (sign_bit - 1))
            return value
        
        elif encoding == 'bcd':
            # Binary-coded decimal: each nibble is a decimal digit
            result = 0
            multiplier = 1
            temp = value
            for _ in range(size * 2):  # 2 nibbles per byte
                digit = temp & 0x0F
                if digit > 9:
                    raise ValueError(f"Invalid BCD digit: {digit}")
                result += digit * multiplier
                multiplier *= 10
                temp >>= 4
            return result
        
        elif encoding == 'gray':
            # Gray code to binary: XOR with right-shifted versions
            result = value
            shift = 1
            while shift < size * 8:
                result ^= (result >> shift)
                shift <<= 1
            return result
        
        return value
    
    def _encode_encoding(self, value: int, encoding: str, size: int) -> int:
        """
        Encode value to special encoding format.
        
        Reverse of _decode_encoding for encoding payloads.
        """
        # PS-463: a value the code cannot represent is rejected, not truncated.
        if encoding == 'sign_magnitude':
            sign_bit = 1 << (size * 8 - 1)
            if abs(value) > sign_bit - 1:
                raise ValueError(f"{value} has no {size * 8}-bit sign-magnitude form (PS-463)")
            if value < 0:
                return sign_bit | abs(value)
            return value
        
        elif encoding == 'bcd':
            if value < 0 or value > 10 ** (size * 2) - 1:
                raise ValueError(f"{value} has no {size * 8}-bit BCD form (PS-463)")
            # Binary to BCD
            result = 0
            shift = 0
            temp = abs(int(value))
            while temp > 0:
                digit = temp % 10
                result |= (digit << shift)
                shift += 4
                temp //= 10
            return result
        
        elif encoding == 'gray':
            # Binary to Gray code: XOR with right-shifted self
            return value ^ (value >> 1)
        
        return value
    
    def _parse_bitfield_type(self, type_str: str) -> Tuple[int, int, int]:
        """
        Parse bitfield type string.
        
        Returns: (base_size_bytes, bit_offset, bit_width)
        """
        # Bracket range: u8[3:4] - bits 3 to 4 inclusive. This is the only bitfield
        # spelling. The Verilog part-select `u8[3+:2]`, the C++ template `bits<3,2>`,
        # the @ notation `bits:2@3` and the sequential `u8:2` were withdrawn by
        # CR-2026-006, so a schema still using one must fail loudly rather than be
        # accepted by an interpreter no other language agrees with.
        # `sN[start:end]` is a signed range (PS-352): sign-extended from the range's own
        # width (PS-353), which the decode applies.
        match = re.match(r'[us](\d+)\[(\d+):(\d+)\]$', type_str)
        if match:
            base_size = int(match.group(1)) // 8
            start = int(match.group(2))
            end = int(match.group(3))
            width = end - start + 1
            return base_size, start, width

        raise ValueError(
            f"Unknown bitfield format: {type_str} - the only bitfield spelling is "
            f"uN[start:end] or sN[start:end], e.g. u8[3:4]. uN[base+:width], bits<offset,width>, "
            f"bits:width@offset and uN:width were withdrawn by CR-2026-006"
        )
    
    def _extract_bits(self, buf: bytes, pos: int, bit_offset: int, 
                      bit_width: int, base_size: int) -> Tuple[int, int, bool]:
        """
        Extract bits from buffer.
        
        Returns: (value, new_pos, consumed_byte)
        """
        if pos >= len(buf):
            raise BufferUnderflow.peek(pos)

        # The base width is part of the type - `u24[4:23]` means bits 4-23 of a
        # 24-bit big-endian value - so read that many bytes before masking. Reading
        # only buf[pos] made every range wider than one byte decode from the first
        # byte alone: rakwireless/qingping declares a 12-bit humidity as u24[0:11]
        # and got 11 where the device means 1320.
        if pos + base_size > len(buf):
            raise BufferUnderflow.peek(pos)
        # PS-059 (CR-2026-052): the base is assembled in the field's effective byte
        # order, which `self.endian` carries here. This read it big-endian whatever the
        # schema said, while the encoder packed it in the schema's order - so a range
        # wider than a byte under a little-endian schema did not round-trip.
        raw = int.from_bytes(buf[pos:pos + base_size],
                             'little' if self.endian == Endian.LITTLE else 'big')
        mask = (1 << bit_width) - 1
        value = (raw >> bit_offset) & mask

        # A bracket range never advances the read position on its own; `consume: N`
        # is the only way to move it (PS-060). The sequential form's implicit
        # advance on reaching bit 0 went with CR-2026-006.
        return value, pos, False
    
    # A field's own `endian:` overrides the schema's for that field's read only. The
    # constructs are excluded because their members re-derive the default themselves:
    # Go and Java both compute an effective endian per field from the *context* endian,
    # so a parent's override does not reach a nested member there, and this must not be
    # the one implementation where it does.
    _ENDIAN_OPAQUE_TYPES = ('object', 'repeat', 'switch')

    def _decode_field(self, field_def: Dict[str, Any], buf: bytes,
                      pos: int) -> Tuple[Any, int]:
        """Decode a single field, honouring a field-level `endian:` override (PS-053).

        The override was parsed by Go, Java, C# and the C interpreter and silently
        ignored here: every read site consults ``self.endian``, which is schema-level,
        so a field declaring `endian: little` under a big-endian schema decoded
        big-endian and nothing reported it. The reference implementation was the only
        one of the five that got it wrong, and it is the surface `td-tools` imports.
        """
        self._produced(field_def)
        try:
            if field_def.get('type') in ('object', 'repeat'):
                # Its members are another level of output, described by the
                # declaration's nested entries (PS-371, PS-481), not by what they wrote.
                self._meta_depth += 1
                try:
                    return self._decode_field_endian(field_def, buf, pos)
                finally:
                    self._meta_depth -= 1
            return self._decode_field_endian(field_def, buf, pos)
        except BufferUnderflow as exc:
            # PS-535: the innermost field that read past the end is named; an enclosing
            # object or repeat passes its member's error through unchanged.
            if exc.named:
                raise
            raise name_underflow(exc, field_def.get('name'), field_def.get('type')) from exc

    def _produced(self, field_def: Dict[str, Any], key: Optional[str] = None) -> None:
        """PS-490: record the declaration about to report `key` (its name by default).
        The last one decoded is the one whose value is reported, so the last write wins."""
        if self._meta_depth:
            return
        key = key if key is not None else field_def.get('name')
        if isinstance(key, str) and not key.startswith('_'):
            self._producers[key] = field_def

    def _decode_field_endian(self, field_def: Dict[str, Any], buf: bytes,
                             pos: int) -> Tuple[Any, int]:
        endian_override = field_def.get('endian')
        if (endian_override is None
                or field_def.get('type', 'u8') in self._ENDIAN_OPAQUE_TYPES):
            return self._decode_field_inner(field_def, buf, pos)

        try:
            override = Endian(endian_override)
        except ValueError:
            raise ValueError(
                f"field 'endian' must be 'big' or 'little', got {endian_override!r}"
            )
        saved = self.endian
        self.endian = override
        try:
            return self._decode_field_inner(field_def, buf, pos)
        finally:
            self.endian = saved

    def _decode_field_inner(self, field_def: Dict[str, Any], buf: bytes,
                            pos: int) -> Tuple[Any, int]:
        """Decode a single field from buffer."""
        field_type = field_def.get('type')
        if not field_type:
            # PS-334: no default type. This read a typeless field as a `u8`, so a
            # missing key decoded one plausible byte and shifted every later field.
            raise ValueError(
                f"Field '{field_def.get('name', '?')}' declares no type"
            )
        consume = field_def.get('consume', None)
        
        # Handle bitfields. `hex:upper` is a string type, not a bit range, so it
        # must not be parsed as one just because it contains a colon.
        if field_type not in _COLON_STRING_TYPES and any(
            c in str(field_type) for c in ['[', ':', '<']
        ):
            base_size, bit_offset, bit_width = self._parse_bitfield_type(field_type)
            value, new_pos, auto_consumed = self._extract_bits(
                buf, pos, bit_offset, bit_width, base_size
            )
            if str(field_type).startswith('s') and value >= 1 << (bit_width - 1):
                # PS-353: two's complement over the range's own width.
                value -= 1 << bit_width
            
            # Determine position advancement
            if consume is not None:
                if pos + consume > len(buf):
                    raise BufferUnderflow.need(consume, pos, len(buf))   # PS-535
                new_pos = pos + consume
            elif auto_consumed:
                new_pos = pos + 1
            else:
                new_pos = pos
            
            return value, new_pos
        
        # Handle standard types with aliases
        # Canonical: u8/s8, Aliases: uint8/int8/i8
        type_info = {
            # Unsigned (canonical: u prefix)
            'u8': (1, False), 'uint8': (1, False),
            'u16': (2, False), 'uint16': (2, False),
            'u24': (3, False), 'uint24': (3, False),
            'u32': (4, False), 'uint32': (4, False),
            'u64': (8, False), 'uint64': (8, False),
            # Signed (canonical: s prefix, aliases: i prefix, int prefix)
            's8': (1, True), 'i8': (1, True), 'int8': (1, True),
            's16': (2, True), 'i16': (2, True), 'int16': (2, True),
            's24': (3, True), 'i24': (3, True), 'int24': (3, True),
            's32': (4, True), 'i32': (4, True), 'int32': (4, True),
            's64': (8, True), 'i64': (8, True), 'int64': (8, True),
        }
        
        # Two 16-bit big-endian units, least significant unit first (PS-271). Neither
        # `endian` setting reaches this: the type fixes both orders, and honouring
        # `endian` would make `u32le16` with `endian: little` a second spelling of plain
        # little-endian u32 (PS-272).
        if field_type in WORD_ORDERED_TYPES:
            if pos + 4 > len(buf):
                raise BufferUnderflow.need(4, pos, len(buf))
            return read_word_ordered(field_type, buf[pos:pos + 4]), pos + 4

        if field_type in type_info:
            size, signed = type_info[field_type]
            value, new_pos = self._read_int(buf, pos, size, signed)
            # Apply encoding if specified (sign_magnitude, bcd, gray)
            encoding = field_def.get('encoding')
            if encoding:
                # For encoded values, read as unsigned first
                if signed:
                    value, _ = self._read_int(buf, pos, size, False)
                # PS-427: a sentinel is the bit pattern, compared before the code. Left
                # undecoded here, so a sentinel no code can represent - BCD 0xFF - is "no
                # reading" rather than an invalid-digit error.
                if value in (field_def.get('sentinel') or ()):
                    return value, new_pos
                value = self._decode_encoding(value, encoding, size)
            return value, new_pos
        
        # Nibble-decimal types: upper nibble = whole, lower nibble = tenths (PS-330).
        # `UDec`/`SDec` are not spellings of them (PS-331).
        if field_type == 'udec':
            if pos >= len(buf):
                raise BufferUnderflow.need(1, pos, len(buf))
            byte = buf[pos]
            value = (byte >> 4) + (byte & 0x0F) * 0.1
            return value, pos + 1
        
        if field_type == 'sdec':
            if pos >= len(buf):
                raise BufferUnderflow.need(1, pos, len(buf))
            byte = buf[pos]
            whole = byte >> 4
            # Sign extend the 4-bit whole part
            if whole >= 8:
                whole -= 16
            value = whole + (byte & 0x0F) * 0.1
            return value, pos + 1
        
        if field_type in MINIFLOAT_SIZES:
            size = MINIFLOAT_SIZES[field_type]
            word, new_pos = self._read_int(buf, pos, size, False)
            value = decode_minifloat(field_type, word)
            return (OMITTED if value is None else value), new_pos

        if field_type == 'f16':
            # IEEE 754 half-precision (2 bytes)
            return self._read_float16(buf, pos)
        
        if field_type == 'bf16':
            word, new_pos = self._read_int(buf, pos, 2, False)       # PS-558
            return decode_bf16(word), new_pos

        if field_type == 'f32':
            return self._read_float(buf, pos, 4)
        
        if field_type == 'f64':
            return self._read_float(buf, pos, 8)
        
        if field_type == 'bool':
            bit = field_def.get('bit', 0)
            if pos >= len(buf):
                raise BufferUnderflow.peek(pos)
            value = bool((buf[pos] >> bit) & 1)
            # Bool doesn't advance position by default
            if consume and pos + consume > len(buf):
                raise BufferUnderflow.need(consume, pos, len(buf))       # PS-535
            if consume:
                return value, pos + consume
            return value, pos
        
        if field_type == 'bytes':
            length = resolve_length(field_def, buf, pos, variables=self._variables)
            if pos + length > len(buf):
                raise BufferUnderflow.need(length, pos, len(buf))
            # PS-588 (CR-2026-175): `consume: 0` re-reads the span; the position stays.
            return (format_bytes(field_def, buf[pos:pos + length]),
                    pos if is_echo(field_def) else pos + length)
        
        if field_type == 'string':
            if 'value' in field_def:
                # A string literal (spec "Literal Types"): a constant in the output,
                # read from no bytes. This read the payload as a one-byte string, so a
                # `{type: string, value: "ppm"}` reported "\x07" and shifted every
                # field after it.
                return field_def['value'], pos
            # PS-361: `string` is a literal or nothing; a string read from the payload
            # is `ascii`. This read the bytes as UTF-8.
            raise ValueError(
                f"Field '{field_def.get('name', '?')}': type string declares no value; a "
                f"string read from the payload is type ascii (PS-361)")
        
        if field_type == 'boolean':
            # A boolean literal (PS-357, PS-519): reported as declared, read from no
            # bytes. A missing or non-boolean value is refused at load (PS-519, PS-520).
            if 'value' not in field_def:
                raise ValueError(
                    f"Field '{field_def.get('name', '?')}': type boolean is a literal and "
                    f"declares no value (PS-519)")
            return field_def['value'], pos

        if field_type == 'ascii':
            length = resolve_length(field_def, buf, pos, variables=self._variables)
            if pos + length > len(buf):
                raise BufferUnderflow.need(length, pos, len(buf))
            value = decode_ascii(field_def.get('name'), buf[pos:pos + length],
                                 self._nested_warnings)
            if field_def.get('numeral'):
                value = decode_numeral(field_def.get('name'), value, field_def['numeral'])
            return value, pos if is_echo(field_def) else pos + length          # PS-588
        
        # `hex:upper` is a `bytes` format (PS-079), not a type (CR-2026-037), so it
        # falls through to the unknown-type error below.
        if field_type == 'hex':
            length = resolve_length(field_def, buf, pos, variables=self._variables)
            if pos + length > len(buf):
                raise BufferUnderflow.need(length, pos, len(buf))
            # PS-074: `hex` output MUST be lowercase without separators. This
            # emitted uppercase, so it disagreed with the specification, with the
            # Go and Java interpreters, and with every vendor decoder. Uppercase
            # is `type: bytes` with `format: hex:upper`.
            return buf[pos:pos + length].hex(), pos if is_echo(field_def) else pos + length
        
        if field_type == 'base64':
            import base64 as b64
            length = resolve_length(field_def, buf, pos, variables=self._variables)
            if pos + length > len(buf):
                raise BufferUnderflow.need(length, pos, len(buf))
            value = b64.b64encode(buf[pos:pos + length]).decode('ascii')
            return value, pos + length
        
        if field_type == 'skip':
            # Padding/reserved bytes - advance position but don't output
            length = resolve_length(field_def, buf, pos, variables=self._variables)
            # A skip past the end is a short read like any other, as in Go, Java and C#.
            # It succeeded, so a truncated payload decoded with no error.
            if pos + length > len(buf):
                raise BufferUnderflow.need(length, pos, len(buf))
            return None, pos + length
        
        if field_type == 'object':
            # A nested group's `fields` is a field list (PS-347, PS-441), decoded by the
            # same decoder as the top level, so a member may be a construct, a computed
            # field, a literal or an internal field. This used to be its own loop of
            # plain reads, which rejected a `byte_group` or `match` member as declaring
            # no type and a `type: number` member as an unknown type. Names it binds
            # stay bound after it, as in Go, Java and C#.
            return self._decode_nested_list(field_def.get('fields', []), buf, pos)
        
        if field_type == 'repeat':
            # Repeated/array field
            return self._decode_repeat(field_def, buf, pos)
        
        if field_type == 'enum':
            # Enum type: decode base type then map to string
            return self._decode_enum(field_def, buf, pos)
        
        # PS-327/PS-328: reject, naming the field and the type. `float`, `double`,
        # `UDec`, `SDec` and `version_string` were read here once; none is a type of
        # the specification (PS-326, PS-331, PS-401).
        raise ValueError(
            f"Field '{field_def.get('name', '?')}': unknown type: {field_type}"
        )
    
    def _decode_enum(self, field_def: Dict[str, Any], buf: bytes,
                     pos: int) -> Tuple[Any, int]:
        """Decode enum field: base integer type mapped to string value."""
        base_type = field_def.get('base', 'u8')
        values = field_def.get('values', {})
        
        # Decode the base integer type
        base_field = {'type': base_type}
        try:
            raw_value, new_pos = self._decode_field(base_field, buf, pos)
        except BufferUnderflow as exc:
            # PS-535 names the enum, not its base. Unnamed until here: the base's own
            # declaration is synthetic and has no name.
            raise name_underflow(BufferUnderflow(getattr(exc, 'detail', str(exc))),
                                 field_def.get('name'), 'enum') from exc
        # PS-523 (CR-2026-132): an enum is a lookup by another spelling, so a reference
        # binds its code and a template reads its name.
        if hasattr(self, '_lookup_numbers'):
            self._lookup_numbers[field_def] = raw_value
        
        # Map to string value
        # Values can be dict {0: 'idle', 1: 'running'} or list ['idle', 'running']
        if isinstance(values, dict):
            # Convert string keys to int if needed
            values_map = {int(k) if isinstance(k, str) else k: v for k, v in values.items()}
            if raw_value in values_map:
                return enum_label(values_map[raw_value]), new_pos
            else:
                # An unmapped value takes the declared `default` (PS-068). Only
                # where none is declared does it fall back to the marker below,
                # which was previously returned unconditionally and ignored the
                # default the schema asked for.
                if 'default' in field_def:
                    return field_def['default'], new_pos
                # PS-068 (CR-2026-132): with no `default` the field is omitted, as a
                # lookup miss is (PS-269), so it keeps one type; a reference still binds
                # the code (PS-523). This reported "unknown(N)", and the others the code.
                return OMITTED, new_pos
        elif isinstance(values, list):
            if 0 <= raw_value < len(values):
                return enum_label(values[raw_value]), new_pos
            elif 'default' in field_def:
                return field_def['default'], new_pos
            else:
                return OMITTED, new_pos      # PS-068, as above
        
        # No mapping - return raw value
        return raw_value, new_pos
    
    def _decode_repeat(self, field_def: Dict[str, Any], buf: bytes,
                       pos: int) -> Tuple[List[Any], int]:
        """
        Decode repeated/array field.

        Supports three modes:
        - count: fixed number of iterations (int or $variable)
        - byte_length: repeat until N bytes consumed (int or $variable)
        - until: "end" to repeat until payload exhausted, less any `reserve` (PS-350)

        Options:
        - max: maximum iterations (safety limit, default 1000)
        - min: minimum required iterations
        - fields: nested fields to decode per iteration
        - index, count_as, present_if (PS-366 to PS-370, PS-386): the iterator
        - reserve, trailer (PS-350, PS-351, PS-383): bytes at the end it must not read

        Each element goes through the same field-list decoder as the top level, in a
        scope of its own: its names resolve within the element and are gone after it
        (PS-368). A carried field sees its own previous value (PS-378 to PS-380).
        """
        nested_fields, element_name = repeat_members(field_def)    # PS-528
        max_iterations = field_def.get('max', 1000)
        min_iterations = field_def.get('min', 0)

        result = []
        iterations = 0

        # Determine iteration mode
        count = field_def.get('count')
        byte_length = field_def.get('byte_length')
        until = field_def.get('until')

        # PS-378, PS-380: a carried field starts again from its `carry` value each time
        # the repeat begins, including each iteration of an enclosing repeat.
        carry_state = {}
        for nested in nested_fields:
            if isinstance(nested, dict) and 'carry' in nested:
                carry_state[nested['name']] = self._carry_initial(nested)

        def element(at: int, region: bytes) -> int:
            """Decode one element from `region`; append it unless present_if drops it."""
            nonlocal iterations
            outer = dict(self._variables)
            outer_labels = dict(self._labels)
            saved_current = self._current_data
            saved_names = getattr(self, '_element_names', None)
            self._element_names = {f.get('name') for f in nested_fields if isinstance(f, dict)}
            if field_def.get('index'):
                # PS-366, PS-530: the position counted from index_base.
                self._variables[field_def['index']] = iterations + index_base(field_def)
            self._variables.update(carry_state)                      # PS-379
            scratch = DecodeResult(data={}, bytes_consumed=0)
            self._current_data = scratch.data
            # PS-541: the records this element's `records` entry yields, if any.
            saved_records = self._pending_records
            self._pending_records = []
            # PS-572: the element's position, in the locations of what it decodes.
            pushed = (self._trace.push(field_def, 'repeat', iterations)
                      if self._trace is not None else False)
            try:
                new_pos = self._decode_field_list(nested_fields, region, at, scratch)
                if scratch.errors:
                    raise reraise_text(scratch.errors[0])
                records = self._pending_records
                for carried in carry_state:
                    if carried in self._variables:
                        carry_state[carried] = self._variables[carried]
                # The element is whole here, so a name of it with no value is absent,
                # not "not yet decoded", and the condition fails (PS-540).
                keep = (not field_def.get('present_if')
                        or self._evaluate_guard({'when': [field_def['present_if']]},
                                                element_complete=True)[0])
            finally:
                # PS-368: an element's names do not outlive it.
                self._variables = outer
                self._labels = outer_labels
                self._current_data = saved_current
                self._element_names = saved_names
                self._pending_records = saved_records
                if self._trace is not None:
                    self._trace.pop(pushed)
            iterations += 1
            if keep and records:
                # PS-541: the records, as consecutive elements, in place of the element;
                # the element's other reported fields go into each, before its own.
                result.extend({**scratch.data, **record} for record in records)
            elif keep:                                               # PS-386
                # PS-528: an element repeat reports the value, or None where omitted.
                result.append(scratch.data if element_name is None
                              else element_value(scratch.data))
            return new_pos

        if count is not None:
            # Count-based: fixed number of iterations
            if isinstance(count, str) and count.startswith('$'):
                var_name = count[1:]
                if hasattr(self, '_variables') and var_name in self._variables:
                    count = int(self._variables[var_name])
                else:
                    raise ValueError(f"repeat count variable not found: {var_name}")
            else:
                count = int(count)

            # PS-396: more elements than `max` is an error, not a silent truncation. The
            # count was clamped here, so every field after the repeat was read from
            # inside an element the clamp had discarded.
            if count > max_iterations:
                raise ValueError(repeat_limit_message(
                    field_def, max_iterations, f"count {count}", pos, len(buf)))

            for _ in range(count):
                pos = element(pos, buf)

        elif byte_length is not None:
            # Byte-length based: consume specified number of bytes
            if isinstance(byte_length, str) and byte_length.startswith('$'):
                var_name = byte_length[1:]
                if hasattr(self, '_variables') and var_name in self._variables:
                    byte_length = int(self._variables[var_name])
                else:
                    raise ValueError(f"repeat byte_length variable not found: {var_name}")
            else:
                byte_length = int(byte_length)

            end_pos = pos + byte_length

            while pos < end_pos and iterations < max_iterations:
                pos = element(pos, buf)

            if pos != end_pos:
                # PS-088: the members must divide the span exactly. Two ways they do not,
                # and the message used to blame the payload for both - "expected end at 4,
                # got 2" reads as a short payload even where the schema's own ceiling
                # stopped the loop with bytes to spare (CR-2026-022).
                if iterations >= max_iterations and pos < end_pos:
                    raise ValueError(repeat_limit_message(
                        field_def, max_iterations, f"byte_length {byte_length}", pos,
                        end_pos))
                raise ValueError(
                    f"repeat byte_length mismatch: expected end at {end_pos}, got {pos}")

        elif until == 'end':
            # Until-end: repeat until payload exhausted. PS-343 to PS-344a: a tail too
            # short for a whole element is an error naming the repeat as a ragged tail,
            # tested before the element begins where the element's size is fixed. This
            # began the element and failed part-way with "Buffer too short", which read
            # as an underrun of whatever member happened to run out.
            #
            # PS-350, PS-351: the region ends `reserve` bytes before the payload does,
            # and the ragged-tail rule applies to that region. Its elements decode from
            # a buffer that stops there, so nothing inside one can read the trailer.
            reserve = int(field_def.get('reserve', 0) or 0)
            region_end = len(buf) - reserve
            if region_end < pos:
                raise ValueError(
                    f"repeat {field_def.get('name', '?')!r} reserves {reserve} byte(s) "
                    f"but {len(buf) - pos} remain at offset {pos} (PS-351)")
            region = buf[:region_end]
            element_size = fixed_element_size(nested_fields)
            while pos < region_end and iterations < max_iterations:
                if element_size is not None and region_end - pos < element_size:
                    raise RaggedTailError(ragged_tail_message(
                        field_def, region_end - pos, element_size, pos))
                start_pos = pos
                try:
                    pos = element(pos, region)
                except ValueError as exc:
                    if 'too short' not in str(exc).lower():
                        raise
                    raise RaggedTailError(ragged_tail_message(
                        field_def, region_end - start_pos, None, start_pos)) from exc
                # Safety: check we made progress
                if pos == start_pos:
                    break
            if iterations >= max_iterations and pos < region_end:
                # PS-396: stopping at the ceiling with payload left is an error, not a
                # quiet end; the bytes after it were never decoded.
                raise ValueError(repeat_limit_message(
                    field_def, max_iterations, "until: end", pos, region_end))
            # PS-383: a trailer was decoded from the reserved bytes before the first
            # element, so nothing after the repeat reads them again.
            if field_def.get('trailer'):
                pos = len(buf)
        else:
            raise ValueError("repeat field must specify one of: count, byte_length, or until")

        # Validate minimum iterations
        if len(result) < min_iterations:
            raise ValueError(f"repeat produced {len(result)} elements, but minimum is {min_iterations}")

        # PS-546: a sibling identifying these values position by position reported as
        # many values as this repeat, or the pairing names nothing.
        for name in dict.fromkeys(n for _, n, _ in sibling_references(field_def)):
            reported = (self._current_data or {}).get(name, self._variables.get(name))
            if not isinstance(reported, list) or len(reported) != len(result):
                have = len(reported) if isinstance(reported, list) else 'none'
                raise ValueError(f"Repeat '{field_def.get('name')}' has {len(result)} "
                                 f"values and '{name}', which identifies them, has {have} "
                                 f"(PS-546)")

        # PS-367: the number of elements reported, bound for every later field only.
        if field_def.get('count_as'):
            self._variables[field_def['count_as']] = len(result)

        return result, pos

    def _ref(self, name: str) -> Any:
        """The value a `$name` reference resolves to.

        PS-368: a name declared in a repeat's elements has a value only inside the
        element, and only once it is decoded. A reference that breaks either rule is an
        error rather than the 0 every other unbound reference still reads as - which is
        how a reference to the last element's field, from after the array, went unnoticed.
        """
        if name in self._variables:
            return self._variables[name]
        if name in (getattr(self, '_element_names', None) or ()):
            raise ValueError(f"${name} refers to a field of this element that is not yet "
                             f"decoded (PS-368)")
        if name in self._repeat_only_names:
            raise ValueError(f"${name} is declared only inside a repeat's elements, so it "
                             f"has no value here (PS-368)")
        return 0

    def _ref_value(self, name: str) -> Any:
        """A computed field's `$name` source or operand, or OMITTED where it has none.

        PS-314: a value that cannot be resolved is omitted, and PS-429 makes a reference
        to an omitted field a reference to an absent one. So a `ref` or `compute` naming
        a field that was not reported - a zero divisor upstream, a log of 0 - is absent
        itself. It read 0 here, where Go and C# failed the whole decode: dragino
        wsc2-lb's `rain` was reported as 0 for a zero divisor rather than left out. The
        PS-368 scope errors stay errors.
        """
        value = self._ref(name)
        if name not in self._variables:
            return OMITTED
        return value

    def _sentinel_hit(self, field_def: Dict[str, Any], raw: Any, buf: bytes,
                      start: int) -> bool:
        """PS-427: the integer read, before any modifier or encoding, is a sentinel."""
        sentinels = field_def.get('sentinel')
        if not sentinels:
            return False
        if field_def.get('encoding'):
            # `raw` already went through the code; the sentinel is the bit pattern.
            size = INTEGER_TYPE_INFO.get(str(field_def.get('type')), (None,))[0]
            if size:
                raw, _ = self._read_int(buf, start, size, False)
        return (isinstance(raw, int) and not isinstance(raw, bool)) and raw in sentinels

    def _range_value(self, field_def: Dict[str, Any], value: Any) -> Tuple[bool, Any]:
        """(checked, value): the value `valid_range` compares, or (False, _) for none.

        PS-475 puts the comparison after the arithmetic and before the lookup, so a
        looked-up field is compared on the number its label stands for - compared after,
        a label was never a number and every one read "good". A guard's `else` ends the
        sequence (PS-443) and is not compared at all.
        """
        key = field_def
        if key in getattr(self, '_guard_else', ()):
            return False, value
        if field_def.get('lookup') is not None:
            return key in self._pre_lookup, self._pre_lookup.get(key)
        return True, value

    def _range_omits(self, field_def: Dict[str, Any], value: Any) -> bool:
        """PS-428: `out_of_range: omit` and a value outside `valid_range`."""
        if field_def.get('out_of_range') != 'omit':
            return False
        checked, value = self._range_value(field_def, value)
        if not checked:
            return False
        bounds = field_def.get('valid_range')
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            return False
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return not (bounds[0] <= value <= bounds[1])

    def _mark_absent(self, field_def: Dict[str, Any], name: str, result: Any,
                     why: str) -> None:
        """Record an omitted reading in `_quality`, where `_quality` is produced.

        PS-427, PS-428: a field that declares `valid_range` produces `_quality` itself;
        otherwise the mark waits for `decode` to learn whether anything else did (PS-182).
        """
        if field_def.get('valid_range') and hasattr(result, 'quality'):
            result.quality[name] = why
        else:
            self._pending_absent[name] = why

    def _internal_encode_value(self, field_def: Dict[str, Any], data: Dict[str, Any]) -> Any:
        """What an encoder writes for an internal field that reads bytes (PS-434).

        Its `value` (PS-360); else the input's value under its name; else an error naming
        it. This used to write the field's `default` or 0, which reads back as a value
        the device never sent wherever the field was not padding - and Python alone also
        inferred a discriminator from the case the data fit.
        """
        if 'value' in field_def:
            return field_def['value']
        name = field_def.get('name')
        if name in data:
            return data[name]
        has_default, default = field_default(field_def)                # PS-554
        if has_default:
            return default
        raise ValueError(f"internal field {name!r} reads payload bytes and declares no "
                         f"value, and the input does not supply it (PS-434)")

    def _carry_initial(self, field_def: Dict[str, Any]) -> Any:
        """A carried field's value before the first element (PS-378)."""
        carry = field_def['carry']
        if isinstance(carry, str) and carry.startswith('$'):
            name = carry[1:]
            if name not in self._variables:
                raise ValueError(f"carry of {field_def.get('name')!r} names {carry}, which "
                                 f"was not decoded before the repeat (PS-381)")
            return self._variables[name]
        return carry

    def _decode_match(self, field_def: Dict[str, Any], buf: bytes, 
                      pos: int) -> Tuple[Dict[str, Any], int]:
        """
        Decode a `match` construct (Clause 4):
        
          match:
            field: $var_name    # variable-based
            length: 1           # OR inline read
            name: output_name   # optional: include match value in output
            var: var_name       # optional: store as variable
            cases:
              1: [fields...]
              2: [fields...]
        
        Case patterns:
        - Single value: 1
        - List of values: [1, 2, 3]
        - Range: "2..5" or 2..5
        - Default handling: default: error | skip | {fields}
        """
        match_def = field_def.get('match', {})
        return self._decode_match_option_b(match_def, buf, pos)

    def _bind(self, field_def: Dict[str, Any], names: List[Optional[str]], value: Any) -> None:
        """Bind a decoded field's value under each of `names`, as PS-523 says.

        A field with a lookup, or an enum (CR-2026-132), binds the label where it is a number - a
        table of sizes or exponents feeds arithmetic - and otherwise the number before
        the lookup: under a text or boolean label, and where the mapping had no entry
        and the field is omitted (PS-269). A `match` therefore selects on the number
        under a command's name (PS-524). The value reported is kept in `_labels`, which
        is what a `name_from` template writes (PS-532); OMITTED there marks a miss.

        Every variable table used to hold the label, so `$cmd` in a `compute` failed to
        convert 'report_rsp', a case keyed 129 never matched it, and a miss left the
        name unbound altogether.
        """
        names = [n for n in names if n]
        if field_def.get('lookup') is not None or field_def.get('type') == 'enum':
            number = self._lookup_numbers.pop(field_def, OMITTED)
            numeric = (value is not OMITTED and isinstance(value, (int, float))
                       and not isinstance(value, bool))
            bound = value if numeric else number
            if bound is OMITTED:
                return
            for n in names:
                self._variables[n] = bound
                self._labels[n] = value
            return
        if value is OMITTED:
            return
        for n in names:
            self._variables[n] = value
            self._labels.pop(n, None)

    def _bind_internal(self, field_def: Dict[str, Any], name: str, value: Any) -> None:
        """Bind an internal (`_`-prefixed) field's value without reporting it.

        The value is bound after modifiers, as an ordinary field's is, under its own
        name and under its `var:` if it declares one. A skip, or a lookup with no
        entry, binds nothing.
        """
        if value is None:
            return
        value = self._apply_modifiers(value, field_def)
        self._bind(field_def, [name, field_def.get('var')], value)
        self._record_internal(field_def, value)                             # PS-572

    def _record_internal(self, field_def: Dict[str, Any], value: Any) -> None:
        """PS-572: an internal field's decoded value - what it would report, its label
        where it has a lookup - at its location in the decode record."""
        if self._trace is None or value is None or value is OMITTED:
            return
        where = self._trace.loc(field_def)
        if where is None:
            return
        reported = normalize_output(value)
        if reported is not OMITTED and reported is not None:
            self._trace.internal[where] = reported

    def _decode_match_option_b(self, match_def: Dict[str, Any], buf: bytes,
                                pos: int) -> Tuple[Dict[str, Any], int]:
        """
        Decode match using Option B syntax.
        
        match_def has:
          field: $var_name  (variable-based)
          OR length: N      (inline read)
          name: key         (optional: include value in output)
          var: var_name     (optional: store as variable)
          default: error|skip|[fields]
          cases: {value: [fields], ...}
        """
        result = {}
        # PS-399: exactly one discriminator source. With both, `field` won and the
        # `length` byte was left unread, so every later field came from the wrong offset.
        if sum(key in match_def for key in ('field', 'length', 'remaining')) != 1:
            raise ValueError("a match must declare exactly one of 'field', 'length' and "
                             "'remaining' (PS-399, PS-416)")
        field_ref = match_def.get('field')
        length = match_def.get('length')
        match_name = match_def.get('name')
        match_var = match_def.get('var')
        cases = match_def.get('cases', {})
        default = match_def.get('default', 'error')
        
        discriminator = None

        if match_def.get('remaining') is True:
            # PS-414: the bytes from here to the end, less any enclosing repeat's reserve
            # - which the region-limited buffer already excludes. Nothing is read.
            discriminator = len(buf) - pos
        elif 'remaining' in match_def:
            raise ValueError("a match's remaining must be true (PS-414)")
        elif field_ref:
            # Variable-based: look up stored variable
            var_name = field_ref.lstrip('$')
            if hasattr(self, '_variables') and var_name in self._variables:
                discriminator = self._variables[var_name]
            elif hasattr(self, '_current_data') and self._current_data:
                discriminator = self._current_data.get(var_name)
        elif length is not None:
            # Inline: read bytes from payload
            if pos + length > len(buf):
                # PS-535: the discriminator is the field that read; it is named by the
                # match's `name`, where it declares one.
                raise name_underflow(BufferUnderflow.need(length, pos, len(buf)),
                                     match_def.get('name'), 'match')
            if length == 1:
                discriminator = buf[pos]
            elif length == 2:
                if self.endian == Endian.LITTLE:
                    discriminator = buf[pos] | (buf[pos + 1] << 8)
                else:
                    discriminator = (buf[pos] << 8) | buf[pos + 1]
            else:
                discriminator = int.from_bytes(buf[pos:pos + length],
                    'little' if self.endian == Endian.LITTLE else 'big')
            pos += length
            
            # Optionally include in JSON output
            if match_name:
                result[match_name] = discriminator
                if hasattr(self, '_current_data'):
                    self._current_data[match_name] = discriminator
            
            # Optionally store as variable
            if match_var:
                if not hasattr(self, '_variables'):
                    self._variables = {}
                self._variables[match_var] = discriminator
        
        if discriminator is None:
            if field_ref:
                # The key is present; what it names was never bound. Saying the key
                # is missing sent the reader to the wrong line of the schema.
                raise ValueError(
                    "Match field %s has no value: no earlier field or var: binds it"
                    % field_ref
                )
            raise ValueError("Match has neither 'field' nor 'length'")
        
        # Cases in Option B are a dict: {value: [field_list], ...}
        matched_fields = None
        default_fields = None
        chosen_key = 'default'
        
        for case_key, case_fields in cases.items():
            if case_key == 'default':
                default_fields = case_fields
                continue
            if self._match_case_pattern(discriminator, case_key):
                matched_fields = case_fields
                chosen_key = case_key
                break
        
        if matched_fields is None and default_fields is None and default == 'error':
            raise ValueError(f"No matching case for value {discriminator}")
        if self._trace is not None:                                         # PS-572
            where = self._trace.loc(match_def)
            if where is not None:
                self._trace.matches[where] = {'case': chosen_key,
                                              'value': normalize_output(discriminator)}
        if matched_fields is None:
            if default_fields is not None:
                matched_fields = default_fields
            elif default == 'skip':
                return result, pos
            elif isinstance(default, list):
                matched_fields = default
            else:
                return result, pos
        
        # A case body is a field list like any other, decoded by the same decoder as the
        # top level and repeat elements, so it has computed fields, constructs and the
        # per-field rules (optional, sentinel, out_of_range). It used to have its own
        # loop of plain reads. Its fields join the enclosing output and scope.
        scratch = DecodeResult(data={}, bytes_consumed=0)
        saved_current = getattr(self, '_current_data', None)
        self._current_data = scratch.data
        try:
            pos = self._decode_field_list(matched_fields, buf, pos, scratch)
        finally:
            self._current_data = saved_current
        if scratch.errors:
            raise reraise_text(scratch.errors[0])
        result.update(scratch.data)
        if saved_current is not None:
            saved_current.update(scratch.data)
        # A case body's `_quality` belongs to the decode, as it does in Go and C#, which
        # keep one quality table per decode.
        self._case_quality.update(scratch.quality)
        return result, pos
    
    def _decode_nested_list(self, fields: Any, buf: bytes,
                            pos: int) -> Tuple[Dict[str, Any], int]:
        """Decode a nested field list - a group's members or a tlv case body - into a
        fresh output object, returning it and the read position after it.

        The list goes through `_decode_field_list`, so it is decoded exactly as the top
        level is. Its names are bound in the decode's one scope. Its first error is
        raised, so the enclosing field fails as it would on a short read; its `_quality`
        and warnings belong to the decode, as a match case body's do.
        """
        if not isinstance(fields, list):
            raise ValueError(
                "a field list must be a list of fields, got %r (PS-441)" % (fields,))
        scratch = DecodeResult(data={}, bytes_consumed=0)
        saved_current = getattr(self, '_current_data', None)
        self._current_data = scratch.data
        try:
            pos = self._decode_field_list(fields, buf, pos, scratch)
        finally:
            self._current_data = saved_current
        if scratch.errors:
            raise reraise_text(scratch.errors[0])
        self._case_quality.update(scratch.quality)
        self._nested_warnings.extend(scratch.warnings)
        return scratch.data, pos

    def _match_case_pattern(self, value: Any, pattern: Any) -> bool:
        """
        Check if value matches case pattern.
        
        Supports:
        - Single value: 1
        - List: [1, 2, 3]
        - Range string: "2..5"
        - Range (parsed from YAML): handled as string
        """
        if value is None:
            return False
        
        # List of values. Written as a quoted flow sequence, "[1, 2, 3]" (PS-398): an
        # unquoted [1, 2, 3] is a YAML sequence used as a mapping key, which PyYAML cannot
        # load at all, and the quoted form was compared here as the literal text.
        if isinstance(pattern, str) and pattern.strip().startswith('['):
            pattern = parse_list_case_key(pattern)
            if pattern is None:
                return False
        if isinstance(pattern, list):
            return value in pattern
        
        # Range pattern (string like "2..5")
        if isinstance(pattern, str) and '..' in pattern:
            bounds = parse_range_case_key(pattern)
            if bounds is None or isinstance(value, (bool, str)):
                return False
            return bounds[0] <= value <= bounds[1]
        
        # Single value comparison. A mapping key read from JSON is always a string,
        # so the same schema as JSON carried "1" where the YAML carried 1, and an
        # integer discriminator never equalled it: 19 corpus schemas decoded a
        # different branch - netvox r718x its `default:` on every report, dnt and
        # mla20 every tlv channel as unknown - and reported success.
        if value == pattern:
            return True
        if isinstance(pattern, str) and not isinstance(value, str):
            text = pattern.strip()
            try:
                number = int(text, 16) if text.lower().startswith('0x') else int(text)
            except ValueError:
                return False
            return value == number
        return False
    
    def _decode_flagged(self, flagged_def: Dict[str, Any], buf: bytes, pos: int) -> Tuple[Dict[str, Any], int]:
        """Decode flagged/bitmask field groups."""
        field_name = flagged_def.get('field', '')
        groups = flagged_def.get('groups', [])
        
        # The mask is a reference like any other, so it reads the PS-523 binding: the
        # number under a text or boolean label, a numeric label itself, an enum's code.
        # Written `$mask`, it found nothing, because the variables are bound by name.
        var_name = str(field_name).lstrip('$')
        if var_name not in self._variables:
            raise ValueError(f"Flagged field reference not found: {field_name}")
        flags = int(self._variables[var_name])
        if self._trace is not None:                                         # PS-572
            where = self._trace.loc(flagged_def)
            if where is not None:
                self._trace.flagged[where] = flags
        
        # A present group's fields are a field list (PS-347, PS-441), decoded as the top
        # level is. They were their own loop of plain reads, so a group holding another
        # flagged, a match or a byte_group failed with "declares no type" (mutelcor's
        # nested measurement groups); computed and internal fields, sentinels,
        # out_of_range and valid_range now behave exactly as they do anywhere else.
        result = {}
        for group in groups:
            bit = group.get('bit', 0)
            if (flags >> bit) & 1:
                group_data, pos = self._decode_nested_list(group.get('fields', []), buf, pos)
                result.update(group_data)

        return result, pos
    
    def _decode_computed_field(self, field_def: Dict[str, Any]) -> Optional[float]:
        """Decode a computed field (type: number) - ref, polynomial, compute, guard.

        PS-443: the source - the `ref` value with its `polynomial`, or the `compute`
        result - then the top-level modifiers, the `transform` stages and the lookup, as
        for a field read from the payload. The modifiers and the lookup used to be
        dropped on `compute`, and the lookup on `ref` too, with success.

        PS-444: a guard is evaluated first, and a failed one's `else` is the value as
        declared. The `compute` path passed it through the `transform`, so
        `transform: [{mult: 10}]` turned `else: 5` into 50.
        """
        self._produced(field_def)
        if not (field_def.get('ref') or field_def.get('compute')):
            # Literal value
            return field_def.get('value')

        self._guard_else.discard(field_def)
        if 'guard' in field_def:
            passed, fallback = self._evaluate_guard(field_def['guard'])
            if not passed:
                self._guard_else.add(field_def)
                return fallback if fallback is not None else float('nan')

        if field_def.get('ref'):
            value = self._ref_source(field_def)
        else:
            value = self._evaluate_compute(field_def['compute'])
        # An omitted source short-circuits: there is no value to carry on with.
        if value is OMITTED or value is None:
            return value
        return self._apply_modifiers(value, field_def)
    
    def _bitfield_string_order(self, field_def: Dict[str, Any]) -> str:
        """The byte order of a bitfield_string's packed value (PS-050a, PS-090)."""
        declared = field_def.get('endian')
        if declared is None:
            return 'little' if self.endian == Endian.LITTLE else 'big'
        try:
            return Endian(declared).value
        except ValueError:
            raise ValueError(f"field 'endian' must be 'big' or 'little', got {declared!r}")

    def _decode_bitfield_string(self, field_def: Dict[str, Any], buf: bytes, pos: int) -> Tuple[str, int]:
        """Decode a bitfield_string field (e.g., firmware version)."""
        self._produced(field_def)
        length = field_def.get('length', 2)
        parts = field_def.get('parts', [])
        delimiter = field_def.get('delimiter', '.')
        prefix = field_def.get('prefix', '')
        
        if pos + length > len(buf):
            raise BufferUnderflow.need(length, pos, len(buf))
        
        data = buf[pos:pos + length]
        # PS-050a: the field's own `endian` wins. This read the document's order alone,
        # because the field-list path dispatches here without the override wrapper.
        int_val = int.from_bytes(data, self._bitfield_string_order(field_def))
        pos += length
        
        part_strs = []
        for part in parts:
            bit_off = part[0]
            bit_len = part[1]
            fmt = part[2] if len(part) >= 3 else 'decimal'
            mask = (1 << bit_len) - 1
            raw = (int_val >> bit_off) & mask
            # PS-430, PS-582: the part's format writes it; any other format is refused
            # when the schema is loaded.
            part_strs.append(format_bitfield_part(raw, bit_len, fmt))
        
        return prefix + delimiter.join(part_strs), pos
    
    def _evaluate_polynomial(self, coefficients: List[float], x: float) -> float:
        """
        Evaluate polynomial using Horner's method for numerical stability.
        
        Coefficients are in descending power order: [a_n, a_{n-1}, ..., a_1, a_0]
        Result: a_n * x^n + a_{n-1} * x^(n-1) + ... + a_1 * x + a_0
        
        Horner's form: (((a_n * x + a_{n-1}) * x + a_{n-2}) * x + ...) * x + a_0
        """
        if not coefficients:
            return 0.0
        
        result = float(coefficients[0])
        for coef in coefficients[1:]:
            result = result * x + float(coef)
        return result
    
    def _evaluate_compute(self, compute_def: Dict[str, Any]) -> float:
        """
        Evaluate a cross-field binary computation.
        
        compute_def: {op: 'add'|'sub'|'mul'|'div'|'mod'|'idiv'|'pow', a: '$field'|literal, b: '$field'|literal}
        """
        op = compute_def.get('op', 'add')
        a_spec = compute_def.get('a', 0)
        b_spec = compute_def.get('b', 0)
        
        # Resolve operands
        def resolve_operand(spec):
            if isinstance(spec, str) and spec.startswith('$'):
                value = self._ref_value(spec[1:])
                return value if value is OMITTED else float(value)
            return float(spec)

        a = resolve_operand(a_spec)
        b = resolve_operand(b_spec)
        # PS-314/PS-429: an operand with no value - a field omitted earlier, by a zero
        # divisor, a log of x <= 0, a sentinel or a lookup with no entry - cannot be
        # computed with, so this field is absent too.
        if a is OMITTED or b is OMITTED:
            return OMITTED
        
        # Apply operation
        if op == 'add':
            return a + b
        elif op == 'sub':
            return a - b
        elif op == 'mul':
            return a * b
        elif op == 'div':
            # PS-278: omit rather than emit NaN, which is not a JSON value.
            if b == 0:
                return OMITTED
            return a / b
        elif op == 'mod':
            # PS-278: a zero divisor omits the field. Returning NaN, as this used to,
            # produces `{"x": NaN}` - which is not JSON, so one zero divisor made the
            # whole decode unparseable by a conforming consumer.
            if b == 0:
                return OMITTED
            # PS-277 floored, and PS-284: operands truncate toward zero first.
            # Python's `%` is already floored, so the remainder takes the divisor's
            # sign and `mod(a, 8)` stays in 0..7.
            return float(int(a) % int(b))
        elif op == 'idiv':
            if b == 0:
                return OMITTED
            # PS-276 floored: Python's `//` rounds toward negative infinity.
            return float(int(a) // int(b))
        elif op == 'pow':
            # PS-584: binary64 a**b; a result that is not a finite real number (a
            # negative base with a fractional exponent, 0 to a negative power, an
            # overflow) omits the field, as a zero divisor does.
            try:
                result = math.pow(a, b)
            except (ValueError, OverflowError, ZeroDivisionError):
                return OMITTED
            return result if math.isfinite(result) else OMITTED
        else:
            raise ValueError(f"Unknown compute op: {op}")
    
    def _evaluate_guard(self, guard_def: Dict[str, Any],
                        element_complete: bool = False) -> Tuple[bool, Any]:
        """
        Evaluate guard conditions.
        
        guard_def: {when: [{field: '$x', gt: 0}, ...], else: fallback}
        
        Returns: (conditions_passed, fallback_value)

        PS-540 (CR-2026-117): a condition whose field is absent (PS-314) or unbound
        fails, so `else` applies, or the field is omitted without one (PS-400). It was
        read as 0 here, which passed `gte: 0` on a value the device never sent. The
        PS-368 scope errors stay errors, except for a repeat's `present_if`, evaluated
        once its element is whole (`element_complete`).
        """
        when_conditions = guard_def.get('when', [])
        else_value = guard_def.get('else', None)
        
        for condition in when_conditions:
            field_ref = condition.get('field', '')
            if isinstance(field_ref, str) and field_ref.startswith('$'):
                field_name = field_ref[1:]
                field_value = (self._variables.get(field_name, OMITTED) if element_complete
                               else self._ref_value(field_name))
                # PS-540: an absent operand fails. PS-549 (CR-2026-127): so does a
                # boolean one - comparisons take numbers, and `true` is not 1 (PS-513).
                if field_value is OMITTED or isinstance(field_value, bool):
                    return (False, else_value)
                field_value = float(field_value)
            else:
                continue  # Invalid condition
            
            # Check comparison operators
            passed = True
            if 'gt' in condition:
                passed = field_value > float(condition['gt'])
            elif 'gte' in condition:
                passed = field_value >= float(condition['gte'])
            elif 'lt' in condition:
                passed = field_value < float(condition['lt'])
            elif 'lte' in condition:
                passed = field_value <= float(condition['lte'])
            elif 'eq' in condition:
                passed = field_value == float(condition['eq'])
            elif 'ne' in condition:
                passed = field_value != float(condition['ne'])
            elif 'in' in condition:                                          # PS-543
                passed = field_value in [float(v) for v in condition['in']]
            elif 'not_in' in condition:
                passed = field_value not in [float(v) for v in condition['not_in']]
            
            if not passed:
                return (False, else_value)
        
        return (True, else_value)
    
    def _ref_source(self, field_def: Dict[str, Any]) -> float:
        """A `ref` field's source: the referenced value with its `polynomial` (PS-443)."""
        ref_field = field_def['ref']
        if isinstance(ref_field, str) and ref_field.startswith('$'):
            value = self._ref_value(ref_field[1:])
            if value is OMITTED:
                return OMITTED  # PS-314/PS-429: the referenced field has no value
            value = float(value)
        else:
            value = float(ref_field)
        coeffs = field_def.get('polynomial')
        if isinstance(coeffs, list) and len(coeffs) >= 2:
            value = self._evaluate_polynomial(coeffs, value)
        return value

    def _resolve_encode_name(self, field_def: Dict[str, Any], name: str,
                             data: Dict[str, Any]) -> Optional[str]:
        """The output key a `name_from` field was reported under, or None if unresolvable.

        Decoding resolves the template against `_variables`, which encoding does not have:
        it is handed the decoded output, keyed by field name. So a `${ref}` is looked for
        in the data first, and failing that through the field that declared `var: ref` -
        whose name is often not the variable's (PS-267).

        Without this the encoder looked the value up under the schema-declared name,
        found nothing, warned "Missing field: reading" - naming a key the schema never
        reports - and wrote a zero byte. name-from.yaml re-encoded `032a` as `0300`
        (CR-2026-030).
        """
        template = field_def.get('name_from')
        if not template:
            return name

        missing: List[str] = []

        def substitute(match):
            reference = match.group(1)
            # The template wrote the value the field reports - its label where it has a
            # lookup (PS-532) - and that is what the data carries, so it is used as is.
            if reference in data:
                value = data[reference]
            else:
                source = self._field_declaring_var(reference)
                if source and source.get('name') in data:
                    value = data[source['name']]
                else:
                    missing.append(reference)
                    return ''
            return render_template_value(value, reference, template)

        resolved = re.sub(r'\$\{(\w+)\}', substitute, str(template))
        return None if missing else resolved

    def _resolve_field_name(self, field_def: Dict[str, Any], name: str) -> str:
        """Resolve a field's output key, honouring `name_from` (PS-265, PS-266).

        The payload often identifies which instance a reading belongs to, and the
        vendor puts that in the key: "region_3_avg_dwell", "channel_2_error". The
        template's ${...} references name fields decoded earlier in this payload.
        """
        template = field_def.get('name_from')
        if not template:
            return name

        missing = []

        def substitute(match):
            reference = match.group(1)
            if reference in self._variables:
                # A template writes the value reported - a looked-up field's label -
                # not the number a reference binds (PS-532, PS-523).
                value = self._labels.get(reference, self._variables[reference])
                if value is OMITTED:
                    raise ValueError(
                        "name_from for %r references %s, which reported no value "
                        "(PS-266, PS-532)" % (name, reference))
                return render_template_value(value, reference, template)
            missing.append(reference)
            return ''

        resolved = re.sub(r'\$\{(\w+)\}', substitute, str(template))
        if missing:
            raise ValueError(
                "name_from for %r references %s, which %s not been decoded"
                % (name, ", ".join(repr(m) for m in missing),
                   "have" if len(missing) > 1 else "has")
            )
        return resolved

    def _apply_transform(self, value: float, transform_ops: List[Dict[str, Any]]) -> float:
        """
        Apply transform operations sequentially.
        
        Supported ops: sqrt, abs, pow, floor, ceiling, clamp, log10, log,
                       add, mult, div

        Returns OMITTED where a stage has no value to give: a zero divisor (PS-100) or
        the log of a non-positive number (PS-117). The log stages clamped their input at
        1e-10 instead, so log10(0) was reported as -10 - a plausible number standing in
        for no reading at all.
        """
        import math
        
        for op in transform_ops:
            if value is OMITTED:
                return OMITTED
            if 'sqrt' in op and op['sqrt']:
                value = math.sqrt(max(0, value))  # Clamp to avoid domain error
            elif 'abs' in op and op['abs']:
                value = abs(value)
            elif 'pow' in op:
                value = math.pow(value, float(op['pow']))
            elif 'floor' in op:  # Clamp lower bound (renamed from max)
                value = max(value, float(op['floor']))
            elif 'ceiling' in op:  # Clamp upper bound (renamed from min)
                value = min(value, float(op['ceiling']))
            elif 'log10' in op and op['log10']:
                value = math.log10(value) if value > 0 else OMITTED
            elif 'log' in op and op['log']:
                value = math.log(value) if value > 0 else OMITTED
            elif any(key in op for key in CANONICAL_MODIFIER_ORDER):
                # A stage normally carries one arithmetic op. Where it carries
                # several, they are applied in the canonical order so that a
                # stage cannot mean different things in different languages --
                # and so that none of them is silently dropped, which an
                # either/or chain here used to do.
                value = apply_canonical_modifiers(value, op)
            elif 'op' in op:
                # PS-390: `round` is the one named operation. Anything else - `floor`
                # and `ceiling` were read here as rounding down and up, which the
                # specification never defined and which clash with the clamp stages
                # of the same names - is rejected, not skipped.
                if op['op'] != 'round':
                    raise ValueError(
                        f"transform stage names an unknown operation {op['op']!r} "
                        f"(PS-390)")
                value = round_decimal(value, op.get('decimals', 0), op.get('ties', 'even'))
            elif 'round' in op:
                # `{round: n}` was accepted here and by the generator, and ignored with
                # success by Go, Java and C#. The specified spelling is the op: form.
                raise ValueError(
                    "`{round: n}` is not a transform stage; write "
                    "{op: round, decimals: n} (PS-390)")
            else:
                raise ValueError(
                    f"transform stage {op!r} names no operation of the PS-115 table "
                    f"(PS-390)")
        
        return value
    
    def _decode_byte_group(self, field_def: Dict[str, Any], buf: bytes,
                           pos: int, result: DecodeResult) -> int:
        """
        Decode a byte_group - multiple bitfields sharing the same byte(s).
        
        byte_group automatically handles:
        - All fields read from same starting position
        - Advances position by group size after all fields decoded
        
        Supports two formats:
            # Format 1: List directly under byte_group
            - byte_group:
                - name: flags_low
                  type: u8[0:3]
              size: 1
            
            # Format 2: Nested fields key
            - byte_group:
                size: 1
                fields:
                  - name: flags_low
                    type: u8[0:3]
        """
        byte_group = field_def.get('byte_group', [])
        
        # Handle both formats
        if isinstance(byte_group, dict):
            # Format 2: {size: N, fields: [...]}
            group_fields = byte_group.get('fields', [])
        else:
            # Format 1: list of fields directly
            group_fields = byte_group
        if not group_fields:
            return pos
        # PS-070 (CR-2026-131): the widest member's base, widened by any `size`; a size
        # narrower than a member, an overlap or a member of another kind is rejected.
        group_size = byte_group_width(field_def)
        group_endian = byte_group_endian(field_def, self.endian)
        saved_endian, self.endian = self.endian, group_endian
        
        # Decode all fields from the same starting position, the group's bytes assembled
        # in its effective byte order (PS-364).
        try:
            self._decode_byte_group_members(group_fields, buf, pos, result, int(group_size))
        finally:
            self.endian = saved_endian
        
        # Advance past the group
        return pos + group_size

    def _decode_byte_group_members(self, group_fields, buf, pos, result, group_size=1):
        # PS-364: the group's bytes are one value, and a member's bit positions refer to
        # that value. Each member used to read its own base from the group's start, which
        # agrees only where every member is as wide as the group.
        if pos + group_size > len(buf):
            # PS-535: the group is read as one value (CR-2026-131), so the failing read
            # is the group's and every member is lost; it is identified by its members.
            exc = BufferUnderflow(f"Field {byte_group_label(group_fields)}: "
                                  f"{BufferUnderflow.peek(pos)} (PS-535)")
            exc.named = True
            exc.detail = str(BufferUnderflow.peek(pos))
            raise exc
        group_value = int.from_bytes(buf[pos:pos + group_size],
                                     'little' if self.endian == Endian.LITTLE else 'big')
        for gf in group_fields:
            # A member MAY leave reserved bits unnamed (PS-553): read, and neither
            # reported nor bound. Python reported it under the key `unknown`.
            name = gf.get('name') or ''
            try:
                # PS-070: every member is a range of the group's value, read from its
                # start - a whole integer is its full range, a bool one bit - so no member
                # moves the position. A whole integer used to be read as its own field.
                _, start, end, signed = byte_group_member_range(gf)
                self._produced(gf)
                width = end - start + 1
                value = (group_value >> start) & ((1 << width) - 1)
                if gf.get('type') == 'bool':
                    value = bool(value)
                elif signed and value >= 1 << (width - 1):
                    value -= 1 << width
                value = self._apply_modifiers(value, gf)
                if name and not name.startswith('_'):
                    result.data[name] = value
                # Add to variables so field can be referenced by compute/formula
                self._bind(gf, [name, gf.get('var')], value)
                if name.startswith('_') and self._trace is not None:
                    # PS-572: as the prototype records a group member - a bool its flag,
                    # any other the number its name binds.
                    self._record_internal(gf, value if gf.get('type') == 'bool'
                                          else self._variables.get(name, OMITTED))
            except Exception as e:
                result.errors.append(error_text(f"Error in byte_group field {name}: ", e))
    
    def _decode_tlv(self, field_def: Dict[str, Any], buf: bytes,
                    pos: int,
                    outer: Optional[DecodeResult] = None,
                    ) -> Tuple[Dict[str, Any], int]:
        """
        Decode TLV (Type-Length-Value) loop using Option B syntax.
        
        tlv:
          tag_size: 1
          length_size: 0        # 0 = implicit (no length field)
          merge: true           # merge into parent (default)
          unknown: skip|error|raw
          cases:
            0x01:
              - name: temperature
                type: s16
        """
        tlv_def = field_def.get('tlv', {})
        tag_size = tlv_def.get('tag_size', 1)
        # PS-471: the loop stops `reserve` bytes before the payload ends, and the
        # fields after the tlv decode from them. A buffer that stops there keeps every
        # entry read inside the region.
        reserve = int(tlv_def.get('reserve', 0) or 0)
        if reserve:
            if len(buf) - pos < reserve:
                raise ValueError(f"tlv reserves {reserve} byte(s) but {len(buf) - pos} "
                                 f"remain at offset {pos} (PS-471)")
            buf = buf[:len(buf) - reserve]
        length_size = tlv_def.get('length_size', 0)
        merge = tlv_def.get('merge', True)
        unknown_mode = tlv_def.get('unknown', 'skip')
        cases = tlv_def.get('cases', {})
        tag_fields = tlv_def.get('tag_fields')
        tag_key = tlv_def.get('tag_key')
        
        result = {}
        channels = []
        # PS-525: how a name reported again combines. Its schema errors were reported
        # at load (on_repeat_schema_errors), so only the modes are needed here.
        modes, last_members, _ = tlv_repeat_modes(tlv_def)
        collected = set()       # names already turned into arrays (PS-157)
        entry_index = -1        # PS-572: each entry's index, in payload order

        while pos < len(buf):
            # Where this entry begins, so PS-302 can count the bytes abandoned from the
            # unknown tag itself rather than from after it.
            entry_start = pos
            entry_index += 1
            # PS-477: a tag is never partly read. Fewer bytes than the tag at the start of
            # an entry is an error identifying the tlv; this used to stop the loop and
            # report a complete decode. A composite tag's width is its tag_fields'.
            width = (fixed_element_size(tag_fields) if tag_fields and tag_key else None) \
                or tag_size
            if pos + width > len(buf):
                raise ValueError(
                    f"tlv entry at offset {pos}: {len(buf) - pos} byte(s) remain, fewer "
                    f"than its {width}-byte tag (PS-477)")
            
            if tag_fields and tag_key:
                # Composite tag: read sub-fields
                tag_parts = {}
                tag_start = pos
                for tf in tag_fields:
                    tf_name = tf.get('name', 'unknown')
                    tf_value, pos = self._decode_field(tf, buf, pos)
                    tag_parts[tf_name] = tf_value
                    # A component is a field decoded earlier in the payload, so its name
                    # is a reference the case body may make (`ref: $rsn`, PS-265), as a
                    # field's is. It was bound only under `var:`, so `$rsn` read the 0 an
                    # unbound name reads here and Go and C# failed the decode.
                    # A component's `var:` binds for the case body (a `name_from`
                    # template, a `match`), as a field's does; it bound nothing, so
                    # `name_from: "temperature_${ch}"` failed in every case.
                    # PS-523 (CR-2026-132): both bind as any field's do - through its
                    # modifiers, and a lookup's number under a text label, with the
                    # label kept for templates. The tag is still matched on the value read.
                    self._bind(tf, [tf.get('name'), tf.get('var')],
                               self._apply_modifiers(tf_value, tf))
                
                # Build composite key for matching
                if isinstance(tag_key, list):
                    tag_tuple = tuple(tag_parts[k] for k in tag_key)
                else:
                    tag_tuple = (tag_parts[tag_key],)
            else:
                # Simple tag
                if tag_size == 1:
                    tag_value = buf[pos]
                elif tag_size == 2:
                    if self.endian == Endian.LITTLE:
                        tag_value = buf[pos] | (buf[pos + 1] << 8)
                    else:
                        tag_value = (buf[pos] << 8) | buf[pos + 1]
                else:
                    tag_value = int.from_bytes(buf[pos:pos + tag_size],
                        'little' if self.endian == Endian.LITTLE else 'big')
                pos += tag_size
                tag_tuple = (tag_value,)
            
            # Read length if present
            data_length = None
            if length_size > 0:
                # PS-486: an entry is never partly read. A length cut short is an error,
                # not leftover bytes for PS-472: the schema describes them.
                if pos + length_size > len(buf):
                    raise ValueError(
                        f"tlv entry at offset {entry_start}: {len(buf) - pos} byte(s) "
                        f"remain after its tag, fewer than its {length_size}-byte "
                        f"length (PS-486)")
                if length_size == 1:
                    data_length = buf[pos]
                elif length_size == 2:
                    if self.endian == Endian.LITTLE:
                        data_length = buf[pos] | (buf[pos + 1] << 8)
                    else:
                        data_length = (buf[pos] << 8) | buf[pos + 1]
                pos += length_size
                # PS-486: nor may its value run past the end (`buf` already stops at a
                # `reserve`). This holds for an unknown tag that would be skipped too.
                if data_length > len(buf) - pos:
                    raise ValueError(
                        f"tlv entry at offset {entry_start}: its length declares "
                        f"{data_length} byte(s), {len(buf) - pos} remain (PS-486)")
            
            # Find matching case. Exact keys are tried first, then negated, then
            # wildcard, so a specific case is never shadowed by a broader one
            # (PS-270).
            matched_fields = None
            for specificity in (0, 1, 2):
                for case_key, case_fields in cases.items():
                    if case_key == 'default':
                        continue
                    # Normalize case key for comparison
                    if isinstance(case_key, (list, tuple)):
                        if specificity == 0 and tuple(case_key) == tag_tuple:
                            matched_fields = case_fields
                            break
                    elif isinstance(case_key, str) and case_key.startswith('['):
                        matched, key_specificity = _match_composite_key(case_key, tag_tuple)
                        if matched and key_specificity == specificity:
                            matched_fields = case_fields
                            break
                    elif (
                        specificity == 0
                        and len(tag_tuple) == 1
                        and self._match_case_pattern(tag_tuple[0], case_key)
                    ):
                        matched_fields = case_fields
                        break
                if matched_fields is not None:
                    break
            
            if matched_fields is None:
                # A tag the schema does not describe. Whatever the mode, the fact is
                # reported: silence here is indistinguishable from a device that sent
                # fewer fields, which is what PS-301 and PS-302 exist to prevent.
                tag_text = ", ".join(
                    f"0x{part:02X}" if isinstance(part, int) else str(part)
                    for part in tag_tuple
                )

                if unknown_mode == 'error':
                    raise ValueError(f"Unknown TLV tag: {tag_text}")

                if unknown_mode == 'raw':
                    span = data_length if data_length is not None else len(buf) - pos
                    entry = {'tag': list(tag_tuple), 'raw': buf[pos:pos + span].hex()}
                    # PS-303: reported either way. Merged output has no channel list to
                    # put it in, so it goes under `unknown_tags`, where it cannot collide
                    # with a field name.
                    if merge:
                        result.setdefault('unknown_tags', []).append(entry)
                    else:
                        channels.append(entry)
                    pos += span
                    if data_length is None:
                        if outer is not None:
                            outer.warnings.append(
                                f"unknown TLV tag ({tag_text}) captured raw; "
                                f"{span} byte(s) after it could not be delimited"
                            )
                        break
                    continue

                # skip, the default
                if data_length is not None:
                    if outer is not None:
                        outer.warnings.append(
                            f"unknown TLV tag ({tag_text}) skipped, "
                            f"{data_length} byte(s) discarded"
                        )
                    pos += data_length
                    continue

                # Without a length there is nothing to skip over, so decoding stops here
                # and everything from the tag onwards is lost (PS-302).
                if outer is not None:
                    outer.warnings.append(
                        f"unknown TLV tag ({tag_text}) at offset {entry_start}: "
                        f"{len(buf) - entry_start} of {len(buf)} byte(s) left undecoded"
                    )
                # This warning is PS-472's for these bytes; no second one is reported.
                self._leftover_reported = True
                break
            
            # A case body is a field list (PS-347, PS-441), decoded by the same decoder
            # as the top level, match cases and repeat elements, so it has computed
            # fields, literals, `var:` bindings, internal fields, constructs and the
            # per-field rules (optional, sentinel, out_of_range). It used to be its own
            # loop that knew only `byte_group` and `bitfield_string`, so a number
            # literal in a case was "unknown type: number".
            pushed = (self._trace.push(tlv_def, 'tlv', entry_index)
                      if self._trace is not None else False)
            try:
                tag_result, pos = self._decode_nested_list(matched_fields, buf, pos)
            finally:
                if self._trace is not None:
                    self._trace.pop(pushed)

            if merge:
                for k, v in tag_result.items():
                    mode = modes.get(k, 'collect')
                    if k not in result:
                        result[k] = v
                    elif mode != 'collect':                         # PS-525
                        result[k] = combine_repeated(k, result[k], v, mode,
                                                     last_members.get(k, set()))
                    elif k in collected:                            # PS-157
                        result[k].append(v)
                    else:
                        # A name reported again becomes an array. Whether it already is
                        # one is not the test: a value that is itself a list (a repeat)
                        # was appended into, so its second report was spliced among the
                        # first's elements.
                        result[k] = [result[k], v]
                        collected.add(k)
            else:
                entry = {'tag': list(tag_tuple)}
                entry.update(tag_result)
                channels.append(entry)
        
        if not merge and channels:
            result['channels'] = channels
        
        return result, pos
    
    def _check_valid_range(self, value: Any, field_def: Dict[str, Any], 
                           result: 'DecodeResult') -> str:
        """
        Check if value is within valid_range and update quality.
        
        Returns: "good" if in range (or no range defined), "out_of_range" otherwise
        """
        valid_range = field_def.get('valid_range')
        name = field_def.get('name', 'unknown')
        checked, value = self._range_value(field_def, value)
        if not checked:
            return "good"
        
        if valid_range is None or not isinstance(value, (int, float)):
            return "good"
        
        if not isinstance(valid_range, list) or len(valid_range) < 2:
            return "good"
        
        min_val, max_val = valid_range[0], valid_range[1]
        
        if value < min_val or value > max_val:
            result.warnings.append(
                f"{name}: value {value} outside valid range [{min_val}, {max_val}]"
            )
            return "out_of_range"
        
        return "good"
    
    def _apply_modifiers(self, value: Any, field_def: Dict[str, Any]) -> Any:
        """Apply arithmetic modifiers to decoded value."""
        if not isinstance(value, (int, float)):
            return value
        
        value = apply_canonical_modifiers(value, field_def)

        # Apply transform array (new declarative constructs)
        transform = field_def.get('transform')
        if transform and isinstance(transform, list) and value is not OMITTED:
            value = self._apply_transform(float(value), transform)
        
        # The value valid_range compares, before the lookup (PS-475).
        if field_def.get('lookup') is not None and field_def.get('valid_range'):
            self._pre_lookup[field_def] = value
            # An omitted value takes no further step (PS-443): it is not looked up, so
            # an index the table lacks is no error when the field is dropped anyway.
            if self._range_omits(field_def, value):
                return value
        # PS-523: a reference binds the number before the lookup where the lookup
        # reports a label, so it is kept for `_bind` to find.
        if field_def.get('lookup') is not None:
            self._lookup_numbers[field_def] = value
        # Apply lookup table
        value = apply_lookup(value, field_def.get('lookup'))
        
        return value
    
    def _report_leftover(self, payload: bytes, pos: int, result: DecodeResult) -> None:
        """PS-472: bytes after the last field are reported, not silently dropped.

        The decode is reported as it would be otherwise; this only warns, with the
        offset of the first byte not decoded and the count to the end. A frame from newer
        firmware and a frame decoded with the wrong layout both used to look complete.
        """
        left = len(payload) - pos
        if left <= 0 or result.errors or self._leftover_reported:
            return
        result.warnings.append(
            f"{left} byte(s) after the last field left undecoded, from offset {pos} "
            f"(PS-472)")

    def decode(self, payload: bytes, fPort: int = None, input_metadata: Dict[str, Any] = None,
               direction: str = None, trace: Optional[DecodeTrace] = None) -> DecodeResult:
        """
        Decode payload bytes using schema.
        
        Args:
            payload: Raw payload bytes
            fPort: LoRaWAN fPort (for port-based schema selection)
            input_metadata: Optional TS013 input metadata (recvTime, rxMetadata, etc.)
            direction: Direction the message was travelling, 'uplink' or 'downlink'.
                Omit where the caller cannot know it, such as a schema-authoring tool
                decoding a captured payload; the check is then skipped and PS-021 is
                not satisfied (PS-290).
            
        Returns:
            DecodeResult with decoded data
        """
        result = DecodeResult(data={}, bytes_consumed=0)
        if self._load_errors:
            result.errors.extend(self._load_errors)
            return result

        # PS-335, PS-337: a top-level fPort is a statement about the document, checked
        # here and never consulted to select fields (PS-336).
        document_problems = fport_declaration_errors(self.schema)
        if document_problems:
            result.errors.extend(document_problems)
            return result

        # PS-021: a message travelling the way the selected entry says it does not is
        # not decoded at all. Uplink bytes read through downlink field definitions
        # produce numbers with no relationship to what the device measured, and nothing
        # in the output would mark them as such, so no field is reported (PS-288).
        direction_error = self._direction_error(fPort, direction)
        if direction_error:
            result.errors.append(direction_error)
            return result

        # Track current data for match references
        self._current_data = result.data
        # PS-572: the record `interpret(..., decode_record=True)` keeps; decode() itself
        # never reports it.
        self._trace = trace
        # Variable storage for Option B match references
        self._variables = {}
        # PS-523, PS-532: the value a looked-up field reports, by the names it binds,
        # for templates; and the number before each lookup, by field definition.
        self._labels = {}
        self._lookup_numbers = IdentityTable()
        # PS-541: the records the current repeat element yields; None outside one.
        self._pending_records = None
        # Readings omitted under PS-427/PS-428, for `_quality` if it is produced.
        self._pending_absent = {}
        # `_quality` from match case bodies, which decode into a scratch result.
        self._case_quality = {}
        # Warnings raised inside a nested field list, reported with the decode's own.
        self._nested_warnings = []
        # Set where a PS-302 warning has already reported the bytes left (PS-472).
        self._leftover_reported = False
        # PS-475: a looked-up field's pre-lookup value, and the guards that reported
        # their `else`, by field definition.
        self._pre_lookup = IdentityTable()
        self._guard_else = IdentityTable()
        self._producers = {}
        self._meta_depth = 0
        
        pos = 0
        fields = self._resolve_fields(fPort)
        
        pos = self._decode_field_list(fields, payload, 0, result)
        
        result.bytes_consumed = pos
        self._report_leftover(payload, pos, result)
        
        # Metadata enrichment
        metadata_def = self.schema.get('metadata')
        if metadata_def and input_metadata is not None:
            self._enrich_metadata(result.data, metadata_def, input_metadata,
                                  result.warnings)
        
        result.warnings.extend(self._nested_warnings)
        for case_name, flag in self._case_quality.items():
            result.quality.setdefault(case_name, flag)
        # PS-427, PS-428: an omitted reading is recorded where `_quality` is produced -
        # which a field declaring `valid_range` does (PS-182) - and only there.
        if result.quality:
            for omitted_name, why in self._pending_absent.items():
                result.quality.setdefault(omitted_name, why)
        # Add quality dict to output if any quality flags were set
        if result.quality:
            result.data['_quality'] = dict(result.quality)

        # CR-2026-008: report each value in its JSON representation. Done once here
        # rather than at each of the several places a value enters result.data, so no
        # decode path can bypass it.
        normalized = {}
        for key, value in result.data.items():
            reported = normalize_output(value)
            if reported is not OMITTED:
                normalized[key] = reported
        result.data = normalized

        return result
    
    def interpret(self, payload: bytes, fPort: int = None,
                  input_metadata: Dict[str, Any] = None,
                  direction: str = None, decode_record: bool = False) -> DecodeResult:
        """Interpreter output: the decoded JSON with its `_meta` (PS-174, PS-175, PS-180).

        `decode()` returns the decoded fields alone and is unchanged, because td-tools and
        every other caller reads it; a generated codec never carries `_meta` (PS-467).
        This is what an interpreter integration hands downstream.

        The input context (PS-495) is the TS013 uplink input and the device's EUI:
        `fPort`, and `input_metadata` with `recvTime` and `devEUI` (`fPort` there too,
        where the argument is not given). `_meta` holds:
          - `schema` and `version` from the schema;
          - `device_eui` (PS-496), `rx_time` (PS-177) and `fPort` (PS-340 to PS-342),
            each omitted where the context does not supply it;
          - `fields`: exactly one entry per top-level reported key (PS-181), from the
            declaration that produced its value (PS-490), a `name_from` key's included
            (PS-492). Nested entries list what is declared (PS-371, PS-481) and no
            internal field has one at any depth (PS-494).
        A failed decode has no `_meta` (PS-497), and neither has a malformed devEUI or
        recvTime: each is an error, as in the specification prototype.

        With `decode_record`, the output also carries `_decode` (PS-572, CR-2026-162): the
        location of every match evaluated with the case it took and the discriminator it
        read, every internal field decoded with its value, and every flagged construct with
        its flags. `encode` follows it where the input is given back with it (PS-573).
        """
        context = dict(input_metadata or {})
        if fPort is None and isinstance(context.get('fPort'), int):
            fPort = context['fPort']
        trace = DecodeTrace(self._pointers) if decode_record else None
        try:
            result = self.decode(payload, fPort=fPort, input_metadata=input_metadata,
                                 direction=direction, trace=trace)
        finally:
            self._trace = None
        if not result.success:
            return result

        meta: Dict[str, Any] = {}
        if self.schema.get('name') is not None:
            meta['schema'] = self.schema['name']
        if 'version' in self.source_schema:
            meta['version'] = self.source_schema['version']
        # PS-496, PS-495: a context that is present but malformed is refused, not
        # silently dropped - the Integration Layer binds identity to device_eui (IL-191),
        # and a missing one would read as "not supplied".
        if context.get('devEUI') is not None:
            eui = normalise_dev_eui(context['devEUI'])
            if eui is None:
                result.errors.append("devEUI %r is not 16 hexadecimal digits (PS-496)"
                                     % (context['devEUI'],))
                return result
            meta['device_eui'] = eui
        if context.get('recvTime') is not None:
            rx_time = rx_time_seconds(context['recvTime'])
            if rx_time is None:
                result.errors.append("recvTime %r is not an ISO 8601 time or a number of "
                                     "seconds (PS-495)" % (context['recvTime'],))
                return result
            meta['rx_time'] = rx_time
        if fPort is not None:
            meta['fPort'] = fPort                          # PS-340, PS-342: as supplied

        declared = meta_declarations(self._resolve_fields(fPort))
        siblings = element_repeats(self._resolve_fields(fPort))       # PS-546
        meta['fields'] = {}
        for key in result.data:
            if key.startswith('_'):
                continue                                   # `_quality`, `_warnings`
            source = self._producers.get(key) or declared.get(key)
            meta['fields'][key] = (field_meta(source, siblings=siblings)
                                   if source is not None else {})
        result.data = dict(result.data, _meta=meta)
        if trace is not None:
            result.data['_decode'] = trace.as_dict()                 # PS-572
        return result

    def _decode_field_list(self, fields: List[Dict[str, Any]], payload: bytes, pos: int,
                           result: DecodeResult) -> int:
        """Decode a list of fields into `result`, returning the read position after them.

        The top-level field list and every repeat element go through here, so an element
        has the same constructs, computed fields and internal fields as the top level.
        Elements used to decode each member with `_decode_field` alone, which knows no
        computed type and no bare construct, and reported `_` members.

        Errors are recorded in `result.errors`; a field that cannot be read stops the
        list, since every later offset would be wrong.
        """
        for field_def in fields:
            # PS-383: a repeat's trailer is decoded from the reserved bytes before its
            # first element, so its names are bound for the elements, and it is reported
            # beside the repeat.
            if field_def.get('type') == 'repeat' and field_def.get('trailer'):
                reserve = int(field_def.get('reserve', 0) or 0)
                if len(payload) - reserve < pos:
                    result.errors.append(
                        f"Error decoding {field_def.get('name', '?')}: repeat reserves "
                        f"{reserve} byte(s) but {len(payload) - pos} remain at offset "
                        f"{pos} (PS-351)")
                    break
                self._decode_field_list(field_def['trailer'], payload,
                                        len(payload) - reserve, result)
                if result.errors:
                    break
            # PS-402, PS-403: an optional field is decoded where its bytes remain and is
            # absent where none do. Every later field is optional too (PS-404), so once
            # one is absent the list ends. Some bytes but too few is an error: an optional
            # field is never partly read.
            if field_def.get('optional') is True:
                remaining = len(payload) - pos
                if remaining <= 0:
                    break
                size = optional_field_size(field_def)
                if size and remaining < size:
                    result.errors.append(
                        f"Error decoding {field_def.get('name', '?')}: optional field takes "
                        f"{size} byte(s) but {remaining} remain at offset {pos} (PS-403)")
                    break
            # Handle $ref - inline the referenced definition
            if '$ref' in field_def:
                try:
                    ref_def = self._resolve_ref(field_def['$ref'])
                    ref_fields = ref_def.get('fields', [])
                    for rf in ref_fields:
                        rf_name = rf.get('name') or ''      # PS-553: a skip only
                        if rf_name and not rf_name.startswith('_'):
                            value, pos = self._decode_field(rf, payload, pos)
                            value = self._apply_modifiers(value, rf)
                            if value is not None:
                                result.data[rf_name] = value
                        else:
                            _, pos = self._decode_field(rf, payload, pos)
                except Exception as e:
                    result.errors.append(error_text(f"Error resolving $ref: ", e))
                    # A construct that fails to read stops the decode, as a plain field's error
                    # does. These went on from wherever the failed read left the position, so a
                    # following field was read from the wrong offset and reported beside the error
                    # (a trailer after a failed tlv came back as the tlv's own bytes). Go, Java and
                    # C# stop at the first error.
                    break
                continue
            
            # Handle byte_group construct
            if 'byte_group' in field_def:
                try:
                    pos = self._decode_byte_group(field_def, payload, pos, result)
                except Exception as e:
                    # A schema error such as overlapping members (PS-397) fails the
                    # decode like any other field's error, rather than raising out of it.
                    result.errors.append(error_text(f"Error decoding byte_group: ", e))
                    break
                continue
            
            # PS-541: each field list yields one record, which the enclosing repeat
            # reports in place of the element. records_errors() confines it, at load, to
            # a repeat's element or a match case within it.
            if 'records' in field_def and not field_def.get('type'):
                if self._pending_records is None:
                    result.errors.append("`records` belongs in a repeat's element, or "
                                         "a match case within it (PS-541)")
                    break
                try:
                    for n, body in enumerate(field_def['records']):
                        # PS-572: a record's location is `records/n[0]`.
                        pushed = (self._trace.push(field_def['records'], 'records', 0,
                                                   f"/{n}")
                                  if self._trace is not None else False)
                        try:
                            record, pos = self._decode_nested_list(body, payload, pos)
                        finally:
                            if self._trace is not None:
                                self._trace.pop(pushed)
                        self._pending_records.append(record)
                except Exception as e:
                    result.errors.append(f"Error in records: {e}")
                    break
                continue

            # Option B: match: as top-level key
            if 'match' in field_def and not field_def.get('type'):
                try:
                    match_result, pos = self._decode_match(field_def, payload, pos)
                    result.data.update(match_result)
                except Exception as e:
                    result.errors.append(error_text(f"Error in match: ", e))
                    break
                continue
            
            # The `object:` key is withdrawn (PS-466, CR-2026-074): a nested group is
            # `type: object` with `name` and `fields`. This was the only implementation
            # that read the key; Go, Java, C# and the generator never did.
            if 'object' in field_def and not field_def.get('type'):
                result.errors.append(object_key_withdrawn(field_def))
                break
            
            # Option B: tlv: as top-level key
            if 'tlv' in field_def and not field_def.get('type'):
                try:
                    tlv_result, pos = self._decode_tlv(field_def, payload, pos, result)
                    result.data.update(tlv_result)
                except Exception as e:
                    result.errors.append(error_text(f"Error in tlv: ", e))
                    break
                continue
            
            # Phase 2: flagged: construct (bitmask field presence)
            if 'flagged' in field_def and not field_def.get('type'):
                try:
                    flagged_def = field_def['flagged']
                    flagged_result, pos = self._decode_flagged(flagged_def, payload, pos)
                    result.data.update(flagged_result)
                    # The groups' fields are bound and range-checked by the field-list
                    # decoder that read them.
                except Exception as e:
                    result.errors.append(error_text(f"Error in flagged: ", e))
                    break
                continue
            
            name = field_def.get('name') or ''   # PS-553: only a skip is nameless
            field_type = field_def.get('type', 'u8')
            
            # Phase 2: bitfield_string type
            if field_type == 'bitfield_string':
                try:
                    value, pos = self._decode_bitfield_string(field_def, payload, pos)
                    result.data[name] = value
                    self._variables[name] = value
                except BufferUnderflow as e:
                    result.errors.append(str(name_underflow(e, field_def.get('name'),
                                                            field_type)))
                    break
                except Exception as e:
                    result.errors.append(error_text(f"Error decoding {name}: ", e))
                    break
                continue
            
            # Computed field - supports ref, polynomial, compute, guard.
            # `integer` (PS-283) has identical semantics to `number` and declares that
            # the result is an integer, so it reports as one. It performs no rounding
            # of its own: `idiv` truncates and {op: round} rounds, both already
            # available, and a type that also rounded would give two spellings for one
            # operation - the defect CR-2026-006 removed from bitfields.
            if field_type in COMPUTED_TYPES:
                try:
                    value = self._decode_computed_field(field_def)
                    if value is OMITTED:
                        # Zero divisor (PS-278): the field is absent, and decoding of
                        # the rest of the payload continues. A lookup with no entry
                        # omits it too, and leaves the name bound to the number (PS-523).
                        self._bind(field_def, [name], value)
                        continue
                    if field_type == 'integer' and value is not None:
                        # PS-283: an error, not a silent truncation. The schema is
                        # expected to state which rounding it means, with `idiv` or a
                        # {op: round} stage.
                        try:
                            numeric = float(value)
                        except (TypeError, ValueError):
                            numeric = None
                        if numeric is not None and not numeric.is_integer():
                            result.errors.append(
                                "%s: type integer but the computed value is %r; add "
                                "`idiv` to truncate or a {op: round} transform stage"
                                % (name, value)
                            )
                            continue
                    if value is not None and self._range_omits(field_def, value):
                        self._mark_absent(field_def, name, result, 'out_of_range')
                        continue
                    if value is not None:
                        # A leading underscore marks an internal field: it becomes a
                        # variable later fields can reference, but is not reported.
                        # Every other construct checked this; the computed-field path
                        # did not, so mclimate/vicki reported four intermediates
                        # (_motorPosPercent, _motorPosRatio and two high-byte helpers)
                        # in its output.
                        if not name.startswith('_'):
                            result.data[name] = value
                        else:
                            self._record_internal(field_def, value)         # PS-572
                        self._bind(field_def, [name], value)
                        # Check valid_range for computed fields
                        if field_def.get('valid_range'):
                            quality = self._check_valid_range(value, field_def, result)
                            result.quality[name] = quality
                except Exception as e:
                    result.errors.append(error_text(f"Error computing {name}: ", e))
                continue
            
            # An internal field is read and bound, but not reported. It used to be
            # read and discarded, so `$_kind` in a later `match`, `ref` or `var:`
            # resolved to nothing - while the computed-field path above already bound
            # `_` names, which is what 31 corpus intermediates rely on. Go, Java, C#
            # and the TS013 generator all bind it. PS-176 reserves `_` names for
            # interpreter metadata and the specification has no other spelling for
            # "decoded and referenced but not emitted", so this is a prototype
            # convention pending a CR, not specified behaviour.
            if name.startswith('_'):
                try:
                    start = pos
                    value, pos = self._decode_field(field_def, payload, pos)
                    if not self._sentinel_hit(field_def, value, payload, start):
                        self._bind_internal(field_def, name, value)
                except Exception as e:
                    result.errors.append(error_text(f"Error in internal field: ", e))
                continue
            
            try:
                start = pos
                value, pos = self._decode_field(field_def, payload, pos)
                if value is not None and self._sentinel_hit(field_def, value, payload, start):
                    # PS-427: a reserved value means no reading; the field is absent.
                    self._mark_absent(field_def, name, result, 'absent')
                    continue
                # Skip type returns None - don't add to output
                if value is not None:
                    value = self._apply_modifiers(value, field_def)
                    if value is not OMITTED and self._range_omits(field_def, value):
                        # PS-428: outside the range is no reading, not a flagged one.
                        self._mark_absent(field_def, name, result, 'out_of_range')
                        continue
                    if value is OMITTED:
                        # A lookup with no entry for this value: the device did not
                        # report anything the schema can name, so the field is left
                        # out rather than reported as a raw integer (PS-269). A
                        # reference to it still binds the number (PS-523).
                        self._bind(field_def, [field_def.get('var'), name], value)
                        continue
                    output_name = self._resolve_field_name(field_def, name)
                    if output_name != name:
                        self._produced(field_def, output_name)      # PS-492
                    result.data[output_name] = value
                    # Check valid_range and update quality
                    if field_def.get('valid_range'):
                        quality = self._check_valid_range(value, field_def, result)
                        result.quality[output_name] = quality
                    # Store the value under its `var:` (Option B) and always under the
                    # field name (for flagged/formula lookups). The schema-level name is
                    # used here, not the resolved output name, so $references keep
                    # working when name_from is in play. PS-523 decides what is bound.
                    self._bind(field_def, [field_def.get('var'), name], value)
            except RaggedTailError as e:
                result.errors.append(str(e))                    # PS-343, bare
                break
            except Exception as e:
                result.errors.append(error_text(f"Error decoding {name}: ", e))
                break
        
        return pos

    def _resolve_metadata_ref(self, ref: str, input_meta: Dict[str, Any]) -> Any:
        """Resolve a $ metadata reference against TS013 input."""
        if not isinstance(ref, str) or not ref.startswith('$'):
            return None
        path = ref[1:]  # Remove $
        import re as _re
        path = _re.sub(r'\[(\d+)\]', r'.\1', path)
        parts = path.split('.')
        current = input_meta
        for part in parts:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, (list, tuple)):
                try:
                    current = current[int(part)]
                except (IndexError, ValueError):
                    return None
            else:
                return None
        return current
    
    def _enrich_metadata(self, data: Dict[str, Any], metadata_def: Dict[str, Any],
                         input_meta: Dict[str, Any],
                         warnings: Optional[List[str]] = None) -> None:
        """Add runtime-context values to a decoded output. PS-309 to PS-320.

        Enrichment runs after every field has been read and contributes no bytes, which
        is what makes an implementation free to decline it entirely (PS-310, PS-311).

        Two rules here were defects until CR-2026-036, and both were silent:

        - **A decoded field wins a name collision** (PS-313). An `include` entry named
          for a field already decoded from the payload used to replace it, so a `u16`
          decoding to 60 came back as an ISO timestamp string.
        - **An unresolved value omits its key** (PS-314). `mode: rx_time` with no
          `recvTime` in the input used to write `None`, which no consumer can tell from
          a device that reported nothing. Four swallowed exceptions did the same for a
          malformed input, and reported nothing either way.
        """
        from datetime import datetime, timedelta, timezone

        warn = warnings if warnings is not None else []
        # Every key present now came from the payload. PS-313 gives those precedence.
        decoded = set(data)

        def place(name: str, value: Any, what: str) -> None:
            """Assign an enriched value, or say why it was not assigned."""
            if name in decoded:
                warn.append(f"metadata: '{name}' not added, a decoded field has that name")
                return
            if value is None:
                warn.append(f"metadata: '{name}' omitted, {what} could not be resolved")
                return
            data[name] = value

        def as_utc(iso: str) -> Optional[Any]:
            try:
                return datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
            except Exception:
                return None

        def stamp(dt) -> str:
            return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}Z'

        for mapping in metadata_def.get('include', []):
            name, source = mapping.get('name'), mapping.get('source')
            if name and source:
                place(name, self._resolve_metadata_ref(source, input_meta), source)

        for ts in metadata_def.get('timestamps', []):
            name = ts.get('name', 'timestamp')
            mode = ts.get('mode')

            if mode == 'rx_time' or ts.get('source') == '$recvTime':
                place(name, input_meta.get('recvTime'), 'the receive time')

            elif mode == 'subtract' or mode == 'elapsed_to_absolute':
                # PS-318: `offset_field` is the older spelling of `elapsed_field`.
                field = (ts.get('offset_field') if mode == 'subtract'
                         else (ts.get('elapsed_field') or ts.get('offset_field')))
                # PS-319: the base defaults to the receive time.
                base = ts.get('time_base', 'rx_time')
                recv = input_meta.get('recvTime') if base == 'rx_time' else None
                if not field:
                    warn.append(f"metadata: '{name}' omitted, mode '{mode}' names no field")
                    continue
                if field not in data:
                    place(name, None, f"field '{field}'")
                    continue
                rx_dt = as_utc(recv) if recv else None
                if rx_dt is None:
                    place(name, None, f"the {base} base")
                    continue
                try:
                    place(name, stamp(rx_dt - timedelta(seconds=data[field])), field)
                except Exception as exc:
                    warn.append(f"metadata: '{name}' omitted, {field} is not a number "
                                f"of seconds ({exc})")

            elif mode in EPOCH_MODES:
                field = ts.get('field')
                if not field:
                    warn.append(f"metadata: '{name}' omitted, mode '{mode}' names no field")
                    continue
                # PS-435: an internal field is not reported, and still resolves here.
                if field in data:
                    count = data[field]
                elif field.startswith('_') and field in self._variables:
                    count = self._variables[field]
                else:
                    place(name, None, f"field '{field}'")
                    continue
                if isinstance(count, bool) or not isinstance(count, (int, float)):
                    warn.append(f"metadata: '{name}' omitted, {field} is not a number "
                                f"of seconds")
                    continue
                # PS-354: seconds since the entry's epoch, 1970 where none is declared.
                # PS-356: the time is the epoch plus that count, in UTC (PS-320).
                unix = parse_epoch(ts.get('epoch', '1970-01-01T00:00:00Z')) + count
                if isinstance(unix, float) and unix.is_integer():
                    unix = int(unix)
                try:
                    dt = (datetime(1970, 1, 1, tzinfo=timezone.utc)
                          + timedelta(seconds=unix))
                except OverflowError as exc:
                    warn.append(f"metadata: '{name}' omitted, {field} is out of range ({exc})")
                    continue
                if mode == 'unix_epoch':
                    place(name, unix, field)
                elif mode == 'iso8601':
                    text = dt.strftime('%Y-%m-%dT%H:%M:%S')
                    if dt.microsecond:
                        text += ('.%06d' % dt.microsecond).rstrip('0')
                    place(name, text + 'Z', field)
                else:
                    # PS-410 to PS-413: the instant's UTC parts. A datetime never holds
                    # second 60, which is PS-413's POSIX reading.
                    parts = {'year': dt.year, 'month': dt.month, 'day': dt.day,
                             'hour': dt.hour, 'minute': dt.minute, 'second': dt.second}
                    labels = ts.get('month_labels')
                    if labels:
                        parts['month'] = labels[dt.month - 1]
                    keys = ts.get('keys') or {}
                    place(name, {keys.get(p, p): parts[p] for p in CALENDAR_PARTS}, field)

            elif mode is not None:
                warn.append(f"metadata: '{name}' omitted, unknown mode '{mode}'")

    def encode(self, data: Dict[str, Any], fPort: int = None,
               direction: str = None,
               decode_record: Optional[Dict[str, Any]] = None) -> EncodeResult:
        """
        Encode data dict to payload bytes using schema.
        
        Args:
            data: Dictionary of field values
            fPort: Optional LoRaWAN fPort for port-based schema selection
            direction: Direction the message will travel, 'uplink' or 'downlink'.
                Omit where the caller cannot know it; the check is then skipped and
                PS-292 is not satisfied.
            decode_record: The `_decode` that `interpret(..., decode_record=True)`
                produced (PS-572), or None. It may instead arrive inside `data` as
                `_decode`; the argument wins. The encoder follows it (PS-573): each
                recorded case and discriminator, internal field and flags value is
                used where the input gives none, and input contradicting it is
                rejected. Where neither the input nor the record gives a match's case,
                this encoder derives it (PS-551's nested-match rule, PS-563, PS-564),
                as PS-574 permits and requires it to say.
            
        Returns:
            EncodeResult with encoded payload
        """
        result = EncodeResult(payload=b'')
        # PS-573: the record, given beside the input or inside it as `_decode`; `_meta`
        # describes a decode and is not input either.
        if isinstance(data, dict) and ('_decode' in data or '_meta' in data):
            if decode_record is None:
                decode_record = data.get('_decode')
            data = {k: v for k, v in data.items() if k not in ('_decode', '_meta')}
        self._trace = (DecodeTrace(self._pointers, decode_record)
                       if isinstance(decode_record, dict) else None)
        self._record_supplied = set()   # names whose value the record, not the input, gave
        self._encode_input_names = set(data) if isinstance(data, dict) else set()
        try:
            return self._encode(data, fPort, direction, result)
        finally:
            self._trace = None

    def _encode(self, data: Dict[str, Any], fPort: Optional[int], direction: Optional[str],
                result: EncodeResult) -> EncodeResult:
        """The body of `encode`, with the decode record (if any) in `self._trace`."""
        self._encode_warnings = []
        self._ps564 = []            # PS-564: each `remaining` match's chosen case
        self._ps564_depth = 0       # inside a tlv or repeat, where it is not checked
        if self._load_errors:
            result.errors.extend(self._load_errors)
            return result

        # PS-581: which port entry this encode uses, and the FPort it reports.
        try:
            fPort = self._choose_encode_port(fPort, direction)
        except ValueError as e:
            result.errors.append(str(e))
            return result
        if fPort is None:
            doc = self.schema.get('fPort', self.schema.get('fport'))
            result.fport = doc if isinstance(doc, int) and not isinstance(doc, bool) else None
        else:
            result.fport = fPort

        # PS-292, the mirror of the decode check: encoding for an entry that disclaims
        # this direction produces bytes the far end will read against different field
        # definitions. Emitting them would put a malformed frame on the air, so nothing
        # is encoded.
        direction_error = self._direction_error(fPort, direction)
        if direction_error:
            result.errors.append(direction_error)
            return result

        output = FrameBuffer()          # CR-2026-136: laid out as the decoder reads it

        fields = self._resolve_fields(fPort)
        # The fields this message is encoded from: a reference is looked for among them
        # before the rest of the schema, since each port may declare its own `code`.
        self._encode_scope = fields
        self._enclosing = [[fields, None]]      # CR-2026-165: the lists enclosing the entry
        
        # PS-551: a value the input omits but the rest of it fixes - a repeat's count or
        # byte_length field, a match's discriminator, a flagged mask - is derived before
        # anything is written, and a supplied one that disagrees is rejected. The flagged
        # mask was the one derivation already made, and it overwrote a supplied mask.
        try:
            data = self._derive_inputs(fields, data)
        except Exception as e:
            result.errors.append(str(e))
            return result

        omitted_optional = None     # the first optional field the input left out (PS-405)

        tails = TailLevel(self, output)                  # PS-564
        for _kind, _item in self._bitfield_runs(fields):
            tails.start()
            # A run of bit ranges shares one span of bytes, so it is packed once rather
            # than a byte per field (CR-2026-023). The LoRaWAN MHDR's three ranges used to
            # come back as three bytes of unshifted values: `40` encoded as `020000`.
            if _kind == 'bits':
                try:
                    self._encode_bitfield_run(_item, data, output)
                except Exception as e:
                    names = ", ".join(str(f.get('name')) for f in _item)
                    result.errors.append(error_text(f"Error encoding bit range(s) {names}: ", e))
                continue
            field_def = _item
            self._enclosing[-1][1] = field_def
            # PS-405, before any construct: an optional object or repeat the input omits
            # writes nothing, as a plain field does. The object branch ran first and wrote
            # its members as zeros.
            if field_def.get('optional') is True and field_def.get('name'):
                if data.get(field_def['name']) is None:
                    omitted_optional = omitted_optional or field_def['name']
                    continue
                if omitted_optional:
                    result.errors.append(
                        f"Error encoding {field_def['name']}: optional field supplied while "
                        f"the earlier optional field {omitted_optional!r} is not (PS-405)")
                    continue
            if '$ref' in field_def:
                # Decoding splices the referenced definition's fields in place; encoding
                # never did, so the whole header collapsed to one zero byte -
                # ref-header.yaml re-encoded 01020304 as 000304.
                try:
                    ref_def = self._resolve_ref(field_def['$ref'])
                    output.extend(
                        self._encode_field_list(ref_def.get('fields') or [], data))
                except Exception as e:
                    result.errors.append(error_text(f"Error encoding $ref: ", e))
                continue
            
            if 'byte_group' in field_def:
                try:
                    output.extend(self._encode_byte_group(field_def, data))
                except Exception as e:
                    result.errors.append(error_text(f"Error encoding byte_group: ", e))
                continue
            
            if 'tlv' in field_def and not field_def.get('type'):
                try:
                    output.extend(self._encode_tlv(field_def, data))
                except Exception as e:
                    result.errors.append(error_text(f"Error encoding tlv: ", e))
                continue
            
            if 'match' in field_def and not field_def.get('type'):
                try:
                    output.extend(self._encode_match(field_def, data, output.pending, fields))
                except Exception as e:
                    result.errors.append(error_text(f"Error encoding match: ", e))
                continue
            
            if field_def.get('type') == 'repeat':
                try:
                    output.extend(self._encode_repeat(field_def, data, fields))
                except Exception as e:
                    result.errors.append(
                        f"Error encoding repeat {field_def.get('name')!r}: {e}")
                continue

            if field_def.get('type') == 'object':
                # Encoding had no top-level object case: "Cannot encode type: object".
                # A `name_from` object was reported under its templated key, so that is
                # the key to read it back from (CR-2026-030), as for any other field.
                try:
                    key = self._resolve_encode_name(
                        field_def, field_def.get('name'), data)
                except ValueError as e:
                    result.errors.append(
                        f"Error encoding object {field_def.get('name')!r}: {e}")
                    continue
                nested = data.get(key) if key is not None else None
                if not isinstance(nested, dict):
                    # PS-551: a whole object the input omits. Its members were written as
                    # zeros, each warned about (or, in Go, nothing written at all).
                    result.errors.append(missing_field_message(
                        key if key is not None else field_def.get('name')))
                    continue
                try:
                    output.extend(self._encode_object_members(
                        field_def.get('fields') or [], nested))
                except Exception as e:
                    result.errors.append(
                        f"Error encoding object {field_def.get('name')!r}: {e}")
                continue
            
            if 'flagged' in field_def:
                try:
                    output.extend(self._encode_flagged(field_def['flagged'], data))
                except Exception as e:
                    # The per-field path below reports its errors; this one used to
                    # propagate, so one unencodable group killed the whole call.
                    result.errors.append(error_text(f"Error encoding flagged group: ", e))
                continue
            
            name = field_def.get('name') or ''   # PS-553: only a skip is nameless
            field_type = field_def.get('type', 'u8')
            
            # A derived value is computed from other fields and occupies no bytes of its
            # own. This required the deprecated `formula` spelling, so a field using
            # `ref`, `compute`, `polynomial` or `guard` fell through to "Cannot encode
            # type: number" - every schema with a computed field failed to encode.
            if field_type in COMPUTED_TYPES:
                continue
            
            # Skip type: emit zero bytes, no input needed
            if field_type == 'skip':
                length = field_def.get('length', 1)
                # `remaining` gives no count to pad on encode (PS-014).
                length = 0 if isinstance(length, str) else max(0, int(length))
                output.extend(padding(length))         # CR-2026-141: no value
                continue

            # PS-589 (CR-2026-175): a byte string that does not consume re-reads bytes the
            # other fields write, so it writes none and is not required; a value given for
            # it is checked where the frame closes.
            if is_echo(field_def):
                try:
                    output.echo(field_def, data)
                except ValueError as e:
                    result.errors.append(error_text(f"Error encoding {name}: ", e))
                continue
            
            # PS-359: a literal came from no bytes and writes none, and its key is not
            # required of the input. This warned "Missing field" for it.
            if is_literal(field_def):
                continue
            # PS-360: `value` on a field that reads bytes is the constant to write,
            # whatever the input supplies. The input's value was written instead.
            if 'value' in field_def:
                try:
                    output.extend(self._encode_field(field_def, field_def['value']))
                except Exception as e:
                    result.errors.append(error_text(f"Error encoding {name}: ", e))
                continue

            # PS-434: an internal field that reads bytes writes its value, else the
            # input's, else it is an error naming the field. A nameless field other than
            # a skip is refused at load (PS-553); it used to be written as zero padding.
            if name.startswith('_'):
                try:
                    value = self._internal_encode_value(field_def, data)
                except ValueError as e:
                    result.errors.append(error_text(f"Error encoding {name}: ", e))
                    continue
            else:
                # `name_from` reports the value under a templated key, so that is the key
                # to read it back from (CR-2026-030).
                try:
                    lookup_name = self._resolve_encode_name(field_def, name, data)
                except ValueError as e:
                    result.errors.append(error_text(f"Error encoding {name}: ", e))
                    continue
                if lookup_name is None:
                    result.errors.append(
                        f"Error encoding {name}: name_from "
                        f"{field_def.get('name_from')!r} references a field the data does "
                        "not carry, so its output key cannot be rebuilt")
                    continue
                value = data.get(lookup_name)
                if value is None and field_def.get('optional') is True:
                    # PS-405: an optional field is written only where the input has it.
                    omitted_optional = omitted_optional or name
                    continue
                if value is not None and field_def.get('optional') is True \
                        and omitted_optional:
                    result.errors.append(
                        f"Error encoding {name}: optional field supplied while the earlier "
                        f"optional field {omitted_optional!r} is not (PS-405)")
                    continue
                if value is None:
                    # PS-554: a declared default is a defined absence, encoded as input.
                    has_default, default = field_default(field_def)
                    if has_default:
                        value = default
                if value is None and field_def.get('sentinel'):
                    # A reading the decode omitted as "no reading" (PS-427) goes back as
                    # the sentinel it was, not as zero.
                    try:
                        output.extend(self._encode_field(raw_bits_field(field_def),
                                                         field_def['sentinel'][0]))
                    except Exception as e:
                        result.errors.append(error_text(f"Error encoding {name}: ", e))
                    continue
                if value is None and self._derive_bool(field_def, output):
                    continue                        # PS-551 (CR-2026-141): derived
                if value is None:
                    # PS-551: rejected, naming the field. This warned "Missing field" and
                    # wrote a zero, which reads back as a real zero.
                    result.errors.append(missing_field_message(lookup_name))
                    continue
            
            try:
                if field_type == 'bitfield_string':
                    # Inside the try: a segment that is not a number raised out of
                    # encode() itself.
                    output.extend(self._encode_bitfield_string(field_def, str(value)))
                    continue
                # Reverse modifiers
                value = self._reverse_modifiers(value, field_def)
                if field_type == 'bool':
                    self._place_bool(field_def, value, output)        # CR-2026-136
                    continue

                encoded = self._encode_field(field_def, value)
                output.extend(encoded)
            except Exception as e:
                result.errors.append(error_text(f"Error encoding {name}: ", e))
        
        # A rejected input produces no payload: the bytes written before the error are a
        # frame short of the rest, as Go has always returned nothing.
        tails.close()
        frame = output.frame()
        if not result.errors:
            try:
                check_peeks(frame)                          # PS-551 (CR-2026-141)
            except ValueError as e:
                result.errors.append(str(e))
        if not result.errors:
            # PS-564: the bytes from each `remaining` match to the end select its case.
            for check in self._ps564:
                selected = remaining_selection(check['match'], check['tail'],
                                               self._match_case_pattern)
                if selected != check['chosen']:
                    result.errors.append(remaining_mismatch_message(
                        check['match'], check['tail'], selected, check['chosen']))
                    break
        if not result.errors and not frame:
            # PS-442: the last line against a construct that wrote nothing and said so
            # to no one - a match with no case chosen, a tlv entry no case claimed.
            empty = empty_payload_error(fields, data)
            if empty:
                result.errors.append(empty)
        result.payload = b'' if result.errors else bytes(frame)
        result.warnings.extend(self._encode_warnings)
        return result
    
    @staticmethod
    def _size_source(fields: List[Dict[str, Any]], ref: str) -> Optional[Dict[str, Any]]:
        """The field of this list a `$ref` names, by its name or its `var:`.

        Searched into a byte_group's members, since a count is often a few bits of a
        shared byte. Not into any other construct: a name bound inside a match branch or
        a repeat element is not one this list's values can fix.
        """
        for f in fields:
            if not isinstance(f, dict):
                continue
            group = f.get('byte_group')
            if group is not None:
                members = group.get('fields') if isinstance(group, dict) else group
                found = SchemaInterpreter._size_source(members or [], ref)
                if found is not None:
                    return found
                continue
            if f.get('name') and (f.get('name') == ref or f.get('var') == ref):
                return f
        return None

    @staticmethod
    def _derivable(source: Optional[Dict[str, Any]]) -> bool:
        """Whether a field's value can be derived and written as it stands (PS-551).

        It reads bytes of its own and reports them unchanged: no `value` (PS-360 writes
        that), no computed type, no `name_from`, and no arithmetic or lookup between the
        bytes and the value a reference binds.
        """
        if not isinstance(source, dict) or not source.get('name'):
            return False
        if source.get('type') in COMPUTED_TYPES or is_literal(source):
            return False
        return not any(key in source for key in (
            'value', 'name_from', 'lookup', 'mult', 'div', 'add', 'transform',
            'polynomial', 'encoding', 'compute', 'ref', 'guard'))

    def _is_peek(self, fields: List[Dict[str, Any]], source: Dict[str, Any]) -> bool:
        """Whether `source` is a bit range in a run nothing closes with `consume`.

        Such a run reads bytes the fields after it read again (r718x, vobo, dnt, the
        match-u32-peek fixture), and the encoder writes those bytes twice - once for
        the run and once for what follows - so a value derived into it would add
        bytes rather than fix them. Not derived; it stays an error naming the field.
        """
        for kind, item in self._bitfield_runs(fields):
            if kind == 'bits' and any(f is source for f in item):
                return int(item[-1].get('consume', 0) or 0) < 1
        return False

    def _derived_source(self, fields: List[Dict[str, Any]],
                        ref: Any) -> Optional[Dict[str, Any]]:
        """The field a `$ref` names, where a value can be derived into it (PS-551)."""
        if not (isinstance(ref, str) and ref.startswith('$')):
            return None
        source = self._size_source(fields, ref[1:])
        if not self._derivable(source) or self._is_peek(fields, source):
            return None
        return source

    def _repeat_records(self, field_def: Dict[str, Any], data: Dict[str, Any]):
        """A repeat's list as the input gives it, or None where it gives none."""
        records = data.get(field_def.get('name', ''))
        if isinstance(records, dict):
            return [records]
        if isinstance(records, (list, tuple)):
            return list(records)
        return None

    def _exact_case_value(self, case_key: Any) -> Optional[int]:
        """The one discriminator a match case key stands for, or None (a list, a range,
        `default`, a pattern)."""
        if isinstance(case_key, bool):
            return None
        if isinstance(case_key, int):
            return case_key
        if isinstance(case_key, str):
            try:
                return int(case_key.strip(), 0)
            except ValueError:
                return None
        return None

    def _derive_byte_count(self, fields: List[Dict[str, Any]], field_def: Dict[str, Any],
                           ref: str, data: Dict[str, Any],
                           derived: Dict[str, Any]) -> None:
        """PS-602: a byte string's value fixes the field its `length` names, as a
        repeat's list fixes its count (PS-551). Derived where the input omits it; a given
        value that differs is rejected. Neither was checked: the value's own length was
        written whatever the field said."""
        count = len(byte_string_input(field_def, data[field_def['name']]))
        source = self._size_source(fields, ref)
        name = source['name'] if source is not None else ref
        if derived.get(name) is None:
            if self._derivable(source) and not self._is_peek(fields, source):
                derived[name] = count
            return
        given = self._encode_reference(name, derived)
        if isinstance(given, (int, float)) and not isinstance(given, bool) \
                and given != count:
            raise FieldError(f"Field '{field_def['name']}' is given {count} bytes, but "
                             f"'{name}', its length, is {message_value(given)} (PS-602)")

    def _derive_inputs(self, fields: List[Dict[str, Any]],
                       data: Dict[str, Any]) -> Dict[str, Any]:
        """PS-551: the values this field list's input omits but the rest of it fixes.

        - a repeat's `count: $n` or `byte_length: $n` field, from the list given;
        - a match's `field: $d` discriminator, from the one case whose fields are given;
        - a flagged construct's mask field, from the groups given;
        - the field a `bytes`, `hex` or `base64` field's `length` names, from the value's
          byte count (PS-602, CR-2026-181).

        A supplied value is kept: the construct checks that it agrees with what the rest
        of the input says, and rejects the input where it does not. The input itself is
        not modified.
        """
        derived = dict(data)
        if self._trace is not None:
            derived = self._recorded_inputs(fields, data, derived)          # PS-573
        for f in fields:
            if not isinstance(f, dict):
                continue
            ref = byte_string_named_length(f)
            if ref is not None and data.get(f.get('name')) is not None:
                self._derive_byte_count(fields, f, ref, data, derived)
                continue
            if f.get('type') == 'repeat':
                records = self._repeat_records(f, data)
                if records is None:
                    continue
                for key in ('count', 'byte_length'):
                    source = self._derived_source(fields, f.get(key))
                    if source is None or derived.get(source['name']) is not None:
                        continue
                    if key == 'count':
                        derived[source['name']] = len(records)
                    else:
                        derived[source['name']] = len(
                            self._encode_repeat_elements(f, data, records))
            elif isinstance(f.get('match'), dict) and not f.get('type'):
                source = self._derived_source(fields, f['match'].get('field'))
                if source is None or derived.get(source['name']) is not None:
                    continue
                key = self._sole_case_given(f['match'], data)
                if key is not None:
                    derived[source['name']] = key
            elif isinstance(f.get('flagged'), dict):
                mask = str(f['flagged'].get('field', '')).lstrip('$')
                source = self._size_source(fields, mask)
                name = source['name'] if source is not None else mask
                bits = 0
                for group in f['flagged'].get('groups') or []:
                    if self._flagged_group_given(group, data):
                        bits |= 1 << int(group.get('bit', 0))
                given = data.get(name)
                if given is None and self._recorded_flags(f['flagged']) is not None:
                    # PS-573: the flags the decode record holds; the groups written are
                    # the ones they set (_encode_flagged), so there is nothing to check.
                    derived.setdefault(name, self._recorded_flags(f['flagged']))
                    continue
                if given is None and name in derived:
                    # An internal mask the decode record supplied (PS-573), checked as a
                    # supplied one is.
                    given = derived[name]
                    data = dict(data, **{name: given})
                if given is None:
                    derived[name] = bits
                    continue
                declared = 0
                for group in f['flagged'].get('groups') or []:
                    declared |= 1 << int(group.get('bit', 0))
                # The groups test what a reference to the mask binds (PS-523, CR-2026-132):
                # an enum's code, the number under a text label. Compared as reported, an
                # enum name or a text label passed unchecked and wrote a mask disagreeing
                # with the groups after it.
                given = self._encode_reference(name, data)
                if isinstance(given, int) and not isinstance(given, bool) \
                        and (given & declared) != bits:
                    raise ValueError(
                        f"Flagged field '{name}' is given {given}, and the groups given "
                        f"set {bits} (PS-551)")
        return derived

    def _recorded_inputs(self, fields: List[Dict[str, Any]], data: Dict[str, Any],
                         derived: Dict[str, Any]) -> Dict[str, Any]:
        """PS-573: what the decode record supplies for this field list.

        - each internal field's recorded value, where the input does not give it; an input
          giving another value is rejected, naming the field (the prototype's text);
        - each flagged construct's recorded flags, under its flags field, where the input
          gives that field no value.

        A byte_group's internal members are looked into, as the record locates them.
        """
        trace = self._trace

        def internal(f):
            name = f.get('name')
            if not isinstance(name, str) or not name.startswith('_') \
                    or f.get('type') in COMPUTED_TYPES or is_literal(f) or 'value' in f:
                return
            recorded = trace.lookup('internal', f)
            if recorded is _UNRECORDED:
                return
            if name in data and name not in self._record_supplied:
                if data[name] != recorded:
                    raise ValueError(f"Field '{name}' is given {message_value(data[name])}, but "
                                     f"the decode record holds {message_value(recorded)} "
                                     f"(PS-573)")
                return
            derived[name] = recorded
            self._record_supplied.add(name)

        for f in fields:
            if not isinstance(f, dict):
                continue
            group = f.get('byte_group')
            if group is not None:
                members = group.get('fields') if isinstance(group, dict) else group
                for member in members or []:
                    if isinstance(member, dict):
                        internal(member)
                continue
            if isinstance(f.get('flagged'), dict) and not f.get('type'):
                mask = str(f['flagged'].get('field', '')).lstrip('$')
                recorded = trace.lookup('flagged', f['flagged'])
                if mask and recorded is not _UNRECORDED and data.get(mask) is None \
                        and derived.get(mask) is None:
                    derived[mask] = recorded
                continue
            internal(f)
        return derived

    @staticmethod
    def _default_body(match_def: Dict[str, Any]) -> Optional[List[Any]]:
        """A match's default field list - `default:` beside its cases, or a `default`
        case - or None where its default is `error` or `skip`."""
        default = match_def.get('default')
        if isinstance(default, list):
            return default
        default = (match_def.get('cases') or {}).get('default')
        return default if isinstance(default, list) else None

    def _best_case(self, match_def: Dict[str, Any], data: Dict[str, Any]):
        """The case whose fields the input gives most of, scored by _body_score, as
        ``(key, fields, tied)``, or ``(None, None, False)``. The first of equal cases
        wins, and ``tied`` says another equalled it. Where the match's default list
        explains more of the input than any case, no case is returned, and the default
        is what a decoder takes for every value no case lists (dc600's report, whose
        `monitorStatus` a case also declares)."""
        best, best_fields, best_hits, tied = None, None, 0, False
        for case_key, case_fields in (match_def.get('cases') or {}).items():
            if case_key == 'default' or not isinstance(case_fields, list):
                continue
            hits = self._body_score(case_fields, data) or 0
            if hits > best_hits:
                best, best_fields, best_hits, tied = case_key, case_fields, hits, False
            elif hits and hits == best_hits:
                tied = True
        default = self._default_body(match_def)
        if best is not None and default is not None \
                and (self._body_score(default, data) or 0) > best_hits:
            return None, None, False
        return best, best_fields, tied

    def _sole_case_given(self, match_def: Dict[str, Any],
                         data: Dict[str, Any]) -> Optional[int]:
        """The discriminator of the one match case whose fields the input gives.

        None where no case's fields are given, where two cases explain the input equally
        well, where the default explains it as well, or where the case stands for more
        than one value. A case is scored by _body_score, so the fields of a match nested
        in it count, and a literal the input contradicts rules it out.
        """
        best, _, tied = self._best_case(match_def, data)
        if best is None or tied:
            return None
        return self._exact_case_value(best)

    def _body_score(self, body: Any, data: Dict[str, Any]) -> Optional[int]:
        """How many of a match branch's reported names the input gives, or None where the
        input rules the branch out (no-silent-empty).

        Counted through every construct a decode of the branch reports from: a byte_group's
        or flagged group's members, a nested match's best branch and a tlv's cases. A literal the input gives
        with another value rules the branch out (PS-359, PS-513), as does a nested match
        every branch of which is ruled out and whose default is not `skip`. A computed
        field is not counted: its name says only that the branch reports it, and a
        literal of that name in another branch says which value it came from. Only the top
        level of a branch used to be counted, so a branch whose fields sit in another
        construct - imbuildings' and dingtek's `match: {remaining: true}` cases, a header
        and then a match on its type byte - scored nothing, no case was chosen, and the
        match wrote no bytes and reported nothing.
        """
        if not isinstance(body, list):
            return None
        score = 0
        for f in body:
            if not isinstance(f, dict):
                continue
            if 'byte_group' in f and not f.get('type'):
                group = f['byte_group']
                members = group.get('fields') if isinstance(group, dict) else group
                hits = self._body_score(members or [], data)
                if hits is None:
                    return None
                score += hits
                continue
            if 'flagged' in f and not f.get('type'):
                for group in f['flagged'].get('groups') or []:
                    score += self._body_score(group.get('fields') or [], data) or 0
                continue
            if isinstance(f.get('match'), dict) and not f.get('type'):
                inner = f['match']
                if inner.get('name') and inner['name'] in data:
                    score += 1
                inner_cases = inner.get('cases') or {}
                bodies = [b for k, b in inner_cases.items()
                          if k != 'default' and isinstance(b, list)]
                default = inner.get('default', 'error')
                if isinstance(default, list):
                    bodies.append(default)
                elif isinstance(inner_cases.get('default'), list):
                    bodies.append(inner_cases['default'])
                scores = [h for h in (self._body_score(b, data) for b in bodies)
                          if h is not None]
                if scores:
                    score += max(scores)
                elif default != 'skip':
                    return None
                continue
            if isinstance(f.get('tlv'), dict) and not f.get('type'):
                names = set()
                for case_fields in (f['tlv'].get('cases') or {}).values():
                    if isinstance(case_fields, list):
                        names.update(c['name'] for c, _ in self._case_claims(case_fields))
                score += sum(1 for n in names if n in data)
                continue
            name = f.get('name')
            if not name or str(name).startswith('_') \
                    or (f.get('type') in COMPUTED_TYPES and not is_literal(f)):
                continue
            key = name
            if f.get('name_from'):
                try:
                    key = self._resolve_encode_name(f, name, data)
                except ValueError:
                    key = None
            if key is None or key not in data:
                continue
            if is_literal(f) and not same_label(f['value'], data[key]):
                return None
            score += 1
        return score

    def _flagged_group_given(self, group: Dict[str, Any], data: Dict[str, Any]) -> bool:
        """Whether the input gives any of a flagged group's own values."""
        fields = group.get('fields') or []
        if any(isinstance(gf, dict) and gf.get('name') and gf['name'] in data
               for gf in fields):
            return True
        return any(f.get('name') in data
                   for f in self._claimable_fields(fields))

    def _recorded_flags(self, flagged_def: Dict[str, Any]) -> Optional[int]:
        """The flags the decode record holds for this flagged construct (PS-573), where
        the input gives its flags field no value; else None."""
        if self._trace is None:
            return None
        mask = str(flagged_def.get('field', '')).lstrip('$')
        if mask in self._encode_input_names:
            return None
        recorded = self._trace.lookup('flagged', flagged_def)
        if recorded is _UNRECORDED or isinstance(recorded, bool) \
                or not isinstance(recorded, (int, float)):
            return None
        return int(recorded)

    def _encode_flagged(self, flagged_def: Dict[str, Any], data: Dict[str, Any]) -> bytes:
        """Encode flagged groups: only encode groups where data is present - or, with a
        decode record and no flags in the input, the groups its recorded flags set."""
        groups = flagged_def.get('groups', [])
        recorded = self._recorded_flags(flagged_def)
        if recorded is not None:
            groups = [g for g in groups if (recorded >> int(g.get('bit', 0))) & 1]
        # CR-2026-136: the given groups' members are one field list, laid out as a
        # decoder reads them, so a bool or bit range in a group is its bits of a shared
        # byte (vobo's digital groups) and a construct in one is encoded as any is (dnt's
        # object). Each member used to be written whole through _encode_field: a bool as
        # a byte, an object as "Cannot encode type: object". A nameless member is taken
        # only where it is a construct in its group (a nested flagged block, a byte_group).
        members = []
        for group in groups:
            group_fields = group.get('fields', [])
            if recorded is None and not self._flagged_group_given(group, data):
                continue
            if any(isinstance(gf, dict) and not gf.get('name') for gf in group_fields):
                # A construct in the group - a nested flagged block, a byte_group - is
                # encoded as any field list is. It was passed over as nameless, so a
                # nested group's values were dropped without a word.
                members.extend(gf for gf in group_fields if isinstance(gf, dict))
                continue
            members.extend(gf for gf in group_fields
                           if isinstance(gf, dict) and gf.get('name'))
        # PS-551 holds here as in any field list: a given group's members are each
        # required, and a missing one is rejected naming it.
        return self._encode_field_list(members, data)

    
    def _encode_bitfield_string(self, field_def: Dict[str, Any], value: str) -> bytes:
        """Encode bitfield_string: parse string back into packed integer bytes."""
        parts = field_def.get('parts', [])
        delimiter = field_def.get('delimiter', '.')
        prefix = field_def.get('prefix', '')
        length = field_def.get('length', 2)
        
        if prefix and value.startswith(prefix):
            value = value[len(prefix):]
        
        segments = value.split(delimiter)
        int_val = 0
        
        for i, part in enumerate(parts):
            if len(part) < 2:
                continue
            bit_off = int(part[0])
            bit_len = int(part[1])
            fmt = part[2] if len(part) > 2 else 'decimal'
            seg = segments[i] if i < len(segments) else '0'
            # PS-096, PS-431, PS-583: parsed by the part's format, a value the width cannot
            # hold rejected rather than masked.
            int_val |= parse_bitfield_segment(seg, bit_len, fmt, field_def.get('name')) << bit_off
        
        # PS-050a: in the field's own byte order, as it is decoded.
        return int_val.to_bytes(length, self._bitfield_string_order(field_def), signed=False)
    
    def _encode_byte_group(self, field_def: Dict[str, Any], data: Dict[str, Any]) -> bytes:
        """Pack a ``byte_group``'s bit ranges back into their shared byte(s).

        Encoding had no byte_group case at all: the construct fell through to the plain
        field path, which found no ``name``, encoded a default of 0 and emitted one zero
        byte. rbs30x's first byte is a group of ``u8[4:7]`` and ``u8[0:3]``, so every one
        of its payloads came back with 0x00 where the version and counter belong - the
        right length, the wrong bits, and no error to say so.
        """
        byte_group = field_def.get('byte_group', [])
        if isinstance(byte_group, dict):
            group_fields = byte_group.get('fields', []) or []
        else:
            group_fields = byte_group or []
        # PS-070 (CR-2026-131): the same width and member checks the decoder applies.
        size = byte_group_width(field_def)

        packed = 0
        for gf in group_fields:
            if not isinstance(gf, dict):
                continue
            name = gf.get('name', '')
            gtype = str(gf.get('type', ''))
            if gtype in COMPUTED_TYPES or is_literal(gf):
                continue                # no bits of its own (PS-359)
            if not name:
                value = gf.get('default', 0)
            elif 'value' in gf or str(name).startswith('_'):
                value = self._internal_encode_value(gf, data)          # PS-360, PS-434
            elif data.get(name) is None and field_default(gf)[0]:
                value = field_default(gf)[1]                           # PS-554
            elif data.get(name) is None:
                # PS-551. A member was written as zero bits, which read back as a value.
                raise ValueError(missing_field_message(name))
            else:
                value = data[name]
            if gtype == 'bool':
                # PS-364: a bool member is its `bit` of the group's value. It was taken as a
                # full-width member, so True set bit 0 whatever `bit` it declared.
                if value:
                    packed |= 1 << int(gf.get('bit', 0))
                continue
            value = self._reverse_modifiers(value, gf)
            if not isinstance(value, (int, float)):
                continue
            value = round_to_integer(value)                                    # PS-552
            # A whole-integer member is its full range (PS-070): masked like any other, so
            # a negative s8 writes its own byte, not a sign extended across the group.
            _, start, end, signed = byte_group_member_range(gf)
            width = end - start + 1
            low, high = integer_bounds(signed=signed, bits=width)
            check_fits(value, low, high, name, self._given_value(gf, value),
                       gtype if '[' in gtype else None)                        # PS-168
            packed |= (value & ((1 << width) - 1)) << start
        group_endian = byte_group_endian(field_def, self.endian)
        byteorder = 'little' if group_endian == Endian.LITTLE else 'big'
        return int(packed).to_bytes(max(1, size), byteorder)

    @staticmethod
    def _is_bare_bitfield(field_def: Dict[str, Any]) -> bool:
        """Whether a plain field reads a bit range out of a byte it may share.

        The same test `_encode_field` uses to recognise one, minus the string types whose
        names carry no bracket.
        """
        if not isinstance(field_def, dict) or 'byte_group' in field_def:
            return False
        ftype = str(field_def.get('type', ''))
        if ftype == 'bitfield_string':
            return False
        return any(marker in ftype for marker in ('[', ':', '<'))

    def _bitfield_runs(self, fields: List[Dict[str, Any]]):
        """Group a field list so bitfields sharing a span are emitted together.

        Yields ``('field', field_def)`` for an ordinary field and ``('bits', [fields])``
        for a run that occupies one span of bytes.

        A run ends at the field that advances the position - `consume` of 1 or more - the
        way decoding does: every bitfield before it reads from the same offset without
        moving. Encoding had no notion of this at all. `_encode_field` wrote each bitfield
        as a whole byte holding its unshifted value, so the three bit ranges of a LoRaWAN
        MHDR came back as three bytes of raw values, `40` encoding as `020000`: the wrong
        length, the wrong bits, and no error (CR-2026-023). `byte_group` was given this
        treatment when its own encoding was fixed; a bare run never was.
        """
        run: List[Dict[str, Any]] = []
        for field_def in fields:
            if not isinstance(field_def, dict):
                continue
            if not self._is_bare_bitfield(field_def):
                if run:
                    yield 'bits', run
                    run = []
                yield 'field', field_def
                continue
            run.append(field_def)
            # `consume` of 1 or more closes the span, which is where the run ends.
            if int(field_def.get('consume', 0) or 0) >= 1:
                yield 'bits', run
                run = []
        if run:
            yield 'bits', run

    @staticmethod
    def _bitfield_label_value(label: str, field_def: Dict[str, Any]) -> Any:
        """The number a bit range's label stands for, under `enum:` or `values:`.

        Both spellings appear in the corpus - the LoRaWAN frame definitions use `enum:` on
        a `u8[5:7]` - and neither was reachable from encoding before, because a bit range
        never got as far as needing one.
        """
        for key in ('enum', 'values'):
            table = field_def.get(key)
            if isinstance(table, dict):
                for number, name in table.items():
                    if name == label:
                        return int(number)
            elif isinstance(table, list) and label in table:
                return table.index(label)
        raise ValueError(
            f"{label!r} is not a declared value of {field_def.get('name')!r}")

    def _place_bool(self, field_def: Dict[str, Any], value: Any, out: 'FrameBuffer') -> None:
        """A bool is its `bit` of the byte it peeks, advancing only where it declares
        `consume` (PS-389, PS-066). It was written as a whole byte of 0 or 1."""
        out.place_bits(1, 'big', int(field_def.get('bit', 0) or 0), 1 if value else 0,
                       int(field_def.get('consume', 0) or 0), 1, field_def.get('name'))

    @staticmethod
    def _derive_bool(field_def: Dict[str, Any], out: 'FrameBuffer') -> bool:
        """PS-551 (CR-2026-141): an absent bool that does not consume is derived from the
        fields that write its bit, and writes none itself; where none does, the frame's
        check rejects it as missing."""
        if field_def.get('type') != 'bool' or int(field_def.get('consume', 0) or 0):
            return False
        out.place_bits(1, 'big', int(field_def.get('bit', 0) or 0), 0, 0, 1,
                       field_def.get('name'), derived=True)
        return True

    def _encode_bitfield_run(self, run: List[Dict[str, Any]],
                             data: Dict[str, Any], out: 'FrameBuffer') -> None:
        """Place one run of bit ranges into the byte(s) they share.

        The same shape as `_encode_byte_group`, which is the construct that already did
        this correctly - a run of bare bitfields is the same thing without the wrapper.
        Each range is ORed into the frame's pending base and advances only by its own
        `consume` (CR-2026-136, PS-016), so bytes a range peeks are shared with whatever
        field comes next, as a decoder reads them. The run used to be written whole, and
        the next field's bytes after it.
        """
        # The run shares one base, assembled in its fields' effective byte order (PS-059):
        # a field's own `endian` where one declares it, else the schema's.
        declared = next((f.get('endian') for f in run if f.get('endian')), None)
        endian = Endian(declared) if declared else self.endian
        byteorder = 'little' if endian == Endian.LITTLE else 'big'
        for field_def in run:
            name = field_def.get('name', '')
            ftype = str(field_def.get('type', ''))
            # A nameless range is refused at load (PS-553); it was written as zero bits.
            if 'value' in field_def or str(name).startswith('_'):
                value = self._internal_encode_value(field_def, data)   # PS-360, PS-434
            elif data.get(name) is None and field_default(field_def)[0]:
                value = field_default(field_def)[1]                    # PS-554
            elif data.get(name) is None:
                if not int(field_def.get('consume', 0) or 0):
                    # PS-551 (CR-2026-141): a range that does not consume is fixed by the
                    # fields that write its bits, so it is derived and writes none; the
                    # frame's check rejects it as missing where a bit is left unwritten.
                    base_size, start, width = self._parse_bitfield_type(ftype)
                    out.place_bits(max(1, base_size), byteorder, start, 0, 0, width,
                                   name, derived=True)
                    continue
                # PS-551. A range was written as zero bits, which read back as a value.
                raise ValueError(missing_field_message(name))
            else:
                value = data[name]
            value = self._reverse_modifiers(value, field_def)
            if isinstance(value, str):
                # A bit range carrying `enum:` or `values:` reports a label, so the number
                # has to be recovered before it can be shifted into place. Raised rather
                # than skipped: writing zero for a label nobody could map is the silent
                # wrong answer this CR exists to remove.
                value = self._bitfield_label_value(value, field_def)
            if not isinstance(value, (int, float)):
                raise ValueError(
                    f"cannot encode {field_def.get('name')!r} into its bit range: "
                    f"{value!r} is not a number")
            value = round_to_integer(value)                                    # PS-552
            base_size, start, width = self._parse_bitfield_type(ftype)
            low, high = integer_bounds(signed=ftype.startswith('s'), bits=width)
            # PS-064 before PS-062: a value the range cannot hold is rejected, never
            # masked into a neighbour's bits; the mask only keeps a signed value's two's
            # complement within the width.
            check_fits(value, low, high, name,
                       self._given_value(field_def, value), ftype)             # PS-168
            out.place_bits(max(1, base_size), byteorder, start,
                           value & ((1 << width) - 1),
                           int(field_def.get('consume', 0) or 0), width, name)

    def _field_declaring_var(self, var_name: str) -> Optional[Dict[str, Any]]:
        """The field that declared ``var: <var_name>``: in the fields being encoded, else
        anywhere in the schema.

        A match's discriminator is named by its variable, and the variable's name is
        often not the field's: rbs30x has ``name: event_type`` with ``var: evt`` and
        matches on ``$evt``. Decoded output is keyed by the *field* name, so encoding
        has to get from one to the other.
        """
        def visit(node):
            if isinstance(node, dict):
                if node.get('var') == var_name and node.get('name'):
                    return node
                for value in node.values():
                    found = visit(value)
                    if found is not None:
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = visit(item)
                    if found is not None:
                        return found
            return None
        found = visit(getattr(self, '_encode_scope', None))
        return found if found is not None else visit(self.schema)

    def _field_named(self, name: str) -> Optional[Dict[str, Any]]:
        """A field named `name` that carries a lookup or is an enum: in the fields being
        encoded, else anywhere in the schema."""
        def visit(node):
            if isinstance(node, dict):
                if node.get('name') == name and (node.get('lookup') is not None
                                                 or node.get('type') == 'enum'):
                    return node
                for value in node.values():
                    found = visit(value)
                    if found is not None:
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = visit(item)
                    if found is not None:
                        return found
            return None
        found = visit(getattr(self, '_encode_scope', None))
        return found if found is not None else visit(self.schema)

    def _encode_reference(self, reference: str, data: Dict[str, Any]) -> Any:
        """The value `$reference` was bound to when `data` was decoded, or None.

        PS-523: a looked-up field binds the number under a text or boolean label, and a
        numeric label as itself. The output carries the label, so a text or boolean one
        is reversed through the field's lookup; a numeric one is used as it is. The
        variable's name is often not the field's (PS-267), so a `var` is followed to
        the field that declared it.
        """
        if reference in data:
            value, source = data[reference], self._field_named(reference)
        else:
            source = self._field_declaring_var(reference)
            if not source or source.get('name') not in data:
                return None
            value = data[source['name']]
        lookup = source.get('lookup') if source else None
        if lookup is not None and isinstance(value, (str, bool)):
            return reverse_lookup(value, lookup, source.get('name'))       # PS-169
        if source and source.get('type') == 'enum' and isinstance(value, str):
            return reverse_enum(value, source)      # its code (CR-2026-132)
        return value

    def _encode_match(self, field_def: Dict[str, Any], data: Dict[str, Any],
                      peeked: Optional[bytes] = None,
                      siblings: Optional[List[Any]] = None) -> bytes:
        """Rebuild a ``match`` construct's bytes from decoded output.

        Two sources of the discriminator, and they encode differently. An inline match
        (``length: N``) read those bytes itself, so encoding writes them back. A match on
        ``field: $var`` read nothing - the variable came from a field earlier in the
        list, which the main loop encodes on its own - so writing the discriminator here
        would duplicate it.
        """
        match_def = field_def.get('match', {}) or {}
        cases = match_def.get('cases', {}) or {}
        length = match_def.get('length')
        match_name = match_def.get('name')
        field_ref = match_def.get('field')
        default = match_def.get('default', 'error')
        byteorder = 'little' if self.endian == Endian.LITTLE else 'big'

        discriminator = None
        if match_name and match_name in data:
            discriminator = data[match_name]
        elif field_ref:
            discriminator = self._encode_reference(str(field_ref).lstrip('$'), data)

        if discriminator is None and match_def.get('var') \
                and data.get(match_def['var']) is not None:
            # An inline discriminator bound to a variable the rest of the input fixes -
            # dnt's presence mask, which the flagged block after it reads.
            discriminator = data[match_def['var']]
        if discriminator is None and length is not None and peeked \
                and len(peeked) >= int(length):
            # CR-2026-136: a bit range before the match peeked these bytes and has filled
            # them, so they are the discriminator the decoder will read (r718x's command
            # and device bytes, dnt's first command).
            discriminator = int.from_bytes(bytes(peeked[:int(length)]), byteorder)

        # PS-573: where the input gives no discriminator, the decode record's is written
        # and its case taken - before any derivation, which PS-574 makes the fallback.
        given = discriminator is not None
        if field_ref and str(field_ref).lstrip('$') in self._record_supplied:
            given = False           # an internal value the record supplied, not input
        record = (self._trace.lookup('matches', match_def)
                  if self._trace is not None else _UNRECORDED)
        record = record if isinstance(record, dict) else None
        recorded_key = _UNRECORDED
        if not given and record is not None:
            if match_def.get('remaining') is True:
                recorded_key = self._recorded_case(match_def, record.get('case'))
            elif discriminator is None:
                discriminator = record.get('value')

        if length is not None:
            check_written_discriminator(discriminator, match_name)        # PS-557
            if discriminator is not None:
                # PS-168, before a case is looked for, as the spec prototype checks it: a
                # discriminator its bytes cannot hold is rejected as such.
                low, high = integer_bounds(int(length), False)
                check_fits(round_to_integer(discriminator), low, high,
                           match_name or match_def.get('var') or 'match', discriminator)

        matched_key, matched_fields = None, None
        if recorded_key is not _UNRECORDED:
            if recorded_key != 'default':
                matched_key, matched_fields = recorded_key, cases[recorded_key]
            elif self._default_body(match_def) is not None:
                matched_fields = self._default_body(match_def)
            else:
                matched_fields = []
        if discriminator is not None:
            for case_key, case_fields in cases.items():
                if case_key == 'default':
                    continue
                if self._match_case_pattern(discriminator, case_key):
                    matched_key, matched_fields = case_key, case_fields
                    break

        if record is not None and not given:
            self._check_recorded_case(match_def, matched_key, data)  # PS-573

        if matched_fields is None and discriminator is None:
            # Where the discriminator is not in the output - an inline match with no
            # `name` reports nothing of itself, and a `remaining` match has none to
            # report - the case is recovered from which of its fields are there; the
            # first of equal cases, in declaration order, wins (PS-563). A given
            # discriminator that matches no case takes the default, or is rejected
            # (PS-565): the fields given cannot overrule it, and they did here.
            matched_key, matched_fields, _ = self._best_case(match_def, data)

        if matched_fields is None and match_def.get('remaining') is True \
                and not self._body_score(self._default_body(match_def), data):
            # No case's fields are given, nor the default's: a case reading nothing is
            # selected by the payload ending here, so the case that matches 0 bytes is
            # the one writing nothing selects (pm-io-5-sm's `0..1: []`). Its fields, if
            # any, are asked for as any case's are.
            for case_key, case_fields in cases.items():
                if case_key != 'default' and isinstance(case_fields, list) \
                        and self._match_case_pattern(0, case_key):
                    matched_key, matched_fields = case_key, case_fields
                    break

        label = match_name or match_def.get('var') or 'match'
        if matched_fields is None and discriminator is None and length is not None:
            # CR-2026-151 (PS-551): a nested match reading this discriminator,
            # with exactly one case the input fills, fixes it (Netvox r718n3's
            # report_type, which only its `default` branch's nested match reads).
            nested = self._nested_discriminator(match_def, data, label)
            if nested is not None:
                discriminator, matched_key, matched_fields = nested
                if match_def.get('var'):
                    data = dict(data, **{match_def['var']: discriminator})

        if matched_fields is None:
            if isinstance(default, list):
                matched_fields = default
            elif isinstance(cases.get('default'), list):
                matched_fields = cases['default']
            elif length is not None and discriminator is None:
                # PS-551: an inline discriminator the input neither gives nor fixes
                # through a case's fields, nor a bit range before it peeked. This returned
                # no bytes at all where the match was nameless or its default `skip`,
                # dropping the bytes the decoder reads (PS-442). Named by the
                # discriminator, its `var` where it has no `name` (CR-2026-151).
                raise ValueError(undetermined_case_message(label))
            elif default == 'skip':
                # The discriminator, if the match reads one, and nothing for the case: a
                # decoder skips an unmatched value. A `remaining` match given no case's
                # fields writes nothing, and the payload's length then matches no case.
                matched_fields = []
            elif discriminator is not None:
                # PS-442: no case for the discriminator given, and no default. A decoder
                # rejects it, as an encoder must; this wrote the discriminator alone.
                raise ValueError(f"No matching case for value {discriminator}")
            else:
                # PS-442, PS-551: no discriminator given or derivable, and no case whose
                # fields the input gives. This wrote nothing and reported nothing - a
                # `remaining` match, or one on an internal field (imbuildings, dingtek).
                raise ValueError(undetermined_case_message(
                    match_name or match_def.get('var')
                    or str(field_ref or 'match').lstrip('$')))

        out = bytearray()
        if length is not None:
            value = discriminator
            if value is None:
                if matched_key is None:
                    raise ValueError(undetermined_case_message(label))
                value = self._exact_case_value(matched_key)
                if value is None:
                    raise ValueError(
                        f"match case {matched_key!r} names no single discriminator value")
            # PS-168: a discriminator, given or derived, fits the bytes it is written
            # in.
            low, high = integer_bounds(int(length), False)
            check_fits(round_to_integer(value), low, high, label, value)
            out.extend(int(value).to_bytes(int(length), byteorder))
        body = self._encode_field_list(matched_fields, data)
        if match_def.get('remaining') is True and not getattr(self, '_ps564_depth', 1):
            # PS-564: checked at the top, once the bytes after the match are known.
            # Inside a tlv or repeat the bytes after it are not all in one field list,
            # so it is not checked there.
            self._ps564.append({'match': match_def, 'tail': len(body),
                                'chosen': matched_key if matched_key is not None
                                else 'default'})
        return prefixed_frame(bytes(out), body)

    @staticmethod
    def _recorded_case(match_def: Dict[str, Any], key: Any) -> Any:
        """The case of `match_def` a decode record names (PS-573): `'default'`, or the
        key as the schema writes it. A key the match does not have is rejected."""
        if key == 'default':
            return 'default'
        for case_key in (match_def.get('cases') or {}):
            if case_key != 'default' and (case_key == key
                                          or str(case_key).strip() == str(key).strip()):
                return case_key
        raise ValueError(f"Field {match_label(match_def)}: the recorded case "
                         f"{message_value(key)} is not "
                         f"a case of the match (PS-573)")

    def _check_recorded_case(self, match_def: Dict[str, Any], chosen: Any,
                             data: Dict[str, Any]) -> None:
        """PS-573: under a case taken from the decode record, the input may not give a
        field only another case of the match declares (`chosen` None for the default).

        A name that something outside the match declares within the same reported object
        (CR-2026-165) is not one only another case declares. The match's `default` list is
        one of its cases (CR-2026-164)."""
        cases = match_def.get('cases') or {}
        default = self._default_body(match_def)
        if chosen is not None and chosen in cases:
            mine = set(record_declared_fields(cases[chosen]))
        else:
            mine = set(record_declared_fields(default))
        others = set()
        for key, body in cases.items():
            if key == 'default' or (chosen is not None and key == chosen):
                continue
            others |= set(record_declared_fields(body))
        if chosen is not None and default is not None:
            others |= set(record_declared_fields(default))
        # CR-2026-165: a name something outside this match declares, within the same
        # reported object - the other entries of the lists enclosing it, through the
        # constructs that merge into them - is accounted for there, not by another case.
        outside = set()
        for fields, current in getattr(self, '_enclosing', []):
            outside |= set(record_declared_fields(fields, skip=current))
        clash = sorted(n for n in others - mine - outside if n in data)
        if clash:
            raise ValueError(f"Field {match_label(match_def)}: the input gives '{clash[0]}', "
                             f"which only another case declares, against the recorded "
                             f"case (PS-573)")

    def _nested_discriminator(self, match_def, data, label):
        """CR-2026-151 (PS-551): the value a nested match fixes for this
        match's discriminator, as (value, case key or None for `default`, branch
        fields), or None
        where no nested match reading it has a case the input fills.

        The input fixes the discriminator where it fills exactly one case among the
        nested matches that read it, that case's key is a single value, and the value
        selects, in this match and in every match between, the branch holding the nested
        match. Where the input fills a case of such a match but any of that fails -
        several filled cases, a list or range key, a value selecting another branch -
        the input is rejected, naming the discriminator.
        """
        names = {match_def.get('var'), match_def.get('name')} - {None}
        if not names:
            return None
        cases = match_def.get('cases', {}) or {}
        branches = [(k, v) for k, v in cases.items()
                    if k != 'default' and isinstance(v, list)]
        fallback = match_def.get('default')
        if not isinstance(fallback, list) and isinstance(cases.get('default'), list):
            fallback = cases['default']
        if isinstance(fallback, list):
            branches.append(('default', fallback))

        def names_of(fields):
            return {f['name'] for f in fields
                    if isinstance(f, dict) and f.get('name')
                    and not str(f['name']).startswith('_')
                    and f.get('type') not in COMPUTED_TYPES}

        filled = []      # (path of (match, branch key) reading it, filled case key)

        def walk(fields, path):
            for f in fields or []:
                if not isinstance(f, dict) or not isinstance(f.get('match'), dict) \
                        or f.get('type'):
                    continue
                inner = f['match']
                reads = str(inner.get('field') or '').lstrip('$') in names
                inner_cases = inner.get('cases', {}) or {}
                bodies = [(k, v) for k, v in inner_cases.items()
                          if k != 'default' and isinstance(v, list)]
                if isinstance(inner.get('default'), list):
                    bodies.append(('default', inner['default']))
                # A case is filled where the input gives every one of its fields and
                # nothing any other case of this match declares beyond them: Netvox
                # r718n3's case 3 holds the fields of cases 1 and 2 together.
                declared = [names_of(b) for k, b in bodies if k != 'default']
                given = set().union(*declared) & set(data) if reads else set()
                for key, body in bodies:
                    own = names_of(body)
                    if reads and key != 'default' and own and own <= set(data) \
                            and given <= own:
                        filled.append((path, key))
                    walk(body, path + ([(inner, key)] if reads else []))

        for key, body in branches:
            walk(body, [(match_def, key)])
        if not filled:
            return None
        if len(filled) != 1:
            raise ValueError(undetermined_case_message(label))
        path, case_key = filled[0]
        value = self._exact_case_value(case_key)
        if value is None:
            raise ValueError(undetermined_case_message(label))
        for enclosing, branch_key in path:
            selected = 'default'
            for key, body in (enclosing.get('cases', {}) or {}).items():
                if key != 'default' and self._match_case_pattern(value, key):
                    selected = key
                    break
            if selected != branch_key:
                raise ValueError(undetermined_case_message(label))
        outer_key = path[0][1]
        body = dict(branches)[outer_key]
        return value, (None if outer_key == 'default' else outer_key), body

    def _encode_tlv_tag(self, case_key: Any, tlv_def: Dict[str, Any]) -> bytes:
        """Rebuild a TLV tag from the case key that matched it while decoding.

        The composite form carries the tag values in the key - ``"[3, 103]"`` against
        ``tag_key: [channel_id, channel_type]`` - so encoding reads them back out and
        writes each through its own ``tag_fields`` entry. A key using ``!`` or ``*``
        (PS-270) names no single tag, so it cannot be encoded; 3 of the corpus's 762
        composite keys are of that kind.
        """
        byteorder = 'little' if self.endian == Endian.LITTLE else 'big'
        tag_fields = tlv_def.get('tag_fields')
        tag_key = tlv_def.get('tag_key')

        if tag_fields and tag_key:
            text = str(case_key).strip()
            if text.startswith('['):
                text = text[1:-1] if text.endswith(']') else text[1:]
            parts = [part.strip().strip('"\'') for part in text.split(',')]
            if any(part == '*' or part.startswith('!') for part in parts):
                raise ValueError(
                    f"TLV case {case_key!r} matches a range of tags, so encoding cannot "
                    "choose one")
            values = {}
            names = tag_key if isinstance(tag_key, list) else [tag_key]
            if len(parts) != len(names):
                raise ValueError(f"TLV case {case_key!r} does not match tag_key {names}")
            for name, part in zip(names, parts):
                values[name] = int(part, 0)
            out = bytearray()
            for tf in tag_fields:
                tf_name = tf.get('name', '')
                if tf_name not in values:
                    raise ValueError(f"TLV case {case_key!r} gives no value for {tf_name!r}")
                out.extend(self._encode_field(tf, values[tf_name]))
            return bytes(out)

        tag_size = tlv_def.get('tag_size', 1)
        value = int(str(case_key), 0) if not isinstance(case_key, int) else case_key
        return int(value).to_bytes(tag_size, byteorder)

    def _encode_object_members(self, fields: List[Dict[str, Any]],
                               data: Dict[str, Any]) -> bytes:
        """An object's members or a repeat element's fields: a new reported object, so the
        lists enclosing it are not the ones that report beside it (CR-2026-165)."""
        saved, self._enclosing = getattr(self, '_enclosing', []), []
        try:
            return self._encode_field_list(fields, data)
        finally:
            self._enclosing = saved

    def _encode_field_list(self, fields: List[Dict[str, Any]], data: Dict[str, Any]) -> bytes:
        """Encode a list of fields - a TLV case, match branch, repeat element or object."""
        if not hasattr(self, '_enclosing'):
            self._enclosing = []
        entry = [fields, None]
        self._enclosing.append(entry)
        try:
            return self._encode_field_list_in(fields, data, entry)
        finally:
            self._enclosing.pop()

    def _encode_field_list_in(self, fields: List[Dict[str, Any]], data: Dict[str, Any],
                              entry: list) -> bytes:
        data = self._derive_inputs(fields, data)                           # PS-551
        out = FrameBuffer()             # CR-2026-136: laid out as the decoder reads it
        omitted_optional = None     # the first optional field the input left out (PS-405)
        tails = TailLevel(self, out)                     # PS-564
        for kind, item in self._bitfield_runs(fields):
            tails.start()
            # A run of bit ranges shares one span of bytes, so it is packed once rather
            # than a byte per field (CR-2026-023).
            if kind == 'bits':
                self._encode_bitfield_run(item, data, out)
                continue
            f = item
            if not isinstance(f, dict):
                continue
            entry[1] = f
            name = f.get('name', '')
            # PS-405, before any construct: an optional object or repeat the input omits
            # writes nothing, as a plain field does.
            if f.get('optional') is True and name:
                key = self._resolve_encode_name(f, name, data) or name
                if data.get(key) is None:
                    omitted_optional = omitted_optional or name
                    continue
                if omitted_optional:
                    raise ValueError(f"optional field {name!r} is supplied while the earlier "
                                     f"optional field {omitted_optional!r} is not (PS-405)")
            # A case body may hold another construct rather than a plain field.
            if 'match' in f and not f.get('type'):
                out.extend(self._encode_match(f, data, out.pending, fields))
                continue
            if 'tlv' in f and not f.get('type'):
                out.extend(self._encode_tlv(f, data))
                continue
            if 'byte_group' in f:
                out.extend(self._encode_byte_group(f, data))
                continue
            if 'flagged' in f and not f.get('type'):
                # It was refused here as "declares no type", though the top level encoded
                # the same construct, so a flagged block in a match, tlv or repeat could
                # not be encoded at all.
                out.extend(self._encode_flagged(f['flagged'], data))
                continue
            ftype = f.get('type', 'u8')
            if ftype == 'repeat':
                out.extend(self._encode_repeat(f, data, fields))
                continue
            if ftype == 'object':
                # A nested object is reported under its own name (PS-139), so its members
                # are read from that mapping. They were looked up in the enclosing data,
                # where they are not, and every member encoded as a zero.
                key = self._resolve_encode_name(f, name, data) or name
                nested = data.get(key)
                if not isinstance(nested, dict):
                    # PS-551: its members were written as zeros.
                    raise ValueError(missing_field_message(key))
                out.extend(self._encode_object_members(f.get('fields') or [], nested))
                continue
            if ftype in COMPUTED_TYPES:
                # Derived: computed from other fields, no bytes of its own.
                continue
            if ftype == 'skip':
                length = f.get('length', 1)
                length = 0 if isinstance(length, str) else max(0, int(length))
                out.extend(padding(length))             # CR-2026-141: no value
                continue
            if is_echo(f):
                out.echo(f, data)               # PS-589 (CR-2026-175): writes nothing
                continue
            if is_literal(f):
                continue            # PS-359: a literal writes no bytes
            if 'value' in f:
                # PS-360: the constant is written whatever the input says.
                out.extend(self._encode_field(f, f['value']))
                continue
            # A nameless field other than a skip is refused at load (PS-553); it was
            # written here as zero padding.
            if name.startswith('_'):
                value = self._internal_encode_value(f, data)           # PS-434
            else:
                # As in the top-level loop: a templated key is where the value lives.
                lookup_name = self._resolve_encode_name(f, name, data) or name
                has_default, default = field_default(f)
                if data.get(lookup_name) is None and has_default:
                    data = dict(data, **{lookup_name: default})        # PS-554
                if data.get(lookup_name) is None:
                    if f.get('sentinel'):
                        # A reading the decode omitted as "no reading" (PS-427) goes back
                        # as the sentinel it was, not as zero.
                        out.extend(self._encode_field(raw_bits_field(f), f['sentinel'][0]))
                        continue
                    if self._derive_bool(f, out):
                        continue                    # PS-551 (CR-2026-141): derived
                    # PS-551. This wrote a zero, silently.
                    raise ValueError(missing_field_message(lookup_name))
                value = data[lookup_name]
            if ftype == 'bitfield_string':
                out.extend(self._encode_bitfield_string(f, str(value)))
                continue
            value = self._reverse_modifiers(value, f)
            if ftype == 'bool':
                self._place_bool(f, value, out)                       # CR-2026-136
                continue
            out.extend(self._encode_field(f, value))
        tails.close()
        return out.frame()

    @tail_unchecked
    def _encode_repeat_elements(self, field_def: Dict[str, Any], data: Dict[str, Any],
                                records: List[Any], count: Optional[int] = None) -> bytes:
        """A repeat's elements back to back: no framing, no trailer.

        `count`, where the schema or the input states one, is checked after the refusals
        that concern the schema rather than the input (PS-550, PS-542, PS-387).
        """
        name = field_def.get('name', '')
        # PS-550: a sibling identifying these values position by position is given as
        # many values as this repeat, as a decoder requires (PS-546).
        for sibling in dict.fromkeys(n for _, n, _ in sibling_references(field_def)):
            given = data.get(sibling)
            if not isinstance(given, (list, tuple)) or len(given) != len(records):
                have = len(given) if isinstance(given, (list, tuple)) else 'none'
                raise ValueError(f"Repeat '{name}' is given {len(records)} values and "
                                 f"'{sibling}', which identifies them, {have} (PS-550)")
        record_fields, element_name = repeat_members(field_def)
        if records_reachable(record_fields):
            # PS-542: the records do not say which element they came from.
            raise ValueError(f"Repeat '{name}' has elements that yield several "
                             f"records, which cannot be encoded (PS-542)")
        if element_name is not None:
            # PS-529: each value is encoded as the element, wrapped as it was decoded.
            # PS-544: a null is written as the element's first sentinel, which the field
            # list writes as raw bits for a None value; with no sentinel it is an error.
            if not record_fields[0].get('sentinel'):
                for position, item in enumerate(records):
                    if item is None:
                        raise ValueError(
                            f"Repeat '{name}' has null at position {position}, and its "
                            f"element declares no `sentinel` to write for it (PS-544)")
            records = [{element_name: item} for item in records]
        # PS-387: an element present_if dropped is not in the data, and its bytes were in
        # the payload, so they cannot be written back. Only elements that read nothing
        # (computed fields and literals) can be encoded.
        if field_def.get('present_if') and any(
                isinstance(f, dict) and f.get('type') not in COMPUTED_TYPES
                and not is_literal(f) for f in record_fields):
            raise ValueError(
                f"repeat field {name!r} declares present_if and its elements read payload "
                f"bytes, so the elements it dropped cannot be encoded (PS-387)")
        if count is not None and len(records) != count:
            # PS-551: the count and the list must agree; nothing is padded or cut.
            raise ValueError(f"Repeat '{name}' is given {len(records)} values for a count "
                             f"of {count} (PS-551)")
        index = field_def.get('index')
        out = bytearray()
        for position, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(
                    f"repeat field {name!r}: expected each record to be a mapping, got "
                    f"{type(record).__name__}")
            if index:
                # PS-370: the index is bound while each element is encoded, as it was
                # while each was decoded, so a name_from template resolves the same way.
                record = dict(record, **{index: position + index_base(field_def)})
            # PS-572: the element's position, as its decode recorded it.
            pushed = (self._trace.push(field_def, 'repeat', position)
                      if self._trace is not None else False)
            try:
                element = self._encode_object_members(record_fields, record)
            finally:
                if self._trace is not None:
                    self._trace.pop(pushed)
            check_peeks(element)            # PS-551: an element's bytes close with it
            out.extend(element)
        return bytes(out)

    @tail_unchecked
    def _encode_repeat(self, field_def: Dict[str, Any], data: Dict[str, Any],
                       siblings: Optional[List[Dict[str, Any]]] = None) -> bytes:
        """Encode a ``repeat``: its records back to back, then any trailer.

        The framing costs no bytes of its own here. ``count: $n`` and
        ``byte_length: $len`` name a field earlier in the list, which the field list
        encodes from its own value - derived from this list where the input omits it
        (PS-551) - and ``until: end`` needs no header at all. Each record is a dict, and
        its fields are looked up inside it rather than in the payload-level output.

        PS-551: an absent list is a missing value, and a list must agree with its count
        and byte_length, literal or referenced. An absent list wrote nothing, leaving the
        frame short of the elements a count or byte_length field still declared.
        """
        name = field_def.get('name', '')
        raw = data.get(name)
        if raw is not None and not isinstance(raw, (list, tuple, dict)):
            raise ValueError(
                f"repeat field {name!r}: expected a list of records, got "
                f"{type(raw).__name__}")
        records = self._repeat_records(field_def, data)
        if records is None:
            raise ValueError(missing_field_message(name))
        siblings = siblings or []
        count = self._repeat_size(field_def, 'count', data, siblings)
        out = bytearray(self._encode_repeat_elements(field_def, data, records, count))
        byte_length = self._repeat_size(field_def, 'byte_length', data, siblings)
        if byte_length is not None and len(out) != byte_length:
            raise ValueError(f"Repeat '{name}' encodes to {len(out)} bytes for a "
                             f"byte_length of {byte_length} (PS-551)")
        # PS-385: the elements, then the trailer, whose values sit beside the repeat.
        if field_def.get('trailer'):
            trailer = self._encode_field_list(field_def['trailer'], data)
            check_peeks(trailer)                                        # PS-551
            out.extend(trailer)
        return bytes(out)

    def _repeat_size(self, field_def: Dict[str, Any], key: str, data: Dict[str, Any],
                     siblings: List[Dict[str, Any]]) -> Optional[int]:
        """A repeat's `count` or `byte_length` as the schema or the input states it."""
        size = field_def.get(key)
        if isinstance(size, str) and size.startswith('$'):
            source = self._size_source(siblings, size[1:])
            size = data.get(source['name'] if source is not None else size[1:])
        if isinstance(size, bool) or not isinstance(size, (int, float)):
            return None
        if isinstance(size, float) and not size.is_integer():
            return None
        return int(size)

    def _claimable_fields(
        self, case_fields: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Flatten a tlv case to the fields looked up in the case's own data map.

        A byte_group or flagged field is nameless: its names live in its own group's
        `fields`, and _encode_byte_group and _encode_flagged both read them straight out
        of the same flat map. Collecting only the top level therefore found nothing to
        claim for such a case, never selected it as a candidate, and dropped the channel
        with no bytes and no error. hbi/mla20's case 0x20 is two of these.

        Deliberately not descended into:

        - an object's or repeat's `fields`, whose values live in a nested map under the
          field's own name rather than in this map, so claiming their members would
          claim names this map does not have. The field's own name is claimed instead,
          which is what the nested objects in the milesight schemas rely on.
        - a nested match or tlv, where which branch supplies a name depends on the data,
          so claiming every branch's names would over-claim.
        """
        out = []  # type: List[Dict[str, Any]]
        for f in case_fields:
            if not isinstance(f, dict):
                continue
            if 'byte_group' in f and not f.get('type'):
                group = f['byte_group']
                group_fields = (
                    group.get('fields') or [] if isinstance(group, dict) else group
                )
                out.extend(self._claimable_fields(group_fields or []))
                continue
            if 'flagged' in f and not f.get('type'):
                for group in (f['flagged'].get('groups') or []):
                    out.extend(self._claimable_fields(group.get('fields') or []))
                continue
            name = f.get('name')
            if not name or str(name).startswith('_') or f.get('type') in COMPUTED_TYPES:
                continue
            out.append(f)
        return out

    def _claim_width(self, field_def):
        """How many fields a case's claim counts for when ranking cases (PS-527 as
        CR-2026-129 states it): an object counts its members, so a merged {adr, dr} case
        outranks a {dr} one, and anything else counts one."""
        if field_def.get('type') == 'object':
            return max(1, len(self._case_claims(field_def.get('fields') or [])))
        return 1

    def _case_claims(self, case_fields, optional=False):
        """_claimable_fields with each field marked optional or not (PS-527): a case is
        filled only once the input supplies every required field. A flagged group's
        fields are optional, since its mask says which are present, as is a field
        declaring `optional: true` (PS-405)."""
        out = []
        covered = covered_peeks(case_fields)
        for f in case_fields:
            if not isinstance(f, dict):
                continue
            if 'byte_group' in f and not f.get('type'):
                group = f['byte_group']
                group_fields = (
                    group.get('fields') or [] if isinstance(group, dict) else group
                )
                out.extend(self._case_claims(group_fields or [], optional))
                continue
            if 'flagged' in f and not f.get('type'):
                for group in (f['flagged'].get('groups') or []):
                    out.extend(self._case_claims(group.get('fields') or [], True))
                continue
            if 'match' in f and not f.get('type'):
                out.extend(self._match_claims(f['match']))
                continue
            name = f.get('name')
            if not name or str(name).startswith('_'):
                continue
            if is_literal(f) or f.get('type') in COMPUTED_TYPES or is_echo(f):
                # A literal or computed field writes no bytes, but only this case reports
                # it, so it claims its name, never required: a literal where the input
                # gives its value (wt303's `device_request`, a literal-only entry), a
                # computed field so that the case is chosen and what it is computed from
                # is asked for (PS-434). Neither claimed anything, so an entry reporting
                # only these was never written and the encode returned no bytes and no
                # error.
                out.append((f, True))
                continue
            # PS-551 (CR-2026-141): a peek the case's next field writes over is derived
            # where the input omits it, so the case does not need it given.
            out.append((f, optional or f.get('optional') is True
                        or any(f is c for c in covered)))
        return out

    def _match_claims(self, match_def):
        """The claims a nameless inline match makes for the case or object holding it:
        its `name`, and every branch's fields, each optional, since which branch
        supplies a name depends on the data. Each name once. Not descending into it, a
        tlv case whose only content is a match claimed nothing, was never written, and
        the encode returned an empty payload with no error; an object holding one
        refused every value, since its members were none of the names it declared."""
        if not isinstance(match_def, dict):
            return []
        out, seen = [], set()   # a literal is seen by its value as well (no-silent-empty)
        if match_def.get('name'):
            width = int(match_def.get('length') or 1)
            out.append(({'name': match_def['name'], 'type': f'u{8 * min(width, 8)}'}
                        if width in (1, 2, 3, 4, 8) else {'name': match_def['name']},
                        True))
            seen.add(match_def['name'])
        bodies = list((match_def.get('cases') or {}).values())
        if isinstance(match_def.get('default'), list):
            bodies.append(match_def['default'])
        for body in bodies:
            if isinstance(body, dict):
                body = body.get('fields')
            if not isinstance(body, list):
                continue
            for f, _ in self._case_claims(body, True):
                # Branches report one name as different literals (aquascope's `pc`, "ok"
                # in one and "alarm" in another): each is a claim, since the input's
                # value says which branch it came from.
                key = (f['name'], repr(f['value'])) if is_literal(f) else f['name']
                if key not in seen:
                    seen.add(key)
                    out.append((f, True))
        return out

    def _case_takes(self, field_def: Dict[str, Any], value: Any) -> Tuple[bool, bool]:
        """Whether a tlv case's field can take a value, and whether it reproduces it
        exactly: (fits, exact). CR-2026-148 amends PS-527 to say so:

        - Fits: the value, with the field's arithmetic undone (the lookup reversed,
          the `transform` stages and mult/div/add inverted) and, for an integer type or
          a bit range, rounded as PS-552 requires, lies within the type's range
          (PS-168). A float takes any finite value no larger than its largest (65504
          for f16, about 3.4028235e38 for f32). am308's `tvoc` of 4369 needs 436900
          through one case's `div: 100`, which its u16 cannot hold, so that case does
          not fit.
        - Exact: the field's arithmetic re-applied to the raw value written, through
          the type's storage, gives the value under binary64 equality. A decoded value
          always reproduces exactly in the case it came from, so this re-selects that
          case where another can also take the value; 21.8 typed into an f32 still fits.

        No tolerance applies to either test. The test this replaces asked that the value
        reverse to an integer within an absolute 1e-9, which refused every float field
        (an f32 of 21.8) and 0xfffffe through `div: 100` (16777214.000000002).

        CR-2026-181 amends it again: a `bytes`, `hex` or `base64` field takes a value
        that is well formed (PS-601) and, where its `length` is a literal, has that many
        bytes (PS-602). A 3-byte id offered to a 6-byte case went there and was padded.
        """
        if field_def.get('type') in BYTE_STRING_TYPES:
            try:
                check_byte_string_length(field_def, byte_string_input(field_def, value))
            except ValueError:
                return False, False
            return True, True
        if field_def.get('type') == 'repeat' and isinstance(value, (list, tuple)):
            # A repeat takes a list whose elements its members take, so a Tektelic
            # `devices` list of 3-byte ids goes to the case whose element declares
            # `length: 3`, not the 6-byte one declared first (CR-2026-181).
            members, element_name = repeat_members(field_def)
            exact = True
            for item in value:
                record = {element_name: item} if element_name is not None else item
                if not isinstance(record, dict):
                    continue
                for member in members:
                    name = member.get('name') if isinstance(member, dict) else None
                    if record.get(name) is None:
                        continue
                    fit, member_exact = self._case_takes(member, record[name])
                    if not fit:
                        return False, False
                    exact = exact and member_exact
            return True, exact
        given = reverse_lookup(value, field_def.get('lookup'))
        if not isinstance(given, (int, float)) or isinstance(given, bool):
            return True, True
        try:
            raw = reverse_transform_stages(given, field_def.get('transform'))
            raw = reverse_canonical_modifiers(raw, field_def)
        except Exception:
            return False, False
        if not isinstance(raw, (int, float)) or raw != raw or raw in (float('inf'),
                                                                        float('-inf')):
            return False, False
        field_type = str(field_def.get('type', 'u8'))
        if '[' in field_type:
            _, _, width = self._parse_bitfield_type(field_type)
            low, high = integer_bounds(signed=field_type.startswith('s'), bits=width)
            stored = round_to_integer(raw)
            if not low <= stored <= high:
                return False, False
        elif field_type in INTEGER_TYPE_INFO:
            low, high = integer_range(field_type)
            stored = round_to_integer(raw)
            if not low <= stored <= high:
                return False, False
        elif field_type == 'bf16':
            # CR-2026-149: through the type's storage is through PS-559's rounding; a
            # value that rounds past the largest bf16 does not fit.
            try:
                stored = decode_bf16(encode_bf16(raw))
            except ValueError:
                return False, False
        elif field_type in FLOAT_TYPE_MAX:
            if abs(raw) > FLOAT_TYPE_MAX[field_type]:
                return False, False
            code = {'f16': 'e', 'f64': 'd'}.get(field_type, 'f')
            try:
                stored = struct.unpack(code, struct.pack(code, float(raw)))[0]
            except (OverflowError, struct.error):
                return False, False
        elif field_type in ('udec', 'sdec'):
            n = round_to_integer(float(raw) * 10)                         # PS-556
            low, high = (-80, 79) if field_type == 'sdec' else (0, 159)
            if not low <= n <= high:
                return False, False
            stored = (n // 10) + (n % 10) * 0.1
        elif field_type in MINIFLOAT_SIZES:
            try:
                stored = decode_minifloat(field_type, encode_minifloat(field_type, raw))
            except (ValueError, OverflowError):
                return False, False
            if stored is None:
                return False, False
        else:
            return True, True
        forward = apply_canonical_modifiers(stored, field_def)
        if field_def.get('transform') and forward is not OMITTED:
            forward = self._apply_transform(forward, field_def['transform'])
        return True, forward is not OMITTED and forward == given

    @tail_unchecked
    def _encode_tlv(self, field_def: Dict[str, Any], data: Dict[str, Any]) -> bytes:
        """Rebuild a TLV payload from decoded output.

        Decoding flattens every channel into one dict, so the channels have to be
        recovered from which field names are present. Their order comes from the order
        those names appear in the dict, which for output straight from `decode` is the
        order they were read - that is what lets a payload round-trip rather than come
        back with its channels rearranged.

        A case whose fields are all absent is not emitted. A case that cannot be
        encoded - a wildcard tag, or a field the data does not carry - raises, and the
        caller records it against the payload rather than writing a wrong tag.
        """
        tlv_def = field_def.get('tlv', {})
        if tlv_def.get('merge', True) is False:
            # Its entries are a list under `channels`, which the claiming below cannot
            # see, so this wrote an empty payload and reported nothing. Encoding them is
            # not defined yet; say so rather than write a frame with the entries missing.
            raise ValueError(
                "a tlv with merge: false reports its entries under 'channels', and "
                "encoding them is not supported")
        cases = tlv_def.get('cases', {}) or {}
        length_size = tlv_def.get('length_size', 0) or 0
        byteorder = 'little' if self.endian == Endian.LITTLE else 'big'
        order = list(data)
        modes, last_members, _ = tlv_repeat_modes(tlv_def)
        plan = tlv_encode_plan(tlv_def, modes, last_members)
        # PS-527: a supplied member of a merged object that no case declares has
        # nowhere to be written.
        for n, members in plan['members'].items():
            if isinstance(data.get(n), dict):
                stray = sorted(set(data[n]) - members)
                if stray:
                    raise ValueError(f"tlv: the merged object '{n}' is given {stray}, "
                                     f"which no case declares (PS-527)")

        # PS-527 as CR-2026-128 replaces it: each value - a scalar, each element of an
        # array PS-157 collected, a `last` value, a merged object's members - goes under
        # the first case in declaration order whose declared fields it supplies in full.
        # Nothing is zero-filled and nothing dropped; what no case takes is an error.
        entries = []  # (case key, fields, required, optional)
        for case_key, case_fields in cases.items():
            if case_key == 'default' or not isinstance(case_fields, list):
                continue
            required, optional = [], []
            for f, is_optional in self._case_claims(case_fields):
                (optional if is_optional else required).append(f)
            entries.append((case_key, case_fields, required, optional))
        # A case requiring more fields is tried first, then declaration order. Among
        # cases sharing a name, only the fuller one can take the others it declares:
        # em400-mud's [3, 103] (temperature) is declared before [131, 103]
        # (temperature, temperature_alarm), and taking temperature first strands the
        # alarm, which no case would then take in full (CR-2026-029).
        entries.sort(key=lambda entry: -sum(self._claim_width(f) for f in entry[2]))

        queues = {}  # type: Dict[str, list]
        for _, _, required, optional in entries:
            for f in required + optional:
                n = f['name']
                if n in data and n not in queues and modes.get(n) != 'merge':
                    value = data[n]
                    collected = isinstance(value, list) and not _reports_list(f) \
                        and modes.get(n, 'collect') == 'collect'
                    queues[n] = list(value) if collected else [value]
        pending = {n: set(data[n]) for n in plan['members']
                   if isinstance(data.get(n), dict)}

        def members_of(f):
            return {m['name'] for m in f.get('fields') or []
                    if isinstance(m, dict) and m.get('name')}

        def fits(f, value, exact_only):
            # An object element supplies exactly the members its case declares, and a
            # case takes no value it cannot write (CR-2026-148, _case_takes); in the
            # first sweep, only a value it reproduces exactly.
            if f.get('type') == 'object' and isinstance(value, dict) \
                    and modes.get(f['name']) != 'merge':
                claims = self._case_claims(f.get('fields') or [])
                every = {m['name'] for m, _ in claims}
                needed = {m['name'] for m, is_optional in claims if not is_optional}
                return needed <= set(value) <= every
            # A mapping is no value for a field that reads a number, a string or bytes:
            # two cases may report one name as an object in one and a scalar in another
            # (sensative/strips' `temperature`), and the scalar case cannot have written
            # an object. It reached the numeric encode path and raised from float().
            if isinstance(value, dict) and f.get('type') not in ('object', None, 'repeat'):
                return False
            if f.get('type') == 'object' and not isinstance(value, dict) \
                    and modes.get(f['name']) != 'merge':
                return False
            if is_literal(f):
                return same_label(f['value'], value)        # PS-359, PS-513
            if f.get('type') in COMPUTED_TYPES:
                return True             # written from what it is computed from (PS-434)
            fit, exact = self._case_takes(f, value)
            return fit and (exact or not exact_only)

        # CR-2026-148 (PS-527 amended): among the cases that can take a value, one where
        # every field reproduces it exactly is preferred, so a first sweep places only
        # exact values and a second the rest; the PS-527 ranking (widest claim, then
        # declaration order) applies within each.
        written = []  # (case key, fields, data for the entry)
        for exact_only in (True, False):
            progress = True
            while progress:
                progress = False
                for case_key, case_fields, required, optional in entries:
                    while True:
                        values, taken, complete = {}, [], True
                        for f in required:
                            n = f['name']
                            if modes.get(n) == 'merge':
                                members = members_of(f)
                                if n not in pending or not members <= set(data[n]):
                                    complete = False
                                    break
                                values[n] = {m: data[n][m] for m in members}
                                if members & pending[n]:
                                    taken.append(n)
                            else:
                                queue = queues.get(n)
                                if not queue or not fits(f, queue[0], exact_only):
                                    complete = False
                                    break
                                values[n] = queue[0]
                                taken.append(n)
                        if not complete:
                            break
                        for f in optional:
                            queue = queues.get(f['name'])
                            if queue and fits(f, queue[0], exact_only):
                                values[f['name']] = queue[0]
                                taken.append(f['name'])
                        if not taken:
                            break
                        for n in values:
                            if modes.get(n) == 'merge':
                                pending[n] -= set(values[n])
                            else:
                                queues[n].pop(0)
                        written.append((case_key, case_fields, dict(data, **values),
                                        min(order.index(n) for n in values)))
                        progress = True
        for n, queue in queues.items():
            if queue:
                raise ValueError(f"tlv: no case takes the value of '{n}' {queue[0]!r} in "
                                 f"full (PS-502, PS-527)")
        for n, members in pending.items():
            if members:
                raise ValueError(f"tlv: no case takes the members {sorted(members)} of "
                                 f"the merged object '{n}' in full (PS-527)")
        # A `name_from` field reports under a key built from its template, which no
        # claim above can name. Where the template reads a tag component (rakwireless's
        # `digital_in_${ch}`) the tag is not in the data, so no case can take the value -
        # and it was dropped with nothing reported. A key some field declares by name is
        # that field's, not the template's.
        declared = None
        for case_fields in cases.values():
            if not isinstance(case_fields, list):
                continue
            for f, _ in self._case_claims(case_fields):
                template = f.get('name_from')
                if not isinstance(template, str):
                    continue
                pattern = re.compile('.+'.join(
                    re.escape(part) for part in re.split(r'\$\{\w+\}', template)))
                for key in data:
                    if not isinstance(key, str) or key in queues or key in pending \
                            or not pattern.fullmatch(key):
                        continue
                    if declared is None:
                        declared = declared_sources(getattr(self, '_encode_scope', None) or [])
                    if key in declared:
                        continue
                    raise ValueError(f"tlv: no case takes the value of '{key}' "
                                     f"{data[key]!r} in full (PS-502, PS-527)")

        # The entries go out in payload order - the order of the names in decoded
        # output - so a payload round-trips; entries of one name keep the order the
        # cases took them in.
        written.sort(key=lambda item: item[3])
        out = bytearray()
        for entry_index, (case_key, case_fields, case_data, _) in enumerate(written):
            tag = self._encode_tlv_tag(case_key, tlv_def)
            # PS-572: the entry's index in payload order, as its decode recorded it.
            pushed = (self._trace.push(tlv_def, 'tlv', entry_index)
                      if self._trace is not None else False)
            try:
                value = self._encode_field_list(case_fields, case_data)
            finally:
                if self._trace is not None:
                    self._trace.pop(pushed)
            check_peeks(value)              # PS-551: a tlv value is measured whole
            out.extend(tag)
            if length_size > 0:
                out.extend(len(value).to_bytes(length_size, byteorder))
            out.extend(value)
        return bytes(out)

    def _reverse_modifiers(self, value: Any, field_def: Dict[str, Any]) -> Any:
        """Reverse arithmetic modifiers for encoding."""
        # The lookup comes first, before the numeric guard below: a lookup's whole
        # purpose is to report a label, so the value arriving here is a string and the
        # guard returned it untouched - reverse_lookup was dead code for every label,
        # and the label then reached int() as "invalid literal for int() with base 10:
        # 'Class A'". 69 corpus vectors failed to encode on exactly that.
        # The value as given, for a PS-168 rejection to name (PS-552).
        self._encode_given = (field_def, value)     # held, compared by identity
        value = reverse_lookup(value, field_def.get('lookup'),
                               field_def.get('name', '?'))                 # PS-169

        if isinstance(value, bool) and field_def.get('lookup'):
            # PS-513: a boolean matches only a boolean label, and none carries this one
            # under a key of its own. Where only the mapping's `default` carries it, it
            # stands for every unmapped value (PS-269, PS-409), so there is nothing to
            # write. It used to reach int() below and be written as 1.
            raise ValueError(
                f"{value!r} is not a label with a key of its own in the lookup for "
                f"{field_def.get('name')!r}; a boolean matches only a boolean label, "
                "and a `default` label has no value to recover (PS-409, PS-513)")

        if isinstance(value, str) and field_def.get('lookup'):
            # The label is not in the table, so it came from the mapping's `default`,
            # which stands for every value the table does not list (PS-269) - there is no
            # original to recover. Said plainly here, because otherwise int() reported
            # "invalid literal for int() with base 10: 'unknown'".
            template = lookup_template(field_def.get('lookup'))
            if template is not None:
                raise ValueError(
                    f"{value!r} is neither a label in the lookup for "
                    f"{field_def.get('name')!r} nor a match for its default "
                    f"{template!r} (PS-409)")
            raise ValueError(
                f"{value!r} is not a label in the lookup for {field_def.get('name')!r}; "
                "a `default` label matches any unmapped value, so the value that "
                "produced it cannot be recovered")

        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return value
        
        # Decoding applies the canonical modifiers, then the transform chain, then the
        # lookup, so encoding undoes them in the opposite order.
        value = reverse_transform_stages(value, field_def.get('transform'))

        value = reverse_canonical_modifiers(value, field_def)
        
        # Float types should preserve fractional values
        field_type = field_def.get('type', 'u8')
        if field_type in ('f16', 'bf16', 'f32', 'f64', 'udec', 'sdec', 'f32le16', 'f32be16le',
                          'uflt16', 'sflt16', 'sflt24'):
            return float(value)
        
        return int(round(value))
    
    def _encode_field(self, field_def: Dict[str, Any], value: Any) -> bytes:
        """Encode a single field value, honouring a field-level `endian:` override.

        The mirror of the decode override. Without it a schema using the key round-trips
        to different bytes than it decoded from, which is the defect the round-trip
        suite exists to catch.
        """
        endian_override = field_def.get('endian')
        if (endian_override is not None
                and field_def.get('type', 'u8') not in self._ENDIAN_OPAQUE_TYPES):
            try:
                override = Endian(endian_override)
            except ValueError:
                raise ValueError(
                    f"field 'endian' must be 'big' or 'little', got {endian_override!r}"
                )
            saved = self.endian
            self.endian = override
            try:
                return self._encode_field_inner(field_def, value)
            finally:
                self.endian = saved
        return self._encode_field_inner(field_def, value)

    def _given_value(self, field_def: Dict[str, Any], value: Any) -> Any:
        """The value the input gave for this field, before its arithmetic was inverted."""
        recorded = getattr(self, '_encode_given', None)
        if recorded is not None and recorded[0] is field_def:
            return recorded[1]
        return value

    def _encode_field_inner(self, field_def: Dict[str, Any], value: Any) -> bytes:
        """Encode a single field value."""
        field_type = field_def.get('type')
        if not field_type:
            raise ValueError(
                f"Field '{field_def.get('name', '?')}' declares no type"
            )
        
        given = self._given_value(field_def, value)

        # A lone bit range: its byte, the value in place. Rounded and checked against the
        # range's width (PS-552, PS-168), where it was masked to a byte silently.
        if any(c in str(field_type) for c in ['[', ':', '<']):
            base_size, start, width = self._parse_bitfield_type(str(field_type))
            raw = round_to_integer(value)
            low, high = integer_bounds(signed=str(field_type).startswith('s'), bits=width)
            check_fits(raw, low, high, field_def.get('name'), given, str(field_type))
            packed = (raw & ((1 << width) - 1)) << start
            return packed.to_bytes(max(1, base_size), 'little' if self.endian == Endian.LITTLE
                                   else 'big')
        
        type_info = INTEGER_TYPE_INFO

        # The inverse of the word-ordered read (PS-271): least significant 16-bit unit
        # first, each unit big-endian, and `endian` plays no part (PS-272).
        if field_type in WORD_ORDERED_TYPES:
            if WORD_ORDERED_TYPES[field_type][1] != 'f':
                raw = round_to_integer(value)                                 # PS-552
                low, high = integer_bounds(4, field_type.startswith('s'))
                check_fits(raw, low, high, field_def.get('name'), given)      # PS-168
                value = raw
            return write_word_ordered(field_type, value)

        if field_type in type_info:
            size, signed = type_info[field_type]
            # PS-552: nearest, a tie to even, before any encoding; int() truncated.
            int_val = round_to_integer(value)
            # Apply encoding if specified (sign_magnitude, bcd, gray)
            encoding = field_def.get('encoding')
            if encoding:
                int_val = self._encode_encoding(int_val, encoding, size)
                # Encoded values are written as unsigned
                signed = False
            low, high = integer_bounds(size, signed)
            check_fits(int_val, low, high, field_def.get('name'), given)     # PS-168
            return self._write_int(int_val, size, signed)
        
        if field_type in MINIFLOAT_SIZES:
            return self._write_int(encode_minifloat(field_type, value),
                                   MINIFLOAT_SIZES[field_type], False)

        if field_type in ('udec', 'sdec'):
            # PS-556 (CR-2026-147): n is the value times 10, rounded as PS-552 requires
            # (half to even, on the binary64 product); the upper nibble is n // 10 (floored,
            # so -0.5 is -1 and 5 tenths) and the lower n % 10, never above 9. Splitting the
            # whole part off first and rounding the fraction gave 3.45 as 0x35, not 0x34.
            n = round_to_integer(float(value) * 10)
            check_nibble_decimal(n, field_type == 'sdec',
                                 field_def.get('name', '?'), given)       # PS-168
            return bytes([((n // 10) & 0x0F) << 4 | (n % 10)])

        if field_type == 'f16':
            return pack_float('e', value, self.endian == Endian.LITTLE)      # PS-566
        
        if field_type == 'bf16':
            return self._write_int(encode_bf16(positive_zero(value), field_def.get('name')), 2, False)

        if field_type == 'f32':
            return pack_float('f', value, self.endian == Endian.LITTLE)      # PS-566
        
        if field_type == 'f64':
            return pack_float('d', value, self.endian == Endian.LITTLE)      # PS-566
        
        if field_type == 'bool':
            return bytes([1 if value else 0])
        
        if field_type == 'skip':
            length = field_def.get('length', 1)
            length = 0 if isinstance(length, str) else max(0, int(length))
            return bytes(length)
        
        if field_type == 'bytes':
            # Accepts every form a `bytes` field can arrive in (PS-081), because
            # CR-2026-008 makes the decoder report one as a lowercase hex string (PS-281)
            # and encode(decode(payload)) has to keep round-tripping. CR-2026-181: a
            # malformed value (PS-601) or one whose byte count is not a literal `length`
            # (PS-602) is rejected naming the field; this cut and zero-padded it.
            return check_byte_string_length(field_def, byte_string_input(field_def, value))
        
        if is_literal(field_def):
            # A literal came from no bytes, so it writes none (PS-359).
            return b''
        if field_type in ('string', 'ascii'):
            # PS-537. A fixed `length` pads and bounds the value; `remaining` or a
            # reference has no fixed count, so the value supplies it.
            fixed = field_def.get('length')
            if not isinstance(fixed, int) or isinstance(fixed, bool) or fixed < 0:
                fixed = None
            if field_def.get('numeral') and field_type == 'ascii':
                value = encode_numeral(field_def.get('name'), value, fixed,
                                       field_def['numeral'])
            return encode_ascii(field_def.get('name'), value, fixed,
                                self._encode_warnings)
        
        if field_type in ('hex', 'base64'):
            # PS-601 and PS-602 apply to the hex and base64 types as to `bytes` with that
            # format (CR-2026-181); both cut and zero-padded to `length`.
            return check_byte_string_length(field_def, byte_string_input(field_def, value))
        
        if field_type == 'enum':
            return self._encode_enum(field_def, value)
        
        raise ValueError(f"Cannot encode type: {field_type}")
    
    def _encode_enum(self, field_def: Dict[str, Any], value: Any) -> bytes:
        """An enum's name back to its code, or a code as given (PS-067).

        A name several codes share is rejected (PS-169), and so is one with no code, the
        `default` label among them (PS-068, PS-554). A code is rounded (PS-552) and must
        fit the base, the error naming the enum (PS-168): the base was encoded as a
        field of its own with no name, so the message said 'None'. `unknown(N)`, the
        label PS-269 withdrew, is no longer read back.
        """
        name = field_def.get('name', '?')
        base_type = field_def.get('base', 'u8')
        values = field_def.get('values', {})
        if isinstance(value, str):
            if isinstance(values, dict):
                codes = [lookup_key_code(k) for k, v in values.items()
                         if enum_label(v) == value]
            else:
                codes = [i for i, v in enumerate(values or []) if enum_label(v) == value]
            codes = [c for c in codes if c is not None]
            if not codes:
                raise ValueError(f"Field '{name}': {label_text(value)} is not a name of "
                                 f"the enum (PS-067)")
            if len(codes) > 1:
                raise shared_label_error(name, value, codes)               # PS-169
            int_value = codes[0]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            int_value = round_to_integer(value)                           # PS-552
        else:
            raise ValueError(f"Field '{name}': {label_text(value)} is not a name or code "
                             f"of the enum (PS-067)")
        size, signed = INTEGER_TYPE_INFO.get(base_type, (1, False))
        low, high = integer_bounds(size, signed)
        check_fits(int_value, low, high, name, self._given_value(field_def, value))
        return self._write_int(int_value, size, signed)

    def encode_command(self, command_name: str, data: Dict[str, Any] = None) -> EncodeResult:
        """
        Encode a downlink command by name.
        
        Args:
            command_name: Name of the command (from downlink_commands)
            data: Command parameters (field values)
            
        Returns:
            EncodeResult with encoded payload starting with command_id
        """
        result = EncodeResult(payload=b'')
        self._encode_warnings = []
        data = data or {}
        
        if command_name not in self.downlink_commands:
            result.errors.append(f"Unknown command: {command_name}")
            return result
        
        cmd_def = self.downlink_commands[command_name]
        command_id = cmd_def.get('command_id', 0)
        fields = cmd_def.get('fields', [])
        self._encode_scope = fields
        
        output = bytearray()
        
        # Write command_id as first byte
        if isinstance(command_id, int):
            output.append(command_id & 0xFF)
        elif isinstance(command_id, str) and command_id.startswith('0x'):
            output.append(int(command_id, 16) & 0xFF)
        
        # Encode command fields
        for field_def in fields:
            name = field_def.get('name', '_')
            if name.startswith('_'):
                continue
            
            value = data.get(name)
            if value is None and field_default(field_def)[0]:
                value = field_default(field_def)[1]                    # PS-554
            if value is None:
                # PS-551: written as zero, silently where the key was absent.
                result.errors.append(missing_field_message(name))
                continue
            
            try:
                value = self._reverse_modifiers(value, field_def)
                encoded = self._encode_field(field_def, value)
                output.extend(encoded)
            except Exception as e:
                result.errors.append(error_text(f"Error encoding command field {name}: ", e))
        
        result.payload = b'' if result.errors else bytes(output)
        result.warnings.extend(self._encode_warnings)
        return result
    
    def decode_command(self, payload: bytes) -> DecodeResult:
        """
        Decode a downlink command from payload.
        
        First byte is command_id, followed by command-specific fields.
        
        Returns:
            DecodeResult with decoded data including '_command' name
        """
        result = DecodeResult(data={}, bytes_consumed=0)
        
        if len(payload) < 1:
            result.errors.append("Payload too short for command_id")
            return result
        
        command_id = payload[0]
        pos = 1
        
        # Find matching command by command_id
        matched_cmd = None
        matched_name = None
        for cmd_name, cmd_def in self.downlink_commands.items():
            cmd_id = cmd_def.get('command_id', -1)
            if isinstance(cmd_id, str) and cmd_id.startswith('0x'):
                cmd_id = int(cmd_id, 16)
            if cmd_id == command_id:
                matched_cmd = cmd_def
                matched_name = cmd_name
                break
        
        if matched_cmd is None:
            result.errors.append(f"Unknown command_id: 0x{command_id:02X}")
            result.data['_command_id'] = command_id
            result.bytes_consumed = 1
            return result
        
        result.data['_command'] = matched_name
        result.data['_command_id'] = command_id
        
        # Decode command fields
        fields = matched_cmd.get('fields', [])
        for field_def in fields:
            name = field_def.get('name', 'unknown')
            try:
                value, pos = self._decode_field(field_def, payload, pos)
                if value is not None:
                    value = self._apply_modifiers(value, field_def)
                    if not name.startswith('_'):
                        result.data[name] = value
            except Exception as e:
                result.errors.append(error_text(f"Error decoding command field {name}: ", e))
                break
        
        result.bytes_consumed = pos
        return result
    
    def list_commands(self) -> Dict[str, Dict[str, Any]]:
        """
        List available downlink commands with their metadata.
        
        Returns:
            Dict mapping command names to their definitions
        """
        commands = {}
        for cmd_name, cmd_def in self.downlink_commands.items():
            cmd_id = cmd_def.get('command_id', 0)
            if isinstance(cmd_id, str) and cmd_id.startswith('0x'):
                cmd_id = int(cmd_id, 16)
            
            fields = []
            for f in cmd_def.get('fields', []):
                field_info = {'name': f.get('name'), 'type': f.get('type', 'u8')}
                if 'unit' in f:
                    field_info['unit'] = f['unit']
                fields.append(field_info)
            
            commands[cmd_name] = {
                'command_id': cmd_id,
                'fields': fields,
            }
        return commands
    
    def get_field_metadata(self, field_name: str = None) -> Dict[str, Any]:
        """
        Get semantic metadata for schema fields.
        
        Args:
            field_name: Specific field name, or None for all fields
            
        Returns:
            Metadata dict with unit, valid_range, resolution, unece, ipso, etc.
        """
        def extract_metadata(field_def: Dict[str, Any]) -> Dict[str, Any]:
            meta = {}
            for key in ('unit', 'valid_range', 'resolution', 'unece', 
                       'description', 'semantic', 'ipso', 'senml', 'senml_unit'):
                if key in field_def:
                    meta[key] = field_def[key]
            # Flatten semantic sub-dict
            if 'semantic' in meta:
                for k, v in meta.pop('semantic').items():
                    meta[k] = v
            return meta
        
        def collect_fields(fields: List[Dict[str, Any]], result: Dict[str, Dict]):
            for field_def in fields:
                name = field_def.get('name')
                if name:
                    meta = extract_metadata(field_def)
                    if meta:
                        result[name] = meta
                # Recurse into nested structures
                if 'fields' in field_def:
                    collect_fields(field_def['fields'], result)
                if 'byte_group' in field_def:
                    collect_fields(field_def['byte_group'], result)
        
        all_metadata = {}
        collect_fields(self.schema.get('fields', []), all_metadata)
        
        if field_name:
            return all_metadata.get(field_name, {})
        return all_metadata
    
    def get_semantic_output(self, decoded: Dict[str, Any], 
                           format: str = 'ipso', fPort: int = None) -> Dict[str, Any]:
        """
        Convert decoded data to semantic format.
        
        Args:
            decoded: Decoded field values
            format: 'ipso', 'senml', or 'ttn'
            fPort: the port the payload arrived on, for a schema that selects its
                fields by port; its entry's fields are the ones reported (PS-478).
            
        Returns:
            Semantically formatted output. What it could not express is in
            `semantic_warnings` afterwards.
        """
        self.semantic_warnings = []
        if fPort is not None or not self.schema.get('ports'):
            fields = self._resolve_fields(fPort)
        else:
            # No port given: every entry's fields are searched, first declaration first.
            fields = list(self.schema.get('fields') or [])
            for entry in self.schema['ports'].values():
                group = entry.get('fields') if isinstance(entry, dict) else entry
                fields.extend(group or [])
        
        if format == 'ipso':
            return self._to_ipso(decoded, fields)
        elif format == 'senml':
            return self._to_senml(decoded, fields)
        elif format == 'ttn':
            return self._to_ttn(decoded, fields)
        else:
            return decoded
    
    def _to_ipso(self, decoded: Dict[str, Any], 
                 fields: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert to IPSO Smart Object format."""
        result = {}
        
        for field_def in fields:
            name = field_def.get('name')
            if name not in decoded:
                continue
            
            semantic = field_def.get('semantic', {})
            ipso = semantic.get('ipso')
            
            if ipso:
                obj_id = str(ipso)
                if obj_id not in result:
                    result[obj_id] = {}
                result[obj_id]['value'] = decoded[name]
                
                unit = field_def.get('unit')
                if unit:
                    result[obj_id]['unit'] = unit
            else:
                result[name] = decoded[name]
        
        return result
    
    @staticmethod
    def _reported_fields(fields: List[Any]) -> Dict[str, Dict[str, Any]]:
        """Reported key -> declaring field, for one level of decoded output.

        A construct that merges its fields into its parent (PS-156, PS-163) contributes
        them at this level: a match case, a byte_group, a flagged group, a tlv case
        unless `merge: false`. An object or repeat contributes its own name; its members
        are another level. The first declaration of a name wins, as the first case to
        report it would.
        """
        found: Dict[str, Dict[str, Any]] = {}

        def visit(field_list):
            for f in field_list or []:
                if not isinstance(f, dict):
                    continue
                if 'byte_group' in f:
                    group = f['byte_group']
                    visit(group.get('fields') if isinstance(group, dict) else group)
                if isinstance(f.get('flagged'), dict):
                    for g in f['flagged'].get('groups') or []:
                        visit(g.get('fields'))
                match = f.get('match')
                if isinstance(match, dict):
                    for body in (match.get('cases') if isinstance(match.get('cases'), dict) else {}).values():
                        if isinstance(body, list):
                            visit(body)
                    if isinstance(match.get('default'), list):
                        visit(match['default'])
                    if match.get('name'):
                        found.setdefault(match['name'], {'name': match['name']})
                tlv = f.get('tlv')
                if isinstance(tlv, dict):
                    if tlv.get('merge', True) is False and f.get('name'):
                        found.setdefault(f['name'], dict(f, type='object'))
                    else:
                        for body in (tlv.get('cases') if isinstance(tlv.get('cases'), dict) else {}).values():
                            if isinstance(body, list):
                                visit(body)
                if f.get('name'):
                    found.setdefault(f['name'], f)

        visit(fields)
        return found

    def _to_senml(self, decoded: Dict[str, Any],
                  fields: List[Dict[str, Any]], prefix: str = '') -> List[Dict[str, Any]]:
        """Convert to SenML format (PS-478, PS-479).

        One record per field the output reports, wherever it is declared, named by its
        `senml.name` or else its reported name, with the unit PS-487 and PS-488 derive
        from `senml.unit` or else `unit`: a secondary unit is re-expressed in its primary
        unit, and an unregistered one is left out with a warning (CR-2026-094). This
        used to walk the top-level field list only and ignore the `senml` block: a
        schema's `{name: temp, unit: Cel}` came out as `temperature` in `°C`, and a
        field inside a port entry, a case or a group had no record. A member of an
        object with no `senml.name` is named by the object's reported name and its own,
        joined by `/` (PS-479). Internal fields and omitted ones are not reported, so
        they have no record.
        """
        records = []
        declared = self._reported_fields(fields)

        for name, value in decoded.items():
            if str(name).startswith('_'):
                continue        # `_quality`, `_warnings`: not fields
            field_def = declared.get(name, {})
            path = f"{prefix}/{name}" if prefix else str(name)
            if isinstance(value, dict):
                # A nested object: its members are records of their own (PS-479).
                records.extend(self._to_senml(value, field_def.get('fields') or [], path))
                continue
            senml = field_def.get('senml') if isinstance(field_def.get('senml'), dict) else {}
            record = {'n': senml.get('name') or path}
            value, unit, unregistered = senml_unit(
                value, senml.get('unit') or field_def.get('unit'))
            if unregistered:
                self.semantic_warnings.append(
                    f"{path}: unit {unregistered!r} is not a registered SenML unit, "
                    f"so its record has no 'u' (PS-488)")

            if isinstance(value, bool):
                record['vb'] = value
            elif isinstance(value, (int, float)):
                record['v'] = value
            elif isinstance(value, str):
                record['vs'] = value
            elif isinstance(value, bytes):
                record['vd'] = value.hex()
            else:
                record['v'] = value     # a repeat's elements: PS-377, unchanged here

            if unit:
                record['u'] = unit

            records.append(record)

        return records
    
    def _to_ttn(self, decoded: Dict[str, Any], 
                fields: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert to TTN normalized format."""
        return {
            'decoded_payload': decoded,
            'normalized_payload': [
                {
                    'measurement': {
                        field_def.get('name'): {
                            'value': decoded.get(field_def.get('name')),
                            'unit': field_def.get('unit', ''),
                        }
                    }
                }
                for field_def in fields
                if field_def.get('name') in decoded
            ]
        }


def decode_payload(schema: Dict[str, Any], payload: bytes) -> Dict[str, Any]:
    """Convenience function to decode payload."""
    interpreter = SchemaInterpreter(schema)
    result = interpreter.decode(payload)
    if not result.success:
        raise ValueError(f"Decode errors: {result.errors}")
    return result.data


def encode_payload(schema: Dict[str, Any], data: Dict[str, Any]) -> bytes:
    """Convenience function to encode data."""
    interpreter = SchemaInterpreter(schema)
    result = interpreter.encode(data)
    if not result.success:
        raise ValueError(f"Encode errors: {result.errors}")
    return result.payload


#: Plain scalars that YAML 1.1 loaders read as booleans and YAML 1.2 loaders as strings
#: (PS-575, CR-2026-164). `y` and `n` are not here: common loaders read them as text, and
#: they are field names (an accelerometer's `y` axis).
YAML11_BOOLEANS = frozenset(
    ['yes', 'Yes', 'YES', 'no', 'No', 'NO', 'on', 'On', 'ON', 'off', 'Off', 'OFF'])


class YamlBooleanError(ValueError):
    """A schema's YAML text holds a plain scalar the two YAML versions read differently."""


def check_yaml_booleans(content: str) -> None:
    """PS-575: reject a plain (unquoted) scalar whose text is a YAML 1.1 boolean, found in
    the event stream before any value is constructed, so a YAML 1.1 and a YAML 1.2 loader
    agree. Applies where YAML text is loaded; a mapping already built is not checked."""
    import yaml
    try:
        for event in yaml.parse(content):
            if (isinstance(event, yaml.ScalarEvent) and event.style is None
                    and event.value in YAML11_BOOLEANS):
                raise YamlBooleanError(
                    f"Line {event.start_mark.line + 1}: the unquoted value '{event.value}' "
                    f"is a boolean in YAML 1.1 and a string in YAML 1.2; quote it "
                    f"(\"{event.value}\") or write true or false (PS-575)")
    except yaml.YAMLError:
        # Not well-formed YAML: the loader that follows reports it.
        return


def _cli(argv: List[str]) -> int:
    """``decode <schema.yaml> <hex> [--fport N]``: print the result as JSON.

    The documentation had shown this command for as long as it existed while the
    script only ran its demo, so every documented invocation printed the demo's
    output and ignored its arguments.
    """
    import argparse
    import json

    import yaml

    parser = argparse.ArgumentParser(
        prog='schema_interpreter.py',
        description='Decode a payload with a Payload Schema (reference interpreter).')
    sub = parser.add_subparsers(dest='command', required=True)
    dec = sub.add_parser('decode', help='decode a hex payload')
    dec.add_argument('schema', help='schema YAML or JSON file')
    dec.add_argument('payload', help='payload as hex; spaces are ignored')
    dec.add_argument('--fport', type=int, default=None, help='LoRaWAN FPort')
    args = parser.parse_args(argv)

    with open(args.schema, encoding='utf-8') as handle:
        text = handle.read()
    try:
        check_yaml_booleans(text)
    except YamlBooleanError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    schema = yaml.safe_load(text)
    payload = bytes.fromhex(args.payload.replace(' ', ''))
    result = SchemaInterpreter(schema).decode(payload, fPort=args.fport)
    print(json.dumps({
        'success': result.success,
        'data': result.data,
        'errors': result.errors,
        'warnings': result.warnings,
    }, indent=2, default=lambda v: v.hex() if isinstance(v, (bytes, bytearray))
       else str(v)))
    return 0 if result.success else 1


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        sys.exit(_cli(sys.argv[1:]))

if __name__ == '__main__':
    # Demo
    print("=== Schema Interpreter Demo ===\n")
    
    schema = {
        'name': 'env_sensor',
        'endian': 'big',
        'fields': [
            {'name': 'temperature', 'type': 's16', 'mult': 0.01, 'unit': '°C',
             'semantic': {'ipso': 3303}},
            {'name': 'humidity', 'type': 'u8', 'mult': 0.5, 'unit': '%RH',
             'semantic': {'ipso': 3304}},
            {'name': 'battery_mv', 'type': 'u16', 'unit': 'mV',
             'semantic': {'ipso': 3316}},
            {'name': 'status', 'type': 'u8'},
        ]
    }
    
    # Sample payload: temp=23.45°C, humidity=65%, battery=3300mV, status=0
    # temp: 2345 (0x0929), hum: 130 (0x82), batt: 3300 (0x0CE4), status: 0
    payload = bytes([0x09, 0x29, 0x82, 0x0C, 0xE4, 0x00])
    
    interpreter = SchemaInterpreter(schema)
    
    print(f"Schema: {schema['name']}")
    print(f"Payload: {payload.hex().upper()}")
    print(f"Payload length: {len(payload)} bytes\n")
    
    result = interpreter.decode(payload)
    print("Decoded:")
    for k, v in result.data.items():
        print(f"  {k}: {v}")
    
    print(f"\nBytes consumed: {result.bytes_consumed}")
    
    # Semantic outputs
    print("\n--- IPSO Format ---")
    ipso = interpreter.get_semantic_output(result.data, 'ipso')
    for obj_id, obj in ipso.items():
        if isinstance(obj, dict):
            print(f"  /{obj_id}: {obj}")
        else:
            print(f"  {obj_id}: {obj}")
    
    print("\n--- SenML Format ---")
    senml = interpreter.get_semantic_output(result.data, 'senml')
    for record in senml:
        print(f"  {record}")
    
    # Round-trip test
    print("\n--- Encode Round-Trip ---")
    encoded = interpreter.encode(result.data)
    print(f"Original:  {payload.hex().upper()}")
    print(f"Encoded:   {encoded.payload.hex().upper()}")
    print(f"Match: {payload == encoded.payload}")
