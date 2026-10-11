#!/usr/bin/env python3
"""
binary_schema_loader.py - Load binary schemas for fast interpretation

Loads the compact binary schema format and converts to interpreter format,
or provides direct binary-to-decode capability for maximum performance.

Performance benefits:
- No YAML/JSON parsing (biggest win)
- Pre-parsed type codes (no string matching)
- Direct struct unpacking
- Memory-mappable for C interpreters
"""

import struct
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple, BinaryIO
from enum import IntEnum


class TypeCode(IntEnum):
    """Binary type codes (4 bits)."""
    UINT = 0x0
    SINT = 0x1
    FLOAT = 0x2
    BYTES = 0x3
    BOOL = 0x4
    ENUM = 0x5
    BITFIELD = 0x6
    MATCH = 0x7
    SKIP = 0x8


@dataclass
class BinaryField:
    """Pre-parsed field for fast decoding."""
    name: str
    type_code: TypeCode
    size: int  # bytes or bits for bitfield
    mult: float = 1.0
    add: float = 0.0
    field_id: int = 0
    bitfield_start: int = 0
    bitfield_width: int = 0
    consume: bool = True
    lookup: Optional[Dict[int, str]] = None
    
    # For match fields
    match_var: str = ''
    cases: List[Tuple[int, List['BinaryField']]] = field(default_factory=list)


@dataclass 
class BinarySchema:
    """Pre-parsed schema for fast decoding."""
    version: int
    endian: str  # 'big' or 'little'
    fields: List[BinaryField]
    
    # Original dict for compatibility
    _dict_cache: Optional[Dict] = None


def exp_to_mult(exp: int) -> float:
    """Convert exponent byte to multiplier."""
    if exp == 0:
        return 1.0
    
    # Check for special 0.5-based multipliers (0x80 | scale)
    # 0x81 = 0.5, 0x82 = 0.25, etc.
    if exp == 0x81:
        return 0.5
    if exp == 0x82:
        return 0.25
    if exp == 0x84:
        return 0.0625
    
    # Treat as signed byte for power-of-10
    if exp > 127:
        exp = exp - 256
    
    return 10.0 ** exp


def _field_from_dict(field: Dict[str, Any]) -> BinaryField:
    """A BinaryField from one field of schema_binary.decode_schema()'s output."""
    import re
    type_str = field['type']
    bits = re.match(r'^u8\[(\d+):(\d+)\]$', type_str)
    sizes = {'8': 1, '16': 2, '24': 3, '32': 4, '64': 8}
    if bits:
        start, end = int(bits.group(1)), int(bits.group(2))
        code, size = TypeCode.BITFIELD, end - start + 1
    elif type_str in ('skip', 'bytes'):
        code = TypeCode.SKIP if type_str == 'skip' else TypeCode.BYTES
        size = field.get('length', 0)
    elif type_str in ('bool', 'enum'):
        code, size = (TypeCode.BOOL if type_str == 'bool' else TypeCode.UINT), 1
    else:
        code = {'u': TypeCode.UINT, 's': TypeCode.SINT, 'f': TypeCode.FLOAT}[type_str[0]]
        size = sizes[type_str[1:]]
    lookup = field.get('lookup')
    if isinstance(lookup, list):
        lookup = dict(enumerate(lookup))
    return BinaryField(
        name=field.get('name', ''),
        type_code=code,
        size=size,
        mult=field.get('mult', 1.0),
        add=field.get('add', 0.0),
        field_id=(field.get('ipso') or {}).get('object', 0),
        bitfield_start=int(bits.group(1)) if bits else 0,
        bitfield_width=size if bits else 0,
        consume=bool(field.get('consume')) if bits else True,
        lookup=lookup,
    )


def load_binary_schema(data: bytes) -> BinarySchema:
    """Load a schema from tools/schema_binary.py's binary form.

    The blob is parsed by schema_binary.decode_schema(), so there is one reader of the
    format: names come from its name table (PS-555, CR-2026-142). This module used to
    parse the records itself and name each field from its IPSO object - 3316 became
    "voltage" - or "field_xxxx". A blob without the name table is refused, because this
    decoder reports the decoded object, which needs the names.
    """
    from schema_binary import decode_schema

    decoded = decode_schema(data)
    if not decoded['names']:
        raise ValueError("binary schema carries no names; a decoder reporting the "
                         "decoded object cannot use it (PS-555)")
    return BinarySchema(
        version=data[2],
        endian=decoded['endian'],
        fields=[_field_from_dict(f) for f in decoded['fields']],
    )


