"""Every relative link in `docs/` resolves.

Three were broken on master and nothing noticed, because no test read the links:

- `C-INTERPRETER-STATUS.md` and `TS013-COMPLIANCE-ANALYSIS.md` were linked from
  `BIDIRECTIONAL-CODEC.md` and `TTN-CODEC-CONVERSION-GUIDE.md` but exist only on the
  unrelated-history `internal-docs` root commit (`a4d7ec3`) - they were never on master,
  so the links were dead the day they were written. Both now point at
  `SPEC-IMPLEMENTATION-STATUS.md`, which covers the same ground per implementation.
- `../la-payload-schema/...` was off by one directory level. Written relative to the repo
  root, but the file containing it is in `docs/`, so it resolved to
  `<repo>/la-payload-schema`. The spec repo is a *sibling* of this one: `../../`.

Links that leave the repo are checked for shape, not existence - the sibling spec repo is
not present in a fresh clone or in CI, and a test that demanded it would fail there for a
reason that has nothing to do with this repo.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"

#: `[text](target)`, skipping pure in-page anchors and absolute URLs.
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def links():
    """(doc, raw target, path part) for every relative link under docs/."""
    for path in sorted(DOCS.rglob("*.md")):
        for target in LINK.findall(path.read_text()):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            yield path, target, target.split("#", 1)[0]


ALL = list(links())
IN_REPO, ESCAPING = [], []
for _doc, _raw, _part in ALL:
    resolved = (_doc.parent / _part).resolve()
    (IN_REPO if REPO_ROOT in resolved.parents or resolved == REPO_ROOT
     else ESCAPING).append((_doc, _raw, resolved))


def ident(entry):
    return f"{entry[0].relative_to(REPO_ROOT)}->{entry[1]}"


def test_there_are_links_to_check():
    """A regex that silently matches nothing would make every test below vacuous."""
    # A floor, not the count. 37 when this landed; the point is only that the regex
    # matched something, so a typo in it cannot quietly make every test below vacuous.
    assert len(ALL) > 20, len(ALL)
    assert IN_REPO, "no in-repo links found, so nothing was actually verified"


@pytest.mark.parametrize("entry", IN_REPO, ids=ident)
def test_an_in_repo_link_resolves(entry):
    doc, raw, resolved = entry
    assert resolved.exists(), (
        f"{doc.relative_to(REPO_ROOT)} links to {raw!r}, which does not exist at "
        f"{resolved.relative_to(REPO_ROOT)}"
    )


@pytest.mark.parametrize("entry", ESCAPING, ids=ident)
def test_a_link_leaving_the_repo_points_at_a_known_sibling(entry):
    """Shape only. The sibling repo is absent in CI; the level it sits at is the bug."""
    doc, raw, resolved = entry
    assert "la-payload-schema" in str(resolved), (
        f"{doc.relative_to(REPO_ROOT)} links outside the repo to {raw!r}, which is not "
        f"the companion spec repo - probably a wrong number of `../`"
    )
    assert REPO_ROOT.parent in resolved.parents, (
        f"{doc.relative_to(REPO_ROOT)}: {raw!r} escapes past the repo's parent"
    )


class TestTheSpecificLinksThatWereBroken:
    """Named so a regression is legible, not just a parametrised id."""

    def test_the_bidirectional_codec_status_link(self):
        text = (DOCS / "BIDIRECTIONAL-CODEC.md").read_text()
        assert "C-INTERPRETER-STATUS.md" not in text, "the dead link is back"
        assert "SPEC-IMPLEMENTATION-STATUS.md" in text

    def test_the_ttn_guide_compliance_link(self):
        text = (DOCS / "TTN-CODEC-CONVERSION-GUIDE.md").read_text()
        assert "TS013-COMPLIANCE-ANALYSIS.md" not in text, "the dead link is back"
        assert "SPEC-IMPLEMENTATION-STATUS.md" in text

    def test_the_sibling_repo_link_has_the_right_depth(self):
        text = (DOCS / "BIDIRECTIONAL-CODEC.md").read_text()
        assert "](../../la-payload-schema/" in text
        assert "](../la-payload-schema/" not in text.replace("](../../la-payload", "")

    def test_the_replacement_target_covers_both_subjects(self):
        """Pointing at a doc that does not discuss C or TS013 would be a worse link."""
        text = (DOCS / "SPEC-IMPLEMENTATION-STATUS.md").read_text()
        assert "### C (`include/schema_interpreter.h`)" in text
        assert "generate_ts013_codec.py" in text
