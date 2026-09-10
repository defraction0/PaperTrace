"""One file is one reference's paper, or it is nobody's.

Attributing a provided PDF is bipartite matching: each file to at most one
reference, each reference to at most one file. `refs.py` solved it with one
independent greedy decision per reference — find candidates by a key, accept the
first a veto does not reject — and nothing anywhere said *"this file is already
reference [76], so it cannot be reference [75]."* Without that constraint the
result is not a matching at all but a one-to-many relation.

Measured on a real 101-reference paper (Fu et al., photon-counting CT, 39
provided PDFs): six files were each attributed to 2-4 references, and every one
was reported as `identity confirmed`. `li-2023.pdf` answered for [75], [76],
[78] and [81] — İnce, Li, Ma and Ren, four different papers — because
`_named_for` keeps only slug tokens with `len(t) > 3`, so `nce-2023`, `ma-2023`
and `ren-2023` all reduce to the key `{"2023"}` and every 2023 filename matches
it. `_title_check_text` passed them at 5/13 to 13/19: on a bibliography that is
uniformly about machine learning in interventional oncology, a bag-of-words
containment ratio has no precision left.

The rule these tests pin is one sentence: **once a file is positively attributed
to one reference, it is not a candidate for any other.** Positive attribution is
an exact `<slug>.pdf` stem (the user's own assertion) or `identify_by_content`
(the file's own DOI, else a unique title). Token overlap proposes; it no longer
disposes.

What must NOT change, and is pinned elsewhere: an exact-stem filename still
beats content (`test_provided_identity.py:302`), and token-only matching still
works where no other reference owns the file — the reference-manager-export case
at `test_refs.py:364`.

Offline. PDFs are built in-test and never committed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import RefEntry  # noqa: E402

# The four real references `li-2023.pdf` was attributed to. Trimmed, but the
# shared vocabulary is the point — it is what defeated the token check.
INCE = ("İnce O, Önder H, Gençtürk M, Cebeci H, Golzarian J, Young S. Machine learning "
        "models in prediction of treatment response after transarterial "
        "chemoembolization. Cardiovasc Intervent Radiol. 2023;46:1181-1191.")
LI = ("Li J, Zhang Y, Ye H, Hu L, Li X, Li Y, et al. Machine learning-based development "
      "of nomogram for hepatocellular carcinoma prediction. Acad Radiol. 2023;30:1889.")
MA = ("Ma J, Bo Z, Zhao Z, Yang J, Yang Y, Li H, et al. Machine learning to predict the "
      "response to lenvatinib combined with transarterial chemoembolization. "
      "Cancers. 2023;15:625.")
REN = ("Ren H, An C, Fu W, Wu J, Yao W, Yu J, et al. Prediction of local tumor "
       "progression after microwave ablation for early-stage hepatocellular carcinoma. "
       "J Cancer Res Ther. 2023;19:1000.")


LI_TITLE = ("Machine learning-based development of nomogram for hepatocellular "
            "carcinoma prediction")

# Li's own abstract, and the reason the token veto cannot save us. Nothing here
# is contrived: four papers on machine-learning prediction of TACE response in
# hepatocellular carcinoma share this vocabulary, so a bag-of-words containment
# ratio scores the wrong three as highly as the right one. Verified by
# `test_the_token_veto_really_does_pass_the_wrong_three` below, which fails if a
# future edit makes this fixture unrealistically clean.
LI_BODY = """Purpose: to develop a machine learning based nomogram for the
prediction of treatment response after transarterial chemoembolization in
patients with hepatocellular carcinoma. Materials and methods: models were
trained to predict local tumor progression and objective response, and
compared with microwave ablation and lenvatinib combined regimens reported
for early stage cancers. Results: the machine learning models improved
prediction of treatment response over clinical staging alone. Acad Radiol."""


def _pdf(path: Path, *, title: str = "", body: str = "", doi: str | None = None) -> Path:
    """A one-page PDF that prints its own DOI, as every real paper does."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = 80
    if doi:
        page.insert_text((55, y), f"https://doi.org/{doi}", fontsize=9)
        y += 16
    for line in ([title] if title else []) + body.split("\n"):
        page.insert_text((55, y), line, fontsize=9)
        y += 14
    if title:
        doc.set_metadata({"title": title})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def _four_entries() -> list[RefEntry]:
    """The four references, with the slugs `_slug` really produces.

    `nce-2023` is not a typo: `re.sub(r"[^A-Za-z\\-]", "", "İnce")` deletes the
    non-ASCII `İ` (U+0130), and the surviving `nce` is three characters, so
    `_named_for`'s `len(t) > 3` filter drops it too.
    """
    return [
        RefEntry(num="75", raw=INCE, slug="nce-2023", year="2023",
                 doi="10.1007/s00270-023-03574-z"),
        RefEntry(num="76", raw=LI, slug="li-2023", year="2023",
                 doi="10.1016/j.acra.2023.05.014"),
        RefEntry(num="78", raw=MA, slug="ma-2023", year="2023",
                 doi="10.3390/cancers15030625"),
        RefEntry(num="81", raw=REN, slug="ren-2023", year="2023",
                 doi="10.4103/jcrt.jcrt_319_23"),
    ]


