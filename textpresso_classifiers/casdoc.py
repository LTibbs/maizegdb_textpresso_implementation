"""Parse a whole Textpresso CAS2 (.tpcas.gz) file into a renderable document.

casannot.py extracts sentence-level ontology annotations for search
filtering; this module extracts everything a full-text reader needs -- the
paper text plus page breaks, paragraph breaks, figure positions, sections,
sentence spans and lexical annotations -- all as character offsets into one
clean display string.

Two CAS layouts exist, distinguished by <textpresso:rawsource value="...">:

  tai  -- text assembled from per-page .txt files (e.g. all of MaizeOA).
          The sofaString is plain text with "\\n" line breaks; structure comes
          from separate <textpresso:page>, <textpresso:dblbrk> (paragraph
          break) and <textpresso:image filename=... page=...> elements.
  pdf  -- text from pdf2tpcas. Structure is inline in the sofaString as
          <_pdf .../> pseudo-tags (see agr_textpresso
          libtpc/cas-generators/pdf2tpcas/ElementCluster.cpp and PdfInfo.cpp):
            _page=N   start of page N        _sbr       paragraph break
            _cr       new line               _image=P   embedded image file P
            _fsc/_fnc/_ydiff/_hbr            font/position changes (dropped)
          Annotation offsets index into the sofaString *with* these tags in
          place, so they're stripped here and every offset is remapped.

Either way the result has the same shape (see parse_cas_document), and
figures are reported by file basename only -- same as Textpresso Central's
Viewer.cpp, which ignores the directory part of the stored path.
"""

import gzip
import html
import importlib.util
import os
import re
from array import array

if __package__:
    from . import casannot as _ca
else:
    # Loaded as a bare file (the GUI backend does this, like
    # tpc_search_combined.py does for casannot.py) so that the package
    # __init__.py -- which imports sklearn/numpy -- never runs.
    _spec = importlib.util.spec_from_file_location(
        "casannot", os.path.join(os.path.dirname(os.path.abspath(__file__)), "casannot.py"))
    _ca = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_ca)

_get_sofa, _ontology_of = _ca._get_sofa, _ca._ontology_of

# Not casannot._PDF_TAG_RE: that one is "<_pdf[^/]*/>", which can't match an
# _image tag because its value is a path ("<_pdf _image=C/id/images/id.3.jpg/>").
_PDF_TAG_RE = re.compile(r"<_pdf\s[^<>]*?/>")
# Splits a tag into type and optional value: "<_pdf _page=3/>" -> ("_page", "3").
_PDF_TAG_PARTS_RE = re.compile(r"<_pdf\s+(_\w+)(?:=(.*?))?\s*/>$")

# Line/paragraph/page/image tags become a single space (they separate words);
# font and position tags are dropped entirely so e.g. "Ca<_pdf _ydiff=3/>2+"
# reads "Ca2+".
_PDF_SEPARATOR_TAGS = {"_cr", "_sbr", "_page", "_image"}


def _attr(el, name):
    m = re.search(rf'\b{name}="([^"]*)"', el)
    return html.unescape(m.group(1)) if m else None


def _elements(content, kind):
    return re.finditer(rf"<textpresso:{kind}\s.*?/>", content, re.DOTALL)


def _strip_pdf_tags(sofa):
    """Remove <_pdf .../> tags from sofa.

    Returns (text, offset_map, events) where offset_map[i] is the position in
    text of sofa offset i (len(sofa)+1 entries, so end offsets map too; an
    offset inside a tag maps to where that tag's replacement starts), and
    events is a list of (text_pos, tag_type, value) for every tag.
    """
    pieces = []
    offset_map = array("l")
    events = []
    j = 0
    last = 0
    for m in _PDF_TAG_RE.finditer(sofa):
        chunk = sofa[last:m.start()]
        offset_map.extend(range(j, j + len(chunk)))
        pieces.append(chunk)
        j += len(chunk)

        parts = _PDF_TAG_PARTS_RE.match(m.group(0))
        tag_type, value = (parts.group(1), parts.group(2)) if parts else ("", None)
        events.append((j, tag_type, value))
        offset_map.extend([j] * (m.end() - m.start()))
        if tag_type in _PDF_SEPARATOR_TAGS:
            pieces.append(" ")
            j += 1
        last = m.end()
    chunk = sofa[last:]
    offset_map.extend(range(j, j + len(chunk) + 1))
    pieces.append(chunk)
    return "".join(pieces), offset_map, events


