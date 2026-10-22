"""Tests for the runtime tier gate (annotation_tiers.TierIndex.verdict).

Covers the two fixes from the 2026-10-21 corpus run: sentence-case spellings
of lowercase T1 forms are kept ('Chromosome'), and plurals the matcher
produced are judged as their dictionary singular ('roots' -> 'root') rather
than bypassing the gate as unknown forms. Also: uppercase-stored T1 forms
still need their exact case ('Kit' vs 'KIT'). And the TD tier (2026-10-22):
a curated abbreviation is kept only when the paper body spells it out.
"""

import csv
import gzip
import importlib.util
import json
import os
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "annotation_tiers", os.path.join(_ROOT, "textpresso_classifiers", "annotation_tiers.py"))
ti = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ti)


def index(*rows):
    recs = {}
    for text, tier, channel, term_id in rows:
        recs.setdefault(text.lower(), []).append((text, tier, channel, term_id))
    return ti.TierIndex(recs)


IDX = index(
    ("chromosome", "T1", "GO", "GO:0005694"),
    ("KIT", "T1", "GO", "GO:0005020"),
    ("ABA", "T1", "ChEBI", "CHEBI:2365"),
    ("seedling", "T1", "PO", "PO:0008037"),
    ("water", "T2", "ChEBI", "CHEBI:15377"),
    ("water deficit", "T0", "PECO", "PECO:0007331"),
    ("root", "T2", "NCBITaxon", "NCBITaxon:1"),
    ("root", "T0", "PO", "PO:0009005"),
    ("pore", "T2", "GO", "GO:0046930"),
    ("POR", "T1", "GO", "GO:0047023"),
    ("GAS", "T1", "GO", "GO:0034005"),
)


class TestCase(unittest.TestCase):

    def test_sentence_case_of_lowercase_form_is_kept(self):
        self.assertEqual(IDX.verdict("Chromosome", "GO"), (False, "tier_T1"))

    def test_uppercase_stored_form_needs_exact_case(self):
        self.assertTrue(IDX.verdict("Kit", "GO")[0])
        self.assertEqual(IDX.verdict("KIT", "GO"), (False, "tier_T1"))
        self.assertTrue(IDX.verdict("Aba", "ChEBI")[0])

    def test_other_case_variants_still_fail(self):
        self.assertTrue(IDX.verdict("CHROMOSOME", "GO")[0])


class TestInflection(unittest.TestCase):

    def test_plural_resolves_to_singular(self):
        self.assertEqual(IDX.verdict("roots", "PO"), (False, "tier_T0"))
        self.assertEqual(IDX.verdict("Seedlings", "PO"), (False, "tier_T1"))

    def test_plural_inherits_t2_context_test(self):
        self.assertEqual(IDX.verdict("roots", "NCBITaxon", "The roots were washed."),
                         (True, "tier_T2_no_context"))

    def test_plain_s_is_tried_before_es(self):
        self.assertEqual(IDX.verdict("pores", "GO", "Nuclear pores were counted."),
                         (True, "tier_T2_no_context"))   # judged as 'pore', not 'POR'
        self.assertEqual(IDX.verdict("gases", "GO")[1], "tier_T1_case_mismatch:GAS")

    def test_unknown_plural_stays_unknown(self):
        self.assertEqual(IDX.verdict("widgets", "PO"), (False, "tier_unknown_form"))

    def test_channel_separates_po_from_taxon(self):
        self.assertEqual(IDX.verdict("root", "PO", "Root length was measured."), (False, "tier_T0"))


class TestTierRule(unittest.TestCase):

    def test_gene_ontologies_are_never_judged(self):
        # 'Kit' would fail GO's T1 'KIT'; as a gene mention it must not be judged
        self.assertEqual(ti.tier_rule(IDX, "Kit", "MAIZE_GENES_RELATED"), (False, ""))
        self.assertTrue(ti.tier_rule(IDX, "Kit", "GO")[0])



TD = ti.TierIndex(
    {"ga": [("GA", "TD", "ChEBI", "CHEBI:24250")],
     "et": [("ET", "TD", "ChEBI", "CHEBI:18153")]},
    long_form_patterns={("GA", "ChEBI", "CHEBI:24250"): [r"\bgibberell"],
                        ("ET", "ChEBI", "CHEBI:18153"): [r"\bethylene\b", r"\bethene\b"]})


class TestDefinedAbbreviation(unittest.TestCase):

    def test_kept_when_body_has_long_form(self):
        drop, reason = TD.verdict("GA", "ChEBI", body="Gibberellins (GA) promote growth.")
        self.assertFalse(drop)
        self.assertTrue(reason.startswith("tier_TD_defined:"))

    def test_dropped_when_body_lacks_long_form(self):
        self.assertEqual(TD.verdict("GA", "ChEBI", body="Smith GA, Jones JA."),
                         (True, "tier_TD_undefined"))

    def test_word_boundary_rejects_polyethylene_glycol(self):
        self.assertTrue(TD.verdict("ET", "ChEBI", body="20% polyethylene glycol (PEG)")[0])
        self.assertFalse(TD.verdict("ET", "ChEBI", body="ethylene (ET) signalling")[0])

    def test_needs_stored_case(self):
        self.assertEqual(TD.verdict("Ga", "ChEBI", body="gibberellin")[1],
                         "tier_TD_case_mismatch:GA")

    def test_no_body_keeps_and_says_so(self):
        self.assertEqual(TD.verdict("GA", "ChEBI"), (False, "tier_TD_body_unavailable"))

    def test_tier_rule_passes_body(self):
        self.assertTrue(ti.tier_rule(TD, "GA", "CHEBI", "", None, body="no hormone")[0])

    def test_long_forms_read_from_csv(self):
        path = os.path.join(tempfile.mkdtemp(), "md.csv.gz")
        with gzip.open(path, "wt", newline="") as fh:
            w = csv.DictWriter(fh, ["match_string", "tier", "channel", "term_id", "long_forms"])
            w.writeheader()
            w.writerow({"match_string": "GA", "tier": "TD", "channel": "ChEBI",
                        "term_id": "CHEBI:24250", "long_forms": json.dumps([r"\bgibberell"])})
            w.writerow({"match_string": "ABA", "tier": "T1", "channel": "ChEBI",
                        "term_id": "CHEBI:2365", "long_forms": ""})
        idx = ti.TierIndex.from_csv(path)
        self.assertFalse(idx.verdict("GA", "ChEBI", body="gibberellic acid")[0])
        self.assertTrue(idx.verdict("GA", "ChEBI", body="abscisic acid")[0])


if __name__ == "__main__":
    unittest.main()
