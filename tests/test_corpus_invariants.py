"""Properties every corpus decode must have, whatever its expected values say.

`test_corpus_conformance.py` asks whether each vector decodes to the values it lists.
That question cannot see the defect shape this repository keeps finding: a decode that
reports success, matches every key it was asked about, and read the wrong bytes - or
left bytes unread, or carried a value no JSON parser accepts. A vector lists what its
author thought to check; these invariants hold for every vector whether or not anyone
thought to check them.

Every corpus vector with a payload is decoded once, through the Python reference
interpreter, and the result is shared by every invariant below (the `decoded` fixture):

1. FULL CONSUMPTION   a successful decode consumes the whole payload. The exceptions
                      are listed, each with its reason, and the list is a ratchet in
                      both directions.
2. STRICT JSON        the output serialises with `allow_nan=False` (PS-282).
3. NO STRAY `_` KEYS  PS-176 reserves `_` names; only `_quality`, `_warnings` and
                      `_meta` may be reported.
4. SUCCESS == NO ERRORS
5. INTEGRAL VALUES ARE INTEGERS  (CR-2026-008 / PS-280)
6. DETERMINISM        a fresh interpreter decodes the same bytes to the same output,
                      in the same key order.
7. YAML == JSON       the same schema authored as JSON decodes identically. The
                      interpreted-versus-generated comparison is not repeated here:
                      `tools/vector-verdicts.py` does it, and
                      `test_cr_2026_036_metadata_enrichment.py` runs it (marked slow).
8. EVERY KEY IS READ  every key a corpus schema uses is one some implementation or
                      tool reads, checked against a vocabulary table whose every entry
                      is itself checked against the source it names.

Counts are bounded, never pinned: the corpus grows with every CR, and a test pinning a
running total is broken by the next one (see AGENTS.md).
"""

import glob
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import is_encode_vector  # noqa: E402

CORPUS = REPO_ROOT / "schemas" / "devices"

#: Output keys PS-176 reserves for interpreter metadata.
RESERVED_OUTPUT_KEYS = frozenset({"_quality", "_warnings", "_meta"})


# --------------------------------------------------------------------------------------
# Loading and decoding, once per module
# --------------------------------------------------------------------------------------


class Decoded:
    """One corpus vector, decoded twice from YAML and once from its JSON rendering."""

    def __init__(self, key: str, schema: dict, vector: dict):
        self.key = key
        self.schema = schema
        self.vector = vector
        self.payload = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
        # Both spellings, as every corpus runner reads them (AGENTS.md).
        self.fport = vector.get("fPort") or vector.get("fport")
        self.result = self._decode(schema)
        self.again = self._decode(schema)
        self.from_json = None  # filled in by the fixture, per schema

    def _decode(self, schema: dict):
        return SchemaInterpreter(schema).decode(self.payload, fPort=self.fport)

    @property
    def unread(self) -> int:
        return len(self.payload) - self.result.bytes_consumed


def _load_corpus() -> List[Tuple[str, dict]]:
    schemas = []
    for path in sorted(CORPUS.rglob("*.yaml")):
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        schemas.append((path.relative_to(CORPUS).as_posix(), schema))
    return schemas


@pytest.fixture(scope="module")
def corpus() -> List[Tuple[str, dict]]:
    return _load_corpus()


@pytest.fixture(scope="module")
def decoded(corpus) -> List[Decoded]:
    out = []
    for rel, schema in corpus:
        vectors = [
            v
            for v in schema.get("test_vectors") or []
            if isinstance(v, dict) and "payload" in v and not is_encode_vector(v)
        ]
        if not vectors:
            continue
        # json.dumps renders an integer mapping key as a string, which is exactly what
        # a schema authored as JSON carries.
        as_json = json.loads(json.dumps(schema))
        for vector in vectors:
            item = Decoded("%s::%s" % (rel, vector.get("name")), schema, vector)
            item.from_json = SchemaInterpreter(as_json).decode(
                item.payload, fPort=item.fport
            )
            out.append(item)
    return out


