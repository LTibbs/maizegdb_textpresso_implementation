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
import csv
import http.client
import importlib.util
import json
import os
import re
import sys
import threading
import hashlib
from array import array
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
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
_LOCAL_CAS_ROOT_DEFAULT = "/Users/laura/Documents/Textpresso_files/maizeopenaccess-20261015/tpcas-2"
_env_cas_root = os.environ.get("TEXTPRESSO_CAS_ROOT")
if _env_cas_root:
    CAS_ROOT = _env_cas_root
elif Path(_LOCAL_CAS_ROOT_DEFAULT).is_dir():
    CAS_ROOT = _LOCAL_CAS_ROOT_DEFAULT
else:
    CAS_ROOT = None

# Old live-server corpus names -> local directory name. MaizeOpenAccess
# supersedes all three; identifiers from the live API still carry the old names
# while the local CAS tree already has everything under MaizeOpenAccess.
_CORPUS_REMAP = {
    "MaizeOA": "MaizeOpenAccess",
    "MaizeTest": "MaizeOpenAccess",
    "MaizeTest100": "MaizeOpenAccess",
}

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
# Whole-document CAS2 parser for the full-text viewer (/api/document).
casdoc = _load_module("casdoc", REPO_ROOT / "textpresso_classifiers" / "casdoc.py")

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


def _load_annotations_with_retry(doc, args, ontology_filter=None, include_related=False,
                                 retries=4, **kwargs):
    """Local CAS2 first, else the HTTP /annotate sidecar with retry + circuit breaker.

    Local files are read directly and never touch the breaker. Previously a
    paper that simply isn't in the local tree (on the dev box only MaizeOA
    is) counted as a sidecar failure, so a handful of MaizeTest100 results
    tripped the breaker -- which then blocked every lookup, local MaizeOA
    files included, for the cooldown, silently switching off section
    exclusion and snippets. Papers not stored locally now go to the HTTP
    endpoint instead of failing outright.

    The retry loop is for transient network truncation
    (http.client.IncompleteRead), which tpc._load_annotations() itself
    doesn't catch (only urllib.error.HTTPError/URLError) -- harmless
    defensive measure, separate from the circuit breaker above.

    Other keyword arguments (hygiene, hygiene_report) pass straight through.
    """
    if getattr(args, "cas_root", None) and _has_local_cas(doc.get("identifier") or ""):
        raw_id = doc.get("identifier") or ""
        id_parts = [p for p in raw_id.split("/") if p]
        if len(id_parts) >= 1 and id_parts[0] in _CORPUS_REMAP:
            remapped = _CORPUS_REMAP[id_parts[0]] + "/" + "/".join(id_parts[1:])
            doc = {**doc, "identifier": remapped}
        return _orig_load_annotations(doc, args, ontology_filter, include_related, **kwargs)
    http_args = argparse.Namespace(**{**vars(args), "cas_root": None})
    if _sidecar_is_down():
        return None, None, None, False
    for attempt in range(retries):
        try:
            result = _orig_load_annotations(doc, http_args, ontology_filter, include_related,
                                            **kwargs)
            _sidecar_note(result[0] is not None)
            return result
        except (http.client.IncompleteRead, ConnectionError) as e:
            if attempt == retries - 1:
                print(f"  [annotation fetch failed after {retries} attempts: {e}]", file=sys.stderr)
                _sidecar_note(False)
                return None, None, None, False
            time.sleep(0.3)
    return None, None, None, False


# Category lookups (~0.6s each over HTTP) are repeated constantly: every
# search re-validates its categories one by one and re-expands them, and the
# autocomplete re-asks for prefixes already typed. The OBO files change at
# most monthly, so answers are kept for a day. Bounded by estimated memory
# (TEXTPRESSO_GUI_CATEGORY_CACHE_MB, default 50), least recently used dropped
# first; an entry is counted as 4x its JSON size, the ratio measured for a
# 100-term expansion (~25 KB JSON, ~100 KB in memory). Failed lookups (None)
# are never kept, so an outage isn't remembered.
_CATEGORY_CACHE = OrderedDict()  # call arguments -> (time, estimated bytes, matches)
_CATEGORY_CACHE_TTL_SECONDS = 86400
_CATEGORY_CACHE_MAX_BYTES = int(float(os.environ.get("TEXTPRESSO_GUI_CATEGORY_CACHE_MB", "50")) * 1e6)
_CATEGORY_CACHE_MEMORY_PER_JSON_BYTE = 4
_CATEGORY_CACHE_STATE = {"bytes": 0}
_CATEGORY_CACHE_LOCK = threading.Lock()


def _category_search_with_breaker(*args, **kwargs):
    key = json.dumps([args, kwargs], sort_keys=True, default=list)
    with _CATEGORY_CACHE_LOCK:
        hit = _CATEGORY_CACHE.get(key)
        if hit and time.time() - hit[0] <= _CATEGORY_CACHE_TTL_SECONDS:
            _CATEGORY_CACHE.move_to_end(key)
            return [dict(m) for m in hit[2]]
    if _sidecar_is_down():
        return None
    result = _orig_category_search(*args, **kwargs)
    _sidecar_note(result is not None)
    if result is not None:
        size = _CATEGORY_CACHE_MEMORY_PER_JSON_BYTE * (len(key) + len(json.dumps(result)))
        with _CATEGORY_CACHE_LOCK:
            old = _CATEGORY_CACHE.pop(key, None)
            if old:
                _CATEGORY_CACHE_STATE["bytes"] -= old[1]
            if size <= _CATEGORY_CACHE_MAX_BYTES:
                _CATEGORY_CACHE[key] = (time.time(), size, [dict(m) for m in result])
                _CATEGORY_CACHE_STATE["bytes"] += size
            while _CATEGORY_CACHE_STATE["bytes"] > _CATEGORY_CACHE_MAX_BYTES:
                _CATEGORY_CACHE_STATE["bytes"] -= _CATEGORY_CACHE.popitem(last=False)[1][1]
    return result


# Monkeypatch, not just a local alias: tpc.filter_gene_category_results()
# also calls _load_annotations() internally, and Python resolves that name
# from the module's own globals at call time, so this covers both our own
# call sites below and that imported function's. Same reasoning for
# category_search.
tpc._load_annotations = _load_annotations_with_retry
tpc.category_search = _category_search_with_breaker

# --- Parsed-paper cache -------------------------------------------------------
# One search reads the same paper's CAS2 several times (gene check, section
# filter, snippets), and "Load more", annotation panels and "Show unfiltered"
# read it again. parse_cas_file() is ~0.5s on a large paper; as dicts its
# result is 10-14 MB (20,000 annotations), too much to keep many of. Kept
# packed instead -- annotation fields in arrays, strings stored once per paper
# -- it is ~1 MB and unpacks in ~10 ms to exactly the parsed value (measured
# 2026-10-22 on agr_textpresso's sample CAS files). Fresh dicts on every read,
# so callers can't change the cached copy. Keyed by path and checked against
# the file's mtime and size, so a re-tokenized paper is never served stale.
# Hygiene/italics/tier filtering runs after this, per call, so its settings
# and data files are never cached here. TEXTPRESSO_GUI_PAPER_CACHE sets how
# many papers are kept (default 300, i.e. roughly 300 MB at most).
_PAPER_CACHE = OrderedDict()  # path -> ((mtime_ns, size), packed parse)
_PAPER_CACHE_SIZE = int(os.environ.get("TEXTPRESSO_GUI_PAPER_CACHE", "300"))
_PAPER_CACHE_LOCK = threading.Lock()
_ANN_STRING_FIELDS = ("term", "category", "ontology", "onto_id")
_orig_parse_cas_file = tpc._ca.parse_cas_file


def _pack_parse(parsed):
    sentences, annotations, sections = parsed
    strings, index = [], {}

    def ref(s):
        i = index.get(s)
        if i is None:
            i = index[s] = len(strings)
            strings.append(s)
        return i

    columns = {"begin": array("q", (a["begin"] for a in annotations)),
               "end": array("q", (a["end"] for a in annotations))}
    for field in _ANN_STRING_FIELDS:
        columns[field] = array("I", (ref(a[field]) for a in annotations))
    return (tuple((s["begin"], s["end"], s["text"]) for s in sentences),
            columns, tuple(strings), tuple(sections))


