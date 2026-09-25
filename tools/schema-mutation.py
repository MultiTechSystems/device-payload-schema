#!/usr/bin/env python3
"""How well do a schema's test vectors constrain it? Mutate the schema and see.

`tools/score_schema.py` runs a schema against its own vectors and reports whether they
pass. That measures self-consistency, and AGENTS.md records what it cannot see: a schema
that mis-decoded every real payload held a 100% Platinum score, and `dl-isf` carried a
wrong sensor id through cross-validation because the vendor's payload never exercised
that field - "agreement covers only the payloads you have". This tool asks the question
underneath both: **if the schema were wrong here, would any vector notice?**

Method. For every corpus schema carrying decode vectors, one small semantic change is
applied at a time - a mutant - and every vector is decoded against it with the Python
reference interpreter, compared exactly as `tests/test_corpus_conformance.py` compares
(`values_match` at `CONFORMANCE_TOLERANCE`, plus `warnings_match`). A vector that
passed on the original and fails on the mutant KILLS it. A mutant every vector still
passes SURVIVED, and marks a part of the schema no vector checks:

    python tools/schema-mutation.py                          # whole corpus, summary
    python tools/schema-mutation.py schemas/devices/dragino -v   # survivors listed
    python tools/schema-mutation.py --json out.json --operators ENDIAN,SIGN
    python tools/schema-mutation.py --schema-limit 10 --workers 4

Operators, each applied at every applicable field, recursing through `match` cases (both
spellings), `tlv` cases and tag fields, `flagged` groups, `byte_group`, `object` and
`repeat` members, `ports` entries and `definitions`:

  ENDIAN      flip a multi-byte integer's or float's effective byte order, by adding or
              flipping a field-level `endian:`.
  SIGN        uN <-> sN (and u32le16 <-> s32le16, an enum's `base`).
  WIDTH       uN -> u(N+8) and u(N-8) within 8..32, which also moves every later field.
  SCALE       an existing `mult` or `div` (bare or in a transform stage) times 10.
  ADD         an existing `add` plus 1, or `add: 1` inserted on a numeric field that has
              none - the most direct probe of "is this value asserted anywhere".
  DROP-MOD    remove one bare modifier or one transform stage.
  BITRANGE    shift a bit range one bit (`u8[a:b]` -> `u8[a+1:b+1]`, or down at the top),
              move a bool's `bit`, and toggle a bit range's `consume`.
  LOOKUP      swap the first two entries of a `lookup` or enum `values`; relabel an
              entry (at most 8 per table, evenly spaced).
  CASE        swap the bodies of two adjacent match/tlv cases; delete a case.
  FLAGGED     move a `flagged` group to the next bit.
  DROP-FIELD  remove a field that is not the last in its list. It shifts everything
              after it, so it is killed far more easily than the rest and is scored
              separately rather than allowed to flatter the main score.

Outcomes, and what is and is not counted:

  killed               some originally-passing vector fails on the mutant.
  survived             every vector passes. Split by why: `unasserted` - the mutant's
                       decode differs, but in a key no vector's `expected` lists; and
                       `indistinguishable` - the decoded output is identical on every
                       payload the schema carries, so no payload tells the two schemas
                       apart (an ENDIAN survivor of this kind means every multi-byte
                       value in the vectors is a byte palindrome, or zero).
  possibly-equivalent  a SIGN survivor whose output is identical on every vector: every
                       decoded value lies in the non-negative half range, where the two
                       types agree. Whether the device can ever send the upper half is
                       not knowable from the schema, so these are reported and left out
                       of the score.
  unreached            the mutated field or case is never decoded by any vector, and the
                       mutant's output is identical everywhere. Not counted in the score -
                       nothing tested it either way - but listed, because code no vector
                       exercises is itself the finding. Reach is recorded by decoding the
                       original with an instrumented interpreter; a mutation of an
                       unreached site that nonetheless changes some decode (a case swap
                       that routes a reached tag into it) is judged like any other.
  equivalent           statically unable to change any decode, so never generated: byte
                       order on a one-byte type or on a bit range (which always reads its
                       base big-endian), SIGN on a field with an `encoding` (read
                       unsigned regardless), `consume` inside a `byte_group` (forced to
                       0), a swap of two identical cases or labels. Counted, never scored.
  timeout              a mutant whose decode did not finish within --timeout seconds is
                       counted as killed: the change was observable.

Mutation score = killed / (killed + survived), excluding DROP-FIELD, which has its own.

Limits. Only the Python interpreter is used; a mutant it kills may survive on a
generated codec and vice versa. The operators are first-order and syntactic: `compute`
operands, `polynomial` coefficients, `guard` thresholds, `name_from` templates, repeat
counts and TLV tag/length sizes are not mutated. A killed mutant says a vector would
notice *that* change, not that the schema is right - a vector copied from our own decoder
kills mutants as readily as a vendor's does, which is why the report compares schemas by
provenance. And a high score shares the scorer's blind spot: it is measured against the
payloads the schema happens to carry.
"""

import argparse
import copy
import json
import os
import re
import signal
import statistics
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import yaml  # noqa: E402

from schema_interpreter import SchemaInterpreter  # noqa: E402
from score_schema import CONFORMANCE_TOLERANCE  # noqa: E402
from validate_schema import (  # noqa: E402
    is_encode_vector,
    values_match,
    warnings_match,
)

CORPUS = REPO_ROOT / "schemas" / "devices"

