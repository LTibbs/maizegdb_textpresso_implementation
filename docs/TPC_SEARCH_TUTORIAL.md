# Textpresso programmatic search tutorial

A walkthrough of the Textpresso search CLI. Every command below
was run against the live server and its real output is shown
(long outputs are trimmed with `...` where noted). For the full reference for the python helper script, see [TPC_SEARCH_GUIDE.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/TPC_SEARCH_GUIDE.md); for the raw HTTP API underneath it (no Python required), see
[TPC_API_GUIDE.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/TPC_API_GUIDE.md). For the python script, see [tpc_search_combined.py](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/bin/tpc_search_combined.py)

Each example also shows the equivalent API call (`curl`), for callers
not using Python. These use three base URLs, exported once for the rest of
the tutorial:

```bash
export BASE="http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api"
export ANNOTATE="http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/annotate"
export CATSEARCH="http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/category_search"
```

## 1. What corpora exist?

Before searching anything, find out what corpus names (`-c`) are valid.

```bash
python3 bin/tpc_search_combined.py --list-corpora
```

```
ReproTest062
MaizeTest
SorghumBase
MaizeTest100
MaizeOA
GDR
GrainGenes
```

**API equivalent:**

```bash
curl -s "$BASE/available_corpora"
```

```json
["ReproTest062","MaizeTest","SorghumBase","MaizeTest100","MaizeOA","GDR","GrainGenes"]
```

`MaizeTest100` (100 papers) is used for most examples below since it's big
enough to give real results but small enough to read through.

## 2. Keyword search

The simplest possible search: one corpus, one keyword. With no `--type`,
the default is `sentence`, which returns the actual matching sentences from the full paper text.

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 "anthocyanin" --count 2
```

```
[1] Paulsmeyer MN, Juvik JA. (2023). Increasing aleurone layer number and pericarp yield for elevated nutrient content in maize.. G3 (Bethesda). [10.1093_g3journal_jkad085]
  - In the blue corn population, anthocyanin content increased 20 to 30% with the addition of MALs demonstrating its effectiveness at increasing aleurone yield
  - Results of this study may assist plant breeders enhancing anthocyanin content and other beneficial phytonutrients in maize
  ...

[2] Hu C, Li Q, Shen X, Quan S, Lin H, Duan L, Wang Y, Luo Q, Qu G, Han Q, Lu Y, Zhang D, Yuan Z, Shi J. (2016). Characterization of factors underlying the metabolic shifts in developing kernels of colored maize.. Sci Rep. [10.1038_srep35479]
  - A remarkable up-regulation of anthocyanin and phlobaphene pathway distinguished SW93 from SW48, ...
  ...
```

Each result is a reference line (`author (year). title. journal. [accession]`)
followed by every matched sentence from that paper. `--count` caps how many
*documents* come back (default 50), not sentences — a single document can
contribute many matched sentences, which is why this output is long even
with `--count 2`. Also, note that "accession" is the doi of the paper, but with all "/" replaced with "_".

**API equivalent:** `corpora` nests inside `query`; `include_match_sentences`
is required to get sentence text back, and only valid for `type: "sentence"`.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"sentence","corpora":["MaizeTest100"],"keywords":"anthocyanin"},
       "count":2,"include_match_sentences":true}' \
  "$BASE/search_documents"
```

```json
[{"matched_sentences":["In the blue corn population, anthocyanin content increased 20 to 30% with the addition of MALs demonstrating its effectiveness at increasing aleurone yield","Results of this study may assist plant breeders enhancing anthocyanin content and other beneficial phytonutrients in maize", ...], "identifier": "...", "title": "...", ...}, ...]
```

The raw response has the same fields as the Python script's `--format json`
output (see step 10) plus `matched_sentences` — the CLI's text formatting
(the `[1] Author (year)...` / `- sentence` layout above) is done client-side,
not by the API.

## 3. Getting document citations only: `--type document`

If you just want to know which documents matched, not every matching
sentence, use `--type document`. This returns the citation for each matching document and no sentence text, making it much easier to scan.

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document "anthocyanin" --count 3
```

```
[1] Hu C, Li Q, Shen X, Quan S, Lin H, Duan L, Wang Y, Luo Q, Qu G, Han Q, Lu Y, Zhang D, Yuan Z, Shi J. (2016). Characterization of factors underlying the metabolic shifts in developing kernels of colored maize.. Sci Rep. [10.1038_srep35479]

[2] Paulsmeyer MN, Juvik JA. (2023). Increasing aleurone layer number and pericarp yield for elevated nutrient content in maize.. G3 (Bethesda). [10.1093_g3journal_jkad085]

[3] Colombo F, Pagano A, Sangiorgio S, Macovei A, Balestrazzi A, Araniti F, Pilu R. (2023). Study of Seed Ageing in <i>lpa1-1</i> Maize Mutant and Two Possible Approaches to Restore Seed Germination.. Int J Mol Sci. [10.3390_ijms24010732]
```

**API equivalent:** same shape, `type` changed to `"document"`, and
`include_match_sentences` dropped (it's invalid for non-`sentence` types).

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeTest100"],"keywords":"anthocyanin"},"count":3}' \
  "$BASE/search_documents"
```

