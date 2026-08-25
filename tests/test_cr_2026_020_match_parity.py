"""CR-2026-020: every `match` key honoured by every implementation.

CR-2026-018 described the construct and found the support for it uneven. This closes that,
and these tests are about the behaviour rather than the description - the six fixtures in
`schemas/devices/_language-conformance/match-*.yaml` are what the Go, Java and C# runners
check, and what `tools/vector-verdicts.py` runs on both Python-side paths.

What was wrong, per implementation:

- **Go** dropped `length`, `name`, `var` and `default` in the parser. `length` was the
  damaging one: `decodeMatch` defaults it to 1, so a two-byte discriminator was read as one
  byte and every field after the construct came from the wrong offset. It also had no
  string-range case keys, turned a `default` case key into an ordinary case matching the
  string "default", and built its case list by ranging over a Go map - so where two keys
  could match the same value the winner varied between runs.
- **Java and C#** read `length` but never reported `name`, never stored `var`, and never
  read `default`.
- **The TS013 generator** had no notion of an inline discriminator, emitting `vars.` for
  the field it was not given, and interpolated case keys straight into a comparison, so a
  range key produced `vars.kind === 2..5` - not valid JavaScript, so the entire codec
  failed to parse and the schema had no generated path at all.

The tests here cover the Python interpreter and the generated codec directly. The other
three are covered by the fixtures through their own corpus runners; there is a test below
asserting those fixtures exist and say what this CR needs them to say, so the parity
cannot quietly stop being checked.
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

#: The fixtures this CR added, and the key each exists to pin.
VECTORS = {
    "match-inline-discriminator.yaml": ("length", "name"),
    "match-case-range.yaml": (),
    "match-default-fields.yaml": ("default",),
    "match-default-skip.yaml": ("default",),
    "match-cases-default-key.yaml": (),
    "match-var.yaml": ("length", "var"),
}


def decode(schema, payload):
    return SchemaInterpreter(schema).decode(bytes.fromhex(payload))


def decode_js(schema, payload):
    """The same schema through the generated codec, which is the other conformance path."""
    js = TS013Generator(schema).generate()
    harness = (
        js
        + "\nvar _r = decodeUplink({fPort: 1, bytes: ["
        + ",".join(str(b) for b in bytes.fromhex(payload))
        + "]});\nconsole.log(JSON.stringify("
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
        return {"data": None, "errors": [done.stderr.strip()[:200]]}
    return json.loads(done.stdout)


def inline_schema(**block):
    match = {"length": 1, "cases": {2: [{"name": "reading", "type": "u8"}]}}
    match.update(block)
    return {"name": "probe", "endian": "big", "fields": [{"match": match}]}


def keyed_schema(cases, **block):
    match = {"field": "kind", "cases": cases}
    match.update(block)
    return {
        "name": "probe",
        "endian": "big",
        "fields": [{"name": "kind", "type": "u8"}, {"match": match}],
    }


class TestAnInlineDiscriminator:
    """`length` decides the width, and getting it wrong corrupts everything after."""

    def test_a_two_byte_discriminator_consumes_two_bytes(self):
        schema = {"name": "p", "endian": "big", "fields": [
            {"match": {"length": 2, "cases": {258: [{"name": "after", "type": "u8"}]}}}]}
        assert decode(schema, "01027F").data == {"after": 127}

    def test_the_generated_codec_consumes_the_same_two(self):
        schema = {"name": "p", "endian": "big", "fields": [
            {"match": {"length": 2, "cases": {258: [{"name": "after", "type": "u8"}]}}}]}
        assert decode_js(schema, "01027F")["data"] == {"after": 127}

    def test_name_reports_the_discriminator(self):
        assert decode(inline_schema(name="kind"), "027F").data == {
            "kind": 2, "reading": 127}

    def test_the_generated_codec_reports_it_too(self):
        assert decode_js(inline_schema(name="kind"), "027F")["data"] == {
            "kind": 2, "reading": 127}

    def test_var_stores_without_reporting(self):
        schema = inline_schema(var="kind")
        schema["fields"].append({"name": "echo", "type": "number", "ref": "$kind"})
        result = decode(schema, "027F")
        assert result.data == {"reading": 127, "echo": 2}, result.errors

    def test_the_generated_codec_stores_it_too(self):
        schema = inline_schema(var="kind")
        schema["fields"].append({"name": "echo", "type": "number", "ref": "$kind"})
        assert decode_js(schema, "027F")["data"] == {"reading": 127, "echo": 2}

    def test_a_discriminator_from_a_field_is_not_reported_twice(self):
        """It is already in the output under its own name."""
        schema = keyed_schema({2: [{"name": "reading", "type": "u8"}]})
        assert decode(schema, "027F").data == {"kind": 2, "reading": 127}


class TestARangeCaseKey:
    """`"2..5"` is inclusive at both ends, and used to break the generated codec."""

    @pytest.mark.parametrize("value", [2, 3, 5])
    def test_values_inside_the_range_match(self, value):
        schema = keyed_schema({"2..5": [{"name": "hit", "type": "u8"}]})
        assert decode(schema, f"{value:02X}7F").data == {"kind": value, "hit": 127}

    @pytest.mark.parametrize("value", [1, 6])
    def test_values_outside_it_do_not(self, value):
        schema = keyed_schema({"2..5": [{"name": "hit", "type": "u8"}]},
                             default="skip")
        assert decode(schema, f"{value:02X}7F").data == {"kind": value}

    @pytest.mark.parametrize("value", [2, 3, 5])
    def test_the_generated_codec_agrees(self, value):
        schema = keyed_schema({"2..5": [{"name": "hit", "type": "u8"}]})
        assert decode_js(schema, f"{value:02X}7F")["data"] == {
            "kind": value, "hit": 127}

    def test_the_generated_codec_is_valid_javascript(self):
        """The whole point: a range key used to emit `=== 2..5` and fail to parse."""
        schema = keyed_schema({"2..5": [{"name": "hit", "type": "u8"}]})
        js = TS013Generator(schema).generate()
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(js)
            path = fh.name
        try:
            done = subprocess.run(["node", "--check", path],
                                  capture_output=True, text=True)
        finally:
            Path(path).unlink(missing_ok=True)
        assert done.returncode == 0, done.stderr

    def test_an_exact_key_beside_a_range_still_works(self):
        schema = keyed_schema({"2..5": [{"name": "ranged", "type": "u8"}],
                               9: [{"name": "exact", "type": "u8"}]})
        assert decode(schema, "097F").data == {"kind": 9, "exact": 127}
        assert decode_js(schema, "097F")["data"] == {"kind": 9, "exact": 127}

    def test_a_key_no_value_can_satisfy_emits_no_branch(self):
        """A `"[1, 2]"` key matches nothing anywhere, so it earns no test in the codec."""
        schema = keyed_schema({"[1, 2]": [{"name": "never", "type": "u8"}]},
                              default="skip")
        assert decode(schema, "017F").data == {"kind": 1}
        js = TS013Generator(schema).generate()
        assert "[1, 2]" not in js


class TestTheDefault:
    """`error` is the default default, and four implementations used to skip instead."""

    def test_a_field_list_is_decoded(self):
        schema = keyed_schema({1: [{"name": "known", "type": "u8"}]},
                             default=[{"name": "fallback", "type": "u8"}])
        assert decode(schema, "097F").data == {"kind": 9, "fallback": 127}
        assert decode_js(schema, "097F")["data"] == {"kind": 9, "fallback": 127}

    def test_skip_leaves_the_fields_absent(self):
        schema = keyed_schema({1: [{"name": "known", "type": "u8"}]}, default="skip")
        assert decode(schema, "097F").data == {"kind": 9}
        assert decode_js(schema, "097F")["data"] == {"kind": 9}

    def test_a_match_still_beats_the_default(self):
        schema = keyed_schema({1: [{"name": "known", "type": "u8"}]},
                             default=[{"name": "fallback", "type": "u8"}])
        assert decode(schema, "017F").data == {"kind": 1, "known": 127}
        assert decode_js(schema, "017F")["data"] == {"kind": 1, "known": 127}

    def test_error_is_what_absence_means(self):
        schema = keyed_schema({1: [{"name": "known", "type": "u8"}]})
        result = decode(schema, "097F")
        assert result.errors, "an unmatched value with no default must not pass silently"

    def test_the_generated_codec_errors_too(self):
        schema = keyed_schema({1: [{"name": "known", "type": "u8"}]})
        assert decode_js(schema, "097F")["errors"]

    def test_a_default_case_key_is_the_same_thing(self):
        schema = keyed_schema({1: [{"name": "known", "type": "u8"}],
                               "default": [{"name": "fallback", "type": "u8"}]})
        assert decode(schema, "097F").data == {"kind": 9, "fallback": 127}
        assert decode_js(schema, "097F")["data"] == {"kind": 9, "fallback": 127}

    def test_an_exact_case_beats_a_default_case_written_before_it(self):
        """Go returned on the default the moment it saw it, at random map order."""
        cases = {"default": [{"name": "fallback", "type": "u8"}],
                 1: [{"name": "known", "type": "u8"}]}
        schema = keyed_schema(cases)
        assert decode(schema, "017F").data == {"kind": 1, "known": 127}
        assert decode_js(schema, "017F")["data"] == {"kind": 1, "known": 127}

    def test_a_default_case_beats_the_default_key(self):
        schema = keyed_schema({1: [{"name": "known", "type": "u8"}],
                               "default": [{"name": "from_case", "type": "u8"}]},
                              default=[{"name": "from_key", "type": "u8"}])
        assert decode(schema, "097F").data == {"kind": 9, "from_case": 127}


class TestTheFixturesArePresent:
    """Parity nobody exercises is parity nobody is checking."""

    @pytest.mark.parametrize("filename", sorted(VECTORS))
    def test_the_fixture_exists_and_validates(self, filename):
        path = FIXTURES / filename
        assert path.is_file(), f"{filename} is missing"
        schema = yaml.safe_load(path.read_text())
        assert schema["test_vectors"], filename

    @pytest.mark.parametrize("filename,keys", sorted(VECTORS.items()))
    def test_the_fixture_uses_the_keys_it_exists_for(self, filename, keys):
        blocks = []

        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("match"), dict):
                    blocks.append(node["match"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(yaml.safe_load((FIXTURES / filename).read_text()))
        assert blocks, f"{filename} declares no match block"
        present = {key for block in blocks for key in block}
        missing = set(keys) - present
        assert not missing, f"{filename} no longer uses {sorted(missing)}"

    def test_every_fixture_pins_its_warnings(self):
        """`expected_warnings: []` is what catches a fallback that starts warning."""
        without = []
        for filename in sorted(VECTORS):
            schema = yaml.safe_load((FIXTURES / filename).read_text())
            for vector in schema["test_vectors"]:
                if "expected_warnings" not in vector:
                    without.append(f"{filename}: {vector.get('name')}")
        assert not without, without

    def test_between_them_they_cover_every_key(self):
        covered = set()
        for filename in VECTORS:
            def walk(node):
                if isinstance(node, dict):
                    if isinstance(node.get("match"), dict):
                        covered.update(node["match"])
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for item in node:
                        walk(item)

            walk(yaml.safe_load((FIXTURES / filename).read_text()))
        assert {"field", "length", "name", "var", "cases", "default"} <= covered, covered
