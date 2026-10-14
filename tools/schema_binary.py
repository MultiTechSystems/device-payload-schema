#!/usr/bin/env python3
"""
schema_binary.py - Compact Binary Schema Encoder/Decoder

Writes and reads the binary schema form that include/schema_interpreter.h's
schema_load_binary() loads, described in the specification's companion document
docs/features/OTA-SCHEMA-TRANSFER.md ("Binary Schema Encoding").

Layout, format version 3 (CR-2026-142, PS-555):

  Header (5 bytes):
    'P' 'S'        magic
    0x03           format version. A reader refuses any other: version 1 put the
                   schema's own `version` here and carried no names, and 2 is
                   binary_schema_v2.py's different layout under the same magic.
    flags          bit 0 little-endian; bits 1-2 declared direction
    count          bit 7: a name table follows the records; bits 0-6: field count (<= 127)

  Field records, one per field (4 bytes minimum):
  +---------+----------+-----------+-----------+
  | Type    | Mult Exp | IPSO obj  | [Options] |
  | 1 byte  | 1 byte   | 2 bytes   | variable  |
  +---------+----------+-----------+-----------+
    Type byte: bit 7 lookup table follows; bits 4-6 type; bits 0-3 size
      0=uint 1=sint 2=float 3=bytes 4=bool 5=enum 6=bitfield 7=skip
    IPSO obj: the field's `ipso.object`, little-endian, 0 where none is declared. It is an
      annotation only: no reader derives a name from it (PS-555).

  Name table (where count bit 7 is set), one entry per record, in field order:
    1 length byte, then that many bytes of UTF-8. Length 0 only for a `skip`.

A form without the name table is for an interpreter that hands back values by field
index; nothing decoding from it may report the decoded object (PS-555).

Usage:
  python schema_binary.py encode schema.yaml -o schema.bin [--no-names]
  python schema_binary.py decode schema.bin -o schema.yaml
  python schema_binary.py dump schema.bin
  python schema_binary.py info schema.yaml
"""

import argparse
import struct
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
import re

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# Type codes (bits 4-6 of the type byte; bit 7 is the lookup flag)
TYPE_UINT = 0x0
TYPE_SINT = 0x1
TYPE_FLOAT = 0x2
TYPE_BYTES = 0x3
TYPE_BOOL = 0x4
TYPE_ENUM = 0x5
TYPE_BITFIELD = 0x6
# Format version 3 moved `skip` from 0x8, which the lookup flag shadowed, to 0x7, which
# held a `match` record no reader parsed and every writer refused (PS-446).
TYPE_SKIP = 0x7

#: The binary format version this module writes and the only one it reads.
FORMAT_VERSION = 3
#: Bit 7 of the count byte: a name table follows the field records (PS-555).
NAMES_FLAG = 0x80
#: Bits 0-6 of the count byte hold the field count.
MAX_FORMAT_FIELDS = 0x7F


class BinarySchemaFormatError(ValueError):
    """A blob that is not a well-formed binary schema of FORMAT_VERSION."""


def parse_type(type_str: str) -> Tuple[int, int, Optional[Tuple[int, int]]]:
    """Parse type string, return (type_code, size, bitfield_info)."""
    type_str = type_str.lower().strip()
    
    # Bitfield: u8[3:5]. The bracket range is the only spelling; the Verilog
    # part-select `u8[3+:2]` was withdrawn by CR-2026-006.
    bitfield_match = re.match(r'u8\[(\d+):(\d+)\]$', type_str)
    if bitfield_match:
        start, end = int(bitfield_match.group(1)), int(bitfield_match.group(2))
        width = end - start + 1
        return TYPE_BITFIELD, width, (start, width)


    # Standard types
    type_map = {
        'u8': (TYPE_UINT, 1), 'uint8': (TYPE_UINT, 1),
        'u16': (TYPE_UINT, 2), 'uint16': (TYPE_UINT, 2),
        'u24': (TYPE_UINT, 3),
        'u32': (TYPE_UINT, 4), 'uint32': (TYPE_UINT, 4),
        's8': (TYPE_SINT, 1), 'i8': (TYPE_SINT, 1), 'int8': (TYPE_SINT, 1),
        's16': (TYPE_SINT, 2), 'i16': (TYPE_SINT, 2), 'int16': (TYPE_SINT, 2),
        's24': (TYPE_SINT, 3),
        's32': (TYPE_SINT, 4), 'i32': (TYPE_SINT, 4), 'int32': (TYPE_SINT, 4),
        'f32': (TYPE_FLOAT, 4), 'float': (TYPE_FLOAT, 4),
        'f64': (TYPE_FLOAT, 8), 'double': (TYPE_FLOAT, 8),
        'f16': (TYPE_FLOAT, 2),
        'bool': (TYPE_BOOL, 1),
        'skip': (TYPE_SKIP, 1),
        'bytes': (TYPE_BYTES, 0),  # Variable
        'string': (TYPE_BYTES, 0),
        'ascii': (TYPE_BYTES, 0),
        'hex': (TYPE_BYTES, 0),
        'enum': (TYPE_ENUM, 1),
    }
    
    if type_str in type_map:
        code, size = type_map[type_str]
        return code, size, None
    
    return TYPE_UINT, 1, None  # Default


