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
import json
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


class TestEnglishWordGenes(unittest.TestCase):
    """Gene mentions that are English words need the paper's support."""
    ENGLISH = frozenset({"late", "red", "co", "binding"})
    LATE = "Zm00001eb059970 (tpzm:0007478)"      # 'late' is an EXACT locus_synonym of this gene

    def _clean(self, annotations, italics, **kw):
        return hy.clean_annotations(annotations, [], collapse=False, italics=italics,
                                    italic_rule=it.never_italicised,
                                    obo_class_of=lambda t: "alias",
                                    english_forms=self.ENGLISH, **kw)

    def test_dropped_with_no_italics_and_no_other_synonym(self):
        # a paper missing from the italics index ({}): no typography to go on
        kept, dropped = self._clean([_ann("late", "MAIZE_GENES", self.LATE)], {})
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0]["reason"], "english_word_gene:no_italic_no_synonym")

    def test_applies_to_the_exact_channel_the_italics_rule_exempts(self):
        paper = {"late": [0, 4]}               # present, never italic
        kept, dropped = self._clean([_ann("late", "MAIZE_GENES", self.LATE)], paper)
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0]["reason"], "english_word_gene:no_italic_no_synonym")

    def test_kept_when_italicised_at_least_once(self):
        paper = {"late": [1, 40]}
        kept, dropped = self._clean([_ann("late", "MAIZE_GENES", self.LATE)], paper)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_kept_when_another_synonym_of_the_gene_appears(self):
        annotations = [_ann("late", "MAIZE_GENES", self.LATE, begin=0),
                       _ann("Zm00001eb059970", "MAIZE_GENES", self.LATE, begin=50)]
        kept, dropped = self._clean(annotations, {})
        self.assertEqual({a["term"] for a in kept}, {"late", "Zm00001eb059970"})
        self.assertEqual(dropped, [])

    def test_synonym_of_a_different_gene_is_no_support(self):
        annotations = [_ann("late", "MAIZE_GENES", self.LATE),
                       _ann("zcn8", "MAIZE_GENES", "zcn8 (tpzm:0000099)", begin=50)]
        kept, _ = self._clean(annotations, {})
        self.assertEqual([a["term"] for a in kept], ["zcn8"])

    def test_two_english_synonyms_do_not_support_each_other(self):
        annotations = [_ann("late", "MAIZE_GENES", self.LATE),
                       _ann("red", "MAIZE_GENES", self.LATE, begin=50)]
        kept, dropped = self._clean(annotations, {})
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 2)

    def test_a_dropped_synonym_is_no_support(self):
        # the other synonym sits in the references, so it never reaches the rule
        sections = [{"begin": 50, "end": 100, "type": "references"}]

        def types_at(begin, end, secs):
            return [s["type"] for s in secs if s["begin"] <= begin < s["end"]]
        annotations = [_ann("late", "MAIZE_GENES", self.LATE, begin=0),
                       _ann("Zm00001eb059970", "MAIZE_GENES", self.LATE, begin=60)]
        kept, dropped = hy.clean_annotations(
            annotations, sections, collapse=False, italics={}, italic_rule=it.never_italicised,
            obo_class_of=lambda t: "alias", english_forms=self.ENGLISH, section_types_at=types_at)
        self.assertEqual(kept, [])
        self.assertEqual(sorted(d["reason"].split(":")[0] for d in dropped),
                         ["english_word_gene", "section"])

    def test_capitals_are_no_exemption(self):
        # 'CO' and 'ABA' match the English-form list case-insensitively. An
        # all-caps abbreviation needs the same support as 'co' (measured against
        # curator gene lists: the exemption kept 19,175 mentions at 3.1% confirmed)
        for term in ("CO", "RED", "Co", "co"):
            kept, dropped = self._clean([_ann(term, "MAIZE_GENES", "co (tpzm:0000005)")], {})
            self.assertEqual(kept, [], term)
            self.assertEqual(dropped[0]["reason"], "english_word_gene:no_italic_no_synonym")

    def test_all_caps_form_kept_with_support(self):
        # italic evidence is keyed by the lowercase form, so 'CO' is rescued by italic 'co'
        kept, _ = self._clean([_ann("CO", "MAIZE_GENES", "co (tpzm:0000005)")], {"co": [2, 30]})
        self.assertEqual(len(kept), 1)
        annotations = [_ann("CO", "MAIZE_GENES", "co (tpzm:0000005)", begin=0),
                       _ann("CONSTANS", "MAIZE_GENES", "co (tpzm:0000005)", begin=40)]
        kept, dropped = self._clean(annotations, {})
        self.assertEqual({a["term"] for a in kept}, {"CO", "CONSTANS"})
        self.assertEqual(dropped, [])

    def test_sentence_case_is_the_english_word(self):
        kept, _ = self._clean([_ann("Late", "MAIZE_GENES", self.LATE)], {})
        self.assertEqual(kept, [])

    def test_related_channel_too(self):
        kept, _ = self._clean([_ann("binding", "MAIZE_GENES_RELATED", "RELATED:abc1 (tpzm:0000007)")], {})
        self.assertEqual(kept, [])

    def test_only_gene_ontologies(self):
        kept, _ = self._clean([_ann("late", "PATO", "RELATED:delayed (PATO:0000502)")], {})
        self.assertEqual(len(kept), 1)

    def test_off_without_a_forms_list_and_without_typography(self):
        ann = [_ann("late", "MAIZE_GENES", self.LATE)]
        kept, _ = hy.clean_annotations(ann, [], collapse=False, italics={},
                                       italic_rule=it.never_italicised, obo_class_of=lambda t: "alias")
        self.assertEqual(len(kept), 1)
        # italics=None is "typography off" (italics=0): the rule's first leg is typography
        kept, _ = self._clean(ann, None)
        self.assertEqual(len(kept), 1)

    def test_hidden_channel_still_supports(self):
        # RELATED synonyms are not shown by default but still count as evidence
        annotations = [_ann("late", "MAIZE_GENES", self.LATE, begin=0),
                       _ann("early flowering 1", "MAIZE_GENES_RELATED", "RELATED:" + self.LATE, begin=50)]
        kept, dropped = self._clean(annotations, {},
                                    include=lambda a: not a["ontology"].endswith("_RELATED"))
        self.assertEqual([a["term"] for a in kept], ["late"])
        self.assertEqual(dropped, [])

    def test_load_english_forms(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "e.json")
            with open(p, "w") as fh:
                json.dump({"zipf_min": 3.0, "forms": ["Late", "red"]}, fh)
            self.assertEqual(it.load_english_forms(p), frozenset({"late", "red"}))
            self.assertEqual(it.load_english_forms(os.path.join(d, "missing.json")), frozenset())
            self.assertEqual(it.load_english_forms(""), frozenset())


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

    def test_default_requests_hygiene(self):
        # On by default, and sent explicitly so the result never depends on
        # the server's TEXTPRESSO_HYGIENE_DEFAULT.
        q = self._params()
        self.assertEqual(q["hygiene"], ["1"])
        self.assertNotIn("italics", q)

    def test_no_hygiene_sends_zero(self):
        q = self._params(hygiene=False)
        self.assertEqual(q["hygiene"], ["0"])
        self.assertNotIn("italics", q)

    def test_hygiene_and_no_italics_forwarded(self):
        q = self._params(hygiene=True, italics=False)
        self.assertEqual(q["hygiene"], ["1"])
        self.assertEqual(q["italics"], ["0"])

    def test_cli_flags(self):
        parse = lambda *argv: self._parse(list(argv))
        self.assertTrue(parse("x").hygiene)
        self.assertFalse(parse("--no-hygiene", "x").hygiene)
        self.assertTrue(parse("--hygiene", "x").hygiene)   # old scripts keep working

    def _parse(self, argv):
        """Run main()'s own parser on argv and return the namespace."""
        orig = argparse.ArgumentParser.parse_args
        captured = {}

        class Stop(Exception):
            pass

        def spy(parser, *a, **k):
            captured["args"] = orig(parser, argv)
            raise Stop

        with mock.patch.object(argparse.ArgumentParser, "parse_args", spy):
            with self.assertRaises(Stop):
                self.tpc.main()
        return captured["args"]

    def test_report_returned_over_http(self):
        payload = {"sentences": [], "annotations": [], "sections": [],
                   "hygiene": {"kept": 3, "dropped": 2,
                               "dropped_by_reason": {"section": 2}}}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(payload).encode()

        args = argparse.Namespace(cas_root=None, url=self.tpc.DEFAULT_URL)
        report = {}
        with mock.patch.object(self.tpc.urllib.request, "urlopen", lambda *a, **k: Resp()):
            self.tpc._load_annotations({"identifier": "MaizeOA/x/x.tpcas"}, args,
                                       hygiene_report=report)
        self.assertEqual(2, report["dropped"])
        self.assertEqual("3 shown, 2 filtered: 2 references/acknowledgments",
                         self.tpc.format_hygiene_report(report))



