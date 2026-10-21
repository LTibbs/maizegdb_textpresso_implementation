"""Tests for jats_text.convert(), the reference JATS -> clean text + standoff
converter that defines the ``jats`` CAS layout.

The fixture is synthetic but built from constructs seen in MaizeOA JATS:
italic gene symbols adjacent to plain text, MathML and <sup> exponents with a
U+2212 minus, Wiley's U+2010 hyphen, OUP citations with no whitespace between
elements, sec-type-less sections, declarations inside <body>, tables with
empty rows, and a list nested inside a paragraph.
"""

import importlib.util
import os
import shutil
import tempfile
import unittest

_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "textpresso_classifiers")
_spec = importlib.util.spec_from_file_location("jats_text", os.path.join(_PKG, "jats_text.py"))
jats_text = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jats_text)

ARTICLE = """<?xml version="1.0" encoding="UTF-8"?>
<pmc-articleset><article xmlns:xlink="http://www.w3.org/1999/xlink"
    xmlns:mml="http://www.w3.org/1998/Math/MathML">
<front><article-meta>
  <title-group><article-title>The <italic>vpp5</italic> gene of maize</article-title></title-group>
  <contrib-group><contrib><name><surname>Author</surname></name></contrib></contrib-group>
  <abstract><p>We describe   <italic>vpp5</italic>s
     and ZmPR\u20101\u20108.</p></abstract>
  <abstract abstract-type="graphical"><p>Graphical abstract text.</p></abstract>
</article-meta></front>
<body>
  <p>Unheaded opening paragraph.</p>
  <sec sec-type="intro"><title>Background and aims</title>
    <p>Intro text with <bold>bold</bold> and a list:<list><list-item><p>first item</p></list-item>
      <list-item><p>second item</p></list-item></list> and the paragraph continues.</p>
  </sec>
  <sec><label>2</label><title>Methods and materials</title>
    <sec><title>Plant growth</title><p>Grown at 25\u00a0\u00b0C.</p></sec>
  </sec>
  <sec sec-type="results|discussion"><title>Results and Discussion</title>
    <p>Significant at <italic>P</italic> \u2264 1 \u00d7 10<sup>\u22125</sup> and
      <inline-formula><mml:math><mml:msup><mml:mn>10</mml:mn><mml:mrow><mml:mo>\u2212</mml:mo><mml:mn>8</mml:mn></mml:mrow></mml:msup></mml:math></inline-formula>.</p>
    <fig id="F1"><label>Figure 1</label><caption><title>Expression of <italic>vpp5</italic>.</title>
      <p>Bars show SD.</p></caption><graphic xlink:href="fig1.jpg"/></fig>
    <table-wrap id="T1"><label>Table 1</label><caption><p>Primers.</p></caption>
      <table><thead><tr><th>Gene</th><th>Primer</th></tr></thead>
      <tbody><tr><td><italic>vpp5</italic></td><td>ACGT</td></tr><tr><td></td><td></td></tr></tbody></table>
    </table-wrap>
  </sec>
  <sec><title>Author contributions</title><p>AB wrote it.</p></sec>
  <sec><title>Maize genetic resources</title><p>Review topic.</p></sec>
</body>
<back>
  <ack><title>Acknowledgements</title><p>We thank <italic>vpp5</italic> people.</p></ack>
  <sec sec-type="supplementary-material"><title>Supplementary material</title><p>Table S1.</p></sec>
  <ref-list><title>References</title>
    <ref id="R1"><element-citation><person-group><name><surname>Frey</surname><given-names>FP</given-names></name><etal/></person-group><year>2022</year><source>Current Biology</source><volume>32</volume><pub-id pub-id-type="doi">10.1/x</pub-id></element-citation></ref>
    <ref id="R2"><mixed-citation><string-name><surname>Ang</surname>, <given-names>A.</given-names>
      </string-name> , (2023). <article-title>TIRs</article-title>.</mixed-citation></ref>
  </ref-list>
</back>
<sub-article><body><p>Decision letter.</p></body></sub-article>
</article></pmc-articleset>
""".encode("utf-8")


def _span(doc, item):
    return doc.text[item["begin"]:item["end"]]


