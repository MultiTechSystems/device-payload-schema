#!/usr/bin/env python3
"""
generate_firmware_codec.py - Generate firmware C codec from Payload Schema YAML.

Generates a header-only C file with:
  - Struct definitions for each payload variant
  - pack_<name>() encoder (sensor values → bytes for LoRaWAN TX)
  - unpack_<name>() decoder (bytes → struct for downlink RX)
  - payload_size_<name>() size calculator
  - Static test vectors for validation

Targets: Zephyr RTOS, Arduino, STM32 HAL, bare-metal ARM

Usage:
    python tools/generate_firmware_codec.py schema.yaml [-o output.h]
    python tools/generate_firmware_codec.py schemas/ -o generated/
"""

import argparse
import json
import re
import sys
import yaml
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Type map: schema type → (C type, byte size, signed, format)
TYPE_MAP = {
    'u8': ('uint8_t', 1, False), 'uint8': ('uint8_t', 1, False),
    's8': ('int8_t', 1, True), 'i8': ('int8_t', 1, True), 'int8': ('int8_t', 1, True),
    'u16': ('uint16_t', 2, False), 'uint16': ('uint16_t', 2, False),
    's16': ('int16_t', 2, True), 'i16': ('int16_t', 2, True), 'int16': ('int16_t', 2, True),
    'u24': ('uint32_t', 3, False),
    's24': ('int32_t', 3, True), 'i24': ('int32_t', 3, True),
    'u32': ('uint32_t', 4, False), 'uint32': ('uint32_t', 4, False),
    's32': ('int32_t', 4, True), 'i32': ('int32_t', 4, True), 'int32': ('int32_t', 4, True),
    'u64': ('uint64_t', 8, False), 'uint64': ('uint64_t', 8, False),
    's64': ('int64_t', 8, True), 'i64': ('int64_t', 8, True), 'int64': ('int64_t', 8, True),
    'f32': ('float', 4, True), 'float': ('float', 4, True),
    'f64': ('double', 8, True), 'double': ('double', 8, True),
}


def to_c(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_]', '_', name).lower()


def to_upper(name: str) -> str:
    return to_c(name).upper()


def type_size(t: str) -> Optional[int]:
    clean = re.sub(r'^(?:be_|le_)?', '', t)
    if clean in TYPE_MAP:
        return TYPE_MAP[clean][1]
    return None


def type_ctype(t: str) -> str:
    clean = re.sub(r'^(?:be_|le_)?', '', t)
    if clean in TYPE_MAP:
        return TYPE_MAP[clean][0]
    return 'uint8_t'


def is_signed(t: str) -> bool:
    clean = re.sub(r'^(?:be_|le_)?', '', t)
    return clean in TYPE_MAP and TYPE_MAP[clean][2]


def is_float(t: str) -> bool:
    clean = re.sub(r'^(?:be_|le_)?', '', t)
    return clean in ('f32', 'f64', 'float', 'double')


def field_endian(t: str, default: str) -> str:
    if t.startswith('be_'): return 'big'
    if t.startswith('le_'): return 'little'
    return default