OPERATORS = (
    "ENDIAN",
    "SIGN",
    "WIDTH",
    "SCALE",
    "ADD",
    "DROP-MOD",
    "BITRANGE",
    "LOOKUP",
    "CASE",
    "FLAGGED",
    "DROP-FIELD",
)

#: Scored separately: removing a field shifts every later one, so it is easy to kill.
SEPARATE_OPERATORS = ("DROP-FIELD",)

KILLED = "killed"
SURVIVED = "survived"
POSSIBLY_EQUIVALENT = "possibly-equivalent"
UNREACHED = "unreached"
TIMEOUT = "timeout"

#: Key the reach pass stores on each field dict of its own marked copy of the schema.
MARK = "__mutation_site__"

#: Relabel at most this many entries of one lookup table.
MAX_RELABELS = 8

_INT_RE = re.compile(r"^(u|s|i|uint|int)(8|16|24|32|64)$")
_BITS_RE = re.compile(r"^u(\d+)\[(\d+):(\d+)\]$")
_FLOAT_SIZES = {"f16": 2, "f32": 4, "float": 4, "f64": 8, "double": 8}
_WORD_ORDERED = {"u32le16": "s32le16", "s32le16": "u32le16"}


# --------------------------------------------------------------------------- types


def int_type(type_name: Any) -> Optional[Tuple[int, bool]]:
    """(bits, signed) for a plain integer type spelling, else None."""
    if not isinstance(type_name, str):
        return None
    match = _INT_RE.match(type_name)
    if not match:
        return None
    return int(match.group(2)), match.group(1) in ("s", "i", "int")


