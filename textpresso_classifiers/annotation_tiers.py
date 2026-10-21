"""Tier gate for ontology match strings (decision 4 of ONTOLOGY_BUILD.md).

Drop this file next to casannot.py, beside annotation_hygiene.py. It reads the
frozen `match_dictionary.csv` that build_match_dictionary.py produces -- tiers
are decided at build time, so this module has no third-party imports and never
touches an OBO file.

    from annotation_tiers import TierIndex, tier_rule
    tiers = TierIndex.from_csv(os.environ["TEXTPRESSO_MATCH_DICTIONARY"])

    kept, dropped = clean_annotations(annotations, sections,
                                      tiers=tiers, tier_rule=tier_rule,
                                      sentence_at=casannot.sentence_at, ...)

The four tiers, as built:

    T0  match freely, case-insensitive                      186,242 strings
    T1  match only when the case matches a stored form        3,640
    T2  match only with supporting context in the sentence      326
    T3  never string-matched; not present in the dictionary      (284 blocked)

A T2 string is not dropped blindly: `verdict()` looks for three kinds of
support in the surrounding sentence, in this order, and records which one
fired. That makes the gate auditable -- a curator sees `tier_T2_context:
longer_form` rather than a silent disappearance.

WHAT THIS DOES NOT FIX: 'Kit' and 'Ltd' are GO RELATED synonyms stored in
exactly the case papers use them in ("Radioiodination Kit", "Elsevier Science
Ltd"), so they are T1 and they pass the case test. They are stop-forms, handled
by the `exclusions=` list in annotation_hygiene.clean_annotations. Tiering and
stop-forms are complementary; neither subsumes the other.
"""

import csv
import re

TIER_ORDER = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}

# Qualifiers that make a common word a term mention: "water deficit",
# "nitrogen stress", "ear length", "kernel number". Deliberately short -- a
# missing qualifier only forces the other two context tests to run.
DEFAULT_QUALIFIERS = (
    "stress", "deficit", "deficiency", "tolerance", "resistance", "response",
    "severity", "incidence", "content", "concentration", "treatment", "length",
    "width", "weight", "number", "yield", "rate", "index", "ratio", "density",
    "area", "depth", "angle", "time", "duration", "quality", "potential",
)

# Per-channel additions. A species name in a maize paper is usually a real
# species mention, and the phenotype qualifiers above never fire on it, so the
# taxon channel gets the idiom comparative work actually uses.
CHANNEL_QUALIFIERS = {
    "PO": ("architecture", "system", "growth", "development", "elongation",
           "biomass", "tip", "hair", "hairs", "zone", "surface", "volume",
           "mass", "diameter", "emergence", "initiation", "primordium"),
    "TO": ("architecture", "growth", "development", "biomass", "emergence",
           "initiation", "trait", "traits", "qtl"),
    "NCBITaxon": ("genome", "genomes", "ortholog", "orthologs", "orthologue",
                  "orthologues", "homolog", "homologs", "homologue", "homologues",
                  "cultivar", "cultivars", "genotype", "genotypes", "accession",
                  "accessions", "species", "subsp", "cv", "line", "lines",
                  "infection", "inoculation", "isolate", "isolates", "pathogen"),
}

# Channels whose qualifier test is sentence-wide rather than windowed: a
# species name and the word that licenses it ("orthologues were identified in
# rice") are routinely far apart, where a phenotype qualifier is adjacent.
SENTENCE_SCOPE_CHANNELS = frozenset({"NCBITaxon"})

# Textpresso ontology labels -> dictionary channels. MAIZE_GENES is absent on
# purpose: gene mentions are gated by annotation_italics, not by tiers.
ONTOLOGY_TO_CHANNEL = {
    "GO": "GO", "PO": "PO", "TO": "TO", "PECO": "PECO", "PSO": "PSO",
    "CO_322": "CO_322", "PATO": "PATO", "SO": "SO", "ECO": "ECO",
    "CHEBI": "ChEBI", "ChEBI": "ChEBI", "NCBITAXON": "NCBITaxon",
    "NCBITaxon": "NCBITaxon",
}


def channel_for(ontology):
    """Map a Textpresso ontology label to a dictionary channel, or None."""
    if not ontology:
        return None
    base = ontology.split(":")[-1].strip()
    return ONTOLOGY_TO_CHANNEL.get(base) or ONTOLOGY_TO_CHANNEL.get(base.upper())


