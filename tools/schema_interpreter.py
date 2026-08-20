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
    's8': (1, True), 'i8': (1, True), 'int8': (1, True),
    's16': (2, True), 'i16': (2, True), 'int16': (2, True),
    's24': (3, True), 'i24': (3, True), 'int24': (3, True),
    's32': (4, True), 'i32': (4, True), 'int32': (4, True),
    's64': (8, True), 'i64': (8, True), 'int64': (8, True),
}


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


class LookupIndexError(ValueError):
    """An out-of-bounds index into a sequence ``lookup`` (PS-105).

    Every implementation used to report the raw index instead, silently, so a payload
    that did not match its schema decoded as though it did.
    """


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
    if not lookup or isinstance(value, bool) or not isinstance(value, int):
        return value
    if isinstance(lookup, dict):
        for key, label in lookup.items():
            try:
                if int(key) == value:
                    return label
            except (TypeError, ValueError):
                continue
        if 'default' in lookup:
            return lookup['default']
        return OMITTED
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
    """Map a label back to its integer for encoding."""
    if not lookup:
        return value
    if isinstance(lookup, dict):
        for key, label in lookup.items():
            if label == value:
                try:
                    return int(key)
                except (TypeError, ValueError):
                    return value
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
    """
    for key in CANONICAL_MODIFIER_ORDER:
        operand = field_def.get(key)
        if operand is None:
            continue
        if key == 'mult':
            value = value * operand
        elif key == 'div':
            if operand != 0:
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

        if fPort is not None:
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
        if encoding == 'sign_magnitude':
            sign_bit = 1 << (size * 8 - 1)
            if value < 0:
                return sign_bit | abs(value)
            return value
        
        elif encoding == 'bcd':
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
        match = re.match(r'u(\d+)\[(\d+):(\d+)\]$', type_str)
        if match:
            base_size = int(match.group(1)) // 8
            start = int(match.group(2))
            end = int(match.group(3))
            width = end - start + 1
            return base_size, start, width

        raise ValueError(
            f"Unknown bitfield format: {type_str} - the only bitfield spelling is "
            f"uN[start:end], e.g. u8[3:4]. uN[base+:width], bits<offset,width>, "
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
        raw = int.from_bytes(buf[pos:pos + base_size], 'big')
        mask = (1 << bit_width) - 1
        value = (raw >> bit_offset) & mask

        # A bracket range never advances the read position on its own; `consume: N`
        # is the only way to move it (PS-060). The sequential form's implicit
        # advance on reaching bit 0 went with CR-2026-006.
        return value, pos, False
    
    def _decode_field(self, field_def: Dict[str, Any], buf: bytes, 
                      pos: int) -> Tuple[Any, int]:
        """Decode a single field from buffer."""
        field_type = field_def.get('type', 'u8')
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
        if field_type in ('u32le16', 's32le16'):
            if pos + 4 > len(buf):
                raise ValueError(f"Buffer too short: need 4 bytes at pos {pos}")
            low = int.from_bytes(buf[pos:pos + 2], 'big')
            high = int.from_bytes(buf[pos + 2:pos + 4], 'big')
            value = low + (high << 16)
            if field_type == 's32le16' and value >= 0x80000000:
                value -= 0x100000000
            return value, pos + 4

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
        
        # Nibble-decimal types: upper nibble = whole, lower nibble = tenths
        if field_type in ('udec', 'UDec'):
            if pos >= len(buf):
                raise ValueError("Buffer too short for udec")
            byte = buf[pos]
            value = (byte >> 4) + (byte & 0x0F) * 0.1
            return value, pos + 1
        
        if field_type in ('sdec', 'SDec'):
            if pos >= len(buf):
                raise ValueError("Buffer too short for sdec")
            byte = buf[pos]
            whole = byte >> 4
            # Sign extend the 4-bit whole part
            if whole >= 8:
                whole -= 16
            value = whole + (byte & 0x0F) * 0.1
            return value, pos + 1
        
        if field_type == 'f16':
            # IEEE 754 half-precision (2 bytes)
            return self._read_float16(buf, pos)
        
        if field_type in ('f32', 'float'):
            return self._read_float(buf, pos, 4)
        
        if field_type in ('f64', 'double'):
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
            value = buf[pos:pos + length]
            return value, pos + length
        
        if field_type == 'string':
            length = resolve_length(field_def, buf, pos)
            if pos + length > len(buf):
                raise ValueError("Buffer too short for string")
            value = buf[pos:pos + length].decode('utf-8', errors='replace').rstrip('\x00')
            return value, pos + length
        
        if field_type == 'ascii':
            length = resolve_length(field_def, buf, pos)
            if pos + length > len(buf):
                raise ValueError("Buffer too short for ascii")
            value = buf[pos:pos + length].decode('ascii', errors='replace').rstrip('\x00')
            return value, pos + length
        
        if field_type in ('hex', 'hex:upper'):
            length = resolve_length(field_def, buf, pos)
            if pos + length > len(buf):
                raise ValueError("Buffer too short for hex")
            # PS-074: `hex` output MUST be lowercase without separators. This
            # emitted uppercase, so it disagreed with the specification, with the
            # Go and Java interpreters, and with every vendor decoder. Uppercase
            # is the separate `hex:upper` type.
            raw = buf[pos:pos + length].hex()
            value = raw.upper() if field_type == 'hex:upper' else raw
            return value, pos + length
        
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
        
        if field_type == 'version_string':
            # Phase 3: Assemble a version string from packed bytes
            return self._decode_version_string(field_def, buf, pos)
        
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
        
        raise ValueError(f"Unknown type: {field_type}")
    
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
                return values_map[raw_value], new_pos
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
                return values[raw_value], new_pos
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
        - until: "end" to repeat until payload exhausted
        
        Options:
        - max: maximum iterations (safety limit, default 1000)
        - min: minimum required iterations
        - fields: nested fields to decode per iteration
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
            
            count = min(count, max_iterations)
            
            for _ in range(count):
                element = {}
                for nested_field in nested_fields:
                    name = nested_field.get('name', 'unknown')
                    value, pos = self._decode_field(nested_field, buf, pos)
                    value = self._apply_modifiers(value, nested_field)
                    if value is not None:
                        element[name] = value
                result.append(element)
                
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
                element = {}
                for nested_field in nested_fields:
                    name = nested_field.get('name', 'unknown')
                    value, pos = self._decode_field(nested_field, buf, pos)
                    value = self._apply_modifiers(value, nested_field)
                    if value is not None:
                        element[name] = value
                result.append(element)
                iterations += 1
            
            if pos != end_pos:
                raise ValueError(f"repeat byte_length mismatch: expected end at {end_pos}, got {pos}")
                
        elif until == 'end':
            # Until-end: repeat until payload exhausted
            while pos < len(buf) and iterations < max_iterations:
                element = {}
                start_pos = pos
                for nested_field in nested_fields:
                    name = nested_field.get('name', 'unknown')
                    value, pos = self._decode_field(nested_field, buf, pos)
                    value = self._apply_modifiers(value, nested_field)
                    if value is not None:
                        element[name] = value
                # Safety: check we made progress
                if pos == start_pos:
                    break
                result.append(element)
                iterations += 1
        else:
            raise ValueError("repeat field must specify one of: count, byte_length, or until")
        
        # Validate minimum iterations
        if len(result) < min_iterations:
            raise ValueError(f"repeat produced {len(result)} elements, but minimum is {min_iterations}")
        
        return result, pos
    
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
                # Internal field - decode but don't output
                _, pos = self._decode_field(cf, buf, pos)
            else:
                value, pos = self._decode_field(cf, buf, pos)
                value = self._apply_modifiers(value, cf)
                result[name] = value
        
        return result, pos
    
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
            
            # Option B: nested object: inside case
            if 'object' in cf and not cf.get('type'):
                obj_name = cf['object']
                sub_result, pos = self._decode_nested_object_b(cf, buf, pos)
                result[obj_name] = sub_result
                if hasattr(self, '_current_data'):
                    self._current_data[obj_name] = sub_result
                continue
            
            name = cf.get('name', 'unknown')
            if name.startswith('_'):
                _, pos = self._decode_field(cf, buf, pos)
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
        
        # List of values
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
        
        # Single value comparison
        return value == pattern
    
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
                    if gf_type == 'number':
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
            if fmt == 'hex':
                # Lowercase, as PS-074 requires of the `hex` type and as every vendor
                # codec renders it - JavaScript's toString(16) is lowercase, and the
                # generated TS013 codec was the only implementation getting this right.
                part_strs.append(format(raw, 'x'))
            else:
                part_strs.append(str(raw))
        
        return prefix + delimiter.join(part_strs), pos
    
    def _decode_version_string(self, field_def: Dict[str, Any], buf: bytes,
                                pos: int) -> Tuple[str, int]:
        """
        Phase 3: Decode version_string - assemble version from sequential bytes.
        
        version_string:
          fields: [major, minor, patch]  # byte names (for docs)
          length: 3                      # bytes to consume
          delimiter: '.'
          prefix: 'v'
        
        Reads N bytes and joins them as "prefix" + "byte1.byte2.byte3"
        """
        length = field_def.get('length', 3)
        delimiter = field_def.get('delimiter', '.')
        prefix = field_def.get('prefix', '')
        
        if pos + length > len(buf):
            raise ValueError(f"Buffer too short for version_string at pos {pos}")
        
        parts = []
        for i in range(length):
            parts.append(str(buf[pos + i]))
        pos += length
        
        return prefix + delimiter.join(parts), pos
    
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
                      lambda m: str(self._variables.get(m.group(1), 0)), expr)
        
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
                return float(self._variables.get(field_name, 0))
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
                field_value = float(self._variables.get(field_name, 0))
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
            value = float(self._variables.get(ref_name, 0))
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
        """
        import math
        
        for op in transform_ops:
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
                value = math.log10(max(1e-10, value))  # Avoid domain error
            elif 'log' in op and op['log']:
                value = math.log(max(1e-10, value))  # Natural log
            elif any(key in op for key in CANONICAL_MODIFIER_ORDER):
                # A stage normally carries one arithmetic op. Where it carries
                # several, they are applied in the canonical order so that a
                # stage cannot mean different things in different languages --
                # and so that none of them is silently dropped, which an
                # either/or chain here used to do.
                value = apply_canonical_modifiers(value, op)
            elif 'round' in op:
                decimals = op['round']
                if decimals is True or decimals == 0:
                    value = round(value)
                else:
                    value = round(value, int(decimals))
            elif 'op' in op:
                # Handle {op: 'round', decimals: N} syntax
                if op['op'] == 'round':
                    decimals = op.get('decimals', 0)
                    if decimals == 0:
                        value = round(value)
                    else:
                        value = round(value, int(decimals))
                elif op['op'] == 'floor':
                    value = math.floor(value)
                elif op['op'] == 'ceiling' or op['op'] == 'ceil':
                    value = math.ceil(value)
        
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
        
        # Decode all fields from the same starting position
        for gf in group_fields:
            name = gf.get('name', 'unknown')
            
            # Force consume: 0 for all but track internally
            gf_copy = dict(gf)
            gf_copy['consume'] = 0
            
            try:
                value, _ = self._decode_field(gf_copy, buf, pos)
                value = self._apply_modifiers(value, gf)
                if not name.startswith('_'):
                    result.data[name] = value
                # Add to variables so field can be referenced by compute/formula
                self._variables[name] = value
            except Exception as e:
                result.errors.append(f"Error in byte_group field {name}: {e}")
        
        # Advance past the group
        return pos + group_size
    
    def _decode_nested_object_b(self, field_def: Dict[str, Any], buf: bytes,
                                 pos: int) -> Tuple[Dict[str, Any], int]:
        """Decode nested object using Option B syntax (object: key)."""
        nested_fields = field_def.get('fields', [])
        nested_result = {}
        
        for nf in nested_fields:
            if 'match' in nf and not nf.get('type'):
                match_result, pos = self._decode_match(nf, buf, pos)
                nested_result.update(match_result)
            elif 'object' in nf and not nf.get('type'):
                sub_name = nf['object']
                sub_result, pos = self._decode_nested_object_b(nf, buf, pos)
                nested_result[sub_name] = sub_result
            else:
                nf_name = nf.get('name', 'unknown')
                value, pos = self._decode_field(nf, buf, pos)
                if value is not None:
                    value = self._apply_modifiers(value, nf)
                    if not nf_name.startswith('_'):
                        nested_result[nf_name] = value
                if nf.get('var'):
                    self._variables[nf['var']] = value
        
        return nested_result, pos
    
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
        if transform and isinstance(transform, list):
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
        
        for field_def in fields:
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
                pos = self._decode_byte_group(field_def, payload, pos, result)
                continue
            
            # Option B: match: as top-level key
            if 'match' in field_def and not field_def.get('type'):
                try:
                    match_result, pos = self._decode_match(field_def, payload, pos)
                    result.data.update(match_result)
                except Exception as e:
                    result.errors.append(f"Error in match: {e}")
                continue
            
            # Option B: object: as top-level key
            if 'object' in field_def and not field_def.get('type'):
                try:
                    obj_name = field_def['object']
                    nested_fields = field_def.get('fields', [])
                    nested_result = {}
                    saved_data = self._current_data
                    # nested object still adds vars to top-level scope
                    for nf in nested_fields:
                        # Recursively handle Option B constructs in nested fields
                        if 'match' in nf and not nf.get('type'):
                            match_result, pos = self._decode_match(nf, payload, pos)
                            nested_result.update(match_result)
                        elif 'object' in nf and not nf.get('type'):
                            sub_name = nf['object']
                            sub_result, pos = self._decode_nested_object_b(nf, payload, pos)
                            nested_result[sub_name] = sub_result
                        else:
                            nf_name = nf.get('name', 'unknown')
                            value, pos = self._decode_field(nf, payload, pos)
                            if value is not None:
                                value = self._apply_modifiers(value, nf)
                                if not nf_name.startswith('_'):
                                    nested_result[nf_name] = value
                            # Store variable if var: specified
                            if nf.get('var'):
                                self._variables[nf['var']] = value
                    self._current_data = saved_data
                    result.data[obj_name] = nested_result
                except Exception as e:
                    result.errors.append(f"Error in object '{field_def.get('object')}': {e}")
                continue
            
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
            if field_type in ('number', 'integer'):
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
            
            # Skip internal fields
            if name.startswith('_'):
                try:
                    _, pos = self._decode_field(field_def, payload, pos)
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
        
        result.bytes_consumed = pos
        
        # Metadata enrichment
        metadata_def = self.schema.get('metadata')
        if metadata_def and input_metadata is not None:
            self._enrich_metadata(result.data, metadata_def, input_metadata)
        
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
                         input_meta: Dict[str, Any]) -> None:
        """Enrich decoded data with network metadata from TS013 input."""
        from datetime import datetime, timedelta, timezone
        
        # Include mappings
        for mapping in metadata_def.get('include', []):
            name = mapping.get('name')
            source = mapping.get('source')
            if name and source:
                value = self._resolve_metadata_ref(source, input_meta)
                if value is not None:
                    data[name] = value
        
        # Timestamp enrichment
        for ts in metadata_def.get('timestamps', []):
            name = ts.get('name', 'timestamp')
            mode = ts.get('mode')
            
            if mode == 'rx_time' or ts.get('source') == '$recvTime':
                data[name] = input_meta.get('recvTime')
            
            elif mode == 'subtract':
                offset_field = ts.get('offset_field')
                recv_time = input_meta.get('recvTime')
                if recv_time and offset_field and offset_field in data:
                    try:
                        rx_dt = datetime.fromisoformat(recv_time.replace('Z', '+00:00'))
                        offset_sec = data[offset_field]
                        meas_dt = rx_dt - timedelta(seconds=offset_sec)
                        data[name] = meas_dt.strftime('%Y-%m-%dT%H:%M:%S.') + \
                            f'{meas_dt.microsecond // 1000:03d}Z'
                    except Exception:
                        pass
            
            elif mode == 'unix_epoch':
                field = ts.get('field')
                if field and field in data:
                    try:
                        dt = datetime.fromtimestamp(data[field], tz=timezone.utc)
                        data[name] = dt.strftime('%Y-%m-%dT%H:%M:%S.') + \
                            f'{dt.microsecond // 1000:03d}Z'
                    except Exception:
                        pass
            
            elif mode == 'iso8601':
                # Format a raw epoch/offset field as ISO 8601 string
                field = ts.get('field')
                fmt = ts.get('format', '%Y-%m-%dT%H:%M:%SZ')
                if field and field in data:
                    try:
                        dt = datetime.fromtimestamp(data[field], tz=timezone.utc)
                        data[name] = dt.strftime(fmt)
                    except Exception:
                        pass
            
            elif mode == 'elapsed_to_absolute':
                # Convert elapsed seconds to absolute time: rx_time - elapsed
                elapsed_field = ts.get('elapsed_field') or ts.get('offset_field')
                time_base = ts.get('time_base', 'rx_time')
                recv_time = input_meta.get('recvTime') if time_base == 'rx_time' else None
                if recv_time and elapsed_field and elapsed_field in data:
                    try:
                        rx_dt = datetime.fromisoformat(recv_time.replace('Z', '+00:00'))
                        offset_sec = data[elapsed_field]
                        abs_dt = rx_dt - timedelta(seconds=offset_sec)
                        data[name] = abs_dt.strftime('%Y-%m-%dT%H:%M:%S.') + \
                            f'{abs_dt.microsecond // 1000:03d}Z'
                    except Exception:
                        pass
    
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
        
        for field_def in fields:
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
            if field_type == 'number':
                continue
            
            # Bitfield string encoding
            if field_type == 'bitfield_string':
                value = data.get(name, '')
                encoded = self._encode_bitfield_string(field_def, str(value))
                output.extend(encoded)
                continue
            
            # Version string encoding
            if field_type == 'version_string':
                value = data.get(name, '')
                encoded = self._encode_version_string(field_def, str(value))
                output.extend(encoded)
                continue
            
            # Skip type: emit zero bytes, no input needed
            if field_type == 'skip':
                length = field_def.get('length', 1)
                # `remaining` gives no count to pad on encode (PS-014).
                length = 0 if isinstance(length, str) else max(0, int(length))
                output.extend(bytes(length))
                continue
            
            # Skip internal fields - use default or 0
            if name.startswith('_'):
                default = field_def.get('default', 0)
                value = default
            elif name in flags_patches:
                value = flags_patches[name]
            else:
                value = data.get(name)
                if value is None:
                    result.warnings.append(f"Missing field: {name}")
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
                if gf_type == 'number':
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
            val = int(seg, 16) if fmt == 'hex' else int(seg)
            mask = (1 << bit_len) - 1
            int_val |= (val & mask) << bit_off
        
        return self._write_int(int_val, length, signed=False)
    
    def _encode_version_string(self, field_def: Dict[str, Any], value: str) -> bytes:
        """Phase 3: Encode version_string back to bytes."""
        length = field_def.get('length', 3)
        delimiter = field_def.get('delimiter', '.')
        prefix = field_def.get('prefix', '')
        
        if prefix and value.startswith(prefix):
            value = value[len(prefix):]
        
        segments = value.split(delimiter)
        output = bytearray(length)
        for i in range(min(length, len(segments))):
            try:
                output[i] = int(segments[i]) & 0xFF
            except ValueError:
                output[i] = 0
        
        return bytes(output)
    
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
        byteorder = 'little' if self.endian == Endian.LITTLE else 'big'
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
                and not str(f['name']).startswith('_') and f.get('type') != 'number'
            ]
            hits = sum(1 for n in names if n in data)
            if hits > best_hits:
                best_key, best_fields, best_hits = case_key, case_fields, hits
        return best_key, best_fields

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
        for f in fields:
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
                # A nested object's fields are written in place; the interpreter reports
                # them flattened, so they are looked up by their own names.
                out.extend(self._encode_field_list(f.get('fields') or [], data))
                continue
            if ftype == 'number':
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
            if ftype == 'version_string':
                out.extend(self._encode_version_string(f, str(data.get(name, ''))))
                continue
            if not name or name.startswith('_'):
                value = f.get('default', 0)
            else:
                value = data.get(name, f.get('default', 0))
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
        out = bytearray()
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(
                    f"repeat field {name!r}: expected each record to be a mapping, got "
                    f"{type(record).__name__}")
            out.extend(self._encode_field_list(record_fields, record))
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
            if not name or str(name).startswith('_') or f.get('type') == 'number':
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
        if field_type in ('f16', 'f32', 'float', 'f64', 'double'):
            return float(value)
        
        return int(round(value))
    
    def _encode_field(self, field_def: Dict[str, Any], value: Any) -> bytes:
        """Encode a single field value."""
        field_type = field_def.get('type', 'u8')
        
        # Handle bitfields - simplified (just return byte with value)
        if any(c in str(field_type) for c in ['[', ':', '<']):
            return bytes([int(value) & 0xFF])
        
        type_info = INTEGER_TYPE_INFO

        # The inverse of the word-ordered read (PS-271): least significant 16-bit unit
        # first, each unit big-endian, and `endian` plays no part (PS-272).
        if field_type in ('u32le16', 's32le16'):
            int_val = int(value)
            if int_val < 0:
                int_val += 0x100000000
            int_val &= 0xFFFFFFFF
            return (int_val & 0xFFFF).to_bytes(2, 'big') + (int_val >> 16).to_bytes(2, 'big')

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
        
        if field_type == 'f16':
            fmt = '<e' if self.endian == Endian.LITTLE else '>e'
            return struct.pack(fmt, float(value))
        
        if field_type in ('f32', 'float'):
            fmt = '<f' if self.endian == Endian.LITTLE else '>f'
            return struct.pack(fmt, float(value))
        
        if field_type in ('f64', 'double'):
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
            elif isinstance(value, str):
                text = value.replace(' ', '')
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
        
        if field_type == 'version_string':
            return self._encode_version_string(field_def, str(value))
        
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
                if v == value:
                    int_value = int(k) if isinstance(k, str) else k
                    break
        elif isinstance(values, list):
            # Find index of value
            if value in values:
                int_value = values.index(value)
        
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