def _unpack_parse(packed):
    sentences, columns, strings, sections = packed
    return (
        [{"begin": b, "end": e, "text": t} for b, e, t in sentences],
        [{"begin": b, "end": e, "term": strings[t], "category": strings[c],
          "ontology": strings[o], "onto_id": strings[i]}
         for b, e, t, c, o, i in zip(columns["begin"], columns["end"],
                                     *(columns[f] for f in _ANN_STRING_FIELDS))],
        [dict(s) for s in sections],
    )


def _cached_parse_cas_file(cas_path):
    key = str(cas_path)
    st = os.stat(cas_path)
    stamp = (st.st_mtime_ns, st.st_size)
    with _PAPER_CACHE_LOCK:
        hit = _PAPER_CACHE.get(key)
        if hit and hit[0] == stamp:
            _PAPER_CACHE.move_to_end(key)
            return _unpack_parse(hit[1])
    parsed = _orig_parse_cas_file(cas_path)
    with _PAPER_CACHE_LOCK:
        _PAPER_CACHE[key] = (stamp, _pack_parse(parsed))
        _PAPER_CACHE.move_to_end(key)
        while len(_PAPER_CACHE) > _PAPER_CACHE_SIZE:
            _PAPER_CACHE.popitem(last=False)
    return parsed


# tpc._load_annotations() calls _ca.parse_cas_file() through its module, so
# this covers every local-CAS2 read in this process. The CLI is unaffected.
if _PAPER_CACHE_SIZE > 0:
    tpc._ca.parse_cas_file = _cached_parse_cas_file

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

# The search API returns 200 results per call; tpc.search() pages past that,
# at the cost of one more ~6s server-side search per extra 200 (run in
# parallel). MAX_RESULTS is the most a search returns ("Load more" stops
# there). MAX_CANDIDATES is how far down the ranking a search looks when it
# has to filter results itself afterwards -- categories limited to a section,
# keywords in a section plus categories, "all of" with expanded categories.
# Papers ranked below it are never seen, and the response says so.
MAX_RESULTS = 1000
MAX_CANDIDATES = int(os.environ.get("TEXTPRESSO_GUI_MAX_CANDIDATES", "2000"))

# --- Open-access gating -----------------------------------------------------
# Per-paper access level, from what the PDF pipeline records for every paper
# it fetched: Unpaywall's oa_status, plus the reuse licence Europe PMC reports
# (epmc_license, written by the pipeline's refresh_licenses.py and re-checked
# each run, so delayed licences are picked up). Unpaywall alone isn't enough:
# it calls journals that relicense on a delay (Genome Research, J Cell Biol)
# "bronze" even once the article is CC BY-NC, and has no status at all for
# some fully CC BY papers (PLoS ONE, microPublication Biology).
#
# The default is metadata/MaizeOpenAccess_access.csv in this repo: those two
# columns cut from the pipeline's download_manifest.csv for the 3,299
# MaizeOpenAccess papers (10 not in the manifest take oa_status from
# MaizeOpenAccess_papers.csv). Regenerate it with bin/build_access_csv.py
# when the corpus changes. Any CSV
# with doi, oa_status and epmc_license columns works, e.g. the full
# download_manifest.csv, via TEXTPRESSO_GUI_OA_MANIFEST. Deliberately not
# TEXTPRESSO_OA_MANIFEST: the API sidecars read that name as a different file
# (a whitespace-separated TSV, textpressoapi/access.py). Three levels:
#
#   "open"        Unpaywall gold / green / hybrid, OR any Creative Commons
#                 licence in Europe PMC: the full set of top matched
#                 sentences, and the full-text viewer where the paper is
#                 stored locally.
#   "publisher"   bronze without a CC licence -- free to read on the
#                 publisher's site but not licensed for reuse, so the full
#                 text isn't republished here (no viewer); still gets the full
#                 set of top matched sentences.
#   "restricted"  everything else (closed, no status, not in the manifest):
#                 only the top few matched sentences.
#
# Ontology annotations are never gated (separate /api/annotate call). If the
# manifest can't be read, every paper is treated as restricted rather than
# guessing that it's open.
OA_MANIFEST = Path(os.environ.get(
    "TEXTPRESSO_GUI_OA_MANIFEST",
    REPO_ROOT / "sorghumbase_textpresso_implementation" / "metadata" / "MaizeOpenAccess_access.csv"))
_OPEN_STATUSES = {"gold", "green", "hybrid"}
_PUBLISHER_STATUSES = {"bronze"}
_OA_STATUSES = None  # accession-form DOI -> (Unpaywall oa_status, Europe PMC licence)
_OA_LOCK = threading.Lock()

MAX_SNIPPETS = {"open": 8, "publisher": 8, "restricted": 3}


def _load_oa_statuses() -> dict:
    global _OA_STATUSES
    with _OA_LOCK:
        if _OA_STATUSES is None:
            table = {}
            try:
                with open(OA_MANIFEST, encoding="utf-8", errors="replace", newline="") as f:
                    for row in csv.DictReader(f):
                        if row.get("doi"):
                            table[_abstract_key(row["doi"])] = (
                                (row.get("oa_status") or "").strip().lower(),
                                (row.get("epmc_license") or "").strip().lower())
            except OSError as e:
                print(f"  [WARNING: OA manifest not readable ({e}); treating every paper as "
                      f"restricted -- set TEXTPRESSO_GUI_OA_MANIFEST]", file=sys.stderr)
            _OA_STATUSES = table
        return _OA_STATUSES


def _oa_info(identifier: str) -> tuple:
    """(Unpaywall oa_status, Europe PMC licence) for a paper; "" where unknown."""
    # "MaizeOA/<acc>/<acc>.tpcas" -> <acc>, which is the DOI with "/" -> "_"
    parts = [p for p in identifier.split("/") if p]
    return _load_oa_statuses().get(parts[1].lower(), ("", "")) if len(parts) > 1 else ("", "")


def _oa_status(identifier: str) -> str:
    return _oa_info(identifier)[0]


def _access_level(identifier: str) -> str:
    status, license_ = _oa_info(identifier)
    if status in _OPEN_STATUSES or license_.startswith("cc"):
        return "open"
    if status in _PUBLISHER_STATUSES:
        return "publisher"
    return "restricted"


def _is_open_access(identifier: str) -> bool:
    return _access_level(identifier) == "open"


def _can_view_full_text(identifier: str, user=None) -> bool:
    """Whether the caller may open this paper in the full-text viewer.

    The single gate for /api/document and /api/figure (and the per-result
    viewer_allowed flag the search page uses to show or hide the link).
    Open-access papers only for now; when GUI login exists, pass the
    authenticated user here and return True for restricted papers they're
    entitled to -- nothing else needs to change.
    """
    return _is_open_access(identifier)


class SearchRequest(BaseModel):
    keywords: Optional[str] = None
    search_in: str = Field(default="full_text", description="Key from SEARCH_SCOPES")
    corpora: Optional[List[str]] = Field(default=None, description="Default: all available corpora")
    count: int = Field(default=50, ge=1, le=MAX_RESULTS)

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
    # On by default. The frontend shows the filter report under each panel
    # with a "show unfiltered" toggle (hygiene=false), so curators are never
    # shown fewer rows with no way back.
    hygiene: bool = True,
    italics: bool = True,
    *,
    request: Request,
    response: Response,
):
    etag = _local_etag(identifier)
    if _not_modified(request, etag):
        return Response(status_code=304, headers={"ETag": etag, **_BROWSER_CACHE_HEADERS})
    if etag:
        response.headers.update({"ETag": etag, **_BROWSER_CACHE_HEADERS})
    # hygiene/italics are applied inside tpc._load_annotations (local CAS2) or by
    # the sidecar (HTTP); the GUI carries no filtering logic of its own.
    args =argparse.Namespace(cas_root=CAS_ROOT, url=tpc.DEFAULT_URL,
                              hygiene=hygiene, italics=italics)
    ontology_filter = set(ontology) if ontology else None
    report = {}
    sentences, annotations, sections, _ = tpc._load_annotations(
        {"identifier": identifier}, args, ontology_filter=ontology_filter,
        include_related=related_synonyms, hygiene_report=report)
    if annotations is None:
        raise HTTPException(status_code=404, detail=f"No CAS2 annotation data for {identifier}")
    if exclude_sections:
        annotations = tpc._ca.exclude_sections(annotations, sections, set(exclude_sections))
    return {
        "identifier": identifier,
        "ontology_summary": tpc._ca.summarize_by_ontology(annotations),
        "hygiene": _hygiene_block(hygiene, italics, len(annotations), report, exclude_sections),
    }


