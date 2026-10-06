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

import struct
import re
import json
import math
from dataclasses import dataclass, field
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


def write_word_ordered(field_type, value):
    """The inverse of read_word_ordered."""
    layout, kind = WORD_ORDERED_TYPES[field_type]
    if kind == 'f':
        word = int.from_bytes(struct.pack('>f', float(value)), 'big')
    else:
        word = int(value)
        if word < 0:
            word += 0x100000000
        word &= 0xFFFFFFFF
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


def integer_range(field_type: str):
    """Inclusive (low, high) a field of this type can hold, or None if not an integer."""
    info = INTEGER_TYPE_INFO.get(str(field_type))
    if info is None:
        return None
    size, signed = info
    if signed:
        return -(1 << (8 * size - 1)), (1 << (8 * size - 1)) - 1
    return 0, (1 << (8 * size)) - 1


def encode_length(field_def, natural: int) -> int:
    """The byte count to write for a variable-length field.

    `length: remaining` has no fixed count when encoding (PS-014) - the value supplies
    it. Slicing with the word itself raised "slice indices must be integers", which is
    how radio-bridge's stored downlink failed to re-encode.
    """
    raw = field_def.get('length', natural)
    if isinstance(raw, str):
        return max(0, int(natural))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return max(0, int(natural))


def resolve_length(field_def, buf, pos, default=1):
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
        if text.startswith('$'):
            # The specification also allows a `$variable` reference here. No
            # implementation has it, and `int()` would fail with "invalid literal for
            # int() with base 10: '$len'", which does not say that. `repeat` supports
            # the reference on its own `byte_length` key if that is what was meant.
            raise ValueError(
                f"length: {raw} - a $variable reference is not implemented for "
                "'length'; use an integer or the keyword 'remaining'"
            )
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


