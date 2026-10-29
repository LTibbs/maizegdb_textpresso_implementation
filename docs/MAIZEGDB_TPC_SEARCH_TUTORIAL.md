# MaizeGDB Textpresso programmatic search tutorial

A walkthrough of searching the MaizeGDB Textpresso instance from the command
line and over HTTP. Every command below was run against the live server,
`https://textpresso.maizegdb.org`, on 2026-10-29, and its real output is shown.
Long outputs are trimmed with `...` where noted. Search results come from a
live index, so scores, rankings and counts will drift as the corpus is updated.

- Python helper script: [tpc_search_combined.py](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/bin/tpc_search_combined.py)
  (with [tpc_category_search.py](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/bin/tpc_category_search.py) for category lookups)
- How the annotation filters were built and measured:
  [ONTOLOGY_BUILD.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/ONTOLOGY_BUILD.md)
  and [ITALICS_RULE.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/ITALICS_RULE.md)
- Every request field, parameter and endpoint in one place: section 14

Both scripts default to the MaizeGDB server, so no `--url` is needed. To
point them elsewhere, pass `--url` or set `TPC_API_URL`.

No API key is needed for anything in this tutorial, because every paper in
`MaizeOpenAccess` is open access. Section 13 covers what changes for papers
that are not.

Each example also shows the equivalent API call (`curl`, plus `jq` for
post-processing) for callers not using Python. These use three base URLs,
exported once for the rest of the tutorial:

```bash
export BASE="https://textpresso.maizegdb.org/v1/textpresso/api"
export ANNOTATE="https://textpresso.maizegdb.org/v1/textpresso/annotate"
export CATSEARCH="https://textpresso.maizegdb.org/v1/textpresso/category_search"
```

`$BASE` is the Textpresso search API. `$ANNOTATE` and `$CATSEARCH` are a
separate MaizeGDB service that reads each paper's annotations and the
ontology files directly.

## 1. What corpora exist?

Before searching anything, find out which corpus names (`-c`) are valid.

```bash
python3 bin/tpc_search_combined.py --list-corpora
```

```
MaizeOpenAccess
```

**API equivalent:**

```bash
curl -s "$BASE/available_corpora"
```

```json
["MaizeOpenAccess"]
```

MaizeGDB serves a single corpus, `MaizeOpenAccess`: 3,299 open-access maize
papers. Because it is the only corpus, leaving out `-c` searches the same
papers. The examples still pass `-c MaizeOpenAccess` explicitly so they keep
working if more corpora are added later.

## 2. Keyword search

The simplest possible search is one corpus and one keyword. With no `--type`,
the default is `sentence`, which returns the actual matching sentences from
the full paper text.

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess "anthocyanin" --count 2
```

```
[1] Bulanov AN; Andreeva EA; Tsvetkova NV; Zykin PA (2025). Regulation of Flavonoid Biosynthesis by the MYB-bHLH-WDR (MBW) Complex in Plants and Its Specific Features in Cereals.. International journal of molecular sciences. [10.3390_ijms26020734]
  - The transcription factors C1 and Lc were among the first studied in plants, which, together with the WDR coregulators, activate the anthocyanin biosynthesis
  - Numerous alleles of R locus are known, which may cause a lack of anthocyanin pigmentation, have pigmentation only in vegetative organs, only in generative organs, or in both
  ... (233 matched sentences in this paper)

[2] Chachar Z; Lai R; Ahmed N; Lingling M; Chachar S; Paker NP; Qi Y (2024). Cloned genes and genetic regulation of anthocyanin biosynthesis in maize, a comparative review.. Frontiers in plant science. [10.3389_fpls.2024.1310634]
  - Cloned genes and genetic regulation of anthocyanin biosynthesis in maize, a comparative review
  - The research on fruit corn with a high anthocyanin content is not sufficiently extensive
  ... (186 matched sentences in this paper)
```

Each result is a reference line (`authors (year). title. journal. [accession]`)
followed by every matched sentence from that paper. `--count` caps how many
*documents* come back (default 50), not sentences. One document can
contribute hundreds of matched sentences, which is why this output is long
even with `--count 2`.

The **accession** is the paper's DOI with every `/` replaced by `_`, so
`10.3390_ijms26020734` is `doi:10.3390/ijms26020734`.

**API equivalent:** `corpora` nests inside `query`. `include_match_sentences`
is required to get sentence text back, and is only valid for
`type: "sentence"`.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"sentence","corpora":["MaizeOpenAccess"],"keywords":"anthocyanin"},
       "count":2,"include_match_sentences":true}' \
  "$BASE/search_documents"
```

```json
[{"year":"2025","doc_type":"Journal_article","score":...,
  "identifier":"MaizeOpenAccess/10.3390_ijms26020734/10.3390_ijms26020734.tpcas",
  "title":"Regulation of Flavonoid Biosynthesis by the MYB-bHLH-WDR (MBW) Complex in Plants and Its Specific Features in Cereals.",
  "author":"Bulanov AN; Andreeva EA; Tsvetkova NV; Zykin PA","accession":"10.3390_ijms26020734",
  "journal":"International journal of molecular sciences",
  "matched_sentences":["The transcription factors C1 and Lc were among the first studied in plants, ...", ...]},
 ...]
```

The raw response has the same fields as the script's `--format json` output
(see section 10), plus `matched_sentences`. The `[1] Authors (year)...` /
`- sentence` layout above is formatted by the script, not by the API.

### How many papers match in total?

`search_documents` returns at most 200 results per call. To get the total
number of matching papers, use `get_documents_count` with the same `query`:

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeOpenAccess"],"keywords":"anthocyanin"}}' \
  "$BASE/get_documents_count"
```

```
470
```

The Python script pages past the 200 limit for you. `--count 1000` fetches
up to 1,000 papers, about 6 s per round of pages. Over raw HTTP, page
yourself with `"since_num"`, which is a result offset (`0`, `200`, `400`, ...).
Each page re-runs the whole search, but pages requested at the same time
run in parallel.

### Keyword syntax: words are OR'd unless you say otherwise

Several words without quotes match papers containing **any** of them, not
all of them. Results are ranked by relevance, so papers with every word
still come first, but the result set is much larger than you might expect.
Counts for `MaizeOpenAccess` (`--type document`):

| Keywords | Papers | Meaning |
|---|---|---|
| `flowering time` | 2,650 | `flowering` **or** `time` |
| `flowering AND time` | 1,050 | both words, anywhere in the paper |
| `"flowering time"` | 558 | the exact phrase |
| `anthocyanin NOT drought` | 360 | first word without the second |
| `(anthocyanin OR flavonoid) AND drought` | 126 | parentheses group terms |
| `anthocyan*` | 0 | wildcards are **not** supported |

On the command line, put the phrase quotes inside single quotes:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document '"flowering time" AND photoperiod' --count 3
```

In JSON, escape them: `"keywords": "\"flowering time\" AND photoperiod"`.

Matching ignores case by default. `--case-sensitive` (`"case_sensitive":
true` over HTTP) makes it exact: `zmmads` finds the same 4 papers as
`ZmMADS` by default, and none with `--case-sensitive`.

## 3. Getting document citations only: `--type document`

If you only need to know which papers matched, not every matching sentence,
use `--type document`. It returns one citation per matching paper and no
sentence text, which is much easier to scan.

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document "anthocyanin" --count 3
```

```
[1] Anirban A; Hong HT; O'Hare TJ (2023). Profiling and Quantification of Anthocyanins in Purple-Pericarp Sweetcorn and Purple-Pericarp Maize.. Molecules (Basel, Switzerland). [10.3390_molecules28062665]

[2] Guo G; Wang Y; Zhang B; Yu H; Li L; Cao G; Chen B; Li C; Bu F; Teng S; Yu Q; Gao M; Jiang B; Yang K (2024). Comparative transcriptomic and metabolomic analysis reveals mechanisms of selenium-regulated anthocyanin synthesis in waxy maize (<i>Zea mays L.</i>).. Frontiers in plant science. [10.3389_fpls.2024.1466756]

[3] Li J; Chen Z; Su B; Zhang Y; Wang Z; Ma K; Lu B; Ren J; Xue J (2025). Evaluation of Functional Quality of Maize with Different Grain Colors and Differences in Enzymatic Properties of Anthocyanin Metabolism.. Foods (Basel, Switzerland). [10.3390_foods14040544]
```

**API equivalent:** same shape with `type` changed to `"document"`, and
`include_match_sentences` dropped. Sending it with any type other than
`sentence` makes the API return HTTP 401, which is an API quirk, not an
authentication error.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeOpenAccess"],"keywords":"anthocyanin"},"count":3}' \
  "$BASE/search_documents"
```

```json
[
  {"year": "2023", "doc_type": "Journal_article", "score": 0.759488,
   "identifier": "MaizeOpenAccess/10.3390_molecules28062665/10.3390_molecules28062665.tpcas",
   "title": "Profiling and Quantification of Anthocyanins in Purple-Pericarp Sweetcorn and Purple-Pericarp Maize.",
   "author": "Anirban A; Hong HT; O'Hare TJ", "accession": "10.3390_molecules28062665",
   "journal": "Molecules (Basel, Switzerland)"},
  {"year": "2024", "doc_type": "Journal_article", "score": 0.696875,
   "identifier": "MaizeOpenAccess/10.3389_fpls.2024.1466756/10.3389_fpls.2024.1466756.tpcas",
   "title": "Comparative transcriptomic and metabolomic analysis reveals mechanisms of selenium-regulated anthocyanin synthesis in waxy maize (<i>Zea mays L.</i>).",
   "author": "Guo G; Wang Y; ...", "accession": "10.3389_fpls.2024.1466756", "journal": "Frontiers in plant science"},
  {"year": "2025", "doc_type": "Journal_article", "score": 0.664142,
   "identifier": "MaizeOpenAccess/10.3390_foods14040544/10.3390_foods14040544.tpcas",
   "title": "Evaluation of Functional Quality of Maize with Different Grain Colors and Differences in Enzymatic Properties of Anthocyanin Metabolism.",
   "author": "Li J; Chen Z; ...", "accession": "10.3390_foods14040544", "journal": "Foods (Basel, Switzerland)"}
]
```

The `identifier` field (`corpus/accession/accession.tpcas`) is what the
annotation endpoint takes in sections 5, 6, 11 and 12.

Most of the remaining examples use `--type document` for readability. The
same flags work with `--type sentence`, which also returns the sentence text.

## 4. Using category search

`--category` restricts results to papers annotated with a specific
ontology term. On MaizeGDB, the searchable ontologies are:

