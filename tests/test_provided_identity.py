"""A provided PDF is identified by what is in it, not by what it is called.

Filename matching stays the first pass — it carries the user's own assertion
that this file is that reference. This module is about the gap it leaves: a
publisher download is called `s41467-023-39631-x.pdf` and matches nothing, so
before this the audit ran, looked entirely normal, and used none of it.

Offline like the rest of the suite. PDFs are built in-test and never committed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import RefEntry  # noqa: E402

PYRROS = ("Pyrros A, Borstelmann SM, et al (2023) Opportunistic detection of type 2 "
          "diabetes using deep learning from frontal chest radiographs. "
          "Nat Commun 14:4039. doi:10.1038/s41467-023-39631-x")
SUDLOW = ("Sudlow C, Gallacher J, Allen N, et al (2015) UK Biobank: An Open Access "
          "Resource for Identifying the Causes of a Wide Range of Complex Diseases "
          "of Middle and Old Age. PLoS Med 12:e1001779")
CORRIGENDUM = ("Sudlow C, et al (2015) Correction: UK Biobank: An Open Access Resource "
               "for Identifying the Causes of a Wide Range of Complex Diseases of "
               "Middle and Old Age. PLoS Med 12:e1001800")


def _pdf(path: Path, *, title: str = "", body: str = "", doi: str | None = None) -> Path:
    """A one-page PDF with an optional metadata title — how real papers arrive."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = 80
    for line in ([f"https://doi.org/{doi}"] if doi else []) + body.split("\n"):
        page.insert_text((55, y), line, fontsize=10)
        y += 15
    if title:
        doc.set_metadata({"title": title})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def _entries() -> list[RefEntry]:
    return [
        RefEntry(num="1", raw=PYRROS, slug="pyrros-2023", doi="10.1038/s41467-023-39631-x"),
        RefEntry(num="2", raw=SUDLOW, slug="sudlow-2015", doi="10.1371/journal.pmed.1001779"),
    ]


def _identify(entries, folder, claimed=frozenset()):
    from papertrace.refs import identify_by_content

    return identify_by_content(entries, folder, set(claimed))


# --- the two signals -------------------------------------------------------


def test_a_publisher_named_download_is_identified_by_its_doi(tmp_path):
    """The motivating case: nothing in `s41467-023-39631-x.pdf` names Pyrros."""
    _pdf(tmp_path / "src" / "s41467-023-39631-x.pdf",
         title="Opportunistic detection of type 2 diabetes using deep learning "
               "from frontal chest radiographs",
         doi="10.1038/s41467-023-39631-x", body="Article")
    assigned, unclaimed = _identify(_entries(), tmp_path / "src")

    assert {p.name: (v.entry.num, v.kind, v.signal) for p, v in assigned.items()} == {
        "s41467-023-39631-x.pdf": ("1", "article", "DOI")
    }
    assert unclaimed == []


def test_a_paper_whose_reference_printed_no_doi_is_identified_by_its_title(tmp_path):
    """Bibliographies often omit the DOI, so title has to carry the rest."""
    entries = _entries()
    entries[1].doi = None  # the reference printed no DOI
    f = _pdf(tmp_path / "src" / "journal.pmed.1001779.pdf",
             title="UK Biobank: An Open Access Resource for Identifying the Causes of "
                   "a Wide Range of Complex Diseases of Middle and Old Age",
             body="HEALTH IN ACTION")
    assigned, unclaimed = _identify(entries, tmp_path / "src")

    assert [(v.entry.num, v.kind, v.signal) for v in assigned.values()] == [
        ("2", "article", "title")
    ]
    assert f in assigned


def test_a_pdf_with_no_metadata_title_falls_back_to_its_first_page(tmp_path):
    """Roughly one paper in seven carries no usable metadata title."""
    entries = _entries()
    entries[1].doi = None
    _pdf(tmp_path / "src" / "download.pdf",
         body="UK Biobank: An Open Access Resource for Identifying the Causes of a\n"
              "Wide Range of Complex Diseases of Middle and Old Age")
    assigned, _ = _identify(entries, tmp_path / "src")
    assert [(v.entry.num, v.kind) for v in assigned.values()] == [("2", "article")]


# --- refusing to guess -----------------------------------------------------


def test_a_title_matching_two_references_is_refused_not_ranked(tmp_path):
    """A corrigendum shares nearly every distinctive word with its original.
    Picking the better score here judges a claim against the wrong paper, and
    nothing downstream could ever notice."""
    entries = _entries() + [RefEntry(num="9", raw=CORRIGENDUM, slug="sudlow-2015-r9")]
    for e in entries:
        e.doi = None
    f = _pdf(tmp_path / "src" / "download.pdf",
             title="UK Biobank: An Open Access Resource for Identifying the Causes of "
                   "a Wide Range of Complex Diseases of Middle and Old Age")
    assigned, unclaimed = _identify(entries, tmp_path / "src")

    assert assigned == {}
    assert len(unclaimed) == 1
    path, why = unclaimed[0]
    assert path == f
    assert "[2]" in why and "[9]" in why, why


