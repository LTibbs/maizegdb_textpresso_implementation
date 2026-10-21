"""Tests for the shared annotation hygiene layer and its CLI wiring.

Covers:
  - the abbreviation case check drops a form whose stored spelling is
    abbreviation-cased ('Ltd' vs 'LTD'), but not sentence case of a lowercase
    stored form ('Root' vs 'root') and never a gene-ontology mention ('HAK5'
    vs the registered lowercase symbol 'hak5').
  - OntologyView.from_category_index reads the original-case spellings from
    the index's surface_forms.
  - never_italicised(): drops a short RELATED alias that is never italic,
    exempts the EXACT channel and registered symbols, abstains when the form
    is absent from the body.
  - references/acknowledgments are dropped with a section reason.
  - tpc_search_combined forwards --hygiene / --no-italics to the sidecar.
"""

import argparse
import importlib.util
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hy = _load("annotation_hygiene", "textpresso_classifiers/annotation_hygiene.py")
it = _load("annotation_italics", "textpresso_classifiers/annotation_italics.py")


def _ann(term, ontology, category, begin=0):
    return {"term": term, "ontology": ontology, "category": category,
            "begin": begin, "end": begin + len(term)}


class TestCaseCheck(unittest.TestCase):
    def setUp(self):
        self.view = hy.OntologyView.from_category_index({
            "parents_by_type": {},
            "synonyms": {},
            "surface_forms": {"ltd": {"LTD"}, "root": {"root"}, "hak5": {"hak5"}},
        })

    def _clean(self, annotations):
        return hy.clean_annotations(annotations, [], ontology_view=self.view,
                                    collapse=False)

    def test_surface_forms_feed_known_forms(self):
        self.assertEqual(self.view.known_forms("Ltd"), {"LTD"})

    def test_drops_case_mismatch_against_abbreviation_cased_form(self):
        kept, dropped = self._clean([_ann("Ltd", "GO", "long-term depression (GO:0060292)")])
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0]["reason"], "abbrev_case_mismatch:LTD")

    def test_keeps_sentence_case_of_lowercase_form(self):
        kept, dropped = self._clean([_ann("Root", "PO", "root (PO:0009005)")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_gene_ontologies_are_exempt(self):
        kept, dropped = self._clean([_ann("HAK5", "MAIZE_GENES", "hak5 (tpzm:0000001)")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])


class TestItalicsRule(unittest.TestCase):
    PAPER = {"myb": [0, 5], "o2": [0, 14], "sh2": [3, 3]}

    def test_drops_never_italic_related_alias(self):
        self.assertEqual(it.never_italicised(self.PAPER, "MYB", "MAIZE_GENES_RELATED", "alias"),
                         (True, 0, 5))

    def test_exact_channel_is_exempt(self):
        self.assertFalse(it.never_italicised(self.PAPER, "MYB", "MAIZE_GENES", "alias")[0])

    def test_registered_symbol_is_exempt(self):
        self.assertFalse(it.never_italicised(self.PAPER, "o2", "MAIZE_GENES_RELATED", "registered")[0])

    def test_abstains_when_form_absent_from_body(self):
        self.assertEqual(it.never_italicised(self.PAPER, "bhlh", "MAIZE_GENES_RELATED", "alias"),
                         (False, None, None))

    def test_not_applied_to_go(self):
        self.assertFalse(it.never_italicised(self.PAPER, "myb", "GO", "unknown")[0])

    def test_clean_annotations_reports_reason(self):
        kept, dropped = hy.clean_annotations(
            [_ann("myb", "MAIZE_GENES_RELATED", "myb1 (tpzm:0000002)")], [],
            italics=self.PAPER, italic_rule=it.never_italicised,
            obo_class_of=lambda t: "alias")
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0]["reason"], "not_italicised:0/5")


class TestSectionDrop(unittest.TestCase):
    def test_reference_section_dropped(self):
        sections = [{"begin": 100, "end": 200, "type": "references"}]

        def types_at(begin, end, secs):
            return [s["type"] for s in secs if s["begin"] <= begin < s["end"]]

        kept, dropped = hy.clean_annotations(
            [_ann("starch", "GO", "starch (CHEBI:28017)", begin=10),
             _ann("starch", "GO", "starch (CHEBI:28017)", begin=150)],
            sections, section_types_at=types_at)
        self.assertEqual([a["begin"] for a in kept], [10])
        self.assertEqual(dropped[0]["reason"], "section:references")


class TestCliForwardsHygiene(unittest.TestCase):
    def setUp(self):
        self.tpc = _load("tpc_search_combined", "bin/tpc_search_combined.py")

    def _params(self, **flags):
        captured = {}

        def fake_urlopen(req, *a, **kw):
            captured["url"] = req.full_url
            raise RuntimeError("stop after capturing the request")

        args = argparse.Namespace(cas_root=None, url=self.tpc.DEFAULT_URL, **flags)
        with mock.patch.object(self.tpc.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(RuntimeError):
                self.tpc._load_annotations({"identifier": "MaizeOA/x/x.tpcas"}, args)
        return parse_qs(urlparse(captured["url"]).query)

    def test_default_sends_no_hygiene_params(self):
        q = self._params()
        self.assertNotIn("hygiene", q)
        self.assertNotIn("italics", q)

    def test_hygiene_and_no_italics_forwarded(self):
        q = self._params(hygiene=True, italics=False)
        self.assertEqual(q["hygiene"], ["1"])
        self.assertEqual(q["italics"], ["0"])


if __name__ == "__main__":
    unittest.main()