| Name | Ontology |
|---|---|
| `GO` | Gene Ontology |
| `PO` | Plant Ontology (anatomy and growth stages) |
| `TO` | Plant Trait Ontology |
| `CO_322` | Crop Ontology maize traits |
| `PATO` | phenotypic qualities |
| `CHEBI` | chemical entities |
| `SO` | Sequence Ontology |
| `ECO` | evidence and conclusion codes |
| `PECO` | Plant Experimental Conditions Ontology |
| `PSO` | Plant Stress Ontology |
| `NCBITaxon` | taxonomy |
| `MAIZE_GENES` | curated MaizeGDB loci (section 6) |

Unlike keywords, `--category` takes the *exact* stored `"name (ID)"`
string. A bare word like `"seed"` is rejected. Look up the string first with
`bin/tpc_category_search.py`:

```bash
python3 bin/tpc_category_search.py "seed" --limit 3
```

```
Categories matching "seed":

  seed (PO:0009010)                                 [PO, exact]  parents via: is_a
  Seed size for target use/market (CO_322:0001176)  [CO_322, name_prefix]  parents via: is_a
  Seedling disease incidence (CO_322:0000093)       [CO_322, name_prefix]  parents via: is_a

Example:
  python3 bin/tpc_search_combined.py -c <corpus> --category "seed (PO:0009010)" "<keywords>"
```

Add `--ontology PO` (repeatable) to limit the lookup to one ontology, and
`--format json` for machine-readable output.

