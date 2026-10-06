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

The remaining ~6s per search is the live API's own response time for a
~450KB-before-truncation payload (`matched_sentences` now capped at 8 per
document server-side to cut transfer size) — not fixable from this side
short of the CAS2-local-file option above (which only helps `/annotate`,
not the core search).
