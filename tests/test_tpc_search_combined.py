"""Tests for tpc_search_combined.py's relationship-type category expansion.

Covers:
  - category_search() forwards relationship_types / ancestor_relationship_types
    as repeated relationship_type / ancestor_relationship_type query params.
  - expand_categories_by_relationship() unions in descendant and/or ancestor
    categories returned by category_search(), without duplicating or
    reordering the original --category values, and is a no-op when neither
    direction is given.
"""

import importlib.util
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")


def _load_module(name, filename):
    path = os.path.join(_BIN, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_search_combined = _load_module("tpc_search_combined", "tpc_search_combined.py")


class TestCategorySearchRelationshipParam(unittest.TestCase):
    def test_relationship_types_added_as_repeated_query_param(self):
        captured = {}

        class FakeResponse:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return b'{"matches": []}'

        def fake_urlopen(endpoint):
            captured["url"] = endpoint
            return FakeResponse()

        with mock.patch.object(_search_combined.urllib.request, "urlopen", fake_urlopen):
            _search_combined.category_search(
                "seed", url="http://host/v1/textpresso/api",
                relationship_types=["is_a", "part_of"])

        qs = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(qs["relationship_type"], ["is_a", "part_of"])

    def test_ancestor_relationship_types_added_as_repeated_query_param(self):
        captured = {}

        class FakeResponse:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return b'{"matches": []}'

        def fake_urlopen(endpoint):
            captured["url"] = endpoint
            return FakeResponse()

        with mock.patch.object(_search_combined.urllib.request, "urlopen", fake_urlopen):
            _search_combined.category_search(
                "hypocotyl", url="http://host/v1/textpresso/api",
                ancestor_relationship_types=["is_a"])

        qs = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(qs["ancestor_relationship_type"], ["is_a"])

    def test_no_relationship_type_param_when_omitted(self):
        captured = {}

        class FakeResponse:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return b'{"matches": []}'

        def fake_urlopen(endpoint):
            captured["url"] = endpoint
            return FakeResponse()

        with mock.patch.object(_search_combined.urllib.request, "urlopen", fake_urlopen):
            _search_combined.category_search("seed", url="http://host/v1/textpresso/api")

        qs = parse_qs(urlparse(captured["url"]).query)
        self.assertNotIn("relationship_type", qs)
        self.assertNotIn("ancestor_relationship_type", qs)


class TestExpandCategoriesByRelationship(unittest.TestCase):
    def test_no_relationship_types_is_a_no_op(self):
        categories = ['seed (PO:0009010)', 'seed coat (PO:0030124)']
        result = _search_combined.expand_categories_by_relationship(categories, None)
        self.assertEqual(result, categories)

    def test_no_relationship_types_in_either_direction_is_a_no_op(self):
        categories = ['seed (PO:0009010)']
        result = _search_combined.expand_categories_by_relationship(
            categories, None, ancestor_relationship_types=None)
        self.assertEqual(result, categories)

    def test_expands_and_dedupes_descendants(self):
        def fake_category_search(term_id, url=None, limit=None, relationship_types=None,
                                  ancestor_relationship_types=None):
            self.assertEqual(term_id, "PO:0009010")
            self.assertEqual(relationship_types, {"is_a"})
            self.assertIsNone(ancestor_relationship_types)
            return [
                {"id": "PO:0009010", "name": "seed", "category": "seed (PO:0009010)",
                 "ontology": "PO", "matched_on": "exact",
                 "relationship_types": ["is_a"], "parent_relationship_types": []},
                {"id": "PO:0020123", "name": "hypocotyl", "category": "hypocotyl (PO:0020123)",
                 "ontology": "PO", "matched_on": "exact+descendant",
                 "relationship_types": [], "parent_relationship_types": ["is_a"]},
            ]

        with mock.patch.object(_search_combined, "category_search", fake_category_search):
            result = _search_combined.expand_categories_by_relationship(
                ["seed (PO:0009010)"], {"is_a"})

        self.assertEqual(result, ["seed (PO:0009010)", "hypocotyl (PO:0020123)"])

    def test_expands_and_dedupes_ancestors(self):
        def fake_category_search(term_id, url=None, limit=None, relationship_types=None,
                                  ancestor_relationship_types=None):
            self.assertEqual(term_id, "PO:0020123")
            self.assertIsNone(relationship_types)
            self.assertEqual(ancestor_relationship_types, {"is_a"})
            return [
                {"id": "PO:0020123", "name": "hypocotyl", "category": "hypocotyl (PO:0020123)",
                 "ontology": "PO", "matched_on": "exact",
                 "relationship_types": [], "parent_relationship_types": ["is_a"]},
                {"id": "PO:0009010", "name": "seed", "category": "seed (PO:0009010)",
                 "ontology": "PO", "matched_on": "exact+ancestor",
                 "relationship_types": ["is_a"], "parent_relationship_types": []},
            ]

        with mock.patch.object(_search_combined, "category_search", fake_category_search):
            result = _search_combined.expand_categories_by_relationship(
                ["hypocotyl (PO:0020123)"], None, ancestor_relationship_types={"is_a"})

        self.assertEqual(result, ["hypocotyl (PO:0020123)", "seed (PO:0009010)"])

    def test_preserves_original_categories_when_lookup_returns_none(self):
        with mock.patch.object(_search_combined, "category_search", return_value=None):
            result = _search_combined.expand_categories_by_relationship(
                ["seed (PO:0009010)"], {"is_a"})

        self.assertEqual(result, ["seed (PO:0009010)"])


class TestUnavailableRelationshipTypeWarning(unittest.TestCase):
    def _fake_category_search_factory(self, exact_record):
        def fake_category_search(term_id, url=None, limit=None, relationship_types=None,
                                  ancestor_relationship_types=None):
            return [exact_record]
        return fake_category_search

    def test_warns_when_requested_child_type_unavailable(self):
        exact = {"id": "PO:0009010", "name": "seed", "category": "seed (PO:0009010)",
                  "ontology": "PO", "matched_on": "exact",
                  "relationship_types": ["part_of"], "parent_relationship_types": []}
        fake = self._fake_category_search_factory(exact)

        with mock.patch.object(_search_combined, "category_search", fake):
            with mock.patch.object(_search_combined.sys, "stderr") as fake_stderr:
                _search_combined.expand_categories_by_relationship(
                    ["seed (PO:0009010)"], {"is_a"})

        warnings = "".join(c.args[0] for c in fake_stderr.write.call_args_list)
        self.assertIn("is_a", warnings)
        self.assertIn("seed (PO:0009010)", warnings)
        self.assertIn("part_of", warnings)  # what's actually available, per the message

    def test_warns_when_requested_ancestor_type_unavailable(self):
        exact = {"id": "PO:0009010", "name": "seed", "category": "seed (PO:0009010)",
                  "ontology": "PO", "matched_on": "exact",
                  "relationship_types": [], "parent_relationship_types": ["is_a"]}
        fake = self._fake_category_search_factory(exact)

        with mock.patch.object(_search_combined, "category_search", fake):
            with mock.patch.object(_search_combined.sys, "stderr") as fake_stderr:
                _search_combined.expand_categories_by_relationship(
                    ["seed (PO:0009010)"], None, ancestor_relationship_types={"part_of"})

        warnings = "".join(c.args[0] for c in fake_stderr.write.call_args_list)
        self.assertIn("part_of", warnings)

    def test_no_warning_when_requested_type_is_available(self):
        exact = {"id": "PO:0009010", "name": "seed", "category": "seed (PO:0009010)",
                  "ontology": "PO", "matched_on": "exact",
                  "relationship_types": ["is_a", "part_of"], "parent_relationship_types": []}
        fake = self._fake_category_search_factory(exact)

        with mock.patch.object(_search_combined, "category_search", fake):
            with mock.patch.object(_search_combined.sys, "stderr") as fake_stderr:
                _search_combined.expand_categories_by_relationship(
                    ["seed (PO:0009010)"], {"is_a"})

        fake_stderr.write.assert_not_called()

    def test_no_warning_when_exact_match_missing_from_results(self):
        # lookup service down / exact term not echoed back -- fail-open, no warning
        with mock.patch.object(_search_combined, "category_search", return_value=[]):
            with mock.patch.object(_search_combined.sys, "stderr") as fake_stderr:
                _search_combined.expand_categories_by_relationship(
                    ["seed (PO:0009010)"], {"is_a"})

        fake_stderr.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
