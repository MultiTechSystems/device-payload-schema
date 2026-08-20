"""PS-301 to PS-304: an unknown TLV tag has to be visible.

`unknown` already had three modes and PS-155 already required them to be honoured. What
was missing was any way for a consumer to tell that the default had fired: a decoder that
met an undescribed tag and stopped reported a successful decode carrying fewer fields,
indistinguishable from a device that sent fewer fields.

That is not a hypothetical. 85 of the corpus's 87 `tlv` constructs are tag-only, so `skip`
cannot skip and instead abandons the remainder of the payload; seven corpus vectors,
four of them a vendor's own reference payload, do exactly this. `hbi/mla20` described one
of eight message types for months because nothing said the other seven existed.

The tests below pin what the warning must say, that the modes differ from each other, and
that the generated codec - the other conformance path - says the same thing. The last is
what PS-304 exists for: before it, the generator ignored `unknown` entirely, so a schema
declaring `error` raised on one path and skipped in silence on the other.
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

#: Tag 0x01 carries a u16 of 60; tag 0x09 is not described. Tag-only, so nothing
#: delimits the unknown entry and the two trailing bytes cannot be reached.
TAG_ONLY_PAYLOAD = "01003C090BB8"
#: The same, with a length byte after each tag, so the unknown entry can be stepped over.
DELIMITED_PAYLOAD = "0102003C09020BB8"


def schema(mode=None, length_size=0):
    tlv = {"tag_size": 1, "cases": {1: [{"name": "known", "type": "u16"}]}}
    if mode is not None:
        tlv["unknown"] = mode
    if length_size:
        tlv["length_size"] = length_size
    return {"name": "t", "endian": "big", "fields": [{"tlv": tlv}]}


def decode(mode=None, length_size=0, payload=TAG_ONLY_PAYLOAD):
    return SchemaInterpreter(schema(mode, length_size)).decode(bytes.fromhex(payload))


def decode_js(mode=None, length_size=0, payload=TAG_ONLY_PAYLOAD):
    js = TS013Generator(schema(mode, length_size)).generate()
    octets = list(bytes.fromhex(payload))
    driver = (
        js
        + f"\nvar r = decodeUplink({{bytes: {json.dumps(octets)}, fPort: 1}});"
        + "\nconsole.log(JSON.stringify(r));"
    )
    out = subprocess.run(["node", "-e", driver], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class TestTheDefaultIsReported:
    """PS-301, PS-302. `skip` stays the default; what changes is that it says so."""

    def test_the_fields_before_the_unknown_tag_are_still_reported(self):
        # The reason this is a warning and not an error: those fields are good.
        assert decode().data["known"] == 60

    def test_stopping_short_is_reported(self):
        result = decode()
        assert result.warnings == [
            "unknown TLV tag (0x09) at offset 3: 3 of 6 byte(s) left undecoded"
        ]

    def test_the_decode_still_succeeds(self):
        assert decode().success

    def test_the_count_runs_from_the_tag_not_from_after_it(self):
        # The tag itself is undecoded too, so it is counted: 3 bytes from offset 3, not
        # the 2 bytes of payload that followed it.
        assert "3 of 6" in decode().warnings[0]

    def test_skip_is_what_happens_without_the_parameter(self):
        assert decode(mode=None).warnings == decode(mode="skip").warnings

    def test_a_delimited_entry_is_stepped_over_and_reported(self):
        result = decode(mode="skip", length_size=1, payload=DELIMITED_PAYLOAD)
        assert result.data["known"] == 60
        assert result.warnings == ["unknown TLV tag (0x09) skipped, 2 byte(s) discarded"]

    def test_a_payload_of_only_known_tags_carries_no_warning(self):
        assert decode(payload="01003C").warnings == []


class TestTheWarningNamesTheTag:
    """A warning saying only "unknown tag" sends an author looking; one naming the tag
    tells them what to add."""

    def test_a_single_byte_tag(self):
        assert "(0x09)" in decode().warnings[0]

    def test_a_composite_tag_names_every_component(self):
        # The Milesight shape: channel then type. Both are needed to add the case.
        composite = {
            "name": "t",
            "endian": "big",
            "fields": [{"tlv": {
                "tag_fields": [{"name": "channel", "type": "u8"},
                               {"name": "kind", "type": "u8"}],
                "tag_key": ["channel", "kind"],
                "cases": {"[1, 117]": [{"name": "battery", "type": "u8"}]},
            }}],
        }
        result = SchemaInterpreter(composite).decode(bytes.fromhex("0175640569"))
        assert result.data["battery"] == 100
        assert "(0x05, 0x69)" in result.warnings[0]


class TestErrorMode:
    def test_it_fails_the_decode(self):
        result = decode(mode="error")
        assert not result.success
        assert "Unknown TLV tag: 0x09" in result.errors[0]

    def test_it_is_available_but_not_the_default(self):
        # The CR's reasoning: an error discards the fields decoded before the tag, and
        # those are good, so it stays opt-in.
        assert decode(mode="skip").success
        assert not decode(mode="error").success


class TestRawMode:
    """PS-303. `raw` built its entry and then dropped it under the default `merge: true`,
    which is every schema that does not set `merge`."""

    def test_the_entry_is_reported_when_output_is_merged(self):
        result = decode(mode="raw")
        assert result.data["unknown_tags"] == [{"tag": [9], "raw": "0bb8"}]

    def test_the_key_cannot_collide_with_a_field(self):
        # PS-303 names the key rather than leaving it to the implementation, so a
        # consumer reading merged output knows where to look.
        assert "unknown_tags" in decode(mode="raw").data

    def test_a_delimited_entry_captures_only_its_own_bytes(self):
        result = decode(mode="raw", length_size=1, payload=DELIMITED_PAYLOAD)
        assert result.data["unknown_tags"] == [{"tag": [9], "raw": "0bb8"}]
        assert result.warnings == []

    def test_an_undelimited_capture_says_it_could_not_be_delimited(self):
        # Everything to the end of the buffer is captured, because nothing says where the
        # entry ends. That is worth reporting: the capture may hold several entries.
        assert decode(mode="raw").warnings == [
            "unknown TLV tag (0x09) captured raw; 2 byte(s) after it could not be delimited"
        ]

    def test_the_entry_appears_in_the_channel_list_when_output_is_not_merged(self):
        unmerged = schema(mode="raw")
        unmerged["fields"][0]["tlv"]["merge"] = False
        result = SchemaInterpreter(unmerged).decode(bytes.fromhex(TAG_ONLY_PAYLOAD))
        assert {"tag": [9], "raw": "0bb8"} in result.data["channels"]


class TestTheGeneratedCodecAgrees:
    """PS-304. A schema is conformant through either path, so the paths must not differ.
    The generator ignored `unknown` entirely before this."""

    def test_skip_reports_the_same_warning(self):
        assert decode_js()["warnings"] == decode().warnings

    def test_a_delimited_skip_reports_the_same_warning(self):
        assert (decode_js(mode="skip", length_size=1, payload=DELIMITED_PAYLOAD)["warnings"]
                == decode(mode="skip", length_size=1, payload=DELIMITED_PAYLOAD).warnings)

    def test_error_fails_on_the_generated_path_too(self):
        result = decode_js(mode="error")
        assert result["errors"] == ["Unknown TLV tag: 0x09"]
        assert result["data"] == {}

    def test_raw_captures_the_same_entry(self):
        result = decode_js(mode="raw")
        assert result["data"]["unknown_tags"] == [{"tag": [9], "raw": "0bb8"}]
        assert result["warnings"] == decode(mode="raw").warnings

    @pytest.mark.parametrize("mode", ["skip", "raw", "error"])
    def test_the_fields_before_the_tag_match_on_both_paths(self, mode):
        generated = decode_js(mode=mode)["data"].get("known")
        interpreted = decode(mode=mode).data.get("known")
        assert generated == interpreted


class TestTheCorpus:
    """The measurement the CR was filed on: real schemas, real vendor payloads."""

    def test_the_vendor_reference_payloads_that_stop_short_now_say_so(self):
        # am307's own reference payload decodes battery, temperature and humidity, then
        # meets tag (0x05, 0x6A) and stops. Nothing said so before.
        path = REPO_ROOT / "schemas" / "devices" / "milesight" / "am307.yaml"
        document = yaml.safe_load(path.read_text())
        vector = next(v for v in document["test_vectors"]
                      if v["name"] == "vendor_reference")
        result = SchemaInterpreter(document).decode(
            bytes.fromhex(vector["payload"].replace(" ", ""))
        )
        assert result.success
        assert any("0x05, 0x6A" in w for w in result.warnings)

    def test_the_recorded_expectations_are_unchanged(self):
        # No decoded value changes anywhere: the vectors that stop short keep their
        # expectations and gain a warning. This is what makes the CR safe to apply to a
        # corpus of 1229 vectors.
        path = REPO_ROOT / "schemas" / "devices" / "milesight" / "am307.yaml"
        document = yaml.safe_load(path.read_text())
        vector = next(v for v in document["test_vectors"]
                      if v["name"] == "vendor_reference")
        result = SchemaInterpreter(document).decode(
            bytes.fromhex(vector["payload"].replace(" ", ""))
        )
        for key, expected in vector["expected"].items():
            assert result.data[key] == pytest.approx(expected), key
