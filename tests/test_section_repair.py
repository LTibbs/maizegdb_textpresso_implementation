"""Tests for casannot's section fixes: repair_sections() (false headings) and
add_letter_spaced_headings() (missed headings).

The cases mirror real MaizeOA papers (see the notes above these functions in
casannot.py):
- 10.1093_plcell_koae007: a wrapped "(see Materials\\nand methods)" cross-reference
  and a table column header "Reference";
- 10.1007_s00122-025-05008-5: "Methods\\nEcol Evol" inside a reference list;
- 10.1111_mpp.13317: a two-column layout where the text after a real heading
  starts lowercase;
- 10.1016_j.cub.2017.09.040: Cell's "STAR+METHODS";
- 10.1002_pei3.10141 / tpg2.20451: letter-spaced headings, and an abstract
  that ran on through a review.
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


class Doc:
    """Builds a text from parts and records each heading's offset."""

    def __init__(self):
        self.text = ""
        self.at = {}

    def add(self, s, mark=None):
        if mark:
            self.at[mark] = len(self.text)
        self.text += s
        return self


def sec(begin, end, typ, content):
    return {"begin": begin, "end": end, "type": typ, "content": content}


def types(sections):
    return [(s["type"], s["begin"], s["end"]) for s in sections]


class TestRepairSections(unittest.TestCase):

    def test_heading_with_prose_before_it_folds_into_previous(self):
        d = (Doc().add("Results\n", "res").add("Lines fell into 6 clusters (Fig. 2; see ")
             .add("Materials\n", "mm1").add("and methods). Cluster 3 ...\n")
             .add("Discussion\n", "dis").add("By affecting light ...\n")
             .add("Materials and methods\n", "mm2").add("Plant materials\n"))
        a, n = d.at, len(d.text)
        out = casannot.repair_sections([
            sec(a["res"], a["mm1"], "result", "Lines fell ..."),
            sec(a["mm1"], a["dis"], "materials and methods", "and methods). Cluster 3 ..."),
            sec(a["dis"], a["mm2"], "discussion", "By affecting ..."),
            sec(a["mm2"], n, "materials and methods", "Plant materials"),
        ], d.text)
        self.assertEqual(types(out), [
            ("result", a["res"], a["dis"]),
            ("discussion", a["dis"], a["mm2"]),
            ("materials and methods", a["mm2"], n),
        ])

    def test_heading_sandwiched_mid_sentence_folds(self):
        # The wrapped word sits on a line of its own, mid-sentence.
        d = (Doc().add("Results\n", "res").add("We grouped the lines (see\n")
             .add("Materials\n", "mm").add("and methods) by canopy.\n"))
        a, n = d.at, len(d.text)
        out = casannot.repair_sections([
            sec(a["res"], a["mm"], "result", "We grouped ..."),
            sec(a["mm"], n, "materials and methods", "and methods) by canopy."),
        ], d.text)
        self.assertEqual(types(out), [("result", a["res"], n)])

    def test_two_column_heading_with_lowercase_text_after_is_kept(self):
        # Real heading after a blank line; the next line is the other column's
        # hyphenated word -- starts lowercase, but it's still a heading.
        d = (Doc().add("Results\n", "res").add("Six independent ex-\n\n")
             .add("ACKNOWLEDGEMENTS\n", "ack").add("pendent experiments were analysed.\n"))
        a, n = d.at, len(d.text)
        out = casannot.repair_sections([
            sec(a["res"], a["ack"], "result", "Six independent ex-"),
            sec(a["ack"], n, "acknowledgments", "pendent experiments were analysed."),
        ], d.text)
        self.assertEqual([t for t, _, _ in types(out)], ["result", "acknowledgments"])

    def test_text_glued_to_heading_word_is_not_prose(self):
        d = Doc().add("Discussion\n", "dis").add("Text.\n").add("STAR+", "line").add("METHODS\n", "mm").add("KEY RESOURCES TABLE\n")
        a, n = d.at, len(d.text)
        out = casannot.repair_sections([
            sec(a["dis"], a["mm"], "discussion", "Text."),
            sec(a["mm"], n, "materials and methods", "KEY RESOURCES TABLE"),
        ], d.text)
        self.assertEqual([t for t, _, _ in types(out)], ["discussion", "materials and methods"])

    def test_numbered_heading_is_kept(self):
        d = Doc().add("Intro text.\n").add("3 | ").add("DISCUSSION\n", "dis").add("We found ...\n")
        a, n = d.at, len(d.text)
        out = casannot.repair_sections([
            sec(0, a["dis"], "introduction", "Intro text."),
            sec(a["dis"], n, "discussion", "We found ..."),
        ], d.text)
        self.assertEqual([t for t, _, _ in types(out)], ["introduction", "discussion"])

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

    def test_without_text_or_content_nothing_is_judged(self):
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

    def test_runaway_abstract_ends_at_next_page(self):
        out = casannot.repair_sections([
            sec(600, 41000, "abstract", "The production of embryogenic callus ..."),
            sec(41000, 41300, "acknowledgments", "This work was supported ..."),
        ], pages=[0, 3800, 7000])
        self.assertEqual(types(out), [("abstract", 600, 3800), ("acknowledgments", 41000, 41300)])

    def test_normal_abstract_is_not_cut(self):
        out = casannot.repair_sections([sec(600, 2300, "abstract", "Short.")], pages=[0, 1500])
        self.assertEqual(types(out), [("abstract", 600, 2300)])


class TestLetterSpacedHeadings(unittest.TestCase):

    def test_inserts_missed_headings_and_splits_the_section_containing_them(self):
        d = (Doc().add("Abstract\n", "abs").add("We studied maize.\n\n")
             .add("I NTRO D U C TI O N\n", "intro").add("Maize is a crop.\n\n")
             .add("M ATE R I A L S A N D M E TH O DS\n", "mm").add("Plants were grown.\n\n")
             .add("AC K N OW L E D G M E N T S\n", "ack").add("We thank everyone.\n\n")
             .add("REFERENCES\n", "ref").add(REFS))
        a, n = d.at, len(d.text)
        raw = [sec(a["abs"], a["ref"], "abstract", "We studied maize."),
               sec(a["ref"], n, "references", REFS)]
        out = casannot.repair_sections(casannot.add_letter_spaced_headings(raw, d.text), d.text)
        self.assertEqual(types(out), [
            ("abstract", a["abs"], a["intro"]),
            ("introduction", a["intro"], a["mm"]),
            ("materials and methods", a["mm"], a["ack"]),
            ("acknowledgments", a["ack"], a["ref"]),
            ("references", a["ref"], n),
        ])

    def test_ignores_letter_spaced_lines_that_are_not_headings(self):
        d = Doc().add("Results\n", "res").add("C O N FL I C T O F I N T E R E S T\nNone.\n")
        raw = [sec(0, len(d.text), "result", "C O N FL I C T ...")]
        self.assertEqual(types(casannot.add_letter_spaced_headings(raw, d.text)), types(raw))


if __name__ == "__main__":
    unittest.main()