Matches are ranked by how they matched, shown in brackets: `exact` (the
term's name), then `name_prefix`, `synonym_exact`, `name_substring` and
`synonym_substring`. A synonym match lets you find a term under a name you
know. For example, the QTL name `qRfg1` finds its gene:

```bash
python3 bin/tpc_category_search.py "qRfg1" --limit 3
```

```
Categories matching "qRfg1":

  cct1 (tpzm:0010325)  [MAIZE_GENES, synonym_exact]  parents via: is_a
```

If you skip the lookup and pass a bare word, the search script refuses to
run and suggests exact strings instead:

```
$ python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --category "seed" "development"
tpc_search_combined.py: error:
--category "seed" does not exactly match a known category. Closest matches:
  --category "seed (PO:0009010)"   (PO)
  --category "Seed size for target use/market (CO_322:0001176)"   (CO_322)
  --category "Seedling disease incidence (CO_322:0000093)"   (CO_322)
  --category "Seedling vigor (CO_322:0001017)"   (CO_322)
  --category "seed abscission (GO:0097548)"   (GO)
  --category "seed chalaza (PO:0006333)"   (PO)
  --category "seed coat (PO:0009088)"   (PO)
  --category "seed coat color (TO:0000190)"   (TO)
Example: --category "seed (PO:0009010)"

Tip: python3 bin/tpc_category_search.py "<term>" searches the ontology directly for candidate --category strings.
```

(The usage summary printed above the error is trimmed here.) If the lookup
service itself cannot be reached, this check is skipped and the search runs
as typed, so an outage never blocks ordinary searching.

**API equivalent** of the lookup:

```bash
curl -s -G "$CATSEARCH" --data-urlencode "q=seed" --data-urlencode "limit=3"
```

```json
{
  "query": "seed",
  "matches": [
    {"id": "PO:0009010", "name": "seed", "category": "seed (PO:0009010)", "ontology": "PO",
     "matched_on": "exact", "relationship_types": [], "parent_relationship_types": ["is_a"]},
    {"id": "CO_322:0001176", "name": "Seed size for target use/market",
     "category": "Seed size for target use/market (CO_322:0001176)", "ontology": "CO_322",
     "matched_on": "name_prefix", "relationship_types": [], "parent_relationship_types": ["is_a"]},
    {"id": "CO_322:0000093", "name": "Seedling disease incidence",
     "category": "Seedling disease incidence (CO_322:0000093)", "ontology": "CO_322",
     "matched_on": "name_prefix", "relationship_types": [], "parent_relationship_types": ["is_a"]}
  ]
}
```

Then pass the exact `category` string to `--category`:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --category "seed (PO:0009010)" "development" --count 3
```

```
[1] Chen C; Bai M; Wang T; Zhang W; Yu H; Pang T; Wu J; Li Z; Wang X (2024). An RGB image dataset for seed germination prediction and vigor detection - maize.. Frontiers in plant science. [10.3389_fpls.2024.1341335]

[2] Li H; Yue H; Lu M; Jia R; Jiang X (2024). Rupture Test: A New Method for Evaluating Maize (<i>Zea mays</i>) Seed Vigour.. Plants (Basel, Switzerland). [10.3390_plants13131847]

[3] Nogueira A; Puga H; Gerós H; Teixeira A (2025). Ultrasound-enhanced seed hydration: impacts on seedling vigor, gene expression and absorption kinetics in maize, bean and pepper seeds.. Journal of the science of food and agriculture. [10.1002_jsfa.14426]
```

**API equivalent:** `categories` is a list field inside `query`. The script
checks a `--category` value before searching and refuses a non-exact one.
The raw API does no such check: it runs whatever string you give it, so
confirm the string with `category_search` first.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeOpenAccess"],
       "keywords":"development","categories":["seed (PO:0009010)"]},"count":3}' \
  "$BASE/search_documents"
```

```json
[
  {"year": "2024", "identifier": "MaizeOpenAccess/10.3389_fpls.2024.1341335/10.3389_fpls.2024.1341335.tpcas",
   "title": "An RGB image dataset for seed germination prediction and vigor detection - maize.",
   "accession": "10.3389_fpls.2024.1341335", "journal": "Frontiers in plant science", ...},
  {"year": "2024", "identifier": "MaizeOpenAccess/10.3390_plants13131847/10.3390_plants13131847.tpcas",
   "title": "Rupture Test: A New Method for Evaluating Maize (<i>Zea mays</i>) Seed Vigour.",
   "accession": "10.3390_plants13131847", "journal": "Plants (Basel, Switzerland)", ...},
  {"year": "2025", "identifier": "MaizeOpenAccess/10.1002_jsfa.14426/10.1002_jsfa.14426.tpcas",
   "title": "Ultrasound-enhanced seed hydration: impacts on seedling vigor, gene expression and absorption kinetics in maize, bean and pepper seeds.",
   "accession": "10.1002_jsfa.14426", "journal": "Journal of the science of food and agriculture", ...}
]
```

> **Several categories over raw HTTP:** with more than one entry in
> `categories`, the API requires papers to match **all** of them unless you
> also send `"categories_and_ed": false`. The script sends `false` (match
> **any**) by default, and `--categories-and` switches it to all. If you
> build multi-category requests by hand, always set the field explicitly.

## 4b. Expanding `--category` to include related ontology terms

Section 4's `--category` matches only the exact term you give it. A search
for `"root (PO:0009005)"` finds papers annotated with `root` itself, not
papers annotated only with a more specific child term like `crown root` or
`lateral root`, and not papers annotated only with a broader parent term.
This is a different axis from the gene EXACT/RELATED distinction in section 6:
that is about synonym ambiguity for one locus, while this walks the ontology
*graph* of parent and child terms.

Two repeatable flags cover the two directions:

- `--expand-relationship-type` walks *down* to descendants (more specific
  terms).
- `--expand-ancestor-relationship-type` walks *up* to ancestors (more
  general terms).

> **On MaizeGDB, `is_a` is currently the only relationship type.** The
> ontology files behind this index are a build that keeps the `is_a`
> hierarchy and drops other relationship types such as `part_of`, `has_part`
> and `develops_from`. Pass `is_a` to both flags. Any other type is accepted
> but contributes nothing, and the script warns you (see below). A pending
> server update adds `part_of` edges for GO, PO and TO. After it is
> deployed, `--expand-relationship-type part_of` will also reach terms such
> as the parts of the nucleus.

### Descendants

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document \
  --category "root (PO:0009005)" --expand-relationship-type is_a "architecture" --count 3
```

```
[1] York LM; Lynch JP (2015). Intensive field phenotyping of maize (Zea mays L.) root crowns identifies phenes and phene integration associated with plant growth and nitrogen acquisition.. Journal of experimental botany. [10.1093_jxb_erv241]

[2] Salvi S; Giuliani S; Ricciolini C; Carraro N; Maccaferri M; Presterl T; Ouzunova M; Tuberosa R (2016). Two major quantitative trait loci controlling the number of seminal roots in maize co-map with the root developmental genes rtcs and rum1.. Journal of experimental botany. [10.1093_jxb_erw011]

[3] Hochholdinger F; Woll K; Sauer M; Dembinsky D (2004). Genetic dissection of root formation in maize (Zea mays) reveals root-type specific developmental programmes.. Annals of botany. [10.1093_aob_mch056]
```

Compare with the plain, unexpanded search:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document \
  --category "root (PO:0009005)" "architecture" --count 3
```

```
[1] Wu J; Guo Y (2014). An integrated method for quantifying root architecture of field-grown maize.. Annals of botany. [10.1093_aob_mcu009]

[2] Dreyer J; Edelmann HG (2018). Root cap-mediated evaluation of soil resistance towards graviresponding roots of maize (Zea mays L.) and the relevance of ethylene.. Annals of botany. [10.1093_aob_mcx209]

[3] Yu P; Hochholdinger F; Li C (2015). Root-type-specific plasticity in response to localized high nitrate supply in maize (Zea mays).. Annals of botany. [10.1093_aob_mcv127]
```

The expansion adds 22 descendants of `root`: `crown root`, `seminal root`,
`prop root`, `lateral root`, `primary root`, `shoot-borne root`, and others.
That raises the match count only from 797 to 799 papers, because almost
every paper that mentions a crown root also says "root". The bigger effect is
ranking: papers that discuss specific root types, such as root crowns and
seminal roots, now rank above general ones.

### Ancestors

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document \
  --category "seed coat (PO:0009088)" --expand-ancestor-relationship-type is_a \
  "development" --count 3
```

```
[1] Satya Srii V; Nagarajappa N (2024). Impact of accelerated aging on seed quality, seed coat physical structure and antioxidant enzyme activity of Maize (<i>Zea mays</i> L.).. PeerJ. [10.7717_peerj.17988]

[2] Li J; Wang L; Wan J; Dang K; Lin Y; Meng S; Qiu X; Wang Q; Zhao J; Mu L; Luo H; Ding D; Chen Z; Tang J (2023). Dynamic patterns of gene expression and regulatory variation in the maize seed coat.. BMC plant biology. [10.1186_s12870-023-04078-1]

[3] Zhang J; Yue Y; Hu M; Yi F; Chen J; Lai J; Xin B (2024). Dynamic transcriptome landscape of maize pericarp development.. The Plant journal : for cell and molecular biology. [10.1111_tpj.16548]
```

Ancestor expansion widens a search much faster than descendant expansion.
The ancestors of `seed coat` are `portion of plant tissue`,
`plant structure` and `plant anatomical entity`, which together take the
match count from 87 to 539 papers. Use it when you want "anything in this
family", and expect the top hits to stay the papers that match your
original term best.

Leaving out both flags keeps the exact-match-only behavior from section 4.
You can pass both flags at once to expand in both directions.

### Which relationship types are available for a given term?

Every `tpc_category_search.py` match reports both directions: which
relationship types the term has children through (`children via`, what
`--expand-relationship-type` needs) and which it has parents through
(`parents via`, what `--expand-ancestor-relationship-type` needs):

```bash
python3 bin/tpc_category_search.py "seed" --ontology PO --limit 5
```

```
Categories matching "seed":

  seed (PO:0009010)                    [PO, exact]  parents via: is_a
  seed chalaza (PO:0006333)            [PO, name_prefix]  parents via: is_a
  seed coat (PO:0009088)               [PO, name_prefix]  parents via: is_a
  seed coat epidermis (PO:0006048)     [PO, name_prefix]  children via: is_a; parents via: is_a
  seed development stage (PO:0001170)  [PO, name_prefix]  children via: is_a; parents via: is_a
```

`seed` and `seed coat` have no `children via` entry. They have no `is_a`
children in this ontology, so `--expand-relationship-type` cannot add
anything for them. `seed development stage`, by contrast, has `is_a` children
such as `dry seed stage` and `endosperm development stage`.

You don't have to check by hand before searching. If you request a
relationship type a term doesn't have, the search script prints a warning
to stderr and runs the search anyway with whatever is available:

```
$ python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --category "seed (PO:0009010)" \
  --expand-relationship-type part_of "development" --count 1
Warning: --expand-relationship-type part_of -- "seed (PO:0009010)" has no children via part_of, so this contributes nothing for it. Available for this term: (none -- no relationships in this direction)
[1] Chen C; Bai M; Wang T; Zhang W; Yu H; Pang T; Wu J; Li Z; Wang X (2024). An RGB image dataset for seed germination prediction and vigor detection - maize.. Frontiers in plant science. [10.3389_fpls.2024.1341335]
```

**API equivalent:** expansion is done in two steps. First, ask
`category_search` for the expanded term list with `relationship_type`
(descendants) or `ancestor_relationship_type` (ancestors):

```bash
curl -s -G "$CATSEARCH" --data-urlencode "q=root (PO:0009005)" \
  --data-urlencode "relationship_type=is_a" --data-urlencode "limit=5000" \
  | jq -r '.matches[] | "\(.category)  \(.matched_on)"'
```

```
root (PO:0009005)  exact
basal root (PO:0025002)  exact+descendant
crown root (PO:0000043)  exact+descendant
embryo root (PO:0000045)  exact+descendant
lateral root (PO:0020121)  exact+descendant
...
seminal root (PO:0000046)  exact+descendant
shoot-borne root (PO:0000042)  exact+descendant
...
```

(23 matches in total: `root` plus its 22 descendants.)

Always pass a large `limit` when you expand, because the default is 20 and a
cut-off list silently drops terms from your search. The live server
currently caps every response at 100 matches, whatever `limit` you ask for,
so a term with more relatives than that cannot be fully expanded yet.
`organelle (GO:0043226)` has 420 `is_a` descendants but returns 100. If a
response holds exactly 100 matches, assume it was cut. The script does this
check and prints a warning to stderr when it fires.

The pending server update raises the cap to 5,000 for expansions and adds a
`truncated` field, which is `true` when the list was still cut. Once it is
deployed, check that field rather than counting.

Then pass every `category` string to `search_documents`, with
`categories_and_ed: false` so a paper needs only one of them (see the note
at the end of section 4):

```bash
CATS=$(curl -s -G "$CATSEARCH" --data-urlencode "q=root (PO:0009005)" \
  --data-urlencode "relationship_type=is_a" --data-urlencode "limit=5000" \
  | jq -c '[.matches[].category]')
curl -s -X POST -H "Content-Type: application/json" \
  -d "{\"query\":{\"type\":\"document\",\"corpora\":[\"MaizeOpenAccess\"],\"keywords\":\"architecture\",
       \"categories\":$CATS,\"categories_and_ed\":false},\"count\":3}" \
  "$BASE/search_documents"
```

This is what the script does internally: it collects the expanded
`category` strings into the `categories` field of one `search_documents`
request.

## 5. Ontology annotations with `--annotate`

So far, both keywords and `--category` have *found* papers. `--annotate`
does something different: given a paper you have already identified (here,
with `--accession`), it *describes* that paper. It appends every ontology
term found anywhere in the paper, grouped by ontology, independent of what
you searched for. To find papers by annotation, use `--category` (section 4).

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --accession 10.3390_molecules28062665 --annotate --ontology TO
```

```
[1] Anirban A; Hong HT; O'Hare TJ (2023). Profiling and Quantification of Anthocyanins in Purple-Pericarp Sweetcorn and Purple-Pericarp Maize.. Molecules (Basel, Switzerland). [10.3390_molecules28062665]
  Ontology annotations:
    TO: End, Moisture content, anthocyanin content, moisture content
```

`--ontology TO` restricts the summary to the Trait Ontology for a short
example. The values are the surface forms as written in the paper, which is
why `Moisture content` and `moisture content` both appear. Section 11 shows
how to see which ontology term each surface form was matched to.

`End` is a known false match. It is a RELATED synonym of the trait
`endosperm quality`, and here it comes from the `End of Article` marker that
the ingest writes at the end of every paper. Fixes dated 2026-10-29 drop
matches on that marker and block `End` for this term, and the annotation
will disappear once the server loads them. Until the corpus is re-indexed, a
`--category "endosperm quality (TO:0000587)"` search still returns
essentially every paper, for the same reason.

Without `--ontology`, every ontology is shown. For the same paper:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --accession 10.3390_molecules28062665 --annotate
```

```
[1] Anirban A; Hong HT; O'Hare TJ (2023). Profiling and Quantification of Anthocyanins in Purple-Pericarp Sweetcorn and Purple-Pericarp Maize.. Molecules (Basel, Switzerland). [10.3390_molecules28062665]
  Ontology annotations:
    CHEBI: Anthocyanins, HCl, Pelargonidin, Solvents, acid, aluminum, anthocyanins, based, dyes, ...
    GO: Pigmentation, anthocyanin biosynthesis, anthocyanin synthesis, biosynthesis, methylation, pigmentation, pollination, starch biosynthesis, synthesis
    MAIZE_GENES: AAT1, a1, anthocyanin acyltransferase1, anthocyaninless1, pr1, red, red aleurone1, sh2, shrunken2
    NCBITaxon: Maize, all, maize
    OTHER: Measurement
    PATO: Area, Colour, Frozen, Mature, Maturity, ...
    PO: Costa, Kernel, Kernels, aleurone layer, cob, endosperm, initial, kernel, kernels, pericarp, pericarps, seeds, silk, vessels
    SO: Accession, Accessions, accessions, allele, alleles, genotype, genotypes, intermediate, linear, mobile, reverse
    TO: End, Moisture content, anthocyanin content, moisture content
    [810 shown, 305 filtered: 162 references/acknowledgments, 107 common words without context, 26 case mismatch, 6 broader terms, 2 case mismatch, 1 tier TD undefined, 1 english word gene; --no-hygiene to show all]
```

The broad ontologies (PATO, CHEBI, SO, NCBITaxon) match many everyday
words. That makes them useful for filtering a search, but noisy as a
summary. The bracketed line at the end is the hygiene report, explained next.

### Annotation hygiene: what the filter drops

Annotations are filtered by default before you see them. The last line
above says 305 of this paper's 1,115 raw matches were dropped. The filter
never removes a paper from search results; it only changes which
annotations are listed for a paper. The CLI, the API and the MaizeGDB
Textpresso web interface all apply the same filter. The web interface shows
the same report under each annotation panel, with a "Show unfiltered"
toggle.

Each drop has a reason code. The CLI prints a short label, and the API
returns the code itself (in `hygiene.dropped_by_reason`):

| CLI label | API reason code | What it drops |
|---|---|---|
| references/acknowledgments | `section` | matches inside the bibliography or acknowledgments, which describe cited papers rather than this one |
| broader terms | `ancestor_of_sibling_on_same_span` | a parent term matched on the same words as a more specific child term (keeps `anthocyanin biosynthesis`, drops the bare parent on the same span) |
| case mismatch | `abbrev_case_mismatch` | an abbreviation written in a case the ontology never uses, such as `Ltd` for a synonym stored only as `LTD` |
| case mismatch | `tier_T1_case_mismatch` | a tier-1 string (an abbreviation or uncommon word) not written in its stored case; see the tiers below |
| common words without context | `tier_T2_no_context` | a common English word stored as a synonym, used in a sentence that gives no sign of the ontology sense |
| tier TD undefined | `tier_TD_undefined` | one of eight curated abbreviations (`GA`, `JA`, `SA`, `BR`, `CK`, `ET`, `ER`, `E3`) in a paper that never spells out the long form |
| blocked short forms | `tier_T3_blocked` | strings blocked from matching entirely (most are 2 characters or fewer) |
| stop form | `stop_form` | 18 abbreviations whose usual meaning in maize papers is not the ontology term, such as `SSR` (simple sequence repeat, not GO's subsynaptic reticulum) and `NAC` (the transcription factor family) |
| never italicised | `not_italicised` | **genes only:** a short alias (4 characters or fewer) that this paper never italicises |
| english word gene | `english_word_gene` | **genes only:** a gene synonym that is also an English word (`late`, `red`, `promoter`, `AS`), never italicised in this paper, with no other name of the same gene present |

The two "case mismatch" entries in the CLI line are different rules
(`tier_T1_case_mismatch` and `abbrev_case_mismatch`) that share a label. The
CLI also prints any code it has no label for with the underscores replaced
by spaces, which is why it shows `tier TD undefined` and `english word gene`.

In this paper, the dropped matches include:

- **Common words without context** (107): `lines` as the Sequence Ontology's
  `LINE_element` (24 times), `present`, `based` and `gene`. This rule also
  removed `day`/`days` (`CO_322`), `evidence` (`ECO`) and `rats` (`PECO`),
  which is why those ontologies are missing from the output above. The same
  word is kept when its sentence supports it:
  `white` is dropped 11 times but kept in the abstract ("crossing
  purple-pericarp maize with white shrunken2 sweetcorn ..."), where another
  PATO term, `maturity`, appears in the same sentence.
- **Tier TD undefined** (1): `SA` as salicylic acid. It occurs once, in
  "Victor Harbor, SA, Australia", and the paper never writes "salicylic
  acid".
- **English word gene** (1): `sec`, a synonym of gene model
  `Zm00001eb017010`.

#### How the tiers work

Every ontology match string except genes is assigned a tier when the
ontology files are built. The tier sets how much evidence a match needs:

| Tier | Which strings | What a match needs |
|---|---|---|
| T0 | most strings, such as `anthocyanin biosynthesis` and `kernel` | nothing: matched freely, in any case |
| T1 | abbreviations (`DNA`, `ABA`, `QTL`) and uncommon single words | the stored case (`ABA` is kept, `aba` is dropped) |
| T2 | very common single words (`water`, `white`, `present`, `lines`) and short common words | support from its sentence: a longer form of the same term (`water deficit`), a qualifier within two words (`root architecture`), or another unambiguous term from the same ontology |
| TD | the eight curated abbreviations above | the stored case, and the paper body must spell out the long form somewhere outside the references |
| T3 | other strings of 2 characters or fewer, and a short list blocked by name | never matched |

Core plant-structure words (`leaf`, `root`, `seed`, `stem`, `kernel`, `ear`,
`tassel`, ...) are T0 in the Plant Ontology, so they are not held to the T2
context test there. A plural is judged as its singular. Gene names are never
tiered; the italics and English-word rules below handle them.

For how the tiers were derived and measured, see
[ONTOLOGY_BUILD.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/ONTOLOGY_BUILD.md#5--four-tier-match-string-policy).

#### The gene rules: italics and English words

Gene symbols are italicised by convention, while ordinary abbreviations
that happen to be recorded as gene synonyms are not. For gene annotations
(`MAIZE_GENES` and `MAIZE_GENES_RELATED`), the filter uses each paper's own
typography, taken from its PubMed Central XML where available and from the
source PDF's fonts otherwise:

- **Never italicised.** A form of 4 characters or fewer is dropped when it
  occurs in the paper body and none of those occurrences is italic. EXACT
  matches (`MAIZE_GENES`), registered locus names (`p1`, `o2`) and gene model
  IDs are exempt.
- **English word gene.** A gene synonym that is also an English word (`late`,
  `red`, `promoter`, `CO`, `ABA`) is kept only when the paper italicises it
  at least once, or when another, non-English name of the same gene also
  appears in the paper.

Both rules only remove a match when there is evidence against it. If a form
never occurs in the body, or the paper has no italics data, the italics test
abstains and the match is kept. The API's `italic_coverage` field (below)
reports how many of the paper's gene annotations had italics data. Section 6
shows both rules on real gene matches. The measurements behind them are in
[ITALICS_RULE.md](https://github.com/LTibbs/maizegdb_textpresso_implementation/blob/master/docs/ITALICS_RULE.md).

#### Turning the filter off

Use `--no-hygiene` to see everything, including the borderline matches, when
you want to check what was filtered:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --accession 10.3390_molecules28062665 --annotate --no-hygiene
```

```
...
    CO_322: day, days
    ECO: Exp, evidence
    GO: Biosynthesis, Cobs, Pigmentation, Soc, Transport, anthocyanin biosynthesis, ...
    MAIZE_GENES: AAT1, a1, anthocyanin acyltransferase1, anthocyaninless1, pr1, red, red aleurone1, sec, sh2, shrunken2
    NCBITaxon: All, Fragaria, Maize, Zea, Zea mays, all, maize
...
```

All 1,115 raw matches are listed, and no hygiene line is printed. Typical
of what the filter removes: `Soc` (matched to a GO term), `Exp` (from
`J. Exp`) and `Fragaria` (from a cited title on strawberry), all three in the
reference list.

`--no-italics` keeps the rest of the filter but skips both gene rules. For
this paper, it brings back only `sec`:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --accession 10.3390_molecules28062665 --annotate --no-italics
```

```
...
    MAIZE_GENES: AAT1, a1, anthocyanin acyltransferase1, anthocyaninless1, pr1, red, red aleurone1, sec, sh2, shrunken2
...
    [811 shown, 304 filtered: 162 references/acknowledgments, 107 common words without context, 26 case mismatch, 6 broader terms, 2 case mismatch, 1 tier TD undefined; --no-hygiene to show all]
```

### API equivalent: the `annotate` endpoint

The `annotate` endpoint takes an `identifier` (from a
prior search result) and returns raw `sentences`, `annotations` and
`sections` lists, plus the `hygiene` report. There is no ready-made summary
field: the script groups `annotations` by `ontology` itself. You can do the
same with `jq`:

```bash
ID="MaizeOpenAccess/10.3390_molecules28062665/10.3390_molecules28062665.tpcas"
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" --data-urlencode "ontology=TO" \
  | jq '.annotations | group_by(.ontology) | map({(.[0].ontology): ([.[].term] | unique)}) | add'
```

```json
{
  "TO": ["End", "Moisture content", "anthocyanin content", "moisture content"]
}
```

The drop report is in the `hygiene` field:

```bash
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" | jq '.hygiene'
```

```json
{
  "kept": 810,
  "dropped": 305,
  "dropped_by_reason": {
    "tier_T2_no_context": 107,
    "tier_T1_case_mismatch": 26,
    "abbrev_case_mismatch": 2,
    "tier_TD_undefined": 1,
    "section": 162,
    "english_word_gene": 1,
    "ancestor_of_sibling_on_same_span": 6
  },
  "italic_coverage": {
    "gene_annotations": 35,
    "with_italic_data": 33
  }
}
```

`italic_coverage` says how many of the paper's gene annotations the gene
rules could judge. Here 33 of 35 had italics data for their form; for the
other 2 the italics test abstained and kept the match.

Each surviving annotation also carries a `tier` field recording why it was
kept, such as `tier_T0` (matched freely) or
`tier_T2_context:companion_term` (a common word, kept because another term
from the same ontology is in its sentence). Gene annotations, and the
few in the `OTHER` channel, have no `tier`.
To count them for this paper:

```bash
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" \
  | jq '[.annotations[].tier // "none"] | group_by(.) | map({(.[0]): length}) | add'
```

```json
{
  "none": 34,
  "tier_T0": 597,
  "tier_T1": 122,
  "tier_T2_context:companion_term": 50,
  "tier_T2_context:longer_form": 1,
  "tier_T2_context:qualifier:concentration": 3,
  "tier_T2_context:qualifier:lines": 1,
  "tier_T2_context:qualifier:ratio": 1,
  "tier_T2_context_unavailable": 1
}
```

`tier_T2_context_unavailable` means the test could not run because no
sentence was found for the match, so the match was kept. As with italics, a
test that cannot run never drops anything.

#### Per-request switches

The `annotate` endpoint takes these optional parameters. Each changes one
request only:

| Parameter | Default | Effect | CLI equivalent |
|---|---|---|---|
| `hygiene=0` | `1` | turn the whole filter off; no `hygiene` field in the response | `--no-hygiene` |
| `italics=0` | `1` | skip both gene rules (italics and English word), keep the rest | `--no-italics` |
| `tiers=0` | `1` | skip the tier gate (T1 case, T2 context, TD and T3), keep the rest | none |
| `exclude_sections=NAME` | `references`, `acknowledgments` | replace the set of sections whose matches are dropped (repeatable) | none; see `--exclude-type` in section 12 |
| `ontology=NAME` | all | return only these ontologies (repeatable) | `--ontology` |
| `related_synonyms=1` | `0` | without `ontology`, also return `MAIZE_GENES_RELATED` matches | `--related-synonyms` |

For this paper:

| Request | Kept | Dropped |
|---|---|---|
| default | 810 | 305 |
| `italics=0` | 811 | 304 (`sec` is back) |
| `tiers=0` | 941 | 174 (no `tier_*` reasons) |
| `exclude_sections=references` | 816 | 299 (acknowledgment matches are now kept) |
| `hygiene=0` | 1,115 | none |

For example, to see what the tier gate alone removes, compare the default
with `tiers=0`:

```bash
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" --data-urlencode "tiers=0" | jq '.hygiene'
```

```json
{
  "kept": 941,
  "dropped": 174,
  "dropped_by_reason": {
    "abbrev_case_mismatch": 4,
    "section": 162,
    "english_word_gene": 1,
    "ancestor_of_sibling_on_same_span": 7
  },
  "italic_coverage": {
    "gene_annotations": 35,
    "with_italic_data": 33
  }
}
```

With `--annotate --format json`, the CLI includes the same `hygiene` object
in each result (section 10).

## 6. Gene search: keyword vs. EXACT vs. RELATED vs. category

The categories from section 4 include maize genes. They are built from
MaizeGDB's curated OBO file of published maize loci, with IDs of the form
`tpzm:NNNNNNN`.

Gene categories let you search for a locus under all of its names at once.
For example, a category search for `"cct1 (tpzm:0010325)"` returns papers
that mention `cct1` or any of its curated synonyms, such as the B73 v5 gene
model `Zm00001eb418700`.

Synonyms come in two kinds, EXACT and RELATED. An EXACT synonym refers to
this locus only. A RELATED synonym is a name that has been used for this
locus but is ambiguous: a given abbreviation may have been used in the
literature for more than one locus, or one locus may have two gene models
in one genome version.

Annotations keep the two apart: `--ontology MAIZE_GENES` shows EXACT
matches only, and `--ontology MAIZE_GENES_RELATED` shows RELATED matches.

The `cct` family (`cct1`, `cct9`, `cct10`, ...) is a good demonstration
because its members have many synonyms and its `ZmCCT`-prefixed aliases are
common, and shared, in the literature. To look up the category string:

```bash
python3 bin/tpc_category_search.py "cct1" --ontology MAIZE_GENES --limit 3
```

```
Categories matching "cct1":

  cct1 (tpzm:0010325)    [MAIZE_GENES, exact]  parents via: is_a
  cct10 (tpzm:0010326)   [MAIZE_GENES, name_prefix]  parents via: is_a
  cct101 (tpzm:0010327)  [MAIZE_GENES, name_prefix]  parents via: is_a
```

### Keyword search

A plain keyword search for `"cct1"` matches only papers whose text contains
that literal string:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document "cct1"
```

```
[1] Tibbs-Cortes LE; Guo T; Andorf CM; Li X; Yu J (2024). Comprehensive identification of genomic and environmental determinants of phenotypic plasticity in maize.. Genome research. [10.1101_gr.279027.124]

[2] Guo W; Wang F; Lv J; Yu J; Wu Y; Wuriyanghan H; Le L; Pu L (2025). Phenotyping, genome-wide dissection, and prediction of maize root architecture for temperate adaptability.. iMeta. [10.1002_imt2.70015]

[3] Balliau T; Ashenafi M; Blein-Nicolas M; Turc O; Zivy M; Marchadier E (2024). A Moderate Water Deficit Induces Profound Changes in the Proteome of Developing Maize Ovaries.. Biomolecules. [10.3390_biom14101239]
```

Only 3 papers. This is precise, but only as good as your knowledge of every
name authors use for the gene. Miss a synonym and you miss the paper.

**API equivalent:** same shape as section 3.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeOpenAccess"],"keywords":"cct1"},"count":50}' \
  "$BASE/search_documents" | jq -r '.[].accession'
```

```
10.1101_gr.279027.124
10.1002_imt2.70015
10.3390_biom14101239
```

### Inspecting a hit with `--annotate`: is `cct1` an EXACT match here?

`--annotate --ontology MAIZE_GENES` describes a paper you already have,
showing its EXACT gene matches. For the first keyword hit:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --accession 10.1101_gr.279027.124 --annotate --ontology MAIZE_GENES
```

```
[1] Tibbs-Cortes LE; Guo T; Andorf CM; Li X; Yu J (2024). Comprehensive identification of genomic and environmental determinants of phenotypic plasticity in maize.. Genome research. [10.1101_gr.279027.124]
  Ontology annotations:
    MAIZE_GENES: CO, SCS, Zm00001eb023220, Zm00001eb353250, Zm00001eb380460, Zm00001eb382070, Zm00001eb418700, cct1, cct103, constans1, conz1, cry3, cryptochrome3, hp2, pebp8, phosphatidylethanolamine-binding protein8, vgt2
    [54 shown, 14 filtered: 9 references/acknowledgments, 5 english word gene; --no-hygiene to show all]
```

`cct1` and its v5 gene model `Zm00001eb418700` are both tagged as EXACT
matches.

The 5 `english word gene` drops show the English-word rule from section 5:
`EH` (3 times, a synonym of `IDP7290`), `AS` (a
synonym of `asn1`) and `promoter` (recorded as a synonym of `ubi1`). Each is
an EXACT synonym, so the italics rule exempts it. They are dropped because
the paper never italicises them and no other name of those genes appears.

`CO` survives the same rule because this paper italicises it, in gene names
such as *CO CO-LIKE TIMING OF CAB1 protein domain1*. That is also why it is
a false match: here `CO` is attributed to `col11`, but in the text it is
part of the full name of `cct1` and `cct103`. The filter removes mentions it
has evidence against, not every doubtful one, so a curator still needs to
judge what remains. `--no-hygiene` shows all 68 matches, including `AS`,
`EH` and `promoter`.

**API equivalent:** the same `/annotate` plus `jq` grouping as section 5:

```bash
ID="MaizeOpenAccess/10.1101_gr.279027.124/10.1101_gr.279027.124.tpcas"
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" --data-urlencode "ontology=MAIZE_GENES" \
  | jq '.annotations | group_by(.ontology) | map({(.[0].ontology): ([.[].term] | unique)}) | add'
```

### Inspecting a hit with `--annotate`: RELATED synonyms instead

These two papers are returned by the `--category` search below, but neither
mentions `cct1` literally, and neither has an EXACT `cct1` match.
`--ontology MAIZE_GENES_RELATED` shows why they are still tagged with the
`cct1` category:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --accession 10.1093_nar_gkac1195 --annotate --ontology MAIZE_GENES_RELATED
```

```
[1] Chen G; Wang R; Jiang Y; Dong X; Xu J; Xu Q; Kan Q; Luo Z; Springer NM; Li Q (2023). A novel active transposon creates allelic variation through altered translation rate to influence protein abundance.. Nucleic acids research. [10.1093_nar_gkac1195]
  Ontology annotations:
    MAIZE_GENES_RELATED: ZmCCT10, ZmDREB1D, hexose transporter, transmembrane protein
    [17 shown, 41 filtered: 27 never italicised, 14 references/acknowledgments; --no-hygiene to show all]
```

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --accession 10.1093_plcell_koae090 --annotate --ontology MAIZE_GENES_RELATED
```

```
[1] Romero JM; Serrano-Bueno G; Camacho-Fernández C; Vicente MH; Ruiz MT; Pérez-Castiñeira JR; Pérez-Hormaeche J; Nogueira FTS; Valverde F (2024). CONSTANS, a HUB for all seasons: How photoperiod pervades plant physiology regulatory circuits.. The Plant cell. [10.1093_plcell_koae090]
  Ontology annotations:
    MAIZE_GENES_RELATED: DNA binding, P1, P2, ZmCCT10, zinc finger
    [14 shown, 105 filtered: 92 never italicised, 13 references/acknowledgments; --no-hygiene to show all]
```

In both papers, the link to `cct1` is `ZmCCT10`. That name has been used in
the literature for both `cct1` and `cct10`, so it is recorded as a RELATED
synonym of each. To see which locus each form was matched to, read the
`category` field from the API:

```bash
ID="MaizeOpenAccess/10.1093_nar_gkac1195/10.1093_nar_gkac1195.tpcas"
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" --data-urlencode "ontology=MAIZE_GENES_RELATED" \
  | jq -c '[.annotations[] | select(.onto_id=="tpzm:0010325") | {term, category}] | unique'
```

```json
[{"term":"ZmCCT10","category":"RELATED:cct1 (tpzm:0010325)"}]
```

Treat RELATED matches as candidates to verify, not confirmed mentions.
RELATED trades precision for recall.

Most of the noise among short RELATED synonyms is removed by the italics
rule from section 5. In these two papers it drops 119 matches between them:
`CCT` (the protein domain, 84 matches across four gene models), `CDS`, `CA`,
`TGA`, `PIF4`, `Glu` and `bZIP`. Each is 4 characters or fewer and never
italicised in the paper, so it is used as an ordinary abbreviation, not a
gene symbol. `P1` and `P2` are not italicised either, but they survive
because `p1` and `p2` are registered maize locus names, which the rule never
drops. Judge them by reading the sentence. Add `--no-hygiene` to see
everything the rule removed.

### `--category`: searching by the gene category itself

`--category "cct1 (tpzm:0010325)"` searches on the category directly, with
no keyword. Unlike `--annotate`, it finds papers, like keyword search but
more broadly. By default it keeps only papers with an **EXACT** `cct1`
match:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --category "cct1 (tpzm:0010325)" --count 200
```

```
[1] Wang C; Yang Q; Wang W; Li Y; Guo Y; Zhang D; Ma X; Song W; Zhao J; Xu M (2017). A transposon-directed epigenetic change in ZmCCT underlies quantitative resistance to Gibberella stalk rot in maize.. The New phytologist. [10.1111_nph.14688]

[2] Tibbs-Cortes LE; Guo T; Andorf CM; Li X; Yu J (2024). Comprehensive identification of genomic and environmental determinants of phenotypic plasticity in maize.. Genome research. [10.1101_gr.279027.124]

[3] Liu Y; Guo Y; Ma C; Zhang D; Wang C; Yang Q (2016). Transcriptome analysis of maize resistance to Fusarium graminearum.. BMC genomics. [10.1186_s12864-016-2780-5]

...

[14] Pyhäjärvi T; Hufford MB; Mezmouk S; Ross-Ibarra J (2013). Complex patterns of local adaptation in teosinte.. Genome biology and evolution. [10.1093_gbe_evt109]
```

That is 14 papers, against 3 for the keyword search. The extra papers use
a different EXACT name for the same locus. For example, Wang et al. (2017)
call it `qRfg1`, the name of the stalk-rot resistance QTL it underlies, and
Pyhäjärvi et al. (2013) give only its B73 v3 gene model, `GRMZM2G381691`.
Neither paper contains the string `cct1`. The search does *not* include
`10.1093_nar_gkac1195` or `10.1093_plcell_koae090`, because those papers
only have a RELATED match.

This search is slower than the others (about a minute here). The search
index cannot tell EXACT from RELATED matches, so the script checks each
candidate paper's annotations individually (see the API equivalent below).

Add `--related-synonyms` to include RELATED matches too:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --category "cct1 (tpzm:0010325)" --related-synonyms --count 200
```

```
[1] Li W; Jia H; Li M; Huang Y; Chen W; Yin P; Yang Z; Chen Q; Tian F; Zhang Z; Yang X; Liu L (2023). Divergent selection of KNR6 maximizes grain production by balancing the flowering-time adaptation and ear size in maize.. Plant biotechnology journal. [10.1111_pbi.14050]

[2] Wang C; Yang Q; Wang W; Li Y; Guo Y; Zhang D; Ma X; Song W; Zhao J; Xu M (2017). A transposon-directed epigenetic change in ZmCCT underlies quantitative resistance to Gibberella stalk rot in maize.. The New phytologist. [10.1111_nph.14688]

...

[14] Chen G; Wang R; Jiang Y; Dong X; Xu J; Xu Q; Kan Q; Luo Z; Springer NM; Li Q (2023). A novel active transposon creates allelic variation through altered translation rate to influence protein abundance.. Nucleic acids research. [10.1093_nar_gkac1195]

[15] Romero JM; Serrano-Bueno G; Camacho-Fernández C; Vicente MH; Ruiz MT; Pérez-Castiñeira JR; Pérez-Hormaeche J; Nogueira FTS; Valverde F (2024). CONSTANS, a HUB for all seasons: How photoperiod pervades plant physiology regulatory circuits.. The Plant cell. [10.1093_plcell_koae090]

... (36 results total)
```

That is 36 papers, and the two RELATED-only papers from the previous
subsection are back. Many of the 22 additions are flowering-time papers,
where `ZmCCT10` and the other shared `ZmCCT` names are common.

**API equivalent:** the raw `search_documents` endpoint has no notion of
EXACT vs. RELATED. A category query matches either kind, so it returns all
36, the same as `--related-synonyms`:

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeOpenAccess"],
       "categories":["cct1 (tpzm:0010325)"]},"count":200}' \
  "$BASE/search_documents" > raw_cat.json
