#!/usr/bin/env python3
"""add_jats_figures.py -- give JATS-built papers the figures extracted from their PDFs.

A CAS-1 built from JATS (articles2cas -t 5, TjTokenizer) has no figure
annotations, so neither Textpresso Central's viewer nor the GUI viewer shows
figures for it. pdf2txtimg has already extracted every paper's images from its
PDF (raw_files/pdf/<corpus>/<acc>/<acc>.NNNNN.img-NNN.{png,jpg}); this places
them in the JATS text as org.apache.uima.textpresso.image annotations, the same
type TdTokenizer writes for PDF-built papers and both viewers draw.

Placement, per PDF page that has images:
  1. caption -- a JATS figure caption is found in that page's text (the
     per-page .txt from pdf2txtimg): every image on the page is anchored at
     the first such caption, spanning it;
  2. page    -- else a run of the page's text is found in the JATS text: the
     images are anchored there (zero-length), as TdTokenizer anchors images to
     their page;
  3. near    -- else (a page with no text layer: mostly image-only pages of
     merged supplements) where the nearest earlier page was found, else the
     nearest later one;
  4. end     -- else at the "End of Article" marker, so no image is lost.
On MaizeOpenAccess (2026-11-02), 96% of JATS figures were found on a PDF page
(150-paper sample); see the run's summary line for the per-rule split.

Two modes, both over the JATS papers of a corpus (raw_files/xml/<corpus>/<acc>/
<acc>.jats.json):

  --jats-json   add an "images" array (code-point offsets, like the rest of the
                file) to each jats.json, for TjTokenizer to emit at the next
                tokenize. Run after pdf2txtimg and before articles2cas -t 5
                (process_batch.sh does). fetch_jats.py rewrites jats.json
                without it, so re-run this after any re-fetch.
  --cas DIR     add the annotations to already-built CAS files under DIR
                (tpcas-1/<corpus>, tpcas-2/<corpus>; give both). Offsets are
                converted to UTF-16. A CAS that already has image annotations
                is left alone unless --replace. Takes effect in both viewers
                at once: they read tpcas-2 directly, and the Lucene index does
                not store images, so no re-index is needed.

The viewers fetch the files from tpcas-1/<corpus>/<acc>/images/, which
sorghumbase_textpresso_implementation/scripts/stage_cas1_images.sh fills.

Runs inside the Textpresso container (Python 3.6, stdlib only), as root since
the CAS trees are root-owned:

  docker exec -i agr-textpresso-textpresso-1 python3 - -c MaizeOpenAccess \\
      --cas /data/textpresso/tpcas-1/MaizeOpenAccess \\
      --cas /data/textpresso/tpcas-2/MaizeOpenAccess < bin/add_jats_figures.py
"""
import argparse, collections, gzip, json, os, re, sys, unicodedata
from xml.sax.saxutils import quoteattr

IMG_RE = re.compile(r"\.(\d{5})\.img-\d{3}\.(?:png|jpg)$")
PAGE_TXT_RE = re.compile(r"\.(\d{5})\.txt$")
# normalized caption label: "figure1", "fig2b", "supplementaryfigures3" ...
LABEL_RE = re.compile(r"^(?:supplementary)?fig(?:ure)?s?\d+[a-z]?")
END_MARKER = "\nEnd of Article"
SNIPPET = 60          # normalized chars matched between PDF page and JATS text
MIN_SNIPPET = 25
PAGE_PROBES = 8       # page-text runs tried per page, 200 chars apart


def normalize(s):
    """Lowercase ASCII alphanumerics after NFKC (folds ligatures, full-width).

    Returns (normalized, index) where index[k] is the position in s of the
    character normalized[k] came from.
    """
    out, index = [], []
    for i, ch in enumerate(s):
        for c in unicodedata.normalize("NFKC", ch).lower():
            if c < "\x80" and c.isalnum():   # str.isascii() is 3.7+; the container has 3.6
                out.append(c)
                index.append(i)
    return "".join(out), index


def pdf_pages(raw_dir, acc):
    """{page: normalized text} and {page: [image basenames]} from pdf2txtimg output."""
    texts, images = {}, collections.defaultdict(list)
    for name in os.listdir(raw_dir):
        if not name.startswith(acc + "."):
            continue
        m = IMG_RE.search(name)
        if m:
            images[int(m.group(1))].append(name)
            continue
        m = PAGE_TXT_RE.search(name)
        if m:
            with open(os.path.join(raw_dir, name), encoding="utf-8", errors="replace") as f:
                texts[int(m.group(1))] = normalize(f.read())[0]
    return texts, {p: sorted(n) for p, n in images.items()}


