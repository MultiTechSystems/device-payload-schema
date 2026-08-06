#!/usr/bin/env python3
"""
score_schema.py - Quality scoring tool for payload schemas.

Validates schemas and calculates a quality tier as defined in the specification,
Section 10 (Conformance): Platinum 95-100%, Gold 85-94%, Silver 70-84%,
Bronze 60-69%, Rejected below 60%.

Gold and Platinum additionally have MUST requirements (PS-239): certification
passes, at least 5 test vectors, every conditional branch covered, and edge case
vectors present. Those are gates, not points -- a schema missing any of them is
capped below Gold however high its weighted score.

What this tool can and cannot tell you:

    It runs a schema against its OWN test vectors, so it measures internal
    consistency. It cannot tell whether the vectors themselves are right. A
    schema whose vectors were generated from this interpreter's output scores
    perfectly while mis-decoding every real payload. Vectors should therefore
    declare where their expected values came from (`source:` on each vector) and
    --require-provenance makes independent provenance a condition for Platinum.
    For a genuinely independent check, compare against a vendor decoder (see
    tools/crossvalidate_decentlab.py).

Usage:
    python tools/score_schema.py schema.yaml
    python tools/score_schema.py schema.yaml --verbose
    python tools/score_schema.py schemas/ --all --report score-report.json
    python tools/score_schema.py schemas/ --all --baseline score-report.json
    python tools/score_schema.py schemas/ --all --min-tier SILVER
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import yaml
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).parent))

from schema_interpreter import SchemaInterpreter
from validate_schema import validate_schema, ValidationResult, values_match

#: Tolerance for comparing a decoded value with a test vector's expected value.
#: Shared with validate_schema.values_match so the two tools cannot disagree
#: about whether a vector passes.
CONFORMANCE_TOLERANCE = 0.001

#: Tier thresholds from the specification, Section 10 (Conformance).
TIER_THRESHOLDS = (
    (95, 'PLATINUM'),
    (85, 'GOLD'),
    (70, 'SILVER'),
    (60, 'BRONZE'),
)
#: Below the lowest threshold the specification calls the schema Rejected:
#: "insufficient coverage for repository acceptance".
TIER_REJECTED = 'REJECTED'

#: Vector `source:` values that constitute an independent oracle -- the expected
#: values did not come from this interpreter. `generated` and `unknown` do not.
INDEPENDENT_SOURCES = frozenset(
    {'vendor-doc', 'vendor-codec', 'field-capture', 'spec-example'}
)
KNOWN_SOURCES = INDEPENDENT_SOURCES | frozenset({'generated', 'unknown'})

#: Minimum test vectors for Gold/Platinum (PS-239).
MIN_VECTORS_HIGH_TIER = 5


@dataclass
class ScoringResult:
    """Result of schema quality scoring."""
    schema_path: str
    timestamp: str
    score: float
    tier: str
    details: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_schema(path: str) -> Tuple[Dict[str, Any], List[str]]:
    """Load and parse YAML schema file."""
    errors = []
    try:
        with open(path, 'r') as f:
            schema = yaml.safe_load(f)
        if not isinstance(schema, dict):
            errors.append("Schema must be a YAML dictionary")
            return {}, errors
        return schema, errors
    except yaml.YAMLError as e:
        errors.append(f"YAML parse error: {e}")
        return {}, errors
    except FileNotFoundError:
        errors.append(f"File not found: {path}")
        return {}, errors


def check_schema_valid(schema: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate schema structure using validate_schema."""
    result = validate_schema(schema)
    return result.schema_valid, result.schema_errors


def check_test_vectors_exist(schema: Dict[str, Any]) -> Tuple[bool, int, List[str]]:
    """Check if schema has test vectors and count them."""
    vectors = schema.get('test_vectors', [])
    count = len(vectors)
    issues = []
    
    if count == 0:
        issues.append("No test vectors defined")
        return False, 0, issues
    
    if count < 3:
        issues.append(f"Only {count} test vectors (recommend at least 3)")
    
    usable = 0
    for i, tv in enumerate(vectors):
        complete = True
        if not tv.get('payload'):
            issues.append(f"Test vector {i}: missing 'payload'")
            complete = False
        if not tv.get('expected'):
            issues.append(f"Test vector {i}: missing 'expected'")
            complete = False
        if complete:
            usable += 1

    # A vector without a payload or expectation asserts nothing, so it must not
    # earn the "has test vectors" points or count toward the PS-239 minimum.
    return usable >= 1, usable, issues


def run_python_tests(
    schema: Dict[str, Any]
) -> Tuple[bool, int, int, List[str], List[Tuple[Dict, Dict]]]:
    """Run test vectors through the Python interpreter.

    Also returns the decoded output of each passing vector, so edge case
    coverage can be judged from the values a vector actually produces rather
    than from words in its name.
    """
    vectors = schema.get('test_vectors', [])
    if not vectors:
        return False, 0, 0, ["No test vectors to run"], []

    try:
        interpreter = SchemaInterpreter(schema)
    except Exception as e:
        return False, 0, 0, [f"Failed to create interpreter: {e}"], []

    passed = 0
    failed = 0
    errors = []
    decoded: List[Tuple[Dict, Dict]] = []

    for i, tv in enumerate(vectors):
        tv_name = tv.get('name', f'test_{i}')
        payload_hex = tv.get('payload', '').replace(' ', '')
        expected = tv.get('expected', {})
        fport = tv.get('fPort') or tv.get('fport')
        
        try:
            payload_bytes = bytes.fromhex(payload_hex)
            result = interpreter.decode(payload_bytes, fPort=fport)
            
            if not result.success:
                failed += 1
                errors.append(f"{tv_name}: decode failed - {result.errors}")
                continue
            
            # Compare expected values using the same comparison (and the same
            # tolerance) as validate_schema, so the two tools cannot disagree
            # about whether a vector passes.
            all_match = True
            for key, exp_val in expected.items():
                actual_val = result.data.get(key)
                if actual_val is None:
                    all_match = False
                    errors.append(f"{tv_name}: missing field '{key}'")
                    continue
                match, message = values_match(
                    exp_val, actual_val, CONFORMANCE_TOLERANCE
                )
                if not match:
                    all_match = False
                    errors.append(f"{tv_name}: {key}: {message}")

            if all_match:
                passed += 1
                decoded.append((tv, result.data))
            else:
                failed += 1

        except Exception as e:
            failed += 1
            errors.append(f"{tv_name}: exception - {e}")

    return failed == 0, passed, failed, errors, decoded