jq 'length' raw_cat.json
```

```
36
```

To reproduce the script's default EXACT-only result over raw HTTP, check
each result's EXACT annotations and keep the papers that have this
category's ID (`tpzm:0010325`). Send `hygiene=0` so that no match is
filtered out before you check it; the script does the same:

```bash
for id in $(jq -r '.[].identifier' raw_cat.json); do
  has_exact=$(curl -s -G "$ANNOTATE" --data-urlencode "identifier=$id" \
      --data-urlencode "ontology=MAIZE_GENES" --data-urlencode "hygiene=0" \
    | jq '[.annotations[] | select(.onto_id=="tpzm:0010325")] | length > 0')
  [ "$has_exact" = "true" ] && jq -r --arg id "$id" '.[] | select(.identifier==$id) | .accession' raw_cat.json
done
```

```
10.1111_nph.14688
10.1101_gr.279027.124
10.1186_s12864-016-2780-5
10.1186_s12870-016-0930-1
10.1002_imt2.70015
10.1371_journal.pone.0168910
10.3390_plants14091376
10.1080_15592324.2025.2502739
10.3390_plants14101552
10.1111_jipb.13419
10.1007_s00122-022-04239-0
10.1016_j.molp.2018.10.005
10.1016_j.jare.2024.10.024
10.1093_gbe_evt109
```

These are the same 14 papers as the script returns. The check costs one
`/annotate` call per raw result (36 here), the same work the script does
internally.

> **Non-gene ontologies:** the EXACT/RELATED filtering above applies only to
> gene categories (`tpzm:` IDs). GO, PO, TO and the other ontologies also
> have RELATED synonyms; you can see them as a `RELATED:` prefix on an
> annotation's `category` in section 11. A `--category` search on those
> ontologies always includes RELATED matches.

### Summary

Two of these approaches find papers, and two describe why a paper you
already have counts as a match. Each splits further into EXACT (the
default) and RELATED:

| | What it does | Precision | Recall |
|---|---|---|---|
| **Keyword search** (`"cct1"`): *finds papers* (3) | Papers with that literal string in the text | High | Low: misses every alias you didn't search for |
| **`--category "cct1 (tpzm:0010325)"`** (EXACT, default): *finds papers* (14) | Every paper with an EXACT `cct1` synonym | High | Medium: misses RELATED-only mentions |
| **`--category ... --related-synonyms`**: *finds papers* (36) | Adds papers whose only match is a shared RELATED synonym | Lower: includes ambiguous names | Highest |
| `--annotate --ontology MAIZE_GENES`: *describes a paper* | Shows the EXACT gene matches in that paper | High | (not a search) |
| `--annotate --ontology MAIZE_GENES_RELATED`: *describes a paper* | Shows which RELATED synonym (`ZmCCT10`, ...) is driving a match | Lower | (not a search) |

## 7. Restricting to a section of the paper

Instead of `sentence` or `document`, `--type` can name a paper section to
match only within it: `title`, `abstract`, `introduction`, `background`,
`result`, `discussion`, `conclusion`, `design`, `materials and methods`,
`acknowledgments`, `references` or `supplementary` (see the note below).
These return citations only, like `--type document`; matched sentence text
is available only with `--type sentence`.

`--type title` is the narrowest. Of the 470 papers that mention anthocyanin,
25 have it in the title:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type title "anthocyanin" --count 3
```

