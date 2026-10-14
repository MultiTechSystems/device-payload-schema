"""CR-2026-178 (PS-178, PS-596): `_meta` carries `display_unit` beside the SenML `unit`.

The cases live in `tests/cr-2026-178-cases.yaml`, read by every implementation that produces
`_meta`; the TS013 codec carries none.
"""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

CASES = yaml.safe_load((ROOT / "tests" / "cr-2026-178-cases.yaml").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_display_unit(case):
    schema = yaml.safe_load(case["schema"])
    res = SchemaInterpreter(schema).interpret(bytes.fromhex(case["payload"]))
    assert res.success, res.errors
    entry = res.data["_meta"]["fields"]
    for key in case["path"].split("."):
        entry = entry[key]
    for key in ("unit", "display_unit"):
        if case[key] == "absent":
            assert key not in entry
        else:
            assert entry[key] == case[key]
    if "display_unit" in entry:
        keys = list(entry)
        assert keys.index("display_unit") == keys.index("unit") + 1
