#!/usr/bin/env python3
"""build_access_csv.py -- emit the per-paper open-access table the GUI reads.

    python3 bin/build_access_csv.py \
        --manifest ~/maize_pipeline/pipeline/download_manifest.csv \
        --papers-csv sorghumbase_textpresso_implementation/metadata/MaizeOpenAccess_papers.csv \
        --tpcas-dir $DATA/tpcas-2/MaizeOpenAccess \
        --out sorghumbase_textpresso_implementation/metadata/MaizeOpenAccess_access.csv

One row per corpus paper (the accession directories under --tpcas-dir, or the
lines of --accession-file): doi (accession form), Unpaywall oa_status, Europe
PMC epmc_license, and which file the row came from. The GUI's
_access_level() turns those into open / publisher / restricted.

The pipeline's download_manifest.csv is the source: it carries epmc_license,
which upgrades papers Unpaywall calls bronze but that are CC-licensed. Papers
missing from it fall back to oa_status from the corpus metadata CSV (no
licence); papers in neither are written with empty values and show as
restricted. Where the manifest has several rows for one DOI the last wins, as
it did when the GUI read the manifest directly. Rebuild after a corpus refresh.
"""
import argparse, collections, csv, os, sys

OPEN_STATUSES = {"gold", "green", "hybrid"}


def key(doi):
    return (doi or "").strip().lower().replace("/", "_")


def read_csv(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return list(csv.DictReader(f))


def level(status, licence):
    """Mirror of _access_level() in textpresso_gui/backend/main.py, for the summary."""
    if status in OPEN_STATUSES or licence.startswith("cc"):
        return "open"
    return "publisher" if status == "bronze" else "restricted"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", required=True, help="pipeline download_manifest.csv")
    ap.add_argument("--papers-csv", help="corpus metadata CSV (fallback oa_status)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--tpcas-dir", help="tpcas-2/<corpus> directory; its subdirectories are the accessions")
    src.add_argument("--accession-file", help="one accession per line")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.tpcas_dir:
        accessions = sorted(d for d in os.listdir(args.tpcas_dir)
                            if os.path.isdir(os.path.join(args.tpcas_dir, d)))
    else:
        accessions = sorted({l.strip() for l in open(args.accession_file, encoding="utf-8") if l.strip()})

    manifest = {}
    for r in read_csv(args.manifest):
        if r.get("doi"):
            manifest[key(r["doi"])] = r          # last row wins
    papers = {key(r.get("doi")): r for r in read_csv(args.papers_csv)} if args.papers_csv else {}

    counts = collections.Counter()
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["doi", "oa_status", "epmc_license", "source"])
        for acc in accessions:
            k = key(acc)
            if k in manifest:
                r, source = manifest[k], "download_manifest"
                status, licence = r.get("oa_status"), r.get("epmc_license")
            elif k in papers:
                source, status, licence = "papers_csv", papers[k].get("oa_status"), ""
            else:
                source, status, licence = "none", "", ""
            status, licence = (status or "").strip().lower(), (licence or "").strip().lower()
            w.writerow([acc, status, licence, source])
            counts[source] += 1
            counts[level(status, licence)] += 1

    print(f"{len(accessions)} papers -> {args.out}", file=sys.stderr)
    print("  source: " + ", ".join(f"{s} {counts[s]}" for s in ("download_manifest", "papers_csv", "none")),
          file=sys.stderr)
    print("  access: " + ", ".join(f"{s} {counts[s]}" for s in ("open", "publisher", "restricted")),
          file=sys.stderr)


if __name__ == "__main__":
    main()