```
[1] Zhang M; Li X; Wang X; Jiang S; Zhang J; Sun M; Zhou Z; Zhang J; Li M; Lv Y; Qi E; Tian Z; Zhu H; Zhang X; Zhao X; Xu C; Lübberstedt T; Zhang X; Yang X; Zhou C; Liu H (2025). Modulation of lignin and anthocyanin homeostasis by GTP cyclohydrolase1 in maize.. Plant biotechnology journal. [10.1111_pbi.70061]

[2] Cone KC; Burr FA; Burr B (1986). Molecular analysis of the maize anthocyanin regulatory locus C1.. Proceedings of the National Academy of Sciences of the United States of America. [10.1073_pnas.83.24.9631]

[3] Sharma M; Cortes-Cruz M; Ahern KR; McMullen M; Brutnell TP; Chopra S (2011). Identification of the pr1 gene product completes the anthocyanin biosynthesis pathway of maize.. Genetics. [10.1534_genetics.110.126136]
```

The other sections work the same way:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type abstract "drought" --count 2
```

```
[1] Yan X; Zhang M; Zhong Y; Zenda T; Liu S; Dong A; Kou M; Liu J; Wang N; Duan H (2025). CHH demethylation in the ZmGST2 promoter enhances maize drought tolerance by regulating ROS scavenging and root growth.. BMC plant biology. [10.1186_s12870-025-07012-9]

