"""A construct that fails to read stops the decode, as a plain field's error does.

`_decode_field_list` went on after a failed `$ref`, `match`, `tlv`, `flagged` or
`bitfield_string`, from wherever the failed read had left the position. A field after it was
then read from the wrong offset and reported beside the error: a trailer after a tlv whose
case read too far came back as the tlv's own first bytes. Go, Java and C# stop at the first
error.
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

TLV = """
name: t
version: 1
endian: big
fields:
  - tlv:
      tag_size: 1
      reserve: 2
      cases:
        1:
          - {name: reading, type: u32}
  - {name: trailer, type: u16}
"""

MATCH = """
name: m
version: 1
endian: big
fields:
  - {name: kind, type: u8}
  - match:
      field: $kind
      cases:
        1:
          - {name: reading, type: u32}
  - {name: trailer, type: u16}
"""

FLAGGED = """
name: f
version: 1
endian: big
fields:
  - {name: mask, type: u8}
  - flagged:
      field: mask
      groups:
        - bit: 0
          fields:
            - {name: reading, type: u32}
  - {name: trailer, type: u16}
"""


def decode(schema_text, hex_payload):
    return SchemaInterpreter(yaml.safe_load(schema_text)).decode(
        bytes.fromhex(hex_payload)
    )


@pytest.mark.parametrize(
    "schema_text, short, whole, expected",
    [
        (TLV, "0112BEEF", "0111223344BEEF", {"reading": 0x11223344, "trailer": 0xBEEF}),
        (
            MATCH,
            "0112BEEF",
            "0111223344BEEF",
            {"kind": 1, "reading": 0x11223344, "trailer": 0xBEEF},
        ),
        (
            FLAGGED,
            "0112BEEF",
            "0111223344BEEF",
            {"mask": 1, "reading": 0x11223344, "trailer": 0xBEEF},
        ),
    ],
    ids=["tlv", "match", "flagged"],
)
def test_nothing_after_a_failed_construct_is_reported(
    schema_text, short, whole, expected
):
    result = decode(schema_text, short)
    assert not result.success
    assert result.errors
    assert "trailer" not in result.data

    result = decode(schema_text, whole)
    assert result.success, result.errors
    assert result.data == expected
