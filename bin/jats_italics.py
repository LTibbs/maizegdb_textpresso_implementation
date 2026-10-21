#!/usr/bin/env python3
"""
jats_italics.py -- recover typographic signal for Textpresso's open-access papers.

Textpresso ingests PDFs through `pdf2tpcas`, which discards italicisation. Maize
nomenclature italicises gene symbols, which makes italics the single most
informative gene-vs-look-alike feature available. Measured over 300 maize
open-access papers with JATS XML intact: pooled italic rate 61.5% for gene
symbols against 0.3% for terms removed from the OBO as generic. Per symbol the
rate is diagnostic -- sh2 99%, su1 100%, wx1 100%, but r1 3%, c1 16%, p1 21%,
because most occurrences of those strings are not gene mentions at all.

Rather than modify the C++ CAS generator, this builds a *sidecar*: for each
accession it fetches the PubMed Central JATS XML and records, per surface form,
how many occurrences are italicised. `maize_gene_context.py` consumes the sidecar
as an `italic_rate` feature.

Offsets are deliberately not aligned between JATS and the PDF-derived tpcas text
-- PDF extraction reorders captions and normalises whitespace, so character
offsets do not correspond. Per-paper, per-surface-form rates are robust to that
and carry most of the signal.

Usage:
  python3 jats_italics.py --accession-file accs.txt --out italics.json
  python3 jats_italics.py --accession 10.1093_jxb_erad297
"""
from __future__ import annotations

import argparse, json, re, sys, threading, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
ITALIC_TAGS = {"italic", "i", "em"}

#: Elements whose contents are excluded from the italic count. Reference lists
#: are the important one: a gene-shaped string appearing only in cited paper
#: titles must not be counted as a body occurrence, and `et`/`al` are italicised
#: in every reference. `<back>` normally holds ref-list and ack, but some
#: publishers put acknowledgments inside `<body>`, so both are checked.
SKIP_TAGS = {"ack", "ref-list", "back", "fn-group", "glossary", "notes"}

#: `<sec>` elements excluded by sec-type or title text.
SKIP_SEC = re.compile(
    r"acknowledg|reference|bibliograph|conflict|funding|author.?contribut"
    r"|competing|supplementar|data.?availab", re.I)

#: match tokens that could be a gene symbol: letters optionally followed by
#: digits, or any token containing a digit. Deliberately permissive -- the
#: consumer decides which of these are in the OBO.
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9\-/]{0,24})(?![A-Za-z0-9])")


def accession_to_doi(acc: str) -> str:
    """MaizeGDB accessions are DOIs with '/' replaced by '_'."""
    head, sep, tail = acc.partition("_")
    return f"{head}/{tail}" if sep else acc


#: NCBI permits 3 requests/second without an API key, 10 with one. Each paper
#: costs two requests (esearch + efetch), so the limiter is shared process-wide.
#: Exceeding it returns HTTP 429, which an earlier version of this script
#: swallowed in a bare `except` -- producing a silent 96% miss rate.
_RATE_LOCK = threading.Lock()
_MIN_INTERVAL = 0.34          # seconds between requests; 0.11 with an API key
_last_call = [0.0]


def set_rate(per_second: float):
    global _MIN_INTERVAL
    _MIN_INTERVAL = 1.0 / max(0.1, per_second)


def _throttle():
    with _RATE_LOCK:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def _get(url: str, timeout: int = 90, attempts: int = 4) -> bytes:
    """GET with throttling and exponential backoff on 429/5xx.

    Raises on persistent failure rather than returning None -- a silent miss
    here looks identical to "this paper has no PMC record", which is what made
    the original rate-limit bug invisible.
    """
    last = None
    for i in range(attempts):
        _throttle()
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (2 ** i))
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            time.sleep(1.5 * (2 ** i))
    raise RuntimeError("giving up after %d attempts: %r" % (attempts, last))


def doi_to_pmcid(doi: str, email: str | None = None) -> str | None:
    params = {"db": "pmc", "term": f'"{doi}"[DOI]', "retmode": "xml"}
    if email:
        params["email"] = email
    raw = _get(EUTILS + "esearch.fcgi?" + urllib.parse.urlencode(params)).decode()
    ids = re.findall(r"<Id>(\d+)</Id>", raw)
    return ids[0] if ids else None


def fetch_jats(pmcid: str, email: str | None = None) -> bytes | None:
    params = {"db": "pmc", "id": pmcid, "retmode": "xml"}
    if email:
        params["email"] = email
    return _get(EUTILS + "efetch.fcgi?" + urllib.parse.urlencode(params))


def _tag(elem):
    return elem.tag.lower().split("}")[-1]


def _excluded(elem):
    """True for references, acknowledgments and other non-body matter."""
    if _tag(elem) in SKIP_TAGS:
        return True
    if _tag(elem) == "sec":
        if SKIP_SEC.search(elem.get("sec-type") or ""):
            return True
        title = elem.find("title")
        if title is not None:
            text = "".join(title.itertext())
            if text and SKIP_SEC.search(text):
                return True
    return False


