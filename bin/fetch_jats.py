#!/usr/bin/env python3
"""Fetch PubMed Central JATS for a corpus and stage it for the jats ingest path.

For each paper in a MaizeGDB import CSV (columns doi = accession, doi_clean,
pmcid), resolves a PMCID (the CSV's, else NCBI's ID converter by DOI), fetches
the article with E-utilities efetch, and keeps it only if it has a full-text
<body> that textpresso_classifiers/jats_text.py can convert. PMC withholds
XML full text for some publishers (Genetics, PNAS, ...): efetch then returns
front matter only, and those papers stay on the PDF path.

Files land where articles2cas -t 5 -p expects them, named by accession:

    OUT_DIR/<accession>/<accession>.jats.json   what the ingest reads
                                                (TjTokenizer): jats_text.py's
                                                text and standoff annotations
    OUT_DIR/<accession>/<accession>.nxml.gz     the JATS as fetched
    OUT_DIR/<accession>/<accession>.suppl.txt   (only with --pdf-dir, and only
                                                 if the PDF has supplements)

The .suppl.txt file is the merged PDF's page text from the first supplement
cover page on (jats_text.read_pdf_supplement), pages joined by blank lines;
it is already appended in the .jats.json as the "supplementary material"
section, so a paper keeps its supplementary tables when its main text comes
from JATS. Re-running over staged papers re-converts them from the local
.nxml.gz without fetching (use --refresh to fetch again).

Every paper gets a row in the status TSV (accession, pmcid, status, bytes,
supplement_pages, checked), status one of: ok, exists, no_pmcid, no_body,
http_<code>, error. An HTTP failure is recorded as such -- never as no_body
-- because a 429 counted as "no full text" once produced a silent 96% miss
rate in jats_italics.py.

--annotate-csv writes a copy of the metadata CSV with four columns added, so
which papers were ingested from JATS can be filtered on later:

    text_source    jats (status ok/exists) or pdf
    jats_status    the status above
    jats_pmcid     the PMCID tried, if any
    jats_checked   UTC date of this run

NCBI allows 3 requests/second without an API key; --rate defaults to that.

Usage:
    python3 bin/fetch_jats.py --metadata MaizeOpenAccess_papers.csv \\
        --out-dir /data/textpresso/raw_files/xml/MaizeOA \\
        --pdf-dir /data/textpresso/raw_files/pdf/MaizeOA \\
        --status jats_status.tsv --annotate-csv MaizeOpenAccess_papers.jats.csv
    python3 bin/fetch_jats.py --metadata papers.csv --out-dir xml/MaizeTest \\
        --accessions maizetest100.txt --text-dir jats_text_out
"""

import argparse
import csv
import datetime
import gzip
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_BIN = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "jats_text", os.path.join(_BIN, "..", "textpresso_classifiers", "jats_text.py"))
jats_text = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jats_text)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
IDCONV = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/?"
TOOL = "maizegdb_textpresso_jats"

_lock = threading.Lock()
_last = [0.0]
_interval = [1 / 3]


def _throttle():
    with _lock:
        wait = _last[0] + _interval[0] - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()


class HttpFailure(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


def _get(url, attempts=5, timeout=90):
    """GET with backoff on 429/5xx; raises HttpFailure when retries run out."""
    code = None
    for attempt in range(attempts):
        _throttle()
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            code = e.code
            if e.code != 429 and e.code < 500:
                break
        except (urllib.error.URLError, TimeoutError):
            code = "timeout"
        time.sleep(2 * (attempt + 1))
    raise HttpFailure(code)


def resolve_pmcids(rows, email=None):
    """{accession: 'PMC...'} from the CSV, plus ID-converter lookups by DOI."""
    out = {r["doi"]: r["pmcid"].strip() for r in rows if r.get("pmcid", "").strip()}
    need = [r for r in rows if r["doi"] not in out and r.get("doi_clean", "").strip()]
    for i in range(0, len(need), 100):
        chunk = need[i:i + 100]
        params = {"ids": ",".join(r["doi_clean"] for r in chunk), "format": "json", "tool": TOOL}
        if email:
            params["email"] = email
        try:
            records = json.loads(_get(IDCONV + urllib.parse.urlencode(params)))["records"]
        except HttpFailure as e:
            print(f"[warn] ID converter failed for {len(chunk)} DOIs: {e}", file=sys.stderr)
            continue
        by_doi = {r["doi_clean"].lower(): r["doi"] for r in chunk}
        for rec in records:
            acc = by_doi.get(rec.get("requested-id", "").lower())
            if acc and rec.get("pmcid"):
                out[acc] = rec["pmcid"]
    return {acc: p if p.upper().startswith("PMC") else "PMC" + p for acc, p in out.items()}


def fetch_jats(pmcid, email=None):
    params = {"db": "pmc", "id": pmcid[3:], "retmode": "xml", "tool": TOOL}
    if email:
        params["email"] = email
    return _get(EUTILS + urllib.parse.urlencode(params))


SUPPLEMENT_SUFFIX = ".suppl.txt"
JSON_SUFFIX = ".jats.json"


def stage(acc, pmcid, out_dir, pdf_dir=None, text_dir=None, refresh=False, email=None):
    """-> (status, bytes, supplement_pages)

    A paper already staged is not fetched again, but is re-converted from its
    local .nxml.gz, so a change to jats_text.py or to the PDF supplements
    reaches the .jats.json with a re-run and no network traffic."""
    dest = os.path.join(out_dir, acc, acc + ".nxml.gz")
    pages = jats_text.read_pdf_supplement(os.path.join(pdf_dir, acc)) if pdf_dir else []
    if os.path.exists(dest) and not refresh:
        with gzip.open(dest, "rb") as f:
            xml = f.read()
        status = "exists"
    else:
        try:
            xml = fetch_jats(pmcid, email)
        except HttpFailure as e:
            return f"http_{e.code}", 0, len(pages)
        status = "ok"
    try:
        doc = jats_text.convert(xml, pages)
    except jats_text.NoBodyError:
        return "no_body", len(xml), len(pages)
    except Exception as e:                       # malformed XML
        print(f"[error] {acc} {pmcid}: {e!r}", file=sys.stderr)
        return "error", len(xml), len(pages)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if status == "ok":
        with gzip.open(dest, "wb") as f:
            f.write(xml)
    _write_supplement(dest, acc, pages)
    with open(os.path.join(os.path.dirname(dest), acc + JSON_SUFFIX), "w", encoding="utf-8") as f:
        json.dump(doc.as_dict(), f, ensure_ascii=False)
    if text_dir:
        os.makedirs(text_dir, exist_ok=True)
        with open(os.path.join(text_dir, acc + ".txt"), "w", encoding="utf-8") as f:
            f.write(doc.text)
    return status, len(xml), len(pages)


def _write_supplement(dest, acc, pages):
    path = os.path.join(os.path.dirname(dest), acc + SUPPLEMENT_SUFFIX)
    if pages:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(pages))
    elif os.path.exists(path):
        os.remove(path)                          # supplements gone from the PDF


