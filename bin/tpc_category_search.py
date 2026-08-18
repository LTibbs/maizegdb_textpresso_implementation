#!/usr/bin/env python3
"""Search the Textpresso ontology categories directly for --category candidates.

--category (in tpc_search.py / tpc_search_internal.py) requires the *exact*
stored "name (ID)" string, e.g. "seed (PO:0009010)" -- guessing at it doesn't
reliably fail, it can silently over-match or under-match instead (see "Other
notes" in docs/TPC_SEARCH_GUIDE.md). This tool searches GO/PO/TO/MAIZE_GENES
by name or synonym and prints the exact strings to use.

Talks to the same cas_annotate_server.py sidecar the search scripts use for
category validation (GET /v1/textpresso/category_search), so it needs only
network access -- no server-side file access required.

Usage:
    python3 bin/tpc_category_search.py "seed"
    python3 bin/tpc_category_search.py "adh1" --ontology MAIZE_GENES
    python3 bin/tpc_category_search.py "anthocyanin" --ontology GO --ontology PO --limit 10
    python3 bin/tpc_category_search.py "seed" --format json
"""

import argparse
import importlib.util
import json
import os
import sys

_BIN = os.path.dirname(os.path.abspath(__file__))


def _load_module(name, rel_path):
    path = os.path.join(_BIN, rel_path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_search = _load_module("tpc_search", "tpc_search.py")


def _warn_unavailable_relationship_types(matches, relationship_types, ancestor_relationship_types):
    """Print a stderr warning for each directly-matched term (matched_on has no
    "+descendant"/"+ancestor" suffix) that's missing one or more of the
    requested relationship types in that direction -- per that match's own
    relationship_types/parent_relationship_types (see category_index.py).
    Expanded matches are skipped since they trivially have the type that
    produced them. No-op if neither --relationship-type nor
    --ancestor-relationship-type was passed.
    """
    if not relationship_types and not ancestor_relationship_types:
        return

    def _warn(cat, requested, available, direction, flag):
        unavailable = sorted(set(requested) - set(available or ()))
        if not unavailable:
            return
        available_note = ", ".join(available) if available else "(none -- no relationships in this direction)"
        print(f'Warning: {flag} {", ".join(unavailable)} -- "{cat}" has no {direction} via '
              f'{"/".join(unavailable)}, so this contributes nothing for it. '
              f'Available for this term: {available_note}', file=sys.stderr)

    for m in matches:
        if "+" in m.get("matched_on", ""):
            continue  # already an expanded result, not the term the flags were requested for
        if relationship_types:
            _warn(m["category"], relationship_types, m.get("relationship_types"),
                  "children", "--relationship-type")
        if ancestor_relationship_types:
            _warn(m["category"], ancestor_relationship_types, m.get("parent_relationship_types"),
                  "parents", "--ancestor-relationship-type")


def main():
    parser = argparse.ArgumentParser(
        description="Search Textpresso ontology categories for --category candidates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query", help='Term to search for, e.g. "seed" or "adh1"')
    parser.add_argument("--ontology", action="append", metavar="NAME",
                        choices=["GO", "PO", "TO", "MAIZE_GENES", "MAIZE_GENES_RELATED", "OTHER"],
                        help="Restrict to specific ontologies (repeatable); "
                             "default: search all")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max results (default: 20)")
    parser.add_argument("--relationship-type", action="append", metavar="TYPE",
                        dest="relationship_types",
                        help="Also include descendants (child terms) of each match "
                             "along this OBO relationship (repeatable), e.g. "
                             "--relationship-type is_a --relationship-type part_of. "
                             "Default: literal match only, no ontology-graph expansion.")
    parser.add_argument("--ancestor-relationship-type", action="append", metavar="TYPE",
                        dest="ancestor_relationship_types",
                        help="Mirror of --relationship-type: also include ancestors "
                             "(parent terms) of each match along this OBO relationship "
                             "(repeatable). Combine with --relationship-type to expand "
                             "both directions at once.")
    parser.add_argument("--url", default=_search.DEFAULT_URL,
                        help=f"API base URL (default: {_search.DEFAULT_URL})")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format (default: text)")
    args = parser.parse_args()

    matches = _search.category_search(args.query, args.url, args.ontology, args.limit,
                                       args.relationship_types, args.ancestor_relationship_types)
    if matches is None:
        print(f"Could not reach the category lookup service at "
              f"{_search.annotation_service_url(args.url)}/category_search", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        json.dump(matches, sys.stdout, indent=2)
        print()
        return

    if not matches:
        print(f'No categories found matching "{args.query}".')
        print("Try a shorter or more general term, or a different --ontology.")
        return

    _warn_unavailable_relationship_types(matches, args.relationship_types,
                                          args.ancestor_relationship_types)

    print(f'Categories matching "{args.query}":\n')
    width = max(len(m["category"]) for m in matches)
    for m in matches:
        rel_types = m.get("relationship_types") or []
        parent_rel_types = m.get("parent_relationship_types") or []
        notes = []
        if rel_types:
            notes.append(f"children via: {', '.join(rel_types)}")
        if parent_rel_types:
            notes.append(f"parents via: {', '.join(parent_rel_types)}")
        rel_note = f"  {'; '.join(notes)}" if notes else ""
        print(f'  {m["category"]:<{width}}  [{m["ontology"]}, {m["matched_on"]}]{rel_note}')

    print(f'\nExample:\n  python3 bin/tpc_search_combined.py -c <corpus> --category "{matches[0]["category"]}" "<keywords>"')


if __name__ == "__main__":
    main()