```json
[
  {"year": "2016", "doc_type": "Journal_article", "score": 2.65517,
   "identifier": "MaizeTest100/10.1038_srep35479/10.1038_srep35479.tpcas",
   "title": "Characterization of factors underlying the metabolic shifts in developing kernels of colored maize.",
   "author": "Hu C, Li Q, Shen X, ...", "accession": "10.1038_srep35479", "journal": "Sci Rep"},
  {"year": "2023", "doc_type": "Journal_article", "score": 2.64723,
   "identifier": "MaizeTest100/10.1093_g3journal_jkad085/10.1093_g3journal_jkad085.tpcas",
   "title": "Increasing aleurone layer number and pericarp yield for elevated nutrient content in maize.",
   "author": "Paulsmeyer MN, Juvik JA.", "accession": "10.1093_g3journal_jkad085", "journal": "G3 (Bethesda)"},
  {"year": "2023", "doc_type": "Journal_article",
   "identifier": "MaizeTest100/10.3390_ijms24010732/10.3390_ijms24010732.tpcas",
   "title": "Study of Seed Ageing in <i>lpa1-1</i> Maize Mutant and Two Possible Approaches to Restore Seed Germination.",
   "author": "Colombo F, Pagano A, ...", "accession": "10.3390_ijms24010732", "journal": "Int J Mol Sci"}
]
```

Most of the remaining examples use `--type document` for readability — the
same flags all work identically with `--type sentence` (you just get
sentence text back too).

## 4. Using category search

`--category` restricts results to papers annotated with a specific
term, which includes not only GO/TO/PO ontologies but also gene IDs (currently implemented for maize).

Unlike keywords, this must be the *exact* stored `"name (ID)"` string
— a bare word like `"seed"` is rejected. Look up category IDs first with
`bin/tpc_category_search.py`:

```bash
python3 bin/tpc_category_search.py "seed" --limit 3
```

```
Categories matching "seed":

  seed (PO:0009010)             [PO, exact]
  seed abscission (GO:0097548)  [GO, name_prefix]
  seed chalaza (PO:0006333)     [PO, name_prefix]

Example:
  python3 bin/tpc_search_combined.py -c <corpus> --category "seed (PO:0009010)" "<keywords>"
```

