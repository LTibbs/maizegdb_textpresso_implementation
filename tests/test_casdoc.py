"""Tests for casdoc.parse_cas_document(), the full-text viewer's CAS2 parser.

Covers both CAS layouts:
  - pdf: structure is inline <_pdf .../> tags in the sofaString, so every
    annotation/section/sentence offset must be remapped after stripping them
    (checked by asserting text[begin:end] still equals the annotated term).
  - tai: plain sofaString plus separate page/dblbrk/image elements.
"""

import gzip
import html
import importlib.util
import os
import tempfile
import unittest

_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "textpresso_classifiers")

# Loaded by file path, not package import, so textpresso_classifiers/__init__.py
# (sklearn/numpy) never runs -- same as the GUI backend.
_spec = importlib.util.spec_from_file_location("casdoc", os.path.join(_PKG, "casdoc.py"))
casdoc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(casdoc)


def _write_cas(directory, sofa, elements):
    """Write a minimal CAS2 file: sofaString (XML-escaped) plus the given elements."""
    body = "\n".join(elements)
    content = (f'<?xml version="1.0"?><xmi:XMI>\n{body}\n'
               f'<cas:Sofa xmi:id="1" sofaNum="1" sofaID="_InitialView" '
               f'sofaString="{html.escape(sofa, quote=True)}"/>\n</xmi:XMI>')
    path = os.path.join(directory, "paper.tpcas.gz")
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _span(sofa, word, start=0):
    b = sofa.index(word, start)
    return b, b + len(word)


class TestPdfLayout(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sofa = (
            "<_pdf _page=1/>Seed <_pdf _fnc=Times-Bold/>development<_pdf _cr/>in maize."
            "<_pdf _sbr/>Ca<_pdf _ydiff=3/>2+ signals."
            "<_pdf _page=2/><_pdf _image=MaizeX/acc1/images/acc1.3.jpg/>"
            "Drought tolerance."
        )
        s = self.sofa
        dev_b, dev_e = _span(s, "development")
        dro_b, dro_e = _span(s, "Drought tolerance")
        sent_b = s.index("Seed")
        sent_e = s.index("<_pdf _sbr/>")
        self.path = _write_cas(self.tmp.name, s, [
            '<textpresso:rawsource xmi:id="2" sofa="1" begin="0" end="1" value="pdf"/>',
            f'<textpresso:lexicalannotation xmi:id="3" sofa="1" begin="{dev_b}" end="{dev_e}" '
            'term="development" category="developmental process (GO:0032502)"/>',
            f'<textpresso:lexicalannotation xmi:id="4" sofa="1" begin="{dev_b}" end="{dev_e}" '
            'term="development" category="PTCATbiological_process (GO:0008150)"/>',
            f'<textpresso:lexicalannotation xmi:id="5" sofa="1" begin="{dro_b}" end="{dro_e}" '
            'term="Drought tolerance" category="drought tolerance (TO:0000276)"/>',
            f'<textpresso:sentence xmi:id="6" sofa="1" begin="{sent_b}" end="{sent_e}" content="x"/>',
            f'<textpresso:section xmi:id="7" sofa="1" begin="{dro_b}" end="{len(s)}" type="result"/>',
        ])
        self.doc = casdoc.parse_cas_document(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tags_stripped(self):
        text = self.doc["text"]
        self.assertNotIn("<_pdf", text)
        self.assertIn("Seed development in maize.", text)
        self.assertIn("Ca2+ signals.", text)  # _ydiff dropped, not turned into a space

    def test_annotation_offsets_remapped(self):
        text = self.doc["text"]
        anns = self.doc["annotations"]
        self.assertEqual(len(anns), 2)  # PTCAT skipped
        for a in anns:
            self.assertEqual(text[a["begin"]:a["end"]], a["term"])
        self.assertEqual([a["ontology"] for a in anns], ["GO", "TO"])
        self.assertEqual(anns[1]["onto_id"], "TO:0000276")

    def test_sentence_and_section_remapped(self):
        text = self.doc["text"]
        sent = self.doc["sentences"][0]
        self.assertEqual(text[sent["begin"]:sent["end"]], "Seed development in maize.")
        sec = self.doc["sections"][0]
        self.assertEqual(sec["type"], "result")
        self.assertEqual(text[sec["begin"]:sec["end"]], "Drought tolerance.")

    def test_structure(self):
        text = self.doc["text"]
        self.assertEqual(self.doc["rawsource"], "pdf")
        self.assertEqual([p["page"] for p in self.doc["pages"]], [1, 2])
        self.assertTrue(text[self.doc["pages"][1]["pos"]:].lstrip().startswith("Drought"))
        self.assertEqual(len(self.doc["paragraphs"]), 1)
        self.assertTrue(text[self.doc["paragraphs"][0]:].lstrip().startswith("Ca2+"))
        fig = self.doc["figures"]
        self.assertEqual(len(fig), 1)
        self.assertEqual(fig[0]["name"], "acc1.3.jpg")  # basename only
        self.assertEqual(fig[0]["page"], 2)


class TestTaiLayout(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        s = "Title line\nwrapped.\n\nPage two & more text about anthocyanin.\n"
        p2 = s.index("Page two")
        brk = s.index("\n\n")
        ab, ae = _span(s, "anthocyanin")
        self.text = s
        self.path = _write_cas(self.tmp.name, s, [
            '<textpresso:rawsource xmi:id="2" sofa="1" begin="0" end="1" value="tai"/>',
            f'<textpresso:page xmi:id="3" sofa="1" begin="0" end="{p2}" value="1"/>',
            f'<textpresso:page xmi:id="4" sofa="1" begin="{p2}" end="{len(s)}" value="2"/>',
            f'<textpresso:dblbrk xmi:id="5" sofa="1" begin="{brk}" end="{brk + 2}"/>',
            f'<textpresso:image xmi:id="6" sofa="1" begin="{p2}" end="{len(s)}" '
            'filename="MaizeOA/acc/images/acc.00002.img-001.png" page="2"/>',
            f'<textpresso:lexicalannotation xmi:id="7" sofa="1" begin="{ab}" end="{ae}" '
            'term="anthocyanin" category="anthocyanin-containing compound biosynthetic process (GO:0009718)"/>',
        ])
        self.doc = casdoc.parse_cas_document(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_unchanged(self):
        self.assertEqual(self.doc["text"], self.text)  # &amp; unescaped, \n kept

    def test_structure(self):
        p2 = self.text.index("Page two")
        self.assertEqual(self.doc["rawsource"], "tai")
        self.assertEqual(self.doc["pages"], [{"pos": 0, "page": 1}, {"pos": p2, "page": 2}])
        self.assertEqual(self.doc["paragraphs"], [self.text.index("\n\n")])
        self.assertEqual(self.doc["figures"], [{"pos": p2, "page": 2, "name": "acc.00002.img-001.png"}])

    def test_annotation(self):
        a = self.doc["annotations"][0]
        self.assertEqual(self.doc["text"][a["begin"]:a["end"]], "anthocyanin")
        self.assertEqual(a["ontology"], "GO")


if __name__ == "__main__":
    unittest.main()