def place(text, figures, page_texts, page_images):
    """[(begin, end, page, basename, how)] in code points; how is caption/page/end."""
    ntext, index = normalize(text)
    caption_at = {}                       # page -> (begin, end) of first caption found on it
    for fig in figures:
        ncap = LABEL_RE.sub("", normalize(text[fig["begin"]:fig["end"]])[0])
        for snip in (ncap[:SNIPPET], ncap[40:40 + SNIPPET]):
            if len(snip) < MIN_SNIPPET:
                continue
            pages = sorted(p for p, t in page_texts.items() if snip in t)
            if pages:
                if pages[0] not in caption_at or fig["begin"] < caption_at[pages[0]][0]:
                    caption_at[pages[0]] = (fig["begin"], fig["end"])
                break
    page_at = {}                          # page -> where its text is in the JATS text
    for page, t in page_texts.items():
        for off in range(0, PAGE_PROBES * 200, 200):
            snip = t[off:off + SNIPPET]
            if len(snip) < SNIPPET:
                break
            k = ntext.find(snip)
            if k >= 0:
                page_at[page] = index[k]
                break
    end = text.rfind(END_MARKER)
    end = end + 1 if end >= 0 else len(text)
    last = max(list(page_texts) + list(page_images) + [0])
    placed = []
    for page in sorted(page_images):
        if page in caption_at:
            (b, e), how = caption_at[page], "caption"
        elif page in page_at:
            b = e = page_at[page]
            how = "page"
        else:
            # no text layer (an image-only supplement page, mostly): next to
            # the nearest earlier page found in the text, else the nearest later
            near = [p for p in range(page - 1, 0, -1) if p in page_at][:1] or \
                   [p for p in range(page + 1, last + 1) if p in page_at][:1]
            b = e = page_at[near[0]] if near else end
            how = "near" if near else "end"
        for name in page_images[page]:
            placed.append((b, e, page, name, how))
    return placed


def jats_papers(base, corpus, only):
    xml_root = os.path.join(base, "raw_files", "xml", corpus)
    for acc in sorted(os.listdir(xml_root)):
        if only and acc not in only:
            continue
        path = os.path.join(xml_root, acc, acc + ".jats.json")
        if os.path.isfile(path):
            yield acc, path


def utf16_offsets(text):
    """offset[cp] = UTF-16 offset of code point cp (len(text)+1 entries)."""
    out, u = [], 0
    for ch in text:
        out.append(u)
        u += 2 if ord(ch) > 0xFFFF else 1
    out.append(u)
    return out


ELEMENT_RE = re.compile(r"<textpresso:image [^>]*/>\s*")
ID_RE = re.compile(r'xmi:id="(\d+)"')
AID_RE = re.compile(r' aid="(\d+)"')


def read_jats_cas(path):
    """The CAS's XMI, or None if it was not built from JATS (never touch those)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        xmi = f.read()
    return xmi if re.search(r'<textpresso:rawsource [^>]*value="jats"', xmi) else None


def strip_images(xmi):
    """Remove every image annotation and its id in the view's member list."""
    old_ids = {m.group(1) for el in ELEMENT_RE.findall(xmi) for m in [ID_RE.search(el)] if m}
    xmi = ELEMENT_RE.sub("", xmi)
    return re.sub(r'(<cas:View [^>]*members=")([^"]*)(")',
                  lambda m: m.group(1) + " ".join(i for i in m.group(2).split() if i not in old_ids) + m.group(3),
                  xmi, count=1)


def write_cas(path, xmi):
    tmp = path + ".tmp"
    # level 6 = gzip's default, as the pipeline writes them; Python's default 9
    # takes minutes on the largest CAS (75 MB compressed)
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
        f.write(xmi)
    os.chmod(tmp, os.stat(path).st_mode & 0o7777)
    os.replace(tmp, path)


