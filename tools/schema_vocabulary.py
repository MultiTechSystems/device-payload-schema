"""The key vocabulary of the payload schema language, per context, backed by the code.

Every key a schema may carry is listed here under the context it sits in - a field, a
match block, a transform stage, a test vector - together with the readers that act on
it. `tests/test_corpus_invariants.py` checks every entry against the source of each
reader it names, so the table cannot go on vouching for a key nobody reads any more,
and checks that every key the corpus uses is in it.

`tools/validate_schema.py` uses the same table to reject a key that is not in the
vocabulary for its context. That is the point of sharing it: a key nothing reads is a
claim about the device that nothing honours - `optional: true` reads as "may be
absent", `conditional:` as "only when" - and a misspelt key (`tag_sze`, `cout`) is
silently a no-op. Both used to validate.

Three classes of key are accepted beyond VOCABULARY:

* DOCUMENTATION_KEYS, in every context. They are prose for a human, read by no
  decoder by design, so no reader backing is asked of them.
* UNREAD_KEYS: keys the corpus uses that nothing reads, each with its reason. It is
  a ratchet (the invariants test fails when a listed key stops occurring). The
  validator accepts one ONLY inside a definition that no field list reaches through
  `$ref` - where, by construction, it cannot affect a decode. Anywhere else it is an
  error like any other unknown key.
* Keys under a DATA mapping (a lookup table, an enum's labels, a case map, a port
  map): those keys are data, not vocabulary, and are never checked.

Python 3.8 compatible; no third-party imports.
"""

import difflib
import glob
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where each reader named in VOCABULARY lives. An entry naming a reader is checked to
#: occur as a quoted literal in that reader's source - weak evidence for a common word
#: such as `name`, real evidence for a rare one, and in both cases it breaks the entry
#: the day a reader stops mentioning the key at all.
READERS: Dict[str, List[str]] = {
    "py": ["tools/schema_interpreter.py"],
    "go": [
        f
        for f in sorted(glob.glob(str(REPO_ROOT / "go/schema/*.go")))
        if not f.endswith("_test.go")
    ],
    "java": sorted(
        glob.glob(str(REPO_ROOT / "bindings/java/src/main/**/*.java"), recursive=True)
    ),
    "cs": sorted(glob.glob(str(REPO_ROOT / "dotnet/PayloadSchema/*.cs"))),
    "ts013": ["tools/generate_ts013_codec.py"],
    "score": ["tools/score_schema.py"],
    "outschema": ["tools/generate_output_schema.py"],
    "validate": ["tools/validate_schema.py"],
    # The corpus runners, which are what read a test vector's keys.
    "runners": [
        "tests/test_corpus_conformance.py",
        "tools/vector-verdicts.py",
        "go/schema/corpus_conformance_test.go",
        "dotnet/PayloadSchema.Tests/CorpusConformanceTests.cs",
        "bindings/java/src/test/java/org/lora/schema/CorpusConformanceTest.java",
    ],
    "verdicts": ["tools/vector-verdicts.py"],
    # The binary schema encoders, which read the `semantic: {ipso: N}` form.
    "binary": ["tools/schema_binary.py", "tools/binary_schema.py"],
    # Documentation, for an annotation no code reads: the key must be described there.
    "doc:reference": ["docs/SCHEMA-LANGUAGE-REFERENCE.md"],
    "doc:metaschema": ["schemas/payload-schema.json"],
}

_DECODERS = ("py", "go", "java", "cs", "ts013")

