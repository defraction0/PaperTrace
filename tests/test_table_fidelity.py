"""A table cell the converter dropped is a fidelity loss, not console noise.

docling's TableFormer post-processor logs through
`logging.getLogger("MatchingPostProcessor")` — a bare top-level name whose
parent is `root`, which it levels itself and to which it attaches its own
`StreamHandler(sys.stdout)`. So `_quiet_third_party_loggers`, which levels
`"docling"`, could never reach it, and a real audit printed ~200 lines of:

    MatchingPostProcessor WARNING  Orphan pdf_cell 186 recovered to col=6 by
                                   nearest-column fallback (row=11, x=620.5)

Those are repairs and they are noise. But buried in them was one that is not:

    5 of 65 pdf cells matched neither a row nor a column band of the 24x4 grid
    and were dropped from the table

That is text missing from a cited source's table, reported only as a stray line
from a third-party library. Silencing the logger without surfacing it would hide
a fidelity loss, which is the one thing this codebase must not do — so the same
filter that quiets the noise captures the rest.

**The classification defaults to SURFACING.** Only the self-describing recovery
messages are treated as noise; anything else that logger emits is captured and
reported. That is deliberate: the emitter lives in `docling-ibm-models`, whose
message wording is not ours and differs between versions — the one that produced
the audit above does not exist in the version installed here. Matching the loss
message literally would silently under-report on a version we have not seen;
matching the *repair* message and surfacing the remainder fails the safe way.

Offline: these tests emit records on that logger by name. docling is never
imported and never runs.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.ingest.docling_ import capture_table_warnings  # noqa: E402
from papertrace.models import Block, SourceMap  # noqa: E402

LOGGER = "MatchingPostProcessor"
RECOVERED = ("Orphan pdf_cell 186 recovered to col=6 by nearest-column fallback "
             "(row=11, x=620.5, dist=53.1)")
RECOVERED_ROW = ("Orphan pdf_cell 63 recovered to row=8 by nearest-row fallback "
                 "(col=1, y=1131.4, dist=82.3)")
DROPPED = ("5 of 65 pdf cells matched neither a row nor a column band of the "
           "24x4 grid and were dropped from the table")


def _emit(*messages, level=logging.WARNING):
    log = logging.getLogger(LOGGER)
    for m in messages:
        log.log(level, m)


def test_a_repaired_cell_is_noise_and_is_not_captured():
    with capture_table_warnings() as notes:
        _emit(RECOVERED, RECOVERED_ROW)
    assert notes == [], notes


def test_a_dropped_cell_is_captured_verbatim():
    """The message is the finding: it says how many cells, of how many, and the
    grid they did not fit. Paraphrasing it would lose all three."""
    with capture_table_warnings() as notes:
        _emit(RECOVERED, DROPPED, RECOVERED_ROW)
    assert notes == [DROPPED], notes


def test_an_unrecognised_warning_is_captured_rather_than_dropped():
    """The emitter is a third-party package whose wording changes between
    versions — the message above does not exist in the version installed here.
    So anything that is not a recognised repair is surfaced."""
    with capture_table_warnings() as notes:
        _emit("some future table warning nobody here has seen")
    assert notes == ["some future table warning nobody here has seen"], notes


def test_nothing_from_that_logger_reaches_the_console(caplog):
    """The ticker is unreadable otherwise. Captured, not printed — the report is
    where a fidelity loss belongs."""
    with capture_table_warnings():
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _emit(RECOVERED, DROPPED)
    assert caplog.records == [], [r.getMessage() for r in caplog.records]


def test_info_chatter_is_not_mistaken_for_a_loss():
    with capture_table_warnings() as notes:
        _emit("predicting table structure", level=logging.INFO)
    assert notes == [], notes


def test_the_filter_is_removed_afterwards():
    """It is installed for one conversion. Leaking it would swallow warnings
    from every later one, including losses."""
    before = list(logging.getLogger(LOGGER).filters)
    with capture_table_warnings():
        pass
    assert list(logging.getLogger(LOGGER).filters) == before


def test_it_is_removed_even_when_the_conversion_raises():
    before = list(logging.getLogger(LOGGER).filters)
    try:
        with capture_table_warnings():
            raise RuntimeError("conversion blew up")
    except RuntimeError:
        pass
    assert list(logging.getLogger(LOGGER).filters) == before


# --- it reaches the reader ------------------------------------------------


def _rendered(tmp_path, losses):
    from papertrace.models import ClaimResult, RunResults
    from papertrace.report import write_reports

    claim = ClaimResult(id=1, claim="X causes Y.", location="Intro", refs=["1"],
                        verdict="supported", source_slug="a-2020", source_page=2)
    results = RunResults(manuscript="m.pdf", date="2026-01-01", claims=[claim],
                         source_table_warnings=losses)
    write_reports(results, None, tmp_path, png=False)
    return {p.name: p.read_text() for p in tmp_path.glob("report*.*")}


def test_a_dropped_cell_reaches_every_format(tmp_path):
    from papertrace.disclosures import TABLE_LOSS_TOKEN

    rendered = _rendered(tmp_path, {"xue-2024": [DROPPED]})
    assert len(rendered) == 4, sorted(rendered)
    for name, body in rendered.items():
        assert TABLE_LOSS_TOKEN in body, f"{name} does not disclose the loss"


def test_the_count_the_converter_gave_is_not_paraphrased_away(tmp_path):
    """"5 of 65" is the finding. A sentence saying only "cells were dropped"
    tells the reader nothing about how much of the table is missing."""
    rendered = _rendered(tmp_path, {"xue-2024": [DROPPED]})
    # the terminal renders `short` by design, as `source_fidelity` does, so the
    # count and the slug reach the two formats that render `text`
    for name in ("report.md", "report_editor.html"):
        assert "5 of 65" in rendered[name], name
        assert "xue-2024" in rendered[name], name


def test_a_run_that_recorded_nothing_grows_no_warning(tmp_path):
    """An empty dict means the run did not record it — a results.json from
    before this — not that every source's tables came through whole."""
    from papertrace.disclosures import TABLE_LOSS_TOKEN

    for name, body in _rendered(tmp_path, {}).items():
        assert TABLE_LOSS_TOKEN not in body, name