def _runs(elem, inherited=False):
    """Yield (text, is_italic) over an element subtree, skipping excluded parts."""
    if _excluded(elem):
        return
    here = inherited or (_tag(elem) in ITALIC_TAGS)
    if elem.text:
        yield elem.text, here
    for child in elem:
        if _excluded(child):
            if child.tail:
                yield child.tail, inherited      # keep text that follows it
            continue
        yield from _runs(child, here)
        if child.tail:
            yield child.tail, inherited


def italic_stats(xml_bytes: bytes) -> dict[str, dict]:
    """-> {surface_form_lower: {'italic': n, 'total': n}} over the article body,
    with references, acknowledgments and back matter excluded (see SKIP_TAGS).

    The exclusion is load-bearing for the strict-zero rule: counting reference
    titles would add non-italic occurrences to forms that are italicised
    everywhere they are actually used, and would make `never_italicised()` fire
    on genuine gene mentions."""
    stats: dict[str, dict] = {}
    root = ET.fromstring(xml_bytes)
    for article in root.iter("article"):
        body = article.find(".//body")
        if body is None:
            continue
        for text, is_italic in _runs(body):
            if not text:
                continue
            for m in TOKEN_RE.finditer(text):
                key = m.group(1).lower()
                e = stats.setdefault(key, {"italic": 0, "total": 0})
                e["total"] += 1
                if is_italic:
                    e["italic"] += 1
    return stats


def obo_surface_forms(obo_path):
    """Lowercase surface forms (name + synonyms) from a Textpresso OBO.

    Pruning the index to these cuts it from ~37 MB to ~1 MB for the 3,299-paper
    MaizeOpenAccess corpus with no loss of coverage, because only forms the annotator
    can actually match are ever looked up.
    """
    forms, syn_re = set(), re.compile(r'^synonym:\s*"(.*?)"')
    with open(obo_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("name: "):
                forms.add(line[6:].strip().lower())
            elif line.startswith("synonym:"):
                m = syn_re.match(line)
                if m:
                    forms.add(m.group(1).strip().lower())
    forms.discard("")
    return forms


def build(accessions, email=None, sleep=0.4, verbose=True, keep_forms=None):
    out, misses = {}, []
    for acc in accessions:
        doi = accession_to_doi(acc)
        try:
            pmcid = doi_to_pmcid(doi, email)
            if not pmcid:
                misses.append((acc, "no PMC record")); continue
            xml = fetch_jats(pmcid, email)
            st = italic_stats(xml)
            if not st:
                misses.append((acc, "no <body> in JATS")); continue
            terms = {k: [v["italic"], v["total"]] for k, v in st.items()
                     if v["total"] >= 1 and (keep_forms is None or k in keep_forms)}
            if not terms:
                misses.append((acc, "no OBO-known forms")); continue
            out[acc] = terms
            if verbose:
                it = sum(v["italic"] for v in st.values())
                print(f"[ok] {acc} PMC{pmcid}: {len(st)} forms, {it} italic tokens",
                      file=sys.stderr)
        except Exception as e:
            misses.append((acc, repr(e)[:60]))
        time.sleep(sleep)
    return out, misses


def lookup(sidecar: dict, accession: str, term: str):
    """-> italic rate in [0,1] for `term` in `accession`, or None if unknown."""
    rec = sidecar.get(accession)
    if not rec:
        return None
    e = rec.get(term.lower())
    if not e or not e[1]:
        return None
    return e[0] / float(e[1])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accession"); ap.add_argument("--accession-file")
    ap.add_argument("--out", default="italics_sidecar.json")
    ap.add_argument("--email", help="contact address for NCBI E-utilities (optional)")
    ap.add_argument("--obo", action="append", default=None,
                    help="prune the index to this OBO's surface forms "
                         "(repeatable; strongly recommended -- 37 MB -> ~1 MB)")
    ap.add_argument("--rate", type=float, default=2.9,
                    help="requests/second (NCBI allows 3 without an API key, 10 with)")
    a = ap.parse_args()

    accs = [a.accession] if a.accession else [
        l.strip() for l in open(a.accession_file) if l.strip()]
    set_rate(a.rate)
    keep = None
    if a.obo:
        keep = set()
        for p in a.obo:
            keep |= obo_surface_forms(p)
        print("[info] pruning to %d OBO surface forms" % len(keep), file=sys.stderr)
    out, misses = build(accs, a.email, sleep=0.0, keep_forms=keep)
    json.dump(out, open(a.out, "w"))
    print(f"[done] {len(out)}/{len(accs)} papers -> {a.out}", file=sys.stderr)
    for acc, why in misses[:10]:
        print(f"  [miss] {acc}: {why}", file=sys.stderr)


if __name__ == "__main__":
    main()