#: context -> key -> (readers, why). A context is the kind of mapping the key sits in,
#: as `iter_keys` walks it. The corpus-used keys were the original table; the rest were
#: added so the validator does not reject a key some implementation reads, and each
#: names a reader the invariants test checks.
VOCABULARY: Dict[str, Dict[str, Tuple[Tuple[str, ...], str]]] = {
    "schema": {
        "name": (_DECODERS, "schema identifier"),
        "version": (("py", "go", "java", "cs"), "schema version (self.version)"),
        "description": (("py", "outschema"), "documentation; carried into outputs"),
        "endian": (_DECODERS, "default byte order"),
        "fields": (_DECODERS, "the top-level field list"),
        "ports": (_DECODERS, "per-fPort field lists (PS-004)"),
        "definitions": (_DECODERS, "targets of `$ref`"),
        "metadata": (("py", "validate"), "OPTIONAL enrichment block (CR-2026-036)"),
        "test_vectors": (("score", "validate", "runners"), "the conformance vectors"),
        "manufacturer": (("doc:metaschema",), "identity; documentation only"),
        "device": (("doc:metaschema",), "identity; documentation only"),
        "direction": (("py",), "uplink | downlink | bidirectional (PS-021)"),
    },
    "port": {
        "description": (("py", "outschema"), "documentation"),
        "fields": (_DECODERS, "the port's field list"),
        "direction": (("py",), "uplink | downlink | bidirectional (PS-021)"),
    },
    "definition": {
        "name": (("py",), "label of a definition; documentation"),
        "description": (("py", "outschema"), "documentation"),
        "fields": (_DECODERS, "spliced in place of the `$ref`"),
    },
    "field": {
        "name": (_DECODERS, "output key"),
        "type": (_DECODERS, "wire type or construct"),
        "description": (("py", "outschema"), "documentation"),
        "length": (_DECODERS, "byte length of a string/bytes/skip"),
        "endian": (_DECODERS, "per-field byte order override"),
        "consume": (_DECODERS, "advance after a bit range"),
        "add": (_DECODERS, "bare modifier (PS-101)"),
        "mult": (_DECODERS, "bare modifier (PS-101)"),
        "div": (_DECODERS, "bare modifier (PS-101)"),
        "lookup": (_DECODERS, "sequence or sparse mapping (PS-104/PS-268)"),
        "transform": (_DECODERS, "ordered modifier stages"),
        "polynomial": (_DECODERS, "fitted curve"),
        "compute": (_DECODERS, "two-operand computed field"),
        "guard": (_DECODERS, "conditional computed value"),
        "ref": (_DECODERS, "computed field's source"),
        "var": (_DECODERS, "binds a variable"),
        "name_from": (_DECODERS, "templated output key (CR-2026-004)"),
        "fields": (_DECODERS, "members of an object/repeat"),
        "count": (_DECODERS, "repeat count"),
        "byte_length": (_DECODERS, "repeat span (PS-088)"),
        "until": (_DECODERS, "repeat terminator"),
        "max": (_DECODERS, "repeat ceiling (CR-2026-021)"),
        "min": (("py", "go"), "repeat floor (CR-2026-021)"),
        "match": (_DECODERS, "inline discriminated union (Option B)"),
        "tlv": (_DECODERS, "tag dispatch"),
        "flagged": (_DECODERS, "bitmask-gated groups"),
        "byte_group": (_DECODERS, "fields sharing bytes"),
        "size": (_DECODERS, "byte count of the array `byte_group` spelling"),
        "$ref": (_DECODERS, "splices a definition"),
        "bit": (_DECODERS, "bit index of a `type: bool`"),
        "value": (_DECODERS, "literal/constant field value"),
        "base": (_DECODERS, "an enum's wire type"),
        "values": (_DECODERS, "an enum's label table"),
        "default": (("py", "go", "java", "cs"), "an enum's unmapped label (PS-068)"),
        "enum": (
            ("py",),
            "label table on a bit range. Read by the Python ENCODER only "
            "(`_bitfield_label_value`); no decoder applies it, so decode reports the "
            "number the encoder would accept a label for",
        ),
        "parts": (_DECODERS, "bitfield_string components"),
        "delimiter": (_DECODERS, "bitfield_string separator"),
        "prefix": (_DECODERS, "bitfield_string prefix"),
        "formula": (("py",), "legacy formula expression (Python only)"),
        "encode_formula": (("py",), "inverse of `formula` for encoding"),
        "on": (("py",), "legacy `type: match` discriminator (`on: $var`)"),
        "cases": (("py",), "legacy `type: match` case list"),
        "valid_range": (("py", "go", "cs", "outschema"), "quality flag (_quality)"),
        "resolution": (("py", "go", "cs"), "measurement resolution annotation"),
        "unece": (("py", "go", "cs"), "UN/ECE unit code annotation"),
        "unit": (("py", "score", "outschema"), "annotation"),
        "ipso": (("py", "score"), "annotation"),
        "senml": (("py", "score"), "annotation"),
        "semantic": (("py", "score"), "annotation"),
        "sensor": (("score",), "scorer hint overriding keyword detection"),
    },
    "match": {
        "field": (_DECODERS, "discriminator reference"),
        "length": (_DECODERS, "inline discriminator width"),
        "name": (_DECODERS, "reports the inline discriminator"),
        "var": (_DECODERS, "binds the inline discriminator"),
        "cases": (_DECODERS, "case bodies"),
        "default": (_DECODERS, "error | skip | fields"),
    },
    "tlv": {
        "cases": (_DECODERS, "case bodies"),
        "tag_size": (_DECODERS, "tag width"),
        "tag_fields": (_DECODERS, "multi-component tag"),
        "tag_key": (("py", "go", "java", "cs"), "which tag fields form the case key"),
        "length_size": (_DECODERS, "length prefix width"),
        "merge": (("py", "go", "java", "cs"), "merge case output into the parent"),
        "unknown": (_DECODERS, "skip | error | raw (PS-301)"),
    },
    "flagged": {
        "field": (_DECODERS, "the mask field"),
        "groups": (_DECODERS, "the bit-gated groups"),
    },
    "flagged_group": {
        "bit": (_DECODERS, "gating bit"),
        "fields": (_DECODERS, "group members"),
    },
    "byte_group": {
        "fields": (_DECODERS, "members"),
        "size": (_DECODERS, "shared byte count"),
    },
    "semantic": {
        "ipso": (
            ("binary",),
            "the `semantic: {ipso: N}` form the binary encoders read",
        ),
    },
    "transform": {
        "add": (_DECODERS, "stage"),
        "mult": (_DECODERS, "stage"),
        "div": (_DECODERS, "stage"),
        "abs": (_DECODERS, "stage"),
        "sqrt": (_DECODERS, "stage (input clamped at 0)"),
        "pow": (_DECODERS, "stage"),
        "log": (_DECODERS, "stage (input clamped at 1e-10)"),
        "log10": (_DECODERS, "stage (input clamped at 1e-10)"),
        "floor": (("py", "ts013"), "stage: lower clamp - see DIVERGENT_KEYS"),
        "ceiling": (("py", "ts013"), "stage: upper clamp - see DIVERGENT_KEYS"),
        "clamp": (("py", "ts013"), "stage: [lo, hi] clamp - see DIVERGENT_KEYS"),
        "op": (_DECODERS, "named stage, e.g. {op: round}"),
        "decimals": (_DECODERS, "round's precision"),
    },
    "compute": {
        "op": (_DECODERS, "add sub mul div mod idiv"),
        "a": (_DECODERS, "first operand"),
        "b": (_DECODERS, "second operand"),
    },
    "guard": {
        "when": (_DECODERS, "conditions"),
        "else": (_DECODERS, "value when a condition fails"),
    },
    "guard_when": {
        "field": (_DECODERS, "operand"),
        "gt": (_DECODERS, "comparison"),
        "gte": (_DECODERS, "comparison"),
        "lt": (_DECODERS, "comparison"),
        "lte": (_DECODERS, "comparison"),
        "eq": (_DECODERS, "comparison"),
        "ne": (_DECODERS, "comparison"),
    },
    "ipso": {
        "object": (("py", "score"), "IPSO object id, checked by the scorer"),
        "instance": (
            ("doc:reference",),
            "documented in the language reference; no tool reads it",
        ),
        "resource": (
            ("doc:reference",),
            "documented in the language reference; no tool reads it",
        ),
    },
    "senml": {
        "name": (("score",), "SenML record name"),
        "unit": (("score", "outschema"), "SenML unit, checked by the scorer"),
    },
    "metadata": {
        "include": (("py", "validate"), "copy input metadata into the output"),
        "timestamps": (("py", "validate"), "timestamp enrichment"),
    },
    "metadata.include": {
        "name": (("py",), "output key"),
        "source": (("py",), "$-reference into the TS013 input"),
    },
    "metadata.timestamps": {
        "name": (("py",), "output key"),
        "mode": (("py", "validate"), "e.g. elapsed_to_absolute"),
        "elapsed_field": (("py", "validate"), "field holding seconds ago"),
        "time_base": (("py",), "e.g. rx_time"),
    },
    "vector": {
        "name": (("runners",), "vector identifier"),
        "description": (("validate",), "documentation"),
        "payload": (("runners",), "bytes to decode"),
        "expected": (("runners",), "values to compare"),
        "expected_warnings": (("runners",), "PS-305 warning assertions"),
        "expected_payload": (("runners",), "encode vector's bytes (PS-047)"),
        "input": (("runners",), "encode vector's values (PS-047)"),
        "input_metadata": (("verdicts",), "TS013 input for metadata enrichment"),
        "fPort": (("runners",), "port"),
        "fport": (("runners",), "port, other spelling"),
        "port": (
            ("verdicts",),
            "third port spelling - read by vector-verdicts.py only; the four language "
            "runners ignore it (harmless in laq4, which is not port-based)",
        ),
        "source": (("score", "validate"), "provenance (PS-263/PS-264)"),
        "expected_quality": (
            ("validate",),
            "asserts `_quality` flags; checked by validate_schema.py only, so it belongs "
            "in examples, not the shared corpus (Java produces no `_quality`)",
        ),
        "direction": (
            ("doc:metaschema",),
            "described by payload-schema.json ('inferred from the keys when absent') "
            "but read by no runner: no vector passes it to decode()/encode(), so it "
            "documents the vector and exercises nothing (dl-5tm's encode vectors)",
        ),
    },
}

