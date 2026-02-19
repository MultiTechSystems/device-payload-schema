#!/usr/bin/env python3
"""
validate_schema.py - Validate schema and run test vectors

Usage:
    python tools/validate_schema.py schema.yaml
    python tools/validate_schema.py schema.yaml --verbose
    python tools/validate_schema.py schema.yaml --json

Features:
    - Validates schema syntax
    - Runs all embedded test vectors
    - Reports pass/fail with details
    - Optionally outputs JSON results
"""

import argparse
import yaml
import json
import sys
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass, field

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent))
from schema_interpreter import SchemaInterpreter, DecodeResult


@dataclass
class TestResult:
    """Result of a single test vector."""
    name: str
    passed: bool
    description: str = ""
    payload_hex: str = ""
    expected: Dict[str, Any] = field(default_factory=dict)
    actual: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'passed': self.passed,
            'description': self.description,
            'payload': self.payload_hex,
            'expected': self.expected,
            'actual': self.actual,
            'errors': self.errors,
        }


@dataclass
class ValidationResult:
    """Result of schema validation."""
    schema_valid: bool
    schema_errors: List[str] = field(default_factory=list)
    test_results: List[TestResult] = field(default_factory=list)
    
    @property
    def tests_passed(self) -> int:
        return sum(1 for t in self.test_results if t.passed)
    
    @property
    def tests_failed(self) -> int:
        return sum(1 for t in self.test_results if not t.passed)
    
    @property
    def total_tests(self) -> int:
        return len(self.test_results)
    
    @property
    def all_passed(self) -> bool:
        return self.schema_valid and self.tests_failed == 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'schema_valid': self.schema_valid,
            'schema_errors': self.schema_errors,
            'tests_passed': self.tests_passed,
            'tests_failed': self.tests_failed,
            'total_tests': self.total_tests,
            'all_passed': self.all_passed,
            'test_results': [t.to_dict() for t in self.test_results],
        }


def parse_payload(payload: Any) -> bytes:
    """Parse payload from various formats to bytes."""
    if isinstance(payload, bytes):
        return payload
    
    if isinstance(payload, list):
        return bytes(payload)
    
    if isinstance(payload, str):
        # Remove spaces, 0x prefixes
        clean = payload.replace(' ', '').replace('0x', '').replace(',', '')
        return bytes.fromhex(clean)
    
    raise ValueError(f"Cannot parse payload: {payload}")


def values_match(expected: Any, actual: Any, tolerance: float = 0.001) -> Tuple[bool, str]:
    """Compare expected and actual values with tolerance for floats."""
    if expected is None and actual is None:
        return True, ""
    
    if expected is None or actual is None:
        return False, f"expected {expected}, got {actual}"
    
    if isinstance(expected, bool) and isinstance(actual, bool):
        if expected != actual:
            return False, f"expected {expected}, got {actual}"
        return True, ""
    
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if abs(expected - actual) > tolerance:
            return False, f"expected {expected}, got {actual} (diff: {abs(expected - actual)})"
        return True, ""
    
    if isinstance(expected, str) and isinstance(actual, str):
        if expected != actual:
            return False, f"expected '{expected}', got '{actual}'"
        return True, ""
    
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in expected:
            if key not in actual:
                return False, f"missing key '{key}'"
            match, msg = values_match(expected[key], actual[key], tolerance)
            if not match:
                return False, f"{key}: {msg}"
        return True, ""
    
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False, f"list length mismatch: expected {len(expected)}, got {len(actual)}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            match, msg = values_match(e, a, tolerance)
            if not match:
                return False, f"[{i}]: {msg}"
        return True, ""
    
    # Type mismatch
    if type(expected) != type(actual):
        return False, f"type mismatch: expected {type(expected).__name__}, got {type(actual).__name__}"
    
    # Direct comparison
    if expected != actual:
        return False, f"expected {expected}, got {actual}"
    
    return True, ""


