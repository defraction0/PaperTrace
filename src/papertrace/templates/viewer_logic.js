/* PaperTrace report viewer — the half that decides things.

   No DOM in here. This module reads the embedded case data (results.json,
   annotated.md, scout.json, the retrieval manifest and the disclosures decided
   in Python) and turns it into the view model the page renders: which block is
   a heading, where each audited sentence sits in the text, what a crop set
   looks like, which claims a filter keeps. It runs under node for the tests
   (tests/test_report_viewer_js.py) and in the page for the reader, unchanged.

   Rules worth not re-breaking:
   - a quote that cannot be found is not anchored, and the page counts it as
     such — there is no "nearest sentence" fallback;
   - a claim's `ctx_ids` name the block the extractor read it from, and that
     block is searched first, so a sentence the paper repeats lands where it
     was cited and not on the first paragraph that happens to carry it;
   - disclosures are read from the payload, never re-derived here: a second
     copy of disclosures.py would drift from the other three looks unseen.
*/
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PaperTraceLogic = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // order matters everywhere: chips, cards, legend and the markdown export
  const VERDICTS = [
    { key: 'supported', label: 'Supported', short: 'Supported' },
    { key: 'partial', label: 'Partially supported', short: 'Partial' },
    { key: 'contradicted', label: 'Contradicted', short: 'Contradicted' },
    { key: 'not_addressed', label: 'Does not address the claim', short: 'Does not address' },
    { key: 'not_retrieved', label: 'Not retrieved', short: 'Not retrieved' },
    { key: 'unchecked', label: 'Check failed', short: 'Check failed' },
    { key: 'uncited', label: 'No citation', short: 'Uncited' },
  ];
  const VMAP = Object.fromEntries(VERDICTS.map(v => [v.key, v]));
  const ALL_KEYS = VERDICTS.map(v => v.key);

  // a verdict this page has no name for is shown as it is — never mapped onto
  // the nearest one it knows
  function verdictInfo(v) { return VMAP[v] || { key: v, label: String(v), short: String(v) }; }

  function sectionOf(loc) { return (loc || 'Unlocated').split(/,|¶/)[0].trim() || 'Unlocated'; }

  function stripTags(s) { return String(s || '').replace(/<[^>]+>|&lt;[^&]+&gt;/g, ''); }

  // how a judgement names its document — the same three phrasings as
  // SourceJudgement.origin, so the page and report.md agree
  function originOf(j) {
    if (j.kind === 'own_supplement') return "this paper's own supplement";
    if (j.kind === 'supplement') return `supplement to [${j.ref}]`;
    return `cited as [${j.ref}]`;
  }

  // ---- annotated.md → blocks ---------------------------------------------

  // headings that open the body: the run of headings above the first of these
  // (and above the first long paragraph) is the paper's title
  const SECTION_RE = /^(abstract|summary|key\s+(results|points|findings)|highlights?|importance|introduction|background|keywords?|abbreviations|graphical abstract|objectives?|purpose|materials and methods|methods|results|discussion|conclusions?)\b/i;
  const MARKER_RE = /<!--\s*(block_\d+),\s*page\s*(\d+)(,\s*table)?\s*-->/g;

  function parseAnnotated(md) {
    const blocks = [];
    if (!md) return { blocks, title: '' };
    let last = 0, pendingTable = null, m, refMode = false, refNum = 0;
    const push = (text, id, page) => {
      text = text.trim();
      if (!text) return;
      if (/^##\s+/.test(text)) {
        const t = text.replace(/^##\s+/, '').trim();
        refMode = /^references/i.test(t);
        refNum = 0;
        blocks.push({ id, page, type: 'heading', text: t });
        return;
      }
      if (refMode && /^-\s+/.test(text)) {
        blocks.push({ id, page, type: 'ref', num: ++refNum, text: text.replace(/^-\s+/, '') });
        return;
      }
      if (/^\[FIGURE/.test(text)) {
        blocks.push({ id, page, type: 'figure', text: text.replace(/^\[FIGURE:?\s*/, '').replace(/\]$/, '') || 'image' });
        return;
      }
      // the caption paragraph that follows a figure placeholder duplicates it
      const prev = blocks[blocks.length - 1];
      if (prev && prev.type === 'figure' && prev.text === text) return;
      blocks.push({ id, page, type: 'para', text: text.replace(/^-\s+■?\s*/gm, '• ') });
    };
    const flushTable = (seg) => {
      const lines = seg.split('\n');
      let lt = -1;
      lines.forEach((l, i) => { if (/^\s*\|/.test(l) || /^\*\*/.test(l)) lt = i; });
      const tbl = lines.slice(0, lt + 1).join('\n').trim();
      const cap = (tbl.match(/^\*\*(.+?)\*\*/) || [])[1] || (tbl.match(/^\|\s*([^|]+)\|/) || [])[1] || '';
      blocks.push({
        id: pendingTable.id, page: pendingTable.page, type: 'table',
        text: tbl.replace(/^\*\*.+\*\*\n*/, '').replace(/^Table \d+:.*\n*/m, '').trim(),
        caption: cap.trim().slice(0, 90),
      });
      pendingTable = null;
      return lines.slice(lt + 1).join('\n');
    };
    // a block's marker follows its text; a table's marker precedes it
    while ((m = MARKER_RE.exec(md))) {
      const seg = md.slice(last, m.index);
      last = MARKER_RE.lastIndex;
      if (pendingTable) {
        const rest = flushTable(seg);
        if (m[3]) { push(rest, 'orphan-' + m[1], +m[2]); pendingTable = { id: m[1], page: +m[2] }; }
        else push(rest, m[1], +m[2]);
      } else if (m[3]) {
        push(seg, 'orphan-' + m[1], +m[2]);
        pendingTable = { id: m[1], page: +m[2] };
      } else {
        push(seg, m[1], +m[2]);
      }
    }
    if (pendingTable) flushTable(md.slice(last)); // a table that ends the document
    MARKER_RE.lastIndex = 0;

    // title: the headings above the first body paragraph or section heading.
    // A heading shorter than 12 characters up there is a journal name or a
    // running head, and is dropped rather than shown as a section
    const parts = [];
    for (const b of blocks) {
      if (b.type === 'para' && b.text.length > 200) break;
      if (b.type !== 'heading') continue;
      if (SECTION_RE.test(b.text)) break;
      if (b.text.length >= 12) parts.push(b.text);
      b.type = 'skip';
    }
    return { blocks: blocks.filter(b => b.type !== 'skip'), title: parts.join(' ').replace(/\s+/g, ' ') };
  }

  // ---- anchoring quotes to the text --------------------------------------

  // lowercase alphanumerics only, parenthetical numeric citations removed —
  // and a map from each kept character back to its original offset
  function norm(text) {
    const skip = new Array(text.length).fill(false);
    for (const c of text.matchAll(/\(\s*\d[\d,\s\-]*\)/g)) {
      for (let i = c.index; i < c.index + c[0].length; i++) skip[i] = true;
    }
    let out = '';
    const map = [];
    for (let i = 0; i < text.length; i++) {
      if (skip[i]) continue;
      const ch = text[i].toLowerCase();
      if (/[a-z0-9]/.test(ch)) { out += ch; map.push(i); }
    }
    return { out, map };
  }

  function ctxBlocks(c) { return (c.ctx_ids || []).map(x => String(x).split(':')[0]); }

  // sets `claim.anchor` ({block, start, end} or null) and `block.segments` on
  // every paragraph; returns how many claims were located
  function anchorClaims(blocks, claims) {
    const paras = blocks.filter(b => b.type === 'para').map(b => ({ b, n: norm(b.text) }));
    const byId = new Map();
    paras.forEach(p => { if (!byId.has(p.b.id)) byId.set(p.b.id, p); });
    for (const c of claims) {
      c.anchor = null;
      if (!c.quote) continue;
      const q = norm(c.quote).out;
      if (q.length < 12) continue; // too little to identify a sentence by
      const first = ctxBlocks(c).map(id => byId.get(id)).filter(Boolean);
      const order = first.concat(paras.filter(p => first.indexOf(p) < 0));
      for (const { b, n } of order) {
        let s = n.out.indexOf(q), e = s >= 0 ? s + q.length - 1 : -1;
        if (s < 0) {
          // a quote the extractor trimmed in the middle still opens and
          // closes verbatim: match its first and last 40 characters, in one
          // paragraph, no further apart than the quote's own length allows
          const head = q.slice(0, 40), tail = q.slice(-40);
          const hs = n.out.indexOf(head);
          if (hs >= 0) {
            const ts = n.out.indexOf(tail, hs);
            if (ts >= 0 && ts - hs < q.length * 1.6) { s = hs; e = ts + tail.length - 1; }
          }
        }
        if (s < 0) continue;
        let start = n.map[s], end = n.map[e] + 1;
        // extend over the citation and the full stop, so the underline covers `(10).`
        const trail = b.text.slice(end).match(/^\s*(\(\s*\d[\d,\s\-]*\))?\s*[.,;:]?/);
        if (trail) end += trail[0].length;
        c.anchor = { block: b.id, start, end };
        break;
      }
    }
    // segments per paragraph; several claims can share one sentence (one per
    // cited reference) and then share its span
    for (const { b } of paras) {
      const hits = claims.filter(c => c.anchor && c.anchor.block === b.id)
        .sort((x, y) => x.anchor.start - y.anchor.start);
      const segs = [];
      let pos = 0, lastSeg = null;
      for (const c of hits) {
        if (lastSeg && c.anchor.start < pos) {
          if (c.anchor.start === lastSeg.start) { lastSeg.claims.push(c.key); c.anchor.shared = lastSeg.claim; }
          else c.anchor = null; // overlapping but different: the earlier one keeps the text
          continue;
        }
        if (c.anchor.start > pos) segs.push({ text: b.text.slice(pos, c.anchor.start) });
        lastSeg = { text: b.text.slice(c.anchor.start, c.anchor.end), claim: c.key, claims: [c.key], start: c.anchor.start };
        segs.push(lastSeg);
        pos = c.anchor.end;
      }
      if (pos < b.text.length) segs.push({ text: b.text.slice(pos) });
      b.segments = segs;
    }
    return claims.filter(c => c.anchor).length;
  }

  // ---- the view model ------------------------------------------------------

  function gapReason(c) {
    if (c.verdict === 'unchecked') return 'check failed';
    return (String(c.note || '').match(/\(([^)]+)\)/) || [])[1] || 'not retrieved';
  }

  function judgementOf(j, c, disclosures) {
    return {
      slug: j.source_slug, ref: String(j.ref || (c.refs || [])[0] || ''), kind: j.kind || 'article',
      verified: !!j.verified, verdict: j.verdict || c.verdict, note: j.note || c.note || '',
      page: j.source_page, block: j.source_block, anchors: j.anchor_phrases || [],
      images: j.evidence_image ? [j.evidence_image].concat(j.continuation_images || []) : [],
      located: j.anchor_located, disclosures: disclosures || [],
      origin: originOf({ kind: j.kind || 'article', ref: String(j.ref || (c.refs || [])[0] || '') }),
    };
  }

  function buildModel(payload) {
    const results = payload.results || {};
    const md = payload.annotated || '';
    const scout = payload.scout || null;
    const manifest = payload.manifest || null;
    const disc = payload.disclosures || {};
    const claimDisc = disc.claims || {}, judgeDisc = disc.judgements || {};

    const claims = (results.claims || []).map(c => {
      const id = String(c.id);
      const own = claimDisc[id] || [];
      let judgements;
      if (c.judgements && c.judgements.length) {
        judgements = c.judgements.map((j, i) => judgementOf(j, c, (judgeDisc[id] || [])[i] || []));
      } else if (c.source_slug) {
        // a results.json from before per-source judgements: the claim's own
        // anchor is the one crop, and its anchor disclosure captions it
        judgements = [judgementOf(c, c, own.filter(d => d.key === 'anchor'))];
      } else {
        judgements = [];
      }
      return {
        key: id, id, kind: 'claim', claim: c.claim || '', quote: c.quote || '',
        location: c.location || '', section: sectionOf(c.location), refs: (c.refs || []).map(String),
        verdict: c.verdict, note: c.note || '', unjudged: (c.unjudged_refs || []).map(String),
        ctx_ids: c.ctx_ids || [], ownSupplement: !!c.own_supplement, judgements, disclosures: own,
      };
    }).concat((results.uncited || []).map(u => ({
      key: 'U' + u.id, id: 'U' + u.id, kind: 'uncited', claim: u.claim || '', quote: u.quote || '',
      location: u.location || '', section: sectionOf(u.location), refs: [], verdict: 'uncited',
      note: '', unjudged: [], ctx_ids: [], ownSupplement: false, judgements: [], disclosures: [],
    })));

    const parsed = parseAnnotated(md);
    const synthetic = !parsed.blocks.length;
    if (synthetic) {
      // no annotated.md: a skeleton of the audited sentences, one heading per
      // section and one paragraph per distinct sentence, in claim order
      const order = [], bySec = new Map();
      for (const c of claims) {
        if (!bySec.has(c.section)) { bySec.set(c.section, []); order.push(c.section); }
        bySec.get(c.section).push(c);
      }
      let n = 0;
      for (const s of order) {
        parsed.blocks.push({ id: 'syn_' + (++n), page: 0, type: 'heading', text: s });
        const seen = new Set();
        for (const c of bySec.get(s)) {
          const t = c.quote || c.claim;
          const k = norm(t).out;
          if (!t || seen.has(k)) continue;
          seen.add(k);
          parsed.blocks.push({ id: 'syn_' + (++n), page: 0, type: 'para', text: t });
        }
      }
    }
    const anchored = anchorClaims(parsed.blocks, claims);

    // reference text: the manifest's parsed entry, else the manuscript's own list
    const refsList = parsed.blocks.filter(b => b.type === 'ref');
    const manifestByNum = new Map(((manifest && manifest.entries) || []).map(e => [String(e.num), e]));
    const refText = n => {
      const e = manifestByNum.get(String(n));
      if (e && e.raw) return e.raw;
      const r = refsList.find(b => String(b.num) === String(n));
      return r ? r.text : '';
    };
    // every document a claim was judged against, and every source that was
    // available — keyed by slug, valued by the label it answers for
    const slugRef = {}, slugKind = {};
    claims.forEach(c => c.judgements.forEach(j => { if (j.slug) { slugRef[j.slug] = j.ref; slugKind[j.slug] = j.kind; } }));
    if (manifest) {
      for (const e of manifest.entries || []) {
        if (e.slug && (e.status === 'retrieved' || e.status === 'provided') && !(e.slug in slugRef)) { slugRef[e.slug] = String(e.num); slugKind[e.slug] = 'article'; }
      }
    }
    // references nobody could read, with the reason: the manifest's own
    // status, or — without a manifest — what the claim's note says
    const missing = {}, missingReason = {};
    if (manifest) {
      for (const e of manifest.entries || []) {
        if (e.status !== 'retrieved' && e.status !== 'provided') {
          missing[String(e.num)] = String(e.status || 'not retrieved').replace(/_/g, ' ');
          missingReason[String(e.num)] = e.reason || '';
        }
      }
    } else {
      const judgedRefs = new Set(Object.values(slugRef));
      claims.filter(c => c.verdict === 'not_retrieved' || c.verdict === 'unchecked')
        .forEach(c => c.refs.forEach(r => { if (!judgedRefs.has(r) && !missing[r]) missing[r] = gapReason(c); }));
      claims.forEach(c => c.unjudged.forEach(r => { if (!missing[r] && !judgedRefs.has(r)) missing[r] = 'co-cited, not obtained'; }));
    }

    const cov = results.coverage || {};
    const occ = cov.occurrences || {};
    const scopeSources = (results.scope && results.scope.sources) || {};
    const refsSkipped = (scopeSources.skipped_for_claims || []).length + (scopeSources.skipped_by_cap || []).length;
    const title = (scout && scout.paper && stripTags(scout.paper.title).trim()) || parsed.title || results.manuscript || 'Manuscript';
    const scoutRows = scout
      ? [].concat(
        (scout.newer || []).map(s => ({ ...s, kind: 'newer' })),
        (scout.overlooked || []).map(s => ({ ...s, kind: 'overlooked' })),
        (scout.same_year || []).map(s => ({ ...s, kind: 'same year' })),
      ).map(s => ({ ...s, title: stripTags(s.title) }))
      : [];

    return {
      synthetic, anchored, title,
      caseId: (results.manuscript || 'case').replace(/\.pdf$/i, '').slice(0, 80),
      meta: {
        manuscript: results.manuscript || '', checker: results.checker || '', date: results.date || '',
        converter: results.converter || '', refs: results.refs || {}, version: payload.version || '',
      },
      claims, blocks: parsed.blocks, refText, slugRef, slugKind, missing, missingReason,
      coverage: {
        total: occ.total || 0, covered: occ.covered || 0, uncovered: occ.uncovered || 0,
        uncertain: occ.uncertain || 0, labels: (cov.labels_in_text || []).length,
        hasOccurrences: !!cov.occurrences, audited: !!(cov.labels_in_text || []).length || !!occ.total,
      },
      scout: scoutRows, scoutQuery: scout ? (scout.query || '') : '', scoutError: scout ? (scout.error || '') : '',
      scoutPresent: !!scout, manifestPresent: !!manifest, manifest,
      disclosures: disc.run || [],
      // the run was cut short on request. The disclosure is Python's, carried
      // in the payload; the page states it in the header, the manuscript
      // subline and the summary's last card. `refsSkipped` counts the
      // references nobody tried, which are neither available nor unobtainable
      scope: (disc.run || []).find(x => x.key === 'scope') || null,
      refsSkipped: refsSkipped,
    };
  }

  // ---- filtering ------------------------------------------------------------

  function matches(c, f) {
    const vs = f.verdicts instanceof Set ? f.verdicts : new Set(f.verdicts || ALL_KEYS);
    if (!vs.has(c.verdict)) return false;
    if (f.section && f.section !== 'all' && c.section !== f.section) return false;
    const q = (f.query || '').trim().toLowerCase();
    if (q) {
      const hay = [c.id, c.claim, c.quote, c.location]
        .concat(c.refs.map(x => '[' + x + ']'), c.judgements.map(j => j.slug + ' ' + j.note))
        .join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  // ---- evidence crops -------------------------------------------------------

  // one passage, possibly split over several rectangles (the next column or
  // the next page). The first image opens the passage; the red box may sit in
  // a later one, so the set is captioned once and each crop only says where it
  // continues
  function cropsOf(j) {
    const images = j.images || [];
    const n = images.length, multi = n > 1;
    const pageOf = (img, k) => {
      if (k === 0) return j.page;
      const m = String(img).match(/_p(\d+)(?:_cont\d+)?\.[a-z]+$/i);
      return m ? +m[1] : j.page;
    };
    return images.map((image, k) => {
      const page = pageOf(image, k);
      const prev = k ? pageOf(images[k - 1], k - 1) : null;
      const divider = k === 0 ? '' : (String(page) !== String(prev) ? `continues on p.${page} →` : 'continues →');
      const cap = multi ? (k === 0 ? `crop 1 of ${n} · the passage opens here` : `crop ${k + 1} of ${n} · p.${page}`) : '';
      return { index: k, image, page, divider, cap };
    });
  }

  // ---- the claim map --------------------------------------------------------

  const TOP_RE = /^(abstract|introduction|background|materials and methods|methods|results|discussion|conclusions?)\b/i;
  const STOP_RE = /^(references|author affiliations|acknowledg|funding|supplemental)/i;

  // one cell per audited claim in reading order, grouped by top-level section;
  // a paragraph with nothing flagged is one neutral cell
  function mapGroups(data) {
    const byKey = new Map(data.claims.map(c => [c.key, c]));
    const groups = [];
    let cur = { key: 'front', label: data.synthetic ? 'Front matter' : 'Abstract · Introduction', cells: [] };
    for (const b of data.blocks) {
      if (b.type === 'heading') {
        if (STOP_RE.test(b.text)) break;
        if (TOP_RE.test(b.text)) {
          if (cur.cells.length) groups.push(cur);
          cur = { key: b.id, label: b.text, cells: [] };
        }
        continue;
      }
      if (b.type !== 'para') continue;
      const cs = (b.segments || []).filter(s => s.claim).flatMap(s => s.claims || [s.claim])
        .map(k => byKey.get(k)).filter(Boolean);
      if (!cs.length) {
        cur.cells.push({ kind: 'para', key: b.id, block: b.id, page: b.page, text: b.text });
        continue;
      }
      for (const c of cs) {
        cur.cells.push({
          kind: 'claim', key: b.id + '-' + c.key, claim: c.key, id: c.id, uncited: c.kind === 'uncited',
          verdict: c.verdict, quote: c.quote || c.claim, page: b.page, location: c.location, block: b.id,
        });
      }
    }
    if (cur.cells.length) groups.push(cur);
    return groups;
  }

  // ---- the markdown export: report.md's structure, plus the reviewed marks ----

  function markdownReport(d, reviewed) {
    const R = reviewed || {};
    const L = [];
    const flag = dis => (dis.level === 'warn' ? '⚠️ ' : '') + dis.text;
    L.push('# Fact-Check Report', '', `**${d.title}**`, '',
      `Checker: ${d.meta.checker} · ${d.meta.date} · Sources: ${d.meta.refs.available} / ${d.meta.refs.total}`
      + (d.refsSkipped ? ` · ${d.refsSkipped} skipped on request` : ''), '');
    for (const v of VERDICTS) {
      const n = d.claims.filter(c => c.verdict === v.key).length;
      if (n) L.push(`- ${v.label}: ${n}`);
    }
    L.push('');
    for (const dis of d.disclosures) L.push(`> ${flag(dis)}`, '');
    for (const c of d.claims.filter(c => c.kind === 'claim' && c.judgements.length)) {
      L.push(`## Claim ${c.id}: “${c.claim}”`, '', `Status: ${verdictInfo(c.verdict).label}${R[c.key] ? ' · reviewed' : ''}`, '');
      if (c.quote) L.push(`> ${c.quote}`, '');
      for (const dis of c.disclosures.filter(x => x.key !== 'anchor')) L.push(`> ${flag(dis)}`, '');
      for (const j of c.judgements) {
        L.push(`### ${verdictInfo(j.verdict).label} — ${j.slug} (${j.origin})`);
        if (j.page) L.push(`Source: page ${j.page} (${j.block})`);
        if (j.note) L.push(j.note);
        for (const img of j.images) L.push(`![evidence](${img})`);
        for (const dis of j.disclosures) L.push(`*${flag(dis)}*`);
        L.push('');
      }
    }
    L.push('## Assertions without citation', '');
    d.claims.filter(c => c.kind === 'uncited').forEach(c => L.push(`- [${c.id}] ${c.claim} — ${c.location}`));
    L.push('', '## Not verified — source not retrieved, or check failed', '');
    d.claims.filter(c => c.verdict === 'not_retrieved' || c.verdict === 'unchecked')
      .forEach(c => L.push(`- Claim ${c.id}: ${c.claim} (${c.note || c.verdict})`));
    if (d.scope) {
      // last, like report.md: the closing word on a report of a slice
      L.push('', '## Scope of this audit', '', `⚠️ **${d.scope.text}**`);
      for (const row of d.scope.rows || []) L.push(`- ${row}`);
    }
    return L.join('\n');
  }

  return {
    VERDICTS, VMAP, ALL_KEYS, verdictInfo, sectionOf, stripTags, originOf,
    parseAnnotated, norm, anchorClaims, buildModel, matches, cropsOf, gapReason,
    mapGroups, markdownReport,
  };
});
