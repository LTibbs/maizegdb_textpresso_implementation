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
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
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


def _load_annotations_with_retry(doc, args, ontology_filter=None, include_related=False, retries=4):
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
    """
    if getattr(args, "cas_root", None) and _has_local_cas(doc.get("identifier") or ""):
        return _orig_load_annotations(doc, args, ontology_filter, include_related)
    http_args = argparse.Namespace(**{**vars(args), "cas_root": None})
    if _sidecar_is_down():
        return None, None, None, False
    for attempt in range(retries):
        try:
            result = _orig_load_annotations(doc, http_args, ontology_filter, include_related)
            _sidecar_note(result[0] is not None)
            return result
        except (http.client.IncompleteRead, ConnectionError) as e:
            if attempt == retries - 1:
                print(f"  [annotation fetch failed after {retries} attempts: {e}]", file=sys.stderr)
                _sidecar_note(False)
                return None, None, None, False
            time.sleep(0.3)
    return None, None, None, False


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
# Per-paper access level, from what the PDF pipeline records for every paper
# it fetched (maize_pipeline/pipeline/download_manifest.csv; covers all 1438
# MaizeOA papers): Unpaywall's oa_status, plus the reuse licence Europe PMC
# reports (epmc_license, written by the pipeline's refresh_licenses.py and
# re-checked each run, so delayed licences are picked up). Unpaywall alone
# isn't enough: it calls journals that relicense on a delay (Genome Research,
# J Cell Biol) "bronze" even once the article is CC BY-NC, and has no status
# at all for some fully CC BY papers (PLoS ONE, microPublication Biology).
# TEXTPRESSO_OA_MANIFEST overrides the path. Three levels:
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
    "TEXTPRESSO_OA_MANIFEST",
    "//wsl.localhost/Ubuntu/home/ltibbs/maize_pipeline/pipeline/download_manifest.csv"))
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
                print(f"  [OA manifest not readable ({e}); treating every paper as restricted]",
                      file=sys.stderr)
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
    sentences, annotations, sections, _ = tpc._load_annotations(
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


# --- Abstracts from our own corpus metadata CSVs -----------------------------
# search_documents returns no abstract, so the GUI's Abstract panel reads it
# from the per-corpus metadata CSVs the corpora were built from
# (sorghumbase_textpresso_implementation/metadata/<Corpus>_papers.csv, columns
# doi/pubmed_id/title/abstract/...) -- deliberately not PubMed, so the text
# shown is exactly what was ingested. TEXTPRESSO_METADATA_DIR overrides the
# directory. Keyed by DOI in Textpresso's accession form ("/" -> "_",
# lowercased) plus PMID; every CSV is merged into one lookup, since the same
# paper can appear in several corpora (MaizeTest is a superset of
# MaizeTest100) and only some of those rows carry an abstract. Loaded once on
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


def _run_search(args: argparse.Namespace, corpora: List[str]) -> list:
    # (Double-encoded author/title/journal metadata is already repaired inside
    # tpc.search() -- see fix_mojibake() in tpc_search_combined.py.)
    try:
        return tpc.search(tpc.build_query(args, corpora), url=tpc.DEFAULT_URL) or []
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


def _search_with_categories(req: SearchRequest, groups, corpora: List[str], query_type: str):
    """Run the search for req, honoring any/all across category groups.

    Returns (results, args). groups is _category_groups(req), or None when
    the request has no categories.
    """
    if not groups:
        args = _build_args(req, corpora, query_type)
        return _run_search(args, corpora), args
    if req.categories_and and len(groups) > 1 and any(len(g) > 1 for g in groups):
        return _search_category_groups(req, groups, corpora, query_type)
    flat_req = req.copy(update={"category": list(dict.fromkeys(c for g in groups for c in g))})
    args = _build_args(flat_req, corpora, query_type)
    return tpc.filter_gene_category_results(_run_search(args, corpora), args), args


def _search_category_groups(req: SearchRequest, groups, corpora: List[str], query_type: str):
    """"All of these categories" when ancestor/descendant expansion is also on.

    Expansion turns each category into a group (the term plus its ancestors/
    descendants) that should match if ANY member does, while "all" should
    require EVERY group. The API can't express that in one query: flattening
    every group into one list with categories_and_ed=true would demand every
    expanded term at once. Instead, run one OR'd search per group (in
    parallel) and keep only the papers found in every group, in the first
    group's relevance order.

    Each group search fetches the API max (200) so the intersection isn't cut
    short by a smaller page size -- but a paper ranked past 200 in any one
    group can still be missed.
    """
    def _one_group(group):
        group_req = req.copy(update={"category": group, "categories_and": False, "count": 200})
        group_args = _build_args(group_req, corpora, query_type)
        docs = _run_search(group_args, corpora)
        return tpc.filter_gene_category_results(docs, group_args), group_args

    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        results = list(pool.map(_one_group, groups))

    common = set.intersection(*({_identifier_key(d) for d in docs} for docs, _ in results))
    first_docs, first_args = results[0]
    kept = [d for d in first_docs if _identifier_key(d) in common][:req.count]
    # Later steps only use args for CAS2/annotation lookups (cas_root, url),
    # which are the same for every group.
    return kept, first_args


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


def _paper_level_search(req: SearchRequest, groups, corpora: List[str], exclude_types: set,
                        query_type: str):
    """Keywords AND categories in the same paper, with section exclusion.

    The search-scope dropdown applies to the keywords only: query_type is
    "sentence" for Full text, or a section name (abstract, title, ...).
    Categories always match anywhere in the paper.

    Returns (results, args). For Full text, each result's matched_sentences is
    set to its keyword sentences outside the excluded sections (for snippets);
    section-scoped searches have no matched sentences to show.
    """
    kw_req = req.copy(update={"category": None, "count": 200})
    with ThreadPoolExecutor(max_workers=2) as pool:
        cand_future = pool.submit(_search_with_categories, req, groups, corpora, "document")
        kw_future = pool.submit(_run_search, _build_args(kw_req, corpora, query_type), corpora)
        candidates, args = cand_future.result()
        kw_docs = kw_future.result()

    if query_type != "sentence":
        # Section scope: candidates have the keywords somewhere and the
        # categories somewhere; also require the keywords in the chosen
        # section. (Can't ask the API in one query -- its section-level
        # indexes carry no category data, so e.g. type=abstract with any
        # category always returns 0.) Limited to the keyword search's top 200.
        in_section = {_identifier_key(d) for d in kw_docs}
        candidates = [d for d in candidates if _identifier_key(d) in in_section]
        if not exclude_types:
            return candidates, args

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
        if query_type != "sentence":
            return True  # keywords are in a section that's never excluded
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
    sentences, _, sections, _ = tpc._load_annotations(doc, args)
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
    # The scope dropdown applies to keywords only. Category annotations only
    # exist in the sentence/document indexes (any section type returns 0), so
    # a categories-only search ignores the scope and matches at sentence level.
    if groups and not req.keywords:
        query_type = "sentence"
    # Both query rows: match at paper level, not same-sentence (see
    # _paper_level_search), which also does its own section exclusion.
    paper_level = bool(req.keywords and groups)
    if paper_level:
        results, args = _paper_level_search(req, groups, corpora, exclude_types, query_type)
    else:
        results, args = _search_with_categories(req, groups, corpora, query_type)

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
            sentences, _, sections, _ = tpc._load_annotations(doc, args)
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

    return {"count": len(results), "results": results}


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
    # "MaizeOA/<acc>/<acc>.tpcas" -> CAS_ROOT/MaizeOA/<acc>/<acc>.bib
    bib = Path(CAS_ROOT) / Path(identifier).with_suffix(".bib")
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
    only MaizeOA is), so the search page only offers the viewer where it
    will actually open.
    """
    if not CAS_ROOT:
        return False
    try:
        corpus, acc = _split_identifier(identifier)
        return (Path(CAS_ROOT) / corpus / acc / f"{acc}.tpcas.gz").is_file()
    except (HTTPException, OSError):
        return False


