#!/usr/bin/env python3
"""
Generate JSON Schema for device codec output.

This tool generates a JSON Schema that describes the structure of the decoded
payload data returned by the codec. This schema can be used to validate
decoder output with standard JSON Schema tools.

Usage:
    python generate_output_schema.py schema.yaml > output-schema.json
    python generate_output_schema.py schema.yaml -o output-schema.json
"""

import argparse
import json
import re
import sys
import yaml
from typing import Any, Dict, List, Optional, Tuple


def scalar_json_type(value: Any) -> str:
    """The JSON Schema type name for a literal from a schema (a lookup value, say)."""
    if isinstance(value, bool):     # before int - a bool is an int in Python
        return 'boolean'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, float):
        return 'number'
    if value is None:
        return 'null'
    return 'string'


def union_types(values: List[Any]) -> List[str]:
    """The distinct JSON Schema types a set of literals needs, in first-seen order."""
    types: List[str] = []
    for value in values:
        candidate = scalar_json_type(value)
        if candidate not in types:
            types.append(candidate)
    if 'number' in types and 'integer' in types:
        # Every integer is a JSON number, so the union is redundant.
        types.remove('integer')
    return types


def lookup_json_schema(lookup: Any) -> Optional[Dict[str, Any]]:
    """The schema for a field's reported value once its `lookup` is applied.

    PS-106: lookup values MAY be numbers or strings, so the type comes from the values
    rather than being assumed. This used to declare a mapping as
    `["string", "integer"]` - too loose for the 23 string-valued mappings in the corpus,
    and wrong for a mapping to floats, which it typed as an integer.

    Both forms are closed:

    - A mapping omits the field entirely where the value is not a key and no `default`
      is declared (PS-269), so the raw number is never reported and cannot be a type. A
      `default` supplies one more possible value.
    - A sequence is indexed from zero (PS-104) and an out-of-bounds index MUST be an
      error (PS-105), so only its entries can be reported.

    Note that all five implementations *silently report the raw index* on an
    out-of-bounds sequence lookup instead of erroring - measured, not assumed. That is a
    PS-105 conformance gap in the implementations rather than a reason to loosen this
    declaration: validating such output should fail, because the payload is malformed.
    SESSION-NOTES.md records it.
    """
    if isinstance(lookup, dict):
        values = list(lookup.values())      # a `default:` label is one of these
    elif isinstance(lookup, list):
        values = list(lookup)
    else:
        return None
    if not values:
        return None
    types = union_types(values)
    schema: Dict[str, Any] = {"type": types[0] if len(types) == 1 else types}
    unique: List[Any] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    schema['enum'] = unique
    return schema


