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

import argparse, collections, json, os, re, sys, threading, time, unicodedata, urllib.error, urllib.parse, urllib.request
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

#: The ingest's character fold, applied before tokenising so index keys are
#: spelled the way the CAS text (and so the annotation) spells them. Mirrors
#: jats_text._map_char and PdfTextNormalizer.h in the jats-ingest work: without
#: it, `PYR1‐LIKE` splits into `pyr1` + `like` here while the CAS says
#: `pyr1-like`, the lookup misses, and the rule abstains on a form it could judge.
#: Both paths: U+2010/U+2011 hyphens -> "-", soft hyphen dropped, characters
#: outside the BMP NFKC-folded (mathematical italic letters -> plain).
_FOLD = {0x2010: "-", 0x2011: "-", 0x00AD: None}
#: PDF path only: zero-width characters, BOM and replacement characters dropped;
#: ligatures (U+FB00-FB06) NFKC-folded with the non-BMP characters below.
_FOLD_PDF = {**_FOLD, **{c: None for c in (
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062, 0x2063,
    0x2064, 0xFEFF, 0xFFFD, 0xFFFE, 0xFFFF)}}
_NFKC_RE = re.compile("[\U00010000-\U0010FFFF]")
_NFKC_PDF_RE = re.compile("[ﬀ-ﬆ\U00010000-\U0010FFFF]")
_PRIVATE_USE_RE = re.compile("[-\U000F0000-\U0010FFFF]")


def _nfkc_bmp(m):
    folded = unicodedata.normalize("NFKC", m.group(0))
    return "".join(c if ord(c) <= 0xFFFF else "�" for c in folded)


def fold_text(text: str, pdf: bool = False) -> str:
    """Apply the ingest's character fold (see _FOLD). pdf=True adds the
    PDF-only rules: ligatures, zero-width characters and private-use glyphs."""
    if pdf:
        text = _PRIVATE_USE_RE.sub("", text).translate(_FOLD_PDF)
        return _NFKC_PDF_RE.sub(_nfkc_bmp, text)
    return _NFKC_RE.sub(_nfkc_bmp, text.translate(_FOLD))


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


#: A JATS body with less italic text than this is treated as carrying no
#: italic markup at all (some publishers deposit none -- 15-73 KB bodies with
#: zero <italic> tags, while the PDF italicises every gene name) and abstains,
#: so pdf_italics.py fills it. Same threshold as pdf_italics.MIN_ITALIC_CHARS.
MIN_ITALIC_CHARS = 20

#: PMC candidates tried per DOI. esearch's [DOI] field also returns papers that
#: merely cite the DOI, newest first: 77 of 1,625 accessions in the 2026-10-21
#: build had been indexed from someone else's paper. Each candidate is checked
#: against the DOI in its own <article-meta>.
MAX_CANDIDATES = 5


def doi_to_pmcids(doi: str, email: str | None = None) -> list[str]:
    """-> PMC ids esearch associates with doi. Not all of them ARE that paper;
    see MAX_CANDIDATES and article_doi()."""
    params = {"db": "pmc", "term": f'"{doi}"[DOI]', "retmode": "xml",
              "retmax": str(MAX_CANDIDATES)}
    if email:
        params["email"] = email
    raw = _get(EUTILS + "esearch.fcgi?" + urllib.parse.urlencode(params)).decode()
    return re.findall(r"<Id>(\d+)</Id>", raw)


def doi_to_pmcid(doi: str, email: str | None = None) -> str | None:
    """First esearch hit, unverified. Prefer build(), which checks the DOI."""
    ids = doi_to_pmcids(doi, email)
    return ids[0] if ids else None


def fetch_jats(pmcid: str, email: str | None = None) -> bytes | None:
    params = {"db": "pmc", "id": pmcid, "retmode": "xml"}
    if email:
        params["email"] = email
    return _get(EUTILS + "efetch.fcgi?" + urllib.parse.urlencode(params))


def article_doi(xml_bytes: bytes) -> str | None:
    """The DOI the article declares for itself (front/article-meta), lowercased."""
    root = ET.fromstring(xml_bytes)
    for article in root.iter("article"):
        meta = article.find("front/article-meta")
        if meta is None:
            continue
        for aid in meta.findall("article-id"):
            if (aid.get("pub-id-type") or "").lower() == "doi" and aid.text:
                return aid.text.strip().lower()
    return None


