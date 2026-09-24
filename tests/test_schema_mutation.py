"""The schema-mutation harness: each operator, each outcome, and a corpus floor.

`tools/schema-mutation.py` asks whether a schema's vectors would notice if the schema
were wrong. These tests hold the harness itself to that: each operator must produce the
mutant it describes, a fixture whose vectors pin every value must kill every mutant, an
under-constrained one must leave the survivor its weakness implies, and a mutant that
cannot change any decode must be skipped rather than counted against the schema.

The corpus guard at the end is a bound, not a pin (AGENTS.md: "A CR-specific test must
bound every floor it names"). Adding a vector raises the score; the floor only has to
catch the harness breaking or the fixtures losing their teeth.
"""

import copy
import json
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "schema-mutation.py"
CONFORMANCE = REPO_ROOT / "schemas" / "devices" / "_language-conformance"


def _load():
    loader = SourceFileLoader("schema_mutation", str(TOOL))
    module = module_from_spec(spec_from_loader("schema_mutation", loader))
    loader.exec_module(module)
    return module


sm = _load()


def mutants_of(schema, operator):
    mutants, equivalent = sm.generate_mutants(schema, [operator])
    return mutants, equivalent


def applied(schema, mutant):
    mutated = copy.deepcopy(schema)
    mutant.apply(mutated)
    return mutated


def one_field(**field):
    field.setdefault("name", "v")
    return {"name": "t", "endian": "big", "fields": [field]}


# --------------------------------------------------------------------------- operators


def test_endian_flips_a_multi_byte_field():
    schema = one_field(type="u16")
    (mutant,), _ = mutants_of(schema, "ENDIAN")
    assert mutant.detail == "big -> little"
    assert applied(schema, mutant)["fields"][0]["endian"] == "little"


def test_endian_flips_an_existing_field_override():
    schema = one_field(type="f32", endian="little")
    (mutant,), _ = mutants_of(schema, "ENDIAN")
    assert applied(schema, mutant)["fields"][0]["endian"] == "big"


@pytest.mark.parametrize("ftype", ["u8", "s8", "u16[0:11]", "bool"])
def test_endian_on_a_type_it_cannot_affect_is_equivalent_not_generated(ftype):
    mutants, equivalent = mutants_of(one_field(type=ftype), "ENDIAN")
    assert mutants == []
    assert equivalent == {"ENDIAN": 1}


def test_sign_swaps_signedness_and_skips_an_encoded_field():
    schema = one_field(type="u16")
    (mutant,), _ = mutants_of(schema, "SIGN")
    assert applied(schema, mutant)["fields"][0]["type"] == "s16"

    mutants, equivalent = mutants_of(one_field(type="u8", encoding="bcd"), "SIGN")
    assert mutants == [] and equivalent == {"SIGN": 1}


def test_sign_reaches_an_enum_base():
    schema = one_field(type="enum", base="s16", values={0: "a", 1: "b"})
    (mutant,), _ = mutants_of(schema, "SIGN")
    assert applied(schema, mutant)["fields"][0]["base"] == "u16"


def test_width_moves_eight_bits_each_way():
    schema = one_field(type="u16")
    mutants, _ = mutants_of(schema, "WIDTH")
    assert sorted(applied(schema, m)["fields"][0]["type"] for m in mutants) == [
        "u24",
        "u8",
    ]
    (only,), _ = mutants_of(one_field(type="s8"), "WIDTH")
    assert applied(one_field(type="s8"), only)["fields"][0]["type"] == "s16"


def test_scale_add_and_drop_mod():
    schema = one_field(type="u16", mult=0.5, add=-40)
    (scale,), _ = mutants_of(schema, "SCALE")
    assert applied(schema, scale)["fields"][0]["mult"] == 5.0
    (add,), _ = mutants_of(schema, "ADD")
    assert applied(schema, add)["fields"][0]["add"] == -39
    drops, _ = mutants_of(schema, "DROP-MOD")
    remaining = sorted(
        tuple(
            sorted(k for k in applied(schema, m)["fields"][0] if k in ("mult", "add"))
        )
        for m in drops
    )
    assert remaining == [("add",), ("mult",)]


