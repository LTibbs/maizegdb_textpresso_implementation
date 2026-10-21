"""Shared annotation hygiene for Textpresso GO/PO/TO annotations.

Drop this file next to casannot.py. Every consumer already funnels annotation
parsing through casannot, so one call here reaches all of them:

    cas_annotate_server.py   -> /v1/textpresso/annotate            (public API)
    tpc_search_combined.py   -> _load_annotations()                (CLI)
    textpresso_gui/backend   -> imports the CLI module as `tpc`,
                                calls tpc._load_annotations() and tpc._ca.*   (GUI)

Implements three of the four audited fixes plus the italics rule, all as drops
with a reason code. Every drop is reversible per request: `hygiene=0` returns
the unfiltered set, and the report names what was removed and why, so borderline
mentions stay reachable for a curator.

The italics rule is the strict-zero form with both exemptions (see
annotation_italics.never_italicised and ITALICS_RULE.md). Confidence scoring was
evaluated and not adopted -- it sits within 1.3 points of this rule's precision
at the same retention, for considerably more machinery. The fitted model remains
in maize_gene_context.py if it is ever wanted.

Implements three of the four audited fixes. Measured over 659,163 GO/PO/TO
annotations in MaizeOA (1,423 papers; 300 mentions judged in 5 strata):

  fix 4  drop references/acknowledgments      -149,161 (22.6%); only 32% of
                                               those were judged correct, 33%
                                               were pure boilerplate
  fix 3  collapse to the most specific concept 1.78 -> 1.19 concepts per text
                                               span, -219,046 rows (33%), no
                                               information lost (closure is
                                               recoverable from the OBO and the
                                               search API expands it on request)
  fix 2  case-sensitive abbreviation matching  -15,811 (3.7% of GO); kills
                                               'Kit' (9,180, from
                                               "Radioiodination Kit"), 'Ltd'
                                               (2,085, "Elsevier Science Ltd"),
                                               'Hao' (an author name), while
                                               keeping genuine uppercase usage
                                               (SOD, MAPK, RT)

Fix 1 (excluding the 667 abbreviation-shaped GO RELATED synonyms from the
matching lexicon) is a build-time change; pass a reviewed list as
`exclusions=` to apply it at response time in the meantime.

WHAT THIS CANNOT DO: search results. `search_documents` is served from the
Lucene index, which already contains the rolled-up matches, so hygiene applied
to a response cannot change which documents a --category query returns. Two
options there: (a) verify each hit by checking it still has a surviving
annotation for that category - the same per-hit /annotate check
filter_gene_category_results() already does for gene EXACT/RELATED, at one
round-trip per hit; (b) fix the lexicon and re-index, which is the durable
answer.
"""

import re

DEFAULT_DROP_SECTIONS = frozenset({"references", "acknowledgments"})
_ID_IN_CATEGORY = re.compile(r"\(([A-Za-z]+:\d+)\)\s*$")


def _concept_id(category):
    m = _ID_IN_CATEGORY.search(category or "")
    return m.group(1) if m else None


def _span(a):
    return (a.get("begin"), a.get("end"), a.get("term"))


def is_abbrev_form(text, max_len=5):
    """Abbreviation-shaped: short and carrying an uppercase letter (KIT, LTD, GA)."""
    return bool(text) and len(text) <= max_len and any(c.isupper() for c in text)


class OntologyView:
    """Ancestor lookup over the maps category_index.build_index() already returns.

    Pass the existing index (the sidecar builds one for /category_search), so no
    second OBO parse is needed:

        from category_index import build_index
        view = OntologyView.from_category_index(build_index(casannot._ontology_of))
    """

    def __init__(self, parents_by_id, synonym_forms=None):
        self._parents = parents_by_id or {}
        self._syn = synonym_forms or {}
        self._cache = {}

    @classmethod
    def from_category_index(cls, index):
        parents = {}
        for _rel, mapping in (index.get("parents_by_type") or {}).items():
            for child, plist in mapping.items():
                parents.setdefault(child, set()).update(plist)
        # original-case synonym forms, keyed by lowercase, for the case check.
        # Records in index["synonyms"] carry only the term name, so the stored
        # case of a synonym ('Kit', 'Ltd') is only available from surface_forms.
        if index.get("surface_forms") is not None:
            return cls(parents, {low: set(forms)
                                 for low, forms in index["surface_forms"].items()})
        syn = {}
        for low, recs in (index.get("synonyms") or {}).items():
            for r in recs:
                for s in (r.get("synonyms") or []):
                    if s.lower() == low:
                        syn.setdefault(low, set()).add(s)
                name = r.get("name")
                if name and name.lower() == low:
                    syn.setdefault(low, set()).add(name)
        return cls(parents, syn)

    def ancestors(self, term_id):
        if term_id not in self._cache:
            seen, stack = set(), [term_id]
            while stack:
                for p in self._parents.get(stack.pop(), ()):
                    if p not in seen:
                        seen.add(p)
                        stack.append(p)
            self._cache[term_id] = seen
        return self._cache[term_id]

    def known_forms(self, text):
        """Original-case spellings the ontology actually stores for this form."""
        return self._syn.get((text or "").lower(), set())


#: Ontologies the abbreviation case check never applies to. The maize gene OBO
#: stores registered symbols in lowercase ('hak5', 'tir1', 'emp32'), and papers
#: write the protein in uppercase, so the check would drop real -- often EXACT --
#: gene mentions. Gene annotations are gated by the italics rule instead; the
#: case check was measured on GO/PO/TO only.
CASE_CHECK_EXEMPT_ONTOLOGIES = frozenset({"MAIZE_GENES", "MAIZE_GENES_RELATED"})


