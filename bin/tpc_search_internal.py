#!/usr/bin/env python3
"""Search a Textpresso corpus with CAS2 ontology annotation.

Extends tpc_search.py with two annotation modes backed by CAS2 data. By
default this data is fetched over HTTP from the cas_annotate_server.py
endpoint on the Textpresso server (same --url host, /v1/textpresso/annotate),
so -- like tpc_search.py -- this script works from anywhere with network
access; no server-side file access is required. Pass --cas-root to instead
parse CAS2 files directly from a local directory (server-side use only).

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
"""

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

_BIN  = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_BIN, "..")

# --cas-root fallback only (server-side, local-file parsing). Not used by the
# default HTTP path.
# Host-side path to the CAS2 files (the container mounts this at /data/textpresso).
# Pass --cas-root /data/textpresso/tpcas-2 when running inside the container.
DEFAULT_CAS_ROOT = "/home/ec2-user/agr_textpresso/.data/tpcas-2"


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


# Load search helpers from the public script and the CAS2 parsing library.
_search = _load_module("tpc_search", "bin/tpc_search.py")
_ca     = _load_module("casannot",   "textpresso_classifiers/casannot.py")

build_query           = _search.build_query
search                = _search.search
list_corpora          = _search.list_corpora
add_search_args       = _search.add_search_args
validate_search_args  = _search.validate_search_args
check_categories_or_exit = _search.check_categories_or_exit
DEFAULT_URL           = _search.DEFAULT_URL
SEARCH_TYPES          = _search.SEARCH_TYPES


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
        description="Search Textpresso with CAS2 ontology annotation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Reuse all standard search arguments from the public script
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
        corpora = args.corpora or list_corpora(args.url)
        payload = build_query(args, corpora)
        results = search(payload, url=args.url)
        print_results(results, args)

    except urllib.error.HTTPError as e:
        print(f"API error {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection failed: {e.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