def _hygiene_block(enabled, italics, kept, report, exclude_sections=None):
    """The filter report the frontend shows under an annotation panel; shared by
    /api/annotate and /api/document so both describe the filter the same way."""
    return {
        "kept": kept,
        "enabled": enabled,
        "italics": italics,
        "excluded_sections": sorted(exclude_sections or ()),
        "dropped": report.get("dropped", 0),
        "dropped_by_reason": report.get("dropped_by_reason", {}),
        "summary": (tpc.format_hygiene_report({**report, "kept": kept})
                    if report.get("dropped") else ""),
    }


# --- Abstracts from our own corpus metadata CSVs -----------------------------
# search_documents returns no abstract, so the GUI's Abstract panel reads it
# from the per-corpus metadata CSVs the corpora were built from
# (sorghumbase_textpresso_implementation/metadata/<Corpus>_papers.csv, columns
# doi/pubmed_id/title/abstract/...) -- deliberately not PubMed, so the text
# shown is exactly what was ingested. TEXTPRESSO_METADATA_DIR overrides the
# directory. Keyed by DOI in Textpresso's accession form ("/" -> "_",
# lowercased) plus PMID; every CSV is merged into one lookup, since the same
# paper can appear in several corpora and only some of those rows carry an
# abstract. MaizeOpenAccess_papers.csv covers every paper of the superseded
# MaizeOA/MaizeTest/MaizeTest100 corpora, so their identifiers still resolve
# while the live server serves them. Loaded once on
# first use; a paper with no matching row just gets no abstract.
METADATA_DIR = Path(os.environ.get(
    "TEXTPRESSO_METADATA_DIR",
    REPO_ROOT / "sorghumbase_textpresso_implementation" / "metadata"))
_ABSTRACTS = None  # normalized doi / "pmid:<n>" -> abstract
_ABSTRACTS_LOCK = threading.Lock()


def _abstract_key(doi: str) -> str:
    return doi.strip().replace("/", "_").lower()


def _load_abstracts():
    global _ABSTRACTS
    with _ABSTRACTS_LOCK:
        if _ABSTRACTS is not None:
            return _ABSTRACTS
        table = {}
        for path in sorted(METADATA_DIR.glob("*_papers.csv")):
            try:
                with open(path, encoding="utf-8", errors="replace", newline="") as f:
                    for row in csv.DictReader(f):
                        abstract = (row.get("abstract") or "").strip()
                        if not abstract:
                            continue
                        if row.get("doi"):
                            table.setdefault(_abstract_key(row["doi"]), abstract)
                        if (row.get("pubmed_id") or "").strip():
                            table.setdefault(f"pmid:{row['pubmed_id'].strip()}", abstract)
            except (OSError, csv.Error) as e:
                print(f"  [could not read metadata CSV {path}: {e}]", file=sys.stderr)
        _ABSTRACTS = table
        return table


def _abstract_for(doc) -> Optional[str]:
    table = _load_abstracts()
    # "MaizeOA/<acc>/<acc>.tpcas" -> <acc>, which is the DOI with "/" -> "_"
    parts = (doc.get("identifier") or "").split("/")
    accession = doc.get("accession") or (parts[1] if len(parts) > 1 else "")
    if not accession:
        return None
    return table.get(_abstract_key(accession)) or table.get(f"pmid:{accession}")


# --- Ranking matched sentences ------------------------------------------------
# The API returns a document's matched sentences in reading order with no
# per-sentence score (its `score` is for the whole document), so "the first
# N" is usually the title plus the opening of the abstract. Instead, pick the
# N best by:
#   1. demoting sentences that just repeat the title or abstract (the GUI
#      already shows both) and very long "sentences" (usually an embedded
#      data table rather than prose), and ones carrying PDF page furniture
#      (running headers/footers like "PLOS Genetics | www.plosgenetics.org"
#      glued onto the sentence during text extraction);
#   2. then preferring sentences containing more distinct search terms;
#   3. then preferring Results/Discussion/Conclusion over Introduction/
#      Background -- the only signal that separates sentences in a
#      single-keyword search, where every sentence ties on (2). Needs the
#      paper's CAS2 section map, which post_search already loads for
#      References/Acknowledgments exclusion (see _sections_by_text); without
#      it every sentence counts as neutral;
#   4. ties go to the earlier sentence.
# The chosen sentences are returned in reading order, so they still read
# naturally top to bottom.
_LONG_SENTENCE_CHARS = 600
_BOOLEAN_WORDS = {"AND", "OR", "NOT"}
_PREFERRED_SECTIONS = {"result", "discussion", "conclusion"}
_LESS_PREFERRED_SECTIONS = {"introduction", "background", "beginning of article"}
_PAGE_FURNITURE_RE = re.compile(
    r"www\.|https?://|\bdoi\s*:?\s*10\.\d|©|\bcopyright\b|beginning of article"
    r"|\|\s*(?:volume|issue|page)\b|\bvolume\s+\d+\s*\|"
    r"|^\s*\d+\s+of\s+\d+\s*$"                              # "7 of 50" page counter on its own line
    r"|^\s*(?:received|accepted|published)(?:\s+online)?\s*:?\s*\d",
    re.IGNORECASE | re.MULTILINE)


def _search_terms(keywords: Optional[str]) -> List[re.Pattern]:
    """One case-insensitive prefix pattern per distinct keyword.

    Same tokenizing as the frontend's highlightTerms(): quotes and parens
    dropped, boolean operators skipped. Prefix match so "anthocyanin" also
    counts "anthocyanins"; Lucene wildcards are stripped for the same reason.
    """
    words = re.sub(r'["()*?]', " ", keywords or "").split()
    seen = []
    for w in words:
        if w.upper() not in _BOOLEAN_WORDS and w.lower() not in seen:
            seen.append(w.lower())
    return [re.compile(r"(?<!\w)" + re.escape(w), re.IGNORECASE) for w in seen]


def _norm_text(s: Optional[str]) -> str:
    # Lowercase, tags and punctuation stripped, whitespace collapsed -- so a
    # CAS2 sentence and the CSV abstract compare equal despite formatting.
    s = re.sub(r"<[^>]+>", " ", s or "").lower()
    return " ".join(re.sub(r"[^\w]+", " ", s).split())


def _sections_by_text(cas_sentences, sections) -> dict:
    """Map each CAS2 sentence's text to the section types it falls in.

    Keyed the same way _filter_matched_sentences() matches API sentence
    strings back to CAS2 (whitespace-normalized text). Text that occurs more
    than once gets the union of its sections.
    """
    out = {}
    for cs in cas_sentences:
        types = tpc._ca.section_types_at(cs["begin"], cs["end"], sections)
        out.setdefault(tpc._normalize_text(cs["text"]), set()).update(types)
    return out


def _section_preference(types) -> int:
    if types & _PREFERRED_SECTIONS:
        return 2
    if types & _LESS_PREFERRED_SECTIONS:
        return 0
    return 1  # methods, unlabeled, or no section map at all


