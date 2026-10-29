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

import bisect
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
    # TdTokenizer writes it since 2026-10-22; split_supplementary() adds it
    # at read time for CAS2s tokenized before then
    "supplementary",
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
    # the eleven-channel annotation build (build_match_dictionary.py). \b keeps
    # PSO:/PATO: from reading as SO:/TO:
    ("CHEBI",               re.compile(r"\bCHEBI:\d+")),
    ("PATO",                re.compile(r"\bPATO:\d+")),
    ("PECO",                re.compile(r"\bPECO:\d+")),
    ("PSO",                 re.compile(r"\bPSO:\d+")),
    ("SO",                  re.compile(r"\bSO:\d+")),
    ("ECO",                 re.compile(r"\bECO:\d+")),
    ("CO_322",              re.compile(r"\bCO_322:\d+")),
    ("NCBITaxon",           re.compile(r"\bNCBITaxon:\d+")),
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
    return len(_REF_ENTRY_RE.findall(text[:_FOLLOWING_CHARS])) >= _MIN_REF_ENTRIES


# What a heading is judged on: the text after its line, up to 3000
# characters or the next references-heading line -- the whole window for the
# reference-list test, its first non-blank line for the citation test, its
# first non-blank character for the mid-sentence test. TdTokenizer's
# validateSectionHeadings uses the same window (FollowingText, 2026-10-22).
# Before, both sides used the text up to the next heading of any kind, which
# a false heading inside a reference list cut short; and the CAS2 content
# attribute, which this used to read, runs to the next *real* heading since
# the same change, so read-time and tokenize-time judgments could differ.
# Judged from the text, they can't, whichever CAS2.
# The window stops at a references heading because a reference list belongs
# to the nearest one above it: otherwise a "Reference" table column header
# just before "LITERATURE CITED" (10.1534_g3.112.005207) took the list as its
# own and the table caption became references.
_FOLLOWING_CHARS = 3000
_REF_HEADING_KEYS = frozenset(w for w, t in _HEADING_WORDS.items() if t == ("references",))
_LETTERS_LINE_RE = re.compile(r"[A-Za-z \t]+")


def _is_references_heading_line(line):
    """A line that is only a references heading: letters and spaces, PDF tags
    aside, whose letters are one of the references heading words (spacing and
    case ignored, as for letter-spaced headings)."""
    line = _PDF_ANY_TAG_RE.sub("", line).strip()
    return (bool(_LETTERS_LINE_RE.fullmatch(line))
            and re.sub(r"[^A-Za-z]", "", line).lower() in _REF_HEADING_KEYS)


def following_text(text, section):
    """The text the heading that starts section is judged on (see above).

    Falls back to section's content without text, and for TdTokenizer's
    implicit reference block, whose begin is a list entry, not a heading.
    A heading's own line always ends in "\\n" (the heading strings do), and
    its CAS2 content starts right after it: checked on 2,227 sections.
    """
    content = section.get("content")
    if text is None or (content or "").startswith("(implicit"):
        return content
    start = text.find("\n", section["begin"]) + 1
    if not start:
        return ""
    window = text[start:start + _FOLLOWING_CHARS]
    pos = 0
    for line in window.split("\n"):
        if _is_references_heading_line(line):
            return window[:pos]
        pos += len(line) + 1
    return window


