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

    def test_reference_list_cut_short_by_a_false_heading_is_kept(self):
        # A wrapped journal name ("Methods\nEcol Evol ...") two entries into
        # the list ends the raw segment after the References heading, so its
        # content holds too few entries; a real Methods section after the
        # references then made it look like a table header before body text.
        # Judged on the text after the heading (following_text), it's a list.
        refs = ("Ahrens T, Smith J (2019) Maize roots. Plant J 1:2.\n"
                "Brown AB, Green C (2018) Leaf traits in\n")
        d = (Doc().add("Results\n", "res").add("Yield rose.\n\n").add("References\n", "refs").add(refs)
             .add("Methods\n", "fake").add("Ecol Evol 12(11):2084-2090.\n" + REFS + "\n\n")
             .add("Methods\n", "mm").add("Plants were grown.\n"))
        a, n = d.at, len(d.text)
        raw = [sec(a["res"], a["refs"], "result", "Yield rose.\n\n"),
               sec(a["refs"], a["fake"], "references", refs),
               sec(a["fake"], a["mm"], "materials and methods", "Ecol Evol 12(11):2084-2090.\n"),
               sec(a["mm"], n, "materials and methods", "Plants were grown.\n")]
        self.assertFalse(casannot.looks_like_reference_list(refs))
        self.assertEqual(types(casannot.repair_sections(raw, d.text)), [
            ("result", a["res"], a["refs"]),
            ("references", a["refs"], a["mm"]),
            ("materials and methods", a["mm"], n),
        ])

    def test_table_header_before_the_real_references_heading_is_dropped(self):
        # 10.1534_g3.112.005207: a "Reference" column header ends Table 5
        # just before "LITERATURE CITED"; the list after that is the later
        # heading's, so the table caption stays out of the references.
        d = (Doc().add("Results\n", "res").add("QTL were found.\n(Wu et al. 2008)\n\n")
             .add("Reference\n", "hdr").add("\nTable 5 QTL number and candidate genes\n\n")
             .add("LITERATURE CITED\n", "refs").add(REFS + "\n"))
        a, n = d.at, len(d.text)
        raw = [sec(a["res"], a["hdr"], "result", "QTL were found."),
               sec(a["hdr"], a["refs"], "references", "\nTable 5 QTL number and candidate genes\n\n"),
               sec(a["refs"], n, "references", REFS)]
        self.assertEqual(casannot.following_text(d.text, raw[1]),
                         "\nTable 5 QTL number and candidate genes\n\n")
        self.assertEqual(types(casannot.repair_sections(raw, d.text)), [
            ("result", a["res"], a["refs"]),
            ("references", a["refs"], n),
        ])

    def test_references_heading_line_needs_the_whole_line(self):
        for line in ("References", "  LITERATURE CITED ", "R e f e r e n c e s",
                     "<_pdf _fsc=+10/>REFERENCES<_pdf _cr/>"):
            self.assertTrue(casannot._is_references_heading_line(line), line)
        for line in ("", "References 1-20", "See References", "Reference list:"):
            self.assertFalse(casannot._is_references_heading_line(line), line)

    def test_following_text_is_the_window_after_the_heading_line(self):
        text = "Intro.\nReferences\n" + "x" * 5000
        s = sec(7, len(text), "references", "stale content")
        self.assertEqual(casannot.following_text(text, s), "x" * 3000)
        self.assertEqual(casannot.following_text(None, s), "stale content")
        implicit = sec(7, len(text), "references", "(implicit: numbered-list reference block)")
        self.assertEqual(casannot.following_text(text, implicit), implicit["content"])

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