def yaml_type_to_json_schema(field_type: str, field_def: Dict[str, Any]) -> Dict[str, Any]:
    """Convert YAML field type to JSON Schema type definition."""
    
    # Handle bitfield syntax like u8[4:7]
    base_type = field_type.split('[')[0].split(':')[0]
    
    # Remove endian prefix
    if base_type.startswith('be_') or base_type.startswith('le_'):
        base_type = base_type[3:]
    
    # Integer types
    if base_type in ('u8', 'u16', 'u24', 'u32', 'u64', 'uint8', 'uint16', 'uint24', 'uint32', 'uint64'):
        schema = {"type": "integer", "minimum": 0}
        # Add maximum based on bit width
        bits = {'u8': 8, 'u16': 16, 'u24': 24, 'u32': 32, 'u64': 64,
                'uint8': 8, 'uint16': 16, 'uint24': 24, 'uint32': 32, 'uint64': 64}
        if base_type in bits:
            # Check for bitfield extraction
            if '[' in field_type:
                import re
                match = re.search(r'\[(\d+):(\d+)\]', field_type)
                if match:
                    low, high = int(match.group(1)), int(match.group(2))
                    bit_width = high - low + 1
                    schema["maximum"] = (1 << bit_width) - 1
            else:
                schema["maximum"] = (1 << bits[base_type]) - 1
        return schema
    
    # Signed integer types
    if base_type in ('s8', 's16', 's24', 's32', 's64', 'i8', 'i16', 'i24', 'i32', 'i64',
                     'int8', 'int16', 'int24', 'int32', 'int64'):
        return {"type": "integer"}
    
    # Float types - after modifiers, output is always number
    if base_type in ('f16', 'f32', 'f64', 'float16', 'float32', 'float64'):
        return {"type": "number"}
    
    # Bool type
    if base_type == 'bool':
        return {"type": "boolean"}
    
    # String types. `hex` is lowercase per PS-074 and PS-281; `hex:upper` is the
    # opt-out and keeps a plain string.
    if base_type == 'hex':
        return {"type": "string", "pattern": "^[0-9a-f]*$"}
    if base_type in ('ascii', 'string', 'base64', 'hex:upper'):
        return {"type": "string"}
    
    # Bytes type. PS-281 fixes this as a lowercase hex string, so the `format: array`
    # branch this used to offer would describe output no interpreter produces. It is
    # used by no schema in the repository; a schema that wants an octet array should
    # say `type: repeat` over `u8` and mean it.
    if base_type == 'bytes':
        return {"type": "string", "pattern": "^[0-9a-f]*$"}
    
    # bitfield_string and version_string report a formatted string, not a number.
    # These fell through to the default below, so every version field in the corpus -
    # 280 values - was declared "number" while reporting "v1.2.52".
    if base_type in ('bitfield_string', 'version_string'):
        return {"type": "string"}

    # `repeat` reports an array, `object` an object. Both also fell through.
    if base_type == 'repeat':
        return {"type": "array"}
    if base_type == 'object':
        return {"type": "object"}

    # Computed fields. `number` reports a JSON number; `integer` (PS-283) declares
    # that the arithmetic result is an integer, which is the only way an arithmetic
    # result can be typed as one - and therefore the only way a generated binding can
    # give it an integer type.
    if base_type == 'number':
        return {"type": "number"}
    if base_type == 'integer':
        return {"type": "integer"}
    
    # Enum type
    if base_type == 'enum':
        values = field_def.get('values', {})
        if values:
            listed = list(values.values()) if isinstance(values, dict) else list(values)
            fallback = field_def.get('default')
            if fallback is not None:
                # PS-068: an unmapped value is reported as `default`, so the set is
                # closed and can be enumerated.
                listed = listed + [fallback]
                types = union_types(listed)
                schema = {"type": types[0] if len(types) == 1 else types}
                unique = []
                for value in listed:
                    if value not in unique:
                        unique.append(value)
                schema['enum'] = unique
                return schema
            # With no `default`, an unmapped value is reported as the string
            # "unknown(<n>)", so the set is open: no `enum`, and `string` belongs in the
            # type even where every declared value is a number.
            types = union_types(listed + ['unknown(0)'])
            return {"type": types[0] if len(types) == 1 else types}
        return {"type": ["string", "integer"]}
    
    # Default to number (most fields with modifiers become numbers)
    return {"type": "number"}


def has_modifiers(field_def: Dict[str, Any]) -> bool:
    """Check if field has arithmetic modifiers that convert to float."""
    return any(k in field_def for k in ('mult', 'div', 'add', 'polynomial', 'transform'))


