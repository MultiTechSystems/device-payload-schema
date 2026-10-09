"""CR-2026-071 in the C interpreter, and PS-446 on the paths that feed it.

**PS-443: the lookup sees the value after the modifiers.** The C interpreter keyed its
table on the raw value while the modifiers it had just computed went unused, so
`{type: u8, add: 1, lookup: [a, b, c]}` decoded raw 1 as "b" where every other
implementation gives "c". The float, u64, udec/sdec and enum paths each returned early
with only part of the post-read step - floats applied no modifier at all - so the header
now has one function, `finish_numeric`, that every numeric path ends in.
`src/selftest_schema.c` pins the behaviour; `make selftest` runs it.

**PS-446: a property the interpreter cannot apply rejects the schema.** C reads YAML
nowhere: it is fed a binary blob compiled on the host by `tools/schema_binary.py`, or a
schema built through the struct API (`tools/c-corpus-harness.py`). The compiler emitted a
blob for anything - `transform`, `guard`, `compute`, `polynomial`, `encoding: bcd`,
`sentinel` and `out_of_range` were each dropped, and the C interpreter decoded the field as
if it had a plain type, with success. It now refuses, naming the field and the property.

Probing every accepted corpus schema through the blob found three more silent losses in
the format itself, refused for the same reason: `skip` (TYPE_SKIP is 0x8, and the C
loader reads a three-bit type code because the high bit is the lookup flag, so a skip of
2 came back as a u16 with a lookup table), a plain `u8` after a bit range that does not
consume (its type byte 0x01 is the consume marker), and `type: match` (written, never
parsed by the loader).
"""

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HEADER = REPO_ROOT / "include" / "schema_interpreter.h"
SELFTEST = REPO_ROOT / "src" / "selftest_schema.c"
HARNESS = REPO_ROOT / "tools" / "c-corpus-harness.py"
FIXTURE = (
    REPO_ROOT
    / "schemas"
    / "devices"
    / "_language-conformance"
    / "arithmetic-order-read.yaml"
)

sys.path.insert(0, str(REPO_ROOT / "tools"))
import schema_binary  # noqa: E402


def _schema(*fields):
    return {"name": "probe", "version": 1, "endian": "big", "fields": list(fields)}


UNSUPPORTED = {
    "transform": {"name": "t", "type": "u8", "transform": [{"add": 1}]},
    "guard": {
        "name": "t",
        "type": "u8",
        "guard": {"when": [{"field": "$t", "gt": 5}], "else": 0},
    },
    "compute": {
        "name": "t",
        "type": "number",
        "compute": {"op": "add", "a": "$a", "b": "$b"},
    },
    "polynomial": {"name": "t", "type": "u16", "polynomial": [1, 2, 3]},
    "encoding": {"name": "t", "type": "u8", "encoding": "bcd"},
    "sentinel": {"name": "t", "type": "u16", "sentinel": [65535]},
    "out_of_range": {
        "name": "t",
        "type": "u8",
        "valid_range": [0, 50],
        "out_of_range": "omit",
    },
    "default": {"name": "t", "type": "u8", "lookup": {"1": "one", "default": "other"}},
}


