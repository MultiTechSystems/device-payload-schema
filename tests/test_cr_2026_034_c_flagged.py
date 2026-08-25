"""CR-2026-034: `flagged` in the C interpreter, and in the harness that measures it.

The construct CR-2026-033 named as the largest remaining gap. Attempted vectors went
453 -> 488, all passing, and `flagged` is no longer a reason a corpus schema cannot be
reached.

The representation reuses what `match` and `tlv` already have: `match_var` holds the name of
the field carrying the mask, and each group is a `case_def_t` whose `match_value` is its bit
position and whose `field_start`/`field_count` point at a body placed above `field_count` -
the invariant CR-2026-033 established, for the same reason.

**The mask field must declare `var_name`.** This interpreter records a value in its variable
table only where a field declares one, while a YAML `flagged` refers to a field *by name* -
so no corpus schema carries `var:` for it and the harness patches it in when building. A
builder that forgets gets `SCHEMA_ERR_MATCH`, not a silent decode of nothing: `var_get`
returns 0 for a miss, which for a bitmask is indistinguishable from "no bits set", so a new
`var_has()` tells the two apart. The reference interpreter raises in the same case
("Flagged field reference not found"), which is what this matches.

Six of the 34 flagged schemas are now built; the rest are blocked by constructs the harness
does not build - `transform` (26), `bitfield_string` (24), computed fields - not by
`flagged`. That distinction is why the skip reasons name a side, and this CR also corrected
a message that did not: `no constructor for type 'u32le16'` read as a C gap when the
interpreter has `FIELD_TYPE_U32LE16` and decodes it perfectly well. It now says which side
each limit is on.

One slip of mine, and the same shape as ever: CR-2026-033's test anchored on
`"FIELD_TYPE_TLV\\n} field_type_t;"`, which pinned TLV as the *last* enum member - incidental,
and broken the moment this CR appended `FIELD_TYPE_FLAGGED` after it. It compares positions
now.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HEADER = REPO_ROOT / "include" / "schema_interpreter.h"
HARNESS = REPO_ROOT / "tools" / "c-corpus-harness.py"
CORPUS = REPO_ROOT / "schemas" / "devices"

#: What this CR reached. A bound - closing `transform` or `bitfield_string` raises it.
ATTEMPTED_FLOOR = 488


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("c34") / "r.json"
    done = subprocess.run([sys.executable, str(HARNESS), "--json", str(out)],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-2000:]
    return json.loads(out.read_text())


class TestTheInterpreterDecodesFlagged:
    def test_the_field_type_exists(self):
        assert "FIELD_TYPE_FLAGGED" in HEADER.read_text()

    def test_it_is_after_the_parse_sentinel(self):
        text = HEADER.read_text()
        assert text.index("FIELD_TYPE_UNKNOWN,") < text.index("FIELD_TYPE_FLAGGED")

    def test_there_is_a_constructor_and_a_group_adder(self):
        text = HEADER.read_text()
        assert "field_flagged(" in text
        assert "field_add_flagged_group(" in text

    def test_a_group_bit_is_bounded(self):
        text = HEADER.read_text()
        start = text.index("static inline bool field_add_flagged_group(")
        assert "bit > 63" in text[start:start + 400]

    def test_groups_are_walked_in_the_order_added(self):
        """PS-160: the order they are written is the order their bytes appear."""
        text = HEADER.read_text()
        start = text.index("if (field->type == FIELD_TYPE_FLAGGED) {")
        assert "PS-160" in text[max(0, start - 900):start]

    def test_a_clear_bit_consumes_nothing(self):
        text = HEADER.read_text()
        start = text.index("if (field->type == FIELD_TYPE_FLAGGED) {")
        window = text[start:start + 1800]
        assert "& 1) == 0) continue" in window, window[:400]

    def test_bodies_are_bounded_by_the_array_not_field_count(self):
        """Same invariant as a tlv case body, for the same reason."""
        text = HEADER.read_text()
        start = text.index("if (field->type == FIELD_TYPE_FLAGGED) {")
        window = text[start:start + 2200]
        assert "field_idx >= SCHEMA_MAX_FIELDS" in window
        assert "field_idx >= schema->field_count" not in window


class TestAMissingMaskIsAnErrorNotZero:
    """`var_get` returns 0 for a miss, which for a bitmask means "decode nothing"."""

    def test_var_has_exists(self):
        assert "static inline bool var_has(" in HEADER.read_text()

    def test_the_construct_checks_it_before_reading(self):
        text = HEADER.read_text()
        start = text.index("if (field->type == FIELD_TYPE_FLAGGED) {")
        window = text[start:start + 900]
        assert "var_has(&vars" in window
        assert window.index("var_has(&vars") < window.index("var_get(&vars")

    def test_it_reports_rather_than_returning_empty(self):
        text = HEADER.read_text()
        start = text.index("if (field->type == FIELD_TYPE_FLAGGED) {")
        window = text[start:start + 900]
        assert "SCHEMA_ERR_MATCH" in window
        assert "flagged field reference not found" in window

    def test_the_constructor_documents_the_var_name_requirement(self):
        """Read from the doc comment's own start, not a byte count before the function.

        Two slips in one assertion, both mine. A 1200-character window captured the tail
        of the comment and missed its opening; and the phrase searched for is broken across
        a line in the wrapped comment, so it never appears contiguously. Read the comment
        block and normalise its whitespace.
        """
        text = HEADER.read_text()
        start = text.index("static inline field_def_t field_flagged(")
        comment_start = text.rindex("/*", 0, start)
        near = " ".join(text[comment_start:start].replace("*", " ").split())
        assert "var_name" in near, near[:300]
        assert "silent decode of nothing" in near, near[:300]


class TestTheHarnessBuildsFlaggedInStep:
    def test_it_emits_a_flagged(self):
        assert "field_add_flagged_group" in HARNESS.read_text()

    def test_it_patches_var_name_onto_the_mask_field(self):
        text = HARNESS.read_text()
        assert "f.var_name" in text

    def test_it_refuses_a_mask_field_that_is_not_there(self):
        text = HARNESS.read_text()
        assert "not a field here" in text

    def test_a_flagged_schema_is_actually_built(self):
        """`0 differ` proves nothing if no flagged group ever fires."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("h", HARNESS)
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)

        built = 0
        for path in sorted(CORPUS.rglob("*.yaml")):
            text = path.read_text()
            if "flagged:" not in text:
                continue
            try:
                schema = yaml.safe_load(text)
            except yaml.YAMLError:
                continue
            if not isinstance(schema, dict) or not schema.get("test_vectors"):
                continue
            source, _ = harness.schema_source(0, schema)
            if source and any("field_flagged(" in line for line in source):
                built += 1
        assert built >= 6, built

    def test_a_built_flagged_schema_sets_the_var_name(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("h", HARNESS)
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)

        for path in sorted(CORPUS.rglob("*.yaml")):
            if "flagged:" not in path.read_text():
                continue
            try:
                schema = yaml.safe_load(path.read_text())
            except yaml.YAMLError:
                continue
            if not isinstance(schema, dict) or not schema.get("test_vectors"):
                continue
            source, _ = harness.schema_source(0, schema)
            if not source or not any("field_flagged(" in l for l in source):
                continue
            assert any("var_name" in l for l in source), path.name
            return
        pytest.fail("no flagged schema was built, so nothing was checked")


