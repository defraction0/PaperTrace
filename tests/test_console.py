"""What the console says while a run happens — the surface read first.

A fact stated once, in the middle of a third-party log dump, is not disclosed in
any useful sense. Two things went wrong on a real audit: the verdict tally
omitted a whole bucket, so it accounted for 31 of 34 claims; and the line naming
the ingest backend arrived after thirteen lines of `[INFO] [RapidOCR]` noise and
was never repeated across four minutes of work.
"""

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.cli import _verdict_line  # noqa: E402

# --- the tally has to account for every claim ------------------------------


def _counts(**kw) -> dict:
    base = {"supported": 0, "partial": 0, "contradicted": 0,
            "not_addressed": 0, "not_retrieved": 0, "unchecked": 0}
    base.update(kw)
    return base


def test_the_tally_counts_not_addressed():
    """A real run printed `0 supported · 19 partial · 0 contradicted · 12 not
    retrieved` for 34 claims. results.json had `not_addressed: 3` — the verdict
    was added to all three report templates and missed here, on the surface a
    user reads first."""
    line = _verdict_line(_counts(partial=19, not_addressed=3, not_retrieved=12))
    assert "3 does not address" in line


@pytest.mark.parametrize("counts", [
    _counts(supported=5, partial=2, contradicted=1, not_retrieved=3),
    _counts(partial=19, not_addressed=3, not_retrieved=12),
    _counts(supported=1, not_addressed=1, unchecked=2),
    _counts(not_addressed=7),
])
def test_the_printed_buckets_sum_to_every_claim(counts):
    """The arithmetic a reader does by eye must work. Any bucket that is
    non-zero and unprinted makes the line quietly wrong."""
    import re

    line = _verdict_line(counts)
    printed = sum(int(n) for n in re.findall(r"(\d+)\s+(?:supported|partial|"
                                             r"contradicted|does not address|"
                                             r"not retrieved|unchecked)", line))
    assert printed == sum(counts.values()), line


def test_a_run_without_the_new_verdicts_prints_the_original_four():
    """No `not_addressed`, no `unchecked` — the line must not grow empty
    buckets for a run that has none, or every ordinary audit gains noise."""
    line = _verdict_line(_counts(supported=3, partial=1, contradicted=2, not_retrieved=1))
    assert "does not address" not in line
    assert "unchecked" not in line


# --- provenance, restated where it can be seen ----------------------------


def _results(tmp_path: Path, converter: str) -> Path:
    out = tmp_path / "out"
    out.mkdir(parents=True)
    payload = {
        "manuscript": "m.pdf", "checker": "claude -p", "date": "2026-08-29",
        "refs": {"total": 1, "available": 1}, "converter": converter,
        "counts": {}, "claims": [], "uncited": [], "coverage": {}, "truncated": {},
    }
    (out / "results.json").write_text(json.dumps(payload))
    return tmp_path


@pytest.mark.parametrize("converter", ["docling 2.118.1", "pymupdf"])
def test_report_names_the_backend_that_read_the_paper(tmp_path, converter):
    """`papertrace report -c case` printed no provenance at all: only `ingest`
    ever did, so a standalone render told the reader nothing about how the
    paper had been read."""
    from typer.testing import CliRunner

    from papertrace.cli import app

    case = _results(tmp_path, converter)
    res = CliRunner().invoke(app, ["report", "-c", str(case), "--no-png"])
    assert res.exit_code == 0, res.output
    out = " ".join(res.output.split())
    assert converter in out


def test_report_says_cited_sources_are_read_as_flat_text(tmp_path):
    """Sources are always ingested with the flat backend, deliberately — but the
    console has never said so anywhere, and a reader cannot infer it from a line
    that names docling for the manuscript."""
    from typer.testing import CliRunner

    from papertrace.cli import app

    case = _results(tmp_path, "docling 2.118.1")
    out = " ".join(CliRunner().invoke(app, ["report", "-c", str(case),
                                            "--no-png"]).output.split())
    assert "cited sources" in out.lower()
    assert "flat" in out.lower()