#: Prose for a human. Accepted in every context and deliberately not reader-backed: a
#: decoder that acted on a description would be the defect.
DOCUMENTATION_KEYS = frozenset(
    {"description", "comment", "comments", "note", "notes", "title", "summary"}
)

#: Keys the corpus uses that NOTHING reads - no decoder, encoder, generator, validator,
#: scorer or runner. A ratchet: a new one fails the invariants test, and a listed one
#: that stops occurring fails until it is removed here.
#:
#: Every entry lives in `_library-composed/`, in the catalogue definitions that
#: `tools/compose_library_vectors.py` copies along with the one it composes, and which
#: no field list references. `is_known` accepts them there and nowhere else, so the
#: validator still rejects each of them in a field that is actually decoded.
#:
#: Carrying only the reached definitions removes every entry and changes no decode
#: (measured: all 1524 decode outputs byte-identical, vector-verdicts unchanged). It
#: was not done because it moves ts007_multi_package__package_version_req from the
#: `repeat` to the `plain fixed` encode shape bucket in all four round-trip harnesses
#: (they classify by construct names in the file, and the only `repeat` was in an
#: unreferenced definition), which needs the per-shape floors moved in the language
#: runners. That is a separate, deliberate change.
UNREAD_KEYS: Dict[Tuple[str, str], str] = {
    ("field", "conditional"): (
        "a C-like expression (`status_and_id & 0x3C == 0`) in ts005/ts006 definitions. "
        "Nothing evaluates it, so the field is always decoded - the one entry here that "
        "would change a decode if its definition were ever referenced, which is why it "
        "is accepted only in an unreferenced definition and is an error anywhere else"
    ),
    ("field", "optional"): (
        "lorawan_frames / udp_packet_forwarder: implies the field may be absent; no "
        "implementation honours it, so a short frame errors instead"
    ),
    ("field", "max_length"): "lorawan_frames `fopts`: a 15-byte cap nobody enforces",
    ("field", "example"): "udp_packet_forwarder: documentation-style sample value",
    ("field", "items"): (
        "udp_packet_forwarder `type: array` + `items: {$ref}` - neither exists in the "
        "language (`repeat` does). validate_schema.py walks `items` only as a list"
    ),
    (
        "field",
        "version",
    ): "lorawan_frames: LoRaWAN version a field applies to ('1.1.0+')",
    ("definition", "version"): "lorawan_frames / mac_commands: LoRaWAN version tag",
    ("definition", "cid"): "command id of a library command definition; no reader",
    ("definition", "direction"): (
        "uplink/downlink of a library command definition. PS-021 reads `direction` on "
        "the schema and on a port entry, never on a definition"
    ),
}

