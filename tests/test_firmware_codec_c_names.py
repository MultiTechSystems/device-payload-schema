"""Every member of a generated struct has its own schema name. A field's C name is lower-cased and keyword-escaped
(to_c), so two different schema names can share one (`Seq` and `seq`, `switch` and `switch_`); they used to become
one member silently, and a name with no C spelling at all (`4_20_mA`) became a member that does not compile. Both are
refused, as the meta-schema front end refuses them for its wrapper of this header."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from generate_firmware_codec import FirmwareGenerator  # noqa: E402


def generate(fields):
    return FirmwareGenerator({"name": "probe", "endian": "big", "fields": fields}, "inline").generate()


@pytest.mark.parametrize("a, b, c", [
    ("Seq", "seq", "seq"),
    ("switch", "switch_", "switch_"),
    ("a+b", "a-b", "a_b"),
])
def test_two_names_with_one_c_spelling_are_refused(a, b, c):
    with pytest.raises(ValueError) as e:
        generate([{"name": a, "type": "u8"}, {"name": b, "type": "u8"}])
    assert str(e.value) == f"fields {a!r} and {b!r} are both {c!r} in C"


def test_a_clash_inside_a_construct_is_refused():
    fields = [{"name": "kind", "type": "u8"},
              {"match": {"field": "$kind", "cases": {1: [{"name": "Temp", "type": "s16"}],
                                                     2: [{"name": "temp", "type": "s16"}]}}}]
    with pytest.raises(ValueError, match="fields 'Temp' and 'temp' are both 'temp' in C"):
        generate(fields)


def test_one_name_in_several_cases_is_still_one_member():
    fields = [{"name": "kind", "type": "u8"},
              {"match": {"field": "$kind", "cases": {1: [{"name": "temp", "type": "s16"}],
                                                     2: [{"name": "temp", "type": "s16"}]}}}]
    assert generate(fields).count("int16_t temp;") == 1


def test_a_name_with_no_c_spelling_is_refused():
    with pytest.raises(ValueError) as e:
        generate([{"name": "4_20_mA", "type": "u16"}])
    assert str(e.value) == "field '4_20_mA' has no C spelling ('4_20_ma')"


def test_the_ports_are_separate_structs():
    schema = {"name": "probe", "endian": "big", "ports": {
        1: {"direction": "uplink", "fields": [{"name": "Seq", "type": "u8"}]},
        2: {"direction": "uplink", "fields": [{"name": "seq", "type": "u8"}]}}}
    FirmwareGenerator(schema, "inline").generate()
