"""A `tlv` with `merge: false` (Clause 4): its entries are a list under `channels`.

No device schema uses it, so nothing exercised it until `meta-tlv-channels.yaml` was added
for CR-2026-097's `_meta` entry, and that fixture found three gaps outside `_meta`:

- the generated TS013 codec did not decode it, so `channels` was missing with no error;
- the Python encoder and the generated codec's encoder wrote an empty payload for it and
  reported nothing - the silent write of nothing AGENTS.md warns about. Encoding the list
  is not defined yet (a CR is being drafted), so both now refuse it by name;
- the output schema did not declare `channels`.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import generate_output_schema  # noqa: E402
from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402


def schema(unknown="skip"):
    return {
        "name": "x",
        "fields": [
            {
                "tlv": {
                    "tag_size": 1,
                    "length_size": 1,
                    "merge": False,
                    "unknown": unknown,
                    "cases": {1: [{"name": "a", "type": "u8"}]},
                }
            }
        ],
    }


def run_js(sch, call):
    code = TS013Generator(sch).generate()
    out = subprocess.run(
        ["node"],
        input=code + "\nprocess.stdout.write(JSON.stringify(%s));" % call,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_the_generated_codec_decodes_channels_as_the_interpreter_does():
    for unknown, payload in (("skip", "010107"), ("raw", "0101070902aabb")):
        sch = schema(unknown)
        raw = bytes.fromhex(payload)
        interpreted = SchemaInterpreter(sch).decode(raw)
        generated = run_js(sch, "decodeUplink({bytes: %s, fPort: 1})" % list(raw))
        assert generated["data"] == interpreted.data, unknown
    assert interpreted.data == {
        "channels": [{"tag": [1], "a": 7}, {"tag": [9], "raw": "aabb"}]
    }


def test_both_encoders_refuse_channels_rather_than_write_nothing():
    data = {"channels": [{"tag": [1], "a": 7}]}
    python = SchemaInterpreter(schema()).encode(data)
    assert not python.success and python.payload == b""
    assert any("merge: false" in e for e in python.errors), python.errors
    js = run_js(schema(), "encodeDownlink({data: %s})" % json.dumps(data))
    assert js["bytes"] == [] and any("merge: false" in e for e in js["errors"]), js


def test_the_output_schema_declares_channels():
    declared = generate_output_schema(schema("raw"))["properties"]
    assert declared["channels"]["type"] == "array"
    item = declared["channels"]["items"]
    assert set(item["properties"]) == {"tag", "a", "raw"} and item["required"] == [
        "tag"
    ]
