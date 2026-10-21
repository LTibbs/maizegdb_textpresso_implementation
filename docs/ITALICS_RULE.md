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
3. The form occurs **at least once** in the paper's JATS `<body>`, with
   references, acknowledgments and back matter excluded. If it never occurs
   there, the test **abstains and keeps** — no data is not evidence of absence.
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

## Operating it

```bash
# 1. italic counts per paper (~1 MB, ~65 min for 3,299 papers at 3 req/s; 1,627 have JATS)
python3 bin/jats_italics.py --accession-file maizeopenaccess_accessions.txt \
    --obo .data/obofiles4production/zmays_genes_20260708.obo \
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
(`not_italicised:0/5`), `hygiene=0` returns the unfiltered set, and
`italics=0` disables only the typography test.