def collect_fields(fields: List[Dict], prefix: str = '') -> List[Tuple[str, str, Dict]]:
    """Flatten fields into (c_name, c_type, field_dict) tuples for struct generation."""
    result = []
    for field in fields:
        if 'byte_group' in field:
            bg = field['byte_group']
            bg_fields = bg if isinstance(bg, list) else bg.get('fields', bg)
            for bf in (bg_fields if isinstance(bg_fields, list) else []):
                bname = bf.get('name', '_')
                if bname.startswith('_'):
                    continue
                btype = bf.get('type', 'u8')
                bit_m = re.match(r'u(\d+)\[(\d+):(\d+)\]', btype)
                if bit_m:
                    base_bits = int(bit_m.group(1))
                    ctype = f'uint{base_bits}_t' if base_bits <= 32 else 'uint32_t'
                    result.append((to_c(bname), ctype, bf))
                else:
                    result.append((to_c(bname), type_ctype(btype), bf))
            continue

        if 'flagged' in field:
            fg = field['flagged']
            for group in fg.get('groups', []):
                for gf in group.get('fields', []):
                    if gf.get('name', '_').startswith('_'):
                        continue
                    gtype = gf.get('type', 'u8')
                    if gtype == 'bitfield_string':
                        result.append((to_c(gf['name']), 'char', gf))
                    else:
                        result.append((to_c(gf['name']), type_ctype(gtype), gf))
            continue

        if 'tlv' in field:
            tlv = field['tlv']
            for case_key, case_fields in tlv.get('cases', {}).items():
                for cf in case_fields:
                    cfname = cf.get('name', '_')
                    if cfname.startswith('_'):
                        continue
                    cftype = cf.get('type', 'u8')
                    if cftype == 'bitfield_string':
                        result.append((to_c(cfname), 'char', cf))
                    else:
                        result.append((to_c(cfname), type_ctype(cftype), cf))
            continue

        if 'match' in field:
            match = field['match']
            for case_val, case_fields in match.get('cases', {}).items():
                if isinstance(case_fields, list):
                    result.extend(collect_fields(case_fields, prefix))
            continue

        name = field.get('name', '_')
        ftype = field.get('type', 'u8')

        # Skip computed fields (formula, ref, compute)
        if ftype == 'number':
            is_computed = any(k in field for k in ('formula', 'ref', 'compute'))
            if is_computed:
                continue

        if ftype == 'skip':
            continue

        if name.startswith('_'):
            continue

        if ftype == 'bitfield_string':
            result.append((to_c(name), 'char', field))
        elif ftype in ('ascii', 'hex'):
            result.append((to_c(name), 'char', field))
        elif ftype == 'enum':
            base = field.get('base', 'u8')
            result.append((to_c(name), type_ctype(base), field))
        else:
            result.append((to_c(name), type_ctype(ftype), field))

    # Deduplicate (union fields from match/tlv may repeat)
    seen = set()
    deduped = []
    for item in result:
        if item[0] not in seen:
            seen.add(item[0])
            deduped.append(item)
    return deduped


def calc_payload_sizes(fields: List[Dict]) -> Dict[str, int]:
    """Calculate min/max/typical payload sizes."""
    fixed = 0
    optional_groups = []

    for field in fields:
        if 'flagged' in field:
            fg = field['flagged']
            for group in fg.get('groups', []):
                group_size = 0
                for gf in group.get('fields', []):
                    sz = type_size(gf.get('type', 'u8'))
                    if sz:
                        group_size += sz
                    elif gf.get('type') == 'bitfield_string':
                        group_size += gf.get('length', 2)
                optional_groups.append(group_size)
            continue

        if 'tlv' in field:
            # TLV is variable, estimate max
            tlv = field['tlv']
            tag_size = sum(type_size(tf.get('type', 'u8')) or 1 for tf in tlv.get('tag_fields', []))
            max_case = 0
            for case_fields in tlv.get('cases', {}).values():
                case_size = tag_size
                for cf in case_fields:
                    sz = type_size(cf.get('type', 'u8'))
                    if sz:
                        case_size += sz
                    elif cf.get('type') == 'bitfield_string':
                        case_size += cf.get('length', 2)
                max_case = max(max_case, case_size)
            optional_groups.append(max_case * len(tlv.get('cases', {})))
            continue

        if 'byte_group' in field:
            bg = field['byte_group']
            if isinstance(bg, dict):
                fixed += bg.get('size', 1)
            else:
                fixed += 1
            continue

        if 'match' in field:
            # Variable
            continue

        ftype = field.get('type', 'u8')
        if ftype == 'number':
            continue
        if ftype == 'skip':
            fixed += field.get('length', 1)
            continue
        if ftype in ('ascii', 'hex', 'bytes'):
            fixed += field.get('length', 1)
            continue
        if ftype == 'bitfield_string':
            fixed += field.get('length', 2)
            continue
        sz = type_size(ftype)
        if sz:
            fixed += sz

    return {
        'min': fixed,
        'max': fixed + sum(optional_groups),
        'all_groups': fixed + sum(optional_groups),
    }


