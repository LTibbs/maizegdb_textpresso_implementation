# Ontology annotation build — the six decisions, as implemented

Expands Textpresso annotation from GO/PO/TO to eleven channels, and replaces the
"everything in the OBO is a match string" assumption with a four-tier policy.
Nothing here deletes a term: scoping decides what enters a *match dictionary*,
the published collection keeps every term and every relationship.

Inputs: `maize-ontology-collection/` (the published collection, sha256-verified
against its `MANIFEST.csv`) plus the full `chebi.obo`.
Outputs: `match_dictionary.csv`, eleven `*_annot.obo`, `build_report.json`.

```bash
pip install wordfreq                       # build-time only
python3 build_match_dictionary.py \
    --collection maize-ontology-collection \
    --chebi-full chebi.obo --outdir build/
# or, so quarterly refreshes reapply it automatically:
python3 scripts/refresh_ontologies.py --annotation-build \
    --collection maize-ontology-collection --chebi-full chebi.obo \
    --annotation-outdir build/
```

Measured output of the build as shipped: **190,208 match strings over 59,504
matchable terms** — 186,242 T0, 3,640 T1, 326 T2; by kind 59,504 labels, 72,394
EXACT synonyms, 58,310 RELATED.

| channel | matchable terms | strings | version built from |
|---|---|---|---|
| GO | 37,633 | 117,443 | releases/2026-07-26 |
| ChEBI | 7,240 | 42,922 | 256 |
| SO | 2,409 | 6,561 | 2026-08-07 |
| PO | 1,659 | 6,438 | releases/2026-01-09 |
| NCBITaxon | 2,680 | 5,038 | 2026-07-12 (pruned) |
| ECO | 2,217 | 3,088 | releases/2026-07-10 |
| TO | 1,587 | 2,991 | releases/2026-01-14 |
| PATO | 1,886 | 2,758 | releases/2025-05-14 |
| CO_322 | 921 | 1,118 | cropontology-live/2026-09-22 |
| PSO | 754 | 994 | releases/2026-01-08 |
| PECO | 518 | 857 | releases/2025-12-02 |

Drop accounting, from `build_report.json`: 211,771 ChEBI stanzas out of scope,
9,134 imported stanzas (PSO 4,145, TO 2,979, PECO 2,010), 459 GO
`gocheck_do_not_annotate`, 422 non-maize species-scoped synonyms, 284 strings
blocked at T3.

## 1 · ChEBI — full release, RELATED synonyms kept, scoped by agronomic role

`chebi_lite.obo` carries **zero synonyms** across 218,822 terms: every match
string in it is a systematic IUPAC name, so a paper writing "ABA" or "abscisic
acid" matches nothing. The build therefore reads the **full** `chebi.obo`
(271 MB) and keeps RELATED synonyms. The decisive case: on
`CHEBI:2365 (+)-abscisic acid`, both `ABA` and `Abscisic acid` exist **only** as
RELATED. Without them ChEBI cannot annotate the single most-mentioned hormone in
the maize literature.

Scope is option B plus four role roots (`CHEBI_ROLE_ROOTS` in the builder):
referenced closure + `has_role` subtrees for plant metabolite, agrochemical,
plant growth regulator, plant hormone, nutrient, mycotoxin, micro/macronutrient,
**herbicide, fungicide, insecticide, fertilizer**. Fertilizer added 0 new terms
(all 38 bearers arrive via other roles); herbicide +169, insecticide +161,
fungicide +60.

The **3:STAR subset was not used**. It is ChEBI's curation-status flag (2:STAR
153,510, 3:STAR 62,119, 1:STAR 2,904 in the lite file) and says nothing about
relevance to maize — it would still admit 62k systematic names while discarding
real compounds.

