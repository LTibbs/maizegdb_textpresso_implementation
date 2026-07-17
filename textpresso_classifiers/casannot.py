"""Parse Textpresso CAS2 (.tpcas.gz) files to extract sentence-level ontology annotations.

CAS2 files are gzip-compressed UIMA XMI documents produced by the Textpresso
annotation pipeline (runAECpp).  Each file contains:

  <cas:Sofa sofaString="...full paper text..."/>   — the raw text, HTML-escaped
  <textpresso:sentence begin="N" end="N" content="..."/>  — one element per sentence
  <textpresso:lexicalannotation begin="N" end="N"          — one per ontology match
      term="..." category="term name (ONT:id)"/>
  <textpresso:section begin="N" end="N" type="..."/>       — one per detected
      paper section (e.g. "references", "abstract"); requires the paper to
      have been (re)tokenized after the section-detection fix (see
      sorghumbase_textpresso_implementation/docs/Laura_work_updates_log.md,
      2026-07-13). Empty for older CAS2 files.

The begin/end attributes are character offsets into the sofaString.  The
sentence content attribute already has PDF formatting tags stripped, so it can
be used directly as display text without touching sofaString.

We use regex rather than an XML parser because the sofaString value itself
contains embedded pseudo-XML tags (<_pdf .../>) that break ElementTree.

CAS2 sentence coverage note
----------------------------
The textpresso:sentence elements typically cover only fragments of the document
(e.g. the abstract and reference entries).  The paper body is often absent.
When annotations fall in uncovered regions, parse_cas_file synthesises sentence
contexts by extracting a window of text from the sofaString directly.
"""

import gzip
import html
import re

CAS_ROOT_DEFAULT = "/data/textpresso/tpcas-2"

# Section types written by TdTokenizer::combineSectionAnnotations() (see
# agr_textpresso/libtpc/uima-annotators/TdTokenizer/TdTokenizer.cpp). Distinct
# from tpc_search.py's SEARCH_TYPES: "sentence"/"document"/"title" are query
# scopes, not CAS section annotations, so they're not valid --exclude-type
# values even though they appear in --type's choices.
SECTION_TYPES = [
    "beginning of article",
    "end of article",
    "abstract",
    "introduction",
    "result",
    "discussion",
    "conclusion",
    "background",
    "materials and methods",
    "design",
    "acknowledgments",
    "references",
]

# Each entry maps a short label to the regex that identifies its ontology IDs
# in a category string like "seed development (GO:0048316)".
_ONTOLOGY_PREFIXES = [
    ("GO",                  re.compile(r"\bGO:\d+")),
    ("PO",                  re.compile(r"\bPO:\d+")),
    ("TO",                  re.compile(r"\bTO:\d+")),
    # tpzma: = classical_maize_genes.obo (legacy, small curated gene list)
    # tpzm:  = zmays_genes_20260708.obo and later (full genome-scale gene set)
    ("MAIZE_GENES_RELATED", re.compile(r"^RELATED:.*\btpzma?:\d+")),
    ("MAIZE_GENES",         re.compile(r"\btpzma?:\d+")),
]

# Matches the pseudo-XML PDF formatting tags embedded in the sofaString,
# e.g. <_pdf _cr/>, <_pdf _fsc=+10/>, <_pdf _page=2/>.
_PDF_TAG_RE = re.compile(r"<_pdf[^/]*/>")


def _get_sofa(content):
    """Extract and html-unescape the sofaString from raw CAS2 XML content.

    The sofaString is stored as an XML attribute value; any embedded " chars are
    encoded as &quot;, so [^"]* correctly captures the full value.  The C++
    annotator works on the unescaped string, so annotation begin/end offsets
    index into the result of html.unescape().
    """
    m = re.search(r'sofaString="([^"]*)"', content)
    return html.unescape(m.group(1)) if m else ""