def parse_cas_document(cas_path):
    """Parse a CAS2 file into a dict ready for a full-text viewer.

    Returns:
      rawsource   -- "tai", "pdf", "nxml", ... (whatever the CAS declares)
      text        -- the paper text, PDF tags removed (may contain "\\n")
      pages       -- [{pos, page}] page starts, sorted
      paragraphs  -- [pos] paragraph-break positions, sorted
      figures     -- [{pos, page, name}] embedded images; name is a basename
      sections    -- [{begin, end, type}] (see casannot.SECTION_TYPES)
      sentences   -- [{begin, end}]
      annotations -- [{begin, end, term, category, ontology, onto_id}],
                     PTCAT parent-category links and matches on the
                     "Beginning/End of Article" markers skipped (same as
                     casannot)

    All positions are offsets into text.
    """
    with gzip.open(cas_path, "rt", encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    rawsource = ""
    for m in _elements(content, "rawsource"):
        rawsource = _attr(m.group(0), "value") or ""
        break

    sofa = _get_sofa(content)
    pages, paragraphs, figures = [], [], []

    if _PDF_TAG_RE.search(sofa):
        text, offset_map, events = _strip_pdf_tags(sofa)
        remap = lambda o: offset_map[min(max(o, 0), len(offset_map) - 1)]
        page = 1
        for pos, tag_type, value in events:
            if tag_type == "_page":
                page = int(value) if value and value.strip().isdigit() else page + 1
                pages.append({"pos": pos, "page": page})
            elif tag_type == "_sbr":
                paragraphs.append(pos)
            elif tag_type == "_image" and value:
                figures.append({"pos": pos, "page": page, "name": os.path.basename(value.strip())})
    else:
        text = sofa
        remap = lambda o: min(max(o, 0), len(text))

    for m in _elements(content, "page"):
        el = m.group(0)
        value = _attr(el, "value") or ""
        pages.append({"pos": remap(int(_attr(el, "begin"))),
                      "page": int(value) if value.isdigit() else len(pages) + 1})
    for m in _elements(content, "dblbrk"):
        paragraphs.append(remap(int(_attr(m.group(0), "begin"))))
    for m in _elements(content, "image"):
        el = m.group(0)
        name = _attr(el, "filename")
        if not name:
            continue
        page = _attr(el, "page") or ""
        figures.append({"pos": remap(int(_attr(el, "begin"))),
                        "page": int(page) if page.isdigit() else None,
                        "name": os.path.basename(name)})

    sections = []
    for m in _elements(content, "section"):
        el = m.group(0)
        sections.append({"begin": int(_attr(el, "begin")),
                         "end": int(_attr(el, "end")),
                         "type": _attr(el, "type") or "",
                         "content": _attr(el, "content")})
    # Same heading fixes the search's section filtering uses (casannot.py),
    # judged on the raw sofaString (line breaks intact), then remapped.
    sections = _ca.repair_sections(_ca.add_letter_spaced_headings(sections, sofa), sofa,
                                   _ca.page_starts(content, sofa))
    for s in sections:
        s["begin"], s["end"] = remap(s["begin"]), remap(s["end"])

    sentences = []
    for m in _elements(content, "sentence"):
        el = m.group(0)
        sentences.append({"begin": remap(int(_attr(el, "begin"))),
                          "end": remap(int(_attr(el, "end")))})

    markers = _ca.frame_marker_spans(sofa)
    annotations = []
    for m in _elements(content, "lexicalannotation"):
        el = m.group(0)
        if "PTCAT" in el:
            continue
        begin, end = int(_attr(el, "begin")), int(_attr(el, "end"))
        if _ca.in_frame_marker(begin, end, markers):
            continue  # same as casannot: tokenizer marker text, not the paper's
        category = _attr(el, "category") or ""
        id_m = re.search(r"\(([A-Za-z][A-Za-z0-9_]*:\d+)\)", category)
        annotations.append({
            "begin": remap(begin),
            "end": remap(end),
            "term": _attr(el, "term") or "",
            "category": category,
            "ontology": _ontology_of(category),
            "onto_id": id_m.group(1) if id_m else "",
        })

    by_begin = lambda d: (d["begin"], d["end"])
    return {
        "rawsource": rawsource,
        "text": text,
        "pages": sorted(pages, key=lambda p: p["pos"]),
        "paragraphs": sorted(set(paragraphs)),
        "figures": sorted(figures, key=lambda f: f["pos"]),
        "sections": sorted(sections, key=by_begin),
        "sentences": sorted(sentences, key=by_begin),
        "annotations": sorted(annotations, key=by_begin),
    }