**Known gap, handled:** ChEBI's role annotation is incomplete exactly where
maize cares most. Fumonisin B1, aflatoxin B1 and zearalenone carry no
`has_role mycotoxin` (FB1 is annotated "metabolite" and "carcinogenic agent"),
so the ear-rot toxins fell outside the role closure. `CHEBI_FAMILY_SEEDS` adds
them by `is_a` subtree: +183 terms. Check that list when a compound class you
expect turns out to be missing — the role graph is not a safe assumption.

**Version drift to resolve:** the collection pins `chebi_lite.obo` at release
255; this build fetched 256. Re-pin the collection to 256 on the next refresh so
one release governs both files. Recomputing the 8-root option B on 256 gives
6,678 terms where the September proposal reported 7,404 — release drift plus a
stricter reading of "referenced" (all CHEBI references, 2,077, rather than
logical-only, 1,123).

## 2 · GO — everything live except what GO forbids annotating to

All 37,633 live terms minus the 459 in `gocheck_do_not_annotate`. That subset is
GO's own flag for classes no annotation may target: roots, grouping classes, and
over-general terms. 134 of them have one- or two-word names (`transport`,
`cell wall`, `fermentation`, `cytokinesis`, `transporter activity`) — the
generic strings that fire constantly and tell a curator nothing. The sibling
flag `gocheck_do_not_manually_annotate` does not exist in `go-basic.obo`, so it
cannot be filtered on here.

GO precision is handled **after** annotation, by `annotation_hygiene.py`
(sections, stop-forms, ancestor collapse) and the tier gate — consistent with
gate-don't-delete: you cannot score a mention that was never matched.

## 3 · Native stanzas only

PSO ships 4,899 live stanzas of which only 754 are PSO; TO 4,566 of which 1,587
are TO; PECO 2,528 of which 518 are PECO. TO's and PECO's imports are nameless
stubs, so dropping them costs nothing. PSO's 3,917 imported NCBITaxon stanzas
*do* carry names — 4,945 match strings — and that is where `rice`, `rape`,
`milo`, `pear` and `root` entered a plant-stress dictionary.

An imported id now resolves against the ontology that owns it: `PO:0025034`
matches as PO, once, not separately as PSO.

Pruned parents are re-emitted in the `*_annot.obo` files as **nameless graph
stubs** (`property_value: graph_stub "true"`), so ancestor expansion still
traverses GO's do-not-annotate grouping classes and out-of-scope ChEBI parents
while those contribute no match strings. 1,290 stubs in ChEBI, 219 in GO, 63 in
TO, a handful elsewhere. All eleven files parse clean through `pronto`.

## 4 · Species get their own channel

The cross-species request — a user who works in sorghum pulling papers that
mention their species — is served by promoting
`external/ext_ncbitaxon_maize_subset.obo` (2,680 stanzas, 2,650 of them ids PSO
also imports) to its own `NCBITaxon` channel, instead of letting species ride in
as PSO. Species are then labelled as species, and `root` and `California` are
tiered as the hazards they are rather than appearing in a stress facet.

**Coverage caveat:** the pruned subset was built for the collection's own
reference needs, not as a species index. `sorghum` is **not** in it (`rice` is).
Before advertising cross-species search, extend the wanted-id set in
`refresh_ontologies.py prune_taxonomy()` with the species MaizeGDB users
actually ask about, and rebuild.

## 5 · Four-tier match-string policy

| tier | strings | rule | runtime behaviour |
|---|---|---|---|
| T0 | 186,242 | everything else | match freely, case-insensitive |
| T1 | 3,640 | abbreviation shape (`DNA`, `ABA`, `QTL`), or single token Zipf 2.5–3.5 | must match the stored case |
| T2 | 326 | single common word (Zipf ≥ 5.0), or ≤ 4 chars and Zipf ≥ 3.0 | needs supporting context |
| T3 | 284 blocked | ≤ 2 characters | never in the dictionary; id-only |

