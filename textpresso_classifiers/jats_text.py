"""JATS (PubMed Central NXML) -> clean text plus standoff annotations.

Reference implementation of the ``jats`` CAS layout. PDF ingest (pdf2tpcas /
the tai page-text path) loses italics, superscript signs (p-value exponents),
``<=`` (rendered ``#``), line-break hyphenation and table structure, and has to
guess section boundaries from heading-shaped lines. JATS carries all of that
explicitly, so this module turns one JATS article into:

  text      -- plain text, framed like a tai sofaString
               ("\\nBeginning of Article\\n" ... "\\nEnd of Article\\n"); no
               inline markup, so every offset is an offset into readable text
  blocks    -- [{begin, end, kind}] one per paragraph, heading, caption,
               table row, list item, reference ...; sentences never cross a
               block, which keeps headings and table rows out of the next
               sentence
  sections  -- [{begin, end, type, content}] with ``type`` from
               casannot.SECTION_TYPES, taken from the document structure
               rather than inferred from headings (see "Section types")
  formats   -- [{begin, end, value}] value in FORMAT_VALUES; italic spans are
               what annotation_italics' never-italicised rule needs
  figures   -- [{begin, end, id, graphic}] caption span and image href

This module is the only implementation of these rules. bin/fetch_jats.py
stages its output as <accession>.jats.json, and the C++ annotator
(agr_textpresso libtpc/uima-annotators/TjTokenizer) reads that JSON to build
the CAS -- adding tokens and block-bounded sentences -- rather than parsing
JATS itself. Offsets are Python str (code point) indices; TjTokenizer
converts them to UIMA's UTF-16 offsets.

Layout
------
Blocks are separated by a blank line, except that a heading is followed by a
single newline, and consecutive table rows / list items / references are one
per line. Inside a block, runs of whitespace (including the pretty-printing
newlines in the XML, non-breaking and thin spaces) collapse to one space;
table cells are separated by " | ". The text has no tabs at all: UIMA's XMI
writer escapes newlines in the sofaString attribute but not tabs, so every
XML reader of the CAS (Xerces in the CAS1 -> CAS2 step included) turns a tab
into a space. Unicode is kept as written, so
``P < 1 × 10<sup>−5</sup>`` becomes ``P < 1 × 10−5`` with a ``sup`` span over
``−5``.

Section types
-------------
Only top-level sections get a section annotation; nested ``<sec>`` elements
inherit their parent's span. A section's type comes from its ``sec-type``
attribute when that is one PMC uses (``intro``, ``materials|methods``,
``results|discussion`` ...), otherwise from its title through casannot's
heading table (the same words TdTokenizer recognises). A combined "Results
and Discussion" gets two annotations over the same span, as TdTokenizer
produces. Top-level sections whose type can't be determined get none -- the
same outcome as an unrecognised heading in the PDF path -- and are listed in
``stats["untyped_sections"]`` so the mapping can be extended from data.

  <abstract> (not graphical/teaser/toc)       abstract
  <ack>, and in <back>: declaration-type
  sections (funding, competing interests,
  author contributions, data availability,
  ethics) plus <fn-group>, <notes>, <glossary> acknowledgments
  <ref-list>                                  references

Declarations map to "acknowledgments" because that is where the PDF path puts
them (they follow the Acknowledgments heading and no heading of theirs is
known), and because the default ``exclude_sections`` and the italics rule
both treat that span as non-body matter.

Not included: author names and affiliations (bibliographic data comes from
the import CSV, as for PDFs), keywords, non-English ``<trans-abstract>``, and
``<sub-article>`` (decision letters, author responses).

PDF supplements
---------------
JATS only links to supplementary files, but the MaizeGDB literature pipeline
merges supplements into the paper's PDF, each behind a cover page
("SUPPLEMENTARY MATERIAL / <file name> / ... inserted by the MaizeGDB
literature pipeline"). Supplementary tables are often where gene lists are,
so convert(..., supplement_pages=read_pdf_supplement(page_dir)) appends the
page texts from the first cover page on, after the back matter, under one
"supplementary material" section. That text is kept as the PDF path produced
it (lines and newlines intact, blocks of kind "pdf-text" split at blank
lines), so it carries the PDF path's defects -- but only there.
"""