[2] Cao Y; Yang W; Ma J; Cheng Z; Zhang X; Liu X; Wu X; Zhang J (2024). An Integrated Framework for Drought Stress in Plants.. International journal of molecular sciences. [10.3390_ijms25179347]
```

> **Spell section names exactly.** The API treats an unrecognized `type` as
> `document` and silently searches the full text. For example,
> `"type": "abstracts"` returns the same 470 papers for `anthocyanin` as a
> whole-document search, with no error.

**Supplementary material.** About a third of the corpus (1,091 papers)
has supplementary files appended after the references. `supplementary` is a
valid `--type` and will search only that part, but on the current index it
returns no papers: those sections only get their own label when the corpus
is next re-indexed. Until then, use `--type document` to include
supplementary text.

Annotations already handle supplements correctly. `--annotate` and the
`annotate` endpoint mark the appended material as a `supplementary material`
section when they read each paper, so the hygiene filter keeps its matches
instead of dropping them as references.

**API equivalent:** `type` becomes the section name.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"abstract","corpora":["MaizeOpenAccess"],"keywords":"drought"},"count":2}' \
  "$BASE/search_documents"
```

```json
[
  {"year": "2025", "score": 1.35193,
   "identifier": "MaizeOpenAccess/10.1186_s12870-025-07012-9/10.1186_s12870-025-07012-9.tpcas",
   "title": "CHH demethylation in the ZmGST2 promoter enhances maize drought tolerance by regulating ROS scavenging and root growth.",
   "author": "Yan X; Zhang M; ...", "accession": "10.1186_s12870-025-07012-9", "journal": "BMC plant biology"},
  {"year": "2024", "score": 1.2598,
   "identifier": "MaizeOpenAccess/10.3390_ijms25179347/10.3390_ijms25179347.tpcas",
   "title": "An Integrated Framework for Drought Stress in Plants.",
   "author": "Cao Y; Yang W; ...", "accession": "10.3390_ijms25179347", "journal": "International journal of molecular sciences"}
]
```

## 8. Filtering by metadata instead of (or alongside) keywords

`--author`, `--journal`, `--year`, `--accession` and `--paper-type` filter
on bibliographic metadata. They can be combined with keywords or used alone.
At least one of keywords, `--author`, `--journal`, `--year`, `--accession`
or `--category` is required.

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --author "Buckler" --year 2014
```

```
[1] Wallace JG; Larsson SJ; Buckler ES (2014). Entering the second century of maize quantitative genetics.. Heredity. [10.1038_hdy.2013.6]

[2] Glaubitz JC; Casstevens TM; Lu F; Harriman J; Elshire RJ; Sun Q; Buckler ES (2014). TASSEL-GBS: a high capacity genotyping by sequencing analysis pipeline.. PloS one. [10.1371_journal.pone.0090346]

[3] Wallace JG; Bradbury PJ; Zhang N; Gibon Y; Stitt M; Buckler ES (2014). Association mapping across numerous traits reveals patterns of functional variation in maize.. PLoS genetics. [10.1371_journal.pgen.1004845]

[4] Peiffer JA; Romay MC; Gore MA; Flint-Garcia SA; Zhang Z; Millard MJ; Gardner CA; McMullen MD; Holland JB; Bradbury PJ; Buckler ES (2014). The genetic architecture of maize height.. Genetics. [10.1534_genetics.113.159152]

[5] Owens BF; Lipka AE; Magallanes-Lundback M; Tiede T; Diepenbrock CH; Kandianis CB; Kim E; Cepela J; Mateos-Hernandez M; Buell CR; Buckler ES; DellaPenna D; Gore MA; Rocheford T (2014). A foundation for provitamin A biofortification of maize: genome-wide association and genomic prediction models of carotenoid levels.. Genetics. [10.1534_genetics.114.169979]

[6] Hirsch CN; Flint-Garcia SA; Beissinger TM; Eichten SR; Deshpande S; Barry K; McMullen MD; Holland JB; Buckler ES; Springer N; Buell CR; de Leon N; Kaeppler SM (2014). Insights into the effects of long-term artificial selection on seed size in maize.. Genetics. [10.1534_genetics.114.167155]
```

### How author and journal matching works

Without `--exact-author`, `--author` matches papers whose author list
contains **any word** of the value, the same OR rule as keywords. That is
fine for a distinctive surname, but adding initials widens the search
instead of narrowing it:

| Filter | Papers |
|---|---|
| `--author "Buckler"` | 62 |
| `--author "Buckler ES"` | 828 (every author with initials `ES` too) |
| `--author "Buckler ES" --exact-author` | 0 |
| `--author "Wallace JG; Larsson SJ; Buckler ES" --exact-author` | 1 |

`--exact-author` compares against the **whole** author list, exactly as
stored (`Surname Initials`, separated by `; `), so it is useful only for
picking out one known paper. To narrow by author, use a single distinctive
surname and combine it with keywords or `--year`.

`--journal` works the same way. `--journal "Plant Cell"` matches 3,002
papers, because almost every journal name contains "plant" or "cell".
`--exact-journal` needs the journal name exactly as stored, which is the
NLM title, not the familiar short form:

| Filter | Papers |
|---|---|
| `--journal "Plant Cell" --exact-journal` | 0 |
| `--journal "The Plant cell" --exact-journal` | 32 |

Take the stored spelling from the `journal` field of any search result.

### Sorting and paper type

Results are ranked by relevance score. `--sort-by-year` (`"sort_by_year":
true`) puts the newest first instead:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document '"flowering time" AND photoperiod' --sort-by-year --count 3
```

```
[1] Guo Y; Lu X; Li D; Kuang X; Song W; Ye X; Wei Z; Wang M; Wang H; Yang X; Yan Y; Ma Z; Song R; Chen S; Fu J; Li M; Liu W (2026). A novel genetic framework reveals transcriptional "butterfly effect" underlying heterosis in maize.. Journal of advanced research. [10.1016_j.jare.2025.08.021]

[2] Drouault J; Palaffre C; Millet EJ; Rodriguez J; Martre P; Johnson K; Parent B; Welcker C; Wisser RJ (2025). A reaction norm for flowering time plasticity reveals physiological footprints of maize adaptation.. G3 (Bethesda, Md.). [10.1093_g3journal_jkaf095]

[3] Han X; Li J; Li D; Guo L; Wu L; Liang Y; Jia H; Xia J; Qin C; Qin W; Chen Q; Deng D; Wang C; Tian F (2025). A copy number variation in the ZmMADS1 promoter enhances maize adaptation to high altitudes.. Journal of integrative plant biology. [10.1111_jipb.13924]
```

