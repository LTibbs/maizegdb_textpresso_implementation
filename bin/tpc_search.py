#!/usr/bin/env python3
"""Search a Textpresso corpus and print matched sentences with reference metadata.

Talks to the Textpresso REST API at DEFAULT_URL.  No local file access required,
so this script works for any user with network access to the public server.

For server-side use with CAS2 ontology annotation, see tpc_search_internal.py.

API notes:
  - The 'corpora' list must be nested inside 'query', not at the top level.
  - 'include_match_sentences' is only valid when type == 'sentence'; sending it
    with any other type causes a 401 response (an API quirk, not an auth error).
  - Section-scoped types (abstract, result, etc.) return document-level hits;
    matched sentence text is only available with type == 'sentence'.
"""

import argparse
import json
import sys
import urllib.request
import urllib.error

DEFAULT_URL = "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api"
# DEFAULT_URL = "http://localhost:18080/v1/textpresso/api"

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


def build_query(args, corpora):
    """Build the JSON payload for the search_documents endpoint.

    args    — argparse Namespace; only the standard search attributes are read,
              so an extended Namespace from tpc_search_internal.py works fine.
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


def apply_document_level_exclusion(results, args, corpora):
    """Drop documents whose only detectable match is within an excluded section.

    Best-effort: this script has no local CAS2 access, so unlike
    tpc_search_internal.py's precise sentence-position check, this can't
    verify arbitrary fulltext spans directly -- there's no REST query that
    exposes per-match position, and Lucene's 'fulltext' and e.g. 'references'
    fields aren't a disjoint pair that can be boolean-subtracted to isolate
    "matches outside references". Instead: for documents that match the
    excluded section(s), check whether each also matches in any OTHER named
    section type (abstract, introduction, results, etc.) for the same query
    -- if so, it has a real match elsewhere and is kept; if the excluded
    section is the only named section it matches in, it's dropped.

    Known gap: a match sitting in prose not covered by any detected section
    boundary won't be found by this check, so a document whose only
    non-excluded match is in such "uncovered" text will be incorrectly
    dropped. tpc_search_internal.py doesn't have this gap -- it checks real
    matched-sentence positions (via --type sentence, which covers the whole
    document) against CAS2 section boundaries directly, so an uncovered-prose
    match is correctly recognized as outside the excluded section.
    """
    if not args.exclude_type:
        return results
    exclude_set = set(args.exclude_type)
    other_types = [t for t in SECTION_TYPES if t not in exclude_set]

    excluded_ids = set()
    for excluded in args.exclude_type:
        exclude_args = argparse.Namespace(**vars(args))
        exclude_args.type = excluded
        exclude_args.exclude_type = None
        exclude_args.count = 200  # API max -- get a complete picture, not just the outer --count
        payload = build_query(exclude_args, corpora)
        for doc in (search(payload, url=args.url) or []):
            identifier = doc.get("identifier") or doc.get("accession")
            if identifier:
                excluded_ids.add(identifier)
    if not excluded_ids:
        return results  # nothing matched the excluded section(s) at all

    # Of the documents that match the excluded section, keep any that also
    # match in some other named section -- proof of a match elsewhere.
    also_matches_elsewhere = set()
    for other in other_types:
        other_args = argparse.Namespace(**vars(args))
        other_args.type = other
        other_args.exclude_type = None
        other_args.count = 200
        payload = build_query(other_args, corpora)
        for doc in (search(payload, url=args.url) or []):
            identifier = doc.get("identifier") or doc.get("accession")
            if identifier in excluded_ids:
                also_matches_elsewhere.add(identifier)

    drop_ids = excluded_ids - also_matches_elsewhere
    if not drop_ids:
        return results
    return [d for d in results
            if (d.get("identifier") or d.get("accession")) not in drop_ids]


def print_results(results, fmt="text"):
    """Print search results in text or JSON format."""
    if not results:
        print("No results.")
        return
    if fmt == "json":
        json.dump(results, sys.stdout, indent=2)
        print()
        return

    for i, doc in enumerate(results, 1):
        ref = (
            f"{doc.get('author', '?')} ({doc.get('year', '?')}). "
            f"{doc.get('title', '?')}. "
            f"{doc.get('journal', '?')}. "
            f"[{doc.get('accession', doc.get('identifier', '?'))}]"
        )
        sentences = doc.get("matched_sentences", [])
        print(f"[{i}] {ref}")
        for s in sentences:
            print(f"  - {s.strip()}")
        print()


def add_search_args(parser):
    """Add the standard search arguments to an argparse parser.

    Defined as a standalone function so that tpc_search_internal.py can reuse
    the same argument set without duplicating it.
    """
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
                        help="Restrict to ontology category (repeatable)")
    parser.add_argument("--categories-and", action="store_true",
                        help="Require ALL categories to match (default: ANY)")
    parser.add_argument("--sort-by-year", action="store_true",
                        help="Sort results by year instead of relevance score")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format (default: text)")
    parser.add_argument("--url", default=DEFAULT_URL,
                        help=f"API base URL (default: {DEFAULT_URL})")
    parser.add_argument("--list-corpora", action="store_true",
                        help="List available corpora and exit")


def validate_search_args(args, parser):
    """Require at least one filtering criterion (keyword or metadata field).

    Shared with tpc_search_internal.py -- keep this to checks valid for both
    scripts. tpc_search.py-only validation (e.g. --exclude-type's --type
    document requirement) belongs in this script's own main(), not here.
    """
    if not args.keywords and not args.author and not args.journal \
            and not args.year and not args.accession and not args.category:
        parser.error("provide at least one of: keywords, --author, --journal, "
                     "--year, --accession, --category")


def main():
    parser = argparse.ArgumentParser(
        description="Search a Textpresso corpus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
search types:
  sentence              match anywhere (default); returns matching sentence text
  document              match anywhere; returns document-level hits only
  abstract / title / introduction / result / discussion /
  conclusion / background / design / materials and methods /
  acknowledgments / references
                        restrict match to that section of the paper
                        (returns document-level hits; section must be annotated
                        in the CAS2 pipeline to appear in results)

examples:
  %(prog)s -c MaizeTest100 "flowering time"
  %(prog)s -c MaizeTest100 --type abstract "drought"
  %(prog)s -c MaizeTest100 --exclude "Arabidopsis" "kernel weight"
  %(prog)s -c MaizeTest100 --author "Buckler" --year 2022 "GWAS"
  %(prog)s -c MaizeTest100 --journal "Plant Cell" --exact-journal "flowering"
  %(prog)s -c MaizeTest100 --case-sensitive "ZmMADS"
  %(prog)s -c MaizeTest100 --sort-by-year "yield" --count 20
  %(prog)s -c MaizeTest100 "anthocyanin" --format json
  %(prog)s -c MaizeTest100 --type document "MARK" --exclude-type references
  %(prog)s --list-corpora
""",
    )
    add_search_args(parser)
    parser.add_argument("--exclude-type", action="append", metavar="TYPE",
                        choices=SECTION_TYPES,
                        help="Drop a document if the excluded section is the only "
                             "named section it matches in (repeatable); requires "
                             "--type document. Best-effort, document-level only -- "
                             "see apply_document_level_exclusion() for the 'uncovered "
                             "prose' gap this can't detect. For precise sentence-level "
                             "exclusion, use tpc_search_internal.py instead.")
    args = parser.parse_args()

    try:
        if args.list_corpora:
            for c in list_corpora(args.url):
                print(c)
            return

        validate_search_args(args, parser)
        if args.exclude_type and args.type != "document":
            parser.error("--exclude-type requires --type document in this script "
                         "(document-level only, no per-sentence position data "
                         "available here); for sentence-level --exclude-type, use "
                         "tpc_search_internal.py instead")
        corpora = args.corpora or list_corpora(args.url)
        payload = build_query(args, corpora)
        results = search(payload, url=args.url)
        results = apply_document_level_exclusion(results, args, corpora)
        print_results(results, fmt=args.format)

    except urllib.error.HTTPError as e:
        print(f"API error {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection failed: {e.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