def test_a_pdf_that_matches_nothing_is_reported_not_dropped(tmp_path):
    """Before this, an unmatched article PDF was silently ignored while a
    supplement-named one was reported — the user learned nothing either way."""
    f = _pdf(tmp_path / "src" / "some-other-paper.pdf",
             title="Cardiac magnetic resonance in hypertrophic cardiomyopathy")
    assigned, unclaimed = _identify(_entries(), tmp_path / "src")

    assert assigned == {}
    assert [p for p, _ in unclaimed] == [f]
    assert "could not tell" in unclaimed[0][1].lower()


def test_too_few_distinctive_words_is_not_an_accept(tmp_path):
    """`titles_match` returns None for a title with under four distinctive
    words. A filename match may be accepted as unverifiable because the user
    named it; a content match has no such assertion behind it."""
    entries = [RefEntry(num="1", raw="Smith J (2020) Brief note. BMJ 1:1", slug="smith-2020")]
    _pdf(tmp_path / "src" / "download.pdf", title="Brief note")
    assigned, unclaimed = _identify(entries, tmp_path / "src")
    assert assigned == {}
    assert len(unclaimed) == 1


# --- supplements -----------------------------------------------------------


def test_a_publisher_named_supplement_is_recognised_from_its_own_text(tmp_path):
    """`41467_2023_39631_MOESM1_ESM.pdf` carries no filename marker at all —
    `\\besm\\b` cannot match after an underscore — but its first page says what
    it is."""
    f = _pdf(tmp_path / "src" / "41467_2023_39631_MOESM1_ESM.pdf",
             title="Supplementary Information for Opportunistic detection of type 2 "
                   "diabetes using deep learning from frontal chest radiographs")
    assigned, unclaimed = _identify(_entries(), tmp_path / "src")

    assert [(v.entry.num, v.kind) for v in assigned.values()] == [("1", "supplement")]
    assert f in assigned


def test_the_article_and_its_supplement_both_land_on_the_same_reference(tmp_path):
    """Both titles match [1]; the supplementary marker is what separates them."""
    art = _pdf(tmp_path / "src" / "s41467-023-39631-x.pdf",
               title="Opportunistic detection of type 2 diabetes using deep learning "
                     "from frontal chest radiographs")
    sup = _pdf(tmp_path / "src" / "mmc1.pdf",
               title="Supplementary Information for Opportunistic detection of type 2 "
                     "diabetes using deep learning from frontal chest radiographs")
    entries = _entries()
    entries[0].doi = None
    assigned, unclaimed = _identify(entries, tmp_path / "src")

    assert assigned[art].kind == "article"
    assert assigned[sup].kind == "supplement"
    assert assigned[art].entry.num == assigned[sup].entry.num == "1"
    assert unclaimed == []


# --- filename matching keeps priority --------------------------------------


def test_a_file_already_claimed_by_filename_is_left_alone(tmp_path):
    """Content inference fills gaps; it never re-decides what the user named."""
    named = _pdf(tmp_path / "src" / "pyrros-2023.pdf", title="Something else entirely")
    assigned, unclaimed = _identify(_entries(), tmp_path / "src", claimed={named})
    assert assigned == {}
    assert unclaimed == []


def test_no_folder_is_not_an_error(tmp_path):
    assert _identify(_entries(), None) == ({}, [])
    assert _identify(_entries(), tmp_path / "nope") == ({}, [])


# --- nothing in the folder goes unremarked ---------------------------------


def test_an_unused_article_pdf_is_reported_like_an_orphan_supplement(tmp_path):
    """The asymmetry this removes: a supplement-named orphan was reported, an
    unmatched article PDF was ignored without a word, and the user could not
    tell the difference between "used" and "silently skipped"."""
    from papertrace.models import Supplement
    from papertrace.refs import unused_provided

    d = tmp_path / "src"
    used = _pdf(d / "pyrros-2023.pdf", title="Opportunistic detection")
    used_sup = _pdf(d / "pyrros-2023-supplement.pdf", title="Supplementary Information")
    stray = _pdf(d / "some-other-paper.pdf", title="Cardiac magnetic resonance")

    entries = _entries()
    entries[0].pdf_path = str(used)
    entries[0].supplements = [Supplement("pyrros-2023-supplement", str(used_sup))]

    out = dict(unused_provided(entries, d))
    assert list(out) == [stray], out
    assert "could not tell" in out[stray].lower()


def test_a_reason_from_the_inference_pass_is_carried_through(tmp_path):
    """`identify_by_content` already knows *why* it refused a file — an
    ambiguous title is a different problem from an unrecognisable one, and the
    fixes differ."""
    from papertrace.refs import unused_provided

    d = tmp_path / "src"
    f = _pdf(d / "download.pdf", title="UK Biobank: An Open Access Resource")
    out = dict(unused_provided(_entries(), d, reasons={f: "its title matches [2] and [9]"}))
    assert out[f] == "its title matches [2] and [9]"


