#!/usr/bin/env python3
"""Run corpus vectors through the C interpreter, and say what it cannot reach.

The C interpreter is the only implementation with **no corpus coverage at all**. Its four
dedicated test files - `src/test_interpreter.c` and three others - are in no build target,
and `src/selftest_schema.c`, the one that is, exercises hand-built schemas rather than
corpus vectors. So nothing measured it, and every gap in it was invisible: it has no field
type for `tlv`, `flagged` or `repeat`, which between them appear in about 60% of the corpus,
and that was recorded in AGENTS.md as an assertion rather than a measurement.

There is no YAML reader in C (`bindings/c/schema_ffi.c`'s `schema_create_yaml()` is a
declared stub) and the header parses no binary format - a schema is built through the
struct API, `field_u8("x")` and `schema_add_field()`. So this generates C that builds each
expressible schema through that API, compiles it once, runs it, and compares what it printed
against the vectors' `expected` blocks with the same `values_match` the other runners use.

    python tools/c-corpus-harness.py             # summary and the skip reasons
    python tools/c-corpus-harness.py --failures  # every vector that differs
    python tools/c-corpus-harness.py --json out.json

**A skipped schema is not a passing one.** The summary separates the two deliberately: the
count that matters is how many vectors the C interpreter *decodes correctly*, against how
many the corpus holds. Everything else is a reason it could not be asked.

The expressible subset is what the struct API can build today: plain integers and floats,
`bool`, a `u8[a:b]` bit range, `skip`, `ascii`/`hex`/`bytes`, `enum`, a `lookup` table, and
the `mult`/`div`/`add` modifiers. A construct outside it is named in the skip reasons rather
than approximated - an approximated schema would report a pass for something the interpreter
cannot actually do.
"""

import argparse
import collections
import json
import pathlib
import re
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import yaml  # noqa: E402

from validate_schema import is_encode_vector, values_match  # noqa: E402

CORPUS = REPO_ROOT / "schemas" / "devices"

#: Plain wire types the struct API has a constructor for, mapped to it.
INT_CTORS = {
    "u8": "field_u8({name})", "s8": "field_s8({name})",
    "u16": "field_u16({name}, {endian})", "s16": "field_s16({name}, {endian})",
    "u24": "field_u24({name}, {endian})", "s24": "field_s24({name}, {endian})",
    "u32": "field_u32({name}, {endian})", "s32": "field_s32({name}, {endian})",
    "u64": "field_u64({name}, {endian})", "s64": "field_s64({name}, {endian})",
    "f16": "field_f16({name}, {endian})", "f32": "field_f32({name}, {endian})",
    "f64": "field_f64({name}, {endian})",
}
SIZED_CTORS = {"ascii": "field_ascii", "hex": "field_hex", "bytes": "field_bytes_type"}

#: Wire types the interpreter has a FIELD_TYPE_ for but this harness has no constructor
#: mapping for. Reported apart from a type C genuinely lacks, because "no constructor for
#: type 'u32le16'" reads as a C gap when C decodes it perfectly well - the same
#: misattribution the tlv/flagged reasons are careful to avoid.
C_HAS_NO_CONSTRUCTOR_HERE = {"u32le16", "s32le16", "udec", "sdec", "base64"}

#: Keys that put a schema or a field outside the subset, and the reason to report.
UNREACHABLE_KEYS = {
    # `tlv` was here until CR-2026-033 and `flagged` until CR-2026-034; the interpreter has
    # a field type for both now. What remains unreachable about either is size, not shape -
    # see tlv_source() and flagged_source().
    "match": "no inline match support in this harness",
    "byte_group": "no byte_group support in this harness",
    "object": "no nested object support in this harness",
    "$ref": "no $ref splicing in this harness",
    "name_from": "no name template in C (fixed-size name buffers)",
    # These say "the interpreter has none" rather than "not built by the struct API",
    # which was the wording here and read as a limit on this harness. It is not: the
    # header has no transform machinery at all - `transform`, `polynomial`, `sqrt`,
    # `pow`, `log`, `floor`, `clamp` and `compute` are each zero occurrences in
    # include/schema_interpreter.h, as are `version_string`, `sign_magnitude`, `bcd`
    # and `gray`. The struct API cannot build them because there is nothing to build.
    #
    # The distinction is the whole point of this harness and the old wording inverted
    # it: SESSION-NOTES.md concluded "the next work on C is widening the harness, not
    # the interpreter" and named `transform` (26 schemas) and `bitfield_string` (24) as
    # harness limits whose status was "unknown". They are the interpreter's largest
    # gaps and their status was knowable by grep. CR-2026-035 corrected both.
    "transform": "the interpreter has no transform pipeline",
    "polynomial": "the interpreter has no transform pipeline (polynomial)",
    "compute": "the interpreter has no computed fields",
    "ref": "the interpreter has no computed fields (ref)",
    "guard": "the interpreter has no guard",
    "parts": "the interpreter has no bitfield_string/version_string",
    "encoding": "the interpreter has no sign_magnitude/bcd/gray encodings",
    "var": "variables are only read by match, which this harness skips",
    # AGENTS.md records this one: a `default` on a lookup or enum is Python, Go, Java and
    # C# only, because the struct has no slot for it. Building such a field without its
    # default and then reporting the difference as a decode failure would misattribute a
    # known representational gap as a C bug - which is what this harness did on its first
    # run, alongside three bugs of its own.
    "default": "no enum/lookup default in the struct API (a known C gap)",
}