def test_add_is_inserted_on_a_numeric_field_without_one():
    schema = one_field(type="u8")
    (mutant,), _ = mutants_of(schema, "ADD")
    assert mutant.detail == "insert add: 1"
    assert applied(schema, mutant)["fields"][0]["add"] == 1


def test_transform_stages_are_mutated():
    schema = one_field(type="u16", transform=[{"div": 4}, {"add": 3}])
    (scale,), _ = mutants_of(schema, "SCALE")
    assert applied(schema, scale)["fields"][0]["transform"][0] == {"div": 40}
    drops, _ = mutants_of(schema, "DROP-MOD")
    assert len(drops) == 2


def test_bitrange_shifts_and_toggles_consume():
    schema = one_field(type="u8[0:3]")
    mutants, _ = mutants_of(schema, "BITRANGE")
    results = [applied(schema, m)["fields"][0] for m in mutants]
    assert {"type": "u8[1:4]", "name": "v"} in results
    assert any(r.get("consume") == 1 for r in results)

    top = one_field(type="u8[4:7]", consume=1)
    results = [applied(top, m)["fields"][0] for m in mutants_of(top, "BITRANGE")[0]]
    assert any(r["type"] == "u8[3:6]" for r in results), "shifts down at the top"
    assert any(r.get("consume") == 0 for r in results)


def test_consume_inside_a_byte_group_is_equivalent():
    schema = {
        "name": "t",
        "fields": [
            {"byte_group": {"size": 1, "fields": [{"name": "a", "type": "u8[0:3]"}]}}
        ],
    }
    mutants, equivalent = mutants_of(schema, "BITRANGE")
    assert [m.detail for m in mutants] == ["u8[0:3] -> u8[1:4]"]
    assert equivalent["BITRANGE"] == 1


def test_lookup_swaps_and_relabels():
    schema = one_field(type="u8", lookup=["off", "on", "fault"])
    mutants, _ = mutants_of(schema, "LOOKUP")
    tables = [applied(schema, m)["fields"][0]["lookup"] for m in mutants]
    assert ["on", "off", "fault"] in tables
    assert ["off", "on_mutant", "fault"] in tables
    assert len(mutants) == 4  # one swap, three relabels


def test_lookup_relabels_are_capped():
    schema = one_field(type="u8", lookup={i: "l%d" % i for i in range(100)})
    mutants, _ = mutants_of(schema, "LOOKUP")
    assert len(mutants) == 1 + sm.MAX_RELABELS


TLV = {
    "name": "t",
    "fields": [
        {
            "tlv": {
                "tag_size": 1,
                "cases": {
                    1: [{"name": "a", "type": "u8"}],
                    2: [{"name": "b", "type": "u16"}],
                },
            }
        }
    ],
}


def test_case_swaps_and_deletes():
    mutants, _ = mutants_of(TLV, "CASE")
    details = sorted(m.detail for m in mutants)
    assert details == [
        "tlv delete case 0x01",
        "tlv delete case 0x02",
        "tlv swap cases 0x01 <-> 0x02",
    ]
    swap = [m for m in mutants if "swap" in m.detail][0]
    cases = applied(TLV, swap)["fields"][0]["tlv"]["cases"]
    assert cases[1][0]["name"] == "b" and cases[2][0]["name"] == "a"


def test_case_swap_of_identical_bodies_is_equivalent():
    schema = copy.deepcopy(TLV)
    schema["fields"][0]["tlv"]["cases"][2] = [{"name": "a", "type": "u8"}]
    mutants, equivalent = mutants_of(schema, "CASE")
    assert all("swap" not in m.detail for m in mutants)
    assert equivalent == {"CASE": 1}


