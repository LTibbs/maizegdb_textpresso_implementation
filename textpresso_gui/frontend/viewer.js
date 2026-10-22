(function () {
  "use strict";

  // Full-text viewer: viewer.html?id=<identifier>[&q=<keywords>][&cs=1][&ex=references,acknowledgments][&cat=<category>...]
  //
  // The search page's "Annotated full text" link fills in q/cs/ex/cat from the
  // search that found the paper, so the viewer can step through the same
  // matches. The paper itself comes from /api/document (the local CAS2 file,
  // parsed by textpresso_classifiers/casdoc.py): one text string plus page,
  // paragraph, figure, section, sentence and annotation offsets into it.

  // Same default as app.js -- see GUI_SETUP.md.
  const API_BASE = window.TEXTPRESSO_API_BASE || "http://127.0.0.1:8010";
  const CONTEXT_KEY = "tpViewerContext";        // result list saved by app.js, for prev/next paper
  const ONTO_PREFS_KEY = "tpViewerOntologies";  // which ontology highlights are switched on

  // Colours match app.js's annotation panel / category badges (CSS vars in viewer.css).
  const ONTOLOGIES = [
    { key: "MAIZE_GENES", label: "Maize genes", badge: "Gene", color: "var(--onto-gene)", on: true },
    { key: "MAIZE_GENES_RELATED", label: "Maize genes (related synonyms)", badge: "Gene", color: "var(--onto-gene-related)", on: false },
    { key: "GO", label: "Gene Ontology", badge: "GO", color: "var(--onto-go)", on: true },
    { key: "PO", label: "Plant Ontology", badge: "PO", color: "var(--onto-po)", on: true },
    { key: "TO", label: "Trait Ontology", badge: "TO", color: "var(--onto-to)", on: true },
  ];
  const OTHER_ONTOLOGY = { label: "Other", badge: "Other", color: "#5f6368", on: true };
  const ontologyInfo = (key) => ONTOLOGIES.find((o) => o.key === key) || { ...OTHER_ONTOLOGY, key, label: key };

  const $ = (id) => document.getElementById(id);
  const els = {
    paperNav: $("paperNav"),
    prevPaper: $("prevPaper"),
    nextPaper: $("nextPaper"),
    paperPos: $("paperPos"),
    docHeader: $("docHeader"),
    docTitle: $("docTitle"),
    docAuthors: $("docAuthors"),
    docCitation: $("docCitation"),
    sidebar: $("sidebar"),
    findForm: $("findForm"),
    findInput: $("findInput"),
    catInput: $("catInput"),
    catChips: $("catChips"),
    catSuggest: $("catSuggest"),
    searchMeta: $("searchMeta"),
    annotationsMeta: $("annotationsMeta"),
    contentsMeta: $("contentsMeta"),
    hitPrev: $("hitPrev"),
    hitNext: $("hitNext"),
    hitPos: $("hitPos"),
    hitNote: $("hitNote"),
    catPrev: $("catPrev"),
    catNext: $("catNext"),
    catPos: $("catPos"),
    catNote: $("catNote"),
    categoryMeta: $("categoryMeta"),
    expandRow: $("expandRow"),
    expandAnc: $("expandAnc"),
    expandDesc: $("expandDesc"),
    expandNote: $("expandNote"),
    focusBox: $("focusBox"),
    focusLabel: $("focusLabel"),
    focusPrev: $("focusPrev"),
    focusNext: $("focusNext"),
    focusPos: $("focusPos"),
    focusClear: $("focusClear"),
    ontoGroups: $("ontoGroups"),
    tocList: $("tocList"),
    docText: $("docText"),
    main: $("viewerMain"),
    annAll: $("annAll"),
    annNone: $("annNone"),
    popover: $("annPopover"),
  };

  const params = new URLSearchParams(location.search);
  const identifier = params.get("id") || "";
  const caseSensitive = params.get("cs") === "1";
  const excludedSections = new Set((params.get("ex") || "").split(",").map((s) => s.trim()).filter(Boolean));
  // Categories being searched for: the search page's categories to start
  // with, then whatever is added/removed in the sidebar's category finder.
  // Full category strings, e.g. "cct1 (tpzm:0010325)".
  let findCats = params.getAll("cat");
  const includeRelated = params.get("rel") === "1";  // search used "include related gene synonyms"
  // Also match broader (ancestor) / narrower (descendant) terms of GO/PO/TO
  // categories -- starts from the search page's choice, then the checkboxes.
  const catExpand = { anc: params.get("anc") === "1", desc: params.get("desc") === "1" };
  const PANELS_KEY = "tpViewerPanels";               // which sidebar panels are expanded

  let doc = null;         // /api/document response
  let text = "";          // doc.text with "\n" shown as " " -- same length, so every offset still holds
  let segments = [];      // [{begin, end, anns: [annotation index], hit, chit, sent, el}] -- one <span> each
  let focus = null;       // {category, occ: [annotation index], cursor} -- "highlight this label"
  const enabledCats = new Set();  // categories whose highlighting is switched on in the sidebar
  let catSegs = new Map();        // category -> [segment index], rebuilt by renderText()
  const ontoUI = new Map();       // ontology -> {master, summary, cats, rows: Map(category -> {li, box})}
  const MAX_TINTED_SENTENCE = 500;  // chars; see tintSentence()
  const figureEls = new Map();  // reused across re-renders so images don't reload

  // ---------------------------------------------------------------------
  // Helpers (same as app.js)
  // ---------------------------------------------------------------------

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Titles may carry <i>/<sup>/<sub>; escape everything, then allow only those.
  function sanitizeTitle(s) {
    return escapeHtml(s).replace(/&lt;(\/?(?:i|sup|sub))&gt;/g, "<$1>");
  }

  function escapeRegExp(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  async function fetchJSON(url, options) {
    const res = await fetch(url, options);
    let body = null;
    try { body = await res.json(); } catch (_) { /* non-JSON error body */ }
    if (!res.ok) {
      const detail = body && body.detail ? body.detail : res.statusText;
      const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      err.status = res.status;
      throw err;
    }
    return body;
  }

  function readStorage(key) {
    try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (_) { return null; }
  }

  function writeStorage(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* private mode etc. */ }
  }

  const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function scrollToEl(el) {
    if (el) el.scrollIntoView({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
  }

  function clearClass(cls) {
    els.docText.querySelectorAll(`.${cls}`).forEach((e) => e.classList.remove(cls));
  }

  function sectionTypesAt(begin, end) {
    return doc.sections.filter((s) => s.begin < end && s.end > begin).map((s) => s.type);
  }

  // First segment starting at or after pos (segments are sorted by begin).
  function segmentAt(pos) {
    let lo = 0;
    let hi = segments.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (segments[mid].end <= pos) lo = mid + 1;
      else hi = mid;
    }
    return segments[lo];
  }

  // ---------------------------------------------------------------------
  // Search matches: two independent steppers -- keywords and categories --
  // each with its own ‹ ›, count and highlight colour, so stepping through
  // one never jumps into the other.
  // ---------------------------------------------------------------------

  // Keyword query -> terms to highlight. Quoted phrases stay whole; AND/OR
  // are dropped; NOT/-prefixed terms are excluded terms, so not highlighted.
  function searchTerms(q) {
    const terms = [];
    let negate = false;
    (q.replace(/[()]/g, " ").match(/"[^"]*"|\S+/g) || []).forEach((tok) => {
      const upper = tok.toUpperCase();
      if (upper === "NOT") { negate = true; return; }
      if (upper === "AND" || upper === "OR") return;
      if (negate || tok.startsWith("-")) { negate = false; return; }
      const t = tok.replace(/^\+/, "").replace(/^"|"$/g, "").trim();
      if (t) terms.push(t);
    });
    return terms;
  }

  // Whole-word match; "*" and "?" are wildcards; words in a phrase may be
  // separated by any whitespace.
  function termPattern(term) {
    return term.split(/\s+/)
      .map((w) => escapeRegExp(w).replace(/\\\*/g, "\\w*").replace(/\\\?/g, "\\w"))
      .join("\\s+");
  }

  // Sorted, overlap-merged spans. Matches inside sections the search excluded
  // (references/acknowledgments by default) are still marked, but greyed out
  // and not stepped through -- the search itself didn't count them.
  function mergeSpans(found) {
    found.sort((a, b) => a.begin - b.begin || b.end - a.end);
    const merged = [];
    found.forEach((h) => {
      const last = merged[merged.length - 1];
      if (last && h.begin < last.end) last.end = Math.max(last.end, h.end);
      else merged.push({ begin: h.begin, end: h.end });
    });
    merged.forEach((h) => {
      h.excluded = sectionTypesAt(h.begin, h.end).some((t) => excludedSections.has(t));
    });
    return merged;
  }

  function keywordHits(q) {
    const found = [];
    const terms = searchTerms(q);
    if (terms.length) {
      const alternatives = terms.map(termPattern).sort((a, b) => b.length - a.length).join("|");
      const re = new RegExp(`(?<!\\w)(?:${alternatives})(?!\\w)`, caseSensitive ? "g" : "gi");
      let m;
      while ((m = re.exec(text))) {
        if (!m[0].length) { re.lastIndex++; continue; }
        found.push({ begin: m.index, end: m.index + m[0].length });
      }
    }
    return mergeSpans(found);
  }

  // Matched on the ontology ID, so the search page's category string and the
  // paper's own annotation string needn't be identical. RELATED gene synonyms
  // only count for a "RELATED:" category, or when the search included them.
  function categoryKey(category) {
    const m = category.match(/\(([A-Za-z][A-Za-z0-9_]*:\d+)\)\s*$/);
    return m ? m[1] : category;
  }

  function annotationMatches(a, category) {
    const wantsRelated = category.startsWith("RELATED:");
    const isRelated = a.ontology.endsWith("_RELATED");
    if (isRelated && !wantsRelated && !includeRelated) return false;
    if (!isRelated && wantsRelated) return false;
    const key = categoryKey(category);
    return a.onto_id ? a.onto_id === key : a.category === category;
  }

  // --- Broader/narrower terms (GO/PO/TO only, like the search page) -------
  // Looked up through /api/category_search the same way the search page's
  // "Include broader/narrower terms" does: the term's own relationship
  // types, in each direction. The lookup service caps a list at 100 terms.

  const EXPANDABLE = new Set(["GO", "PO", "TO"]);
  const EXPANSION_CAP = 100;
  const expansions = new Map();  // category -> {status, ancestors: [category], descendants: [category], truncated, error}

  const isExpandable = (category) => EXPANDABLE.has(ontologyOfCategory(category));

  async function fetchExpansion(category) {
    const entry = { status: "loading", ancestors: [], descendants: [], truncated: false, error: "" };
    expansions.set(category, entry);
    const id = categoryKey(category);
    const lookup = async (extra) => {
      const qs = new URLSearchParams([["q", id], ["limit", String(EXPANSION_CAP)], ...extra]);
      return (await fetchJSON(`${API_BASE}/api/category_search?${qs}`)).matches || [];
    };
    try {
      const exact = (await lookup([])).find((m) => m.id === id);
      const parents = (exact && exact.parent_relationship_types) || [];
      const children = (exact && exact.relationship_types) || [];
      const [up, down] = await Promise.all([
        parents.length ? lookup(parents.map((r) => ["ancestor_relationship_type", r])) : [],
        children.length ? lookup(children.map((r) => ["relationship_type", r])) : [],
      ]);
      entry.ancestors = up.filter((m) => m.id !== id).map((m) => m.category);
      entry.descendants = down.filter((m) => m.id !== id).map((m) => m.category);
      entry.truncated = down.length >= EXPANSION_CAP;
      entry.status = "ok";
    } catch (e) {
      entry.status = "error";
      entry.error = e.status === 502 || !e.status ? "the term lookup service is unavailable" : e.message;
    }
  }

  // Fetch whatever expansions the current chips + checkboxes need.
  async function ensureExpansions() {
    if (!catExpand.anc && !catExpand.desc) return;
    const todo = findCats.filter((c) => isExpandable(c) && !expansions.has(c));
    if (!todo.length) return;
    updateExpandNote(true);
    await Promise.all(todo.map(fetchExpansion));
  }

  // The extra categories a chip brings in with the current checkboxes.
  function expandedCategories(category) {
    const e = expansions.get(category);
    if (!e || e.status !== "ok" || !isExpandable(category)) return [];
    return [...(catExpand.anc ? e.ancestors : []), ...(catExpand.desc ? e.descendants : [])];
  }

  // Per chip: its own annotations plus any expanded-term annotations.
  function chipMatches(category) {
    const own = doc.annotations.filter((a) => annotationMatches(a, category));
    const extraIds = new Set(expandedCategories(category).map(categoryKey));
    const extra = extraIds.size
      ? doc.annotations.filter((a) => !a.ontology.endsWith("_RELATED") && extraIds.has(a.onto_id)) : [];
    return { own, extra };
  }

  function categoryHits() {
    const found = [];
    findCats.forEach((c) => {
      const { own, extra } = chipMatches(c);
      own.concat(extra).forEach((a) => found.push({ begin: a.begin, end: a.end }));
    });
    return mergeSpans(found);
  }

  // --- The two steppers ---------------------------------------------------

  const steppers = {
    kw: {
      field: "hit", cls: "hit", hits: [], steppable: [], cursor: -1,
      prev: els.hitPrev, next: els.hitNext, pos: els.hitPos, note: els.hitNote,
      hasTerms: () => searchTerms(els.findInput.value).length > 0, emptyText: "No keywords",
    },
    cat: {
      field: "chit", cls: "chit", hits: [], steppable: [], cursor: -1,
      prev: els.catPrev, next: els.catNext, pos: els.catPos, note: els.catNote,
      hasTerms: () => findCats.length > 0, emptyText: "No categories",
    },
  };
  let lastStepped = null;  // whose current match gets the sentence tint

  function setHits(st, list) {
    st.hits = list;
    st.steppable = list.map((h, i) => (h.excluded ? -1 : i)).filter((i) => i >= 0);
    st.cursor = -1;
  }

  function updateStepperUI(st) {
    const n = st.steppable.length;
    st.prev.disabled = !n;
    st.next.disabled = !n;
    if (!st.hasTerms()) st.pos.textContent = st.emptyText;
    else if (!n) st.pos.textContent = "No matches";
    else if (st.cursor < 0) st.pos.textContent = `${n} match${n === 1 ? "" : "es"}`;
    else st.pos.textContent = `Match ${st.cursor + 1} of ${n}`;
    const skipped = st.hits.length - n;
    st.note.hidden = !skipped;
    if (skipped) {
      st.note.textContent = `${skipped} more in ${[...excludedSections].join(" / ")} `
        + "(excluded from your search) — shown in grey, not stepped through.";
    }
  }

  // Tint the match's sentence -- unless the CAS "sentence" is really a run of
  // several (some papers' sentence spans cover whole paragraphs), where the
  // tint would just wash over the page.
  function tintSentence(segs) {
    clearClass("sent-current");
    const sent = segs.length ? segs[0].sent : -1;
    if (sent < 0) return;
    const s = doc.sentences[sent];
    if (s.end - s.begin > MAX_TINTED_SENTENCE) return;
    segments.forEach((seg) => { if (seg.sent === sent) seg.el.classList.add("sent-current"); });
  }

  function markCurrent(st, scroll) {
    clearClass(`${st.cls}-current`);
    if (st.cursor < 0) return;
    const hi = st.steppable[st.cursor];
    const segs = segments.filter((s) => s[st.field] === hi);
    segs.forEach((s) => s.el.classList.add(`${st.cls}-current`));
    if (lastStepped === st) tintSentence(segs);
    if (scroll && segs.length) scrollToEl(segs[0].el);
  }

  function gotoMatch(st, n) {
    if (!st.steppable.length) return;
    st.cursor = ((n % st.steppable.length) + st.steppable.length) % st.steppable.length;
    lastStepped = st;
    markCurrent(st, true);
    updateStepperUI(st);
  }

  function stepMatch(st, delta) {
    if (st.cursor < 0) gotoMatch(st, delta > 0 ? 0 : st.steppable.length - 1);
    else gotoMatch(st, st.cursor + delta);
  }

  // Re-render the paper with both match sets, keeping each stepper's place.
  function rerender() {
    renderText();
    markCurrent(steppers.kw, false);
    markCurrent(steppers.cat, false);
    updateStepperUI(steppers.kw);
    updateStepperUI(steppers.cat);
  }

  function computeKeywordHits() {
    const q = els.findInput.value.trim();
    if (q) params.set("q", q); else params.delete("q");
    history.replaceState(null, "", `?${params}`);
    els.searchMeta.textContent = q ? `“${q}”` : "";
    setHits(steppers.kw, keywordHits(q));
  }

  function computeCategoryHits() {
    params.delete("cat");
    findCats.forEach((c) => params.append("cat", c));
    if (catExpand.anc) params.set("anc", "1"); else params.delete("anc");
    if (catExpand.desc) params.set("desc", "1"); else params.delete("desc");
    history.replaceState(null, "", `?${params}`);
    const n = findCats.length;
    const dirs = [catExpand.anc && "broader", catExpand.desc && "narrower"].filter(Boolean);
    els.categoryMeta.textContent = n
      ? (dirs.length && findCats.some(isExpandable) ? `${n} (+${dirs.join("/")})` : `${n} categor${n === 1 ? "y" : "ies"}`) : "";
    setHits(steppers.cat, categoryHits());
    renderCatChips();
    updateExpandControls();
    updateExpandNote(false);
  }

  els.findForm.addEventListener("submit", (e) => {
    e.preventDefault();
    computeKeywordHits();
    rerender();
    if (steppers.kw.steppable.length) gotoMatch(steppers.kw, 0);
  });
  els.hitPrev.addEventListener("click", () => stepMatch(steppers.kw, -1));
  els.hitNext.addEventListener("click", () => stepMatch(steppers.kw, 1));
  els.catPrev.addEventListener("click", () => stepMatch(steppers.cat, -1));
  els.catNext.addEventListener("click", () => stepMatch(steppers.cat, 1));

  // ---------------------------------------------------------------------
  // Category finder: type-ahead over the categories annotated in this paper.
  // ---------------------------------------------------------------------

  let catOptions = [];      // [{category, name, ontology, id, count}] -- this paper's categories
  let catSuggestions = [];  // the ones currently listed
  let catActive = -1;       // keyboard-highlighted suggestion

  const ID_PREFIX_ONTOLOGY = {
    GO: "GO", PO: "PO", TO: "TO", tpzm: "MAIZE_GENES", tpzma: "MAIZE_GENES",
    // the eleven-channel annotation build; labels as casannot._ontology_of gives them
    CHEBI: "CHEBI", PATO: "PATO", PECO: "PECO", PSO: "PSO", SO: "SO", ECO: "ECO",
    CO_322: "CO_322", NCBITaxon: "NCBITaxon",
  };

  // One option per ontology ID (RELATED gene synonyms separately): the CAS
  // can spell the same term's category string more than one way, and
  // matching is by ID anyway.
  function buildCatOptions() {
    const byKey = new Map();
    doc.annotations.forEach((a) => {
      const key = `${a.ontology.endsWith("_RELATED") ? "R:" : ""}${a.onto_id || a.category}`;
      const o = byKey.get(key)
        || { category: a.category, name: categoryName(a.category), ontology: a.ontology, id: a.onto_id, count: 0 };
      o.count += 1;
      byKey.set(key, o);
    });
    catOptions = [...byKey.values()];
  }

  function ontologyOfCategory(category) {
    const opt = catOptions.find((o) => o.category === category);
    if (opt) return opt.ontology;
    const prefix = categoryKey(category).split(":")[0];
    const onto = ID_PREFIX_ONTOLOGY[prefix] || "OTHER";
    return category.startsWith("RELATED:") && onto === "MAIZE_GENES" ? "MAIZE_GENES_RELATED" : onto;
  }

  function badgeHtml(ontology) {
    const info = ontologyInfo(ontology);
    return `<span class="pop-badge" style="background:${info.color}">${escapeHtml(info.badge)}</span>`;
  }

  function renderCatChips() {
    els.catChips.innerHTML = "";
    findCats.forEach((category) => {
      const { own, extra } = doc ? chipMatches(category) : { own: [], extra: [] };
      const n = own.length + extra.length;
      const extraNames = new Map();
      extra.forEach((a) => {
        const name = categoryName(a.category);
        extraNames.set(name, (extraNames.get(name) || 0) + 1);
      });
      const chip = document.createElement("span");
      chip.className = `cat-chip${n ? "" : " none"}`;
      chip.title = !n ? "Not annotated in this paper"
        : `${own.length} for this term${extraNames.size
          ? `, ${extra.length} from related terms:\n${[...extraNames].map(([k, v]) => `  ${k} (${v})`).join("\n")}` : ""}`;
      chip.innerHTML = `${badgeHtml(ontologyOfCategory(category))}
        <span class="chip-label">${escapeHtml(categoryName(category))}</span>
        <span class="chip-count">${n}</span>
        ${extraNames.size ? `<span class="chip-extra">+${extraNames.size} term${extraNames.size === 1 ? "" : "s"}</span>` : ""}
        <button type="button" class="chip-remove" aria-label="Remove ${escapeHtml(categoryName(category))}">&times;</button>`;
      chip.querySelector("button").addEventListener("click", () => removeCat(category));
      els.catChips.appendChild(chip);
    });
  }

  function updateExpandControls() {
    els.expandRow.hidden = !findCats.some(isExpandable);
    els.expandAnc.checked = catExpand.anc;
    els.expandDesc.checked = catExpand.desc;
  }

  // Lookup progress, failures, and the 100-term cap.
  function updateExpandNote(loading) {
    const msgs = [];
    if (loading) msgs.push("Looking up broader/narrower terms…");
    else if (catExpand.anc || catExpand.desc) {
      findCats.filter(isExpandable).forEach((c) => {
        const e = expansions.get(c);
        if (!e) return;
        if (e.status === "error") msgs.push(`Couldn't look up related terms for ${categoryName(c)} (${e.error}).`);
        else if (catExpand.desc && e.truncated) {
          msgs.push(`${categoryName(c)}: only the first ${EXPANSION_CAP} narrower terms are included.`);
        }
      });
    }
    els.expandNote.hidden = !msgs.length;
    els.expandNote.textContent = msgs.join(" ");
  }

  async function refreshCategories(jumpTo) {
    await ensureExpansions();
    computeCategoryHits();
    rerender();
    if (!jumpTo) return;
    // Jump to that category's first match rather than the first match overall.
    const { own, extra } = chipMatches(jumpTo);
    const spans = own.concat(extra);
    const st = steppers.cat;
    const first = st.steppable.findIndex((hi) => spans.some((a) => a.begin < st.hits[hi].end && a.end > st.hits[hi].begin));
    if (first >= 0) gotoMatch(st, first);
  }

  function hideCatSuggestions() {
    els.catSuggest.hidden = true;
    els.catInput.setAttribute("aria-expanded", "false");
    els.catInput.removeAttribute("aria-activedescendant");
    catSuggestions = [];
    catActive = -1;
  }

  function highlightCatSuggestion(i) {
    catActive = i;
    els.catSuggest.querySelectorAll(".cat-option").forEach((li, j) => {
      li.classList.toggle("active", j === i);
      li.setAttribute("aria-selected", String(j === i));
      if (j === i) li.scrollIntoView({ block: "nearest" });
    });
    if (i >= 0) els.catInput.setAttribute("aria-activedescendant", `catOpt${i}`);
  }

  // Name matches (prefix first), then most frequent. Public IDs (GO:0009414)
  // are searchable too; internal gene IDs aren't shown or matched.
  function showCatSuggestions() {
    const q = els.catInput.value.trim().toLowerCase();
    if (!q) { hideCatSuggestions(); return; }
    const chosen = new Set(findCats.map(categoryKey));
    const starts = (o) => o.name.toLowerCase().startsWith(q);
    catSuggestions = catOptions
      .filter((o) => !chosen.has(o.id || o.category)
        && (o.name.toLowerCase().includes(q) || (!/^tpzm/i.test(o.id) && o.id.toLowerCase().includes(q))))
      .sort((a, b) => starts(b) - starts(a) || b.count - a.count || a.name.localeCompare(b.name))
      .slice(0, 8);
    els.catSuggest.innerHTML = catSuggestions.length
      ? catSuggestions.map((o, i) => `
          <li id="catOpt${i}" class="cat-option" role="option" aria-selected="false">
            ${badgeHtml(o.ontology)}
            <span class="cat-name">${escapeHtml(o.name)}${o.ontology.endsWith("_RELATED") ? ' <span class="meta">(related synonym)</span>' : ""}</span>
            <span class="count">${o.count}</span>
          </li>`).join("")
      : `<li class="cat-empty">No matching annotations in this paper</li>`;
    els.catSuggest.querySelectorAll(".cat-option").forEach((li, i) => {
      // mousedown, not click: fires before the input's blur hides the list.
      li.addEventListener("mousedown", (e) => { e.preventDefault(); addCat(catSuggestions[i].category); });
      li.addEventListener("mousemove", () => highlightCatSuggestion(i));
    });
    els.catSuggest.hidden = false;
    els.catInput.setAttribute("aria-expanded", "true");
    highlightCatSuggestion(catSuggestions.length ? 0 : -1);
  }

  function addCat(category) {
    if (!findCats.includes(category)) findCats.push(category);
    els.catInput.value = "";
    hideCatSuggestions();
    refreshCategories(category);
    els.catInput.focus();
  }

  function removeCat(category) {
    findCats = findCats.filter((c) => c !== category);
    refreshCategories(null);
  }

  els.catInput.addEventListener("input", showCatSuggestions);
  els.catInput.addEventListener("focus", showCatSuggestions);
  els.catInput.addEventListener("blur", () => setTimeout(hideCatSuggestions, 100));
  els.catInput.addEventListener("keydown", (e) => {
    const open = !els.catSuggest.hidden && catSuggestions.length;
    if (e.key === "ArrowDown" && open) {
      e.preventDefault();
      highlightCatSuggestion((catActive + 1) % catSuggestions.length);
    } else if (e.key === "ArrowUp" && open) {
      e.preventDefault();
      highlightCatSuggestion((catActive - 1 + catSuggestions.length) % catSuggestions.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && catActive >= 0) addCat(catSuggestions[catActive].category);
    } else if (e.key === "Escape") {
      hideCatSuggestions();
    } else if (e.key === "Backspace" && !els.catInput.value && findCats.length) {
      removeCat(findCats[findCats.length - 1]);
    }
  });

  els.expandAnc.addEventListener("change", () => { catExpand.anc = els.expandAnc.checked; refreshCategories(null); });
  els.expandDesc.addEventListener("change", () => { catExpand.desc = els.expandDesc.checked; refreshCategories(null); });

  // ---------------------------------------------------------------------
  // Collapsible sidebar panels -- all start collapsed; the choice is
  // remembered (per browser) once the user opens or closes one.
  // ---------------------------------------------------------------------

  const panelState = readStorage(PANELS_KEY) || {};
  document.querySelectorAll(".panel-toggle").forEach((btn) => {
    const name = btn.closest(".side-panel").dataset.panel;
    const body = $(btn.getAttribute("aria-controls"));
    const caret = btn.querySelector(".caret");
    const setOpen = (open) => {
      body.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
      caret.classList.toggle("open", open);
    };
    setOpen(Boolean(panelState[name]));
    btn.addEventListener("click", () => {
      const open = body.hidden;
      setOpen(open);
      panelState[name] = open;
      writeStorage(PANELS_KEY, panelState);
    });
  });

  // ---------------------------------------------------------------------
  // Rendering the paper
  // ---------------------------------------------------------------------

  function figureEl(fig, index) {
    const key = `${index}:${fig.name}`;
    if (figureEls.has(key)) return figureEls.get(key);
    const src = `${API_BASE}/api/figure?${new URLSearchParams({ identifier, name: fig.name })}`;
    const caption = `Image ${index + 1}${fig.page ? ` · page ${fig.page}` : ""}`;
    const el = document.createElement("figure");
    el.className = "doc-figure";
    el.id = `figure-${index + 1}`;
    el.innerHTML = `
      <a href="${escapeHtml(src)}" target="_blank" rel="noopener" title="Open full size in a new tab">
        <img loading="lazy" src="${escapeHtml(src)}" alt="${escapeHtml(caption)}">
      </a>
      <figcaption>${escapeHtml(caption)} &middot; click to enlarge</figcaption>`;
    el.querySelector("img").addEventListener("error", () => {
      const note = document.createElement("div");
      note.className = "figure-missing";
      note.textContent = `${caption}: this image isn't available.`;
      el.querySelector("a").replaceWith(note);
    }, { once: true });
    figureEls.set(key, el);
    return el;
  }

  // Cuts the text into <span>s at every annotation, match and sentence
  // boundary (so each span is wholly inside or outside each of those), and
  // into <p>/page dividers/figures at the structural positions.
  function renderText() {
    const anns = doc.annotations;
    const sents = doc.sentences;

    const markers = [];
    doc.pages.forEach((p) => { if (p.pos > 0) markers.push({ pos: p.pos, order: 0, kind: "page", page: p.page }); });
    doc.paragraphs.forEach((pos) => markers.push({ pos, order: 1, kind: "para" }));
    doc.figures.forEach((f, i) => markers.push({ pos: f.pos, order: 2, kind: "figure", fig: f, index: i }));
    markers.sort((a, b) => a.pos - b.pos || a.order - b.order);

    const cuts = new Set([0, text.length]);
    markers.forEach((m) => cuts.add(m.pos));
    anns.forEach((a) => { cuts.add(a.begin); cuts.add(a.end); });
    const hits = steppers.kw.hits;
    const chits = steppers.cat.hits;
    hits.forEach((h) => { cuts.add(h.begin); cuts.add(h.end); });
    chits.forEach((h) => { cuts.add(h.begin); cuts.add(h.end); });
    sents.forEach((s) => { cuts.add(s.begin); cuts.add(s.end); });
    const points = [...cuts].filter((p) => p >= 0 && p <= text.length).sort((a, b) => a - b);

    const frag = document.createDocumentFragment();
    let para = null;
    const emit = (m) => {
      para = null;
      if (m.kind === "page") {
        const div = document.createElement("div");
        div.className = "page-break";
        div.id = `page-${m.page}`;
        div.textContent = `Page ${m.page}`;
        frag.appendChild(div);
      } else if (m.kind === "figure") {
        frag.appendChild(figureEl(m.fig, m.index));
      }
    };

    segments = [];
    catSegs = new Map();
    let mi = 0;
    let ai = 0;
    let active = [];
    let si = 0;
    let sentIdx = -1;
    let hi = 0;
    let ci = 0;
    for (let k = 0; k < points.length - 1; k++) {
      const a = points[k];
      const b = points[k + 1];
      while (mi < markers.length && markers[mi].pos <= a) emit(markers[mi++]);

      while (ai < anns.length && anns[ai].begin <= a) active.push(ai++);
      active = active.filter((i) => anns[i].end > a);
      while (si < sents.length && sents[si].begin <= a) sentIdx = si++;
      const sent = sentIdx >= 0 && sents[sentIdx].end > a ? sentIdx : -1;
      while (hi < hits.length && hits[hi].end <= a) hi++;
      const hit = hi < hits.length && hits[hi].begin <= a ? hi : -1;
      while (ci < chits.length && chits[ci].end <= a) ci++;
      const chit = ci < chits.length && chits[ci].begin <= a ? ci : -1;

      const span = document.createElement("span");
      let cls = "seg";
      if (active.length) {
        cls += " ann";
        span.title = [...new Set(active.map((i) => categoryName(anns[i].category)))].join("\n");
      }
      if (hit >= 0) cls += hits[hit].excluded ? " hit hit-excluded" : " hit";
      if (chit >= 0) cls += chits[chit].excluded ? " chit chit-excluded" : " chit";
      span.className = cls;
      span.dataset.seg = segments.length;
      span.textContent = text.slice(a, b);
      if (!para) {
        para = document.createElement("p");
        frag.appendChild(para);
      }
      para.appendChild(span);
      const seg = { begin: a, end: b, anns: active.slice(), hit, chit, sent, el: span, shown: [] };
      active.forEach((i) => {
        const list = catSegs.get(anns[i].category) || [];
        if (list[list.length - 1] !== segments.length) list.push(segments.length);
        catSegs.set(anns[i].category, list);
      });
      segments.push(seg);
      refreshSeg(seg);
    }
    while (mi < markers.length) emit(markers[mi++]);

    frag.querySelectorAll("p").forEach((p) => { if (!p.textContent.trim()) p.remove(); });
    els.docText.replaceChildren(frag);
    applyFocus();
  }

  // ---------------------------------------------------------------------
  // Annotations: per-ontology toggles, "highlight this label", popover
  // ---------------------------------------------------------------------

  // Highlighting is chosen per category (term) in the sidebar. A segment
  // gets an ann-<ONTOLOGY> class (which colours it) only for annotations whose
  // category is switched on. Each ontology's on/off is remembered across
  // papers; individual term choices last for this page only.
  const ontoPrefs = readStorage(ONTO_PREFS_KEY) || {};

  function refreshSeg(seg) {
    seg.shown.forEach((o) => seg.el.classList.remove(`ann-${o}`));
    seg.shown = [...new Set(seg.anns
      .filter((i) => enabledCats.has(doc.annotations[i].category))
      .map((i) => doc.annotations[i].ontology))];
    seg.shown.forEach((o) => seg.el.classList.add(`ann-${o}`));
  }

  function setCategories(categories, on) {
    const touched = new Set();
    categories.forEach((c) => {
      if (on) enabledCats.add(c); else enabledCats.delete(c);
      (catSegs.get(c) || []).forEach((i) => touched.add(i));
    });
    touched.forEach((i) => refreshSeg(segments[i]));
    syncOntologyUI();
  }

  function rememberOntology(key, on) {
    ontoPrefs[key] = on;
    writeStorage(ONTO_PREFS_KEY, ontoPrefs);
  }

  // Master checkbox: checked = every term on, indeterminate = some.
  function syncOntologyUI() {
    ontoUI.forEach((ui) => {
      const n = ui.cats.filter((c) => enabledCats.has(c)).length;
      ui.master.checked = n === ui.cats.length;
      ui.master.indeterminate = n > 0 && n < ui.cats.length;
      ui.summary.textContent = n === ui.cats.length ? "all highlighted"
        : n ? `${n} highlighted` : "none highlighted";
      ui.rows.forEach(({ li, box }, c) => {
        box.checked = enabledCats.has(c);
        li.classList.toggle("off", !box.checked);
      });
    });
  }

  // For display: no "RELATED:" prefix, and no internal maize-gene IDs
  // ("cct1 (tpzm:0010325)" -> "cct1"). GO/PO/TO IDs are public and stay.
  function categoryName(category) {
    return category.replace(/^RELATED:/, "").replace(/\s*\(tpzma?:\d+\)/g, "").trim();
  }

  function renderOntologyPanel() {
    const byOnto = new Map();  // ontology -> Map(category -> {count, terms})
    doc.annotations.forEach((a) => {
      if (!byOnto.has(a.ontology)) byOnto.set(a.ontology, new Map());
      const cats = byOnto.get(a.ontology);
      if (!cats.has(a.category)) cats.set(a.category, { count: 0, terms: new Set() });
      const c = cats.get(a.category);
      c.count += 1;
      c.terms.add(a.term);
    });

    const order = ONTOLOGIES.map((o) => o.key).filter((k) => byOnto.has(k))
      .concat([...byOnto.keys()].filter((k) => !ONTOLOGIES.some((o) => o.key === k)));
    els.ontoGroups.innerHTML = "";
    ontoUI.clear();
    enabledCats.clear();
    els.annAll.parentElement.hidden = !order.length;
    const termCount = new Set(doc.annotations.map((a) => a.category)).size;
    els.annotationsMeta.textContent = termCount ? `${termCount} term${termCount === 1 ? "" : "s"}` : "none";
    if (!order.length) {
      els.ontoGroups.innerHTML = `<div class="field-note">No ontology annotations in this paper.</div>`;
      return;
    }

    order.forEach((key) => {
      const info = ontologyInfo(key);
      const cats = [...byOnto.get(key).entries()]
        .sort((x, y) => y[1].count - x[1].count || x[0].localeCompare(y[0]));
      const total = cats.reduce((n, [, c]) => n + c.count, 0);
      const group = document.createElement("div");
      group.className = "onto-group";
      group.innerHTML = `
        <label class="onto-toggle" title="Highlight all ${escapeHtml(info.label)} terms">
          <input type="checkbox" class="onto-master">
          <span class="onto-swatch" style="background:${info.color}"></span>
          ${escapeHtml(info.label)}
          <span class="count">${total}</span>
        </label>
        <details class="onto-terms">
          <summary>${cats.length} term${cats.length === 1 ? "" : "s"} &middot; <span class="shown"></span></summary>
          <div class="term-tools">
            ${cats.length > 10 ? `<input type="search" class="term-filter" placeholder="Filter terms" aria-label="Filter ${escapeHtml(info.label)} terms">` : ""}
            <button type="button" class="link-btn" data-act="all">Select all</button>
            <span class="sep">&middot;</span>
            <button type="button" class="link-btn" data-act="none">Deselect all</button>
          </div>
          <ul></ul>
        </details>`;

      const ui = {
        master: group.querySelector(".onto-master"),
        summary: group.querySelector(".shown"),
        cats: cats.map(([category]) => category),
        rows: new Map(),
      };
      ontoUI.set(key, ui);
      if (key in ontoPrefs ? ontoPrefs[key] : info.on) ui.cats.forEach((c) => enabledCats.add(c));

      // Clicking an indeterminate box checks it, i.e. "turn them all on".
      ui.master.addEventListener("change", () => {
        setCategories(ui.cats, ui.master.checked);
        rememberOntology(key, ui.master.checked);
      });

      const ul = group.querySelector("ul");
      cats.forEach(([category, c]) => {
        const li = document.createElement("li");
        li.className = "term-row";
        li.innerHTML = `
          <input type="checkbox" aria-label="Highlight ${escapeHtml(categoryName(category))}">
          <button type="button" class="term-btn" title="Step through every occurrence (matched as: ${escapeHtml([...c.terms].join(", "))})">
            <span>${escapeHtml(categoryName(category))}</span><span class="count">${c.count}</span>
          </button>`;
        const box = li.querySelector("input");
        box.addEventListener("change", () => setCategories([category], box.checked));
        const btn = li.querySelector("button");
        btn.dataset.category = category;
        btn.addEventListener("click", () => setFocus(category));
        ui.rows.set(category, { li, box });
        ul.appendChild(li);
      });

      // Select/deselect all act on the terms the filter is showing.
      const visibleCats = () => ui.cats.filter((cat) => !ui.rows.get(cat).li.hidden);
      group.querySelector('[data-act="all"]').addEventListener("click", () => setCategories(visibleCats(), true));
      group.querySelector('[data-act="none"]').addEventListener("click", () => setCategories(visibleCats(), false));
      const filter = group.querySelector(".term-filter");
      if (filter) {
        filter.addEventListener("input", () => {
          const q = filter.value.trim().toLowerCase();
          ui.rows.forEach(({ li }, cat) => { li.hidden = Boolean(q) && !cat.toLowerCase().includes(q); });
        });
      }
      els.ontoGroups.appendChild(group);
    });
    syncOntologyUI();
  }

  function setAllOntologies(on) {
    setCategories([...ontoUI.values()].flatMap((ui) => ui.cats), on);
    ontoUI.forEach((_, key) => rememberOntology(key, on));
  }

  els.annAll.addEventListener("click", () => setAllOntologies(true));
  els.annNone.addEventListener("click", () => setAllOntologies(false));

  function setFocus(category) {
    const occ = [];
    doc.annotations.forEach((a, i) => { if (a.category === category) occ.push(i); });
    if (!occ.length) return;
    focus = { category, occ, cursor: -1 };
    applyFocus();
    gotoFocus(0);
  }

  function clearFocus() {
    focus = null;
    applyFocus();
  }

  function applyFocus() {
    clearClass("ann-focus");
    clearClass("ann-current");
    els.ontoGroups.querySelectorAll(".term-btn").forEach((b) => {
      b.classList.toggle("active", Boolean(focus) && b.dataset.category === focus.category);
    });
    if (focus) {
      const set = new Set(focus.occ);
      segments.forEach((s) => { if (s.anns.some((i) => set.has(i))) s.el.classList.add("ann-focus"); });
      if (focus.cursor >= 0) markFocusCurrent();
    }
    updateFocusUI();
  }

  function markFocusCurrent() {
    clearClass("ann-current");
    const ai = focus.occ[focus.cursor];
    const segs = segments.filter((s) => s.anns.includes(ai));
    segs.forEach((s) => s.el.classList.add("ann-current"));
    return segs;
  }

  function gotoFocus(n) {
    const len = focus.occ.length;
    focus.cursor = ((n % len) + len) % len;
    const segs = markFocusCurrent();
    if (segs.length) scrollToEl(segs[0].el);
    updateFocusUI();
  }

  function updateFocusUI() {
    els.focusBox.hidden = !focus;
    if (!focus) return;
    const info = ontologyInfo(doc.annotations[focus.occ[0]].ontology);
    els.focusLabel.innerHTML = `<span class="pop-badge" style="background:${info.color}">${escapeHtml(info.badge)}</span> `
      + escapeHtml(categoryName(focus.category));
    els.focusPos.textContent = focus.cursor < 0
      ? `${focus.occ.length} occurrence${focus.occ.length === 1 ? "" : "s"}`
      : `${focus.cursor + 1} of ${focus.occ.length}`;
  }

  els.focusPrev.addEventListener("click", () => focus && gotoFocus(focus.cursor - 1));
  els.focusNext.addEventListener("click", () => focus && gotoFocus(focus.cursor + 1));
  els.focusClear.addEventListener("click", clearFocus);

  function hidePopover() {
    els.popover.hidden = true;
  }

  function showPopover(seg, x, y) {
    const seen = new Set();
    const items = [];
    seg.anns.forEach((i) => {
      const a = doc.annotations[i];
      if (seen.has(a.category)) return;
      seen.add(a.category);
      const info = ontologyInfo(a.ontology);
      const count = doc.annotations.filter((o) => o.category === a.category).length;
      items.push(`
        <div class="pop-item">
          <div class="pop-head">
            <span class="pop-badge" style="background:${info.color}">${escapeHtml(info.badge)}</span>
            <span class="pop-category">${escapeHtml(categoryName(a.category))}</span>
          </div>
          <div class="pop-term">Matched &ldquo;${escapeHtml(a.term)}&rdquo;${a.ontology.endsWith("_RELATED") ? " (related synonym)" : ""}</div>
          <button type="button" class="link-btn" data-category="${escapeHtml(a.category)}">
            Highlight all ${count} in this paper
          </button>
        </div>`);
    });
    els.popover.innerHTML = items.join("");
    els.popover.querySelectorAll("button[data-category]").forEach((btn) => {
      btn.addEventListener("click", () => {
        setFocus(btn.dataset.category);
        hidePopover();
      });
    });
    els.popover.hidden = false;
    const w = els.popover.offsetWidth;
    const h = els.popover.offsetHeight;
    els.popover.style.left = `${Math.max(8, Math.min(x - 20, window.innerWidth - w - 8))}px`;
    els.popover.style.top = `${y + 16 + h > window.innerHeight - 8 ? Math.max(8, y - h - 12) : y + 16}px`;
  }

  els.docText.addEventListener("click", (e) => {
    const span = e.target.closest(".seg.ann");
    if (!span || String(window.getSelection() || "").trim()) return;  // ignore text selection drags
    e.stopPropagation();
    showPopover(segments[Number(span.dataset.seg)], e.clientX, e.clientY);
  });
  document.addEventListener("click", (e) => {
    if (!els.popover.hidden && !els.popover.contains(e.target)) hidePopover();
  });
  els.main.addEventListener("scroll", hidePopover, { passive: true });
  window.addEventListener("resize", hidePopover);

  // ---------------------------------------------------------------------
  // Contents (sections + figures)
  // ---------------------------------------------------------------------

  function renderToc() {
    // The CAS often splits one section into several adjacent spans of the
    // same type, so list a type again only once something else came between.
    const rows = [];
    let lastType = null;
    doc.sections.forEach((s) => {
      if (s.type === lastType || s.end <= s.begin) return;
      lastType = s.type;
      const label = s.type.charAt(0).toUpperCase() + s.type.slice(1);
      const meta = excludedSections.has(s.type) ? "not searched" : "";
      rows.push({ label, meta, go: () => { const seg = segmentAt(s.begin); if (seg) scrollToEl(seg.el); } });
    });
    const figRows = doc.figures.map((f, i) => ({
      label: `Image ${i + 1}`,
      meta: f.page ? `page ${f.page}` : "",
      go: () => scrollToEl($(`figure-${i + 1}`)),
    }));

    els.tocList.innerHTML = "";
    const addGroup = (title, list) => {
      if (!list.length) return;
      const head = document.createElement("li");
      head.className = "toc-group";
      head.textContent = title;
      els.tocList.appendChild(head);
      list.forEach((r) => {
        const li = document.createElement("li");
        li.innerHTML = `<button type="button"><span>${escapeHtml(r.label)}</span><span class="meta">${escapeHtml(r.meta)}</span></button>`;
        li.querySelector("button").addEventListener("click", r.go);
        els.tocList.appendChild(li);
      });
    };
    addGroup("Sections", rows);
    addGroup("Figures", figRows);
    els.contentsMeta.textContent = [
      rows.length ? `${rows.length} section${rows.length === 1 ? "" : "s"}` : "",
      figRows.length ? `${figRows.length} figure${figRows.length === 1 ? "" : "s"}` : "",
    ].filter(Boolean).join(" · ");
    if (!rows.length && !figRows.length) {
      els.tocList.innerHTML = `<li class="field-note">No sections or figures detected.</li>`;
    }
  }

  // ---------------------------------------------------------------------
  // Header + previous/next paper
  // ---------------------------------------------------------------------

  // The result list app.js saved when the search results were shown;
  // restricted papers (no viewer) are skipped.
  function setupPaperNav() {
    const ctx = readStorage(CONTEXT_KEY);
    const items = ctx && Array.isArray(ctx.items) ? ctx.items.filter((it) => it.viewer_allowed !== false) : [];
    const idx = items.findIndex((it) => it.identifier === identifier);
    if (idx < 0) return null;
    const go = (it) => {
      params.set("id", it.identifier);
      location.href = `viewer.html?${params}`;
    };
    els.paperNav.hidden = false;
    els.paperPos.textContent = `Paper ${idx + 1} of ${items.length}`;
    els.prevPaper.disabled = idx === 0;
    els.nextPaper.disabled = idx === items.length - 1;
    els.prevPaper.addEventListener("click", () => go(items[idx - 1]));
    els.nextPaper.addEventListener("click", () => go(items[idx + 1]));
    return items[idx];
  }

  function renderHeader(ctxItem) {
    const meta = (doc && doc.metadata) || {};
    const ctx = ctxItem || {};
    const accession = (doc && doc.accession) || identifier.split("/").filter(Boolean)[1] || "";
    const title = meta.title || ctx.title || accession || "Untitled";
    els.docHeader.hidden = false;
    els.docTitle.innerHTML = sanitizeTitle(title);
    document.title = `${title.replace(/<[^>]+>/g, "")} — MaizeGDB Textpresso`;
    els.docAuthors.textContent = meta.author || ctx.author || "";

    // PubMed's DOI (saved by app.js) when we have it; otherwise the same
    // first-"_" guess app.js falls back to.
    const doi = ctx.doi || (accession.startsWith("10.") ? accession.replace("_", "/") : "");
    const parts = [];
    const cite = [meta.journal || ctx.journal, meta.year || ctx.year].filter(Boolean).join(", ");
    if (cite) parts.push(`<span>${escapeHtml(cite)}</span>`);
    const corpus = (doc && doc.corpus) || identifier.split("/")[0];
    if (corpus) parts.push(`<span class="corpus-badge">${escapeHtml(corpus)}</span>`);
    if (doc && doc.pmid) {
      parts.push(`<a href="https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(doc.pmid)}/" target="_blank" rel="noopener">PubMed ${escapeHtml(doc.pmid)}</a>`);
    }
    if (doi) {
      parts.push(`<a href="https://doi.org/${escapeHtml(doi)}" target="_blank" rel="noopener" title="doi:${escapeHtml(doi)} (opens the publisher's site in a new tab)">Publisher full text <span aria-hidden="true">&#8599;</span></a>`);
    }
    els.docCitation.innerHTML = parts.join("");
  }

  // ---------------------------------------------------------------------
  // Keyboard: [ / ] step through matches (or the highlighted label), Esc closes the popover
  // ---------------------------------------------------------------------

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { hidePopover(); return; }
    if (!doc || e.ctrlKey || e.metaKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.key === "{" || e.key === "}") {
      stepMatch(steppers.cat, e.key === "}" ? 1 : -1);
      e.preventDefault();
      return;
    }
    if (e.key !== "[" && e.key !== "]") return;
    const delta = e.key === "]" ? 1 : -1;
    if (focus) gotoFocus(focus.cursor < 0 ? 0 : focus.cursor + delta);
    else stepMatch(steppers.kw, delta);
    e.preventDefault();
  });

  // ---------------------------------------------------------------------

  async function load() {
    const ctxItem = setupPaperNav();
    if (!identifier) {
      els.docText.innerHTML = `<div class="error-state">No paper selected. Open one from the search results with &ldquo;Annotated full text&rdquo;.</div>`;
      return;
    }
    try {
      doc = await fetchJSON(`${API_BASE}/api/document?${new URLSearchParams({ identifier })}`);
    } catch (e) {
      renderHeader(ctxItem);
      const msg = e.status === 403
        ? "Full text is available for open-access papers only. Signing in to read restricted papers isn't supported yet."
        : e.status === 404 ? "This paper's full text isn't in the local Textpresso data."
        : e.status ? `Could not load this paper: ${e.message}`
        : `Could not reach the Textpresso backend at ${API_BASE} (${e.message}). Is it running? See GUI_SETUP.md.`;
      els.docText.innerHTML = `<div class="error-state">${escapeHtml(msg)}</div>`;
      return;
    }

    text = doc.text.replace(/[\r\n]/g, " ");
    renderHeader(ctxItem);
    els.sidebar.hidden = false;
    buildCatOptions();
    renderOntologyPanel();
    renderToc();
    els.findInput.value = params.get("q") || "";
    computeKeywordHits();
    computeCategoryHits();
    rerender();
    // Jump straight to the first match, like opening a paper from Textpresso
    // Central's search -- keywords first, else categories.
    if (steppers.kw.steppable.length) gotoMatch(steppers.kw, 0);
    else if (steppers.cat.steppable.length) gotoMatch(steppers.cat, 0);
    // Broader/narrower terms need a lookup; fold them in once they arrive.
    if ((catExpand.anc || catExpand.desc) && findCats.some(isExpandable)) refreshCategories(null);
  }

  load();
})();
