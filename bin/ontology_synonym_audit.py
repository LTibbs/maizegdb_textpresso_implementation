#!/usr/bin/env python3
"""Audit an ontology's matched synonyms across a Textpresso CAS2 corpus for
generic-word false positives.

A gene-specific synonym (a locus ID, a specific allele name) should only
appear in the handful of papers that actually discuss that gene. A synonym
that is really a common English word or word-fragment (e.g. "binding",
"promoter", "expression") will instead appear across a large fraction of an
unrelated, mixed-topic corpus. This script counts document frequency and
total occurrence count per matched term across a corpus's CAS2 files, ranks
by document frequency, and (optionally) traces the top offenders back to
their source OBO file entries so the responsible synonym line(s) can be
identified.

Usage:
    python3 bin/ontology_synonym_audit.py \\
        --cas-root /home/ec2-user/agr_textpresso/.data/tpcas-2 \\
        --corpus MaizeTest100 \\
        --id-prefix tpzm: \\
        --obo-file /home/ec2-user/agr_textpresso/.data/obofiles4production/zmays_genes_20260708.obo \\
        --min-doc-freq 5 \\
        --exclude-type references

--exclude-type (repeatable) drops matches inside a CAS section type -- e.g.
--exclude-type references excludes bibliography/citation matches (author
initials, journal abbreviations) from the frequency counts, so a term isn't
flagged as a "generic word false positive" just because it happens to
collide with something common in citations. Requires papers to have been
(re)tokenized after the section-detection fix (see
sorghumbase_textpresso_implementation/docs/Laura_work_updates_log.md,
2026-07-13); a no-op on older CAS2 files with no section data.

Uses textpresso_classifiers/casannot.py for CAS2 parsing (loaded via
importlib so this doesn't trigger the package __init__.py / sklearn import --
see tpc_search_internal.py for the same pattern). No pip packages beyond
that repo module are needed.
"""
import argparse
import glob
import importlib.util
import os
import re
from collections import defaultdict

TERM_RE = re.compile(r'^id: (\S+)')
NAME_RE = re.compile(r'^name: (.*)$')
SYN_RE = re.compile(r'^synonym: "([^"]*)" (\S+) (\S+) \[\]')

_BIN = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_BIN, "..")