import glob
import importlib.util
import os
import re
import unicodedata
import xml.etree.ElementTree as ET

try:
    from . import casannot as _ca
except ImportError:
    # Loaded by file path (the GUI backend and tests do this for casannot.py
    # and casdoc.py) so that the package __init__ -- which imports
    # sklearn/numpy -- never runs.
    _spec = importlib.util.spec_from_file_location(
        "casannot", os.path.join(os.path.dirname(os.path.abspath(__file__)), "casannot.py"))
    _ca = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_ca)

RAWSOURCE = "jats"
FRAME_BEGIN = "Beginning of Article"
FRAME_END = "End of Article"
SUPPLEMENT_SECTION = "supplementary material"
CELL_SEPARATOR = " | "

# The cover page the literature pipeline puts in front of each merged supplement.
_SUPPLEMENT_COVER_RE = re.compile(
    r"\A\s*SUPPLEMENTARY MATERIAL\n.{0,400}?inserted by the MaizeGDB literature pipeline", re.S)
_PAGE_TXT_GLOB = "*.[0-9][0-9][0-9][0-9][0-9].txt"     # <accession>.00012.txt

FORMAT_TAGS = {
    "italic": "italic", "i": "italic", "em": "italic",
    "bold": "bold", "b": "bold", "strong": "bold",
    "sup": "sup", "sub": "sub", "sc": "sc",
    "monospace": "monospace", "underline": "underline",
}
FORMAT_VALUES = sorted(set(FORMAT_TAGS.values()))

# Consecutive blocks of these kinds are one per line (no blank line between).
LINE_KINDS = {"table-row", "list-item", "ref"}

# Elements that start a block of their own, even when they occur inside a <p>.
_BLOCK_TAGS = {
    "p", "sec", "fig", "fig-group", "table-wrap", "table-wrap-group", "list",
    "def-list", "disp-formula", "disp-formula-group", "disp-quote", "boxed-text",
    "statement", "supplementary-material", "media", "preformat", "code",
    "verse-group", "speech", "ref-list", "fn-group", "glossary", "notes", "app",
    "app-group", "ack", "caption", "alternatives", "table", "graphic",
}
# Dropped with their content (their tails are kept).
_SKIP_TAGS = {
    "graphic", "inline-graphic", "object-id", "permissions", "attrib",
    "tex-math", "sec-meta", "kwd-group", "processing-meta", "x",
    "alt-text", "long-desc", "bio",        # accessibility duplicates of captions; author bios
    "pub-id",                              # DOI/PMID strings inside references
}
# Citation parts publishers run together with no whitespace between them
# (OUP: <surname>Frey</surname><given-names>FP</given-names>,
# <source>Current Biology</source><volume>32</volume>): a space follows each.
_WORD_END_TAGS = {
    "surname", "given-names", "prefix", "suffix", "string-name", "name", "collab",
    "etal", "article-title", "chapter-title", "source", "year", "volume", "issue",
    "fpage", "lpage", "publisher-name", "publisher-loc", "edition",
}
# U+2010 HYPHEN and U+2011 NON-BREAKING HYPHEN (Wiley, Springer) -> "-", so a
# form like "ZmPR-1-8" matches the ASCII hyphen the OBO uses. Dashes and the
# minus sign (U+2212, as in "10\u22125") are kept.
# The degree sign becomes " degree " and MICRO SIGN U+00B5 becomes GREEK SMALL
# MU U+03BC, as on the tai (PDF) path (agr_textpresso
# libtpc/uima-annotators/PdfTextNormalizer.h), so a paper reads the same
# whichever path ingested it. Lucene indexes "\u00b5M" and "\u03bcM" as
# different tokens. The surrounding spaces collapse with the text's own.
_CHAR_MAP = {"\u2010": "-", "\u2011": "-", "\u00ad": "",   # U+00AD soft hyphen: dropped
             "\u00b0": " degree ", "\u00b5": "\u03bc"}


