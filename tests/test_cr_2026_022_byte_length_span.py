"""CR-2026-022: a `byte_length` span must be consumed exactly, on every path.

Opened to fix the `max`/`byte_length` conflict CR-2026-021 recorded, and the conflict
turned out to be one of two ways into a larger hole: **the C# interpreter and the TS013
generator had no post-loop span check at all.**

PS-088 requires a `byte_length` repeat's members to divide the span exactly. The Python,
Go and Java interpreters enforced it. C# and the generator did not, so both ways of
failing were accepted silently:

- **A ceiling stopping the loop early** - `byte_length: 4` with `max: 2` over one-byte
  members leaves two bytes of the span unread.
- **Members that do not divide the span** - a 2-byte member over a 5-byte span starts an
  iteration at offset 4, which is inside the span, and finishes at 6, which is past it.

Either way the read position ends somewhere other than the span's end, and **every field
after the repeat comes from the wrong offset with nothing reported**. The uneven case is
the worse of the two: the third record's `b` held the *following field's* byte, and the
following field then read past the payload and got zero. No error, no warning, wrong
numbers.

The conflict itself is fixed as a diagnostic. Stopping at the ceiling was reported as
`byte_length mismatch: expected end at 4, got 2`, which reads as a short payload when the
cause is the schema's own cap. All five now say so, naming the ceiling and the bytes left.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402

FIXTURES = REPO_ROOT / "schemas" / "devices" / "_language-conformance"


def schema_with(repeat, member_width=1, tail=True):
    members = ([{"name": "a", "type": "u8"}] if member_width == 1
               else [{"name": "a", "type": "u8"}, {"name": "b", "type": "u8"}])
    field = {"name": "items", "type": "repeat", "fields": members}
    field.update(repeat)
    fields = [field]
    if tail:
        fields.append({"name": "tail", "type": "u8"})
    return {"name": "probe", "endian": "big", "fields": fields}


def decode(schema, payload):
    return SchemaInterpreter(schema).decode(bytes.fromhex(payload))


def decode_js(schema, payload):
    js = TS013Generator(schema).generate()
    body = ",".join(str(b) for b in bytes.fromhex(payload))
    harness = (
        js
        + f"\nvar _r = decodeUplink({{fPort: 1, bytes: [{body}]}});"
        + "\nconsole.log(JSON.stringify("
        + "{data: _r.data || null, errors: _r.errors || []}));"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(harness)
        path = fh.name
    try:
        done = subprocess.run(["node", path], capture_output=True, text=True)
    finally:
        Path(path).unlink(missing_ok=True)
    if done.returncode != 0:
        return {"data": None, "errors": ["did not run: " + done.stderr.strip()[:160]]}
    return json.loads(done.stdout)


class TestAnUnderrunIsRefused:
    """A ceiling that stops the loop with span left over."""

    SCHEMA = None

    def setup_method(self):
        self.SCHEMA = schema_with({"byte_length": 4, "max": 2})

    def test_the_interpreter_refuses_it(self):
        result = decode(self.SCHEMA, "0A141E28FF")
        assert result.errors, "two of four span bytes were left unread"

    def test_the_generated_codec_refuses_it(self):
        assert decode_js(self.SCHEMA, "0A141E28FF")["errors"]

    def test_the_message_names_the_ceiling_not_the_payload(self):
        """The conflict this CR was opened for: the old text blamed the payload."""
        for text in (decode(self.SCHEMA, "0A141E28FF").errors[0],
                     decode_js(self.SCHEMA, "0A141E28FF")["errors"][0]):
            assert "max of 2" in text, text
            assert "2 of 4 byte(s)" in text, text

    def test_the_two_paths_word_it_the_same(self):
        interpreted = decode(self.SCHEMA, "0A141E28FF").errors[0]
        generated = decode_js(self.SCHEMA, "0A141E28FF")["errors"][0]
        # The interpreter prefixes the field it was decoding; the tail must agree.
        assert interpreted.endswith(generated), (interpreted, generated)


class TestAnOverrunIsRefused:
    """Members that do not divide the span, which is PS-088 proper."""

    SCHEMA = None

    def setup_method(self):
        self.SCHEMA = schema_with({"byte_length": 5}, member_width=2)

    def test_the_interpreter_refuses_it(self):
        assert decode(self.SCHEMA, "0102030405FF").errors

    def test_the_generated_codec_refuses_it(self):
        assert decode_js(self.SCHEMA, "0102030405FF")["errors"]

    def test_both_report_the_offsets(self):
        for text in (decode(self.SCHEMA, "0102030405FF").errors[0],
                     decode_js(self.SCHEMA, "0102030405FF")["errors"][0]):
            assert "expected end at 5" in text, text
            assert "got 6" in text, text

    def test_the_generated_message_renders_the_offset_as_a_number(self):
        """`"..." + a + b` concatenates left to right, so the sum read as "05"."""
        text = decode_js(self.SCHEMA, "0102030405FF")["errors"][0]
        assert "at 05" not in text, text

    def test_what_it_used_to_produce_instead(self):
        """The shape of the silent corruption, recorded so the fix has a witness.

        Before this, the generated codec returned three records - the third holding the
        `tail` byte - and then read `tail` past the payload as zero. Nothing in the output
        marked any of it.
        """
        outcome = decode_js(self.SCHEMA, "0102030405FF")
        assert outcome["errors"], "the silent-corruption path is back"
        assert not (outcome["data"] or {}).get("items"), outcome["data"]


class TestTheValidCaseStillWorks:
    """What a new check could plausibly break."""

    SCHEMA = None

    def setup_method(self):
        self.SCHEMA = schema_with({"byte_length": 4, "max": 2}, member_width=2)

    def test_an_exactly_divided_span_decodes(self):
        expected = {"items": [{"a": 1, "b": 2}, {"a": 3, "b": 4}], "tail": 255}
        assert decode(self.SCHEMA, "01020304FF").data == expected

    def test_the_generated_codec_agrees(self):
        expected = {"items": [{"a": 1, "b": 2}, {"a": 3, "b": 4}], "tail": 255}
        assert decode_js(self.SCHEMA, "01020304FF")["data"] == expected

    def test_the_tail_comes_from_the_right_offset(self):
        """The whole point of the check: the field after the repeat is trustworthy."""
        assert decode(self.SCHEMA, "01020304FF").data["tail"] == 255
        assert decode_js(self.SCHEMA, "01020304FF")["data"]["tail"] == 255

    def test_a_ceiling_reached_exactly_at_the_span_end_is_not_an_error(self):
        """`max` is hit and the span is consumed, so there is nothing to complain about."""
        assert not decode(self.SCHEMA, "01020304FF").errors
        assert not decode_js(self.SCHEMA, "01020304FF")["errors"]

    def test_the_other_bounds_are_untouched(self):
        """The check is byte_length's; count and until-end must not acquire it."""
        for bound in ({"count": 2}, {"until": "end"}):
            schema = schema_with(bound, member_width=2, tail=False)
            assert not decode(schema, "01020304").errors, bound
            assert not decode_js(schema, "01020304")["errors"], bound


class TestTheFixture:
    def test_the_valid_span_fixture_exists(self):
        path = FIXTURES / "repeat-byte-length-span.yaml"
        assert path.is_file()
        schema = yaml.safe_load(path.read_text())
        assert schema["test_vectors"]

    def test_it_declares_a_span_a_ceiling_and_a_field_after_it(self):
        schema = yaml.safe_load((FIXTURES / "repeat-byte-length-span.yaml").read_text())
        repeat = next(f for f in schema["fields"] if f.get("type") == "repeat")
        assert repeat["byte_length"] == 4
        assert repeat["max"] == 2
        assert schema["fields"][-1]["name"] == "tail", (
            "without a field after the repeat a position left short is invisible"
        )

    def test_the_failing_cases_are_deliberately_not_fixtures(self):
        """Both fail the decode, and no vector kind expects a failure."""
        text = (FIXTURES / "repeat-byte-length-span.yaml").read_text()
        assert "no vector kind expects a failure" in text