def update_cas(path, corpus, acc, placed, text, replace):
    """Add image annotations to one CAS file. Returns a status string."""
    xmi = read_jats_cas(path)
    if xmi is None:
        return "not-jats"
    if "<textpresso:image " in xmi:
        if not replace:
            return "has-images"
        xmi = strip_images(xmi)
    doc = re.search(r'<tcas:DocumentAnnotation [^>]*end="(\d+)"', xmi)
    u16 = utf16_offsets(text)
    if not doc or int(doc.group(1)) != u16[-1]:
        return "text-mismatch"            # jats.json changed since this CAS was built
    view = xmi.rfind("<cas:View ")
    if view < 0:
        return "no-view"
    next_id = max(int(i) for i in ID_RE.findall(xmi)) + 1
    next_aid = max([int(a) for a in AID_RE.findall(xmi)] or [0]) + 1
    elements, ids = [], []
    for b, e, page, name, _ in placed:
        ids.append(str(next_id))
        elements.append('<textpresso:image xmi:id="%d" sofa="1" begin="%d" end="%d" filename=%s page="%d" aid="%d"/>'
                        % (next_id, u16[b], u16[e], quoteattr("%s/%s/images/%s" % (corpus, acc, name)),
                           page, next_aid))
        next_id += 1
        next_aid += 1
    xmi = xmi[:view] + "\n".join(elements) + "\n" + xmi[view:]
    xmi = re.sub(r'(<cas:View [^>]*members=")([^"]*)(")',
                 lambda m: m.group(1) + (m.group(2) + " " + " ".join(ids)).strip() + m.group(3),
                 xmi, count=1)
    write_cas(path, xmi)
    return "updated"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--corpus", required=True)
    ap.add_argument("--base", default="/data/textpresso", help="data root (default /data/textpresso)")
    ap.add_argument("--jats-json", action="store_true", help="write the 'images' array into each jats.json")
    ap.add_argument("--cas", action="append", default=[], metavar="DIR",
                    help="CAS tree (<base>/tpcas-N/<corpus>) to add image annotations to; repeatable")
    ap.add_argument("--replace", action="store_true",
                    help="replace image annotations already in a JATS CAS instead of skipping it")
    ap.add_argument("--accession", action="append", default=[],
                    help="only this accession (repeatable); default every JATS paper")
    ap.add_argument("--accession-file", help="only the accessions listed in this file, one per line")
    ap.add_argument("--dry-run", action="store_true", help="report placements, write nothing")
    ap.add_argument("--remove", action="store_true",
                    help="undo: strip the image annotations from the JATS CASes under --cas")
    a = ap.parse_args()
    if not a.jats_json and not a.cas and not a.dry_run:
        ap.error("nothing to do: give --jats-json, --cas DIR, or --dry-run")
    if a.remove and (a.jats_json or not a.cas):
        ap.error("--remove takes --cas DIR only")

    only = set(a.accession)
    if a.accession_file:
        with open(a.accession_file, encoding="utf-8") as f:
            only.update(line.strip() for line in f if line.strip())
        if not only:
            print("%s: accession file is empty, nothing to do" % a.corpus)
            return
    raw_pdf = os.path.join(a.base, "raw_files", "pdf", a.corpus)
    how_n, status_n = collections.Counter(), collections.Counter()
    papers = with_images = 0
    for acc, jpath in jats_papers(a.base, a.corpus, only):
        papers += 1
        if a.remove:
            for tree in a.cas:
                cas = os.path.join(tree, acc, acc + ".tpcas.gz")
                xmi = read_jats_cas(cas) if os.path.isfile(cas) else None
                if xmi is not None and "<textpresso:image " in xmi:
                    write_cas(cas, strip_images(xmi))
                    status_n["removed"] += 1
            continue
        raw_dir = os.path.join(raw_pdf, acc)
        if not os.path.isdir(raw_dir):
            status_n["no-raw-dir"] += 1
            continue
        with open(jpath, encoding="utf-8") as f:
            doc = json.load(f)
        page_texts, page_images = pdf_pages(raw_dir, acc)
        placed = place(doc["text"], doc.get("figures", []), page_texts, page_images)
        if not placed:
            continue
        with_images += 1
        how_n.update(p[4] for p in placed)
        if a.dry_run:
            continue
        if a.jats_json:
            doc["images"] = [{"begin": b, "end": e, "page": page,
                              "filename": "%s/%s/images/%s" % (a.corpus, acc, name)}
                             for b, e, page, name, _ in placed]
            tmp = jpath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
            os.replace(tmp, jpath)
            status_n["jats-json"] += 1
        for tree in a.cas:
            cas = os.path.join(tree, acc, acc + ".tpcas.gz")
            if not os.path.isfile(cas):
                status_n["no-cas"] += 1
                continue
            s = update_cas(cas, a.corpus, acc, placed, doc["text"], a.replace)
            status_n[s] += 1
            if s in ("text-mismatch", "no-view"):
                print("  %s: %s" % (s, cas), file=sys.stderr)
    total = sum(how_n.values())
    if a.remove:
        print("%s: %d JATS papers; image annotations removed from %d CAS files"
              % (a.corpus, papers, status_n["removed"]))
        return
    print("%s: %d JATS papers, %d with PDF images; %d images placed by %s"
          % (a.corpus, papers, with_images, total,
             ", ".join("%s %d (%.0f%%)" % (k, how_n[k], 100.0 * how_n[k] / total)
                       for k in ("caption", "page", "near", "end") if total)))
    if status_n:
        print("  " + ", ".join("%s %d" % kv for kv in sorted(status_n.items())))


if __name__ == "__main__":
    main()