**API equivalent** of the lookup, using the `category_search` sidecar
endpoint (a separate service from `search_documents`, see
[TPC_API_TUTORIAL.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/TPC_API_TUTORIAL.md#4-category-lookup-get-v1textpressocategory_search)):

```bash
curl -s -G "$CATSEARCH" --data-urlencode "q=seed" --data-urlencode "limit=3"
```

```json
{
  "query": "seed",
  "matches": [
    {"id": "PO:0009010", "name": "seed", "category": "seed (PO:0009010)", "ontology": "PO", "matched_on": "exact"},
    {"id": "GO:0097548", "name": "seed abscission", "category": "seed abscission (GO:0097548)", "ontology": "GO", "matched_on": "name_prefix"},
    {"id": "PO:0006333", "name": "seed chalaza", "category": "seed chalaza (PO:0006333)", "ontology": "PO", "matched_on": "name_prefix"}
  ]
}
```

Then use the exact string as `--category`:

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document --category "seed (PO:0009010)" "development" --count 3
```

```
[1] Colombo F, Pagano A, Sangiorgio S, Macovei A, Balestrazzi A, Araniti F, Pilu R. (2023). Study of Seed Ageing in <i>lpa1-1</i> Maize Mutant and Two Possible Approaches to Restore Seed Germination.. Int J Mol Sci. [10.3390_ijms24010732]

[2] Amin F, Shah F, Ullah S, Shah W, Ahmed I, Ali B, Khan AA, Malik T, Mustafa AEMA. (2024). The germination response of Zea mays L. to osmotic potentials across optimal temperatures via halo-thermal time model.. Sci Rep. [10.1038_s41598-024-53129-6]

[3] Wu Y, Fox TW, Trimnell MR, Wang L, Xu RJ, Cigan AM, Huffman GA, Garnaat CW, Hershey H, Albertsen MC. (2016). Development of a novel recessive genetic male sterility system for hybrid seed production in maize and other cross-pollinating crops.. Plant Biotechnol J. [10.1111_pbi.12477]
```

**API equivalent:** `categories` is a list field inside `query`. Unlike
`--category`'s CLI-side validation (which blocks a bad value before ever
calling this endpoint), the raw API does no validation itself — it just
runs whatever string you give it, silently matching however that string
happens to compare, so always confirm the string via `category_search`
first.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeTest100"],
       "keywords":"development","categories":["seed (PO:0009010)"]},"count":3}' \
  "$BASE/search_documents"
```

```json
[
  {"year": "2023", "score": 2.68536, "identifier": "MaizeTest100/10.3390_ijms24010732/10.3390_ijms24010732.tpcas",
   "title": "Study of Seed Ageing in <i>lpa1-1</i> Maize Mutant and Two Possible Approaches to Restore Seed Germination.",
   "author": "Colombo F, Pagano A, ...", "accession": "10.3390_ijms24010732", "journal": "Int J Mol Sci"},
  {"year": "2024", "score": 2.65241, "identifier": "MaizeTest100/10.1038_s41598-024-53129-6/10.1038_s41598-024-53129-6.tpcas",
   "title": "The germination response of Zea mays L. to osmotic potentials across optimal temperatures via halo-thermal time model.",
   "author": "Amin F, Shah F, ...", "accession": "10.1038_s41598-024-53129-6", "journal": "Sci Rep"},
  {"year": "2016", "score": 2.63695, "identifier": "MaizeTest100/10.1111_pbi.12477/10.1111_pbi.12477.tpcas",
   "title": "Development of a novel recessive genetic male sterility system for hybrid seed production in maize and other cross-pollinating crops.",
   "author": "Wu Y, Fox TW, ...", "accession": "10.1111_pbi.12477", "journal": "Plant Biotechnol J"}
]
```

## 5. Ontology annotations with `--annotate`

So far every example has been plain search — keywords and `--category` both
*find* which papers match. `--annotate` does something different: given a
paper you've already identified (here, via `--accession`), it *describes*
that paper by appending a summary of every ontology term found anywhere in
it (GO, PO, TO, MAIZE_GENES), grouped by ontology — independent of what
keyword you searched for. It doesn't search for papers by annotation
content itself; for that, use `--category` (section 4 above).

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document --accession 10.1038_srep35479 --annotate --ontology TO
```

```
[1] Hu C, Li Q, Shen X, Quan S, Lin H, Duan L, Wang Y, Luo Q, Qu G, Han Q, Lu Y, Zhang D, Yuan Z, Shi J. (2016). Characterization of factors underlying the metabolic shifts in developing kernels of colored maize.. Sci Rep. [10.1038_srep35479]
  Ontology annotations:
    TO: heterosis
```

`--ontology TO` restricted the summary to just the Trait Ontology, for a
short example. Dropping `--ontology` returns every ontology's terms at
once — for this same paper that's a much longer block covering GO, PO, and
`MAIZE_GENES` too (dozens of terms — see
[TPC_SEARCH_GUIDE.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/TPC_SEARCH_GUIDE.md#--annotate--ontology-term-summary-per-paper)
for a full example).

**API equivalent:** the `annotate` endpoint takes an `identifier` (from a
prior search result) and returns raw `sentences`/`annotations`/`sections`
— there's no ready-made "summary" field. The CLI builds the grouped summary
client-side by grouping `annotations` by `ontology`; reproduce that with `jq`:

```bash
ID="MaizeTest100/10.1038_srep35479/10.1038_srep35479.tpcas"
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" --data-urlencode "ontology=TO" \
  | jq '.annotations | group_by(.ontology) | map({(.[0].ontology): ([.[].term] | unique)}) | add'
```

```json
{
  "TO": [
    "heterosis"
  ]
}
```

## 6. Gene search: keyword vs. EXACT vs. RELATED vs. category

As covered in the category search section above, available categories
include maize gene names. These categories are based on a curated OBO file
of published maize loci.

This functionality enables searching for loci with multiple synonyms. For
example, if a user wants to search for the locus `cct1`, a category search
for `"cct1 (tpzm:0010325)"` will return results not only for `cct1` but for
its curated locus synonyms `Zm00001eb418700` (B73 v5 gene ID),
`Zm00001d024909` (B73 v4 ID), `stiff2`, `ZmCCT10`, etc.

There are two types of synonyms: EXACT and RELATED. EXACT synonyms always
have a one-to-one relationship. RELATED synonyms do not: for example, a
given locus may have two associated IDs in a given genome version, or a
given abbreviation may have been used for more than one locus in the
literature.

By default, only EXACT synonyms are shown, but `--ontology MAIZE_GENES_RELATED` will show the RELATED ones.

The `cct` gene family (`cct1`, `cct9`, `cct10`, ...) is a good demonstration
case, because its entries have a lot of synonyms in the OBO
file, and its `ZmCCT`-prefixed aliases are common in the literature. The
walkthrough below compares two ways of actually *finding* papers about
`cct1` — a literal keyword search and a category search — and uses
`--annotate` in between to *inspect* why particular papers do or don't
count as an EXACT vs. RELATED match, the same distinction from section 5
above.

### Keyword search

Searching for the plain keyword `"cct1"` matches only papers whose text
contains that literal string:

```bash
python3 bin/tpc_search_combined.py --type document "cct1" --count 10
```

```
[1] Tibbs-Cortes LE, Guo T, Andorf CM, Li X, Yu J. (2024). Comprehensive identification of genomic and environmental determinants of phenotypic plasticity in maize.. Genome Res. [10.1101_gr.279027.124]

[2] Guo W, Wang F, Lv J, Yu J, Wu Y, Wuriyanghan H, Le L, Pu L. (2025). Phenotyping, genome-wide dissection, and prediction of maize root architecture for temperate adaptability.. Imeta. [10.1002_imt2.70015]
```

Just 2 papers, across every corpus. This is precise (both really do discuss
`cct1`) but it's also *only* as good as your knowledge of every way authors
might spell or alias the gene — miss a synonym like `ZmCCT` and you miss the
paper.

**API equivalent:** same shape as sections 2/3, just with `corpora` listing
every corpus (no `-c` was passed) instead of one:

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document",
       "corpora":["ReproTest062","MaizeTest","SorghumBase","MaizeTest100","MaizeOA","GDR","GrainGenes"],
       "keywords":"cct1"},"count":10}' \
  "$BASE/search_documents"
```

```json
[
  {"year": "2024", "identifier": "MaizeOA/10.1101_gr.279027.124/10.1101_gr.279027.124.tpcas",
   "title": "Comprehensive identification of genomic and environmental determinants of phenotypic plasticity in maize.",
   "accession": "10.1101_gr.279027.124", "journal": "Genome Res"},
  {"year": "2025", "identifier": "MaizeOA/10.1002_imt2.70015/10.1002_imt2.70015.tpcas",
   "title": "Phenotyping, genome-wide dissection, and prediction of maize root architecture for temperate adaptability.",
   "accession": "10.1002_imt2.70015", "journal": "Imeta"}
]
```

### Inspecting a hit with `--annotate`: is `cct1` an EXACT match here?

`--annotate --ontology MAIZE_GENES` doesn't find new papers — it describes
one you already have, showing the ontology's EXACT-synonym matches for it:
the gene mentions the OBO file recognizes as `cct1` itself, not a relative.
Checking one of the two keyword hits confirms `cct1` is tagged there as an
EXACT match, alongside `cct1`'s other exact aliases (`constans1`, `conz1`,
`cry3`, `gigantea1`, ...) picked up in the same paper:

```bash
python3 bin/tpc_search_combined.py --type document --accession 10.1101_gr.279027.124 --annotate --ontology MAIZE_GENES
```

```
[1] Tibbs-Cortes LE, Guo T, Andorf CM, Li X, Yu J. (2024). Comprehensive identification of genomic and environmental determinants of phenotypic plasticity in maize.. Genome Res. [10.1101_gr.279027.124]
  Ontology annotations:
    MAIZE_GENES: SCS, Zm00001eb023220, Zm00001eb353250, Zm00001eb380460, Zm00001eb382070, Zm00001eb418700, cct1, cct103, constans1, conz1, cry3, cryptochrome3, gigantea1, pebp8, phosphatidylethanolamine-binding protein8, vgt2
```

**API equivalent:** same `/annotate` + `jq` grouping pattern as section 5,
scoped to this paper's identifier (note the corpus is `MaizeOA` here, not
`MaizeTest100` — this search ran across every corpus):