def mult_to_exp(mult: float) -> int:
    """Convert multiplier to signed exponent byte. mult = 10^exp."""
    if mult == 1.0:
        return 0
    if mult == 0:
        return 0
    
    # Handle common cases
    exp_map = {
        0.001: -3, 0.01: -2, 0.1: -1,
        10: 1, 100: 2, 1000: 3,
        0.0001: -4, 0.00001: -5,
        0.5: 0x80 | 1,  # Special: 0.5 = flag + scale
        0.0625: 0x80 | 4,  # 1/16
        0.00390625: 0x80 | 8,  # 1/256
    }
    
    if mult in exp_map:
        return exp_map[mult] & 0xFF
    
    # Calculate exponent
    import math
    if mult > 0:
        exp = round(math.log10(mult))
        return exp & 0xFF
    
    return 0


def exp_to_mult(exp: int) -> float:
    """Convert exponent byte back to multiplier."""
    if exp == 0:
        return 1.0
    
    # The binary scales the C loader knows (binary_exp_to_mult). Every other byte with
    # the high bit set is a negative power of ten: reading all of them as 1/2^n turned
    # 0xFE (0.01) into 2^-126.
    for factor, code in C_BINARY_SCALES.items():
        if exp == code:
            return factor

    # Signed byte
    if exp > 127:
        exp = exp - 256
    
    return 10.0 ** exp


def get_field_id(field: Dict) -> int:
    """The field's IPSO object for the record's ID bytes, else 0.

    An annotation carried along, never a source of the field's name (PS-555). This used
    to guess an object from the name and otherwise hash the name with Python's hash(),
    which is salted per process, so the same schema compiled to different bytes on every
    run.
    """
    ipso = field.get('ipso')
    if isinstance(ipso, dict) and isinstance(ipso.get('object'), int):
        return ipso['object'] & 0xFFFF
    sem = field.get('semantic')
    if isinstance(sem, dict) and isinstance(sem.get('ipso'), int):
        return sem['ipso'] & 0xFFFF
    return 0


#: Limits of the reader, include/schema_interpreter.h's schema_load_binary(), which is
#: what this format feeds. A table or schema past them is cut there without a word.
C_MAX_FIELDS = 32        # SCHEMA_MAX_FIELDS
C_MAX_LOOKUP = 16        # SCHEMA_MAX_LOOKUP
C_MAX_NAME_BYTES = 31    # SCHEMA_MAX_NAME_LEN - 1: the C loader refuses a longer name
MAX_LABEL_BYTES = 15     # this format's own cap on a lookup label

#: Field keys that change a decoded value and that neither this format nor the C
#: interpreter can carry. Each was dropped on the floor: the field encoded as though the
#: key were absent, and the C interpreter then decoded a different value with success.
#: PS-446 says an implementation that cannot apply one MUST reject the schema.
C_UNSUPPORTED_KEYS = {
    "transform": "the C interpreter has no transform pipeline",
    "polynomial": "the C interpreter has no transform pipeline (polynomial)",
    "compute": "the C interpreter has no computed fields",
    "ref": "the C interpreter has no computed fields (ref)",
    "guard": "the C interpreter has no guard",
    "encoding": "the C interpreter has no sign_magnitude/bcd/gray encodings",
    "numeral": "the C interpreter holds ascii text, not the integer it spells (PS-560)",
    "sentinel": "the C interpreter has no sentinel (PS-427)",
    "out_of_range": "the C interpreter has no out_of_range: omit (PS-428)",
    "default": ("the C interpreter has no enum default, and no field default to write "
                "where the input omits the field (PS-554)"),
    "var": "this format has no variable names",
    "optional": "the C interpreter has no optional fields (PS-402)",
    # Constructs with no record in this format. A field carrying one and no `type` was
    # read here as the default `u8`, one byte where the construct meant something else.
    "tlv": "this format has no tlv record",
    "flagged": "this format has no flagged record",
    "byte_group": "this format has no byte_group record",
    "repeat": "this format has no repeat record",
    # CR-2026-111: a repeat of single values and its index base (PS-528, PS-530). A
    # `type: repeat` is refused already; these name the keys where they appear.
    "element": "this format has no repeat record (PS-528)",
    "index_base": "this format has no repeat record (PS-530)",
    # CR-2026-156: a repeat's sample interval (PS-567).
    "sampled": "this format has no repeat record (PS-567)",
    # CR-2026-118: several records from one repeat element (PS-541).
    "records": "this format has no repeat record (PS-541)",
    "fields": "this format has no nested object record",
    "name_from": "this format has no name template",
    # Read only in a tlv case, which is already refused above; listed so a field
    # carrying it is refused by name wherever it sits (CR-2026-110).
    "on_repeat": "the C interpreter has no on_repeat (PS-525)",
    # A bytes rendering other than lowercase hex (PS-079, PS-391); the C interpreter
    # has neither key.
    "format": "the C interpreter has no bytes format (PS-079)",
    "separator": "the C interpreter has no bytes separator (PS-391)",
}