class TestTheBinaryCompilerRefusesWhatCCannotApply:

    @pytest.mark.parametrize("prop", sorted(UNSUPPORTED))
    def test_it_refuses_naming_the_property(self, prop):
        with pytest.raises(schema_binary.UnrepresentableSchemaError) as caught:
            schema_binary.encode_schema(_schema(UNSUPPORTED[prop]))
        message = str(caught.value)
        assert f"`{prop}`" in message or f"lookup's `{prop}`" in message, message
        assert "PS-446" in message
        assert "'t'" in message

    def test_the_cli_exits_non_zero(self, tmp_path):
        path = tmp_path / "s.yaml"
        path.write_text(yaml.safe_dump(_schema(UNSUPPORTED["transform"])))
        done = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "schema_binary.py"),
                "encode",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 1
        assert "PS-446" in done.stderr and not done.stdout.strip()

    def test_the_conformance_fixture_is_refused_for_each_field_c_cannot_apply(self):
        doc = yaml.safe_load(FIXTURE.read_text())
        errors = schema_binary.schema_errors(doc)
        for name in (
            "modifiers_then_transform",
            "encoding_then_modifiers",
            "sentinel_before_modifiers",
        ):
            assert any(f"'{name}'" in e for e in errors), errors
        for name in ("keys_add_first", "lookup_after_arithmetic"):
            assert not any(f"'{name}'" in e for e in errors), errors

    def test_what_it_can_carry_still_encodes(self):
        """The blob src/selftest_schema.c loads, byte for byte."""
        blob = schema_binary.encode_schema(
            _schema(
                {
                    "name": "temperature",
                    "type": "u8",
                    "add": 1,
                    "lookup": ["a", "b", "c"],
                }
            )
        )
        assert blob.hex() == "50530100018100e70ca0640083000161010162020163"

    @pytest.mark.parametrize(
        "factor,ok",
        [
            ({"mult": 0.1}, True),
            ({"div": 10}, True),
            ({"mult": 0.5}, True),
            ({"mult": 0.0625}, True),
            ({"div": 3}, False),
            ({"mult": 0.2}, False),
            ({"mult": 0.00390625}, False),
            ({"mult": 0}, False),
        ],
    )
    def test_a_scale_the_c_loader_would_not_reproduce_is_refused(self, factor, ok):
        errors = schema_binary.schema_errors(
            _schema({"name": "t", "type": "u16", **factor})
        )
        assert (not errors) == ok, errors

    def test_an_add_is_rounded_to_hundredths_not_truncated(self):
        blob = schema_binary.encode_schema(
            _schema({"name": "t", "type": "u8", "add": 0.29})
        )
        assert blob.endswith(bytes([0xA0, 29, 0]))
        assert schema_binary.schema_errors(
            _schema({"name": "t", "type": "u8", "add": 0.001})
        )

    @pytest.mark.parametrize(
        "field",
        [
            {"name": "t", "type": "skip", "length": 2},
            {"name": "t", "type": "u64"},
            {"name": "t", "type": "ascii", "length": 4},
            {"name": "t", "type": "u8", "endian": "little"},
            {"name": "t", "type": "bytes", "length": 16},
            {"name": "t", "type": "u8", "lookup": [f"v{i}" for i in range(17)]},
            {"name": "t", "type": "u8", "lookup": ["a label longer than fifteen"]},
            {"name": "t", "tlv": {"tag_size": 1, "cases": {}}},
        ],
    )
    def test_a_type_or_size_the_format_would_alter_is_refused(self, field):
        assert schema_binary.schema_errors(_schema(field))

    def test_a_u8_after_a_bit_range_that_does_not_consume_is_refused(self):
        bits = {"name": "b", "type": "u8[0:3]"}
        assert schema_binary.schema_errors(_schema(bits, {"name": "t", "type": "u8"}))
        assert not schema_binary.schema_errors(
            _schema(dict(bits, consume=1), {"name": "t", "type": "u8"})
        )


class TestTheCInterpreterAppliesTheLookupLast:

    def test_one_function_applies_the_lookup(self):
        text = HEADER.read_text()
        start = text.index("static inline int decode_field(")
        end = text.index("static inline int schema_check_direction(")
        decode = text[start:end]
        assert "finish_numeric(" in decode
        # The raw-keyed comparison that gave "b" is gone, from both sites.
        assert not re.search(r"lookup\[\w+\]\.key == \(int\)raw_value", decode)

    def test_the_selftests_are_registered(self):
        text = SELFTEST.read_text()
        for name in (
            "test_the_lookup_follows_the_modifiers",
            "test_the_lookup_follows_the_modifiers_from_binary",
            "test_a_fractional_value_matches_no_key",
            "test_an_enum_applies_its_modifiers_first",
            "test_a_float_applies_its_modifiers",
        ):
            assert f"static void {name}(void)" in text
            assert f"    {name}();" in text

    @pytest.mark.skipif(not shutil.which("cc"), reason="no C compiler")
    def test_the_fixture_fields_c_can_build_pass_the_harness(self, tmp_path):
        """arithmetic-order-read.yaml with each field C cannot apply replaced by a skip
        of its width, so the rest read from their own offsets."""
        doc = yaml.safe_load(FIXTURE.read_text())
        widths = {"u8": 1, "u16": 2}
        dropped = []
        for i, field in enumerate(doc["fields"]):
            if {"transform", "encoding", "sentinel"} & set(field):
                doc["fields"][i] = {"type": "skip", "length": widths[field["type"]]}
                dropped.append(field["name"])
        assert "lookup_after_arithmetic" not in dropped
        for vector in doc["test_vectors"]:
            for name in dropped:
                vector["expected"].pop(name)
        (tmp_path / "fixture.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
        done = subprocess.run(
            [sys.executable, str(HARNESS), "--corpus", str(tmp_path), "--failures"],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert "2 of 2 attempted vectors decode as the corpus expects" in done.stdout


class TestTheHarnessSaysWhoseLimitItIs:

    def test_a_lookup_default_is_the_known_c_gap(self):
        spec = importlib.util.spec_from_file_location("harness", HARNESS)
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        lines, reason = harness.field_source(
            {"name": "t", "type": "u8", "lookup": {"1": "one", "default": "x"}}, "big"
        )
        assert lines is None
        assert reason == harness.UNREACHABLE_KEYS["default"]

    def test_each_unappliable_property_is_worded_as_a_c_limit(self):
        spec = importlib.util.spec_from_file_location("harness", HARNESS)
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        for key in (
            "transform",
            "polynomial",
            "compute",
            "ref",
            "guard",
            "encoding",
            "sentinel",
            "out_of_range",
        ):
            assert harness.UNREACHABLE_KEYS[key].startswith(
                "the interpreter has no"
            ), key
