"""`valid_range` -> `_quality` in the generated TS013 codec.

PS-129/PS-130 constrain the range, PS-131 requires an out-of-range value to be
flagged, PS-132 requires it to pass through unmodified, and PS-182 allows `_quality`
only when a field actually carried a range.

The generator implemented none of it, so every schema with `valid_range` produced a
codec that silently dropped the quality object the interpreters report. That was
visible as 12 vectors' worth of difference in tools/crossvalidate_js_json.py once the
harness started comparing nested values at all.

Each behavioural test asserts the interpreter and the generated codec agree, rather
than asserting the codec alone - the point of the codec is to be a drop-in for the
interpreter.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter  # noqa: E402
from validate_schema import is_encode_vector  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js is not installed"
)


def run_codec(schema, payload_hex):
    """Decode through the generated codec, returning TS013 {data, warnings}."""
    js = TS013Generator(schema).generate()
    octets = json.dumps(list(bytes.fromhex(payload_hex.replace(" ", ""))))
    driver = (
        js
        + f"\nvar _r = decodeUplink({{ bytes: {octets}, fPort: 1 }});"
        + "\nconsole.log(JSON.stringify({data: _r.data, warnings: _r.warnings}));"
    )
    out = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def run_interpreter(schema, payload_hex):
    result = SchemaInterpreter(schema).decode(
        bytes.fromhex(payload_hex.replace(" ", ""))
    )
    assert not result.errors, result.errors
    return {"data": result.data, "warnings": result.warnings}


def schema_with(valid_range="[5, 30]", extra=""):
    body = "name: t\nendian: big\nfields:\n  - name: temperature\n    type: u8\n"
    if valid_range is not None:
        body += f"    valid_range: {valid_range}\n"
    return yaml.safe_load(body + extra)


class TestAgreement:
    @pytest.mark.parametrize(
        "payload,expected",
        [
            ("14", "good"),          # 20, inside
            ("05", "good"),          # 5, the low boundary
            ("1E", "good"),          # 30, the high boundary
            ("04", "out_of_range"),  # 4, below
            ("1F", "out_of_range"),  # 31, above
        ],
    )
    def test_flag_matches_the_interpreter(self, payload, expected):
        schema = schema_with()
        js = run_codec(schema, payload)
        py = run_interpreter(schema, payload)
        assert js["data"]["_quality"]["temperature"] == expected
        assert py["data"]["_quality"] == js["data"]["_quality"]

    def test_boundaries_are_good(self):
        # PS-182: "values at range boundaries (min, max) are considered good".
        for payload in ("05", "1E"):
            assert run_codec(schema_with(), payload)["data"]["_quality"][
                "temperature"
            ] == "good"

    def test_out_of_range_value_is_not_modified(self):
        # PS-132: flagged, but passed through as-is.
        js = run_codec(schema_with(), "1F")
        py = run_interpreter(schema_with(), "1F")
        assert js["data"]["temperature"] == 31
        assert py["data"]["temperature"] == 31

    def test_warning_text_matches_the_interpreter(self):
        js = run_codec(schema_with(), "04")
        py = run_interpreter(schema_with(), "04")
        assert js["warnings"] == ["temperature: value 4 outside valid range [5, 30]"]
        assert py["warnings"] == js["warnings"]

    def test_in_range_emits_no_warning(self):
        assert run_codec(schema_with(), "14")["warnings"] == []

    def test_quality_is_absent_without_a_range(self):
        # PS-182: only when at least one field declares valid_range.
        schema = schema_with(valid_range=None)
        js = run_codec(schema, "14")
        py = run_interpreter(schema, "14")
        assert "_quality" not in js["data"]
        assert "_quality" not in py["data"]

    def test_a_range_on_a_computed_field_is_checked(self):
        # The interpreter checks computed fields too, and after the arithmetic.
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: raw\n    type: u8\n"
            "  - name: scaled\n    type: number\n"
            "    compute:\n      op: mul\n      a: $raw\n      b: 10\n"
            "    valid_range: [0, 100]\n"
        )
        # raw 20 -> scaled 200, out of range only after the multiply. `compute` takes
        # a: and b:, not operands: - written the other way the field computes to 0 and
        # the test passes for the wrong reason.
        js = run_codec(schema, "14")
        py = run_interpreter(schema, "14")
        assert js["data"]["_quality"]["scaled"] == "out_of_range"
        assert py["data"]["_quality"] == js["data"]["_quality"]

    def test_range_is_applied_after_modifiers(self):
        # PS-131: "range checking is applied AFTER all arithmetic transformations".
        # The raw byte 200 is outside [0, 30]; 20 after the divide is inside, so a
        # `good` flag can only come from checking the modified value.
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: temperature\n    type: u8\n    div: 10\n"
            "    valid_range: [0, 30]\n"
        )
        js = run_codec(schema, "C8")
        py = run_interpreter(schema, "C8")
        assert js["data"]["temperature"] == 20
        assert js["data"]["_quality"]["temperature"] == "good"
        assert py["data"]["_quality"] == js["data"]["_quality"]

        # And the same range against an unmodified field does flag 200.
        unscaled = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: temperature\n    type: u8\n    valid_range: [0, 30]\n"
        )
        assert (
            run_codec(unscaled, "C8")["data"]["_quality"]["temperature"]
            == "out_of_range"
        )


class TestTheSchemasThatNeededIt:
    """The two device schemas whose quality output the codec used to drop."""

    @pytest.mark.parametrize(
        "schema_path,expected_fields",
        [
            (
                "schemas/devices/mclimate/vicki.yaml",
                {
                    "targetTemperature",
                    "sensorTemperature",
                    "relativeHumidity",
                    "batteryVoltage",
                    "valveOpenness",
                },
            ),
            (
                "schemas/devices/rakwireless/qingping.yaml",
                {"temperature", "humidity", "co2", "battery"},
            ),
        ],
    )
    def test_every_vector_agrees_on_quality(self, schema_path, expected_fields):
        path = REPO_ROOT / schema_path
        schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        seen = set()
        for vector in schema["test_vectors"]:
            if is_encode_vector(vector):
                continue
            payload = str(vector["payload"])
            js = run_codec(schema, payload)
            py = run_interpreter(schema, payload)
            assert js["data"].get("_quality") == py["data"].get("_quality"), (
                f"{vector['name']}: codec {js['data'].get('_quality')} != "
                f"interpreter {py['data'].get('_quality')}"
            )
            seen |= set((js["data"].get("_quality") or {}).keys())
        # Pinned so the fields cannot quietly stop being reported.
        assert seen == expected_fields

    def test_vicki_reports_an_out_of_range_target(self):
        # keepalive_min_values decodes a target of 0 against a range of [5, 30] - the
        # corpus vector that actually exercises the flag.
        schema = yaml.safe_load(
            (REPO_ROOT / "schemas/devices/mclimate/vicki.yaml").read_text(
                encoding="utf-8"
            )
        )
        js = run_codec(schema, "01 00 00 00 00 00 00 00 00")
        assert js["data"]["_quality"]["targetTemperature"] == "out_of_range"
        assert js["warnings"] == [
            "targetTemperature: value 0 outside valid range [5, 30]"
        ]
