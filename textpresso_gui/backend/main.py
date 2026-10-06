"""FastAPI backend for the Textpresso Maize GUI.

Thin HTTP wrapper around the live Textpresso REST API. It exists for two
reasons:

1. CORS: the live API sends no Access-Control-Allow-Origin header, so a
   browser can't call it directly from a different origin. This server
   proxies every call and adds CORS headers for our own frontend.
2. Smarter search behavior: EXACT-vs-RELATED gene synonym filtering,
   ontology-graph expansion (ancestors/descendants), and CAS2
   section-exclusion (e.g. dropping bibliography-only matches) are not
   things the raw search_documents endpoint can do -- they're client-side
   logic. Rather than reimplement that logic here, this module imports
   bin/tpc_search_combined.py directly and calls its functions, so the
   GUI and the CLI share exactly one implementation.

This deliberately uses the SAME host tpc_search_combined.py itself defaults
to (https://textpresso.maizegdb.org) -- not the undocumented internal
fallback host mentioned in a comment there, even though that one tested more
reliable during development. See GUI_SETUP.md "Which Textpresso host" for
the full investigation and why: the public host is the one actually meant
to serve this data, and switching to an internal host silently would mean
serving different (and possibly test/internal-only) data than intended.

As of 2026-09-23 the public host has two known issues, both worked around
below without changing which host is used:
  - /api/* (search_documents, available_corpora) 403s requests carrying
    urllib's default "Python-urllib/3.x" User-Agent -- fixed by installing a
    browser-like UA on the process-wide urllib opener.
  - /annotate and /category_search currently 403/503 fairly consistently
    regardless of User-Agent -- this looks like a real outage/misconfig on
    MaizeGDB's end, not something fixable client-side. /category_search has
    no local-file equivalent, so category/gene lookup still fails open
    (matches kept rather than dropped, same as tpc_search_combined.py's own
    fail-open behavior) until that's fixed upstream. /annotate does have a
    local-file equivalent (see CAS_ROOT below), which this backend now uses
    by default -- so ontology annotation display, gene EXACT/RELATED
    filtering, and exclude_sections filtering no longer depend on that
    endpoint at all, and no longer fail open, as long as the local CAS2
    tree is reachable.

Run locally:
    pip install -r textpresso_gui/backend/requirements.txt
    uvicorn textpresso_gui.backend.main:app --reload --port 8000
"""

import argparse
import http.client
import importlib.util
import os
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"

# Parse CAS2 files from a local directory instead of fetching them from the
# live /annotate endpoint (see tpc_search_combined.py's own --cas-root
# option, which this reuses -- casannot.py's parse_cas_file() already
# supports this, this just plumbs the same path through the GUI backend).
# Only helps /annotate and exclude_sections/gene EXACT-RELATED filtering;
# /category_search has no local-file equivalent (it looks up OBO ontology
# terms, a different dataset from per-paper CAS2 annotations), so that still
# goes over HTTP and is still subject to the /annotate sidecar's known
# flakiness either way.
#
# Confirmed working against the real tpcas-2 tree (see GUI_SETUP.md): on
# this machine it's reachable via a WSL UNC mount of the agr_textpresso data
# volume, so that's the default rather than something you have to opt into.
# TEXTPRESSO_CAS_ROOT still overrides it (e.g. running this backend on the
# Linux box itself, where the path is .../agr_textpresso/.data/tpcas-2
# directly with no WSL mount needed). If neither path exists (WSL not
# running, wrong machine), falls back to the live HTTP endpoint rather than
# failing every annotation lookup.
_LOCAL_CAS_ROOT_DEFAULT = "//wsl.localhost/Ubuntu/home/ltibbs/agr_textpresso/.data/tpcas-2"
_env_cas_root = os.environ.get("TEXTPRESSO_CAS_ROOT")
if _env_cas_root:
    CAS_ROOT = _env_cas_root
elif Path(_LOCAL_CAS_ROOT_DEFAULT).is_dir():
    CAS_ROOT = _LOCAL_CAS_ROOT_DEFAULT
