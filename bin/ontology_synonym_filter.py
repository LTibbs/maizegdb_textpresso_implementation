#!/usr/bin/env python3
"""Reconstruction of the "cheap first filter" heuristic described in
docs/Laura_work_updates_log.md (2026-07-14 entry, "v2: reworked to positively
detect common English/molecular-biology words"). The original script that
produced docs/synonym_audit_maizetest100_maizeoa_20260714_filtered.csv was
never saved to either repo -- only its output and a prose description of its
logic survived. This is a best-effort rebuild from that description, used
here to extend the same classification to rows the original run skipped
(source_fields with no locus_synonym-sourced entry, e.g. classical
single-locus symbols like c1/b1/r1 that only carry a `locus`/gene-symbol
source), rather than to redo the original 296 already-classified rows.

Per term/row: filtered=False immediately if any observed surface form
contains a digit, or a lowercase-to-uppercase transition (species-prefix +
symbol pattern, e.g. "ZmUBI") -- both treated as hard "real gene ID" signals.
Otherwise, split each surface form into tokens on whitespace/hyphen/slash/
semicolon and classify each token as generic if it's a stopword, a short
molecular-biology acronym, a domain noun, a manually-confirmed ambiguous
abbreviation (MANUALLY_CONFIRMED_AMBIGUOUS -- grows only by explicit human
review, e.g. "SAM"/"EH"/"SRA"/"AI"/"BAM"/"POD"), ends in "-ase" and is >=5
chars, or is >=5 chars and found in /usr/share/dict/words. filtered=True only
if every token in every surface form is generic.

NOTE ON FIDELITY: the exact membership of the STOPWORDS/SHORT_BIO_ACRONYMS/
DOMAIN_NOUNS lists below is reconstructed from the log's examples, not
recovered from the original source -- only presumed complete enough to cover
the specific tokens in the "out of filter scope" rows this script targets.
Do not assume it reproduces the original algorithm's judgment on terms
outside that set without spot-checking.
"""
import argparse
import csv
import re

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "for", "and", "or", "to", "is",
    "are", "with", "by", "from", "as", "this", "that", "these", "those",
    "its", "it", "was", "were", "been", "be", "has", "have", "had",
    "gene", "genes", "protein", "proteins", "factor", "factors",
}

SHORT_BIO_ACRONYMS = {
    "rna", "mrna", "trna", "rrna", "dna", "cdna", "atp", "adp", "amp", "gtp",
    "gdp", "gmp", "utr", "orf", "pcr", "snp", "nad", "fad", "coa", "camp",
    "cgmp", "kb", "bp", "ph", "uv",
}

SHORT_COMMON_WORDS = {"like", "rich", "late", "red", "zinc"}

DOMAIN_NOUNS = {
    "calmodulin", "thioredoxin", "glutaredoxin", "ubiquitin", "chorismate",
    "expansin", "pentatricopeptide",
}

# Manually confirmed by domain-expert review (2026-08-07): short abbreviations
# with a well-known, common meaning unrelated to the gene they're mapped to as
# a synonym. Documented per-entry since (unlike the automatic categories
# above) there's no general rule generating this list -- it only grows by
# manual confirmation.
MANUALLY_CONFIRMED_AMBIGUOUS = {
    "eh":  "ear height (maize phenotyping trait)",
    "sam": "shoot apical meristem",
    "sra": "NCBI Sequence Read Archive",
    "ai":  "artificial intelligence",
    "bam": "BAM sequence alignment file format",
    "pod": "seed pod",
}

# USPS two-letter state codes + DC. A term that's just a state abbreviation
# (e.g. "CA", "CO", "KS", "PA") is exactly the kind of high-false-positive
# generic match this filter targets, even though it's unrelated in kind to
# the dictionary-word/stopword/acronym checks above.
STATE_ABBREVIATIONS = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}

DICT_PATH = "/usr/share/dict/words"
TOKEN_SPLIT_RE = re.compile(r"[\s/;-]+")
DIGIT_RE = re.compile(r"\d")
# Lowercase-to-uppercase transition within a token, e.g. "ZmUBI", "ZmActin",
# "ZmCCT" -- the species-prefix + gene-symbol capitalization pattern typical
# of real maize gene names, treated as a "real gene ID" signal on the same
# footing as digit presence.
CASE_TRANSITION_RE = re.compile(r"[a-z][A-Z]")