def repair_sections(sections, text=None, pages=None):
    """Undo TdTokenizer's false section headings (see the note above).

    sections -- dicts with begin, end, type and optionally content (the CAS
    section element's content attribute: the text after the heading).
    text -- the string the offsets index into (the sofaString, PDF tags and
    all; for a paper with an appended supplement, only the text before the
    banner -- see fix_sections()). Headings are judged on the text after
    them (following_text()); without text they're judged on content, and
    the line-position checks are skipped; with neither, the reference-list
    checks are too.
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

    def following(group):
        return following_text(text, group[0])

    def misplaced(group):
        return text is not None and _heading_misplaced(text, group[0]["begin"], following(group))

    last_body = max((g[0]["begin"] for g in groups if not misplaced(g)
                     and any(s["type"] in _BODY_SECTIONS for s in g)), default=-1)

    def is_false(group):
        if misplaced(group):
            return True
        if all(s["type"] == "references" for s in group):
            after = following(group)
            if after is None or after.startswith("(implicit") or looks_like_reference_list(after):
                return False
            # Not followed by a reference list (a table column header
            # "Reference", say): false if it comes before body text, or if a
            # later "references" heading is followed by the real list.
            return group[0]["begin"] < last_body or any(
                g[0]["begin"] > group[0]["begin"] and all(s["type"] == "references" for s in g)
                and looks_like_reference_list(following(g) or "") for g in groups)
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
        after = following(group)
        return (previous is not None and any(s["type"] == "references" for s in previous)
                and not any(s["type"] == "references" for s in group)
                and after is not None and continues_citation(after))

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


# The MaizeGDB literature pipeline appends supplementary files to the article
# PDF behind a banner page:
#   SUPPLEMENTARY MATERIAL\n<file name>\n<description>\n\ninserted by the
#   MaizeGDB literature pipeline
# No heading follows the reference list, so TdTokenizer labels every appended
# supplement "references" -- and the default references exclusion then drops
# it. Measured 2026-10-21 over MaizeOpenAccess: 1,052 papers, 224k
# annotations behind the banner, 85% of them dropped as references; 1,378 of
# the 1,670 curator-recorded paper-gene pairs that hygiene removed outright
# were in supplementary tables.
# TdTokenizer applies the same rule (FindSupplementBanner, same pattern) from
# 2026-10-22, and also ignores headings inside the supplement when judging the
# article's own; parse_cas_file() does likewise, so CAS2s from before and
# after that change get the same sections.
_SUPPLEMENT_BANNER_RE = re.compile(
    r"SUPPLEMENTARY MATERIAL\s*\n(?:[^\n]*\n){0,6}?\s*inserted by the MaizeGDB literature pipeline")


def supplement_start(text):
    """Offset of the first pipeline supplement banner in text, or None."""
    m = _SUPPLEMENT_BANNER_RE.search(text or "")
    return m.start() if m else None


def split_supplementary(sections, text):
    """Label everything from the first pipeline supplement banner onward
    "supplementary", so appended supplements stop inheriting "references".

    sections -- as returned by repair_sections(); text -- the sofaString.
    Sections are truncated at the banner and those starting after it are
    replaced by one "supplementary" section running to the end of the text
    ("beginning/end of article" frames are left alone). A supplement's own
    headings ("References" inside an appended PDF) are folded in too: one type
    for all appended material keeps the policy decision in one place.
    Returns a new list; unchanged when there is no banner.
    """
    cut = supplement_start(text)
    if cut is None:
        return sections
    end = max([len(text)] + [s["end"] for s in sections])
    out = []
    for s in sections:
        if s["type"] in _FRAME_SECTIONS:
            out.append(dict(s))
        elif s["begin"] < cut:
            out.append(dict(s, end=min(s["end"], cut)))
    out.append({"begin": cut, "end": end, "type": "supplementary"})
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


# TdTokenizer (and jats_text.py) wrap every document in "\nBeginning of
# Article\n" ... "\nEnd of Article\n". The markers aren't paper text, but the
# lexical annotator matches them: TO's RELATED synonym "End" (endosperm
# quality, TO:0000587) fired in every paper, so a category search for it
# returned all 3,299 in MaizeOpenAccess (2026-10-29). No sentence covers a
# marker, so the mention also got a synthetic sentence of the bibliography
# around it -- which then passed the T2 context test. Only the marker strings
# go: the "beginning of article" frame section can run on over the real title.
_FRAME_MARKER_RES = (
    re.compile(r"\A(?:\s|<_pdf[^<>]*/>)*(Beginning of Article)\s"),
    re.compile(r"\s(End of Article)(?:\s|<_pdf[^<>]*/>)*\Z"),
)


def frame_marker_spans(sofa):
    """[(begin, end)] of the tokenizer's "Beginning/End of Article" markers:
    the first at the very start of sofa, the last at its very end (PDF tags
    and whitespace aside). The same words inside the paper don't count."""
    spans = []
    for rx in _FRAME_MARKER_RES:
        m = rx.search(sofa or "")
        if m:
            spans.append(m.span(1))
    return spans


def in_frame_marker(begin, end, spans):
    """True if [begin, end) overlaps any span from frame_marker_spans()."""
    return any(begin < e and end > b for b, e in spans)