def clean_annotations(annotations, sections, *, drop_sections=DEFAULT_DROP_SECTIONS,
                      collapse=True, case_sensitive_abbrev=True, exclusions=(),
                      ontology_view=None, section_types_at=None,
                      italics=None, italic_rule=None, obo_class_of=None,
                      tiers=None, tier_rule=None, sentence_at=None):
    """Return (kept, dropped). Every dropped item carries a 'reason'.

    annotations/sections  -- as returned by casannot.parse_cas_file()
    section_types_at      -- pass casannot.section_types_at (keeps this module
                             import-free); when omitted, sections are ignored
    ontology_view         -- an OntologyView; required for `collapse` and for
                             the case check to know the stored spelling
    exclusions            -- reviewed stop-forms (fix 1), matched case-insensitively
    italics               -- per-paper italic sub-index from
                             annotation_italics.for_paper(index, identifier);
                             omit to skip the typography test entirely
    italic_rule           -- pass annotation_italics.never_italicised (keeps
                             this module import-free, like section_types_at)
    obo_class_of          -- callable(term) -> 'identifier'|'registered'|
                             'alias'|'fragment'|'unknown'; required by the
                             italics rule for its class exemption
    italic_rule           -- pass annotation_italics.non_italicised (keeps this
                             module import-free, like section_types_at). Applies
                             to gene ontologies only and is a NEGATIVE test:
                             low italic rate means "probably not a gene", a high
                             rate proves nothing ('et', 'al' and statistical
                             'p'/'n'/'r' are italicised by convention)

    tiers                 -- an annotation_tiers.TierIndex over
                             match_dictionary.csv; omit to skip tier gating
    tier_rule             -- pass annotation_tiers.tier_rule (keeps this module
                             import-free, like section_types_at). T1 forms must
                             match the stored case, T2 forms need supporting
                             context, T3 forms are not in the dictionary at all.
                             Survivors carry the verdict in a 'tier' field, so
                             'tier_T2_context:qualifier:deficit' is visible to a
                             curator rather than implicit
    sentence_at           -- callable(begin, end) -> sentence text, for the T2
                             context test; without it T2 forms are kept and
                             flagged tier_T2_context_unavailable, never dropped

    Order matters: section and form-level drops run first, so the collapse sees
    only surviving concepts and never promotes a boilerplate match.
    """
    kept, dropped = [], []
    excl = {e.lower() for e in (exclusions or ())}
    drop_sections = {s.lower() for s in (drop_sections or ())}

    stage1 = []
    for a in annotations:
        term = a.get("term") or ""
        if drop_sections and sections and section_types_at:
            types = {t.lower() for t in section_types_at(a.get("begin"), a.get("end"), sections)}
            if types and types <= drop_sections:
                dropped.append(dict(a, reason="section:" + ",".join(sorted(types))))
                continue
        if term.lower() in excl:
            dropped.append(dict(a, reason="excluded_form"))
            continue
        if italics and italic_rule is not None:
            cls = obo_class_of(term) if obo_class_of is not None else "unknown"
            drop_it, n_italic, n_total = italic_rule(
                italics, term, a.get("ontology"), cls)
            if drop_it:
                dropped.append(dict(a, reason="not_italicised:0/%d" % n_total))
                continue
        if tiers is not None and tier_rule is not None:
            context = ""
            if sentence_at is not None:
                context = sentence_at(a.get("begin"), a.get("end")) or ""
            drop_it, reason = tier_rule(tiers, term, a.get("ontology"), context,
                                        _concept_id(a.get("category")))
            if drop_it:
                dropped.append(dict(a, reason=reason))
                continue
            if reason:
                a = dict(a, tier=reason)
        if (case_sensitive_abbrev and ontology_view is not None and is_abbrev_form(term)
                and a.get("ontology") not in CASE_CHECK_EXEMPT_ONTOLOGIES):
            forms = ontology_view.known_forms(term)
            # only when every stored spelling is itself abbreviation-cased
            # ('KIT', 'SER'); a lowercase stored form ('root') means a
            # capitalised text match ('Root') is just sentence case
            if (forms and term not in forms
                    and all(any(c.isupper() for c in f) for f in forms)):
                dropped.append(dict(a, reason="abbrev_case_mismatch:" + "/".join(sorted(forms))))
                continue
        stage1.append(a)

    if not collapse or ontology_view is None:
        return stage1, dropped

    by_span = {}
    for a in stage1:
        by_span.setdefault(_span(a), []).append(a)
    for span, items in by_span.items():
        ids = {_concept_id(i.get("category")) for i in items} - {None}
        redundant = {x for x in ids for y in ids
                     if x != y and x in ontology_view.ancestors(y)}
        for i in items:
            cid = _concept_id(i.get("category"))
            if cid in redundant:
                dropped.append(dict(i, reason="ancestor_of_sibling_on_same_span"))
            else:
                kept.append(i)
    kept.sort(key=lambda a: (a.get("begin") or 0, a.get("category") or ""))
    return kept, dropped


def hygiene_report(kept, dropped):
    """Small summary suitable for an API field or a GUI footer."""
    counts = {}
    for d in dropped:
        counts[(d.get("reason") or "").split(":")[0]] = counts.get((d.get("reason") or "").split(":")[0], 0) + 1
    return {"kept": len(kept), "dropped": len(dropped), "dropped_by_reason": counts}
