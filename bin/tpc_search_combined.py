#!/usr/bin/env python3
"""Search a Textpresso corpus, with optional CAS2 ontology annotation.

This is the single, standalone entry point for Textpresso search: plain
keyword/metadata/section search plus the CAS2 annotation modes below, all in
one script with no dependency on the older tpc_search.py / tpc_search_internal.py
pair (see the top of those files -- they're kept only for compatibility).

By default all data -- search results and CAS2 annotations alike -- is
fetched over HTTP, so this script works from anywhere with network access; no
server-side file access is required. Pass --cas-root to instead parse CAS2
files directly from a local directory (server-side use only).

API notes (search):
  - The 'corpora' list must be nested inside 'query', not at the top level.
  - 'include_match_sentences' is only valid when type == 'sentence'; sending it
    with any other type causes a 401 response (an API quirk, not an auth error).
  - Section-scoped types (abstract, result, etc.) return document-level hits;
    matched sentence text is only available with type == 'sentence'.

Annotation modes (CAS2-backed):

  --annotate
      After each result, append an ontology term summary showing all terms found
      in the paper, grouped by ontology (GO, PO, TO, MAIZE_GENES).  Compatible
      with both text and JSON output formats.

  --annotate-sentences
      Output full sentence-level annotation as JSON.  For each result paper the
      output contains:
        paper                    — metadata (title, author, year, journal, accession)
        search_matched_sentences — keyword-matched sentences from the API search
        annotated_sentences      — all sentences that have at least one ontology hit,
                                   each with its list of overlapping annotations
      Add --full-text to include every sentence in the paper, including those
      with no annotations.

  --exclude-type TYPE
      Exclude a CAS section type from results (repeatable), e.g. drop matches
      that come from the bibliography with --exclude-type references.
        --type sentence     filters the returned matched_sentences directly.
        --type document     drops a document entirely if every determinable
                             match falls within an excluded section (verified
                             via a supplementary --type sentence query scoped
                             to that document, not guessed at) -- kept if
                             match status can't be determined.
        --annotate /        filters the ontology summary / annotated_sentences
        --annotate-sentences by CAS2 section, independent of --type, since
                             these read the whole CAS2 file rather than the
                             search match itself.
      Requires papers to have been (re)tokenized after the section-detection
      fix; a no-op on older CAS2 files with no section data.

examples:
  %(prog)s -c MaizeTest100 "flowering time"
  %(prog)s -c MaizeTest100 --type abstract "drought"
  %(prog)s -c MaizeTest100 --exclude "Arabidopsis" "kernel weight"
  %(prog)s -c MaizeTest100 --author "Buckler" --year 2022 "GWAS"
  %(prog)s -c MaizeTest100 "anthocyanin" --annotate
  %(prog)s -c MaizeTest100 "anthocyanin" --annotate --format json
  %(prog)s -c MaizeTest100 "anthocyanin" --annotate-sentences
  %(prog)s -c MaizeTest100 "anthocyanin" --annotate-sentences --full-text
  %(prog)s -c MaizeTest100 "anthocyanin" --annotate-sentences --ontology GO --ontology PO
  %(prog)s -c MaizeTest100 --author "Buckler" --annotate
  %(prog)s -c MaizeTest100 "drought" --annotate-sentences \\
      --cas-root /data/textpresso/tpcas-2   # local-file fallback; inside the container
  %(prog)s -c MaizeTest100 --type sentence "MARK" --exclude-type references
  %(prog)s -c MaizeTest100 --type document "MARK" --exclude-type references
  %(prog)s -c MaizeTest100 "adh1" --annotate --exclude-type references --exclude-type acknowledgments
  %(prog)s --list-corpora
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

_BIN  = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_BIN, "..")

DEFAULT_URL = "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api"
# DEFAULT_URL = "http://localhost:18080/v1/textpresso/api"

# --cas-root fallback only (server-side, local-file parsing). Not used by the
# default HTTP path.
# Host-side path to the CAS2 files (the container mounts this at /data/textpresso).
# Pass --cas-root /data/textpresso/tpcas-2 when running inside the container.
DEFAULT_CAS_ROOT = "/home/ec2-user/agr_textpresso/.data/tpcas-2"

SEARCH_TYPES = [
    "sentence",
    "document",
    "abstract",
    "title",
    "introduction",
    "materials and methods",
    "result",
    "discussion",
    "conclusion",
    "background",
    "design",
    "acknowledgments",
    "references",
]

# Valid --exclude-type values: SEARCH_TYPES minus the ones that are query
# scopes rather than CAS section types ("sentence"/"document" describe what
# to search, not a section; "title" is a dedicated bib-info field, not
# produced by section detection at all).
SECTION_TYPES = [t for t in SEARCH_TYPES if t not in ("sentence", "document", "title")]


def _load_module(name, rel_path):
    """Load a sibling Python file as a module without triggering package __init__.py.

    Regular 'from textpresso_classifiers.casannot import ...' would execute
    textpresso_classifiers/__init__.py, which imports sklearn/numpy and fails
    in environments where those aren't installed.  importlib.util lets us load
    just the file we need.
    """
    path = os.path.join(_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load only the CAS2 parsing library -- everything else this script needs is
# defined below, so this file has no dependency on tpc_search.py.
_ca = _load_module("casannot", "textpresso_classifiers/casannot.py")


# ---------------------------------------------------------------------------
# Search (search_documents / available_corpora)
# ---------------------------------------------------------------------------

def build_query(args, corpora):
    """Build the JSON payload for the search_documents endpoint.

    args    — argparse Namespace; only the standard search attributes are read.
    corpora — list of corpus names (already resolved from --corpus or the API).
    """
    query = {
        "type": args.type,
        "corpora": corpora,   # must be inside 'query', not at top level
    }
    if args.keywords:
        query["keywords"] = args.keywords
    if args.exclude:
        query["exclude_keywords"] = args.exclude
    if args.author:
        query["author"] = args.author
        query["exact_match_author"] = args.exact_author
    if args.journal:
        query["journal"] = args.journal
        query["exact_match_journal"] = args.exact_journal
    if args.year:
        query["year"] = args.year
    if args.accession:
        query["accession"] = args.accession
    if args.paper_type:
        query["paper_type"] = args.paper_type
    if args.category:
        query["categories"] = args.category
        query["categories_and_ed"] = args.categories_and
    if args.case_sensitive:
        query["case_sensitive"] = True
    if args.sort_by_year:
        query["sort_by_year"] = True

    payload = {"query": query, "count": args.count}
    if args.type == "sentence":
        # include_match_sentences is only valid for sentence-type queries
        payload["include_match_sentences"] = True
    return payload


def search(payload, url=DEFAULT_URL):
    """POST a search payload and return the parsed JSON result list."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{url}/search_documents",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def list_corpora(url=DEFAULT_URL):
    """Return the list of corpus names available on the server."""
    with urllib.request.urlopen(f"{url}/available_corpora") as resp:
        return json.loads(resp.read())


