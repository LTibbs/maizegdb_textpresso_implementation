"""Per-paper italicisation lookup for Textpresso gene annotations.

Drop this next to casannot.py, alongside annotation_hygiene.py.

Textpresso ingests PDFs through pdf2tpcas, which discards italicisation. Maize
nomenclature italicises gene symbols, so that lost typography is the single most
discriminating gene-vs-look-alike signal available. This module reads a
precomputed index (built by jats_italics.py from PubMed Central JATS XML, then
filled from the source PDFs' fonts by pdf_italics.py) and exposes the per-paper,
per-surface-form italic counts that annotation_hygiene.clean_annotations(
italics=...) consumes.

THE ADOPTED RULE is never_italicised() below (strict zero, with the EXACT
channel and registered/identifier forms exempt). The rate-based
non_italicised() and the table that follows are the earlier exploration it
replaced, kept for reference. Measured on 337 curated MaizeOA papers / 46,566
gene annotations, curator confirmation against MaizeGDB's curated gene lists
by italic rate:

    italic rate   all forms     forms <= 4 chars
    0.00 - 0.05      23.6%          4.2%     <- the rule fires here
    0.05 - 0.25      55.1%         38.8%
    0.25 - 0.60      60.9%         50.7%
    0.60 - 0.95      71.0%         55.7%
    0.95 - 1.00      65.5%         69.5%

(Superseded.) Dropping gene annotations whose surface form is <= 4 characters
and italicised in <= 5% of its occurrences removes 7.6% of gene mention volume while retaining
99.3% of curator-confirmed mentions. The terms it removes are family and domain
names that are 0% curator-confirmed: MYB (516 mentions), bHLH (423), PER (324),
WD40 (260), FER (152), CCT, GST, PAL, C2H2, HCT.

IMPORTANT -- scope. The rule applies to gene ontologies only. GO/PO/TO concept
names are not italicised by convention, so an italic test there would reject
everything. It is also a NEGATIVE test only: low italic rate means "probably not
a gene", but high italic rate does not mean "gene" -- `et`, `al` and the
statistical `p`, `n`, `r` are italicised too.

The index is keyed by surface form rather than character offset on purpose:
PDF-derived tpcas offsets do not correspond to JATS offsets, because PDF
extraction reorders captions and normalises whitespace. Per-paper per-form rates
are robust to that and carry the signal.
"""

import json
import os

#: Synonym `source` fields that carry a stable, machine-assigned identifier.
#: The maize OBO uses one field per assembly, including every NAM founder, so
#: match by suffix rather than listing them -- an earlier hard-coded set of four
#: left 19 GenBank accessions (dq100346, x17565, af305892 ...) classified as
#: 'alias' instead of 'identifier', i.e. at the least reliable prior instead of
#: the most reliable one.
def is_identifier_source(src):
    return bool(src) and (src.endswith("_id") or src == "GenBankID")


#: Ontologies whose annotations are gene symbols, and so subject to the test.
GENE_ONTOLOGIES = frozenset({"MAIZE_GENES", "MAIZE_GENES_RELATED"})

#: Defaults validated in the table above. `max_len` keeps the rule off long
#: descriptive names, where a low italic rate is uninformative rather than
#: damning; `min_occurrences` abstains when the JATS evidence is too thin.
DEFAULT_MAX_RATE = 0.05
DEFAULT_MAX_LEN = 4
DEFAULT_MIN_OCCURRENCES = 3


def load_class_map(path):
    """Load {surface_form_lower: obo_class} for forms at or under max_len.

    Only forms short enough to reach the italics test are needed, which is 5,419
    entries / ~98 KB for zmays_genes_20260708.obo -- so the server does not have
    to parse a 10.5 MB OBO at startup just to answer "is this a registered
    symbol?". Rebuild it with build_class_map.py whenever the OBO changes.

    Returns a callable suitable for clean_annotations(obo_class_of=...), which
    reports 'unknown' for anything absent -- and 'unknown' is NOT exempt, so a
    stale map makes the rule more aggressive, not less. Rebuild it with the OBO.
    """
    if not path or not os.path.exists(path):
        return lambda term: "unknown"
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    return lambda term: m.get((term or "").lower().strip(), "unknown")


