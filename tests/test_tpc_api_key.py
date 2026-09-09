"""Tests for --api-key / $TPC_API_KEY plumbing and non-open-access handling.

Covers:
  - search() sends the X-API-Key header only when a key is given.
  - _load_annotations() forwards the key on the /annotate request, returns a
    4-tuple, and reports the server's access_limited flag.
  - _ontology_summary() falls back to the category label when the matched term
    is blank (what the server returns for a limited non-open-access response).
  - the --api-key argument reads its default from $TPC_API_KEY.
"""

import argparse
import importlib.util
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")


def _load_module(name, filename):
    path = os.path.join(_BIN, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = _load_module("tpc_search_combined", "tpc_search_combined.py")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


class SearchApiKeyHeaderTests(unittest.TestCase):
    def _run_search(self, api_key):
        captured = {}

        def fake_urlopen(req):
            captured["req"] = req
            return _FakeResponse([])

        with mock.patch.object(cli.urllib.request, "urlopen", fake_urlopen):
            cli.search({"query": {}}, url="http://host/v1/textpresso/api", api_key=api_key)
        return captured["req"]

    def test_header_present_with_key(self):
        req = self._run_search("secret-key")
        self.assertEqual(req.get_header("X-api-key"), "secret-key")

    def test_header_absent_without_key(self):
        req = self._run_search(None)
        self.assertIsNone(req.get_header("X-api-key"))


class LoadAnnotationsTests(unittest.TestCase):
    def _args(self, api_key=None):
        return SimpleNamespace(cas_root=None, url="http://host/v1/textpresso/api",
                               api_key=api_key)

    def _run(self, payload, api_key=None):
        captured = {}

        def fake_urlopen(req):
            captured["req"] = req
            return _FakeResponse(payload)

        with mock.patch.object(cli.urllib.request, "urlopen", fake_urlopen):
            result = cli._load_annotations({"identifier": "C//a/a.tpcas"}, self._args(api_key))
        return captured["req"], result

    def test_returns_four_tuple_and_forwards_key(self):
        payload = {"sentences": [{"begin": 0, "end": 1, "text": "x"}],
                   "annotations": [], "sections": []}
        req, result = self._run(payload, api_key="k")
        self.assertEqual(len(result), 4)
        self.assertEqual(req.get_header("X-api-key"), "k")
        sentences, annotations, sections, limited = result
        self.assertEqual(sentences, payload["sentences"])
        self.assertFalse(limited)

    def test_reports_access_limited_flag(self):
        payload = {"sentences": [], "annotations": [], "sections": [],
                   "access_limited": True}
        req, result = self._run(payload)
        self.assertIsNone(req.get_header("X-api-key"))
        self.assertTrue(result[3])

    def test_missing_cas_returns_four_tuple(self):
        def boom(req):
            raise cli.urllib.error.HTTPError("u", 404, "nf", {}, None)

        with mock.patch.object(cli.urllib.request, "urlopen", boom):
            result = cli._load_annotations({"identifier": "x"}, self._args())
        self.assertEqual(result, (None, None, None, False))


class OntologySummaryTests(unittest.TestCase):
    def test_prefers_term(self):
        anns = [{"ontology": "GO", "term": "seed development",
                 "category": "seed development (GO:0048316)"}]
        self.assertEqual(cli._ontology_summary(anns), {"GO": ["seed development"]})

    def test_falls_back_to_category_when_term_blank(self):
        anns = [{"ontology": "GO", "term": "",
                 "category": "seed development (GO:0048316)"},
                {"ontology": "PO", "term": "", "category": "seed (PO:0009010)"}]
        self.assertEqual(
            cli._ontology_summary(anns),
            {"GO": ["seed development (GO:0048316)"], "PO": ["seed (PO:0009010)"]})

    def test_skips_entries_with_no_label(self):
        anns = [{"ontology": "GO", "term": "", "category": ""}]
        self.assertEqual(cli._ontology_summary(anns), {})

    def test_strips_related_prefix_from_category_fallback(self):
        anns = [{"ontology": "GO", "term": "",
                 "category": "RELATED:response to water (GO:0009415)"}]
        self.assertEqual(
            cli._ontology_summary(anns), {"GO": ["response to water (GO:0009415)"]})


class ApiKeyArgTests(unittest.TestCase):
    def _parse(self, argv):
        parser = argparse.ArgumentParser()
        cli.add_search_args(parser)
        return parser.parse_args(argv)

    def test_flag_sets_api_key(self):
        args = self._parse(["kw", "--api-key", "flag-key"])
        self.assertEqual(args.api_key, "flag-key")

    def test_env_var_is_default(self):
        with mock.patch.dict(os.environ, {"TPC_API_KEY": "env-key"}):
            # DEFAULT_API_KEY is read at import time; reload picks up the env
            reloaded = _load_module("tpc_search_combined_reload", "tpc_search_combined.py")
        parser = argparse.ArgumentParser()
        reloaded.add_search_args(parser)
        self.assertEqual(parser.parse_args(["kw"]).api_key, "env-key")

    def test_default_is_none_without_env(self):
        args = self._parse(["kw"])
        self.assertIsNone(args.api_key)


class LimitedTextOutputTests(unittest.TestCase):
    def test_access_limited_note_printed(self):
        results = [{"author": "A", "year": "2020", "title": "T", "journal": "J",
                    "accession": "10.1/x", "matched_sentences": ["s1"],
                    "open_access": False, "access_limited": True}]
        args = SimpleNamespace(ontology=None, related_synonyms=False, exclude_type=None,
                               type="sentence", annotate=False, annotate_sentences=False,
                               format="text")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.print_results(results, args)
        self.assertIn("access limited", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
