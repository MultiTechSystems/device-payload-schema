"""CR-2026-138 (PS-553): a field has a name; padding is a skip.

A nameless field other than a skip decoded in Python under the key `unknown`, was read and
dropped by Go, Java, C# and the generated codec, and was written as zero padding by every
encoder. A schema with one is now refused at load, identified by the field's position, in
the spec prototype's text - by the interpreter, the validator and the TS013 generator here,
and by Go, Java and C# from the same case list. Constructs, byte_group members, a repeat's
element and a skip stay nameless and load as before.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter, field_name_errors  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-138-cases.yaml").read_text())["cases"]
IDS = [case["name"] for case in CASES]
REJECTED = [c for c in CASES if "error" in c]
LOADED = [c for c in CASES if "error" not in c]


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_the_check_names_the_field_exactly(case):
    assert field_name_errors(yaml.safe_load(case["schema"])) == [case["error"]]


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_every_loader_refuses_it(case):
    schema = yaml.safe_load(case["schema"])
    decoded = SchemaInterpreter(schema).decode(b"\x01\x02\x03\x04", fPort=2)
    assert case["error"] in decoded.errors, decoded.errors
    assert decoded.data == {}
    encoded = SchemaInterpreter(schema).encode({"a": 1, "b": 2}, fPort=2)
    assert case["error"] in encoded.errors, encoded.errors
    assert encoded.payload == b""
    assert case["error"] in validate_schema_structure(schema)
    with pytest.raises(ValueError, match="PS-553"):
        TS013Generator(schema).generate()


def run_codec(schema, payload):
    script = (
        TS013Generator(schema).generate()
        + "\nvar d = decodeUplink({bytes: "
        + json.dumps(list(payload))
        + ", fPort: 1});"
        "\nvar e = encodeDownlink({data: d.data, fPort: 1});"
        "\nprocess.stdout.write(JSON.stringify({d: d, e: e}));"
    )
    done = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize("case", LOADED, ids=[c["name"] for c in LOADED])
def test_the_exempt_load_decode_and_re_encode(case):
    schema = yaml.safe_load(case["schema"])
    assert field_name_errors(schema) == []
    assert not any("PS-553" in e for e in validate_schema_structure(schema))
    payload = bytes.fromhex(case["payload"])
    expected = json.loads(case["expected"])
    interpreter = SchemaInterpreter(schema)
    decoded = interpreter.decode(payload)
    assert decoded.success, decoded.errors
    assert decoded.data == expected       # no `unknown` key for an unnamed member
    encoded = interpreter.encode(decoded.data)
    assert encoded.success, encoded.errors
    assert encoded.payload.hex() == case["payload"]
    codec = run_codec(schema, payload)
    assert codec["d"]["data"] == expected, codec
    assert bytes(codec["e"]["bytes"]).hex() == case["payload"], codec


def test_the_binary_converter_refuses_it_for_c():
    """C's binary format carries no names - each field is an IPSO object id and the
    loader invents its name - so the YAML is checked before conversion."""
    from schema_binary import schema_errors

    case = REJECTED[0]
    assert case["error"] in schema_errors(yaml.safe_load(case["schema"]))
    skip = next(c for c in LOADED if c["name"] == "a_skip_is_nameless")
    assert not any("PS-553" in e for e in schema_errors(yaml.safe_load(skip["schema"])))


def test_the_padding_paths_are_gone():
    """The encoders' zero-padding branch for a nameless field was the only reader of a
    field-level `default` outside a byte_group; it cannot be reached now, and is gone."""
    source = (ROOT / "tools" / "schema_interpreter.py").read_text()
    assert "it is padding, as a" not in source
    runtime = (ROOT / "tools" / "generate_ts013_codec.py").read_text()
    assert "// padding, as a skip is" not in runtime