```bash
ID="MaizeOA/10.1101_gr.279027.124/10.1101_gr.279027.124.tpcas"
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" --data-urlencode "ontology=MAIZE_GENES" \
  | jq '.annotations | group_by(.ontology) | map({(.[0].ontology): ([.[].term] | unique)}) | add'
```

```json
{
  "MAIZE_GENES": [
    "SCS", "Zm00001eb023220", "Zm00001eb353250", "Zm00001eb380460",
    "Zm00001eb382070", "Zm00001eb418700", "cct1", "cct103", "constans1",
    "conz1", "cry3", "cryptochrome3", "gigantea1", "pebp8",
    "phosphatidylethanolamine-binding protein8", "vgt2"
  ]
}
```

### Inspecting a hit with `--annotate`: RELATED synonyms instead

Same idea, different papers: these two are among the results the
`--category` search below returns, but neither one mentions `cct1`
literally, and neither has `cct1` as an EXACT match. `--annotate
--ontology MAIZE_GENES_RELATED` shows why they're still tagged with the
`cct1` category — each mentions a curated synonym that's used for this
gene but also has some ambiguity. For example, `ZmCCT`, `ZmCCT9`, `ZmCCT10`
are found in the literature as RELATED synonyms of `cct1`, but aren't
one-to-one EXACT synonyms the way `--annotate --ontology MAIZE_GENES`
requires:

```bash
python3 bin/tpc_search_combined.py --type document --accession 10.1093_nar_gkac1195 --annotate --ontology MAIZE_GENES_RELATED
```

```
[1] Chen G, Wang R, Jiang Y, Dong X, Xu J, Xu Q, Kan Q, Luo Z, Springer NM, Li Q. (2023). A novel active transposon creates allelic variation through altered translation rate to influence protein abundance.. Nucleic Acids Res. [10.1093_nar_gkac1195]
  Ontology annotations:
    MAIZE_GENES_RELATED: NAC, ZmCCT, ZmCCT10, ZmCCT9, ZmDREB1D, ZmVPP1, hexose transporter, pentatricopeptide repeat protein, transmembrane protein
```

**API equivalent:**

```bash
ID="MaizeOA/10.1093_nar_gkac1195/10.1093_nar_gkac1195.tpcas"
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" --data-urlencode "ontology=MAIZE_GENES_RELATED" \
  | jq '.annotations | group_by(.ontology) | map({(.[0].ontology): ([.[].term] | unique)}) | add'
```

```json
{
  "MAIZE_GENES_RELATED": [
    "NAC", "ZmCCT", "ZmCCT10", "ZmCCT9", "ZmDREB1D", "ZmVPP1",
    "hexose transporter", "pentatricopeptide repeat protein", "transmembrane protein"
  ]
}
```

```bash
python3 bin/tpc_search_combined.py --type document --accession 10.1093_plcell_koae090 --annotate --ontology MAIZE_GENES_RELATED
```

```
[1] Romero JM, Serrano-Bueno G, Camacho-Fernández C, Vicente MH, Ruiz MT, Pérez-Castiñeira JR, Pérez-Hormaeche J, Nogueira FTS, Valverde F. (2024). CONSTANS, a HUB for all seasons: How photoperiod pervades plant physiology regulatory circuits.. Plant Cell. [10.1093_plcell_koae090]
  Ontology annotations:
    MAIZE_GENES_RELATED: CCT, P1, P2, PIF4, ZmCCT10, bZIP
```

**API equivalent:**

```bash
ID="MaizeOA/10.1093_plcell_koae090/10.1093_plcell_koae090.tpcas"
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" --data-urlencode "ontology=MAIZE_GENES_RELATED" \
  | jq '.annotations | group_by(.ontology) | map({(.[0].ontology): ([.[].term] | unique)}) | add'
```

```json
{
  "MAIZE_GENES_RELATED": ["CCT", "P1", "P2", "PIF4", "ZmCCT10", "bZIP"]
}
```