else:
    CAS_ROOT = None

# See module docstring: the public host 403s urllib's default User-Agent on
# /api/*. Installed before importing tpc_search_combined so every HTTP call
# in that module and this one (all go through urllib.request.urlopen()) picks
# it up. Confirmed this also breaks bin/tpc_search_combined.py run standalone
# (e.g. `python3 bin/tpc_search_combined.py --list-corpora` currently fails
# with "API error 403" without this) -- worth fixing upstream in that script
# too.
_opener = urllib.request.build_opener()
_opener.addheaders = [("User-Agent", "Mozilla/5.0 (compatible; TextpressoMaizeGUI/1.0)")]
urllib.request.install_opener(_opener)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# tpc_search_combined.py loads its own sibling module (casannot.py) via a
# path relative to its own __file__, so importing it this way (rather than
# via sys.path + `import`) works regardless of where uvicorn is launched from.
# Its own DEFAULT_URL (the public textpresso.maizegdb.org host) is used
# unchanged -- see module docstring.
tpc = _load_module("tpc_search_combined", BIN_DIR / "tpc_search_combined.py")

_orig_load_annotations = tpc._load_annotations
_orig_category_search = tpc.category_search

# Circuit breaker for the /annotate + /category_search sidecar (see
# GUI_SETUP.md "Known issue"). Without this, a search with the default
# exclude_sections filter made one /annotate call per result -- with that
# service down, 50 results meant 50 sequential doomed HTTP round trips,
# adding ~15s to every search (measured).
#
# Rate-based, not a simple consecutive-failure streak: the sidecar turns out
# to be flaky rather than hard-down (mostly 403/503, but an occasional call
# genuinely succeeds) -- a streak-based breaker resets to zero on that one
# stray success and never stays tripped, which is what happened in testing.
# Trips once a sliding window of recent calls is mostly failures, and only
# clears after a short run of consecutive successes (one lucky call isn't
# enough evidence it's actually back).
_SIDECAR_WINDOW = deque(maxlen=12)
_SIDECAR_STATE = {"down_until": 0.0, "consecutive_successes": 0}
_SIDECAR_LOCK = threading.Lock()
_SIDECAR_TRIP_MIN_SAMPLES = 5
_SIDECAR_TRIP_FAILURE_RATE = 0.7
_SIDECAR_RECOVER_STREAK = 3
_SIDECAR_COOLDOWN_SECONDS = 60


def _sidecar_is_down():
    return time.time() < _SIDECAR_STATE["down_until"]


def _sidecar_note(ok: bool):
    with _SIDECAR_LOCK:
        _SIDECAR_WINDOW.append(ok)
        if ok:
            _SIDECAR_STATE["consecutive_successes"] += 1
            if _SIDECAR_STATE["consecutive_successes"] >= _SIDECAR_RECOVER_STREAK:
                _SIDECAR_STATE["down_until"] = 0.0
        else:
            _SIDECAR_STATE["consecutive_successes"] = 0
            if len(_SIDECAR_WINDOW) >= _SIDECAR_TRIP_MIN_SAMPLES:
                failure_rate = 1 - (sum(_SIDECAR_WINDOW) / len(_SIDECAR_WINDOW))
                if failure_rate >= _SIDECAR_TRIP_FAILURE_RATE:
                    _SIDECAR_STATE["down_until"] = time.time() + _SIDECAR_COOLDOWN_SECONDS


def _load_annotations_with_retry(doc, args, ontology_filter=None, include_related=False, retries=4):
    """Retry + circuit-breaker wrapper around tpc._load_annotations().

    The retry loop is for transient network truncation
    (http.client.IncompleteRead), which tpc._load_annotations() itself
    doesn't catch (only urllib.error.HTTPError/URLError) -- harmless
    defensive measure, separate from the circuit breaker above.
    """
    if _sidecar_is_down():
        return None, None, None
    for attempt in range(retries):
        try:
            result = _orig_load_annotations(doc, args, ontology_filter, include_related)
            _sidecar_note(result[0] is not None)
            return result
        except (http.client.IncompleteRead, ConnectionError) as e:
            if attempt == retries - 1:
                print(f"  [annotation fetch failed after {retries} attempts: {e}]", file=sys.stderr)
                _sidecar_note(False)
                return None, None, None
            time.sleep(0.3)
    return None, None, None