Abbreviation shape is tested **before** frequency — otherwise `DNA` (Zipf 5.2 as
a lowercase word) lands in T2 and SO loses its most common term. Tiers are
computed once at build time by `make_tier_fn()` and frozen into the CSV, which
is why `annotation_tiers.py` needs no third-party imports.

**What T2 means in practice** (`TierIndex.verdict`, three tests, in order):

1. *longer form* — the same term has a longer string and that is what the text
   writes (`water deficit`, not bare `water`; `Senescence`, not `Sen`).
2. *qualifier* — a qualifier sits within two words (`root architecture`,
   `root and shoot biomass`). `DEFAULT_QUALIFIERS` plus per-channel lists in
   `CHANNEL_QUALIFIERS`; for `NCBITaxon` the test is sentence-wide, because
   "orthologues were identified in rice" puts the licensing word far from the
   species name.
3. *companion term* — another unambiguous term (T0, ≥ 7 chars) from the same
   channel appears in the sentence.

Survivors carry the verdict (`tier_T2_context:qualifier:deficit`) in a `tier`
field rather than disappearing silently, and **a mention with no sentence
available is kept**, flagged `tier_T2_context_unavailable` — absence of evidence
is not evidence of absence. 76 of the 192 hazardous strings reviewed in
September sit on a term that also carries a safe string, so for those T2 reduces
to "match the longer form".

**What tiering does not fix:** `Kit` and `Ltd` are GO RELATED synonyms stored in
exactly the case papers use ("Radioiodination Kit", "Elsevier Science Ltd"), so
they are T1 and pass the case test. They are stop-forms, handled by
`exclusions=` in `annotation_hygiene.clean_annotations`. Tiering and stop-forms
are complementary; neither subsumes the other.

## 6 · RELATED synonyms in, non-maize species scoping out

RELATED synonyms are included — they are 58,310 of the 190,208 strings, and in
ChEBI they are the only route to `ABA`. Forms explicitly scoped to another
species (`05-booting in wheat`) are dropped: 422 in this build, 409 of them in
GO. Maize-scoped forms are kept. The September count was 493 against the raw
files; it is lower here because imported stanzas are dropped first, so their
scoped synonyms never reach the test.

## 7 · Obsolete terms

Excluded from matching, retained for id resolution: 11,696 obsolete stanzas
(10,248 of them GO) are written into the `*_annot.obo` files with
`is_obsolete: true`, their `replaced_by`/`consider` links intact, and their
label moved to `property_value: retired_label` so no matcher can read it as a
match string. An old paper's retired identifier still resolves; it just never
fires on text.

## Wiring it at annotation time

```python
import os
from annotation_hygiene import clean_annotations
from annotation_tiers import TierIndex, tier_rule

TIERS = TierIndex.from_csv(os.environ["TEXTPRESSO_MATCH_DICTIONARY"])

kept, dropped = clean_annotations(
    annotations, sections,
    section_types_at=casannot.section_types_at,
    ontology_view=view,
    tiers=TIERS, tier_rule=tier_rule,
    sentence_at=lambda b, e: casannot.sentence_text_at(b, e, sentences),
)
```

`tiers=None` (the default) leaves behaviour exactly as it is today, so this
ships dark and is enabled per request like `hygiene`. Memory cost of the index
is the CSV itself — 23 MB on disk, ~180k distinct forms.

## Verify

```bash
python3 -m py_compile build_match_dictionary.py annotation_tiers.py annotation_hygiene.py
python3 annotation_tiers.py build/match_dictionary.csv      # index stats + demo verdicts
python3 -c "import pronto,glob; [pronto.Ontology(p) for p in glob.glob('build/*_annot.obo')]"
```

Expected from the demo: `ABA` keeps as `tier_T1`, `aba` drops as
`tier_T1_case_mismatch:ABA`, `water` keeps in "water deficit" and drops in
"rinsed in water", `root` keeps in "Root architecture" and drops in "the root of
the problem", `rice` keeps in "the rice genome".
