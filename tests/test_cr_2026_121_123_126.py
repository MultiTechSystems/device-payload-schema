"""CR-2026-121, CR-2026-123 and CR-2026-126: element repeats completed.

- PS-544: an encoder meeting `null` in a repeat declaring `element` writes the element's
  first `sentinel`, and reports an error naming the repeat and the position where it
  declares none.
- PS-545: an `element` is a data field that declares `type`, not a construct; an element
  named with `_` is decoded and reported (PS-432 does not apply), and `$_x` reads it.
- PS-546, PS-547: a repeat's `identity`, or an element's `unit` or `ipso.instance`, may be
  `$name[i]`, naming an earlier sibling repeat declaring `element`, position by position;
  different lengths are a decode error naming both, and `_meta` lists it under `repeat`.
- PS-548: an `ipso.instance` is an integer from 0 to 65534, literally or by every value its
  reference can take.

The cases mirror the spec prototype's TestCR2026121ElementNullsAndShapes,
TestCR2026123IdentityFromASibling and TestCR2026126IpsoInstances.
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
from validate_schema import load_schema_text, validate_schema_structure  # noqa: E402


def schema_of(fields):
    return {"name": "t", "version": 1, "endian": "big", "fields": fields}


def repeat(element, count=3, **extra):
    return [
        dict(
            {"name": "v", "type": "repeat", "count": count, "element": element}, **extra
        )
    ]


def run_codec(schema, payload, data=None):
    code = TS013Generator(schema).generate()
    script = (
        code
        + "\nvar d; try { d = decodeUplink("
        + json.dumps({"bytes": list(payload), "fPort": 1})
        + "); } catch (x) { d = {errors: [String(x.message)]}; }"
        + "\nvar e; try { e = encodeDownlink({data: "
        + (json.dumps(data) if data is not None else "d.data")
        + ", fPort: 1}); } catch (x) { e = {errors: [String(x.message)]}; }"
        "\nprocess.stdout.write(JSON.stringify({d: d, e: e}));"
    )
    done = subprocess.run(
        ["node"], input=script, capture_output=True, text=True, check=True
    )
    return json.loads(done.stdout)


def load_errors(schema):
    """The load-time errors on the interpreter, validator and generator paths."""
    interp = SchemaInterpreter(schema).decode(bytes(32)).errors
    validator = validate_schema_structure(load_schema_text(yaml.safe_dump(schema)))
    try:
        TS013Generator(schema).generate()
        generator = []
    except ValueError as exc:
        generator = [str(exc)]
    return interp, validator, generator


SENTINEL = {"type": "u16", "sentinel": [0xFFFF, 0xFFFE]}
NO_SENTINEL_NULL = (
    "Repeat 'v' has null at position 1, and its element declares no "
    "`sentinel` to write for it (PS-544)"
)


class TestNullElement:
    def test_a_null_round_trips_through_the_first_sentinel(self):
        interp = SchemaInterpreter(schema_of(repeat(SENTINEL)))
        decoded = interp.decode(bytes.fromhex("0001FFFF0003")).data
        assert decoded == {"v": [1, None, 3]}
        assert interp.encode(decoded).payload.hex() == "0001ffff0003"

    def test_the_second_sentinel_re_encodes_as_the_first(self):
        interp = SchemaInterpreter(schema_of(repeat(SENTINEL)))
        decoded = interp.decode(bytes.fromhex("0001FFFE0003")).data
        assert interp.encode(decoded).payload.hex() == "0001ffff0003"

    def test_the_sentinel_is_written_as_raw_bits_not_through_the_modifiers(self):
        element = {"type": "u16", "div": 10, "sentinel": [0xFFFF]}
        encoded = SchemaInterpreter(schema_of(repeat(element, 2))).encode(
            {"v": [0.5, None]}
        )
        assert encoded.payload.hex() == "0005ffff"

    def test_a_null_without_a_sentinel_is_refused_naming_the_position(self):
        encoded = SchemaInterpreter(schema_of(repeat({"type": "u8"}, 2))).encode(
            {"v": [1, None]}
        )
        assert not encoded.success
        assert NO_SENTINEL_NULL in encoded.errors[0]

    def test_the_generated_codec_writes_the_sentinel_and_refuses_without_one(self):
        out = run_codec(schema_of(repeat(SENTINEL)), bytes.fromhex("0001FFFF0003"))
        assert out["d"]["data"] == {"v": [1, None, 3]}
        assert bytes(out["e"]["bytes"]).hex() == "0001ffff0003"
        out = run_codec(schema_of(repeat({"type": "u8"}, 2)), b"", {"v": [1, None]})
        assert any(NO_SENTINEL_NULL in e for e in out["e"]["errors"])


class TestElementShape:
    def test_an_internal_name_is_reported(self):
        fields = repeat({"name": "_x", "type": "u8"}, 2)
        interp = SchemaInterpreter(schema_of(fields))
        assert interp.decode(bytes.fromhex("0102")).data == {"v": [1, 2]}
        assert interp.encode({"v": [1, 2]}).payload.hex() == "0102"
        assert run_codec(schema_of(fields), b"\x01\x02")["d"]["data"] == {"v": [1, 2]}

    def test_an_internal_name_still_binds_for_references(self):
        fields = repeat(
            {"name": "_x", "type": "u8"}, present_if={"field": "$_x", "ne": 0}
        )
        data = SchemaInterpreter(schema_of(fields)).decode(bytes.fromhex("010002")).data
        assert data == {"v": [1, 2]}
        assert run_codec(schema_of(fields), bytes.fromhex("010002"))["d"]["data"] == {
            "v": [1, 2]
        }

    @pytest.mark.parametrize(
        "element",
        [
            {"byte_group": [{"name": "a", "type": "u8[0:3]"}]},
            {"name": "m", "match": {"length": 1, "cases": {}}},
            {"name": "f", "type": "u8", "flagged": {"field": "$x", "groups": []}},
            {"name": "n"},
        ],
    )
    def test_a_construct_or_typeless_element_is_rejected_on_every_path(self, element):
        message = (
            "Repeat 'v': `element` is a data field that declares `type`, not a "
            "construct (PS-545)"
        )
        interp, validator, generator = load_errors(schema_of(repeat(element)))
        assert message in interp
        assert message in validator
        assert any("PS-545" in e for e in generator)

    def test_an_object_element_reports_one_object(self):
        fields = repeat(
            {
                "name": "o",
                "type": "object",
                "fields": [{"name": "a", "type": "u8"}, {"name": "b", "type": "u8"}],
            },
            2,
        )
        data = (
            SchemaInterpreter(schema_of(fields)).decode(bytes.fromhex("01020304")).data
        )
        assert data == {"v": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]}


CODES = {
    "name": "codes",
    "type": "repeat",
    "count": 2,
    "element": {
        "type": "u8",
        "lookup": {1: "1.8.1", 2: "2.8.1", "default": "${value}"},
    },
}


def values(count=2, **extra):
    return dict(
        {
            "name": "values",
            "type": "repeat",
            "count": count,
            "index": "i",
            "identity": "$codes[i]",
            "element": {"type": "u32"},
        },
        **extra,
    )


class TestIdentityFromASibling:
    def test_two_arrays_and_the_meta_identity(self):
        out = (
            SchemaInterpreter(schema_of([CODES, values()]))
            .interpret(bytes.fromhex("01020000007B000001C8"))
            .data
        )
        assert {k: v for k, v in out.items() if k != "_meta"} == {
            "codes": ["1.8.1", "2.8.1"],
            "values": [123, 456],
        }
        entry = out["_meta"]["fields"]["values"]
        assert entry == {
            "type": "repeat",
            "element": {"type": "u32"},
            "identity": {
                "repeat": "codes",
                "values": ["1.8.1", "2.8.1", "${value}"],
                "open": True,
            },
        }
        # Key order is compared exactly across the four (gate-verdicts rule 3).
        assert list(entry["identity"]) == ["repeat", "values", "open"]

    def test_a_closed_listing_carries_no_open(self):
        codes = dict(CODES, element={"type": "u8", "lookup": {1: "a", 2: "b"}})
        out = (
            SchemaInterpreter(schema_of([codes, values()]))
            .interpret(bytes.fromhex("01020000007B000001C8"))
            .data
        )
        assert out["_meta"]["fields"]["values"]["identity"] == {
            "repeat": "codes",
            "values": ["a", "b"],
        }

    def test_a_sibling_unit_is_listed_by_the_repeat(self):
        units = {
            "name": "units",
            "type": "repeat",
            "count": 2,
            "element": {"type": "u8", "lookup": {0: "Wh", 1: "W"}},
        }
        reading = values(element={"type": "u32", "unit": "$units[i]"})
        del reading["identity"]
        out = (
            SchemaInterpreter(schema_of([units, reading]))
            .interpret(bytes.fromhex("00010000007B000001C8"))
            .data
        )
        assert out["_meta"]["fields"]["values"]["element"]["unit"] == {
            "repeat": "units",
            "values": ["Wh", "W"],
        }

    def test_different_lengths_are_an_error_naming_both(self):
        fields = [CODES, values(count=1)]
        result = SchemaInterpreter(schema_of(fields)).decode(
            bytes.fromhex("01020000007B")
        )
        assert not result.success
        assert (
            "Repeat 'values' has 1 values and 'codes', which identifies them, has 2 "
            "(PS-546)"
        ) in result.errors[0]
        out = run_codec(schema_of(fields), bytes.fromhex("01020000007B"))
        assert any("PS-546" in e and "'codes'" in e for e in out["d"]["errors"])

    @pytest.mark.parametrize(
        "fields",
        [
            lambda: [values(), CODES],  # not earlier
            lambda: [CODES, values(index="j")],  # wrong index
            lambda: [dict(CODES, element={"type": "u8"}), values()],  # no lookup
            lambda: [
                dict(
                    CODES,
                    fields=[{"name": "c", "type": "u8"}],  # not an element
                    element=None,
                ),
                values(),
            ],
        ],
    )
    def test_a_reference_that_is_not_an_earlier_labelled_sibling_is_rejected(
        self, fields
    ):
        built = fields()
        for f in built:
            if f.get("element", 1) is None:
                del f["element"]
        interp, validator, generator = load_errors(schema_of(built))
        assert any("PS-546" in e for e in interp)
        assert any("PS-546" in e for e in validator)
        assert any("PS-546" in e for e in generator)

    def test_reference_order_reads_both_names(self):
        """CR-2026-127 (PS-539 amended): `$codes[i]` is two references, the sibling and
        the repeat's own index. In order, neither is reported; a sibling declared after
        the reference is a forward reference as well as a PS-546 error."""
        interp, validator, _ = load_errors(schema_of([CODES, values()]))
        assert not [e for e in interp + validator if "PS-539" in e]
        interp, validator, _ = load_errors(schema_of([values(), CODES]))
        order = (
            "`$codes` (identity) is referenced before the schema declares it (PS-539"
        )
        assert any(order in e for e in interp)
        assert any(order in e for e in validator)
        interp, _, _ = load_errors(schema_of([CODES, values(identity="$nosuch[i]")]))
        assert any("`$nosuch` (identity) names no field" in e for e in interp)

    def test_the_generated_codec_decodes_both_arrays(self):
        out = run_codec(
            schema_of([CODES, values()]), bytes.fromhex("01090000007B000001C8")
        )
        assert out["d"]["data"] == {"codes": ["1.8.1", "9"], "values": [123, 456]}


class TestComputedCount:
    @pytest.mark.parametrize("kind", ["integer", "number"])
    def test_a_count_may_name_a_computed_field(self, kind):
        fields = [
            {"name": "total", "type": "u8"},
            {
                "name": "_n",
                "type": kind,
                "compute": {"op": "sub", "a": "$total", "b": 2},
            },
            {"name": "v", "type": "repeat", "count": "$_n", "element": {"type": "u8"}},
        ]
        interp = SchemaInterpreter(schema_of(fields))
        assert interp.decode(bytes.fromhex("040A0B")).data == {
            "total": 4,
            "v": [10, 11],
        }
        assert interp.encode({"total": 4, "v": [10, 11]}).payload.hex() == "040a0b"
        assert run_codec(schema_of(fields), bytes.fromhex("040A0B"))["d"]["data"] == {
            "total": 4,
            "v": [10, 11],
        }


PS_548 = "its ipso.instance can be other than an integer from 0 to 65534 (PS-548)"


def soil(lookup):
    return [
        {
            "name": "s",
            "type": "repeat",
            "count": 2,
            "index": "i",
            "fields": [
                {"name": "k", "type": "u8", "lookup": lookup},
                {
                    "name": "t",
                    "type": "s16",
                    "ipso": {"object": 3303, "instance": "$k"},
                },
            ],
        }
    ]


class TestIpsoInstance:
    def test_integer_labels_are_accepted(self):
        assert not any(
            "PS-548" in e for e in load_errors(schema_of(soil({0: 1, 1: 2})))[0]
        )

    @pytest.mark.parametrize(
        "lookup", [{0: "soil", 1: "air"}, {0: 70000}, {0: "1", "default": "${value}"}]
    )
    def test_labels_that_are_not_instance_ids_are_rejected(self, lookup):
        for path in load_errors(schema_of(soil(lookup))):
            assert any(PS_548 in e for e in path), path

    @pytest.mark.parametrize("instance", ["two", -1, 65535, True, 1.5])
    def test_a_literal_must_be_an_instance_id(self, instance):
        fields = [
            {"name": "t", "type": "u8", "ipso": {"object": 3303, "instance": instance}}
        ]
        interp, validator, generator = load_errors(schema_of(fields))
        assert "Field 't': " + PS_548 in interp
        assert any(PS_548 in e for e in validator)
        assert any(PS_548 in e for e in generator)

    @pytest.mark.parametrize("instance", [0, 65534])
    def test_a_literal_at_either_end_is_accepted(self, instance):
        fields = [
            {"name": "t", "type": "u8", "ipso": {"object": 3303, "instance": instance}}
        ]
        assert not any("PS-548" in e for e in load_errors(schema_of(fields))[0])

    @pytest.mark.parametrize("base,ok", [(1, True), (65527, True), (65528, False)])
    def test_an_index_must_fit_from_its_base(self, base, ok):
        fields = [
            {
                "name": "s",
                "type": "repeat",
                "count": 8,
                "index": "i",
                "index_base": base,
                "element": {"type": "u8", "ipso": {"object": 3303, "instance": "$i"}},
            }
        ]
        errors = load_errors(schema_of(fields))[0]
        assert any("PS-548" in e for e in errors) is not ok

    @pytest.mark.parametrize(
        "labels,ok", [({0: 3, 1: 4}, True), ({0: "Wh", 1: "W"}, False)]
    )
    def test_a_sibling_reference_is_held_to_it(self, labels, ok):
        fields = [
            {
                "name": "units",
                "type": "repeat",
                "count": 2,
                "element": {"type": "u8", "lookup": labels},
            },
            {
                "name": "v",
                "type": "repeat",
                "count": 2,
                "index": "i",
                "element": {
                    "type": "u32",
                    "ipso": {"object": 3305, "instance": "$units[i]"},
                },
            },
        ]
        errors = load_errors(schema_of(fields))[0]
        assert any("PS-548" in e for e in errors) is not ok
