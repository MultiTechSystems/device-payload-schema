"""The language reference documents every type, value and key the implementations accept.

docs/SCHEMA-LANGUAGE-REFERENCE.md fell behind the language twice over: the word-ordered
types and the MCCI minifloats were in every implementation and absent from it, and it
still described behaviour 0.5.2 had changed - bit ranges read big-endian, `encoding`
Python-only, the logs clamped at 1e-10. This holds the first half mechanically: anything
validate_schema.py's type list or tools/schema_vocabulary.py says an implementation reads
must be mentioned. It cannot hold the second half, which is why that list exists.
"""

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import schema_vocabulary  # noqa: E402

REFERENCE = (REPO_ROOT / "docs" / "SCHEMA-LANGUAGE-REFERENCE.md").read_text()

#: Keys deliberately left out of the language reference.
NOT_LANGUAGE = {
    ("schema", "manufacturer"),     # documentation-only identity in the meta-schema
    ("field", "encode_formula"),    # in the meta-schema, described by no clause (CR-2026-036)
}

VALUES = {
    "bytes format": ["hex", "hex:upper", "base64", "array"],
    "bitfield_string part format": ["decimal", "hex", "hex:upper"],
    "encoding": ["sign_magnitude", "bcd", "gray"],
    "compute op": ["add", "sub", "mul", "div", "mod", "idiv"],
    "guard op": ["gt", "gte", "lt", "lte", "eq", "ne"],
    "transform stage": ["round", "floor", "ceiling", "clamp", "sqrt", "abs", "pow", "log",
                        "log10"],
    "round ties": ["even", "away"],
    "timestamp mode": ["rx_time", "subtract", "unix_epoch", "iso8601", "calendar",
                       "elapsed_to_absolute"],
    "tlv unknown": ["skip", "raw", "error"],
    "direction": ["uplink", "downlink", "bidirectional"],
}


def mentioned(token):
    """Backticked, as a YAML key or `type:` value, or as a word in a code block."""
    t = re.escape(token)
    return bool(re.search(
        rf"`{t}`|type:\s*\"?{t}\"?\b|(?<![\w$]){t}:|[\[,(\s]{t}[\],)\s;]|ties:\s*\w*\|?{t}",
        REFERENCE))


def known_types():
    source = (REPO_ROOT / "tools" / "validate_schema.py").read_text()
    block = re.search(r"KNOWN_TYPES = \{(.*?)\n    \}", source, re.S).group(1)
    return sorted(set(re.findall(r"'([^']+)'", block)))


@pytest.mark.parametrize("type_name", known_types())
def test_every_accepted_type_is_documented(type_name):
    assert mentioned(type_name), f"{type_name} is accepted and never documented"


@pytest.mark.parametrize("kind,value", [(k, v) for k, vs in VALUES.items() for v in vs])
def test_every_accepted_value_is_documented(kind, value):
    assert mentioned(value), f"{kind} {value!r} is accepted and never documented"


def test_every_key_an_implementation_reads_is_documented():
    missing = [f"{ctx}.{key}"
               for ctx, keys in schema_vocabulary.VOCABULARY.items() if ctx != "vector"
               for key in keys
               if (ctx, key) not in NOT_LANGUAGE and not mentioned(key)]
    assert not missing, missing


@pytest.mark.parametrize("stale", [
    "big-endian whatever the schema",          # PS-059 reads the field's byte order
    "clamped at 1e-10",                        # PS-117: the field is absent
    "implemented in the Python interpreter only",
    "or text read with `length:`",             # PS-361: string is a literal
    "exist only in the Python interpreter",    # floor, ceiling and clamp are in all five
])
def test_a_statement_0_5_2_overturned_is_gone(stale):
    assert stale not in REFERENCE