def main():
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog=__doc__[__doc__.index("Usage:"):],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--metadata", required=True, help="import CSV (doi, doi_clean, pmcid columns)")
    parser.add_argument("--out-dir", required=True, help="staging dir, e.g. raw_files/xml/MaizeOA")
    parser.add_argument("--status", default="jats_status.tsv", help="status TSV to write")
    parser.add_argument("--accessions", help="only these accessions (one per line)")
    parser.add_argument("--limit", type=int, help="stop after this many papers")
    parser.add_argument("--pdf-dir", help="PDF-path page texts (raw_files/pdf/<corpus>), to stage "
                                          "each paper's merged supplements as <acc>.suppl.txt")
    parser.add_argument("--annotate-csv", help="write the metadata CSV here with text_source / "
                                               "jats_status / jats_pmcid / jats_checked added")
    parser.add_argument("--text-dir", help="also write each paper's plain text here (<acc>.txt)")
    parser.add_argument("--refresh", action="store_true", help="re-fetch papers already staged")
    parser.add_argument("--rate", type=float, default=3.0, help="requests/second (default 3)")
    parser.add_argument("--email", help="contact address sent to NCBI with each request")
    args = parser.parse_args()
    _interval[0] = 1 / args.rate

    with open(args.metadata, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if args.accessions:
        with open(args.accessions) as f:
            wanted = {line.strip() for line in f if line.strip()}
        rows = [r for r in rows if r["doi"] in wanted]
    if args.limit:
        rows = rows[:args.limit]

    pmcids = resolve_pmcids(rows, args.email)
    checked = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    counts, results = {}, {}
    os.makedirs(os.path.dirname(os.path.abspath(args.status)), exist_ok=True)
    with open(args.status, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["accession", "pmcid", "status", "bytes", "supplement_pages", "checked"])
        for i, r in enumerate(rows, 1):
            acc, pmcid = r["doi"], pmcids.get(r["doi"])
            status, size, n_pages = ("no_pmcid", 0, 0) if not pmcid else stage(
                acc, pmcid, args.out_dir, args.pdf_dir, args.text_dir, args.refresh, args.email)
            counts[status] = counts.get(status, 0) + 1
            results[acc] = (status, pmcid or "")
            w.writerow([acc, pmcid or "", status, size, n_pages, checked])
            f.flush()
            if i % 50 == 0 or i == len(rows):
                print(f"[{i}/{len(rows)}] {counts}", file=sys.stderr)
    if args.annotate_csv:
        annotate_csv(args.metadata, args.annotate_csv, results, checked)
    print(json.dumps(counts))


def annotate_csv(metadata, out_path, results, checked):
    """Copy the metadata CSV adding text_source / jats_status / jats_pmcid /
    jats_checked. Rows this run didn't cover keep any values they had."""
    added = ["text_source", "jats_status", "jats_pmcid", "jats_checked"]
    with open(metadata, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames + [c for c in added if c not in reader.fieldnames]
        rows = list(reader)
    for r in rows:
        if r["doi"] in results:
            status, pmcid = results[r["doi"]]
            r.update(text_source="jats" if status in ("ok", "exists") else "pdf",
                     jats_status=status, jats_pmcid=pmcid, jats_checked=checked)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