def _coverage_test(sentences):
    """-> callable(pos): whether any sentence has begin <= pos < end.

    sentences sorted by begin. Binary search on begin plus a running maximum
    of end, so overlapping spans are handled; the per-annotation scan this
    replaces was 70% of parse time on a 20,000-annotation paper.
    """
    starts = [s["begin"] for s in sentences]
    max_end, m = [], -1
    for s in sentences:
        m = max(m, s["end"])
        max_end.append(m)

    def covered(pos):
        i = bisect.bisect_right(starts, pos) - 1
        return i >= 0 and max_end[i] > pos
    return covered


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


_JATS_RAWSOURCE_RE = re.compile(r'<textpresso:rawsource\s[^>]*\bvalue="jats"')


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
    callers that want the full hierarchy can remove this filter. Matches on
    the tokenizer's "Beginning/End of Article" markers are skipped too (see
    frame_marker_spans()).
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

    sofa = _get_sofa(content)
    markers = frame_marker_spans(sofa)
    annotations = []
    for m in re.finditer(r"<textpresso:lexicalannotation\s.*?/>", content, re.DOTALL):
        el = m.group(0)
        if "PTCAT" in el:
            continue
        begin = int(re.search(r'\bbegin="(\d+)"', el).group(1))
        end   = int(re.search(r'\bend="(\d+)"',   el).group(1))
        if in_frame_marker(begin, end, markers):
            continue  # tokenizer marker text, not the paper's

        tm    = re.search(r'\bterm="([^"]*)"',     el)
        cm    = re.search(r'\bcategory="([^"]*)"', el)
        term     = html.unescape(tm.group(1)) if tm else ""
        category = html.unescape(cm.group(1)) if cm else ""
        # Extract the ontology accession from the parenthetical, e.g. "GO:0048316"
        id_m    = re.search(r"\(([A-Za-z][A-Za-z0-9_]*:\d+)\)", category)
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
    covered = _coverage_test(sentences)
    uncovered = [a for a in annotations if not covered(a["begin"])]
    if uncovered and sofa:
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
    # jats layout: sections come from the JATS structure (jats_text.py), not
    # from heading lines, so the PDF heading repairs don't apply.
    if not _JATS_RAWSOURCE_RE.search(content):
        sections = fix_sections(sections, sofa, page_starts(content, sofa))

    return sentences, annotations, sections


def fix_sections(sections, sofa, pages=None):
    """All read-time section fixes, in order: letter-spaced headings, false
    headings, appended supplements. sections as read from the CAS2 (with
    content); sofa -- the sofaString; pages -- see page_starts().

    Headings inside an appended supplement aren't the article's, so the
    article's headings are judged on its own text, as TdTokenizer does --
    otherwise a supplement's "Methods" could make the article's References
    look like they come before body text. That also makes a CAS2 tokenized
    after 2026-10-22 (which already has a "supplementary" section) come out
    the same as one tokenized before.
    """
    cut = supplement_start(sofa)
    article = sofa if cut is None else sofa[:cut]
    sections = [s for s in sections if s["type"] in _FRAME_SECTIONS or s["begin"] < len(article)]
    sections = add_letter_spaced_headings(sections, article)
    sections = repair_sections(sections, article, pages)
    return split_supplementary(sections, sofa)


def sentence_lookup(sentences):
    """-> callable(begin, end) giving the text of the sentence containing begin.

    For annotation_hygiene.clean_annotations(sentence_at=...): the T2 tier
    test needs the sentence a mention sits in. sentences as returned by
    parse_cas_file() (sorted by begin); "" when no sentence covers the offset.
    """
    starts = [s["begin"] for s in sentences]

    def at(begin, end=None):
        i = bisect.bisect_right(starts, begin) - 1
        if i >= 0 and sentences[i]["begin"] <= begin < sentences[i]["end"]:
            return sentences[i]["text"]
        return ""
    return at


def body_text(sentences, sections, excluded_types=("references", "acknowledgments")):
    """-> the paper's text outside the excluded sections, one sentence per line.

    For annotation_hygiene.clean_annotations(body_text=...): the TD tier keeps a
    curated abbreviation only when its long form is written in the body, and a
    cited title or an acknowledged grant must not count as that.
    """
    return "\n".join(s["text"] for s in exclude_sections(sentences, sections, excluded_types))


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