#: Wire types this format can name and the C loader maps back to the same type.
#: `ascii`, `hex` and `string` all became TYPE_BYTES, which the C interpreter reports as
#: raw bytes; `u64`/`s64` and every type not listed fell through to `u8`.
C_REPRESENTABLE_TYPES = {
    "u8", "u16", "u24", "u32", "s8", "s16", "s24", "s32",
    "f16", "f32", "f64", "bool", "bytes", "enum", "skip",
}
# `skip` was refused before format version 3: TYPE_SKIP was 0x8, and the C loader reads
# the type code as three bits because the type byte's high bit is the lookup flag, so a
# skip of 2 went out as 0x82 and came back as a u16 carrying a lookup table. Version 3
# gives it 0x7 (CR-2026-142), so a skip, whose name-table entry is empty, has a record.

#: Non-decimal scale factors the C loader decodes (binary_exp_to_mult).
C_BINARY_SCALES = {0.5: 0x81, 0.25: 0x82, 0.0625: 0x84}


class UnrepresentableSchemaError(ValueError):
    """A schema declares something this format, or the C interpreter, cannot carry."""


def _exact_scale_exp(factor: float) -> Optional[int]:
    """The exponent byte for `factor` where the C loader reproduces it, else None."""
    if factor == 1.0:
        return 0
    if factor in C_BINARY_SCALES:
        return C_BINARY_SCALES[factor]
    if factor <= 0:
        return None
    import math
    exp = round(math.log10(factor))
    if not -127 <= exp <= 127 or exp == 0:
        return None
    if not math.isclose(10.0 ** exp, factor, rel_tol=1e-12):
        return None
    return exp & 0xFF


def _scale_factor(field: Dict) -> float:
    mult = field.get('mult', 1.0)
    div = field.get('div', 1.0)
    return mult / div if div not in (0, 1.0) else mult


