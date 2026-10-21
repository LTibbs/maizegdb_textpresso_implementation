#!/usr/bin/env python3
"""build_class_map.py -- emit the short-form OBO provenance map the italics rule needs.

    python3 bin/build_class_map.py \
        --obo .data/obofiles4production/zmays_genes_20260708.obo \
        --max-len 4 --out .data/italics/obo_short_form_classes.json

~98 KB for 5,419 forms, versus a 10.5 MB OBO parse at server startup. Rebuild it
whenever the OBO is regenerated: unlisted forms resolve to 'unknown', which is
not exempt, so a stale map makes the italics rule MORE aggressive.
"""
import argparse, collections, json, re, sys

SYN = re.compile(r'^synonym:\s*"(.*?)"\s+(EXACT|RELATED|BROAD|NARROW)\s*([A-Za-z0-9_]*)')
REGISTERED_SOURCES = {"locus", "locus_name"}


def is_identifier_source(src):
    """Any per-assembly id field (B73_v5_id, Mo17_2021_id, every NAM founder) or
    a GenBank accession. Matched by suffix because the maize OBO carries 42 such
    fields; an earlier hard-coded list of four mis-filed 19 GenBank accessions."""
    return bool(src) and (src.endswith("_id") or src == "GenBankID")


def parse(path):
    terms, tid = {}, None
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if line == "[Term]":
            tid = None
        elif line.startswith("id: "):
            tid = line[4:].strip(); terms[tid] = {"name": None, "syns": []}
        elif tid and line.startswith("name: "):
            terms[tid]["name"] = line[6:].strip()
        elif tid and line.startswith("synonym:"):
            m = SYN.match(line)
            if m:
                terms[tid]["syns"].append((m.group(1), m.group(2), m.group(3) or ""))
    return terms


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--obo", required=True)
    ap.add_argument("--max-len", type=int, default=4)
    ap.add_argument("--out", default="obo_short_form_classes.json")
    a = ap.parse_args()

    terms = parse(a.obo)
    owners = collections.defaultdict(set)
    for tid, t in terms.items():
        if t["name"]:
            owners[t["name"].lower()].add(tid)
        for s, _scope, _src in t["syns"]:
            owners[s.lower().strip()].add(tid)

    out = {}
    for form, ids in owners.items():
        if not form or len(form) > a.max_len:
            continue
        srcs, is_name = set(), False
        for tid in ids:
            if (terms[tid]["name"] or "").lower() == form:
                is_name = True
            for s, _scope, src in terms[tid]["syns"]:
                if s.lower().strip() == form:
                    srcs.add(src)
        if any(is_identifier_source(x) for x in srcs):
            out[form] = "identifier"
        elif is_name or (srcs & REGISTERED_SOURCES):
            out[form] = "registered"
        else:
            out[form] = "alias"

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"), sort_keys=True)
    counts = collections.Counter(out.values())
    print("[ok] %d forms <= %d chars -> %s  %s"
          % (len(out), a.max_len, a.out, dict(counts)), file=sys.stderr)


if __name__ == "__main__":
    main()
