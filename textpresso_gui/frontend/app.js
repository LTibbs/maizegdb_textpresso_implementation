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
    sortBy: $("sortBy"),
    categoryQuery: $("categoryQuery"),
    categoryLookupBtn: $("categoryLookupBtn"),
    categoryResults: $("categoryResults"),
    categorySelected: $("categorySelected"),
    categoryNote: $("categoryNote"),
    expandAncestors: $("expandAncestors"),
    expandDescendants: $("expandDescendants"),
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
  };

  let selectedCategoryMatch = null; // full match object from /api/category_search
  let lastResults = [];             // raw docs from the last successful search
  let lastQueryBody = null;         // the request body used, for "Load more"
  const annotateCache = new Map();  // identifier -> ontology_summary | "unavailable"

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Titles from the API sometimes carry a deliberate <i>species name</i>
  // convention (see docs/TPC_SEARCH_TUTORIAL.md). Escape everything, then
  // un-escape only exact <i>/</i> tags, rather than trusting raw HTML from
  // an external response.
  function sanitizeTitle(s) {
    return escapeHtml(s).replace(/&lt;(\/?i)&gt;/g, "<$1>");
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

  async function fetchJSON(url, options) {
    const res = await fetch(url, options);
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
      allLabel.innerHTML = `<input type="checkbox" id="corpusAll" checked> All corpora (${corpora.length})`;
      els.corporaList.appendChild(allLabel);

      corpora.forEach((name) => {
        const label = document.createElement("label");
        label.innerHTML = `<input type="checkbox" class="corpus-item" value="${escapeHtml(name)}" checked disabled> ${escapeHtml(name)}`;
        els.corporaList.appendChild(label);
      });

      const allBox = $("corpusAll");
      allBox.addEventListener("change", () => {
        document.querySelectorAll(".corpus-item").forEach((cb) => {
          cb.disabled = allBox.checked;
        });
      });
    } catch (e) {
      els.corporaList.textContent = "Could not load corpus list.";
      showStatus(`Could not reach the Textpresso backend at ${API_BASE} (${e.message}). Is it running? See GUI_SETUP.md.`);
    }
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
    a.addEventListener("click", (e) => {
      e.preventDefault();
      els.keywords.value = a.dataset.q;
      runSearch();
    });
  });

  // ---------------------------------------------------------------------
  // Category / gene lookup
  // ---------------------------------------------------------------------

  async function doCategoryLookup() {
    const q = els.categoryQuery.value.trim();
    els.categoryNote.hidden = true;
    if (!q) return;
    els.categoryResults.hidden = false;
    els.categoryResults.textContent = "Looking up…";
    try {
      const data = await fetchJSON(`${API_BASE}/api/category_search?${new URLSearchParams({ q, limit: 8 })}`);
      renderCategoryMatches(data.matches || []);
    } catch (e) {
      els.categoryResults.innerHTML = "";
      els.categoryNote.hidden = false;
      els.categoryNote.textContent = e.status === 502
        ? "Category lookup is temporarily unavailable (the Textpresso annotation service is down)."
        : `Category lookup failed: ${e.message}`;
    }
  }

  function renderCategoryMatches(matches) {
    if (!matches.length) {
      els.categoryResults.innerHTML = `<div class="category-match">No matches found.</div>`;
      return;
    }
    els.categoryResults.innerHTML = "";
    matches.forEach((m) => {
      const row = document.createElement("div");
      row.className = "category-match";
      row.innerHTML = `<span>${escapeHtml(m.category)} <span class="meta">&mdash; ${escapeHtml(m.matched_on)}, ${escapeHtml(m.ontology)}</span></span>`;
      row.addEventListener("click", () => selectCategory(m));
      els.categoryResults.appendChild(row);
    });
  }

  function selectCategory(match) {
    selectedCategoryMatch = match;
    els.categoryResults.hidden = true;
    els.categorySelected.hidden = false;
    els.categorySelected.innerHTML = `<span>${escapeHtml(match.category)}</span> <button type="button" aria-label="Remove">&times;</button>`;
    els.categorySelected.querySelector("button").addEventListener("click", () => {
      selectedCategoryMatch = null;
      els.categorySelected.hidden = true;
    });
  }

  els.categoryLookupBtn.addEventListener("click", doCategoryLookup);
  els.categoryQuery.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); doCategoryLookup(); }
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
      sort_by_year: els.sortBy.value === "year",
      exclude_sections: excludeSections,
      related_synonyms: els.relatedSynonyms.checked,
    };

    if (selectedCategoryMatch) {
      body.category = [selectedCategoryMatch.category];
      body.expand_ancestor_relationship_types = els.expandAncestors.checked
        ? (selectedCategoryMatch.parent_relationship_types || []) : [];
      body.expand_relationship_types = els.expandDescendants.checked
        ? (selectedCategoryMatch.relationship_types || []) : [];
    }

    return body;
  }

  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch();
  });

  els.resetBtn.addEventListener("click", () => {
    els.form.reset();
    selectedCategoryMatch = null;
    els.categorySelected.hidden = true;
    els.categoryResults.hidden = true;
    const allBox = $("corpusAll");
    if (allBox) {
      allBox.checked = true;
      document.querySelectorAll(".corpus-item").forEach((cb) => { cb.checked = true; cb.disabled = true; });
    }
  });

  async function runSearch(count) {
    if (!els.keywords.value.trim() && !els.author.value.trim() && !els.journal.value.trim()
        && !els.year.value.trim() && !selectedCategoryMatch) {
      showStatus("Enter a keyword, or set an author/journal/year/category filter, then search.");
      return;
    }
    showStatus(null);
    const body = buildRequestBody(count || 50);
    lastQueryBody = body;
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

  function renderResults(results, keywords) {
    els.resultsSummary.hidden = false;
    els.resultsCount.innerHTML = `<strong>${results.length}</strong> result${results.length === 1 ? "" : "s"}`
      + (keywords ? ` for &ldquo;${escapeHtml(keywords)}&rdquo;` : "");

    if (!results.length) {
      els.resultsList.innerHTML = `<div class="empty-state">No results. Try broadening your search or removing filters.</div>`;
      els.loadMoreRow.hidden = true;
      return;
    }

    els.resultsList.innerHTML = "";
    results.forEach((doc, idx) => {
      const card = document.createElement("div");
      card.className = "result-card";

      const matchedSentences = doc.matched_sentences || [];
      const snippets = matchedSentences.slice(0, 5).map(
        (s) => `<div>${highlightTerms(s.trim(), keywords)}</div>`
      ).join("");
      const isOpenAccess = doc.open_access !== false;
      const totalMatches = doc.matched_sentence_count ?? matchedSentences.length;
      const restrictedNote = (!isOpenAccess && totalMatches > matchedSentences.length)
        ? `<div class="restricted-note">Showing the top ${matchedSentences.length} of ${totalMatches} matching
             sentences. Full text is available for open-access papers (sign-in for others is not yet supported).</div>`
        : "";

      card.innerHTML = `
        <div class="result-head">
          <div>
            <div class="result-title">${sanitizeTitle(doc.title || "Untitled")}</div>
            <div class="result-meta">${escapeHtml(doc.author || "?")} &middot; ${escapeHtml(doc.year || "?")}
              &middot; <em>${escapeHtml(doc.journal || "?")}</em>
              &middot; DOI: ${escapeHtml(doc.accession || "?")}</div>
          </div>
          <div class="badge-col">
            <span class="corpus-badge">${escapeHtml(corpusOf(doc.identifier))}</span>
            <span class="access-badge ${isOpenAccess ? "oa" : "restricted"}">${isOpenAccess ? "Open access" : "Restricted"}</span>
          </div>
        </div>
        ${snippets ? `<div class="result-snippets">${snippets}</div>` : ""}
        ${restrictedNote}
        <button type="button" class="annotate-toggle" data-idx="${idx}">
          <span class="caret">&#9656;</span> Ontology annotations
        </button>
        <div class="annotate-panel" hidden></div>
      `;

      const toggleBtn = card.querySelector(".annotate-toggle");
      const panel = card.querySelector(".annotate-panel");
      const caret = toggleBtn.querySelector(".caret");
      toggleBtn.addEventListener("click", async () => {
        const opening = panel.hidden;
        panel.hidden = !opening;
        caret.classList.toggle("open", opening);
        if (opening && !panel.dataset.loaded) {
          panel.dataset.loaded = "1";
          await loadAnnotations(doc, panel);
        }
      });

      els.resultsList.appendChild(card);
    });

    els.loadMoreRow.hidden = results.length < (lastQueryBody.count || 50);
  }

  async function loadAnnotations(doc, panel) {
    const identifier = doc.identifier;
    panel.innerHTML = `<div class="annotate-unavailable">Loading&hellip;</div>`;

    if (annotateCache.has(identifier)) {
      renderAnnotatePanel(panel, annotateCache.get(identifier));
      return;
    }
    try {
      const params = new URLSearchParams({ identifier });
      const excludeSections = lastQueryBody ? lastQueryBody.exclude_sections : [];
      (excludeSections || []).forEach((s) => params.append("exclude_sections", s));
      const data = await fetchJSON(`${API_BASE}/api/annotate?${params}`);
      annotateCache.set(identifier, data.ontology_summary);
      renderAnnotatePanel(panel, data.ontology_summary);
    } catch (e) {
      const msg = e.status === 404 || e.status === 502
        ? "Ontology annotations are unavailable for this paper right now (the Textpresso annotation service is currently down)."
        : `Could not load ontology annotations: ${e.message}`;
      annotateCache.set(identifier, "unavailable");
      panel.innerHTML = `<div class="annotate-unavailable">${escapeHtml(msg)}</div>`;
    }
  }

  function renderAnnotatePanel(panel, summary) {
    if (summary === "unavailable" || !summary || !Object.keys(summary).length) {
      panel.innerHTML = `<div class="annotate-unavailable">No ontology annotations available for this paper.</div>`;
      return;
    }
    const colors = { GO: "#1565c0", PO: "#6a3fb5", TO: "#b5541f", MAIZE_GENES: "#2e7d32" };
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
    download("textpresso_results.json", JSON.stringify(lastResults, null, 2), "application/json");
  });

  els.exportCsv.addEventListener("click", () => {
    if (!lastResults.length) return;
    const cols = ["title", "author", "year", "journal", "accession", "identifier"];
    const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = [cols.join(",")].concat(
      lastResults.map((d) => cols.map((c) => esc(d[c])).join(","))
    );
    download("textpresso_results.csv", rows.join("\n"), "text/csv");
  });

  // ---------------------------------------------------------------------

  loadCorpora();
})();
