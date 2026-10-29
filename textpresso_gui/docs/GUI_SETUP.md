# Textpresso Maize GUI — local setup

## Backend

```bash
pip install -r textpresso_gui/backend/requirements.txt
uvicorn textpresso_gui.backend.main:app --reload --port 8010
```

(Port 8000 is sometimes already in use on a dev laptop — pick any free port
and point the frontend at it.)

Endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/corpora` | GET | List searchable corpora |
| `/api/search` | POST | Keyword/metadata/category search (JSON body, see `SearchRequest` in `backend/main.py`) |
| `/api/category_search` | GET | Look up exact `category` strings by free-text term |
| `/api/annotate` | GET | Grouped ontology annotation summary for one document |
| `/api/document` | GET | Whole paper for the full-text viewer: text plus page/paragraph/figure/section/sentence/annotation offsets (`?identifier=`) |
| `/api/figure` | GET | One figure image for the viewer (`?identifier=&name=<basename>`) |
| `/api/health` | GET | Liveness check |

## Frontend

Plain HTML/CSS/JS, no build step. Serve it as static files (opening
`index.html` directly via `file://` can hit browser fetch/CORS quirks, so a
tiny static server is more reliable):

```bash
cd textpresso_gui/frontend
python -m http.server 5500
```

Then open `http://127.0.0.1:5500/index.html`. It calls the backend at
`http://127.0.0.1:8010` by default (`app.js`'s `API_BASE`) — if you ran the
backend on a different port, either change that constant or set
`window.TEXTPRESSO_API_BASE` before `app.js` loads (e.g. a small inline
`<script>` in `index.html`).

Working today: keyword/section-scoped search, corpus selection, author/
journal/year/paper-type filters, exclude-keywords, sort by year, CSV/JSON
export, ontology-annotation display, gene EXACT/RELATED filtering, and
references/acknowledgments exclusion. Only `/api/category_search` (category/
gene lookup) is degraded (fails gracefully with an inline message rather
than breaking) until the known issue below is fixed upstream.

## Full-text viewer (`viewer.html`)

The new GUI's counterpart to Textpresso Central's `/tpc/curation` Viewer.
Each search result that can be opened gets an **Annotated full text** action,
and its title becomes a link too. Either one opens
`viewer.html?id=<identifier>&q=…` in a new tab. The separate
**Publisher full text ↗** action goes to the DOI.

The viewer is laid out as two panes that scroll independently: controls on
the left, the paper on the right. The controls therefore stay in view
however far each "next" jumps. Below 860px wide, the controls become a
scrollable strip above the paper.

The four control panels (Keyword matches, Category matches, Annotations,
Contents) start collapsed to a one-line header with a short summary, such
as "81 terms" or "5 sections · 5 figures". Click a header to expand it.
Each panel's open/closed state is remembered in the browser. The steppers
(‹ ›) stay visible even when their panel is collapsed. The viewer provides:

- **The full paper** with page dividers, paragraphs, and figures inline at
  the point where they appear in the PDF. Click a figure to open it full
  size.
- **Keyword matches and category matches, stepped separately.** Each has
  its own ‹ › and count, so stepping through one never jumps into the
  other.
  - Keyword matches are yellow, and the current one is orange. Use `[` and
    `]` to step.
  - Category matches are teal, and the current one is outlined. Use `{` and
    `}` to step.
  - The current match's sentence is tinted, but only when it's a normal
    length (≤ 500 characters). Some papers' CAS "sentences" span whole
    paragraphs.
  - Matches inside sections the search excluded (references/acknowledgments
    by default) are shown in grey and skipped.
  - **Keywords:** the box starts with the search's keywords. Type a new
    term to change it.
  - **Categories:** "Find categories in this paper" is a type-ahead over the
    terms annotated in this paper, searching by name or by GO/PO/TO ID. Each
    chosen category becomes a chip showing its count, and removing the chip
    removes it. The search page's categories start out as chips. Matching
    uses the ontology ID, so different spellings of the same term in the CAS
    still match.
  - **Include broader / narrower terms** (GO/PO/TO chips only) also matches
    the term's ancestors and/or descendants. They're looked up through
    `/api/category_search` using the term's own relationship types, the
    same way the search page expands categories. The checkboxes start from
    the search page's setting (`anc=1` / `desc=1`).
  - A chip shows how many related terms contributed, as "+N terms". Its
    tooltip lists them.
  - The whole set of related terms is looked up (up to 5,000 per direction;
    "organelle" alone has 420 narrower terms), and the panel notes if a list
    was still cut off.
  - **Related gene synonyms** count only if the search had "include related
    gene synonyms" on (passed to the viewer as `rel=1`).
- **Annotations.** Highlighting can be switched on or off for each term:
  - Each ontology (genes, GO, PO, TO) has a checkbox for all its terms. It
    shows as partly checked when only some are on.
  - The term list has a checkbox per term and a filter box for long lists.
    Its Select all / Deselect all links act on the terms the filter is
    showing.
  - Select all / Deselect all in the panel header covers every ontology.
  - Each ontology's on/off setting is remembered between papers. Choices for
    individual terms last only for the current page.
  - Clicking a term's name highlights every occurrence, and ‹ › then steps
    through them.
  - Clicking any annotated word shows its categories.
- **Contents.** Jump to a section or figure. Sections come from the CAS2
  section labels after `casannot.repair_sections()` removes the tokenizer's
  false headings. These include a wrapped "(see Materials / and methods)" or
  a table column header "Reference". The fixes also add letter-spaced
  headings the tokenizer missed ("AC K N OW L E D G M E N T S"), and end an
  abstract that ran on for pages at the next page break. See the 2026-10-14
  work-log entry and its addendum.
- **Previous/next paper**, following the result list the search page last
  showed (saved in `localStorage`).

### Where the data comes from

The viewer reads only the local CAS2 file (`CAS_ROOT`), parsed by
`textpresso_classifiers/casdoc.py`. The live API has nothing equivalent:
`search_documents`' full text has its tags stripped and carries no offsets.
So without a local CAS2 tree there is no viewer: `/api/document` returns
503, and the search page hides the link. The link is also hidden for papers
whose corpus isn't in the local tree (on the dev box that means everything
except MaizeOpenAccess).

