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
    # Confirmed 2026-08-12 during the term-by-term OBO-attribution review:
    # single EXACT locus_synonym on one gene (flz32/tpzm:0013684) that
    # already has four solid identifiers on file -- "ABA" (abscisic acid,
    # the plant hormone) reads like a stray description fragment, not a
    # real name for this gene, and nothing is lost by removing it.
    "aba": "abscisic acid (plant hormone) -- stray fragment on flz32, not a real gene name",
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

# Expert-curated exclusions confirmed 2026-08-12. Unlike every rule below,
# these override even the digit-presence "real gene ID" hard override -- a
# maize agronomy paper's "R1"/"V6"/"chr5" is overwhelmingly the reproductive/
# vegetative growth-stage code or chromosome-location notation, not the gene
# synonym, regardless of what a heuristic sees in the string itself.
GROWTH_STAGE_CODES = {f"v{i}" for i in range(1, 15)} | {"vt"} | {f"r{i}" for i in range(1, 7)}
CHROMOSOME_NAMES = {f"chr{i}" for i in range(1, 11)}

# Real transcription-factor family names, not junk -- flagged by the
# token/case-transition rules only because no automatic rule can tell "real
# family name" from "unresolvable abbreviation." Manually confirmed to be
# kept findable, RELATED-only, same treatment as "bzip" (which the
# case-transition rule already resolves to "real gene ID" on its own; these
# additionally needed a status override out of "kept for manual review").
FAMILY_TERM_RESOLVED = {
    "bzip": "bZIP-family transcription factor name",
    "bzip transcription factor": "bZIP-family transcription factor name",
    "myb": "MYB-family transcription factor name",
    "myb transcription factor": "MYB-family transcription factor name",
    # Confirmed 2026-08-12: mapped to 12 distinct genes (hct16...hct31,
    # gll1, Zm00001d002139), all already typed RELATED, never EXACT --
    # same shape as MYB/bZIP: a real family name (hydroxycinnamoyl
    # transferase), not a single-gene collision.
    "hct": "HCT-family (hydroxycinnamoyl transferase) gene name",
}

# Below this many distinct papers, a term isn't producing enough noise to be
# worth flagging even if it would otherwise read as generic/ambiguous --
# raises the practical action threshold from the >=5 audit-reporting cutoff
# to >=10 for removal/review specifically. Confirmed 2026-08-12. Does NOT
# apply to EXPERT_OVERRIDES below -- those are unconditional regardless of
# frequency, same as MANUALLY_CONFIRMED_AMBIGUOUS always was.
MIN_DOC_FREQ_FOR_ACTION = 10


def _build_expert_overrides():
    """Unify every whole-term, manually-confirmed override into one
    (filtered, reason) lookup, checked first in classify() -- before the
    digit-presence and case-transition hard overrides, and before the
    dictionary-based generic-word path. Consolidated 2026-08-12: previously
    MANUALLY_CONFIRMED_AMBIGUOUS ("sra", "sam", ...) was buried inside
    is_generic_token(), reachable only via the token loop, i.e. only if a
    term happened to contain no digit and no case transition -- true for
    those six terms by luck, not by design. A term added there later that
    *did* contain a digit would have silently never been reached. Growth-
    stage codes and chromosome names need that same top-tier precedence for
    real reasons (r1/chr5 do contain digits), so all four expert-curated
    sources now share one tier and one precedence rule.
    """
    overrides = {}
    for term in GROWTH_STAGE_CODES:
        overrides[term] = (True, (
            "expert-curated exclusion: reproductive/vegetative growth-stage "
            "code (V1-V14, VT, R1-R6) collides with agronomic staging "
            "notation in text"
        ))
    for term in CHROMOSOME_NAMES:
        overrides[term] = (True, (
            "expert-curated exclusion: chromosome name (chr1-chr10) collides "
            "with chromosome-location notation in text"
        ))
    for term, meaning in MANUALLY_CONFIRMED_AMBIGUOUS.items():
        overrides[term] = (True, (
            f"expert-curated exclusion: {meaning} -- manually confirmed "
            "ambiguous abbreviation unrelated to the gene it's mapped to"
        ))
    for term, label in FAMILY_TERM_RESOLVED.items():
        overrides[term] = (False, f"{label}, real term -- kept findable, RELATED-only")
    return overrides


# Whole-term lookup, checked first in classify(). MANUALLY_CONFIRMED_AMBIGUOUS
# is ALSO still consulted inside is_generic_token() below -- that's a
# different, narrower role (is this *one token* of a multi-word term
# generic?) that this whole-term dict can't replace; a compound term like
# "sam-like protein" needs the token-level check to recognize "sam" as one
# generic constituent among others, which is a different question from "is
# the entire matched term exactly 'sam'."
EXPERT_OVERRIDES = _build_expert_overrides()

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


