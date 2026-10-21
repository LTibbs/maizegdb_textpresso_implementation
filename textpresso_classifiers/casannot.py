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
    # jats layout only (jats_text.py): PDF supplement text appended after the
    # JATS article, so it can be told apart from -- or excluded like -- the
    # rest of the paper.
    "supplementary material",
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

# --- Section repair -------------------------------------------------------
# TdTokenizer treats any line that is exactly a heading word ("Materials",
# "Reference", "Methods", ...) as a section heading, and each section runs
# until the next heading. In PDF text that misfires on a wrapped in-text
# cross-reference ("...(see Materials\nand methods)...") or a table column
# header ("Reference"), and one false heading relabels everything up to the
# next real one -- e.g. most of a Results section labelled "materials and
# methods", or part of an Introduction labelled "references" (which the
# search's default references/acknowledgments exclusion then drops).
# Measured 2026-10-14 over MaizeOA: ~300 papers with a mid-sentence
# "heading", ~120 with a non-reference-list "references" span before body
# text. repair_sections() undoes these at read time; the matching tokenizer
# fix is in agr_textpresso's TdTokenizer.cpp (takes effect on reprocessing).
_BODY_SECTIONS = {"introduction", "background", "result", "discussion", "conclusion",
                  "materials and methods", "design"}
_FRAME_SECTIONS = {"beginning of article", "end of article"}
# A real heading starts its line, possibly after a section number ("3 | ",
# "2.1 ", "IV. "); a false one has prose before it ("...(Fig. 2; see
# Materials\nand methods)"). A heading on a line of its own is false if it's
# sandwiched mid-sentence: the previous line is non-blank and doesn't end a
# sentence, and the text after it continues that sentence. (Text after a real
# heading can itself start lowercase in two-column PDFs -- the rest of a
# hyphenated word from the other column -- so that alone isn't enough.)
_NUMBERING_RE = re.compile(r"[\s\d.|:()\-–—•]*(?:[IVXivx]{1,4}[.)])?[\s\d.|:()\-–—•]*")
_CONTINUATION_RE = re.compile(r"^\s*[a-z)\]},;.:]")
_SENTENCE_END_RE = re.compile(r"[.!?:]\s*$")
_PDF_ANY_TAG_RE = re.compile(r"<_pdf[^<>]*/>")


def _heading_misplaced(text, pos, content):
    """True if the heading at text[pos] is mid-sentence rather than a heading."""
    line_start = text.rfind("\n", max(0, pos - 2000), pos) + 1
    if line_start == 0 and pos > 2000:
        return True  # no line break anywhere near: deep inside prose
    prefix = _PDF_ANY_TAG_RE.sub("", text[line_start:pos])
    # Prose = other words, then a space, then the heading word. Text glued to
    # the heading word is part of it ("STAR+METHODS", Cell's "STAR★Methods").
    if not _NUMBERING_RE.fullmatch(prefix) and prefix[-1:].isspace() and re.search(r"[A-Za-z]", prefix):
        return True
    if content is None or not _CONTINUATION_RE.match(content) or line_start == 0:
        return False
    prev_start = text.rfind("\n", max(0, line_start - 2000), line_start - 1) + 1
    prev = _PDF_ANY_TAG_RE.sub("", text[prev_start:line_start - 1]).strip()
    return bool(prev) and not _SENTENCE_END_RE.search(prev)
# A reference-list entry at line start: "Surname AB, ...", "Surname, A. B.,",
# "1. Surname AB", "[12] Surname, A." -- at least a few within the first 3000
# characters of a real reference list.
_REF_ENTRY_RE = re.compile(
    r"^\s*(?:\[?\d{1,3}[\].)]?\s+)?[A-Z][\w'’\-]+(?:\s[A-Z][\w'’\-]+)?,?\s(?:[A-Z]\.?\s?-?){1,3}[,.]",
    re.M)
_MIN_REF_ENTRIES = 3
_MAX_ABSTRACT_CHARS = 6000  # abstract + plain-language summary is ~3-5k