`casdoc.py` handles both CAS layouts:

- **`tai`** (MaizeOpenAccess): structure is stored in separate page, dblbrk and
  image elements.
- **`pdf`** (older corpora): structure is stored as inline `<_pdf …/>` tags,
  which are stripped and all offsets remapped. This is covered by
  `tests/test_casdoc.py`.

### Figures

`/api/figure` tries these locations in order:

1. `CAS_ROOT/<corpus>/<acc>/images/`
2. `TPCAS1_ROOT/<corpus>/<acc>/images/` (default: `tpcas-1` next to
   `CAS_ROOT`; override with `TEXTPRESSO_TPCAS1_ROOT`)
3. `RAW_PDF_ROOT/<corpus>/<acc>/` (default: `raw_files/pdf` next to
   `CAS_ROOT`; override with `TEXTPRESSO_RAW_PDF_ROOT`)
4. Otherwise it returns a 307 redirect to the live
   `https://textpresso.maizegdb.org/tpc/images/<corpus>/<acc>/images/<name>`.

On the dev box, steps 1–2 fail: the tpcas-2 `images` symlink can't be
followed over the WSL UNC mount, and the tpcas-1 `images/` directories are
empty. Figures are therefore served from `raw_files`.

Images in `raw_files` are taken from the PDF before the pipeline's
`invert_img` step (`agr_textpresso/tpctools/CMYKInverter`), so CMYK JPEGs
in it look like colour negatives. `/api/figure` applies the same fix the
pipeline does for that source only: it negates CMYK JPEGs and returns
them as RGB. This needs Pillow, which is in `requirements.txt`; without
Pillow the file is served as-is.

### Restricted papers

Both endpoints, and the `viewer_allowed` flag on each search result, go
through `_can_view_full_text()` in `backend/main.py`. Today that allows
open-access papers only, and restricted papers get 403 and no link.

Access comes from `_access_level()`: Unpaywall `oa_status` gold/green/hybrid
or any Creative Commons Europe PMC licence is `open`, bronze without one is
`publisher` (no viewer, full set of matched sentences), and everything else is
`restricted` (no viewer, 3 matched sentences). The statuses are read from
`sorghumbase_textpresso_implementation/metadata/MaizeOpenAccess_access.csv`
(columns `doi`, `oa_status`, `epmc_license`; all 3,299 MaizeOpenAccess papers),
which works with no setup. `TEXTPRESSO_GUI_OA_MANIFEST` points it at another
CSV with those columns, such as the pipeline's full `download_manifest.csv`.
Do not confuse it with `TEXTPRESSO_OA_MANIFEST`, which the API sidecars read
as a different, TSV-format file. If the file cannot be read, the backend logs
a warning and treats every paper as restricted.

When GUI login exists, add the logged-in user check to
`_can_view_full_text()`. Nothing else needs to change.

## Known issue: annotation sidecar service is unreachable over HTTP — worked around locally