class TestStopForms(unittest.TestCase):

    def ann(self, term, ontology="GO", cid="GO:0000001"):
        return {"begin": 0, "end": len(term), "term": term, "ontology": ontology,
                "category": "x (%s)" % cid}

    def run_clean(self, *anns, **kw):
        return hy.clean_annotations(list(anns), [], collapse=False, **kw)

    def test_default_list_drops_with_reason(self):
        kept, dropped = self.run_clean(self.ann("SSR"), self.ann("SSRs"))
        self.assertEqual(kept, [])
        self.assertEqual({d["reason"] for d in dropped}, {"stop_form:SSR"})

    def test_case_sensitive(self):
        kept, _ = self.run_clean(self.ann("ssr"), self.ann("Nac"))
        self.assertEqual(len(kept), 2)

    def test_scoped_to_wrong_sense_ontologies(self):
        kept, dropped = self.run_clean(self.ann("TEM", "ECO", "ECO:0000001"),
                                       self.ann("TEM", "GO"), self.ann("tRNA", "SO", "SO:0000253"))
        self.assertEqual([k["ontology"] for k in kept], ["ECO", "SO"])
        self.assertEqual([d["reason"] for d in dropped], ["stop_form:TEM"])

    def test_sentence_case_of_lowercase_form(self):
        _, dropped = self.run_clean(self.ann("Pathogenesis"))
        self.assertEqual(dropped[0]["reason"], "stop_form:pathogenesis")

    def test_genes_and_correct_senses_untouched(self):
        kept, _ = self.run_clean(self.ann("NAC", "MAIZE_GENES", "tpzm:0001"),
                                 self.ann("SOD"), self.ann("CDK"))
        self.assertEqual(len(kept), 3)

    def test_can_be_disabled(self):
        kept, _ = self.run_clean(self.ann("SSR"), stop_forms={})
        self.assertEqual(len(kept), 1)


if __name__ == "__main__":
    unittest.main()
