# The italics rule

The adopted gene-annotation filter. Replaces the confidence-scoring approach,
which was evaluated and rejected: it sat within 1.3 points of this rule's
precision at the same retention, for considerably more machinery.

## Definition

Drop a gene annotation when **all five** hold:

1. The annotation comes from a gene ontology — `MAIZE_GENES` or
   `MAIZE_GENES_RELATED`. GO/PO/TO concept names are not italicised by
   convention, so they are never tested.
2. The matched surface form is **4 characters or fewer**.
3. The form occurs **at least once** in the paper's body, with references,
   acknowledgments and back matter excluded. The body is the JATS `<body>`
   where PMC provides one, and the source PDF's text otherwise (see
   [PDF fallback](#pdf-fallback)). If the form never occurs there, or the
   paper has no italic data, the test **abstains and keeps**. No data is not
   evidence of absence.
4. **Zero** of those occurrences are italicised.
5. The form's OBO provenance class is **not** `registered` and not
   `identifier`, **and** the annotation is not from the EXACT channel
   (`MAIZE_GENES`).

Implemented as `annotation_italics.never_italicised()`.

## Provenance classes

Determined by where the exact surface form comes from in the OBO:

| class | definition |
|---|---|
| `identifier` | a synonym whose source field is an assembly or accession id — any `*_id`, or `GenBankID` (42 such fields in the maize OBO) |
| `registered` | the form **is** some gene's `name`, or has source `locus` or `locus_name` |
| `alias` | reached only through `locus_synonym` or `description` |
| `unknown` | absent from the class map — **not exempt**, so a stale map makes the rule more aggressive |

## The two exemptions, and why both

**Channel.** Textpresso's annotation channel *is* the OBO synonym scope —
verified at 99.5% agreement over a 4,000-annotation sample (the exceptions are
forms that are both a term NAME and a RELATED synonym, reported as RELATED).
Curator confirmation splits sharply by channel: EXACT **66.2%**, RELATED
**24.9%**. It is the single best exemption key and it arrives in the API
response, requiring no OBO lookup.

**Class.** Channel alone still loses 19 registered symbols, because a registered
gene name can arrive in the RELATED channel when it is also a RELATED synonym on
another gene — the `pop1` case. The class exemption zeroes that.

They compose rather than substitute.

## Measured effect

45,577 gene annotations across 333 curator-matched MaizeOA papers, scored
against MaizeGDB's curated gene lists:

| exemption | kept | % volume | confirmed rate | confirmed retained | confirmed lost | registered symbols lost |
|---|---|---|---|---|---|---|
| no filter | 45,577 | 100.0 | 45.8% | 100.0% | 0 | 0 |
| registered/identifier only | 40,655 | 89.2 | 50.9% | 99.1% | 198 | 0 |
| EXACT channel only | 41,561 | 91.2 | 50.2% | 99.8% | 51 | 19 |
| **both (adopted)** | **41,795** | **91.7** | **49.9%** | **99.8%** | **43** | **0** |
| no exemption | 40,130 | 88.0 | 51.4% | 98.7% | 265 | 82 |
| test EXACT channel only | 44,146 | 96.9 | 46.8% | 99.0% | 214 | 67 |

What it removes, with curator-confirmation rates: `myb` (627 mentions, 0%),
`bhlh` (555, 0%), `per` (342, 0%), `ltp` (220, 0%), `dfr` (184, 2%),
`gst` (174, 0%), `fer` (152, 0%), `cct` (132, 0%), `l2` (129, 0%),
`c2h2` (100, 1%) — the family and domain abbreviations.

## What it deliberately does not catch

- **EXACT-scope family abbreviations.** `wd40` (274 mentions, 0% confirmed) and
  `l3` (127, 0%) are EXACT-scope `locus_synonym`s, so the channel exemption
  spares them. That exemption costs about 1.0 point of precision but rescues
  `cp24` (44 mentions, 100% confirmed), `akr` (36, 100%) and `pip2` (35, 89%).
  No lexical or structural feature was found that separates the two groups —
  `wd40` and `l3` each map to only one gene, so ambiguity degree does not help.
- **Registered names used non-genically.** `pop1`/`pop3` (177 mentions, 0%
  confirmed, never italic) are mapping *populations* — "Pop1(CML312 x
  Ye107):142 RILs" — but they are registered locus names, so they are exempt.