#: Keys some implementation reads but not all, so a schema using one decodes differently
#: per language. Accepted, but the validator WARNS with the reason.
DIVERGENT_KEYS: Dict[Tuple[str, str], str] = {
    ("field", "format"): (
        "`format` on a bytes field is read by Go, Java and C# (hex / hex:upper / base64 "
        "/ array) and ignored by the Python reference interpreter and the TS013 "
        "generator, so the decoded representation differs per language. Prefer the "
        "type spelling (`hex`, `hex:upper`, `base64`), which every implementation reads"
    ),
    ("transform", "floor"): (
        "the `floor` stage is applied by the Python interpreter and the TS013 generator "
        "only; Go, Java and C# have no such stage and pass the value through unclamped"
    ),
    ("transform", "ceiling"): (
        "the `ceiling` stage is applied by the Python interpreter and the TS013 "
        "generator only; Go, Java and C# pass the value through unclamped"
    ),
    ("transform", "clamp"): (
        "the `clamp` stage is applied by the Python interpreter and the TS013 generator "
        "only; Go, Java and C# pass the value through unclamped"
    ),
    ("field", "separator"): (
        "`separator` accompanies bytes `format`; the Python reference interpreter "
        "ignores both"
    ),
}

VOCABULARY["field"]["format"] = (("go", "java", "cs"), "bytes rendering; see DIVERGENT")
VOCABULARY["field"]["separator"] = (("go", "cs"), "bytes rendering; see DIVERGENT")