_FIXED_TYPE_SIZES = {
    'u8': 1, 'u16': 2, 'u24': 3, 'u32': 4, 'u64': 8,
    's8': 1, 's16': 2, 's24': 3, 's32': 4, 's64': 8,
    'u32le16': 4, 's32le16': 4, 'f32le16': 4,
    'u32be16le': 4, 's32be16le': 4, 'f32be16le': 4, 'f16': 2,
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
        elif ftype in COMPUTED_TYPES or (ftype == 'string' and 'value' in field):
            continue
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


def byte_group_member_bits(member):
    """The bits a byte_group member covers, counted from the group's first bit, or None.

    A range's bits are numbered within its own base (PS-058: 0 is the least significant),
    so they are mapped onto the group's bit string - most significant bit of the first
    byte first - before members of different widths can be compared.
    """
    if not isinstance(member, dict):
        return None
    ftype = str(member.get('type', ''))
    match = _BIT_RANGE.match(ftype)
    if match:
        width, start, end = (int(g) for g in match.groups())
    elif ftype == 'bool':
        width, start = 8, int(member.get('bit', 0))
        end = start
    else:
        return None
    return {width - 1 - bit for bit in range(start, end + 1)}


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
    from the same bits; encoding then OR-ed two values into them.
    """
    seen = []
    for member in members or []:
        bits = byte_group_member_bits(member)
        if not bits:
            continue
        for name, other in seen:
            if bits & other:
                raise ValueError(
                    f"byte_group members '{name}' and '{member.get('name', '?')}' "
                    f"overlap (PS-397)")
        seen.append((member.get('name', '?'), bits))


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


def is_literal(field_def):
    """A `string` or `number` field declaring `value`: a constant read from no bytes (PS-357)."""
    return (isinstance(field_def, dict) and field_def.get('type') in ('string', 'number')
            and 'value' in field_def)


LITERAL_FORBIDDEN = ('ref', 'polynomial', 'compute', 'lookup', 'transform', 'mult', 'div', 'add')


def literal_errors(field_def):
    """PS-358: a literal's value matches its type, and it carries no arithmetic."""
    if not is_literal(field_def):
        return []
    name, value, ftype = field_def.get('name', '?'), field_def['value'], field_def['type']
    errors = []
    if ftype == 'string' and not isinstance(value, str):
        errors.append(f"Field '{name}': a string literal's value must be a string (PS-358)")
    if ftype == 'number' and (isinstance(value, bool) or not isinstance(value, (int, float))):
        errors.append(f"Field '{name}': a number literal's value must be a number (PS-358)")
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

    `ties` is `even` (the default) or `away`, from zero. Decimal(float) is the exact
    binary value, so a value stored just below a tie rounds down as it should, and an
    exact tie such as 78.125 is recognised as one - rounding value * 10**d instead would
    invent ties. `away` exists for vendor decoders that use JavaScript's toFixed.
    """
    from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
    if ties not in ('even', 'away'):
        raise ValueError(f"round `ties` must be 'even' or 'away', got {ties!r} (PS-390)")
    if isinstance(decimals, bool) or not isinstance(decimals, int) or decimals < 0:
        raise ValueError(
            f"round `decimals` must be a non-negative integer, got {decimals!r} (PS-390)")
    if value is OMITTED or isinstance(value, bool):
        return value
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return value
    mode = ROUND_HALF_EVEN if ties == 'even' else ROUND_HALF_UP
    rounded = float(Decimal(value).quantize(Decimal(1).scaleb(-decimals), rounding=mode))
    return 0.0 if rounded == 0 else rounded


class LookupIndexError(ValueError):
    """An out-of-bounds index into a sequence ``lookup`` (PS-105).

    Every implementation used to report the raw index instead, silently, so a payload
    that did not match its schema decoded as though it did.
    """


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


def _repeat_member_ref_errors(repeat, member, where):
    """PS-373, PS-374: a per-element unit, IPSO instance or SenML name, and what it names."""
    errors = []
    by_name = {f.get('name'): f for f in repeat.get('fields') or [] if isinstance(f, dict)}
    index = repeat.get('index')
    refs = []
    if isinstance(member.get('unit'), str) and member['unit'].startswith('$'):
        refs.append(('unit', member['unit'][1:]))
    ipso = member.get('ipso')
    if isinstance(ipso, dict) and isinstance(ipso.get('instance'), str):
        if ipso['instance'].startswith('$'):
            refs.append(('ipso.instance', ipso['instance'][1:]))
    senml = member.get('senml')
    if isinstance(senml, dict) and isinstance(senml.get('name'), str):
        refs.extend(('senml.name', name) for name in re.findall(r'\$\{([^}]+)\}', senml['name']))
    for key, name in refs:
        if name == index:
            if not (isinstance(repeat.get('count'), int) or 'max' in repeat):
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
            members = f.get('fields') or []
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
                    and any(op in present_if for op in ('gt', 'gte', 'lt', 'lte', 'eq', 'ne'))):
                errors.append(f"{at}: present_if must be one guard condition, "
                              f"{{field: $name, <op>: value}} (PS-386)")
            identity = f.get('identity')
            if identity is not None and not (
                    identity == f.get('index')
                    or (isinstance(identity, str) and identity.startswith('$')
                        and identity[1:] in names)):
                errors.append(f"{at}: identity must be the repeat's index or a $ reference "
                              f"to a field of its elements (PS-372)")
            for j, member in enumerate(members):
                if isinstance(member, dict):
                    errors.extend(_repeat_member_ref_errors(
                        f, member, f"{at}.fields[{j}] ({member.get('name', '?')})"))
            errors.extend(iterator_errors(members, tuple(enclosing) + tuple(own), True,
                                          f"{at}.fields"))
            if isinstance(f.get('trailer'), list):
                errors.extend(iterator_errors(f['trailer'], enclosing, False,
                                              f"{at}.trailer"))
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
                    group = case if isinstance(case, list) else (case or {}).get('fields')
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
                walk(value, in_elements or (key == 'fields' and node.get('type') == 'repeat'))
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


def schema_iterator_errors(schema):
    """iterator_errors over the top-level and port field lists, and every tlv's reserve."""
    errors = iterator_errors(schema.get('fields'))
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
    if not isinstance(value, int):
        return value
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


def reverse_lookup(value, lookup):
    """Map a label back to its integer for encoding.

    A string that is no label but matches the mapping's `${value}` default yields the
    value written into it (PS-409); the caller rejects any other string.
    """
    if not lookup:
        return value
    if isinstance(lookup, dict):
        for key, label in lookup.items():
            if key == 'default':
                continue
            if label == value:
                try:
                    return int(key)
                except (TypeError, ValueError):
                    return value
        template = lookup_template(lookup)
        if template is not None and isinstance(value, str):
            recovered = match_lookup_template(template, value)
            if recovered is not None:
                return recovered
        return value
    try:
        return lookup.index(value)
    except (ValueError, AttributeError):
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
        elif 'floor' in stage or 'ceiling' in stage or 'clamp' in stage:
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
    
    def __init__(self, schema: Dict[str, Any]):
        # Every `$ref` is spliced before anything reads the schema (PS-346, PS-347), so
        # a reference works in any field list - a port entry, an object, a repeat, a
        # case - and not only at the top level, which was all this handled. Problems
        # with the references are reported by decode and encode (PS-348).
        self.source_schema = schema
        schema, self._load_errors = expand_refs(schema)
        # PS-358: a malformed literal is a schema error, reported as the references are;
        # so are an `encoding` off an unsigned integer and `match_value` (PS-422, PS-426).
        for field_def in typed_field_dicts(schema):
            self._load_errors.extend(literal_errors(field_def))
            self._load_errors.extend(encoding_errors(field_def))
            self._load_errors.extend(lookup_template_errors(field_def))
        self._load_errors.extend(timestamp_errors(schema.get('metadata')))
        self._load_errors.extend(schema_iterator_errors(schema))
        self._repeat_only_names = repeat_only_names(schema)
        self.schema = schema
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
            raise ValueError(f"Buffer too short: need {size} bytes at pos {pos}")
        
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
            raise ValueError(f"Buffer too short: need {size} bytes at pos {pos}")
        
        data = buf[pos:pos + size]
        fmt = '<f' if self.endian == Endian.LITTLE else '>f'
        if size == 8:
            fmt = '<d' if self.endian == Endian.LITTLE else '>d'
        
        value = struct.unpack(fmt, data)[0]
        return value, pos + size
    
    def _read_float16(self, buf: bytes, pos: int) -> Tuple[float, int]:
        """Read IEEE 754 half-precision float (2 bytes)."""
        if pos + 2 > len(buf):
            raise ValueError(f"Buffer too short: need 2 bytes at pos {pos}")
        
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
            raise ValueError(f"Buffer too short at pos {pos}")

        # The base width is part of the type - `u24[4:23]` means bits 4-23 of a
        # 24-bit big-endian value - so read that many bytes before masking. Reading
        # only buf[pos] made every range wider than one byte decode from the first
        # byte alone: rakwireless/qingping declares a 12-bit humidity as u24[0:11]
        # and got 11 where the device means 1320.
        if pos + base_size > len(buf):
            raise ValueError(
                "Buffer too short for %d-bit bitfield at pos %d" % (base_size * 8, pos)
            )
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
    _ENDIAN_OPAQUE_TYPES = ('object', 'repeat', 'match', 'switch')

    def _decode_field(self, field_def: Dict[str, Any], buf: bytes,
                      pos: int) -> Tuple[Any, int]:
        """Decode a single field, honouring a field-level `endian:` override (PS-053).

        The override was parsed by Go, Java, C# and the C interpreter and silently
        ignored here: every read site consults ``self.endian``, which is schema-level,
        so a field declaring `endian: little` under a big-endian schema decoded
        big-endian and nothing reported it. The reference implementation was the only
        one of the five that got it wrong, and it is the surface `td-tools` imports.
        """
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
                raise ValueError(f"Buffer too short: need 4 bytes at pos {pos}")
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
                value = self._decode_encoding(value, encoding, size)
            return value, new_pos
        
        # Nibble-decimal types: upper nibble = whole, lower nibble = tenths (PS-330).
        # `UDec`/`SDec` are not spellings of them (PS-331).
        if field_type == 'udec':
            if pos >= len(buf):
                raise ValueError("Buffer too short for udec")
            byte = buf[pos]
            value = (byte >> 4) + (byte & 0x0F) * 0.1
            return value, pos + 1
        
        if field_type == 'sdec':
            if pos >= len(buf):
                raise ValueError("Buffer too short for sdec")
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
        
        if field_type == 'f32':
            return self._read_float(buf, pos, 4)
        
        if field_type == 'f64':
            return self._read_float(buf, pos, 8)
        
        if field_type == 'bool':
            bit = field_def.get('bit', 0)
            if pos >= len(buf):
                raise ValueError("Buffer too short for bool")
            value = bool((buf[pos] >> bit) & 1)
            # Bool doesn't advance position by default
            if consume:
                return value, pos + consume
            return value, pos
        
        if field_type == 'bytes':
            length = resolve_length(field_def, buf, pos)
            if pos + length > len(buf):
                raise ValueError("Buffer too short for bytes")
            return format_bytes(field_def, buf[pos:pos + length]), pos + length
        
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
        
        if field_type == 'ascii':
            length = resolve_length(field_def, buf, pos)
            if pos + length > len(buf):
                raise ValueError("Buffer too short for ascii")
            value = buf[pos:pos + length].decode('ascii', errors='replace').rstrip('\x00')
            return value, pos + length
        
        # `hex:upper` is a `bytes` format (PS-079), not a type (CR-2026-037), so it
        # falls through to the unknown-type error below.
        if field_type == 'hex':
            length = resolve_length(field_def, buf, pos)
            if pos + length > len(buf):
                raise ValueError("Buffer too short for hex")
            # PS-074: `hex` output MUST be lowercase without separators. This
            # emitted uppercase, so it disagreed with the specification, with the
            # Go and Java interpreters, and with every vendor decoder. Uppercase
            # is `type: bytes` with `format: hex:upper`.
            return buf[pos:pos + length].hex(), pos + length
        
        if field_type == 'base64':
            import base64 as b64
            length = resolve_length(field_def, buf, pos)
            if pos + length > len(buf):
                raise ValueError("Buffer too short for base64")
            value = b64.b64encode(buf[pos:pos + length]).decode('ascii')
            return value, pos + length
        
        if field_type == 'skip':
            # Padding/reserved bytes - advance position but don't output
            length = resolve_length(field_def, buf, pos)
            return None, pos + length
        
        if field_type == 'object':
            # Nested object
            nested_fields = field_def.get('fields', [])
            nested_result = {}
            for nested_field in nested_fields:
                name = nested_field.get('name', 'unknown')
                value, pos = self._decode_field(nested_field, buf, pos)
                value = self._apply_modifiers(value, nested_field)
                nested_result[name] = value
            return nested_result, pos
        
        if field_type == 'repeat':
            # Repeated/array field
            return self._decode_repeat(field_def, buf, pos)
        
        if field_type == 'enum':
            # Enum type: decode base type then map to string
            return self._decode_enum(field_def, buf, pos)
        
        if field_type == 'match':
            # Conditional decoding
            return self._decode_match(field_def, buf, pos)
        
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
        raw_value, new_pos = self._decode_field(base_field, buf, pos)
        
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
                return f"unknown({raw_value})", new_pos
        elif isinstance(values, list):
            if 0 <= raw_value < len(values):
                return enum_label(values[raw_value]), new_pos
            elif 'default' in field_def:
                return field_def['default'], new_pos
            else:
                return f"unknown({raw_value})", new_pos
        
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
        nested_fields = field_def.get('fields', [])
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
            saved_current = self._current_data
            saved_names = getattr(self, '_element_names', None)
            self._element_names = {f.get('name') for f in nested_fields if isinstance(f, dict)}
            if field_def.get('index'):
                self._variables[field_def['index']] = iterations       # PS-366
            self._variables.update(carry_state)                      # PS-379
            scratch = DecodeResult(data={}, bytes_consumed=0)
            self._current_data = scratch.data
            try:
                new_pos = self._decode_field_list(nested_fields, region, at, scratch)
                if scratch.errors:
                    raise ValueError(scratch.errors[0])
                for carried in carry_state:
                    if carried in self._variables:
                        carry_state[carried] = self._variables[carried]
                keep = (not field_def.get('present_if')
                        or self._evaluate_guard({'when': [field_def['present_if']]})[0])
            finally:
                # PS-368: an element's names do not outlive it.
                self._variables = outer
                self._current_data = saved_current
                self._element_names = saved_names
            iterations += 1
            if keep:                                                 # PS-386
                result.append(scratch.data)
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
                    raise ValueError(ragged_tail_message(
                        field_def, region_end - pos, element_size, pos))
                start_pos = pos
                try:
                    pos = element(pos, region)
                except ValueError as exc:
                    if 'too short' not in str(exc).lower():
                        raise
                    raise ValueError(ragged_tail_message(
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
        Decode conditional/match field.
        
        Supports both legacy syntax and Option B syntax:
        
        Legacy:
          type: match, on: field_name, cases: [{case: 1, fields: [...]}, ...]
        
        Option B:
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
        # Option B syntax: match_def is the nested dict under 'match:'
        match_def = field_def.get('match', {})
        if isinstance(match_def, dict) and match_def:
            return self._decode_match_option_b(match_def, buf, pos)
        
        # Legacy syntax
        on_field = field_def.get('on')
        cases = field_def.get('cases', [])
        default = field_def.get('default', 'error')
        
        # Get the discriminator value from previously decoded fields
        discriminator = None
        if on_field and hasattr(self, '_current_data') and self._current_data:
            # Handle $ prefix for variable reference
            field_name = on_field.lstrip('$')
            discriminator = self._current_data.get(field_name)
            # Also check variables store
            if discriminator is None and hasattr(self, '_variables'):
                discriminator = self._variables.get(field_name)
        
        if discriminator is None:
            # Fallback: peek at next byte as discriminator
            if pos < len(buf):
                discriminator = buf[pos]
        
        # Find matching case
        matched_case = None
        for case in cases:
            case_pattern = case.get('case')
            if self._match_case_pattern(discriminator, case_pattern):
                matched_case = case
                break
        
        # Handle no match
        if matched_case is None:
            if default == 'error':
                raise ValueError(f"No matching case for value {discriminator}")
            elif default == 'skip':
                return {}, pos
            elif isinstance(default, dict) and 'fields' in default:
                # Default case with fields
                matched_case = default
            else:
                return {}, pos
        
        # Decode matched case fields
        result = {}
        case_fields = matched_case.get('fields', [])
        for cf in case_fields:
            name = cf.get('name', 'unknown')
            if name.startswith('_'):
                # Internal field - decode and bind, but don't output
                value, pos = self._decode_field(cf, buf, pos)
                self._bind_internal(cf, name, value)
            else:
                value, pos = self._decode_field(cf, buf, pos)
                value = self._apply_modifiers(value, cf)
                result[name] = value
        
        return result, pos
    
    def _bind_internal(self, field_def: Dict[str, Any], name: str, value: Any) -> None:
        """Bind an internal (`_`-prefixed) field's value without reporting it.

        The value is bound after modifiers, as an ordinary field's is, under its own
        name and under its `var:` if it declares one. A skip, or a lookup with no
        entry, binds nothing.
        """
        if value is None:
            return
        if field_def.get('formula'):
            value = self._evaluate_formula(field_def['formula'], value)
        else:
            value = self._apply_modifiers(value, field_def)
        if value is OMITTED:
            return
        if not hasattr(self, '_variables'):
            self._variables = {}
        self._variables[name] = value
        if field_def.get('var'):
            self._variables[field_def['var']] = value

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
        if ('field' in match_def) == ('length' in match_def):
            raise ValueError(
                "a match must declare exactly one of 'field' and 'length' (PS-399)")
        field_ref = match_def.get('field')
        length = match_def.get('length')
        match_name = match_def.get('name')
        match_var = match_def.get('var')
        cases = match_def.get('cases', {})
        default = match_def.get('default', 'error')
        
        discriminator = None
        
        if field_ref:
            # Variable-based: look up stored variable
            var_name = field_ref.lstrip('$')
            if hasattr(self, '_variables') and var_name in self._variables:
                discriminator = self._variables[var_name]
            elif hasattr(self, '_current_data') and self._current_data:
                discriminator = self._current_data.get(var_name)
        elif length is not None:
            # Inline: read bytes from payload
            if pos + length > len(buf):
                raise ValueError(f"Buffer too short for match: need {length} bytes at pos {pos}")
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
        
        for case_key, case_fields in cases.items():
            if case_key == 'default':
                default_fields = case_fields
                continue
            if self._match_case_pattern(discriminator, case_key):
                matched_fields = case_fields
                break
        
        if matched_fields is None:
            if default_fields is not None:
                matched_fields = default_fields
            elif default == 'error':
                raise ValueError(f"No matching case for value {discriminator}")
            elif default == 'skip':
                return result, pos
            elif isinstance(default, list):
                matched_fields = default
            else:
                return result, pos
        
        # Decode matched case fields (handling nested Option B constructs)
        for cf in matched_fields:
            # Option B: nested match: inside case
            if 'match' in cf and not cf.get('type'):
                nested_result, pos = self._decode_match(cf, buf, pos)
                result.update(nested_result)
                if hasattr(self, '_current_data'):
                    self._current_data.update(nested_result)
                continue
            
            if 'object' in cf and not cf.get('type'):
                raise ValueError(object_key_withdrawn(cf))
            
            name = cf.get('name', 'unknown')
            if name.startswith('_'):
                value, pos = self._decode_field(cf, buf, pos)
                self._bind_internal(cf, name, value)
            else:
                value, pos = self._decode_field(cf, buf, pos)
                value = self._apply_modifiers(value, cf)
                result[name] = value
                if hasattr(self, '_current_data'):
                    self._current_data[name] = value
                # Check for var on nested fields
                if cf.get('var'):
                    if not hasattr(self, '_variables'):
                        self._variables = {}
                    self._variables[cf['var']] = value
        
        return result, pos
    
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
            try:
                parts = pattern.split('..')
                start = int(parts[0])
                end = int(parts[1])
                return start <= value <= end
            except (ValueError, IndexError):
                return False
        
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
        
        if field_name not in self._variables:
            raise ValueError(f"Flagged field reference not found: {field_name}")
        flags = int(self._variables[field_name])
        
        result = {}
        for group in groups:
            bit = group.get('bit', 0)
            is_present = (flags >> bit) & 1
            if is_present:
                for gf in group.get('fields', []):
                    gf_name = gf.get('name', 'unknown')
                    gf_type = gf.get('type', 'u8')
                    
                    # A leading underscore marks an internal field: it becomes a
                    # variable that later fields can reference, but is not reported.
                    # Every other construct already did this; flagged did not, so an
                    # intermediate used to combine two words appeared in the output.
                    internal = gf_name.startswith('_')

                    # Handle computed fields (type: number)
                    if gf_type in COMPUTED_TYPES:
                        value = self._decode_computed_field(gf)
                        if value is not None:
                            if not internal:
                                result[gf_name] = value
                            self._variables[gf_name] = value
                        continue

                    value, pos = self._decode_field(gf, buf, pos)
                    if value is not None:
                        if gf.get('formula'):
                            import warnings
                            warnings.warn(f"Field '{gf_name}': 'formula' is deprecated.", DeprecationWarning)
                            value = self._evaluate_formula(gf['formula'], value)
                        else:
                            value = self._apply_modifiers(value, gf)
                        if not internal:
                            result[gf_name] = value
                        self._variables[gf_name] = value
        
        return result, pos
    
    def _decode_computed_field(self, field_def: Dict[str, Any]) -> Optional[float]:
        """Decode a computed field (type: number) - ref, polynomial, compute, guard."""
        value = None
        
        # Deprecated: formula field
        if field_def.get('formula'):
            import warnings
            warnings.warn(f"Field '{field_def.get('name', 'unknown')}': 'formula' is deprecated.", DeprecationWarning)
            value = self._evaluate_formula(field_def['formula'], None)
        
        # ref + polynomial/transform
        elif field_def.get('ref'):
            if 'guard' in field_def:
                passed, fallback = self._evaluate_guard(field_def['guard'])
                if not passed:
                    value = fallback if fallback is not None else float('nan')
                else:
                    value = self._resolve_ref_value(field_def)
            else:
                value = self._resolve_ref_value(field_def)
        
        # compute (cross-field binary operation)
        elif field_def.get('compute'):
            if 'guard' in field_def:
                passed, fallback = self._evaluate_guard(field_def['guard'])
                if not passed:
                    value = fallback if fallback is not None else float('nan')
                else:
                    value = self._evaluate_compute(field_def['compute'])
            else:
                value = self._evaluate_compute(field_def['compute'])
            
            # Apply transform after compute. An omitted compute short-circuits:
            # there is no value to transform, and float(OMITTED) would raise.
            if value is OMITTED:
                return OMITTED
            if value is not None and 'transform' in field_def:
                value = self._apply_transform(float(value), field_def['transform'])
        
        # Literal value
        elif 'value' in field_def:
            value = field_def['value']
        
        return value
    
    def _decode_bitfield_string(self, field_def: Dict[str, Any], buf: bytes, pos: int) -> Tuple[str, int]:
        """Decode a bitfield_string field (e.g., firmware version)."""
        length = field_def.get('length', 2)
        parts = field_def.get('parts', [])
        delimiter = field_def.get('delimiter', '.')
        prefix = field_def.get('prefix', '')
        
        if pos + length > len(buf):
            raise ValueError(f"Buffer too short for bitfield_string at pos {pos}")
        
        data = buf[pos:pos + length]
        if self.endian == Endian.LITTLE:
            int_val = int.from_bytes(data, 'little')
        else:
            int_val = int.from_bytes(data, 'big')
        pos += length
        
        part_strs = []
        for part in parts:
            bit_off = part[0]
            bit_len = part[1]
            fmt = part[2] if len(part) >= 3 else 'decimal'
            mask = (1 << bit_len) - 1
            raw = (int_val >> bit_off) & mask
            # PS-430 (CR-2026-066): `hex` lower case, `hex:upper` upper case, `decimal`
            # the default; any other part format is rejected rather than read as decimal.
            if fmt == 'hex':
                part_strs.append(format(raw, 'x'))
            elif fmt == 'hex:upper':
                part_strs.append(format(raw, 'X'))
            elif fmt == 'decimal':
                part_strs.append(str(raw))
            else:
                raise ValueError(f"bitfield_string part format {fmt!r} is not one of "
                                 f"decimal, hex, hex:upper (PS-430)")
        
        return prefix + delimiter.join(part_strs), pos
    
    def _evaluate_encode_formula(self, formula: str, value: float) -> float:
        """
        Phase 3: Evaluate an encode_formula for custom encoding.
        
        encode_formula is the inverse of formula, used during encoding.
        Variable 'x' or 'value' refers to the application-level value.
        """
        import math as _math
        
        expr = formula
        # Replace x/value with actual value
        expr = re.sub(r'\bx\b', str(value), expr)
        expr = re.sub(r'\bvalue\b', str(value), expr)
        
        try:
            result = eval(expr, {"__builtins__": {}, "_math": _math,
                                 "abs": abs, "min": min, "max": max, "int": int, "round": round})
            return float(result)
        except Exception as e:
            raise ValueError(f"encode_formula evaluation failed: '{formula}' -> '{expr}': {e}")
    
    def _evaluate_formula(self, formula: str, x=None) -> float:
        """Evaluate a formula with variable substitution and math functions."""
        import math as _math
        
        expr = formula
        
        # Substitute $field_name references
        expr = re.sub(r'\$([a-zA-Z_][a-zA-Z0-9_]*)', 
                      lambda m: str(self._ref(m.group(1))), expr)
        
        # Replace standalone 'x' with raw value
        if x is not None:
            expr = re.sub(r'\bx\b', str(x), expr)
        
        # Replace function names
        expr = re.sub(r'\bpow\s*\(', '_math.pow(', expr)
        expr = re.sub(r'\babs\s*\(', 'abs(', expr)
        expr = re.sub(r'\bsqrt\s*\(', '_math.sqrt(', expr)
        expr = re.sub(r'\bmin\s*\(', 'min(', expr)
        expr = re.sub(r'\bmax\s*\(', 'max(', expr)
        
        # Replace 'and'/'or'
        expr = re.sub(r'\band\b', 'and', expr)
        expr = re.sub(r'\bor\b', 'or', expr)
        
        # Convert C-style ternary (cond ? true_val : false_val) to Python (true_val if cond else false_val)
        ternary_match = re.match(r'^(.+?)\s*\?\s*(.+?)\s*:\s*(.+)$', expr)
        if ternary_match:
            cond, true_val, false_val = ternary_match.groups()
            expr = f"({true_val}) if ({cond}) else ({false_val})"
        
        try:
            result = eval(expr, {"__builtins__": {}, "_math": _math, 
                                 "abs": abs, "min": min, "max": max,
                                 "True": True, "False": False})
            return float(result) if isinstance(result, (int, float)) else 0.0
        except Exception as e:
            raise ValueError(f"Formula evaluation failed: '{formula}' -> '{expr}': {e}")
    
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
        
        compute_def: {op: 'add'|'sub'|'mul'|'div'|'mod'|'idiv', a: '$field'|literal, b: '$field'|literal}
        """
        op = compute_def.get('op', 'add')
        a_spec = compute_def.get('a', 0)
        b_spec = compute_def.get('b', 0)
        
        # Resolve operands
        def resolve_operand(spec):
            if isinstance(spec, str) and spec.startswith('$'):
                field_name = spec[1:]
                return float(self._ref(field_name))
            return float(spec)
        
        a = resolve_operand(a_spec)
        b = resolve_operand(b_spec)
        
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
        else:
            raise ValueError(f"Unknown compute op: {op}")
    
    def _evaluate_guard(self, guard_def: Dict[str, Any]) -> Tuple[bool, Any]:
        """
        Evaluate guard conditions.
        
        guard_def: {when: [{field: '$x', gt: 0}, ...], else: fallback}
        
        Returns: (conditions_passed, fallback_value)
        """
        when_conditions = guard_def.get('when', [])
        else_value = guard_def.get('else', None)
        
        for condition in when_conditions:
            field_ref = condition.get('field', '')
            if isinstance(field_ref, str) and field_ref.startswith('$'):
                field_name = field_ref[1:]
                field_value = float(self._ref(field_name))
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
            
            if not passed:
                return (False, else_value)
        
        return (True, else_value)
    
    def _resolve_ref_value(self, field_def: Dict[str, Any]) -> float:
        """
        Resolve a ref field and apply modifiers/polynomial/transform.
        
        field_def must have 'ref' key.
        """
        ref_field = field_def['ref']
        if isinstance(ref_field, str) and ref_field.startswith('$'):
            ref_name = ref_field[1:]
            value = float(self._ref(ref_name))
        else:
            value = float(ref_field)
        
        # Apply polynomial if present
        if 'polynomial' in field_def:
            coeffs = field_def['polynomial']
            if isinstance(coeffs, list) and len(coeffs) >= 2:
                value = self._evaluate_polynomial(coeffs, value)
        
        value = apply_canonical_modifiers(value, field_def)

        # Apply transform array if present
        if 'transform' in field_def:
            value = self._apply_transform(value, field_def['transform'])
        
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
            if reference in data:
                value = data[reference]
            else:
                source = self._field_declaring_var(reference)
                if source and source.get('name') in data:
                    value = reverse_lookup(data[source['name']], source.get('lookup'))
                else:
                    missing.append(reference)
                    return ''
            if isinstance(value, float) and value == int(value):
                value = int(value)
            return str(value)

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
                value = self._variables[reference]
                if isinstance(value, float) and value == int(value):
                    value = int(value)
                return str(value)
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
            elif 'clamp' in op:
                bounds = op['clamp']
                if isinstance(bounds, list) and len(bounds) >= 2:
                    value = max(float(bounds[0]), min(float(bounds[1]), value))
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
            group_size = byte_group.get('size', 1)
        else:
            # Format 1: list of fields directly
            group_fields = byte_group
            group_size = field_def.get('size', 1)
        
        if not group_fields:
            return pos
        check_byte_group_overlap(group_fields)
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
            raise ValueError(f"Buffer too short for a {group_size}-byte byte_group at pos {pos}")
        group_value = int.from_bytes(buf[pos:pos + group_size],
                                     'little' if self.endian == Endian.LITTLE else 'big')
        for gf in group_fields:
            name = gf.get('name', 'unknown')
            
            # Force consume: 0 for all but track internally
            gf_copy = dict(gf)
            gf_copy['consume'] = 0
            
            try:
                match = _BIT_RANGE.match(str(gf.get('type', '')))
                if match:
                    start, end = int(match.group(2)), int(match.group(3))
                    width = end - start + 1
                    value = (group_value >> start) & ((1 << width) - 1)
                    if str(gf['type']).startswith('s') and value >= 1 << (width - 1):
                        value -= 1 << width
                elif gf.get('type') == 'bool':
                    value = bool((group_value >> int(gf.get('bit', 0))) & 1)
                else:
                    value, _ = self._decode_field(gf_copy, buf, pos)
                value = self._apply_modifiers(value, gf)
                if not name.startswith('_'):
                    result.data[name] = value
                # Add to variables so field can be referenced by compute/formula
                self._variables[name] = value
            except Exception as e:
                result.errors.append(f"Error in byte_group field {name}: {e}")
    
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
        
        while pos < len(buf):
            # Where this entry begins, so PS-302 can count the bytes abandoned from the
            # unknown tag itself rather than from after it.
            entry_start = pos
            # Read tag
            if pos + tag_size > len(buf):
                break
            
            if tag_fields and tag_key:
                # Composite tag: read sub-fields
                tag_parts = {}
                tag_start = pos
                for tf in tag_fields:
                    tf_name = tf.get('name', 'unknown')
                    tf_value, pos = self._decode_field(tf, buf, pos)
                    tag_parts[tf_name] = tf_value
                
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
                if pos + length_size > len(buf):
                    break
                if length_size == 1:
                    data_length = buf[pos]
                elif length_size == 2:
                    if self.endian == Endian.LITTLE:
                        data_length = buf[pos] | (buf[pos + 1] << 8)
                    else:
                        data_length = (buf[pos] << 8) | buf[pos + 1]
                pos += length_size
            
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
                break
            
            # Decode fields for this tag
            tag_result = {}
            for cf in matched_fields:
                cf_name = cf.get('name', 'unknown')
                cf_type = cf.get('type', 'u8')

                # A byte_group carries no name and no type of its own - its names
                # live in its own `fields`, sharing one byte. The generic path
                # below therefore read that shared byte as a `u8` called
                # "unknown" and never descended into the bit ranges, so
                # hbi/mla20's case 0x20 decoded to {"unknown": 81} and its
                # charger_status and device_status were never reported at all.
                # Go descends into it; this brings Python onto the same result.
                #
                # The group is decoded into a scratch result so its fields land
                # in tag_result rather than jumping straight to the payload-level
                # output, which would bypass the merge/channels handling below.
                if 'byte_group' in cf and not cf.get('type'):
                    group = DecodeResult(data={}, bytes_consumed=0)
                    pos = self._decode_byte_group(cf, buf, pos, group)
                    tag_result.update(group.data)
                    # `outer`, not `result`: this method already binds a local
                    # `result` dict for the channel output further down.
                    if outer is not None:
                        outer.errors.extend(group.errors)
                        outer.warnings.extend(group.warnings)
                    continue

                # Handle bitfield_string inside TLV cases
                if cf_type == 'bitfield_string':
                    value, pos = self._decode_bitfield_string(cf, buf, pos)
                    if not cf_name.startswith('_'):
                        tag_result[cf_name] = value
                    continue
                
                value, pos = self._decode_field(cf, buf, pos)
                if value is not None:
                    value = self._apply_modifiers(value, cf)
                    if not cf_name.startswith('_'):
                        tag_result[cf_name] = value
            
            if merge:
                for k, v in tag_result.items():
                    if k in result:
                        # Repeated tag -> collect into array
                        if isinstance(result[k], list):
                            result[k].append(v)
                        else:
                            result[k] = [result[k], v]
                    else:
                        result[k] = v
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
        
        # Formula takes precedence - use sandboxed evaluator (DEPRECATED)
        formula = field_def.get('formula')
        if formula:
            import warnings
            warnings.warn(
                f"Field '{field_def.get('name', 'unknown')}': 'formula' is deprecated. "
                "Use 'polynomial', 'compute', or 'transform' instead.",
                DeprecationWarning
            )
            try:
                value = self._evaluate_formula(formula, x=value)
            except ValueError:
                pass  # Keep original value on formula error
            return value
        
        value = apply_canonical_modifiers(value, field_def)

        # Apply transform array (new declarative constructs)
        transform = field_def.get('transform')
        if transform and isinstance(transform, list) and value is not OMITTED:
            value = self._apply_transform(float(value), transform)
        
        # Apply lookup table
        value = apply_lookup(value, field_def.get('lookup'))
        
        return value
    
    def decode(self, payload: bytes, fPort: int = None, input_metadata: Dict[str, Any] = None,
               direction: str = None) -> DecodeResult:
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
        # Variable storage for Option B match references
        self._variables = {}
        
        pos = 0
        fields = self._resolve_fields(fPort)
        
        pos = self._decode_field_list(fields, payload, 0, result)
        
        result.bytes_consumed = pos
        
        # Metadata enrichment
        metadata_def = self.schema.get('metadata')
        if metadata_def and input_metadata is not None:
            self._enrich_metadata(result.data, metadata_def, input_metadata,
                                  result.warnings)
        
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
            # Handle $ref - inline the referenced definition
            if '$ref' in field_def:
                try:
                    ref_def = self._resolve_ref(field_def['$ref'])
                    ref_fields = ref_def.get('fields', [])
                    for rf in ref_fields:
                        rf_name = rf.get('name', 'unknown')
                        if not rf_name.startswith('_'):
                            value, pos = self._decode_field(rf, payload, pos)
                            value = self._apply_modifiers(value, rf)
                            if value is not None:
                                result.data[rf_name] = value
                        else:
                            _, pos = self._decode_field(rf, payload, pos)
                except Exception as e:
                    result.errors.append(f"Error resolving $ref: {e}")
                continue
            
            # Handle byte_group construct
            if 'byte_group' in field_def:
                try:
                    pos = self._decode_byte_group(field_def, payload, pos, result)
                except Exception as e:
                    # A schema error such as overlapping members (PS-397) fails the
                    # decode like any other field's error, rather than raising out of it.
                    result.errors.append(f"Error decoding byte_group: {e}")
                    break
                continue
            
            # Option B: match: as top-level key
            if 'match' in field_def and not field_def.get('type'):
                try:
                    match_result, pos = self._decode_match(field_def, payload, pos)
                    result.data.update(match_result)
                except Exception as e:
                    result.errors.append(f"Error in match: {e}")
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
                    result.errors.append(f"Error in tlv: {e}")
                continue
            
            # Phase 2: flagged: construct (bitmask field presence)
            if 'flagged' in field_def and not field_def.get('type'):
                try:
                    flagged_def = field_def['flagged']
                    flagged_result, pos = self._decode_flagged(flagged_def, payload, pos)
                    result.data.update(flagged_result)
                    # Store flagged fields as variables and check valid_range
                    for k, v in flagged_result.items():
                        self._variables[k] = v
                    # Check valid_range for fields in flagged groups
                    for group in flagged_def.get('groups', []):
                        for gf in group.get('fields', []):
                            gf_name = gf.get('name')
                            if gf_name and gf_name in flagged_result and gf.get('valid_range'):
                                quality = self._check_valid_range(flagged_result[gf_name], gf, result)
                                result.quality[gf_name] = quality
                except Exception as e:
                    result.errors.append(f"Error in flagged: {e}")
                continue
            
            name = field_def.get('name', 'unknown')
            field_type = field_def.get('type', 'u8')
            
            # Phase 2: bitfield_string type
            if field_type == 'bitfield_string':
                try:
                    value, pos = self._decode_bitfield_string(field_def, payload, pos)
                    result.data[name] = value
                    self._variables[name] = value
                except Exception as e:
                    result.errors.append(f"Error decoding {name}: {e}")
                continue
            
            # Computed field - supports formula, ref, polynomial, compute, guard.
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
                        # the rest of the payload continues.
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
                    if value is not None:
                        # A leading underscore marks an internal field: it becomes a
                        # variable later fields can reference, but is not reported.
                        # Every other construct checked this; the computed-field path
                        # did not, so mclimate/vicki reported four intermediates
                        # (_motorPosPercent, _motorPosRatio and two high-byte helpers)
                        # in its output.
                        if not name.startswith('_'):
                            result.data[name] = value
                        self._variables[name] = value
                        # Check valid_range for computed fields
                        if field_def.get('valid_range'):
                            quality = self._check_valid_range(value, field_def, result)
                            result.quality[name] = quality
                except Exception as e:
                    result.errors.append(f"Error computing {name}: {e}")
                continue
            
            # Handle match at field level (legacy: no name, type: match)
            if field_type == 'match' and name == 'unknown':
                try:
                    match_result, pos = self._decode_match(field_def, payload, pos)
                    result.data.update(match_result)
                except Exception as e:
                    result.errors.append(f"Error in match: {e}")
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
                    value, pos = self._decode_field(field_def, payload, pos)
                    self._bind_internal(field_def, name, value)
                except Exception as e:
                    result.errors.append(f"Error in internal field: {e}")
                continue
            
            try:
                value, pos = self._decode_field(field_def, payload, pos)
                # Skip type returns None - don't add to output
                if value is not None:
                    # Formula takes precedence over mult/add/div modifiers
                    if field_def.get('formula'):
                        value = self._evaluate_formula(field_def['formula'], value)
                    else:
                        value = self._apply_modifiers(value, field_def)
                    if value is OMITTED:
                        # A lookup with no entry for this value: the device did not
                        # report anything the schema can name, so the field is left
                        # out rather than reported as a raw integer (PS-269).
                        continue
                    output_name = self._resolve_field_name(field_def, name)
                    result.data[output_name] = value
                    # Check valid_range and update quality
                    if field_def.get('valid_range'):
                        quality = self._check_valid_range(value, field_def, result)
                        result.quality[output_name] = quality
                    # Store variable if var: specified (Option B)
                    if field_def.get('var'):
                        self._variables[field_def['var']] = value
                    # Always store by field name (for flagged/formula lookups). The
                    # schema-level name is used here, not the resolved output name,
                    # so $references keep working when name_from is in play.
                    self._variables[name] = value
            except Exception as e:
                result.errors.append(f"Error decoding {name}: {e}")
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
               direction: str = None) -> EncodeResult:
        """
        Encode data dict to payload bytes using schema.
        
        Args:
            data: Dictionary of field values
            fPort: Optional LoRaWAN fPort for port-based schema selection
            direction: Direction the message will travel, 'uplink' or 'downlink'.
                Omit where the caller cannot know it; the check is then skipped and
                PS-292 is not satisfied.
            
        Returns:
            EncodeResult with encoded payload
        """
        result = EncodeResult(payload=b'')
        if self._load_errors:
            result.errors.extend(self._load_errors)
            return result

        # PS-292, the mirror of the decode check: encoding for an entry that disclaims
        # this direction produces bytes the far end will read against different field
        # definitions. Emitting them would put a malformed frame on the air, so nothing
        # is encoded.
        direction_error = self._direction_error(fPort, direction)
        if direction_error:
            result.errors.append(direction_error)
            return result

        output = bytearray()
        
        fields = self._resolve_fields(fPort)
        
        # Pre-scan for flagged constructs to compute flags values
        flags_patches = {}
        for field_def in fields:
            if 'flagged' in field_def:
                flagged_def = field_def['flagged']
                field_name = flagged_def.get('field', '')
                groups = flagged_def.get('groups', [])
                flags = 0
                for group in groups:
                    bit = group.get('bit', 0)
                    group_fields = group.get('fields', [])
                    if any(gf.get('name') and gf['name'] in data for gf in group_fields):
                        flags |= (1 << bit)
                flags_patches[field_name] = flags

        # An internal field that a later `match` dispatches on is not in the data - it
        # is internal - so it used to be written as zero while the match went on to
        # emit the fields of whichever case the data fits. The bytes then disagreed
        # with themselves: 0107 came back as 0007, silently. The case the data fits
        # names the discriminator, so write that.
        internal_patches = self._internal_discriminators(fields, data)

        for _kind, _item in self._bitfield_runs(fields):
            # A run of bit ranges shares one span of bytes, so it is packed once rather
            # than a byte per field (CR-2026-023). The LoRaWAN MHDR's three ranges used to
            # come back as three bytes of unshifted values: `40` encoded as `020000`.
            if _kind == 'bits':
                try:
                    output.extend(self._encode_bitfield_run(_item, data))
                except Exception as e:
                    names = ", ".join(str(f.get('name')) for f in _item)
                    result.errors.append(f"Error encoding bit range(s) {names}: {e}")
                continue
            field_def = _item
            if '$ref' in field_def:
                # Decoding splices the referenced definition's fields in place; encoding
                # never did, so the whole header collapsed to one zero byte -
                # ref-header.yaml re-encoded 01020304 as 000304.
                try:
                    ref_def = self._resolve_ref(field_def['$ref'])
                    output.extend(
                        self._encode_field_list(ref_def.get('fields') or [], data))
                except Exception as e:
                    result.errors.append(f"Error encoding $ref: {e}")
                continue
            
            if 'byte_group' in field_def:
                try:
                    output.extend(self._encode_byte_group(field_def, data))
                except Exception as e:
                    result.errors.append(f"Error encoding byte_group: {e}")
                continue
            
            if 'tlv' in field_def and not field_def.get('type'):
                try:
                    output.extend(self._encode_tlv(field_def, data))
                except Exception as e:
                    result.errors.append(f"Error encoding tlv: {e}")
                continue
            
            if 'match' in field_def and not field_def.get('type'):
                try:
                    output.extend(self._encode_match(field_def, data))
                except Exception as e:
                    result.errors.append(f"Error encoding match: {e}")
                continue
            
            if field_def.get('type') == 'repeat':
                try:
                    output.extend(self._encode_repeat(field_def, data))
                except Exception as e:
                    result.errors.append(
                        f"Error encoding repeat {field_def.get('name')!r}: {e}")
                continue

            if field_def.get('type') == 'object':
                # Encoding had no top-level object case: "Cannot encode type: object".
                nested = data.get(field_def.get('name'))
                try:
                    output.extend(self._encode_field_list(
                        field_def.get('fields') or [],
                        nested if isinstance(nested, dict) else {}))
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
                    result.errors.append(f"Error encoding flagged group: {e}")
                continue
            
            name = field_def.get('name', 'unknown')
            field_type = field_def.get('type', 'u8')
            
            # A derived value is computed from other fields and occupies no bytes of its
            # own. This required the deprecated `formula` spelling, so a field using
            # `ref`, `compute`, `polynomial` or `guard` fell through to "Cannot encode
            # type: number" - every schema with a computed field failed to encode.
            if field_type in COMPUTED_TYPES:
                continue
            
            # Bitfield string encoding
            if field_type == 'bitfield_string':
                value = data.get(name, '')
                encoded = self._encode_bitfield_string(field_def, str(value))
                output.extend(encoded)
                continue
            
            # Skip type: emit zero bytes, no input needed
            if field_type == 'skip':
                length = field_def.get('length', 1)
                # `remaining` gives no count to pad on encode (PS-014).
                length = 0 if isinstance(length, str) else max(0, int(length))
                output.extend(bytes(length))
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
                    result.errors.append(f"Error encoding {name}: {e}")
                continue

            # Internal fields: the value a later match needs, else default or 0
            if name.startswith('_'):
                if name in internal_patches:
                    value = internal_patches[name]
                else:
                    value = field_def.get('default', 0)
            elif name in flags_patches:
                value = flags_patches[name]
            else:
                # `name_from` reports the value under a templated key, so that is the key
                # to read it back from (CR-2026-030).
                lookup_name = self._resolve_encode_name(field_def, name, data)
                if lookup_name is None:
                    result.errors.append(
                        f"Error encoding {name}: name_from "
                        f"{field_def.get('name_from')!r} references a field the data does "
                        "not carry, so its output key cannot be rebuilt")
                    continue
                value = data.get(lookup_name)
                if value is None:
                    result.warnings.append(f"Missing field: {lookup_name}")
                    value = 0
            
            try:
                # Reverse modifiers
                value = self._reverse_modifiers(value, field_def)
                
                encoded = self._encode_field(field_def, value)
                output.extend(encoded)
            except Exception as e:
                result.errors.append(f"Error encoding {name}: {e}")
        
        result.payload = bytes(output)
        return result
    
    def _encode_flagged(self, flagged_def: Dict[str, Any], data: Dict[str, Any]) -> bytes:
        """Encode flagged groups: only encode groups where data is present."""
        groups = flagged_def.get('groups', [])
        output = bytearray()
        
        for group in groups:
            bit = group.get('bit', 0)
            group_fields = group.get('fields', [])
            has_data = any(gf.get('name') and gf['name'] in data for gf in group_fields)
            if not has_data:
                continue
            for gf in group_fields:
                gf_name = gf.get('name', '')
                gf_type = gf.get('type', 'u8')
                if not gf_name or gf_name.startswith('_'):
                    continue
                if gf_type in COMPUTED_TYPES:
                    # A derived value: computed from other fields, so it has no bytes of
                    # its own. This skipped only the deprecated `formula` spelling, so a
                    # field using `ref`, `compute`, `polynomial` or `guard` was encoded
                    # as though it were on the wire.
                    continue
                value = data.get(gf_name, 0)
                value = self._reverse_modifiers(value, gf)
                output.extend(self._encode_field(gf, value))
        
        return bytes(output)
    
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
            # PS-431: a hex segment is read without regard to case.
            val = int(seg, 16) if fmt in ('hex', 'hex:upper') else int(seg)
            mask = (1 << bit_len) - 1
            int_val |= (val & mask) << bit_off
        
        return self._write_int(int_val, length, signed=False)
    
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
            size = int(byte_group.get('size', 1))
        else:
            group_fields = byte_group or []
            size = int(field_def.get('size', 1))
        check_byte_group_overlap(group_fields)

        packed = 0
        for gf in group_fields:
            if not isinstance(gf, dict):
                continue
            name = gf.get('name', '')
            gtype = str(gf.get('type', ''))
            if not name or str(name).startswith('_'):
                value = gf.get('default', 0)
            else:
                value = data.get(name, gf.get('default', 0))
            value = self._reverse_modifiers(value, gf)
            if not isinstance(value, (int, float)):
                continue
            value = int(round(value))
            if '[' in gtype:
                base_size, start, width = self._parse_bitfield_type(gtype)
                size = max(size, base_size)
                packed |= (value & ((1 << width) - 1)) << start
            else:
                # A full-width member: it owns the group's bytes outright.
                packed |= value
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

    def _encode_bitfield_run(self, run: List[Dict[str, Any]],
                             data: Dict[str, Any]) -> bytes:
        """Pack one run of bit ranges into the byte(s) they share.

        The same shape as `_encode_byte_group`, which is the construct that already did
        this correctly - a run of bare bitfields is the same thing without the wrapper.
        """
        packed = 0
        size = 1
        for field_def in run:
            name = field_def.get('name', '')
            ftype = str(field_def.get('type', ''))
            if not name or str(name).startswith('_'):
                value = field_def.get('default', 0)
            else:
                value = data.get(name, field_def.get('default', 0))
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
            value = int(round(value))
            base_size, start, width = self._parse_bitfield_type(ftype)
            size = max(size, base_size, int(field_def.get('consume', 0) or 0))
            packed |= (value & ((1 << width) - 1)) << start
        # The run shares one base, assembled in its fields' effective byte order (PS-059):
        # a field's own `endian` where one declares it, else the schema's.
        declared = next((f.get('endian') for f in run if f.get('endian')), None)
        endian = Endian(declared) if declared else self.endian
        byteorder = 'little' if endian == Endian.LITTLE else 'big'
        return int(packed).to_bytes(max(1, size), byteorder)

    def _field_declaring_var(self, var_name: str) -> Optional[Dict[str, Any]]:
        """The field that declared ``var: <var_name>``, searched anywhere in the schema.

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
        return visit(self.schema)

    def _case_fields_present(self, cases: Dict[Any, Any], data: Dict[str, Any]):
        """The case whose fields the data carries most of, or ``(None, None)``.

        Used where the discriminator is not in the output - an inline match with no
        ``name`` reports nothing of itself, so the case has to be recovered from which
        of its fields are there.
        """
        best_key, best_fields, best_hits = None, None, 0
        for case_key, case_fields in cases.items():
            if case_key == 'default' or not isinstance(case_fields, list):
                continue
            names = [
                f.get('name') for f in case_fields
                if isinstance(f, dict) and f.get('name')
                and not str(f['name']).startswith('_') and f.get('type') not in COMPUTED_TYPES
            ]
            hits = sum(1 for n in names if n in data)
            if hits > best_hits:
                best_key, best_fields, best_hits = case_key, case_fields, hits
        return best_key, best_fields

    def _internal_discriminators(self, fields: List[Dict[str, Any]],
                                 data: Dict[str, Any]) -> Dict[str, Any]:
        """Values for internal fields that a `match` in the same list dispatches on.

        Only a case keyed by one exact value can supply one; a range or pattern key
        names no single value, and the field keeps its default.
        """
        internal = {}
        for f in fields:
            fname = f.get('name')
            if isinstance(fname, str) and fname.startswith('_'):
                internal[fname] = fname
                if f.get('var'):
                    internal[f['var']] = fname
        patches: Dict[str, Any] = {}
        for f in fields:
            match_def = f.get('match')
            if not isinstance(match_def, dict):
                continue
            ref = str(match_def.get('field') or '').lstrip('$')
            target = internal.get(ref)
            if not target or target in patches or ref in data:
                continue
            key, _fields = self._case_fields_present(match_def.get('cases') or {}, data)
            if isinstance(key, bool):
                continue
            if isinstance(key, int):
                patches[target] = key
            elif isinstance(key, str):
                try:
                    patches[target] = int(key, 0)
                except ValueError:
                    pass
        return patches

    def _encode_match(self, field_def: Dict[str, Any], data: Dict[str, Any]) -> bytes:
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
            var_name = str(field_ref).lstrip('$')
            if var_name in data:
                discriminator = data[var_name]
            else:
                source = self._field_declaring_var(var_name)
                if source and source.get('name') in data:
                    discriminator = reverse_lookup(
                        data[source['name']], source.get('lookup'))

        matched_key, matched_fields = None, None
        if discriminator is not None:
            for case_key, case_fields in cases.items():
                if case_key == 'default':
                    continue
                if self._match_case_pattern(discriminator, case_key):
                    matched_key, matched_fields = case_key, case_fields
                    break

        if matched_fields is None:
            matched_key, matched_fields = self._case_fields_present(cases, data)

        if matched_fields is None:
            if isinstance(default, list):
                matched_fields = default
            elif isinstance(cases.get('default'), list):
                matched_fields = cases['default']
            else:
                # 'skip', or nothing in the data belongs to any case.
                return b''

        out = bytearray()
        if length is not None:
            value = discriminator
            if value is None:
                if not isinstance(matched_key, (int, str)):
                    raise ValueError(
                        f"match case {matched_key!r} names no single discriminator value")
                value = int(str(matched_key), 0)
            out.extend(int(value).to_bytes(int(length), byteorder))
        out.extend(self._encode_field_list(matched_fields, data))
        return bytes(out)

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

    def _encode_field_list(self, fields: List[Dict[str, Any]], data: Dict[str, Any]) -> bytes:
        """Encode a list of plain fields - a TLV case's value bytes."""
        out = bytearray()
        for kind, item in self._bitfield_runs(fields):
            # A run of bit ranges shares one span of bytes, so it is packed once rather
            # than a byte per field (CR-2026-023).
            if kind == 'bits':
                out.extend(self._encode_bitfield_run(item, data))
                continue
            f = item
            if not isinstance(f, dict):
                continue
            # A case body may hold another construct rather than a plain field.
            if 'match' in f and not f.get('type'):
                out.extend(self._encode_match(f, data))
                continue
            if 'tlv' in f and not f.get('type'):
                out.extend(self._encode_tlv(f, data))
                continue
            if 'byte_group' in f:
                out.extend(self._encode_byte_group(f, data))
                continue
            name = f.get('name', '')
            ftype = f.get('type', 'u8')
            if ftype == 'repeat':
                out.extend(self._encode_repeat(f, data))
                continue
            if ftype == 'object':
                # A nested object is reported under its own name (PS-139), so its members
                # are read from that mapping. They were looked up in the enclosing data,
                # where they are not, and every member encoded as a zero.
                nested = data.get(name)
                out.extend(self._encode_field_list(
                    f.get('fields') or [], nested if isinstance(nested, dict) else data))
                continue
            if ftype in COMPUTED_TYPES:
                # Derived: computed from other fields, no bytes of its own.
                continue
            if ftype == 'skip':
                length = f.get('length', 1)
                length = 0 if isinstance(length, str) else max(0, int(length))
                out.extend(bytes(length))
                continue
            if ftype == 'bitfield_string':
                out.extend(self._encode_bitfield_string(f, str(data.get(name, ''))))
                continue
            if is_literal(f):
                continue            # PS-359: a literal writes no bytes
            if 'value' in f:
                # PS-360: the constant is written whatever the input says.
                out.extend(self._encode_field(f, f['value']))
                continue
            if not name or name.startswith('_'):
                value = f.get('default', 0)
            else:
                # As in the top-level loop: a templated key is where the value lives.
                lookup_name = self._resolve_encode_name(f, name, data) or name
                value = data.get(lookup_name, f.get('default', 0))
            value = self._reverse_modifiers(value, f)
            out.extend(self._encode_field(f, value))
        return bytes(out)

    def _encode_repeat(self, field_def: Dict[str, Any], data: Dict[str, Any]) -> bytes:
        """Encode a ``repeat``: its records back to back, and nothing else.

        The framing costs no bytes of its own here. ``count: $n`` and
        ``byte_length: $len`` name a field earlier in the list, which the main loop
        encodes from its own value, and ``until: end`` needs no header at all - so the
        construct contributes exactly its records. Each record is a dict, and its fields
        are looked up inside it rather than in the payload-level output.

        Encoding reached "Cannot encode type: repeat" before this, so a schema with a
        repeat lost every record: repeat-count.yaml re-encoded 020a14 as 02.
        """
        name = field_def.get('name', '')
        records = data.get(name)
        if records is None:
            return b''
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, (list, tuple)):
            raise ValueError(
                f"repeat field {name!r}: expected a list of records, got "
                f"{type(records).__name__}")
        record_fields = field_def.get('fields') or []
        # PS-387: an element present_if dropped is not in the data, and its bytes were in
        # the payload, so they cannot be written back. Only elements that read nothing
        # (computed fields and literals) can be encoded.
        if field_def.get('present_if') and any(
                isinstance(f, dict) and f.get('type') not in COMPUTED_TYPES
                and not is_literal(f) for f in record_fields):
            raise ValueError(
                f"repeat field {name!r} declares present_if and its elements read payload "
                f"bytes, so the elements it dropped cannot be encoded (PS-387)")
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
                record = dict(record, **{index: position})
            out.extend(self._encode_field_list(record_fields, record))
        # PS-385: the elements, then the trailer, whose values sit beside the repeat.
        if field_def.get('trailer'):
            out.extend(self._encode_field_list(field_def['trailer'], data))
        return bytes(out)

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

    def _case_fidelity(self, case_fields: List[Dict[str, Any]], data: Dict[str, Any]):
        """How well a candidate case explains the data: (matches, lossless).

        Two cases can define the same field name under different tags - am308 has `tvoc`
        under both [8, 125] (``div: 100``) and [8, 230] (raw). Only one of them can have
        produced the value, and the arithmetic says which: 43.69 came from 4369 through
        `div: 100` exactly, while the raw case would need it rounded to 44. A candidate
        that cannot reproduce the value it claims did not write those bytes.
        """
        matches, lossless = 0, True
        for f in self._claimable_fields(case_fields):
            name = f.get('name')
            if name not in data:
                continue
            matches += 1
            raw = reverse_lookup(data[name], f.get('lookup'))
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                try:
                    raw = reverse_transform_stages(raw, f.get('transform'))
                    raw = reverse_canonical_modifiers(raw, f)
                except Exception:
                    lossless = False
                    continue
                if isinstance(raw, float) and abs(raw - round(raw)) > 1e-9:
                    lossless = False
                bounds = integer_range(f.get('type', 'u8'))
                if bounds is not None and not (bounds[0] <= round(raw) <= bounds[1]):
                    # It does not fit the field, so this case cannot have written it:
                    # am308's `tvoc` of 4369 needs 436900 through the `div: 100` case,
                    # which a u16 cannot hold. That raised "int too big to convert".
                    lossless = False
        return matches, lossless

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
        cases = tlv_def.get('cases', {}) or {}
        length_size = tlv_def.get('length_size', 0) or 0
        byteorder = 'little' if self.endian == Endian.LITTLE else 'big'
        order = list(data)

        candidates = []
        for case_key, case_fields in cases.items():
            if case_key == 'default' or not isinstance(case_fields, list):
                continue
            names = [f['name'] for f in self._claimable_fields(case_fields)]
            claimed = [n for n in names if n in data]
            if not claimed:
                continue
            matches, lossless = self._case_fidelity(case_fields, data)
            candidates.append((
                min(order.index(n) for n in claimed),   # payload order
                0 if lossless else 1,                   # one that can reproduce the value
                -matches,                               # then the fuller explanation
                case_key, case_fields, claimed,
            ))

        candidates.sort(key=lambda item: item[:3])

        out = bytearray()
        spent: set = set()
        emitted = []
        for position, _, _, case_key, case_fields, claimed in candidates:
            # Every decoded field belongs to one channel. Without this a name defined
            # under two tags emitted both of them, so am308 grew an extra channel.
            if all(name in spent for name in claimed):
                continue
            spent.update(claimed)
            emitted.append((position, case_key, case_fields))

        emitted.sort(key=lambda item: item[0])
        for _, case_key, case_fields in emitted:
            tag = self._encode_tlv_tag(case_key, tlv_def)
            value = self._encode_field_list(case_fields, data)
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
        value = reverse_lookup(value, field_def.get('lookup'))

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
        
        # Phase 3: encode_formula takes precedence
        encode_formula = field_def.get('encode_formula')
        if encode_formula:
            return int(round(self._evaluate_encode_formula(encode_formula, value)))

        # Decoding applies the canonical modifiers, then the transform chain, then the
        # lookup, so encoding undoes them in the opposite order.
        value = reverse_transform_stages(value, field_def.get('transform'))

        value = reverse_canonical_modifiers(value, field_def)
        
        # Float types should preserve fractional values
        field_type = field_def.get('type', 'u8')
        if field_type in ('f16', 'f32', 'f64', 'udec', 'sdec', 'f32le16', 'f32be16le',
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

    def _encode_field_inner(self, field_def: Dict[str, Any], value: Any) -> bytes:
        """Encode a single field value."""
        field_type = field_def.get('type')
        if not field_type:
            raise ValueError(
                f"Field '{field_def.get('name', '?')}' declares no type"
            )
        
        # Handle bitfields - simplified (just return byte with value)
        if any(c in str(field_type) for c in ['[', ':', '<']):
            return bytes([int(value) & 0xFF])
        
        type_info = INTEGER_TYPE_INFO

        # The inverse of the word-ordered read (PS-271): least significant 16-bit unit
        # first, each unit big-endian, and `endian` plays no part (PS-272).
        if field_type in WORD_ORDERED_TYPES:
            return write_word_ordered(field_type, value)

        if field_type in type_info:
            size, signed = type_info[field_type]
            int_val = int(value)
            # Apply encoding if specified (sign_magnitude, bcd, gray)
            encoding = field_def.get('encoding')
            if encoding:
                int_val = self._encode_encoding(int_val, encoding, size)
                # Encoded values are written as unsigned
                signed = False
            return self._write_int(int_val, size, signed)
        
        if field_type in MINIFLOAT_SIZES:
            return self._write_int(encode_minifloat(field_type, value),
                                   MINIFLOAT_SIZES[field_type], False)

        if field_type in ('udec', 'sdec'):
            # The inverse of PS-330: the whole part in the upper nibble (two's
            # complement for sdec), tenths in the lower. Flooring keeps the tenths
            # non-negative, which is how the decode reads them: -1.5 is -2 + 0.5.
            whole = math.floor(float(value))
            tenths = int(round((float(value) - whole) * 10))
            if tenths == 10:
                whole, tenths = whole + 1, 0
            low, high = (-8, 7) if field_type == 'sdec' else (0, 15)
            if not low <= whole <= high:
                raise ValueError(
                    f"Field '{field_def.get('name', '?')}': {value} does not fit {field_type}"
                )
            return bytes([((whole & 0x0F) << 4) | tenths])

        if field_type == 'f16':
            fmt = '<e' if self.endian == Endian.LITTLE else '>e'
            return struct.pack(fmt, float(value))
        
        if field_type == 'f32':
            fmt = '<f' if self.endian == Endian.LITTLE else '>f'
            return struct.pack(fmt, float(value))
        
        if field_type == 'f64':
            fmt = '<d' if self.endian == Endian.LITTLE else '>d'
            return struct.pack(fmt, float(value))
        
        if field_type == 'bool':
            return bytes([1 if value else 0])
        
        if field_type == 'skip':
            length = field_def.get('length', 1)
            length = 0 if isinstance(length, str) else max(0, int(length))
            return bytes(length)
        
        if field_type == 'bytes':
            # Accepts every form a `bytes` field can arrive in, because CR-2026-008
            # makes the decoder report one as a lowercase hex string (PS-281) and
            # encode(decode(payload)) has to keep round-tripping. Falling through to
            # `bytes(length)` used to emit zeros for anything that was not already a
            # bytes object, so a hex string round-tripped to 00000000 silently.
            if isinstance(value, (bytes, bytearray)):
                raw = bytes(value)
            elif isinstance(value, str) and field_def.get('format') == 'base64':
                import base64 as b64
                raw = b64.b64decode(value)
            elif isinstance(value, str):
                text = value.replace(' ', '')
                separator = field_def.get('separator')
                if separator:
                    text = text.replace(str(separator), '')
                try:
                    raw = bytes.fromhex(text)
                except ValueError as exc:
                    raise ValueError(
                        "bytes field %r: expected hex, got %r (%s)"
                        % (field_def.get('name'), value, exc)
                    )
            elif isinstance(value, (list, tuple)):
                raw = bytes(int(b) & 0xFF for b in value)
            else:
                raise ValueError(
                    "bytes field %r: cannot encode %s"
                    % (field_def.get('name'), type(value).__name__)
                )
            length = encode_length(field_def, len(raw))
            return raw[:length].ljust(length, b'\x00')
        
        if field_type == 'string' and 'value' in field_def:
            # A literal came from no bytes, so it writes none.
            return b''
        if field_type in ('string', 'ascii'):
            length = encode_length(field_def, len(str(value).encode('utf-8')))
            encoded = str(value).encode('utf-8')[:length]
            return encoded.ljust(length, b'\x00')
        
        if field_type == 'hex':
            length = encode_length(field_def, len(str(value)) // 2)
            return bytes.fromhex(str(value).replace(' ', ''))[:length].ljust(length, b'\x00')
        
        if field_type == 'base64':
            import base64 as b64
            length = field_def.get('length', 0)
            decoded = b64.b64decode(str(value))
            if length:
                return decoded[:length].ljust(length, b'\x00')
            return decoded
        
        if field_type == 'enum':
            return self._encode_enum(field_def, value)
        
        raise ValueError(f"Cannot encode type: {field_type}")
    
    def _encode_enum(self, field_def: Dict[str, Any], value: Any) -> bytes:
        """Encode enum field: map string value back to integer."""
        base_type = field_def.get('base', 'u8')
        values = field_def.get('values', {})
        
        # Find the integer value for the string
        int_value = None
        
        if isinstance(values, dict):
            # Reverse lookup: string -> int
            for k, v in values.items():
                if enum_label(v) == value:
                    int_value = int(k) if isinstance(k, str) else k
                    break
        elif isinstance(values, list):
            # Find index of value
            labels = [enum_label(v) for v in values]
            if value in labels:
                int_value = labels.index(value)
        
        if int_value is None:
            # Try parsing as integer (e.g., "unknown(5)")
            if isinstance(value, str) and value.startswith('unknown('):
                try:
                    int_value = int(value[8:-1])
                except ValueError:
                    raise ValueError(f"Cannot encode unknown enum value: {value}")
            elif isinstance(value, int):
                int_value = value
            else:
                raise ValueError(f"Enum value not found: {value}")
        
        # Encode as base type
        base_field = {'type': base_type}
        return self._encode_field(base_field, int_value)
    
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
        data = data or {}
        
        if command_name not in self.downlink_commands:
            result.errors.append(f"Unknown command: {command_name}")
            return result
        
        cmd_def = self.downlink_commands[command_name]
        command_id = cmd_def.get('command_id', 0)
        fields = cmd_def.get('fields', [])
        
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
            
            value = data.get(name, 0)
            if value is None:
                result.warnings.append(f"Missing command field: {name}")
                value = 0
            
            try:
                value = self._reverse_modifiers(value, field_def)
                encoded = self._encode_field(field_def, value)
                output.extend(encoded)
            except Exception as e:
                result.errors.append(f"Error encoding command field {name}: {e}")
        
        result.payload = bytes(output)
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
                result.errors.append(f"Error decoding command field {name}: {e}")
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
                       'description', 'semantic', 'ipso', 'senml_unit'):
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
                           format: str = 'ipso') -> Dict[str, Any]:
        """
        Convert decoded data to semantic format.
        
        Args:
            decoded: Decoded field values
            format: 'ipso', 'senml', or 'ttn'
            
        Returns:
            Semantically formatted output
        """
        fields = self.schema.get('fields', [])
        
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
    
    def _to_senml(self, decoded: Dict[str, Any], 
                  fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert to SenML format."""
        records = []
        
        for field_def in fields:
            name = field_def.get('name')
            if name not in decoded:
                continue
            
            record = {'n': name}
            value = decoded[name]
            
            if isinstance(value, bool):
                record['vb'] = value
            elif isinstance(value, (int, float)):
                record['v'] = value
            elif isinstance(value, str):
                record['vs'] = value
            elif isinstance(value, bytes):
                record['vd'] = value.hex()
            else:
                record['v'] = value
            
            unit = field_def.get('unit')
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
        schema = yaml.safe_load(handle)
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