# Characters XML 1.0 cannot carry: C0 controls other than tab, newline and
# carriage return, plus U+FFFE/U+FFFF. PDF text extraction emits them for
# unmapped glyphs (a degree sign as U+0001, a minus as U+0008), mostly in the
# merged supplements. The text ends up in the CAS-1 `sofaString` attribute, and
# runAECpp rejects the whole file ("invalid character 0x1 in attribute value
# 'sofaString'") -- 32 of the 3,299 MaizeOpenAccess papers failed to annotate
# that way. Each becomes one space (never dropped), so offsets stay valid.
_XML_ILLEGAL = frozenset(chr(c) for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)) \
    | {"￾", "￿"}


def _map_char(ch):
    """The text a source character becomes. Besides _CHAR_MAP, XML-illegal
    control characters become a space (see _XML_ILLEGAL), and characters
    outside the BMP (mathematical italic letters such as U+1D452, emoji) are
    NFKC-normalised ("\U0001d452" -> "e"), or replaced by U+FFFD if that
    leaves them outside the BMP. jats text is then entirely BMP, so its code
    point offsets (Python, casannot, casdoc) and UTF-16 offsets (UIMA) agree."""
    if ch in _XML_ILLEGAL:
        return " "
    if ch in _CHAR_MAP:
        return _CHAR_MAP[ch]
    if ord(ch) <= 0xFFFF:
        return ch
    folded = unicodedata.normalize("NFKC", ch)
    return "".join(c if ord(c) <= 0xFFFF else "\ufffd" for c in folded)
# In references only: pretty-printed citation XML leaves a space before
# punctuation ("Ananiev, E. V. , Wu, C.").
_SPACE_BEFORE_PUNCT_RE = re.compile(r" (?=[,;:.)])")
_FORMULA_TAGS = {"inline-formula", "disp-formula", "math"}
_CITATION_TAGS = {"mixed-citation", "element-citation", "citation", "nlm-citation"}
_SKIP_ABSTRACT_TYPES = {"graphical", "teaser", "toc"}

# PMC sec-type tokens (split on "|") -> section type.
_SEC_TYPE_TOKENS = {
    "intro": "introduction", "introduction": "introduction",
    "background": "background",
    "materials": "materials and methods", "methods": "materials and methods",
    "results": "result", "discussion": "discussion",
    "conclusions": "conclusion", "conclusion": "conclusion",
}
# Back-matter declarations, by sec-type or title.
_DECLARATION_RE = re.compile(
    r"acknowledg|funding|financ|conflict|competing|contribution"
    r"|data.?availab|availability|ethic|consent|disclosure|declaration|coi.?statement"
    r"|additional.?information|footnote|fn-group|associated.?data",
    re.I)
_LEADING_NUMBER_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*|[IVXLC]+)[.)|:]?\s+")
# Untyped sections in <back> are non-body matter (footnotes, "Additional
# information", change history ...) except supplementary listings, whose
# captions describe data and stay untyped like their <body> counterparts.
_SUPPLEMENTARY_RE = re.compile(r"supplement|supporting|source.?data", re.I)


def _tag(elem):
    tag = elem.tag
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _text_of(elem):
    return " ".join("".join(elem.itertext()).split())