Note the tradeoff visible in that second list: `P1` and `P2` are exactly the
kind of short, generic-looking RELATED synonym that's likely to be noise
rather than a genuine gene mention (see "Generic-word contamination" in
[TPC_SEARCH_GUIDE.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/TPC_SEARCH_GUIDE.md#other-notes)) — RELATED trades
precision for recall, so treat its hits as candidates to verify, not
confirmed mentions the way EXACT hits are.

### `--category`: searching by the ontology category itself

`--category "cct1 (tpzm:0010325)"` searches on the category directly, with
no keyword at all — this is the search described above, and, unlike
`--annotate`, it's a genuine paper-finding mechanism like keyword search,
just a broader one. By default it matches EXACT `cct1` synonyms only —
the same EXACT/RELATED distinction from the `--annotate` subsections
above, now applied to search itself:

```bash
python3 bin/tpc_search_combined.py --type document --category "cct1 (tpzm:0010325)" --count 200
```

```
[1] Liu Y, Guo Y, Ma C, Zhang D, Wang C, Yang Q. (2016). Transcriptome analysis of maize resistance to Fusarium graminearum.. BMC Genomics. [10.1186_s12864-016-2780-5]

[2] Tibbs-Cortes LE, Guo T, Andorf CM, Li X, Yu J. (2024). Comprehensive identification of genomic and environmental determinants of phenotypic plasticity in maize.. Genome Res. [10.1101_gr.279027.124]

[3] Wang Q, Zhao Z, Li X, Gao X. (2025). The Involvement of Glycerophospholipids in Susceptibility of Maize to Gibberella Root Rot Revealed by Comparative Metabolomics and Mass Spectrometry Imaging Joint Analysis.. Plants (Basel). [10.3390_plants14091376]

...

[8] Schultes SR, Rüger L, Niedeggen D, Freudenthal J, Frindte K, Becker MF, Metzner R, Pflugfelder D, Chlubek A, Hinz C, van Dusschoten D, Bauke SL, Bonkowski M, Watt M, Koller R, Knief C. (2025). Photosynthate distribution determines spatial patterns in the rhizosphere microbiota of the maize root system.. Nat Commun. [10.1038_s41467-025-62550-y]

... (8 results total)
```

8 papers — more than the keyword search (2), because it also catches
`cct1`'s other EXACT aliases (`constans1`, `conz1`, `cry3`, `gigantea1`,
`stiff2`, ...) that a plain `"cct1"` keyword search would miss entirely,
but it does *not* include `10.1093_nar_gkac1195` or `10.1093_plcell_koae090`
from the RELATED example above — those only have a RELATED `cct1` match, so
they're correctly excluded by default.

**API equivalent: there isn't a simple one, and that's the point of the
note above.** The raw `search_documents` endpoint has no concept of
EXACT vs. RELATED for `--category` at all — it's a bare Lucene phrase
query that matches either kind indiscriminately, so calling it directly
still returns all 17, the same over-broad result the CLI used to have
before today's fix:

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document",
       "corpora":["ReproTest062","MaizeTest","SorghumBase","MaizeTest100","MaizeOA","GDR","GrainGenes"],
       "categories":["cct1 (tpzm:0010325)"]},"count":200}' \
  "$BASE/search_documents" > /tmp/raw_cat.json
jq 'length' /tmp/raw_cat.json
```

```
17
```

To reproduce the CLI's EXACT-only filtering from raw HTTP, you have to do
what `filter_gene_category_results()` in `tpc_search_combined.py` does:
check each result's real annotations via `/annotate` and keep only the ones
with an EXACT hit on this category's ID (`tpzm:0010325`):

```bash
for id in $(jq -r '.[].identifier' /tmp/raw_cat.json); do
  has_exact=$(curl -s -G "$ANNOTATE" --data-urlencode "identifier=$id" --data-urlencode "ontology=MAIZE_GENES" \
    | jq '[.annotations[] | select(.onto_id=="tpzm:0010325")] | length > 0')
  [ "$has_exact" = "true" ] && jq -r --arg id "$id" '.[] | select(.identifier==$id) | .accession' /tmp/raw_cat.json
done
```

```
10.1186_s12864-016-2780-5
10.1101_gr.279027.124
10.3390_plants14091376
10.1002_imt2.70015
10.1007_s00122-022-04239-0
10.1080_15592324.2025.2502739
10.1016_j.jare.2024.10.024
10.1038_s41467-025-62550-y
```

Same 8 papers as the CLI, but this needs one `/annotate` call per raw
result (17 extra HTTP round-trips here) — the CLI does the same thing
internally, at the same cost. There's no server-side shortcut yet.

Add `--related-synonyms` to widen the search to include RELATED matches
too — this is the flag that actually matters for `--category`, not just for
`--annotate`:

```bash
python3 bin/tpc_search_combined.py --type document --category "cct1 (tpzm:0010325)" --related-synonyms --count 200
```

```
[1] Liu Y, Guo Y, Ma C, Zhang D, Wang C, Yang Q. (2016). Transcriptome analysis of maize resistance to Fusarium graminearum.. BMC Genomics. [10.1186_s12864-016-2780-5]

[2] Tibbs-Cortes LE, Guo T, Andorf CM, Li X, Yu J. (2024). Comprehensive identification of genomic and environmental determinants of phenotypic plasticity in maize.. Genome Res. [10.1101_gr.279027.124]

...

[6] Chen G, Wang R, Jiang Y, Dong X, Xu J, Xu Q, Kan Q, Luo Z, Springer NM, Li Q. (2023). A novel active transposon creates allelic variation through altered translation rate to influence protein abundance.. Nucleic Acids Res. [10.1093_nar_gkac1195]

...

[10] Romero JM, Serrano-Bueno G, Camacho-Fernández C, Vicente MH, Ruiz MT, Pérez-Castiñeira JR, Pérez-Hormaeche J, Nogueira FTS, Valverde F. (2024). CONSTANS, a HUB for all seasons: How photoperiod pervades plant physiology regulatory circuits.. Plant Cell. [10.1093_plcell_koae090]

... (17 results total)
```

**API equivalent:** with `--related-synonyms`, the CLI's filter accepts
either an EXACT or a RELATED match, which in practice ends up matching
everything the raw, unfiltered search already returns — so this is just
the plain `search_documents` call from the "raw" step above, no
`/annotate` follow-up needed:

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document",
       "corpora":["ReproTest062","MaizeTest","SorghumBase","MaizeTest100","MaizeOA","GDR","GrainGenes"],
       "categories":["cct1 (tpzm:0010325)"]},"count":200}' \
  "$BASE/search_documents" | jq 'length'
```

