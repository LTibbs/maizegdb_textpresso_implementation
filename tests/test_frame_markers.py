"""Tests for dropping matches on the tokenizer's "Beginning/End of Article"
markers (casannot.frame_marker_spans()), in both CAS2 parsers: casannot's
parse_cas_file() (search, API, CLI) and casdoc's parse_cas_document() (GUI
full-text viewer).

The case that prompted it: TO's RELATED synonym "End" (endosperm quality)
matched the "End of Article" marker in every paper.
"""

import gzip
import html
import importlib.util
import os
import sys
import tempfile
import unittest

_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "textpresso_classifiers")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_PKG, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


casannot = _load("casannot")
casdoc = _load("casdoc")

TO_END = "RELATED:endosperm quality (TO:0000587)"


def _write_cas(directory, sofa, annotations, sentences=()):
    """Minimal CAS2: one lexicalannotation per (begin, end, term, category)."""
    els = [f'<textpresso:sentence xmi:id="{10 + i}" sofa="1" begin="{b}" end="{e}" '
           f'content="{html.escape(sofa[b:e], quote=True)}"/>' for i, (b, e) in enumerate(sentences)]
    els += [f'<textpresso:lexicalannotation xmi:id="{100 + i}" sofa="1" begin="{b}" end="{e}" '
            f'term="{html.escape(t, quote=True)}" category="{html.escape(c, quote=True)}"/>'
            for i, (b, e, t, c) in enumerate(annotations)]
    content = ('<?xml version="1.0"?><xmi:XMI>\n' + "\n".join(els) + "\n"
               f'<cas:Sofa xmi:id="1" sofaNum="1" sofaID="_InitialView" '
               f'sofaString="{html.escape(sofa, quote=True)}"/>\n</xmi:XMI>')
    path = os.path.join(directory, "paper.tpcas.gz")
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _ann(sofa, word, category, start=0):
    b = sofa.index(word, start)
    return b, b + len(word), word, category


class TestFrameMarkerSpans(unittest.TestCase):

    def test_markers_at_both_ends(self):
        sofa = "\nBeginning of Article\nTitle\nBody.\nEnd of Article\n"
        spans = casannot.frame_marker_spans(sofa)
        self.assertEqual([sofa[b:e] for b, e in spans], ["Beginning of Article", "End of Article"])

    def test_pdf_tags_around_markers(self):
        sofa = "\nBeginning of Article\n<_pdf _page=1/>Title<_pdf _cr/>\nEnd of Article\n<_pdf _cr/>"
        spans = casannot.frame_marker_spans(sofa)
        self.assertEqual([sofa[b:e] for b, e in spans], ["Beginning of Article", "End of Article"])

    def test_same_words_in_paper_text_are_not_markers(self):
        sofa = "Introduction\nThe End of Article 5 applies.\nBeginning of Article 2.\nDone."
        self.assertEqual(casannot.frame_marker_spans(sofa), [])

    def test_no_sofa(self):
        self.assertEqual(casannot.frame_marker_spans(""), [])
        self.assertEqual(casannot.frame_marker_spans(None), [])


class TestParsers(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # "End" also occurs as a real word in the body, after the title's
        # "endosperm" inside the beginning-of-article frame.
        self.sofa = ("\nBeginning of Article\n<_pdf _page=1/>Maize endosperm quality\n"
                     "End of season yield rose.<_pdf _cr/>\nEnd of Article\n")
        s = self.sofa
        marker = s.rindex("End of Article")
        self.anns = [
            _ann(s, "Beginning", "beginning (PATO:0000001)"),
            _ann(s, "endosperm quality", "endosperm quality (TO:0000587)"),
            _ann(s, "End", TO_END, s.index("End of season")),
            _ann(s, "End", TO_END, marker),
            _ann(s, "Article", "article (ECO:0000001)", marker),
        ]
        title_b = s.index("Maize")
        body_b = s.index("End of season")
        self.path = _write_cas(self.tmp.name, s, self.anns, sentences=[
            (title_b, title_b + len("Maize endosperm quality")),
            (body_b, body_b + len("End of season yield rose."))])

    def test_casannot_drops_marker_matches_only(self):
        sentences, annotations, _ = casannot.parse_cas_file(self.path)
        self.assertEqual([(a["begin"], a["term"]) for a in annotations],
                         [(self.anns[1][0], "endosperm quality"), (self.anns[2][0], "End")])
        # The marker match no longer gets a synthetic sentence of whatever
        # precedes it (the bibliography, in a real paper).
        marker = self.sofa.rindex("End of Article")
        self.assertFalse(any(s["begin"] <= marker < s["end"] for s in sentences))

    def test_casdoc_drops_marker_matches_only(self):
        doc = casdoc.parse_cas_document(self.path)
        self.assertEqual([(doc["text"][a["begin"]:a["end"]], a["category"]) for a in doc["annotations"]],
                         [("endosperm quality", "endosperm quality (TO:0000587)"), ("End", TO_END)])


if __name__ == "__main__":
    unittest.main()