def field_errors(field: Dict, schema_endian: str = 'big') -> List[str]:
    """Everything in `field` this format would drop or alter on its way to C (PS-446).

    Empty where the field reaches the C interpreter decoding the value every other
    implementation decodes. The binary form has no way to report "I could not carry
    this", so the check has to happen here, before the bytes exist.
    """
    name = field.get('name', '?')
    errors = []

    def refuse(what: str, why: str, requirement: str = "PS-446") -> None:
        errors.append(f"field {name!r}: {what} - {why}; refused rather than dropped "
                      f"({requirement})")

    for key, why in C_UNSUPPORTED_KEYS.items():
        if key in field:
            refuse(f"`{key}`", why)

    # PS-555: the name table carries every name unchanged, in one length byte, and the C
    # loader holds C_MAX_NAME_BYTES of one. A skip has none (PS-553).
    raw_name = field.get('name')
    if raw_name is None or raw_name == '':
        if field.get('type') != 'skip':
            errors.append("a field with no `name` - every field but a skip has one "
                          "(PS-553), and the name table carries it (PS-555)")
    elif not isinstance(raw_name, str):
        refuse(f"`name: {raw_name!r}`", "a name is text")
    else:
        width = len(raw_name.encode('utf-8'))
        if width > 255:
            refuse(f"a {width}-byte name", "the name table's length is one byte",
                   "PS-555")
        elif width > C_MAX_NAME_BYTES:
            refuse(f"a {width}-byte name",
                   f"the C interpreter holds {C_MAX_NAME_BYTES} bytes of a name "
                   "(SCHEMA_MAX_NAME_LEN), and a name is never truncated", "PS-555")

    if (field.get('type') in ('bytes', 'hex', 'ascii') and 'consume' in field
            and field.get('consume') == 0 and not isinstance(field.get('consume'), bool)):
        # CR-2026-175: a byte string that re-reads its span (PS-588). The C interpreter
        # advances past every byte string, so the fields after it would read other bytes.
        refuse("`consume: 0` on a byte string",
               "the C interpreter has no non-consuming byte string (PS-588)")

    from schema_interpreter import is_peeked_number
    if is_peeked_number(field):
        # CR-2026-182: a whole number that reads in place (PS-603). The C interpreter
        # advances past every whole field, so the fields after it would read other bytes.
        refuse("`consume: 0` on a whole numeric field",
               "the C interpreter has no whole field that reads in place (PS-603)")

    if 'match' in field and not field.get('type'):
        refuse("`match`", "this format has no inline match record")
    ftype = str(field.get('type', ''))
    if not field.get('type'):
        if not errors:
            refuse("no `type`", "this format would encode it as a u8")
    elif re.match(r'^u8\[(\d+):(\d+)\]$', ftype):
        start, end = map(int, re.match(r'^u8\[(\d+):(\d+)\]$', ftype).groups())
        if not 0 <= start <= end <= 7:
            refuse(f"type {ftype!r}", "not a bit range of one byte")
        if field.get('consume', 0) not in (0, 1, None, False, True):
            # The record's consume marker is one byte, 0x01: `consume: 2` would come back
            # as 1. The C interpreter advances N bytes since CR-2026-136; the format
            # cannot say N, so it is refused rather than changed.
            refuse(f"`consume: {field.get('consume')!r}`",
                   "this format's consume marker advances one byte")
    elif re.match(r'^[us](8|16|24|32|64)\[\d+:\d+\]$', ftype):
        # The C interpreter reads a multi-byte or signed base since CR-2026-136, but the
        # bitfield record holds a start and a width only: its base is one unsigned byte.
        # Carrying N or the sign needs a record this format version does not have.
        refuse(f"type {ftype!r}", "this format's bitfield record has a one-byte "
               "unsigned base; a multi-byte or signed base has no slot")
    elif ftype == 'boolean':
        # A boolean literal (PS-519) reads no bytes; this format has no literal record,
        # so it would have gone out as a u8 and read a byte the payload does not carry.
        refuse("type 'boolean'", "this format has no literal record")
    elif ftype not in C_REPRESENTABLE_TYPES:
        refuse(f"type {ftype!r}", "this format has no such type, and wrote it as u8",
               "PS-327")

    if ftype == 'enum' and field.get('base', 'u8') != 'u8':
        refuse(f"enum base {field.get('base')!r}", "this format's enum is one byte")
    if ftype == 'bool' and field.get('bit', 0) not in (0, None):
        refuse("`bit`", "this format has no bit position for a bool")
    if ftype in ('bytes', 'skip'):
        length = field.get('length', 0 if ftype == 'bytes' else 1)
        if not isinstance(length, int) or not 0 <= length <= 15:
            refuse(f"length {length!r}", "this format's size nibble holds 0-15")

    field_endian = field.get('endian')
    if field_endian and field_endian != schema_endian:
        refuse(f"`endian: {field_endian}`",
               "this format has only the schema's byte order")

    for key in ('mult', 'div', 'add'):
        if key in field and (isinstance(field[key], bool)
                             or not isinstance(field[key], (int, float))):
            refuse(f"`{key}: {field[key]!r}`", "not a number")
    if not errors:
        if field.get('div') == 0:
            refuse("`div: 0`", "this format cannot carry a zero divisor (PS-100)")
        elif _exact_scale_exp(_scale_factor(field)) is None:
            refuse(f"scale {_scale_factor(field)!r} (mult/div)",
                   "this format carries only a power of ten, 0.5, 0.25 or 0.0625")
        add = field.get('add', 0)
        if not isinstance(add, bool) and isinstance(add, (int, float)) and add:
            hundredths = round(add * 100)
            if abs(hundredths - add * 100) > 1e-9 or not -32768 <= hundredths <= 32767:
                refuse(f"`add: {add!r}`",
                       "this format carries an add as a signed 16-bit count of "
                       "hundredths")

    table = field.get('lookup') if 'lookup' in field else field.get('values')
    if table is not None:
        if isinstance(table, list):
            items = list(enumerate(table))
        elif isinstance(table, dict):
            items = list(table.items())
        else:
            items = None
            refuse("the lookup", "neither a sequence nor a mapping")
        if items is not None:
            if 'default' in (str(k) for k, _ in items):
                refuse("the lookup's `default`",
                       "the C interpreter has no lookup default")
            if len(items) > C_MAX_LOOKUP:
                refuse(f"a {len(items)}-entry lookup",
                       f"the C interpreter keeps {C_MAX_LOOKUP} entries")
            for key, label in items:
                if str(key) == 'default':
                    continue
                try:
                    number = int(str(key), 0)
                except ValueError:
                    number = None
                if number is None or not 0 <= number <= 255:
                    refuse(f"lookup key {key!r}", "this format's keys are 0-255")
                if isinstance(label, dict):
                    label = label.get('name', label)
                if isinstance(label, (bool, int, float)):
                    # PS-106 allows a number or boolean label (CR-2026-104), reported as
                    # a JSON number or boolean. This format holds a label as text, so the
                    # C interpreter would report the string "5" or "True": refused
                    # rather than mis-typed.
                    refuse(f"lookup label {label!r}",
                           "this format holds a label as text, and a number or boolean "
                           "label is reported as one (PS-106)")
                    continue
                if len(str(label).encode('utf-8')) > MAX_LABEL_BYTES:
                    refuse(f"lookup label {label!r}",
                           f"this format holds {MAX_LABEL_BYTES} bytes of a label")
    return errors


def _clamp_stages(node, _top=True):
    """Every `{clamp: ...}` stage in a `transform` list of the schema's field lists."""
    if isinstance(node, dict):
        for key, value in node.items():
            if _top and key == 'test_vectors':
                continue
            if key == 'transform' and isinstance(value, list):
                for stage in value:
                    if isinstance(stage, dict) and 'clamp' in stage:
                        yield stage
            else:
                yield from _clamp_stages(value, False)
    elif isinstance(node, list):
        for item in node:
            yield from _clamp_stages(item, False)