def _require_viewer_access(identifier: str):
    if not _can_view_full_text(identifier):
        raise HTTPException(status_code=403, detail="Full text is only available for open-access papers.")


@app.get("/api/document")
def get_document(identifier: str):
    corpus, acc = _split_identifier(identifier)
    _require_viewer_access(identifier)
    if not CAS_ROOT:
        raise HTTPException(status_code=503, detail="Full text is unavailable: no local CAS2 tree "
                            "(set TEXTPRESSO_CAS_ROOT, see GUI_SETUP.md).")

    key = f"{corpus}/{acc}"
    with _DOC_CACHE_LOCK:
        if key in _DOC_CACHE:
            _DOC_CACHE.move_to_end(key)
            return _DOC_CACHE[key]

    path = Path(CAS_ROOT) / corpus / acc / f"{acc}.tpcas.gz"
    try:
        doc = casdoc.parse_cas_document(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No CAS2 file for {identifier}")
    except OSError as e:
        raise HTTPException(status_code=502, detail=f"Could not read CAS2 file for {identifier}: {e}")

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
    if not _FIGURE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Not a figure file name: {name!r}")
    _require_viewer_access(identifier)

    cache_headers = {"Cache-Control": "public, max-age=86400"}
    candidates = [
        (root / corpus / acc / sub / name, raw)
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
