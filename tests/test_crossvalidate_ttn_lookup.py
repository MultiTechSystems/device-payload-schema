"""crossvalidate_ttn.py must find every oracle it claims to compare against.

Three ways it silently compared less than it reported, each fixed:

* codec examples were read with YAML 1.1 booleans, so a vendor's unquoted
  `switch_1: on` - the string its decoder emits - arrived as True and was blamed on
  the vendor as a stale example;
* the vendor decoder was looked up as `<stem>.js`, ignoring the codec's
  `uplinkDecoder.fileName`, so netvox's `payload/*.js` never ran;
* a codec named `<base>-codec-<version>.yaml` (dnt) was not found at all.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import crossvalidate_ttn as cv  # noqa: E402


def test_unquoted_on_off_yes_no_stay_strings(tmp_path):
    codec = tmp_path / "dev-codec.yaml"
    codec.write_text(
        "uplinkDecoder:\n"
        "  examples:\n"
        "    - input: {fPort: 1, bytes: [1]}\n"
        "      output:\n"
        "        data: {a: on, b: off, c: yes, d: no, e: true, f: false}\n"
    )
    data = cv.declared_examples(codec)[0]["output"]["data"]
    assert data == {"a": "on", "b": "off", "c": "yes", "d": "no", "e": True, "f": False}


def test_decoder_located_from_codec_filename(tmp_path):
    (tmp_path / "payload").mkdir()
    (tmp_path / "payload" / "r1.js").write_text("")
    codec = tmp_path / "r1-codec.yaml"
    codec.write_text("uplinkDecoder:\n  fileName: payload/r1.js\n")
    assert cv.find_decoder(tmp_path, codec, "r1") == tmp_path / "payload" / "r1.js"


def test_decoder_falls_back_to_stem(tmp_path):
    codec = tmp_path / "r1-codec.yaml"
    codec.write_text("uplinkDecoder: {}\n")
    assert cv.find_decoder(tmp_path, codec, "r1") == tmp_path / "r1.js"


def test_codec_with_version_suffix_after_codec(tmp_path):
    (tmp_path / "dnt-lw-wsci-codec-2-1-1.yaml").write_text("{}\n")
    (tmp_path / "dnt-lw-wsci-codec.yaml").write_text("{}\n")
    assert cv.find_codec(tmp_path, "dnt-lw-wsci-2-1-1").name == (
        "dnt-lw-wsci-codec-2-1-1.yaml"
    )
    assert cv.find_codec(tmp_path, "dnt-lw-wsci").name == "dnt-lw-wsci-codec.yaml"


def test_a_skipped_decoder_is_reported_not_counted(tmp_path):
    schema = tmp_path / "dev.yaml"
    schema.write_text("name: d\nfields:\n  - {name: a, type: u8}\n")
    (tmp_path / "dev-codec.yaml").write_text(
        "uplinkDecoder:\n"
        "  examples:\n"
        "    - input: {fPort: 1, bytes: [7]}\n"
        "      output: {data: {a: 7}}\n"
    )
    status, problems = cv.check_schema(schema, tmp_path, use_decoder=True)
    assert status == "agrees"
    assert any(p.startswith("note: vendor decoder") for p in problems)


def test_codec_named_without_the_codec_infix(tmp_path):
    # pallidus `codec-mr1.yaml`, mcci `codec-catena4610.yaml`, dragino `sw3l-lbcodec.yaml`,
    # ellenex `codec-single.yaml` (decoder uplinkdecoder-singlesense.js): found by name
    # or by the decoder the codec names.
    (tmp_path / "codec-mr1.yaml").write_text(
        "uplinkDecoder: {fileName: codec-mr1.js}\n"
    )
    (tmp_path / "sw3l-lbcodec.yaml").write_text(
        "uplinkDecoder: {fileName: sw3l-lb.js}\n"
    )
    (tmp_path / "codec-single.yaml").write_text(
        "uplinkDecoder: {fileName: uplinkdecoder-singlesense.js}\n"
    )
    assert cv.find_codec(tmp_path, "codec-mr1").name == "codec-mr1.yaml"
    assert cv.find_codec(tmp_path, "sw3l-lb").name == "sw3l-lbcodec.yaml"
    assert (
        cv.find_codec(tmp_path, "uplinkdecoder-singlesense").name == "codec-single.yaml"
    )