def validate_field_list(fields: List[Dict], path: str, errors: List[str], 
                        known_field_names: List[str]) -> None:
    """Validate a list of field definitions recursively."""
    KNOWN_TYPES = {
        'u8', 'u16', 'u24', 'u32', 'u64',
        'uint8', 'uint16', 'uint24', 'uint32', 'uint64',
        's8', 's16', 's24', 's32', 's64',
        'i8', 'i16', 'i24', 'i32', 'i64',
        'int8', 'int16', 'int24', 'int32', 'int64',
        'f16', 'f32', 'f64', 'float', 'double',
        'bool', 'bytes', 'string', 'ascii', 'hex', 'base64',
        'object', 'match', 'enum', 'repeat', 'skip',
        'bitfield_string', 'number', 'version_string',
        'udec', 'sdec', 'UDec', 'SDec',
    }
    
    for i, fld in enumerate(fields):
        if not isinstance(fld, dict):
            errors.append(f"{path}[{i}]: must be an object")
            continue
        
        # Flagged construct (structural, no name/type)
        if 'flagged' in fld:
            flagged = fld['flagged']
            if not isinstance(flagged, dict):
                errors.append(f"{path}[{i}].flagged: must be an object")
                continue
            if 'field' not in flagged:
                errors.append(f"{path}[{i}].flagged: missing required 'field' (name of flags variable)")
            else:
                ref = flagged['field']
                if ref not in known_field_names:
                    errors.append(f"{path}[{i}].flagged: references unknown field '{ref}' "
                                  f"(must be declared before the flagged: construct)")
            if 'groups' not in flagged:
                errors.append(f"{path}[{i}].flagged: missing required 'groups' array")
            elif not isinstance(flagged['groups'], list):
                errors.append(f"{path}[{i}].flagged.groups: must be an array")
            else:
                seen_bits = set()
                for gi, group in enumerate(flagged['groups']):
                    gpath = f"{path}[{i}].flagged.groups[{gi}]"
                    if not isinstance(group, dict):
                        errors.append(f"{gpath}: must be an object")
                        continue
                    if 'bit' not in group:
                        errors.append(f"{gpath}: missing required 'bit'")
                    else:
                        bit = group['bit']
                        if not isinstance(bit, int) or bit < 0:
                            errors.append(f"{gpath}: 'bit' must be a non-negative integer")
                        elif bit in seen_bits:
                            errors.append(f"{gpath}: duplicate bit position {bit}")
                        else:
                            seen_bits.add(bit)
                    if 'fields' not in group:
                        errors.append(f"{gpath}: missing required 'fields' array")
                    elif not isinstance(group['fields'], list) or len(group['fields']) == 0:
                        errors.append(f"{gpath}: 'fields' must be a non-empty array")
                    else:
                        validate_field_list(group['fields'], f"{gpath}.fields", errors, known_field_names)
                        for gf in group['fields']:
                            if isinstance(gf, dict) and 'name' in gf:
                                known_field_names.append(gf['name'])
            continue
        
        # Normal named field
        name = fld.get('name')
        if name:
            known_field_names.append(name)
        
        if ('name' not in fld and 'type' not in fld and 'tlv' not in fld
                and 'byte_group' not in fld and 'object' not in fld and 'match' not in fld):
            errors.append(f"{path}[{i}]: must have 'name', 'type', 'flagged', 'tlv', 'byte_group', 'object', or 'match'")
        
        if 'type' in fld:
            ftype = fld['type']
            
            # Bitfield string validation
            if ftype == 'bitfield_string':
                if 'parts' not in fld:
                    errors.append(f"{path}[{i}] ({name}): bitfield_string requires 'parts' array")
                elif not isinstance(fld['parts'], list) or len(fld['parts']) == 0:
                    errors.append(f"{path}[{i}] ({name}): 'parts' must be a non-empty array")
                else:
                    for pi, part in enumerate(fld['parts']):
                        if not isinstance(part, list) or len(part) < 2:
                            errors.append(f"{path}[{i}].parts[{pi}]: must be [bitOffset, bitLength] or [bitOffset, bitLength, format]")
                        elif len(part) > 2 and part[2] not in ('hex', 'decimal'):
                            errors.append(f"{path}[{i}].parts[{pi}]: format must be 'hex' or 'decimal'")
                if 'length' not in fld:
                    errors.append(f"{path}[{i}] ({name}): bitfield_string should have explicit 'length'")
                continue
            
            # Computed field (type: number - supports ref, polynomial, compute, guard, or deprecated formula)
            if ftype == 'number':
                has_valid_source = any(key in fld for key in ('ref', 'compute', 'formula', 'value'))
                if not has_valid_source:
                    errors.append(f"{path}[{i}] ({name}): type 'number' requires 'ref', 'compute', 'value', or deprecated 'formula'")
                
                # Validate ref field reference
                if 'ref' in fld:
                    ref_val = fld['ref']
                    if isinstance(ref_val, str) and ref_val.startswith('$'):
                        ref_name = ref_val[1:]
                        if ref_name not in known_field_names:
                            errors.append(f"{path}[{i}] ({name}): ref references unknown field '${ref_name}'")
                
                # Validate polynomial coefficients
                if 'polynomial' in fld:
                    poly = fld['polynomial']
                    if not isinstance(poly, list) or len(poly) < 2:
                        errors.append(f"{path}[{i}] ({name}): 'polynomial' must be array of at least 2 coefficients")
                    elif not all(isinstance(c, (int, float)) for c in poly):
                        errors.append(f"{path}[{i}] ({name}): 'polynomial' coefficients must be numbers")
                
                # Validate compute structure
                if 'compute' in fld:
                    compute = fld['compute']
                    if not isinstance(compute, dict):
                        errors.append(f"{path}[{i}] ({name}): 'compute' must be an object")
                    else:
                        if 'op' not in compute:
                            errors.append(f"{path}[{i}] ({name}): compute missing 'op'")
                        elif compute['op'] not in ('add', 'sub', 'mul', 'div'):
                            errors.append(f"{path}[{i}] ({name}): compute 'op' must be add/sub/mul/div")
                        for operand in ('a', 'b'):
                            if operand in compute:
                                op_val = compute[operand]
                                if isinstance(op_val, str) and op_val.startswith('$'):
                                    ref_name = op_val[1:]
                                    if ref_name not in known_field_names:
                                        errors.append(f"{path}[{i}] ({name}): compute.{operand} references unknown field '${ref_name}'")
                
                # Validate guard structure
                if 'guard' in fld:
                    guard = fld['guard']
                    if not isinstance(guard, dict):
                        errors.append(f"{path}[{i}] ({name}): 'guard' must be an object")
                    elif 'when' not in guard:
                        errors.append(f"{path}[{i}] ({name}): guard missing 'when' conditions")
                    else:
                        when = guard['when']
                        if not isinstance(when, list):
                            errors.append(f"{path}[{i}] ({name}): guard 'when' must be an array")
                        else:
                            valid_ops = ('gt', 'gte', 'lt', 'lte', 'eq', 'ne')
                            for ci, cond in enumerate(when):
                                if not isinstance(cond, dict):
                                    errors.append(f"{path}[{i}] ({name}): guard.when[{ci}] must be an object")
                                elif 'field' not in cond:
                                    errors.append(f"{path}[{i}] ({name}): guard.when[{ci}] missing 'field'")
                                else:
                                    field_ref = cond['field']
                                    if isinstance(field_ref, str) and field_ref.startswith('$'):
                                        ref_name = field_ref[1:]
                                        if ref_name not in known_field_names:
                                            errors.append(f"{path}[{i}] ({name}): guard.when[{ci}].field references unknown '${ref_name}'")
                                    if not any(op in cond for op in valid_ops):
                                        errors.append(f"{path}[{i}] ({name}): guard.when[{ci}] missing comparison operator")
                continue
            
            # Formula on regular field (uses x variable) - DEPRECATED
            if 'formula' in fld:
                formula = fld['formula']
                refs = re.findall(r'\$([a-zA-Z_][a-zA-Z0-9_]*)', formula)
                for ref in refs:
                    if ref not in known_field_names:
                        errors.append(f"{path}[{i}] ({name}): formula references unknown field '${ref}'")
            
            base_type = ftype.split('[')[0].split(':')[0].split('<')[0]
            if base_type not in KNOWN_TYPES and not base_type.startswith('be_') and not base_type.startswith('le_'):
                if not re.match(r'(u|i|s)\d+[\[:]', ftype) and not ftype.startswith('bits'):
                    errors.append(f"{path}[{i}] ({name}): unknown type '{ftype}'")
        
        # Validate modifier values
        for mod in ('mult', 'div', 'add'):
            if mod in fld:
                val = fld[mod]
                if not isinstance(val, (int, float)):
                    errors.append(f"{path}[{i}] ({name}): '{mod}' must be a number")
                elif mod in ('mult', 'div') and val == 0:
                    errors.append(f"{path}[{i}] ({name}): '{mod}' must not be zero")
        
        # Validate transform array
        if 'transform' in fld:
            transform = fld['transform']
            if not isinstance(transform, list):
                errors.append(f"{path}[{i}] ({name}): 'transform' must be an array")
            else:
                valid_ops = ('sqrt', 'abs', 'pow', 'floor', 'ceiling', 'clamp', 'log10', 'log', 'add', 'mult', 'div')
                for ti, op_def in enumerate(transform):
                    if not isinstance(op_def, dict):
                        errors.append(f"{path}[{i}] ({name}): transform[{ti}] must be an object")
                    elif not any(op in op_def for op in valid_ops):
                        errors.append(f"{path}[{i}] ({name}): transform[{ti}] has no valid operation ({', '.join(valid_ops)})")
                    else:
                        # Validate specific ops
                        if 'pow' in op_def and not isinstance(op_def['pow'], (int, float)):
                            errors.append(f"{path}[{i}] ({name}): transform[{ti}].pow must be a number")
                        if 'floor' in op_def and not isinstance(op_def['floor'], (int, float)):
                            errors.append(f"{path}[{i}] ({name}): transform[{ti}].floor must be a number")
                        if 'ceiling' in op_def and not isinstance(op_def['ceiling'], (int, float)):
                            errors.append(f"{path}[{i}] ({name}): transform[{ti}].ceiling must be a number")
                        if 'clamp' in op_def:
                            clamp = op_def['clamp']
                            if not isinstance(clamp, list) or len(clamp) < 2:
                                errors.append(f"{path}[{i}] ({name}): transform[{ti}].clamp must be [min, max]")