def schema_errors(schema: Dict) -> List[str]:
    """Every field_errors() finding in a schema, plus what the C loader would cut."""
    errors = []
    endian = schema.get('endian', 'big')
    # PS-539 (CR-2026-117): a reference naming nothing declared is a schema error on
    # every path, the binary blob the C interpreter loads included.
    from schema_interpreter import expand_refs, reference_errors
    errors.extend(reference_errors(expand_refs(schema)[0]))     # after splicing (CR-2026-127)
    # PS-553 (CR-2026-138): a nameless field other than a skip is refused. Since PS-555
    # (CR-2026-142) the binary form carries the schema's names, and its name table gives a
    # length of 0 to a skip alone; the YAML is checked here, before conversion.
    from schema_interpreter import field_name_errors
    errors.extend(field_name_errors(schema))
    # CR-2026-159: `formula`, `encode_formula`, a transform's `clamp`, a port's `confirmed`
    # and `min`/`max` on a field that is no repeat are withdrawn, whatever the target.
    from schema_interpreter import WITHDRAWN_KEYS, withdrawn_errors
    errors.extend(withdrawn_errors(schema))
    errors.extend(WITHDRAWN_KEYS['clamp'] for _ in _clamp_stages(schema))
    if schema.get('ports'):
        errors.append("`ports` - the C interpreter has no port selection; refused "
                      "rather than dropped (PS-446)")
    fields = schema.get('fields', [])
    if len(fields) > MAX_FORMAT_FIELDS:
        errors.append(f"{len(fields)} fields - the binary format's field count is seven "
                      f"bits, at most {MAX_FORMAT_FIELDS} (CR-2026-142)")
    if len(fields) > C_MAX_FIELDS:
        errors.append(f"{len(fields)} fields - the C interpreter keeps {C_MAX_FIELDS}")
    previous = None
    for field in fields:
        if not isinstance(field, dict):
            errors.append(f"field {field!r} is not a mapping")
            continue
        errors.extend(field_errors(field, endian))
        # The consume marker after a bit range is the byte 0x01, which is also the
        # type byte of a plain u8. A bit range that does not consume, followed by one,
        # is read back as a bit range that does, with the u8 gone: every later field
        # shifts.
        if (previous is not None and _is_bit_range(previous)
                and not previous.get('consume')
                and field.get('type') == 'u8'
                and not (field.get('lookup') or field.get('values'))):
            errors.append(
                f"field {field.get('name', '?')!r}: a plain u8 after the non-consuming "
                f"bit range {previous.get('name', '?')!r} - its type byte 0x01 is this "
                "format's consume marker, so the reader would take it as one; refused "
                "rather than misread (PS-446)")
        previous = field
    return errors


def _is_bit_range(field: Dict) -> bool:
    return bool(re.match(r'^u8\[\d+:\d+\]$', str(field.get('type', ''))))


def encode_field(field: Dict) -> bytes:
    """Encode a single field to binary."""
    result = bytearray()
    
    type_str = field.get('type', 'u8')
    type_code, size, bitfield_info = parse_type(type_str)
    
    # Handle variable-length types
    if type_code == TYPE_BYTES:
        size = field.get('length', 0)
    elif type_code == TYPE_SKIP:
        size = field.get('length', 1)
    
    # Type byte: [TTTT SSSS]
    type_byte = ((type_code & 0x0F) << 4) | (size & 0x0F)
    
    # Check for lookup table
    has_lookup = bool(field.get('lookup') or field.get('values'))
    if has_lookup:
        type_byte |= 0x80  # Set high bit to indicate lookup follows
    
    result.append(type_byte)
    
    # Multiplier exponent. mult and div fold into one factor, which is the same
    # arithmetic in the order PS-101 fixes; field_errors() has already refused a factor
    # the C loader would not reproduce.
    scale = _exact_scale_exp(_scale_factor(field))
    result.append(mult_to_exp(_scale_factor(field)) if scale is None else scale)
    
    # Field ID (2 bytes, little-endian)
    field_id = get_field_id(field)
    result.extend(struct.pack('<H', field_id))
    
    # Bitfield info (if applicable)
    if bitfield_info:
        start, width = bitfield_info
        result.append((start << 4) | width)
        # Consume flag
        consume = field.get('consume', 0)
        if consume:
            result.append(0x01)
    
    # Add offset
    add = field.get('add', 0)
    if add != 0:
        result.append(0xA0)  # Add marker
        # Rounded, not truncated: int(0.29 * 100) is 28.
        result.extend(struct.pack('<h', round(add * 100)))  # Fixed point
    
    # Lookup table
    if has_lookup:
        lookup = field.get('lookup') or field.get('values', {})
        is_sequence = isinstance(lookup, list)
        if is_sequence:
            # Convert list to dict
            lookup = {i: v for i, v in enumerate(lookup)}
        
        # The high bit of the count marks a sequence. Both forms are stored keyed, and
        # without the mark the C interpreter cannot tell an out-of-bounds sequence index
        # (an error, PS-105) from a mapping gap (an omission, PS-269) - it treated every
        # flattened sequence as a mapping.
        assert len(lookup) <= 0x7F, "lookup table too large to carry the sequence flag"
        result.append(len(lookup) | (0x80 if is_sequence else 0x00))
        for key, value in lookup.items():
            result.append(int(key) & 0xFF)
            # An enum's description form reports its name (PS-394); str() of the
            # mapping went out as the label.
            if isinstance(value, dict) and 'name' in value:
                value = value['name']
            value_bytes = str(value).encode('utf-8')[:15]  # Max 15 chars
            result.append(len(value_bytes))
            result.extend(value_bytes)
    
    return bytes(result)