def bit_range(type_name: Any) -> Optional[Tuple[int, int, int]]:
    """(base_bits, start, end) for a bracket bit range, else None."""
    if not isinstance(type_name, str):
        return None
    match = _BITS_RE.match(type_name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


#: Keys that make a field dict a construct rather than a value read from the payload.
_CONSTRUCT_KEYS = ("match", "tlv", "flagged", "byte_group", "object", "$ref")


def is_construct(field: Dict[str, Any]) -> bool:
    """Whether a field dict carries a construct instead of a type of its own."""
    if field.get("type") in ("object", "repeat", "match", "skip"):
        return True
    return any(key in field for key in _CONSTRUCT_KEYS) and not field.get("type")


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --------------------------------------------------------------------------- sites


class Site(object):
    """One field dict in the schema, with where it lives and how to describe it."""

    def __init__(self, path, label, list_path, index, list_len, in_byte_group, endian):
        self.path = path  # tuple of keys from the schema root to the field dict
        self.label = label
        self.list_path = list_path
        self.index = index
        self.list_len = list_len
        self.in_byte_group = in_byte_group
        self.endian = endian


def _field_label(field: Dict[str, Any]) -> str:
    if field.get("name"):
        return str(field["name"])
    for kind in ("tlv", "match", "flagged", "byte_group", "object", "$ref"):
        if kind in field:
            if kind == "object":
                return "object:%s" % field["object"]
            return kind
    return "<%s>" % field.get("type", "u8")


def _join(prefix: str, part: str) -> str:
    return part if not prefix else "%s/%s" % (prefix, part)


def _key_text(key: Any) -> str:
    return (
        "0x%02X" % key
        if isinstance(key, int) and not isinstance(key, bool)
        else str(key)
    )


def top_level_lists(schema: Dict[str, Any]) -> Iterable[Tuple[tuple, str]]:
    """Every field list a decode can start from: fields, ports entries, definitions."""
    if isinstance(schema.get("fields"), list):
        yield ("fields",), ""
    ports = schema.get("ports")
    if isinstance(ports, dict):
        for key, entry in ports.items():
            if isinstance(entry, dict) and isinstance(entry.get("fields"), list):
                yield ("ports", key, "fields"), "port %s" % key
    definitions = schema.get("definitions")
    if isinstance(definitions, dict):
        for key, entry in definitions.items():
            if isinstance(entry, dict) and isinstance(entry.get("fields"), list):
                yield ("definitions", key, "fields"), "def %s" % key


def collect_sites(schema: Dict[str, Any]) -> List[Site]:
    endian = schema.get("endian", "big")
    sites = []  # type: List[Site]
    for path, label in top_level_lists(schema):
        _walk(resolve(schema, path), path, label, False, endian, sites)
    return sites


def _walk(fields, path, label, in_byte_group, endian, sites):
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            continue
        fpath = path + (index,)
        flabel = _join(label, _field_label(field))
        sites.append(
            Site(fpath, flabel, path, index, len(fields), in_byte_group, endian)
        )
        if isinstance(field.get("fields"), list):
            _walk(field["fields"], fpath + ("fields",), flabel, False, endian, sites)
        cases = field.get("cases")
        if field.get("type") == "match" and isinstance(cases, list):
            for ci, case in enumerate(cases):
                if isinstance(case, dict) and isinstance(case.get("fields"), list):
                    _walk(
                        case["fields"],
                        fpath + ("cases", ci, "fields"),
                        "%s[case %s]" % (flabel, case.get("case")),
                        False,
                        endian,
                        sites,
                    )
            default = field.get("default")
            if isinstance(default, dict) and isinstance(default.get("fields"), list):
                _walk(
                    default["fields"],
                    fpath + ("default", "fields"),
                    flabel + "[default]",
                    False,
                    endian,
                    sites,
                )
        for kind in ("match", "tlv"):
            block = field.get(kind)
            if not isinstance(block, dict):
                continue
            for key, body in (block.get("cases") or {}).items():
                if isinstance(body, list):
                    _walk(
                        body,
                        fpath + (kind, "cases", key),
                        "%s[%s]" % (flabel, _key_text(key)),
                        False,
                        endian,
                        sites,
                    )
            if kind == "match" and isinstance(block.get("default"), list):
                _walk(
                    block["default"],
                    fpath + (kind, "default"),
                    flabel + "[default]",
                    False,
                    endian,
                    sites,
                )
            if kind == "tlv" and isinstance(block.get("tag_fields"), list):
                _walk(
                    block["tag_fields"],
                    fpath + (kind, "tag_fields"),
                    flabel + "[tag]",
                    False,
                    endian,
                    sites,
                )
        flagged = field.get("flagged")
        if isinstance(flagged, dict):
            for gi, group in enumerate(flagged.get("groups") or []):
                if isinstance(group, dict) and isinstance(group.get("fields"), list):
                    _walk(
                        group["fields"],
                        fpath + ("flagged", "groups", gi, "fields"),
                        "%s[bit %s]" % (flabel, group.get("bit")),
                        False,
                        endian,
                        sites,
                    )
        group = field.get("byte_group")
        if isinstance(group, list):
            _walk(group, fpath + ("byte_group",), flabel, True, endian, sites)
        elif isinstance(group, dict) and isinstance(group.get("fields"), list):
            _walk(
                group["fields"],
                fpath + ("byte_group", "fields"),
                flabel,
                True,
                endian,
                sites,
            )


def resolve(root: Any, path: tuple) -> Any:
    node = root
    for key in path:
        node = node[key]
    return node


# --------------------------------------------------------------------------- mutants


class Mutant(object):
    """One change: `apply` edits a deep copy of the schema in place."""

    def __init__(self, operator, label, detail, reach, apply):
        self.operator = operator
        self.label = label
        self.detail = detail
        self.reach = reach  # list of path prefixes; reached if any is decoded
        self.apply = apply  # type: Callable[[Dict[str, Any]], None]


def _set_key(path, key, value):
    def apply(root):
        resolve(root, path)[key] = value

    return apply


def _del_key(path, key):
    def apply(root):
        del resolve(root, path)[key]

    return apply


def _transform_owner(field: Dict[str, Any]) -> bool:
    return isinstance(field.get("transform"), list)


def field_mutants(site: Site, field: Dict[str, Any], equivalent: Dict[str, int]):
    """The field-level mutants of one site, counting static equivalents as it goes."""
    out = []  # type: List[Mutant]
    path = site.path
    reach = [path]
    if is_construct(field):
        ftype = None
    else:
        ftype = field.get("type", "u8")
    is_enum = ftype == "enum"
    base = field.get("base", "u8") if is_enum else ftype
    base_key = "base" if is_enum else "type"
    base_int = int_type(base)
    base_bits = bit_range(base)

    def add(operator, detail, apply):
        out.append(Mutant(operator, site.label, detail, reach, apply))

    # ENDIAN
    size = None
    if base_int:
        size = base_int[0] // 8
    elif isinstance(base, str) and base in _FLOAT_SIZES:
        size = _FLOAT_SIZES[base]
    if size is not None and size > 1:
        current = field.get("endian", site.endian)
        flipped = "little" if current == "big" else "big"
        add(
            "ENDIAN", "%s -> %s" % (current, flipped), _set_key(path, "endian", flipped)
        )
    elif size == 1 or base_bits or ftype == "bool":
        equivalent["ENDIAN"] = equivalent.get("ENDIAN", 0) + 1

    # SIGN
    if base_int or base in _WORD_ORDERED:
        if field.get("encoding"):
            equivalent["SIGN"] = equivalent.get("SIGN", 0) + 1
        else:
            if base in _WORD_ORDERED:
                other = _WORD_ORDERED[base]
            else:
                other = ("u" if base_int[1] else "s") + str(base_int[0])
            add("SIGN", "%s -> %s" % (base, other), _set_key(path, base_key, other))

    # WIDTH
    if base_int and base_int[0] <= 32:
        bits, signed = base_int
        prefix = "s" if signed else "u"
        for new_bits in (bits + 8, bits - 8):
            if 8 <= new_bits <= 32:
                other = "%s%d" % (prefix, new_bits)
                add(
                    "WIDTH", "%s -> %s" % (base, other), _set_key(path, base_key, other)
                )

    # SCALE / ADD / DROP-MOD. A `formula` takes precedence over every modifier, so
    # none of them would be read: those mutants are equivalent by construction.
    has_formula = bool(field.get("formula"))
    for key in ("mult", "div", "add"):
        if key not in field or not _numeric(field.get(key)):
            continue
        if has_formula:
            equivalent["DROP-MOD"] = equivalent.get("DROP-MOD", 0) + 1
            continue
        value = field[key]
        if key == "add":
            add(
                "ADD",
                "add %s -> %s" % (value, value + 1),
                _set_key(path, key, value + 1),
            )
        else:
            add(
                "SCALE",
                "%s %s -> %s" % (key, value, value * 10),
                _set_key(path, key, value * 10),
            )
        add("DROP-MOD", "remove %s: %s" % (key, value), _del_key(path, key))
    if _transform_owner(field) and not has_formula:
        for si, stage in enumerate(field["transform"]):
            if not isinstance(stage, dict):
                continue
            spath = path + ("transform", si)
            for key in ("mult", "div", "add"):
                if not _numeric(stage.get(key)):
                    continue
                value = stage[key]
                if key == "add":
                    add(
                        "ADD",
                        "transform[%d] add %s -> %s" % (si, value, value + 1),
                        _set_key(spath, key, value + 1),
                    )
                else:
                    add(
                        "SCALE",
                        "transform[%d] %s %s -> %s" % (si, key, value, value * 10),
                        _set_key(spath, key, value * 10),
                    )

            def drop_stage(root, _si=si):
                del resolve(root, path)["transform"][_si]

            add("DROP-MOD", "remove transform[%d] %s" % (si, stage), drop_stage)
    numeric_leaf = (
        base_int is not None
        or base in _FLOAT_SIZES
        or base in _WORD_ORDERED
        or base_bits is not None
        or ftype in ("udec", "sdec")
        or (ftype == "number" and "ref" in field)
    )
    if (
        numeric_leaf
        and not is_enum
        and "add" not in field
        and "lookup" not in field
        and not has_formula
    ):
        add("ADD", "insert add: 1", _set_key(path, "add", 1))

    # BITRANGE
    if base_bits and not is_enum:
        width, start, end = base_bits
        if end + 1 < width:
            shifted = "u%d[%d:%d]" % (width, start + 1, end + 1)
        elif start > 0:
            shifted = "u%d[%d:%d]" % (width, start - 1, end - 1)
        else:
            shifted = None
        if shifted:
            add(
                "BITRANGE",
                "%s -> %s" % (ftype, shifted),
                _set_key(path, "type", shifted),
            )
    if ftype == "bool":
        bit = field.get("bit", 0)
        if isinstance(bit, int):
            moved = bit + 1 if bit < 7 else bit - 1
            add("BITRANGE", "bit %d -> %d" % (bit, moved), _set_key(path, "bit", moved))
    if (base_bits and not is_enum) or ftype == "bool":
        if site.in_byte_group:
            equivalent["BITRANGE"] = equivalent.get("BITRANGE", 0) + 1
        else:
            consume = field.get("consume")
            if isinstance(consume, int) and consume > 0:
                add(
                    "BITRANGE",
                    "consume %d -> 0" % consume,
                    _set_key(path, "consume", 0),
                )
            else:
                add(
                    "BITRANGE",
                    "consume %s -> 1" % consume,
                    _set_key(path, "consume", 1),
                )

    # LOOKUP
    for key in ("lookup",) + (("values",) if is_enum else ()):
        table = field.get(key)
        if isinstance(table, list):
            keys = list(range(len(table)))
        elif isinstance(table, dict):
            keys = list(table.keys())
        else:
            continue
        out.extend(_lookup_mutants(site, path + (key,), key, table, keys, equivalent))

    # DROP-FIELD
    if site.index < site.list_len - 1:

        def drop(root, _lp=site.list_path, _i=site.index):
            del resolve(root, _lp)[_i]

        add("DROP-FIELD", "remove %s" % _field_label(field), drop)

    return out


def _lookup_mutants(site, tpath, key, table, keys, equivalent):
    out = []
    if len(keys) >= 2:
        k0, k1 = keys[0], keys[1]
        if table[k0] == table[k1]:
            equivalent["LOOKUP"] = equivalent.get("LOOKUP", 0) + 1
        else:

            def swap(root, _k0=k0, _k1=k1):
                t = resolve(root, tpath)
                t[_k0], t[_k1] = t[_k1], t[_k0]

            out.append(
                Mutant(
                    "LOOKUP",
                    site.label,
                    "%s swap %s <-> %s" % (key, k0, k1),
                    [site.path],
                    swap,
                )
            )
    if len(keys) <= MAX_RELABELS:
        chosen = keys
    else:
        step = (len(keys) - 1) / float(MAX_RELABELS - 1)
        chosen = [keys[int(round(i * step))] for i in range(MAX_RELABELS)]
    for k in chosen:
        label = table[k]
        if isinstance(label, str):
            new = label + "_mutant"
        elif _numeric(label):
            new = label + 1
        else:
            continue
        out.append(
            Mutant(
                "LOOKUP",
                site.label,
                "%s[%s] %r -> %r" % (key, k, label, new),
                [site.path],
                _set_key(tpath, k, new),
            )
        )
    return out


def construct_mutants(site: Site, field: Dict[str, Any], equivalent: Dict[str, int]):
    """CASE and FLAGGED mutants of a construct-carrying field."""
    out = []  # type: List[Mutant]
    path = site.path

    # Option B `match:` and `tlv:` - a dict of case bodies.
    for kind in ("match", "tlv"):
        block = field.get(kind)
        if not isinstance(block, dict) or not isinstance(block.get("cases"), dict):
            continue
        cpath = path + (kind, "cases")
        cases = block["cases"]
        keys = [k for k in cases if k != "default"]
        for a, b in zip(keys, keys[1:]):
            if cases[a] == cases[b]:
                equivalent["CASE"] = equivalent.get("CASE", 0) + 1
                continue

            def swap(root, _a=a, _b=b):
                c = resolve(root, cpath)
                c[_a], c[_b] = c[_b], c[_a]

            out.append(
                Mutant(
                    "CASE",
                    site.label,
                    "%s swap cases %s <-> %s" % (kind, _key_text(a), _key_text(b)),
                    [cpath + (a,), cpath + (b,)],
                    swap,
                )
            )
        for k in cases:

            def delete(root, _k=k):
                del resolve(root, cpath)[_k]

            out.append(
                Mutant(
                    "CASE",
                    site.label,
                    "%s delete case %s" % (kind, _key_text(k)),
                    [cpath + (k,)],
                    delete,
                )
            )

    # Legacy `type: match` with `cases: [{case: v, fields: [...]}]`.
    cases = field.get("cases")
    if field.get("type") == "match" and isinstance(cases, list):
        cpath = path + ("cases",)
        for i in range(len(cases) - 1):
            a, b = cases[i], cases[i + 1]
            if not (isinstance(a, dict) and isinstance(b, dict)):
                continue
            if a.get("fields") == b.get("fields"):
                equivalent["CASE"] = equivalent.get("CASE", 0) + 1
                continue

            def swap_legacy(root, _i=i):
                c = resolve(root, cpath)
                c[_i]["case"], c[_i + 1]["case"] = c[_i + 1].get("case"), c[_i].get(
                    "case"
                )

            out.append(
                Mutant(
                    "CASE",
                    site.label,
                    "swap cases %s <-> %s" % (a.get("case"), b.get("case")),
                    [cpath + (i,), cpath + (i + 1,)],
                    swap_legacy,
                )
            )
        for i, case in enumerate(cases):

            def delete_legacy(root, _i=i):
                del resolve(root, cpath)[_i]

            out.append(
                Mutant(
                    "CASE",
                    site.label,
                    "delete case %s"
                    % (case.get("case") if isinstance(case, dict) else i),
                    [cpath + (i,)],
                    delete_legacy,
                )
            )

    # FLAGGED
    flagged = field.get("flagged")
    if isinstance(flagged, dict):
        for gi, group in enumerate(flagged.get("groups") or []):
            if not isinstance(group, dict) or not isinstance(group.get("bit"), int):
                continue
            bit = group["bit"]
            gpath = path + ("flagged", "groups", gi)
            out.append(
                Mutant(
                    "FLAGGED",
                    site.label,
                    "group bit %d -> %d" % (bit, bit + 1),
                    [gpath],
                    _set_key(gpath, "bit", bit + 1),
                )
            )
    return out


#: Operators that change only a field's value, not how many bytes it reads.
_VALUE_OPERATORS = ("ENDIAN", "SIGN", "SCALE", "ADD", "DROP-MOD", "BITRANGE", "LOOKUP")


#: Operators acting on modifiers and lookups, which a TLV tag field never applies.
_MODIFIER_OPERATORS = ("SCALE", "ADD", "DROP-MOD", "LOOKUP")


def _unobservable_value(field: Dict[str, Any], text: str) -> bool:
    """An `_`-prefixed field whose name (and `var:`) appear nowhere else in the schema."""
    name = field.get("name")
    if not isinstance(name, str) or not name.startswith("_"):
        return False
    for token in (name, field.get("var")):
        if not isinstance(token, str) or not token:
            continue
        pattern = r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(token)
        allowed = 1
        if len(re.findall(pattern, text)) > allowed:
            return False
    return True


def generate_mutants(
    schema: Dict[str, Any], operators: Optional[Iterable[str]] = None
) -> Tuple[List[Mutant], Dict[str, int]]:
    """Every mutant of `schema`, and the count of static equivalents per operator."""
    wanted = set(operators) if operators else set(OPERATORS)
    equivalent = {}  # type: Dict[str, int]
    mutants = []  # type: List[Mutant]
    text = json.dumps(schema, default=str)
    for site in collect_sites(schema):
        field = resolve(schema, site.path)
        produced = field_mutants(site, field, equivalent)
        if _unobservable_value(field, text):
            # An internal field nothing refers to is read and discarded, so a change
            # to its value alone cannot reach the output. Its width still can -
            # except inside a byte_group, whose members never move the position.
            kept = []
            for m in produced:
                if site.in_byte_group or (
                    m.operator in _VALUE_OPERATORS
                    and not m.detail.startswith("consume")
                ):
                    equivalent[m.operator] = equivalent.get(m.operator, 0) + 1
                else:
                    kept.append(m)
            produced = kept
        if site.list_path and site.list_path[-1] == "tag_fields":
            # A TLV tag is matched on the raw read: modifiers and lookups on a tag
            # field are never applied, so changing them cannot change a decode.
            kept = []
            for m in produced:
                if m.operator in _MODIFIER_OPERATORS:
                    equivalent[m.operator] = equivalent.get(m.operator, 0) + 1
                else:
                    kept.append(m)
            produced = kept
        mutants.extend(produced)
        mutants.extend(construct_mutants(site, field, equivalent))
    mutants = [m for m in mutants if m.operator in wanted]
    equivalent = {k: v for k, v in equivalent.items() if k in wanted}
    return mutants, equivalent


# --------------------------------------------------------------------------- decoding


class _ReachInterpreter(SchemaInterpreter):
    """Records the MARK of every field dict the decode touches."""

    def __init__(self, schema, reached):
        SchemaInterpreter.__init__(self, schema)
        self._reached = reached

    def _note(self, field_def):
        if isinstance(field_def, dict) and MARK in field_def:
            self._reached.add(field_def[MARK])

    def _decode_field(self, field_def, buf, pos):
        self._note(field_def)
        return SchemaInterpreter._decode_field(self, field_def, buf, pos)

    def _decode_computed_field(self, field_def):
        self._note(field_def)
        return SchemaInterpreter._decode_computed_field(self, field_def)

    def _decode_bitfield_string(self, field_def, buf, pos):
        self._note(field_def)
        return SchemaInterpreter._decode_bitfield_string(self, field_def, buf, pos)

    def _decode_byte_group(self, field_def, buf, pos, result):
        self._note(field_def)
        return SchemaInterpreter._decode_byte_group(self, field_def, buf, pos, result)

    def _decode_match(self, field_def, buf, pos):
        self._note(field_def)
        return SchemaInterpreter._decode_match(self, field_def, buf, pos)

    def _decode_tlv(self, field_def, buf, pos, outer=None):
        self._note(field_def)
        return SchemaInterpreter._decode_tlv(self, field_def, buf, pos, outer)

    def _decode_nested_object_b(self, field_def, buf, pos):
        self._note(field_def)
        return SchemaInterpreter._decode_nested_object_b(self, field_def, buf, pos)


def decode_vectors(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        v
        for v in (schema.get("test_vectors") or [])
        if isinstance(v, dict) and not is_encode_vector(v) and "payload" in v
    ]


def _payload(vector):
    return bytes.fromhex(str(vector.get("payload", "")).replace(" ", ""))


def _port(vector):
    return vector.get("fPort") or vector.get("fport")


class Outcome(object):
    def __init__(self, passed, data, signature):
        self.passed = passed
        self.data = data
        self.signature = signature


def run_vector(interp: SchemaInterpreter, vector: Dict[str, Any]) -> Outcome:
    """Decode one vector and judge it as tests/test_corpus_conformance.py does."""
    try:
        result = interp.decode(_payload(vector), fPort=_port(vector))
    except Exception as exc:  # a mutant may break the interpreter outright
        return Outcome(False, None, ("raised", type(exc).__name__, str(exc)))
    signature = (
        result.success,
        json.dumps(result.data, sort_keys=True, default=repr),
        tuple(result.errors),
        tuple(result.warnings),
    )
    passed = result.success
    if passed:
        for key, want in (vector.get("expected") or {}).items():
            if key not in result.data:
                passed = False
                break
            ok, _detail = values_match(want, result.data[key], CONFORMANCE_TOLERANCE)
            if not ok:
                passed = False
                break
    if passed:
        ok, _detail = warnings_match(vector.get("expected_warnings"), result.warnings)
        passed = ok
    return Outcome(passed, result.data, signature)


def run_all(schema: Dict[str, Any], vectors: List[Dict[str, Any]]) -> List[Outcome]:
    try:
        interp = SchemaInterpreter(schema)
    except Exception as exc:
        return [Outcome(False, None, ("init", str(exc))) for _ in vectors]
    return [run_vector(interp, v) for v in vectors]


def reached_sites(schema: Dict[str, Any], vectors: List[Dict[str, Any]]) -> set:
    marked = copy.deepcopy(schema)
    for site in collect_sites(marked):
        resolve(marked, site.path)[MARK] = site.path
    reached = set()  # type: set
    interp = _ReachInterpreter(marked, reached)
    for vector in vectors:
        try:
            interp.decode(_payload(vector), fPort=_port(vector))
        except Exception:
            pass
    return reached


def _is_reached(prefixes, reached) -> bool:
    for prefix in prefixes:
        n = len(prefix)
        for path in reached:
            if path[:n] == prefix:
                return True
    return False


def _changed_keys(before, after) -> List[str]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    keys = []
    for key in sorted(set(before) | set(after)):
        if key not in before or key not in after:
            keys.append(key)
            continue
        a = json.dumps(before[key], sort_keys=True, default=repr)
        b = json.dumps(after[key], sort_keys=True, default=repr)
        if a != b:
            keys.append(key)
    return keys


class _Timeout(Exception):
    pass


def _alarm(_signum, _frame):
    raise _Timeout()


def _with_timeout(fn, seconds):
    """Run fn() under an interval timer where the platform and thread allow it."""
    usable = seconds and hasattr(signal, "setitimer") and _in_main_thread()
    if not usable:
        return fn()
    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _in_main_thread():
    import threading

    return threading.current_thread() is threading.main_thread()


def classify(
    mutant: Mutant,
    schema: Dict[str, Any],
    vectors: List[Dict[str, Any]],
    baseline: List[Outcome],
    reached: set,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Apply one mutant and say what the vectors made of it."""
    mutated = copy.deepcopy(schema)
    record = {
        "field": mutant.label,
        "operator": mutant.operator,
        "detail": mutant.detail,
    }
    try:
        mutant.apply(mutated)
        outcomes = _with_timeout(lambda: run_all(mutated, vectors), timeout)
    except _Timeout:
        record["status"] = TIMEOUT
        return record
    for base, out in zip(baseline, outcomes):
        if base.passed and not out.passed:
            record["status"] = KILLED
            return record
    identical = all(b.signature == o.signature for b, o in zip(baseline, outcomes))
    if identical and not _is_reached(mutant.reach, reached):
        record["status"] = UNREACHED
        return record
    if identical:
        if mutant.operator == "SIGN":
            record["status"] = POSSIBLY_EQUIVALENT
            return record
        record["status"] = SURVIVED
        record["kind"] = "indistinguishable"
        return record
    record["status"] = SURVIVED
    changed = []  # type: List[str]
    all_asserted = True
    for vector, b, o in zip(vectors, baseline, outcomes):
        keys = _changed_keys(b.data, o.data)
        if b.signature != o.signature and not keys:
            keys = ["<errors/warnings>"]
        expected = vector.get("expected") or {}
        if any(k not in expected for k in keys):
            all_asserted = False
        for key in keys:
            if key not in changed:
                changed.append(key)
    # Every key that moved is one some vector asserts, and the move stayed inside the
    # comparison tolerance: the vectors look at the value, just not closely enough.
    record["kind"] = "within-tolerance" if all_asserted else "unasserted"
    record["changed_keys"] = changed[:6]
    return record


# --------------------------------------------------------------------------- per schema


def provenance(vectors: List[Dict[str, Any]]) -> str:
    sources = [v.get("source") for v in vectors]
    if any(s not in (None, "generated") for s in sources):
        return "independent"
    if any(s == "generated" for s in sources):
        return "generated-only"
    return "unsourced"


def _score(killed, survived):
    total = killed + survived
    return round(killed / float(total), 4) if total else None


def mutate_schema(
    schema: Dict[str, Any],
    name: str = "<inline>",
    operators: Optional[Iterable[str]] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Run every mutant of one schema and summarise it."""
    warnings.simplefilter("ignore")
    vectors = decode_vectors(schema)
    report = {
        "schema": name,
        "vectors": len(vectors),
        "provenance": provenance(vectors),
    }  # type: Dict[str, Any]
    baseline = run_all(schema, vectors)
    failing = [
        str(v.get("name", i))
        for i, (v, o) in enumerate(zip(vectors, baseline))
        if not o.passed
    ]
    report["vectors_failing_original"] = failing
    mutants, equivalent = generate_mutants(schema, operators)
    reached = reached_sites(schema, vectors)
    by_operator = {}  # type: Dict[str, Dict[str, int]]
    survivors = []
    unreached = []
    for mutant in mutants:
        record = classify(mutant, schema, vectors, baseline, reached, timeout)
        status = record["status"]
        bucket = by_operator.setdefault(mutant.operator, {})
        bucket[status] = bucket.get(status, 0) + 1
        if status == SURVIVED:
            survivors.append(record)
        elif status == UNREACHED:
            unreached.append(record)
    for op, count in equivalent.items():
        by_operator.setdefault(op, {})["equivalent"] = count

    def total(status, include):
        return sum(
            counts.get(status, 0)
            for op, counts in by_operator.items()
            if (op in SEPARATE_OPERATORS) == include
        )

    killed = total(KILLED, False) + total(TIMEOUT, False)
    survived = total(SURVIVED, False)
    report.update(
        {
            "generated": len(mutants),
            "killed": killed,
            "survived": survived,
            "possibly_equivalent": total(POSSIBLY_EQUIVALENT, False),
            "unreached": total(UNREACHED, False),
            "equivalent": sum(equivalent.values()),
            "timeouts": total(TIMEOUT, False) + total(TIMEOUT, True),
            "score": _score(killed, survived),
            "drop_field": {
                "killed": total(KILLED, True) + total(TIMEOUT, True),
                "survived": total(SURVIVED, True),
                "unreached": total(UNREACHED, True),
                "score": _score(
                    total(KILLED, True) + total(TIMEOUT, True), total(SURVIVED, True)
                ),
            },
            "by_operator": by_operator,
            "survivors": survivors,
            "unreached_sites": unreached,
        }
    )
    return report


def mutate_file(args) -> Dict[str, Any]:
    path, operators, timeout = args
    try:
        rel = str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path)
    try:
        schema = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return mutate_schema(schema, rel, operators, timeout)
    except Exception as exc:  # pragma: no cover - report, do not abort the run
        return {"schema": rel, "error": "%s: %s" % (type(exc).__name__, exc)}


def schema_files(paths: List[str]) -> List[Path]:
    files = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.yaml")))
        elif path.exists():
            files.append(path)
    selected = []
    for path in files:
        try:
            schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if isinstance(schema, dict) and decode_vectors(schema):
            selected.append(path)
    return selected


# --------------------------------------------------------------------------- report


def aggregate(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in reports if "error" not in r]
    scored = [r for r in ok if r.get("score") is not None]
    totals = {}
    for key in (
        "generated",
        "killed",
        "survived",
        "possibly_equivalent",
        "unreached",
        "equivalent",
        "timeouts",
    ):
        totals[key] = sum(r.get(key, 0) for r in ok)
    totals["drop_field_killed"] = sum(r["drop_field"]["killed"] for r in ok)
    totals["drop_field_survived"] = sum(r["drop_field"]["survived"] for r in ok)
    totals["drop_field_unreached"] = sum(r["drop_field"]["unreached"] for r in ok)
    totals["score"] = _score(totals["killed"], totals["survived"])

    buckets = [0] * 11
    for r in scored:
        buckets[min(10, int(r["score"] * 10))] += 1
    distribution = {}
    for i in range(10):
        distribution["%d-%d%%" % (i * 10, i * 10 + 9)] = buckets[i]
    distribution["100%"] = buckets[10]

    by_operator = {}  # type: Dict[str, Dict[str, int]]
    for r in ok:
        for op, counts in r["by_operator"].items():
            dest = by_operator.setdefault(op, {})
            for status, n in counts.items():
                dest[status] = dest.get(status, 0) + n
    for op, counts in by_operator.items():
        k = counts.get(KILLED, 0) + counts.get(TIMEOUT, 0)
        s = counts.get(SURVIVED, 0)
        counts["survival_rate"] = round(s / float(k + s), 4) if k + s else None

    worst = sorted(
        (r for r in scored if r["killed"] + r["survived"] >= 5),
        key=lambda r: (r["score"], -r["survived"]),
    )[:20]

    groups = {}
    for r in scored:
        groups.setdefault(r["provenance"], []).append(r)
    provenance_cmp = {}
    for label, rs in sorted(groups.items()):
        k = sum(r["killed"] for r in rs)
        s = sum(r["survived"] for r in rs)
        provenance_cmp[label] = {
            "schemas": len(rs),
            "mean_score": round(statistics.mean(r["score"] for r in rs), 4),
            "median_score": round(statistics.median(r["score"] for r in rs), 4),
            "pooled_score": _score(k, s),
            "names": [r["schema"] for r in rs] if label != "independent" else None,
        }
    return {
        "schemas": len(ok),
        "errors": [r for r in reports if "error" in r],
        "totals": totals,
        "distribution": distribution,
        "by_operator": by_operator,
        "worst": [
            {
                "schema": r["schema"],
                "score": r["score"],
                "killed": r["killed"],
                "survived": r["survived"],
                "vectors": r["vectors"],
                "provenance": r["provenance"],
            }
            for r in worst
        ],
        "provenance": provenance_cmp,
    }


def print_summary(summary: Dict[str, Any], reports, verbose: bool, elapsed: float):
    t = summary["totals"]
    print("Schema mutation: %d schemas in %.1f s" % (summary["schemas"], elapsed))
    print(
        "  mutants %d: killed %d, survived %d, possibly-equivalent %d, unreached %d"
        " (static equivalents skipped: %d, timeouts: %d)"
        % (
            t["generated"],
            t["killed"],
            t["survived"],
            t["possibly_equivalent"],
            t["unreached"],
            t["equivalent"],
            t["timeouts"],
        )
    )
    print("  pooled mutation score %s" % _pct(t["score"]))
    print(
        "  DROP-FIELD (separate): killed %d, survived %d, unreached %d"
        % (t["drop_field_killed"], t["drop_field_survived"], t["drop_field_unreached"])
    )
    print("\nScore distribution (schemas):")
    for bucket, n in summary["distribution"].items():
        print("  %-8s %4d %s" % (bucket, n, "#" * n))
    print("\nBy operator:")
    print(
        "  %-11s %7s %8s %6s %9s %7s %8s"
        % ("operator", "killed", "survived", "p-eq", "unreached", "equiv", "surv%")
    )
    for op in OPERATORS:
        c = summary["by_operator"].get(op)
        if not c:
            continue
        print(
            "  %-11s %7d %8d %6d %9d %7d %8s"
            % (
                op,
                c.get(KILLED, 0) + c.get(TIMEOUT, 0),
                c.get(SURVIVED, 0),
                c.get(POSSIBLY_EQUIVALENT, 0),
                c.get(UNREACHED, 0),
                c.get("equivalent", 0),
                _pct(c.get("survival_rate")),
            )
        )
    print("\nWorst schemas (at least 5 scored mutants):")
    for r in summary["worst"]:
        print(
            "  %6s  killed %4d survived %4d  vectors %3d  %-14s %s"
            % (
                _pct(r["score"]),
                r["killed"],
                r["survived"],
                r["vectors"],
                r["provenance"],
                r["schema"],
            )
        )
    print("\nBy vector provenance:")
    for label, p in summary["provenance"].items():
        print(
            "  %-15s %4d schemas  mean %s  median %s  pooled %s"
            % (
                label,
                p["schemas"],
                _pct(p["mean_score"]),
                _pct(p["median_score"]),
                _pct(p["pooled_score"]),
            )
        )
        if p.get("names"):
            print("      " + ", ".join(Path(n).stem for n in p["names"]))
    for err in summary["errors"]:
        print("ERROR %s: %s" % (err["schema"], err["error"]))
    if verbose:
        for r in reports:
            if "error" in r or not r.get("survivors"):
                continue
            print("\n%s  score %s" % (r["schema"], _pct(r["score"])))
            for s in r["survivors"]:
                extra = ""
                if s.get("changed_keys"):
                    extra = "  changed: " + ", ".join(s["changed_keys"])
                print(
                    "  SURVIVED %-10s %-40s %s [%s]%s"
                    % (s["operator"], s["field"], s["detail"], s["kind"], extra)
                )


def _pct(value):
    return "-" if value is None else "%.1f%%" % (value * 100)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure how well each schema's test vectors constrain it."
    )
    parser.add_argument("paths", nargs="*", default=[str(CORPUS)])
    parser.add_argument("--json", metavar="OUT", help="write the full report here")
    parser.add_argument(
        "--operators",
        help="comma-separated subset of: %s" % ",".join(OPERATORS),
    )
    parser.add_argument("--schema-limit", type=int, metavar="N")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="seconds allowed per mutant before it counts as killed (default 5)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    operators = None
    if args.operators:
        operators = [o.strip().upper() for o in args.operators.split(",") if o.strip()]
        unknown = [o for o in operators if o not in OPERATORS]
        if unknown:
            parser.error("unknown operator(s): %s" % ", ".join(unknown))

    files = schema_files(args.paths)
    if args.schema_limit:
        files = files[: args.schema_limit]
    started = time.time()
    jobs = [(str(f), operators, args.timeout) for f in files]
    if args.workers > 1 and len(jobs) > 1:
        # Largest first, so one big TLV schema does not start last and set the runtime.
        jobs.sort(key=lambda j: -os.path.getsize(j[0]))
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            reports = list(pool.map(mutate_file, jobs))
        reports.sort(key=lambda r: r["schema"])
    else:
        reports = [mutate_file(j) for j in jobs]
    elapsed = time.time() - started
    summary = aggregate(reports)
    print_summary(summary, reports, args.verbose, elapsed)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "elapsed_seconds": round(elapsed, 1),
            "operators": operators or list(OPERATORS),
            "summary": summary,
            "schemas": reports,
        }
        out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print("\nwrote %s" % out)
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
