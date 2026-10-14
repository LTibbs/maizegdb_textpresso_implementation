(function () {
  "use strict";

  // Adjust if the backend is running on a different port -- see GUI_SETUP.md.
  const API_BASE = window.TEXTPRESSO_API_BASE || "http://127.0.0.1:8010";

  const $ = (id) => document.getElementById(id);

  const els = {
    statusBanner: $("statusBanner"),
    form: $("searchForm"),
    keywords: $("keywords"),
    searchIn: $("searchIn"),
    advancedToggle: $("advancedToggle"),
    advancedCaret: $("advancedCaret"),
    advancedPanel: $("advancedPanel"),
    corporaList: $("corporaList"),
    excludeKeywords: $("excludeKeywords"),
    categoryBox: $("categoryBox"),
    categoryChips: $("categoryChips"),
    categoryQuery: $("categoryQuery"),
    categoryResults: $("categoryResults"),
    categoryNote: $("categoryNote"),
    categoryOptions: $("categoryOptions"),
    matchModeGroup: $("matchModeGroup"),
    relatedSynonymsLabel: $("relatedSynonymsLabel"),
    expandAncestors: $("expandAncestors"),
    expandDescendants: $("expandDescendants"),
    expandAncestorsLabel: $("expandAncestorsLabel"),
    expandDescendantsLabel: $("expandDescendantsLabel"),
    relatedSynonyms: $("relatedSynonyms"),
    author: $("author"),
    journal: $("journal"),
    year: $("year"),
    paperType: $("paperType"),
    includeReferences: $("includeReferences"),
    includeAcknowledgments: $("includeAcknowledgments"),
    resetBtn: $("resetBtn"),
    resultsSummary: $("resultsSummary"),
    resultsCount: $("resultsCount"),
    resultsList: $("resultsList"),
    exportCsv: $("exportCsv"),
    exportJson: $("exportJson"),
    loadMoreRow: $("loadMoreRow"),
    loadMoreBtn: $("loadMoreBtn"),
    viewTable: $("viewTable"),
    viewCards: $("viewCards"),
    expandControls: $("expandControls"),
  };

  let selectedCategories = [];      // full match objects from /api/category_search, one per chip
  let lastResults = [];             // raw docs from the last successful search
  let lastQueryBody = null;         // the request body used, for "Load more"
  let lastCategoryDesc = "";        // readable categories of the last search, for the summary
  let lastScopeDesc = "";           // e.g. "in the abstract" when keywords were limited to one section
  let viewMode = "table";           // "table" | "cards"
  let tableSort = null;             // { key, dir: 1 | -1 } from a column header, or null = server order
  const annotateCache = new Map();  // identifier|exclude_sections -> Promise<{ summary } | { error }>
  const citationCache = new Map();  // identifier -> PubMed citation details (see /api/citations)

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Titles from the API sometimes carry a deliberate <i>species name</i>
  // convention (see docs/TPC_SEARCH_TUTORIAL.md), plus <sup>/<sub> for
  // things like Ca<sup>2+</sup>. Escape everything, then un-escape only
  // those exact tags, rather than trusting raw HTML from an external response.
  // Maize gene categories carry internal Textpresso IDs ("cct1 (tpzm:0010325)",
  // optionally "RELATED:"-prefixed) that mean nothing to users -- strip those
  // for display. GO/PO/TO IDs are real public IDs and stay.
  const INTERNAL_ID_RE = /\s*\(tpzma?:\d+\)/g;
  function displayCategory(s) {
    return String(s || "").replace(/^RELATED:/, "").replace(INTERNAL_ID_RE, "").trim();
  }
  function displayId(id) {
    return /^tpzma?:/.test(id || "") ? "" : (id || "");
  }

  function sanitizeTitle(s) {
    return escapeHtml(s).replace(/&lt;(\/?(?:i|sup|sub))&gt;/g, "<$1>");
  }

  function escapeRegExp(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function showStatus(message) {
    if (!message) {
      els.statusBanner.hidden = true;
      return;
    }
    els.statusBanner.textContent = message;
    els.statusBanner.hidden = false;
  }

  // timeoutMs: abort a request the backend never answers (e.g. it was
  // restarted mid-request), so callers see an error instead of hanging.
  async function fetchJSON(url, options, timeoutMs) {
    let res;
    if (timeoutMs) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs);
      try {
        res = await fetch(url, { ...options, signal: ctrl.signal });
      } catch (e) {
        if (e.name === "AbortError") {
          const err = new Error(`no response from the backend after ${timeoutMs / 1000}s`);
          err.timedOut = true;
          throw err;
        }
        throw e;
      } finally {
        clearTimeout(timer);
      }
    } else {
      res = await fetch(url, options);
    }
    let body = null;
    try { body = await res.json(); } catch (_) { /* non-JSON error body */ }
    if (!res.ok) {
      const detail = body && body.detail ? body.detail : res.statusText;
      const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  // ---------------------------------------------------------------------
  // Corpora
  // ---------------------------------------------------------------------

  async function loadCorpora() {
    try {
      const corpora = await fetchJSON(`${API_BASE}/api/corpora`);
      els.corporaList.innerHTML = "";
      const allLabel = document.createElement("label");
      allLabel.innerHTML = `<input type="checkbox" id="corpusAll"> All corpora (${corpora.length})`;
      els.corporaList.appendChild(allLabel);

      corpora.forEach((name) => {
        const label = document.createElement("label");
        label.innerHTML = `<input type="checkbox" class="corpus-item" value="${escapeHtml(name)}"> ${escapeHtml(name)}`;
        els.corporaList.appendChild(label);
      });

      const allBox = $("corpusAll");
      allBox.addEventListener("change", () => {
        document.querySelectorAll(".corpus-item").forEach((cb) => {
          cb.disabled = allBox.checked;
        });
      });
      applyCorpusDefaults();
    } catch (e) {
      els.corporaList.textContent = "Could not load corpus list.";
      showStatus(`Could not reach the Textpresso backend at ${API_BASE} (${e.message}). Is it running? See GUI_SETUP.md.`);
    }
  }

  // Default to the corpora that have been processed locally (only MaizeOA so
  // far -- the others still need Textpresso run on them). Falls back to all
  // corpora if none of the defaults exist on this server.
  const DEFAULT_CORPORA = ["MaizeOA"];

  function applyCorpusDefaults() {
    const allBox = $("corpusAll");
    if (!allBox) return;
    const items = Array.from(document.querySelectorAll(".corpus-item"));
    const useAll = !items.some((cb) => DEFAULT_CORPORA.includes(cb.value));
    allBox.checked = useAll;
    items.forEach((cb) => {
      cb.checked = useAll || DEFAULT_CORPORA.includes(cb.value);
      cb.disabled = useAll;
    });
  }

  function selectedCorpora() {
    const allBox = $("corpusAll");
    if (!allBox || allBox.checked) return null; // omit -> backend defaults to all
    return Array.from(document.querySelectorAll(".corpus-item:checked")).map((cb) => cb.value);
  }

  // ---------------------------------------------------------------------
  // Advanced panel toggle
  // ---------------------------------------------------------------------

  els.advancedToggle.addEventListener("click", () => {
    const open = els.advancedPanel.hidden;
    els.advancedPanel.hidden = !open;
    els.advancedCaret.classList.toggle("open", open);
  });

  document.querySelectorAll(".try-link").forEach((a) => {
    a.addEventListener("click", async (e) => {
      e.preventDefault();
      if (a.dataset.category) {
        // Category example: replace the whole query with just this category.
        const match = await exactCategoryMatch(a.dataset.category);
        if (!match) return;
        els.keywords.value = "";
        selectedCategories = [];
        addCategory(match);
      } else {
        els.keywords.value = a.dataset.q;
      }
      startNewSearch();
    });
  });

  // ---------------------------------------------------------------------
  // Category / gene type-ahead
  // ---------------------------------------------------------------------
  //
  // Category search needs the exact stored "name (ID)" string, so free text
  // is never sent as a category: the user picks from /api/category_search
  // suggestions, and each pick becomes a chip.

  const ONTOLOGY_LABELS = { GO: "GO", PO: "PO", TO: "TO", MAIZE_GENES: "Gene", MAIZE_GENES_RELATED: "Gene" };
  let categoryMatches = [];   // suggestions currently shown in the dropdown
  let activeMatch = -1;       // keyboard-highlighted suggestion
  let lookupSeq = 0;          // drops responses to superseded lookups
  let lookupTimer = null;

  function isGene(match) {
    return match.ontology === "MAIZE_GENES" || match.ontology === "MAIZE_GENES_RELATED";
  }

  function ontologyLabel(ontology) {
    return ONTOLOGY_LABELS[ontology] || ontology || "?";
  }

  function ontologyClass(ontology) {
    return `onto-${String(ontology || "other").toLowerCase().replace(/[^a-z_]/g, "")}`;
  }

  async function fetchCategoryMatches(q) {
    const data = await fetchJSON(`${API_BASE}/api/category_search?${new URLSearchParams({ q, limit: 8 })}`);
    return data.matches || [];
  }

  function showCategoryNote(message) {
    els.categoryNote.hidden = !message;
    els.categoryNote.textContent = message || "";
  }

  function lookupErrorMessage(e) {
    return e.status === 502
      ? "Category lookup is temporarily unavailable (the Textpresso annotation service is down)."
      : `Category lookup failed: ${e.message}`;
  }

  async function lookupCategories() {
    const q = els.categoryQuery.value.trim();
    const seq = ++lookupSeq;
    if (q.length < 2) { closeDropdown(); return; }
    try {
      const matches = await fetchCategoryMatches(q);
      if (seq !== lookupSeq) return;
      showCategoryNote(null);
      renderCategoryMatches(matches);
    } catch (e) {
      if (seq !== lookupSeq) return;
      closeDropdown();
      showCategoryNote(lookupErrorMessage(e));
    }
  }

  function renderCategoryMatches(matches) {
    const chosen = new Set(selectedCategories.map((m) => m.category));
    categoryMatches = matches.filter((m) => !chosen.has(m.category));
    activeMatch = categoryMatches.length ? 0 : -1;
    els.categoryResults.innerHTML = "";
    if (!categoryMatches.length) {
      els.categoryResults.innerHTML = `<div class="category-empty">No matching categories.</div>`;
    }
    categoryMatches.forEach((m, i) => {
      const row = document.createElement("div");
      row.className = "category-match";
      row.setAttribute("role", "option");
      const synonym = /synonym/i.test(m.matched_on || "")
        ? ` <span class="meta">(${escapeHtml(m.matched_on.replace(/_/g, " "))})</span>` : "";
      row.innerHTML = `
        <span class="onto-badge ${ontologyClass(m.ontology)}">${escapeHtml(ontologyLabel(m.ontology))}</span>
        <span class="category-name">${escapeHtml(m.name || displayCategory(m.category))}${synonym}</span>
        <span class="category-id">${escapeHtml(displayId(m.id))}</span>`;
      // mousedown, not click: fires before the input's blur closes the dropdown.
      row.addEventListener("mousedown", (e) => { e.preventDefault(); chooseMatch(i); });
      row.addEventListener("mousemove", () => highlightMatch(i));
      els.categoryResults.appendChild(row);
    });
    els.categoryResults.hidden = false;
    els.categoryQuery.setAttribute("aria-expanded", "true");
    highlightMatch(activeMatch);
  }

  function highlightMatch(i) {
    activeMatch = i;
    els.categoryResults.querySelectorAll(".category-match").forEach((row, j) => {
      row.classList.toggle("active", j === i);
      row.setAttribute("aria-selected", String(j === i));
      if (j === i) row.scrollIntoView({ block: "nearest" });
    });
  }

  function closeDropdown() {
    els.categoryResults.hidden = true;
    els.categoryQuery.setAttribute("aria-expanded", "false");
    categoryMatches = [];
    activeMatch = -1;
  }

  function chooseMatch(i) {
    const match = categoryMatches[i];
    if (!match) return;
    addCategory(match);
    els.categoryQuery.value = "";
    closeDropdown();
    els.categoryQuery.focus();
  }

  function addCategory(match) {
    if (!selectedCategories.some((m) => m.category === match.category)) {
      selectedCategories.push(match);
    }
    showCategoryNote(null);
    renderChips();
  }

  function removeCategory(category) {
    selectedCategories = selectedCategories.filter((m) => m.category !== category);
    renderChips();
  }

  function renderChips() {
    els.categoryChips.innerHTML = "";
    selectedCategories.forEach((m) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.title = displayCategory(m.category);
      chip.innerHTML = `
        <span class="onto-badge ${ontologyClass(m.ontology)}">${escapeHtml(ontologyLabel(m.ontology))}</span>
        <span class="chip-name">${escapeHtml(m.name || displayCategory(m.category))}</span>
        <button type="button" class="chip-remove" aria-label="Remove ${escapeHtml(m.name || displayCategory(m.category))}">&times;</button>`;
      chip.querySelector("button").addEventListener("click", () => {
        removeCategory(m.category);
        els.categoryQuery.focus();
      });
      els.categoryChips.appendChild(chip);
    });
    // Options only appear once they mean something: any/all needs two
    // categories, ancestor/descendant expansion needs an ontology (non-gene)
    // term, related synonyms only applies to genes.
    const hasOntologyTerm = selectedCategories.some((m) => !isGene(m));
    els.categoryOptions.hidden = !selectedCategories.length;
    els.matchModeGroup.hidden = selectedCategories.length < 2;
    els.expandAncestorsLabel.hidden = !hasOntologyTerm;
    els.expandDescendantsLabel.hidden = !hasOntologyTerm;
    els.relatedSynonymsLabel.hidden = !selectedCategories.some(isGene);
    els.categoryQuery.placeholder = selectedCategories.length
      ? "Add another category…"
      : "Type a gene or a GO/PO/TO term, e.g. cct1, seed development";
  }

  // The exact match for a typed term, if the lookup has one -- used when the
  // user presses Search with text still in the box, and by the Try links.
  async function exactCategoryMatch(q) {
    try {
      const matches = await fetchCategoryMatches(q);
      const lower = q.toLowerCase();
      return matches.find((m) => m.matched_on === "exact")
        || matches.find((m) => (m.name || "").toLowerCase() === lower)
        || null;
    } catch (e) {
      showCategoryNote(lookupErrorMessage(e));
      return null;
    }
  }

  // Text left in the category box when Search is pressed: add it if it's an
  // exact match, otherwise stop and show suggestions rather than silently
  // searching without it. Returns false if the search should not run.
  async function resolvePendingCategory() {
    const q = els.categoryQuery.value.trim();
    if (!q) return true;
    const match = await exactCategoryMatch(q);
    if (match) {
      addCategory(match);
      els.categoryQuery.value = "";
      closeDropdown();
      return true;
    }
    if (els.categoryNote.hidden) {
      await lookupCategories();
      showCategoryNote(`"${q}" isn't an exact category. Pick one from the list, or clear the box to search without it.`);
    }
    els.categoryQuery.focus();
    return false;
  }

  els.categoryQuery.addEventListener("input", () => {
    clearTimeout(lookupTimer);
    showCategoryNote(null);
    lookupTimer = setTimeout(lookupCategories, 250);
  });

  els.categoryQuery.addEventListener("keydown", (e) => {
    const open = !els.categoryResults.hidden && categoryMatches.length;
    if (e.key === "ArrowDown" && open) {
      e.preventDefault();
      highlightMatch((activeMatch + 1) % categoryMatches.length);
    } else if (e.key === "ArrowUp" && open) {
      e.preventDefault();
      highlightMatch((activeMatch - 1 + categoryMatches.length) % categoryMatches.length);
    } else if (e.key === "Enter" && open && activeMatch >= 0) {
      e.preventDefault();
      chooseMatch(activeMatch);
    } else if (e.key === "Escape") {
      closeDropdown();
    } else if (e.key === "Backspace" && !els.categoryQuery.value && selectedCategories.length) {
      removeCategory(selectedCategories[selectedCategories.length - 1].category);
    }
    // Enter with no suggestion open falls through to the form's submit.
  });

  els.categoryQuery.addEventListener("blur", () => setTimeout(closeDropdown, 100));
  els.categoryQuery.addEventListener("focus", () => {
    if (els.categoryQuery.value.trim().length >= 2) lookupCategories();
  });
  // Clicking the box's empty space (beside the chips) focuses the text input.
  els.categoryBox.addEventListener("mousedown", (e) => {
    if (e.target === els.categoryBox || e.target === els.categoryChips) {
      e.preventDefault();
      els.categoryQuery.focus();
    }
  });

  // ---------------------------------------------------------------------
  // Search
  // ---------------------------------------------------------------------

  function buildRequestBody(count) {
    const excludeSections = [];
    if (!els.includeReferences.checked) excludeSections.push("references");
    if (!els.includeAcknowledgments.checked) excludeSections.push("acknowledgments");

    const body = {
      keywords: els.keywords.value.trim() || null,
      search_in: els.searchIn.value,
      corpora: selectedCorpora(),
      count: count,
      exclude_keywords: els.excludeKeywords.value.trim() || null,
      author: els.author.value.trim() || null,
      journal: els.journal.value.trim() || null,
      year: els.year.value.trim() || null,
      paper_type: els.paperType.value || null,
      exclude_sections: excludeSections,
      related_synonyms: els.relatedSynonyms.checked,
    };

    if (selectedCategories.length) {
      // Relationship types are per term; the backend expands each category
      // along whichever of these it actually has (and ignores the rest).
      // Gene terms are never expanded (the backend skips them too), and a
      // checkbox only counts while it's visible.
      const terms = selectedCategories.filter((m) => !isGene(m));
      const union = (key) => [...new Set(terms.flatMap((m) => m[key] || []))];
      body.category = selectedCategories.map((m) => m.category);
      body.categories_and = categoryMatchMode() === "all";
      body.expand_ancestor_relationship_types = els.expandAncestors.checked && !els.expandAncestorsLabel.hidden
        ? union("parent_relationship_types") : [];
      body.expand_relationship_types = els.expandDescendants.checked && !els.expandDescendantsLabel.hidden
        ? union("relationship_types") : [];
    }

    return body;
  }

  function categoryMatchMode() {
    const checked = document.querySelector('input[name="categoryMatch"]:checked');
    return checked ? checked.value : "all";
  }

  // "cct1 (gene) and drought recovery (TO)" -- for the results summary.
  function describeCategories() {
    if (!selectedCategories.length) return "";
    const joiner = categoryMatchMode() === "all" ? " and " : " or ";
    return selectedCategories.map((m) => {
      const label = ontologyLabel(m.ontology);
      return `${m.name || displayCategory(m.category)} (${label === "Gene" ? "gene" : label})`;
    }).join(joiner);
  }

  els.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!(await resolvePendingCategory())) return;
    startNewSearch();
  });

  els.resetBtn.addEventListener("click", () => {
    els.form.reset();
    selectedCategories = [];
    renderChips();
    closeDropdown();
    showCategoryNote(null);
    applyCorpusDefaults();
  });

  // A fresh search (not "Load more") goes back to relevance order with
  // every detail section collapsed.
  function startNewSearch() {
    tableSort = null;
    sectionState.clear();
    runSearch();
  }

  async function runSearch(count) {
    if (!els.keywords.value.trim() && !els.author.value.trim() && !els.journal.value.trim()
        && !els.year.value.trim() && !selectedCategories.length) {
      showStatus("Enter keywords, pick a category, or set an author/journal/year filter, then search.");
      return;
    }
    showStatus(null);
    const body = buildRequestBody(count || 50);
    lastQueryBody = body;
    lastCategoryDesc = describeCategories();
    // The section dropdown lives in Advanced, so say so in the summary
    // rather than let a non-default scope go unnoticed.
    const scopeOption = els.searchIn.selectedOptions[0];
    lastScopeDesc = body.keywords && els.searchIn.value !== "full_text" && scopeOption
      ? `in the ${scopeOption.textContent.trim().toLowerCase()}` : "";
    els.resultsList.innerHTML = `<div class="empty-state">Searching&hellip;</div>`;
    els.resultsSummary.hidden = true;
    els.loadMoreRow.hidden = true;

    try {
      const data = await fetchJSON(`${API_BASE}/api/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      lastResults = data.results || [];
      renderResults(lastResults, body.keywords || "");
      loadCitations(lastResults);
    } catch (e) {
      els.resultsList.innerHTML = `<div class="error-state">Search failed: ${escapeHtml(e.message)}</div>`;
    }
  }

  function highlightTerms(text, keywords) {
    let out = escapeHtml(text);
    if (!keywords) return out;
    const terms = keywords
      .replace(/["()]/g, " ")
      .split(/\s+/)
      .filter((t) => t && !["AND", "OR", "NOT"].includes(t.toUpperCase()));
    terms.forEach((t) => {
      const re = new RegExp(`(${escapeRegExp(escapeHtml(t))})`, "gi");
      out = out.replace(re, "<mark>$1</mark>");
    });
    return out;
  }

  function corpusOf(identifier) {
    return (identifier || "").split("/")[0] || "?";
  }

  // ---------------------------------------------------------------------
  // Full-text viewer (viewer.html)
  // ---------------------------------------------------------------------

  // Carries the search along so the viewer highlights and steps through the
  // same matches -- see the top of viewer.js for the parameters.
  function viewerUrl(doc) {
    const p = new URLSearchParams({ id: doc.identifier });
    const q = lastQueryBody || {};
    if (q.keywords) p.set("q", q.keywords);
    if (q.case_sensitive) p.set("cs", "1");
    if ((q.exclude_sections || []).length) p.set("ex", q.exclude_sections.join(","));
    (q.category || []).forEach((c) => p.append("cat", c));
    if ((q.category || []).length && q.related_synonyms) p.set("rel", "1");
    if ((q.expand_ancestor_relationship_types || []).length) p.set("anc", "1");
    if ((q.expand_relationship_types || []).length) p.set("desc", "1");
    return `viewer.html?${p}`;
  }

  // Only for papers the backend says can be opened (open access for now --
  // see _can_view_full_text() in backend/main.py).
  function viewerLink(doc) {
    return doc.viewer_allowed
      ? `<a class="action-btn viewer-link" href="${escapeHtml(viewerUrl(doc))}" target="_blank" rel="noopener">Annotated full text</a>`
      : "";
  }

  // The title opens the viewer too, where there is one; otherwise plain text.
  function titleHtml(doc) {
    const title = sanitizeTitle(doc.title || "Untitled");
    return doc.viewer_allowed
      ? `<a class="title-link" href="${escapeHtml(viewerUrl(doc))}" target="_blank" rel="noopener" title="Annotated full text">${title}</a>`
      : title;
  }

  // The arrow marks it as leaving this site (applyCitations() swaps in
  // PubMed's DOI once it arrives).
  function publisherLink(doi) {
    return doi
      ? `<a class="action-btn fulltext-link" href="https://doi.org/${escapeHtml(doi)}" target="_blank" rel="noopener"
           title="Opens the publisher's site in a new tab">Publisher full&nbsp;text&nbsp;<span class="ext-arrow" aria-hidden="true">&#8599;</span></a>`
      : "";
  }

  // The results in display order, for the viewer's previous/next-paper
  // buttons. localStorage rather than sessionStorage because the viewer
  // opens in a new tab.
  function saveViewerContext(ordered) {
    try {
      localStorage.setItem("tpViewerContext", JSON.stringify({
        items: ordered.map((d) => ({
          identifier: d.identifier,
          viewer_allowed: Boolean(d.viewer_allowed),
          title: d.title, author: d.author, journal: d.journal, year: d.year,
          doi: doiOf(d),
        })),
      }));
    } catch (_) { /* storage unavailable: the viewer just has no prev/next */ }
  }

  // ---------------------------------------------------------------------
  // Citations (volume/issue/pages + real DOI, from PubMed via the backend)
  // ---------------------------------------------------------------------

  // Textpresso accessions replace "/" with "_". PubMed's DOI is authoritative;
  // this guess (first "_" only) is the fallback and is wrong for DOIs with
  // more than one slash, e.g. 10.1093/jxb/erq180.
  function doiOf(doc) {
    const cite = citationCache.get(doc.identifier);
    if (cite && cite.doi) return cite.doi;
    return (doc.accession || "").startsWith("10.") ? doc.accession.replace("_", "/") : "";
  }

  // "BMC Plant Biol 25(1):1083" -- falls back to the bare journal abbreviation.
  function citationLine(doc) {
    const cite = citationCache.get(doc.identifier) || {};
    let out = cite.journal || doc.journal || "";
    if (cite.volume) out += ` ${cite.volume}`;
    if (cite.issue) out += `(${cite.issue})`;
    if (cite.pages) out += `${cite.volume || cite.issue ? ":" : " "}${cite.pages}`;
    return out;
  }

  function plainCitation(doc) {
    const strip = (s) => String(s || "").replace(/<\/?(?:i|sup|sub)>/g, "").trim();
    const withStop = (s) => (s && !/[.?!]$/.test(s) ? `${s}.` : s);
    const doi = doiOf(doc);
    return [
      withStop(strip(doc.author)),
      doc.year ? `(${doc.year}).` : "",
      withStop(strip(doc.title)),
      withStop(citationLine(doc)),
      doi ? `https://doi.org/${doi}` : "",
    ].filter(Boolean).join(" ");
  }

  async function loadCitations(results) {
    const identifiers = results.map((d) => d.identifier).filter((i) => i && !citationCache.has(i));
    if (!identifiers.length) return;
    try {
      const data = await fetchJSON(`${API_BASE}/api/citations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifiers }),
      });
      Object.entries(data.citations || {}).forEach(([id, cite]) => citationCache.set(id, cite));
      applyCitations();
    } catch (_) {
      // Best effort: rows keep the journal-only citation and guessed DOI.
    }
  }

  // Update citation text and DOI links in place, so open annotation panels
  // and scroll position survive the citations arriving after first render.
  function applyCitations() {
    const byId = new Map(lastResults.map((d) => [d.identifier, d]));
    els.resultsList.querySelectorAll("[data-identifier]").forEach((el) => {
      const doc = byId.get(el.dataset.identifier);
      if (!doc) return;
      el.querySelectorAll(".citation-line").forEach((c) => { c.textContent = citationLine(doc); });
      el.querySelectorAll(".doi-text").forEach((c) => { c.textContent = doiOf(doc) || "?"; });
      el.querySelectorAll(".fulltext-link").forEach((a) => {
        const doi = doiOf(doc);
        if (doi) a.href = `https://doi.org/${doi}`;
      });
    });
    saveViewerContext(sortedResults(lastResults)); // now with PubMed's DOIs
  }

  async function copyText(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    const label = btn.textContent;
    btn.textContent = "Copied";
    btn.disabled = true;
    setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 1200);
  }

  // ---------------------------------------------------------------------
  // Results
  // ---------------------------------------------------------------------

  function setViewMode(mode) {
    viewMode = mode;
    els.viewTable.classList.toggle("active", mode === "table");
    els.viewCards.classList.toggle("active", mode === "cards");
    els.viewTable.setAttribute("aria-pressed", String(mode === "table"));
    els.viewCards.setAttribute("aria-pressed", String(mode === "cards"));
    if (lastQueryBody && lastResults.length) renderResults(lastResults, lastQueryBody.keywords || "");
  }

  els.viewTable.addEventListener("click", () => setViewMode("table"));
  els.viewCards.addEventListener("click", () => setViewMode("cards"));

  function renderResults(results, keywords) {
    els.resultsSummary.hidden = false;
    els.resultsCount.innerHTML = `<strong>${results.length}</strong> result${results.length === 1 ? "" : "s"}`
      + (keywords ? ` for &ldquo;${escapeHtml(keywords)}&rdquo;` : "")
      + (keywords && lastScopeDesc ? ` ${escapeHtml(lastScopeDesc)}` : "")
      + (lastCategoryDesc ? ` with ${escapeHtml(lastCategoryDesc)}` : "");

    if (!results.length) {
      els.resultsList.innerHTML = `<div class="empty-state">No results. Try broadening your search or removing filters.</div>`;
      els.loadMoreRow.hidden = true;
      return;
    }

    els.resultsList.innerHTML = "";
    const ordered = sortedResults(results);
    saveViewerContext(ordered);
    if (viewMode === "table") renderTable(ordered, keywords);
    else renderCards(ordered, keywords);
    updateExpandButtons();

    els.loadMoreRow.hidden = results.length < (lastQueryBody.count || 50);
  }

  // ---------------------------------------------------------------------
  // Column sorting (client-side, over the results already loaded)
  // ---------------------------------------------------------------------

  // Access level from the backend (see _access_level() in backend/main.py):
  // "open" (gold/green/hybrid), "publisher" (bronze: free to read at the
  // publisher but no reuse licence, so not republished here), "restricted".
  const ACCESS = {
    open: { label: "Open access", rank: 2,
      tip: "Openly licensed: full matched sentences, and the annotated full text where available." },
    publisher: { label: "Free at publisher", rank: 1,
      tip: "Free to read on the publisher's site, but not licensed for reuse, so the full text isn't shown here." },
    restricted: { label: "Restricted", rank: 0,
      tip: "Not open access: only the top few matching sentences are shown." },
  };
  const accessLevel = (doc) => (ACCESS[doc.access] ? doc.access : (doc.open_access === false ? "restricted" : "open"));

  function accessBadge(doc) {
    const level = accessLevel(doc);
    const sources = [doc.license && `licence: ${doc.license.toUpperCase()}`,
                     doc.oa_status && `Unpaywall: ${doc.oa_status}`].filter(Boolean);
    const tip = ACCESS[level].tip + (sources.length ? ` (${sources.join("; ")})` : "");
    return `<span class="access-badge ${level}" title="${escapeHtml(tip)}">${ACCESS[level].label}</span>`;
  }

  // value: what to compare (null/"" sorts last in either direction).
  // firstDir: direction of the first click -- 1 ascending, -1 descending.
  const SORT_COLUMNS = [
    { key: "title", label: "Title / Citation", firstDir: 1,
      value: (d) => String(d.title || "").replace(/<\/?(?:i|sup|sub)>/g, "").trim().toLowerCase() },
    { key: "author", label: "Authors", firstDir: 1,
      value: (d) => String(d.author || "").trim().toLowerCase() },
    { key: "year", label: "Year", firstDir: -1,
      value: (d) => { const y = parseInt(d.year, 10); return Number.isNaN(y) ? null : y; } },
    { key: "score", label: "Score", firstDir: -1,
      value: (d) => (typeof d.score === "number" ? d.score : null) },
    { key: "access", label: "Access", firstDir: -1,
      value: (d) => ACCESS[accessLevel(d)].rank },
  ];
  const sortColumn = (key) => SORT_COLUMNS.find((c) => c.key === key);

  function sortedResults(results) {
    const col = tableSort && sortColumn(tableSort.key);
    if (!col) return results;
    const dir = tableSort.dir;
    const missing = (v) => v === null || v === undefined || v === "";
    // Array.prototype.sort is stable, so ties keep the server's order.
    return results.slice().sort((a, b) => {
      const va = col.value(a);
      const vb = col.value(b);
      if (missing(va) || missing(vb)) return missing(va) - missing(vb);
      if (typeof va === "string") return dir * va.localeCompare(vb);
      return dir * (va - vb);
    });
  }

  // Click cycle per column: first direction -> reversed -> back to server order.
  function cycleSort(key) {
    const col = sortColumn(key);
    if (!tableSort || tableSort.key !== key) tableSort = { key, dir: col.firstDir };
    else if (tableSort.dir === col.firstDir) tableSort = { key, dir: -col.firstDir };
    else tableSort = null;
    renderResults(lastResults, lastQueryBody ? lastQueryBody.keywords || "" : "");
  }

  function sortHeader(key, className) {
    const col = sortColumn(key);
    const active = tableSort && tableSort.key === key;
    const arrow = !active ? "&#8693;" : tableSort.dir === 1 ? "&#9650;" : "&#9660;";
    const ariaSort = !active ? "none" : tableSort.dir === 1 ? "ascending" : "descending";
    return `<th class="${className}" aria-sort="${ariaSort}">
      <button type="button" class="sort-btn${active ? " active" : ""}" data-sort="${key}"
        title="Sort by ${escapeHtml(col.label.toLowerCase())}">${escapeHtml(col.label)}<span class="sort-arrow">${arrow}</span></button>
    </th>`;
  }

  function renderTable(results, keywords) {
    const wrap = document.createElement("div");
    wrap.className = "results-table-wrap";
    wrap.innerHTML = `
      <table class="results-table">
        <thead>
          <tr>
            ${sortHeader("title", "col-title")}
            ${sortHeader("author", "col-authors")}
            ${sortHeader("year", "col-year")}
            ${sortHeader("score", "col-score")}
            ${sortHeader("access", "col-access")}
            <th class="col-actions">Actions</th>
          </tr>
        </thead>
      </table>`;
    wrap.querySelectorAll(".sort-btn").forEach((btn) => {
      btn.addEventListener("click", () => cycleSort(btn.dataset.sort));
    });
    const table = wrap.querySelector("table");

    // One <tbody> per result, holding its main row and its detail row, so
    // the pair hovers and borders as a unit.
    results.forEach((doc) => {
      const group = document.createElement("tbody");
      group.className = "result-group";
      const tr = document.createElement("tr");
      tr.dataset.identifier = doc.identifier;
      const doi = doiOf(doc);
      const score = typeof doc.score === "number" ? doc.score.toFixed(1) : escapeHtml(doc.score ?? "");
      tr.innerHTML = `
        <td class="col-title">
          <div class="table-title">${titleHtml(doc)}</div>
          <div class="citation-line">${escapeHtml(citationLine(doc))}</div>
        </td>
        <td class="col-authors"><div class="table-authors" title="${escapeHtml(doc.author || "")}">${escapeHtml(doc.author || "?")}</div></td>
        <td class="col-year">${escapeHtml(doc.year || "?")}</td>
        <td class="col-score">${score}</td>
        <td class="col-access">${accessBadge(doc)}</td>
        <td class="col-actions">
          <div class="row-actions">
            ${viewerLink(doc)}
            ${publisherLink(doi)}
            <button type="button" class="action-btn copy-citation">Copy citation</button>
            ${doi ? `<button type="button" class="action-btn copy-doi">Copy DOI</button>` : ""}
          </div>
        </td>`;
      tr.querySelector(".copy-citation").addEventListener("click", (e) => copyText(plainCitation(doc), e.currentTarget));
      const copyDoiBtn = tr.querySelector(".copy-doi");
      if (copyDoiBtn) copyDoiBtn.addEventListener("click", (e) => copyText(doiOf(doc), e.currentTarget));

      const detailRow = document.createElement("tr");
      detailRow.className = "detail-row";
      const detailCell = document.createElement("td");
      detailCell.colSpan = 6;
      detailCell.appendChild(buildDetails(doc, keywords));
      detailRow.appendChild(detailCell);

      group.append(tr, detailRow);
      table.appendChild(group);
    });

    els.resultsList.appendChild(wrap);
  }

  function renderCards(results, keywords) {
    results.forEach((doc) => {
      const card = document.createElement("div");
      card.className = "result-card";
      card.dataset.identifier = doc.identifier;

      card.innerHTML = `
        <div class="result-head">
          <div>
            <div class="result-title">${titleHtml(doc)}</div>
            <div class="result-meta">${escapeHtml(doc.author || "?")} &middot; ${escapeHtml(doc.year || "?")}
              &middot; <em class="citation-line">${escapeHtml(citationLine(doc) || "?")}</em>
              &middot; DOI: <span class="doi-text">${escapeHtml(doiOf(doc) || "?")}</span></div>
          </div>
          <div class="badge-col">
            <span class="corpus-badge">${escapeHtml(corpusOf(doc.identifier))}</span>
            ${accessBadge(doc)}
            ${viewerLink(doc)}
            ${publisherLink(doiOf(doc))}
          </div>
        </div>`;
      card.appendChild(buildDetails(doc, keywords));
      els.resultsList.appendChild(card);
    });
  }

  // ---------------------------------------------------------------------
  // Detail sections: Abstract / Top sentences / Ontology annotations
  // ---------------------------------------------------------------------
  //
  // Shared by the table (in a full-width row under each result) and the
  // cards. Open/closed state lives in sectionState, keyed by identifier, so
  // it survives re-sorting and switching views; a section nobody has toggled
  // falls back to sectionDefault().

  const DETAIL_SECTIONS = ["abstract", "sentences", "annotations"];
  const sectionState = new Map(); // identifier -> { abstract?, sentences?, annotations? }

  // Everything starts collapsed, except the cards keep showing their top
  // sentences up front as they always have.
  function sectionDefault(key) {
    return viewMode === "cards" && key === "sentences";
  }

  function sectionAvailable(doc, key) {
    if (key === "abstract") return Boolean(doc.abstract);
    if (key === "sentences") return Boolean((doc.matched_sentences || []).length);
    return true; // annotations: only known once fetched
  }

  function isSectionOpen(doc, key) {
    if (!sectionAvailable(doc, key)) return false;
    const s = sectionState.get(doc.identifier);
    return s && key in s ? s[key] : sectionDefault(key);
  }

  function sectionLabel(doc, key) {
    if (key === "abstract") return doc.abstract ? "Abstract" : "Abstract (not available)";
    if (key === "annotations") return "Ontology annotations";
    const shown = (doc.matched_sentences || []).length;
    const total = doc.matched_sentence_count ?? shown;
    if (!shown) return "Top sentences (none)";
    return `Top sentences (${shown}${total > shown ? ` of ${total}` : ""})`;
  }

  function buildDetails(doc, keywords) {
    const wrap = document.createElement("div");
    wrap.className = "detail-sections";
    wrap.dataset.detailsFor = doc.identifier;

    const toggles = DETAIL_SECTIONS.map((key) => {
      const available = sectionAvailable(doc, key);
      return `<button type="button" class="detail-toggle" data-section="${key}" aria-expanded="false"${available ? "" : " disabled"}>
          <span class="caret">&#9656;</span> ${escapeHtml(sectionLabel(doc, key))}
        </button>`;
    }).join("");
    wrap.innerHTML = `
      <div class="detail-toggles">${toggles}</div>
      <div class="detail-panel abstract-panel" data-section="abstract" hidden></div>
      <div class="detail-panel sentences-panel" data-section="sentences" hidden></div>
      <div class="detail-panel annotate-panel" data-section="annotations" hidden></div>`;

    // Abstract and sentences are already in the search response; fill them now.
    // The abstract's own markup (e.g. <i>) is dropped so keyword highlighting
    // can work on plain text.
    if (doc.abstract) {
      const abstractText = String(doc.abstract).replace(/<\/?[a-z][^>]*>/gi, "");
      wrap.querySelector(".abstract-panel").innerHTML = highlightTerms(abstractText, keywords);
    }
    const sentences = doc.matched_sentences || [];
    if (sentences.length) {
      const total = doc.matched_sentence_count ?? sentences.length;
      const restrictedNote = (accessLevel(doc) === "restricted" && total > sentences.length)
        ? `<div class="restricted-note">Showing the top ${sentences.length} of ${total} matching
             sentences. Full text is available for open-access papers (sign-in for others is not yet supported).</div>`
        : "";
      wrap.querySelector(".sentences-panel").innerHTML =
        sentences.map((s) => `<div class="sentence">${highlightTerms(s.trim(), keywords)}</div>`).join("") + restrictedNote;
    }

    wrap.querySelectorAll(".detail-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.section;
        setSectionOpen(doc, key, !isSectionOpen(doc, key));
        applySection(wrap, doc, key);
        updateExpandButtons();
      });
    });
    DETAIL_SECTIONS.forEach((key) => applySection(wrap, doc, key));
    return wrap;
  }

  function setSectionOpen(doc, key, open) {
    const s = sectionState.get(doc.identifier) || {};
    s[key] = open;
    sectionState.set(doc.identifier, s);
  }

  // Sync one section's toggle and panel in the DOM with isSectionOpen().
  function applySection(wrap, doc, key) {
    const open = isSectionOpen(doc, key);
    const btn = wrap.querySelector(`.detail-toggle[data-section="${key}"]`);
    const panel = wrap.querySelector(`.detail-panel[data-section="${key}"]`);
    btn.setAttribute("aria-expanded", String(open));
    btn.querySelector(".caret").classList.toggle("open", open);
    panel.hidden = !open;
    if (open && key === "annotations" && !panel.dataset.loaded) {
      panel.dataset.loaded = "1";
      loadAnnotations(doc, panel);
    }
  }

  // ---- "Expand all" controls --------------------------------------------

  function allExpanded(key) {
    const docs = lastResults.filter((d) => sectionAvailable(d, key));
    return docs.length > 0 && docs.every((d) => isSectionOpen(d, key));
  }

  function updateExpandButtons() {
    els.expandControls.querySelectorAll("[data-expand]").forEach((btn) => {
      const key = btn.dataset.expand;
      const on = allExpanded(key);
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", String(on));
      btn.disabled = !lastResults.some((d) => sectionAvailable(d, key));
    });
  }

  els.expandControls.querySelectorAll("[data-expand]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.expand;
      const open = !allExpanded(key);
      lastResults.forEach((doc) => setSectionOpen(doc, key, open));
      const byId = new Map(lastResults.map((d) => [d.identifier, d]));
      els.resultsList.querySelectorAll(".detail-sections").forEach((wrap) => {
        const doc = byId.get(wrap.dataset.detailsFor);
        if (doc) applySection(wrap, doc, key);
      });
      updateExpandButtons();
    });
  });

  // ---- Ontology annotations (lazy, shared cache) ------------------------

  // "Expand all" on annotations asks for every result at once; the
  // annotation lookup is slow and has been flaky, so run a few at a time.
  const ANNOTATE_CONCURRENCY = 4;
  let annotateActive = 0;
  const annotateQueue = [];

  function withAnnotateSlot(fn) {
    return new Promise((resolve) => {
      const run = () => {
        annotateActive++;
        fn().then(resolve).finally(() => {
          annotateActive--;
          const next = annotateQueue.shift();
          if (next) next();
        });
      };
      if (annotateActive < ANNOTATE_CONCURRENCY) run();
      else annotateQueue.push(run);
    });
  }

  const ANNOTATE_TIMEOUT_MS = 60000;

  // Resolves to { summary } or { error, retryable }. Cached as a promise, so a
  // panel re-rendered while its request is still in flight (sort, view
  // switch) waits on the same request instead of starting another. Only a
  // definite answer stays cached: a timeout or network failure is dropped
  // from the cache, so collapsing and reopening the panel tries again.
  function getAnnotations(identifier) {
    const excludeSections = (lastQueryBody && lastQueryBody.exclude_sections) || [];
    const cacheKey = `${identifier}|${excludeSections.join(",")}`;
    if (!annotateCache.has(cacheKey)) {
      annotateCache.set(cacheKey, withAnnotateSlot(async () => {
        try {
          // Always ask for RELATED-synonym hits here: the panel shows them as their
          // own MAIZE_GENES_RELATED row, so there's no ambiguity with EXACT hits.
          const params = new URLSearchParams({ identifier, related_synonyms: "true" });
          excludeSections.forEach((s) => params.append("exclude_sections", s));
          const data = await fetchJSON(`${API_BASE}/api/annotate?${params}`, undefined, ANNOTATE_TIMEOUT_MS);
          return { summary: data.ontology_summary };
        } catch (e) {
          if (e.status === 404 || e.status === 502) {
            return { error: "Ontology annotations are unavailable for this paper right now (the Textpresso annotation service is currently down)." };
          }
          annotateCache.delete(cacheKey);
          return { error: `Could not load ontology annotations (${e.message}). Collapse and reopen to try again.`, retryable: true };
        }
      }));
    }
    return annotateCache.get(cacheKey);
  }

  async function loadAnnotations(doc, panel) {
    panel.innerHTML = `<div class="annotate-unavailable">Loading&hellip;</div>`;
    const result = await getAnnotations(doc.identifier);
    if (result.error) {
      panel.innerHTML = `<div class="annotate-unavailable">${escapeHtml(result.error)}</div>`;
      if (result.retryable) delete panel.dataset.loaded; // reopening fetches again
    } else {
      renderAnnotatePanel(panel, result.summary);
    }
  }

  function renderAnnotatePanel(panel, summary) {
    if (!summary || !Object.keys(summary).length) {
      panel.innerHTML = `<div class="annotate-unavailable">No ontology annotations available for this paper.</div>`;
      return;
    }
    const colors = { GO: "#1565c0", PO: "#6a3fb5", TO: "#b5541f", MAIZE_GENES: "#2e7d32", MAIZE_GENES_RELATED: "#689f38" };
    panel.innerHTML = Object.entries(summary).map(([onto, terms]) => `
      <div class="annotate-row">
        <span class="annotate-onto" style="color:${colors[onto] || "#5f6368"}">${escapeHtml(onto)}</span>
        <div class="annotate-terms">${terms.map((t) => `<span class="annotate-term">${escapeHtml(t)}</span>`).join("")}</div>
      </div>
    `).join("");
  }

  els.loadMoreBtn.addEventListener("click", () => {
    const nextCount = Math.min((lastQueryBody.count || 50) + 25, 200);
    runSearch(nextCount);
  });

  // ---------------------------------------------------------------------
  // Export
  // ---------------------------------------------------------------------

  function download(filename, content, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  els.exportJson.addEventListener("click", () => {
    if (!lastResults.length) return;
    download("textpresso_results.json", JSON.stringify(sortedResults(lastResults), null, 2), "application/json");
  });

  els.exportCsv.addEventListener("click", () => {
    if (!lastResults.length) return;
    const cols = ["title", "author", "year", "journal", "score", "access", "oa_status", "license", "accession", "identifier"];
    const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = [cols.join(",")].concat(
      sortedResults(lastResults).map((d) => cols.map((c) => esc(c === "access" ? accessLevel(d) : d[c])).join(","))
    );
    download("textpresso_results.csv", rows.join("\n"), "text/csv");
  });

  // ---------------------------------------------------------------------

  loadCorpora();
})();