def _category_search_with_breaker(*args, **kwargs):
    if _sidecar_is_down():
        return None
    result = _orig_category_search(*args, **kwargs)
    _sidecar_note(result is not None)
    return result


# Monkeypatch, not just a local alias: tpc.filter_gene_category_results()
# also calls _load_annotations() internally, and Python resolves that name
# from the module's own globals at call time, so this covers both our own
# call sites below and that imported function's. Same reasoning for
# category_search.
tpc._load_annotations = _load_annotations_with_retry
tpc.category_search = _category_search_with_breaker

app = FastAPI(title="Textpresso Maize GUI backend")

app.add_middleware(
    CORSMiddleware,
    # Dev-only: wide open so the static frontend can be served from any
    # local port/file origin. Narrow this to the real frontend origin
    # before this is deployed anywhere shared.
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Search-scope dropdown values (frontend) -> Textpresso `query.type` values.
# "full_text" is the one case that maps to "sentence" rather than a literal
# section name: it's the only scope where we also want matched sentence
# snippets back, and (per TPC_API_GUIDE.md) include_match_sentences is only
# valid when type == "sentence".
SEARCH_SCOPES = {
    "full_text": "sentence",
    "title": "title",
    "abstract": "abstract",
    "introduction": "introduction",
    "result": "result",
    "discussion": "discussion",
    "conclusion": "conclusion",
    "background": "background",
    "design": "design",
    "materials_and_methods": "materials and methods",
}

# Sections excluded from search by default (see the GUI mockup: users must
# opt in under Advanced options to include citations/funding boilerplate).
DEFAULT_EXCLUDED_SECTIONS = {"references", "acknowledgments"}

# --- Open-access gating -----------------------------------------------------
# Without login, non-open-access papers should only surface their top few
# matched sentences (plus ontology annotations, which are never gated --
# those come from a separate /api/annotate call this doesn't touch).
# search_documents carries no open-access flag and there's no real per-paper
# OA source wired up yet (the closest existing signal is corpus-level --
# "MaizeOA" is a whole separate open-access corpus, not a per-document field,
# see docs/Laura_work_updates_log.md), so every document defaults to open
# access until that's wired in.
#
# TEST-ONLY: marks every Nth result as non-open-access so the restricted
# view can be exercised without real OA metadata. Set to 0 to disable (i.e.
# everything open access) once real per-paper OA data replaces this.
_TEST_NON_OA_SAMPLE_EVERY = 4

MAX_SNIPPETS_OPEN_ACCESS = 8
MAX_SNIPPETS_RESTRICTED = 3


def _is_open_access(index: int) -> bool:
    if not _TEST_NON_OA_SAMPLE_EVERY:
        return True
    return index % _TEST_NON_OA_SAMPLE_EVERY != 0


class SearchRequest(BaseModel):
    keywords: Optional[str] = None
    search_in: str = Field(default="full_text", description="Key from SEARCH_SCOPES")
    corpora: Optional[List[str]] = Field(default=None, description="Default: all available corpora")
    count: int = Field(default=50, ge=1, le=200)

    exclude_keywords: Optional[str] = None
    case_sensitive: bool = False

    author: Optional[str] = None
    exact_author: bool = False
    journal: Optional[str] = None
    exact_journal: bool = False
    year: Optional[str] = None
    accession: Optional[str] = None
    paper_type: Optional[str] = None

    category: Optional[List[str]] = Field(
        default=None, description='Exact "name (ID)" strings from /api/category_search')
    categories_and: bool = False
    expand_relationship_types: Optional[List[str]] = None
    expand_ancestor_relationship_types: Optional[List[str]] = None
    related_synonyms: bool = False

    sort_by_year: bool = False

    # Sections to exclude from matches. Defaults to references +
    # acknowledgments; pass [] to include everything.
    exclude_sections: List[str] = Field(default_factory=lambda: sorted(DEFAULT_EXCLUDED_SECTIONS))


# Corpora rarely change, and fetching them turned out to be as slow as the
# search itself (~5-6s measured, same order as search_documents) -- the
# frontend omits `corpora` to mean "all", so every plain search was paying
# for a full extra round trip just to re-derive the same list. Cached with a
# generous TTL rather than hitting the API on every search that omits it.
_CORPORA_CACHE = {"value": None, "at": 0.0}
_CORPORA_CACHE_TTL_SECONDS = 600


def _cached_list_corpora():
    now = time.time()
    if _CORPORA_CACHE["value"] is None or (now - _CORPORA_CACHE["at"]) > _CORPORA_CACHE_TTL_SECONDS:
        _CORPORA_CACHE["value"] = tpc.list_corpora(tpc.DEFAULT_URL)
        _CORPORA_CACHE["at"] = now
    return _CORPORA_CACHE["value"]


def _build_args(req: SearchRequest, corpora: List[str], query_type: str) -> argparse.Namespace:
    """Build the argparse.Namespace shape tpc_search_combined.py's functions expect."""
    return argparse.Namespace(
        type=query_type,
        keywords=req.keywords,
        exclude=req.exclude_keywords,
        case_sensitive=req.case_sensitive,
        author=req.author,
        exact_author=req.exact_author,
        journal=req.journal,
        exact_journal=req.exact_journal,
        year=req.year,
        accession=req.accession,
        paper_type=req.paper_type,
        category=req.category,
        categories_and=req.categories_and,
        sort_by_year=req.sort_by_year,
        count=req.count,
        corpora=corpora,
        related_synonyms=req.related_synonyms,
        cas_root=CAS_ROOT,   # None -> HTTP; a path -> parse local CAS2 files (see CAS_ROOT above)
        url=tpc.DEFAULT_URL,
    )


def _validate_categories_or_400(categories: List[str]):
    """Same check as the CLI's check_categories_or_exit(), as an HTTP 400 instead of exit()."""
    problems = []
    for value in categories:
        matches = tpc.category_search(value, tpc.DEFAULT_URL, limit=8)
        if matches is None:
            continue  # lookup service unreachable -- fail open, same as the CLI
        if any(m["category"] == value for m in matches):
            continue
        problems.append({
            "value": value,
            "suggestions": [m["category"] for m in matches],
        })
    if problems:
        raise HTTPException(status_code=400, detail={
            "message": "One or more --category values are not exact matches. "
                       "Use /api/category_search to look up the exact string.",
            "problems": problems,
        })


@app.get("/api/corpora")
def get_corpora():
    try:
        return _cached_list_corpora()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Textpresso API: {e}")


@app.get("/api/category_search")
def get_category_search(
    q: str,
    ontology: Optional[List[str]] = Query(default=None),
    relationship_type: Optional[List[str]] = Query(default=None),
    ancestor_relationship_type: Optional[List[str]] = Query(default=None),
    limit: int = 10,
):
    matches = tpc.category_search(
        q, tpc.DEFAULT_URL, ontology=ontology, limit=limit,
        relationship_types=relationship_type,
        ancestor_relationship_types=ancestor_relationship_type,
    )
    if matches is None:
        raise HTTPException(status_code=502, detail="Category lookup service unreachable")
    return {"query": q, "matches": matches}


@app.get("/api/annotate")
def get_annotate(
    identifier: str,
    ontology: Optional[List[str]] = Query(default=None),
    related_synonyms: bool = False,
    exclude_sections: Optional[List[str]] = Query(default=None),
):
    args = argparse.Namespace(cas_root=CAS_ROOT, url=tpc.DEFAULT_URL)
    ontology_filter = set(ontology) if ontology else None
    sentences, annotations, sections = tpc._load_annotations(
        {"identifier": identifier}, args, ontology_filter=ontology_filter,
        include_related=related_synonyms)
    if annotations is None:
        raise HTTPException(status_code=404, detail=f"No CAS2 annotation data for {identifier}")
    if exclude_sections:
        annotations = tpc._ca.exclude_sections(annotations, sections, set(exclude_sections))
    return {
        "identifier": identifier,
        "ontology_summary": tpc._ca.summarize_by_ontology(annotations),
    }


@app.post("/api/search")
def post_search(req: SearchRequest):
    if not any([req.keywords, req.author, req.journal, req.year, req.accession, req.category]):
        raise HTTPException(status_code=400, detail="Provide at least one of: keywords, "
                             "author, journal, year, accession, category")

    query_type = SEARCH_SCOPES.get(req.search_in)
    if query_type is None:
        raise HTTPException(status_code=400, detail=f"Unknown search_in: {req.search_in!r}; "
                             f"expected one of {sorted(SEARCH_SCOPES)}")

    if req.category:
        _validate_categories_or_400(req.category)
        req = req.copy(update={"category": tpc.expand_categories_by_relationship(
            req.category, req.expand_relationship_types, tpc.DEFAULT_URL,
            req.expand_ancestor_relationship_types)})

    try:
        corpora = req.corpora or _cached_list_corpora()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Textpresso API: {e}")

    args = _build_args(req, corpora, query_type)
    payload = tpc.build_query(args, corpora)

    try:
        results = tpc.search(payload, url=tpc.DEFAULT_URL) or []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Search failed: {e}")

    if req.category:
        results = tpc.filter_gene_category_results(results, args)

    exclude_types = set(req.exclude_sections or ())
    if exclude_types and query_type == "sentence":
        # Same per-document filtering print_results() applies for --exclude-type
        # with --type sentence: map each raw matched-sentence string back to its
        # CAS2 position and drop it if it falls entirely within an excluded
        # section (e.g. a hit that only occurs in the bibliography). This is one
        # /annotate HTTP call per result -- doing that serially made a 50-result
        # search take ~15s longer than the raw search alone (measured), even
        # with every call failing fast, so it's parallelized. A doc whose
        # annotation lookup fails (including the known /annotate outage, see
        # GUI_SETUP.md) is left unfiltered, same fail-open behavior as before.
        def _filter_one(doc):
            sentences, _, sections = tpc._load_annotations(doc, args)
            if sentences is not None:
                doc["matched_sentences"] = tpc._filter_matched_sentences(
                    doc.get("matched_sentences", []), sentences, sections, exclude_types)

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(_filter_one, results))

        # A sentence-type search only returns a doc because it had at least
        # one matched sentence, so an empty list here means every match this
        # doc had was inside an excluded section (e.g. its only "hit" is a
        # citation title in the References list) and got stripped above.
        # There's nothing left to show, so drop the doc entirely instead of
        # displaying a snippet-less card with no explanation -- mirrors
        # --type document's _document_has_match_outside_excluded, which drops
        # the whole doc for the same reason.
        results = [d for d in results if d.get("matched_sentences") != []]

    # matched_sentences can be very large per document (a single "sentence"
    # is sometimes a whole embedded data table) -- the frontend only ever
    # displays the first few, so cap here to cut payload size and transfer
    # time rather than shipping everything over the wire unused. Non-open-
    # access papers get a much smaller cap (see _is_open_access above): only
    # the top few matched sentences, no full text, until login/OA data exists.
    for i, doc in enumerate(results):
        doc["open_access"] = _is_open_access(i)
        sentences = doc.get("matched_sentences")
        if sentences is not None:
            doc["matched_sentence_count"] = len(sentences)
            cap = MAX_SNIPPETS_OPEN_ACCESS if doc["open_access"] else MAX_SNIPPETS_RESTRICTED
            doc["matched_sentences"] = sentences[:cap]

    return {"count": len(results), "results": results}


@app.get("/api/health")
def health():
    return {"status": "ok"}
