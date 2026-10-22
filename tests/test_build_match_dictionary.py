"""Tests for build_match_dictionary.py.

Synonym cleaning and the OBO writers need no third-party packages. write_outputs()
keeps source subsets and labels graph stubs (2026-10-21): pruned parents are
written as nameless stubs (no match string), which tpso labelled " (GO:...)"
until stubs carried their label and parents. The build() tests (do-not-annotate
terms kept, curated TD abbreviations, 2026-10-22) need wordfreq and are skipped
without it.
"""

import csv
import importlib.util
import json
import os
import tempfile
import types
import unittest
from collections import Counter

_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
_spec = importlib.util.spec_from_file_location("build_match_dictionary",
                                               os.path.join(_BIN, "build_match_dictionary.py"))
bmd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bmd)


def term(name, *synonyms):
    return {"name": name, "synonyms": list(synonyms)}


class TestCleanSynonymText(unittest.TestCase):

    def test_strips_embedded_scope(self):
        self.assertEqual(bmd.clean_synonym_text("ear (related)"), ("ear", None))
        self.assertEqual(bmd.clean_synonym_text("kernel color (EXACT)"), ("kernel color", None))

    def test_reports_language(self):
        self.assertEqual(bmd.clean_synonym_text("oreja de la inflorescencia (Spanish, exact)"),
                         ("oreja de la inflorescencia", "Spanish"))
        self.assertEqual(bmd.clean_synonym_text("ear (English, related)"), ("ear", None))

    def test_leaves_ordinary_parentheses_alone(self):
        for s in ("gibberellin A3", "response to Fusarium (ear rot)", "2,4-D (herbicide)"):
            self.assertEqual(bmd.clean_synonym_text(s), (s, None))


class TestStringsFor(unittest.TestCase):

    def test_cleaned_synonyms_and_drop_kinds(self):
        t = term("ear inflorescence",
                 ("ear (related)", "RELATED"),
                 ("oreja (Spanish, exact)", "EXACT"),
                 ("PNWT (related)", "RELATED"),
                 ("ear infructescence (exact)", "EXACT"))
        self.assertEqual(list(bmd.strings_for(t)), [
            ("ear inflorescence", "label"),
            ("oreja", "non_english"),
            ("ear infructescence", "exact"),
            ("ear", "related"),
            ("PNWT", "code"),
        ])

    def test_unsuffixed_abbreviation_is_kept(self):
        # only abbreviations that came alive through the cleaning are codes
        self.assertEqual(list(bmd.strings_for(term("x", ("ABA", "EXACT"))))[1], ("ABA", "exact"))

    def test_trait_stems_are_opt_in_and_multiword(self):
        t = term("flowering time trait")
        self.assertNotIn(("flowering time", "derived"), list(bmd.strings_for(t)))
        self.assertIn(("flowering time", "derived"), list(bmd.strings_for(t, True)))
        self.assertEqual(list(bmd.strings_for(term("yield trait"), True)),
                         [("yield trait", "label")])



GO = """format-version: 1.2
data-version: releases/test
subsetdef: goslim_plant "Plant GO slim"
subsetdef: gocheck_do_not_annotate "Term not to be used for direct annotation"

[Term]
id: GO:0008150
name: biological_process
subset: gocheck_do_not_annotate
subset: goslim_plant

[Term]
id: GO:0009987
name: cellular process
subset: gocheck_do_not_annotate
is_a: GO:0008150

[Term]
id: GO:0043473
name: pigmentation
subset: goslim_plant
is_a: GO:0009987
"""


class WriteOutputsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "go.obo")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(GO)
        terms = bmd.parse_obo(path)
        pig = next(t for t in terms if t["id"] == "GO:0043473")
        report = {"versions": {"GO": "releases/test"}, "channels": {}, "drops": Counter()}
        args = types.SimpleNamespace(outdir=os.path.join(self.tmp, "out"))
        sources = {"GO": ({t["id"]: t for t in terms}, bmd.obo_subsetdefs(path))}
        bmd.write_outputs(args, [], {"GO": [(pig, [("pigmentation", "label", "T0")])]},
                          report, sources)
        with open(os.path.join(args.outdir, "go_annot.obo"), encoding="utf-8") as fh:
            self.out = fh.read()
        self.stanzas = {s.split("\n", 1)[0]: s for s in
                        (b.split("id: ", 1)[1] for b in self.out.split("[Term]\n")[1:])}

    def test_subsetdefs_declared(self):
        self.assertIn('subsetdef: goslim_plant "Plant GO slim"\n', self.out)
        self.assertIn("subsetdef: tier_T0 ", self.out)

    def test_term_keeps_source_subset(self):
        s = self.stanzas["GO:0043473"]
        self.assertIn("subset: goslim_plant\n", s)
        self.assertIn("subset: tier_T0\n", s)

    def test_stubs_closed_transitively_with_label_subsets_parents(self):
        cp, root = self.stanzas["GO:0009987"], self.stanzas["GO:0008150"]
        self.assertIn("is_a: GO:0008150\n", cp)            # parent's parent is emitted too
        self.assertIn('property_value: stub_label "biological_process" xsd:string', root)
        self.assertIn("subset: goslim_plant\n", root)
        for stub in (cp, root):
            self.assertNotIn("\nname:", "\n" + stub)       # still no match string