def binary_schema_to_dict(schema: BinarySchema) -> Dict[str, Any]:
    """Convert BinarySchema to dict for compatibility with SchemaInterpreter."""
    def field_to_dict(f: BinaryField) -> Dict[str, Any]:
        type_names = {
            TypeCode.UINT: {1: 'u8', 2: 'u16', 3: 'u24', 4: 'u32'},
            TypeCode.SINT: {1: 's8', 2: 's16', 3: 's24', 4: 's32'},
            TypeCode.FLOAT: {2: 'f16', 4: 'f32', 8: 'f64'},
            TypeCode.BOOL: {1: 'bool'},
            TypeCode.SKIP: {0: 'skip', 1: 'skip'},
            TypeCode.BYTES: {0: 'bytes'},
            TypeCode.ENUM: {1: 'enum'},
        }
        
        d = {'name': f.name}
        
        if f.type_code == TypeCode.BITFIELD:
            d['type'] = f'u8[{f.bitfield_start}:{f.bitfield_start + f.bitfield_width - 1}]'
            if f.consume:
                d['consume'] = 1
        elif f.type_code == TypeCode.MATCH:
            d['type'] = 'match'
            d['on'] = f'${f.match_var}'
            d['cases'] = []
            for case_val, case_fields in f.cases:
                case_dict = {'fields': [field_to_dict(cf) for cf in case_fields]}
                if case_val == -1:
                    case_dict['default'] = 'skip'
                else:
                    case_dict['case'] = case_val
                d['cases'].append(case_dict)
        else:
            type_map = type_names.get(f.type_code, {})
            d['type'] = type_map.get(f.size, 'u8')
        
        if f.mult != 1.0:
            d['mult'] = f.mult
        if f.add != 0.0:
            d['add'] = f.add
        if f.lookup:
            d['lookup'] = f.lookup
        
        return d
    
    return {
        'version': schema.version,
        'endian': schema.endian,
        'fields': [field_to_dict(f) for f in schema.fields],
    }


class BinarySchemaDecoder:
    """
    Fast decoder using pre-parsed binary schema.
    
    This bypasses the generic SchemaInterpreter for maximum performance.
    """
    
    def __init__(self, schema: BinarySchema):
        self.schema = schema
        self.endian = '<' if schema.endian == 'little' else '>'
    
    def decode(self, payload: bytes) -> Dict[str, Any]:
        """Decode payload using binary schema."""
        result = {}
        variables = {}
        pos = 0
        
        for field in self.schema.fields:
            if pos >= len(payload) and field.type_code != TypeCode.SKIP:
                break
            
            value, pos = self._decode_field(field, payload, pos, variables)
            
            if field.name and not field.name.startswith('_'):
                # Store variable for match
                if field.name:
                    variables[field.name] = value
                
                # Apply modifiers
                if isinstance(value, (int, float)):
                    if field.mult != 1.0:
                        value = value * field.mult
                    if field.add != 0.0:
                        value = value + field.add
                
                # Apply lookup
                if field.lookup and isinstance(value, int):
                    value = field.lookup.get(value, f'unknown({value})')
                
                result[field.name] = value
        
        return result
    
    def _decode_field(self, field: BinaryField, buf: bytes, pos: int, 
                      variables: Dict) -> Tuple[Any, int]:
        """Decode single field."""
        tc = field.type_code
        size = field.size
        
        if tc == TypeCode.UINT:
            if size == 1:
                return buf[pos], pos + 1
            elif size == 2:
                fmt = f'{self.endian}H'
                return struct.unpack_from(fmt, buf, pos)[0], pos + 2
            elif size == 3:
                if self.endian == '>':
                    return (buf[pos] << 16) | (buf[pos+1] << 8) | buf[pos+2], pos + 3
                else:
                    return buf[pos] | (buf[pos+1] << 8) | (buf[pos+2] << 16), pos + 3
            elif size == 4:
                fmt = f'{self.endian}I'
                return struct.unpack_from(fmt, buf, pos)[0], pos + 4
        
        elif tc == TypeCode.SINT:
            if size == 1:
                v = buf[pos]
                return v - 256 if v > 127 else v, pos + 1
            elif size == 2:
                fmt = f'{self.endian}h'
                return struct.unpack_from(fmt, buf, pos)[0], pos + 2
            elif size == 4:
                fmt = f'{self.endian}i'
                return struct.unpack_from(fmt, buf, pos)[0], pos + 4
        
        elif tc == TypeCode.FLOAT:
            if size == 4:
                fmt = f'{self.endian}f'
                return struct.unpack_from(fmt, buf, pos)[0], pos + 4
            elif size == 8:
                fmt = f'{self.endian}d'
                return struct.unpack_from(fmt, buf, pos)[0], pos + 8
        
        elif tc == TypeCode.BOOL:
            return buf[pos] != 0, pos + 1
        
        elif tc == TypeCode.BITFIELD:
            byte_val = buf[pos]
            value = (byte_val >> field.bitfield_start) & ((1 << field.bitfield_width) - 1)
            new_pos = pos + 1 if field.consume else pos
            return value, new_pos
        
        elif tc == TypeCode.SKIP:
            return None, pos + (size if size else 1)
        
        elif tc == TypeCode.MATCH:
            var_value = variables.get(field.match_var, 0)
            for case_val, case_fields in field.cases:
                if case_val == -1 or case_val == var_value:
                    for cf in case_fields:
                        val, pos = self._decode_field(cf, buf, pos, variables)
                        if cf.name and not cf.name.startswith('_'):
                            variables[cf.name] = val
                    break
            return None, pos
        
        return 0, pos


