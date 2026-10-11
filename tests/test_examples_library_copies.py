"""The three examples that used to reach the schema library by cross-document `$ref`.

A pointer naming another file is OPTIONAL (PS-462) and every interpreter here rejects
it; `rename:` and `prefix:` beside a `$ref` are not in the specification at all. So
these examples now carry their fields in the document, and were skipped by no
validation target once they did. What they gave up is being checked against the
library, which nothing else does for `schemas/library/sensors/` (it has no vectors to
compose). These tests restore that, by resolving the library through the authoring-time
preprocessor and comparing, and they keep the one remaining `_resolved` sibling equal
to its source.
"""

import copy
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import expand_refs  # noqa: E402
from schema_preprocessor import SchemaPreprocessor  # noqa: E402

EXAMPLES = REPO_ROOT / "examples"
LIB = "schemas/library/"


def _load(name):
    with open(EXAMPLES / name, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _from_library(entries, tmp_path):
    """Resolve a field list of cross-document references with the preprocessor."""
    source = tmp_path / "refs.yaml"
    source.write_text(yaml.safe_dump({"name": "refs", "fields": entries}), encoding="utf-8")
    processed = SchemaPreprocessor(library_paths=[REPO_ROOT]).process(source)
    return processed["fields"]


def _ref(path, definition, **extra):
    entry = {"$ref": "%s%s#/definitions/%s" % (LIB, path, definition)}
    entry.update(extra)
    return entry


def test_env_sensor_definitions_are_the_librarys(tmp_path):
    schema = _load("env_sensor_with_lib.yaml")
    library = {
        "msg_type_header": "common/headers.yaml",
        "temp_humidity": "profiles/env-sensor.yaml",
        "battery_mv": "sensors/power.yaml",
        "battery_pct_from_mv": "sensors/power.yaml",
    }
    assert sorted(schema["definitions"]) == sorted(library)
    for name, path in library.items():
        assert schema["definitions"][name]["fields"] == _from_library(
            [_ref(path, name)], tmp_path
        ), name


def test_env_sensor_resolved_sibling_is_its_source_expanded():
    expanded, errors = expand_refs(_load("env_sensor_with_lib.yaml"))
    assert errors == []
    resolved = _load("env_sensor_with_lib_resolved.yaml")
    expanded.pop("definitions")
    assert expanded == resolved


def test_lib_sensors_test_fields_are_the_librarys(tmp_path):
    entries = [
        _ref("sensors/environmental.yaml", "temperature_c_div10"),
        _ref("sensors/environmental.yaml", "humidity_pct_half"),
        _ref("sensors/environmental.yaml", "pressure_hpa_div10"),
        _ref("sensors/environmental.yaml", "co2_ppm"),
        _ref("sensors/power.yaml", "battery_pct"),
        _ref("sensors/distance.yaml", "distance_mm"),
        _ref("sensors/motion.yaml", "pir_byte"),
        _ref("sensors/position.yaml", "latitude_3byte"),
        _ref("sensors/position.yaml", "longitude_3byte"),
    ]
    assert _load("lib_sensors_test.yaml")["fields"] == _from_library(entries, tmp_path)


def test_multi_sensor_fields_are_the_librarys_renamed(tmp_path):
    env = "sensors/environmental.yaml"
    entries = [
        _ref("common/headers.yaml", "msg_type_header"),
        _ref(env, "temperature_c_div10", rename={"temperature": "indoor_temp"}),
        _ref(env, "temperature_c_div10", rename={"temperature": "outdoor_temp"}),
        _ref(env, "humidity_pct_half", prefix="indoor_"),
        _ref(env, "humidity_pct_half", prefix="outdoor_"),
        _ref("sensors/power.yaml", "battery_mv"),
        _ref("sensors/power.yaml", "battery_pct_from_mv"),
    ]
    fields = _load("multi_sensor_rename.yaml")["fields"]
    # The one thing the example adds: an IPSO instance per sensor, so the two 3303 and
    # the two 3304 fields do not share a path (IL-180).
    instances = {
        f["name"]: f["ipso"].get("instance") for f in fields if "instance" in f.get("ipso", {})
    }
    assert instances == {
        "indoor_temp": 0,
        "outdoor_temp": 1,
        "indoor_humidity": 0,
        "outdoor_humidity": 1,
    }
    stripped = copy.deepcopy(fields)
    for field in stripped:
        field.get("ipso", {}).pop("instance", None)
    assert stripped == _from_library(entries, tmp_path)


def test_no_example_reaches_another_document():
    """Every example is a schema every implementation decodes as written."""
    offenders = []

    def walk(node, where):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if ref is not None and not str(ref).startswith("#/definitions/"):
                offenders.append("%s: %s" % (where, ref))
            if ref is not None and set(node) != {"$ref"}:
                offenders.append("%s: %s beside $ref" % (where, sorted(set(node) - {"$ref"})))
            for value in node.values():
                walk(value, where)
        elif isinstance(node, list):
            for item in node:
                walk(item, where)

    for path in sorted(EXAMPLES.glob("*.yaml")):
        walk(_load(path.name), path.name)
    assert not offenders, "\n".join(offenders)
