# Release checklist

PaperTrace is **not on PyPI**. It installs from a clone. This checklist exists
so that when a release does happen, nothing is published on a guess.

**Nothing here is automated, and nothing in this repo publishes anything.**

## Version

The version has exactly one home: `src/papertrace/__init__.py`. `pyproject.toml`
reads it via `[tool.hatch.version]`, so there is nothing to keep in sync.

- [ ] Bump `__version__` in `src/papertrace/__init__.py`.
- [ ] Update `version:` in `CITATION.cff`.
- [ ] Add a `CHANGELOG.md` section and its link reference at the bottom.
- [ ] Confirm nothing else hard-codes a number:
      `grep -rn "0\.[0-9]\.[0-9]" --include="*.py" --include="*.toml" --include="*.md" --include="*.j2" . | grep -v CHANGELOG`

## Verify

Use an interpreter that has the dev extras (`~/anaconda3/bin/python3` on the
maintainer's machine; `python3` on PATH may be a different install).

- [ ] `pytest -q` — `tests/` and `evals/tests/`, all offline.
- [ ] `ruff check src tests scripts evals`
- [ ] `uv build` — produces both sdist and wheel. (`python -m build` also works
      if `build` is installed; `uv` needs no extra dependency.)
- [ ] Templates really are in the wheel — they ship as package data, and a
      wheel without them cannot render a report:
      ```bash
      python -c "import zipfile,glob; print([n for n in zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist() if 'templates' in n])"
      ```
- [ ] Clean-environment smoke test:
      ```bash
      python -m venv /tmp/pt-smoke && /tmp/pt-smoke/bin/pip install dist/papertrace-*.whl
      /tmp/pt-smoke/bin/papertrace --help
      /tmp/pt-smoke/bin/python -c "from papertrace.report import _env; _env().get_template('report.md.j2')"
      ```
- [ ] The sdist carries the evaluation harness it advertises — not just its
      README. v0.3.1 shipped `evals/README.md` alone, so the packaged file
      documented a runner the archive did not contain:
      ```bash
      tar -tzf dist/papertrace-*.tar.gz | grep -c '^papertrace-[^/]*/evals/'
      # 1 means the unanchored-glob bug is back; expect dozens
      ```
- [ ] No run artefacts, caches or machine-local files travelled:
      ```bash
      tar -tzf dist/papertrace-*.tar.gz \
        | grep -E 'evals/runs|__pycache__|\.pyc$|\.DS_Store|\.serena|/case/|/demo_case/|settings\.local'
      # must print nothing
      ```
- [ ] The packaged `evals/README.md` instruction actually runs **from inside
      the archive**. This is the check that matters: the others confirm files
      are present, this one confirms they work.
      ```bash
      rm -rf /tmp/papertrace-* /tmp/pt-sdist-evalout
      tar -xzf dist/papertrace-*.tar.gz -C /tmp && cd /tmp/papertrace-*/
      python evals/runners/score_only.py --gold evals/gold/demo_v1.gold.json \
          --results evals/gold/demo_v1.observed.json --out /tmp/pt-sdist-evalout
      python -m pytest -q   # testpaths names evals/tests; it must collect and pass
      ```
      If this fails, check whether the same failure reproduces in the working
      tree before blaming packaging — a sdist built minutes before an unrelated
      edit will carry the older snapshot.
- [ ] `pytest tests/test_packaging.py` — the include/exclude tables still match
      what the docs advertise. This is what makes the three checks above a
      regression guard rather than a ritual, and it is the piece that was
      missing when the bug shipped.
- [ ] Nothing secret or machine-local is in the archive:
      ```bash
      grep -rInE 'sk-ant-|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY' \
        src prompts schemas evals tests examples *.md
      tar -xzf dist/papertrace-*.tar.gz -C /tmp && grep -rIl '/Users/' /tmp/papertrace-*/
      # both must print nothing
      ```
- [ ] CI green on all five Python versions (3.10–3.14).

### A note on what the sdist deliberately leaves out

`docs/` is 3.8 MB and is **not** shipped, and neither is `assets/`. Every
image `README.md` references now uses an absolute
`raw.githubusercontent.com` URL, which is the fix — not shipping either
directory. That matters because `[project].readme` makes `README.md` the long
description, so a repo-relative path resolves neither from an extracted sdist
nor on a PyPI project page.

**Check this again whenever an image is added**, and check it two ways — the
two failures are different and each has already happened once.

*Wrong kind of path.* The four `docs/*.png` references were converted to
absolute URLs and `assets/logo.png` was missed, so the one broken image was the
first thing on the page:

```bash
grep -oE 'src="[^"]*\.png"' README.md | grep -v raw.githubusercontent   # must be empty
```

*Right kind of path, nothing behind it.* Every URL is pinned to `/main/`, so an
image added on a feature branch is a 404 until that branch merges — correct URL,
correct file, no bytes. `docs/wizard.png` was in exactly this state while
`pre-announcement-hardening` was open. The grep above cannot see it; only a
fetch can:

```bash
grep -oE 'https://raw\.githubusercontent\.com/[^"]*\.png' README.md | sort -u |
  while read -r u; do printf '%s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "$u")" "$u"; done
# every line must start with 200
```

Run the fetch **after** merging to `main` and before uploading, because that is
the only point at which both the URL and the ref are final. A pre-merge 404 here
is expected and self-heals; a post-merge one is a broken release.

Hatchling honours `.gitignore` by default, which is why
`examples/demo/demo_manuscript.pdf` (matched by the `/examples` include, caught
by the blanket `*.pdf` ignore) does not travel. The `exclude` table does not
rely on that behaviour — it names every artefact class explicitly — but be
aware that a file's presence in the archive still depends on VCS state as well
as on these tables.

## Documentation honesty pass

The point of a release is that someone reads the README and believes it.

- [ ] Every technical statement still matches the implementation. In
      particular: coverage audits **bracketed numeric** labels only; batch
      judges a co-cited claim against **every retrievable** cited source and
      takes the most adverse verdict as the headline; the model reads extracted
      text with page markers, **not** page images.
- [ ] No accuracy, benchmark or performance figure is claimed anywhere.
- [ ] No hard-coded test count (it goes stale; the CI badge is the signal).
- [ ] `examples/demo/output/report.md` still reflects what a re-run produces —
      or the README says plainly that it does not. Regenerating it needs a live
      model run; run it with `-f viewer --png` so the showcase keeps its
      `report_viewer.html` and its terminal shot, refresh `docs/viewer_*.png`
      with `python scripts/make_viewer_shots.py demo_case/out/report_viewer.html`,
      and correct the run date and docling version in the README's note. Copy
      everything from `<case>/out/` **except `assets/`** — the fonts are already
      package data, and `examples/demo/README.md` says so.
- [ ] `evals/DESIGN.md` still describes the shipped harness, and any gold set
      marked `kind: benchmark` genuinely satisfies the labelling policy there.

## Publishing — requires an explicit decision

- [ ] Confirm the license text and third-party font licenses
      (`src/papertrace/templates/assets/*-OFL.txt`) ship in both artefacts.
- [ ] Tag `v<version>` and push the tag.
- [ ] Draft the GitHub release from the CHANGELOG section.
- [ ] **PyPI:** upload to TestPyPI first, install from it into a clean venv, run
      the smoke test above, and only then upload to PyPI. Until that has been
      done once, the README must keep saying the tool installs from a clone.

## uv / uvx

`uvx papertrace` cannot work before a PyPI release — there is nothing to fetch.

Running from a clone with uv **is verified working** (checked 2026-08-26, uv
0.11.6, CPython 3.13):

```bash
uv run --with-editable . papertrace --help
```

`uv build` is also the build path used above, so uv needs no extra dependency
for either job. Do not add a `uvx papertrace` line to the README until a real
PyPI release exists and that exact command has been run and seen to work.