def load_dict_words():
    with open(DICT_PATH) as f:
        return {w.strip().lower() for w in f if w.strip()}


def is_generic_token(token, dict_words):
    t = token.lower()
    if not t:
        return True
    if t in STOPWORDS or t in SHORT_BIO_ACRONYMS or t in SHORT_COMMON_WORDS:
        return True
    if t in DOMAIN_NOUNS or t in STATE_ABBREVIATIONS:
        return True
    if t in MANUALLY_CONFIRMED_AMBIGUOUS:
        return True
    if len(t) >= 5 and t.endswith("ase"):
        return True
    if len(t) >= 5 and t in dict_words:
        return True
    return False


def classify(surface_forms, dict_words):
    """surface_forms: list of observed surface-form strings for one term.

    Returns (filtered: bool, reason: str), matching the original CSV's
    reason-string conventions.
    """
    for sf in surface_forms:
        if DIGIT_RE.search(sf):
            return False, "contains a digit in some surface form -- real gene ID"

    for sf in surface_forms:
        if CASE_TRANSITION_RE.search(sf):
            return False, (
                "contains a lowercase-to-uppercase transition in some surface "
                "form (species-prefix + symbol pattern, e.g. 'ZmUBI') -- real gene ID"
            )

    for sf in surface_forms:
        for tok in TOKEN_SPLIT_RE.split(sf):
            if tok and not is_generic_token(tok, dict_words):
                return False, (
                    f"contains {tok!r} -- not a recognized common word/jargon "
                    "term, kept for manual review"
                )

    return True, "all constituent words are common English/molecular-biology terms"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_csv")
    ap.add_argument("output_csv")
    ap.add_argument("--target-reason", default=None,
                     help="Only recompute filtered/filter_reason for rows whose "
                          "current filter_reason equals this exact string. "
                          "Overrides the default (see below) if given.")
    args = ap.parse_args()

    dict_words = load_dict_words()

    with open(args.input_csv, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    OUT_OF_SCOPE = "out of filter scope (no locus_synonym-sourced entry)"

    def needs_reclassification(reason):
        if args.target_reason is not None:
            return reason == args.target_reason
        # Default: reprocess anything not already resolved to True or to the
        # digit-override False -- i.e. the out-of-scope rows and the
        # "kept for manual review" rows, so a newly added generic-token rule
        # (like STATE_ABBREVIATIONS) gets applied everywhere it's relevant,
        # not just to whichever subset was targeted last run.
        return reason == OUT_OF_SCOPE or "kept for manual review" in reason

    n_reclassified = 0
    n_changed = 0
    for row in rows:
        old_reason = row["filter_reason"]
        if not needs_reclassification(old_reason):
            continue
        surface_forms = [s.strip() for s in row["surface_forms"].split(";")]
        filtered, reason = classify(surface_forms, dict_words)
        n_reclassified += 1

        # A row still carrying the out-of-scope placeholder was never actually
        # classified, so any real result is new information -- apply it.
        # A row already carrying a real "kept for manual review" reason was
        # already classified by the original (or a prior) run; only overwrite
        # it for an intentional bucket move: flipping to True (auto-remove),
        # or resolving to one of the hard "-- real gene ID" overrides (digit /
        # case-transition), which moves it out of manual-review into a
        # confident real-ID verdict even though `filtered` stays False either
        # way. Anything else -- i.e. it's still an unresolved "kept for manual
        # review" case, just possibly citing a different offending token --
        # is left untouched, since a reconstructed classifier can disagree
        # with the original on *which* token it cites without disagreeing on
        # the actual verdict, and that kind of drift shouldn't be applied
        # silently.
        is_real_id_override = reason.endswith("-- real gene ID")
        if old_reason == OUT_OF_SCOPE or filtered or is_real_id_override:
            if reason != old_reason:
                row["filtered"] = str(filtered)
                row["filter_reason"] = reason
                n_changed += 1

    with open(args.output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Re-evaluated {n_reclassified} rows; {n_changed} got a new filtered/filter_reason")
    print(f"Wrote {args.output_csv}")


if __name__ == "__main__":
    main()