def annotation_service_url(search_url):
    """Base URL of the cas_annotate_server.py sidecar, derived from --url.

    ".../v1/textpresso/api" -> ".../v1/textpresso" -- strips whatever the
    last path segment is, so a custom --url still resolves to the sibling
    /annotate and /category_search endpoints on the same host.
    """
    return search_url.rsplit("/", 1)[0]


def category_search(query, url=DEFAULT_URL, ontology=None, limit=10, relationship_types=None,
                     ancestor_relationship_types=None):
    """Look up candidate --category strings for a free-text query.

    relationship_types -- optional iterable of OBO relationship types (e.g.
    ["is_a", "part_of"]). When given, each match's descendants along those
    relationships are included too (see agr_textpresso's category_index.py).
    Omitted preserves prior literal-match-only behavior.

    ancestor_relationship_types -- same idea, mirrored: when given, each
    match's ancestors (parent terms) along those relationships are included
    too. Combine both to expand in both directions from the same query.

    Returns the server's ranked "matches" list (each a dict with id, name,
    category, ontology, matched_on, relationship_types,
    parent_relationship_types), or None if the lookup service itself
    couldn't be reached (network/service failure -- distinct from a query
    that legitimately has zero matches, which returns an empty list).
    """
    params = [("q", query)]
    params += [("ontology", o) for o in (ontology or ())]
    params += [("relationship_type", r) for r in (relationship_types or ())]
    params += [("ancestor_relationship_type", r) for r in (ancestor_relationship_types or ())]
    if limit:
        params.append(("limit", str(limit)))
    endpoint = f"{annotation_service_url(url)}/category_search?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(endpoint) as resp:
            return json.loads(resp.read())["matches"]
    except (urllib.error.URLError, urllib.error.HTTPError):
        return None