class TestTheSkipReasonsNameASide:
    """A harness limit reported as a C gap is the misattribution to avoid."""

    def test_flagged_is_no_longer_a_reason(self, report):
        assert not any("flagged" in r for r in report["skips"]), sorted(report["skips"])

    def test_a_type_the_interpreter_has_says_so(self, report):
        reasons = [r for r in report["skips"] if "u32le16" in r]
        assert reasons, sorted(report["skips"])
        assert "the interpreter has the type" in reasons[0], reasons

    def test_a_type_the_interpreter_lacks_does_not_claim_otherwise(self, report):
        reasons = [r for r in report["skips"] if "repeat" in r]
        assert reasons, sorted(report["skips"])
        assert "the interpreter has the type" not in reasons[0], reasons


class TestTheMeasurementImproved:
    def test_every_attempted_vector_passes(self, report):
        assert report["failures"] == [], report["failures"][:6]

    def test_it_attempted_at_least_what_this_cr_reached(self, report):
        assert report["attempted"] >= ATTEMPTED_FLOOR, report["attempted"]

    def test_the_accounting_still_holds(self, report):
        assert report["attempted"] + report["skipped_vectors"] == report["corpus_vectors"]

    def test_the_c_tests_and_selftests_still_pass(self):
        for target in ("test-c", "selftest"):
            done = subprocess.run(["make", target], cwd=REPO_ROOT,
                                  capture_output=True, text=True)
            assert done.returncode == 0, (target, done.stdout[-1500:], done.stderr[-1500:])
        combined = done.stdout + done.stderr
        assert re.search(r"ALL \d+ SELFTESTS PASSED", combined), combined[-400:]