def _no_network(monkeypatch):
    """No reference here should reach the online chain except by falling through,
    which is the correct outcome for the three that own no file."""
    from papertrace import refs as refs_mod

    def _paywalled(entry, *a, **k):
        entry.status = "paywalled"
        entry.reason = "DOI resolved but no legal open-access copy found"

    monkeypatch.setattr(refs_mod, "_resolve_by_retrieval", _paywalled)


def _resolve(tmp_path, monkeypatch):
    from papertrace.refs import resolve_all

    _no_network(monkeypatch)
    d = tmp_path / "sources"
    # exactly the real folder: ONE file, named for reference [76]
    pdf = _pdf(d / "li-2023.pdf", title=LI_TITLE, body=LI_BODY,
               doi="10.1016/j.acra.2023.05.014")
    entries = _four_entries()
    resolve_all(entries, tmp_path / "dest", "t@example.org", provided_dir=d)
    return entries, pdf


def test_the_token_veto_really_does_pass_the_wrong_three(tmp_path):
    """The fixture's own premise, asserted so it cannot rot.

    If `_title_check` said `mismatch` here, the 0.4.1 rule (a token-matched file
    that is a different paper is a reason to keep looking) would already handle
    this and the tests below would pass without the fix — which is exactly what
    happened with a cleaner fixture. The defect only exists because the veto
    PASSES. On the real corpus it passed at 5/13 to 13/19.
    """
    from papertrace.refs import TITLE_VERIFIED, _title_check

    pdf = _pdf(tmp_path / "li-2023.pdf", title=LI_TITLE, body=LI_BODY,
               doi="10.1016/j.acra.2023.05.014")
    for e in _four_entries():
        state, detail = _title_check(e, pdf)
        assert state == TITLE_VERIFIED, f"[{e.num}] {state} — {detail}"


def test_one_file_is_not_four_references(tmp_path, monkeypatch):
    """The defect itself. `li-2023.pdf` is Li 2023 and nothing else."""
    entries, pdf = _resolve(tmp_path, monkeypatch)
    holders = [e.num for e in entries if e.pdf_path == str(pdf)]
    assert holders == ["76"], f"one file answered for references {holders}"


def test_the_three_others_report_a_gap_rather_than_a_verdict(tmp_path, monkeypatch):
    """A claim citing İnce, Ma or Ren must be reported as unverifiable, not
    judged against Li's paper. `pdf_path` is what `check` reads."""
    entries, _ = _resolve(tmp_path, monkeypatch)
    for e in entries:
        if e.num == "76":
            continue
        assert e.pdf_path is None, f"[{e.num}] would be judged against another paper"
        assert e.status != "provided", f"[{e.num}] status {e.status}"


def test_the_reference_that_owns_the_file_still_gets_it(tmp_path, monkeypatch):
    """The fix must not cost the feature its point."""
    entries, pdf = _resolve(tmp_path, monkeypatch)
    li = next(e for e in entries if e.num == "76")
    assert (li.status, li.pdf_path) == ("provided", str(pdf))
    assert li.title_check == "verified", li.title_check


def test_a_withheld_file_is_named_with_the_reference_that_owns_it(tmp_path, monkeypatch):
    """A gap that withholds what the tool already knows is the failure this
    codebase exists to avoid — so the reason names the file and the label."""
    entries, _ = _resolve(tmp_path, monkeypatch)
    ince = next(e for e in entries if e.num == "75")
    assert "li-2023.pdf" in ince.reason, ince.reason
    assert "[76]" in ince.reason, ince.reason


def test_content_decides_when_the_filename_names_no_reference(tmp_path, monkeypatch):
    """The publisher-download case, with the same mutual exclusion. The file is
    called nothing recognisable, so only its own DOI can place it — and once it
    is placed, the other three cannot have it either."""
    from papertrace.refs import resolve_all

    _no_network(monkeypatch)
    d = tmp_path / "sources"
    pdf = _pdf(d / "1-s2.0-S1076633223002453-main.pdf", title=LI_TITLE,
               body=LI_BODY, doi="10.1016/j.acra.2023.05.014")
    entries = _four_entries()
    resolve_all(entries, tmp_path / "dest", "t@example.org", provided_dir=d)

    holders = [e.num for e in entries if e.pdf_path == str(pdf)]
    assert holders == ["76"], f"one file answered for references {holders}"