def expand_categories_by_relationship(categories, relationship_types, url=DEFAULT_URL,
                                       ancestor_relationship_types=None):
    """Expand --category values to include related terms along OBO relationship types.

    categories                  -- exact "name (ID)" strings, already validated by
                                    check_categories_or_exit().
    relationship_types          -- iterable of OBO relationship types (e.g. is_a,
                                    part_of) to expand into descendants (children);
                                    falsy/None skips descendant expansion.
    ancestor_relationship_types -- same idea, mirrored: expand into ancestors
                                    (parent terms) instead; falsy/None skips it.

    Both are no-ops when their respective argument is falsy; if both are
    falsy, categories is returned unchanged.

    For each category, looks up its ID via category_search() (which, given
    relationship_types/ancestor_relationship_types, returns the term plus its
    descendants/ancestors -- see agr_textpresso's category_index.py), and
    unions in every returned "category" string. Order: originals first, then
    newly discovered terms in the order returned, deduplicated throughout.

    Warns to stderr (doesn't block the search) for any requested relationship
    type that isn't actually available for that specific term in that
    direction -- there's no fixed vocabulary across GO/PO/TO (a type valid
    for one term can be absent on another), so a typo or a mismatched
    term/relationship-type pairing would otherwise silently expand into
    nothing with no indication why.
    """
    if not relationship_types and not ancestor_relationship_types:
        return categories

    expanded = list(dict.fromkeys(categories))
    seen = set(expanded)
    for cat in categories:
        m = _CATEGORY_ID_RE.search(cat)
        term_id = m.group(1) if m else cat
        matches = category_search(term_id, url=url, limit=100,
                                   relationship_types=relationship_types,
                                   ancestor_relationship_types=ancestor_relationship_types) or ()
        _warn_unavailable_relationship_types(
            cat, term_id, matches, relationship_types, ancestor_relationship_types)
        for match in matches:
            if match["category"] not in seen:
                seen.add(match["category"])
                expanded.append(match["category"])
    return expanded


def _warn_unavailable_relationship_types(cat, term_id, matches, relationship_types,
                                          ancestor_relationship_types):
    """Print a stderr warning for any requested relationship type that has no
    effect for this specific term, per the exact match's own
    relationship_types/parent_relationship_types (see category_index.py).
    Silent if the exact match isn't in matches (e.g. lookup service down) --
    same fail-open stance as check_categories_or_exit().
    """
    exact = next((m for m in matches if m["id"] == term_id), None)
    if exact is None:
        return

    def _warn(requested, available, direction, flag):
        unavailable = sorted(set(requested) - set(available or ()))
        if not unavailable:
            return
        available_note = ", ".join(available) if available else "(none -- no relationships in this direction)"
        print(f'Warning: {flag} {", ".join(unavailable)} -- "{cat}" has no {direction} via '
              f'{"/".join(unavailable)}, so this contributes nothing for it. '
              f'Available for this term: {available_note}', file=sys.stderr)

    if relationship_types:
        _warn(relationship_types, exact.get("relationship_types"),
              "children", "--expand-relationship-type")
    if ancestor_relationship_types:
        _warn(ancestor_relationship_types, exact.get("parent_relationship_types"),
              "parents", "--expand-ancestor-relationship-type")


def format_category_matches(matches):
    """Render a category_search() match list as example --category lines."""
    return "\n".join(f'  --category "{m["category"]}"   ({m["ontology"]})' for m in matches)


