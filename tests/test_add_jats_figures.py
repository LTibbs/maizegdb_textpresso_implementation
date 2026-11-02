"""Tests for bin/add_jats_figures.py: placing PDF-extracted figures in JATS text."""

import gzip
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
_spec = importlib.util.spec_from_file_location("add_jats_figures", os.path.join(_BIN, "add_jats_figures.py"))
ajf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ajf)

FILLER = "The quick survey of maize root anatomy continued across several field seasons. " * 4
CAPTION = "Figure 1. Expression of ZmHPAT2 in roots colonised by arbuscular mycorrhizal fungi at six weeks."
TEXT = ("\nBeginning of Article\nIntroduction paragraph about maize growth and development.\n\n"
        + FILLER + "\n\n" + CAPTION + "\n\nEnd of Article\n")
CAP_BEGIN = TEXT.index(CAPTION)
FIGURES = [{"begin": CAP_BEGIN, "end": CAP_BEGIN + len(CAPTION), "id": "f1", "graphic": "g1.jpg"}]


def norm(s):
    return ajf.normalize(s)[0]


class TestPlace(unittest.TestCase):
    def test_caption_page_near_and_end(self):
        pages = {
            1: norm("Introduction paragraph about maize growth and development. " + FILLER),
            2: norm("Fig. 1 Expression of ZmHPAT2 in roots colonised by arbuscular mycorrhizal fungi"
                    " at six weeks."),
            3: "",                              # image-only supplement page
        }
        images = {1: ["a.00001.img-000.png"], 2: ["a.00002.img-000.jpg", "a.00002.img-001.jpg"],
                  3: ["a.00003.img-000.png"]}
        placed = ajf.place(TEXT, FIGURES, pages, images)
        how = {name: (b, e, h) for b, e, _, name, h in placed}
        self.assertEqual(how["a.00002.img-000.jpg"], (CAP_BEGIN, CAP_BEGIN + len(CAPTION), "caption"))
        self.assertEqual(how["a.00002.img-001.jpg"][2], "caption")
        b, e, h = how["a.00001.img-000.png"]
        self.assertEqual((h, b, e), ("page", TEXT.index("Introduction"), TEXT.index("Introduction")))
        self.assertEqual(how["a.00003.img-000.png"], (b, b, "near"))   # nearest earlier page with text

    def test_end_when_no_page_is_found(self):
        placed = ajf.place(TEXT, [], {1: ""}, {1: ["a.00001.img-000.png"]})
        self.assertEqual(placed[0][4], "end")
        self.assertEqual(TEXT[placed[0][0]:], "End of Article\n")

    def test_ligatures_fold(self):
        self.assertEqual(norm("ﬁeld ﬂowering"), "fieldflowering")


class TestUtf16(unittest.TestCase):
    def test_non_bmp_counts_two(self):
        self.assertEqual(ajf.utf16_offsets("a\U0001D44Eb"), [0, 1, 3, 4])


def make_cas(text, rawsource="jats", extra=""):
    esc = text.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;").replace("\n", "&#10;")
    u16 = ajf.utf16_offsets(text)[-1]
    return ('<?xml version="1.0" encoding="UTF-8"?><xmi:XMI xmlns:textpresso="http:///org/apache/uima/textpresso.ecore" '
            'xmlns:cas="http:///uima/cas.ecore" xmlns:tcas="http:///uima/tcas.ecore" xmlns:xmi="http://www.omg.org/XMI" '
            'xmi:version="2.0"><cas:NULL xmi:id="0"/> <cas:Sofa xmi:id="1" sofaNum="1" sofaID="_InitialView" '
            'mimeType="text" sofaString="%s"/>\n'
            '<tcas:DocumentAnnotation xmi:id="8" sofa="1" begin="0" end="%d" language="x-unspecified"/>\n'
            '<textpresso:token xmi:id="13" sofa="1" begin="1" end="10" content="Beginning" aid="7"/>\n'
            '<textpresso:rawsource xmi:id="20" sofa="1" begin="0" end="%d" value="%s"/>\n%s'
            '<cas:View sofa="1" members="8 13 20"/></xmi:XMI>' % (esc, u16, u16, rawsource, extra))