# --- the noise, silenced at the only seam that works ----------------------


def test_third_party_info_is_silenced_only_inside_the_conversion():
    """RapidOCR creates its logger, sets its own level and adds its own handler
    while docling builds the OCR models — all inside one call, so there is no
    seam to re-apply a level in. `logging.disable` is the mechanism that works
    regardless; what matters is that it is scoped and restored.
    """
    from papertrace.ingest.docling_ import _silence_model_stack

    log = logging.getLogger("papertrace.test.rapidocr-like")
    log.setLevel(logging.INFO)

    with _silence_model_stack():
        assert not log.isEnabledFor(logging.INFO)
        assert log.isEnabledFor(logging.ERROR), "errors must still get through"
    assert log.isEnabledFor(logging.INFO), "the suppression must not leak"


def test_the_silence_is_restored_even_when_the_conversion_raises():
    from papertrace.ingest.docling_ import _silence_model_stack

    log = logging.getLogger("papertrace.test.raises")
    log.setLevel(logging.INFO)
    with pytest.raises(RuntimeError):
        with _silence_model_stack():
            raise RuntimeError("docling blew up")
    assert log.isEnabledFor(logging.INFO)


def test_set_logs_is_not_called_when_we_set_the_env_var(monkeypatch):
    """torch prints `Using TORCH_LOGS environment variable ..., ignoring call to
    set_logs` when both are used — a warning that existed only because we did
    both. The env var is the one that works pre-import, so it wins."""
    import papertrace.ingest.docling_ as mod

    monkeypatch.setenv("TORCH_LOGS", "-dynamo,-inductor")
    called: list[str] = []
    monkeypatch.setattr(mod, "_set_torch_logs", lambda: called.append("x"))
    mod._quiet_third_party_loggers()
    assert called == [], "set_logs must not be called when TORCH_LOGS is set"


# --- the live progress marks must show the verdict just returned -----------


def _mid_run_claim(slug: str, verdict: str):
    """A claim as it exists *during* `check`: the judgement for this source is
    in, but `apply_headline()` has not run yet, so `.verdict` is still default.
    """
    from papertrace.models import ClaimResult, SourceJudgement

    return ClaimResult(
        id=1, claim="c", location="p1/block_0001", refs=["1"],
        judgements=[SourceJudgement(source_slug=slug, ref="1", verdict=verdict)],
    )


def test_progress_marks_read_the_judgement_not_the_headline():
    """`tick` read `c.verdict`, but multi-source checking only assigns it in
    `apply_headline()` — which runs after *every* group. So each group printed
    the field's default, `not_retrieved`, whose glyph is `○`: the demo judged
    2 supported and 2 contradicted while the console showed `○ ○ ○ ○`.
    """
    from papertrace.cli import _tick_marks

    group = [_mid_run_claim("pyrros-2023", "supported"),
             _mid_run_claim("pyrros-2023", "contradicted")]
    marks = _tick_marks("pyrros-2023", group)
    assert "green" in marks and "red" in marks, marks


def test_progress_marks_distinguish_every_judgement_verdict():
    """A single fallback glyph for supported/partial/contradicted/not_addressed
    makes the running display worthless — and `○` already means not retrieved on
    the final tally, so the fallback actively misreports."""
    from papertrace.cli import _tick_marks

    seen = {v: _tick_marks("s", [_mid_run_claim("s", v)])
            for v in ("supported", "partial", "contradicted", "not_addressed")}
    assert len(set(seen.values())) == 4, seen


def test_a_source_with_no_judgement_yet_is_not_claimed_as_judged():
    """A claim in the group whose judgement for *this* slug is missing must not
    borrow another source's verdict."""
    from papertrace.cli import _tick_marks

    marks = _tick_marks("other-2020", [_mid_run_claim("pyrros-2023", "supported")])
    assert "green" not in marks, marks