def check_categories_or_exit(categories, url, parser):
    """Validate each --category value against the live category index.

    --category requires the *exact* stored "name (ID)" string (see "Other
    notes" in docs/TPC_SEARCH_GUIDE.md) -- a near-miss doesn't reliably fail,
    it can silently over-match or under-match depending on what else is in
    the corpus's category list. So rather than risk running a query on a
    guessed string, this blocks and prints suggestions for every mismatched
    value, requiring the user to re-run with an exact match.

    Fails open (no block, no output) if the lookup service itself is
    unreachable -- an infrastructure problem shouldn't prevent an otherwise
    normal search from running.
    """
    problems = []
    for value in categories:
        matches = category_search(value, url, limit=8)
        if matches is None:
            continue  # lookup service unreachable -- don't block on it
        if any(m["category"] == value for m in matches):
            continue  # exact match, nothing to report
        problems.append((value, matches))

    if not problems:
        return

    lines = []
    for value, matches in problems:
        if matches:
            lines.append(f'--category "{value}" does not exactly match a known category. '
                         f'Closest matches:')
            lines.append(format_category_matches(matches))
            lines.append(f'Example: --category "{matches[0]["category"]}"')
        else:
            lines.append(f'--category "{value}" has no matches found. Try a '
                         f'different word with bin/tpc_category_search.py "<term>", or '
                         f'drop --category and search by keyword instead, e.g.:\n'
                         f'  python3 bin/tpc_search_combined.py -c <corpus> "{value}"')
        lines.append("")
    parser.error("\n" + "\n".join(lines).rstrip() +
                 "\n\nTip: python3 bin/tpc_category_search.py \"<term>\" searches the "
                 "ontology directly for candidate --category strings.")


def add_search_args(parser):
    """Add the standard search arguments to an argparse parser."""
    parser.add_argument("keywords", nargs="?",
                        help="Search keywords (AND/OR supported, e.g. \"maize AND drought\")")
    parser.add_argument("-c", "--corpus", dest="corpora", action="append", metavar="CORPUS",
                        help="Corpus to search (repeatable). Default: all available.")
    parser.add_argument("--count", type=int, default=50,
                        help="Max results (default: 50, API hard max: 200)")
    parser.add_argument("--type", default="sentence", metavar="TYPE",
                        choices=SEARCH_TYPES,
                        help="Search scope (default: sentence).")
    parser.add_argument("--exclude", metavar="KEYWORDS",
                        help="Keywords to exclude from results")
    parser.add_argument("--case-sensitive", action="store_true",
                        help="Case-sensitive keyword matching")
    parser.add_argument("--author", metavar="NAME",
                        help="Filter by author name")
    parser.add_argument("--exact-author", action="store_true",
                        help="Require exact author match (default: substring)")
    parser.add_argument("--journal", metavar="NAME",
                        help="Filter by journal name")
    parser.add_argument("--exact-journal", action="store_true",
                        help="Require exact journal match (default: substring)")
    parser.add_argument("--year", metavar="YEAR",
                        help="Filter by publication year (e.g. 2022)")
    parser.add_argument("--accession", metavar="ID",
                        help="Filter by accession / DOI")
    parser.add_argument("--paper-type", metavar="TYPE",
                        help="Filter by paper type (e.g. Journal_article, Review)")
    parser.add_argument("--category", action="append", metavar="CATEGORY",
                        help="Restrict to ontology category (repeatable). Must be the "
                             "exact stored \"name (ID)\" string, e.g. \"seed (PO:0009010)\" -- "
                             "a non-exact value is rejected with suggestions rather than run "
                             "(see check_categories_or_exit()). Use bin/tpc_category_search.py "
                             "to look one up.")
    parser.add_argument("--categories-and", action="store_true",
                        help="Require ALL categories to match (default: ANY)")
    parser.add_argument("--expand-relationship-type", action="append", metavar="TYPE",
                        dest="expand_relationship_types",
                        help="Also search each --category's descendants (child terms) "
                             "along this OBO relationship (repeatable), e.g. "
                             "--expand-relationship-type is_a --expand-relationship-type "
                             "part_of. Default: --category matches only the exact term, "
                             "no ontology-graph expansion.")
    parser.add_argument("--expand-ancestor-relationship-type", action="append", metavar="TYPE",
                        dest="expand_ancestor_relationship_types",
                        help="Mirror of --expand-relationship-type: also search each "
                             "--category's ancestors (parent terms) along this OBO "
                             "relationship (repeatable). Combine with "
                             "--expand-relationship-type to expand both directions at once.")
    parser.add_argument("--sort-by-year", action="store_true",
                        help="Sort results by year instead of relevance score")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format (default: text)")
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"API base URL (default: {DEFAULT_URL})")
    parser.add_argument("--list-corpora", action="store_true",
                        help="List available corpora and exit")