class TestUpdateCas(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "a.tpcas.gz")
        self.placed = [(CAP_BEGIN, CAP_BEGIN + 5, 2, "a.00002.img-000.jpg", "caption")]

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, xmi):
        with gzip.open(self.path, "wt", encoding="utf-8") as f:
            f.write(xmi)

    def read(self):
        with gzip.open(self.path, "rt", encoding="utf-8") as f:
            return f.read()

    def test_adds_image_and_view_member(self):
        self.write(make_cas(TEXT))
        self.assertEqual(ajf.update_cas(self.path, "Corp", "a", self.placed, TEXT, False), "updated")
        xmi = self.read()
        self.assertIn('<textpresso:image xmi:id="21" sofa="1" begin="%d" end="%d" '
                      'filename="Corp/a/images/a.00002.img-000.jpg" page="2" aid="8"/>'
                      % (CAP_BEGIN, CAP_BEGIN + 5), xmi)
        self.assertIn('members="8 13 20 21"', xmi)
        # second run leaves it alone; --replace swaps it without duplicating
        self.assertEqual(ajf.update_cas(self.path, "Corp", "a", self.placed, TEXT, False), "has-images")
        self.assertEqual(ajf.update_cas(self.path, "Corp", "a", self.placed, TEXT, True), "updated")
        xmi = self.read()
        self.assertEqual(xmi.count("<textpresso:image "), 1)
        self.assertIn('members="8 13 20 21"', xmi)   # the removed image's id is free again

    def test_strip_restores_original(self):
        original = make_cas(TEXT)
        self.write(original)
        ajf.update_cas(self.path, "Corp", "a", self.placed * 3, TEXT, False)
        self.assertNotEqual(self.read(), original)
        self.assertEqual(ajf.strip_images(self.read()), original)

    def test_utf16_offsets_written(self):
        text = "\U0001D44E" + TEXT
        self.write(make_cas(text))
        placed = [(1 + CAP_BEGIN, 1 + CAP_BEGIN, 2, "a.00002.img-000.jpg", "caption")]
        ajf.update_cas(self.path, "Corp", "a", placed, text, False)
        self.assertIn('begin="%d" end="%d"' % (CAP_BEGIN + 2, CAP_BEGIN + 2), self.read())

    def test_refuses_pdf_cas_and_changed_text(self):
        self.write(make_cas(TEXT, rawsource="tai"))
        self.assertEqual(ajf.update_cas(self.path, "Corp", "a", self.placed, TEXT, False), "not-jats")
        self.write(make_cas(TEXT))
        self.assertEqual(ajf.update_cas(self.path, "Corp", "a", self.placed, TEXT + "x", False), "text-mismatch")


class TestJatsJsonMode(unittest.TestCase):
    def test_writes_images_array(self):
        base = tempfile.mkdtemp()
        try:
            acc = "10.1_x"
            xml = os.path.join(base, "raw_files", "xml", "Corp", acc)
            pdf = os.path.join(base, "raw_files", "pdf", "Corp", acc)
            os.makedirs(xml)
            os.makedirs(pdf)
            with open(os.path.join(xml, acc + ".jats.json"), "w", encoding="utf-8") as f:
                json.dump({"text": TEXT, "figures": FIGURES}, f)
            with open(os.path.join(pdf, acc + ".00002.txt"), "w", encoding="utf-8") as f:
                f.write(CAPTION)
            open(os.path.join(pdf, acc + ".00002.img-000.jpg"), "wb").close()
            import sys
            argv = sys.argv
            sys.argv = ["add_jats_figures.py", "-c", "Corp", "--base", base, "--jats-json"]
            try:
                ajf.main()
            finally:
                sys.argv = argv
            with open(os.path.join(xml, acc + ".jats.json"), encoding="utf-8") as f:
                doc = json.load(f)
            self.assertEqual(doc["images"], [{"begin": CAP_BEGIN, "end": CAP_BEGIN + len(CAPTION), "page": 2,
                                              "filename": "Corp/%s/images/%s.00002.img-000.jpg" % (acc, acc)}])
        finally:
            shutil.rmtree(base)


if __name__ == "__main__":
    unittest.main()
