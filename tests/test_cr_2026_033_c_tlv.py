"""CR-2026-033: `tlv` in the C interpreter, and in the harness that measures it.

CR-2026-032 built the harness first so this would be checkable rather than asserted. It
was: **attempted vectors went from 50 to 453, and all 453 pass.** `tlv` is no longer a
reason a corpus schema cannot be reached.

The representation, and why:

- **A tag is packed into `case_def_t.match_value`** - a one-byte tag is its own value, a
  two-component tag is `(first << 8) | second`. Every tag component in the corpus is a `u8`
  and none has more than two, so the packing is exact rather than a hash. `tlv_tag_parts`
  records which form was meant, so a builder and the interpreter cannot disagree silently,
  and `schema_tlv_tag()` is the one place the encoding lives.
- **Case bodies are placed above `field_count`**, through the new `schema_place_field()`.
  The existing `match` convention adds them as counted fields after the construct, which
  means the top-level loop decodes each of them a *second* time from wherever the position
  happens to be - `src/test_comprehensive.c`'s match test passes only because it asserts
  `field_count >= 2` rather than what was decoded. A tlv cannot be built that way at all.
- **`merge` is not represented.** This interpreter reports a flat field list, which is
  `merge: true`, and no corpus schema sets `merge`. One that did would need a channel list
  `decode_result_t` cannot hold, so the harness skips it rather than reporting a wrong shape.
- **`unknown: raw` is not supported**, for the same reason: the captured bytes need
  somewhere to go. `skip` and `error` are.

What the C interpreter still cannot do, now measured rather than assumed: 34 schemas need
`flagged`, 24 a `bitfield_string`, 15 more cases than `SCHEMA_MAX_CASES` allows, 3 `repeat`.
That last group is the fixed-size boundary and is the honest limit of a firmware-tier
interpreter: `sizeof(schema_t)` is already 51 KB because every `field_def` carries
`cases[16]` and `lookup[16]` unconditionally, and raising the limits to fit mla20's 67
fields would put it past 110 KB.

**Three bugs found on the way, and all three were mine, not the interpreter's.** The guard
`field_idx >= schema->field_count`, copied from the match block whose invariant I had
deliberately changed, rejected every case body and the loop decoded nothing. The harness
printed case-body lookup labels through the integer branch, because its field-by-name search
was bounded by `field_count` too - 31 vectors reported as C type mismatches. And CR-2026-032's
own test asserted `skipped > attempted * 10`, a ratio true on the day and false as soon as
this CR landed.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HEADER = REPO_ROOT / "include" / "schema_interpreter.h"
HARNESS = REPO_ROOT / "tools" / "c-corpus-harness.py"

#: What this CR reached. Bounds, not equalities - closing `flagged` next raises both.
ATTEMPTED_FLOOR = 453


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("c33") / "r.json"
    done = subprocess.run([sys.executable, str(HARNESS), "--json", str(out)],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-2000:]
    return json.loads(out.read_text())


class TestTheInterpreterDecodesTlv:
    def test_the_field_type_exists(self):
        assert "FIELD_TYPE_TLV" in HEADER.read_text()

    def test_it_is_appended_after_the_sentinel(self):
        """UNKNOWN is parse_type_string's sentinel; inserting before it renumbers it."""
        text = HEADER.read_text()
        assert text.index("FIELD_TYPE_UNKNOWN,") < text.index("FIELD_TYPE_TLV\n} field_type_t;")

    def test_there_is_a_constructor_for_both_tag_forms(self):
        text = HEADER.read_text()
        assert "field_tlv(" in text
        assert "field_tlv_composite(" in text

    def test_the_tag_packing_lives_in_one_place(self):
        """So a builder and the decode loop cannot disagree about the encoding."""
        assert "schema_tlv_tag(" in HEADER.read_text()

    def test_the_decode_loop_is_bounded_by_the_payload(self):
        text = HEADER.read_text()
        start = text.index("if (field->type == FIELD_TYPE_TLV) {")
        assert "while (pos < len)" in text[start:start + 900]

    def test_an_undelimited_unknown_tag_ends_the_loop(self):
        """PS-302: with no length there is nothing to step over."""
        text = HEADER.read_text()
        start = text.index("if (field->type == FIELD_TYPE_TLV) {")
        window = text[start:start + 3000]
        assert "PS-302" in window

    def test_unknown_error_mode_is_supported_and_raw_is_not(self):
        text = HEADER.read_text()
        assert "SCHEMA_TLV_UNKNOWN_ERROR" in text
        assert "SCHEMA_TLV_UNKNOWN_SKIP" in text
        assert "SCHEMA_TLV_UNKNOWN_RAW" not in text, (
            "raw needs a channel decode_result_t does not have; claiming it would be worse"
        )

    def test_the_missing_warning_channel_is_recorded(self):
        """The other five report what they could not read; this one cannot."""
        text = HEADER.read_text()
        start = text.index("if (field->type == FIELD_TYPE_TLV) {")
        assert "no warning channel" in text[max(0, start - 900):start]