# Heading words TdTokenizer knows (agr_textpresso uimaglobaldefinitions.h,
# section*() sets), with spaces removed and lowercased -> section type(s).
_HEADING_WORDS = {}
for _types, _words in [
    (("abstract",), "abstract summary"),
    (("introduction",), "introduction literaturereview"),
    (("result",), "result results"),
    (("result", "discussion"), "resultsanddiscussion"),
    (("discussion",), "discussion"),
    (("discussion", "conclusion"), "discussionandconclusion discussionandconclusions"),
    (("conclusion",), "concludingremarks conclusion conclusions"),
    (("background",), "background"),
    (("materials and methods",), "experimentalprocedures material materialandmethod materialandmethods "
                                 "materials materialsandmethod materialsandmethods method methods "
                                 "methodssummary onlinemethods"),
    (("design",), "design designs"),
    (("acknowledgments",), "acknowledgement acknowledgements acknowledgment acknowledgments"),
    (("references",), "bibliography literaturecited reference references referencescited workscited"),
]:
    for _w in _words.split():
        _HEADING_WORDS[_w] = _types
# A whole line of 1-3 letter fragments separated by single spaces -- how PDF
# extraction renders a letter-spaced heading, unevenly:
# "AC K N OW L E D G M E N T S", "M ATE R I A L S A N D M E TH O DS". The
# tokenizer only knows the evenly spaced form, so these were missed (35
# MaizeOA papers, 2026-10-14), and the section before them ran on -- e.g. an
# "abstract" covering a whole review up to its References.
_LETTER_SPACED_RE = re.compile(r"[ \t]*((?:[A-Za-z]{1,5} ){2,}[A-Za-z]{1,5})[ \t]*")


def add_letter_spaced_headings(sections, text):
    """Insert sections for letter-spaced heading lines the tokenizer missed.

    sections -- as for repair_sections() (begin, end, type, content); text --
    the string their offsets index into. A heading line found where no section
    already starts splits the section containing it: that section now ends at
    the heading, and the new section(s) take over the rest of its span.
    Returns a new list; run repair_sections() on it afterwards so the same
    false-heading checks apply to these headings too. Also works on a paper
    the tokenizer gave no sections at all (each heading found splits the one
    before it).
    """
    out = [dict(s) for s in sections]
    starts = {s["begin"] for s in out}
    pos = 0
    for line in text.split("\n"):
        line_start, pos = pos, pos + len(line) + 1
        m = _LETTER_SPACED_RE.fullmatch(line)
        if not m:
            continue
        types = _HEADING_WORDS.get(m.group(1).replace(" ", "").lower())
        if not types or any(abs(line_start - b) <= 3 for b in starts):
            continue
        containing = [s for s in out if s["type"] not in _FRAME_SECTIONS
                      and s["begin"] < line_start < s["end"]]
        if containing:
            end = max(s["end"] for s in containing)
            for s in containing:
                s["end"] = line_start
        else:
            end = min((b for b in starts if b > line_start), default=len(text))
        content = text[pos:min(end, pos + 3000)]
        out.extend({"begin": line_start, "end": end, "type": t, "content": content} for t in types)
        starts.add(line_start)
    return sorted(out, key=lambda s: (s["begin"], s["end"]))


_PDF_PAGE_TAG_RE = re.compile(r"<_pdf\s+_page=")


def page_starts(content, sofa):
    """Sorted sofa offsets where pages begin: <textpresso:page> elements (tai
    layout) or <_pdf _page=N/> tags in the sofaString (pdf layout)."""
    starts = [int(m.group(1)) for m in re.finditer(r'<textpresso:page\s[^>]*?\bbegin="(\d+)"', content)]
    starts += [m.start() for m in _PDF_PAGE_TAG_RE.finditer(sofa)]
    return sorted(set(starts))


# The rest of a citation: volume(issue):pages, a DOI, or a "(2019)" year.
_CITATION_TAIL_RE = re.compile(r"\b\d+\s*\(\d+\)\s*:|doi\.org/|\bdoi:|\(\s*(?:19|20)\d{2}[a-z]?\s*\)")


def continues_citation(text):
    """True if the first non-empty line of text is the tail of a citation."""
    first = next((line for line in text.split("\n") if line.strip()), "")
    return bool(_CITATION_TAIL_RE.search(first))


def looks_like_reference_list(text):
    return len(_REF_ENTRY_RE.findall(text[:3000])) >= _MIN_REF_ENTRIES