def _top_sentences(sentences: List[str], cap: int, terms: List[re.Pattern],
                   title: Optional[str], abstract: Optional[str],
                   sections_by_text: Optional[dict] = None) -> List[str]:
    if len(sentences) <= cap:
        return sentences
    title_n, abstract_n = _norm_text(title), _norm_text(abstract)
    sections_by_text = sections_by_text or {}

    def rank_key(item):
        index, sentence = item
        norm = _norm_text(sentence)
        repeats = bool(norm) and (
            (title_n and (norm in title_n or title_n in norm))
            or (abstract_n and norm in abstract_n))
        demoted = (repeats or len(sentence) > _LONG_SENTENCE_CHARS
                   or bool(_PAGE_FURNITURE_RE.search(sentence)))
        hits = sum(1 for t in terms if t.search(sentence))
        section = _section_preference(sections_by_text.get(tpc._normalize_text(sentence), set()))
        return (demoted, -hits, -section, index)

    chosen = sorted(enumerate(sentences), key=rank_key)[:cap]
    return [s for _, s in sorted(chosen)]


# --- Search-result cache ------------------------------------------------------
# "Load more" re-sends the same search with count + 25, and the live API
# re-runs the whole Lucene search for it (~6s). The API's cost is the search,
# not the result count (measured 2026-10-22, "drought", all corpora):
#   document-level: 50 results 5.8s / 0.02 MB, 200 results 5.8s / 0.09 MB
#   sentence-level: 50 results 6.9s / 1.3 MB,  200 results 8.4s / 2.3 MB
# So document-level searches always fetch whole 200-result pages, and "Load
# more" within the page is served from here. A sentence-level search fetches
# only what was asked for: prefetching the rest would cost the live server a
# second Lucene search for every full-text search, so its first "Load more"
# pays for one search. Entries are futures, so a request arriving while the
# same fetch is in flight waits on it instead of starting another. The index
# only changes on a re-index, so a 10-minute TTL is safe. A 200-result
# sentence-level entry is ~2 MB as JSON, so the default bounds the cache at a
# few hundred MB in the worst case; TEXTPRESSO_GUI_SEARCH_CACHE sets the size.
_SEARCH_CACHE = OrderedDict()  # payload without count -> (time, count fetched, future)
_SEARCH_CACHE_SIZE = int(os.environ.get("TEXTPRESSO_GUI_SEARCH_CACHE", "32"))
_SEARCH_CACHE_TTL_SECONDS = 600
_SEARCH_CACHE_LOCK = threading.Lock()
_SEARCH_POOL = ThreadPoolExecutor(max_workers=8)


def _search_entry_covers(entry, want: int) -> bool:
    at, fetched, future = entry
    if time.time() - at > _SEARCH_CACHE_TTL_SECONDS:
        return False
    if future.done() and future.exception() is not None:
        return False
    if fetched >= want:
        return True
    # Fewer results than were asked for means that was all of them.
    return future.done() and len(future.result()) < fetched


