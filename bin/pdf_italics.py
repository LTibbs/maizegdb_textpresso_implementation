#!/usr/bin/env python3
"""
pdf_italics.py -- italic counts from the PDFs Textpresso already ingests.

`jats_italics.py` builds the italic index from PubMed Central JATS, but efetch
returns a JATS <body> for only 1,627 of the 3,299 MaizeOpenAccess papers. The
rest are PMC "free to read" without a reuse licence (Genetics, PNAS, ...) or
have no PMC record at all, and no NCBI or Europe PMC bulk route serves them.
Every paper does have its source PDF, though, and the PDF still carries the
italics in its fonts -- it is `pdf2tpcas` that discards them.

This reads the font of every text span with PyMuPDF and writes the same index
shape as `jats_italics.py`: {accession: {surface_form_lower: [n_italic, n_total]}}.
With --jats-index the output is that index plus PDF entries for the accessions
it lacks; JATS always wins where both exist.

A span is italic when any of these says so:
  - the PyMuPDF italic flag,
  - the font name (`Arial-ItalicMT`, `MinionPro-It`, `AdvOTf0bf83d5.I`),
  - a non-zero /ItalicAngle in the font descriptor.
Some typesetters (Advent 3B2: `AdvP4041BD`) emit opaque subset fonts with none
of the three. For those, a font is calibrated as italic per paper when it is not
the main body font and carries conventionally italic anchor words (`Zea mays`,
`in vivo`, `E. coli`, ...). Erring towards "italic" is the safe direction: it
can only make the strict-zero rule keep more.

A paper with no detectable italics anywhere -- scanned, OCR text layer, or
uncalibratable fonts -- is left out of the index, so the rule abstains rather
than reading "never italicised" for every form.

Validated against the JATS index on the 1,500 papers that have both
(2026-10-21), on the forms the rule can act on (<= 4 chars, not
registered/identifier): 98.0% agreement on the zero/non-zero decision;
PDF-says-zero-but-JATS-italic (the direction that would wrongly drop) 0.23%.
14% of the corpus abstains, mostly scanned 1980s-90s backfiles.

Usage:
  python3 pdf_italics.py --accession-file accs.txt \\
      --pdf-root /data/textpresso/raw_files/pdf/MaizeOpenAccess \\
      --obo zmays_genes_20260708.obo \\
      --jats-index .data/italics/MaizeOpenAccess.jats.json \\
      --out .data/italics/MaizeOpenAccess.json

Needs PyMuPDF (`pip install pymupdf`; AGPL). Build-time only -- the server
reads the JSON and never imports this.
"""
from __future__ import annotations

import argparse, collections, json, os, re, sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jats_italics import TOKEN_RE, fold_text, obo_surface_forms  # noqa: E402

#: A line that opens back matter. Everything from here to the end of the PDF is
#: excluded, mirroring jats_italics' SKIP_TAGS/SKIP_SEC: counting reference
#: titles would add non-italic occurrences to genuinely italicised forms.
#: Deliberately narrower than SKIP_SEC -- a PDF has no section tree, so a stop
#: line ends the body, and a "Funding" or "Data availability" heading can sit
#: before the Discussion in some layouts.
STOP_RE = re.compile(
    r"^\s*(\d+\.?\s*)?(references?(\s+and\s+notes)?|literature\s+cited|bibliography"
    r"|works\s+cited|acknowledge?ments?)\s*:?\s*$", re.I)

#: Font names that declare an italic face. Advent 3B2 uses `.I` (OpenType)
#: and `-O`/`-BO` (oblique, e.g. `AdvPSUnv-O`).
NAMED_ITALIC_RE = re.compile(
    r"italic|oblique|[-,+.](it|bi|boldit|mediumit|semiboldit|ital|i|o|bo)$|it$", re.I)

#: Font names that say *anything* about style; such a font is never calibrated
#: from anchors, because its name is already trusted.
STYLED_RE = re.compile(
    r"italic|oblique|bold|regular|roman|medium|light|semibold|book"
    r"|[-,.](it|bi|b|i|bd|rg|o|bo)$", re.I)

