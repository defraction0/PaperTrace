"""A markdown list item owns its line, or it is not a list item.

`report.md.j2` is rendered with `trim_blocks=True` (report.py), which strips the
newline immediately after **any** block tag — `{% endif %}` included. A loop
whose item line ends in one therefore emits every item with no line ending, and
the whole list collapses into a single run-on paragraph. Jinja2's `+%}` is the
opt-out, and the template already uses it on the lines that were noticed
(report.md.j2:6, :122, :127).

Found by running a real 13-page paper with 9 uncited assertions: the markdown
carried ONE list item where there should have been nine, while both HTML looks
were correct — `<li>` does not depend on newlines. The committed demo has
exactly one uncited assertion, so a single-item fixture had been testing the
separator between items vacuously for as long as the section has existed.

Behavioural on purpose. A template-shape check would flag the single-item lists
at :70 and :92, which lose the same newline and are correct anyway because a
blank line follows them — so the rule that actually matters is what the reader
receives, not what the template looks like.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import ClaimResult, RunResults, UncitedClaim  # noqa: E402
from papertrace.report import write_reports  # noqa: E402


def _rendered(tmp_path: Path, **kw) -> str:
    results = RunResults(manuscript="m.pdf", date="2026-01-01", **kw)
    write_reports(results, None, tmp_path, png=False, formats=("md",))
    return (tmp_path / "report.md").read_text()


def test_every_uncited_assertion_is_its_own_list_item(tmp_path):
    md = _rendered(tmp_path, uncited=[
        UncitedClaim(id=1, claim="First.", quote="First.", location="Intro ¶1"),
        UncitedClaim(id=2, claim="Second.", quote="Second.", location="Discussion ¶2"),
        UncitedClaim(id=3, claim="Third.", quote="Third.", location="Limitations"),
    ])
    assert len(re.findall(r"^- \*\*\[U\d+\]\*\*", md, re.M)) == 3, md


def test_an_uncited_assertion_with_no_location_still_ends_its_line(tmp_path):
    """`location` is optional, so the `{% if %}` can close on an empty branch —
    and the newline is eaten either way."""
    md = _rendered(tmp_path, uncited=[
        UncitedClaim(id=1, claim="First.", quote="First."),
        UncitedClaim(id=2, claim="Second.", quote="Second."),
    ])
    assert len(re.findall(r"^- \*\*\[U\d+\]\*\*", md, re.M)) == 2, md


def test_the_gap_register_keeps_one_bullet_per_unverified_claim(tmp_path):
    """Already correct — `{% endif +%}` at report.md.j2:122. Pinned because it
    is the same shape one line away from the same defect."""
    md = _rendered(tmp_path, claims=[
        ClaimResult(id=1, claim="A.", location="Intro", refs=["1"],
                    verdict="not_retrieved", note="paywalled"),
        ClaimResult(id=2, claim="B.", location="Intro", refs=["2"],
                    verdict="not_retrieved", note="paywalled"),
    ])
    assert len(re.findall(r"^  - ⊘", md, re.M)) == 2, md