def _walk_output(value: Any, path: str = "") -> Iterator[Tuple[str, Any, Any]]:
    """Yield (path, key, value) for every mapping entry, and (path, None, v) for leaves."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield path, key, item
            yield from _walk_output(item, "%s.%s" % (path, key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk_output(item, "%s[%d]" % (path, index))
    else:
        yield path, None, value


def _ratchet_message(new: List[str], gone: List[str], what: str) -> str:
    lines = []
    if new:
        lines.append(
            "%d new %s (fix it, or add it to the list with a reason):"
            % (len(new), what)
        )
        lines.extend("  " + item for item in new)
    if gone:
        lines.append(
            "%d listed %s no longer occur - remove them from the list:"
            % (len(gone), what)
        )
        lines.extend("  " + item for item in gone)
    return "\n".join(lines)


def test_the_corpus_is_large_enough_to_mean_something(decoded):
    # A lower bound, not a pin: 1524 decode vectors when this file was written.
    assert len(decoded) > 1400, len(decoded)


# --------------------------------------------------------------------------------------
# 1. Full consumption
# --------------------------------------------------------------------------------------

#: A stop is the point of the fixture. Each reason was checked against the fixture's own
#: comment. The value is the number of bytes left unread.
INTENTIONAL_UNCONSUMED: Dict[str, Tuple[int, str]] = {
    "_language-conformance/match-default-skip.yaml::an_unmatched_value_is_skipped": (
        1,
        "`default: skip` with no case for 9: the construct's byte 7F is deliberately "
        "not read and decoding carries on",
    ),
    "_language-conformance/repeat-max-count.yaml::a_count_above_the_ceiling_is_clamped": (
        2,
        "count 4 clamped to `max: 2`; the two records beyond the ceiling stay unread",
    ),
    "_language-conformance/repeat-max.yaml::until_end_stops_at_the_ceiling": (
        2,
        "`until: end` with `max: 2` over four records; the description says the last "
        "two bytes are left unread",
    ),
    "_language-conformance/unknown-tlv-tag-skip.yaml::undescribed_tag_stops_the_decode": (
        2,
        "undescribed tag 0x09 with no length_size: `unknown: skip` abandons the rest "
        "(PS-301/PS-302). bytes_consumed counts the tag byte, the warning does not",
    ),
}

_NETVOX_PADDING = (
    "Netvox frames are zero-padded to a fixed 11 bytes (4 for an R718PE short "
    "response); payload/r718x.js reads only the leading bytes of this command/report, "
    "and the schema declares no trailing reserved span, so the padding is never read"
)
_AM30X_EXAMPLE = (
    "TTN's declared example ends with channel 05 6A 49 00 - the AM10x PIR `activity` "
    "u16le (am103.js) - which am30x.js has no case for and breaks out on; the schema "
    "agrees with the vendor, and the example was evidently copied from another model. "
    "The tag is consumed, the 2-byte value is not"
)
_WS50X_PADDING = (
    "vector defect, not schema: the harvester padded the FF 29 switch channel to 6 value "
    "bytes where it takes 1, so the next 2 bytes read as an unknown tag (ws50x.js also "
    "breaks out) and the remaining 3 are unread"
)

#: A real gap. Each reason says what the unread bytes are, found in the vendor decoder
#: under ~/Workspace/lora/tools/lorawan-devices/vendor/.
KNOWN_DEFECT_UNCONSUMED: Dict[str, Tuple[int, str]] = {
    "arwin/lrs10701.yaml::ttn_device_settings": (
        1,
        "byte 7 of the TTN example: lrs10701.js `case 12` reads bytes 0-6 only, and the "
        "schema's port 12 declares no trailing reserved byte",
    ),
    "milesight/am307.yaml::vendor_reference": (2, _AM30X_EXAMPLE),
    "milesight/am307l.yaml::vendor_reference": (2, _AM30X_EXAMPLE),
    "milesight/am308.yaml::vendor_reference": (2, _AM30X_EXAMPLE),
    "milesight/am308l.yaml::vendor_reference": (2, _AM30X_EXAMPLE),
    "milesight/ws50x.yaml::ch255_type41_midscale": (3, _WS50X_PADDING),
    "milesight/ws50x.yaml::ch255_type41_zero_values": (3, _WS50X_PADDING),
    "netvox/r718x.yaml::startup_version_report": (2, _NETVOX_PADDING),
    "netvox/r718x.yaml::config_report_response": (8, _NETVOX_PADDING),
    "netvox/r718x.yaml::set_on_distance_threshold_response": (8, _NETVOX_PADDING),
    "netvox/r718x.yaml::get_on_distance_threshold_response": (7, _NETVOX_PADDING),
    "netvox/r718x.yaml::set_fill_max_distance_response": (8, _NETVOX_PADDING),
    "netvox/r718x.yaml::get_fill_max_distance_response": (7, _NETVOX_PADDING),
    "netvox/r718x.yaml::set_dead_zone_distance_response": (8, _NETVOX_PADDING),
    "netvox/r718x.yaml::get_dead_zone_distance_response": (7, _NETVOX_PADDING),
    "netvox/r718x.yaml::config_report_failure": (8, _NETVOX_PADDING),
    "netvox/r718x.yaml::r718pe_set_dead_zone_distance_response": (1, _NETVOX_PADDING),
    "netvox/r718x.yaml::echoed_request_has_no_body": (9, _NETVOX_PADDING),
    "netvox/r718x.yaml::unknown_command": (9, _NETVOX_PADDING),
}


def test_allowlists_do_not_overlap():
    assert not set(INTENTIONAL_UNCONSUMED) & set(KNOWN_DEFECT_UNCONSUMED)


def test_a_successful_decode_consumes_the_whole_payload(decoded):
    """The "reported success but read the wrong bytes" shape, seen from its far end.

    A decode that stops early - a field that consumes nothing, a missing trailing span,
    a construct that gives up - still reports success and still matches every key its
    vector lists. What it cannot hide is where it stopped. The list is a ratchet: a new
    unconsumed vector fails, and so does a listed one that now consumes fully or whose
    unread count changed, so the list only ever shrinks.
    """
    allowed = dict(INTENTIONAL_UNCONSUMED)
    allowed.update(KNOWN_DEFECT_UNCONSUMED)

    actual = {
        item.key: item.unread
        for item in decoded
        if item.result.success and item.unread != 0
    }
    new = sorted(
        "%s leaves %d byte(s) unread" % (key, n)
        for key, n in actual.items()
        if key not in allowed
    )
    changed = sorted(
        "%s now leaves %d byte(s) unread, listed as %d"
        % (key, actual[key], allowed[key][0])
        for key in actual
        if key in allowed and actual[key] != allowed[key][0]
    )
    gone = sorted(key for key in allowed if key not in actual)
    assert not (new or changed or gone), _ratchet_message(
        new + changed, gone, "unconsumed vector(s)"
    )


# --------------------------------------------------------------------------------------
# 2-6. Output shape
# --------------------------------------------------------------------------------------


def test_output_is_strict_json(decoded):
    """PS-282: NaN and the infinities are not JSON, so a field holding one is omitted."""
    bad = []
    for item in decoded:
        try:
            json.dumps(item.result.data, allow_nan=False)
        except (ValueError, TypeError) as exc:
            bad.append("%s: %s" % (item.key, exc))
    assert not bad, "\n".join(bad)


def test_no_output_key_is_an_undeclared_underscore_name(decoded):
    """PS-176 reserves `_` names. An internal field is read and bound, never reported."""
    bad = []
    for item in decoded:
        for path, key, _ in _walk_output(item.result.data):
            if isinstance(key, str) and key.startswith("_"):
                if key not in RESERVED_OUTPUT_KEYS:
                    bad.append("%s: %s.%s" % (item.key, path, key))
    assert not bad, "\n".join(bad[:50])


def test_success_means_no_errors(decoded):
    """`success` is derived from `errors` (DecodeResult.success), so this is definitional
    in Python today. It is asserted anyway, because `result.success` is the public
    contract td-tools reads, and a change that made it a stored flag could let the two
    disagree. Errors must also be non-empty strings, so a failure always says why.
    """
    bad = []
    for item in decoded:
        r = item.result
        if r.success != (r.errors == []):
            bad.append("%s: success=%s errors=%r" % (item.key, r.success, r.errors))
        if any(not isinstance(e, str) or not e for e in r.errors):
            bad.append("%s: unusable error %r" % (item.key, r.errors))
    assert not bad, "\n".join(bad)


def test_integral_values_are_reported_as_integers(decoded):
    """CR-2026-008 (PS-280): an integral value reports as `15`, not `15.0`.

    The interpreter does this once, at the end of `decode`, through `normalize_output`,
    so the contract is on `result.data` itself: no float in it, at any depth, may have
    an integral value. `bool` is left alone (it is an `int` in Python, not a float).
    """
    bad = []
    for item in decoded:
        for path, key, value in _walk_output(item.result.data):
            if key is None and isinstance(value, float) and value.is_integer():
                bad.append("%s: %s = %r" % (item.key, path, value))
    assert not bad, "\n".join(bad[:50])


def _rendering(result) -> str:
    # Unsorted, so key order is compared too: a JS codec's output order is observable.
    return json.dumps(
        [result.data, result.errors, result.warnings, result.bytes_consumed],
        default=repr,
    )


def test_decoding_is_deterministic(decoded):
    """Two fresh interpreters, the same bytes: identical output, key order included.

    Go once built a match's case list by ranging over a map, so where two keys could
    match the winner varied between runs (CR-2026-020). Python state that leaked from
    one decode into the next (`_variables`, `_current_data`) would show up the same way.
    """
    bad = [
        item.key
        for item in decoded
        if _rendering(item.result) != _rendering(item.again)
    ]
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------------------
# 7. The same schema authored as JSON
# --------------------------------------------------------------------------------------

#: Schemas whose JSON rendering decodes differently from their YAML. A ratchet keyed by
#: schema: a new one fails, and a listed one that now agrees on every vector fails too.
#: The count is the number of vectors that disagree.
KNOWN_JSON_DIVERGENCE: Dict[str, Tuple[int, str]] = {
    # Empty since `_match_case_pattern` compares a numeric-string case key as a number.
    # Before that, 163 vectors in 19 schemas decoded differently from their JSON
    # rendering, 116 of them as successful decodes of the wrong branch.
}


def test_a_schema_authored_as_json_decodes_identically(decoded):
    """The language is YAML or JSON, and `yaml.safe_load` reads either.

    A JSON object's keys are strings, so the one thing a JSON rendering changes is the
    type of every integer mapping key - port numbers, lookup keys, match and tlv case
    keys. Ports and lookups survive it; match and tlv cases do not, and in several
    schemas the result is a successful decode of the wrong branch.
    """
    per_schema: Dict[str, int] = {}
    for item in decoded:
        if _rendering(item.result) != _rendering(item.from_json):
            rel = item.key.split("::", 1)[0]
            per_schema[rel] = per_schema.get(rel, 0) + 1

    new = sorted(
        "%s: %d vector(s)" % (rel, n)
        for rel, n in per_schema.items()
        if rel not in KNOWN_JSON_DIVERGENCE
    )
    changed = sorted(
        "%s: %d vector(s) now, listed as %d" % (rel, n, KNOWN_JSON_DIVERGENCE[rel][0])
        for rel, n in per_schema.items()
        if rel in KNOWN_JSON_DIVERGENCE and n != KNOWN_JSON_DIVERGENCE[rel][0]
    )
    gone = sorted(rel for rel in KNOWN_JSON_DIVERGENCE if rel not in per_schema)
    assert not (new or changed or gone), _ratchet_message(
        new + changed, gone, "YAML/JSON divergence(s)"
    )


# --------------------------------------------------------------------------------------
# 8. Every key a corpus schema uses is read by something
# --------------------------------------------------------------------------------------

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
    # Documentation, for an annotation no code reads: the key must be described there.
    "doc:reference": ["docs/SCHEMA-LANGUAGE-REFERENCE.md"],
}

_DECODERS = ("py", "go", "java", "cs", "ts013")

#: context -> key -> (readers, why). A context is the kind of mapping the key sits in,
#: as `_corpus_keys` walks it. Only keys the corpus actually uses are listed; a key some
#: implementation reads that no schema uses needs no entry.
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
    },
    "port": {
        "description": (("py", "outschema"), "documentation"),
        "fields": (_DECODERS, "the port's field list"),
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
        "match": (_DECODERS, "inline discriminated union (Option B)"),
        "tlv": (_DECODERS, "tag dispatch"),
        "flagged": (_DECODERS, "bitmask-gated groups"),
        "byte_group": (_DECODERS, "fields sharing bytes"),
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
        "valid_range": (("py", "go", "cs", "outschema"), "quality flag (_quality)"),
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
    "transform": {
        "add": (_DECODERS, "stage"),
        "mult": (_DECODERS, "stage"),
        "div": (_DECODERS, "stage"),
        "abs": (_DECODERS, "stage"),
        "sqrt": (_DECODERS, "stage (input clamped at 0)"),
        "pow": (_DECODERS, "stage"),
        "log": (_DECODERS, "stage (input clamped at 1e-10)"),
        "log10": (_DECODERS, "stage (input clamped at 1e-10)"),
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
    },
}

#: Keys the corpus uses that NOTHING reads - no decoder, encoder, generator, validator,
#: scorer or runner. A ratchet: a new one fails, and a listed one that stops occurring
#: fails until it is removed here. Every current entry lives in `_library-composed/`,
#: copied verbatim from `schemas/library/` by `tools/compose_library_vectors.py`, which
#: carries a catalogue's unreferenced definitions along with the one it composes.
UNREAD_KEYS: Dict[Tuple[str, str], str] = {
    ("field", "conditional"): (
        "a C-like expression (`status_and_id & 0x3C == 0`) in ts005/ts006 definitions. "
        "Nothing evaluates it, so the field is always decoded - the one entry here that "
        "would change a decode if its definition were ever referenced"
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
    ("vector", "direction"): (
        "on 11 encode vectors. No runner passes it to encode()/decode(direction=), so "
        "the PS-021 direction check is never exercised by a vector"
    ),
}

#: Mappings whose keys are data, not vocabulary: their values are walked, their keys
#: are not recorded.
_DATA_MAPPINGS = frozenset({"lookup", "enum", "values"})
#: Lists or scalars-in-lists that carry no keys of their own.
_OPAQUE = frozenset({"parts", "valid_range", "polynomial"})


def _corpus_keys(corpus) -> Dict[Tuple[str, str], Set[str]]:
    """(context, key) -> the schemas using it, walking the grammar rather than every
    mapping - so a case key, a lookup key or a port number is never mistaken for a
    schema key, and an unrecognised nested mapping is still recorded (under a context
    named after its parent) instead of skipped."""
    used: Dict[Tuple[str, str], Set[str]] = {}

    def rec(ctx: str, mapping: dict, rel: str) -> None:
        for key in mapping:
            used.setdefault((ctx, str(key)), set()).add(rel)

    def field_list(items: Any, rel: str) -> None:
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    field(item, rel)

    def case_map(cases: Any, rel: str) -> None:
        if isinstance(cases, dict):
            for body in cases.values():
                if isinstance(body, list):
                    field_list(body, rel)
                elif isinstance(body, dict):
                    field(body, rel)

    def generic(ctx: str, node: Any, rel: str) -> None:
        if isinstance(node, dict):
            rec(ctx, node, rel)
            for key, value in node.items():
                generic("%s.%s" % (ctx, key), value, rel)
        elif isinstance(node, list):
            for item in node:
                generic(ctx, item, rel)

    def field(fd: dict, rel: str, ctx: str = "field") -> None:
        rec(ctx, fd, rel)
        for key, value in fd.items():
            if key == "fields":
                field_list(value, rel)
            elif key == "cases":
                case_map(value, rel)
            elif key == "default" and isinstance(value, list):
                field_list(value, rel)
            elif key in ("match", "tlv") and isinstance(value, dict):
                rec(key, value, rel)
                case_map(value.get("cases"), rel)
                if isinstance(value.get("default"), list):
                    field_list(value["default"], rel)
            elif key == "flagged" and isinstance(value, dict):
                rec("flagged", value, rel)
                for group in value.get("groups") or []:
                    if isinstance(group, dict):
                        rec("flagged_group", group, rel)
                        field_list(group.get("fields"), rel)
            elif key == "byte_group":
                if isinstance(value, dict):
                    rec("byte_group", value, rel)
                    field_list(value.get("fields"), rel)
                else:
                    field_list(value, rel)
            elif key == "transform" and isinstance(value, list):
                for stage in value:
                    if isinstance(stage, dict):
                        rec("transform", stage, rel)
            elif key == "guard" and isinstance(value, dict):
                rec("guard", value, rel)
                for cond in value.get("when") or []:
                    if isinstance(cond, dict):
                        rec("guard_when", cond, rel)
            elif key in ("compute", "ipso", "senml") and isinstance(value, dict):
                rec(key, value, rel)
            elif key == "items" and isinstance(value, dict):
                field(value, rel)
            elif key in _DATA_MAPPINGS or key in _OPAQUE:
                continue
            elif isinstance(value, (dict, list)):
                generic("%s.%s" % (ctx, key), value, rel)

    for rel, schema in corpus:
        rec("schema", schema, rel)
        field_list(schema.get("fields"), rel)
        ports = schema.get("ports")
        if isinstance(ports, dict):
            for entry in ports.values():
                if isinstance(entry, dict):
                    rec("port", entry, rel)
                    field_list(entry.get("fields"), rel)
                else:
                    field_list(entry, rel)
        for entry in (schema.get("definitions") or {}).values():
            # A definition without fields or a type is a table of named constants
            # (lorawan `mtype_values`): its keys are data.
            if isinstance(entry, dict) and ("fields" in entry or "type" in entry):
                field(entry, rel, "definition")
        for vector in schema.get("test_vectors") or []:
            if isinstance(vector, dict):
                rec("vector", vector, rel)  # values are data; not descended into
        metadata = schema.get("metadata")
        if isinstance(metadata, dict):
            rec("metadata", metadata, rel)
            for key, value in metadata.items():
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, dict):
                        rec("metadata.%s" % key, item, rel)
        for key, value in schema.items():
            if key not in (
                "fields",
                "ports",
                "definitions",
                "test_vectors",
                "metadata",
            ):
                if isinstance(value, (dict, list)):
                    generic("schema.%s" % key, value, rel)
    return used


_SOURCE_CACHE: Dict[str, str] = {}


def _reader_source(reader: str) -> str:
    if reader not in _SOURCE_CACHE:
        texts = []
        for name in READERS[reader]:
            path = Path(name)
            if not path.is_absolute():
                path = REPO_ROOT / path
            texts.append(path.read_text(encoding="utf-8"))
        _SOURCE_CACHE[reader] = "\n".join(texts)
    return _SOURCE_CACHE[reader]


def _mentions(reader: str, key: str) -> bool:
    if reader.startswith("doc:"):
        return re.search(r"\b%s\b" % re.escape(key), _reader_source(reader)) is not None
    return (
        re.search(r"""['"]%s['"]""" % re.escape(key), _reader_source(reader))
        is not None
    )