def classify(term, doc_freq, surface_forms, dict_words):
    """term: lowercased matched-term key. doc_freq: distinct-paper count.
    surface_forms: list of observed surface-form strings for this term.

    Returns (filtered: bool, reason: str), matching the original CSV's
    reason-string conventions. EXPERT_OVERRIDES (growth-stage codes,
    chromosome names, manually-confirmed-ambiguous abbreviations, resolved
    family terms) is checked first and overrides every heuristic below,
    including the digit-presence hard override.
    """
    if term in EXPERT_OVERRIDES:
        return EXPERT_OVERRIDES[term]

    for sf in surface_forms:
        if DIGIT_RE.search(sf):
            return False, "contains a digit in some surface form -- real gene ID"

    # Token-level, not a raw substring search on the whole surface form:
    # "mRNA" also matches [a-z][A-Z] (the "mR" transition) but is a common
    # bio term, not a species-prefix+symbol pattern -- checking is_generic_token
    # on the specific token that carries the transition (not the surface
    # form as a whole) excludes it correctly while still catching "ZmUBI"/
    # "bZIP" (whose lowercased token isn't a recognized common word).
    for sf in surface_forms:
        for tok in TOKEN_SPLIT_RE.split(sf):
            if tok and CASE_TRANSITION_RE.search(tok) and not is_generic_token(tok, dict_words):
                return False, (
                    "contains a lowercase-to-uppercase transition in some surface "
                    "form (species-prefix + symbol pattern, e.g. 'ZmUBI') -- real gene ID"
                )

    for sf in surface_forms:
        for tok in TOKEN_SPLIT_RE.split(sf):
            if tok and not is_generic_token(tok, dict_words):
                if doc_freq < MIN_DOC_FREQ_FOR_ACTION:
                    return False, (
                        f"found in only {doc_freq} papers -- below the "
                        f"{MIN_DOC_FREQ_FOR_ACTION}-document removal/review "
                        "threshold, kept without further review"
                    )
                return False, (
                    f"contains {tok!r} -- not a recognized common word/jargon "
                    "term, kept for manual review"
                )

    if doc_freq < MIN_DOC_FREQ_FOR_ACTION:
        return False, (
            f"found in only {doc_freq} papers -- below the "
            f"{MIN_DOC_FREQ_FOR_ACTION}-document removal/review threshold, "
            "kept without further review"
        )
    return True, "all constituent words are common English/molecular-biology terms"


def apply_curation_overlay(row):
    """Apply only the 2026-08-12 rules (EXPERT_OVERRIDES + MIN_DOC_FREQ_FOR_ACTION)
    on top of a row's EXISTING filtered/filter_reason, without touching the
    rest of classify()'s generic-word/digit/case-transition path.

    Deliberately narrower than "recompute via classify() for every row":
    classify()'s dictionary-based generic-word tier is a reconstruction (see
    the module docstring's fidelity caveat), and a full reclassify was
    confirmed 2026-08-12 to regress several already-correct historical
    verdicts it doesn't independently reproduce (e.g. "mRNA", "CDS", "NO",
    the "S" in "glutathione S-transferase" -- all correctly filtered=True in
    the 2026-08-07 CSV, but not resolvable as generic by the reconstructed
    word lists on their own). This overlay only ever changes a row for one of
    the EXPERT_OVERRIDES entries or the doc-frequency threshold; every other
    row passes through byte-identical.

    Returns True if the row was changed.
    """
    term = row["term"]
    doc_freq = int(row["doc_freq"])

    if term in EXPERT_OVERRIDES:
        new_filtered, new_reason = EXPERT_OVERRIDES[term]
    else:
        old_filtered = row["filtered"] == "True"
        old_is_review = (not old_filtered) and ("manual review" in row["filter_reason"])
        if (old_filtered or old_is_review) and doc_freq < MIN_DOC_FREQ_FOR_ACTION:
            new_filtered, new_reason = False, (
                f"found in only {doc_freq} papers -- below the "
                f"{MIN_DOC_FREQ_FOR_ACTION}-document removal/review threshold, "
                "kept without further review"
            )
        else:
            return False  # untouched

    changed = (row["filtered"] != str(new_filtered)) or (row["filter_reason"] != new_reason)
    row["filtered"] = str(new_filtered)
    row["filter_reason"] = new_reason
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_csv")
    ap.add_argument("output_csv")
    ap.add_argument("--target-reason", default=None,
                     help="Only recompute filtered/filter_reason (via the full "
                          "classify() heuristic, not the narrow curation overlay) "
                          "for rows whose current filter_reason equals this exact "
                          "string. Use to extend classify()'s reach to rows outside "
                          "its original scope (e.g. never-classified rows) -- NOT "
                          "for bulk-reclassifying already-resolved rows, which "
                          "regresses known reconstruction gaps (see "
                          "apply_curation_overlay's docstring). Default (omitted): "
                          "apply only the 2026-08-12 curation overlay, which never "
                          "touches classify()'s generic-word path.")
    args = ap.parse_args()

    with open(args.input_csv, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    n_changed = 0

    if args.target_reason is not None:
        dict_words = load_dict_words()
        for row in rows:
            if row["filter_reason"] != args.target_reason:
                continue
            surface_forms = [s.strip() for s in row["surface_forms"].split(";")]
            filtered, reason = classify(row["term"], int(row["doc_freq"]), surface_forms, dict_words)
            if reason != row["filter_reason"]:
                row["filtered"] = str(filtered)
                row["filter_reason"] = reason
                n_changed += 1
    else:
        for row in rows:
            if apply_curation_overlay(row):
                n_changed += 1

    with open(args.output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{n_changed} rows changed")
    print(f"Wrote {args.output_csv}")


if __name__ == "__main__":
    main()
