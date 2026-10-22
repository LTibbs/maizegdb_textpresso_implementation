"""Tier gate for ontology match strings (decision 4 of ONTOLOGY_BUILD.md).

Drop this file next to casannot.py, beside annotation_hygiene.py. It reads the
frozen `match_dictionary.csv` that build_match_dictionary.py produces -- tiers
are decided at build time, so this module has no third-party imports and never
touches an OBO file.

    from annotation_tiers import TierIndex, tier_rule
    tiers = TierIndex.from_csv(os.environ["TEXTPRESSO_MATCH_DICTIONARY"])

    kept, dropped = clean_annotations(annotations, sections,
                                      tiers=tiers, tier_rule=tier_rule,
                                      sentence_at=casannot.sentence_lookup(sentences),
                                      ...)

The four tiers, as built:

    T0  match freely, case-insensitive                      186,242 strings
    T1  match only when the case matches a stored form        3,640
    T2  match only with supporting context in the sentence      326
    TD  curated abbreviation; kept only when the paper body
        spells out the term (GA, JA, SA, BR, CK, ET, ER, E3)      8
    T3  never string-matched; not present in the dictionary      (284 blocked)

A T2 string is not dropped blindly: `verdict()` looks for three kinds of
support in the surrounding sentence, in this order, and records which one
fired. That makes the gate auditable -- a curator sees `tier_T2_context:
longer_form` rather than a silent disappearance.

Case: a lowercase stored form also accepts its sentence-case spelling
('Chromosome', 'Pollen'); a stored form that starts uppercase ('KIT', 'LTD',
'ABA') needs its exact case, which is what drops 'Kit' ("Radioiodination
Kit") and 'Ltd' ("Elsevier Science Ltd"). Inflection: a plural the matcher
produced ('roots') is judged as its dictionary singular ('root').

TD is a paper-level test, not a sentence-level one: an abbreviation is defined
once and used bare for pages, so the sentence rarely holds the long form. The
long-form patterns come from the dictionary's `long_forms` column; the body is
the paper's sentences outside references and acknowledgments
(casannot.body_text), so author initials in a cited title do not count. A TD
form also needs its stored case, like T1.

WHAT THIS DOES NOT FIX: a homonym stored in exactly the case papers use for
the other sense. Those are stop-forms, handled by the `exclusions=` list in
annotation_hygiene.clean_annotations. Tiering and stop-forms are
complementary; neither subsumes the other.
"""

import csv
import gzip
import json
import re

TIER_ORDER = {"T0": 0, "T1": 1, "T2": 2, "TD": 3, "T3": 4}

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


# Plural endings tried, in order, when a surface form is not in the
# dictionary: (suffix, replacement). Plain 's' before 'es', so 'pores' finds
# 'pore' rather than the unrelated 'POR'; 'gases' still reaches 'gas' because
# 'gase' is not a form.
INFLECTIONS = (("ies", "y"), ("ves", "f"), ("ves", "fe"), ("s", ""), ("es", ""))


def channel_for(ontology):
    """Map a Textpresso ontology label to a dictionary channel, or None."""
    if not ontology:
        return None
    base = ontology.split(":")[-1].strip()
    return ONTOLOGY_TO_CHANNEL.get(base) or ONTOLOGY_TO_CHANNEL.get(base.upper())


class TierIndex:
    """Frozen tier lookup over match_dictionary.csv."""

    def __init__(self, records, qualifiers=DEFAULT_QUALIFIERS, long_form_patterns=None):
        # lowercase string -> list of (stored_string, tier, channel, term_id)
        self._by_form = records
        # (stored_string, channel, term_id) -> compiled long-form patterns (TD)
        self._defined = {k: [re.compile(p, re.I) for p in v]
                         for k, v in (long_form_patterns or {}).items()}
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
        """Read match_dictionary.csv, or the .csv.gz the build ships."""
        records, long_forms = {}, {}
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                records.setdefault(row["match_string"].lower(), []).append(
                    (row["match_string"], row["tier"], row["channel"], row["term_id"]))
                if row.get("long_forms"):
                    long_forms[(row["match_string"], row["channel"], row["term_id"])] = \
                        json.loads(row["long_forms"])
        return cls(records, qualifiers, long_forms)

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

    def _resolve(self, text, channel):
        """-> (form, records). Textpresso's matcher inflects ('roots' matches
        'root'), the dictionary does not, so a plural that is not itself a
        dictionary form is looked up by its singular. Without this every
        plural bypassed the gate as an unknown form -- 182k of 349k
        unknown-form rows over MaizeOpenAccess (2026-10-21)."""
        recs = self._records(text, channel)
        if recs or not text:
            return text, recs
        for suffix, repl in INFLECTIONS:
            if text.lower().endswith(suffix) and len(text) > len(suffix) + 2:
                base = text[:-len(suffix)] + repl
                recs = self._records(base, channel)
                if recs:
                    return base, recs
        return text, []

    def verdict(self, text, channel=None, context="", term_id=None, body=None):
        """Return (drop, reason). `context` is the sentence the mention sits in;
        `body` the paper text outside references and acknowledgments, for TD.

        An unknown form is never dropped -- the dictionary governs what it
        contains, not what it omits.
        """
        form, recs = self._resolve(text, channel)
        if not recs:
            return False, "tier_unknown_form"
        if term_id:
            narrowed = [r for r in recs if r[3] == term_id]
            recs = narrowed or recs

        # A lowercase stored form also matches its sentence-case spelling
        # ('Chromosome' at the start of a sentence, 'Pollen' in a heading), as
        # annotation_hygiene's abbreviation check already allows; without it
        # T1 dropped sentence-initial ordinary words. Stored forms that start
        # uppercase (ABA, IAA) still need their exact case.
        case_exact = [r for r in recs
                      if r[0] == form or (r[0][:1].islower()
                                          and r[0][:1].upper() + r[0][1:] == form)]
        pool = case_exact or recs
        tier = min((r[1] for r in pool), key=lambda t: TIER_ORDER.get(t, 0))

        if tier == "T3":
            return True, "tier_T3_blocked"
        if tier == "TD":
            if not case_exact:
                stored = sorted({r[0] for r in recs})[:3]
                return True, "tier_TD_case_mismatch:" + "/".join(stored)
            if body is None:
                # as for T2: the test could not run, so keep and say so
                return False, "tier_TD_body_unavailable"
            for stored, _tier, ch, tid in pool:
                for pat in self._defined.get((stored, ch, tid), ()):
                    if pat.search(body):
                        return False, "tier_TD_defined:" + pat.pattern
            return True, "tier_TD_undefined"
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


def tier_rule(tiers, term, ontology, context="", term_id=None, body=None):
    """Injection point for annotation_hygiene.clean_annotations.

    Mirrors annotation_italics.never_italicised: returns (drop, reason) and
    keeps annotation_hygiene import-free.
    """
    if tiers is None:
        return False, ""
    channel = channel_for(ontology)
    if channel is None:
        # Not a dictionary channel (MAIZE_GENES*: gated by the italics rule).
        # verdict(channel=None) would search every channel, so a gene symbol
        # could be judged -- and dropped -- against a GO or ChEBI string.
        return False, ""
    return tiers.verdict(term, channel, context or "", term_id, body)


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
