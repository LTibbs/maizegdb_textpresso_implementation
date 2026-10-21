"""write_outputs() keeps source subsets and labels graph stubs (2026-10-21).

tpso restricts GO categories to the goslim_* subsets named in ontology.conf;
an *_annot.obo without them collapses every GO category to the root. Pruned
parents are written as nameless stubs (no match string), which tpso labelled
" (GO:...)" until stubs carried their label and parents.
"""
import os
import sys
import tempfile
import types
import unittest
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import build_match_dictionary as bmd  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