#: Words italicised by convention in plant-biology prose, used to recognise the
#: italic face among opaque fonts.
ANCHORS = frozenset({
    "mays", "zea", "teosinte", "parviglumis", "arabidopsis", "thaliana",
    "oryza", "sativa", "sorghum", "bicolor", "triticum", "aestivum", "hordeum",
    "vulgare", "glycine", "nicotiana", "benthamiana", "agrobacterium",
    "tumefaciens", "escherichia", "coli", "saccharomyces", "cerevisiae",
    "vivo", "vitro", "situ", "planta", "silico", "al",
})

#: Minimum anchor hits for an opaque font, and its minimum share of all anchor
#: hits in the paper.
CALIBRATE_MIN_HITS = 2
CALIBRATE_MIN_SHARE = 0.3

#: A paper whose italic fonts cover fewer characters than this is treated as
#: having no recoverable italics and abstains wholesale.
MIN_ITALIC_CHARS = 20


def _fitz():
    try:
        import fitz
    except ImportError:
        sys.exit("pdf_italics.py needs PyMuPDF: pip install pymupdf")
    fitz.TOOLS.mupdf_display_errors(False)
    return fitz


def _base(fontname: str) -> str:
    """'JBDNOH+AdvP4041BD' -> 'AdvP4041BD' (strip the subset prefix)."""
    return fontname.split("+")[-1]


def font_italic_angles(doc) -> dict[str, float]:
    """-> {base font name: /ItalicAngle}, following Type0 descendant fonts."""
    out: dict[str, float] = {}
    for page in doc:
        for f in page.get_fonts(full=True):
            base = _base(f[3])
            if base in out:
                continue
            angle = 0.0
            obj = doc.xref_object(f[0])
            fd = re.search(r"/FontDescriptor (\d+) 0 R", obj)
            if not fd:
                desc = re.search(r"/DescendantFonts \[?\s*(\d+) 0 R", obj)
                if desc:
                    fd = re.search(r"/FontDescriptor (\d+) 0 R",
                                   doc.xref_object(int(desc.group(1))))
            if fd:
                m = re.search(r"/ItalicAngle (-?[\d.]+)",
                              doc.xref_object(int(fd.group(1))))
                if m:
                    angle = float(m.group(1))
            out[base] = angle
    return out


def body_spans(doc):
    """-> ([(font, flags, text)], stopped) for the body, up to back matter.

    Span text is passed through the ingest's PDF character fold, so tokens are
    spelled as the CAS spells them (`PYR1\u2010LIKE` -> `pyr1-like`)."""
    spans = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                line_spans = [(_base(s["font"]), s["flags"], fold_text(s["text"], pdf=True))
                              for s in line["spans"]]
                if STOP_RE.match("".join(t for _, _, t in line_spans)):
                    return spans, True
                spans.extend(line_spans)
    return spans, False


def italic_fonts(spans, angles) -> tuple[set[str], set[str]]:
    """-> (all italic fonts, the subset calibrated from anchors)."""
    chars, anchors = collections.Counter(), collections.Counter()
    declared = set()
    for font, flags, text in spans:
        chars[font] += len(text)
        if flags & 2 or NAMED_ITALIC_RE.search(font) or angles.get(font, 0.0):
            declared.add(font)
        for m in TOKEN_RE.finditer(text):
            if m.group(1).lower() in ANCHORS:
                anchors[font] += 1
    body = chars.most_common(1)[0][0] if chars else None
    total = sum(anchors.values())
    calibrated = {f for f, n in anchors.items()
                  if f != body and f not in declared and not STYLED_RE.search(f)
                  and n >= CALIBRATE_MIN_HITS and n >= CALIBRATE_MIN_SHARE * total}
    return declared | calibrated, calibrated


def italic_stats(pdf_path, keep_forms=None) -> tuple[dict | None, dict]:
    """-> ({surface_form_lower: [n_italic, n_total]} or None, info).

    None means abstain: the paper shows no recoverable italics, so absence of
    italics is not evidence. `info` records why, for the build summary.
    """
    fitz = _fitz()
    with fitz.open(pdf_path) as doc:
        angles = font_italic_angles(doc)
        spans, stopped = body_spans(doc)
    fonts, calibrated = italic_fonts(spans, angles)
    info = {"stopped_at_back_matter": stopped, "calibrated_fonts": sorted(calibrated)}
    chars = collections.Counter()
    for font, _, text in spans:
        chars[font] += len(text)
    if sum(chars[f] for f in fonts) < MIN_ITALIC_CHARS:
        info["abstain"] = "no detectable italics"
        return None, info
    # Tokenised per span, as jats_italics tokenises per text run.
    stats: dict[str, list[int]] = {}
    for font, _, text in spans:
        italic = font in fonts
        for m in TOKEN_RE.finditer(text):
            key = m.group(1).lower()
            if keep_forms is not None and key not in keep_forms:
                continue
            e = stats.setdefault(key, [0, 0])
            e[1] += 1
            e[0] += italic
    return stats, info