```
17
```

Now both `10.1093_nar_gkac1195` and `10.1093_plcell_koae090` — the same two
papers from the RELATED `--annotate` example above — are back in the list.
In the OBO data behind this corpus, `"ZmCCT10"` is deliberately listed as a
RELATED synonym under both `cct10` and `cct1` (this synonym has been used
in the literature for both loci), so any paper mentioning `ZmCCT10` is
correctly categorized under *both* genes once RELATED matches are included.
This is intentional shared-synonym behavior, not a data error: RELATED
synonyms are meant to tag every locus they're related to.

> **Note:** as of 2026-08-17, `--category`'s EXACT-vs-RELATED filtering is
> implemented as a client-side check in `tpc_search_combined.py`
> (`filter_gene_category_results()`), not in the search backend itself —
> the underlying Lucene query can't natively tell EXACT and RELATED matches
> apart. This works correctly for the cases shown here, but adds one extra
> lookup per result and only applies to gene categories (`tpzm:`/`tpzma:`
> IDs) — GO/PO/TO categories have no EXACT/RELATED distinction to begin
> with.

### Summary

Two of these actually find papers; the other two just describe why a
paper you already have counts as a match. Both the finding and describing
sides split further into EXACT (default) vs. RELATED (`--related-synonyms`):

| | Finds/describes | Precision | Recall |
|---|---|---|---|
| **Keyword search** (`"cct1"`) — *finds papers* | Papers with that literal string in the text | High | Low — misses every alias you didn't think to search for |
| **`--category "cct1 (tpzm:0010325)"`** (EXACT, default) — *finds papers* | Every paper with an EXACT `cct1` synonym | Higher than keyword search's aliasing gap | Medium — misses RELATED-only mentions |
| **`--category ... --related-synonyms`** (RELATED included) — *finds papers* | Adds papers whose only match is a shared RELATED synonym with a sibling locus | Lower — includes generic-looking terms | Highest of the four — the broadest search |
| `--annotate --ontology MAIZE_GENES` (EXACT) — *describes a paper* | Confirms the ontology recognizes an exact `cct1` synonym in that paper | High | — (not a search) |
| `--annotate --ontology MAIZE_GENES_RELATED` (RELATED) — *describes a paper* | Shows which close relative in the `cct1` family (`ZmCCT9`, `ZmCCT10`, ...) is driving the match | Lower — includes generic-looking terms | — (not a search) |

## 7. Restricting to a section of the paper

Instead of `sentence`/`document`, `--type` can name a paper section —
`abstract`, `introduction`, `result`, `discussion`, `conclusion`,
`background`, `design`, `materials and methods`, `acknowledgments`, or
`references` — to only match within that section.

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type abstract "drought" --count 2
```

```
[1] Sheng L, Chai W, Gong X, Zhou L, Cai R, Li X, Zhao Y, Jiang H, Cheng B. (2015). Identification and Characterization of Novel Maize Mirnas Involved in Different Genetic Background.. Int J Biol Sci. [10.7150_ijbs.11619]

[2] Ali Q, Sami A, Haider MZ, Ashfaq M, Javed MA. (2024). Antioxidant production promotes defense mechanism and different gene expression level in Zea mays under abiotic stress.. Sci Rep. [10.1038_s41598-024-57939-6]
```

**API equivalent:** `type` becomes the section name directly.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"abstract","corpora":["MaizeTest100"],"keywords":"drought"},"count":2}' \
  "$BASE/search_documents"
```

```json
[
  {"year": "2015", "score": 2.98306, "identifier": "MaizeTest100/10.7150_ijbs.11619/10.7150_ijbs.11619.tpcas",
   "title": "Identification and Characterization of Novel Maize Mirnas Involved in Different Genetic Background.",
   "author": "Sheng L, Chai W, ...", "accession": "10.7150_ijbs.11619", "journal": "Int J Biol Sci"},
  {"year": "2024", "score": 2.95921, "identifier": "MaizeTest100/10.1038_s41598-024-57939-6/10.1038_s41598-024-57939-6.tpcas",
   "title": "Antioxidant production promotes defense mechanism and different gene expression level in Zea mays under abiotic stress.",
   "author": "Ali Q, Sami A, ...", "accession": "10.1038_s41598-024-57939-6", "journal": "Sci Rep"}
]
```

## 8. Filtering by metadata instead of (or alongside) keywords

`--author`, `--journal`, `--year`, `--accession`, and `--paper-type` filter
on bibliographic metadata. They can be combined with keywords or used
alone — at least one of keywords/`--author`/`--journal`/`--year`/
`--accession`/`--category` is required.

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document --author "Buckler" --year 2014
```

```
[1] Owens BF, Lipka AE, Magallanes-Lundback M, Tiede T, Diepenbrock CH, Kandianis CB, Kim E, Cepela J, Mateos-Hernandez M, Buell CR, Buckler ES, DellaPenna D, Gore MA, Rocheford T. (2014). A foundation for provitamin A biofortification of maize: genome-wide association and genomic prediction models of carotenoid levels.. Genetics. [10.1534_genetics.114.169979]
```

**API equivalent:** `author` and `year` are plain fields inside `query`, no
keywords needed.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeTest100"],"author":"Buckler","year":"2014"},"count":50}' \
  "$BASE/search_documents"
```

```json
[
  {"year": "2014", "score": 4.97372, "identifier": "MaizeTest100/10.1534_genetics.114.169979/10.1534_genetics.114.169979.tpcas",
   "title": "A foundation for provitamin A biofortification of maize: genome-wide association and genomic prediction models of carotenoid levels.",
   "author": "Owens BF, Lipka AE, ... Buckler ES, ...", "accession": "10.1534_genetics.114.169979", "journal": "Genetics"}
]
```