def _load_casannot():
    path = os.path.join(_ROOT, "textpresso_classifiers/casannot.py")
    spec = importlib.util.spec_from_file_location("casannot", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scan_corpus(ca, cas_root, corpora, id_prefix, exclude_types=None):
    """Return (files, term_doc_count, term_total_count, term_surface_forms,
    excluded_count) keyed by lowercased matched term text, counting only
    non-PTCAT annotations whose category contains id_prefix and whose
    position doesn't fall within an excluded section type.

    corpora may be a single corpus name or a list of them -- documents are
    keyed by full file path, which already embeds the corpus name, so
    counts merge naturally across corpora."""
    if isinstance(corpora, str):
        corpora = [corpora]
    files = []
    for corpus in corpora:
        matched = sorted(glob.glob(f"{cas_root}/{corpus}/*/*.tpcas.gz"))
        if not matched:
            raise SystemExit(f"No .tpcas.gz files found under {cas_root}/{corpus}/*/")
        files.extend(matched)

    term_doc_count = defaultdict(set)
    term_total_count = defaultdict(int)
    term_surface_forms = defaultdict(set)
    excluded_count = 0

    for path in files:
        _, annotations, sections = ca.parse_cas_file(path)
        annotations = [a for a in annotations if id_prefix in a["category"]]
        if exclude_types:
            kept = ca.exclude_sections(annotations, sections, exclude_types)
            excluded_count += len(annotations) - len(kept)
            annotations = kept
        for a in annotations:
            key = a["term"].lower()
            term_doc_count[key].add(path)
            term_total_count[key] += 1
            term_surface_forms[key].add(a["term"])

    return files, term_doc_count, term_total_count, term_surface_forms, excluded_count


def trace_obo(obo_file, targets):
    """Map each lowercased target term to the OBO [Term] block(s) where it
    appears as `name:` or as a `synonym:` value, with synonym type/source."""
    hits = defaultdict(list)
    current_id = current_name = None
    with open(obo_file) as f:
        for line in f:
            line = line.rstrip("\n")
            if line == "[Term]":
                current_id = current_name = None
                continue
            m = TERM_RE.match(line)
            if m:
                current_id = m.group(1)
                continue
            m = NAME_RE.match(line)
            if m:
                current_name = m.group(1)
                if current_name.lower() in targets:
                    hits[current_name.lower()].append(
                        (current_id, current_name, "NAME", "-", "-"))
                continue
            m = SYN_RE.match(line)
            if m:
                syn_text, syn_type, syn_source = m.groups()
                if syn_text.lower() in targets:
                    hits[syn_text.lower()].append(
                        (current_id, current_name, "SYNONYM", syn_type, syn_source))
    return hits


def main():
    ca = _load_casannot()

    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cas-root", default="/home/ec2-user/agr_textpresso/.data/tpcas-2")
    ap.add_argument("--corpus", required=True, action="append",
                     help="Corpus name, e.g. MaizeTest100 (repeatable to combine corpora)")
    ap.add_argument("--id-prefix", required=True,
                     help='Substring identifying the ontology in the category '
                          'string, e.g. "tpzm:" (matches tpzm: and tpzma: both)')
    ap.add_argument("--min-doc-freq", type=int, default=5,
                     help="Report terms appearing in at least this many documents "
                          "(default: 5)")
    ap.add_argument("--top", type=int, default=60,
                     help="How many top terms to print (default: 60)")
    ap.add_argument("--obo-file", default=None,
                     help="If given, trace reported terms back to their source "
                          "OBO [Term] blocks (id, name, synonym type/source)")
    ap.add_argument("--trace-limit", type=int, default=5,
                     help="Max OBO entries to show per traced term (default: 5)")
    ap.add_argument("--exclude-type", action="append", metavar="TYPE",
                     choices=ca.SECTION_TYPES,
                     help="Exclude matches inside this CAS section type (repeatable), "
                          "e.g. --exclude-type references to drop bibliography/citation "
                          "matches. Requires papers (re)tokenized after the "
                          "section-detection fix; a no-op on older CAS2 files with no "
                          "section data.")
    args = ap.parse_args()

    files, doc_count, total_count, surface, excluded_count = scan_corpus(
        ca, args.cas_root, args.corpus, args.id_prefix, args.exclude_type)
    corpora_desc = " + ".join(f"{args.cas_root}/{c}/*/" for c in args.corpus)
    print(f"Scanned {len(files)} CAS2 files under {corpora_desc}")
    if args.exclude_type:
        print(f"Excluded {excluded_count} matches inside "
              f"{'/'.join(args.exclude_type)} sections.")

    rows = sorted(
        ((len(doc_count[k]), total_count[k], k, sorted(surface[k]))
         for k in total_count),
        reverse=True,
    )
    reportable = [r for r in rows if r[0] >= args.min_doc_freq]
    print(f"{len(rows)} distinct matched terms total; "
          f"{len(reportable)} appear in >= {args.min_doc_freq} documents "
          f"(out of {len(files)} papers = {100*args.min_doc_freq/len(files):.1f}%+).\n")

    print(f"{'doc_freq':>8} {'total':>6}  term")
    print("-" * 60)
    for doc_freq, total, key, surf in reportable[:args.top]:
        print(f"{doc_freq:>8} {total:>6}  {key!r}  surface={surf}")

    if args.obo_file:
        targets = {k for _, _, k, _ in reportable}
        hits = trace_obo(args.obo_file, targets)
        source_tally = defaultdict(int)
        print(f"\n=== OBO trace for {len(targets)} terms (doc_freq >= {args.min_doc_freq}) ===")
        for doc_freq, total, key, _ in reportable:
            entries = hits.get(key, [])
            print(f"\n{key!r} (doc_freq={doc_freq}, total={total}, {len(entries)} OBO entries)")
            for gene_id, gene_name, kind, syn_type, syn_source in entries[:args.trace_limit]:
                print(f"  {gene_id}  name={gene_name!r}  kind={kind} "
                      f"syn_type={syn_type} source={syn_source}")
                source_tally[f"{syn_type} {syn_source}"] += 1
            if len(entries) > args.trace_limit:
                print(f"  ... and {len(entries) - args.trace_limit} more")

        print(f"\n=== synonym type/source tally across all traced OBO entries ===")
        for src, count in sorted(source_tally.items(), key=lambda x: -x[1]):
            print(f"  {count:>5}  {src}")


if __name__ == "__main__":
    main()