def run_js_tests(schema: Dict[str, Any], schema_path: str) -> Tuple[str, List[str]]:
    """Generate a JS codec and run the test vectors through Node.js.

    Returns a status rather than a bool, because three different things used to
    collapse into "False": the schema decodes wrongly in JS, the generator
    cannot express this schema, and Node.js is not installed. Only the first is
    a fault of the schema; the others must not silently cost it points.

        'pass' | 'fail' | 'generator-gap' | 'skipped'
    """
    vectors = schema.get('test_vectors', [])
    if not vectors:
        return 'skipped', ["No test vectors for JS validation"]

    try:
        from generate_ts013_codec import TS013Generator
        gen = TS013Generator(schema)
        js_code = gen.generate()
    except Exception as e:
        return 'generator-gap', [f"Failed to generate JS codec: {e}"]

    # Create test runner
    test_cases = []
    for i, tv in enumerate(vectors):
        tv_name = tv.get('name', f'test_{i}')
        payload_hex = tv.get('payload', '').replace(' ', '')
        expected = tv.get('expected', {})
        fport = tv.get('fPort') or tv.get('fport') or 1
        
        test_cases.append({
            'name': tv_name,
            'payload': payload_hex,
            'expected': expected,
            'fPort': fport
        })
    
    js_test = f'''
{js_code}

const tests = {json.dumps(test_cases)};
let passed = 0, failed = 0;
const errors = [];

for (const t of tests) {{
    try {{
        const bytes = [];
        for (let i = 0; i < t.payload.length; i += 2) {{
            bytes.push(parseInt(t.payload.substr(i, 2), 16));
        }}
        const result = decodeUplink({{ bytes, fPort: t.fPort }});
        
        if (result.errors && result.errors.length > 0) {{
            failed++;
            errors.push(t.name + ': ' + result.errors.join(', '));
            continue;
        }}
        
        let allMatch = true;
        for (const [key, expVal] of Object.entries(t.expected)) {{
            const actVal = result.data[key];
            if (actVal === undefined) {{
                allMatch = false;
                errors.push(t.name + ': missing ' + key);
            }} else if (typeof expVal === 'number') {{
                if (Math.abs(actVal - expVal) > {CONFORMANCE_TOLERANCE}) {{
                    allMatch = false;
                    errors.push(t.name + ': ' + key + '=' + actVal + ', expected ' + expVal);
                }}
            }} else if (actVal !== expVal) {{
                allMatch = false;
                errors.push(t.name + ': ' + key + '=' + actVal + ', expected ' + expVal);
            }}
        }}
        
        if (allMatch) passed++;
        else failed++;
    }} catch (e) {{
        failed++;
        errors.push(t.name + ': ' + e.message);
    }}
}}

console.log(JSON.stringify({{ passed, failed, errors }}));
'''
    
    # Run with Node.js
    try:
        result = subprocess.run(
            ['node', '-e', js_test],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return 'generator-gap', [f"Node.js error: {result.stderr}"]

        output = json.loads(result.stdout.strip())
        js_errors = output.get('errors', [])

        if output['failed'] == 0:
            return 'pass', []
        return 'fail', js_errors

    except FileNotFoundError:
        return 'skipped', ["Node.js not found - JS validation not run"]
    except subprocess.TimeoutExpired:
        return 'skipped', ["JS test timeout"]
    except json.JSONDecodeError:
        return 'generator-gap', ["Failed to parse JS test output"]
    except Exception as e:
        return 'generator-gap', [f"JS test error: {e}"]


def analyze_branch_coverage(schema: Dict[str, Any]) -> Tuple[float, List[str]]:
    """Analyze test vector coverage of switch/flagged branches."""
    vectors = schema.get('test_vectors', [])
    if not vectors:
        return 0.0, ["No test vectors for branch analysis"]
    
    # Collect every branch in the schema as (kind, selector, value).
    branches = set()

    def scan_fields(fields: List[Dict], port: Optional[str] = None):
        for field in fields:
            if not isinstance(field, dict):
                continue
            # Switch/match branches
            if 'match' in field or field.get('type') == 'match':
                match_def = field.get('match', field)
                on_field = match_def.get('on', match_def.get('field', ''))
                on_field = str(on_field).lstrip('$')
                for case_val in (match_def.get('cases', {}) or {}).keys():
                    branches.add(('match', on_field, str(case_val)))
                for case_def in (match_def.get('cases', {}) or {}).values():
                    if isinstance(case_def, dict) and 'fields' in case_def:
                        scan_fields(case_def['fields'], port)
                    elif isinstance(case_def, list):
                        scan_fields(case_def, port)

            # Flagged groups
            if 'flagged' in field:
                fg = field['flagged']
                flag_field = str(fg.get('field', ''))
                for group in fg.get('groups', []):
                    branches.add(('flagged', flag_field, str(group.get('bit', 0))))
                    if isinstance(group, dict) and 'fields' in group:
                        scan_fields(group['fields'], port)

            if 'fields' in field:
                scan_fields(field['fields'], port)
            if 'byte_group' in field:
                bg = field['byte_group']
                bg_fields = bg if isinstance(bg, list) else bg.get('fields', [])
                scan_fields(bg_fields, port)

    if 'fields' in schema:
        scan_fields(schema['fields'])
    if 'ports' in schema:
        for port_name, port_def in schema['ports'].items():
            branches.add(('port', 'fPort', str(port_name)))
            if isinstance(port_def, dict) and 'fields' in port_def:
                scan_fields(port_def['fields'], str(port_name))

    if not branches:
        return 1.0, []  # No branches = 100% coverage

    # Decode each vector and record which branches it actually exercised. The
    # previous implementation inferred coverage from how many distinct `flags`
    # values appeared and from the vector count, so it could report 90% without
    # any branch being entered, and returned no list of what was untested.
    covered = set()
    try:
        interpreter = SchemaInterpreter(schema)
    except Exception:
        return 0.0, ["Could not build interpreter for branch analysis"]

    for tv in vectors:
        payload_hex = str(tv.get('payload', '')).replace(' ', '')
        fport = tv.get('fPort') or tv.get('fport')
        observed = dict(tv.get('expected', {}) or {})
        try:
            result = interpreter.decode(bytes.fromhex(payload_hex), fPort=fport)
            if result.success:
                # Decoded values win: `expected` may only list a subset.
                observed.update(result.data)
        except Exception:
            pass

        if fport is not None:
            covered.add(('port', 'fPort', str(fport)))

        for kind, selector, value in branches:
            if kind == 'port':
                continue
            actual = observed.get(selector)
            if actual is None:
                continue
            if kind == 'flagged':
                try:
                    if (int(actual) >> int(value)) & 1:
                        covered.add((kind, selector, value))
                except (TypeError, ValueError):
                    continue
            elif kind == 'match':
                if str(actual) == value:
                    covered.add((kind, selector, value))
                    continue
                try:
                    if int(actual) == int(value, 0):
                        covered.add((kind, selector, value))
                except (TypeError, ValueError):
                    continue

    uncovered = [
        "%s %s%s untested" % (
            kind,
            selector,
            "[%s]" % value if kind == 'flagged' else "=%s" % value,
        )
        for kind, selector, value in sorted(branches - covered)
    ]
    coverage = len(covered & branches) / len(branches)
    return coverage, uncovered


STANDARD_SENSOR_IPSO = {
    # Temperature & Environment
    'temperature': 3303,
    'humidity': 3304,
    'pressure': 3323,
    'barometer': 3315,
    'altitude': 3321,
    'depth': 3319,
    
    # Light
    'illuminance': 3301,
    'light': 3301,
    'lux': 3301,
    
    # Electrical
    'voltage': 3316,
    'battery': 3316,
    'current': 3317,
    'power': 3328,
    'energy': 3331,
    'frequency': 3318,
    'powerfactor': 3329,
    
    # Distance & Position
    'distance': 3330,
    'level': 3319,
    
    # Gas & Air Quality
    'co2': 3325,
    'concentration': 3325,
    'conductivity': 3327,
    'acidity': 3326,
    'ph': 3326,
    'loudness': 3324,
    'sound': 3324,
    'noise': 3324,
    
    # Location
    'gps': 3336,
    'location': 3336,
    'latitude': 3336,
    'longitude': 3336,
    
    # Motion sensors
    'accelerometer': 3313,
    'acceleration': 3313,
    'gyroscope': 3334,
    'gyro': 3334,
    'magnetometer': 3314,
    'compass': 3332,
    'direction': 3332,
    'heading': 3332,
    
    # Control & Actuators
    'setpoint': 3308,
    'target': 3308,
    'valve': 3337,
    'positioner': 3337,
    'valveposition': 3337,
    'openness': 3337,
    'dimmer': 3343,
    'actuator': 3306,
    
    # Presence & Input
    'presence': 3302,
    'motion': 3302,
    'occupancy': 3302,
    'pir': 3302,
    'button': 3347,
    'switch': 3342,
    
    # Other
    'load': 3322,
    'weight': 3322,
    'percentage': 3320,
    'digital': 3200,
    'analog': 3202,
    'generic': 3300,
}

SENML_UNITS = {
    # Temperature & Environment
    'temperature': 'Cel',
    'humidity': '%RH',
    'pressure': 'Pa',
    'barometer': 'Pa',
    'altitude': 'm',
    'depth': 'm',
    
    # Light
    'illuminance': 'lx',
    'light': 'lx',
    'lux': 'lx',
    
    # Electrical
    'voltage': 'V',
    'battery': 'V',
    'current': 'A',
    'power': 'W',
    'energy': 'J',
    'frequency': 'Hz',
    
    # Distance
    'distance': 'm',
    'level': 'm',
    
    # Gas & Sound
    'co2': 'ppm',
    'concentration': 'ppm',
    'loudness': 'dB',
    'sound': 'dB',
    'noise': 'dB',
    
    # Location
    'latitude': 'lat',
    'longitude': 'lon',
    
    # Control
    'setpoint': 'Cel',
    'target': 'Cel',
    'valve': '%',
    'openness': '%',
    'dimmer': '%',
    'percentage': '%',
    
    # Weight
    'load': 'kg',
    'weight': 'kg',
    
    # Direction
    'compass': 'deg',
    'direction': 'deg',
    'heading': 'deg',
}


#: Unit strings that ARE the given SenML unit, just written differently. Only
#: these earn SenML credit: a field declared in kPa is not in Pa, and annotating
#: it as Pa misdeclares the value by 1000x downstream.
SENML_UNIT_EQUIVALENTS = {
    'Cel': {'°C', 'C', 'degC', 'Cel'},
    'V': {'V', 'volt'},
    '%RH': {'%', '%RH', 'RH'},
    'ppm': {'ppm'},
    'm': {'m', 'meter'},
    'deg': {'°', 'deg', 'degree'},
    'lx': {'lx', 'lux'},
    'Pa': {'Pa', 'pascal'},
    'A': {'A', 'amp'},
    'W': {'W', 'watt'},
    'J': {'J', 'joule'},
    'Hz': {'Hz'},
    'kg': {'kg'},
    '%': {'%'},
    'dB': {'dB'},
    'lat': {'lat', '°'},
    'lon': {'lon', '°'},
}

#: Units that measure the right quantity in the wrong scale. These are reported
#: as needing a conversion rather than silently credited or silently ignored.
SENML_UNIT_SCALED = {
    'Pa': {'kPa', 'hPa', 'mbar', 'bar', 'mmHg'},
    'm': {'mm', 'cm', 'km'},
    'V': {'mV', 'kV'},
    'A': {'mA', 'µA'},
    'W': {'mW', 'kW'},
    'ppm': {'ppb', 'mg⋅L⁻¹', 'mg/L'},
    'kg': {'g', 'mg', 't'},
}


def _name_tokens(field_name: str) -> List[str]:
    """Split a field name into lowercase word tokens.

    Sensor detection matches whole tokens, not substrings. Substring matching
    reads 'photosynthetically_active_radiation' as pH (3326) and
    'lightning_average_distance' as a light sensor (3301), which would attach
    wrong IPSO objects to real data.
    """
    spaced = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', field_name)
    return [t for t in re.split(r'[^A-Za-z0-9]+', spaced.lower()) if t]


def candidate_sensors(field: Dict[str, Any]) -> List[Tuple[str, int]]:
    """Every sensor type a field's name could plausibly denote, most specific first.

    A name can legitimately match more than one keyword: 'targetTemperature' is
    both a target (Set Point, 3308) and a temperature (3303), and only the author
    knows which the field is. Detection proposes; the annotation disambiguates.
    """
    declared = field.get('sensor')
    if declared is not None:
        key = str(declared).strip().lower()
        if key in ('none', 'false', 'no'):
            return []
        if key in STANDARD_SENSOR_IPSO:
            return [(key, STANDARD_SENSOR_IPSO[key])]
        return []

    tokens = set(_name_tokens(field.get('name', '')))
    if not tokens:
        return []
    matches = []
    # Longest keyword first so 'valveposition' outranks 'valve'.
    for keyword in sorted(STANDARD_SENSOR_IPSO, key=len, reverse=True):
        keyword_tokens = _name_tokens(keyword)
        if keyword_tokens and tokens.issuperset(keyword_tokens):
            matches.append((keyword, STANDARD_SENSOR_IPSO[keyword]))
    return matches


def detect_sensor(field: Dict[str, Any]) -> Optional[Tuple[str, int]]:
    """Return the most likely (keyword, ipso_object) for a field, or None.

    When the field already carries an `ipso:` annotation naming one of the
    plausible readings, that reading is used, so a correct annotation is never
    reported as a mismatch just because another keyword also matched the name.
    """
    matches = candidate_sensors(field)
    if not matches:
        return None
    annotated = _ipso_object_of(field.get('ipso')) if 'ipso' in field else None
    if annotated is not None:
        for keyword, ipso_object in matches:
            if ipso_object == annotated:
                return keyword, ipso_object
    return matches[0]


def _ipso_object_of(annotation: Any) -> Optional[int]:
    """Extract the IPSO object id from an `ipso:` annotation in either form."""
    if isinstance(annotation, dict):
        value = annotation.get('object')
    else:
        value = annotation
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _senml_unit_of(annotation: Any) -> Optional[str]:
    """Extract the unit from a `senml:` annotation."""
    if isinstance(annotation, dict):
        unit = annotation.get('unit')
        return str(unit) if unit is not None else None
    if isinstance(annotation, str):
        return annotation
    return None


def check_annotation_correctness(
    field: Dict[str, Any], keyword: str, ipso_object: int
) -> Tuple[bool, bool, List[str]]:
    """Check whether a field's ipso/senml annotations are actually correct.

    Presence alone used to earn the points, so `ipso: {object: 9999}` or a
    temperature object on a pH field scored the same as a correct mapping.
    Returns (ipso_ok, senml_ok, problems).
    """
    problems = []
    name = field.get('name', '?')

    ipso_ok = False
    if 'ipso' in field:
        found = _ipso_object_of(field['ipso'])
        if found is None:
            problems.append("%s: ipso annotation has no usable object id" % name)
        elif found != ipso_object:
            problems.append(
                "%s: ipso object %s does not match %s (%d) for this sensor type"
                % (name, found, keyword, ipso_object)
            )
        else:
            ipso_ok = True

    senml_ok = False
    expected_unit = SENML_UNITS.get(keyword)
    if 'senml' in field:
        unit = _senml_unit_of(field['senml'])
        declared = field.get('unit')
        if unit is None:
            problems.append("%s: senml annotation has no unit" % name)
        elif expected_unit and unit not in SENML_UNIT_EQUIVALENTS.get(
            expected_unit, {expected_unit}
        ):
            problems.append(
                "%s: senml unit %r is not the SenML unit for %s (expected %r)"
                % (name, unit, keyword, expected_unit)
            )
        elif declared is not None and declared not in SENML_UNIT_EQUIVALENTS.get(
            unit, {unit}
        ):
            # The annotation claims a unit the field itself does not declare.
            if declared in SENML_UNIT_SCALED.get(unit, set()):
                problems.append(
                    "%s: field is in %r but senml says %r -- convert the value or "
                    "declare the real unit" % (name, declared, unit)
                )
            else:
                problems.append(
                    "%s: senml unit %r disagrees with the field's unit %r"
                    % (name, unit, declared)
                )
        else:
            senml_ok = True

    return ipso_ok, senml_ok, problems


def check_semantic_annotations(schema: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Check completeness of semantic annotations for standard output formats."""
    results = {
        'total_fields': 0,
        'ipso_mapped': 0,
        'ipso_missing': [],
        'senml_mapped': 0,
        'senml_missing': [],
        'semantic_mapped': 0,
        'detectable_sensors': 0,
        'annotation_errors': [],
    }
    recommendations = []
    
    def scan_fields(fields: List[Dict], prefix: str = ''):
        for field in fields:
            field_name = field.get('name', '')
            full_name = f"{prefix}{field_name}" if prefix else field_name
            field_type = str(field.get('type', '')).lower()
            
            # Handle flagged groups (can be standalone or with type)
            if 'flagged' in field:
                fg = field['flagged']
                for group in fg.get('groups', []):
                    if 'fields' in group:
                        scan_fields(group['fields'], prefix)
                continue

            # Handle tlv dispatch. Without this, every field of a TLV schema was
            # invisible here: annotation scoring saw one field and no detectable
            # sensors, so a fully annotated channel/type schema scored zero
            # semantic points. This is the shape most of the corpus uses.
            if 'tlv' in field:
                for case_def in (field['tlv'].get('cases') or {}).values():
                    if isinstance(case_def, dict) and 'fields' in case_def:
                        scan_fields(case_def['fields'], prefix)
                    elif isinstance(case_def, list):
                        scan_fields(case_def, prefix)
                continue

            # Handle nested structures
            if field_type in ('object', 'match'):
                if 'fields' in field:
                    scan_fields(field['fields'], f"{full_name}.")
                if 'cases' in field:
                    for case_def in field.get('cases', {}).values():
                        if isinstance(case_def, dict) and 'fields' in case_def:
                            scan_fields(case_def['fields'], f"{full_name}.")
                        elif isinstance(case_def, list):
                            scan_fields(case_def, f"{full_name}.")
                continue
            
            # Skip raw/internal/component fields
            if (field_name.startswith('_') or 
                field_name.endswith('_raw') or
                field_name.endswith('Raw') or
                field_name.endswith('Low') or
                field_name.endswith('High')):
                continue
            
            results['total_fields'] += 1

            detected_sensor = detect_sensor(field)
            if detected_sensor is None:
                # Not a standard sensor reading. Annotations here are fine but
                # earn no credit: counting them let a schema reach full semantic
                # marks by annotating fields the formats do not model.
                continue

            keyword, ipso_object = detected_sensor
            results['detectable_sensors'] += 1

            ipso_ok, senml_ok, problems = check_annotation_correctness(
                field, keyword, ipso_object
            )
            results['annotation_errors'].extend(problems)

            if ipso_ok:
                results['ipso_mapped'] += 1
            elif 'ipso' not in field:
                results['ipso_missing'].append(
                    f"{full_name}: add ipso: {{object: {ipso_object}}}"
                )

            expected_unit = SENML_UNITS.get(keyword)
            if senml_ok:
                results['senml_mapped'] += 1
            elif 'senml' not in field and expected_unit:
                declared = field.get('unit')
                if declared is not None and declared in SENML_UNIT_SCALED.get(
                    expected_unit, set()
                ):
                    results['senml_missing'].append(
                        f"{full_name}: field is in {declared!r}; SenML wants "
                        f"{expected_unit!r} -- convert the value or declare the "
                        f"real unit before annotating"
                    )
                else:
                    results['senml_missing'].append(
                        f"{full_name}: add senml: {{unit: \"{expected_unit}\"}}"
                    )

            if 'semantic' in field and str(field['semantic']).strip():
                results['semantic_mapped'] += 1
    
    fields = schema.get('fields', [])
    scan_fields(fields)
    
    if 'ports' in schema:
        for port_name, port_def in schema['ports'].items():
            if isinstance(port_def, dict) and 'fields' in port_def:
                scan_fields(port_def['fields'], f"port{port_name}.")
    
    if results['ipso_missing']:
        recommendations.append(f"Add IPSO mappings for {len(results['ipso_missing'])} standard sensor fields")
    if results['senml_missing']:
        recommendations.append(f"Add SenML units for {len(results['senml_missing'])} fields")
    if results['annotation_errors']:
        recommendations.append(
            f"Correct {len(results['annotation_errors'])} wrong annotation(s) -- "
            f"a wrong mapping is worse than none"
        )

    return results, recommendations


#: Byte width of each wire type, used to judge what "maximum" means for a schema.
_WIRE_WIDTHS = {
    'u8': 1, 's8': 1, 'bool': 1,
    'u16': 2, 's16': 2, 'f16': 2,
    'u24': 3, 's24': 3,
    'u32': 4, 's32': 4, 'f32': 4,
    'u64': 8, 's64': 8, 'f64': 8,
}


def _iter_field_defs(node: Any):
    """Yield every field definition anywhere in a schema."""
    if isinstance(node, dict):
        if 'name' in node and 'type' in node:
            yield node
        for value in node.values():
            for found in _iter_field_defs(value):
                yield found
    elif isinstance(node, list):
        for item in node:
            for found in _iter_field_defs(item):
                yield found


def _min_field_width(schema: Dict[str, Any]) -> int:
    """Narrowest field in the schema, in bytes.

    A run of 0xFF this long is one whole field driven to all ones, which is what
    "maximum values" coverage means. Requiring a longer run would make the edge
    case unreachable for a schema built from single-byte fields.
    """
    widths = []
    for field in _iter_field_defs(schema.get('fields', schema)):
        base = re.split(r'[\[<]', str(field.get('type', '')))[0].strip()
        if base in _WIRE_WIDTHS:
            widths.append(_WIRE_WIDTHS[base])
    return min(widths) if widths else 1


def _can_decode_negative(schema: Dict[str, Any]) -> bool:
    """Whether any field in this schema is able to produce a negative value.

    A sensor whose every field is unsigned and unshifted cannot report a
    negative reading, so demanding a "negative values" vector would be an
    unsatisfiable requirement rather than a real gap.
    """
    for field in _iter_field_defs(schema.get('fields', schema)):
        base = re.split(r'[\[<]', str(field.get('type', '')))[0].strip()
        if base.startswith('s') or base.startswith('f') or base == 'number':
            return True
        for key in ('add', 'offset'):
            try:
                if float(field.get(key, 0)) < 0:
                    return True
            except (TypeError, ValueError):
                continue
        for op in field.get('transform', []) or []:
            if isinstance(op, dict):
                for key in ('add', 'mult'):
                    try:
                        if float(op.get(key, 0)) < 0:
                            return True
                    except (TypeError, ValueError):
                        continue
        if 'polynomial' in field or 'compute' in field:
            return True
    return False


def _has_variable_length_layout(schema: Dict[str, Any]) -> bool:
    """True when the payload length can differ between uplinks."""
    text = yaml.safe_dump(schema.get('fields', []))
    if 'ports' in schema:
        return True
    return any(
        re.search(r'^\s*%s:' % marker, text, re.MULTILINE)
        for marker in ('flagged', 'tlv', 'match', 'repeat')
    )


def check_edge_cases(
    schema: Dict[str, Any], decoded: Optional[List[Tuple[Dict, Dict]]] = None
) -> Tuple[List[str], List[str]]:
    """Check which edge cases the test vectors actually exercise.

    Judged from decoded values and payload bytes, never from words in a vector's
    name or description. The previous version credited 'max' coverage for any
    payload containing the byte 0xFF anywhere -- including in a device id -- and
    credited 'zero'/'negative' coverage to any vector merely *named* "zero" or
    "negative", so the dimension could be satisfied without testing anything.
    """
    vectors = schema.get('test_vectors', [])
    decoded = decoded or []

    covered = []
    missing = []

    has_zero = False
    has_max = False
    has_negative = False

    # Values a vector actually produced.
    for _tv, data in decoded:
        for key, val in data.items():
            if key.startswith('_') or isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                if val == 0:
                    has_zero = True
                if val < 0:
                    has_negative = True

    payload_lengths = set()
    for tv in vectors:
        payload_hex = tv.get('payload', '').replace(' ', '')
        if not payload_hex:
            continue
        payload_lengths.add(len(payload_hex) // 2)
        body = payload_hex.lower()
        # An all-zero or all-ones payload body is an unambiguous extreme. Two or
        # more consecutive 0xFF bytes indicate a whole field driven to all ones,
        # unlike a lone 0xFF that may be part of an identifier.
        if set(body) == {'0'}:
            has_zero = True
        if re.search(r'(?:ff){%d,}' % _min_field_width(schema), body):
            has_max = True

    # A single fixed-length layout has only one payload length, so there is no
    # shorter form left untested; requiring a "minimum payload" vector there
    # would be an unsatisfiable gap.
    if _has_variable_length_layout(schema):
        has_min_payload = len(payload_lengths) >= 2
    else:
        has_min_payload = bool(payload_lengths)

    if has_zero:
        covered.append('zero')
    else:
        missing.append('zero values')
    
    if has_max:
        covered.append('max')
    else:
        missing.append('maximum values')
    
    if has_negative or not _can_decode_negative(schema):
        covered.append('negative')
    else:
        missing.append('negative values')
    
    if has_min_payload:
        covered.append('min_payload')
    else:
        missing.append('minimum payload length')

    return covered, missing


def check_provenance(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Summarise where the test vectors' expected values came from.

    A vector declares this with `source:` -- one of vendor-doc, vendor-codec,
    field-capture, spec-example (independent of this implementation), or
    generated (produced from our own decoder, which proves only that behaviour
    has not changed). Missing means unknown.

    This is the difference between "the schema agrees with itself" and "the
    schema agrees with the device", and nothing in the score could see it.
    """
    vectors = schema.get('test_vectors', []) or []
    counts: Dict[str, int] = {}
    unknown_sources = []
    for tv in vectors:
        source = str(tv.get('source', 'unknown')).strip().lower()
        if source not in KNOWN_SOURCES:
            unknown_sources.append("%s: unrecognised source %r" % (tv.get('name', '?'), source))
            source = 'unknown'
        counts[source] = counts.get(source, 0) + 1
    independent = sum(counts.get(s, 0) for s in INDEPENDENT_SOURCES)
    return {
        'counts': counts,
        'independent_vectors': independent,
        'verification': 'independent' if independent else 'self',
        'issues': unknown_sources,
    }


def calculate_score(
    results: Dict[str, Any], require_provenance: bool = False
) -> Tuple[float, str]:
    """Calculate the weighted score and the resulting tier.

    The tier is not a pure function of the score: Gold and Platinum have MUST
    requirements (PS-239) that act as gates.
    """
    
    weights = {
        'schema_valid': 12,
        'has_test_vectors': 8,
        'python_tests_pass': 20,
        'js_tests_pass': 15,
        'branch_coverage': 12,
        'edge_cases': 8,
        'test_count': 5,
        'ipso_coverage': 7,
        'senml_coverage': 7,
        'semantic_coverage': 6,
    }
    
    score = 0
    max_score = sum(weights.values())

    # A JS run that never happened (no Node.js, or the generator cannot express
    # this schema) says nothing about the schema, so its weight leaves the
    # denominator instead of being scored as a failure. Otherwise the same
    # schema would score differently on different machines.
    js_status = results.get('js_status', 'skipped')
    if js_status in ('skipped', 'generator-gap'):
        max_score -= weights['js_tests_pass']

    # Schema valid (12 points)
    if results.get('schema_valid'):
        score += weights['schema_valid']

    # Has test vectors (8 points)
    if results.get('has_test_vectors'):
        score += weights['has_test_vectors']

    # Python tests pass (20 points)
    if results.get('python_tests_pass'):
        score += weights['python_tests_pass']

    # JS tests pass (15 points)
    if js_status == 'pass':
        score += weights['js_tests_pass']

    # Branch coverage (12 points, scaled)
    coverage = results.get('branch_coverage', 0)
    score += int(weights['branch_coverage'] * coverage)
    
    # Edge cases (8 points, scaled by coverage)
    edge_covered = results.get('edge_cases_covered', [])
    edge_missing = results.get('edge_cases_missing', [])
    if edge_covered:
        edge_ratio = len(edge_covered) / (len(edge_covered) + len(edge_missing))
        score += int(weights['edge_cases'] * edge_ratio)
    
    # Test count bonus (5 points for 5+ tests)
    test_count = results.get('test_count', 0)
    if test_count >= 5:
        score += weights['test_count']
    elif test_count >= 3:
        score += weights['test_count'] // 2
    
    # Semantic annotation scoring (20 points total)
    semantic = results.get('semantic_annotations', {})
    detectable = semantic.get('detectable_sensors', 0)
    total_fields = semantic.get('total_fields', 0)
    
    if detectable > 0:
        # IPSO coverage (7 points) - ratio of mapped to detectable, capped at 1.0
        ipso_mapped = semantic.get('ipso_mapped', 0)
        ipso_ratio = min(1.0, ipso_mapped / detectable)
        score += int(weights['ipso_coverage'] * ipso_ratio)
        
        # SenML coverage (7 points) - ratio of mapped to detectable, capped at 1.0
        senml_mapped = semantic.get('senml_mapped', 0)
        senml_ratio = min(1.0, senml_mapped / detectable)
        score += int(weights['senml_coverage'] * senml_ratio)
        
        # Semantic/normalized coverage (6 points)
        semantic_mapped = semantic.get('semantic_mapped', 0)
        semantic_ratio = min(1.0, semantic_mapped / detectable)
        score += int(weights['semantic_coverage'] * semantic_ratio)
    elif total_fields > 0:
        # Has fields but no standard sensors - partial credit based on any annotations
        ipso_mapped = semantic.get('ipso_mapped', 0)
        senml_mapped = semantic.get('senml_mapped', 0)
        semantic_mapped = semantic.get('semantic_mapped', 0)
        
        if ipso_mapped > 0:
            score += weights['ipso_coverage']
        if senml_mapped > 0:
            score += weights['senml_coverage']
        if semantic_mapped > 0:
            score += weights['semantic_coverage']
    else:
        # No fields at all - award full semantic points (edge case)
        score += weights['ipso_coverage'] + weights['senml_coverage'] + weights['semantic_coverage']
    
    pct = (score / max_score) * 100 if max_score else 0.0

    tier = TIER_REJECTED
    for threshold, name in TIER_THRESHOLDS:
        if pct >= threshold:
            tier = name
            break

    # PS-239 makes these MUSTs for Gold and Platinum, so they gate the tier
    # rather than contributing points a high score elsewhere can absorb.
    gates = []
    if results.get('test_count', 0) < MIN_VECTORS_HIGH_TIER:
        gates.append(
            "fewer than %d test vectors (PS-239)" % MIN_VECTORS_HIGH_TIER
        )
    if not results.get('python_tests_pass'):
        gates.append("test vectors do not pass (PS-239)")
    if results.get('branch_coverage', 0.0) < 1.0:
        gates.append("not all conditional branches covered (PS-239)")
    if results.get('edge_cases_missing'):
        gates.append("missing edge case vectors (PS-239)")
    if results.get('semantic_annotations', {}).get('annotation_errors'):
        gates.append("incorrect semantic annotations (PS-238)")

    # Platinum claims cross-validation. Without independent provenance the
    # vectors may have been generated from this interpreter, in which case the
    # score only shows self-consistency. Opt-in so the published rubric is not
    # changed silently.
    if require_provenance and results.get('provenance', {}).get(
        'verification'
    ) != 'independent':
        gates.append("no independently sourced test vectors")

    if gates and tier in ('PLATINUM', 'GOLD'):
        tier = 'SILVER' if pct >= 70 else tier
        results['tier_capped_by'] = gates

    return pct, tier


def generate_recommendations(results: Dict[str, Any]) -> List[str]:
    """Generate improvement recommendations."""
    recs = []
    
    if not results.get('schema_valid'):
        recs.append("Fix schema validation errors")
    
    if not results.get('has_test_vectors'):
        recs.append("Add at least 3 test vectors with payload and expected values")
    elif results.get('test_count', 0) < 3:
        recs.append("Add more test vectors (recommend at least 3)")
    
    if not results.get('python_tests_pass'):
        recs.append("Fix failing Python interpreter tests")
    
    js_status = results.get('js_status', 'skipped')
    if js_status == 'fail':
        recs.append("Generated JS codec disagrees with the test vectors")
    elif js_status == 'generator-gap':
        recs.append(
            "JS codec generator cannot express this schema (a generator gap, "
            "not a schema defect) - not counted against the score"
        )
    elif js_status == 'skipped' and results.get('has_test_vectors'):
        recs.append("Install Node.js to include JS cross-validation in the score")

    provenance = results.get('provenance', {})
    if results.get('has_test_vectors') and provenance.get('verification') == 'self':
        recs.append(
            "No test vector declares an independent source; add `source: "
            "vendor-doc|vendor-codec|field-capture` so the score reflects "
            "verification rather than self-consistency"
        )
    for issue in provenance.get('issues', [])[:2]:
        recs.append("Test vector provenance: %s" % issue)

    for gate in results.get('tier_capped_by', [])[:4]:
        recs.append("Tier capped: %s" % gate)

    edge_missing = results.get('edge_cases_missing', [])
    for missing in edge_missing[:3]:  # Top 3
        recs.append(f"Add test vector for {missing}")
    
    if results.get('branch_coverage', 1.0) < 0.8:
        recs.append("Add test vectors covering all switch/flagged branches")
    
    # Semantic annotation recommendations
    semantic = results.get('semantic_annotations', {})
    ipso_missing = semantic.get('ipso_missing', [])
    senml_missing = semantic.get('senml_missing', [])
    
    if ipso_missing:
        recs.append(f"Add IPSO object mappings for standard sensors ({len(ipso_missing)} fields)")
        for rec in ipso_missing[:2]:
            recs.append(f"  → {rec}")
    
    if senml_missing:
        recs.append(f"Add SenML units for standard sensors ({len(senml_missing)} fields)")
        for rec in senml_missing[:2]:
            recs.append(f"  → {rec}")
    
    return recs


def score_schema(
    schema_path: str, verbose: bool = False, require_provenance: bool = False
) -> ScoringResult:
    """Run all quality scoring checks on a schema."""
    
    timestamp = datetime.now().astimezone().isoformat()
    results = {}
    all_errors = []
    
    # Load schema
    schema, load_errors = load_schema(schema_path)
    if load_errors:
        return ScoringResult(
            schema_path=schema_path,
            timestamp=timestamp,
            score=0,
            tier='FAILED',
            details={'load_errors': load_errors},
            recommendations=['Fix YAML syntax errors']
        )
    
    # 1. Schema validation
    schema_valid, schema_errors = check_schema_valid(schema)
    results['schema_valid'] = schema_valid
    results['schema_errors'] = schema_errors
    all_errors.extend(schema_errors)
    
    if verbose and schema_errors:
        print(f"Schema errors: {schema_errors}")
    
    # 2. Test vectors
    has_vectors, test_count, vector_issues = check_test_vectors_exist(schema)
    results['has_test_vectors'] = has_vectors
    results['test_count'] = test_count
    results['vector_issues'] = vector_issues
    
    if verbose:
        print(f"Test vectors: {test_count}")
    
    # 3. Python tests
    py_pass, py_passed, py_failed, py_errors, decoded = run_python_tests(schema)
    results['python_tests_pass'] = py_pass
    results['python_passed'] = py_passed
    results['python_failed'] = py_failed
    results['python_errors'] = py_errors
    all_errors.extend(py_errors)

    if verbose:
        print(f"Python tests: {py_passed} passed, {py_failed} failed")

    # 4. JS cross-validation
    js_status, js_errors = run_js_tests(schema, schema_path)
    results['js_status'] = js_status
    # Retained for readers of existing reports; js_status carries the detail.
    results['js_tests_pass'] = js_status == 'pass'
    results['js_errors'] = js_errors

    if verbose:
        print(f"JS tests: {js_status}")
        if js_errors:
            for e in js_errors[:3]:
                print(f"  {e}")

    # 5. Branch coverage
    coverage, coverage_issues = analyze_branch_coverage(schema)
    results['branch_coverage'] = coverage
    results['coverage_issues'] = coverage_issues
    
    if verbose:
        print(f"Branch coverage: {coverage*100:.0f}%")
    
    # 6. Edge cases, judged from what the vectors actually decoded to
    edge_covered, edge_missing = check_edge_cases(schema, decoded)
    results['edge_cases_covered'] = edge_covered
    results['edge_cases_missing'] = edge_missing
    
    if verbose:
        print(f"Edge cases: {edge_covered}, missing: {edge_missing}")
    
    # 7. Semantic annotations (IPSO, SenML, TTN normalization)
    semantic_results, semantic_recs = check_semantic_annotations(schema)
    results['semantic_annotations'] = semantic_results
    
    if verbose:
        detected = semantic_results.get('detectable_sensors', 0)
        ipso = semantic_results.get('ipso_mapped', 0)
        senml = semantic_results.get('senml_mapped', 0)
        print(f"Semantic: {detected} sensors detected, {ipso} IPSO mapped, {senml} SenML mapped")
    
    # 8. Test vector provenance
    results['provenance'] = check_provenance(schema)

    if verbose:
        print(f"Provenance: {results['provenance']['verification']} "
              f"({results['provenance']['counts']})")

    # Calculate score and tier
    score, tier = calculate_score(results, require_provenance=require_provenance)

    # Generate recommendations
    recommendations = generate_recommendations(results)
    
    return ScoringResult(
        schema_path=schema_path,
        timestamp=timestamp,
        score=round(score, 1),
        tier=tier,
        details=results,
        recommendations=recommendations
    )


def compare_to_baseline(
    results: List[ScoringResult], baseline_path: str
) -> Optional[List[Tuple[str, str, str]]]:
    """Return schemas whose tier or score dropped against a baseline report.

    None means the baseline could not be read. Without this, a schema could
    silently degrade -- exactly what happened when a schema's fields were
    corrected and its stale vectors started failing, with nothing to catch it.
    """
    try:
        with open(baseline_path) as handle:
            baseline = json.load(handle)
    except (OSError, ValueError):
        return None

    order = ['FAILED', 'REJECTED', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM']
    previous = {}
    for entry in baseline.get('schemas', []):
        previous[Path(entry.get('schema_path', '')).name] = entry

    regressions = []
    for result in results:
        name = Path(result.schema_path).name
        was = previous.get(name)
        if not was:
            continue
        old_tier, new_tier = was.get('tier', 'FAILED'), result.tier
        old_score, new_score = was.get('score', 0), result.score
        tier_dropped = (
            old_tier in order and new_tier in order
            and order.index(new_tier) < order.index(old_tier)
        )
        # A small score change with the same tier is normal churn; a drop of more
        # than a point is worth surfacing.
        score_dropped = new_score < old_score - 1.0
        if tier_dropped or score_dropped:
            regressions.append((
                name,
                "%s (%.1f%%)" % (old_tier, old_score),
                "%s (%.1f%%)" % (new_tier, new_score),
            ))
    return regressions


def main():
    parser = argparse.ArgumentParser(
        description='Quality scoring tool for payload schemas'
    )
    parser.add_argument('path', help='Schema file or directory')
    parser.add_argument('--all', action='store_true', help='Process all schemas in directory')
    parser.add_argument('--report', '-r', help='Output JSON report file')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--quiet', '-q', action='store_true', help='Only output tier')
    parser.add_argument(
        '--baseline',
        help='Previous JSON report; fail if any schema regressed against it',
    )
    parser.add_argument(
        '--min-tier',
        choices=['PLATINUM', 'GOLD', 'SILVER', 'BRONZE'],
        help='Fail if any schema scores below this tier',
    )
    parser.add_argument(
        '--require-provenance',
        action='store_true',
        help='Cap the tier unless a test vector declares an independent source',
    )

    args = parser.parse_args()

    path = Path(args.path)
    results = []

    if path.is_file():
        result = score_schema(
            str(path), verbose=args.verbose,
            require_provenance=args.require_provenance,
        )
        results.append(result)
    elif path.is_dir() and args.all:
        for yaml_file in sorted(path.rglob('*.yaml')):
            if args.verbose:
                print(f"\n=== {yaml_file} ===")
            result = score_schema(
                str(yaml_file), verbose=args.verbose,
                require_provenance=args.require_provenance,
            )
            results.append(result)
    else:
        print(f"Error: {path} is not a file. Use --all for directories.")
        sys.exit(1)

    # Output results
    if args.report:
        report = {
            'timestamp': datetime.now().astimezone().isoformat(),
            'schemas': [r.to_dict() for r in results]
        }
        with open(args.report, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"Report written to {args.report}")
    
    # Console output
    for r in results:
        if args.quiet:
            print(f"{r.tier}")
        else:
            tier_color = {
                'PLATINUM': '\033[95m',  # Magenta
                'GOLD': '\033[93m',      # Yellow
                'SILVER': '\033[37m',    # White
                'BRONZE': '\033[33m',    # Orange
                'REJECTED': '\033[91m',  # Red
                'FAILED': '\033[91m',    # Red
            }.get(r.tier, '')
            reset = '\033[0m'

            print(f"\n{Path(r.schema_path).name}: {tier_color}{r.tier}{reset} ({r.score:.1f}%)")

            if r.recommendations and not args.quiet:
                print("Recommendations:")
                for rec in r.recommendations[:5]:
                    print(f"  - {rec}")

    failures = []

    # Regression gate: compare against a committed baseline report. A backlog of
    # low scores must not make the gate useless, so what fails the build is a
    # schema getting *worse*, not the backlog itself.
    if args.baseline:
        regressions = compare_to_baseline(results, args.baseline)
        if regressions is None:
            print(f"\nBaseline {args.baseline} could not be read", file=sys.stderr)
            failures.append('baseline-unreadable')
        elif regressions:
            print("\nRegressions against %s:" % args.baseline)
            for name, before, after in regressions:
                print("  %-40s %s -> %s" % (name, before, after))
            failures.append('regression')
        else:
            print("\nNo regressions against %s" % args.baseline)

    if args.min_tier:
        order = ['REJECTED', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM']
        floor = order.index(args.min_tier)
        below = [
            r for r in results
            if r.tier not in order or order.index(r.tier) < floor
        ]
        if below:
            print("\n%d schema(s) below %s" % (len(below), args.min_tier))
            for r in below[:10]:
                print("  %-40s %s" % (Path(r.schema_path).name, r.tier))
            failures.append('min-tier')

    summary = {}
    for r in results:
        summary[r.tier] = summary.get(r.tier, 0) + 1
    if len(results) > 1 and not args.quiet:
        print("\n%d schema(s): %s" % (
            len(results),
            ", ".join("%s %d" % (t, n) for t, n in sorted(summary.items())),
        ))

    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
