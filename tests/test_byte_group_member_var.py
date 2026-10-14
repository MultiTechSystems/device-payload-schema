"""A `var:` on a byte_group member binds like any field's (PS-523, PS-539).

The Python reference and the generated TS013 codec bound only the member's own name, so
a reference, a computed operand or a match discriminator reading the var was absent or
failed ("Match field $vb has no value"), while Go, Java and C# bound it. The shared
fixture is `_language-conformance/byte-group-member-var.yaml`; this file pins what it
cannot: a member with a var and no name (reserved bits a later field reads), which has no
value to encode and so cannot be a round-trip vector.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

SCHEMA = {
    "name": "probe",
    "version": 1,
    "endian": "big",
    "fields": [
        {
            "byte_group": [
                {"type": "u8[4:7]", "var": "hi"},
                {"name": "lo", "type": "u8[0:3]"},
            ]
        },
        {"name": "c", "type": "number", "ref": "$hi"},
    ],
}


def test_an_unnamed_member_binds_its_var_and_is_not_reported():
    result = SchemaInterpreter(SCHEMA).decode(bytes.fromhex("12"))
    assert result.success, result.errors
    assert result.data == {"lo": 2, "c": 1}
