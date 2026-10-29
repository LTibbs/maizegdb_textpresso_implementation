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
    --chebi-full chebi.obo --derive-trait-stems --outdir build/
# or, so quarterly refreshes reapply it automatically:
python3 scripts/refresh_ontologies.py --annotation-build \
    --collection maize-ontology-collection --chebi-full chebi.obo \
    --annotation-outdir build/
```

Measured output of the build as shipped (2026-10-22, with
`--derive-trait-stems`): **188,114 match strings over 59,963 matchable terms**
— 184,086 T0, 3,673 T1, 347 T2, 8 TD; by kind 59,963 labels, 70,442 EXACT
synonyms, 57,374 RELATED, 328 derived trait stems, 7 curated abbreviations.
Built from `maize-ontology-collection-2026-10-20.tar.gz` (all 34 files match
its `MANIFEST.csv`) and ChEBI release 256; every channel's source version is
the same as the 2026-10-20 build's. That build is kept at
`.data/backups/annotation_build-20261020/` in `agr_textpresso`.

| channel | matchable terms | strings | version built from |
|---|---|---|---|
| GO | 38,092 | 118,623 | releases/2026-07-26 |
| ChEBI | 7,240 | 42,928 | 256 |
| SO | 2,409 | 6,561 | 2026-08-07 |
| NCBITaxon | 2,680 | 5,038 | 2026-07-12 (pruned) |
| PO | 1,659 | 3,364 | releases/2026-01-09 |
| ECO | 2,217 | 3,088 | releases/2026-07-10 |
| TO | 1,587 | 2,846 | releases/2026-01-14 |
| PATO | 1,886 | 2,758 | releases/2025-05-14 |
| CO_322 | 921 | 1,118 | cropontology-live/2026-09-22 |
| PSO | 754 | 994 | releases/2026-01-08 |
| PECO | 518 | 796 | releases/2025-12-02 |

Drop accounting, from `build_report.json`: 211,771 ChEBI stanzas out of scope,
9,134 imported stanzas (PSO 4,145, TO 2,979, PECO 2,010), 2,557 non-English
synonyms, 935 non-maize species-scoped synonyms, 531 trait codes, 294 strings
blocked at T3. GO's `gocheck_do_not_annotate` terms are no longer dropped
(decision 2). PO is smaller than in the 2026-10-20 build (6,438) because that
build still carried the unmatchable `"ear (related)"`-style and non-English
strings that 5a removes.

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

## 2 · GO — every live term, do-not-annotate classes included

All 37,633 live terms, the 459 in `gocheck_do_not_annotate` included
(2026-10-22). That subset is GO's flag for classes no *curator annotation* may
target: roots, grouping classes and over-general terms. Excluding it from text
matching removed 1,314 strings no other term carries, 95,277 MaizeOpenAccess
mentions. Most were generic (`growth` 23,504, `biosynthesis`, `synthesis`,
`metabolism`, `transport`), but `cell wall` (5,050 in 708 papers), `cell
death`, `embryogenesis`, `cell cycle` and the meiotic stages (`pachytene`,
`prophase`, ...) went with them.

The exclusion costs more than it saves once the category depth is 50 (see
*Category depth in tpso* below): a do-not-annotate class then becomes a
category only when it is the matched term, and hygiene's ancestor collapse drops
it whenever a more specific term covers the same span. The tier gate catches
only some of the generic words: `growth` (Zipf 5.0) is T2, `biosynthesis` and
`localization` T1, but `synthesis`, `metabolism`, `transport` and
`degradation` are T0 and match freely, as themselves. That is the accepted
cost: about 30,000 mentions that are correct but uninformative. The
`gocheck_do_not_annotate` subset line stays on the stanza, so anything that
wants to treat these terms differently still can. The sibling flag
`gocheck_do_not_manually_annotate` does not exist in `go-basic.obo`.

GO precision is handled **after** annotation, by `annotation_hygiene.py`
(sections, ancestor collapse) and the tier gate — consistent with
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
traverses out-of-scope ChEBI parents while those contribute no match strings.
A stub keeps its source `is_a` (closed transitively, so depths stay right), its
source subsets, and its label as `property_value: stub_label`, which tpso uses
as the category name. 1,290 stubs in ChEBI and 63 in TO in the 2026-10-20
build, a handful elsewhere; GO's 219 were the do-not-annotate parents and are
full stanzas since decision 2 changed. All eleven files parse clean through
`pronto`.

**`part_of` is kept in GO, PO and TO** (2026-10-29, `PART_OF_CHANNELS`). Until
then only `is_a` was written, so "include narrower terms" could never reach a
term's parts: `nucleus` expanded to 19 terms instead of 468 (`nuclear
envelope`, `nucleolus`, ...), `leaf` to 24 instead of 149. The edges added
were GO 7,058, PO 745 and TO 85, all to terms already in the files, so no new
stubs were needed. Stubs keep their own `part_of` as well. `has_part`,
`regulates` and the rest stay out, because they don't mean "narrower".
What this touches:

- **tpso** reads `relationship: part_of` as a parent edge
  (`OboFileSegmentation.cpp`). At depth 50 every category is still its own ID
  (see "Category depth" below), so no match string or category label changes
  and no re-index is needed. Its per-path tree grows: GO 231k to 439k nodes,
  PO 2.7k to 11k, TO 4.6k to 5.1k.
- **Hygiene** ancestor collapse walks every relationship type, so `part_of`
  adds 12 same-span pairs across GO/PO/TO, almost all animal terms. The one
  plant case keeps `photosynthetic membrane` over `thylakoid`.
- **Search size.** Expansions get larger: `organelle` goes to 1,876 terms.
  Lucene's default limit of 1,024 clauses rejected larger searches with
  HTTP 500, so `libtpc` now raises it (`MAX_QUERY_CLAUSES`). **Rebuild and
  deploy the API with that change before** these OBOs reach the sidecar.
  Otherwise the GUI, which expands along every relationship a term has,
  fails on large terms.

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
| TD | 8 | curated abbreviation (`DEFINED_ABBREVIATIONS`) | stored case, and the paper body must spell the term out |
| T3 | 284 blocked | ≤ 2 characters | never in the dictionary; id-only |

(Counts are the 2026-10-20 build; the 2026-10-22 build is 184,086 / 3,673 /
347 / 8 / 294.)

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

**TD — abbreviations the paper must define** (2026-10-22). `GA`, `JA`, `SA`,
`BR`, `CK` and `ET` as the hormones, `ER` as the endoplasmic reticulum and `E3`
as ubiquitin protein ligase. All are ≤ 2 characters, so the T3 rule would block
them, and for the hormones no source ontology has the abbreviation at all. GO
did match `GA` as an acylase (3,152 mentions) and `CK` as a kinase (1,573).
`DEFINED_ABBREVIATIONS` adds each to one term: the hormones to ChEBI (as RELATED
synonyms, which also brings the term in when it lies outside the ChEBI scope),
`ER` by re-tiering GO's own EXACT synonym, and `E3` to GO:0061630, where GO has
it only as BROAD. Each entry carries long-form regexes, frozen into
`match_dictionary.csv`'s `long_forms` column.

At runtime a TD mention needs its stored case and is kept only if a long form
occurs in the paper **body**: `casannot.body_text()`, the sentences outside
references and acknowledgments. The test is paper-level because an abbreviation
is defined once and then used bare. Excluding references stops a cited author
`Smith GA` from licensing itself. The competing senses this separates are
measured in MaizeOpenAccess: `CK` is defined as the *control check* in 35
papers and as cytokinin in 27; `BR` is brace root in 7; `ET` collides with `ET
AL` running heads and `polyethylene glycol` (hence `\bethylene\b`). Over the
corpus, share of body mentions kept: JA 95%, BR 89%, SA 83%, GA 81%, ER 78%, E3
71%, ET 45%, CK 37%. The drops sampled were affiliations (`Athens, GA`), author
initials in unlabelled reference lists, running heads, `CK` as control, exon
labels `E1/E2/E3` and sequence fragments. The cost is the occasional paper that
writes `GA signaling` or `E3 ligase` without ever spelling the term out.
Without `body_text=` a TD mention is kept and flagged
`tier_TD_body_unavailable`.

The build warns, and records `curated_abbreviations_missing` in
`build_report.json`, when a listed term is absent or obsolete in the source.

**Case and inflection at runtime.** A lowercase stored form also accepts its
sentence-case spelling (`Chromosome`, `Pollen` at the start of a sentence); a
stored form that starts uppercase needs its exact case, which is what drops
`Kit` and `Ltd` (stored `KIT`, `LTD`). Textpresso's matcher inflects (`roots`
matches `root`) but the dictionary does not, so a plural is judged as its
singular — before this, 182k of 349k unknown-form rows over MaizeOpenAccess
were plurals that bypassed the gate.

**Core plant-structure words are T0 in PO** (`CHANNEL_FREE_FORMS`): `leaf`,
`root`, `seed`, `stem`, `silk`, `bud`, `ear`, `kernel`, `husk`, `cob`,
`tassel`. Frequency alone puts them in T2, and the gate then dropped 14,771
`leaf`, 7,066 `root` and 6,914 `seed` mentions that were the plant structure.
Only in PO: `root` as `NCBITaxon:1` stays T2. Genuine homonyms (`style`, `coma`,
`keel`, `bulb`, `bark`) are deliberately not listed.

**What tiering does not fix:** a homonym stored in exactly the case papers use
for the other sense (`SSR` is simple sequence repeat in a maize paper and
subsynaptic reticulum in GO). Those are stop-forms:
`annotation_hygiene.DEFAULT_STOP_FORMS`, applied by default, matched
case-sensitively (plurals included), and scoped to the ontologies where the
sense is wrong, so `TEM` still matches ECO's microscopy evidence and `tRNA`
SO's tRNA. 18 forms, what was left of a 62-form September list once the
≤ 2-character block, the case test, T2 context and depth-50 categories had
taken the rest (2026-10-22): ACS, CAR, DMP, FPC, GCA, ISR, LTP, MCS, NAC, NLB,
PCC, PHT, PPI, SSR, TEM, VIGS, pathogenesis, tRNA. `SOD`, `CDK` and `PFK` were
on that list and are deliberately not: in MaizeOpenAccess they are the enzymes
GO matches. A drop's reason is `stop_form:<form>`; `stop_forms={}` disables the
list. Tiering and stop-forms are complementary; neither subsumes the other.

**Which changes need a re-index.** Tiers live only in `match_dictionary.csv`
and are read by the runtime gate, so a tier change takes effect on a sidecar
restart. A change to *which strings exist* changes the `*_annot.obo` files and
reaches search and the CAS2 annotations only after the tpso lexicon is rebuilt
and the corpus re-tokenized, re-annotated and re-indexed.

## 5a · Scope written inside synonym strings

PO, TO and PECO write the synonym scope — and for PO the language — inside the
quoted string: `synonym: "ear (related)" RELATED []`, `"oreja de la
inflorescencia (Spanish, exact)" EXACT Spanish [...]`. Read literally these can
never match text: 1,653 of 1,655 TO synonyms and 2,668 of 5,605 PO synonyms in
the 2026-10 releases, including PO's `ear`, `kernel`, `husk`, `leaf blade` and
`brace root`. `clean_synonym_text()` strips the suffix; a synonym tagged with a
language other than English is dropped (2,557 in PO). An abbreviation that only
became matchable through the cleaning is dropped as a database trait code
(`PNWT`, `LFLG`, `CANTMP` — 531, nearly all TO), since authors do not write them
and some collide with real text (`KUP`, `LAD`).