def validate_schema_structure(schema: Dict[str, Any]) -> List[str]:
    """Validate schema structure and return list of errors."""
    errors = []
    has_fields = False
    has_ports = False
    
    # Required fields
    if 'name' not in schema:
        errors.append("Missing required field: 'name'")
    
    # Must have either 'fields' or 'ports' (or both)
    if 'fields' in schema:
        has_fields = True
        if not isinstance(schema['fields'], list):
            errors.append("'fields' must be an array")
        elif len(schema['fields']) == 0:
            errors.append("'fields' array must not be empty")
    
    if 'ports' in schema:
        has_ports = True
        ports = schema['ports']
        if not isinstance(ports, dict):
            errors.append("'ports' must be an object mapping port numbers to definitions")
        else:
            for port_key, port_def in ports.items():
                pk = str(port_key)
                if pk != 'default':
                    try:
                        port_num = int(pk)
                        if port_num < 1 or port_num > 255:
                            errors.append(f"ports.{pk}: port number must be 1-255")
                    except ValueError:
                        errors.append(f"ports.{pk}: key must be an integer (1-255) or 'default'")
                
                if not isinstance(port_def, dict):
                    errors.append(f"ports.{pk}: must be an object")
                    continue
                
                if 'fields' not in port_def:
                    errors.append(f"ports.{pk}: missing required 'fields' array")
                elif not isinstance(port_def['fields'], list) or len(port_def['fields']) == 0:
                    errors.append(f"ports.{pk}: 'fields' must be a non-empty array")
                else:
                    known_names = []
                    validate_field_list(port_def['fields'], f"ports.{pk}.fields", errors, known_names)
    
    if not has_fields and not has_ports:
        errors.append("Schema must have either 'fields' or 'ports' (or both)")
    
    # Validate top-level fields
    if has_fields and isinstance(schema.get('fields'), list) and len(schema['fields']) > 0:
        known_names = []
        validate_field_list(schema['fields'], 'fields', errors, known_names)
    
    # Validate endian
    if 'endian' in schema:
        if schema['endian'] not in ('big', 'little'):
            errors.append(f"'endian' must be 'big' or 'little', got '{schema['endian']}'")
    
    # Validate test vectors
    if 'test_vectors' in schema:
        if not isinstance(schema['test_vectors'], list):
            errors.append("'test_vectors' must be an array")
        else:
            for i, tv in enumerate(schema['test_vectors']):
                if not isinstance(tv, dict):
                    errors.append(f"Test vector {i}: must be an object")
                    continue
                
                if 'name' not in tv:
                    errors.append(f"Test vector {i}: missing 'name'")
                
                if 'payload' not in tv:
                    errors.append(f"Test vector {i} ({tv.get('name', '?')}): missing 'payload'")
                
                if 'expected' not in tv:
                    errors.append(f"Test vector {i} ({tv.get('name', '?')}): missing 'expected'")
                
                # Port-based schemas should have fport in test vectors
                if has_ports and not has_fields:
                    if 'fport' not in tv and 'fPort' not in tv:
                        errors.append(f"Test vector {i} ({tv.get('name', '?')}): "
                                      f"port-based schema requires 'fport' in test vectors")
    
    return errors