#: Known mistakes, with what to write instead. Checked before the difflib suggestion.
HINTS: Dict[Tuple[str, str], str] = {
    ("transform", "sub"): "there is no `sub` stage; write `add:` with a negative value",
    ("transform", "subtract"): "write `add:` with a negative value",
    ("transform", "multiply"): "the stage is `mult:`",
    ("transform", "divide"): "the stage is `div:`",
    ("transform", "round"): "rounding is `{op: round, decimals: N}`",
    ("transform", "min"): "a lower clamp is `floor:`",
    ("transform", "max"): "an upper clamp is `ceiling:`",
    ("field", "sub"): "there is no `sub` modifier; write `add:` with a negative value",
    ("field", "scale"): "the modifier is `mult:` (or `div:`)",
    ("field", "offset"): "the modifier is `add:`",
    ("field", "multiplier"): "the modifier is `mult:`",
    ("field", "divisor"): "the modifier is `div:`",
    ("field", "count_field"): "a repeat reads its count from a field as `count: $x`",
    ("field", "length_field"): "a length from a field is written `length: $x`",
    ("field", "match_value"): (
        "a match is keyed by value in its `cases:` map (`cases: {1: [...]}`); there "
        "is no per-field match value"
    ),
    ("field", "conditional"): (
        "nothing evaluates `conditional:`, so the field always decodes. Express the "
        "condition with `match` (on a value) or `flagged` (on a bit)"
    ),
    ("field", "optional"): (
        "no implementation honours `optional:`; a short payload errors. Put the field "
        "under a `match` or `flagged` branch, or a `repeat` with `until: end`"
    ),
    ("field", "max_length"): "no implementation enforces `max_length:`",
    ("field", "items"): "`type: array` + `items:` do not exist; use `type: repeat`",
    ("field", "enum_values"): "an enum's labels are `values:`",
    ("field", "labels"): "a label table is `lookup:` (or `values:` on an enum)",
    ("field", "bits"): "a bit range is spelled in the type: `type: u8[5:7]`",
    ("field", "bit_offset"): (
        "the bit range is spelled in the type (`u8[5:7]`); `bit_offset:` was never read"
    ),
    ("field", "signed"): "signedness is in the type: `s16`, not `u16` + `signed:`",
    ("vector", "hex"): "the vector's bytes are `payload:`",
    ("schema", "header"): "declare header fields in `definitions` and use `$ref`",
    ("schema", "byte_order"): "the key is `endian:`",
    ("field", "byte_order"): "the key is `endian:`",
}

#: Mappings whose keys are data, not vocabulary: their values are walked, their keys
#: are not recorded.
DATA_MAPPINGS = frozenset({"lookup", "enum", "values"})
#: Lists or scalars-in-lists that carry no keys of their own.
OPAQUE = frozenset({"parts", "valid_range", "polynomial"})


def _p(path: str, key: Any) -> str:
    key = str(key)
    return "%s.%s" % (path, key) if path else key


