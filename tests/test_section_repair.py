"""Tests for casannot.repair_sections(): undoing TdTokenizer's false section headings.

The cases mirror real MaizeOA papers (see the note above repair_sections() in
casannot.py): a wrapped "(see Materials\\nand methods)" cross-reference, a
table column header "Reference", and a "Methods\\nEcol Evol" line inside a
reference list.
"""

import importlib.util
import os
import unittest

_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "textpresso_classifiers")
_spec = importlib.util.spec_from_file_location("casannot", os.path.join(_PKG, "casannot.py"))
casannot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(casannot)

REFS = "\n".join([
    "Alexander DH, Novembre J, Lange K (2009) Fast model-based estimation.",
    "Ali M, Smith J (2020) Something about maize.",
    "Brown AB, Green C (2018) More maize.",
    "Chen X, Li Y (2021) Even more maize.",
])


def sec(begin, end, typ, content):
    return {"begin": begin, "end": end, "type": typ, "content": content}


def types(sections):
    return [(s["type"], s["begin"], s["end"]) for s in sections]


class TestRepairSections(unittest.TestCase):

    def test_mid_sentence_heading_folds_into_previous(self):
        out = casannot.repair_sections([
            sec(1, 100, "beginning of article", "Title"),
            sec(100, 500, "result", "Phenotypic variation ..."),
            sec(500, 900, "materials and methods", "and methods). Cluster 3 includes ..."),
            sec(900, 2000, "discussion", "By affecting light ..."),
            sec(2000, 3000, "materials and methods", "Plant materials\nA subset ..."),
        ])
        self.assertEqual(types(out), [
            ("beginning of article", 1, 100),
            ("result", 100, 900),
            ("discussion", 900, 2000),
            ("materials and methods", 2000, 3000),
        ])

    def test_table_header_reference_before_body_is_dropped(self):
        out = casannot.repair_sections([
            sec(100, 500, "introduction", "Maize grain yields ..."),
            sec(500, 800, "references", "\nPAR\n\nGRMZM2G015804\n\n1\n\nbglu\n\n(Wang et al. 2020)"),
            sec(800, 2000, "result", "Results text ..."),
            sec(2000, 3000, "references", REFS),
        ])
        self.assertEqual(types(out), [
            ("introduction", 100, 800),
            ("result", 800, 2000),
            ("references", 2000, 3000),
        ])

    def test_real_reference_list_before_methods_is_kept(self):
        # Nature-style: references, then a real Methods section.
        out = casannot.repair_sections([
            sec(100, 500, "result", "Results text ..."),
            sec(500, 900, "references", REFS),
            sec(900, 2000, "materials and methods", "No statistical methods were used ..."),
        ])
        self.assertEqual([t for t, _, _ in types(out)], ["result", "references", "materials and methods"])

    def test_heading_inside_reference_list_folds_into_references(self):
        out = casannot.repair_sections([
            sec(100, 500, "discussion", "In the present study ..."),
            sec(500, 900, "references", REFS),
            sec(900, 1300, "materials and methods", "Ecol Evol 12(11):2084.\n" + REFS),
        ])
        self.assertEqual(types(out), [("discussion", 100, 500), ("references", 500, 1300)])

    def test_combined_heading_and_adjacent_duplicates(self):
        # "Results and Discussion" tags one span with both types; a repeated
        # adjacent heading of the same types merges into it.
        out = casannot.repair_sections([
            sec(100, 500, "result", "We found ..."),
            sec(100, 500, "discussion", "We found ..."),
            sec(500, 900, "result", "Further, ..."),
            sec(500, 900, "discussion", "Further, ..."),
        ])
        self.assertEqual(sorted(types(out)), [("discussion", 100, 900), ("result", 100, 900)])

    def test_without_content_nothing_is_judged(self):
        out = casannot.repair_sections([
            {"begin": 100, "end": 500, "type": "introduction"},
            {"begin": 500, "end": 900, "type": "references"},
            {"begin": 900, "end": 2000, "type": "result"},
        ])
        self.assertEqual([t for t, _, _ in types(out)], ["introduction", "references", "result"])

    def test_implicit_reference_span_is_kept(self):
        out = casannot.repair_sections([
            sec(100, 500, "introduction", "Intro ..."),
            sec(500, 900, "references", "(implicit: numbered-list reference block, no heading text found)"),
            sec(900, 2000, "result", "Results ..."),
        ])
        self.assertIn("references", [t for t, _, _ in types(out)])


if __name__ == "__main__":
    unittest.main()
