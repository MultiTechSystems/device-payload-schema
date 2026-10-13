"""CR-2026-164 (PS-575): a plain YAML 1.1 boolean scalar in a schema's YAML text is refused.

The interpreter's text loading, the validator, the TS013 generator and the binary converter
for C refuse the same text from one case list, which Go, Java and C# read too. A schema
handed over as an already-built mapping is not checked.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator, load_schema  # noqa: E402
from schema_interpreter import (  # noqa: E402
    SchemaInterpreter, YamlBooleanError, check_yaml_booleans)
from validate_schema import load_schema_text  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-164-cases.yaml").read_text())["cases"]
REJECTED = [c for c in CASES if "error" in c]
LOADED = [c for c in CASES if "error" not in c]


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_the_text_check_refuses_it_in_the_prototypes_words(case):
    with pytest.raises(YamlBooleanError) as raised:
        check_yaml_booleans(case["schema"])
    assert str(raised.value) == case["error"]


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_the_validator_loader_and_the_generator_loader_refuse_it(case, tmp_path):
    with pytest.raises(YamlBooleanError, match="PS-575"):
        load_schema_text(case["schema"], "t.yaml")
    path = tmp_path / "t.yaml"
    path.write_text(case["schema"])
    with pytest.raises(YamlBooleanError) as raised:
        load_schema(path)
    assert str(raised.value) == case["error"]


@pytest.mark.parametrize("case", REJECTED, ids=[c["name"] for c in REJECTED])
def test_the_command_line_tools_refuse_it(case, tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text(case["schema"])
    for argv in (["schema_interpreter.py", "decode", str(path), "00"],
                 ["validate_schema.py", str(path)],
                 ["schema_binary.py", "encode", str(path)]):
        run = subprocess.run([sys.executable, str(ROOT / "tools" / argv[0])] + argv[1:],
                             capture_output=True, text=True)
        assert run.returncode != 0, argv
        assert case["error"] in run.stdout + run.stderr, argv


@pytest.mark.parametrize("case", LOADED, ids=[c["name"] for c in LOADED])
def test_the_valid_ones_load_decode_and_re_encode(case, tmp_path):
    check_yaml_booleans(case["schema"])
    schema = load_schema_text(case["schema"], "t.yaml")
    interpreter = SchemaInterpreter(schema)
    decoded = interpreter.decode(bytes.fromhex(case["payload"]))
    assert decoded.success, decoded.errors
    assert decoded.data == json.loads(case["expected"])
    encoded = interpreter.encode(decoded.data)
    assert encoded.success, encoded.errors
    assert encoded.payload.hex() == case["payload"]
    path = tmp_path / "t.yaml"
    path.write_text(case["schema"])
    TS013Generator(load_schema(path)).generate()


def test_a_mapping_already_built_is_not_checked():
    # `on` as a key of a dict, as a YAML 1.1 loader would have produced True: no text, no check.
    SchemaInterpreter({"name": "t", "version": 1,
                       "fields": [{"name": "s", "type": "u8", "lookup": ["on", "off"]}]})


def test_malformed_yaml_is_left_to_the_loader():
    check_yaml_booleans("name: [unclosed\n")
