"""First author and year are reported beside an attribution, and gate nothing.

Bibliographies always print at least a first author and a year, so it is
tempting to require them to match. Measured against the eight real
misattributions this could have caught, it cannot carry that weight:

    substring containment   vetoes 3/8   `ince` matches "s-ince-"; `ma`, `ren`
                                         and `mo` match almost any page
    word-boundary + NFKD    vetoes 7/8   but wrongly vetoes a CORRECT source,
                                         and misses one wrong one

The wrongly-vetoed one is `yin-2024.pdf`, the real source for its reference:
journal PDFs glue affiliation superscripts to surnames, so `Yin1` has no word
boundary before the digit. Refusing it would report a claim as unverifiable
while its source sat in the folder — a false gap, which this codebase treats as
no better than a false verdict. The missed one is two different Zhang 2024
papers in one bibliography, which no author-year rule can separate.

So it is evidence a reader weighs, never a decision. These tests exist mainly to
pin that: a disagreement changes the *reason* and nothing else.

Three answers, not two — absence of author metadata is not disagreement.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import RefEntry  # noqa: E402

LI = ("Li J, Zhang Y, Ye H, Hu L. Machine learning-based development of nomogram for "
      "hepatocellular carcinoma prediction. Acad Radiol. 2023;30:1889.")


def _pdf(path: Path, *, author: str = "", year: str = "2023") -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((55, 80), "https://doi.org/10.1016/j.acra.2023.05.014", fontsize=9)
    page.insert_text((55, 100), "Machine learning-based development of nomogram for "
                                "hepatocellular carcinoma prediction", fontsize=9)
    page.insert_text((55, 120), f"Academic Radiology, published {year}", fontsize=9)
    meta = {"title": "Machine learning-based development of nomogram for "
                     "hepatocellular carcinoma prediction"}
    if author:
        meta["author"] = author
    doc.set_metadata(meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


# --- the surname rule ------------------------------------------------------


def test_the_first_surname_survives_both_printed_styles():
    """`Slabaugh MA` and `M.A. Slabaugh` are the two shapes that turn up, and
    `_slug` gets the second one wrong: it takes the first letter-bearing token,
    so `F.P. Rivara` slugs `fp-2019`. This must not repeat that."""
    from papertrace.refs import _first_surname

    assert _first_surname("Smith J, Jones B. A study. J Rad 2019;1:1.") == "smith"
    assert _first_surname("M.A. Slabaugh, N.A. Friel. A study. J Rad 2019;1:1.") == "slabaugh"
    assert _first_surname("F.P. Rivara, D.C. Grossman. A study. J Rad 2019;1:1.") == "rivara"


def test_diacritics_fold_rather_than_vanish():
    """`_slug` deletes non-ASCII outright — `İnce`→`nce`, `Müller`→`mller`. A
    surname used for corroboration must fold instead, or it would disagree with
    the very paper it belongs to."""
    from papertrace.refs import _first_surname

    assert _first_surname("İnce O, Önder H. A study. J Rad 2023;1:1.") == "ince"
    assert _first_surname("Müller D, Soto-Rey I. A study. J Rad 2022;1:1.") == "muller"
    assert _first_surname("Gençtürk M. A study. J Rad 2023;1:1.") == "gencturk"


# --- three answers, never two ----------------------------------------------


def test_agreement_is_reported(tmp_path):
    from papertrace.refs import corroborate

    pdf = _pdf(tmp_path / "li-2023.pdf", author="Jian Li", year="2023")
    entry = RefEntry(num="76", raw=LI, slug="li-2023", year="2023")
    state, detail = corroborate(entry, pdf)
    assert state == "agree", (state, detail)
    assert "Li" in detail or "li" in detail, detail
    assert "2023" in detail, detail


def test_a_different_author_is_reported_as_disagreement(tmp_path):
    from papertrace.refs import corroborate

    pdf = _pdf(tmp_path / "x.pdf", author="Osman İnce", year="2023")
    entry = RefEntry(num="76", raw=LI, slug="li-2023", year="2023")
    state, _ = corroborate(entry, pdf)
    assert state == "disagree"


def test_no_author_metadata_is_not_disagreement(tmp_path):
    """The third answer. 10 of 39 real files carry no /Author at all, and
    silence about identity must never read as evidence against it."""
    from papertrace.refs import corroborate

    pdf = _pdf(tmp_path / "x.pdf", author="", year="2023")
    entry = RefEntry(num="76", raw=LI, slug="li-2023", year="2023")
    state, _ = corroborate(entry, pdf)
    assert state == "unknown", state


# --- and it decides nothing ------------------------------------------------


def test_a_disagreement_does_not_change_the_attribution(tmp_path, monkeypatch):
    """The whole point. A file the DOI identifies is still used, still
    `verified`, still that reference's source — the disagreement is stated."""
    from papertrace import refs as refs_mod
    from papertrace.refs import resolve_all

    def _boom(*a, **k):
        raise AssertionError("the online chain was reached for an identified file")

    monkeypatch.setattr(refs_mod, "_resolve_by_retrieval", _boom)

    d = tmp_path / "sources"
    # publisher-named so only its own DOI can place it, and /Author is somebody
    # else — exactly the shape that must NOT become a refusal
    pdf = _pdf(d / "1-s2.0-S1076633223002453-main.pdf", author="Osman İnce", year="2023")
    entries = [RefEntry(num="76", raw=LI, slug="li-2023", year="2023",
                        doi="10.1016/j.acra.2023.05.014")]
    resolve_all(entries, tmp_path / "dest", "t@example.org", provided_dir=d)

    e = entries[0]
    assert e.status == "provided", e.status
    assert e.pdf_path == str(pdf)
    assert e.title_check == "verified"
    assert "first author" in e.reason, e.reason
