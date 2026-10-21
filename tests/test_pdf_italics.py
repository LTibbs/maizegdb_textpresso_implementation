"""Tests for bin/pdf_italics.py.

The font-classification and calibration logic is tested on synthetic spans, so
it runs without PyMuPDF. The end-to-end test builds a small PDF with PyMuPDF's
base-14 fonts and is skipped when PyMuPDF is not installed.
"""

import importlib.util
import os
import tempfile
import unittest

_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")


def _load_module(name, filename):
    path = os.path.join(_BIN, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pi = _load_module("pdf_italics", "pdf_italics.py")
ji = _load_module("jats_italics", "jats_italics.py")

try:
    import fitz  # noqa: F401
    HAVE_FITZ = True
except ImportError:
    HAVE_FITZ = False


class TestFontNames(unittest.TestCase):
    def test_named_italic_faces(self):
        for name in ("Arial-ItalicMT", "Helvetica-Oblique", "MinionPro-It",
                     "TimesNewRomanPS-BoldItalicMT", "AdvOTf0bf83d5.I",
                     "Times-BI", "LiberationSerif-Italic", "AdvPSUnv-O",
                     "AdvPSUnv-BO"):
            self.assertTrue(pi.NAMED_ITALIC_RE.search(name), name)

    def test_roman_faces(self):
        for name in ("ArialMT", "Arial-BoldMT", "AdvP4041BD", "AdvOTa9103878",
                     "TimesNewRomanPSMT", "Helvetica", "LiberationSans-Bold",
                     "AdvPSUnv", "AdvPSUnv-B", "Courier"):
            self.assertFalse(pi.NAMED_ITALIC_RE.search(name), name)

    def test_stop_lines(self):
        for line in ("References", "REFERENCES", "Literature Cited",
                     "7. References", "Acknowledgments", "Acknowledgements:",
                     "References and Notes"):
            self.assertTrue(pi.STOP_RE.match(line), line)
        for line in ("References were checked against GenBank.",
                     "We thank the reviewers", "Results"):
            self.assertFalse(pi.STOP_RE.match(line), line)


class TestFoldText(unittest.TestCase):
    """fold_text must match the ingest's fold (jats_text._map_char and
    PdfTextNormalizer.h), or index keys and CAS spellings diverge."""

    def test_hyphens_fold_to_ascii_in_both_paths(self):
        for pdf in (False, True):
            self.assertEqual("PYR1-LIKE", ji.fold_text("PYR1‐LIKE", pdf=pdf))
            self.assertEqual("ZmPR-1-8", ji.fold_text("ZmPR‑1‐8", pdf=pdf))
            self.assertEqual("regulation", ji.fold_text("regu­lation", pdf=pdf))

    def test_dashes_and_minus_kept(self):
        for pdf in (False, True):
            self.assertEqual("10−5 a–b", ji.fold_text("10−5 a–b", pdf=pdf))

    def test_non_bmp_nfkc_folded(self):
        for pdf in (False, True):
            self.assertEqual("e", ji.fold_text("\U0001d452", pdf=pdf))

    def test_pdf_only_rules(self):
        self.assertEqual("fl2", ji.fold_text("ﬂ2", pdf=True))
        self.assertEqual("ﬂ2", ji.fold_text("ﬂ2"))
        self.assertEqual("sh2", ji.fold_text("sh​2", pdf=True))
        self.assertEqual("sh​2", ji.fold_text("sh​2"))

    def test_folded_form_is_one_token(self):
        tokens = [m.group(1).lower() for m in
                  ji.TOKEN_RE.finditer(ji.fold_text("the PYR1‐LIKE family"))]
        self.assertIn("pyr1-like", tokens)

    def test_jats_stats_use_fold(self):
        xml = (b"<article><body><p>The <italic>PYR1\xe2\x80\x90LIKE</italic> "
               b"receptor and PYR1\xe2\x80\x90LIKE again.</p></body></article>")
        stats = ji.italic_stats(xml)
        self.assertEqual({"italic": 1, "total": 2}, stats["pyr1-like"])
        self.assertNotIn("like", stats)


class TestItalicFonts(unittest.TestCase):
    BODY = "AdvP49811"

    def _spans(self, extra):
        body = [(self.BODY, 4, "the quick brown fox jumps over the lazy dog " * 20)]
        return body + extra

    def test_flag_name_and_angle_are_trusted(self):
        spans = self._spans([("X1", 2, "a"), ("Arial-ItalicMT", 0, "b"),
                             ("OddFont", 0, "c")])
        fonts, calibrated = pi.italic_fonts(spans, {"OddFont": -12.0})
        self.assertEqual({"X1", "Arial-ItalicMT", "OddFont"}, fonts)
        self.assertEqual(set(), calibrated)

    def test_opaque_font_calibrated_from_anchors(self):
        spans = self._spans([("AdvP4041BD", 4, "Zea mays"),
                             ("AdvP4041BD", 4, "mop2"),
                             ("AdvP4041BD", 4, "in vivo")])
        fonts, calibrated = pi.italic_fonts(spans, {})
        self.assertEqual({"AdvP4041BD"}, calibrated)
        self.assertIn("AdvP4041BD", fonts)

    def test_body_font_never_calibrated(self):
        # Anchors in the dominant font are roman prose, not evidence of italics.
        spans = self._spans([(self.BODY, 4, "Zea mays in vivo Arabidopsis thaliana")])
        _, calibrated = pi.italic_fonts(spans, {})
        self.assertEqual(set(), calibrated)

    def test_styled_name_not_calibrated(self):
        # A name that already declares a roman/bold style is trusted as such.
        spans = self._spans([("Arial-BoldMT", 4, "Zea mays in vivo")])
        _, calibrated = pi.italic_fonts(spans, {})
        self.assertEqual(set(), calibrated)

    def test_single_anchor_not_enough(self):
        spans = self._spans([("AdvP4041BD", 4, "Zea")])
        _, calibrated = pi.italic_fonts(spans, {})
        self.assertEqual(set(), calibrated)


@unittest.skipUnless(HAVE_FITZ, "PyMuPDF not installed")
class TestEndToEnd(unittest.TestCase):
    def _pdf(self, lines):
        """lines: [(text, fontname)] with PyMuPDF base-14 short names."""
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        doc = fitz.open()
        page = doc.new_page()
        y = 72
        for text, font in lines:
            page.insert_text((72, y), text, fontname=font, fontsize=10)
            y += 14
        doc.save(tmp.name)
        doc.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_counts_and_reference_exclusion(self):
        path = self._pdf([
            ("We cloned the maize gene", "helv"),
            ("sh2 from Zea mays seedlings", "heit"),  # Helvetica-Oblique, >= MIN_ITALIC_CHARS
            ("and measured ABA content.", "helv"),
            ("The sh2 mutant lacks ABA.", "helv"),
            ("References", "helv"),
            ("Smith J (2001) sh2 and aba in maize.", "helv"),
        ])
        stats, info = pi.italic_stats(path, keep_forms={"sh2", "aba"})
        self.assertTrue(info["stopped_at_back_matter"])
        self.assertEqual([1, 2], stats["sh2"])     # reference occurrence excluded
        self.assertEqual([0, 2], stats["aba"])

    def test_no_italics_abstains(self):
        path = self._pdf([("We cloned sh2 and measured aba.", "helv")] * 5)
        stats, info = pi.italic_stats(path, keep_forms={"sh2", "aba"})
        self.assertIsNone(stats)
        self.assertEqual("no detectable italics", info["abstain"])

    def test_pdf_path_layouts(self):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        os.makedirs(os.path.join(root, "10.1_a"))
        nested = os.path.join(root, "10.1_a", "10.1_a.pdf")
        flat = os.path.join(root, "10.1_b.pdf")
        open(nested, "w").close(); open(flat, "w").close()
        self.assertEqual(nested, pi.pdf_path_for(root, "10.1_a"))
        self.assertEqual(flat, pi.pdf_path_for(root, "10.1_b"))
        self.assertIsNone(pi.pdf_path_for(root, "10.1_c"))


if __name__ == "__main__":
    unittest.main()