class TestCaseBodiesLiveAboveFieldCount:
    """The invariant the whole construct rests on."""

    def test_there_is_a_placement_helper(self):
        assert "schema_place_field(" in HEADER.read_text()

    def test_it_does_not_touch_field_count(self):
        text = HEADER.read_text()
        start = text.index("static inline bool schema_place_field(")
        body = text[start:text.index("}", text.index("{", start))]
        assert "field_count" not in body, body

    def test_it_refuses_an_index_outside_the_array(self):
        text = HEADER.read_text()
        start = text.index("static inline bool schema_place_field(")
        assert "SCHEMA_MAX_FIELDS" in text[start:start + 400]

    def test_the_case_loop_is_bounded_by_the_array_not_field_count(self):
        """Copying the match block's `>= field_count` guard rejected every body."""
        text = HEADER.read_text()
        start = text.index("if (field->type == FIELD_TYPE_TLV) {")
        window = text[start:start + 3500]
        assert "field_idx >= SCHEMA_MAX_FIELDS" in window
        assert "field_idx >= schema->field_count" not in window

    def test_the_reason_is_written_down_where_it_bit(self):
        text = HEADER.read_text()
        start = text.index("if (field->type == FIELD_TYPE_TLV) {")
        assert "decoded nothing at all" in text[start:start + 3500]


class TestTheHarnessBuildsTlvInStep:
    def test_it_emits_a_tlv(self):
        assert "field_add_tlv_case" in HARNESS.read_text()

    def test_it_places_bodies_rather_than_adding_them(self):
        assert "schema_place_field" in HARNESS.read_text()

    def test_it_skips_what_the_representation_cannot_hold(self):
        text = HARNESS.read_text()
        assert "merge:false" in text
        assert "unknown:raw" in text

    def test_it_names_the_fixed_limits_as_one_reason_each(self, report):
        """Not one per count - a single boundary is a single reason."""
        limits = [r for r in report["skips"] if "SCHEMA_MAX" in r]
        assert limits, sorted(report["skips"])
        assert len(limits) <= 2, limits


class TestTheMeasurementImproved:
    def test_every_attempted_vector_passes(self, report):
        assert report["failures"] == [], report["failures"][:6]

    def test_it_attempted_at_least_what_this_cr_reached(self, report):
        assert report["attempted"] >= ATTEMPTED_FLOOR, report["attempted"]

    def test_tlv_is_no_longer_a_skip_reason(self, report):
        assert not any("tlv field type" in r for r in report["skips"]), \
            sorted(report["skips"])

    def test_the_accounting_still_holds(self, report):
        assert report["attempted"] + report["skipped_vectors"] == report["corpus_vectors"]

    def test_flagged_is_now_the_largest_gap(self, report):
        """Which makes it the next construct, if C is to go further."""
        largest = max(report["skips"].items(), key=lambda kv: kv[1])
        assert largest[0] == "no flagged field type", largest


class TestTheExistingSelftestsStillPass:
    def test_make_test_c_succeeds(self):
        done = subprocess.run(["make", "test-c"], cwd=REPO_ROOT,
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stdout[-2500:] + done.stderr[-1500:]
        assert "C interpreter tests pass." in done.stdout

    def test_make_selftest_succeeds(self):
        done = subprocess.run(["make", "selftest"], cwd=REPO_ROOT,
                              capture_output=True, text=True)
        assert done.returncode == 0, (done.stdout[-1500:], done.stderr[-1500:])
        # The selftest binary logs to stderr, not stdout. Checking stdout alone failed on
        # a passing run - the shell commands that seemed to prove otherwise were using
        # `2>&1`, which merged the streams before I looked.
        combined = done.stdout + done.stderr
        assert re.search(r"ALL \d+ SELFTESTS PASSED", combined), combined[-600:]