- **Papers predating the italicisation convention.** `o2` in a 1982 J Cell Biol
  paper has 14 body occurrences, none italic, and `o2` in the curator gene list.
  The class exemption rescues it here, but nothing in typography can detect the
  condition in general. This is the rule's systematic blind spot.

## The English-word companion rule (2026-10-23)

The first bullet above is where the strict-zero rule is blind: it exempts the
EXACT channel, so `late` (an EXACT `locus_synonym` of Zm00001eb059970, class
`alias`) was annotated in every paper that used the ordinary word. A second
rule, `annotation_hygiene.english_word_gate`, closes that for forms that are
plain English words. It runs after the other filters and applies to both gene
channels and every provenance class:

    drop  <=>  gene ontology
          and  the surface form is in the English-forms list (any spelling:
               `late`, `Late`, `CO`, `ABA` -- no capitals exemption)
          and  the form is never italicised in the paper
          and  no OTHER, non-English surface form of the same gene survives
               the other filters in the paper body

So an English-word gene is annotated only with positive evidence: italic at
least once, or another synonym of that gene actually appears. A paper with no
italic data can only pass on the second test. Reason code
`english_word_gene:no_italic_no_synonym`. `italics=0` skips it, because its
first leg is typography; `hygiene=0` returns everything.

The list is `.data/italics/english_gene_forms.json` (117 forms for
`zmays_genes_20260708.obo`: single alphabetic OBO forms with wordfreq Zipf >= 3.0,
the tier policy's "common word" line). Build it with the class map, and
rebuild it with the OBO:

```bash
python3 bin/build_class_map.py --obo .data/obofiles4production/zmays_genes_20260708.obo \
    --out .data/italics/obo_short_form_classes.json \
    --english-out .data/italics/english_gene_forms.json     # needs: pip install wordfreq
export TEXTPRESSO_ENGLISH_GENE_FORMS=.data/italics/english_gene_forms.json
```

**Checked against curator gene lists (2026-10-23)** -- `maizegdb_references_AI_ready.json`
joined by PubMed id, 2,004 MaizeOpenAccess papers with curated genes/gene models
(20,762 symbols, 55,300 gene models). A gene annotation is *confirmed* when its
gene's name, or an exact locus/assembly id, is in that paper's curated list
(`locus_synonym` aliases do not count, or `late` would confirm itself). Of the
384,206 gene mentions that reach this rule (123,971 confirmed, 32.3%), a gene-in-paper
*pair* is confirmed the same way (13,244 pairs):

    variant                                  removed (confirmed)   confirmed pairs lost   survivors confirmed
    no rule                                          0                    0                    1.8%
    capitals exempt (first build)               30,307 (242)             24                    3.1%
    ADOPTED: no capitals exemption              43,512 (252)             28                    6.9%
    ... and italic share >= 25%                 47,778 (463)             34                    8.2%
    ... italic >= 1 only, no synonym leg        44,878 (432)             28                    5.9%

The first build exempted all-caps spellings as "abbreviations" (`CO`, `ABA`). That
kept 19,175 mentions at 3.1% confirmed -- `ABA` (abscisic acid) alone was 6,041
at 0%, `CA`, `EH`, `CO`, `CS` also 0% -- and protected only 4 confirmed pairs, so
it was removed. Why the existing rules did not already catch them: 75% of those
survivors are EXACT-channel (exempt from the italics rule), 17% are italicised
once somewhere in the paper, and the tier gate does not judge genes.

Cost of the adopted rule: 28 of 13,244 confirmed pairs (0.2%). 19 of the first
build's 24 were `r1` matched only as "red"; the rest are `ubi1` as "promoter",
`clt1` as "ct", `vp1` as "pm". What it removes is dominated by OBO-generation
fragments and ordinary words: `promoter`, `binding`, `mrna`, `red`, `expression`,
`late` (0 of 970 confirmed). The other-synonym leg spares about 180 confirmed
mentions and no further pairs, so it is kept on principle rather than on this
data. Confirmation is still not precision (rule 5 in CLAUDE.md): it is a
between-group comparison -- 0.6% confirmed for what the adopted rule removes
(252 of 43,512), against 36.3% for the gene mentions that remain. Gene-in-paper
level, not sentence-checked.

## Caveat on the numbers

"Curator-confirmed rate" is **not precision**. Curators record genes of
curatorial interest, not every gene validly mentioned, so a dropped unconfirmed
mention is not necessarily wrong. Differences between rules are trustworthy; the
absolute 45.8% -> 49.9% is a relative improvement, not a precision figure. The
registered-symbols column is the one to weight most heavily, because those
losses are verifiable directly against the OBO.

The measurements above were taken against `zmays_genes_20260813.obo`. On
2026-10-20 production reverted to `zmays_genes_20260708.obo` (with the
EXACT→RELATED fix), which restores 39 short alias forms the rule can now judge
(`aba`, `co`, `no`, `as`, `bam`, `sam`, ...). Re-measure before quoting these
numbers for the 20260708 OBO.

## PDF fallback

PMC efetch returns a JATS `<body>` for only 1,627 of the 3,299 MaizeOpenAccess
papers. The rest are PMC "free to read" without a reuse licence, or have no PMC
record, and no NCBI or Europe PMC bulk route serves them. `bin/pdf_italics.py`
reads italics from the fonts of the PDFs already staged for ingest instead, and
writes the same index shape. JATS wins wherever both exist.

A span counts as italic if the font flag, the font name (`-Italic`, `-It`,
`.I`, `-O`) or the font descriptor's `/ItalicAngle` says so. Typesetters such
as Advent 3B2 emit opaque subset fonts (`AdvP4041BD`) that report
`ItalicAngle 0` even for the italic face. Such a font is treated as italic, per
paper, when it is not the main body font and carries conventionally italic
words (`Zea mays`, `in vivo`, `E. coli`). Mistakes here err towards "italic",
which can only make the rule keep more. Everything from the first
References/Literature Cited/Acknowledgments heading onwards is excluded.

A paper with fewer than 20 italic characters in its body is **left out of the
index**, so the rule abstains for it rather than reading zero.

Measured against JATS on the 1,500 papers both sources cover (2026-10-21), for
forms the rule can act on (<= 4 characters, not `registered`/`identifier`):

| | forms | share |
|---|---|---|
| agree on zero vs non-zero italic | 15,254 | 98.0% |
| PDF zero, JATS italic (would wrongly drop) | 36 | 0.23% |
| PDF italic, JATS zero (would keep) | 270 | 1.7% |

The 36 wrong drops fall in 23 papers. Some are JATS noise rather than PDF
error, e.g. `as` italicised once in 34 occurrences.

Coverage for the corpus is 2,888 of 3,299 papers (1,560 JATS + 1,328 PDF), up
from 1,627 JATS entries, 77 of which had been built from the wrong paper (see
`jats_italics.py`). Of the 411 left out, most are 1980s-90s PNAS, NAR and EMBO J
backfiles: page images with an invisible Courier OCR layer, so there is no
typography to recover. `--provenance-out` records which source each
accession came from.

## Operating it

```bash
# 1a. italic counts per paper from JATS (~65 min at 3 req/s on a cold cache)
python3 bin/jats_italics.py --accession-file maizeopenaccess_accessions.txt \
    --obo .data/obofiles4production/zmays_genes_20260708.obo \
    --cache-dir .data/italics/jats_cache \
    --out .data/italics/MaizeOpenAccess.jats.json

# 1b. fill the gaps from the source PDFs (needs `pip install pymupdf`; ~2 min, 8 workers)
python3 bin/pdf_italics.py --accession-file maizeopenaccess_accessions.txt \
    --pdf-root /data/textpresso/raw_files/pdf/MaizeOpenAccess \
    --obo .data/obofiles4production/zmays_genes_20260708.obo \
    --jats-index .data/italics/MaizeOpenAccess.jats.json \
    --provenance-out .data/italics/MaizeOpenAccess.provenance.json \
    --out .data/italics/MaizeOpenAccess.json

# 2. provenance class map (~98 KB; avoids a 10.5 MB OBO parse at startup)
python3 bin/build_class_map.py \
    --obo .data/obofiles4production/zmays_genes_20260708.obo \
    --out .data/italics/obo_short_form_classes.json

export TEXTPRESSO_ITALICS_INDEX=.data/italics/MaizeOpenAccess.json
export TEXTPRESSO_OBO_CLASS_MAP=.data/italics/obo_short_form_classes.json
```

**Rebuild both together whenever the OBO is regenerated.** Forms missing from
the class map resolve to `unknown`, which is not exempt — a stale map makes the
rule more aggressive, not less.

Nothing is permanently removed: every drop carries a reason
(`not_italicised:0/5`), `hygiene=0` returns the unfiltered set,
`italics=0` disables only the typography test, and `tiers=0` disables only the
tier gate (which runs only when the server has `TEXTPRESSO_MATCH_DICTIONARY`).