def _cached_search(payload: dict) -> list:
    want = payload.get("count") or tpc.API_PAGE_SIZE
    full_pages = -(-want // tpc.API_PAGE_SIZE) * tpc.API_PAGE_SIZE  # want, rounded up
    sentence_level = bool(payload.get("include_match_sentences"))
    key = json.dumps({k: v for k, v in payload.items() if k != "count"}, sort_keys=True)

    def fetch(n):
        # Under _SEARCH_CACHE_LOCK: submit() only queues, it never blocks.
        future = _SEARCH_POOL.submit(
            lambda: tpc.search({**payload, "count": n}, url=tpc.DEFAULT_URL) or [])
        _SEARCH_CACHE[key] = (time.time(), n, future)
        _SEARCH_CACHE.move_to_end(key)
        while len(_SEARCH_CACHE) > _SEARCH_CACHE_SIZE:
            _SEARCH_CACHE.popitem(last=False)
        return future

    with _SEARCH_CACHE_LOCK:
        entry = _SEARCH_CACHE.get(key)
        if entry and _search_entry_covers(entry, want):
            _SEARCH_CACHE.move_to_end(key)
            future = entry[2]
        else:
            future = fetch(want if sentence_level else full_pages)
    results = future.result()  # a failed fetch raises here and is never served again
    # Copies: callers set per-request fields (matched_sentences, access, ...).
    return [dict(d) for d in results[:want]]


def _run_search(args: argparse.Namespace, corpora: List[str]) -> list:
    # (Double-encoded author/title/journal metadata is already repaired inside
    # tpc.search() -- see fix_mojibake() in tpc_search_combined.py.)
    try:
        return _cached_search(tpc.build_query(args, corpora))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Search failed: {e}")


def _is_gene_category(category: str) -> bool:
    return bool(tpc._gene_category_onto_ids([category]))


def _expand_categories(req: SearchRequest, categories: List[str]) -> List[str]:
    """Add ancestor/descendant terms for ontology (GO/PO/TO) categories.

    Gene categories are never expanded: a gene's "parent" in the gene OBO is
    a catch-all grouping term, so expanding upward would match every gene.
    """
    terms = [c for c in categories if not _is_gene_category(c)]
    expanded = tpc.expand_categories_by_relationship(
        terms, req.expand_relationship_types, tpc.DEFAULT_URL,
        req.expand_ancestor_relationship_types) if terms else []
    return list(dict.fromkeys(list(categories) + list(expanded)))


def _category_groups(req: SearchRequest) -> List[List[str]]:
    """One group per chosen category: the term itself plus its expansion."""
    with ThreadPoolExecutor(max_workers=len(req.category)) as pool:
        return list(pool.map(lambda c: _expand_categories(req, [c]), req.category))


def _identifier_key(doc) -> str:
    # The API returns identifiers with either one or two slashes after the
    # corpus name depending on the query (see tpc_search_combined.py's
    # _document_has_match_outside_excluded), so normalize before comparing.
    return re.sub(r"/+", "/", doc.get("identifier", ""))


def _candidates_note() -> str:
    return (f"Only the top {MAX_CANDIDATES:,} papers for each part of this search could be "
            "checked, so lower-ranked matches may be missing.")


def _take_verified(docs, check, count: int):
    """Run check over docs in rank order until count of them pass.

    check(doc) returns True (keep), False (drop) or None (couldn't be checked
    -- kept, the same fail-open rule as everywhere else here). Papers are
    checked in parallel batches, so a long candidate list costs only as many
    lookups as it takes to fill count. Returns (kept, number kept unchecked).
    """
    kept, unverified, i = [], 0, 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        while i < len(docs) and len(kept) < count:
            batch = docs[i:i + max(count - len(kept), 20)]
            i += len(batch)
            for doc, ok in zip(batch, pool.map(check, batch)):
                if ok is not False and len(kept) < count:
                    kept.append(doc)
                    unverified += ok is None
    return kept, unverified


def _gene_filter(docs, args, count: int) -> list:
    """tpc.filter_gene_category_results() one paper at a time, in parallel,
    stopping once count papers are kept."""
    if not tpc._gene_category_onto_ids(args.category):
        return docs[:count]
    return _take_verified(docs, lambda d: bool(tpc.filter_gene_category_results([d], args)), count)[0]


def _search_with_categories(req: SearchRequest, groups, corpora: List[str], query_type: str,
                            notes: Optional[list] = None, verify_genes: bool = True):
    """Run the search for req, honoring any/all across category groups.

    Returns (results, args). groups is _category_groups(req), or None when
    the request has no categories. verify_genes=False skips the EXACT-only
    gene re-check, for a caller that checks each paper's annotations itself
    (see _section_category_search). Notes for the user are appended to notes.
    """
    if not groups:
        args = _build_args(req, corpora, query_type)
        return _run_search(args, corpora), args
    if req.categories_and and len(groups) > 1 and any(len(g) > 1 for g in groups):
        return _search_category_groups(req, groups, corpora, query_type, notes, verify_genes)
    flat_req = req.copy(update={"category": list(dict.fromkeys(c for g in groups for c in g))})
    args = _build_args(flat_req, corpora, query_type)
    docs = _run_search(args, corpora)
    return (_gene_filter(docs, args, req.count) if verify_genes else docs), args


def _search_category_groups(req: SearchRequest, groups, corpora: List[str], query_type: str,
                            notes: Optional[list] = None, verify_genes: bool = True):
    """"All of these categories" when ancestor/descendant expansion is also on.

    Expansion turns each category into a group (the term plus its ancestors/
    descendants) that should match if ANY member does, while "all" should
    require EVERY group. The API can't express that in one query: flattening
    every group into one list with categories_and_ed=true would demand every
    expanded term at once. Instead, run one OR'd search per group (in
    parallel) and keep only the papers found in every group, in the first
    group's relevance order.

    Each group search goes MAX_CANDIDATES deep so the intersection isn't cut
    short; a paper ranked below that in any one group is missed, and a note
    says so. The EXACT-only gene re-check runs on the intersection, not on
    each group, so it costs one lookup per kept paper rather than per hit.
    """
    def _one_group(group):
        group_req = req.copy(update={"category": group, "categories_and": False,
                                     "count": MAX_CANDIDATES})
        return _run_search(_build_args(group_req, corpora, query_type), corpora)

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        results = list(pool.map(_one_group, groups))
    if notes is not None and any(len(docs) >= MAX_CANDIDATES for docs in results):
        notes.append(_candidates_note())

    common = set.intersection(*({_identifier_key(d) for d in docs} for docs in results))
    kept = [d for d in results[0] if _identifier_key(d) in common]
    # Gene categories are never expanded, so each is a group of one and
    # "every group" means every gene: categories_and=True over the flat list.
    flat_req = req.copy(update={"category": [c for g in groups for c in g], "categories_and": True})
    args = _build_args(flat_req, corpora, query_type)
    return (_gene_filter(kept, args, req.count) if verify_genes else kept), args


# --- "Same paper" search: keywords AND categories anywhere in a paper -------
# With the "Full text" scope, a search with both keywords and categories is a
# sentence-level query, which makes the API require the keyword and the
# category in the SAME sentence (measured: drought + cct1 = 0 papers that
# way vs 16 at paper level). The GUI's two separate query rows read as "papers
# about X that also involve Y", so that combination is searched at paper
# level instead.
#
# Paper-level results have no matched sentences and no section positions, so
# References/Acknowledgments exclusion is re-checked per paper from its CAS2
# sentences, annotations and section map. A paper is only dropped on positive
# evidence that every keyword match, or every match for a required category,
# sits inside an excluded section; anything that can't be checked is kept
# (same fail-open rule as the sentence-level filter in post_search).

# Lucene syntax the local keyword matcher doesn't try to reproduce -- such
# queries go straight to the per-paper server check.
_LUCENE_SYNTAX_RE = re.compile(r'["*?~^:\\()\[\]{}]|\b(?:AND|OR|NOT)\b|(?:^|\s)[+-]')


def _local_keyword_patterns(keywords: str, case_sensitive: bool):
    """Patterns for approximating the server's keyword match on CAS2 text.

    A plain multi-word query is OR'd (measured: most sentences the server
    returns for "drought tolerance" contain only one of the words), and the
    match is roughly word-prefix based ("tolerances" matches "tolerance").
    Returns None for queries using Lucene syntax.
    """
    if not keywords or _LUCENE_SYNTAX_RE.search(keywords):
        return None
    flags = 0 if case_sensitive else re.IGNORECASE
    return [re.compile(r"\b" + re.escape(t), flags) for t in keywords.split()]


def _server_sentences_for_paper(doc, kw_req: SearchRequest):
    """Keyword-only sentence search restricted to one paper (~6s per call)."""
    corpus = _identifier_key(doc).split("/")[0]
    accession = doc.get("accession")
    if not accession or not corpus:
        return None
    one_req = kw_req.copy(update={"accession": accession, "count": 5})
    args = _build_args(one_req, [corpus], "sentence")
    try:
        hits = tpc.search(tpc.build_query(args, [corpus]), url=tpc.DEFAULT_URL) or []
    except Exception:
        return None
    return [s for d in hits for s in d.get("matched_sentences") or []]


def _paper_level_search(req: SearchRequest, groups, corpora: List[str], exclude_types: set):
    """Keywords AND categories in the same paper (Full text), with section exclusion.

    Returns (results, args), each result's matched_sentences set to its
    keyword sentences outside the excluded sections (for snippets). A single
    section is _section_category_search's job.
    """
    # The keyword search is only a source of server-matched sentences;
    # candidates it misses are checked locally (see _keyword_sentences).
    kw_req = req.copy(update={"category": None, "count": max(req.count, tpc.API_PAGE_SIZE)})
    with ThreadPoolExecutor(max_workers=2) as pool:
        cand_future = pool.submit(_search_with_categories, req, groups, corpora, "document")
        kw_future = pool.submit(_run_search, _build_args(kw_req, corpora, "sentence"), corpora)
        candidates, args = cand_future.result()
        kw_docs = kw_future.result()

    server_sentences = {_identifier_key(d): d.get("matched_sentences") or [] for d in kw_docs}
    group_ids = [{m.group(1) for c in g for m in [tpc._CATEGORY_ID_RE.search(c)] if m} for g in groups]
    patterns = _local_keyword_patterns(req.keywords, req.case_sensitive)

    def _categories_ok(annotations, sections):
        if not exclude_types:
            return True
        all_ids = {a["onto_id"] for a in annotations}
        kept_ids = {a["onto_id"] for a in tpc._ca.exclude_sections(annotations, sections, exclude_types)}
        # Per group: True = found outside excluded sections, False = found
        # only inside them, None = not in this CAS2 at all (can't tell -- keep).
        status = [True if ids & kept_ids else (False if ids & all_ids else None) for ids in group_ids]
        if req.categories_and:
            return False not in status
        return any(s is not False for s in status)

    def _keyword_sentences(doc, sentences, sections):
        """Keyword sentences outside excluded sections; [] means none (drop)."""
        raw = server_sentences.get(_identifier_key(doc))
        if raw:
            return tpc._filter_matched_sentences(raw, sentences, sections, exclude_types)
        if patterns:
            local = [s for s in sentences if any(p.search(s.get("text") or "") for p in patterns)]
            kept = tpc._ca.exclude_sections(local, sections, exclude_types)
            if kept:
                return [s["text"] for s in kept]
        if not exclude_types:
            return []  # nothing to verify, just no snippet to show
        # Inconclusive locally (no match, stemmed forms, Lucene syntax, or a
        # restricted paper's blanked text): ask the server about this paper.
        raw = _server_sentences_for_paper(doc, kw_req)
        if raw is None:
            return None
        return tpc._filter_matched_sentences(raw, sentences, sections, exclude_types)

    def _check(doc):
        sentences, annotations, sections, _ = tpc._load_annotations(
            doc, args, include_related=req.related_synonyms)
        if sentences is None:  # no CAS2 available -- can't verify, keep
            doc["matched_sentences"] = server_sentences.get(_identifier_key(doc), [])
            return True
        if not _categories_ok(annotations, sections):
            return False
        doc["_sections_by_text"] = _sections_by_text(sentences, sections)  # for _top_sentences
        kept = _keyword_sentences(doc, sentences, sections)
        if kept is None:  # server check failed -- keep, with whatever we have
            doc["matched_sentences"] = server_sentences.get(_identifier_key(doc), [])
            return True
        if not kept and exclude_types:
            return False
        doc["matched_sentences"] = kept
        return True

    with ThreadPoolExecutor(max_workers=10) as pool:
        keep = list(pool.map(_check, candidates))
    return [d for d, k in zip(candidates, keep) if k], args


# --- Categories limited to one section -----------------------------------------
# The API's section-level indexes carry no category data (type=abstract with
# any category always returns 0), so a section-scoped search with categories
# runs at document level -- categories anywhere in the paper, plus the
# keywords in the chosen section if there are any -- and then checks each
# candidate's CAS2 annotations for the categories inside that section, in
# rank order, until enough papers pass. Where "inside" is decided:
#   - Body sections (Introduction, Results, ...): the annotation's position
#     in the CAS2 section map -- the same map the API's section keyword
#     indexes use (agreed 30/30 for Results and Methods, see below).
#   - Abstract: whether a term the category was annotated with in this paper
#     occurs in the .bib abstract, which is what the API's Abstract keyword
#     scope searches. Not the CAS2 "abstract" section: only 28 of 60 sampled
#     MaizeOA papers have one, and where present it overruns (9,099 chars
#     against a 1,866-char abstract in 10.1371/journal.pgen.1003477, running
#     on through the author summary and introduction). That section is the
#     fallback only for a paper with no .bib abstract.
#   - Title: the same term test against the title (CAS2 has no title section).
# Annotations are loaded with the request's RELATED-synonym setting, so this
# is also the EXACT-only gene check that filter_gene_category_results() does.
_SCOPE_LABELS = {"result": "results"}


def _term_in_text(term: str, text: str) -> bool:
    """Whether an annotated term occurs as a whole word in text.

    Case-insensitive, except that a term with capitals after its first letter
    (AS, ZmCCT9) must match its case -- otherwise "AS" matches every "as".
    """
    if not term or not text:
        return False
    flags = re.IGNORECASE if term[1:] == term[1:].lower() else 0
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, flags) is not None