def test_every_vocabulary_entry_is_backed_by_the_reader_it_names():
    """The table is only as good as its claims: each named reader must mention the key.

    This is what makes the vocabulary "built from the code". Removing a key from a
    reader, or naming a reader that never read it, fails here rather than letting the
    table go on vouching for a key nobody reads any more.
    """
    missing = []
    for ctx, keys in VOCABULARY.items():
        for key, (readers, _) in keys.items():
            assert readers, "%s.%s names no reader" % (ctx, key)
            for reader in readers:
                if not _mentions(reader, key):
                    missing.append("%s.%s: %s never mentions it" % (ctx, key, reader))
    assert not missing, "\n".join(missing)


def test_nothing_listed_unread_is_actually_read():
    """An UNREAD_KEYS entry is a claim that no reader mentions the key at all, so the
    claim is checked against every code reader. Common words are exempt - `version`
    and `direction` are read in another context, which the entry's reason explains.
    """
    context_bound = {"version", "direction", "items"}
    wrong = []
    for ctx, key in UNREAD_KEYS:
        assert key not in VOCABULARY.get(ctx, {}), "%s.%s is in both tables" % (
            ctx,
            key,
        )
        if key in context_bound:
            continue
        for reader in READERS:
            if not reader.startswith("doc:") and _mentions(reader, key):
                wrong.append("%s.%s is mentioned by %s" % (ctx, key, reader))
    assert not wrong, "\n".join(wrong)


def test_every_key_a_corpus_schema_uses_is_read_by_something(corpus):
    """A key nothing reads is a claim about the device that nothing honours.

    `optional: true` reads as "may be absent" and `conditional:` as "only when"; a
    reviewer takes both at their word, and no implementation does anything with either.
    """
    used = _corpus_keys(corpus)
    unknown = sorted(
        "%s.%s (%s)" % (ctx, key, ", ".join(sorted(files)[:3]))
        for (ctx, key), files in used.items()
        if key not in VOCABULARY.get(ctx, {}) and (ctx, key) not in UNREAD_KEYS
    )
    gone = sorted("%s.%s" % pair for pair in UNREAD_KEYS if pair not in used)
    assert not (unknown or gone), _ratchet_message(
        unknown, gone, "key(s) read by nothing"
    )


def test_the_key_walk_finds_the_whole_grammar(corpus):
    """Guard against the walk passing by finding nothing: every context it knows is
    reached. A bound on the key count, never a pin."""
    used = _corpus_keys(corpus)
    contexts = {ctx for ctx, _ in used}
    for ctx in ("schema", "field", "vector", "match", "tlv", "flagged", "transform"):
        assert ctx in contexts, ctx
    assert len(used) > 120, len(used)