## 9. Excluding keywords

`--exclude` drops results that also contain a given term. For example, this can be used to exclude other species such as Arabidopsis.

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document "kernel weight" --exclude "Arabidopsis" --count 3
```

```
[1] Qu J, Yu D, Gu W, Khalid MHB, Kuang H, Dang D, Wang H, Prasanna B, Zhang X, Zhang A, Zheng H, Guan Y. (2024). Genetic architecture of kernel-related traits in sweet and waxy maize revealed by genome-wide association analysis.. Front Genet. [10.3389_fgene.2024.1431043]

[2] Paulsmeyer MN, Juvik JA. (2023). Increasing aleurone layer number and pericarp yield for elevated nutrient content in maize.. G3 (Bethesda). [10.1093_g3journal_jkad085]

[3] Strigens A, Schipprack W, Reif JC, Melchinger AE. (2013). Unlocking the genetic diversity of maize landraces with doubled haploids opens new avenues for breeding.. PLoS One. [10.1371_journal.pone.0057234]
```

**API equivalent:** `exclude_keywords` is a separate field from `keywords`.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeTest100"],
       "keywords":"kernel weight","exclude_keywords":"Arabidopsis"},"count":3}' \
  "$BASE/search_documents"
```

```json
[
  {"year": "2024", "score": 2.93317, "identifier": "MaizeTest100/10.3389_fgene.2024.1431043/10.3389_fgene.2024.1431043.tpcas",
   "title": "Genetic architecture of kernel-related traits in sweet and waxy maize revealed by genome-wide association analysis.",
   "author": "Qu J, Yu D, ...", "accession": "10.3389_fgene.2024.1431043", "journal": "Front Genet"},
  {"year": "2023", "score": 2.90646, "identifier": "MaizeTest100/10.1093_g3journal_jkad085/10.1093_g3journal_jkad085.tpcas",
   "title": "Increasing aleurone layer number and pericarp yield for elevated nutrient content in maize.",
   "author": "Paulsmeyer MN, Juvik JA.", "accession": "10.1093_g3journal_jkad085", "journal": "G3 (Bethesda)"},
  {"year": "2013", "score": 2.86112, "identifier": "MaizeTest100/10.1371_journal.pone.0057234/10.1371_journal.pone.0057234.tpcas",
   "title": "Unlocking the genetic diversity of maize landraces with doubled haploids opens new avenues for breeding.",
   "author": "Strigens A, Schipprack W, ...", "accession": "10.1371_journal.pone.0057234", "journal": "PLoS One"}
]
```

## 10. JSON output for scripting

`--format json` switches to machine-readable output, so results can be piped
into `jq` or parsed in your own script.

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document "anthocyanin" --count 2 --format json
```

```json
[
  {
    "year": "2016",
    "doc_type": "Journal_article",
    "score": 2.65517,
    "identifier": "MaizeTest100/10.1038_srep35479/10.1038_srep35479.tpcas",
    "title": "Characterization of factors underlying the metabolic shifts in developing kernels of colored maize.",
    "author": "Hu C, Li Q, Shen X, Quan S, Lin H, Duan L, Wang Y, Luo Q, Qu G, Han Q, Lu Y, Zhang D, Yuan Z, Shi J.",
    "accession": "10.1038_srep35479",
    "journal": "Sci Rep"
  },
  {
    "year": "2023",
    "doc_type": "Journal_article",
    "score": 2.64723,
    "identifier": "MaizeTest100/10.1093_g3journal_jkad085/10.1093_g3journal_jkad085.tpcas",
    "title": "Increasing aleurone layer number and pericarp yield for elevated nutrient content in maize.",
    "author": "Paulsmeyer MN, Juvik JA.",
    "accession": "10.1093_g3journal_jkad085",
    "journal": "G3 (Bethesda)"
  }
]
```

**API equivalent:** none needed — `--format json` output *is* the API's raw
JSON response, unmodified (the same call as step 3's example). Pipe it
straight into `jq`:

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeTest100"],"keywords":"anthocyanin"},"count":3}' \
  "$BASE/search_documents" | jq -r '.[].title'
```

```
Characterization of factors underlying the metabolic shifts in developing kernels of colored maize.
Increasing aleurone layer number and pericarp yield for elevated nutrient content in maize.
Study of Seed Ageing in lpa1-1 Maize Mutant and Two Possible Approaches to Restore Seed Germination.
```

(Same command with `python3 bin/tpc_search_combined.py -c MaizeTest100 --type document "anthocyanin" --count 3 --format json | jq -r '.[].title'` gives identical output.)

## 11. Full sentence-level annotation with `--annotate-sentences`