class TestJatsText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = jats_text.convert(ARTICLE)
        cls.text = cls.doc.text

    def sections_of(self, type_):
        return [s for s in self.doc.sections if s["type"] == type_]

    def block_texts(self, kind):
        return [_span(self.doc, b) for b in self.doc.blocks if b["kind"] == kind]

    # -- layout -----------------------------------------------------------

    def test_framed_like_tai(self):
        self.assertTrue(self.text.startswith("\nBeginning of Article\n"))
        self.assertTrue(self.text.endswith("\nEnd of Article\n"))
        self.assertEqual(_span(self.doc, self.sections_of("beginning of article")[0]), "Beginning of Article")
        self.assertEqual(_span(self.doc, self.sections_of("end of article")[0]), "End of Article")

    def test_blocks_are_single_trimmed_lines(self):
        for b in self.doc.blocks:
            s = _span(self.doc, b)
            self.assertNotIn("\n", s)
            self.assertEqual(s, s.strip(" "))

    def test_heading_then_single_newline_paragraphs_blank_line(self):
        self.assertIn("2 Methods and materials\nPlant growth\nGrown at", self.text)
        self.assertIn("Unheaded opening paragraph.\n\nBackground and aims\n", self.text)

    def test_whitespace_collapses_including_nbsp(self):
        self.assertIn("We describe vpp5s and ZmPR-1-8.", self.text)
        self.assertIn("Grown at 25 \u00b0C.", self.text)

    def test_front_matter_other_than_title_and_abstract_dropped(self):
        self.assertEqual(self.block_texts("title"), ["The vpp5 gene of maize"])
        self.assertNotIn("Author\n", self.text)
        self.assertNotIn("Graphical abstract", self.text)
        self.assertNotIn("Decision letter", self.text)
        self.assertEqual(self.doc.stats["sub_articles"], 1)

    def test_list_inside_paragraph_splits_blocks(self):
        self.assertEqual(self.block_texts("list-item"), ["first item", "second item"])
        self.assertIn("Intro text with bold and a list:", self.block_texts("paragraph"))
        self.assertIn("and the paragraph continues.", self.block_texts("paragraph"))
        self.assertIn("first item\nsecond item\n", self.text)

    # -- characters the PDF path loses --------------------------------------

    def test_hyphen_normalised_minus_kept(self):
        self.assertNotIn("\u2010", self.text)
        self.assertIn("P \u2264 1 \u00d7 10\u22125 and 10\u22128.", self.text)

    def test_superscripts(self):
        sups = [_span(self.doc, f) for f in self.doc.formats if f["value"] == "sup"]
        self.assertEqual(sups, ["\u22125", "\u22128"])

    def test_italic_spans_exact(self):
        italics = [_span(self.doc, f) for f in self.doc.formats if f["value"] == "italic"]
        # title, abstract ("vpp5" of "vpp5s"), P, caption, table cell, ack
        self.assertEqual(italics.count("vpp5"), 5)
        self.assertIn("P", italics)
        for f in self.doc.formats:
            s = _span(self.doc, f)
            self.assertEqual(s, s.strip())

    # -- sections -----------------------------------------------------------

    def test_section_types(self):
        types = [s["type"] for s in self.doc.sections]
        self.assertEqual(types.count("abstract"), 1)               # graphical skipped
        self.assertEqual(_span(self.doc, self.sections_of("introduction")[0]).split("\n")[0],
                         "Background and aims")                   # sec-type wins over title
        mm = _span(self.doc, self.sections_of("materials and methods")[0])
        self.assertTrue(mm.startswith("2 Methods and materials\nPlant growth"))
        result, discussion = self.sections_of("result"), self.sections_of("discussion")
        self.assertEqual((result[0]["begin"], result[0]["end"]),
                         (discussion[0]["begin"], discussion[0]["end"]))

    def test_unheaded_and_unknown_sections_untyped(self):
        opening = self.text.index("Unheaded opening")
        topic = self.text.index("Review topic")
        for s in self.doc.sections:
            if s["type"] not in ("beginning of article", "end of article"):
                self.assertFalse(s["begin"] <= opening < s["end"], s)
                self.assertFalse(s["begin"] <= topic < s["end"], s)
        self.assertEqual(self.doc.stats["untyped_sections"],
                         ["Maize genetic resources", "Supplementary material"])

    def test_declarations_and_ack_are_acknowledgments(self):
        acks = [_span(self.doc, s) for s in self.sections_of("acknowledgments")]
        self.assertTrue(any(a.startswith("Author contributions") for a in acks))
        self.assertTrue(any(a.startswith("Acknowledgements") for a in acks))

    def test_references(self):
        (refs,) = self.sections_of("references")
        self.assertTrue(_span(self.doc, refs).startswith("References\n"))
        self.assertEqual(self.block_texts("ref"),
                         ["Frey FP, et al.. 2022. Current Biology. 32",
                          "Ang, A., (2023). TIRs."])
        self.assertNotIn("10.1/x", self.text)

    # -- floats -------------------------------------------------------------

    def test_figure(self):
        (fig,) = self.doc.figures
        self.assertEqual(fig["id"], "F1")
        self.assertEqual(fig["graphic"], "fig1.jpg")
        self.assertEqual(_span(self.doc, fig), "Figure 1 Expression of vpp5. Bars show SD.")

    def test_table_rows_tab_separated_empty_row_dropped(self):
        self.assertEqual(self.block_texts("table-row"), ["Gene\tPrimer", "vpp5\tACGT"])
        self.assertIn("Table 1 Primers.", self.block_texts("caption"))

    # -- errors -------------------------------------------------------------

    def test_front_matter_only_raises(self):
        xml = b"<pmc-articleset><article><front><article-meta><title-group>" \
              b"<article-title>T</article-title></title-group></article-meta></front></article></pmc-articleset>"
        with self.assertRaises(jats_text.NoBodyError):
            jats_text.convert(xml)

    def test_section_types_for(self):
        f = jats_text.section_types_for
        self.assertEqual(f("3. Conclusions and future perspectives"), ("conclusion",))
        self.assertEqual(f("Materials and Methods"), ("materials and methods",))
        self.assertEqual(f("Results and analysis"), ("result",))
        self.assertEqual(f("Maize genetic resources"), ())
        self.assertEqual(f("Anything", "materials|methods"), ("materials and methods",))