`--derive-trait-stems` (off by default in the script, used for the shipped
build since 2026-10-22) also matches multi-word TO names
without their ` trait` suffix (`flowering time trait` → `flowering time`, 328
strings) on the RELATED channel. Measured against the curator trait lists for
MaizeOpenAccess, lexicon coverage of curated trait occurrences is 14.0% for the
2026-10-20 build, 16.8% with the suffix cleaning and 21.3% with derived stems.

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

TIERS = TierIndex.from_csv(os.environ.get(
    "TEXTPRESSO_MATCH_DICTIONARY",
    "/data/textpresso/annotation_build/match_dictionary.csv.gz"))

kept, dropped = clean_annotations(
    annotations, sections,
    section_types_at=casannot.section_types_at,
    ontology_view=view,
    tiers=TIERS, tier_rule=tier_rule,
    sentence_at=casannot.sentence_lookup(sentences),
    body_text=casannot.body_text(sentences, sections, DEFAULT_DROP_SECTIONS),
)
```

The tier gate is **on by default** since 2026-10-22, in `cas_annotate_server.py`
and in the CLI's `--cas-root` path: both load the annotation build's dictionary
when it exists. `TEXTPRESSO_MATCH_DICTIONARY` names another file, `0` turns the
gate off, and `tiers=0` skips it for one request. Memory cost of the index is
the CSV itself — 23 MB on disk, ~180k distinct forms.

`casannot._ontology_of()` labels the new channels `CHEBI`, `PATO`, `PECO`,
`PSO`, `SO`, `ECO`, `CO_322` and `NCBITaxon` (before 2026-10-22 they all read
as `OTHER`, which the tier gate and the stop-forms never judge), and the
category-id regexes accept the underscore in `CO_322:`.

## Re-indexing with the annotation build

tpso reads **every** `.obo` file in `obofiles4production/`; `ontology.conf`
only sets each file's depth and subsets. So moving the build in is the
re-index, and the raw GO/PO/TO files have to come out at the same time or GO,
PO and TO are matched twice and the raw `go.obo` restores everything the build
dropped (`GA` as an acylase, the `(related)` strings, ...):

1. Back up `obofiles4production/` (`.data/backups/`).
2. Move `go.obo`, `po.obo` and `to.obo` out of `obofiles4production/`, and
   `drop table` their `tpontology_<stem>_0` and `pcrelations_<stem>` tables:
   `annotate` unions whatever per-ontology tables exist, so moving the file
   alone is not enough (Laura_work_updates_log, 2026-08-12 GDR entry).
3. Copy the eleven `*_annot.obo` from `.data/annotation_build/` in, beside
   `zmays_genes_20260708.obo`.
4. Rebuild the lexica (`CreateLexica.bash`) with the depth-50
   `ontology.conf`/`tpsoinput.json`, then re-tokenize, re-annotate and
   re-index the corpus.
5. Restart the sidecar so it reads the matching `match_dictionary.csv.gz`.

## Category depth in tpso: every term is its own category

`tpso` assigns each lexicon row a *category*, and that category, not the
matched term, is what an annotation records. It is the term's ancestor at the
`ontology.conf` depth, taken separately along every path through the DAG, and
optionally rolled up to the nearest term in a listed subset (GO slims). At the
old settings (GO 6 + slims, PO/TO 8, genes 3, others 5) a single "flowering"
was filed under several coarse ancestors and never under the concept that
matched, and every `chromosome` was filed under `intracellular membraneless
organelle` (31,669 mentions) although `chromosome` is itself in three slims.
This coarsening is the source of the ancestor-propagation rows, and it happens
at index time, where no response-layer collapse can undo it.

So `textpressocentral/etc/ontology.conf` lists every channel at depth 50 with
no subsets, and `tpsoinput.json`'s default depth is 50. Measured on the eleven
channels plus genes (2026-10-21): 100% of rows have the matched term as their
category (GO 0.1% with slims); GO/PO/TO carry 1.00 concepts per span (PO was
1.42); CAS size +15%, from the fuller ancestor links. Ancestors are still
present as `PTCAT` annotations for category search, and are expanded at query
time. Do not put GO slims back on the GO line: rolling up to slims sent
"pigmentation" to `biological_process`.

`build_match_dictionary.py` still carries source subsets (`goslim_*`) onto the
`*_annot.obo` stanzas, and labels pruned parent stubs (`property_value:
stub_label`), so slims remain available to anything that wants them as facets.

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
