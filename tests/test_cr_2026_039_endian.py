"""CR-2026-039: byte order is declared with `endian`, and reaches floats.

The le_/be_ type prefixes are withdrawn and rejected (PS-053a) by the reference, the
generator and the validator - the generator and the validator still accepted them. That
the per-field key reaches f16/f32/f64 (PS-055a) is held by
_language-conformance/float-endian.yaml in all five implementations.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

PREFIXED = ["le_u16", "be_u16", "le_f32", "be_s32", "le_u8[0:3]"]


def one(type_name):
    return {"name": "probe", "fields": [{"name": "v", "type": type_name}]}


@pytest.mark.parametrize("spelling", PREFIXED)
def test_a_prefixed_type_is_rejected_everywhere(spelling):
    assert not SchemaInterpreter(one(spelling)).decode(b"\x01\x02\x03\x04").success
    assert validate_schema_structure(one(spelling))
    with pytest.raises(ValueError):
        TS013Generator(one(spelling)).generate()


def test_the_validator_says_what_to_write_instead():
    errors = validate_schema_structure(one("le_u16"))
    assert any("PS-053a" in e and "endian: little" in e for e in errors), errors