def load_index(path):
    """Load an index written by `jats_italics.py --obo ... --out italics.json`.

    Shape: {accession: {surface_form_lower: [n_italic, n_total]}}
    Pruned to OBO surface forms, this is ~1 MB for the 3,299-paper MaizeOpenAccess
    corpus, so loading it once at process start is cheap.
    """
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def accession_of(identifier):
    """'MaizeOpenAccess/10.1093_jxb_erad297/10.1093_jxb_erad297.tpcas' -> accession."""
    if not identifier:
        return ""
    base = identifier.split("/")[-1]
    for suffix in (".tpcas.gz", ".tpcas"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base or identifier


def for_paper(index, identifier):
    """Per-paper sub-index, or {} when the paper is not covered."""
    if not index:
        return {}
    return index.get(accession_of(identifier)) or index.get(identifier) or {}


def italic_rate(paper_index, term, min_occurrences=DEFAULT_MIN_OCCURRENCES):
    """Fraction of this form's occurrences that are italicised, or None.

    None means abstain -- either the paper has no JATS index or the form occurs
    too few times to judge. Callers must treat None as "no opinion", never as 0.
    """
    if not paper_index:
        return None
    entry = paper_index.get((term or "").lower())
    if not entry:
        return None
    italic, total = entry[0], entry[1]
    if not total or total < min_occurrences:
        return None
    return italic / float(total)


def non_italicised(paper_index, term, ontology, *,
                   max_rate=DEFAULT_MAX_RATE,
                   max_len=DEFAULT_MAX_LEN,
                   min_occurrences=DEFAULT_MIN_OCCURRENCES,
                   gene_ontologies=GENE_ONTOLOGIES):
    """-> (should_drop, rate). `rate` is None when the test abstains."""
    if ontology not in gene_ontologies:
        return False, None
    if not term or len(term) > max_len:
        return False, None
    rate = italic_rate(paper_index, term, min_occurrences)
    if rate is None:
        return False, None
    return (rate <= max_rate), rate


#: Annotation channel that carries EXACT-synonym matches. Textpresso's channel
#: is the OBO synonym scope (verified: channel == scope for 99.5% of a 4,000
#: annotation sample; the exceptions are forms that are both a term NAME and a
#: RELATED synonym, which are reported as RELATED). Curator confirmation splits
#: sharply by channel -- EXACT 66.2%, RELATED 24.9% over 45,577 annotations --
#: so the channel is the single best exemption key, and it arrives in the API
#: response with no OBO lookup required.
EXACT_ONTOLOGY = "MAIZE_GENES"


def never_italicised(paper_index, term, ontology, obo_class, *,
                     max_len=DEFAULT_MAX_LEN,
                     exempt_classes=("registered", "identifier"),
                     exempt_exact_channel=True,
                     gene_ontologies=GENE_ONTOLOGIES):
    """The strict-zero rule: drop a gene annotation that is NEVER italicised.

        drop  <=>  ontology is a gene ontology
              and  len(surface form) <= max_len
              and  the form occurs at least once in the paper body
                   (references and acknowledgments excluded)
              and  zero of those occurrences are italicised
              and  the form's OBO provenance class is not exempt

    The exemption is on the matched SURFACE FORM, not on the gene it was matched
    to: a form that is any gene's registered name or a stable identifier is
    exempt everywhere. That is what spares `o2` in papers predating the italic
    convention, and it is also why `pop1`/`pop3` survive even where they plainly
    mean "population 1" -- they are registered locus names.

    Measured over 45,577 gene annotations in 333 curator-matched MaizeOA papers
    (see italic_rule_comparison.csv):

                                       confirmed  retained  conf.mentions  registered
                                            rate                 lost      symbols lost
        no filter                           45.8%    100.0%             0            0
        strict zero, no exemption           51.4%     98.7%           265           82
        exempt registered/identifier only   50.9%     99.1%           198            0
        exempt EXACT channel only           50.2%     99.8%            51           19
        exempt BOTH  (the default here)     49.9%     99.8%            43            0
        confidence >= 0.3                   51.2%     99.8%            32            3

    Exempting the EXACT channel is what spares `wd40` (274 mentions), `l3` (127)
    and the other EXACT-scope family abbreviations from being dropped -- which
    costs about 1.0 point of precision, because 86% of those are in fact junk,
    but also rescues `cp24` (44 mentions, 100% curator-confirmed), `akr` (36,
    100%) and `pip2` (35, 89%). Set exempt_exact_channel=False for the more
    aggressive variant.

    -> (should_drop, n_italic, n_total). n_* are None when the test abstains.
    """
    if ontology not in gene_ontologies:
        return False, None, None
    if not term or len(term) > max_len:
        return False, None, None
    if exempt_exact_channel and ontology == EXACT_ONTOLOGY:
        return False, None, None
    if obo_class in exempt_classes:
        return False, None, None
    entry = (paper_index or {}).get(term.lower())
    if not entry:
        return False, None, None          # form absent from the body: abstain
    n_italic, n_total = entry[0], entry[1]
    if not n_total:
        return False, None, None
    return (n_italic == 0), n_italic, n_total


def coverage(index, annotations, identifier, min_occurrences=1):
    """Share of a paper's gene annotations the index can speak to -- for the
    hygiene report, so a curator can see when the test simply had no data.

    min_occurrences defaults to 1 because never_italicised() judges a form on
    a single body occurrence; the earlier default of 3 (from the rate rule)
    hid 25.8% of the rule's drops from this count (2026-10-21)."""
    paper = for_paper(index, identifier)
    gene = [a for a in annotations if a.get("ontology") in GENE_ONTOLOGIES]
    if not gene:
        return {"gene_annotations": 0, "with_italic_data": 0}
    n = sum(1 for a in gene
            if italic_rate(paper, a.get("term"), min_occurrences) is not None)
    return {"gene_annotations": len(gene), "with_italic_data": n}