def run_test_vector(interpreter: SchemaInterpreter, tv: Dict[str, Any]) -> TestResult:
    """Run a single test vector and return result."""
    name = tv.get('name', 'unnamed')
    description = tv.get('description', '')
    
    result = TestResult(
        name=name,
        passed=False,
        description=description,
        expected=tv.get('expected', {}),
    )
    
    # Parse payload
    try:
        payload = parse_payload(tv.get('payload', ''))
        result.payload_hex = payload.hex().upper()
    except Exception as e:
        result.errors.append(f"Failed to parse payload: {e}")
        return result
    
    # Decode (pass fPort from test vector for port-based schemas, per TS013)
    fPort = tv.get('fport') or tv.get('fPort')
    try:
        decode_result = interpreter.decode(payload, fPort=fPort)
        if not decode_result.success:
            result.errors.extend(decode_result.errors)
            return result
        result.actual = decode_result.data
    except Exception as e:
        result.errors.append(f"Decode failed: {e}")
        return result
    
    # Compare
    expected = tv.get('expected', {})
    for field_name, expected_value in expected.items():
        if field_name not in result.actual:
            result.errors.append(f"Missing field in output: '{field_name}'")
            continue
        
        actual_value = result.actual[field_name]
        match, msg = values_match(expected_value, actual_value)
        if not match:
            result.errors.append(f"{field_name}: {msg}")
    
    result.passed = len(result.errors) == 0
    return result