def validate_search_args(args, parser):
    """Require at least one filtering criterion (keyword or metadata field)."""
    if not args.keywords and not args.author and not args.journal \
            and not args.year and not args.accession and not args.category:
        parser.error("provide at least one of: keywords, --author, --journal, "
                     "--year, --accession, --category")


# ---------------------------------------------------------------------------
# CAS2 helpers
# ---------------------------------------------------------------------------

def _annotate_endpoint_url(search_url):
    """Derive the cas_annotate_server.py endpoint URL from the search --url.

    ".../v1/textpresso/api" -> ".../v1/textpresso/annotate" -- replaces
    whatever the last path segment is, so a custom --url still resolves to a
    sibling endpoint on the same host.
    """
    return search_url.rsplit("/", 1)[0] + "/annotate"


def _load_annotations(doc, args, ontology_filter=None, include_related=False):
    """Get (sentences, annotations, sections) for a search result document.

    Returns (None, None, None) if the CAS2 data can't be found. Neither
    sentences nor annotations are filtered by section here -- callers apply
    _ca.exclude_sections() themselves, since some uses (e.g. mapping raw API
    sentence strings back to a CAS2 position) need the unfiltered sentence
    list first.
    ontology_filter, if given, is a set of ontology labels (e.g. {'GO', 'PO'})
    used to discard annotations from other ontologies before returning.
    When ontology_filter is None and include_related is False (the default),
    annotations matched via RELATED synonyms (ontology label ending in '_RELATED')
    are excluded.  Pass include_related=True or set ontology_filter explicitly to
    control RELATED synonym visibility.

    By default this fetches from cas_annotate_server.py over HTTP (works from
    anywhere with network access). If args.cas_root is set, it instead parses
    the CAS2 file directly from that local directory (server-side only).
    """
    identifier = doc.get("identifier", "")

    if args.cas_root:
        path = _ca.identifier_to_cas_path(identifier, args.cas_root)
        if not os.path.exists(path):
            print(f"  [CAS2 not found: {path}]", file=sys.stderr)
            return None, None, None
        sentences, annotations, sections = _ca.parse_cas_file(path)
        if ontology_filter:
            annotations = [a for a in annotations if a["ontology"] in ontology_filter]
        elif not include_related:
            annotations = [a for a in annotations
                           if not a["ontology"].endswith("_RELATED")]
        return sentences, annotations, sections

    params = [("identifier", identifier)]
    params += [("ontology", o) for o in (ontology_filter or ())]
    if include_related:
        params.append(("related_synonyms", "1"))
    url = f"{_annotate_endpoint_url(args.url)}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  [CAS2 annotation not found for: {identifier}]", file=sys.stderr)
        else:
            print(f"  [annotation API error {e.code}: {e.reason}]", file=sys.stderr)
        return None, None, None
    except urllib.error.URLError as e:
        print(f"  [annotation API connection failed: {e.reason}]", file=sys.stderr)
        return None, None, None
    return data["sentences"], data["annotations"], data["sections"]


def _normalize_text(s):
    return " ".join(s.split())


_CATEGORY_ID_RE = re.compile(r"\(([A-Za-z]+:\d+)\)")


def _gene_category_onto_ids(categories):
    """Return the onto_ids from --category values that are maize-gene categories.

    Only MAIZE_GENES/MAIZE_GENES_RELATED categories have an EXACT-vs-RELATED
    distinction (see casannot.py's _ONTOLOGY_PREFIXES) -- GO/PO/TO categories
    don't, so those are left alone by filter_gene_category_results() below.
    """
    ids = []
    for cat in categories or []:
        m = _CATEGORY_ID_RE.search(cat)
        if m and re.match(r"tpzma?:", m.group(1)):
            ids.append(m.group(1))
    return ids