def _section_category_search(req: SearchRequest, groups, corpora: List[str], exclude_types: set,
                             section: str, notes: list):
    """Categories (and keywords, if any) limited to one section.

    section is a SEARCH_SCOPES value other than "sentence". Returns
    (results, args). For a categories-only search each result's
    matched_sentences is set to the section's sentences carrying a category;
    keyword searches get keyword snippets later, in post_search.
    """
    kw_docs = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        cand_future = pool.submit(_search_with_categories, req.copy(update={"count": MAX_CANDIDATES}),
                                  groups, corpora, "document", notes, False)
        if req.keywords:
            kw_req = req.copy(update={"category": None, "count": MAX_CANDIDATES})
            kw_docs = pool.submit(_run_search, _build_args(kw_req, corpora, section), corpora).result()
        candidates, args = cand_future.result()
    if len(candidates) >= MAX_CANDIDATES or len(kw_docs or ()) >= MAX_CANDIDATES:
        notes.append(_candidates_note())
    if kw_docs is not None:
        in_section = {_identifier_key(d) for d in kw_docs}
        candidates = [d for d in candidates if _identifier_key(d) in in_section]

    group_ids = [{m.group(1) for c in g for m in [tpc._CATEGORY_ID_RE.search(c)] if m} for g in groups]

    def _check(doc):
        sentences, annotations, sections, limited = tpc._load_annotations(
            doc, args, include_related=req.related_synonyms)
        if sentences is None:
            return None  # no CAS2 -- can't tell
        annotations = tpc._ca.exclude_sections(annotations, sections, exclude_types)
        text = {"title": doc.get("title"), "abstract": _abstract_for(doc)}.get(section)
        if text and not limited:
            hits = [a for a in annotations if _term_in_text(a["term"], text)]
            parts = _SENTENCE_SPLIT_RE.split(text) if section == "abstract" else []
            snippets = [p for p in parts if any(_term_in_text(a["term"], p) for a in hits)]
        elif section == "title" or (section == "abstract"
                                    and not any(s["type"] == "abstract" for s in sections)):
            return None if limited else False  # limited: terms come back blank
        else:
            hits = [a for a in annotations
                    if section in tpc._ca.section_types_at(a["begin"], a["end"], sections)]
            snippets = [s["text"] for s in sentences
                        if any(a["begin"] < s["end"] and a["end"] > s["begin"] for a in hits)]
        found = {a["onto_id"] for a in hits}
        status = [bool(ids & found) for ids in group_ids]
        if not (all(status) if req.categories_and else any(status)):
            return False
        if not req.keywords:
            doc["matched_sentences"] = snippets
        return True

    kept, unverified = _take_verified(candidates, _check, req.count)
    if unverified:
        notes.append(f"{unverified} of these papers could not be checked for the categories in "
                     f"the {_SCOPE_LABELS.get(section, section)} and are shown anyway.")
    return kept, args


# --- Snippets for section-scoped keyword searches ------------------------------
# A section-scoped search (Abstract, Results, ...) comes back with no matched
# sentences -- the API only returns those for sentence-level ("Full text")
# queries. Rebuild them from the paper itself: the sentences in the chosen
# section that contain a keyword. Where that text comes from depends on how
# the API indexes the section (measured on 30 MaizeOA hits per scope):
#   - Abstract: the API searches the .bib abstract (30/30 hits contain the
#     keyword there), while 16/30 papers have no CAS2 "abstract" section at
#     all -- so Abstract snippets are split out of the .bib abstract text.
#   - Other sections: the CAS2 section map agreed with the API 30/30 for
#     Results and Methods, so snippets are the CAS2 sentences in that section.
# Title has nothing to rebuild (the title is already shown). Restricted papers
# whose CAS2 text comes back blanked just get no snippets.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _section_snippets(doc, args, section: str, terms: List[re.Pattern]) -> List[str]:
    def has_term(text):
        return any(t.search(text or "") for t in terms)

    if section == "abstract":
        return [s for s in _SENTENCE_SPLIT_RE.split(_abstract_for(doc) or "") if has_term(s)]
    # Annotations unused, so skip hygiene: it filters only annotations, never
    # sentences or sections, and the ontology-annotation panel loads its own.
    sentences, _, sections, _ = tpc._load_annotations(doc, args, hygiene=False)
    if sentences is None:
        return []
    return [s["text"] for s in sentences
            if section in tpc._ca.section_types_at(s["begin"], s["end"], sections)
            and has_term(s.get("text"))]


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

    try:
        corpora = req.corpora or _cached_list_corpora()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Textpresso API: {e}")

    groups = _category_groups(req) if req.category else None
    exclude_types = set(req.exclude_sections or ())
    notes = []  # caveats for the user (lower-ranked papers not checked, ...)
    # Categories with a single-section scope are checked per paper (see
    # _section_category_search). Full text with both query rows matches at
    # paper level, not same-sentence (see _paper_level_search). Both do their
    # own section exclusion.
    section_scoped = bool(groups) and query_type != "sentence"
    paper_level = bool(req.keywords and groups) and not section_scoped
    if section_scoped:
        results, args = _section_category_search(req, groups, corpora, exclude_types, query_type, notes)
    elif paper_level:
        results, args = _paper_level_search(req, groups, corpora, exclude_types)
    else:
        results, args = _search_with_categories(req, groups, corpora, query_type, notes)

    if exclude_types and query_type == "sentence" and not paper_level:
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
            # hygiene=False: annotations unused here (see _section_snippets).
            sentences, _, sections, _ = tpc._load_annotations(doc, args, hygiene=False)
            if sentences is not None:
                doc["matched_sentences"] = tpc._filter_matched_sentences(
                    doc.get("matched_sentences", []), sentences, sections, exclude_types)
                doc["_sections_by_text"] = _sections_by_text(sentences, sections)  # for _top_sentences

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
    # time rather than shipping everything over the wire unused. Restricted
    # papers get a much smaller cap (see MAX_SNIPPETS / _access_level above):
    # only the top few matched sentences, no full text.
    # Which sentences make the cut is ranked, not just the first N -- see
    # _top_sentences().
    terms = _search_terms(req.keywords)
    if terms and query_type not in ("sentence", "title"):
        with ThreadPoolExecutor(max_workers=10) as pool:
            snippets = list(pool.map(lambda d: _section_snippets(d, args, query_type, terms), results))
        for doc, sentences in zip(results, snippets):
            doc["matched_sentences"] = sentences

    for doc in results:
        doc["access"] = _access_level(doc.get("identifier") or "")  # open | publisher | restricted
        doc["oa_status"], doc["license"] = (v or None for v in _oa_info(doc.get("identifier") or ""))
        doc["open_access"] = doc["access"] == "open"
        doc["viewer_allowed"] = (_can_view_full_text(doc.get("identifier") or "")
                                 and _has_local_cas(doc.get("identifier") or ""))
        doc["abstract"] = _abstract_for(doc)
        sections_by_text = doc.pop("_sections_by_text", None)  # internal; not sent to the browser
        sentences = doc.get("matched_sentences")
        if sentences is not None:
            doc["matched_sentence_count"] = len(sentences)
            cap = MAX_SNIPPETS[doc["access"]]
            doc["matched_sentences"] = _top_sentences(
                sentences, cap, terms, doc.get("title"), doc["abstract"], sections_by_text)

    return {"count": len(results), "results": results, "notes": list(dict.fromkeys(notes))}