def test_legacy_match_cases():
    schema = {
        "name": "t",
        "fields": [
            {"name": "kind", "type": "u8"},
            {
                "type": "match",
                "on": "kind",
                "cases": [
                    {"case": 1, "fields": [{"name": "a", "type": "u8"}]},
                    {"case": 2, "fields": [{"name": "b", "type": "u8"}]},
                ],
            },
        ],
    }
    mutants, _ = mutants_of(schema, "CASE")
    swap = [m for m in mutants if "swap" in m.detail][0]
    cases = applied(schema, swap)["fields"][1]["cases"]
    assert [c["case"] for c in cases] == [2, 1]
    assert len(mutants) == 3


def test_flagged_moves_a_group_bit():
    schema = {
        "name": "t",
        "fields": [
            {"name": "flags", "type": "u8"},
            {
                "flagged": {
                    "field": "flags",
                    "groups": [{"bit": 2, "fields": [{"name": "a", "type": "u8"}]}],
                }
            },
        ],
    }
    (mutant,), _ = mutants_of(schema, "FLAGGED")
    assert applied(schema, mutant)["fields"][1]["flagged"]["groups"][0]["bit"] == 3


def test_drop_field_never_removes_the_last_field():
    schema = {
        "name": "t",
        "fields": [{"name": "a", "type": "u8"}, {"name": "b", "type": "u8"}],
    }
    (mutant,), _ = mutants_of(schema, "DROP-FIELD")
    assert [f["name"] for f in applied(schema, mutant)["fields"]] == ["b"]


def test_recursion_reaches_ports_definitions_and_nested_constructs():
    schema = {
        "name": "t",
        "ports": {"1": {"fields": [{"name": "p", "type": "u16"}]}},
        "definitions": {"hdr": {"fields": [{"name": "d", "type": "u16"}]}},
        "fields": [
            {"name": "obj", "type": "object", "fields": [{"name": "o", "type": "u16"}]},
            {
                "match": {
                    "length": 1,
                    "cases": {1: [{"name": "m", "type": "u16"}]},
                }
            },
        ],
    }
    mutants, _ = mutants_of(schema, "ENDIAN")
    labels = sorted(m.label.rsplit("/", 1)[-1] for m in mutants)
    assert labels == ["d", "m", "o", "p"]


# --------------------------------------------------------------------------- outcomes


WELL_CONSTRAINED = {
    "name": "tight",
    "endian": "big",
    "fields": [
        {"name": "a", "type": "u16", "div": 10},
        {"name": "b", "type": "s16"},
        {"name": "c", "type": "u8"},
    ],
    "test_vectors": [
        {
            "name": "all",
            "payload": "9234 FF38 C8",
            "source": "vendor-doc",
            "expected": {"a": 3742.8, "b": -200, "c": 200},
        }
    ],
}


def test_a_well_constrained_fixture_kills_every_mutant():
    report = sm.mutate_schema(WELL_CONSTRAINED)
    assert report["generated"] > 10
    assert report["survived"] == 0, report["survivors"]
    assert report["possibly_equivalent"] == 0
    assert report["unreached"] == 0
    assert report["score"] == 1.0
    assert report["drop_field"]["survived"] == 0


UNDER_CONSTRAINED = {
    "name": "loose",
    "endian": "big",
    "fields": [
        {"name": "zero", "type": "u16"},
        {
            "match": {
                "length": 1,
                "cases": {
                    1: [{"name": "one", "type": "u8"}],
                    2: [{"name": "two", "type": "s16"}],
                },
            }
        },
    ],
    "test_vectors": [
        {
            "name": "only case one",
            "payload": "0000 01 05",
            "source": "vendor-doc",
            "expected": {"zero": 0, "one": 5},
        }
    ],
}