def iter_keys(schema: Any) -> Iterator[Tuple[str, str, str]]:
    """Yield (context, key, path) for every vocabulary key in a schema.

    Walks the grammar rather than every mapping - so a case key, a lookup key or a port
    number is never mistaken for a schema key - and records an unrecognised nested
    mapping under a context named after its parent (`field.items`), instead of skipping
    it, so an invented block's keys are still reported.
    """
    if not isinstance(schema, dict):
        return

    def rec(ctx: str, mapping: dict, path: str) -> Iterator[Tuple[str, str, str]]:
        for key in mapping:
            yield ctx, str(key), _p(path, key)

    def field_list(items: Any, path: str) -> Iterator[Tuple[str, str, str]]:
        if isinstance(items, list):
            for i, item in enumerate(items):
                if isinstance(item, dict):
                    yield from field(item, "%s[%d]" % (path, i))

    def case_map(cases: Any, path: str) -> Iterator[Tuple[str, str, str]]:
        if isinstance(cases, dict):
            for ck, body in cases.items():
                cpath = "%s[%s]" % (path, ck)
                if isinstance(body, list):
                    yield from field_list(body, cpath)
                elif isinstance(body, dict):
                    yield from field(body, cpath)
        elif isinstance(cases, list):
            # The legacy `type: match` spelling: a list of {case, fields} entries.
            for i, body in enumerate(cases):
                if isinstance(body, dict):
                    cpath = "%s[%d]" % (path, i)
                    yield from rec("legacy_case", body, cpath)
                    yield from field_list(body.get("fields"), _p(cpath, "fields"))

    def generic(ctx: str, node: Any, path: str) -> Iterator[Tuple[str, str, str]]:
        if isinstance(node, dict):
            yield from rec(ctx, node, path)
            for key, value in node.items():
                yield from generic("%s.%s" % (ctx, key), value, _p(path, key))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                yield from generic(ctx, item, "%s[%d]" % (path, i))

    def field(fd: dict, path: str, ctx: str = "field") -> Iterator:
        yield from rec(ctx, fd, path)
        for key, value in fd.items():
            kpath = _p(path, key)
            if key == "fields":
                yield from field_list(value, kpath)
            elif key == "cases":
                yield from case_map(value, kpath)
            elif key == "default" and isinstance(value, list):
                yield from field_list(value, kpath)
            elif key in ("match", "tlv") and isinstance(value, dict):
                yield from rec(key, value, kpath)
                yield from case_map(value.get("cases"), _p(kpath, "cases"))
                if isinstance(value.get("default"), list):
                    yield from field_list(value["default"], _p(kpath, "default"))
            elif key == "flagged" and isinstance(value, dict):
                yield from rec("flagged", value, kpath)
                for i, group in enumerate(value.get("groups") or []):
                    if isinstance(group, dict):
                        gpath = "%s.groups[%d]" % (kpath, i)
                        yield from rec("flagged_group", group, gpath)
                        yield from field_list(group.get("fields"), _p(gpath, "fields"))
            elif key == "byte_group":
                if isinstance(value, dict):
                    yield from rec("byte_group", value, kpath)
                    yield from field_list(value.get("fields"), _p(kpath, "fields"))
                else:
                    yield from field_list(value, kpath)
            elif key == "transform" and isinstance(value, list):
                for i, stage in enumerate(value):
                    if isinstance(stage, dict):
                        yield from rec("transform", stage, "%s[%d]" % (kpath, i))
            elif key == "guard" and isinstance(value, dict):
                yield from rec("guard", value, kpath)
                for i, cond in enumerate(value.get("when") or []):
                    if isinstance(cond, dict):
                        yield from rec("guard_when", cond, "%s.when[%d]" % (kpath, i))
            elif key in ("compute", "ipso", "senml", "semantic") and isinstance(
                value, dict
            ):
                yield from rec(key, value, kpath)
            elif key == "items" and isinstance(value, dict):
                yield from field(value, kpath)
            elif key in DATA_MAPPINGS or key in OPAQUE:
                continue
            elif isinstance(value, (dict, list)):
                yield from generic("%s.%s" % (ctx, key), value, kpath)

    yield from rec("schema", schema, "")
    yield from field_list(schema.get("fields"), "fields")
    ports = schema.get("ports")
    if isinstance(ports, dict):
        for pk, entry in ports.items():
            ppath = "ports[%s]" % pk
            if isinstance(entry, dict):
                yield from rec("port", entry, ppath)
                yield from field_list(entry.get("fields"), _p(ppath, "fields"))
            else:
                yield from field_list(entry, ppath)
    definitions = schema.get("definitions")
    if isinstance(definitions, dict):
        for dk, entry in definitions.items():
            # A definition without fields or a type is a table of named constants
            # (lorawan `mtype_values`): its keys are data.
            if isinstance(entry, dict) and ("fields" in entry or "type" in entry):
                yield from field(entry, "definitions.%s" % dk, "definition")
    for i, vector in enumerate(schema.get("test_vectors") or []):
        if isinstance(vector, dict):
            # Values are data; not descended into.
            yield from rec("vector", vector, "test_vectors[%d]" % i)
    metadata = schema.get("metadata")
    if isinstance(metadata, dict):
        yield from rec("metadata", metadata, "metadata")
        for key, value in metadata.items():
            items = value if isinstance(value, list) else [value]
            for i, item in enumerate(items):
                if isinstance(item, dict):
                    yield from rec(
                        "metadata.%s" % key, item, "metadata.%s[%d]" % (key, i)
                    )
    for key, value in schema.items():
        if key not in ("fields", "ports", "definitions", "test_vectors", "metadata"):
            if isinstance(value, (dict, list)):
                yield from generic("schema.%s" % key, value, str(key))