class TestSplitSupplementary(unittest.TestCase):
    """10.1186_s12864-025-11761-0: a supplement appended after the references."""

    BANNER = ("SUPPLEMENTARY MATERIAL\n12864_2025_11761_MOESM1_ESM.docx\n"
              "supplementary document, converted to PDF\n\n"
              "inserted by the MaizeGDB literature pipeline\n\n")

    def test_supplement_after_references_is_relabelled(self):
        body = "Results\nZmCHS1 was knocked out.\n"
        refs = "References\n" + REFS + "\n\n"
        supp = "Supplementary Figure 1 Mutation types of CHS gene knockout\n"
        text = body + refs + self.BANNER + supp
        cut = len(body) + len(refs)
        sections = [
            {"begin": 0, "end": len(text), "type": "beginning of article"},
            {"begin": 0, "end": len(body), "type": "result"},
            {"begin": len(body), "end": len(text), "type": "references"},
        ]
        out = casannot.split_supplementary(sections, text)
        self.assertEqual(types(out), [("result", 0, len(body)),
                                      ("beginning of article", 0, len(text)),
                                      ("references", len(body), cut),
                                      ("supplementary", cut, len(text))])
        self.assertEqual(casannot.section_types_at(len(text) - 5, len(text) - 1, out),
                         {"beginning of article", "supplementary"})

    def test_supplement_headings_are_folded_in(self):
        text = "References\n" + REFS + "\n" + self.BANNER + "Methods\nmore\nReferences\nX\n"
        cut = text.index("SUPPLEMENTARY")
        sections = [{"begin": 0, "end": text.index("Methods"), "type": "references"},
                    {"begin": text.index("Methods"), "end": len(text), "type": "materials and methods"}]
        out = casannot.split_supplementary(sections, text)
        self.assertEqual(types(out), [("references", 0, cut), ("supplementary", cut, len(text))])

    def test_no_banner_is_unchanged(self):
        sections = [{"begin": 0, "end": 10, "type": "references"}]
        self.assertIs(casannot.split_supplementary(sections, "Supplementary Table S1 ..."), sections)


class TestFixSections(unittest.TestCase):
    """fix_sections() judges the article's headings without the supplement's,
    as TdTokenizer does from 2026-10-22."""

    BANNER = TestSplitSupplementary.BANNER

    def paper(self):
        # The article's "References" isn't followed by a recognisable list
        # (here, a "see ..." line); a "Methods" heading in the appended
        # supplement comes after it.
        d = (Doc().add("Introduction\n", "intro").add("Maize is a crop.\n\n")
             .add("References\n", "refs").add("See the online version for the list.\n\n")
             .add(self.BANNER, "cut").add("Methods\n", "smm").add("Plants were grown.\n"))
        return d, d.at, len(d.text)

    def test_supplement_body_heading_does_not_drop_article_references(self):
        d, a, n = self.paper()
        old_cas2 = [  # tokenized before 2026-10-22: supplement headings are sections
            sec(a["intro"], a["refs"], "introduction", "Maize is a crop.\n\n"),
            sec(a["refs"], a["smm"], "references", "See the online version for the list.\n\n"),
            sec(a["smm"], n, "materials and methods", "Plants were grown.\n"),
        ]
        # Judged on the whole text, the supplement's Methods counts as body
        # text after the references, so they'd fold into the introduction.
        whole = casannot.split_supplementary(casannot.repair_sections(old_cas2, d.text), d.text)
        self.assertEqual([t for t, _, _ in types(whole)], ["introduction", "supplementary"])
        self.assertEqual(types(casannot.fix_sections(old_cas2, d.text)), [
            ("introduction", a["intro"], a["refs"]),
            ("references", a["refs"], a["cut"]),
            ("supplementary", a["cut"], n),
        ])

    def test_old_and_new_cas2_give_the_same_sections(self):
        d, a, n = self.paper()
        old_cas2 = [
            sec(a["intro"], a["refs"], "introduction", "Maize is a crop.\n\n"),
            sec(a["refs"], a["smm"], "references", "See the online version for the list.\n\n"),
            sec(a["smm"], n, "materials and methods", "Plants were grown.\n"),
        ]
        new_cas2 = [  # what the 2026-10-22 TdTokenizer writes
            sec(a["intro"], a["refs"], "introduction", "Maize is a crop.\n\n"),
            sec(a["refs"], a["cut"], "references", "See the online version for the list.\n\n"),
            sec(a["cut"], n, "supplementary", d.text[a["cut"]:]),
        ]
        self.assertEqual(types(casannot.fix_sections(new_cas2, d.text)),
                         types(casannot.fix_sections(old_cas2, d.text)))

    def test_letter_spaced_supplement_heading_is_ignored(self):
        d = (Doc().add("Results\n", "res").add("Yield rose.\n\n").add("References\n", "refs")
             .add(REFS + "\n\n").add(self.BANNER, "cut").add("A C K N O W L E D G M E N T S\nThanks.\n"))
        n = len(d.text)
        raw = [sec(d.at["res"], d.at["refs"], "result", "Yield rose.\n\n"),
               sec(d.at["refs"], n, "references", REFS)]
        self.assertEqual(types(casannot.fix_sections(raw, d.text)), [
            ("result", d.at["res"], d.at["refs"]),
            ("references", d.at["refs"], d.at["cut"]),
            ("supplementary", d.at["cut"], n),
        ])


if __name__ == "__main__":
    unittest.main()
