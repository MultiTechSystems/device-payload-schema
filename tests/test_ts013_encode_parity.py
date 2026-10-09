"""The generated codec's encodeDownlink agrees with the reference encoder.

Nothing exercised the generated encoder over the corpus: tools/vector-verdicts.py checks
decoding only. Measured before the runtime encoder replaced the unrolled one, it
round-tripped 960 of the 1680 corpus vectors the reference round-trips. It undid
`mult`/`div`/`add` in key order (PS-101), never undid a transform stage, a lookup, an
`encoding` or a sentinel, wrote a float as an integer, wrote nothing for a `match`, an
`object` or a `repeat`, and ignored the fPort it was asked to encode for.

Here every corpus decode vector is decoded by the generated codec and encoded back, and
the bytes must equal the reference's `encode(decode(payload))` - or both must fail. The
comparison is against the reference rather than the payload, so a vector the reference
cannot round-trip (a `default` label, an unrecoverable `skip`) still has to come out the
same way.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import is_encode_vector  # noqa: E402

CORPUS = REPO_ROOT / "schemas" / "devices"

RUNNER = """
var C = %s, O = [];
C.forEach(function (c) {
  var o = {};
  try {
    var u = decodeUplink({ bytes: c.b, fPort: c.p });
    var e = encodeDownlink({ data: u.data, fPort: c.p });
    if (e.errors.length) o.error = e.errors[0]; else o.bytes = e.bytes;
  } catch (x) { o.error = "threw: " + x; }
  O.push(o);
});
process.stdout.write(JSON.stringify(O));
"""


def reference(schema, payload, fport):
    interpreter = SchemaInterpreter(schema)
    decoded = interpreter.decode(payload, fPort=fport)
    if not decoded.success:
        return "skip"
    try:
        if fport is None:
            result = interpreter.encode(decoded.data)
        else:
            result = interpreter.encode(decoded.data, fPort=fport)
    except Exception:  # noqa: BLE001 - any failure is a failure to encode
        return None
    return None if result.errors else list(result.payload)


def corpus_schemas():
    for path in sorted(CORPUS.rglob("*.yaml")):
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        vectors = [
            v
            for v in schema.get("test_vectors") or []
            if isinstance(v, dict) and "payload" in v and not is_encode_vector(v)
        ]
        if vectors:
            yield path.relative_to(CORPUS).as_posix(), schema, vectors


def generated_encodes(schema, cases):
    code = TS013Generator(schema).generate()
    out = subprocess.run(
        ["node"],
        input=code + RUNNER % json.dumps(cases),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_the_generated_encoder_matches_the_reference_on_the_corpus():
    compared = same = 0
    differ = []
    for rel, schema, vectors in corpus_schemas():
        cases, expected = [], []
        for vector in vectors:
            payload = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            fport = vector.get("fPort") or vector.get("fport")
            want = reference(schema, payload, fport)
            if want == "skip":
                continue
            cases.append({"b": list(payload), "p": fport})
            expected.append((vector.get("name"), want))
        if not cases:
            continue
        for (name, want), got in zip(expected, generated_encodes(schema, cases)):
            compared += 1
            if (
                want is None
                and "error" in got
                or want is not None
                and got.get("bytes") == want
            ):
                same += 1
            else:
                differ.append(
                    "%s::%s reference %s, generated %s" % (rel, name, want, got)
                )
    assert compared > 2000, compared
    assert not differ, "%d of %d differ:\n  %s" % (
        len(differ),
        compared,
        "\n  ".join(differ[:40]),
    )


def encode(schema, data, fport=None):
    code = TS013Generator(schema).generate()
    script = code + "\nprocess.stdout.write(JSON.stringify(encodeDownlink(%s)));" % (
        json.dumps({"data": data, "fPort": fport})
    )
    out = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def decode(schema, payload, fport=None):
    code = TS013Generator(schema).generate()
    script = code + "\nprocess.stdout.write(JSON.stringify(decodeUplink(%s)));" % (
        json.dumps({"bytes": list(payload), "fPort": fport})
    )
    out = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def one_field(**field):
    return {"name": "t", "fields": [dict({"name": "t"}, **field)]}


@pytest.mark.parametrize(
    "field,value,payload",
    [
        # PS-101: mult, div, add whatever order the keys are written in.
        ({"type": "u16", "add": -40, "div": 10}, 25, "028a"),
        ({"type": "u16", "div": 10, "add": -40}, 25, "028a"),
        ({"type": "u16", "add": 1, "div": 4, "mult": 2}, 6, "000a"),
        # PS-102: the stages, undone last first; the bare modifiers before them.
        ({"type": "u16", "transform": [{"add": -400}, {"div": 10}]}, 25, "028a"),
        (
            {"type": "u16", "div": 10, "transform": [{"add": -40}, {"mult": 2}]},
            50,
            "028a",
        ),
        # PS-463: the encoding is written, not the plain integer.
        ({"type": "u8", "encoding": "bcd", "mult": 2}, 50, "25"),
        ({"type": "u16", "encoding": "sign_magnitude", "div": 10}, -0.1, "8001"),
        # The lookup is reversed after the arithmetic is undone.
        ({"type": "u8", "add": 1, "lookup": ["a", "b", "c"]}, "c", "01"),
        # A float is written as a float, a negative s32 as two's complement.
        ({"type": "f32"}, 1.5, "3fc00000"),
        ({"type": "s32"}, -2, "fffffffe"),
    ],
)
def test_each_step_is_undone(field, value, payload):
    result = encode(one_field(**field), {"t": value})
    assert result["errors"] == [], result
    assert bytes(result["bytes"]).hex() == payload


@pytest.mark.parametrize(
    "sentinel,encoding,payload",
    [
        ([65535], None, "ffff"),
        ([32768], "sign_magnitude", "8000"),
    ],
)
def test_an_absent_sentinel_reading_is_written_as_the_sentinel(
    sentinel, encoding, payload
):
    field = {"type": "u16", "sentinel": sentinel, "div": 10}
    if encoding:
        field["encoding"] = encoding
    assert bytes(encode(one_field(**field), {})["bytes"]).hex() == payload


def test_a_field_named_like_a_codec_variable_keeps_its_name():
    # `d` was staged as `var d`, replacing the output object: data came back as 0.
    schema = {
        "name": "clash",
        "fields": [
            {"name": "d", "type": "u8"},
            {"name": "w", "type": "u8"},
            {"name": "pos", "type": "u8"},
        ],
    }
    result = decode(schema, b"\x01\x02\x03")
    assert result["data"] == {"d": 1, "w": 2, "pos": 3}, result
    assert bytes(encode(schema, result["data"])["bytes"]).hex() == "010203"


def test_the_requested_port_is_encoded():
    schema = {
        "name": "ports",
        "ports": {
            "1": {"fields": [{"name": "a", "type": "u8"}]},
            "2": {"direction": "downlink", "fields": [{"name": "b", "type": "u16"}]},
            "3": {"direction": "uplink", "fields": [{"name": "c", "type": "u8"}]},
        },
    }
    one = encode(schema, {"a": 5}, 1)
    assert (bytes(one["bytes"]).hex(), one["fPort"]) == ("05", 1)
    default = encode(schema, {"b": 7})
    assert (bytes(default["bytes"]).hex(), default["fPort"]) == ("0007", 2)
    assert "PS-292" in encode(schema, {"c": 1}, 3)["errors"][0]


def test_stripping_the_runtime_comments_touches_no_code():
    from generate_ts013_codec import ENCODER_RUNTIME_JS, _strip_js_comments

    code_lines = [
        line.split("//")[0] if "//" in line else line
        for line in ENCODER_RUNTIME_JS.splitlines()
        if line.strip() and not line.strip().startswith(("//", "/*", "*"))
    ]
    # `//` only ever opens a comment: no string or regex in the runtime holds one.
    assert all(line.count('"') % 2 == 0 for line in code_lines)
    stripped = _strip_js_comments(ENCODER_RUNTIME_JS)
    assert "//" not in stripped
    assert len(stripped.splitlines()) == len(code_lines)


def test_a_slim_codec_decodes_the_corpus_exactly_as_the_full_one():
    runner = (
        "\nvar C = %s;\nprocess.stdout.write(JSON.stringify(C.map(function (c) {"
        " return decodeUplink({ bytes: c.b, fPort: c.p }); })));"
    )
    compared = 0
    for rel, schema, vectors in corpus_schemas():
        cases = [
            {
                "b": list(bytes.fromhex(str(v["payload"]).replace(" ", ""))),
                "p": v.get("fPort") or v.get("fport"),
            }
            for v in vectors
        ]
        outputs = []
        for slim in (False, True):
            code = TS013Generator(schema, slim=slim).generate()
            outputs.append(
                subprocess.run(
                    ["node"],
                    input=code + runner % json.dumps(cases),
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
            )
        assert outputs[0] == outputs[1], rel
        compared += len(cases)
    assert compared > 2000, compared


def test_a_slim_codec_carries_no_encoder_and_says_so():
    schema = one_field(type="u16", div=10)
    code = TS013Generator(schema, slim=True).generate()
    assert "encodeRoot" not in code and "ENC_TYPES" not in code
    assert not any(line.strip().startswith("//") for line in code.splitlines())
    script = code + "\nprocess.stdout.write(JSON.stringify(encodeDownlink(%s)));" % (
        json.dumps({"data": {"t": 1}})
    )
    out = json.loads(
        subprocess.run(
            ["node"], input=script, capture_output=True, text=True, check=True
        ).stdout
    )
    assert out["bytes"] == [] and "--slim" in out["errors"][0]