def test_a_supplement_with_no_article_keeps_its_own_reason(tmp_path):
    """Carried over from 0.6.0: this reason names the fix, which is to supply
    the article, and it must not be flattened into the generic one."""
    from papertrace.refs import unused_provided

    d = tmp_path / "src"
    orphan = _pdf(d / "littlejohns-2020-appendix.pdf", title="Appendix")
    entries = [RefEntry(num="3", raw="Littlejohns TJ (2020) UK Biobank imaging",
                        slug="littlejohns-2020", status="paywalled")]
    out = dict(unused_provided(entries, d))
    assert "[3]" in out[orphan] and "not available" in out[orphan]


# --- wired into resolution -------------------------------------------------


def _no_network(monkeypatch):
    """The online chain must not be reached for a file we already identified."""
    from papertrace import refs as refs_mod

    def _boom(*a, **k):
        raise AssertionError("the online chain was reached for an identified file")

    monkeypatch.setattr(refs_mod, "_resolve_by_retrieval", _boom)


def test_resolve_all_uses_a_content_identified_file(tmp_path, monkeypatch):
    from papertrace.refs import resolve_all

    _no_network(monkeypatch)
    d = tmp_path / "src"
    f = _pdf(d / "s41467-023-39631-x.pdf",
             title="Opportunistic detection of type 2 diabetes using deep learning "
                   "from frontal chest radiographs",
             doi="10.1038/s41467-023-39631-x")
    entries = [_entries()[0]]
    resolve_all(entries, tmp_path / "dest", "t@example.org", provided_dir=d)

    e = entries[0]
    assert (e.status, e.resolver) == ("provided", "user")
    assert e.pdf_path == str(f)
    # verified by construction: a positive title or DOI match is what chose it
    assert e.title_check == "verified"


def test_the_reason_says_which_signal_identified_it(tmp_path, monkeypatch):
    """Provenance, not decoration: a DOI is exact and a title is a judgement,
    and a reader deciding how much to trust the verdict needs to know which."""
    from papertrace.refs import resolve_all

    _no_network(monkeypatch)
    d = tmp_path / "src"
    _pdf(d / "by-doi.pdf", title="Opportunistic detection of type 2 diabetes using "
                                 "deep learning from frontal chest radiographs",
         doi="10.1038/s41467-023-39631-x")
    _pdf(d / "by-title.pdf",
         title="UK Biobank: An Open Access Resource for Identifying the Causes of a "
               "Wide Range of Complex Diseases of Middle and Old Age")
    entries = _entries()
    entries[1].doi = None
    resolve_all(entries, tmp_path / "dest", "t@example.org", provided_dir=d)

    assert "DOI" in entries[0].reason, entries[0].reason
    assert "title" in entries[1].reason, entries[1].reason
    assert "by-doi.pdf" in entries[0].reason and "by-title.pdf" in entries[1].reason


def test_a_filename_match_still_wins_over_content(tmp_path, monkeypatch):
    """The filename is the user's own assertion about this file. Content fills
    the gap it leaves; it never overrules it."""
    from papertrace.refs import resolve_all

    _no_network(monkeypatch)
    d = tmp_path / "src"
    named = _pdf(d / "pyrros-2023.pdf",
                 title="Opportunistic detection of type 2 diabetes using deep learning "
                       "from frontal chest radiographs")
    _pdf(d / "s41467-023-39631-x.pdf",
         title="Opportunistic detection of type 2 diabetes using deep learning "
               "from frontal chest radiographs",
         doi="10.1038/s41467-023-39631-x")
    entries = [_entries()[0]]
    resolve_all(entries, tmp_path / "dest", "t@example.org", provided_dir=d)

    assert entries[0].pdf_path == str(named)
    assert "sources folder" in entries[0].reason


def test_a_second_copy_of_an_already_matched_paper_says_so(tmp_path, monkeypatch):
    """Realistic: the user has both the reference-manager export and the
    publisher download of the same paper. The spare is not a mystery file and
    must not be reported as one."""
    from papertrace.refs import resolve_all, unused_provided

    _no_network(monkeypatch)
    d = tmp_path / "src"
    _pdf(d / "pyrros-2023.pdf", title="Opportunistic detection of type 2 diabetes "
                                      "using deep learning from frontal chest radiographs")
    spare = _pdf(d / "s41467-023-39631-x.pdf",
                 title="Opportunistic detection of type 2 diabetes using deep learning "
                       "from frontal chest radiographs",
                 doi="10.1038/s41467-023-39631-x")
    entries = [_entries()[0]]
    resolve_all(entries, tmp_path / "dest", "t@example.org", provided_dir=d)

    out = dict(unused_provided(entries, d))
    assert list(out) == [spare]
    assert "[1]" in out[spare] and "already" in out[spare], out[spare]
