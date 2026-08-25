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

#: Keys that put a schema or a field outside the subset, and the reason to report.
UNREACHABLE_KEYS = {
    "tlv": "no tlv field type",
    "flagged": "no flagged field type",
    "match": "no inline match support in this harness",
    "byte_group": "no byte_group support in this harness",
    "object": "no nested object support in this harness",
    "$ref": "no $ref splicing in this harness",
    "name_from": "no name template in C (fixed-size name buffers)",
    "transform": "transform chain not built by the struct API",
    "polynomial": "computed field not built by the struct API",
    "compute": "computed field not built by the struct API",
    "ref": "computed field not built by the struct API",
    "guard": "guard not built by the struct API",
    "parts": "bitfield_string/version_string not built by the struct API",
    "encoding": "sign_magnitude/bcd/gray not built by the struct API",
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
        return None, f"no constructor for type {ftype!r}"

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
    for field in fields:
        if not isinstance(field, dict):
            return None, "field is not a mapping"
        lines, reason = field_source(field, schema.get("endian"))
        if lines is None:
            return None, reason
        body.extend(lines)
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
        for (int j = 0; j < s.field_count; j++) {
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