def pdf_path_for(pdf_root, accession):
    """Textpresso raw layout is {root}/{acc}/{acc}.pdf; also accept {root}/{acc}.pdf."""
    for p in (os.path.join(pdf_root, accession, accession + ".pdf"),
              os.path.join(pdf_root, accession + ".pdf")):
        if os.path.exists(p):
            return p
    return None


def _one(args):
    acc, pdf_root, keep = args
    path = pdf_path_for(pdf_root, acc)
    if not path:
        return acc, None, "no PDF"
    try:
        stats, info = italic_stats(path, keep)
    except Exception as e:
        return acc, None, "unreadable: " + repr(e)[:60]
    if stats is None:
        return acc, None, info["abstain"]
    if not stats:
        return acc, None, "no OBO-known forms"
    return acc, stats, None


def build(accessions, pdf_root, keep_forms=None, workers=4, verbose=True):
    """-> ({accession: {form: [n_italic, n_total]}}, [(accession, reason)])."""
    out, misses = {}, []
    jobs = [(a, pdf_root, keep_forms) for a in accessions]
    with ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
        for i, (acc, stats, why) in enumerate(ex.map(_one, jobs, chunksize=4), 1):
            if why:
                misses.append((acc, why))
            else:
                out[acc] = stats
            if verbose and i % 100 == 0:
                print(f"[info] {i}/{len(jobs)} PDFs, {len(out)} indexed",
                      file=sys.stderr)
    return out, misses


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accession"); ap.add_argument("--accession-file")
    ap.add_argument("--pdf-root", required=True,
                    help="directory holding {accession}/{accession}.pdf")
    ap.add_argument("--out", default="italics_sidecar.json")
    ap.add_argument("--obo", action="append", default=None,
                    help="prune the index to this OBO's surface forms (repeatable)")
    ap.add_argument("--jats-index",
                    help="existing jats_italics.py index; its entries are kept as-is "
                         "and only accessions missing from it are read from PDF")
    ap.add_argument("--provenance-out",
                    help="write {accession: 'jats'|'pdf'} alongside the index")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    a = ap.parse_args()

    if not (a.accession or a.accession_file):
        ap.error("give --accession or --accession-file")
    accs = [a.accession] if a.accession else [
        l.strip() for l in open(a.accession_file) if l.strip()]
    keep = None
    if a.obo:
        keep = set()
        for p in a.obo:
            keep |= obo_surface_forms(p)
        print("[info] pruning to %d OBO surface forms" % len(keep), file=sys.stderr)

    jats = {}
    if a.jats_index:
        with open(a.jats_index, encoding="utf-8") as fh:
            jats = json.load(fh)
    todo = [x for x in accs if x not in jats]
    print(f"[info] {len(accs) - len(todo)} accessions from JATS, "
          f"{len(todo)} to read from PDF", file=sys.stderr)

    _fitz()   # fail fast on a missing dependency, before forking workers
    pdf, misses = build(todo, a.pdf_root, keep, a.workers)
    merged = {**pdf, **jats}
    with open(a.out, "w") as fh:
        json.dump(merged, fh)
    if a.provenance_out:
        with open(a.provenance_out, "w") as fh:
            json.dump({k: ("jats" if k in jats else "pdf") for k in merged}, fh,
                      indent=0, sort_keys=True)

    print(f"[done] {len(merged)}/{len(accs)} papers -> {a.out} "
          f"({len(accs) - len(todo)} JATS, {len(pdf)} PDF)",
          file=sys.stderr)
    for why, n in collections.Counter(w.split(":")[0] for _, w in misses).most_common():
        print(f"  [miss] {why}: {n}", file=sys.stderr)


if __name__ == "__main__":
    main()