def load_schema(path_or_data) -> Tuple[BinarySchema, Dict]:
    """
    Load schema from file or bytes.
    
    Returns (BinarySchema, dict) for both fast and compatible access.
    """
    if isinstance(path_or_data, bytes):
        data = path_or_data
    else:
        from pathlib import Path
        p = Path(path_or_data)
        if p.suffix in ['.bin', '.pbs']:
            data = p.read_bytes()
        else:
            # YAML/JSON - use schema_binary to convert
            from schema_binary import encode_schema
            import yaml
            schema_dict = yaml.safe_load(p.read_text())
            data = encode_schema(schema_dict)
    
    schema = load_binary_schema(data)
    schema_dict = binary_schema_to_dict(schema)
    
    return schema, schema_dict


# Benchmark helper
def benchmark_binary_vs_yaml(schema_path: str, payload: bytes, iterations: int = 10000):
    """Compare binary vs YAML schema loading and decoding performance."""
    import time
    import yaml
    from pathlib import Path
    from schema_interpreter import SchemaInterpreter
    from schema_binary import encode_schema
    
    # Load YAML
    yaml_content = Path(schema_path).read_text()
    
    # Time YAML parsing
    start = time.perf_counter()
    for _ in range(100):
        schema_dict = yaml.safe_load(yaml_content)
    yaml_parse_time = (time.perf_counter() - start) / 100 * 1000
    
    # Convert to binary
    binary_data = encode_schema(schema_dict)
    
    # Time binary parsing
    start = time.perf_counter()
    for _ in range(100):
        bin_schema = load_binary_schema(binary_data)
    binary_parse_time = (time.perf_counter() - start) / 100 * 1000
    
    # Time YAML-based decoding
    interpreter = SchemaInterpreter(schema_dict)
    start = time.perf_counter()
    for _ in range(iterations):
        interpreter.decode(payload)
    yaml_decode_time = (time.perf_counter() - start) / iterations * 1e6
    
    # Time binary-based decoding
    decoder = BinarySchemaDecoder(bin_schema)
    start = time.perf_counter()
    for _ in range(iterations):
        decoder.decode(payload)
    binary_decode_time = (time.perf_counter() - start) / iterations * 1e6
    
    print(f"Schema parsing:")
    print(f"  YAML:   {yaml_parse_time:.3f} ms")
    print(f"  Binary: {binary_parse_time:.3f} ms")
    print(f"  Speedup: {yaml_parse_time / binary_parse_time:.1f}x")
    print()
    print(f"Payload decoding ({iterations} iterations):")
    print(f"  YAML interpreter:   {yaml_decode_time:.2f} µs/decode")
    print(f"  Binary decoder:     {binary_decode_time:.2f} µs/decode")
    print(f"  Speedup: {yaml_decode_time / binary_decode_time:.1f}x")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'benchmark':
        # Run benchmark
        schema_path = sys.argv[2] if len(sys.argv) > 2 else 'test_schema.yaml'
        payload = bytes.fromhex(sys.argv[3]) if len(sys.argv) > 3 else b'\x09\x29\x82\x0C\xE4\x00'
        benchmark_binary_vs_yaml(schema_path, payload)
    else:
        print("Usage: python binary_schema_loader.py benchmark <schema.yaml> [payload_hex]")
