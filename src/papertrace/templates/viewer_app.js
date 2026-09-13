/* PaperTrace report viewer — the half that draws.

   Everything decided lives in viewer_logic.js; this file only puts it on the
   page and reacts to the reader. State is a handful of values; every change
   re-renders the header's filter row and the right-hand panel from them, and
   restyles the audited spans in the manuscript column (which is built once,
   because rebuilding it would lose the reader's scroll position).

   Reviewed marks and the theme are the reader's own and stay in this browser
   (localStorage); export them from the Export menu.
*/
(function () {
  'use strict';
  const PT = window.PaperTraceLogic;
  // `markStyle` is how audited sentences are marked: underline (the default),
  // highlight, or margin (no mark until selected). `showCaveats` hides the
  // per-claim caveat box when false.
  const OPTIONS = { markStyle: 'underline', showCaveats: true };
  const REVIEW_ORDER = ['supported', 'partial', 'contradicted', 'not_addressed'];

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const vcls = (v) => PT.VMAP[v] ? 'v-' + v : 'v-other';
  const refsLabel = (refs) => refs.map(x => '[' + x + ']').join(', ');
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  let data = null;
  const state = {
    tab: 'summary', selected: null, verdicts: new Set(PT.ALL_KEYS), section: 'all', query: '',
    reviewed: {}, theme: 'light', exportOpen: false, lightbox: null,
  };

  // ---- boot -----------------------------------------------------------------

  function fail(msg) {
    $('pt-article').innerHTML = `<div class="fail">Could not load the case: ${esc(msg)}</div>`;
  }

  function load() {
    let payload;
    try { payload = JSON.parse($('pt-data').textContent); }
    catch (e) { fail('the embedded case data could not be parsed — ' + e.message); return; }
    try { data = PT.buildModel(payload); }
    catch (e) { fail('the case data could not be read — ' + e.message); return; }
    try { state.reviewed = JSON.parse(localStorage.getItem('papertrace:reviewed:' + data.caseId) || '{}') || {}; } catch (e) { state.reviewed = {}; }
    try { const t = localStorage.getItem('papertrace:theme'); if (t === 'dark' || t === 'light') state.theme = t; } catch (e) { /* no storage: light */ }
    document.body.dataset.theme = state.theme;
    document.body.dataset.mark = OPTIONS.markStyle;
    document.title = data.title + ' — PaperTrace';
    $('pt-title').textContent = data.title;
    $('pt-meta').textContent = `${data.meta.date} · ${data.meta.checker} · ${data.meta.refs.available} / ${data.meta.refs.total} cited sources retrieved${data.refsSkipped ? ` · ${data.refsSkipped} skipped on request` : ''}`;
    if (data.scope) {
      // the header is read first, so the limit is flagged there; the full
      // sentence is in the manuscript subline and the summary's last card
      const tag = document.createElement('span');
      tag.className = 'scope-tag';
      tag.textContent = ' · ⚠ audit limited on request';
      tag.title = data.scope.short;
      $('pt-meta').appendChild(tag);
    }
    const sel = $('pt-section');
    const secCounts = {};
    data.claims.forEach(c => { secCounts[c.section] = (secCounts[c.section] || 0) + 1; });
    Object.keys(secCounts).forEach(s => {
      const o = document.createElement('option');
      o.value = s; o.textContent = `${s} (${secCounts[s]})`;
      sel.appendChild(o);
    });
    renderArticle();
    renderAll();
    bind();
  }

  // ---- derived ---------------------------------------------------------------

  const all = () => data.claims;
  const byKey = (k) => data.claims.find(c => c.key === String(k));
  const filters = () => ({ verdicts: state.verdicts, section: state.section, query: state.query });
  const visible = () => data.claims.filter(c => PT.matches(c, filters()));
  const allOn = () => state.verdicts.size === PT.ALL_KEYS.length;
  const filtersActive = () => !allOn() || state.section !== 'all' || !!state.query.trim();
  const counts = () => Object.fromEntries(PT.ALL_KEYS.map(k => [k, data.claims.filter(c => c.verdict === k).length]));
  const reviewedCount = () => data.claims.filter(c => state.reviewed[c.key]).length;
  const isGap = (c) => c.verdict === 'not_retrieved' || c.verdict === 'unchecked';

  // ---- rendering: header -----------------------------------------------------

  function renderHeader() {
    const n = counts();
    const chips = PT.VERDICTS.filter(v => n[v.key] > 0 || v.key === 'contradicted').map(v => {
      const on = state.verdicts.has(v.key);
      return `<button class="chip ${vcls(v.key)} ${on ? 'on' : ''}" data-act="chip" data-v="${v.key}" title="${esc(v.label)} — click to show only this; shift-click to toggle"><span class="dot"></span>${esc(v.short)}<span class="n">${n[v.key]}</span></button>`;
    }).join('');
    const live = !allOn();
    $('pt-chips').innerHTML = chips + `<button class="pill-all ${live ? 'live' : ''}" data-act="all" ${live ? '' : 'disabled'} title="Show all claim types">All</button>`;
    $('pt-section').value = state.section;
    const vis = visible(), total = all().length, rv = reviewedCount();
    const shown = filtersActive() ? `${vis.length} of ${total} claims shown` : `${total} claims`;
    $('pt-status').innerHTML = `<span>${esc(shown)}</span><span class="vdiv2"></span><span class="rv"><span class="bar"><span style="width:${total ? Math.round(100 * rv / total) : 0}%"></span></span>${rv} / ${total} reviewed</span>`;
    $('pt-theme').textContent = state.theme === 'dark' ? 'Light' : 'Dark';
    $('pt-menu').hidden = !state.exportOpen;
  }

  // ---- rendering: the manuscript column (built once) -----------------------

  function segHtml(s) {
    if (!s.claim) return esc(s.text);
    const keys = s.claims || [s.claim];
    const c = byKey(s.claim);
    const dash = c.verdict === 'not_retrieved' || c.verdict === 'unchecked' ? ' dotted' : c.verdict === 'uncited' ? ' dashed' : '';
    const title = keys.length > 1 ? ` title="${keys.length} claims share this sentence — click again to cycle"` : '';
    return `<span class="seg ${vcls(c.verdict)}${dash}" data-act="seg" data-claim="${esc(s.claim)}" data-claims="${esc(keys.join(','))}"${title}>${esc(s.text)}</span>`;
  }

  function renderArticle() {
    const parts = [];
    parts.push(`<h1 class="ms-title">${esc(data.title)}</h1>`);
    const sub = (data.synthetic
      ? 'Audited sentences only — the case folder has no ingest/manuscript/annotated.md, so the full manuscript is not shown · '
      : '') + `Ingest ${esc(data.meta.converter || 'not recorded')} · ${data.anchored} of ${all().length} audited sentences located in the text · underlines mark audited sentences; click one to see its evidence.`
      + (data.scope ? ` <span class="scope-tag">⚠ ${esc(data.scope.short)}.</span>` : '');
    parts.push(`<p class="ms-sub">${sub}</p>`);
    for (const b of data.blocks) {
      const id = `blk-${esc(b.id)}`;
      const pg = b.page ? `<span class="pg">p.${b.page}</span>` : '';
      if (b.type === 'heading') parts.push(`<div class="ms-h" id="${id}"><h2>${esc(b.text)}</h2>${pg}</div>`);
      else if (b.type === 'para') {
        const cls = ['ms-p', b.text.startsWith('• ') ? 'bullet' : '', b.text.includes('\n') ? 'multi' : ''].filter(Boolean).join(' ');
        parts.push(`<p class="${cls}" id="${id}">${(b.segments || [{ text: b.text }]).map(segHtml).join('')}</p>`);
      } else if (b.type === 'figure') parts.push(`<div class="fig" id="${id}"><b>Figure</b> · ${esc(b.text)}</div>`);
      else if (b.type === 'table') parts.push(`<details class="tbl" id="${id}"><summary><b>Table</b> · ${esc(b.caption)} <span class="id">${esc(b.id)} · p.${b.page}</span></summary><pre>${esc(b.text)}</pre></details>`);
      else if (b.type === 'ref') {
        const slug = Object.keys(data.slugRef).find(s => String(data.slugRef[s]) === String(b.num) && data.slugKind[s] !== 'supplement');
        const tag = slug ? `<span class="tag">${esc(slug)}</span>` : (data.missing[b.num] ? `<span class="tag nr">not retrieved</span>` : '');
        parts.push(`<div class="ref" id="${id}"><span class="num">${b.num}</span><span class="txt">${esc(b.text)}</span>${tag}</div>`);
      }
    }
    $('pt-article').innerHTML = parts.join('');
  }

  function updateSegments() {
    const vis = new Set(visible().map(c => c.key));
    const active = filtersActive();
    document.querySelectorAll('#pt-article .seg').forEach(el => {
      const keys = el.dataset.claims.split(',');
      el.classList.toggle('on', keys.includes(String(state.selected)));
      el.classList.toggle('dim', active && !keys.some(k => vis.has(k)));
    });
  }

  // ---- rendering: the right panel ---------------------------------------------

  function rowHtml(c, withSub) {
    const on = c.key === String(state.selected), rv = !!state.reviewed[c.key];
    const sub = withSub ? [
      c.location,
      c.refs.length ? 'cites ' + refsLabel(c.refs) : (c.kind === 'uncited' ? 'no citation' : (c.ownSupplement ? "this paper's own supplement" : '')),
      isGap(c) ? PT.gapReason(c) : '',
    ].filter(Boolean).join(' · ') : '';
    return `<button class="row ${vcls(c.verdict)} ${withSub ? '' : 'single'} ${on ? 'on' : ''} ${rv ? 'rv' : ''}" data-act="select" data-key="${esc(c.key)}"><span class="dot"></span><span class="id">${esc(c.id)}</span><span class="txt"><span>${esc(c.claim)}</span>${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</span><span class="ck">${rv ? '✓' : ''}</span></button>`;
  }

  function discItem(d) {
    const rows = d.rows && d.rows.length ? `<ul>${d.rows.slice(0, 25).map(r => `<li>${esc(r)}</li>`).join('')}${d.rows.length > 25 ? `<li>… ${d.rows.length - 25} more in results.json</li>` : ''}</ul>` : '';
    return `<li><span class="lv ${d.level === 'warn' ? 'warn' : ''}">${d.level === 'warn' ? '⚠' : '·'}</span><span>${esc(d.text)}${rows}</span></li>`;
  }

  function summaryHtml() {
    const n = counts();
    const cards = PT.VERDICTS.filter(v => n[v.key] > 0 || v.key === 'contradicted' || v.key === 'supported')
      .map(v => `<button class="card-v ${vcls(v.key)}" data-act="card" data-v="${v.key}"><span class="big">${n[v.key]}</span><span class="lbl">${esc(v.label)}</span></button>`).join('');

    // the claim map
    const groups = PT.mapGroups(data);
    const untouched = groups.reduce((k, g) => k + g.cells.filter(c => c.kind === 'para').length, 0);
    const touched = new Set(groups.flatMap(g => g.cells.filter(c => c.kind === 'claim').map(c => c.block))).size;
    const unplaced = all().length - data.anchored;
    const legend = PT.VERDICTS.filter(v => n[v.key] > 0 || v.key !== 'unchecked')
      .map(v => `<span class="${vcls(v.key)}"><span class="sw"></span><b>${n[v.key]}</b>${esc(v.short)}</span>`).join('')
      + `<span><span class="sw none"></span><b>${untouched}</b>Nothing flagged</span><span><span class="sw rev">✓</span><b>${reviewedCount()}</b>Reviewed by you</span>`;
    const cells = groups.map(g => `<div><div class="mlabel">${esc(g.label)}</div><div class="cells">${g.cells.map(cell => {
      if (cell.kind === 'para') {
        const t = `¶ ${cell.page ? 'p.' + cell.page + ' · ' : ''}${cell.text.slice(0, 80).replace(/\s+/g, ' ')}…\nNothing flagged`;
        return `<button class="cell para" data-act="cell-para" data-block="${esc(cell.block)}" title="${esc(t)}" aria-label="${esc(t)}"></button>`;
      }
      const on = cell.claim === String(state.selected), rv = !!state.reviewed[cell.claim];
      const t = `${cell.uncited ? 'Assertion' : 'Claim'} ${cell.id} · ${PT.verdictInfo(cell.verdict).label}\n“${cell.quote.slice(0, 90).replace(/\s+/g, ' ')}…”${cell.page ? `\n¶ p.${cell.page}` : `\n${cell.location}`}`;
      return `<button class="cell ${vcls(cell.verdict)} ${on ? 'on' : ''}" data-act="select" data-key="${esc(cell.claim)}" title="${esc(t)}" aria-label="${esc(t)}">${rv ? '<span class="ck">✓</span>' : ''}</button>`;
    }).join('')}</div></div>`).join('');
    const mapIntro = `One box per audited claim, in the order they appear, grouped into ${groups.length} sections; a grey box is a paragraph with nothing flagged against it. ${all().length} claims across ${touched} paragraphs, ${untouched} paragraphs untouched.`
      + (unplaced ? ` ${plural(unplaced, 'claim', 'claims')} could not be located in the text and ${unplaced === 1 ? 'is' : 'are'} not on the map.` : '')
      + ' Colours are the verdict categories from the filter bar; a ✓ marks a claim you have ticked off as reviewed.';

    // review progress
    const rv = reviewedCount(), total = all().length;
    const progress = `<div class="card"><div style="display:flex;justify-content:space-between;margin-bottom:6px"><b>Review progress</b><span style="color:var(--muted);font-variant-numeric:tabular-nums">${rv} / ${total}</span></div><div class="progress"><span style="width:${total ? Math.round(100 * rv / total) : 0}%"></span></div><div class="btnrow"><button class="btn-primary" data-act="start-review">${rv ? 'Continue review' : 'Start review'}</button><button class="btn-outline" data-act="go-verified">Only claims with a verdict</button></div><p class="note">Checkmarks are saved in this browser; export them from the Export menu.</p></div>`;

    // disclosures, decided in Python: coverage and numbering get their own
    // cards; everything else — including any key this page has never heard
    // of — lands in the run notes, so nothing is dropped by an allow-list
    const disc = data.disclosures;
    const coverage = disc.filter(d => d.key === 'coverage' || d.key === 'coverage_caveat');
    const coverageMore = disc.filter(d => d.key === 'coverage_attribution' || d.key === 'supplement_coverage');
    const numbering = disc.filter(d => d.key === 'numbering');
    const rest = disc.filter(d => !['coverage', 'coverage_caveat', 'coverage_attribution', 'supplement_coverage', 'numbering', 'scope'].includes(d.key));
    const numberedClaims = all().filter(c => c.disclosures.some(d => d.key === 'claim_numbering')).length;
    const numberingCard = numbering.map(d => `<div class="card warn"><b>Reference numbering unconfirmed</b> <span style="color:var(--muted)">· affects ${plural(numberedClaims, 'claim', 'claims')}</span><p>${esc(d.text)}</p></div>`).join('');
    const coverageCard = `<div class="card"><b>Citation coverage</b>${coverage.length ? `<ul class="disc">${coverage.map(discItem).join('')}${coverageMore.map(discItem).join('')}</ul>` : '<p>Coverage was not audited in this run: results.json carries no coverage object.</p>'}</div>`;
    const gaps = all().filter(isGap);
    const reasons = [...new Set(gaps.map(PT.gapReason))];
    const retrieval = `${data.meta.refs.available} of ${data.meta.refs.total} cited references were available as PDFs${data.refsSkipped ? `, and ${data.refsSkipped} ${data.refsSkipped === 1 ? 'was' : 'were'} skipped on request` : ''}. ${plural(gaps.length, 'claim', 'claims')} could not be checked${reasons.length ? ': ' + reasons.map(r => `${gaps.filter(g => PT.gapReason(g) === r).length} ${r}`).join(', ') : ''}.`;
    const retrievalCard = `<div class="card"><b>Retrieval and run notes</b><p>${esc(retrieval)}</p>${rest.length ? `<ul class="disc">${rest.map(discItem).join('')}</ul>` : ''}</div>`;
    // last, on purpose: the closing word on a report of a slice, so nobody
    // leaves the summary with the counts of a smaller paper
    const scopeCard = data.scope
      ? `<div class="card warn"><b>Scope of this audit</b><p>${esc(data.scope.text)}</p>${data.scope.rows && data.scope.rows.length ? `<ul class="disc">${data.scope.rows.slice(0, 25).map(r => `<li><span class="lv">·</span><span>${esc(r)}</span></li>`).join('')}</ul>` : ''}</div>`
      : '';

    return `<div class="cards">${cards}</div><div class="stack">
      <div class="card map"><div class="kicker">Claim map</div><p style="margin:0 0 10px">${esc(mapIntro)}</p><div class="legend">${legend}</div><div class="mgroups">${cells}</div><p class="note" style="margin-top:10px">Point at a box, or Tab to the map, to read the claim. Click to open it.</p></div>
      ${progress}${numberingCard}${coverageCard}${retrievalCard}${scopeCard}
      <p class="note">Evidence images are pages of the cited sources; each crop states whether its anchor phrase was located. The judgement is yours — verify before you rely on it.</p>
    </div>`;
  }

  function claimsHtml() {
    const vis = visible();
    if (!vis.length) return '<p class="empty">No claims match the current filters.</p>';
    return `<div class="list">${vis.map(c => rowHtml(c, true)).join('')}</div>`;
  }

  function sourcesHtml() {
    const slugs = Object.keys(data.slugRef);
    const cards = slugs.map(slug => {
      const cl = all().filter(c => c.judgements.some(j => j.slug === slug));
      const ref = data.slugRef[slug], kind = data.slugKind[slug] || 'article';
      const cited = kind === 'article' ? `[${esc(ref)}]` : esc(PT.originOf({ kind, ref }));
      const rows = cl.length ? cl.map(c => rowHtml(c, false)).join('') : '<p class="empty" style="padding:8px 12px;font-size:12px">Available, but no audited claim cites it.</p>';
      return `<div class="src-card"><div class="src-head"><div class="line"><span class="slug">${esc(slug)}</span><span class="cited">${cited}</span><span class="cnt">${plural(cl.length, 'claim', 'claims')}</span></div>${ref ? `<span class="rt">${esc(data.refText(ref))}</span>` : ''}</div>${rows}</div>`;
    }).join('');
    const nums = Object.keys(data.missing).sort((a, b) => +a - +b);
    const missing = nums.map(r => `<div class="miss"><span class="num">${esc(r)}</span><span class="txt">${esc(data.refText(r) || '—')}</span><span class="why" title="${esc(data.missingReason[r] || '')}">${esc(data.missing[r])}</span></div>`).join('');
    return `<p class="intro">Each retrieved source with every claim judged against it.</p>
      ${slugs.length ? `<div class="stack">${cards}</div>` : '<p class="empty">No cited source was available to judge against.</p>'}
      <h3 class="h3">Not retrieved <span>· ${plural(nums.length, 'reference', 'references')}</span></h3>
      ${nums.length ? `<div class="list">${missing}</div>` : '<p class="empty">Every cited reference was available.</p>'}
      ${data.manifestPresent ? '' : '<p class="note">No retrieval manifest was rendered with this report, so the reasons above are read from each claim\'s note.</p>'}`;
  }

  function gapsHtml() {
    const gaps = all().filter(isGap);
    if (!gaps.length) return '<p class="intro">Either the cited PDF could not be obtained, or the check failed on an available source. Reported as such — never filled in from memory.</p><p class="empty">Every claim was checked against an available source.</p>';
    const reasons = [...new Set(gaps.map(PT.gapReason))];
    const label = r => r === 'paywalled' ? 'Source paywalled' : r === 'unknown ref' ? 'Reference could not be resolved' : r;
    return '<p class="intro">Either the cited PDF could not be obtained, or the check failed on an available source. Reported as such — never filled in from memory.</p>'
      + reasons.map(r => { const rows = gaps.filter(g => PT.gapReason(g) === r); return `<h3 class="h3" style="margin-top:14px">${esc(label(r))} <span>· ${rows.length}</span></h3><div class="list">${rows.map(c => rowHtml(c, true)).join('')}</div>`; }).join('');
  }

  function scoutHtml() {
    if (!data.scoutPresent) return '<p class="intro">The literature scout did not run for this case: there is no scout.json beside results.json.</p>';
    const rows = data.scout.map(s => `<div class="sc-row"><div class="k"><span class="kind ${s.kind === 'same year' ? 'same' : s.kind}">${esc(s.kind)}</span><span>${esc(s.year == null ? '?' : s.year)}</span>${s.via ? `<span>${esc(s.via)}</span>` : ''}</div><span class="t">${esc(s.title)}</span><span class="venue">${esc(s.journal || '')}${s.doi ? ` · <a class="doi" href="https://doi.org/${esc(s.doi)}" target="_blank" rel="noopener">${esc(s.doi)}</a>` : ''}</span></div>`).join('');
    return `<p class="intro">What the reference list doesn't know. Search-based (Europe PMC), so absence from these lists proves nothing. Same-year items are listed apart: they may postdate submission.</p>
      ${data.scoutError ? `<p class="intro" style="color:var(--v-partial)">⚠ Scout scan incomplete: ${esc(data.scoutError)}</p>` : ''}
      ${rows ? `<div class="list">${rows}</div>` : '<p class="empty">The scout found no newer, overlooked or same-year candidates.</p>'}
      <p class="note" style="margin-top:10px">Query: ${esc(data.scoutQuery || '—')}</p>`;
  }

  function tabsHtml() {
    const n = counts();
    const tabs = [
      ['summary', 'Summary', 0], ['claims', 'Claims', visible().length], ['sources', 'Sources', Object.keys(data.slugRef).length],
      ['gaps', 'Gaps', n.not_retrieved + n.unchecked], ['scout', 'Scout', data.scout.length],
    ];
    return `<div class="tabs" data-noprint>${tabs.map(([k, label, count]) => `<button class="tab ${state.tab === k ? 'on' : ''}" data-act="tab" data-tab="${k}">${label}${count > 0 ? `<span class="cnt">${count}</span>` : ''}</button>`).join('')}</div>`;
  }

  function anchorInfo(j) {
    // the caption for a crop set, from the disclosure decided in Python. When
    // the run recorded no page there is nothing to disclose, and the caption
    // says so rather than asserting a match
    const d = j.disclosures.find(x => x.key === 'anchor');
    if (!d) return { ok: false, label: 'anchor state not recorded', short: 'anchor state not recorded' };
    const ok = d.level !== 'warn';
    const multi = j.images.length > 1;
    return { ok, label: ok ? (multi ? `${d.token} (in one of the crops)` : d.token) : d.short, short: ok ? d.token : d.short };
  }

  function sourceCardHtml(j, sel) {
    const crops = PT.cropsOf(j);
    const n = crops.length, multi = n > 1;
    const a = anchorInfo(j);
    let body = '';
    if (n) {
      const set = crops.map(cr => `<div class="crop">${cr.divider ? `<div class="cont"><i></i>${esc(cr.divider)}<i></i></div>` : ''}<button class="cropbtn" data-act="lightbox" data-src="${esc(cr.image)}" data-cap="${esc(`${j.slug} · page ${cr.page} · ${j.block || ''}${multi ? ` · crop ${cr.index + 1} of ${n}` : ''} · ${a.short}`)}" title="Click to enlarge"><img src="${esc(cr.image)}" alt="${esc(`${j.slug} page ${cr.page}${cr.index ? ` (continuation ${cr.index + 1})` : ''}`)}" data-path="${esc(cr.image)}"></button>${cr.cap ? `<div class="cap">${esc(cr.cap)}</div>` : ''}</div>`).join('');
      body = `<div class="crops"><div class="cropset">${set}</div><div class="cropmeta"><span>${j.page ? `page ${j.page}${j.block ? ` · ${esc(j.block)}` : ''}${multi ? ` · ${n} crops` : ''}` : ''}</span><button class="jump" data-act="jump">↖ show in text</button><span class="anc ${a.ok ? 'ok' : 'warn'}">${esc(a.label)}</span></div>${j.anchors.length ? `<div class="phrases">${j.anchors.map(p => `<span class="phrase">${esc(p)}</span>`).join('')}</div>` : ''}</div>`;
    } else {
      const d = j.disclosures.find(x => x.key === 'anchor');
      body = d ? `<p class="srcnote ${d.level === 'warn' ? 'warn' : ''}">${d.level === 'warn' ? '⚠ ' : ''}${esc(d.text)}</p>` : '';
    }
    const extra = j.disclosures.filter(x => x.key !== 'anchor').map(d => `<p class="srcnote ${d.level === 'warn' ? 'warn' : ''}">${d.level === 'warn' ? '⚠ ' : ''}${esc(d.text)}</p>`).join('');
    return `<div class="src"><div class="src-hd" data-act="jump" title="Show this claim in the manuscript"><span class="slug">${esc(j.slug)}</span><span class="cited">${esc(j.origin)}</span><span class="badge ${vcls(j.verdict)}">${esc(PT.verdictInfo(j.verdict).label)}</span></div>${body}${extra}<p class="rationale">${esc(j.note)}</p></div>`;
  }

  function detailHtml(sel) {
    const vis = visible();
    const idx = vis.findIndex(c => c.key === sel.key);
    const back = state.tab === 'summary' ? 'Summary' : state.tab.charAt(0).toUpperCase() + state.tab.slice(1);
    const rv = !!state.reviewed[sel.key];
    const where = `${sel.kind === 'uncited' ? 'Assertion' : 'Claim'} ${esc(sel.id)} · ${esc(sel.location)}${sel.refs.length ? ` · cites ${esc(refsLabel(sel.refs))}` : ''}${sel.ownSupplement ? " · points to this paper's own supplement" : ''}`;
    const note = sel.note ? sel.note.charAt(0).toUpperCase() + sel.note.slice(1).replace(/\.?$/, '.') : 'The check did not run.';
    const gap = isGap(sel) ? `<div class="gapbox"><b>Not verified.</b> ${esc(note)} Nothing was filled in from memory — the claim stands unchecked.</div>` : '';
    const uncited = sel.kind === 'uncited' ? `<div class="gapbox"><b>No citation.</b> A statement that would normally carry a reference but doesn't. Not verified — flagged for your judgement.</div>` : '';
    const sources = sel.judgements.map(j => sourceCardHtml(j, sel)).join('');
    // caveats: every claim-level disclosure except the anchor, which captions
    // the crop, plus each source's own warnings named by slug
    const caveats = OPTIONS.showCaveats ? sel.disclosures.filter(d => d.key !== 'anchor').map(d => d.text)
      .concat(sel.judgements.flatMap(j => j.disclosures.filter(d => d.level === 'warn').map(d => `${j.slug}: ${d.text}`))) : [];
    const cav = caveats.length ? `<details class="cav"><summary><span class="w">⚠</span>${plural(caveats.length, 'caveat', 'caveats')}<span class="show">show</span></summary><ul>${caveats.map(t => `<li>${esc(t)}</li>`).join('')}</ul></details>` : '';
    return `<div class="det">
      <div class="det-top"><button class="back" data-act="back">← ${esc(back)}</button><span class="pos">${idx >= 0 ? `${idx + 1} of ${vis.length} shown` : 'filtered out'}</span><button class="nav" data-act="prev" title="Previous (k)">↑</button><button class="nav" data-act="next" title="Next (j)">↓</button></div>
      <div class="det-meta"><button class="badge ${vcls(sel.verdict)}" data-act="jump" title="Show in manuscript">${esc(PT.verdictInfo(sel.verdict).label)}</button><span class="where">${where}</span></div>
      <p class="det-claim">${esc(sel.claim)}</p>
      ${sel.quote ? `<blockquote class="q" data-act="jump" title="Show in manuscript">“${esc(sel.quote)}”${sel.anchor ? '' : '<span class="nl">quote not located in the processed manuscript text</span>'}</blockquote>` : ''}
      <label class="rvlbl"><input type="checkbox" data-act="reviewed" ${rv ? 'checked' : ''}><span>${rv ? 'Reviewed' : 'Mark as reviewed'}</span><kbd>r</kbd></label>
      ${gap}${uncited}${sources}${cav}
      <p class="keys" data-noprint>Keys: <kbd>j</kbd>/<kbd>k</kbd> next/prev · <kbd>r</kbd> reviewed · <kbd>esc</kbd> back</p>
    </div>`;
  }

  let asideView = null; // which view the panel last drew: a claim key, or a tab name

  function renderAside() {
    const aside = $('pt-aside');
    const sel = state.selected != null ? byKey(state.selected) : null;
    // keep the scroll position while the same view redraws (a filter, a
    // reviewed mark); start at the top when the view changes, or a claim
    // opened from far down the list shows its rationale and not its header
    const view = sel ? 'claim:' + sel.key : 'tab:' + state.tab;
    const top = view === asideView ? aside.scrollTop : 0;
    asideView = view;
    if (sel) aside.innerHTML = detailHtml(sel);
    else {
      const pane = { summary: summaryHtml, claims: claimsHtml, sources: sourcesHtml, gaps: gapsHtml, scout: scoutHtml }[state.tab]();
      aside.innerHTML = tabsHtml() + `<div class="pane">${pane}</div>`;
    }
    aside.scrollTop = top;
    // an evidence crop that is not beside the report says so, in its place
    aside.querySelectorAll('.cropbtn img').forEach(img => img.addEventListener('error', () => {
      const btn = img.parentElement;
      btn.innerHTML = `<span class="lost">evidence image not found beside this report: ${esc(img.dataset.path)}</span>`;
      btn.style.cursor = 'default';
      btn.removeAttribute('data-act');
    }));
  }

  function renderLightbox() {
    const lb = $('pt-lightbox');
    if (!state.lightbox) { lb.hidden = true; lb.innerHTML = ''; return; }
    lb.hidden = false;
    lb.innerHTML = `<div class="lb" data-act="lightbox-close"><img src="${esc(state.lightbox.url)}" alt="${esc(state.lightbox.caption)}"><div class="cap">${esc(state.lightbox.caption)}</div></div>`;
  }

  function renderAll() {
    renderHeader();
    updateSegments();
    renderAside();
    renderLightbox();
  }

  // ---- interaction --------------------------------------------------------------

  function headerHeight() { const h = $('pt-header'); return h ? h.getBoundingClientRect().height : 0; }

  function scrollToClaim(key) {
    const c = byKey(key);
    const spanKey = c && c.anchor && c.anchor.shared ? c.anchor.shared : key;
    const el = document.querySelector(`#pt-article [data-claim="${CSS.escape(String(spanKey))}"]`);
    if (!el) return;
    const hdr = headerHeight();
    const se = document.scrollingElement || document.documentElement;
    const target = el.getBoundingClientRect().top + se.scrollTop - hdr - Math.max(80, (window.innerHeight - hdr) * 0.3);
    se.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
  }

  function scrollToBlock(id) {
    const el = document.getElementById('blk-' + id);
    if (!el) return;
    const se = document.scrollingElement || document.documentElement;
    se.scrollTo({ top: el.getBoundingClientRect().top + se.scrollTop - headerHeight() - 100, behavior: 'smooth' });
  }

  function select(key) {
    state.selected = String(key);
    state.exportOpen = false;
    renderAll();
    requestAnimationFrame(() => scrollToClaim(state.selected));
  }

  function step(d) {
    const vis = visible();
    if (!vis.length) return;
    const i = vis.findIndex(c => c.key === String(state.selected));
    const n = i < 0 ? (d > 0 ? 0 : vis.length - 1) : Math.min(vis.length - 1, Math.max(0, i + d));
    select(vis[n].key);
  }

  function toggleReviewed(key) {
    const reviewed = { ...state.reviewed };
    if (reviewed[key]) delete reviewed[key]; else reviewed[key] = new Date().toISOString();
    state.reviewed = reviewed;
    try { localStorage.setItem('papertrace:reviewed:' + data.caseId, JSON.stringify(reviewed)); } catch (e) { /* not persisted; the page still shows it */ }
    renderAll();
  }

  function toggleTheme() {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    document.body.dataset.theme = state.theme;
    try { localStorage.setItem('papertrace:theme', state.theme); } catch (e) { /* not persisted */ }
    renderHeader();
  }

  function download(name, text, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  function onChip(v, e) {
    const s = new Set(state.verdicts);
    if (e.shiftKey || e.metaKey || e.ctrlKey) { if (s.has(v)) s.delete(v); else s.add(v); }
    else if (allOn() || !(s.size === 1 && s.has(v))) { s.clear(); s.add(v); }
    else PT.ALL_KEYS.forEach(x => s.add(x));
    state.verdicts = s;
    renderAll();
  }

  function act(el, e) {
    const a = el.dataset.act;
    switch (a) {
      case 'select': select(el.dataset.key); break;
      case 'seg': {
        const keys = el.dataset.claims.split(',');
        const vis = new Set(visible().map(c => c.key));
        const cur = String(state.selected);
        const next = keys.includes(cur) && keys.length > 1 ? keys[(keys.indexOf(cur) + 1) % keys.length] : (keys.find(k => vis.has(k)) || keys[0]);
        select(next);
        break;
      }
      case 'chip': onChip(el.dataset.v, e); break;
      case 'all': state.verdicts = new Set(PT.ALL_KEYS); renderAll(); break;
      case 'tab': state.tab = el.dataset.tab; renderAll(); break;
      case 'card': state.verdicts = new Set([el.dataset.v]); state.tab = 'claims'; renderAll(); break;
      case 'cell-para': scrollToBlock(el.dataset.block); break;
      case 'back': state.selected = null; renderAll(); break;
      case 'prev': step(-1); break;
      case 'next': step(1); break;
      case 'jump': if (state.selected != null) scrollToClaim(state.selected); break;
      case 'theme': toggleTheme(); break;
      case 'export-toggle': state.exportOpen = !state.exportOpen; renderHeader(); break;
      case 'export-md': state.exportOpen = false; renderHeader(); download('report.md', PT.markdownReport(data, state.reviewed), 'text/markdown'); break;
      case 'export-pdf': state.exportOpen = false; renderHeader(); setTimeout(() => window.print(), 100); break;
      case 'export-json': state.exportOpen = false; renderHeader(); download('review.json', JSON.stringify({ case_id: data.caseId, reviewed: state.reviewed }, null, 2), 'application/json'); break;
      case 'lightbox': state.lightbox = { url: el.dataset.src, caption: el.dataset.cap }; renderLightbox(); break;
      case 'lightbox-close': state.lightbox = null; renderLightbox(); break;
      case 'start-review': {
        const vis = visible();
        const first = vis.find(c => !state.reviewed[c.key]) || vis[0];
        if (first) select(first.key);
        break;
      }
      case 'go-verified': state.verdicts = new Set(REVIEW_ORDER); state.tab = 'claims'; renderAll(); break;
      default: return false;
    }
    return true;
  }

  function bind() {
    document.addEventListener('click', e => {
      const el = e.target.closest('[data-act]');
      if (!el) { if (state.exportOpen && !e.target.closest('.export-wrap')) { state.exportOpen = false; renderHeader(); } return; }
      if (el.dataset.act === 'reviewed') return; // handled on change
      if (act(el, e)) e.preventDefault();
    });
    document.addEventListener('change', e => {
      const el = e.target;
      if (el.dataset.act === 'reviewed' && state.selected != null) toggleReviewed(state.selected);
      else if (el.id === 'pt-section') { state.section = el.value; renderAll(); }
    });
    $('pt-search').addEventListener('input', e => { state.query = e.target.value; renderAll(); });
    window.addEventListener('keydown', e => {
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'select' || tag === 'textarea') { if (e.key === 'Escape') e.target.blur(); return; }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '/') { e.preventDefault(); $('pt-search').focus(); }
      else if (e.key === 'j') step(1);
      else if (e.key === 'k') step(-1);
      else if (e.key === 'r' && state.selected != null) toggleReviewed(state.selected);
      else if (e.key === 't') toggleTheme();
      else if (e.key === 'Escape') {
        if (state.lightbox) { state.lightbox = null; renderLightbox(); }
        else { state.selected = null; state.exportOpen = false; renderAll(); }
      }
    });
    // the panel's sticky offset is the header's measured height: the chip
    // row wraps at narrow widths, so it is not a constant
    const measure = () => document.body.style.setProperty('--hdr', Math.round(headerHeight()) + 'px');
    if (window.ResizeObserver) new ResizeObserver(measure).observe($('pt-header'));
    window.addEventListener('resize', measure);
    measure();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', load);
  else load();
})();