def _sofa_sentence(sofa, ann_begin, ann_end, padding=400):
    """Build a synthetic sentence dict from sofaString context around an annotation.

    The returned begin/end span the padding window (guaranteed to contain
    [ann_begin, ann_end]) so that the standard position-overlap check in
    annotate_sentences() will always fire.  The text is the cleaned window
    content with PDF tags stripped and whitespace normalised.
    """
    s = max(0, ann_begin - padding)
    e = min(len(sofa), ann_end + padding)
    raw = sofa[s:e]
    # Strip complete PDF tags; also remove any partial tag fragments left at
    # window boundaries (e.g. "<_pdf _fnc=..." cut before "/>", or "_cr/>" cut
    # after "<_pdf ").
    text = _PDF_TAG_RE.sub(" ", raw)
    text = re.sub(r"<_pdf[^>]*$", "", text)   # leading partial tag (cut before />)
    text = re.sub(r"^[^<]*/>", "", text)       # trailing partial tag (cut after <_pdf)
    text = re.sub(r"\s+", " ", text).strip()
    return {"begin": s, "end": e, "text": text}


def identifier_to_cas_path(identifier, cas_root=CAS_ROOT_DEFAULT):
    """Convert an API document identifier to its local CAS2 file path.

    The API returns identifiers like:
      "MaizeTest100//10.1038_srep35479/10.1038_srep35479.tpcas"

    The double-slash separates corpus from accession; collapsing it and
    appending .gz gives the filesystem path under the CAS2 root:
      "/data/textpresso/tpcas-2/MaizeTest100/10.1038_srep35479/10.1038_srep35479.tpcas.gz"

    On the host (outside the container) the CAS2 root is:
      /home/ec2-user/agr_textpresso/.data/tpcas-2
    Inside the container it is /data/textpresso/tpcas-2.
    """
    return f"{cas_root}/{identifier.replace('//', '/')}.gz"


def _ontology_of(category):
    """Return the ontology label for a category string, or 'OTHER' if unrecognized."""
    for label, pattern in _ONTOLOGY_PREFIXES:
        if pattern.search(category):
            return label
    return "OTHER"