COVER = ("SUPPLEMENTARY MATERIAL\nPLD3-7-e492-s001.pdf\nsupplementary PDF\n\n"
         "inserted by the MaizeGDB literature pipeline\n\n")


class TestPdfSupplement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        pages = ["Main text page 1 mentioning Supplementary Table S1.\n",
                 "Main text page 2.\n",
                 COVER,
                 "Supplementary Tables\nTable S1: vpp5 and bx1\u2010like genes.\nGene\tFold\n\nbx1\t2.3\n",
                 COVER.replace("s001", "s002")]
        for i, p in enumerate(pages, 1):
            with open(os.path.join(self.tmp, "10.1_x.%05d.txt" % i), "w", encoding="utf-8") as f:
                f.write(p)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_read_from_first_cover_page(self):
        pages = jats_text.read_pdf_supplement(self.tmp)
        self.assertEqual(len(pages), 3)
        self.assertTrue(pages[0].startswith("SUPPLEMENTARY MATERIAL\nPLD3-7-e492-s001.pdf"))

    def test_no_cover_page_no_supplement(self):
        os.remove(os.path.join(self.tmp, "10.1_x.00003.txt"))
        os.remove(os.path.join(self.tmp, "10.1_x.00005.txt"))
        self.assertEqual(jats_text.read_pdf_supplement(self.tmp), [])

    def test_appended_as_supplementary_section(self):
        doc = jats_text.convert(ARTICLE, jats_text.read_pdf_supplement(self.tmp))
        (sup,) = [s for s in doc.sections if s["type"] == jats_text.SUPPLEMENT_SECTION]
        span = doc.text[sup["begin"]:sup["end"]]
        self.assertTrue(span.startswith("SUPPLEMENTARY MATERIAL\nPLD3-7-e492-s001.pdf"))
        self.assertIn("Table S1: vpp5 and bx1-like genes.\nGene\tFold", span)   # lines kept
        self.assertTrue(span.endswith("inserted by the MaizeGDB literature pipeline"))
        refs = [s for s in doc.sections if s["type"] == "references"][0]
        end = [s for s in doc.sections if s["type"] == "end of article"][0]
        self.assertLess(refs["end"], sup["begin"])
        self.assertLess(sup["end"], end["begin"])
        self.assertNotIn("Main text page", doc.text)
        self.assertEqual(doc.stats["supplement_pages"], 3)
        self.assertTrue(all(b["kind"] == "pdf-text" for b in doc.blocks
                            if sup["begin"] <= b["begin"] < sup["end"]))

    def test_joined_staging_file_converts_identically(self):
        # fetch_jats stages the pages as one <acc>.suppl.txt, joined by blank lines
        pages = jats_text.read_pdf_supplement(self.tmp)
        a = jats_text.convert(ARTICLE, pages)
        b = jats_text.convert(ARTICLE, ["\n\n".join(pages)])
        for field in ("text", "blocks", "sections", "formats"):
            self.assertEqual(getattr(a, field), getattr(b, field), field)


if __name__ == "__main__":
    unittest.main()