class TierIndex:
    """Frozen tier lookup over match_dictionary.csv."""

    def __init__(self, records, qualifiers=DEFAULT_QUALIFIERS):
        # lowercase string -> list of (stored_string, tier, channel, term_id)
        self._by_form = records
        self._qualifiers = tuple(qualifiers)
        self._by_term = {}
        for recs in records.values():
            for stored, _tier, channel, term_id in recs:
                self._by_term.setdefault((channel, term_id), set()).add(stored)
        self._long_forms = {}
        for low, recs in records.items():
            for stored, tier, channel, _term in recs:
                if tier == "T0" and len(stored) >= 7:
                    self._long_forms.setdefault(channel, set()).add(stored.lower())

    @classmethod
    def from_csv(cls, path, qualifiers=DEFAULT_QUALIFIERS):
        records = {}
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                records.setdefault(row["match_string"].lower(), []).append(
                    (row["match_string"], row["tier"], row["channel"], row["term_id"]))
        return cls(records, qualifiers)

    def __len__(self):
        return sum(len(v) for v in self._by_form.values())

    def tier_of(self, text, channel=None):
        """Strictest tier recorded for this surface form, or None if unknown."""
        recs = self._records(text, channel)
        if not recs:
            return None
        return max((r[1] for r in recs), key=lambda t: TIER_ORDER.get(t, 0))

    def _records(self, text, channel):
        recs = self._by_form.get((text or "").lower(), ())
        if channel:
            recs = [r for r in recs if r[2] == channel]
        return list(recs)

    def verdict(self, text, channel=None, context="", term_id=None):
        """Return (drop, reason). `context` is the sentence the mention sits in.

        An unknown form is never dropped -- the dictionary governs what it
        contains, not what it omits.
        """
        recs = self._records(text, channel)
        if not recs:
            return False, "tier_unknown_form"
        if term_id:
            narrowed = [r for r in recs if r[3] == term_id]
            recs = narrowed or recs

        case_exact = [r for r in recs if r[0] == text]
        pool = case_exact or recs
        tier = min((r[1] for r in pool), key=lambda t: TIER_ORDER.get(t, 0))

        if tier == "T3":
            return True, "tier_T3_blocked"
        if tier == "T1" and not case_exact:
            stored = sorted({r[0] for r in recs})[:3]
            return True, "tier_T1_case_mismatch:" + "/".join(stored)
        if tier == "T2":
            if not context:
                # No sentence was supplied, so the test could not run. Absence
                # of evidence is not evidence of absence: keep and say so.
                return False, "tier_T2_context_unavailable"
            support = self._context_support(text, pool, context)
            if not support:
                return True, "tier_T2_no_context"
            return False, "tier_T2_context:" + support
        return False, "tier_" + tier

    def _context_support(self, text, recs, context):
        """Which kind of support the sentence provides for a T2 mention."""
        if not context:
            return None
        low = context.lower()
        tl = text.lower()

        # 1. the same term has a longer form, and that form is what is written
        for _stored, _tier, channel, term_id in recs:
            for form in self._by_term.get((channel, term_id), ()):
                fl = form.lower()
                if fl != tl and tl in fl and fl in low:
                    return "longer_form"

        # 2. a qualifier sits next to the mention ("water deficit", "rice genome")
        channels = {r[2] for r in recs}
        quals = list(self._qualifiers)
        for ch in channels:
            quals.extend(CHANNEL_QUALIFIERS.get(ch, ()))
        # adjacent, or separated by at most two short words ("rice and sorghum
        # orthologues", "root and shoot biomass")
        gap = r"(?:[\s\-]+(?:\w{1,12}[\s\-]+){0,2})"
        if channels & SENTENCE_SCOPE_CHANNELS:
            for q in quals:
                if re.search(r"\b%s\b" % re.escape(q), low):
                    return "qualifier:" + q
        for q in quals:
            if re.search(r"\b%s%s%s\b" % (re.escape(tl), gap, re.escape(q)), low) or \
               re.search(r"\b%s%s%s\b" % (re.escape(q), gap, re.escape(tl)), low):
                return "qualifier:" + q

        # 3. another unambiguous term from the same channel is in the sentence
        for _stored, _tier, channel, _term in recs:
            for form in self._long_forms.get(channel, ()):
                if form != tl and form in low:
                    return "companion_term"
        return None


def tier_rule(tiers, term, ontology, context="", term_id=None):
    """Injection point for annotation_hygiene.clean_annotations.

    Mirrors annotation_italics.never_italicised: returns (drop, reason) and
    keeps annotation_hygiene import-free.
    """
    if tiers is None:
        return False, ""
    return tiers.verdict(term, channel_for(ontology), context or "", term_id)


if __name__ == "__main__":
    import sys
    from collections import Counter

    path = sys.argv[1] if len(sys.argv) > 1 else "match_dictionary.csv"
    idx = TierIndex.from_csv(path)
    tiers = Counter()
    for recs in idx._by_form.values():
        for _s, t, _c, _i in recs:
            tiers[t] += 1
    print("%s: %d strings, %d distinct forms, tiers %s"
          % (path, len(idx), len(idx._by_form), dict(tiers)))
    demos = [
        ("ABA", "CHEBI", "ABA content increased under drought."),
        ("aba", "CHEBI", "aba content increased under drought."),
        ("water", "CHEBI", "Plants were grown under water deficit."),
        ("water", "CHEBI", "Samples were rinsed in water."),
        ("root", "PO", "Root length was measured at V6."),
        ("rice", "NCBITaxon", "Orthologues were identified in rice and sorghum."),
    ]
    for text, ont, sentence in demos:
        drop, reason = tier_rule(idx, text, ont, sentence)
        print("  %-6s %-10s %-9s %s" % (text, ont, "DROP" if drop else "keep", reason))
