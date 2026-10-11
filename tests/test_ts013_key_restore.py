"""A field named like a codec local is reported under its own name, at every depth.

The generated codec stages a field whose name clashes with one of its own locals
(`d`, `e`, `pos`, ...) as `name_`, and restores the schema's name at its entry points.
The restore matched only the entry points whose result is called `r`, so a `default`
port entry - whose result is `rd` - reported `button: {d_: false, e_: false}`. Every
entry point is restored now, and nested object members with it.

Synthetic schemas only.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402

BUTTON = [
    {
        "name": "button",
        "type": "object",
        "fields": [
            {"name": "d", "type": "bool", "bit": 0},
            {"name": "e", "type": "bool", "bit": 1, "consume": 1},
            {"name": "pos", "type": "u8"},
        ],
    }
]

SCHEMAS = {
    "plain": {"name": "probe_locals_plain", "endian": "big", "fields": BUTTON},
    "default_port": {
        "name": "probe_locals_default_port",
        "endian": "big",
        "ports": {"default": {"fields": BUTTON}},
    },
    "listed_port": {
        "name": "probe_locals_listed_port",
        "endian": "big",
        "ports": {5: {"fields": BUTTON}},
    },
}


def decode_js(schema, payload, fport, slim):
    code = TS013Generator(schema, slim=slim).generate()
    code += "\nconsole.log(JSON.stringify(decodeUplink({bytes: %s, fPort: %d})));" % (
        json.dumps(list(payload)),
        fport,
    )
    out = subprocess.run(
        ["node", "-"], input=code, capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
@pytest.mark.parametrize("slim", [False, True])
@pytest.mark.parametrize("kind", sorted(SCHEMAS))
def test_nested_members_named_like_codec_locals(kind, slim):
    schema = SCHEMAS[kind]
    payload = bytes([0x01, 0x09])
    expected = {"button": {"d": True, "e": False, "pos": 9}}
    reference = SchemaInterpreter(schema).decode(payload, fPort=5)
    assert reference.success, reference.errors
    assert reference.data == expected
    result = decode_js(schema, payload, 5, slim)
    assert result["errors"] == []
    assert result["data"] == expected