def validate_schema(schema: Dict[str, Any]) -> ValidationResult:
    """Validate schema and run all test vectors."""
    result = ValidationResult(schema_valid=True)
    
    # Validate structure
    structure_errors = validate_schema_structure(schema)
    if structure_errors:
        result.schema_valid = False
        result.schema_errors = structure_errors
        return result
    
    # Create interpreter
    try:
        interpreter = SchemaInterpreter(schema)
    except Exception as e:
        result.schema_valid = False
        result.schema_errors.append(f"Failed to create interpreter: {e}")
        return result
    
    # Run test vectors
    test_vectors = schema.get('test_vectors', [])
    for tv in test_vectors:
        test_result = run_test_vector(interpreter, tv)
        result.test_results.append(test_result)
    
    return result


def print_results(result: ValidationResult, verbose: bool = False):
    """Print validation results to console."""
    # Schema validation
    if result.schema_valid:
        print("Schema: VALID")
    else:
        print("Schema: INVALID")
        for error in result.schema_errors:
            print(f"  - {error}")
        return
    
    # Test vectors
    if result.total_tests == 0:
        print("\nNo test vectors found in schema.")
        return
    
    print(f"\nTest Vectors: {result.tests_passed}/{result.total_tests} passed")
    print("-" * 50)
    
    for tr in result.test_results:
        status = "PASS" if tr.passed else "FAIL"
        symbol = "✓" if tr.passed else "✗"
        print(f"{symbol} {tr.name}: {status}")
        
        if verbose or not tr.passed:
            if tr.description:
                print(f"    Description: {tr.description}")
            if tr.payload_hex:
                print(f"    Payload: {tr.payload_hex}")
            
            if tr.errors:
                for error in tr.errors:
                    print(f"    ERROR: {error}")
            
            if verbose and tr.passed:
                print(f"    Expected: {tr.expected}")
                print(f"    Actual: {tr.actual}")
        
        if not tr.passed or verbose:
            print()
    
    print("-" * 50)
    if result.all_passed:
        print(f"PASSED: All {result.total_tests} tests passed")
    else:
        print(f"FAILED: {result.tests_failed} of {result.total_tests} tests failed")


def main():
    parser = argparse.ArgumentParser(
        description='Validate Payload Schema and run test vectors'
    )
    parser.add_argument('schema', help='Path to schema YAML file')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Show detailed output for all tests')
    parser.add_argument('--json', action='store_true',
                       help='Output results as JSON')
    args = parser.parse_args()
    
    # Load schema
    try:
        with open(args.schema) as f:
            schema = yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading schema: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Validate
    result = validate_schema(schema)
    
    # Output
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"Validating: {args.schema}")
        print("=" * 50)
        print_results(result, args.verbose)
    
    # Exit code
    sys.exit(0 if result.all_passed else 1)


if __name__ == '__main__':
    main()
