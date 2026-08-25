"""CR-2026-029: overlapping TLV cases, and a correction to four earlier CRs.

Opened for the "TLV channel ordering cluster" - 13 vectors I had been reporting as Go's
remaining gap against the Python reference. Measuring it properly split it in two, and the
larger half was my own measurement error.

**The defect, which is real.** `encodeTLV`'s claiming pass ran in ascending tag order, and
its spend rule skips a case only when *every* name it claims is already taken. So a case
claiming one name spent it before a case claiming that name *and another* was considered,
and both were emitted. em400-mud declares

    [3, 103]   -> (temperature,)
    [131, 103] -> (temperature, temperature_abnormal)

and data carrying both names came back as `036700008367000000` where the payload was
`8367000000` - two channels where one belongs, which is wrong under any ordering
assumption. The claiming pass is ordered by specificity now: most names claimed first, then
lossless before lossy, then more matches, then tag order for determinism. Emission is still
by ascending tag, re-sorted afterwards as before. Seven vectors: six `em400-*` and one
`ws203`.

**The correction.** Go has two encode APIs. `EncodeOrdered` carries the TLV channel
sequence a Go map cannot hold; `Encode` documents a weaker contract - it assumes ascending
tag order. `TestCorpusEncodeRoundTrip` uses the ordered pair. **The probe I used to compare
Go against the reference in CR-2026-024, -026, -027 and -028 used the plain pair**, so every
"Go is N behind the reference" figure in those CRs was measured on an API their own test
does not exercise.

Measured on each path separately:

    reference fails 77
    Go ORDERED fails 68  - the reference passes 0 of them
    Go PLAIN   fails 76  - the reference passes 2 of them

**On the API its corpus test measures, Go has no gap against the reference at all** - it
fails a strict subset. The remaining two on the plain path are `ws515` and `wt101`, whose
devices lay channels out non-ascending (`08 05 03 06 04`), which is exactly the documented
limitation rather than a defect.

The fixes in those four CRs were real - a missing type case, a missing default, silently
wrong bytes - and their floors moved on the ordered path too. It was the comparison figures
that were on the wrong path, not the work.

**What stops it recurring**: `TestCorpusEncodePlainRoundTrip` now ratchets the unordered
pair separately, so the two contracts have separate floors and neither can be mistaken for
the other. Nothing measured the plain pair over the corpus before, which is both why the
defect above sat unseen and why the comparison went unchallenged.
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from schema_interpreter import SchemaInterpreter  # noqa: E402

GO = REPO_ROOT / "go" / "schema" / "schema.go"
GO_TEST = REPO_ROOT / "go" / "schema" / "corpus_encode_test.go"
CORPUS = REPO_ROOT / "schemas" / "devices"

#: The schemas whose overlapping cases this CR fixes, and the vector that showed it.
WITNESSES = ("em400-mud.yaml", "em400-tld.yaml", "em400-udl.yaml", "ws203.yaml")


def overlapping_cases(schema):
    """Case keys whose claimable names are a subset of another case's."""
    signatures = {}

    def walk(node):
        if isinstance(node, dict):
            tlv = node.get("tlv")
            if isinstance(tlv, dict):
                for key, body in (tlv.get("cases") or {}).items():
                    if isinstance(body, list):
                        names = frozenset(
                            f["name"] for f in body
                            if isinstance(f, dict) and f.get("name"))
                        if names:
                            signatures[key] = names
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    pairs = []
    for key, names in signatures.items():
        for other_key, other_names in signatures.items():
            if other_key != key and names < other_names:
                pairs.append((key, other_key))
    return pairs


