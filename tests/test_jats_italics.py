"""Tests for bin/jats_italics.py: DOI verification, the no-markup abstention
and the on-disk cache. NCBI is mocked throughout."""

import importlib.util
import os
import shutil
import tempfile
import unittest
from unittest import mock

_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
_spec = importlib.util.spec_from_file_location("jats_italics", os.path.join(_BIN, "jats_italics.py"))
ji = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ji)


def _jats(doi, body):
    return (f'<pmc-articleset><article><front><article-meta>'
            f'<article-id pub-id-type="pmid">1</article-id>'
            f'<article-id pub-id-type="doi">{doi}</article-id>'
            f'</article-meta></front><body>{body}</body></article></pmc-articleset>').encode()


# >= MIN_ITALIC_CHARS of italic text, as any real body with markup has.
ITALIC_BODY = ("<p>The <italic>sh2</italic> and <italic>bt2</italic> mutants of "
               "<italic>Zea mays</italic> and <italic>Arabidopsis thaliana</italic> lack ABA.</p>")
PLAIN_BODY = "<p>The sh2 and bt2 mutants of Zea mays and Arabidopsis thaliana lack ABA.</p>"


class TestArticleDoi(unittest.TestCase):
    def test_reads_own_doi_lowercased(self):
        self.assertEqual("10.1371/journal.pgen.1000733",
                         ji.article_doi(_jats("10.1371/JOURNAL.PGEN.1000733", "")))

    def test_missing(self):
        xml = b"<pmc-articleset><article><front><article-meta/></front></article></pmc-articleset>"
        self.assertIsNone(ji.article_doi(xml))


class TestBuild(unittest.TestCase):
    ACC = "10.1371_journal.pgen.1000733"

    def _build(self, ids, xml_by_id, **kw):
        with mock.patch.object(ji, "doi_to_pmcids", lambda doi, email=None: ids), \
             mock.patch.object(ji, "fetch_jats", lambda pmcid, email=None: xml_by_id[pmcid]):
            return ji.build([self.ACC], verbose=False, sleep=0, **kw)

    def test_citing_paper_skipped_for_matching_candidate(self):
        # esearch's first hit is a 2023 paper that cites this DOI.
        out, misses = self._build(["10090103", "2770123"], {
            "10090103": _jats("10.1186/s12870-023-0001-x", ITALIC_BODY),
            "2770123": _jats("10.1371/journal.pgen.1000733", ITALIC_BODY),
        })
        self.assertEqual([], misses)
        self.assertEqual([1, 1], out[self.ACC]["sh2"])

    def test_doi_with_several_slashes_and_colon(self):
        # Accessions flatten every '/' and ':' to '_', so the DOI check must too.
        for acc, doi in (("10.1093_aob_mcac132", "10.1093/aob/mcac132"),
                         ("10.1042_0264-6021_3540037", "10.1042/0264-6021:3540037")):
            with mock.patch.object(ji, "doi_to_pmcids", lambda d, email=None: ["1"]), \
                 mock.patch.object(ji, "fetch_jats", lambda p, email=None: _jats(doi, ITALIC_BODY)):
                out, misses = ji.build([acc], verbose=False, sleep=0)
            self.assertEqual([], misses, acc)

    def test_no_matching_candidate_is_a_miss(self):
        out, misses = self._build(["10090103"], {
            "10090103": _jats("10.1186/s12870-023-0001-x", ITALIC_BODY)})
        self.assertEqual({}, out)
        self.assertIn("citing papers", misses[0][1])

    def test_body_without_italic_markup_abstains(self):
        out, misses = self._build(["2770123"], {
            "2770123": _jats("10.1371/journal.pgen.1000733", PLAIN_BODY)})
        self.assertEqual({}, out)
        self.assertEqual("no italic markup in JATS body", misses[0][1])

    def test_cache_avoids_refetch(self):
        cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, cache)
        xml = {"2770123": _jats("10.1371/journal.pgen.1000733", ITALIC_BODY)}
        first, _ = self._build(["2770123"], xml, cache_dir=cache)

        def boom(*a, **k):
            raise AssertionError("NCBI called despite cache")
        with mock.patch.object(ji, "doi_to_pmcids", boom), \
             mock.patch.object(ji, "fetch_jats", boom):
            second, misses = ji.build([self.ACC], verbose=False, sleep=0, cache_dir=cache)
        self.assertEqual([], misses)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
