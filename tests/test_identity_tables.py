"""Per-definition tables are not keyed by object address alone (CR-2026-141's audit).

The spec prototype keyed its PS-546 SIBLINGS table by `id()` of a field definition, so
a definition allocated at a freed address inherited another schema's siblings: an
intermittent false PS-550. The same shape was here: the decode tables PS-475 and PS-523
keep (`_guard_else`, `_pre_lookup`, `_lookup_numbers`) and the PS-168 message's given
value were keyed by `id()`, and a repeat's element is a dict built afresh for each
decode of the repeat (`repeat_members`), so its entries outlived it. The generated codec
named a variable after `id(field) % 100000`, so one schema generated different code on
each run.
"""

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import IdentityTable, SchemaInterpreter  # noqa: E402

SCHEMA = {"name": "t", "fields": [{"name": "n", "type": "u8"}]}


def computed():
    return {"name": "_v", "type": "number", "ref": "$n",
            "guard": {"when": [{"field": "$n", "gt": 100}], "else": 0}}


def ranged():
    return {"name": "w", "type": "u8", "valid_range": [0, 10], "out_of_range": "omit"}


def same_address(address, make, tries=2000):
    """A new definition at `address`, if the allocator hands it out again; kept alive."""
    held = []
    for _ in range(tries):
        candidate = make()
        if id(candidate) == address:
            return candidate, held
        held.append(candidate)
    return make(), held


def test_a_freed_definition_does_not_leak_its_failed_guard():
    interp = SchemaInterpreter(SCHEMA)
    assert interp.decode(bytes([1])).success
    first = computed()
    interp._decode_computed_field(first)     # n = 1: the guard fails, `else` is reported
    address = id(first)
    del first
    second, _held = same_address(address, ranged)
    # 99 is outside [0, 10], so the field is omitted. With the table keyed by id() and
    # `second` at the freed address, the failed guard read as `second`'s and its
    # valid_range was never compared.
    assert interp._range_omits(second, 99) is True


def test_the_given_value_belongs_to_the_definition_that_recorded_it():
    interp = SchemaInterpreter(SCHEMA)
    first = {"name": "a", "type": "u8"}
    interp._reverse_modifiers(7, first)
    address = id(first)
    del first
    second, _held = same_address(address, lambda: {"name": "b", "type": "u8"})
    assert interp._given_value(second, 300) == 300


def test_identity_table_matches_by_identity_and_holds_the_key():
    table = IdentityTable()
    key = {"a": 1}
    table[key] = 5
    assert key in table and table.get(key) == 5
    assert {"a": 1} not in table             # an equal dict is another definition
    assert table.pop(key) == 5 and key not in table
    table.add(key)
    table.discard(key)
    assert key not in table


def test_the_generated_codec_does_not_depend_on_object_addresses():
    schema = {"name": "t", "fields": [
        {"name": "o", "type": "object", "fields": [
            {"name": "o", "type": "object", "fields": [{"name": "x", "type": "u8"}]}]}]}
    first = TS013Generator(copy.deepcopy(schema)).generate()
    second = TS013Generator(copy.deepcopy(schema)).generate()
    assert first == second