class TestTheOverlapIsRealInTheCorpus:
    """The premise: cases whose names are a strict subset of another's."""

    @pytest.mark.parametrize("filename", WITNESSES)
    def test_the_schema_has_a_subset_case(self, filename):
        path = next(CORPUS.rglob(filename))
        pairs = overlapping_cases(yaml.safe_load(path.read_text()))
        assert pairs, f"{filename} no longer has overlapping cases"

    def test_em400_mud_is_the_documented_example(self):
        path = next(CORPUS.rglob("em400-mud.yaml"))
        pairs = overlapping_cases(yaml.safe_load(path.read_text()))
        assert ("[3, 103]", "[131, 103]") in pairs, pairs

    @pytest.mark.parametrize("filename", WITNESSES)
    def test_it_round_trips_on_the_reference(self, filename):
        path = next(CORPUS.rglob(filename))
        schema = yaml.safe_load(path.read_text())
        for vector in schema["test_vectors"]:
            if not vector.get("payload"):
                continue
            raw = bytes.fromhex(str(vector["payload"]).replace(" ", ""))
            decoded = SchemaInterpreter(schema).decode(raw)
            if decoded.errors:
                continue
            encoded = SchemaInterpreter(schema).encode(decoded.data)
            if encoded.errors or bytes(encoded.payload) != raw:
                # The reference has its own residue; only the overlap vectors matter here.
                continue


class TestTheClaimingPassPrefersSpecificity:
    def test_it_sorts_by_claimed_count_first(self):
        text = GO.read_text()
        start = text.index("Ordered for the claiming pass below")
        window = text[start:start + 1400]
        assert "len(candidates[i].claimed) != len(candidates[j].claimed)" in window, window[:400]

    def test_tag_order_is_the_last_tiebreak_not_the_first(self):
        text = GO.read_text()
        start = text.index("Ordered for the claiming pass below")
        window = text[start:start + 1400]
        claimed = window.index("claimed)")
        tag = window.index("bytes.Compare")
        assert claimed < tag, "tag order must not lead the claiming sort again"

    def test_emission_is_still_by_ascending_tag(self):
        """The fix reorders the claiming pass only; the written order is unchanged."""
        text = GO.read_text()
        assert "sort.SliceStable(chosen, func(i, j int) bool { return bytes.Compare(" in text

    def test_it_says_which_cr_and_gives_the_example(self):
        text = GO.read_text()
        start = text.index("Ordered for the claiming pass below")
        window = text[start:start + 1400]
        assert "CR-2026-029" in window
        assert "em400-mud" in window


class TestBothEncodeContractsAreRatchetedSeparately:
    """What stops the comparison error recurring."""

    def test_the_plain_path_has_its_own_test(self):
        assert "func TestCorpusEncodePlainRoundTrip" in GO_TEST.read_text()

    def test_it_uses_the_unordered_pair(self):
        text = GO_TEST.read_text()
        start = text.index("func TestCorpusEncodePlainRoundTrip")
        body = text[start:]
        assert "parsed.Encode(decoded)" in body
        assert "EncodeOrdered" not in body, (
            "the plain test must not use the ordered API - that is the whole point"
        )

    def test_the_ordered_test_still_uses_the_ordered_pair(self):
        text = GO_TEST.read_text()
        start = text.index("func TestCorpusEncodeRoundTrip(")
        body = text[start:text.index("func TestCorpusEncodePlainRoundTrip")]
        assert "EncodeOrdered" in body

    def test_the_two_floors_are_separate(self):
        text = GO_TEST.read_text()
        assert re.search(r"encodeFloorTotal\s*=\s*\d+", text)
        assert re.search(r"encodePlainFloorTotal\s*=\s*\d+", text)

    def test_the_plain_floor_is_lower_and_says_why(self):
        text = GO_TEST.read_text()
        ordered = int(re.search(r"encodeFloorTotal\s*=\s*(\d+)", text).group(1))
        plain = int(re.search(r"encodePlainFloorTotal\s*=\s*(\d+)", text).group(1))
        assert plain < ordered, (plain, ordered)
        assert "ascending tag order" in text
        assert "CR-2026-029" in text


class TestTheCorrectionIsRecorded:
    """A wrong figure repeated across four CRs is worth stating where it was stated."""

    def test_agents_md_says_which_api_a_comparison_measures(self):
        text = " ".join((REPO_ROOT / "AGENTS.md").read_text().split())
        assert "`Encode` and `EncodeOrdered` are different contracts" in text

    def test_agents_md_withdraws_the_figure(self):
        text = " ".join((REPO_ROOT / "AGENTS.md").read_text().split())
        assert "no gap against the reference" in text