def encode_schema(schema: Dict, names: bool = True) -> bytes:
    """Encode full schema to binary, for include/schema_interpreter.h's
    schema_load_binary().

    `names` writes the name table (CR-2026-142, PS-555), which every reader that reports
    the decoded object needs. Leave it out only for an interpreter that hands values back
    by field index: the C loader then reports no name at all, never one made up.

    Raises UnrepresentableSchemaError, naming every field and property, where the schema
    declares something the format or the C interpreter cannot carry. This used to emit a
    blob for any schema, with each such property dropped: `transform: [{add: 1}]`,
    `encoding: bcd`, `sentinel`, `guard`, `compute` and `polynomial` all produced bytes
    the C interpreter decoded, with success, as if the field had a plain type. PS-446
    makes that a rejection.
    """
    errors = schema_errors(schema)
    fields = schema.get('fields', [])
    if (names and not errors and fields and _is_bit_range(fields[-1])
            and not fields[-1].get('consume')
            and len((fields[0].get('name') or '').encode('utf-8')) == 1):
        # The same ambiguity schema_errors() refuses between records: the first name's
        # length byte 0x01 would be read as the last bit range's consume marker.
        errors.append(
            f"field {fields[-1].get('name', '?')!r}: a bit range that does not consume, "
            f"last, before a one-byte first name {fields[0].get('name')!r} - its length "
            "byte 0x01 is this format's consume marker; refused rather than misread "
            "(PS-446)")
    if errors:
        raise UnrepresentableSchemaError(
            f"schema {schema.get('name', '?')!r} cannot be carried by the binary "
            f"schema format to the C interpreter:\n  " + "\n  ".join(errors))
    result = bytearray()

    # Header
    result.append(0x50)  # Magic: 'P' for Payload Schema
    result.append(0x53)  # Magic: 'S'
    # The format's version, not the schema's own `version`, which version 1 put here.
    result.append(FORMAT_VERSION)

    # Flags
    flags = 0
    if schema.get('endian', 'big') == 'little':
        flags |= 0x01
    # Bits 1-2 carry the schema's declared direction, so a binary-loaded schema keeps it
    # (PS-291). Dropping it here would leave the C interpreter unable to perform the
    # check no matter what the caller supplied - the failure CR-2026-009 found when this
    # format flattened a sequence lookup into a mapping.
    flags |= {None: 0, 'uplink': 1, 'downlink': 2, 'both': 3}[schema.get('direction')] << 1
    result.append(flags)

    # Field count, bits 0-6; bit 7 announces the name table.
    fields = schema.get('fields', [])
    result.append(len(fields) | (NAMES_FLAG if names else 0))

    for field in fields:
        result.extend(encode_field(field))

    if names:
        for field in fields:
            name = (field.get('name') or '').encode('utf-8')
            result.append(len(name))
            result.extend(name)

    return bytes(result)


def _need(data: bytes, offset: int, count: int, what: str) -> None:
    if offset + count > len(data):
        raise BinarySchemaFormatError(
            f"{what} at offset {offset} needs {count} byte(s), {len(data) - offset} "
            "remain")


_TYPE_NAMES = {
    TYPE_UINT: {1: 'u8', 2: 'u16', 3: 'u24', 4: 'u32'},
    TYPE_SINT: {1: 's8', 2: 's16', 3: 's24', 4: 's32'},
    TYPE_FLOAT: {2: 'f16', 4: 'f32', 8: 'f64'},
    TYPE_BOOL: {1: 'bool'},
    TYPE_ENUM: {1: 'enum'},
}