For programmatic use, `--annotate-sentences` returns JSON with every
annotated sentence and its exact character offsets and ontology hits —
more detail than the `--annotate` summary, always as JSON regardless of
`--format`.

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document --accession 10.1038_srep35479 --annotate-sentences --ontology TO
```

```json
[
  {
    "paper": {
      "identifier": "MaizeTest100/10.1038_srep35479/10.1038_srep35479.tpcas",
      "title": "Characterization of factors underlying the metabolic shifts in developing kernels of colored maize.",
      "author": "Hu C, Li Q, Shen X, Quan S, Lin H, Duan L, Wang Y, Luo Q, Qu G, Han Q, Lu Y, Zhang D, Yuan Z, Shi J.",
      "year": "2016",
      "journal": "Sci Rep",
      "accession": "10.1038_srep35479"
    },
    "annotated_sentences": [
      {
        "begin": 1906,
        "end": 2194,
        "text": "mays L.) is of global significance not only as a food, feed and a source of diverse industrial\nproducts, but also as a model system with tremendous genetic diversity for general plant biology studies as well as\nfor specific biological phenomena such as heterosis and gene transposition1,2",
        "annotations": [
          {
            "begin": 2159,
            "end": 2168,
            "term": "heterosis",
            "category": "heterosis (TO:0000355)",
            "ontology": "TO",
            "onto_id": "TO:0000355"
          }
        ]
      }
    ]
  }
]
```

By default this only includes sentences that have at least one annotation;
add `--full-text` to include every sentence in the paper, annotated or not.

**API equivalent:** the `annotate` endpoint's raw `sentences`/`annotations`
are flat lists keyed by character offset, with no pre-joined
"sentence with its annotations" structure — that join is done client-side.
Reproduce the default (annotated-only) filtering with `jq`:

```bash
ID="MaizeTest100/10.1038_srep35479/10.1038_srep35479.tpcas"
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" --data-urlencode "ontology=TO" | jq '
  .annotations as $anns
  | [.sentences[] | . as $s
     | ($anns | map(select(.begin >= $s.begin and .end <= $s.end))) as $matched
     | select($matched | length > 0)
     | $s + {annotations: $matched}]
'
```

```json
[
  {
    "begin": 1906,
    "end": 2194,
    "text": "mays L.) is of global significance not only as a food, feed and a source of diverse industrial\nproducts, but also as a model system with tremendous genetic diversity for general plant biology studies as well as\nfor specific biological phenomena such as heterosis and gene transposition1,2",
    "annotations": [
      {
        "begin": 2159,
        "end": 2168,
        "term": "heterosis",
        "category": "heterosis (TO:0000355)",
        "ontology": "TO",
        "onto_id": "TO:0000355"
      }
    ]
  }
]
```

(This matches the Python output above, minus the `paper`/
`search_matched_sentences` wrapper, which the CLI adds from the earlier
search result.)

## 12. Precise section exclusion with `--exclude-type`

The most advanced flag: `--exclude-type` drops results whose only match is
inside a given CAS section — e.g. dropping a paper that only matches
because a tool or gene name appears in a bibliography citation.

Without exclusion, this maize corpus has exactly one paper matching
`HelitronScanner` — and it turns out the only mention is a citation of the
HelitronScanner tool's own paper, in the bibliography:

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document "HelitronScanner"
```

```
[1] Martin GT, Solares E, Guadardo-Mendez J, Muyle A, Bousios A, Gaut BS. (2023). miRNA-like secondary structures in maize (Zea mays) genes and transposable elements correlate with small RNAs, methylation, and expression.. Genome Res. [10.1101_gr.277459.122]
```

**API equivalent** of the un-excluded search:

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeTest100"],"keywords":"HelitronScanner"},"count":50}' \
  "$BASE/search_documents"
```

```json
[
  {"year": "2023", "score": 1.60233, "identifier": "MaizeTest100/10.1101_gr.277459.122/10.1101_gr.277459.122.tpcas",
   "title": "miRNA-like secondary structures in maize (<i>Zea mays</i>) genes and transposable elements correlate with small RNAs, methylation, and expression.",
   "author": "Martin GT, Solares E, ...", "accession": "10.1101_gr.277459.122", "journal": "Genome Res"}
]
```

Excluding `references` correctly drops it to zero, since there's no match
anywhere else in the paper:

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document "HelitronScanner" --exclude-type references
```

```
No results.
```

**API equivalent:** there's no `exclude_type` parameter on the API at all —
`--exclude-type document` mode works by re-running the search scoped to
`type: "sentence"` to get the real match position(s), then checking each
against the `annotate` endpoint's `sections` list. Two calls:

```bash
ACC="10.1101_gr.277459.122"
ID="MaizeTest100/$ACC/$ACC.tpcas"

# 1. Get the matching sentence's exact position
curl -s -X POST -H "Content-Type: application/json" \
  -d "{\"query\":{\"type\":\"sentence\",\"corpora\":[\"MaizeTest100\"],
       \"keywords\":\"HelitronScanner\",\"accession\":\"$ACC\"},
       \"count\":200,\"include_match_sentences\":true}" \
  "$BASE/search_documents" | jq -c '.[0].matched_sentences'
```

```json
["HelitronScanner uncovers a\nlarge overlooked cache of Helitron transposons in many plant genomes"]
```

```bash
# 2. Check whether that sentence's offset falls inside a "references" section
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" | jq '
  .sentences as $sents
  | .sections as $secs
  | ($sents[] | select(.text | contains("HelitronScanner uncovers"))) as $match
  | {match: $match,
     in_references: ([$secs[] | select(.type=="references" and $match.begin >= .begin and $match.end <= .end)] | length > 0)}
'
```

```json
{
  "match": {
    "begin": 89356,
    "end": 89451,
    "text": "HelitronScanner uncovers a\nlarge overlooked cache of Helitron transposons in many plant genomes"
  },
  "in_references": true
}
```

`in_references: true` and no other match exists, so the document would be
dropped — matching the CLI's "No results." exactly.

`--exclude-type` is repeatable (e.g. `--exclude-type references
--exclude-type acknowledgments`) and works together with `--annotate` /
`--annotate-sentences` too, filtering the ontology data by section as well
as the search match — see
[TPC_SEARCH_GUIDE.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/TPC_SEARCH_GUIDE.md#--exclude-type--precise-cas2-based-section-exclusion)
for the full per-mode breakdown.

## Where to go next

- [TPC_SEARCH_GUIDE.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/TPC_SEARCH_GUIDE.md) — complete flag reference
- [TPC_API_TUTORIAL.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/TPC_API_TUTORIAL.md) — the same functionality via
  raw `curl`/HTTP, for non-Python callers