def filter_gene_category_results(results, args):
    """Work around a known --category bug (see Laura_work_updates_log.md,
    2026-08-17): the underlying Lucene search can't distinguish EXACT from
    RELATED gene-synonym matches, because the query phrase never includes the
    'EXACT:'/'RELATED:' prefix stored in the index -- so a plain
    --category "cct1 (tpzm:0010325)" search silently includes RELATED-only
    matches (e.g. papers only mentioning "ZmCCT10") even without
    --related-synonyms, contrary to the documented EXACT-by-default design.

    This is a client-side patch, not a real fix: it re-checks each result's
    actual annotations (the same data --annotate reads) and drops any whose
    only match to a requested gene category is RELATED, unless
    --related-synonyms was passed. The proper fix belongs in the search
    query itself (agr_textpresso's DataStructures.cpp), which would avoid
    the extra --annotate lookup per result this requires -- see the log
    entry for why that wasn't done today.

    Only applies to maize-gene categories (tpzm:/tpzma: IDs); GO/PO/TO
    categories have no EXACT/RELATED distinction and are returned as-is.
    Known limitation: with multiple --category values mixing gene and
    non-gene categories under --categories-and, this only re-checks the gene
    ones -- the non-gene ones are trusted as already correctly matched by
    search.
    """
    gene_ids = _gene_category_onto_ids(args.category)
    if not gene_ids:
        return results

    ontology_filter = ({"MAIZE_GENES", "MAIZE_GENES_RELATED"}
                        if args.related_synonyms else {"MAIZE_GENES"})
    require_all = args.categories_and

    kept = []
    for doc in results:
        _, annotations, _ = _load_annotations(
            doc, args, ontology_filter=ontology_filter, include_related=args.related_synonyms)
        if annotations is None:
            kept.append(doc)  # can't verify -- keep rather than risk dropping a real match
            continue
        matched_ids = {a["onto_id"] for a in annotations}
        hits = [gid in matched_ids for gid in gene_ids]
        if all(hits) if require_all else any(hits):
            kept.append(doc)
    return kept


def _filter_matched_sentences(raw_sentences, cas_sentences, sections, exclude_types):
    """Drop API-returned matched-sentence strings that fall in an excluded section.

    The REST API returns matched sentences as plain text with no position
    info, so each string is mapped back to a CAS2 sentence by exact text
    match (after whitespace normalization) to recover its begin/end offset.
    A sentence that can't be matched unambiguously (zero or multiple CAS2
    sentences with the same text) is kept rather than guessed at -- avoids
    silently over-filtering on an uncertain match.
    """
    if not exclude_types:
        return raw_sentences
    by_text = {}
    for cs in cas_sentences:
        by_text.setdefault(_normalize_text(cs["text"]), []).append(cs)
    kept = []
    for raw in raw_sentences:
        candidates = by_text.get(_normalize_text(raw), [])
        if len(candidates) != 1:
            kept.append(raw)  # unmatched or ambiguous -> keep
            continue
        cs = candidates[0]
        if not (_ca.section_types_at(cs["begin"], cs["end"], sections) & exclude_types):
            kept.append(raw)
    return kept