`--paper-type` (`"paper_type"`) filters on the `doc_type` field. Every paper
in `MaizeOpenAccess` is stored as `Journal_article`, reviews included, so
this filter cannot separate reviews from research papers here:
`--paper-type Review` returns nothing.

**API equivalent:** `author` and `year` are plain fields inside `query`;
no keywords are needed.

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeOpenAccess"],"author":"Buckler","year":"2014"},"count":50}' \
  "$BASE/search_documents" | jq -r '.[] | "\(.accession)  \(.title)"'
```

```
10.1038_hdy.2013.6  Entering the second century of maize quantitative genetics.
10.1371_journal.pone.0090346  TASSEL-GBS: a high capacity genotyping by sequencing analysis pipeline.
10.1371_journal.pgen.1004845  Association mapping across numerous traits reveals patterns of functional variation in maize.
10.1534_genetics.113.159152  The genetic architecture of maize height.
10.1534_genetics.114.169979  A foundation for provitamin A biofortification of maize: genome-wide association and genomic prediction models of carotenoid levels.
10.1534_genetics.114.167155  Insights into the effects of long-term artificial selection on seed size in maize.
```

## 9. Excluding keywords

`--exclude` drops results that also contain a given term. For example, it
can exclude papers that mention another species, such as Arabidopsis. The
phrase is quoted so that it matches `kernel weight`, not `kernel` or
`weight` (section 2):

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document '"kernel weight"' --exclude "Arabidopsis" --count 3
```

```
[1] Borrás L; Westgate ME; Otegui ME (2003). Control of kernel weight and kernel water relations by post-flowering source-sink ratio in maize.. Annals of botany. [10.1093_aob_mcg090]

[2] Lang Z; Wills DM; Lemmon ZH; Shannon LM; Bukowski R; Wu Y; Messing J; Doebley JF (2014). Defining the Role of prolamin-box binding factor1 Gene During Maize Domestication.. The Journal of heredity. [10.1093_jhered_esu019]

[3] Lopez-Valdivia I; Vallebueno-Estrada M; Rangarajan H; Swarts K; Benz BF; Blake M; Sidhu JS; Perez-Limon S; Sawers RJH; Schneider H; Lynch JP (2025). In silico analysis of the evolution of root phenotypes during maize domestication in Neolithic soils of Tehuacán.. The New phytologist. [10.1111_nph.70245]
```

The exclusion is strict: a paper is dropped if `Arabidopsis` appears
anywhere in it, even once. Here that takes the phrase from 183 papers to 37. `NOT` inside the keywords (`'"kernel weight" NOT Arabidopsis'`)
does the same thing.

**API equivalent:** `exclude_keywords` is a separate field from `keywords`.

```bash
curl -s -X POST -H "Content-Type: application/json"   -d '{"query":{"type":"document","corpora":["MaizeOpenAccess"],
       "keywords":"\"kernel weight\"","exclude_keywords":"Arabidopsis"},"count":3}'   "$BASE/search_documents"
```

```json
[
  {"year": "2003", "score": 0.524417, "identifier": "MaizeOpenAccess/10.1093_aob_mcg090/10.1093_aob_mcg090.tpcas",
   "title": "Control of kernel weight and kernel water relations by post-flowering source-sink ratio in maize.",
   "author": "Borrás L; Westgate ME; Otegui ME", "accession": "10.1093_aob_mcg090", "journal": "Annals of botany"},
  {"year": "2014", "score": 0.434634, "identifier": "MaizeOpenAccess/10.1093_jhered_esu019/10.1093_jhered_esu019.tpcas",
   "title": "Defining the Role of prolamin-box binding factor1 Gene During Maize Domestication.",
   "author": "Lang Z; Wills DM; ...", "accession": "10.1093_jhered_esu019", "journal": "The Journal of heredity"},
  {"year": "2025", "score": 0.410191, "identifier": "MaizeOpenAccess/10.1111_nph.70245/10.1111_nph.70245.tpcas",
   "title": "In silico analysis of the evolution of root phenotypes during maize domestication in Neolithic soils of Tehuacán.",
   "author": "Lopez-Valdivia I; Vallebueno-Estrada M; ...", "accession": "10.1111_nph.70245", "journal": "The New phytologist"}
]
```

## 10. JSON output for scripting

`--format json` switches to machine-readable output, so results can be
piped into `jq` or parsed in your own script.

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document "anthocyanin" --count 2 --format json
```

```json
[
  {
    "year": "2023",
    "doc_type": "Journal_article",
    "score": 0.759488,
    "identifier": "MaizeOpenAccess/10.3390_molecules28062665/10.3390_molecules28062665.tpcas",
    "title": "Profiling and Quantification of Anthocyanins in Purple-Pericarp Sweetcorn and Purple-Pericarp Maize.",
    "author": "Anirban A; Hong HT; O'Hare TJ",
    "accession": "10.3390_molecules28062665",
    "journal": "Molecules (Basel, Switzerland)"
  },
  {
    "year": "2024",
    "doc_type": "Journal_article",
    "score": 0.696875,
    "identifier": "MaizeOpenAccess/10.3389_fpls.2024.1466756/10.3389_fpls.2024.1466756.tpcas",
    "title": "Comparative transcriptomic and metabolomic analysis reveals mechanisms of selenium-regulated anthocyanin synthesis in waxy maize (<i>Zea mays L.</i>).",
    "author": "Guo G; Wang Y; Zhang B; Yu H; Li L; Cao G; Chen B; Li C; Bu F; Teng S; Yu Q; Gao M; Jiang B; Yang K",
    "accession": "10.3389_fpls.2024.1466756",
    "journal": "Frontiers in plant science"
  }
]
```

**API equivalent:** for a plain search, `--format json` output is the API's
JSON response unchanged (the same call as section 3), so pipe it straight
into `jq`:

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"document","corpora":["MaizeOpenAccess"],"keywords":"anthocyanin"},"count":3}' \
  "$BASE/search_documents" | jq -r '.[].title'
```

```
Profiling and Quantification of Anthocyanins in Purple-Pericarp Sweetcorn and Purple-Pericarp Maize.
Comparative transcriptomic and metabolomic analysis reveals mechanisms of selenium-regulated anthocyanin synthesis in waxy maize (<i>Zea mays L.</i>).
Evaluation of Functional Quality of Maize with Different Grain Colors and Differences in Enzymatic Properties of Anthocyanin Metabolism.
```

`python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document "anthocyanin" --count 3 --format json | jq -r '.[].title'`
gives identical output. Titles keep the publisher's inline HTML (`<i>...</i>`).

With `--annotate`, each JSON result also carries an `ontology_summary`
(the same per-ontology lists as the text output) and the `hygiene` report
from section 5:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --accession 10.3390_molecules28062665 \
  --annotate --format json | jq '.[0] | {accession, genes: .ontology_summary.MAIZE_GENES, hygiene}'
```

```json
{
  "accession": "10.3390_molecules28062665",
  "genes": ["AAT1", "a1", "anthocyanin acyltransferase1", "anthocyaninless1", "pr1", "red", "red aleurone1", "sh2", "shrunken2"],
  "hygiene": {
    "kept": 810,
    "dropped": 305,
    "dropped_by_reason": {"tier_T2_no_context": 107, "tier_T1_case_mismatch": 26, "abbrev_case_mismatch": 2,
                          "tier_TD_undefined": 1, "section": 162, "english_word_gene": 1,
                          "ancestor_of_sibling_on_same_span": 6},
    "italic_coverage": {"gene_annotations": 35, "with_italic_data": 33}
  }
}
```

In JSON, `dropped_by_reason` uses the API's reason codes, not the CLI's
labels. Use it when you need to tell the two kinds of case mismatch apart.

## 11. Full sentence-level annotation with `--annotate-sentences`

For programmatic use, `--annotate-sentences` returns JSON with every
annotated sentence, its character offsets, and each ontology match in it.
This is more detail than the `--annotate` summary, and it is always JSON
regardless of `--format`.

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document --accession 10.3390_molecules28062665 --annotate-sentences --ontology TO
```

```json
[
  {
    "paper": {
      "identifier": "MaizeOpenAccess/10.3390_molecules28062665/10.3390_molecules28062665.tpcas",
      "title": "Profiling and Quantification of Anthocyanins in Purple-Pericarp Sweetcorn and Purple-Pericarp Maize.",
      "author": "Anirban A; Hong HT; O'Hare TJ",
      "year": "2023",
      "journal": "Molecules (Basel, Switzerland)",
      "accession": "10.3390_molecules28062665"
    },
    "annotated_sentences": [
      {
        "begin": 12983,
        "end": 13165,
        "text": "The kernel moisture content of ‘Costa Rica’, ‘Tim’s white’, ‘Tim1’, ‘Tim2’, ‘Tim4’ and ‘Tim5’ at 25 DAP was calculated to be 68.9%, 70.9%, 72.2%, 72.7%, 74.0% and 70.2%, respectively",
        "annotations": [
          {
            "begin": 12994,
            "end": 13010,
            "term": "moisture content",
            "category": "RELATED:moisture content trait (TO:0000228)",
            "ontology": "TO",
            "onto_id": "TO:0000228",
            "tier": "tier_T0"
          }
        ]
      },
      ...
      {
        "begin": 29313,
        "end": 29552,
        "text": "The current investigation revealed variation in total anthocyanin content, anthocyanin ratios, and the degree of anthocyanin malonylation between the purple-pericarp maize parent ‘Costa Rica’ and the derived ‘Tim’ sh2-supersweet accessions",
        "annotations": [
          {
            "begin": 29367,
            "end": 29386,
            "term": "anthocyanin content",
            "category": "anthocyanin content (TO:0000071)",
            "ontology": "TO",
            "onto_id": "TO:0000071",
            "tier": "tier_T0"
          }
        ]
      },
      ...
    ],
    "hygiene": {"kept": 7, "dropped": 0, "dropped_by_reason": {},
                "italic_coverage": {"gene_annotations": 0, "with_italic_data": 0}}
  }
]
```

This paper has 6 annotated sentences for TO, with 7 annotations; two are
shown. The sixth is the `End` match from section 5. Each annotation gives:

- `term`: the text as written in the paper
- `category`: the ontology term it was matched to
- `onto_id`: that term's ID
- `tier`: why the match was kept (section 5); absent for gene annotations