def section_types_for(title, sec_type=None):
    """Section type(s) for a top-level section, () if not determinable."""
    if sec_type:
        types = []
        for token in re.split(r"[|\s]+", sec_type.lower()):
            t = _SEC_TYPE_TOKENS.get(token)
            if t and t not in types:
                types.append(t)
        if types:
            return tuple(types)
    if title:
        key = re.sub(r"[^a-z]", "", _LEADING_NUMBER_RE.sub("", title).lower())
        if key in _ca._HEADING_WORDS:
            return _ca._HEADING_WORDS[key]
        # "Conclusions and future perspectives", "Methods and materials",
        # "Results and analysis": a known heading word followed by "and ...".
        # The longest such prefix wins ("materialsandmethods" over "materials").
        prefixes = [w for w in _ca._HEADING_WORDS
                    if len(w) >= 6 and key.startswith(w) and key[len(w):].startswith("and")]
        if prefixes:
            return _ca._HEADING_WORDS[max(prefixes, key=len)]
    return ()


class JatsText:
    def __init__(self, text, blocks, sections, formats, figures, stats):
        self.text = text
        self.blocks = blocks
        self.sections = sections
        self.formats = formats
        self.figures = figures
        self.stats = stats

    def as_dict(self):
        return {"rawsource": RAWSOURCE, "text": self.text, "blocks": self.blocks,
                "sections": self.sections, "formats": self.formats,
                "figures": self.figures, "stats": self.stats}


class NoBodyError(ValueError):
    """The JATS has front matter only (PMC withholds XML full text for some
    publishers), so it can't stand in for the PDF."""


class _Writer:
    """Accumulates text; inline content is buffered per block as
    (string, formats, literal) runs and normalised when the block ends."""

    def __init__(self):
        self.chunks = []
        self.pos = 0
        self.tail_newlines = 0
        self.blocks = []
        self.formats = []
        self._runs = None
        self._kind = None

    def raw(self, s):
        self.chunks.append(s)
        self.pos += len(s)
        stripped = s.rstrip("\n")
        self.tail_newlines = (self.tail_newlines + len(s)) if not stripped else len(s) - len(stripped)

    def begin_block(self, kind):
        if self._runs is not None:
            self.end_block()
        self._runs, self._kind = [], kind

    def add(self, s, fmts=frozenset(), literal=False):
        if self._runs is not None and s:
            self._runs.append((s, fmts, literal))

    def end_block(self):
        """Close the open block; returns its {begin, end, kind} or None if empty."""
        runs, kind = self._runs, self._kind
        self._runs = self._kind = None
        if not runs:
            return None
        chars, fmts_at = [], []
        for s, fmts, literal in runs:
            for source_ch in s:
                for ch in _map_char(source_ch):
                    if not literal and ch.isspace():
                        if not chars or chars[-1] in " \t":
                            continue
                        ch = " "
                    chars.append(ch)
                    fmts_at.append(fmts)
        while chars and chars[-1] == " ":
            chars.pop()
            fmts_at.pop()
        lead = 0
        while lead < len(chars) and chars[lead] == " ":   # " | x": leading empty cells
            lead += 1
        chars, fmts_at = chars[lead:], fmts_at[lead:]
        if not chars or all(ch.isspace() or ch == "|" for ch in chars):   # a row of empty cells
            return None
        if kind == "ref":
            drop = {m.start() for m in _SPACE_BEFORE_PUNCT_RE.finditer("".join(chars))}
            chars = [c for i, c in enumerate(chars) if i not in drop]
            fmts_at = [f for i, f in enumerate(fmts_at) if i not in drop]
        prev = self.blocks[-1]["kind"] if self.blocks else None
        need = 1 if prev in (None, "heading") or (kind in LINE_KINDS and kind == prev) else 2
        while self.tail_newlines < need:
            self.raw("\n")
        begin = self.pos
        self.raw("".join(chars))
        self.raw("\n")
        for value in sorted({v for f in fmts_at for v in f}):
            i, n = 0, len(chars)
            while i < n:
                if value not in fmts_at[i]:
                    i += 1
                    continue
                j = i
                while j < n and value in fmts_at[j]:
                    j += 1
                b, e = i, j
                while b < e and chars[b].isspace():
                    b += 1
                while e > b and chars[e - 1].isspace():
                    e -= 1
                if b < e:
                    self.formats.append({"begin": begin + b, "end": begin + e, "value": value})
                i = j
        block = {"begin": begin, "end": begin + len(chars), "kind": kind}
        self.blocks.append(block)
        return block

    def verbatim_block(self, text, kind):
        """A block written as given, newlines included (PDF-derived text)."""
        if self._runs is not None:
            self.end_block()
        lines = ["".join(_map_char(ch) for ch in line).replace("\t", " ").rstrip()
                 for line in text.replace("\r", "").replace("\f", "").split("\n")]
        text = "\n".join(lines).strip("\n")
        if not text.strip():
            return None
        while self.tail_newlines < 2:
            self.raw("\n")
        begin = self.pos
        self.raw(text)
        self.raw("\n")
        block = {"begin": begin, "end": begin + len(text), "kind": kind}
        self.blocks.append(block)
        return block

    @property
    def in_block(self):
        return self._runs is not None