def decode_field(data: bytes, offset: int) -> Tuple[Dict, int]:
    """Decode one field record. Returns (field_dict, new_offset); the name comes from
    the name table, never from the record (PS-555)."""
    field = {}
    where = f"field record at offset {offset}"

    _need(data, offset, 4, where)
    type_byte = data[offset]
    has_lookup = bool(type_byte & 0x80)
    type_code = (type_byte >> 4) & 0x07
    size = type_byte & 0x0F
    mult = exp_to_mult(data[offset + 1])
    field_id = struct.unpack_from('<H', data, offset + 2)[0]
    offset += 4

    if type_code == TYPE_BITFIELD:
        _need(data, offset, 1, where)
        bf_byte = data[offset]
        start = (bf_byte >> 4) & 0x0F
        width = bf_byte & 0x0F
        offset += 1
        field['type'] = f'u8[{start}:{start + width - 1}]'
        if offset < len(data) and data[offset] == 0x01:
            field['consume'] = 1
            offset += 1
    elif type_code == TYPE_SKIP:
        field['type'] = 'skip'
        field['length'] = size
    elif type_code == TYPE_BYTES:
        field['type'] = 'bytes'
        field['length'] = size
    else:
        type_name = _TYPE_NAMES.get(type_code, {}).get(size)
        if type_name is None:
            raise BinarySchemaFormatError(
                f"{where}: type {type_code} with size {size} names no type")
        field['type'] = type_name

    if field_id:
        field['ipso'] = {'object': field_id}
    if mult != 1.0:
        field['mult'] = mult

    if offset < len(data) and data[offset] == 0xA0:
        _need(data, offset, 3, where)
        field['add'] = struct.unpack_from('<h', data, offset + 1)[0] / 100.0
        offset += 3

    if has_lookup:
        _need(data, offset, 1, where)
        header = data[offset]
        offset += 1
        lookup = {}
        for _ in range(header & 0x7F):
            _need(data, offset, 2, where)
            key, str_len = data[offset], data[offset + 1]
            offset += 2
            _need(data, offset, str_len, where)
            lookup[key] = data[offset:offset + str_len].decode('utf-8')
            offset += str_len
        if header & 0x80:
            field['lookup'] = [lookup[k] for k in sorted(lookup)]
        else:
            field['lookup'] = lookup

    return field, offset


def decode_schema(data: bytes) -> Dict:
    """Decode a binary schema of FORMAT_VERSION to a schema dict.

    With a name table every field carries its name, unchanged (PS-555). Without one the
    fields carry none, and the dict is a description of the records rather than a schema
    that could report a decoded object; `names` is False there. A blob of another version,
    one that ends inside a record or a name, or one with bytes after the last name is a
    BinarySchemaFormatError.
    """
    if len(data) < 5:
        raise BinarySchemaFormatError("Binary too short")
    if data[0:2] != b'PS':
        raise BinarySchemaFormatError("Invalid magic bytes")
    if data[2] != FORMAT_VERSION:
        raise BinarySchemaFormatError(
            f"binary schema format version {data[2]} is not supported; this reader "
            f"reads version {FORMAT_VERSION} (CR-2026-142)")

    has_names = bool(data[4] & NAMES_FLAG)
    schema = {
        'endian': 'little' if data[3] & 0x01 else 'big',
        'names': has_names,
        'fields': [],
    }
    direction = (data[3] >> 1) & 0x03
    if direction:
        schema['direction'] = {1: 'uplink', 2: 'downlink', 3: 'both'}[direction]

    offset = 5
    for _ in range(data[4] & 0x7F):
        field, offset = decode_field(data, offset)
        schema['fields'].append(field)

    if has_names:
        for index, field in enumerate(schema['fields']):
            _need(data, offset, 1, f"name table entry {index}")
            length = data[offset]
            offset += 1
            _need(data, offset, length, f"name of field {index}")
            name = data[offset:offset + length].decode('utf-8')
            offset += length
            if not name and field['type'] != 'skip':
                raise BinarySchemaFormatError(
                    f"field {index} ({field['type']}) has an empty name; only a skip "
                    "has none (PS-553)")
            # Rebuilt so the name leads, as a schema writes it.
            named = {'name': name} if name else {}
            named.update(field)
            schema['fields'][index] = named

    if offset != len(data):
        raise BinarySchemaFormatError(
            f"{len(data) - offset} byte(s) after the last "
            f"{'name' if has_names else 'field record'}, from offset {offset}")
    return schema


def dump_binary(data: bytes) -> str:
    """Create an annotated hex dump."""
    lines = [f"Binary Schema: {len(data)} bytes", "-" * 60]
    if len(data) < 5:
        lines.append("ERROR: Too short")
        return '\n'.join(lines)

    lines.append(f"00-01: {data[0]:02X} {data[1]:02X}  Magic: 'PS'")
    lines.append(f"02:    {data[2]:02X}        Format version: {data[2]}")
    flags = data[3]
    lines.append(f"03:    {flags:02X}        Flags: "
                 f"{'little-endian' if flags & 1 else 'big-endian'}")
    has_names = bool(data[4] & NAMES_FLAG)
    lines.append(f"04:    {data[4]:02X}        Field count: {data[4] & 0x7F}, name table: "
                 f"{'yes' if has_names else 'no (values by index only)'}")
    try:
        schema = decode_schema(data)
    except (BinarySchemaFormatError, UnicodeDecodeError) as exc:
        lines.append(f"ERROR: {exc}")
        return '\n'.join(lines)

    offset = 5
    records = []
    for index in range(data[4] & 0x7F):
        _, end = decode_field(data, offset)
        records.append((offset, end))
        offset = end
    kinds = ['uint', 'sint', 'float', 'bytes', 'bool', 'enum', 'bits', 'skip']
    for index, ((start, end), field) in enumerate(zip(records, schema['fields'])):
        type_byte = data[start]
        name = field.get('name')
        label = repr(name) if name else ('(no name)' if has_names else f'#{index}')
        lines.append("")
        lines.append(f"Field {index}: {label}  {field['type']}")
        lines.append(f"  {start:02X}: {data[start:end].hex(' ').upper()}")
        lines.append(f"      type {kinds[(type_byte >> 4) & 7]}, size {type_byte & 0x0F}, "
                     f"lookup {'yes' if type_byte & 0x80 else 'no'}, "
                     f"mult {exp_to_mult(data[start + 1])}, "
                     f"IPSO object {struct.unpack_from('<H', data, start + 2)[0]}")
    if has_names:
        lines.append("")
        lines.append(f"Name table at {offset:02X}:")
        for index, field in enumerate(schema['fields']):
            length = data[offset]
            lines.append(f"  {offset:02X}: {data[offset:offset + 1 + length].hex(' ').upper()}"
                         f"  {index}: {field.get('name', '')!r}")
            offset += 1 + length
    return '\n'.join(lines)