def parse_cas_file(cas_path):
    """Parse a CAS2 file and return (sentences, annotations, sections).

    sentences   — list of dicts {begin, end, text}, sorted by begin offset
    annotations — list of dicts {begin, end, term, category, ontology, onto_id}
    sections    — list of dicts {begin, end, type}, sorted by begin offset;
                  type is one of SECTION_TYPES (e.g. "references", "abstract").
                  A span can be covered by more than one section simultaneously
                  (e.g. a "Results and Discussion" combined header is tagged as
                  both "result" and "discussion" — see TdTokenizer.cpp). Empty
                  for CAS2 files predating the section-detection fix.

    Annotations with a 'PTCAT' prefix are skipped.  PTCAT entries are
    broader/parent-category links added by Textpresso (e.g. a match for
    "seed development" also generates PTCATs for "developmental process",
    "biological process", etc.).  They add noise at this level of analysis;
    callers that want the full hierarchy can remove this filter.
    """
    with gzip.open(cas_path, "rt", encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    sentences = []
    for m in re.finditer(r"<textpresso:sentence\s.*?/>", content, re.DOTALL):
        el = m.group(0)
        begin = int(re.search(r'\bbegin="(\d+)"', el).group(1))
        end   = int(re.search(r'\bend="(\d+)"',   el).group(1))
        cm    = re.search(r'\bcontent="([^"]*)"', el)
        # content is already clean text; html.unescape handles &amp; etc.
        text  = html.unescape(cm.group(1)) if cm else ""
        sentences.append({"begin": begin, "end": end, "text": text.strip()})

    sentences.sort(key=lambda s: s["begin"])

    annotations = []
    for m in re.finditer(r"<textpresso:lexicalannotation\s.*?/>", content, re.DOTALL):
        el = m.group(0)
        if "PTCAT" in el:
            continue
        begin = int(re.search(r'\bbegin="(\d+)"', el).group(1))
        end   = int(re.search(r'\bend="(\d+)"',   el).group(1))
        tm    = re.search(r'\bterm="([^"]*)"',     el)
        cm    = re.search(r'\bcategory="([^"]*)"', el)
        term     = html.unescape(tm.group(1)) if tm else ""
        category = html.unescape(cm.group(1)) if cm else ""
        # Extract the ontology accession from the parenthetical, e.g. "GO:0048316"
        id_m    = re.search(r"\(([A-Za-z]+:\d+)\)", category)
        onto_id = id_m.group(1) if id_m else ""
        annotations.append({
            "begin": begin, "end": end,
            "term": term, "category": category,
            "ontology": _ontology_of(category),
            "onto_id": onto_id,
        })

    # Supplement sparse CAS2 sentences: for any annotation whose position is
    # not covered by an existing sentence span, synthesise a sentence from the
    # sofaString context around that annotation.  This handles papers where the
    # sentence tokeniser only annotated the abstract and reference fragments but
    # left the body text without sentence spans.
    uncovered = [
        a for a in annotations
        if not any(s["begin"] <= a["begin"] < s["end"] for s in sentences)
    ]
    if uncovered:
        sofa = _get_sofa(content)
        if sofa:
            synthetic = []
            for a in uncovered:
                # Skip if a previously added synthetic sentence already covers this position.
                if any(syn["begin"] <= a["begin"] < syn["end"] for syn in synthetic):
                    continue
                synthetic.append(_sofa_sentence(sofa, a["begin"], a["end"]))
            sentences.extend(synthetic)
            sentences.sort(key=lambda s: s["begin"])

    sections = []
    for m in re.finditer(r"<textpresso:section\s.*?/>", content, re.DOTALL):
        el = m.group(0)
        begin = int(re.search(r'\bbegin="(\d+)"', el).group(1))
        end   = int(re.search(r'\bend="(\d+)"',   el).group(1))
        tm    = re.search(r'\btype="([^"]*)"', el)
        section_type = html.unescape(tm.group(1)) if tm else ""
        sections.append({"begin": begin, "end": end, "type": section_type})
    sections.sort(key=lambda s: s["begin"])

    return sentences, annotations, sections


def section_types_at(begin, end, sections):
    """Return the set of section types whose span overlaps [begin, end)."""
    return {s["type"] for s in sections if s["begin"] < end and s["end"] > begin}


def exclude_sections(items, sections, excluded_types):
    """Drop items (dicts with 'begin'/'end') that fall in an excluded section type.

    excluded_types — iterable of section-type strings, e.g. {"references"}.
    An item is dropped if it overlaps a section whose type is excluded. If
    sections is empty (CAS2 predates the section-detection fix) or
    excluded_types is empty, items is returned unchanged.
    """
    excluded_types = set(excluded_types or ())
    if not excluded_types or not sections:
        return items
    return [
        it for it in items
        if not (section_types_at(it["begin"], it["end"], sections) & excluded_types)
    ]


def annotate_sentences(sentences, annotations):
    """Return sentences with any overlapping annotations attached.

    Overlap is defined as: the annotation span intersects the sentence span,
    i.e. ann.begin < sent.end AND ann.end > sent.begin.  This handles both
    fully-contained and partially-overlapping spans.

    Returns a list of dicts with all sentence fields plus an 'annotations' key.
    Sentences with no annotations get an empty list.
    """
    result = []
    for sent in sentences:
        sb, se = sent["begin"], sent["end"]
        overlapping = [
            a for a in annotations
            if a["begin"] < se and a["end"] > sb
        ]
        result.append({**sent, "annotations": overlapping})
    return result


def summarize_by_ontology(annotations):
    """Return {ontology_label: sorted list of unique terms} for a set of annotations.

    Useful for a quick per-paper overview of what ontology vocabulary appears,
    without sentence-level detail.
    """
    groups = {}
    for a in annotations:
        groups.setdefault(a["ontology"], set()).add(a["term"])
    return {k: sorted(v) for k, v in sorted(groups.items())}