As of 2026-09-23, `https://textpresso.maizegdb.org/v1/textpresso/annotate`
and `.../category_search` return `403 Forbidden` (Cloudflare WAF block,
`error code: 1010`) for a non-browser `User-Agent`, and `503 Service
Unavailable` (Cloudflare's own generic error page, not an app error) for a
browser-like one — unusable by any client we've tried, confirmed with raw
`curl -v`. The main search API (`search_documents`, `available_corpora`) is
unaffected. Full repro and a forwardable report:
[`docs/BUG_REPORT_textpresso_api_blocking.md`](../../docs/BUG_REPORT_textpresso_api_blocking.md)
(repo root `docs/`, not this directory).

This is a live infrastructure issue on the Textpresso host, not a bug in
this GUI or its backend, and it's still unresolved upstream. But `/annotate`
(unlike `/category_search`) has a local-file equivalent — see below — so
in practice this backend no longer depends on that endpoint at all.

### Working around it: CAS2 files are parsed locally instead of over HTTP

`backend/main.py` defaults `CAS_ROOT` to a WSL UNC mount of the
`agr_textpresso` data volume:

```
//wsl.localhost/Ubuntu/home/ltibbs/agr_textpresso/.data/tpcas-2
```

and parses `.tpcas.gz` files directly from there via `casannot.py`'s
`parse_cas_file()` (the same code `tpc_search_combined.py --cas-root` uses)
instead of ever calling `/annotate`. This is confirmed working end-to-end
against the real tree — e.g. it correctly identifies matches inside
citation titles as `references`/`acknowledgments` and drops them, which the
live sidecar can no longer be relied on to do. It fixes both the outage
above and the latency (no network round trip) for:
- ontology-annotation display (the per-card "Ontology annotations" panel)
- `exclude_sections` filtering (including dropping a doc entirely when
  every match it had turns out to be inside an excluded section — see
  `post_search`'s filter step)
- gene EXACT/RELATED filtering

Override with `TEXTPRESSO_CAS_ROOT` if running this backend somewhere else
(e.g. directly on the Linux box, where the path is
`~/agr_textpresso/.data/tpcas-2` with no WSL mount needed):

```bash
export TEXTPRESSO_CAS_ROOT=/path/to/tpcas-2
uvicorn textpresso_gui.backend.main:app --reload --port 8010
```

If neither the default WSL path nor an overridden path exists (WSL not
running, wrong machine), `CAS_ROOT` falls back to `None` and behavior
reverts to the flaky HTTP path described above, same as before this was
wired up — it doesn't crash, it just degrades.

### The same annotation filter in every view

The search results, the annotation panels, the full-text viewer and the API
sidecar all call `annotation_hygiene.clean_annotations`; the GUI backend reaches
it through `tpc.clean_local_annotations()`, so no view has filtering of its own.
`/api/document` takes `hygiene=false` (the viewer's "Show unfiltered" link under
the annotations panel) and reports what it removed.

For the local filter to match the API exactly it needs the same environment the
sidecar has (see `~/start_local_gui.sh` on the dev box):

```bash
export TPC_API_URL=http://localhost:8080/v1/textpresso/api   # a local container, not the public host
export TEXTPRESSO_MATCH_DICTIONARY=$DATA/annotation_build/match_dictionary.csv.gz   # tier gate
export TEXTPRESSO_ITALICS_INDEX=$DATA/italics/MaizeOpenAccess.json
export TEXTPRESSO_OBO_CLASS_MAP=$DATA/italics/obo_short_form_classes.json
export TEXTPRESSO_ENGLISH_GENE_FORMS=$DATA/italics/english_gene_forms.json           # english-word gene rule
export TEXTPRESSO_CATEGORY_INDEX=<agr_textpresso>/textpressoapi/category_index.py     # broader-term collapse and
export TEXTPRESSO_OBO_DIR=$DATA/obofiles4production                                  # the abbreviation case check
```

Each one that is missing switches its rule off silently, and the GUI then shows
more than the API does. Check by comparing `/api/document` with the sidecar's
`/annotate?related_synonyms=1` for the same paper: the annotation sets are
identical when the environment is complete (17 of 17 viewable papers checked
2026-10-23, 1,296 annotations on the first).

### Abstracts come from the corpus metadata CSVs

`search_documents` returns no abstract, so `/api/search` attaches one to
each result from the metadata CSVs the corpora were built from
(`sorghumbase_textpresso_implementation/metadata/*_papers.csv`), matched by
DOI in accession form (`/` → `_`) or PMID. They're read once on the first
search. Override the directory with `TEXTPRESSO_METADATA_DIR`. A paper with
no matching row (or an empty `abstract` cell) has its Abstract toggle
greyed out in the GUI. Abstracts are deliberately not fetched from PubMed,
so what's shown is exactly what was ingested.

**It does not help `/category_search`** (no local-file equivalent — that
endpoint looks up OBO ontology term IDs, a different dataset from per-paper
CAS2 annotations) or the core search itself (`search_documents`, a
separate Lucene index, not CAS2 files) — those still depend on the live
API being reachable.

## Known issue: default urllib User-Agent is blocked (fixed here)

The live host (Cloudflare-fronted) currently returns `403 Forbidden` for
requests carrying Python's default `Python-urllib/3.x` User-Agent, even
though the request is otherwise identical to one from `curl` or a browser.
This backend works around it by installing a browser-like `User-Agent` on
the process-wide `urllib` opener at import time (`backend/main.py`), which
also fixes it for the imported `tpc_search_combined` functions.

`bin/tpc_search_combined.py` run standalone (e.g. `python3
bin/tpc_search_combined.py --list-corpora`) is still affected by this and
currently fails with `API error 403: Forbidden` — worth a fix upstream in
that script too.

## Performance notes

A default search (all corpora, `exclude_sections` on) originally took
~21s, ~15s of which was this backend's own overhead on top of the raw
`search_documents` call (~6s baseline, out of our control — Cloudflare +
MaizeGDB's own Lucene search latency). Two backend-side fixes brought
steady-state searches down to ~6s, matching the raw API almost exactly:

1. **`exclude_sections` filtering was making one `/annotate` HTTP call per
   result, serially.** With that service down, 50 results meant 50
   sequential doomed round trips. Fixed with a rate-based circuit breaker
   (`_SIDECAR_STATE`/`_sidecar_note()` in `backend/main.py`) that stops
   attempting calls once a sliding window of recent ones is mostly
   failures, and a `ThreadPoolExecutor` to parallelize whatever calls do
   go out. Rate-based rather than a simple consecutive-failure streak
   because the sidecar turned out to be *flaky*, not hard-down — an
   occasional call genuinely succeeds, which reset a naive streak-based
   breaker before it could ever stay tripped.
2. **Every search that omitted `corpora` (the frontend's "All corpora"
   case) re-fetched the corpora list from the API** — which, measured in
   isolation, was itself as slow as a full search (~5-6s). Now cached for
   10 minutes (`_cached_list_corpora()`).

3. **"Load more" re-ran the whole search** (count + 25, another ~6s Lucene
   call). Search results are now cached for 10 minutes (`_cached_search()`).
   Document-level searches always fetch whole 200-result pages, which cost
   the same as 50 (measured 5.8s either way), so "Load more" within 200
   makes no API call. A full-text search fetches only what was asked for
   (200 sentence-level results take 8.4s against 6.9s, and prefetching the
   rest would double the live server's load), so its first "Load more"
   still runs one search. At most 32 searches are kept
   (`TEXTPRESSO_GUI_SEARCH_CACHE`), a few hundred MB in the worst case.
4. **Each paper's CAS2 was parsed again for every use** (gene check,
   section filter, snippets, "Load more", annotation panels). Parses are
   now cached in memory in packed form: ~1 MB per paper instead of the
   10-14 MB the parsed dicts take, unpacked in ~10 ms, checked against the
   file's mtime. At most 300 papers (`TEXTPRESSO_GUI_PAPER_CACHE`, 0 turns
   it off). Hygiene/italics/tier filtering still runs on every call.
5. **Browser caching.** `/api/annotate` and `/api/document` send an ETag
   (the CAS2 file's mtime and size, plus a token that changes on every
   backend restart) with `Cache-Control: private, no-cache`. The browser
   keeps the response and revalidates; an unchanged paper gets an empty 304
   before anything is parsed. Papers served over HTTP get no ETag.
6. **`casannot.parse_cas_file()` checked every annotation against every
   sentence** to find uncovered ones (7.4M comparisons, 70% of parse time on
   a 20,800-annotation paper). Now a binary search; output identical on all
   17 sample CAS files, about 2.3x faster. Same fix in `agr_textpresso`'s
   copy, so the public `/annotate` speeds up too.

7. **Hygiene ran where its output was discarded.** Section snippets and the
   matched-sentence references/acknowledgments filter use only a paper's
   sentences and sections, which hygiene never changes, so they now load
   with `hygiene=False`. Every place whose annotations reach the user or
   decide which papers are kept (`/api/annotate`, the keyword+category and
   category-in-section checks) still runs it.
8. **Category lookups are cached for a day** (validation, expansion and the
   autocomplete all go through `tpc.category_search`, ~0.5s each over HTTP).
   Capped at ~50 MB of memory (`TEXTPRESSO_GUI_CATEGORY_CACHE_MB`); failed
   lookups are not cached.

The remaining ~6s per search is the live API's own response time for a
~450KB-before-truncation payload (`matched_sentences` now capped at 8 per
document server-side to cut transfer size) — not fixable from this side
short of the CAS2-local-file option above (which only helps `/annotate`,
not the core search).