The `hygiene` report here counts only the TO annotations, because
`--ontology TO` was applied before filtering. Every flag from section 5
(`--no-hygiene`, `--no-italics`, `--related-synonyms`) works the same way
with `--annotate-sentences`.

A `RELATED:` prefix on `category` means the match came through a RELATED
synonym. Here, `moisture content` is a RELATED synonym of the TO term
`moisture content trait`, while `anthocyanin content` matched the term's own
name.

By default only sentences with at least one annotation are included. Add
`--full-text` to include every sentence in the paper.

**API equivalent:** the `annotate` endpoint returns `sentences` and
`annotations` as flat lists keyed by character offset, not pre-joined.
Join them with `jq` to reproduce the default (annotated-only) output:

```bash
ID="MaizeOpenAccess/10.3390_molecules28062665/10.3390_molecules28062665.tpcas"
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" --data-urlencode "ontology=TO" | jq '
  .annotations as $anns
  | [.sentences[] | . as $s
     | ($anns | map(select(.begin >= $s.begin and .end <= $s.end))) as $matched
     | select($matched | length > 0)
     | $s + {annotations: $matched}]
'
```

This prints the same `annotated_sentences` list, without the `paper`
wrapper, which the script adds from the search result.

## 12. Precise section exclusion with `--exclude-type`

`--exclude-type` drops results whose only match is inside a given section.
For example, it drops a paper that matches only because a tool or gene name
appears in a bibliography entry.

Four papers in the corpus mention `HelitronScanner`:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document "HelitronScanner"
```

```
[1] Ou S; Scheben A; Collins T; Qiu Y; Seetharam AS; Menard CC; Manchanda N; Gent JI; Schatz MC; Anderson SN; Hufford MB; Hirsch CN (2024). Differences in activity and stability drive transposable element variation in tropical and temperate maize.. Genome research. [10.1101_gr.278131.123]

[2] Jiao Y; Peluso P; Shi J; Liang T; ... Ware D (2017). Improved maize reference genome with single-molecule technologies.. Nature. [10.1038_nature22971]

[3] Lynch BT; Patrick TL; Moreno JJ; Siebert AE; Klusman KM; Shodja DN; Hannah LC; Lal SK (2015). Differential pre-mRNA Splicing Alters the Transcript Diversity of Helitrons Between the Maize Inbred Lines.. G3 (Bethesda, Md.). [10.1534_g3.115.018630]

[4] Martin GT; Solares E; Guadardo-Mendez J; Muyle A; Bousios A; Gaut BS (2023). miRNA-like secondary structures in maize (<i>Zea mays</i>) genes and transposable elements correlate with small RNAs, methylation, and expression.. Genome research. [10.1101_gr.277459.122]
```

Excluding `references` drops two of them. Their only mention of
HelitronScanner is a citation of the tool's own paper:

```bash
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document "HelitronScanner" --exclude-type references
```

```
[1] Ou S; Scheben A; Collins T; Qiu Y; Seetharam AS; Menard CC; Manchanda N; Gent JI; Schatz MC; Anderson SN; Hufford MB; Hirsch CN (2024). Differences in activity and stability drive transposable element variation in tropical and temperate maize.. Genome research. [10.1101_gr.278131.123]

[2] Jiao Y; Peluso P; Shi J; Liang T; ... Ware D (2017). Improved maize reference genome with single-molecule technologies.. Nature. [10.1038_nature22971]
```

**API equivalent:** the API has no `exclude_type` parameter. The script
re-runs the search as `type: "sentence"` to get the matching sentences, then
checks each one's position against the `sections` list from `annotate`.
Two calls per paper, shown here for the Martin et al. paper:

```bash
ACC="10.1101_gr.277459.122"
ID="MaizeOpenAccess/$ACC/$ACC.tpcas"

# 1. Get the matching sentence text
curl -s -X POST -H "Content-Type: application/json" \
  -d "{\"query\":{\"type\":\"sentence\",\"corpora\":[\"MaizeOpenAccess\"],
       \"keywords\":\"HelitronScanner\",\"accession\":\"$ACC\"},
       \"count\":200,\"include_match_sentences\":true}" \
  "$BASE/search_documents" | jq -c '.[0].matched_sentences'
```

```json
["HelitronScanner uncovers a large overlooked cache of Helitron transposons in many plant genomes"]
```

```bash
# 2. Check whether that sentence falls inside a "references" section
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
    "begin": 82841,
    "end": 82936,
    "text": "HelitronScanner uncovers a large overlooked cache of Helitron transposons in many plant genomes"
  },
  "in_references": true
}
```

The only match is in the references, so the paper is dropped, matching the
script's result.

`--exclude-type` is repeatable (for example, `--exclude-type references
--exclude-type acknowledgments`). What it filters depends on the mode:

- **`--type sentence`:** each returned sentence is located in the paper by
  its text, and dropped if it falls in an excluded section. A sentence that
  cannot be located is kept.
- **`--type document`:** a paper is dropped only if **every** match is
  inside excluded sections, checked as shown above. If that cannot be
  determined (for example, the paper's annotation data is unavailable), the
  paper is kept.
- **`--annotate` and `--annotate-sentences`:** the ontology data is filtered
  by section too, whatever `--type` is. This is in addition to the hygiene
  filter's own references/acknowledgments drop (section 5); the
  `exclude_sections` API parameter is the server-side equivalent.

Don't confuse `--exclude-type` with `--exclude` (section 9): `--exclude`
drops papers that contain a *keyword* anywhere, while `--exclude-type` drops
matches by the *section* they fall in.

## 13. Papers that are not open access

Every paper in `MaizeOpenAccess` is open access, so nothing in this section
affects it today. It applies if the server is given papers or corpora marked
as not open access.

Without an API key, such a paper still appears in search results, but with
only its metadata, abstract and first 3 matched sentences. The CLI flags it:

```
[access limited: non-open-access paper: showing metadata, abstract, and the
first few matched sentences only. Pass --api-key KEY (or set $TPC_API_KEY) for
full text.]
```

In JSON, every result then carries `"open_access": true` or `false`, and a
trimmed one also has `"access_limited": true`. The `annotate` endpoint keeps
the full response shape (every sentence and annotation, with offsets,
categories and sections) but blanks each sentence's `text` and each
annotation's `term`, and adds `"access_limited": true`. `--annotate` then
lists category names in place of the withheld terms. `available_corpora` and
`category_search` are never restricted.

Keys are issued by the server operator. Pass one with `--api-key KEY` or by
setting `TPC_API_KEY`. Over HTTP, any of these works:

```bash
curl -H "X-API-Key: $KEY" ...                          # any endpoint
curl -H "Authorization: Bearer $KEY" ...               # any endpoint
-d '{"api_key":"'"$KEY"'","query":{...}}'              # in a POST body
curl -G "$ANNOTATE" --data-urlencode "api_key=$KEY" ...  # annotate query string
```

## 14. Reference

### Endpoints

| Endpoint | Method | Purpose | Sections |
|---|---|---|---|
| `$BASE/available_corpora` | GET | list corpus names | 1 |
| `$BASE/search_documents` | POST, JSON body | search: keywords, metadata, sections, categories | 2-4, 7-9 |
| `$BASE/get_documents_count` | POST, JSON body | total number of matching papers for the same `query` | 2 |
| `$ANNOTATE` | GET, query string | one paper's sentences, annotations and sections, hygiene-filtered | 5, 6, 11, 12 |
| `$CATSEARCH` | GET, query string | look up exact category strings; expand to related terms | 4, 4b |

`annotate` and `category_search` sit next to `api` in the URL, not under
it. `annotate` returns HTTP 404 (`"CAS2 file not found for identifier"`)
for an identifier with no annotation data, and both return 400 when the
required parameter is missing.

### `search_documents` request fields

Only `query.type` and `query.corpora` are required. Leave out a field
rather than sending it empty.

| Field | CLI flag | Notes |
|---|---|---|
| `query.type` | `--type` | `sentence`, `document`, or a section name (section 7). Unknown values search the whole document |
| `query.corpora` | `-c` | list; goes **inside** `query` |
| `query.keywords` | positional | OR between words; `AND`, `NOT`, `"phrases"` and parentheses work; no wildcards (section 2) |
| `query.exclude_keywords` | `--exclude` | drop papers containing these (section 9) |
| `query.case_sensitive` | `--case-sensitive` | default `false` |
| `query.author` | `--author` | any word matches (section 8) |
| `query.exact_match_author` | `--exact-author` | whole author list, exactly as stored |
| `query.journal` | `--journal` | any word matches |
| `query.exact_match_journal` | `--exact-journal` | whole journal name, exactly as stored |
| `query.year` | `--year` | a string, such as `"2014"` |
| `query.accession` | `--accession` | DOI with `/` replaced by `_` |
| `query.paper_type` | `--paper-type` | every `MaizeOpenAccess` paper is `Journal_article` |
| `query.categories` | `--category` | list of exact `"name (ID)"` strings (section 4) |
| `query.categories_and_ed` | `--categories-and` | `true` = all categories; the API defaults to all, the CLI to any |
| `query.sort_by_year` | `--sort-by-year` | newest first instead of by score |
| `count` | `--count` | at most 200 per call; `0`, or any value of 200 or more, returns 200 |
| `since_num` | (automatic) | result offset for paging past 200 |
| `include_match_sentences` | (automatic with `--type sentence`) | only with `type: "sentence"`; with any other type the API returns HTTP 401 |
| `include_all_sentences` | | adds an `all_sentences` list for each paper |
| `include_fulltext` | | adds the `abstract`; `fulltext` comes back empty on this server, so use `annotate` for the text |
| `api_key` | `--api-key` | section 13 |

### `annotate` parameters

`identifier` (required, the `identifier` field of a search result) plus the
per-request switches in the table in section 5: `ontology`,
`related_synonyms`, `hygiene`, `italics`, `tiers` and `exclude_sections`.

### `category_search` parameters

| Parameter | Notes |
|---|---|
| `q` | required: a name, synonym, ID (`PO:0009010`) or exact `"name (ID)"` string |
| `ontology` | repeatable: limit to these ontologies |
| `limit` | default 20; currently capped at 100 (section 4b) |
| `relationship_type` | repeatable: add descendants along this relationship (section 4b) |
| `ancestor_relationship_type` | repeatable: add ancestors along this relationship |

Each match has `id`, `name`, `category` (the string to pass to `--category`
or `categories`), `ontology`, `matched_on` (section 4; `+descendant` or
`+ancestor` marks a term added by expansion), and `relationship_types` /
`parent_relationship_types` (section 4b).