def _load_yaml_checked(path: Path):
    """Read a schema's YAML text; PS-575 refuses a plain YAML 1.1 boolean first."""
    from schema_interpreter import YamlBooleanError, check_yaml_booleans
    text = path.read_text()
    try:
        check_yaml_booleans(text)
    except YamlBooleanError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    return yaml.safe_load(text)


def main():
    parser = argparse.ArgumentParser(description='Binary schema encoder/decoder')
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # Encode
    enc = subparsers.add_parser('encode', help='Encode YAML schema to binary')
    enc.add_argument('input', type=Path, help='Input YAML file')
    enc.add_argument('-o', '--output', type=Path, help='Output binary file')
    enc.add_argument('--base64', action='store_true', help='Output as base64')
    enc.add_argument('--no-names', action='store_true',
                     help='leave out the name table, for an interpreter that hands '
                          'back values by field index (PS-555)')
    
    # Decode
    dec = subparsers.add_parser('decode', help='Decode binary to YAML')
    dec.add_argument('input', type=Path, help='Input binary file')
    dec.add_argument('-o', '--output', type=Path, help='Output YAML file')
    
    # Dump
    dmp = subparsers.add_parser('dump', help='Hex dump with annotations')
    dmp.add_argument('input', type=Path, help='Input binary file')
    
    # Info
    inf = subparsers.add_parser('info', help='Show schema size info')
    inf.add_argument('input', type=Path, help='Input YAML file')
    
    args = parser.parse_args()
    
    if not HAS_YAML:
        print("Error: PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    
    if args.command == 'encode':
        schema = _load_yaml_checked(args.input)
        try:
            binary = encode_schema(schema, names=not args.no_names)
        except UnrepresentableSchemaError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        
        if args.base64:
            import base64
            output = base64.b64encode(binary).decode('ascii')
            print(output)
        elif args.output:
            args.output.write_bytes(binary)
            print(f"Encoded to {args.output} ({len(binary)} bytes)", file=sys.stderr)
        else:
            # Hex output
            print(binary.hex())
        
        print(f"# YAML: {args.input.stat().st_size} bytes -> Binary: {len(binary)} bytes", file=sys.stderr)
        print(f"# Compression: {args.input.stat().st_size / len(binary):.1f}x", file=sys.stderr)
    
    elif args.command == 'decode':
        binary = args.input.read_bytes()
        try:
            schema = decode_schema(binary)
        except BinarySchemaFormatError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        
        output = yaml.dump(schema, default_flow_style=False, sort_keys=False)
        
        if args.output:
            args.output.write_text(output)
            print(f"Decoded to {args.output}", file=sys.stderr)
        else:
            print(output)
    
    elif args.command == 'dump':
        binary = args.input.read_bytes()
        print(dump_binary(binary))
    
    elif args.command == 'info':
        schema = _load_yaml_checked(args.input)
        try:
            binary = encode_schema(schema)
        except UnrepresentableSchemaError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        nameless = encode_schema(schema, names=False)

        yaml_size = args.input.stat().st_size
        bin_size = len(binary)
        names = [f.get('name') or '' for f in schema.get('fields', [])]

        print(f"Schema: {schema.get('name', 'unnamed')}")
        print(f"Fields: {len(names)}")
        for index, name in enumerate(names):
            print(f"  {index:3d}  {name!r}" if name else f"  {index:3d}  (skip, no name)")
        print(f"YAML size: {yaml_size} bytes")
        print(f"Binary size: {bin_size} bytes with names "
              f"({bin_size - len(nameless)} in the name table), "
              f"{len(nameless)} without")
        print(f"Compression: {yaml_size / bin_size:.1f}x")
        print(f"LoRaWAN packets (DR0, 51 bytes): {(bin_size + 50) // 51} "
              f"({(len(nameless) + 50) // 51} without names)")
        print(f"LoRaWAN packets (DR3, 115 bytes): {(bin_size + 114) // 115} "
              f"({(len(nameless) + 114) // 115} without names)")
        print(f"QR code friendly: {'Yes' if bin_size < 100 else 'No (consider compression)'}")


if __name__ == '__main__':
    main()
