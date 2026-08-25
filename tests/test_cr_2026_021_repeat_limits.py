"""CR-2026-021: `max` and `min` on a repeat, honoured by the generated codec too.

The last gap CR-2026-019 recorded. The four interpreters clamp a `count` to `max`, guard
the byte_length and until-end loops with it, and fail a decode that produces fewer than
`min` elements. The TS013 generator read neither: it had a no-progress guard, which stops
a zero-width member but is not a ceiling, so it ran to the end of the payload and produced
more records than any interpreter would - and it returned a short array where the
interpreters refuse one.

**The reason this was invisible is the more serious half of this CR.**
`tools/vector-verdicts.py` compared field values with `if not values_match(...)`.
`values_match` returns `(ok, detail)`, and a two-element tuple is always truthy, so that
condition could never hold. From the day the second return value was added until now the
tool compared *key presence only*: every value the two conformance paths disagreed on was
reported as a pass, and its "0 vectors where the two paths disagree" line meant far less
than it read. The `max` gap is exactly the shape of defect it hid - the same keys, the
wrong number of records under them.

Fixing the comparison first, then the generator, is what these tests pin: the fixtures
`repeat-max.yaml` and `repeat-max-count.yaml` fail on the generated path with the
comparison fixed and the generator not, which is the state this CR passed through.
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
from validate_schema import values_match  # noqa: E402

FIXTURES = REPO_ROOT / "schemas" / "devices" / "_language-conformance"

#: The interpreters' ceiling where a schema declares none.
DEFAULT_MAX = 1000


def repeat_schema(**overrides):
    field = {"name": "items", "type": "repeat",
             "fields": [{"name": "v", "type": "u8"}]}
    field.update(overrides)
    return {"name": "probe", "endian": "big", "fields": [field]}


def decode(schema, payload):
    return SchemaInterpreter(schema).decode(bytes.fromhex(payload))


def decode_js(schema, payload):
    js = TS013Generator(schema).generate()
    body = ",".join(str(b) for b in bytes.fromhex(payload))
    harness = (
        js
        + f"\nvar _r = decodeUplink({{fPort: 1, bytes: [{body}]}});"
        + "\nconsole.log(JSON.stringify("
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


class TestTheComparisonActuallyCompares:
    """The tooling defect that hid the gap. Guarded so it cannot come back."""

    def test_values_match_returns_a_pair(self):
        """Why `if not values_match(...)` was always false: the tuple is truthy."""
        outcome = values_match(1, 2)
        assert isinstance(outcome, tuple) and len(outcome) == 2
        assert bool(outcome) is True, "a non-empty tuple is truthy; hence the defect"

    def test_a_length_mismatch_is_reported(self):
        ok, detail = values_match([{"v": 1}], [{"v": 1}, {"v": 2}])
        assert not ok
        assert "length" in detail

    def test_the_verdicts_tool_unpacks_it(self):
        """A regression here would silently stop comparing every value again."""
        source = (REPO_ROOT / "tools" / "vector-verdicts.py").read_text()
        assert "if not values_match(" not in source, (
            "vector-verdicts.py is testing the tuple again, so no value is compared"
        )
        assert "ok, detail = values_match(" in source

    def test_the_verdicts_tool_reports_a_real_mismatch(self):
        """End to end: a schema whose paths differ must be counted as a disagreement."""
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader

        loader = SourceFileLoader("verdicts", str(REPO_ROOT / "tools" / "vector-verdicts.py"))
        spec = spec_from_loader("verdicts", loader)
        verdicts = module_from_spec(spec)
        loader.exec_module(verdicts)
        ok, detail = verdicts.matches({"items": [{"v": 10}]},
                                      {"items": [{"v": 10}, {"v": 20}]})
        assert not ok, "the tool still blesses a value mismatch"
        assert "items" in detail


class TestMaxIsACeiling:
    @pytest.mark.parametrize("bound", [{"until": "end"}, {"count": 4}])
    def test_it_caps_both_spellings(self, bound):
        schema = repeat_schema(max=2, **bound)
        assert decode(schema, "0A141E28").data == {
            "items": [{"v": 10}, {"v": 20}]}

    @pytest.mark.parametrize("bound", [{"until": "end"}, {"count": 4}])
    def test_the_generated_codec_caps_them_too(self, bound):
        schema = repeat_schema(max=2, **bound)
        assert decode_js(schema, "0A141E28")["data"] == {
            "items": [{"v": 10}, {"v": 20}]}

    def test_a_payload_within_the_ceiling_is_untouched(self):
        schema = repeat_schema(until="end", max=2)
        assert decode(schema, "0A14").data == {"items": [{"v": 10}, {"v": 20}]}
        assert decode_js(schema, "0A14")["data"] == {
            "items": [{"v": 10}, {"v": 20}]}

    def test_no_max_means_the_default_not_no_limit(self):
        """Both paths run to the payload's end well below 1000."""
        schema = repeat_schema(until="end")
        expected = {"items": [{"v": 10}, {"v": 20}, {"v": 30}, {"v": 40}]}
        assert decode(schema, "0A141E28").data == expected
        assert decode_js(schema, "0A141E28")["data"] == expected

    def test_the_default_ceiling_is_emitted(self):
        """A schema with no `max` still gets a bound, so a corrupt payload terminates."""
        js = TS013Generator(repeat_schema(until="end")).generate()
        assert f"_max = {DEFAULT_MAX}" in js

    def test_a_declared_ceiling_is_emitted(self):
        js = TS013Generator(repeat_schema(until="end", max=7)).generate()
        assert "_max = 7" in js