def repair_sections(sections, text=None, pages=None):
    """Undo TdTokenizer's false section headings (see the note above).

    sections -- dicts with begin, end, type and optionally content (the CAS
    section element's content attribute: the text after the heading).
    text -- the string the offsets index into (the sofaString, PDF tags and
    all). Without it the line-position checks are skipped; without content
    the reference-list checks are.
    pages -- sorted offsets where pages begin (see page_starts()); used
    to end an abstract that ran on (below). Optional.

    A heading is false if it is mid-sentence (see _heading_misplaced); if it
    is a "references" heading that comes before body text and isn't followed
    by a reference list; or if it comes right after a references section and
    its text is still reference entries. A false heading's span is folded into the section
    before it (which is what it would have been without that heading); then
    adjacent spans of the same type(s) are merged. "beginning/end of article"
    are left alone. Returns new dicts with begin, end and type only.
    """
    frames = [s for s in sections if s["type"] in _FRAME_SECTIONS]
    groups = []  # sections sharing one heading position ("Results and Discussion" -> both)
    for s in sorted((s for s in sections if s["type"] not in _FRAME_SECTIONS),
                    key=lambda s: (s["begin"], s["end"])):
        if groups and groups[-1][0]["begin"] == s["begin"]:
            groups[-1].append(s)
        else:
            groups.append([s])

    def misplaced(group):
        return text is not None and _heading_misplaced(text, group[0]["begin"], group[0].get("content"))

    last_body = max((g[0]["begin"] for g in groups if not misplaced(g)
                     and any(s["type"] in _BODY_SECTIONS for s in g)), default=-1)

    def is_false(group):
        if misplaced(group):
            return True
        if all(s["type"] == "references" for s in group):
            content = group[0].get("content")
            if content is None or content.startswith("(implicit") or looks_like_reference_list(content):
                return False
            # Not followed by a reference list (a table column header
            # "Reference", say): false if it comes before body text, or if a
            # later "references" heading is followed by the real list.
            return group[0]["begin"] < last_body or any(
                g[0]["begin"] > group[0]["begin"] and all(s["type"] == "references" for s in g)
                and looks_like_reference_list(g[0].get("content") or "") for g in groups)
        return False

    # A heading inside a reference list whose text is still reference entries
    # -- e.g. "Methods\nEcol Evol 12(11)..." from a wrapped citation of the
    # journal Methods in Ecology and Evolution -- is part of that list. A real
    # Methods section after the references (Nature/Cell style) isn't
    # reference entries, so it stays.
    # Judged on the line right after the heading only: in two-column layouts
    # a real heading after the references (Acknowledgments, say) can be
    # followed a few lines later by more of the reference list.
    def inside_reference_list(group, previous):
        content = group[0].get("content")
        return (previous is not None and any(s["type"] == "references" for s in previous)
                and not any(s["type"] == "references" for s in group)
                and content is not None and continues_citation(content))

    kept = []
    for group in groups:
        if is_false(group) or inside_reference_list(group, kept[-1] if kept else None):
            if kept:  # fold into the previous section
                end = max(s["end"] for s in group)
                for s in kept[-1]:
                    s["end"] = max(s["end"], end)
            continue
        group = [{"begin": s["begin"], "end": s["end"], "type": s["type"]} for s in group]
        if kept and {s["type"] for s in kept[-1]} == {s["type"] for s in group} \
                and max(s["end"] for s in kept[-1]) >= group[0]["begin"]:
            end = max(s["end"] for s in group)
            for s in kept[-1]:
                s["end"] = max(s["end"], end)
            continue
        kept.append(group)

    # An abstract that runs on for pages -- because the paper's body headings
    # weren't recognised (a review with topical headings, say) -- ends at the
    # first page break after it starts; the body after that stays unlabelled
    # rather than being called "abstract".
    for group in kept:
        if (pages and [s["type"] for s in group] == ["abstract"]
                and group[0]["end"] - group[0]["begin"] > _MAX_ABSTRACT_CHARS):
            cut = next((p for p in pages if p > group[0]["begin"] + 200), None)
            if cut is not None and cut < group[0]["end"]:
                group[0]["end"] = cut

    out = [{"begin": s["begin"], "end": s["end"], "type": s["type"]} for s in frames]
    out += [s for group in kept for s in group]
    return sorted(out, key=lambda s: (s["begin"], s["end"]))


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
        cm    = re.search(r'\bcontent="([^"]*)"', el)
        section_type = html.unescape(tm.group(1)) if tm else ""
        sections.append({"begin": begin, "end": end, "type": section_type,
                         "content": html.unescape(cm.group(1)) if cm else None})
    sofa = _get_sofa(content)
    sections = add_letter_spaced_headings(sections, sofa)
    sections = repair_sections(sections, sofa, page_starts(content, sofa))

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