def _document_has_match_outside_excluded(doc, args, exclude_types):
    """For a --type document result, decide whether to keep it under --exclude-type.

    --type document has no matched_sentences to filter individually (the API
    only returns those for --type sentence), so instead this drops the whole
    document if every determinable match is within an excluded section --
    i.e. re-runs the same search scoped to just this document with
    --type sentence (to get the server's own real matched-sentence
    positions), then checks each match's CAS2 section membership the same
    way _filter_matched_sentences does. Returns True (keep) if at least one
    match falls outside every excluded section, or if match status can't be
    determined (missing accession/corpus, API error, no CAS2 file) -- keep
    rather than risk hiding a document with a real match elsewhere.
    """
    identifier = doc.get("identifier", "")
    accession = doc.get("accession")
    # The API has been observed to return identifiers with either one or two
    # slashes after the corpus name (e.g. "MaizeTest100/acc/acc.tpcas" from
    # --type document vs "MaizeTest100//acc/acc.tpcas" elsewhere) -- split on
    # a single "/" so the corpus name (the first component either way) is
    # extracted reliably regardless of which format this response used.
    corpus = identifier.split("/")[0] if identifier else None
    if not accession or not corpus:
        return True

    sent_args = argparse.Namespace(**vars(args))
    sent_args.type = "sentence"
    sent_args.accession = accession
    sent_args.count = 200
    payload = build_query(sent_args, [corpus])
    try:
        sub_results = search(payload, url=args.url) or []
    except (urllib.error.HTTPError, urllib.error.URLError):
        return True

    matched = []
    for d in sub_results:
        matched.extend(d.get("matched_sentences", []))
    if not matched:
        return True

    sentences, _, sections = _load_annotations(doc, args)
    if sentences is None:
        return True

    return len(_filter_matched_sentences(matched, sentences, sections, exclude_types)) > 0