def test_a_source_watched_and_clean_grows_no_warning(tmp_path):
    from papertrace.disclosures import TABLE_LOSS_TOKEN

    for name, body in _rendered(tmp_path, {"xue-2024": []}).items():
        assert TABLE_LOSS_TOKEN not in body, name


# --- wire format ----------------------------------------------------------


def test_table_warnings_round_trip_and_absence_means_not_recorded(tmp_path):
    """`None` is a third answer. An empty list says the converter was watched
    and dropped nothing; `None` says nobody watched — a map written before this,
    or a backend with no table model. Reading `None` as "none dropped" would be
    the invented reassurance this project exists to avoid."""
    import json

    smap = SourceMap(doc="x.pdf", pages=1, table_warnings=[DROPPED],
                     blocks=[Block("block_0001", "table", 1, (0, 0, 1, 1), [], "| a |")])
    smap.to_json(tmp_path / "m.json")
    assert SourceMap.from_json(tmp_path / "m.json").table_warnings == [DROPPED]

    (tmp_path / "old.json").write_text(json.dumps({
        "doc": "x.pdf", "pages": 1,
        "blocks": [{"id": "block_0001", "type": "text", "page": 1,
                    "bbox": [0, 0, 1, 1], "text": "t"}],
    }))
    assert SourceMap.from_json(tmp_path / "old.json").table_warnings is None

    clean = SourceMap(doc="x.pdf", pages=1, table_warnings=[])
    clean.to_json(tmp_path / "clean.json")
    assert SourceMap.from_json(tmp_path / "clean.json").table_warnings == []