class EscapeTest(unittest.TestCase):
    def test_round_trip(self):
        for text in ['8-{(Z)-pent-2-en-1-yl}octanoic acid', 'the "blue" form', 'a\\b!c']:
            self.assertEqual(bmd.obo_unescape(bmd.obo_unquoted(text)), text)
            self.assertEqual(bmd.obo_unescape(bmd.obo_quoted(text)), text)

    def test_parse_resolves_escapes(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "c.obo")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('format-version: 1.2\n\n[Term]\nid: CHEBI:1\nname: 8-\\{(Z)-pent-2-en-1-yl\\}octanoic acid\n'
                     'synonym: "the \\"blue\\" form" EXACT []\n')
        (t,) = bmd.parse_obo(path)
        self.assertEqual(t["name"], "8-{(Z)-pent-2-en-1-yl}octanoic acid")
        self.assertEqual(t["synonyms"], [('the "blue" form', "EXACT")])



GO_BUILD = """format-version: 1.2
data-version: releases/test
subsetdef: gocheck_do_not_annotate "Term not to be used for direct annotation"

[Term]
id: GO:0008150
name: biological_process
subset: gocheck_do_not_annotate

[Term]
id: GO:0040007
name: growth
subset: gocheck_do_not_annotate
is_a: GO:0008150

[Term]
id: GO:0005783
name: endoplasmic reticulum
synonym: "ER" EXACT []

[Term]
id: GO:0019787
name: ubiquitin-like protein transferase activity
synonym: "E3" RELATED []

[Term]
id: GO:0061630
name: ubiquitin protein ligase activity
synonym: "E3" BROAD []
"""

CHEBI_BUILD = """format-version: 1.2
data-version: 256

[Term]
id: CHEBI:24250
name: gibberellin

[Term]
id: CHEBI:1
name: out of scope compound
"""

try:
    import wordfreq  # noqa: F401
except ImportError:
    wordfreq = None


@unittest.skipIf(wordfreq is None, "build() needs wordfreq")
class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp()
        coll = os.path.join(tmp, "collection")
        for _channel, rel, _prefixes in bmd.CHANNELS:
            path = os.path.join(coll, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(GO_BUILD if rel.endswith("go-basic.obo") else "format-version: 1.2\n")
        chebi = os.path.join(tmp, "chebi.obo")
        with open(chebi, "w", encoding="utf-8") as fh:
            fh.write(CHEBI_BUILD)
        args = types.SimpleNamespace(collection=coll, chebi_full=chebi, chebi_skip=False,
                                     derive_trait_stems=False,
                                     outdir=os.path.join(tmp, "out"))
        cls.report = bmd.build(args)
        with open(os.path.join(args.outdir, "match_dictionary.csv"), encoding="utf-8") as fh:
            cls.rows = list(csv.DictReader(fh))
        with open(os.path.join(args.outdir, "go_annot.obo"), encoding="utf-8") as fh:
            cls.go = fh.read()
        with open(os.path.join(args.outdir, "chebi_annot.obo"), encoding="utf-8") as fh:
            cls.chebi = fh.read()

    def strings(self, term_id):
        return {(r["match_string"], r["tier"]) for r in self.rows if r["term_id"] == term_id}

    def test_do_not_annotate_terms_are_matchable(self):
        self.assertIn("growth", {s for s, _t in self.strings("GO:0040007")})
        self.assertNotIn("gocheck_do_not_annotate", self.report["drops"])
        stanza = self.go.split("id: GO:0040007\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("name: growth\n", stanza)
        self.assertIn("subset: gocheck_do_not_annotate\n", stanza)

    def test_source_abbreviation_is_retiered(self):
        (row,) = [r for r in self.rows if r["match_string"] == "ER"]
        self.assertEqual((row["tier"], row["string_kind"]), ("TD", "exact"))
        self.assertEqual(json.loads(row["long_forms"]), [r"\bendoplasmic reticul"])

    def test_curated_abbreviation_only_on_its_term(self):
        self.assertIn(("E3", "TD"), self.strings("GO:0061630"))
        # the RELATED 'E3' on the broader term stays blocked as <= 2 characters
        self.assertNotIn("E3", {s for s, _t in self.strings("GO:0019787")})

    def test_curated_chebi_term_bypasses_scope(self):
        self.assertEqual(self.strings("CHEBI:24250"), {("gibberellin", "T0"), ("GA", "TD")})
        self.assertEqual(self.strings("CHEBI:1"), set())
        (row,) = [r for r in self.rows if r["match_string"] == "GA"]
        self.assertEqual(row["inclusion_reason"], "curated_abbreviation")
        self.assertIn('synonym: "GA" RELATED []', self.chebi)

    def test_missing_curated_entries_are_reported(self):
        missing = self.report["curated_abbreviations_missing"]
        self.assertIn("ChEBI CHEBI:18292 JA", missing)
        self.assertNotIn("ChEBI CHEBI:24250 GA", missing)


if __name__ == "__main__":
    unittest.main()