def _ref(doc):
    """Format a one-line reference string for a result document."""
    return (
        f"{doc.get('author', '?')} ({doc.get('year', '?')}). "
        f"{doc.get('title', '?')}. "
        f"{doc.get('journal', '?')}. "
        f"[{doc.get('accession', doc.get('identifier', '?'))}]"
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_results(results, args):
    """Dispatch to the appropriate output mode based on args flags."""
    if not results:
        print("No results.")
        return

    ontology_filter = set(args.ontology) if args.ontology else None
    include_related = args.related_synonyms
    exclude_types = set(args.exclude_type) if args.exclude_type else None

    # --type document has no per-sentence matches to filter (the API only
    # returns matched_sentences for --type sentence), so drop the whole
    # document instead if every determinable match is within an excluded
    # section -- see _document_has_match_outside_excluded for how "every
    # determinable match" is verified precisely via a supplementary
    # --type sentence query, rather than guessed at.
    if exclude_types and args.type == "document":
        results = [d for d in results
                  if _document_has_match_outside_excluded(d, args, exclude_types)]
        if not results:
            print("No results.")
            return

    # Pre-filter each doc's API-returned matched_sentences (sentence-type
    # queries only) in place, before dispatching to any output mode below --
    # this way every mode (text, json, --annotate, --annotate-sentences) sees
    # already-excluded matched_sentences without duplicating the filtering
    # logic per branch. Ontology annotations and --annotate-sentences'
    # per-sentence annotation lists are filtered separately below, since they
    # need the CAS2 data loaded again with ontology_filter/include_related
    # applied.
    if exclude_types and args.type == "sentence":
        for doc in results:
            sentences, _, sections = _load_annotations(doc, args)
            if sentences is not None:
                doc["matched_sentences"] = _filter_matched_sentences(
                    doc.get("matched_sentences", []), sentences, sections, exclude_types)

    if args.annotate_sentences:
        output = []
        for doc in results:
            sentences, annotations, sections = _load_annotations(
                doc, args, ontology_filter, include_related)
            annotated = []
            if sentences is not None:
                if exclude_types:
                    sentences = _ca.exclude_sections(sentences, sections, exclude_types)
                    annotations = _ca.exclude_sections(annotations, sections, exclude_types)
                all_enriched = _ca.annotate_sentences(sentences, annotations)
                # Default: only sentences that have at least one annotation.
                # --full-text: all sentences, including unannotated ones.
                annotated = all_enriched if args.full_text else [
                    s for s in all_enriched if s["annotations"]
                ]
            meta = {k: doc.get(k, "") for k in
                    ("identifier", "title", "author", "year", "journal", "accession")}
            entry = {"paper": meta, "annotated_sentences": annotated}
            if args.type == "sentence":
                entry["search_matched_sentences"] = doc.get("matched_sentences", [])
            output.append(entry)
        json.dump(output, sys.stdout, indent=2)
        print()
        return

    # --annotate with JSON output: add ontology_summary key to each result doc
    if args.format == "json":
        if args.annotate:
            for doc in results:
                _, annotations, sections = _load_annotations(
                    doc, args, ontology_filter, include_related)
                if exclude_types and annotations is not None:
                    annotations = _ca.exclude_sections(annotations, sections, exclude_types)
                doc["ontology_summary"] = (
                    _ca.summarize_by_ontology(annotations)
                    if annotations is not None else {}
                )
        json.dump(results, sys.stdout, indent=2)
        print()
        return

    # Text output (with or without --annotate)
    for i, doc in enumerate(results, 1):
        print(f"[{i}] {_ref(doc)}")
        for s in doc.get("matched_sentences", []):
            print(f"  - {s.strip()}")
        if args.annotate:
            _, annotations, sections = _load_annotations(
                doc, args, ontology_filter, include_related)
            if annotations is not None:
                if exclude_types:
                    annotations = _ca.exclude_sections(annotations, sections, exclude_types)
                summary = _ca.summarize_by_ontology(annotations)
                print("  Ontology annotations:")
                for ontology, terms in summary.items():
                    print(f"    {ontology}: {', '.join(terms)}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Search Textpresso, with optional CAS2 ontology annotation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Standard search arguments
    add_search_args(parser)

    ann = parser.add_argument_group(
        "CAS2 annotation (fetched over HTTP from --url by default; "
        "--cas-root for local-file fallback)")
    ann.add_argument("--annotate", action="store_true",
                     help="Append ontology term summary per paper to each result")
    ann.add_argument("--annotate-sentences", action="store_true",
                     help="Output full sentence-level annotation as JSON")
    ann.add_argument("--full-text", action="store_true",
                     help="With --annotate-sentences: include unannotated sentences too")
    ann.add_argument("--related-synonyms", action="store_true",
                     help="Include annotations matched via RELATED synonyms "
                          "(requires Textpresso rebuilt with RELATED synonym indexing; "
                          "ignored when --ontology is specified)")
    ann.add_argument("--ontology", action="append", metavar="NAME",
                     choices=["GO", "PO", "TO", "MAIZE_GENES", "MAIZE_GENES_RELATED", "OTHER"],
                     help="Restrict annotations to specific ontologies (repeatable); "
                          "use MAIZE_GENES for EXACT-synonym matches only, "
                          "MAIZE_GENES_RELATED for RELATED-synonym matches only, "
                          "or both together")
    ann.add_argument("--cas-root", default=None, metavar="PATH",
                     help="Bypass the network annotation endpoint and parse CAS2 "
                          "files directly from this local root directory instead "
                          "(server-side use only, e.g. "
                          f"{DEFAULT_CAS_ROOT} on the host, or "
                          "/data/textpresso/tpcas-2 inside the container). "
                          "Default: unset, i.e. fetch over HTTP from --url -- "
                          "works without local file access.")
    ann.add_argument("--exclude-type", action="append", metavar="TYPE",
                     choices=_ca.SECTION_TYPES,
                     help="Exclude results from this CAS section type (repeatable), "
                          "e.g. --type sentence --exclude-type references. Filters "
                          "matched_sentences, --annotate's ontology summary, and "
                          "--annotate-sentences output alike. Requires papers to have "
                          "been (re)tokenized after the section-detection fix (see "
                          "Laura_work_updates_log.md, 2026-07-13) -- has no effect on "
                          "older CAS2 files with no section annotations.")

    args = parser.parse_args()

    try:
        if args.list_corpora:
            for c in list_corpora(args.url):
                print(c)
            return

        validate_search_args(args, parser)
        if args.category:
            check_categories_or_exit(args.category, args.url, parser)
            args.category = expand_categories_by_relationship(
                args.category, args.expand_relationship_types, args.url,
                args.expand_ancestor_relationship_types)
        corpora = args.corpora or list_corpora(args.url)
        payload = build_query(args, corpora)
        results = search(payload, url=args.url)
        if args.category:
            results = filter_gene_category_results(results, args)
        print_results(results, args)

    except urllib.error.HTTPError as e:
        print(f"API error {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection failed: {e.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
