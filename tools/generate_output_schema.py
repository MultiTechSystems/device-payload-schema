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
import sys
import yaml
from typing import Any, Dict, List, Optional


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
            return {"type": "string", "enum": list(values.values())}
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
    
    # Handle lookup - converts to string or keeps original
    if 'lookup' in field_def:
        lookup = field_def['lookup']
        if isinstance(lookup, dict):
            # Lookup values become the output
            schema = {"type": ["string", "integer"]}
        elif isinstance(lookup, list):
            schema = {"type": "string", "enum": lookup}
    
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
                       definitions: Optional[Dict[str, Any]] = None):
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
                  definitions: Optional[Dict[str, Any]] = None):
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
        process_fields(branch, properties, required, definitions)


def process_fields(fields: List[Dict], properties: Dict, required: List[str],
                   definitions: Optional[Dict[str, Any]] = None):
    """Process field list and populate properties dict."""
    
    for field in expand_refs(fields, definitions):
        if not isinstance(field, dict):
            continue
        
        # Handle byte_group
        if 'byte_group' in field:
            process_byte_group(field['byte_group'], properties, required, definitions)
            continue
        
        # Handle flagged groups
        if 'flagged' in field:
            flagged = field['flagged']
            for group in flagged.get('groups', []):
                if 'fields' in group:
                    process_fields(group['fields'], properties, required, definitions)
            continue
        
        # Handle switch
        if 'switch' in field:
            switch = field['switch']
            for case_fields in switch.get('cases', {}).values():
                if isinstance(case_fields, list):
                    process_fields(case_fields, properties, required, definitions)
            continue
        
        # Handle match, in either syntax
        if 'match' in field or field.get('type') in ('match', 'Match'):
            process_match(field, properties, required, definitions)
            continue
        
        # Handle tlv
        if 'tlv' in field:
            tlv = field['tlv']
            for case_fields in tlv.get('cases', {}).values():
                if isinstance(case_fields, list):
                    process_fields(case_fields, properties, required, definitions)
            continue
        
        # Handle nested object
        if 'object' in field and not field.get('type'):
            obj_name = field['object']
            nested_props = {}
            nested_req = []
            if 'fields' in field:
                process_fields(field['fields'], nested_props, nested_req, definitions)
            properties[obj_name] = {
                "type": "object",
                "properties": nested_props
            }
            if nested_req:
                properties[obj_name]["required"] = nested_req
            required.append(obj_name)
            continue
        
        # Regular field
        name = field.get('name', '')
        if name and not name.startswith('_'):
            schema = field_to_json_schema(field)
            if schema:
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
    
    # Process top-level fields
    fields = yaml_schema.get('fields', [])
    process_fields(fields, properties, required, definitions)
    
    # Process port-based fields
    ports = yaml_schema.get('ports', {})
    for port_num, port_def in ports.items():
        if isinstance(port_def, dict) and 'fields' in port_def:
            process_fields(port_def['fields'], properties, required, definitions)
    
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
