#!/usr/bin/env python3
"""
schema_binary.py - Compact Binary Schema Encoder/Decoder

Creates the binary schema format defined in Payload Schema Section 17 (OTA Transfer).

Binary format (per field, 4 bytes minimum):
┌─────────┬──────────┬───────────┬───────────┐
│ Type    │ Mult Exp │ Field ID  │ [Options] │
│ 1 byte  │ 1 byte   │ 2 bytes   │ variable  │
└─────────┴──────────┴───────────┴───────────┘

Type byte: [TTTT SSSS]
  TTTT = Type (4 bits): 0=uint, 1=sint, 2=float, 3=bytes, 4=bool, 5=enum, 6=bitfield
  SSSS = Size (4 bits): byte count or bit count for bitfields

Usage:
  # Encode schema to binary
  python schema_binary.py encode schema.yaml -o schema.bin
  
  # Decode binary to YAML
  python schema_binary.py decode schema.bin -o schema.yaml
  
  # Show binary hex dump with annotations
  python schema_binary.py dump schema.bin
  
  # Get info/stats
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

# Type codes (4 bits)
TYPE_UINT = 0x0
TYPE_SINT = 0x1
TYPE_FLOAT = 0x2
TYPE_BYTES = 0x3
TYPE_BOOL = 0x4
TYPE_ENUM = 0x5
TYPE_BITFIELD = 0x6
TYPE_MATCH = 0x7
TYPE_SKIP = 0x8
TYPE_LOOKUP = 0x9  # Field has lookup table following

# IPSO Smart Object IDs (common ones)
IPSO_IDS = {
    'temperature': 3303,
    'humidity': 3304,
    'pressure': 3315,
    'voltage': 3316,
    'current': 3317,
    'power': 3328,
    'energy': 3331,
    'distance': 3330,
    'illuminance': 3301,
    'presence': 3302,
    'accel_x': 3313,
    'accel_y': 3314,
    'accel_z': 3315,
    'latitude': 3336,
    'longitude': 3337,
    'altitude': 3338,
}

# Reverse lookup
IPSO_NAMES = {v: k for k, v in IPSO_IDS.items()}


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
        'match': (TYPE_MATCH, 0),
        'object': (TYPE_MATCH, 0),  # Nested
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
    
    # Handle special flag for non-power-of-10
    if exp & 0x80:
        scale = exp & 0x7F
        return 1.0 / (2 ** scale)
    
    # Signed byte
    if exp > 127:
        exp = exp - 256
    
    return 10.0 ** exp


def get_field_id(field: Dict) -> int:
    """Get numeric field ID from field definition."""
    # Check for explicit IPSO mapping
    if 'semantic' in field:
        sem = field['semantic']
        if isinstance(sem, dict) and 'ipso' in sem:
            return sem['ipso']
    
    # Check name against known IPSO
    name = field.get('name', '').lower()
    if name in IPSO_IDS:
        return IPSO_IDS[name]
    
    # Generate hash-based ID for unknown fields
    name_bytes = field.get('name', '').encode('utf-8')
    return (hash(name_bytes) & 0xFFFF) | 0x8000  # Set high bit for custom


#: Limits of the reader, include/schema_interpreter.h's schema_load_binary(), which is
#: what this format feeds. A table or schema past them is cut there without a word.
C_MAX_FIELDS = 32        # SCHEMA_MAX_FIELDS
C_MAX_LOOKUP = 16        # SCHEMA_MAX_LOOKUP
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
    "formula": "the C interpreter has no formula",
    "guard": "the C interpreter has no guard",
    "encoding": "the C interpreter has no sign_magnitude/bcd/gray encodings",
    "sentinel": "the C interpreter has no sentinel (PS-427)",
    "out_of_range": "the C interpreter has no out_of_range: omit (PS-428)",
    "default": "the C interpreter has no enum/lookup default",
    "var": "this format has no variable names",
    "optional": "the C interpreter has no optional fields (PS-402)",
    # Constructs with no record in this format. A field carrying one and no `type` was
    # read here as the default `u8`, one byte where the construct meant something else.
    "tlv": "this format has no tlv record",
    "flagged": "this format has no flagged record",
    "byte_group": "this format has no byte_group record",
    "repeat": "this format has no repeat record",
    "fields": "this format has no nested object record",
    "name_from": "this format has no name template",
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
    "f16", "f32", "f64", "bool", "bytes", "enum",
}
# `skip` is not among them. TYPE_SKIP is 0x8, and the C loader reads the type code
# as three bits because the type byte's high bit is the lookup flag: a skip of 2 went
# out as 0x82 and came back as a u16 carrying a lookup table, which it then tried to
# read from the following field's bytes. skip-type.yaml decoded `b` as 65535 that way.

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


def schema_errors(schema: Dict) -> List[str]:
    """Every field_errors() finding in a schema, plus what the C loader would cut."""
    errors = []
    endian = schema.get('endian', 'big')
    if schema.get('ports'):
        errors.append("`ports` - the C interpreter has no port selection; refused "
                      "rather than dropped (PS-446)")
    fields = schema.get('fields', [])
    if len(fields) > C_MAX_FIELDS:
        errors.append(f"{len(fields)} fields - the C interpreter keeps {C_MAX_FIELDS}")
    previous = None
    for field in fields:
        if not isinstance(field, dict):
            errors.append(f"field {field!r} is not a mapping")
            continue
        if field.get('type') == 'match':
            # encode_schema() writes a match record, which schema_load_binary() does not
            # parse: it reads the record's bytes as ordinary fields.
            errors.append(f"field {field.get('name', '?')!r}: `type: match` - the C "
                          "binary loader has no match record; refused rather than "
                          "dropped (PS-446)")
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


def encode_schema(schema: Dict) -> bytes:
    """Encode full schema to binary, for include/schema_interpreter.h's
    schema_load_binary().

    Raises UnrepresentableSchemaError, naming every field and property, where the schema
    declares something the format or the C interpreter cannot carry. This used to emit a
    blob for any schema, with each such property dropped: `transform: [{add: 1}]`,
    `encoding: bcd`, `sentinel`, `guard`, `compute` and `polynomial` all produced bytes
    the C interpreter decoded, with success, as if the field had a plain type. PS-446
    makes that a rejection.
    """
    errors = schema_errors(schema)
    if errors:
        raise UnrepresentableSchemaError(
            f"schema {schema.get('name', '?')!r} cannot be carried by the binary "
            f"schema format to the C interpreter:\n  " + "\n  ".join(errors))
    result = bytearray()
    
    # Header
    result.append(0x50)  # Magic: 'P' for Payload Schema
    result.append(0x53)  # Magic: 'S'
    result.append(schema.get('version', 1))
    
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
    
    # Field count
    fields = schema.get('fields', [])
    result.append(len(fields))
    
    # Encode each field
    for field in fields:
        field_type = field.get('type', '')
        
        # Handle match/nested
        if field_type == 'match':
            result.append((TYPE_MATCH << 4) | 0)
            # Variable name
            var_name = field.get('on', field.get('true', '')).lstrip('$')
            var_bytes = var_name.encode('utf-8')[:15]
            result.append(len(var_bytes))
            result.extend(var_bytes)
            
            # Cases
            cases = field.get('cases', [])
            result.append(len(cases))
            for case in cases:
                if 'default' in case:
                    result.append(0xFF)  # Default marker
                else:
                    case_val = case.get('case', 0)
                    result.append(case_val & 0xFF)
                
                # Case fields
                case_fields = case.get('fields', [])
                result.append(len(case_fields))
                for cf in case_fields:
                    result.extend(encode_field(cf))
        else:
            result.extend(encode_field(field))
    
    return bytes(result)


def decode_field(data: bytes, offset: int) -> Tuple[Dict, int]:
    """Decode a single field from binary. Returns (field_dict, new_offset)."""
    field = {}
    
    type_byte = data[offset]
    has_lookup = bool(type_byte & 0x80)
    type_code = (type_byte >> 4) & 0x07
    size = type_byte & 0x0F
    offset += 1
    
    # Multiplier
    mult_exp = data[offset]
    mult = exp_to_mult(mult_exp)
    offset += 1
    
    # Field ID
    field_id = struct.unpack_from('<H', data, offset)[0]
    offset += 2
    
    # Determine type string
    type_names = {
        TYPE_UINT: {1: 'u8', 2: 'u16', 3: 'u24', 4: 'u32'},
        TYPE_SINT: {1: 's8', 2: 's16', 3: 's24', 4: 's32'},
        TYPE_FLOAT: {2: 'f16', 4: 'f32', 8: 'f64'},
        TYPE_BOOL: {1: 'bool'},
        TYPE_SKIP: {0: 'skip'},
        TYPE_BYTES: {0: 'bytes'},
        TYPE_ENUM: {1: 'enum'},
    }
    
    if type_code == TYPE_BITFIELD:
        # Read bitfield info
        bf_byte = data[offset]
        start = (bf_byte >> 4) & 0x0F
        width = bf_byte & 0x0F
        offset += 1
        field['type'] = f'u8[{start}:{start + width - 1}]'
        
        # Check consume
        if offset < len(data) and data[offset] == 0x01:
            field['consume'] = 1
            offset += 1
    else:
        type_map = type_names.get(type_code, {})
        field['type'] = type_map.get(size, 'u8')
    
    # Name from IPSO or generate
    if field_id in IPSO_NAMES:
        field['name'] = IPSO_NAMES[field_id]
    elif field_id & 0x8000:
        field['name'] = f'field_{field_id & 0x7FFF:04x}'
    else:
        field['name'] = f'ipso_{field_id}'
    
    # Multiplier
    if mult != 1.0:
        field['mult'] = mult
    
    # Check for add marker
    if offset < len(data) and data[offset] == 0xA0:
        offset += 1
        add_val = struct.unpack_from('<h', data, offset)[0] / 100.0
        field['add'] = add_val
        offset += 2
    
    # Lookup table
    if has_lookup:
        lookup_count = data[offset]
        offset += 1
        lookup = {}
        for _ in range(lookup_count):
            key = data[offset]
            offset += 1
            str_len = data[offset]
            offset += 1
            value = data[offset:offset + str_len].decode('utf-8')
            offset += str_len
            lookup[key] = value
        field['lookup'] = lookup
    
    return field, offset


def decode_schema(data: bytes) -> Dict:
    """Decode binary schema to dict."""
    if len(data) < 5:
        raise ValueError("Binary too short")
    
    if data[0:2] != b'PS':
        raise ValueError("Invalid magic bytes")
    
    schema = {
        'version': data[2],
        'endian': 'little' if data[3] & 0x01 else 'big',
        'fields': []
    }
    
    field_count = data[4]
    offset = 5
    
    for _ in range(field_count):
        if offset >= len(data):
            break
        
        type_peek = data[offset]
        type_code = (type_peek >> 4) & 0x0F
        
        if type_code == TYPE_MATCH:
            # Match field
            offset += 1
            var_len = data[offset]
            offset += 1
            var_name = data[offset:offset + var_len].decode('utf-8')
            offset += var_len
            
            match_field = {
                'type': 'match',
                'on': f'${var_name}',
                'cases': []
            }
            
            case_count = data[offset]
            offset += 1
            
            for _ in range(case_count):
                case_val = data[offset]
                offset += 1
                
                case_obj = {}
                if case_val == 0xFF:
                    case_obj['default'] = 'skip'
                else:
                    case_obj['case'] = case_val
                
                case_field_count = data[offset]
                offset += 1
                case_obj['fields'] = []
                
                for _ in range(case_field_count):
                    cf, offset = decode_field(data, offset)
                    case_obj['fields'].append(cf)
                
                match_field['cases'].append(case_obj)
            
            schema['fields'].append(match_field)
        else:
            field, offset = decode_field(data, offset)
            schema['fields'].append(field)
    
    return schema


def dump_binary(data: bytes) -> str:
    """Create annotated hex dump."""
    lines = []
    lines.append(f"Binary Schema: {len(data)} bytes")
    lines.append("-" * 60)
    
    if len(data) < 5:
        lines.append("ERROR: Too short")
        return '\n'.join(lines)
    
    lines.append(f"00-01: {data[0]:02X} {data[1]:02X}  Magic: 'PS'")
    lines.append(f"02:    {data[2]:02X}        Version: {data[2]}")
    lines.append(f"03:    {data[3]:02X}        Flags: {'little-endian' if data[3] & 1 else 'big-endian'}")
    lines.append(f"04:    {data[4]:02X}        Field count: {data[4]}")
    
    offset = 5
    field_num = 0
    
    while offset < len(data):
        lines.append("")
        type_byte = data[offset]
        type_code = (type_byte >> 4) & 0x07
        size = type_byte & 0x0F
        has_lookup = bool(type_byte & 0x80)
        
        type_names = ['uint', 'sint', 'float', 'bytes', 'bool', 'enum', 'bits', 'match', 'skip', 'lookup']
        type_name = type_names[type_code] if type_code < len(type_names) else 'unknown'
        
        lines.append(f"Field {field_num}:")
        lines.append(f"  {offset:02X}: {type_byte:02X}  Type: {type_name}, size: {size}, lookup: {has_lookup}")
        offset += 1
        
        if offset >= len(data):
            break
        
        if type_code == TYPE_MATCH:
            var_len = data[offset]
            offset += 1
            var_name = data[offset:offset + var_len].decode('utf-8', errors='replace')
            offset += var_len
            lines.append(f"       Match on: ${var_name}")
            # Skip case details for dump
            break
        
        mult_exp = data[offset]
        mult = exp_to_mult(mult_exp)
        lines.append(f"  {offset:02X}: {mult_exp:02X}  Mult exp: {mult_exp} -> {mult}")
        offset += 1
        
        if offset + 1 >= len(data):
            break
        
        field_id = struct.unpack_from('<H', data, offset)[0]
        name = IPSO_NAMES.get(field_id, f'custom_{field_id:04X}')
        lines.append(f"  {offset:02X}: {data[offset]:02X} {data[offset+1]:02X}  Field ID: {field_id} ({name})")
        offset += 2
        
        field_num += 1
        
        # Skip detailed parsing for dump
        if has_lookup and offset < len(data):
            lookup_count = data[offset]
            lines.append(f"  {offset:02X}: {lookup_count:02X}  Lookup entries: {lookup_count}")
            offset += 1
            # Skip lookup data
            for _ in range(lookup_count):
                if offset >= len(data):
                    break
                offset += 1  # key
                if offset >= len(data):
                    break
                str_len = data[offset]
                offset += 1 + str_len
    
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Binary schema encoder/decoder')
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # Encode
    enc = subparsers.add_parser('encode', help='Encode YAML schema to binary')
    enc.add_argument('input', type=Path, help='Input YAML file')
    enc.add_argument('-o', '--output', type=Path, help='Output binary file')
    enc.add_argument('--base64', action='store_true', help='Output as base64')
    
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
        schema = yaml.safe_load(args.input.read_text())
        try:
            binary = encode_schema(schema)
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
        schema = decode_schema(binary)
        
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
        schema = yaml.safe_load(args.input.read_text())
        binary = encode_schema(schema)
        
        yaml_size = args.input.stat().st_size
        bin_size = len(binary)
        
        print(f"Schema: {schema.get('name', 'unnamed')}")
        print(f"Fields: {len(schema.get('fields', []))}")
        print(f"YAML size: {yaml_size} bytes")
        print(f"Binary size: {bin_size} bytes")
        print(f"Compression: {yaml_size / bin_size:.1f}x")
        print(f"LoRaWAN packets (DR0, 51 bytes): {(bin_size + 50) // 51}")
        print(f"LoRaWAN packets (DR3, 115 bytes): {(bin_size + 114) // 115}")
        print(f"QR code friendly: {'Yes' if bin_size < 100 else 'No (consider compression)'}")


if __name__ == '__main__':
    main()