def field_to_json_schema(field_def: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a field definition to JSON Schema property."""
    
    field_type = field_def.get('type', '')
    name = field_def.get('name', '')
    
    # Skip internal fields
    if name.startswith('_'):
        return None
    
    # Get base schema from type
    schema = yaml_type_to_json_schema(field_type, field_def)
    
    # If field has modifiers, output becomes number
    if has_modifiers(field_def) and schema.get('type') == 'integer':
        schema = {"type": "number"}
    
    # Handle lookup - the reported value is the mapped one, typed from the mapping
    if 'lookup' in field_def:
        looked_up = lookup_json_schema(field_def['lookup'])
        if looked_up:
            schema = looked_up
    
    # Add description from field
    if field_def.get('description'):
        schema['description'] = field_def['description']
    
    # Add unit as description suffix
    if field_def.get('unit'):
        unit_desc = f"Unit: {field_def['unit']}"
        if 'description' in schema:
            schema['description'] += f" ({unit_desc})"
        else:
            schema['description'] = unit_desc
    
    # Add valid_range as bounds hint in description
    if field_def.get('valid_range'):
        vr = field_def['valid_range']
        range_desc = f"Valid range: [{vr[0]}, {vr[1]}]"
        if 'description' in schema:
            schema['description'] += f". {range_desc}"
        else:
            schema['description'] = range_desc
    
    return schema


NAME_FROM_REF = re.compile(r'\$\{(\w+)\}')

# Guard on the cross-product of enumerated keys. Two references over twenty labels each
# is 400 properties describing one field, which documents nothing; past this the pattern
# form is clearer.
MAX_ENUMERATED_KEYS = 64


def variable_sources(schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map every name a `name_from` reference can resolve to onto its definition.

    The interpreters store each field's value under its own name and, where `var:` is
    given, under that alias too, so both are addressable from a template.
    """
    sources: Dict[str, Dict[str, Any]] = {}

    def visit(node):
        if isinstance(node, dict):
            for key in ('name', 'var'):
                label = node.get(key)
                if isinstance(label, str) and label:
                    sources.setdefault(label, node)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(schema)
    return sources


def reference_labels(field_def: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """The closed set of strings a reference can substitute, or None if it is open.

    A `lookup` is closed for this purpose: PS-269 drops a field whose value is not in
    the mapping rather than reporting the raw number, so an unmapped value never reaches
    a key - it makes the whole `name_from` field fail to decode instead.
    """
    if not isinstance(field_def, dict):
        return None
    lookup = field_def.get('lookup')
    labels: List[str] = []
    if isinstance(lookup, dict):
        for key, value in lookup.items():
            if key == 'default':
                # `lookup: default:` supplies a label for everything unmapped, so the
                # set stays closed - it just gains one more member.
                labels.append(str(value))
            else:
                labels.append(str(value))
    elif isinstance(lookup, list):
        labels = [str(v) for v in lookup]
    elif field_def.get('type') in ('enum', 'Enum'):
        values = field_def.get('values')
        if isinstance(values, dict):
            labels = [str(v) for v in values.values()]
        elif isinstance(values, list):
            labels = [str(v) for v in values]
    return labels or None


def reference_pattern(field_def: Optional[Dict[str, Any]]) -> str:
    """A regular-expression fragment for whatever a reference substitutes.

    Deliberately permissive where the type is unclear: a fragment that is too narrow
    would make `patternProperties` reject a key the decoder really produces, while one
    that is too wide only describes the shape loosely.
    """
    labels = reference_labels(field_def)
    if labels:
        return '(?:' + '|'.join(re.escape(label) for label in sorted(set(labels))) + ')'
    if not isinstance(field_def, dict):
        return '.+'
    if 'cases' in field_def:      # a match discriminator substitutes its value
        return r'-?\d+'
    field_type = str(field_def.get('type', ''))
    if has_modifiers(field_def) or field_type.startswith(('f', 'number', 'udec', 'sdec')):
        # A scaled or floating value renders as a decimal, and an integral one loses its
        # trailing zero: `v_2.5_reading`, but `v_2_reading`.
        return r'-?\d+(?:\.\d+)?'
    if field_type.startswith(('u', 'byte', 'bool')):
        return r'\d+'
    if field_type.startswith(('s', 'i')) and field_type not in ('string',):
        return r'-?\d+'
    return '.+'


def name_from_targets(
    field_def: Dict[str, Any], sources: Dict[str, Dict[str, Any]]
) -> Tuple[List[str], Optional[str]]:
    """Resolve a `name_from` template into (exact keys, key pattern).

    Exactly one of the two is populated. Every reference resolving to a closed label set
    makes the whole key set finite, so the keys are declared outright; otherwise the
    template becomes an anchored pattern for `patternProperties`.
    """
    template = str(field_def.get('name_from') or '')
    references = NAME_FROM_REF.findall(template)
    if not references:
        return [template], None

    label_sets = [reference_labels(sources.get(ref)) for ref in references]
    if all(label_sets):
        total = 1
        for labels in label_sets:
            total *= len(labels)
        if total <= MAX_ENUMERATED_KEYS:
            keys = [template]
            for ref, labels in zip(references, label_sets):
                keys = [k.replace('${%s}' % ref, label) for k in keys for label in labels]
            return sorted(set(keys)), None

    pattern = '^'
    position = 0
    for match in NAME_FROM_REF.finditer(template):
        pattern += re.escape(template[position:match.start()])
        pattern += reference_pattern(sources.get(match.group(1)))
        position = match.end()
    pattern += re.escape(template[position:]) + '$'
    return [], pattern


def expand_refs(
    fields: List[Dict], definitions: Optional[Dict[str, Any]], depth: int = 0
) -> List[Dict]:
    """Splice `$ref: '#/definitions/name'` entries into the list they appear in.

    Matches the interpreters and the TS013 generator: only local
    `#/definitions/...` references resolve, and the target's `fields:` are spliced
    into the list rather than nested, because a container with no `type: object` is
    never descended into.

    Unresolved before this, so every field behind a reference was missing from the
    output schema - `basic_station.yaml` and `udp_packet_forwarder.yaml` described no
    properties at all, and `ref-header.yaml` described one of its three. Cross-file
    references are a pre-step (tools/schema_preprocessor.py), not this tool's job.
    """
    out: List[Dict] = []
    if depth > 16:   # a definition that refers to itself, directly or in a cycle
        return [f for f in fields if isinstance(f, dict)]
    definitions = definitions or {}
    prefix = '#/definitions/'
    for field in fields:
        if not isinstance(field, dict):
            continue
        ref = field.get('$ref')
        if not isinstance(ref, str):
            out.append(field)
            continue
        target = None
        if ref.startswith(prefix):
            definition = definitions.get(ref[len(prefix):])
            if isinstance(definition, dict):
                target = definition.get('fields')
        if not isinstance(target, list):
            # Unresolvable. Dropped rather than kept: unlike a decoder, this tool has
            # no read offsets to keep in step, and an entry with no name and no type
            # would only produce an empty property.
            continue
        out.extend(expand_refs(target, definitions, depth + 1))
    return out


def process_byte_group(bg_def: Dict[str, Any], properties: Dict, required: List[str],
                       definitions: Optional[Dict[str, Any]] = None,
                       context: Optional[Dict[str, Any]] = None):
    """Process byte_group fields and add to properties."""
    if isinstance(bg_def, dict):
        fields = bg_def.get('fields', [])
    else:
        fields = bg_def if isinstance(bg_def, list) else []
    
    for field in expand_refs(fields, definitions):
        if not isinstance(field, dict):
            continue
        name = field.get('name', '')
        if name and not name.startswith('_'):
            schema = field_to_json_schema(field)
            if schema:
                merge_property(properties, name, schema)
                required.append(name)


def merge_property(properties: Dict[str, Any], name: str, schema: Dict[str, Any]) -> None:
    """Record a property, widening its type if another branch already declared one.

    A `match` or `tlv` schema can report the same key from several branches with
    different types. milesight/am308 reads `tvoc` as a raw u16 on channel [8, 230] and
    as u16 with `div: 100` on [8, 125], so it is an integer on one branch and a number
    on the other - and whichever branch the walk saw last used to win, leaving the
    declared type wrong for every payload taking the other one.
    """
    existing = properties.get(name)
    if not existing or existing == schema:
        properties[name] = schema
        return
    old_types = existing.get('type')
    new_types = schema.get('type')
    if old_types is None or new_types is None or old_types == new_types:
        return
    merged = []
    for candidate in (old_types if isinstance(old_types, list) else [old_types]) + \
                     (new_types if isinstance(new_types, list) else [new_types]):
        if candidate not in merged:
            merged.append(candidate)
    # An integer is a JSON number, so "number" alone describes both without a union.
    if set(merged) == {'integer', 'number'}:
        merged = ['number']
    widened = dict(existing)
    widened['type'] = merged[0] if len(merged) == 1 else merged
    # Bounds and patterns from one branch do not hold for the other.
    for key in ('minimum', 'maximum', 'pattern', 'enum'):
        widened.pop(key, None)
    properties[name] = widened


def match_branches(field: Dict[str, Any]) -> List[List[Dict]]:
    """Every field list a `match` can take, across both syntaxes.

    Option B nests the construct under `match:` with `cases` keyed by value; the legacy
    form puts `on:`/`cases:` on a `type: match` field with cases as a list of
    `{case, fields}`. A `default` may itself carry fields, and `cases` may hold a
    literal `default` key - both are branches whose fields can be reported.

    Every branch merges into the same flat property set, which is what merge_property's
    type widening is for: two cases reporting the same name with different types produce
    one property accepting both.
    """
    branches: List[List[Dict]] = []

    def add(candidate):
        if isinstance(candidate, list):
            branches.append([f for f in candidate if isinstance(f, dict)])
        elif isinstance(candidate, dict) and isinstance(candidate.get('fields'), list):
            branches.append([f for f in candidate['fields'] if isinstance(f, dict)])

    match_def = field.get('match')
    if isinstance(match_def, dict) and match_def:
        cases = match_def.get('cases') or {}
        default = match_def.get('default')
    else:
        cases = field.get('cases') or []
        default = field.get('default')

    if isinstance(cases, dict):
        for case in cases.values():
            add(case)
    elif isinstance(cases, list):
        for case in cases:
            # Legacy: [{case: 1, fields: [...]}, ...]. A bare list of fields would have
            # no `fields` key and is not a branch.
            add(case)

    # `default: error` and `default: skip` report nothing.
    add(default)
    return branches


def process_match(field: Dict[str, Any], properties: Dict, required: List[str],
                  definitions: Optional[Dict[str, Any]] = None,
                  context: Optional[Dict[str, Any]] = None):
    """Declare the properties a `match` construct can report.

    Not traversed at all before this - process_fields handled `switch` and `tlv` but not
    `match` - so every field inside a match case was missing from the output schema.
    rbs30x.yaml reported 40 keys the schema described none of.
    """
    match_def = field.get('match')
    if isinstance(match_def, dict):
        # `name:` reports the discriminator itself; `var:` only stores it.
        name = match_def.get('name')
        if isinstance(name, str) and name and not name.startswith('_'):
            merge_property(properties, name, {
                "type": "integer",
                "description": "Matched discriminator value",
            })
            required.append(name)

    for branch in match_branches(field):
        process_fields(branch, properties, required, definitions, context)


def process_fields(fields: List[Dict], properties: Dict, required: List[str],
                   definitions: Optional[Dict[str, Any]] = None,
                   context: Optional[Dict[str, Any]] = None):
    """Process field list and populate properties dict."""
    
    for field in expand_refs(fields, definitions):
        if not isinstance(field, dict):
            continue
        
        # Handle byte_group
        if 'byte_group' in field:
            process_byte_group(field['byte_group'], properties, required, definitions,
                               context)
            continue
        
        # Handle flagged groups
        if 'flagged' in field:
            flagged = field['flagged']
            for group in flagged.get('groups', []):
                if 'fields' in group:
                    process_fields(group['fields'], properties, required, definitions, context)
            continue
        
        # Handle switch
        if 'switch' in field:
            switch = field['switch']
            for case_fields in switch.get('cases', {}).values():
                if isinstance(case_fields, list):
                    process_fields(case_fields, properties, required, definitions, context)
            continue
        
        # Handle match, in either syntax
        if 'match' in field or field.get('type') in ('match', 'Match'):
            process_match(field, properties, required, definitions, context)
            continue
        
        # Handle tlv
        if 'tlv' in field:
            tlv = field['tlv']
            for case_fields in tlv.get('cases', {}).values():
                if isinstance(case_fields, list):
                    process_fields(case_fields, properties, required, definitions, context)
            continue
        
        # Handle nested object
        if 'object' in field and not field.get('type'):
            obj_name = field['object']
            nested_props = {}
            nested_req = []
            nested_patterns: Dict[str, Any] = {}
            if 'fields' in field:
                nested_context = dict(context or {})
                nested_context['patterns'] = nested_patterns
                process_fields(field['fields'], nested_props, nested_req, definitions,
                               nested_context)
            properties[obj_name] = {
                "type": "object",
                "properties": nested_props
            }
            if nested_patterns:
                properties[obj_name]["patternProperties"] = nested_patterns
            if nested_req:
                properties[obj_name]["required"] = nested_req
            required.append(obj_name)
            continue
        
        # Regular field
        name = field.get('name', '')
        if name and not name.startswith('_'):
            schema = field_to_json_schema(field)
            if schema:
                if field.get('name_from'):
                    # The reported key comes from a template filled in from values
                    # decoded earlier (PS-265/PS-266), so the field's own name is never
                    # a key. Declared as exact properties where the template's
                    # references are closed, and as a patternProperties entry otherwise.
                    sources = (context or {}).get('sources') or {}
                    keys, pattern = name_from_targets(field, sources)
                    for key in keys:
                        merge_property(properties, key, schema)
                        required.append(key)
                    if pattern is not None:
                        patterns = (context or {}).get('patterns')
                        if patterns is not None:
                            patterns[pattern] = schema
                    continue
                merge_property(properties, name, schema)
                # Fields are generally required unless conditional
                required.append(name)


QUALITY_FLAG = {
    "type": "string",
    "enum": ["good", "out_of_range"],
    "description": "good = within valid_range; out_of_range = outside it",
}


def collect_quality_fields(node: Any, found: Dict[str, bool] = None) -> Dict[str, bool]:
    """Names of every field declaring a `valid_range`, mapped to "has name_from".

    Walked over the whole schema rather than just the field lists the properties come
    from, deliberately over-collecting: a declared-but-absent property costs nothing in
    JSON Schema, whereas a missing one combined with `additionalProperties: false`
    would reject valid decoder output. `definitions` matters most here - 159 of the
    corpus's 196 `valid_range` declarations live there, spliced in by `$ref`, which
    process_fields does not resolve.
    """
    if found is None:
        found = {}
    if isinstance(node, dict):
        name = node.get('name')
        valid_range = node.get('valid_range')
        if (
            isinstance(name, str)
            and not name.startswith('_')
            and isinstance(valid_range, (list, tuple))
            and len(valid_range) >= 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in valid_range[:2])
        ):
            found[name] = bool(node.get('name_from'))
        for value in node.values():
            collect_quality_fields(value, found)
    elif isinstance(node, list):
        for item in node:
            collect_quality_fields(item, found)
    return found


def quality_property(yaml_schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The `_quality` property, or None when no field declares a `valid_range`.

    PS-182: the object appears only when at least one field has `valid_range`, and only
    such fields appear in it - so the key set is closed and can be declared.
    """
    fields = collect_quality_fields(yaml_schema)
    if not fields:
        return None
    prop = {
        "type": "object",
        "description": (
            "Per-field quality flags for fields declaring valid_range (PS-131/PS-182). "
            "Present only when at least one such field is decoded; absent otherwise."
        ),
        "properties": {name: dict(QUALITY_FLAG) for name in sorted(fields)},
    }
    if any(fields.values()):
        # A `name_from` field's output key is decided at run time, so the key set is not
        # closed after all. Constrain the values and accept the key.
        prop["additionalProperties"] = dict(QUALITY_FLAG)
    else:
        prop["additionalProperties"] = False
    return prop


def generate_output_schema(yaml_schema: Dict[str, Any]) -> Dict[str, Any]:
    """Generate JSON Schema for codec output from YAML payload schema."""
    
    schema_name = yaml_schema.get('name', 'payload')
    schema_version = yaml_schema.get('version', 1)
    description = yaml_schema.get('description', f'{schema_name} decoded payload')
    
    properties = {}
    required = []
    definitions = yaml_schema.get('definitions') or {}
    patterns: Dict[str, Any] = {}
    context = {'patterns': patterns, 'sources': variable_sources(yaml_schema)}
    
    # Process top-level fields
    fields = yaml_schema.get('fields', [])
    process_fields(fields, properties, required, definitions, context)
    
    # Process port-based fields
    ports = yaml_schema.get('ports', {})
    for port_num, port_def in ports.items():
        if isinstance(port_def, dict) and 'fields' in port_def:
            process_fields(port_def['fields'], properties, required, definitions, context)
    
    # PS-182: `_quality` is part of the decoder's contract, so it is described rather
    # than merely tolerated by additionalProperties - a consumer reading this schema
    # could not otherwise learn the field exists.
    quality = quality_property(yaml_schema)
    if quality:
        properties['_quality'] = quality

    # Build output schema
    output_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": f"https://lorawan-schema.org/devices/{schema_name}/v{schema_version}/output",
        "title": f"{schema_name} Decoded Payload",
        "description": description,
        "type": "object",
        "properties": properties,
        # Metadata enrichment and implementation extensions may add keys; `_quality`
        # itself is declared above.
        "additionalProperties": True
    }

    # A `name_from` key that could not be enumerated is described by its shape.
    if patterns:
        output_schema['patternProperties'] = patterns
    
    # Don't require all fields since some may be conditional
    # Only mark truly required fields
    # For now, don't add required array since most fields are conditional
    
    return output_schema


def main():
    parser = argparse.ArgumentParser(
        description='Generate JSON Schema for device codec output'
    )
    parser.add_argument('schema', help='Input YAML schema file')
    parser.add_argument('-o', '--output', help='Output file (default: stdout)')
    parser.add_argument('--compact', action='store_true', help='Compact JSON output')
    
    args = parser.parse_args()
    
    # Load YAML schema
    with open(args.schema, 'r') as f:
        yaml_schema = yaml.safe_load(f)
    
    # Generate output schema
    output_schema = generate_output_schema(yaml_schema)
    
    # Format output
    indent = None if args.compact else 2
    json_output = json.dumps(output_schema, indent=indent)
    
    # Write output
    if args.output:
        with open(args.output, 'w') as f:
            f.write(json_output)
            f.write('\n')
        print(f"Generated: {args.output}", file=sys.stderr)
    else:
        print(json_output)


if __name__ == '__main__':
    main()