class FirmwareGenerator:
    def __init__(self, schema: Dict[str, Any], source: str = ''):
        self.schema = schema
        self.source = source
        self.name = to_c(schema.get('name', 'unknown'))
        self.name_upper = to_upper(schema.get('name', 'unknown'))
        self.version = schema.get('version', 1)
        self.endian = schema.get('endian', 'big')
        self.has_ports = 'ports' in schema

    def generate(self) -> str:
        guard = f'{self.name_upper}_CODEC_H'
        sections = []

        sections.append(self._gen_header(guard))
        sections.append(self._gen_read_write_helpers())

        if self.has_ports:
            for port_key, port_def in self.schema['ports'].items():
                sections.append(self._gen_port_section(int(port_key), port_def))
        else:
            fields = self.schema.get('fields', [])
            sections.append(self._gen_struct(self.name, fields))
            sections.append(self._gen_sizes(self.name, fields))
            sections.append(self._gen_pack(self.name, fields))
            sections.append(self._gen_unpack(self.name, fields))

        sections.append(self._gen_test_vectors())
        sections.append(self._gen_footer(guard))

        return '\n'.join(sections)

    def _gen_header(self, guard: str) -> str:
        desc = self.schema.get('description', '').strip().replace('\n', '\n * ')
        return f'''/**
 * {self.name}_codec.h — Auto-generated firmware codec
 *
 * {desc}
 *
 * Generated from: {self.source}
 * Schema version:  {self.version}
 * Generated at:    {datetime.now().strftime('%Y-%m-%d %H:%M')}
 * Endianness:      {self.endian}
 *
 * DO NOT EDIT — regenerate from schema
 */

#ifndef {guard}
#define {guard}

#include <stdint.h>
#include <stddef.h>
#include <string.h>
'''

    def _gen_footer(self, guard: str) -> str:
        return f'''
#endif /* {guard} */
'''

    def _gen_read_write_helpers(self) -> str:
        return '''/* ---- Byte-order helpers ---- */

static inline void put_u8(uint8_t *buf, size_t *pos, uint8_t v) {
    buf[*pos] = v; (*pos)++;
}

static inline void put_u16_be(uint8_t *buf, size_t *pos, uint16_t v) {
    buf[*pos] = (v >> 8) & 0xFF; buf[*pos + 1] = v & 0xFF; *pos += 2;
}

static inline void put_u16_le(uint8_t *buf, size_t *pos, uint16_t v) {
    buf[*pos] = v & 0xFF; buf[*pos + 1] = (v >> 8) & 0xFF; *pos += 2;
}

static inline void put_u24_be(uint8_t *buf, size_t *pos, uint32_t v) {
    buf[*pos] = (v >> 16) & 0xFF; buf[*pos + 1] = (v >> 8) & 0xFF;
    buf[*pos + 2] = v & 0xFF; *pos += 3;
}

static inline void put_u24_le(uint8_t *buf, size_t *pos, uint32_t v) {
    buf[*pos] = v & 0xFF; buf[*pos + 1] = (v >> 8) & 0xFF;
    buf[*pos + 2] = (v >> 16) & 0xFF; *pos += 3;
}

static inline void put_u32_be(uint8_t *buf, size_t *pos, uint32_t v) {
    buf[*pos] = (v >> 24); buf[*pos + 1] = (v >> 16) & 0xFF;
    buf[*pos + 2] = (v >> 8) & 0xFF; buf[*pos + 3] = v & 0xFF; *pos += 4;
}

static inline void put_u32_le(uint8_t *buf, size_t *pos, uint32_t v) {
    buf[*pos] = v & 0xFF; buf[*pos + 1] = (v >> 8) & 0xFF;
    buf[*pos + 2] = (v >> 16) & 0xFF; buf[*pos + 3] = (v >> 24); *pos += 4;
}

static inline uint8_t get_u8(const uint8_t *buf, size_t *pos) {
    return buf[(*pos)++];
}

static inline uint16_t get_u16_be(const uint8_t *buf, size_t *pos) {
    uint16_t v = ((uint16_t)buf[*pos] << 8) | buf[*pos + 1]; *pos += 2; return v;
}

static inline uint16_t get_u16_le(const uint8_t *buf, size_t *pos) {
    uint16_t v = buf[*pos] | ((uint16_t)buf[*pos + 1] << 8); *pos += 2; return v;
}

static inline int16_t get_s16_be(const uint8_t *buf, size_t *pos) {
    return (int16_t)get_u16_be(buf, pos);
}

static inline int16_t get_s16_le(const uint8_t *buf, size_t *pos) {
    return (int16_t)get_u16_le(buf, pos);
}

static inline uint32_t get_u32_be(const uint8_t *buf, size_t *pos) {
    uint32_t v = ((uint32_t)buf[*pos] << 24) | ((uint32_t)buf[*pos+1] << 16) |
                 ((uint32_t)buf[*pos+2] << 8) | buf[*pos+3];
    *pos += 4; return v;
}

static inline uint32_t get_u32_le(const uint8_t *buf, size_t *pos) {
    uint32_t v = buf[*pos] | ((uint32_t)buf[*pos+1] << 8) |
                 ((uint32_t)buf[*pos+2] << 16) | ((uint32_t)buf[*pos+3] << 24);
    *pos += 4; return v;
}
'''

    def _gen_port_section(self, port_key: int, port_def: Dict) -> str:
        direction = port_def.get('direction', 'uplink')
        desc = port_def.get('description', '')
        suffix = f'{self.name}_port{port_key}'
        fields = port_def.get('fields', [])
        parts = []
        parts.append(f'/* ==== Port {port_key}: {desc} ({direction}) ==== */\n')
        parts.append(self._gen_struct(suffix, fields))
        parts.append(self._gen_sizes(suffix, fields))
        if direction == 'uplink':
            parts.append(self._gen_unpack(suffix, fields))
        else:
            parts.append(self._gen_pack(suffix, fields))
            parts.append(self._gen_unpack(suffix, fields))
        return '\n'.join(parts)

    def _gen_struct(self, name: str, fields: List[Dict]) -> str:
        members = collect_fields(fields)
        lines = [f'typedef struct {{']
        for cname, ctype, fdef in members:
            unit = fdef.get('unit', '')
            unit_comment = f'  /* {unit} */' if unit else ''
            if ctype == 'char' and fdef.get('type') == 'bitfield_string':
                maxlen = 32
                lines.append(f'    char {cname}[{maxlen}];{unit_comment}')
            elif ctype == 'char':
                maxlen = fdef.get('length', 16) + 1
                lines.append(f'    char {cname}[{maxlen}];')
            else:
                # Determine if we need a float for decoded value or raw int
                has_modifier = any(k in fdef for k in ('div', 'mult', 'add', 'formula', 'transform'))
                if has_modifier and not is_float(fdef.get('type', '')):
                    lines.append(f'    float {cname};{unit_comment}')
                else:
                    lines.append(f'    {ctype} {cname};{unit_comment}')
        # Presence flags for flagged groups (only if not already in struct)
        existing_names = {m[0] for m in members}
        for field in fields:
            if 'flagged' in field:
                fg = field['flagged']
                fname = to_c(fg['field'])
                if fname not in existing_names:
                    flag_ctype = type_ctype(self._find_field_type(fields, fg['field']))
                    lines.append(f'    {flag_ctype} {fname};')
                    existing_names.add(fname)
        lines.append(f'}} {name}_t;')
        lines.append('')
        return '\n'.join(lines)

    def _find_field_type(self, fields: List[Dict], name: str) -> str:
        for f in fields:
            if f.get('name') == name:
                return f.get('type', 'u16')
        return 'u16'

    def _gen_sizes(self, name: str, fields: List[Dict]) -> str:
        sizes = calc_payload_sizes(fields)
        lines = []
        lines.append(f'#define {to_upper(name)}_PAYLOAD_MIN  {sizes["min"]}')
        lines.append(f'#define {to_upper(name)}_PAYLOAD_MAX  {sizes["max"]}')
        lines.append('')
        return '\n'.join(lines)

    def _gen_pack(self, name: str, fields: List[Dict]) -> str:
        endian = self.endian
        suffix = '_be' if endian == 'big' else '_le'
        lines = []
        lines.append(f'/**')
        lines.append(f' * Pack {name} payload into bytes for LoRaWAN TX.')
        lines.append(f' * @param data  Input struct with sensor values')
        lines.append(f' * @param buf   Output buffer (at least {to_upper(name)}_PAYLOAD_MAX bytes)')
        lines.append(f' * @return      Bytes written, or -1 on error')
        lines.append(f' */')
        lines.append(f'static inline int pack_{name}(const {name}_t *data, uint8_t *buf) {{')
        lines.append(f'    if (!data || !buf) return -1;')
        lines.append(f'    size_t pos = 0;')
        lines.append('')
        self._gen_pack_fields(lines, fields, '    ', suffix)
        lines.append(f'    return (int)pos;')
        lines.append(f'}}')
        lines.append('')
        return '\n'.join(lines)

    def _gen_pack_fields(self, lines: List[str], fields: List[Dict], indent: str, suffix: str):
        for field in fields:
            # byte_group
            if 'byte_group' in field:
                bg = field['byte_group']
                bg_fields = bg if isinstance(bg, list) else bg.get('fields', bg)
                bg_size = 1
                if isinstance(bg, dict):
                    bg_size = bg.get('size', 1)
                lines.append(f'{indent}/* byte_group */')
                lines.append(f'{indent}{{')
                lines.append(f'{indent}    uint{bg_size*8}_t packed = 0;')
                for bf in (bg_fields if isinstance(bg_fields, list) else []):
                    bname = bf.get('name', '_')
                    btype = bf.get('type', 'u8')
                    bit_m = re.match(r'u\d+\[(\d+):(\d+)\]', btype)
                    if bit_m:
                        lo, hi = int(bit_m.group(1)), int(bit_m.group(2))
                        width = hi - lo + 1
                        mask = (1 << width) - 1
                        if bname.startswith('_'):
                            lines.append(f'{indent}    /* {bname}: reserved */')
                        else:
                            rev = self._c_reverse_mod(f'data->{to_c(bname)}', bf)
                            lines.append(f'{indent}    packed |= ((uint{bg_size*8}_t)({rev}) & 0x{mask:X}U) << {lo};')
                lines.append(f'{indent}    put_u{bg_size*8}{suffix}(buf, &pos, packed);')
                lines.append(f'{indent}}}')
                continue

            # flagged — uses the flags field already written above
            if 'flagged' in field:
                fg = field['flagged']
                flag_name = to_c(fg['field'])
                flag_type = self._find_field_type(fields, fg['field'])
                flag_sz = type_size(flag_type) or 2
                lines.append(f'{indent}/* flagged groups (using {flag_name} already written above) */')
                lines.append(f'{indent}uint{flag_sz*8}_t flags_val = data->{flag_name};')
                lines.append('')
                for group in fg.get('groups', []):
                    bit = group['bit']
                    lines.append(f'{indent}if (flags_val & (1U << {bit})) {{')
                    for gf in group.get('fields', []):
                        self._gen_pack_one(lines, gf, indent + '    ', suffix)
                    lines.append(f'{indent}}}')
                continue

            # tlv
            if 'tlv' in field:
                self._gen_pack_tlv(lines, field['tlv'], indent, suffix)
                continue

            # match — skip
            if 'match' in field:
                lines.append(f'{indent}/* TODO: match encoding */')
                continue

            # type: number (computed) — skip
            if field.get('type') == 'number':
                continue

            self._gen_pack_one(lines, field, indent, suffix)

    def _gen_pack_one(self, lines: List[str], field: Dict, indent: str, suffix: str):
        name = field.get('name', '_')
        ftype = field.get('type', 'u8')
        cname = to_c(name)

        if ftype == 'skip':
            length = field.get('length', 1)
            lines.append(f'{indent}memset(buf + pos, 0, {length}); pos += {length};')
            return

        if ftype == 'bitfield_string':
            lines.append(f'{indent}/* bitfield_string {cname} — encode from string */')
            lines.append(f'{indent}/* TODO: parse data->{cname} string and pack bits */')
            length = field.get('length', 2)
            lines.append(f'{indent}pos += {length}; /* placeholder */')
            return

        if ftype in ('ascii', 'hex'):
            length = field.get('length', 1)
            lines.append(f'{indent}memcpy(buf + pos, data->{cname}, {length}); pos += {length};')
            return

        sz = type_size(ftype)
        if sz is None:
            lines.append(f'{indent}/* unsupported type: {ftype} */')
            return

        if name.startswith('_'):
            const_val = field.get('value', 0)
            if sz == 1:
                lines.append(f'{indent}put_u8(buf, &pos, {const_val});')
            else:
                lines.append(f'{indent}put_u{sz*8}{suffix}(buf, &pos, {const_val});')
            return

        has_modifier = any(k in field for k in ('div', 'mult', 'add', 'formula', 'transform'))
        if 'formula' in field and not any(k in field for k in ('div', 'mult', 'add', 'transform')):
            lines.append(f'{indent}/* formula field: provide raw value */')
            lines.append(f'{indent}/* decode formula: {field["formula"]} */')

        rev = self._c_reverse_mod(f'data->{cname}', field)

        if sz == 1:
            lines.append(f'{indent}put_u8(buf, &pos, (uint8_t)({rev}));')
        elif is_signed(ftype):
            lines.append(f'{indent}put_u{sz*8}{suffix}(buf, &pos, (uint{sz*8}_t)(int{sz*8}_t)({rev}));')
        else:
            lines.append(f'{indent}put_u{sz*8}{suffix}(buf, &pos, (uint{sz*8}_t)({rev}));')

    def _gen_pack_tlv(self, lines: List[str], tlv: Dict, indent: str, suffix: str):
        tag_fields = tlv.get('tag_fields', [])
        cases = tlv.get('cases', {})
        lines.append(f'{indent}/* TLV encoding */')

        for case_key, case_fields in cases.items():
            if case_key.startswith('['):
                tag_vals = json.loads(case_key)
            else:
                tag_vals = [int(case_key)]

            data_names = [cf.get('name') for cf in case_fields if cf.get('name') and not cf['name'].startswith('_')]
            if not data_names:
                continue

            # Simple presence check: use first field
            first = to_c(data_names[0])
            lines.append(f'{indent}/* case {case_key} */')
            lines.append(f'{indent}{{')
            # Write tag bytes
            for tv in tag_vals:
                lines.append(f'{indent}    put_u8(buf, &pos, 0x{tv:02X});')
            # Write value fields
            for cf in case_fields:
                self._gen_pack_one(lines, cf, indent + '    ', suffix)
            lines.append(f'{indent}}}')

    def _c_reverse_mod(self, expr: str, field: Dict) -> str:
        """Generate C expression to reverse modifiers in opposite YAML key order: value → raw."""
        has_formula_only = 'formula' in field and not any(k in field for k in ('div', 'mult', 'add'))
        if has_formula_only:
            return expr  # Can't auto-reverse arbitrary formula

        # Collect modifier keys in YAML order, then reverse
        mod_keys = [k for k in field if k in ('add', 'mult', 'div')]
        result = expr
        for key in reversed(mod_keys):
            if key == 'add':
                result = f'({result} - ({float(field["add"])}f))'
            elif key == 'div':
                result = f'({result} * {float(field["div"])}f)'
            elif key == 'mult':
                result = f'({result} / {float(field["mult"])}f)'

        # If any modifier was applied, we need rounding
        if mod_keys:
            return f'(int32_t)({result} + 0.5f)'

        return result

    def _gen_unpack(self, name: str, fields: List[Dict]) -> str:
        endian = self.endian
        suffix = '_be' if endian == 'big' else '_le'
        lines = []
        lines.append(f'/**')
        lines.append(f' * Unpack {name} payload from received bytes.')
        lines.append(f' * @param buf   Input buffer')
        lines.append(f' * @param len   Buffer length')
        lines.append(f' * @param data  Output struct')
        lines.append(f' * @return      Bytes consumed, or -1 on error')
        lines.append(f' */')
        lines.append(f'static inline int unpack_{name}(const uint8_t *buf, size_t len, {name}_t *data) {{')
        lines.append(f'    if (!buf || !data) return -1;')
        lines.append(f'    memset(data, 0, sizeof(*data));')
        lines.append(f'    size_t pos = 0;')
        lines.append('')
        self._gen_unpack_fields(lines, fields, '    ', suffix)
        lines.append(f'    return (int)pos;')
        lines.append(f'}}')
        lines.append('')
        return '\n'.join(lines)

    def _gen_unpack_fields(self, lines: List[str], fields: List[Dict], indent: str, suffix: str):
        for field in fields:
            if 'byte_group' in field:
                bg = field['byte_group']
                bg_fields = bg if isinstance(bg, list) else bg.get('fields', bg)
                bg_size = 1
                if isinstance(bg, dict):
                    bg_size = bg.get('size', 1)
                lines.append(f'{indent}/* byte_group */')
                lines.append(f'{indent}if (pos + {bg_size} > len) return -1;')
                lines.append(f'{indent}{{')
                lines.append(f'{indent}    uint{bg_size*8}_t packed = get_u{bg_size*8}{suffix}(buf, &(size_t){{pos}});')
                for bf in (bg_fields if isinstance(bg_fields, list) else []):
                    bname = bf.get('name', '_')
                    btype = bf.get('type', 'u8')
                    bit_m = re.match(r'u\d+\[(\d+):(\d+)\]', btype)
                    if bit_m and not bname.startswith('_'):
                        lo, hi = int(bit_m.group(1)), int(bit_m.group(2))
                        width = hi - lo + 1
                        mask = (1 << width) - 1
                        extracted = f'(packed >> {lo}) & 0x{mask:X}U'
                        val = self._c_apply_mod(extracted, bf)
                        lines.append(f'{indent}    data->{to_c(bname)} = {val};')
                lines.append(f'{indent}    pos += {bg_size};')
                lines.append(f'{indent}}}')
                continue

            if 'flagged' in field:
                fg = field['flagged']
                flag_name = to_c(fg['field'])
                flag_type = self._find_field_type(fields, fg['field'])
                flag_sz = type_size(flag_type) or 2
                lines.append(f'{indent}/* flagged on {flag_name} */')
                for group in fg.get('groups', []):
                    bit = group['bit']
                    lines.append(f'{indent}if (data->{flag_name} & (1U << {bit})) {{')
                    for gf in group.get('fields', []):
                        self._gen_unpack_one(lines, gf, indent + '    ', suffix)
                    lines.append(f'{indent}}}')
                continue

            if 'tlv' in field:
                lines.append(f'{indent}/* TLV decoding — use reference interpreter */')
                continue

            if 'match' in field:
                lines.append(f'{indent}/* match decoding — use reference interpreter */')
                continue

            if field.get('type') == 'number':
                continue

            self._gen_unpack_one(lines, field, indent, suffix)

    def _gen_unpack_one(self, lines: List[str], field: Dict, indent: str, suffix: str):
        name = field.get('name', '_')
        ftype = field.get('type', 'u8')
        cname = to_c(name)

        if ftype == 'skip':
            lines.append(f'{indent}pos += {field.get("length", 1)};')
            return

        if ftype == 'bitfield_string':
            length = field.get('length', 2)
            lines.append(f'{indent}/* bitfield_string — decode in application */')
            lines.append(f'{indent}pos += {length};')
            return

        sz = type_size(ftype)
        if sz is None:
            lines.append(f'{indent}/* unsupported: {ftype} */')
            return

        lines.append(f'{indent}if (pos + {sz} > len) return -1;')

        signed = is_signed(ftype)
        eo = field_endian(ftype, self.endian)
        esuffix = '_be' if eo == 'big' else '_le'

        if sz == 1:
            raw = 'get_u8(buf, &pos)'
            if signed:
                raw = f'(int8_t){raw}'
        else:
            fn = f'get_{"s" if signed else "u"}{sz*8}{esuffix}'
            raw = f'{fn}(buf, &pos)'

        if name.startswith('_'):
            # Read but store for flagged reference
            if field.get('var') or name == to_c(field.get('var', '')):
                lines.append(f'{indent}data->{cname} = {raw};')
            else:
                lines.append(f'{indent}(void){raw}; /* {name} */')
            return

        val = self._c_apply_mod(raw, field)
        lines.append(f'{indent}data->{cname} = {val};')

    def _c_apply_mod(self, raw_expr: str, field: Dict) -> str:
        if 'formula' in field:
            return raw_expr  # Application computes
        expr = raw_expr
        # Apply modifiers in YAML key order (dict preserves insertion order)
        for key in field:
            if key == 'mult':
                expr = f'({expr} * {float(field["mult"])}f)'
            elif key == 'div':
                expr = f'({expr} / {float(field["div"])}f)'
            elif key == 'add':
                expr = f'({expr} + {float(field["add"])}f)'
        
        # Apply transform array if present
        if 'transform' in field:
            for op in field['transform']:
                if 'sqrt' in op and op['sqrt']:
                    expr = f'sqrtf({expr} > 0 ? {expr} : 0)'
                elif 'abs' in op and op['abs']:
                    expr = f'fabsf({expr})'
                elif 'pow' in op:
                    expr = f'powf({expr}, {float(op["pow"])}f)'
                elif 'floor' in op:  # Clamp lower
                    expr = f'fmaxf({expr}, {float(op["floor"])}f)'
                elif 'ceiling' in op:  # Clamp upper
                    expr = f'fminf({expr}, {float(op["ceiling"])}f)'
                elif 'clamp' in op:
                    bounds = op['clamp']
                    if isinstance(bounds, list) and len(bounds) >= 2:
                        expr = f'fmaxf({float(bounds[0])}f, fminf({float(bounds[1])}f, {expr}))'
                elif 'log10' in op and op['log10']:
                    expr = f'log10f({expr} > 1e-10f ? {expr} : 1e-10f)'
                elif 'log' in op and op['log']:
                    expr = f'logf({expr} > 1e-10f ? {expr} : 1e-10f)'
                elif 'add' in op:
                    expr = f'({expr} + {float(op["add"])}f)'
                elif 'mult' in op:
                    expr = f'({expr} * {float(op["mult"])}f)'
                elif 'div' in op and op['div'] != 0:
                    expr = f'({expr} / {float(op["div"])}f)'
        
        return expr

    def _gen_test_vectors(self) -> str:
        vectors = self.schema.get('test_vectors', [])
        if not vectors:
            return '/* No test vectors in schema */\n'

        lines = ['/* ==== Test Vectors ==== */']
        lines.append('')

        for idx, tv in enumerate(vectors):
            tv_name = to_c(tv.get('name', f'test{idx}'))
            payload_hex = tv.get('payload', '').replace(' ', '')
            payload_bytes = [f'0x{payload_hex[i:i+2]}' for i in range(0, len(payload_hex), 2)]

            lines.append(f'/* {tv.get("description", tv_name)} */')
            lines.append(f'static const uint8_t {self.name}_tv_{tv_name}[] = {{')
            # Format 12 bytes per line
            for i in range(0, len(payload_bytes), 12):
                chunk = ', '.join(payload_bytes[i:i+12])
                lines.append(f'    {chunk},')
            lines.append(f'}};')
            lines.append(f'#define {to_upper(self.name)}_TV_{to_upper(tv_name)}_LEN {len(payload_bytes)}')

            # Expected values as comments
            expected = tv.get('expected', {})
            if expected:
                lines.append(f'/* Expected:')
                for k, v in expected.items():
                    lines.append(f' *   {k} = {v}')
                lines.append(f' */')
            lines.append('')

        return '\n'.join(lines)