# --- Citation details (volume / issue / pages / real DOI) --------------------
# search_documents only returns an abbreviated journal name, and neither the
# CAS2 .bib files nor the MaizeGDB import CSV carry volume/issue/pages -- the
# .bib only has "citation|PMID: N". So the table view's citation line is
# filled in from PubMed esummary, batched (one request per page of results)
# and cached per PMID for the life of the process. This also gives the true
# DOI: Textpresso accessions replace "/" with "_", which can't be reversed
# reliably (10.1093_jxb_erq180 is really 10.1093/jxb/erq180).
#
# Best effort throughout: a doc with no local .bib, no PMID, or a failed
# PubMed call just gets no entry, and the frontend keeps its journal-only
# citation and a guessed DOI.
_EUTILS_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_PMID_RE = re.compile(r"PMID:\s*(\d+)")
_CITATION_CACHE = {}  # pmid -> citation dict, or None if PubMed had nothing


class CitationRequest(BaseModel):
    identifiers: List[str] = Field(..., max_length=200)


def _read_bib(identifier: str) -> dict:
    """The "key|value" lines of the .bib file next to a paper's CAS2 file.

    Keys seen: author, accession, type, title, journal, citation ("PMID: N"),
    year, abstract. Empty dict if there's no CAS_ROOT or no .bib.
    """
    if not CAS_ROOT:
        return {}
    # "MaizeOA/<acc>/<acc>.tpcas" -> CAS_ROOT/MaizeOpenAccess/<acc>/<acc>.bib
    id_path = Path(identifier)
    corpus = id_path.parts[0]
    local_corpus = _CORPUS_REMAP.get(corpus, corpus)
    bib = Path(CAS_ROOT) / local_corpus / Path(*id_path.parts[1:]).with_suffix(".bib")
    try:
        lines = bib.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    fields = {}
    for line in lines:
        key, sep, value = line.partition("|")
        if sep:
            fields.setdefault(key.strip(), value.strip())
    return fields


def _pmid_for_identifier(identifier: str) -> Optional[str]:
    m = _PMID_RE.search(_read_bib(identifier).get("citation", ""))
    return m.group(1) if m else None


def _fetch_pubmed_citations(pmids: List[str]):
    params = urllib.parse.urlencode({
        "db": "pubmed", "id": ",".join(pmids), "retmode": "json",
        "tool": "textpresso_maize_gui",
    })
    try:
        with urllib.request.urlopen(f"{_EUTILS_ESUMMARY}?{params}", timeout=15) as resp:
            result = json.load(resp).get("result", {})
    except (urllib.error.URLError, http.client.HTTPException, ValueError, OSError) as e:
        print(f"  [PubMed esummary failed: {e}]", file=sys.stderr)
        return  # leave uncached so a later request can retry
    for pmid in pmids:
        rec = result.get(pmid)
        if not rec or "error" in rec:
            _CITATION_CACHE[pmid] = None
            continue
        doi = next((a["value"] for a in rec.get("articleids", [])
                    if a.get("idtype") == "doi"), None)
        _CITATION_CACHE[pmid] = {
            "pmid": pmid,
            "journal": rec.get("source") or None,
            "volume": rec.get("volume") or None,
            "issue": rec.get("issue") or None,
            "pages": rec.get("pages") or None,
            "pubdate": rec.get("pubdate") or None,
            "doi": doi,
        }


@app.post("/api/citations")
def post_citations(req: CitationRequest):
    pmid_by_id = {i: _pmid_for_identifier(i) for i in req.identifiers}
    missing = sorted({p for p in pmid_by_id.values() if p and p not in _CITATION_CACHE})
    for start in range(0, len(missing), 200):
        _fetch_pubmed_citations(missing[start:start + 200])
    return {"citations": {i: _CITATION_CACHE.get(p) for i, p in pmid_by_id.items()
                          if p and _CITATION_CACHE.get(p)}}


# --- Full-text viewer (viewer.html) -----------------------------------------
# The whole paper -- text, pages, paragraphs, figures, sections, sentences and
# lexical annotations -- parsed from the local CAS2 file by casdoc.py, the
# same tree CAS_ROOT already points at for /api/annotate. There's no HTTP
# fallback: the live API has no endpoint returning the sofaString with
# annotation offsets (search_documents' include_fulltext is tag-stripped
# Lucene text with no positions), so without CAS_ROOT the viewer is off.
#
# Figures: tpcas-2/<corpus>/<acc>/images is a symlink into tpcas-1, which on
# the dev box can't be followed over the WSL UNC mount (and is empty there
# anyway). The same files, under the same names, are also written next to the
# source PDF in raw_files/pdf/<corpus>/<acc>/ -- so look in all three, then
# fall back to the live Textpresso Central URL (Viewer.cpp serves the same
# basename from /tpc/images/<corpus>/<acc>/images/).
def _sibling_root(env_var: str, *parts: str) -> Optional[Path]:
    if os.environ.get(env_var):
        return Path(os.environ[env_var])
    return Path(CAS_ROOT).parent.joinpath(*parts) if CAS_ROOT else None


TPCAS1_ROOT = _sibling_root("TEXTPRESSO_TPCAS1_ROOT", "tpcas-1")
RAW_PDF_ROOT = _sibling_root("TEXTPRESSO_RAW_PDF_ROOT", "raw_files", "pdf")
_LIVE_IMAGES_BASE = "{0.scheme}://{0.netloc}/tpc/images".format(urllib.parse.urlsplit(tpc.DEFAULT_URL))

_FIGURE_NAME_RE = re.compile(r"^[\w.\-]+\.(?:png|jpe?g|gif)$", re.IGNORECASE)
_PATH_PART_RE = re.compile(r"^[\w.\-]+$")

# Parsed documents, most recently used last. Parsing is fast locally but a
# 500KB gz read over the UNC mount isn't, and a reader paging back and forth
# between a few papers re-requests the same ones.
_DOC_CACHE = OrderedDict()
_DOC_CACHE_SIZE = 16
_DOC_CACHE_LOCK = threading.Lock()


def _split_identifier(identifier: str):
    """'MaizeOA/<acc>/<acc>.tpcas' (or with '//') -> (corpus, acc), or HTTP 400.

    Strict on purpose: both parts are joined onto filesystem paths below.
    """
    parts = [p for p in identifier.split("/") if p]
    if (len(parts) != 3 or parts[2] != f"{parts[1]}.tpcas"
            or not all(_PATH_PART_RE.match(p) and p not in (".", "..") for p in parts[:2])):
        raise HTTPException(status_code=400, detail=f"Not a Textpresso document identifier: {identifier!r}")
    return parts[0], parts[1]


def _has_local_cas(identifier: str) -> bool:
    """Whether /api/document can serve this paper (its CAS2 file is under CAS_ROOT).

    Not every searchable corpus is in every local CAS2 tree (on the dev box
    only MaizeOpenAccess is), so the search page only offers the viewer where it
    will actually open.
    """
    if not CAS_ROOT:
        return False
    try:
        corpus, acc = _split_identifier(identifier)
        local_corpus = _CORPUS_REMAP.get(corpus, corpus)
        return (Path(CAS_ROOT) / local_corpus / acc / f"{acc}.tpcas.gz").is_file()
    except (HTTPException, OSError):
        return False