class _Converter:
    def __init__(self):
        self.w = _Writer()
        self.sections = []
        self.figures = []
        self.stats = {"untyped_sections": [], "unknown_tags": {}, "sub_articles": 0}
        self._flatten = 0      # >0 inside table cells: nested blocks become inline

    # -- sections ---------------------------------------------------------

    def _span_since(self, first_block):
        if self.w.in_block:
            self.w.end_block()
        if len(self.w.blocks) <= first_block:
            return None
        return self.w.blocks[first_block]["begin"], self.w.blocks[-1]["end"]

    def with_section(self, types, content, fn):
        first = len(self.w.blocks)
        fn()
        span = self._span_since(first)
        if span:
            for t in types:
                self.sections.append({"begin": span[0], "end": span[1], "type": t, "content": content})

    # -- inline -----------------------------------------------------------

    def inline(self, elem, fmts, kind):
        """Append elem's content (not its tail) to the open block."""
        tag = _tag(elem)
        if tag in _SKIP_TAGS:
            return
        if tag in _FORMULA_TAGS:
            self.formula(elem, fmts)
            return
        if tag == "break":
            self.w.add(" ", fmts)
            return
        if tag in FORMAT_TAGS:
            fmts = fmts | {FORMAT_TAGS[tag]}
        if elem.text:
            self.w.add(elem.text, fmts)
        for child in elem:
            ctag = _tag(child)
            if ctag in _BLOCK_TAGS and not self._flatten and ctag not in _SKIP_TAGS:
                self.w.end_block()
                self.block(child)
                self.w.begin_block(kind)
            elif ctag in _BLOCK_TAGS and self._flatten:
                self.w.add(" ", fmts)
                self.inline(child, fmts, kind)
                self.w.add(" ", fmts)
            else:
                self.inline(child, fmts, kind)
                if ctag in _WORD_END_TAGS:
                    self.w.add(" ", fmts)
            if child.tail:
                self.w.add(child.tail, fmts)

    def formula(self, elem, fmts):
        math = elem if _tag(elem) == "math" else next(
            (m for m in elem.iter() if _tag(m) == "math"), None)
        if math is None:
            return                   # tex-math / graphic only: nothing readable
        alt = math.get("alttext")
        if alt:
            self.w.add(alt, fmts)
            return
        self.mathml(math, fmts)

    def mathml(self, elem, fmts):
        tag = _tag(elem)
        children = list(elem)
        if tag in ("msup", "msub", "msubsup") and len(children) >= 2:
            self.mathml(children[0], fmts)
            if tag == "msubsup" and len(children) >= 3:
                self.mathml(children[1], fmts | {"sub"})
                self.mathml(children[2], fmts | {"sup"})
            else:
                self.mathml(children[1], fmts | {"sup" if tag == "msup" else "sub"})
            return
        if tag == "mfrac" and len(children) == 2:
            self.mathml(children[0], fmts)
            self.w.add("/", fmts)
            self.mathml(children[1], fmts)
            return
        if tag in ("annotation", "annotation-xml"):
            return
        if elem.text and tag in ("mi", "mn", "mo", "mtext", "ms"):
            self.w.add(elem.text.strip(), fmts)
        for child in children:
            self.mathml(child, fmts)

    def para(self, elem, kind, prefix=None):
        self.w.begin_block(kind)
        if prefix:
            self.w.add(prefix + " ")
        self.inline(elem, frozenset(), kind)
        return self.w.end_block()

    # -- blocks -----------------------------------------------------------

    def children(self, elem, skip=()):
        for child in elem:
            if _tag(child) not in skip:
                self.block(child)

    def block(self, elem):
        tag = _tag(elem)
        if tag in _SKIP_TAGS or tag in ("label", "title"):
            return
        if tag == "sec":
            self.heading(elem)
            self.children(elem, skip=("label", "title"))
        elif tag in ("p", "preformat", "code", "statement", "speech", "verse-line"):
            self.para(elem, "code" if tag in ("preformat", "code") else "paragraph")
        elif tag in ("fig", "supplementary-material", "media"):
            self.figure(elem)
        elif tag == "table-wrap":
            self.table_wrap(elem)
        elif tag == "table":
            self.table(elem)
        elif tag == "list":
            self.heading(elem)
            for item in elem:
                if _tag(item) == "list-item":
                    self.list_item(item)
        elif tag == "def-list":
            self.heading(elem)
            for item in elem:
                if _tag(item) == "def-item":
                    self.w.begin_block("list-item")
                    for part in item:
                        self.inline(part, frozenset(), "list-item")
                        self.w.add(" ")
                    self.w.end_block()
        elif tag in ("disp-formula", "disp-formula-group"):
            self.w.begin_block("formula")
            self.inline(elem, frozenset(), "formula")
            self.w.end_block()
        elif tag == "ref-list":
            self.ref_list(elem)
        elif tag == "fn":
            self.children(elem)
        elif tag in ("fig-group", "table-wrap-group", "boxed-text", "disp-quote", "verse-group",
                     "fn-group", "glossary", "notes", "app", "app-group", "ack", "caption",
                     "alternatives", "floats-group", "body", "back", "abstract"):
            self.heading(elem)
            self.children(elem, skip=("label", "title"))
        else:
            unknown = self.stats["unknown_tags"]
            unknown[tag] = unknown.get(tag, 0) + 1
            if "".join(elem.itertext()).strip():
                self.para(elem, "paragraph")

    def heading(self, elem):
        label, title = elem.find("label"), elem.find("title")
        if title is None and label is None:
            return None
        self.w.begin_block("heading")
        if label is not None:
            self.inline(label, frozenset(), "heading")
            self.w.add(" ")
        if title is not None:
            self.inline(title, frozenset(), "heading")
        return self.w.end_block()

    def list_item(self, item):
        first = True
        for part in item:
            ptag = _tag(part)
            if ptag == "list":
                self.block(part)
            elif ptag == "label":
                continue
            else:
                prefix = None
                label = item.find("label")
                if first and label is not None:
                    prefix = _text_of(label)
                self.para(part, "list-item", prefix)
                first = False

    def caption_block(self, elem, kind="caption"):
        label, caption = elem.find("label"), elem.find("caption")
        self.w.begin_block(kind)
        if label is not None:
            self.inline(label, frozenset(), kind)
            self.w.add(" ")
        if caption is not None:
            self._flatten += 1
            for part in caption:
                self.inline(part, frozenset(), kind)
                self.w.add(" ")
            self._flatten -= 1
        return self.w.end_block()

    def figure(self, elem):
        block = self.caption_block(elem)
        if block and _tag(elem) == "fig":
            graphic = next((g for g in elem.iter() if _tag(g) == "graphic"), None)
            href = None
            if graphic is not None:
                href = next((v for k, v in graphic.attrib.items() if k.endswith("href")), None)
            self.figures.append({"begin": block["begin"], "end": block["end"],
                                 "id": elem.get("id"), "graphic": href})
        for child in elem:
            if _tag(child) not in ("label", "caption", "graphic", "alternatives"):
                self.block(child)

    def table_wrap(self, elem):
        self.caption_block(elem)
        for child in elem:
            ctag = _tag(child)
            if ctag == "table":
                self.table(child)
            elif ctag == "alternatives":
                for t in child:
                    if _tag(t) == "table":
                        self.table(t)
            elif ctag == "table-wrap-foot":
                for part in child:
                    if _tag(part) == "fn-group":
                        for fn in part:
                            self.para(fn, "caption")
                    else:
                        self.para(part, "caption")

    def table(self, elem):
        self._flatten += 1
        for tr in elem.iter():
            if _tag(tr) != "tr":
                continue
            self.w.begin_block("table-row")
            first = True
            for cell in tr:
                if _tag(cell) not in ("td", "th"):
                    continue
                if not first:
                    self.w.add(CELL_SEPARATOR, literal=True)
                first = False
                self.inline(cell, frozenset(), "table-row")
            self.w.end_block()
        self._flatten -= 1

    def ref_list(self, elem):
        self.heading(elem)
        for child in elem:
            ctag = _tag(child)
            if ctag == "ref":
                self.ref(child)
            elif ctag == "ref-list":
                self.ref_list(child)
            elif ctag not in ("label", "title"):
                self.block(child)

    def ref(self, ref):
        self.w.begin_block("ref")
        label = ref.find("label")
        if label is not None:
            self.inline(label, frozenset(), "ref")
            self.w.add(" ")
        for cit in ref:
            ctag = _tag(cit)
            if ctag == "mixed-citation":
                self.inline(cit, frozenset(), "ref")
            elif ctag in _CITATION_TAGS:
                parts = []
                for part in cit:
                    ptag = _tag(part)
                    if ptag == "person-group":
                        names = []
                        for name in part:
                            ntag = _tag(name)
                            if ntag == "name":
                                names.append(" ".join(x for x in (
                                    _text_of(name.find("surname")) if name.find("surname") is not None else "",
                                    _text_of(name.find("given-names")) if name.find("given-names") is not None else "",
                                ) if x))
                            elif ntag == "etal":
                                names.append("et al.")
                            elif ntag in ("string-name", "collab"):
                                names.append(_text_of(name))
                        parts.append((", ".join(n for n in names if n), None))
                    elif ptag not in ("pub-id", "object-id", "comment", "ext-link", "uri"):
                        parts.append((None, part))
                for i, (text, part) in enumerate(parts):
                    if i:
                        self.w.add(". ")
                    if text is not None:
                        self.w.add(text)
                    else:
                        self.inline(part, frozenset(), "ref")
            self.w.add(" ")
        self.w.end_block()

    # -- article ----------------------------------------------------------

    def supplement(self, pages):
        def write():
            for page in pages:
                for para in re.split(r"\n[ \t]*\n", page):
                    self.w.verbatim_block(para, "pdf-text")
        self.with_section((SUPPLEMENT_SECTION,), "Supplementary material (PDF)", write)
        self.stats["supplement_pages"] = len(pages)

    def article(self, article, supplement_pages=None):
        self.w.raw("\n" + FRAME_BEGIN + "\n")
        self.sections.append({"begin": 1, "end": 1 + len(FRAME_BEGIN),
                              "type": "beginning of article", "content": FRAME_BEGIN})
        meta = article.find("front/article-meta")
        if meta is not None:
            title = meta.find("title-group/article-title")
            if title is not None:
                self.para(title, "title")
            for abstract in meta.findall("abstract"):
                if (abstract.get("abstract-type") or "").lower() in _SKIP_ABSTRACT_TYPES:
                    continue
                title = abstract.find("title")
                self.with_section(("abstract",), _text_of(title) if title is not None else "Abstract",
                                  lambda a=abstract: self.block(a))
        body = article.find("body")
        for child in body:
            if _tag(child) == "sec":
                self.top_section(child)
            else:
                self.block(child)
        floats = article.find("floats-group")
        if floats is not None:
            self.block(floats)
        back = article.find("back")
        if back is not None:
            for child in back:
                self.back_part(child)
        self.stats["sub_articles"] = len(article.findall("sub-article"))
        if supplement_pages:
            self.supplement(supplement_pages)
        if self.w.in_block:
            self.w.end_block()
        while self.w.tail_newlines < 1:
            self.w.raw("\n")
        end_begin = self.w.pos + 1
        self.w.raw("\n" + FRAME_END + "\n")
        self.sections.append({"begin": end_begin, "end": end_begin + len(FRAME_END),
                              "type": "end of article", "content": FRAME_END})

    def top_section(self, sec, in_back=False):
        title = sec.find("title")
        title_text = _text_of(title) if title is not None else ""
        types = section_types_for(title_text, sec.get("sec-type"))
        if not types and (_DECLARATION_RE.search(sec.get("sec-type") or "")
                          or _DECLARATION_RE.search(title_text)):
            types = ("acknowledgments",)   # some publishers put these in <body>
        if not types and in_back and not _SUPPLEMENTARY_RE.search(title_text):
            types = ("acknowledgments",)
        if not types:
            self.stats["untyped_sections"].append(title_text)
        self.with_section(types, title_text, lambda: self.block(sec))

    def back_part(self, elem):
        tag = _tag(elem)
        if tag == "ack":
            self.with_section(("acknowledgments",), "Acknowledgments", lambda: self.block(elem))
        elif tag == "ref-list":
            title = elem.find("title")
            self.with_section(("references",), _text_of(title) if title is not None else "References",
                              lambda: self.block(elem))
        elif tag in ("fn-group", "notes", "glossary"):
            self.with_section(("acknowledgments",), tag, lambda: self.block(elem))
        elif tag == "sec":
            self.top_section(elem, in_back=True)
        elif tag not in ("label", "title"):
            self.block(elem)