def test_an_under_constrained_fixture_leaves_survivors_and_unreached_code():
    report = sm.mutate_schema(UNDER_CONSTRAINED)
    survivors = {(s["field"], s["operator"], s["kind"]) for s in report["survivors"]}
    # A zero is a byte palindrome: nothing tells big-endian from little.
    assert ("zero", "ENDIAN", "indistinguishable") in survivors
    unreached = {(u["field"], u["operator"]) for u in report["unreached_sites"]}
    assert ("match[0x02]/two", "ENDIAN") in unreached
    assert ("match[0x02]/two", "WIDTH") in unreached
    # Unreached mutants are listed, never scored.
    assert report["score"] == pytest.approx(
        report["killed"] / float(report["killed"] + report["survived"]), abs=1e-4
    )
    assert report["unreached"] >= 4


def test_a_change_to_a_key_no_vector_asserts_is_unasserted():
    schema = copy.deepcopy(WELL_CONSTRAINED)
    del schema["test_vectors"][0]["expected"]["c"]
    report = sm.mutate_schema(schema)
    kinds = {
        (s["field"], s["operator"]): (s["kind"], s.get("changed_keys"))
        for s in report["survivors"]
    }
    assert kinds[("c", "ADD")] == ("unasserted", ["c"])


def test_sign_in_the_lower_half_range_is_possibly_equivalent_not_scored():
    schema = copy.deepcopy(WELL_CONSTRAINED)
    schema["test_vectors"][0]["payload"] = "1234 FF38 C8"
    schema["test_vectors"][0]["expected"]["a"] = 466.0
    report = sm.mutate_schema(schema)
    assert report["by_operator"]["SIGN"].get("possibly-equivalent") == 1
    assert report["survived"] == 0 and report["score"] == 1.0


def test_an_unreferenced_internal_field_skips_value_mutants():
    schema = {
        "name": "t",
        "fields": [
            {"name": "_pad", "type": "u16"},
            {"name": "v", "type": "u8"},
        ],
        "test_vectors": [
            {"name": "x", "payload": "0000 07", "expected": {"v": 7}},
        ],
    }
    report = sm.mutate_schema(schema)
    operators = {s["operator"] for s in report["survivors"]}
    assert operators == set(), report["survivors"]
    equivalent = {op: c.get("equivalent", 0) for op, c in report["by_operator"].items()}
    assert equivalent["ENDIAN"] >= 1 and equivalent["SIGN"] >= 1
    # Its width still moves `v`, so WIDTH is generated and killed (two on _pad, one
    # on v).
    assert report["by_operator"]["WIDTH"]["killed"] == 3


def test_mutation_of_a_tlv_tag_modifier_is_equivalent():
    schema = {
        "name": "t",
        "fields": [
            {
                "tlv": {
                    "tag_fields": [{"name": "ch", "type": "u8"}],
                    "tag_key": "ch",
                    "cases": {"[1]": [{"name": "a", "type": "u8"}]},
                }
            }
        ],
    }
    mutants, equivalent = sm.generate_mutants(schema, ["ADD"])
    assert [m.label for m in mutants] == ["tlv[[1]]/a"]
    assert equivalent == {"ADD": 1}


def test_provenance_classes():
    assert sm.provenance([{"source": "generated"}, {}]) == "generated-only"
    assert sm.provenance([{"source": "generated"}, {"source": "vendor-codec"}]) == (
        "independent"
    )
    assert sm.provenance([{}]) == "unsourced"


# --------------------------------------------------------------------------- corpus


def test_conformance_fixtures_constrain_their_constructs(tmp_path):
    """A floor on the `_language-conformance` fixtures, bounded rather than pinned.

    Measured at 95.8% pooled over 236 scored mutants when this was written. The bound
    catches the harness breaking (a score of 0 or 1 across the board) or the fixtures
    losing their vectors, and leaves room for both to move.
    """
    out = tmp_path / "mutation.json"
    assert sm.main([str(CONFORMANCE), "--workers", "1", "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    totals = data["summary"]["totals"]
    assert totals["score"] >= 0.85, totals
    assert totals["killed"] >= 150, totals
    assert totals["timeouts"] == 0
    assert not data["summary"]["errors"]
    for report in data["schemas"]:
        assert not report["vectors_failing_original"], report["schema"]
