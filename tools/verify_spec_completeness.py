#!/usr/bin/env python3
"""
verify_spec_completeness.py - Requirements traceability and spec completeness verification.

Ensures the spec, language reference, interpreter, and tests are synchronized.

Usage:
    python tools/verify_spec_completeness.py
    python tools/verify_spec_completeness.py --report completeness-report.json
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class FeatureMapping:
    """Mapping of a feature across spec, reference, implementation, and tests."""
    name: str
    spec_section: Optional[str] = None
    spec_req_ids: List[str] = field(default_factory=list)
    lang_ref_section: Optional[str] = None
    interpreter_func: Optional[str] = None
    has_tests: bool = False
    test_count: int = 0
    status: str = 'unknown'  # complete, partial, missing
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CompletenessReport:
    """Report on spec/implementation completeness."""
    timestamp: str
    features: List[FeatureMapping] = field(default_factory=list)
    requirements_found: int = 0
    requirements_with_tests: int = 0
    features_complete: int = 0
    features_partial: int = 0
    features_missing: int = 0
    issues: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Core language features to verify
CORE_FEATURES = [
    # Basic types
    ('integer_types', 'u8/u16/u32/s8/s16/s32 integer decoding'),
    ('float_types', 'f16/f32/f64 float decoding'),
    ('bool_type', 'bool type with bit position'),
    ('bytes_type', 'bytes/hex/base64 binary types'),
    ('string_type', 'ascii/string text types'),
    ('enum_type', 'enum with values mapping'),
    
    # Modifiers
    ('mult_modifier', 'mult: multiplication modifier'),
    ('div_modifier', 'div: division modifier'),
    ('add_modifier', 'add: addition/offset modifier'),
    ('lookup_modifier', 'lookup: array index to string'),
    
    # Computed fields
    ('polynomial', 'polynomial: calibration curves'),
    ('compute', 'compute: cross-field binary ops'),
    ('guard', 'guard: conditional evaluation'),
    ('transform', 'transform: sequential math ops'),
    ('ref', 'ref: field reference for computed fields'),
    
    # Conditional parsing
    ('switch_match', 'switch/match conditional parsing'),
    ('flagged', 'flagged: bitmask field presence'),
    
    # Complex types
    ('bitfield', 'bitfield: bit-level extraction'),
    ('bitfield_string', 'bitfield_string: formatted bit output'),
    ('byte_group', 'byte_group: shared byte bitfields'),
    ('tlv', 'tlv: type-length-value parsing'),
    ('nested_object', 'object: nested field groups'),
    ('repeat', 'repeat: array/repeated fields'),
    
    # Advanced
    ('ports', 'ports: fPort-based schema selection'),
    ('definitions', 'definitions: reusable field groups'),
    ('var', 'var: variable storage for references'),
    ('skip', 'skip: ignore bytes'),
    ('endian', 'endian: big/little endianness'),
    
    # Output
    ('unit', 'unit: engineering unit annotation'),
    ('semantic', 'semantic: IPSO/SenML mapping'),
    
    # Deprecated
    ('formula', 'formula: (deprecated) expression evaluation'),
]


def find_spec_requirements(spec_dir: Path) -> Dict[str, List[str]]:
    """Find REQ-xxx identifiers in spec files."""
    requirements = {}
    
    if not spec_dir.exists():
        return requirements
    
    for md_file in spec_dir.rglob('*.md'):
        content = md_file.read_text()
        # Match REQ-xxx-xxx-nnn patterns
        reqs = re.findall(r'REQ-[A-Za-z0-9-]+', content)
        for req in reqs:
            if req not in requirements:
                requirements[req] = []
            requirements[req].append(str(md_file.relative_to(spec_dir)))
    
    return requirements


def find_test_coverage(test_file: Path) -> Dict[str, int]:
    """Find which features have tests and count them."""
    coverage = {}
    
    if not test_file.exists():
        return coverage
    
    content = test_file.read_text()
    
    # Count test methods per class
    current_class = None
    for line in content.split('\n'):
        class_match = re.match(r'^class\s+(\w+)', line)
        if class_match:
            current_class = class_match.group(1).lower()
        
        test_match = re.match(r'\s+def\s+(test_\w+)', line)
        if test_match and current_class:
            # Map class names to features
            feature = None
            if 'polynomial' in current_class or 'polynomial' in test_match.group(1):
                feature = 'polynomial'
            elif 'compute' in current_class or 'compute' in test_match.group(1):
                feature = 'compute'
            elif 'guard' in current_class or 'guard' in test_match.group(1):
                feature = 'guard'
            elif 'transform' in current_class or 'transform' in test_match.group(1):
                feature = 'transform'
            elif 'formula' in current_class or 'formula' in test_match.group(1):
                feature = 'formula'
            elif 'integer' in current_class or 'u8' in test_match.group(1) or 'u16' in test_match.group(1):
                feature = 'integer_types'
            elif 'float' in current_class or 'f32' in test_match.group(1):
                feature = 'float_types'
            elif 'bool' in current_class or 'bool' in test_match.group(1):
                feature = 'bool_type'
            elif 'bitfield' in current_class or 'bitfield' in test_match.group(1):
                feature = 'bitfield'
            elif 'match' in current_class or 'switch' in test_match.group(1):
                feature = 'switch_match'
            elif 'flagged' in current_class:
                feature = 'flagged'
            elif 'tlv' in current_class:
                feature = 'tlv'
            elif 'enum' in current_class or 'enum' in test_match.group(1):
                feature = 'enum_type'
            elif 'modifier' in current_class:
                if 'mult' in test_match.group(1):
                    feature = 'mult_modifier'
                elif 'div' in test_match.group(1):
                    feature = 'div_modifier'
                elif 'add' in test_match.group(1):
                    feature = 'add_modifier'
            elif 'nested' in test_match.group(1) or 'object' in test_match.group(1):
                feature = 'nested_object'
            elif 'repeat' in test_match.group(1):
                feature = 'repeat'
            elif 'port' in test_match.group(1):
                feature = 'ports'
            elif 'ref' in test_match.group(1):
                feature = 'ref'
            
            if feature:
                coverage[feature] = coverage.get(feature, 0) + 1
    
    return coverage


def find_interpreter_functions(interp_file: Path) -> Dict[str, str]:
    """Find which features are implemented in interpreter."""
    functions = {}
    
    if not interp_file.exists():
        return functions
    
    content = interp_file.read_text()
    
    # Map features to function patterns
    feature_patterns = {
        'polynomial': r'_evaluate_polynomial',
        'compute': r'_evaluate_compute',
        'guard': r'_evaluate_guard',
        'transform': r'_apply_transform',
        'ref': r'_resolve_ref_value',
        'formula': r'_evaluate_formula',
        'bitfield': r'_parse_bitfield|_extract_bits',
        'bitfield_string': r'_decode_bitfield_string',
        'switch_match': r'_decode_match',
        'flagged': r'_decode_flagged',
        'tlv': r'_decode_tlv',
        'byte_group': r'_decode_byte_group',
        'nested_object': r"type.*==.*'object'",
        'repeat': r'repeat.*in.*field',
        'enum_type': r"ftype.*==.*'enum'",
        'bool_type': r"ftype.*==.*'bool'",
    }
    
    for feature, pattern in feature_patterns.items():
        if re.search(pattern, content):
            functions[feature] = pattern
    
    return functions


def find_lang_ref_sections(ref_file: Path) -> Dict[str, str]:
    """Find which features are documented in language reference."""
    sections = {}
    
    if not ref_file.exists():
        return sections
    
    content = ref_file.read_text()
    
    # Map features to heading patterns
    feature_headings = {
        'integer_types': r'##.*Integer',
        'float_types': r'##.*Float',
        'bool_type': r'##.*Bool',
        'bytes_type': r'##.*Bytes|##.*Binary',
        'string_type': r'##.*String',
        'enum_type': r'##.*Enum',
        'mult_modifier': r'mult:|Arithmetic.*Modifier',
        'div_modifier': r'div:|Arithmetic.*Modifier',
        'add_modifier': r'add:|Arithmetic.*Modifier',
        'lookup_modifier': r'lookup:|##.*Lookup',
        'polynomial': r'##.*Polynomial|polynomial:',
        'compute': r'##.*Compute|compute:',
        'guard': r'##.*Guard|guard:',
        'transform': r'##.*Transform|transform:',
        'ref': r'\bref:',
        'switch_match': r'##.*Switch|##.*Match|##.*Conditional',
        'flagged': r'##.*Flagged',
        'bitfield': r'##.*Bitfield',
        'bitfield_string': r'bitfield_string',
        'byte_group': r'byte_group',
        'tlv': r'##.*TLV|tlv:',
        'nested_object': r'##.*Object|##.*Nested',
        'repeat': r'##.*Repeat|##.*Array',
        'ports': r'##.*Port',
        'definitions': r'##.*Definition',
        'var': r'\bvar:',
        'skip': r'\bskip\b',
        'endian': r'##.*Endian',
        'unit': r'\bunit:',
        'semantic': r'##.*Semantic|ipso:|senml',
        'formula': r'formula.*deprecated|##.*Formula',
    }
    
    for feature, pattern in feature_headings.items():
        if re.search(pattern, content, re.IGNORECASE):
            sections[feature] = pattern
    
    return sections


def verify_completeness(
    spec_dir: Optional[Path] = None,
    lang_ref: Optional[Path] = None,
    interp_file: Optional[Path] = None,
    test_file: Optional[Path] = None
) -> CompletenessReport:
    """Verify spec/implementation completeness."""
    from datetime import datetime
    
    report = CompletenessReport(
        timestamp=datetime.now().astimezone().isoformat()
    )
    
    # Find requirements from spec
    requirements = {}
    if spec_dir and spec_dir.exists():
        requirements = find_spec_requirements(spec_dir)
        report.requirements_found = len(requirements)
    
    # Find test coverage
    test_coverage = {}
    if test_file and test_file.exists():
        test_coverage = find_test_coverage(test_file)
    
    # Find interpreter implementations
    interp_funcs = {}
    if interp_file and interp_file.exists():
        interp_funcs = find_interpreter_functions(interp_file)
    
    # Find language reference sections
    lang_sections = {}
    if lang_ref and lang_ref.exists():
        lang_sections = find_lang_ref_sections(lang_ref)
    
    # Build feature mappings
    for feature_id, feature_desc in CORE_FEATURES:
        mapping = FeatureMapping(name=feature_id)
        
        # Check language reference
        if feature_id in lang_sections:
            mapping.lang_ref_section = lang_sections[feature_id]
        
        # Check interpreter
        if feature_id in interp_funcs:
            mapping.interpreter_func = interp_funcs[feature_id]
        
        # Check tests
        if feature_id in test_coverage:
            mapping.has_tests = True
            mapping.test_count = test_coverage[feature_id]
        
        # Determine status
        has_impl = mapping.interpreter_func is not None
        has_doc = mapping.lang_ref_section is not None
        has_test = mapping.has_tests
        
        if has_impl and has_doc and has_test:
            mapping.status = 'complete'
            report.features_complete += 1
        elif has_impl and (has_doc or has_test):
            mapping.status = 'partial'
            report.features_partial += 1
        elif has_impl:
            mapping.status = 'undocumented'
            report.features_partial += 1
            report.issues.append(f"{feature_id}: implemented but missing documentation/tests")
        else:
            mapping.status = 'missing'
            report.features_missing += 1
            report.issues.append(f"{feature_id}: not implemented")
        
        report.features.append(mapping)
    
    # Count requirements with test coverage
    for req_id in requirements:
        # Simple heuristic: check if req appears in test file
        if test_file and test_file.exists():
            test_content = test_file.read_text()
            if req_id in test_content:
                report.requirements_with_tests += 1
    
    return report


def main():
    parser = argparse.ArgumentParser(
        description='Verify spec/implementation completeness'
    )
    parser.add_argument('--spec-dir', help='Spec sections directory')
    parser.add_argument('--lang-ref', help='Language reference markdown file')
    parser.add_argument('--interpreter', help='Interpreter Python file')
    parser.add_argument('--tests', help='Test file')
    parser.add_argument('--report', '-r', help='Output JSON report file')
    parser.add_argument('--verbose', '-v', action='store_true')
    
    args = parser.parse_args()
    
    # Default paths relative to proto project
    proto_root = Path(__file__).parent.parent
    spec_root = proto_root.parent / 'la-payload-schema'
    
    spec_dir = Path(args.spec_dir) if args.spec_dir else spec_root / 'spec' / 'sections'
    lang_ref = Path(args.lang_ref) if args.lang_ref else proto_root / 'docs' / 'SCHEMA-LANGUAGE-REFERENCE.md'
    interp_file = Path(args.interpreter) if args.interpreter else proto_root / 'tools' / 'schema_interpreter.py'
    test_file = Path(args.tests) if args.tests else proto_root / 'tests' / 'test_schema_interpreter.py'
    
    report = verify_completeness(
        spec_dir=spec_dir,
        lang_ref=lang_ref,
        interp_file=interp_file,
        test_file=test_file
    )
    
    # Output
    if args.report:
        with open(args.report, 'w') as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"Report written to {args.report}")
    
    # Console summary
    total = len(report.features)
    print(f"\nSpec Completeness Report")
    print(f"========================")
    print(f"Features: {report.features_complete}/{total} complete, "
          f"{report.features_partial} partial, {report.features_missing} missing")
    print(f"Requirements: {report.requirements_found} found, "
          f"{report.requirements_with_tests} with tests")
    
    if args.verbose:
        print(f"\nFeature Status:")
        for f in report.features:
            status_icon = {
                'complete': '✓',
                'partial': '◐',
                'undocumented': '?',
                'missing': '✗'
            }.get(f.status, '?')
            tests = f"({f.test_count} tests)" if f.has_tests else "(no tests)"
            print(f"  {status_icon} {f.name}: {f.status} {tests}")
    
    if report.issues:
        print(f"\nIssues ({len(report.issues)}):")
        for issue in report.issues[:10]:
            print(f"  - {issue}")
        if len(report.issues) > 10:
            print(f"  ... and {len(report.issues) - 10} more")
    
    # Exit code
    completeness = report.features_complete / total if total > 0 else 0
    sys.exit(0 if completeness >= 0.8 else 1)


if __name__ == '__main__':
    main()