class _Cache:
    """Optional on-disk cache of esearch results and efetch XML, so a rebuild
    after a tokeniser or OBO change needs no NCBI round-trips. Only successful
    responses are stored; a cache dir from an older run is safe to reuse."""

    def __init__(self, root):
        self.root = root
        if root:
            os.makedirs(os.path.join(root, "esearch"), exist_ok=True)
            os.makedirs(os.path.join(root, "xml"), exist_ok=True)

    def _path(self, kind, key, ext):
        return os.path.join(self.root, kind, re.sub(r"[^\w.\-]", "_", key) + ext)

    def pmcids(self, acc, fetch):
        if not self.root:
            return fetch()
        p = self._path("esearch", acc, ".json")
        if os.path.exists(p):
            with open(p) as fh:
                return json.load(fh)
        ids = fetch()
        with open(p, "w") as fh:
            json.dump(ids, fh)
        return ids

    def xml(self, pmcid, fetch):
        if not self.root:
            return fetch()
        p = self._path("xml", "PMC" + pmcid, ".xml")
        if os.path.exists(p):
            with open(p, "rb") as fh:
                return fh.read()
        data = fetch()
        with open(p, "wb") as fh:
            fh.write(data)
        return data


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


def italic_stats(xml_bytes: bytes, info: dict | None = None) -> dict[str, dict]:
    """-> {surface_form_lower: {'italic': n, 'total': n}} over the article body,
    with references, acknowledgments and back matter excluded (see SKIP_TAGS).

    The exclusion is load-bearing for the strict-zero rule: counting reference
    titles would add non-italic occurrences to forms that are italicised
    everywhere they are actually used, and would make `never_italicised()` fire
    on genuine gene mentions.

    If `info` is given, info["italic_chars"] receives the amount of italic text
    in the body (whitespace excluded), for the MIN_ITALIC_CHARS check."""
    stats: dict[str, dict] = {}
    italic_chars = 0
    root = ET.fromstring(xml_bytes)
    for article in root.iter("article"):
        body = article.find(".//body")
        if body is None:
            continue
        for text, is_italic in _runs(body):
            if not text:
                continue
            if is_italic:
                italic_chars += len("".join(text.split()))
            for m in TOKEN_RE.finditer(fold_text(text)):
                key = m.group(1).lower()
                e = stats.setdefault(key, {"italic": 0, "total": 0})
                e["total"] += 1
                if is_italic:
                    e["italic"] += 1
    if info is not None:
        info["italic_chars"] = italic_chars
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


def _accession_key(s):
    """Compare DOIs in accession space. Accessions replace '/' (and ':', as in
    `10.1042/0264-6021:3540037`) with '_', so accession_to_doi() cannot invert
    them: `10.1093_aob_mcac132` becomes `10.1093/aob_mcac132`. esearch finds the
    paper anyway, but an exact DOI comparison would reject it."""
    return re.sub(r"[^a-z0-9.\-]", "_", (s or "").lower())


def _verified_jats(acc, email, cache):
    """-> (pmcid, xml) for the PMC record whose own DOI is acc's, or (None, why)."""
    doi = accession_to_doi(acc).lower()
    ids = cache.pmcids(acc, lambda: doi_to_pmcids(doi, email))
    if not ids:
        return None, "no PMC record"
    for pmcid in ids[:MAX_CANDIDATES]:
        xml = cache.xml(pmcid, lambda: fetch_jats(pmcid, email))
        if _accession_key(article_doi(xml)) == _accession_key(acc):
            return pmcid, xml
    return None, "no PMC record with this DOI (esearch hits are citing papers)"


def build(accessions, email=None, sleep=0.4, verbose=True, keep_forms=None,
          cache_dir=None):
    out, misses = {}, []
    cache = _Cache(cache_dir)
    for acc in accessions:
        try:
            pmcid, xml = _verified_jats(acc, email, cache)
            if pmcid is None:
                misses.append((acc, xml)); continue
            info = {}
            st = italic_stats(xml, info)
            if not st:
                misses.append((acc, "no <body> in JATS")); continue
            if info["italic_chars"] < MIN_ITALIC_CHARS:
                misses.append((acc, "no italic markup in JATS body")); continue
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
    ap.add_argument("--cache-dir",
                    help="keep esearch results and JATS XML here; a rebuild with "
                         "the same cache needs no NCBI requests")
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
    out, misses = build(accs, a.email, sleep=0.0, keep_forms=keep,
                        cache_dir=a.cache_dir)
    json.dump(out, open(a.out, "w"))
    print(f"[done] {len(out)}/{len(accs)} papers -> {a.out}", file=sys.stderr)
    for why, n in collections.Counter(w.split(":")[0] for _, w in misses).most_common():
        print(f"  [miss] {why}: {n}", file=sys.stderr)
    for acc, why in misses:
        if why.startswith(("RuntimeError", "HTTPError", "URLError")):
            print(f"  [error] {acc}: {why}  (transient? re-run with --accession)",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