def fix_yaml_booleans(obj):
    if isinstance(obj, dict):
        return {('on' if k is True else 'off' if k is False else k): fix_yaml_booleans(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fix_yaml_booleans(i) for i in obj]
    return obj


def load_schema(path: Path) -> Dict[str, Any]:
    with open(path) as f:
        schema = yaml.safe_load(f)
    return fix_yaml_booleans(schema)


def main():
    parser = argparse.ArgumentParser(description='Generate firmware C codec from Payload Schema YAML')
    parser.add_argument('input', help='Schema file or directory')
    parser.add_argument('-o', '--output', help='Output file or directory')
    args = parser.parse_args()

    input_path = Path(args.input)
    if input_path.is_file():
        files = [input_path]
    else:
        files = sorted(list(input_path.glob('*.yaml')) + list(input_path.glob('*.yml')))
        files = [f for f in files if f.stem.lower() != 'readme']

    output_path = Path(args.output) if args.output else None

    for schema_path in files:
        try:
            schema = load_schema(schema_path)
            if not isinstance(schema, dict):
                continue
            if 'fields' not in schema and 'ports' not in schema:
                continue

            gen = FirmwareGenerator(schema, schema_path.name)
            code = gen.generate()

            if output_path:
                if output_path.suffix in ('.h', '.c'):
                    out = output_path
                else:
                    output_path.mkdir(parents=True, exist_ok=True)
                    out = output_path / (to_c(schema.get('name', schema_path.stem)) + '_codec.h')
                out.write_text(code)
                print(f'Generated: {out}')
            else:
                print(code)
        except Exception as e:
            print(f'Error: {schema_path.name}: {e}', file=sys.stderr)
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()