#: A `definition` is a field (it may carry `type`, `$ref`, ...) plus its own labels, so
#: its accepted keys are the union. Keeping the `definition` table itself small keeps
#: the invariants test's reader check about what a definition adds.
def allowed_keys(ctx: str) -> List[str]:
    keys = set(VOCABULARY.get(ctx, {}))
    if ctx == "definition":
        keys |= set(VOCABULARY["field"])
    if ctx == "legacy_case":
        keys |= {"case", "fields", "default"}
    return sorted(keys)


def referenced_definitions(schema: Any) -> set:
    """Names of the definitions reachable from the schema's field lists through local
    `#/definitions/<name>` references, transitively."""

    def refs(node: Any) -> Iterator[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    if value.startswith("#/definitions/"):
                        yield value[len("#/definitions/") :]
                elif key != "definitions":
                    yield from refs(value)
        elif isinstance(node, list):
            for item in node:
                yield from refs(item)

    definitions = schema.get("definitions") if isinstance(schema, dict) else None
    if not isinstance(definitions, dict):
        return set()
    roots = {
        k: v for k, v in schema.items() if k not in ("definitions", "test_vectors")
    }
    seen = set()
    pending = list(refs(roots))
    while pending:
        name = pending.pop()
        if name in seen or name not in definitions:
            continue
        seen.add(name)
        pending.extend(refs(definitions[name]))
    return seen


def _unreferenced_definition(path: str, referenced: set) -> bool:
    if not path.startswith("definitions."):
        return False
    name = path[len("definitions.") :].split(".", 1)[0].split("[", 1)[0]
    return name not in referenced


def is_known(ctx: str, key: str, inert: bool = False) -> bool:
    """Whether `key` is accepted in `ctx`. `inert` means the key sits in a definition
    nothing references, where the UNREAD_KEYS allowlist applies."""
    if key in DOCUMENTATION_KEYS:
        return True
    if inert and (ctx, key) in UNREAD_KEYS:
        return True
    if ctx == "legacy_case":
        return key in allowed_keys(ctx)
    if ctx not in VOCABULARY and ctx != "definition":
        # A mapping the grammar does not describe (`field.items`, `schema.foo`): no
        # key in it is read by anything.
        return False
    return key in allowed_keys(ctx)


def suggest(ctx: str, key: str) -> Optional[str]:
    """What to write instead of an unknown key, or None."""
    hint = HINTS.get((ctx, key))
    if hint is None and ctx == "definition":
        hint = HINTS.get(("field", key))
    if hint:
        return hint
    close = difflib.get_close_matches(key, allowed_keys(ctx), n=1, cutoff=0.75)
    if close:
        return "did you mean `%s`?" % close[0]
    return None


def unknown_keys(schema: Any) -> List[Tuple[str, str, str, Optional[str]]]:
    """(path, context, key, suggestion) for every key not in the vocabulary."""
    out = []
    referenced = referenced_definitions(schema)
    for ctx, key, path in iter_keys(schema):
        inert = _unreferenced_definition(path, referenced)
        if not is_known(ctx, key, inert):
            out.append((path, ctx, key, suggest(ctx, key)))
    return out


def divergent_keys(schema: Any) -> List[Tuple[str, str, str]]:
    """(path, key, reason) for every accepted key that decodes differently per language."""
    out = []
    for ctx, key, path in iter_keys(schema):
        fctx = "field" if ctx == "definition" else ctx
        if (fctx, key) in DIVERGENT_KEYS:
            out.append((path, key, DIVERGENT_KEYS[(fctx, key)]))
    return out