def _first_article(root):
    if _tag(root) == "article":
        return root
    return next((e for e in root.iter() if _tag(e) == "article"), None)


def read_pdf_supplement(page_dir):
    """Page texts of a paper's merged PDF from the first supplement cover page
    on, [] if it has none. page_dir holds the per-page <accession>.NNNNN.txt
    files of the PDF path (raw_files/pdf/<corpus>/<accession>/)."""
    pages = []
    for path in sorted(glob.glob(os.path.join(page_dir, _PAGE_TXT_GLOB))):
        with open(path, encoding="utf-8", errors="replace") as f:
            pages.append(f.read())
    first = next((i for i, p in enumerate(pages) if _SUPPLEMENT_COVER_RE.match(p)), None)
    return [] if first is None else pages[first:]


def convert(xml_bytes, supplement_pages=None):
    """JATS bytes (a PMC efetch <pmc-articleset> or a bare <article>) -> JatsText.

    supplement_pages -- optional PDF page texts to append as the
    "supplementary material" section (see read_pdf_supplement()).

    Raises NoBodyError when there is no <body> (front matter only)."""
    root = ET.fromstring(xml_bytes)
    article = _first_article(root)
    if article is None or article.find("body") is None or not "".join(
            article.find("body").itertext()).strip():
        raise NoBodyError("no <body> in JATS")
    c = _Converter()
    c.article(article, supplement_pages)
    text = "".join(c.w.chunks)
    sections = sorted(c.sections, key=lambda s: (s["begin"], s["type"]))
    formats = sorted(c.w.formats, key=lambda f: (f["begin"], f["value"]))
    return JatsText(text, c.w.blocks, sections, formats, c.figures, c.stats)