BIT_RANGE = re.compile(r"^u(\d+)\[(\d+):(\d+)\]$")


def c_string(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def field_source(field, schema_endian):
    """The C that builds one field, or (None, reason) where the API cannot."""
    for key, reason in UNREACHABLE_KEYS.items():
        if key in field:
            return None, reason

    name = field.get("name")
    ftype = str(field.get("type", ""))

    if ftype == "skip":
        length = field.get("length", 1)
        if not isinstance(length, int):
            return None, "skip length is not a literal"
        # Added like any other field. Returning here without the schema_add_field below
        # built the padding and dropped it, so every field after a `skip` read from the
        # padding's offset - reported as two C defects that were this harness's own.
        return [f"    f = field_skip({length});",
                "    schema_add_field(s, &f);"], None
    if not name:
        return None, "field has no name"

    endian = "ENDIAN_LITTLE" if schema_endian == "little" else "ENDIAN_BIG"
    lines = []

    match = BIT_RANGE.match(ftype)
    if match:
        start, end = int(match.group(2)), int(match.group(3))
        consume = "true" if int(field.get("consume", 0) or 0) >= 1 else "false"
        lines.append(f"    f = field_bits({c_string(name)}, {start}, "
                     f"{end - start + 1}, {consume});")
    elif ftype == "bool":
        bit = int(field.get("bit", 0) or 0)
        consume = "true" if int(field.get("consume", 1) or 0) >= 1 else "false"
        lines.append(f"    f = field_bool({c_string(name)}, {bit}, {consume});")
    elif ftype in INT_CTORS:
        lines.append(f"    f = " + INT_CTORS[ftype].format(
            name=c_string(name), endian=endian) + ";")
    elif ftype in SIZED_CTORS:
        length = field.get("length")
        if not isinstance(length, int):
            return None, f"{ftype} length is not a literal"
        lines.append(f"    f = {SIZED_CTORS[ftype]}({c_string(name)}, {length});")
    elif ftype == "enum":
        values = field.get("values")
        if not isinstance(values, dict):
            return None, "enum values are not a mapping"
        lines.append(f"    f = field_enum({c_string(name)}, 1);")
        for key, label in values.items():
            try:
                number = int(str(key), 0)
            except ValueError:
                return None, "enum key is not an integer"
            lines.append(f"    field_add_lookup(&f, {number}, {c_string(label)});")
    else:
        if ftype in C_HAS_NO_CONSTRUCTOR_HERE:
            return None, (f"this harness has no constructor for {ftype!r} "
                          "(the interpreter has the type)")
        return None, f"no {ftype!r} field type"

    lookup = field.get("lookup")
    if isinstance(lookup, dict):
        for key, label in lookup.items():
            try:
                number = int(str(key), 0)
            except ValueError:
                return None, "lookup key is not an integer"
            lines.append(f"    field_add_lookup(&f, {number}, {c_string(label)});")
    elif isinstance(lookup, list):
        for index, label in enumerate(lookup):
            lines.append(f"    field_add_lookup(&f, {index}, {c_string(label)});")
        lines.append("    f.lookup_is_sequence = true;")
    elif lookup is not None:
        return None, "lookup is neither a mapping nor a sequence"

    for key, setter in (("mult", "field_set_mult"), ("div", "field_set_div"),
                        ("add", "field_set_add")):
        if key in field:
            if not isinstance(field[key], (int, float)):
                return None, f"{key} is not a literal number"
            lines.append(f"    {setter}(&f, {float(field[key])!r});")

    lines.append("    schema_add_field(s, &f);")
    return lines, None



def tlv_source(field, schema_endian, next_index):
    """The C that builds a `tlv` field and its case bodies (CR-2026-033).

    Returns (case-body lines, tlv lines, fields consumed, reason). The bodies are emitted
    as ordinary schema fields first, because a case keys a range of the flat field array -
    `case_def_t.field_start`/`field_count` - which is also why the limits below are real
    rather than defensive: every case body counts against SCHEMA_MAX_FIELDS.
    """
    tlv = field["tlv"]
    if tlv.get("merge") is False:
        return None, None, 0, "merge:false needs a channel list decode_result_t cannot hold"
    if tlv.get("unknown") == "raw":
        return None, None, 0, "unknown:raw needs somewhere to put the captured bytes"

    tag_fields = tlv.get("tag_fields") or []
    if tag_fields:
        if any(str(f.get("type")) != "u8" for f in tag_fields):
            return None, None, 0, "a tag component wider than u8 does not pack into a case key"
        parts = len(tag_fields)
        if parts > 4:
            return None, None, 0, "more than four tag components do not pack into an int"
        ctor = f"field_tlv_composite({parts}, {int(tlv.get('length_size', 0) or 0)})"
    else:
        parts = 0
        ctor = (f"field_tlv({int(tlv.get('tag_size', 1) or 1)}, "
                f"{int(tlv.get('length_size', 0) or 0)})")

    cases = tlv.get("cases") or {}
    if len(cases) > 16:
        # One message rather than one per count, so the report groups them: this is a
        # single boundary - the fixed SCHEMA_MAX_CASES - not fifteen separate reasons.
        return None, None, 0, "more cases than SCHEMA_MAX_CASES (16) allows"

    members = []          # [(packed, [per-member build lines])]
    for key, body in cases.items():
        if not isinstance(body, list):
            return None, None, 0, "a case body is not a field list"
        packed = tlv_case_key(key, parts)
        if packed is None:
            return None, None, 0, f"case key {key!r} is not a tag this interpreter can key on"
        built = []
        for member in body:
            if not isinstance(member, dict):
                return None, None, 0, "a case member is not a mapping"
            lines, reason = field_source(member, schema_endian)
            if lines is None:
                return None, None, 0, reason
            built.append(lines)
        members.append((packed, built))

    return members, ctor, sum(len(b) for _, b in members), None


def tlv_case_key(key, parts):
    """A case key packed the way the interpreter packs a tag, or None."""
    if isinstance(key, bool):
        return None
    if isinstance(key, int):
        return key if parts <= 1 else None
    text = str(key).strip()
    if text.startswith("[") and text.endswith("]"):
        pieces = [p.strip().strip("\"'") for p in text[1:-1].split(",")]
        if len(pieces) != parts:
            return None
        packed = 0
        for piece in pieces:
            if piece == "*" or piece.startswith("!"):
                return None      # a range of tags; encoding cannot choose one (PS-270)
            try:
                value = int(piece, 0)
            except ValueError:
                return None
            if not 0 <= value <= 0xFF:
                return None
            packed = (packed << 8) | value
        return packed
    try:
        return int(text, 0) if parts <= 1 else None
    except ValueError:
        return None



def flagged_source(field, schema_endian):
    """The C that builds a `flagged` construct (CR-2026-034).

    Returns (groups, flags_field_name, reason) where groups is [(bit, [member lines])].
    The bodies are placed above `field_count` by the caller, as a tlv case body is.

    The field holding the mask must carry `var_name`, because this interpreter records a
    value in its variable table only where a field declares one while `flagged` refers to a
    field by name. The caller patches that in - see schema_source().
    """
    flagged = field["flagged"]
    if not isinstance(flagged, dict):
        return None, None, "flagged is not a mapping"
    flags_field = flagged.get("field")
    if not isinstance(flags_field, str) or not flags_field:
        return None, None, "flagged names no field to read the mask from"

    groups = flagged.get("groups")
    if not isinstance(groups, list) or not groups:
        return None, None, "flagged has no groups"
    if len(groups) > 16:
        return None, None, "more groups than SCHEMA_MAX_CASES (16) allows"

    built = []
    for group in groups:
        if not isinstance(group, dict):
            return None, None, "a flagged group is not a mapping"
        bit = group.get("bit")
        if not isinstance(bit, int) or isinstance(bit, bool) or not 0 <= bit <= 63:
            return None, None, f"group bit {bit!r} is not a bit position"
        members = group.get("fields")
        if not isinstance(members, list) or not members:
            return None, None, "a flagged group has no fields"
        lines_per_member = []
        for member in members:
            if not isinstance(member, dict):
                return None, None, "a group member is not a mapping"
            lines, reason = field_source(member, schema_endian)
            if lines is None:
                return None, None, reason
            lines_per_member.append(lines)
        built.append((bit, lines_per_member))

    return built, flags_field, None


def schema_source(index, schema):
    """The C that builds one schema, or (None, reason)."""
    if schema.get("ports"):
        return None, "port-based schema (this interpreter has no port selection)"
    fields = schema.get("fields")
    if not isinstance(fields, list) or not fields:
        return None, "no top-level fields"

    body = [f"static void build_{index}(schema_t* s) {{",
            "    field_def_t f;",
            "    memset(s, 0, sizeof(*s));",
            f"    s->endian = {'ENDIAN_LITTLE' if schema.get('endian') == 'little' else 'ENDIAN_BIG'};"]
    # The flat field index a tlv case body will start at. Top-level fields are added in
    # order, so it advances by one per plain field and by the body size per tlv.
    # Pass one: the fields the top-level loop walks, in order. A tlv or a flagged is one
    # of them; their bodies are deferred to pass two.
    top_level, deferred, mask_fields = [], [], set()
    for field in fields:
        if not isinstance(field, dict):
            return None, "field is not a mapping"
        if "tlv" in field and not field.get("type"):
            members, ctor, _, reason = tlv_source(field, schema.get("endian"), 0)
            if members is None:
                return None, reason
            top_level.append(("tlv", ctor, len(deferred)))
            deferred.append(members)
            continue
        if "flagged" in field and not field.get("type"):
            groups, flags_field, reason = flagged_source(field, schema.get("endian"))
            if groups is None:
                return None, reason
            mask_fields.add(flags_field)
            top_level.append(("flagged", flags_field, len(deferred)))
            deferred.append([(bit, members) for bit, members in groups])
            continue
        lines, reason = field_source(field, schema.get("endian"))
        if lines is None:
            return None, reason
        top_level.append(("plain", lines, None))

    # The mask field has to declare `var_name` or the construct cannot find it. Patched in
    # here rather than required of the schema: a YAML `flagged` names a field, not a
    # variable, so no corpus schema carries `var:` for it.
    if mask_fields:
        declared = {f.get("name") for f in fields if isinstance(f, dict)}
        missing = mask_fields - declared
        if missing:
            return None, f"flagged reads a mask from {sorted(missing)!r}, not a field here"

    counted = len(top_level)
    # Pass two: case bodies go above `field_count`, so the top-level loop never walks
    # them - they are reached only through case_def_t.field_start.
    placements, slot = [], counted
    case_calls = collections.defaultdict(list)
    for group_index, members in enumerate(deferred):
        for packed, built in members:
            start = slot
            for member_lines in built:
                placements.append((slot, member_lines))
                slot += 1
            case_calls[group_index].append((packed, start, slot - start))
    if slot > 32:
        return None, "more fields than SCHEMA_MAX_FIELDS (32) allows"

    for kind, payload, group_index in top_level:
        if kind == "plain":
            body.extend(payload)
            # A mask field needs `var_name` set before it is added, so the construct that
            # reads it can find it in the variable table.
            name = next((line.split('"')[1] for line in payload
                         if 'f = field_' in line and '"' in line), None)
            if name in mask_fields:
                # Insert the var_name assignment before the add.
                add = body.pop()
                body.append(f'    strncpy(f.var_name, "{name}", SCHEMA_MAX_NAME_LEN - 1);')
                body.append(add)
            continue
        if kind == "flagged":
            body.append(f"    f = field_flagged({c_string(payload)});")
            for bit, start, count in case_calls[group_index]:
                body.append(f"    field_add_flagged_group(&f, {bit}, {start}, {count});")
            body.append("    schema_add_field(s, &f);")
            continue
        body.append(f"    f = {payload};")
        for packed, start, count in case_calls[group_index]:
            body.append(f"    field_add_tlv_case(&f, {packed}, {start}, {count});")
        body.append("    schema_add_field(s, &f);")

    for index, member_lines in placements:
        # The member's own build lines end in schema_add_field; placed instead.
        body.extend(line for line in member_lines
                    if "schema_add_field" not in line)
        body.append(f"    schema_place_field(s, {index}, &f);")

    body.append("}")
    return body, None


PROGRAM_HEAD = '''/* Generated by tools/c-corpus-harness.py - do not edit, and do not commit.
 *
 * Builds each expressible corpus schema through the struct API, decodes its vectors, and
 * prints one line per field so the caller can compare against the vector's `expected`.
 */
#include "schema_interpreter.h"
#include <stdio.h>
#include <stdbool.h>
#include <string.h>

static void emit(const char* schema, const char* vector,
                 const uint8_t* payload, int len,
                 void (*build)(schema_t*)) {
    schema_t s;
    decode_result_t r;
    build(&s);
    int rc = schema_decode(&s, payload, len, &r);
    printf("V\\t%s\\t%s\\t%d\\t%d\\n", schema, vector, rc, r.field_count);
    if (rc != SCHEMA_OK) {
        printf("E\\t%s\\n", r.error_msg);
        return;
    }
    for (int i = 0; i < r.field_count; i++) {
        const decoded_field_t* d = &r.fields[i];
        if (!d->valid) continue;
        /* `field_value_t` is a union, so which member holds the value has to be decided
         * before reading one: an integer field carrying a `lookup` reports the label in
         * `.str`, and reading `.i64` would return the string's bytes as a number. The
         * decoded field carries no flag for that, so the schema field of the same name is
         * consulted - `lookup_count` is the only reliable signal available. */
        bool labelled = false;
        int width = 0;
        /* The whole array, not `field_count`: a tlv case body is placed above field_count
         * so the top-level loop does not walk it, which also makes it invisible to a
         * search bounded by field_count. Bounding it there printed every lookup label in
         * a case body through the integer branch - 31 vectors reported as C type
         * mismatches that were this harness not finding the field. Unused slots are
         * zeroed, so their empty name never matches. */
        for (int j = 0; j < SCHEMA_MAX_FIELDS; j++) {
            if (strcmp(s.fields[j].name, d->name) == 0) {
                labelled = s.fields[j].lookup_count > 0;
                width = s.fields[j].size;
                break;
            }
        }
        switch (d->type) {
            case FIELD_TYPE_F16: case FIELD_TYPE_F32: case FIELD_TYPE_F64:
                printf("F\\t%s\\t%.10g\\n", d->name, d->value.f64); break;
            case FIELD_TYPE_BOOL:
                printf("B\\t%s\\t%s\\n", d->name, d->value.b ? "true" : "false"); break;
            case FIELD_TYPE_ASCII: case FIELD_TYPE_HEX: case FIELD_TYPE_BASE64:
            case FIELD_TYPE_ENUM:
                printf("S\\t%s\\t%s\\n", d->name, d->value.str); break;
            case FIELD_TYPE_BYTES:
                /* Stored raw in `.bytes`, not as text; the corpus expects lowercase hex.
                 * Printing it through the integer branch read the first eight bytes as a
                 * number and reported two C defects that were this harness's own. */
                printf("S\\t%s\\t", d->name);
                for (int b = 0; b < width; b++) printf("%02x", d->value.bytes[b]);
                printf("\\n");
                break;
            default:
                if (labelled)
                    printf("S\\t%s\\t%s\\n", d->name, d->value.str);
                else
                    printf("I\\t%s\\t%lld\\n", d->name, (long long)d->value.i64);
                break;
        }
    }
}

'''


def build_program(entries):
    parts = [PROGRAM_HEAD]
    for index, (_, _, source, _) in entries:
        parts.append("\n".join(source) + "\n\n")
    parts.append("int main(void) {\n")
    for index, (schema_name, vectors, _, _) in entries:
        for vector in vectors:
            payload = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            body = ", ".join(f"0x{b:02x}" for b in payload) or "0"
            parts.append(f"    {{ static const uint8_t p[] = {{{body}}};\n"
                         f"      emit({c_string(schema_name)}, "
                         f"{c_string(vector.get('name', '?'))}, p, {len(payload)}, "
                         f"build_{index}); }}\n")
    parts.append("    return 0;\n}\n")
    return "".join(parts)


def parse_output(text):
    """The generated program's output, as {(schema, vector): (rc, {name: value})}."""
    results = {}
    current = None
    for line in text.splitlines():
        parts = line.split("\t")
        if parts[0] == "V" and len(parts) >= 5:
            current = (parts[1], parts[2])
            results[current] = (int(parts[3]), {})
        elif current is None or len(parts) < 3:
            continue
        elif parts[0] == "I":
            results[current][1][parts[1]] = int(parts[2])
        elif parts[0] == "F":
            results[current][1][parts[1]] = float(parts[2])
        elif parts[0] == "B":
            results[current][1][parts[1]] = parts[2] == "true"
        elif parts[0] == "S":
            results[current][1][parts[1]] = parts[2]
    return results


def run():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--failures", action="store_true",
                        help="list every vector whose decode differs")
    parser.add_argument("--json", metavar="PATH", help="write the full result as JSON")
    parser.add_argument("--cc", default="cc", help="compiler to use (default cc)")
    args = parser.parse_args()

    entries, skips, vector_total = [], collections.Counter(), 0
    skipped_vectors = 0
    for path in sorted(CORPUS.rglob("*.yaml")):
        try:
            schema = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        if not isinstance(schema, dict) or not schema.get("test_vectors"):
            continue
        vectors = [v for v in schema["test_vectors"]
                   if v.get("payload") and not is_encode_vector(v)]
        if not vectors:
            continue
        vector_total += len(vectors)
        source, reason = schema_source(len(entries), schema)
        if source is None:
            skips[reason] += 1
            skipped_vectors += len(vectors)
            continue
        entries.append((len(entries), (path.name, vectors, source, schema)))

    entries = [(i, payload) for i, payload in enumerate(p for _, p in entries)]
    program = build_program(entries)

    with tempfile.TemporaryDirectory() as tmp:
        csrc = pathlib.Path(tmp) / "harness.c"
        cbin = pathlib.Path(tmp) / "harness"
        csrc.write_text(program)
        compile_cmd = [args.cc, "-std=c11", "-O1", "-I", str(REPO_ROOT / "include"),
                       str(csrc), "-o", str(cbin), "-lm"]
        built = subprocess.run(compile_cmd, capture_output=True, text=True)
        if built.returncode != 0:
            print("=" * 74)
            print("C HARNESS - did not compile")
            print("=" * 74)
            print(built.stderr[:4000])
            return 1
        ran = subprocess.run([str(cbin)], capture_output=True, text=True)
        if ran.returncode != 0:
            print(f"harness exited {ran.returncode}: {ran.stderr[:2000]}")
            return 1

    produced = parse_output(ran.stdout)
    passed, failures = 0, []
    for _, (schema_name, vectors, _, _) in entries:
        for vector in vectors:
            key = (schema_name, vector.get("name", "?"))
            rc, fields = produced.get(key, (None, {}))
            if rc is None:
                failures.append((key, "the harness printed no result"))
                continue
            if rc != 0:
                failures.append((key, f"decode returned {rc}"))
                continue
            problem = None
            for name, want in (vector.get("expected") or {}).items():
                if name not in fields:
                    problem = f"{name} missing"
                    break
                ok, detail = values_match(want, fields[name])
                if not ok:
                    problem = f"{name}: {detail}"
                    break
            if problem:
                failures.append((key, problem))
            else:
                passed += 1

    attempted = sum(len(v) for _, (_, v, _, _) in entries)
    print("=" * 74)
    print("C INTERPRETER, AGAINST THE CORPUS")
    print("=" * 74)
    print(f"  {passed:>5} of {attempted} attempted vectors decode as the corpus expects")
    print(f"  {len(failures):>5} differ")
    print(f"  {skipped_vectors:>5} not attempted, in {sum(skips.values())} schemas the "
          "struct API cannot build")
    print(f"  {vector_total:>5} vectors in the corpus altogether")
    print()
    print("  Why a schema was not attempted:")
    for reason, count in skips.most_common():
        print(f"  {count:>5} {reason}")

    if failures and args.failures:
        print()
        for (schema_name, vector), detail in failures:
            print(f"  {schema_name}::{vector}: {detail}")

    if args.json:
        pathlib.Path(args.json).write_text(json.dumps({
            "passed": passed, "attempted": attempted,
            "skipped_vectors": skipped_vectors, "corpus_vectors": vector_total,
            "skips": dict(skips),
            "failures": [{"schema": s, "vector": v, "detail": d}
                         for (s, v), d in failures],
        }, indent=2) + "\n")
        print(f"\n  Wrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(run())