# --- Browser caching for /api/annotate and /api/document ----------------------
# Both answers depend only on the paper's local files and on what this process
# loaded at start-up (code, italics index, class map, match dictionary). So
# their ETag is the file's mtime and size plus a token that changes on every
# restart, and "private, no-cache" lets the browser keep the response but ask
# each time; an unchanged paper gets an empty 304 before anything is parsed.
# Papers served over HTTP (no local CAS2) get no ETag and are not cached.
_PROCESS_TOKEN = f"{os.getpid()}-{time.time_ns()}"
_BROWSER_CACHE_HEADERS = {"Cache-Control": "private, no-cache"}


def _local_etag(identifier: str, with_bib: bool = False) -> Optional[str]:
    if not CAS_ROOT:
        return None
    try:
        corpus, acc = _split_identifier(identifier)
        base = Path(CAS_ROOT) / _CORPUS_REMAP.get(corpus, corpus) / acc
        stamps = [os.stat(base / f"{acc}.tpcas.gz")]
        if with_bib:
            try:
                stamps.append(os.stat(base / f"{acc}.bib"))
            except OSError:
                pass
    except (HTTPException, OSError):
        return None
    raw = "|".join([_PROCESS_TOKEN] + [f"{s.st_mtime_ns}:{s.st_size}" for s in stamps])
    return '"' + hashlib.sha1(raw.encode()).hexdigest() + '"'


def _not_modified(request: Request, etag: Optional[str]) -> bool:
    match = request.headers.get("if-none-match")
    return bool(etag and match and etag in (t.strip() for t in match.split(",")))


def _require_viewer_access(identifier: str):
    if not _can_view_full_text(identifier):
        raise HTTPException(status_code=403, detail="Full text is only available for open-access papers.")


def _annotation_key(a):
    return (a.get("begin"), a.get("end"), a.get("ontology"), a.get("category"))


@app.get("/api/document")
def get_document(identifier: str, request: Request, response: Response,
                 # Same filter as /api/annotate and the search results, from the
                 # same code (tpc.clean_local_annotations). hygiene=false is the
                 # viewer's "show unfiltered" toggle.
                 hygiene: bool = True, italics: bool = True):
    corpus, acc = _split_identifier(identifier)
    _require_viewer_access(identifier)
    if not CAS_ROOT:
        raise HTTPException(status_code=503, detail="Full text is unavailable: no local CAS2 tree "
                            "(set TEXTPRESSO_CAS_ROOT, see GUI_SETUP.md).")
    etag = _local_etag(identifier, with_bib=True)
    if etag:   # the same paper is a different answer with the filter off
        etag = etag[:-1] + f"-h{int(hygiene)}i{int(italics)}" + '"'
    if _not_modified(request, etag):
        return Response(status_code=304, headers={"ETag": etag, **_BROWSER_CACHE_HEADERS})
    if etag:
        response.headers.update({"ETag": etag, **_BROWSER_CACHE_HEADERS})

    local_corpus = _CORPUS_REMAP.get(corpus, corpus)
    key = f"{local_corpus}/{acc}|{int(hygiene)}{int(italics)}"
    with _DOC_CACHE_LOCK:
        if key in _DOC_CACHE:
            _DOC_CACHE.move_to_end(key)
            return _DOC_CACHE[key]

    path = Path(CAS_ROOT) / local_corpus / acc / f"{acc}.tpcas.gz"
    try:
        doc = casdoc.parse_cas_document(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No CAS2 file for {identifier}")
    except OSError as e:
        raise HTTPException(status_code=502, detail=f"Could not read CAS2 file for {identifier}: {e}")

    report = {}
    if hygiene:
        # Run the shared hygiene layer on the parsed CAS2 and keep only the
        # viewer annotations it keeps. Every channel is filtered (the viewer's
        # own checkboxes decide which are shown); nothing here is viewer-specific.
        sentences, annotations, sections = tpc._ca.parse_cas_file(str(path))
        kept, dropped = tpc.clean_local_annotations(
            identifier, annotations, sentences, sections,
            argparse.Namespace(cas_root=CAS_ROOT, url=tpc.DEFAULT_URL,
                               hygiene=True, italics=italics))
        report = tpc._hy.hygiene_report(kept, dropped)
        keep = {_annotation_key(a) for a in kept}
        doc["annotations"] = [a for a in doc["annotations"] if _annotation_key(a) in keep]
    doc["hygiene"] = _hygiene_block(hygiene, italics, len(doc["annotations"]), report)

    bib = _read_bib(f"{corpus}/{acc}/{acc}.tpcas")
    doc.update({
        "identifier": identifier,
        "corpus": corpus,
        "accession": acc,
        "metadata": {k: bib.get(k) for k in ("title", "author", "journal", "year", "type", "abstract")},
        "pmid": _pmid_for_identifier(f"{corpus}/{acc}/{acc}.tpcas"),
    })
    with _DOC_CACHE_LOCK:
        _DOC_CACHE[key] = doc
        while len(_DOC_CACHE) > _DOC_CACHE_SIZE:
            _DOC_CACHE.popitem(last=False)
    return doc


# raw_files/pdf/ holds images exactly as extracted from the PDF, before the
# pipeline's invert_img step. That step (agr_textpresso
# tpctools/CMYKInverter/CMYKModifier.cpp, run over every tpcas-1 images/ dir)
# negates every CMYK .jpg -- the PDF stores them with an inverting /Decode
# array, so served raw they show up as colour negatives. Do the same here,
# converting to RGB while we're at it, and cache the result since a figure is
# often several MB. None means "serve the file as-is" (not a CMYK JPEG, or
# Pillow not installed).
_CMYK_CACHE = OrderedDict()
_CMYK_CACHE_SIZE = 32
_CMYK_CACHE_LOCK = threading.Lock()


def _uninverted_cmyk_jpeg(path: Path) -> Optional[bytes]:
    if path.suffix.lower() not in (".jpg", ".jpeg"):
        return None
    key = str(path)
    with _CMYK_CACHE_LOCK:
        if key in _CMYK_CACHE:
            _CMYK_CACHE.move_to_end(key)
            return _CMYK_CACHE[key]
    try:
        from io import BytesIO
        from PIL import Image, ImageChops
        with Image.open(path) as im:
            if im.mode != "CMYK":
                result = None
            else:
                buf = BytesIO()
                ImageChops.invert(im).convert("RGB").save(buf, "JPEG", quality=90)
                result = buf.getvalue()
    except ImportError:
        return None
    except OSError as e:
        print(f"  [could not convert CMYK figure {path}: {e}]", file=sys.stderr)
        return None
    with _CMYK_CACHE_LOCK:
        _CMYK_CACHE[key] = result
        while len(_CMYK_CACHE) > _CMYK_CACHE_SIZE:
            _CMYK_CACHE.popitem(last=False)
    return result


@app.get("/api/figure")
def get_figure(identifier: str, name: str):
    corpus, acc = _split_identifier(identifier)
    local_corpus = _CORPUS_REMAP.get(corpus, corpus)
    if not _FIGURE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Not a figure file name: {name!r}")
    _require_viewer_access(identifier)

    cache_headers = {"Cache-Control": "public, max-age=86400"}
    candidates = [
        (root / local_corpus / acc / sub / name, raw)
        for root, sub, raw in ((Path(CAS_ROOT) if CAS_ROOT else None, "images", False),
                               (TPCAS1_ROOT, "images", False),
                               (RAW_PDF_ROOT, "", True))
        if root
    ]
    for path, raw in candidates:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue  # e.g. the tpcas-2 images symlink, unresolvable over the UNC mount
        if raw:
            fixed = _uninverted_cmyk_jpeg(path)
            if fixed is not None:
                return Response(fixed, media_type="image/jpeg", headers=cache_headers)
        return FileResponse(path, headers=cache_headers)
    return RedirectResponse(
        f"{_LIVE_IMAGES_BASE}/{urllib.parse.quote(corpus)}/{urllib.parse.quote(acc)}/images/"
        f"{urllib.parse.quote(name)}", status_code=307)


@app.get("/api/health")
def health():
    return {"status": "ok"}