class TestMinIsAFloor:
    def test_too_few_elements_fail_the_decode(self):
        schema = repeat_schema(until="end", min=3)
        result = decode(schema, "0A14")
        assert result.errors, "two elements must not satisfy a minimum of three"
        assert "minimum is 3" in result.errors[0]

    def test_the_generated_codec_fails_the_same_way(self):
        schema = repeat_schema(until="end", min=3)
        outcome = decode_js(schema, "0A14")
        assert outcome["errors"], "the generated codec returned a short array"
        assert "minimum is 3" in outcome["errors"][0]

    def test_enough_elements_pass_on_both(self):
        schema = repeat_schema(until="end", min=2)
        expected = {"items": [{"v": 10}, {"v": 20}]}
        assert decode(schema, "0A14").data == expected
        assert decode_js(schema, "0A14")["data"] == expected

    def test_no_min_emits_no_check(self):
        """The default is zero, which nothing can fall below."""
        js = TS013Generator(repeat_schema(until="end")).generate()
        assert "minimum is" not in js

    def test_a_zero_min_emits_no_check_either(self):
        js = TS013Generator(repeat_schema(until="end", min=0)).generate()
        assert "minimum is" not in js


class TestTheFixtures:
    """Corpus coverage, which is what carries this to the Go, Java and C# runners."""

    @pytest.mark.parametrize("filename", ["repeat-max.yaml", "repeat-max-count.yaml"])
    def test_it_exists_and_declares_max(self, filename):
        schema = yaml.safe_load((FIXTURES / filename).read_text())
        maxima = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "repeat":
                    maxima.append(node.get("max"))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(schema)
        assert maxima and all(isinstance(m, int) for m in maxima), maxima

    def test_between_them_both_bound_spellings_are_covered(self):
        """A single fixture would let the fix pass on one spelling's behaviour."""
        bounds = set()
        for filename in ("repeat-max.yaml", "repeat-max-count.yaml"):
            schema = yaml.safe_load((FIXTURES / filename).read_text())

            def walk(node):
                if isinstance(node, dict):
                    if node.get("type") == "repeat":
                        bounds.update(
                            k for k in ("count", "byte_length", "until") if k in node)
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for item in node:
                        walk(item)

            walk(schema)
        assert {"count", "until"} <= bounds, bounds

    def test_min_is_deliberately_not_a_fixture(self):
        """It fails the decode, and no vector kind expects a failure.

        Recorded so the absence reads as a decision rather than an oversight; the unit
        tests above cover it on both Python-side paths.
        """
        for filename in ("repeat-max.yaml", "repeat-max-count.yaml"):
            text = (FIXTURES / filename).read_text()
            assert "min:" not in text, filename
        assert "min" in (FIXTURES / "repeat-max.yaml").read_text(), (
            "the fixture should still explain why min has no vector"
        )
