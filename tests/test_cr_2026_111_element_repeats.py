"""CR-2026-111: a repeat of single values, and an index that counts from `index_base`
(PS-083, PS-366, PS-371, PS-375 amended; PS-528 to PS-531).

A repeat declaring `element`, one field, in place of `fields` reports an array of the
element's values, with `null` at a position whose value was omitted (PS-528). An encoder
takes the array back (PS-529); a `null` position with no `sentinel` to write is
refused, naming it (PS-544, CR-2026-121). `index_base` counts the index from an integer
(PS-530). `_meta` nests the element's entry under `element` and an index listing carries
a non-zero base (PS-531, PS-375).

The cases mirror the spec prototype's TestCR2026111ElementRepeat, and the messages are
its wording, which Go, Java, C# and the TS013 generator give identically.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import generate_output_schema  # noqa: E402
from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_binary import schema_errors  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import load_schema_text, validate_schema_structure  # noqa: E402

INCREMENTS = [{"name": "increments", "type": "repeat", "until": "end",
               "element": {"type": "u16", "div": 10}}]


def schema_of(fields):
    return {"name": "t", "version": 1, "endian": "big", "fields": fields}


def decode(fields, payload):
    result = SchemaInterpreter(schema_of(fields)).decode(bytes.fromhex(payload))
    assert result.success, result.errors
    return result.data


def run_codec(schema, payload, data=None):
    code = TS013Generator(schema).generate()
    script = (
        code
        + "\nvar d = decodeUplink(" + json.dumps({"bytes": list(payload), "fPort": 1})
        + ");\nvar e; try { e = encodeDownlink({data: "
        + (json.dumps(data) if data is not None else "d.data")
        + ", fPort: 1}); } catch (x) { e = {errors: [String(x.message)]}; }"
        "\nprocess.stdout.write(JSON.stringify({d: d, e: e}));"
    )
    done = subprocess.run(["node"], input=script, capture_output=True, text=True,
                          check=True)
    return json.loads(done.stdout)


class TestDecode:
    def test_it_reports_an_array_of_values(self):
        assert decode(INCREMENTS, "00780096005A") == {"increments": [12, 15, 9]}

    def test_an_omitted_value_is_null_in_its_position(self):
        fields = [{"name": "v", "type": "repeat", "count": 3,
                   "element": {"type": "u8", "sentinel": [0xFF]}}]
        assert decode(fields, "01FF03") == {"v": [1, None, 3]}

    def test_a_mapping_miss_is_null_too(self):
        fields = [{"name": "v", "type": "repeat", "count": 2,
                   "element": {"type": "u8", "lookup": {1: "on"}}}]
        assert decode(fields, "0102") == {"v": ["on", None]}

    def test_a_value_that_cannot_be_computed_is_null(self):
        fields = [{"name": "v", "type": "repeat", "count": 2, "element": {
            "name": "x", "type": "u8", "transform": [{"log10": True}]}}]
        assert decode(fields, "0A00") == {"v": [1, None]}

    def test_present_if_refers_to_the_value_by_its_name(self):
        fields = [{"name": "v", "type": "repeat", "count": 3,
                   "present_if": {"field": "$x", "ne": 0},
                   "element": {"name": "x", "type": "u8"}}]
        assert decode(fields, "010002") == {"v": [1, 2]}

    def test_a_named_element_is_referred_to_by_its_name(self):
        # CR-2026-127: a nameless element declares no name (PS-528, PS-539), so the
        # element is named to be referred to.
        fields = [{"name": "v", "type": "repeat", "count": 3,
                   "present_if": {"field": "$element", "ne": 0},
                   "element": {"name": "element", "type": "u8"}}]
        assert decode(fields, "010002") == {"v": [1, 2]}

    def test_an_object_element_reports_the_object(self):
        fields = [{"name": "v", "type": "repeat", "until": "end", "element": {
            "type": "object", "fields": [{"name": "a", "type": "u8"}]}}]
        assert decode(fields, "0102") == {"v": [{"a": 1}, {"a": 2}]}

    def test_index_base_numbers_from_1(self):
        fields = [{"name": "devices", "type": "repeat", "count": 2, "index": "i",
                   "index_base": 1,
                   "fields": [{"name": "id", "type": "u16", "name_from": "Device${i}ID"}]}]
        assert decode(fields, "000A000B") == {
            "devices": [{"Device1ID": 10}, {"Device2ID": 11}]}

    def test_count_as_is_unaffected_by_the_base(self):
        fields = [{"name": "v", "type": "repeat", "count": 2, "index": "i",
                   "index_base": 5, "count_as": "n", "element": {"type": "u8"}},
                  {"name": "total", "type": "number", "ref": "$n"}]
        assert decode(fields, "0102") == {"v": [1, 2], "total": 2}


class TestEncode:
    def test_the_encoder_takes_an_array_of_values(self):
        encoded = SchemaInterpreter(schema_of(INCREMENTS)).encode({"increments": [12, 15, 9]})
        assert encoded.success and encoded.payload == bytes.fromhex("00780096005A")

    def test_a_null_position_is_refused_naming_it(self):
        encoded = SchemaInterpreter(schema_of(INCREMENTS)).encode({"increments": [12, None]})
        assert not encoded.success
        assert ("Repeat 'increments' has null at position 1, and its element "
                "declares no `sentinel` to write for it (PS-544)") in encoded.errors[0]

    def test_index_base_encodes_as_it_decodes(self):
        fields = [{"name": "v", "type": "repeat", "count": 2, "index": "i",
                   "index_base": 1,
                   "fields": [{"name": "n", "type": "u8"},
                              {"name": "pos", "type": "number", "ref": "$i"}]}]
        interp = SchemaInterpreter(schema_of(fields))
        assert interp.decode(b"\x05\x07").data == {
            "v": [{"n": 5, "pos": 1}, {"n": 7, "pos": 2}]}
        assert interp.encode({"v": [{"n": 5}, {"n": 7}]}).payload == b"\x05\x07"

    def test_a_name_from_key_counted_from_1_encodes(self):
        fields = [{"name": "devices", "type": "repeat", "count": 2, "index": "i",
                   "index_base": 1,
                   "fields": [{"name": "id", "type": "u16", "name_from": "Device${i}ID"}]}]
        encoded = SchemaInterpreter(schema_of(fields)).encode(
            {"devices": [{"Device1ID": 10}, {"Device2ID": 11}]})
        assert encoded.success and encoded.payload.hex() == "000a000b"


# The load-time rules, with the prototype's wording.
REFUSED = {
    "both": ({"count": 1, "fields": [{"name": "a", "type": "u8"}],
              "element": {"type": "u8"}},
             "Repeat 'v' declares both of `fields` and `element`; it declares exactly one "
             "(PS-083, PS-528)"),
    "neither": ({"count": 1},
                "Repeat 'v' declares neither of `fields` and `element`; it declares "
                "exactly one (PS-083, PS-528)"),
    "element not a mapping": ({"count": 1, "element": [{"type": "u8"}]},
                              "Repeat 'v': `element` is a single field definition "
                              "(PS-528)"),
    "string base": ({"count": 1, "index": "i", "index_base": "1",
                     "element": {"type": "u8"}},
                    "Repeat 'v': `index_base` is an integer (PS-530)"),
    "fractional base": ({"count": 1, "index": "i", "index_base": 1.5,
                         "element": {"type": "u8"}},
                        "Repeat 'v': `index_base` is an integer (PS-530)"),
    "boolean base": ({"count": 1, "index": "i", "index_base": True,
                      "element": {"type": "u8"}},
                     "Repeat 'v': `index_base` is an integer (PS-530)"),
}


@pytest.mark.parametrize("label", sorted(REFUSED))
def test_a_malformed_repeat_is_refused_on_every_path(label):
    extra, message = REFUSED[label]
    schema = schema_of([dict({"name": "v", "type": "repeat"}, **extra)])
    assert message in SchemaInterpreter(schema).decode(b"\x01").errors
    assert message in SchemaInterpreter(schema).encode({"v": [1]}).errors
    assert message in validate_schema_structure(load_schema_text(yaml.safe_dump(schema)))
    with pytest.raises(ValueError, match="PS-5[23][08]"):
        TS013Generator(schema).generate()


class TestMeta:
    def test_the_meta_entry_nests_the_element(self):
        schema = {"name": "t", "fields": [
            {"name": "temperatures", "type": "repeat", "count": 8, "index": "i",
             "index_base": 1, "identity": "i",
             "element": {"name": "t", "type": "u16", "unit": "Cel"}}]}
        meta = SchemaInterpreter(schema).interpret(bytes(16)).data["_meta"]
        entry = meta["fields"]["temperatures"]
        assert entry == {"type": "repeat", "reports": "array",
                         "element": {"type": "u16", "reports": "integer", "unit": "Cel", "display_unit": "Cel"},
                         "identity": {"index": "i", "count": 8, "index_base": 1}}
        # Key order is compared exactly across the four (gate-verdicts rule 3).
        assert list(entry) == ["type", "reports", "element", "identity"]
        assert list(entry["identity"]) == ["index", "count", "index_base"]

    def test_a_base_of_0_adds_nothing_to_the_listing(self):
        schema = {"name": "t", "fields": [
            {"name": "v", "type": "repeat", "count": 2, "index": "i", "identity": "i",
             "fields": [{"name": "a", "type": "u8"}]}]}
        meta = SchemaInterpreter(schema).interpret(b"\x01\x02").data["_meta"]
        assert meta["fields"]["v"]["identity"] == {"index": "i", "count": 2}

    def test_a_per_element_reference_names_the_element(self):
        schema = {"name": "t", "fields": [
            {"name": "v", "type": "repeat", "count": 2, "index": "i", "index_base": 1,
             "element": {"type": "s16", "ipso": {"object": 3303, "instance": "$i"}}}]}
        meta = SchemaInterpreter(schema).interpret(bytes(4)).data["_meta"]
        assert meta["fields"]["v"]["element"]["ipso"] == {
            "object": 3303, "instance": {"index": "i", "count": 2, "index_base": 1},
            "resource": 5700}


class TestGeneratedCodec:
    @pytest.mark.parametrize("fields,payload,expected", [
        (INCREMENTS, "00780096005A", {"increments": [12, 15, 9]}),
        ([{"name": "v", "type": "repeat", "count": 3,
           "element": {"type": "u8", "sentinel": [0xFF], "lookup": {1: "a", 3: "c"}}}],
         "01FF02", {"v": ["a", None, None]}),
        ([{"name": "v", "type": "repeat", "count": 2, "index": "i", "index_base": 1,
           "fields": [{"name": "n", "type": "u8"},
                      {"name": "pos", "type": "number", "ref": "$i"}]}],
         "0507", {"v": [{"n": 5, "pos": 1}, {"n": 7, "pos": 2}]}),
    ])
    def test_it_decodes_as_the_interpreter_does(self, fields, payload, expected):
        out = run_codec(schema_of(fields), bytes.fromhex(payload))
        assert out["d"]["data"] == expected
        assert decode(fields, payload) == expected

    def test_it_encodes_the_values_and_refuses_a_null(self):
        out = run_codec(schema_of(INCREMENTS), bytes.fromhex("00780096005A"))
        assert bytes(out["e"]["bytes"]).hex() == "00780096005a"
        out = run_codec(schema_of(INCREMENTS), b"", {"increments": [12, None]})
        message = ("Repeat 'increments' has null at position 1, and its element "
                   "declares no `sentinel` to write for it (PS-544)")
        assert any(message in e for e in out["e"]["errors"])


def test_the_output_schema_is_an_array_of_nullable_values():
    out = generate_output_schema(schema_of([{"name": "v", "type": "repeat", "count": 2,
                                             "element": {"type": "u8",
                                                         "lookup": {1: "on"}}}]))
    assert out["properties"]["v"] == {
        "type": "array", "items": {"type": ["string", "null"], "enum": ["on", None]}}


def test_the_binary_schema_refuses_rather_than_drops():
    errors = schema_errors(schema_of(INCREMENTS))
    assert any("`element`" in e and "PS-528" in e for e in errors)
