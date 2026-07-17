# Laura's Work Updates Log

Running log of Textpresso search/ontology/annotation-pipeline work: what was
done, what broke, and how it was fixed. Also doubles as the usage guide for
`bin/tpc_search.py` and `bin/tpc_search_internal.py` (see below).

## Update log — 2026-06-18

### What was done

Investigated the Textpresso REST API and built a set of command-line search
tools on top of it.  The Textpresso instance is running inside a Docker
container (`agr-textpresso-textpresso-1`) and its API is exposed publicly at:

```
http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api/
```

Port 18080 (the API's native port) is blocked externally; the API is proxied
through lighttpd on port 80.  The GUI at `/tpc/search` and these scripts both
talk to the same backend.

**Files created:**

| File | Purpose |
|------|---------|
| `bin/tpc_search.py` | Public search script; no local file access required |
| `bin/tpc_search_internal.py` | Server-only; extends public script with CAS2 ontology annotation |
| `textpresso_classifiers/casannot.py` | Shared library for parsing CAS2 files |

**Key findings during development:**

- The `corpora` list must be nested inside the `query` object in the request
  body, not at the top level.  The example in `agr_textpresso/textpressoapi/example-query.txt`
  shows the correct structure.
- Sending `include_match_sentences: true` with any query type other than
  `"sentence"` returns HTTP 401 (an API quirk in the source, not an auth error).
- Section-scoped types (`abstract`, `result`, etc.) only return results for
  papers whose CAS2 files were annotated with section boundaries by `runAECpp`.
  Not all papers in a corpus have this markup.
- The `get_category_matches_document_fulltext` API endpoint exists but returned
  empty results for MaizeTest100.  Rich ontology annotation data is available
  directly from the CAS2 files, which is the approach taken here.
- CAS2 files are gzip-compressed UIMA XMI.  The `lexicalannotation` elements
  carry exact character-offset spans, the matched term text, and the ontology
  category with accession (e.g. `GO:0048316`).  Entries prefixed with `PTCAT`
  are broader parent-category links added automatically and are filtered out
  by default.
- The Docker container mounts its `/data/textpresso` from
  `/home/ec2-user/agr_textpresso/.data/` on the host.  The default
  `--cas-root` in the internal script points to the host-side path.

---

## Available corpora (as of 2026-06-18)

```
ReproTest062
MaizeTest        (3 papers — small smoke-test corpus)
MaizeTest100     (100 papers — use this for meaningful queries)
MaizeOA          (open-access maize papers — full corpus)
SorghumBase      (sorghum papers — ingest in progress as of this date)
```

---

## `bin/tpc_search.py` — public search script

No local file access required.  Works for any user with network access to the
public server.

### Usage

```
python3 bin/tpc_search.py [options] [keywords]
```

### Search types (`--type`)

| Type | What it searches |
|------|-----------------|
| `sentence` | Full paper text; returns matching sentence text (default) |
| `document` | Full paper text; returns document-level hits only |
| `abstract` | Abstract section only |
| `title` | Title only |
| `introduction` | Introduction section |
| `result` | Results section |
| `discussion` | Discussion section |
| `conclusion` | Conclusion section |
| `background` | Background section |
| `design` | Study design section |
| `materials and methods` | Methods section |
| `acknowledgments` | Acknowledgments section |
| `references` | References section |

Section types require that the paper's CAS2 file was annotated with section
boundaries.  Papers without that markup will not appear in section-scoped
results even if the keyword appears in that section.

### Keyword syntax

The `keywords` argument is passed directly to the Lucene index.  Boolean
operators are supported:

```
"maize AND drought"
"kernel OR grain"
"flowering time"       # phrase treated as AND
```

### All options

```
keywords                  Search keywords
-c CORPUS                 Corpus to search (repeatable; default: all)
--count N                 Max results (default: 50, API max: 200)
--type TYPE               Search scope (default: sentence)
--exclude KEYWORDS        Keywords to exclude
--case-sensitive          Case-sensitive keyword matching
--author NAME             Filter by author (substring match by default)
--exact-author            Require exact author match
--journal NAME            Filter by journal
--exact-journal           Require exact journal match
--year YEAR               Filter by publication year
--accession ID            Filter by DOI / accession
--paper-type TYPE         Filter by paper type (Journal_article, Review, ...)
--category CATEGORY       Restrict to ontology category (repeatable)
--categories-and          Require ALL categories to match (default: ANY)
--sort-by-year            Sort by year instead of relevance score
--format text|json        Output format (default: text)
--url URL                 API base URL
--list-corpora            List available corpora and exit
```

### Examples

```bash
# Basic sentence search
python3 bin/tpc_search.py -c MaizeTest100 "flowering time"

# Multiple corpora
python3 bin/tpc_search.py -c MaizeTest100 -c SorghumBase "drought tolerance"

# Section-scoped
python3 bin/tpc_search.py -c MaizeTest100 --type abstract "drought"

# Metadata filters
python3 bin/tpc_search.py -c MaizeTest100 --author "Buckler" --year 2022 "GWAS"
python3 bin/tpc_search.py -c MaizeTest100 --journal "Nature" --exact-journal "maize"

# Keyword modifiers
python3 bin/tpc_search.py -c MaizeTest100 --exclude "Arabidopsis" "kernel weight"
python3 bin/tpc_search.py -c MaizeTest100 --case-sensitive "ZmMADS"

# JSON output (pipe-friendly)
python3 bin/tpc_search.py -c MaizeTest100 "anthocyanin" --format json | jq '.[].title'

# List available corpora
python3 bin/tpc_search.py --list-corpora
```

---

## `bin/tpc_search_internal.py` — server-side search with annotation

Identical search interface to `tpc_search.py`, with additional flags that read
the local CAS2 files.  Requires access to the CAS2 data directory.

**CAS2 root paths:**

| Context | Path |
|---------|------|
| Host (outside container) | `/home/ec2-user/agr_textpresso/.data/tpcas-2` |
| Inside the container | `/data/textpresso/tpcas-2` |

The default `--cas-root` is set to the host path.

### Additional options

```
--annotate              Append an ontology term summary to each result
--annotate-sentences    Output sentence-level annotation JSON
--full-text             With --annotate-sentences: include unannotated sentences
--ontology NAME         Restrict annotations to GO, PO, TO, MAIZE_GENES, or OTHER
                        (repeatable; default: all ontologies)
--cas-root PATH         CAS2 root directory
```

### `--annotate` — ontology term summary per paper

Appends a grouped list of all ontology terms found anywhere in the paper to
each result.  Works with text output (default) or JSON (`--format json`).

```bash
python3 bin/tpc_search_internal.py -c MaizeTest100 "anthocyanin" --annotate
```

Text output adds an "Ontology annotations" block after each paper's matched
sentences:

```
[1] Author (year). Title. Journal. [accession]
  - matched sentence ...
  Ontology annotations:
    GO: anthocyanin biosynthesis, biosynthetic process, pigmentation, seed development, ...
    MAIZE_GENES: R1, pl1
    PO: endosperm, kernel, pericarp, seed, ...
    TO: heterosis
```

JSON output (`--format json`) adds an `ontology_summary` key to each result
object.

### `--annotate-sentences` — full sentence-level annotation JSON

Always outputs JSON.  For each result paper:

```json
{
  "paper": {
    "identifier": "MaizeTest100//10.1038_srep35479/10.1038_srep35479.tpcas",
    "title": "...",
    "author": "...",
    "year": "2016",
    "journal": "Sci Rep",
    "accession": "10.1038_srep35479"
  },
  "search_matched_sentences": ["sentence that matched the search keyword", ...],
  "annotated_sentences": [
    {
      "begin": 2615,
      "end": 2681,
      "text": "fie three endosperm development stages are distinct but overlapped",
      "annotations": [
        {
          "begin": 2625, "end": 2646,
          "term": "endosperm development",
          "category": "seed development (GO:0048316)",
          "ontology": "GO",
          "onto_id": "GO:0048316"
        }
      ]
    }
  ]
}
```

By default `annotated_sentences` contains only sentences that have at least
one annotation.  Add `--full-text` to include all sentences in the paper.

```bash
# Annotated sentences only (default)
python3 bin/tpc_search_internal.py -c MaizeTest100 "anthocyanin" --annotate-sentences

# All sentences
python3 bin/tpc_search_internal.py -c MaizeTest100 "anthocyanin" \
    --annotate-sentences --full-text

# Filter to GO and PO only
python3 bin/tpc_search_internal.py -c MaizeTest100 "anthocyanin" \
    --annotate-sentences --ontology GO --ontology PO
```

---

## `textpresso_classifiers/casannot.py` — CAS2 parsing library

Shared module used by `tpc_search_internal.py`.  Can also be imported
directly for custom analysis.

```python
import importlib.util, os
spec = importlib.util.spec_from_file_location(
    "casannot", "textpresso_classifiers/casannot.py")
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)

# Use importlib (not a regular import) to avoid triggering the package
# __init__.py, which imports sklearn/numpy.

cas_path = ca.identifier_to_cas_path(
    "MaizeTest100//10.1038_srep35479/10.1038_srep35479.tpcas",
    cas_root="/home/ec2-user/agr_textpresso/.data/tpcas-2"
)
sentences, annotations = ca.parse_cas_file(cas_path)
enriched = ca.annotate_sentences(sentences, annotations)
summary  = ca.summarize_by_ontology(annotations)
```

**Functions:**

| Function | Returns |
|----------|---------|
| `identifier_to_cas_path(identifier, cas_root)` | Filesystem path to the `.tpcas.gz` file |
| `parse_cas_file(cas_path)` | `(sentences, annotations)` — see below |
| `annotate_sentences(sentences, annotations)` | Sentences with overlapping annotations attached |
| `summarize_by_ontology(annotations)` | `{ontology_label: [sorted unique terms]}` |

`parse_cas_file` returns:
- **sentences** — `[{begin, end, text}]`, sorted by position in the document
- **annotations** — `[{begin, end, term, category, ontology, onto_id}]`;
  PTCAT (parent-category) entries are excluded by default

---

## Update log — 2026-06-25

### RELATED synonym support

#### Background

Textpresso OBO entries can carry multiple synonym types.  Previously only
`EXACT` synonyms were indexed and searchable.  For the maize gene ontology
(`tpzma`), this meant that legacy genome identifiers and cross-references
carried as `RELATED` synonyms were invisible to the search system.

Example: the `adh1` entry (`tpzma:0000072`) has both EXACT synonyms (`adh1`)
and RELATED synonyms (`Zm00001d033931`, `GRMZM2G442658`, `alcohol
dehydrogenase1`, `X00580`).  Before this change, only `Zm00001e004758` and
`adh1` were indexed; the RELATED synonyms matched nothing.

#### C++ changes required (agr_textpresso)

**File:** `agr_textpresso/textpressocentral/Tpso/main.cpp`

The `PopulateDataVector` function in `Tpso/main.cpp` is where synonym rows are
written into the `tpontology` PostgreSQL table.  This is the file that matters:
`tpso` (the binary that CreateLexica.bash calls) is built from `Tpso/main.cpp`
directly and does not link `TpOntApi.cpp`.

The synonym-parsing loop was updated to collect RELATED synonyms alongside EXACT
ones, then write each RELATED synonym as a separate row with `RELATED:` prepended
to the category name:

| `eid` | `term` | `category` |
|-------|--------|------------|
| `tpzma:0000072` | `Zm00001e004758` | `gene (Z. mays) (tpzma:0000072)` |
| `tpzma:0000072` | `adh1` | `gene (Z. mays) (tpzma:0000072)` |
| `tpzma:0000072` | `Zm00001d033931` | `RELATED:gene (Z. mays) (tpzma:0000072)` |
| `tpzma:0000072` | `GRMZM2G442658` | `RELATED:gene (Z. mays) (tpzma:0000072)` |
| `tpzma:0000072` | `alcohol dehydrogenase1` | `RELATED:gene (Z. mays) (tpzma:0000072)` |
| `tpzma:0000072` | `X00580` | `RELATED:gene (Z. mays) (tpzma:0000072)` |

The `PTCAT` prefix convention already existed in the codebase for parent
category links; `RELATED:` follows the same pattern and requires no changes to
the annotator (`TpLexiconAnnotatorFromPg`), which already loads every
`(term, category)` row from PostgreSQL without filtering.

**Gotcha: the Docker image ships a pre-compiled `tpso` binary and removes the
source after build.** `./initialize.sh -t` inside the container will not
recompile `tpso` from the patched source unless the source has been re-copied
in.  To apply this patch to an existing container:

```bash
# 1. Copy patched source into the container
docker cp agr_textpresso/textpressocentral \
    agr-textpresso-textpresso-1:/data/textpresso/textpressocentral

# 2. Build just the tpso target (cmake cache already present from image build)
docker exec agr-textpresso-textpresso-1 bash -lc "
  cd /data/textpresso/textpressocentral &&
  mkdir -p build && cd build &&
  cmake -DCMAKE_BUILD_TYPE=Release .. -DCMAKE_INSTALL_PREFIX=/usr/local &&
  make -j4 tpso &&
  cp tpso /usr/local/bin/tpso
"

# 3. Re-run tpso to repopulate tpontology with RELATED entries
docker exec agr-textpresso-textpresso-1 bash -lc "
  echo 'drop table ontologymembers' | psql www-data
  tpso -j /usr/local/etc/tpsoinput.json
  echo 'grant all privileges on table ontologymembers to \"www-data\"' | psql www-data
"

# 4. Regenerate lexical variations
docker exec agr-textpresso-textpresso-1 bash -lc "
  for t in \$(echo 'select tablename from pg_tables' | psql www-data | grep tpontology); do
    generatelexicalvariations \$t &
  done; wait
"

# 5. Verify RELATED entries are present before re-annotating
docker exec agr-textpresso-textpresso-1 bash -lc "
  psql -At -d www-data -c \
    \"select count(*) from tpontology_classical_maize_genes_0 where category like 'RELATED:%';\"
"
# Expected: non-zero (325 for the current maize gene OBO)

# 6. Re-annotate (CAS-1 → CAS-2) and re-index
docker exec agr-textpresso-textpresso-1 bash -lc "
  annotate -c /data/textpresso/tpcas-1 -C /data/textpresso/tpcas-2 -t /data/textpresso/tmp -P 2
  index   -C /data/textpresso/tpcas-2 -i /data/textpresso/luceneindex
"
```

For a **fresh image build** (rebuilding the Docker image from scratch), the
patch to `Tpso/main.cpp` will be compiled automatically by `initialize.sh -t`
and no manual steps are needed.

#### Python changes

**`textpresso_classifiers/casannot.py`**

Added `MAIZE_GENES_RELATED` to `_ONTOLOGY_PREFIXES`, checked before
`MAIZE_GENES` so that a category string like
`RELATED:gene (Z. mays) (tpzma:0000072)` is not misclassified as a plain
`MAIZE_GENES` match:

| `ontology` label | Matches |
|------------------|---------|
| `MAIZE_GENES` | EXACT synonym matches (unchanged behaviour) |
| `MAIZE_GENES_RELATED` | RELATED synonym matches (new) |

**`bin/tpc_search_internal.py`**

New flag and updated option:

```
--related-synonyms      Include RELATED-synonym annotations in output
                        (default: excluded; ignored when --ontology is set)
--ontology NAME         Now also accepts MAIZE_GENES_RELATED
```

Default behaviour is unchanged — RELATED annotations are excluded unless
explicitly requested, so existing scripts produce the same output as before.

#### Updated additional options table

Supersedes the options table in the `tpc_search_internal.py` section above.

```
--annotate              Append an ontology term summary to each result
--annotate-sentences    Output sentence-level annotation JSON
--full-text             With --annotate-sentences: include unannotated sentences
--related-synonyms      Include RELATED-synonym annotations (new)
--ontology NAME         Restrict to GO, PO, TO, MAIZE_GENES, MAIZE_GENES_RELATED,
                        or OTHER (repeatable; default: all except MAIZE_GENES_RELATED)
--cas-root PATH         CAS2 root directory
```

#### Usage examples

```bash
# default: EXACT synonyms only (same as before this change)
python3 bin/tpc_search_internal.py -c MaizeTest100 "adh1" --annotate

# include RELATED synonyms (legacy IDs like GRMZM2G026930 also reported)
python3 bin/tpc_search_internal.py -c MaizeTest100 "adh1" --annotate --related-synonyms

# RELATED-only matches (to audit which papers were found via legacy IDs)
python3 bin/tpc_search_internal.py -c MaizeTest100 "adh1" \
    --annotate --ontology MAIZE_GENES_RELATED

# both EXACT and RELATED via explicit --ontology (equivalent to --related-synonyms)
python3 bin/tpc_search_internal.py -c MaizeTest100 "adh1" \
    --annotate --ontology MAIZE_GENES --ontology MAIZE_GENES_RELATED

# sentence-level with RELATED synonyms included
python3 bin/tpc_search_internal.py -c MaizeTest100 "adh1" \
    --annotate-sentences --related-synonyms
```

#### Observed output (MaizeTest100, "adh1")

Verification run against the live index after the rebuild described above.
`--annotate` (default, EXACT-only) and `--annotate --related-synonyms` return
the same 4 papers with identical annotations, **except** paper [4] (Char et
al. 2017, CRISPR/Cas9 maize paper, `10.1111_pbi.12611`), where the RELATED
run adds one line:

```
# default (EXACT-only)
[4] Char SN, Neelakandan AK, ... (2017). An Agrobacterium-delivered CRISPR/Cas9
    system for high-frequency targeted mutagenesis in maize.. Plant Biotechnol J.
    [10.1111_pbi.12611]
  Ontology annotations:
    GO: CA, Cas, Cat, DNA binding, DNA repair, ... ubiquitin
    MAIZE_GENES: Adh1, a1
    PO: anthers, leaf, mesophyll, plant cells, pollen, seed, seedlings, seeds, stem

# with --related-synonyms
[4] Char SN, Neelakandan AK, ... (2017). An Agrobacterium-delivered CRISPR/Cas9
    system for high-frequency targeted mutagenesis in maize.. Plant Biotechnol J.
    [10.1111_pbi.12611]
  Ontology annotations:
    GO: CA, Cas, Cat, DNA binding, DNA repair, ... ubiquitin
    MAIZE_GENES: Adh1, a1
    MAIZE_GENES_RELATED: GRMZM2G026930
    PO: anthers, leaf, mesophyll, plant cells, pollen, seed, seedlings, seeds, stem
```

Papers [1]–[3] have no RELATED matches for this query, so
`--ontology MAIZE_GENES_RELATED` alone returns them with an empty
`Ontology annotations:` block and only paper [4] populated:

```
[4] Char SN, Neelakandan AK, ... [10.1111_pbi.12611]
  Ontology annotations:
    MAIZE_GENES_RELATED: GRMZM2G026930
```

---

## Update log — 2026-07-10

### Replacing `classical_maize_genes.obo` with a genome-scale OBO file

#### Background

`classical_maize_genes.obo` (82 genes, a hand-curated classical-genetics gene
list) was replaced with `zmays_genes_20260708.obo` — a genome-scale file with
24,969 gene terms and ~186k total rows once RELATED/EXACT synonyms are
expanded. `ontology.conf` was updated to point at the new file and
`CreateLexica.bash` / `tpso` was re-run, followed by `annotate` + `index`
scoped to `MaizeTest100`.

#### Symptom: GUI "Categories" tree showed 0 children — this is expected, not a bug

After the reindex, the Textpresso Central GUI's Categories browser showed
"Z. mays gene (tpzm:0000000)" with **0 children**, which looked like the
ingest had failed. It hadn't. **Textpresso's category-tree builder caps any
node at 200 children.** In `textpressocentral/Tpso/main.cpp`,
`GrowTreeFromObo()`:

```cpp
// set restriction on category: number of children
for (auto it = growntree.begin(); it != growntree.end(); it++)
    if (it.number_of_children() > 200)
        for (auto cit = it.begin(); cit != it.end(); cit++)
            (*cit).second = (*it).second;
```

If a category would have more than 200 direct children, all of them get
collapsed into the parent's category label rather than becoming their own
tree nodes (`WriteRelationstables()` only emits a `pcrelations` row for a node
whose own category still equals itself, i.e. one that wasn't collapsed). The
old `classical_maize_genes.obo` had only 82 genes — under the cap — so every
gene showed up as its own tree node. The new file has 24,969 genes as direct
children of the root term, so **all of them collapse into "Z. mays gene"**
and the tree legitimately has nothing to show at that level. This is a
curation-UI browsability limit, not a data-loading or annotation failure —
it will happen for any OBO file with a flat structure of more than ~200
leaf terms under one parent.

The genes are still fully loaded into the ontology lexicon and are still
found in full-text annotation; see verification steps below.

#### How to verify a large OBO file actually loaded and is being used (don't trust the GUI tree for large ontologies)

```bash
# 1. tpontology table has the expected row count (~7-8 rows per gene: id, EXACT
#    locus match, several RELATED synonym rows)
docker exec agr-textpresso-textpresso-1 bash -lc \
  "psql -At -d www-data -c \"select count(*) from tpontology_<stem>_0;\""

# 2. ontologymembers lists the new stem (confirms tpso/CreateLexica.bash ran)
docker exec agr-textpresso-textpresso-1 bash -lc \
  "psql -At -d www-data -c \"select list from ontologymembers order by list;\""

# 3. After annotate has run, grep a re-annotated CAS2 file directly for the
#    new ontology's ID prefix — this is the definitive check that terms are
#    actually being matched in paper text (the GUI tree is not this check):
zcat /home/ec2-user/agr_textpresso/.data/tpcas-2/<corpus>/<accession>/<accession>.tpcas.gz \
  | grep -oE '<[a-zA-Z:]*lexicalannotation[^>]*category="[^"]*<stem prefix>:[0-9]+\)"[^>]*/?>' | head

# Example from this run — confirmed "a1"/"a4" (real maize gene symbols) matched
# under the new ontology in the Char et al. 2017 CRISPR/Cas9 maize paper:
#   <textpresso:lexicalannotation ... term="a1" category="Z. mays gene (tpzm:0000000)" .../>
#   <textpresso:lexicalannotation ... term="a4" category="Z. mays gene (tpzm:0000000)" .../>
```

Note: `--category` filtering via the REST API (`tpc_search.py --category ...`)
returned "No results" for both the old and a known-good GO category during
this check — that's the pre-existing `get_category_matches_document_fulltext`
limitation already noted in the June 2026 entry above, not something specific
to this ontology swap. Don't use it as a verification signal.

#### Gotcha: stale ontology tables from a replaced OBO file aren't dropped automatically

`CreateLexica.bash`/`tpso` only (re)creates tables for OBO files currently
listed in `ontology.conf`. Removing a line from `ontology.conf` (e.g.
`classical_maize_genes.obo`) does **not** drop that stem's
`tpontology_<stem>_0` / `pcrelations_<stem>` tables — they stay in Postgres.
The `annotate` step's table-merge logic (`tpctools/07cas1tocas2.sh`) blindly
unions **every** table matching `tpontology*` / `pcrelations*` it finds via
`select tablename from pg_tables`, regardless of `ontology.conf` membership.
Result: papers got re-annotated with **both** the new ontology's terms and
leftover terms from the old, removed one (confirmed here: the Char et al. CAS2
file had 113 stale `tpzma:` annotations alongside 386 new `tpzm:` ones before
cleanup).

Fix — drop the orphaned tables for any OBO file removed from `ontology.conf`,
then re-annotate/re-index so old category strings are purged from CAS2 files:

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
  echo 'drop table tpontology_<old_stem>_0' | psql www-data
  echo 'drop table pcrelations_<old_stem>' | psql www-data
"

# touch CAS-1 files for the affected corpus to force reprocessing, then
# re-run annotate + index scoped to that corpus (see step 4/5 in the
# runbook — AWS_explore.sh in this repo's sibling agr_textpresso checkout)
```

#### Gotcha: `casannot.py`'s MAIZE_GENES regex was hardcoded to the old ontology's ID prefix

`textpresso_classifiers/casannot.py` classified ontology categories by
regexing the accession prefix (`GO:`, `PO:`, `TO:`, `tpzma:`). The new OBO
file uses a **different** ID prefix — `tpzm:` (no trailing `a`) — since it's
a distinct ontology stem (`zmays_genes_20260708`) from the old
`classical_maize_genes` (`tpzma:`). Before the fix, every match from the new
ontology fell into the catch-all `OTHER` bucket instead of `MAIZE_GENES` in
`--annotate` / `--annotate-sentences` output — annotations existed and were
correct in the CAS2 files, but this repo's own summary/search tooling
mislabeled them.

Fixed in `_ONTOLOGY_PREFIXES` (casannot.py) by making the trailing `a`
optional so both prefixes match one pattern:

```python
("MAIZE_GENES_RELATED", re.compile(r"^RELATED:.*\btpzma?:\d+")),
("MAIZE_GENES",         re.compile(r"\btpzma?:\d+")),
```

This is forward-compatible with any future maize-gene OBO stem as long as its
ID prefix continues to start with `tpzm`. If a future OBO uses an unrelated
prefix, add a new tuple to `_ONTOLOGY_PREFIXES` rather than editing this one.

#### Checklist for adding/replacing a large (>200-term) OBO file

1. Place the `.obo` file in `/data/textpresso/obofiles4production/` (host:
   `/home/ec2-user/agr_textpresso/.data/obofiles4production/`).
2. Add/update its line in `textpressocentral/etc/ontology.conf`, then
   `docker cp` it into the container's runtime copy at
   `/usr/local/etc/ontology.conf` (the repo copy is not mounted live).
3. If replacing an existing OBO file for the same category, note its old
   table stem — you'll need to drop `tpontology_<old_stem>_0` and
   `pcrelations_<old_stem>` after the new one is confirmed working (see
   gotcha above). Don't drop it beforehand; `tpso` needs the old
   `ontologymembers` row gone from `ontology.conf`, not the table.
4. Run `CreateLexica.bash` (drops+repopulates `ontologymembers`, runs `tpso`,
   then `generatelexicalvariations` per new `tpontology_*` table).
5. Verify with the DB queries above — **do not** rely on the GUI Categories
   tree if the OBO has more than ~200 terms under one parent; check
   `tpontology_<stem>_0` row count and `ontologymembers` instead.
6. Touch the corpus's CAS-1 files/dirs to force reprocessing, then run
   `annotate` (CAS-1 → CAS-2) and `index` (CAS-2 → Lucene) scoped to the
   affected corpus.
7. Verify the new ontology's ID prefix actually appears in a re-annotated
   CAS2 file's `lexicalannotation` elements (grep example above) — this is
   the real pass/fail signal, not the GUI tree.
8. Drop the old OBO's orphaned `tpontology_*`/`pcrelations_*` tables (if
   replacing), and re-run annotate + index once more to purge stale
   annotations from CAS2 files.
9. If using this repo's search tooling (`tpc_search_internal.py`,
   `casannot.py`) to inspect results, confirm `_ONTOLOGY_PREFIXES` in
   `casannot.py` recognizes the new OBO's ID prefix — update it if the stem
   uses a prefix not already covered.

---

## Update log — 2026-07-10 (continued): auditing `zmays_genes_20260708.obo` for generic-word synonyms

### Background

After confirming the new OBO's genes were being correctly found in text (see
above), a spot check of the `--annotate` output on the Char et al. 2017 paper
showed obviously-wrong `MAIZE_GENES` hits: `binding`, `promoter`, `zinc
finger`, `expression`, `red`, `endonuclease`. These are common English words
or generic molecular-biology terms, not gene mentions — but they were present
in the OBO as legitimate-looking `synonym:` lines, so the annotator matched
them correctly per its rules. The question was how to find *all* such
entries systematically, not just the ones that happened to show up in one
test paper.

### Method: document frequency across an unrelated, mixed-topic corpus

A synonym that names one specific gene should only be matched in the small
number of papers that actually discuss that gene. A synonym that is really a
common word will be matched across a large fraction of *any* corpus,
regardless of topic. So: parse every re-annotated CAS2 file in `MaizeTest100`
(110 papers, topically unrelated to each other beyond "maize"), count, for
each matched term text (case-folded), how many distinct papers it appears in
(`doc_freq`) and how many times total (`total_count`), excluding `PTCAT`
parent-category duplicates. Rank by `doc_freq` descending.

Saved as a reusable tool: **`bin/ontology_synonym_audit.py`**. Scans a
corpus's CAS2 files for lexicalannotation categories matching a given
ontology ID-prefix substring, ranks matched terms by document frequency, and
(with `--obo-file`) traces each reported term back to the OBO `[Term]`
block(s) and `synonym:` line(s) that produced it (id, name, synonym
type/source).

```bash
python3 bin/ontology_synonym_audit.py \
  --corpus MaizeTest100 \
  --id-prefix "tpzm:" \
  --min-doc-freq 5 \
  --obo-file /home/ec2-user/agr_textpresso/.data/obofiles4production/zmays_genes_20260708.obo
```

### Findings (MaizeTest100, 110 papers, zmays_genes_20260708 ontology)

- 1,781 distinct term strings were matched under `tpzm:` categories across
  the corpus. Most (1,689) appeared in fewer than 5 documents — consistent
  with genuine, gene-specific matches (a real locus ID or allele name should
  only come up in papers about that gene).
- **92 terms appeared in ≥5 of 110 papers (≥4.5% of the corpus)** — this is
  the reportable set. The top of that list is almost entirely generic words:
  `sci` (79 docs), `red` (60), `expression` (54), `binding` (44), `ca` (40),
  `promoter` (36), `late` (36), `transcription factor` (34), `mrna` (28),
  `rs` (26), `aba` (18), `mining` (16), `transferase` (14), etc.
- **Tracing those 92 terms back to the OBO: 99 of ~110 matched OBO entries
  (49 `EXACT locus_synonym` + 48 `RELATED locus_synonym` = 97, plus a
  handful of `EXACT locus`/`locus_name`) come from the `synonym: ... 
  locus_synonym []` field.** 10 of the 92 terms had zero direct OBO matches —
  those are auto-generated lexical variations (plurals/inflections, e.g.
  `promoters`, `bins`, `mining`, `marked`) of already-generic base terms,
  produced by `generatelexicalvariations` from an already-bad base synonym.
- Root cause: the `locus_synonym` field (59,291 of 315,610 synonym lines in
  the whole OBO — the single largest synonym category) appears to carry
  fragments of free-text gene descriptions as if they were standalone
  synonyms. E.g. `tpzm:0023372` (`ubi1`, ubiquitin1) has
  `synonym: "promoter" EXACT locus_synonym []` sitting alongside legitimate
  synonyms like `ubiquitin1`, `UBQ`, `ZmUBQ1` — "promoter" reads like a
  fragment of "ubiquitin1 promoter" that got split into its own line during
  OBO generation. `tpzm:0010898` (`cl14466_1`) has `synonym: "binding" EXACT
  locus_synonym []` with no visible whole phrase it could be a fragment of in
  that block — same failure mode, unclear exact origin.

### Important nuance: not every high-doc-freq term is wrong

Some terms with `doc_freq ≥ 5` are legitimate — famous, well-studied
classical maize genes with short 2-character symbols that are genuinely
mentioned across many papers, e.g. `c1`, `b1` (5 docs each) both traced back
to `kind=NAME`/`EXACT locus` (i.e. the gene's actual registered symbol, not
a `locus_synonym` fragment). Document frequency is a heuristic for
*surfacing candidates*, not an automatic classifier — the `locus_synonym`
field concentration (99/110 of traced OBO entries above) is the more
reliable signal for what to actually remove, since it isolates the specific
noisy field rather than penalizing short-but-real gene symbols.

### Suggested next steps (not yet applied — a curation decision, not a bug fix)

1. Review `synonym: ... locus_synonym []` lines in
   `zmays_genes_20260708.obo` specifically — this is the field responsible
   for nearly all the false positives found here. Whatever script generates
   this OBO from the source data (MaizeGDB) is likely splitting a
   description field into fragments rather than keeping only whole alias
   names.
2. A cheap first filter: drop `locus_synonym` entries that are (a) a single
   common English word, or (b) look like generic molecular-biology jargon
   with no digits/mixed-case/gene-ID pattern (e.g. reject bare lowercase
   dictionary words, keep anything with a digit, an uppercase-after-lowercase
   transition, or a hyphen, which are typical of real gene symbols/IDs).
3. Re-run `bin/ontology_synonym_audit.py --min-doc-freq 5` after any OBO
   edit to confirm the flagged terms are gone and no new ones appeared, then
   follow the standard re-ingest checklist above (`CreateLexica.bash` →
   touch CAS-1 → `annotate` → `index`).
4. This audit only covered `MaizeTest100` (110 papers). Re-run against
   `MaizeOA` (the full open-access maize corpus) once available for a larger,
   more statistically reliable sample before finalizing any synonym
   denylist.

---

## Update log — 2026-07-13

Follow-up on the 2026-07-10 generic-word-synonym audit above. Four separate
fixes landed this session, all against `zmays_genes_20260708.obo` /
`MaizeTest100`.

### 1. Lexical variants disabled for the maize gene ontology

The audit's "10 of the 92 terms had zero direct OBO matches" finding (e.g.
`marked`, `promoters`, `mining`) traced to `generatelexicalvariations`
auto-inflecting already-bad generic-word synonyms, plus real gene symbols
that are also common words (e.g. `MARK`, a real maize gene, inflecting to
`marked`/`marking`/`marks`). Fix: `agr_textpresso/textpressocentral/Tpso/CreateLexica.bash`
now skips `generatelexicalvariations` for any table matching
`tpontology_zmays_genes*`:

```bash
case "$i" in
    tpontology_zmays_genes*)
        continue
        ;;
esac
```

Patched in the repo source, the `.data` mirror, and the container's deployed
copy at `/usr/local/bin/CreateLexica.bash`. Confirmed live: `MARK`'s
`lexicalvariations` column is now empty. This is a prefix match, so it stays
forward-compatible with future dated OBO stems (e.g. `zmays_genes_20261001`).

### 2. Per-gene category collapse fixed (200-child cap)

`--category` search for an individual gene (e.g. `r1 (tpzm:0020929)`) always
returned nothing. Root cause: `GrowTreeFromObo()` in
`agr_textpresso/textpressocentral/Tpso/main.cpp` collapses any category node
with more than 200 direct children into its parent's category label:

```cpp
if (it.number_of_children() > 200)
    for (auto cit = it.begin(); cit != it.end(); cit++)
        (*cit).second = (*it).second;
```

This was previously assumed (2026-07-10 entry above) to be a GUI-tree-only
cosmetic limit. It isn't — the same mutated tree is passed into
`WriteOntologytable()`/`PopulateDataVector()`, which writes the `category`
column into Postgres. Since all 24,969 genes in `zmays_genes_20260708.obo`
are direct children of one root term (`tpzm:0000000`), every one of them
was collapsed to the single category `Z. mays gene (tpzm:0000000)` — the
whole table had only 2 distinct category values, `Z. mays gene (tpzm:0000000)`
and `RELATED:Z. mays gene (tpzm:0000000)`. Full-text term matching still
worked fine (the `term` column was never touched), but no gene could ever be
searched by its own category.

Fix — added a named constant and raised it well above maize's current gene
count:

```cpp
// TextpressoCentralGlobalDefinitions.h
#define CATEGORYMAXCHILDREN 100000
```

```cpp
// Tpso/main.cpp, GrowTreeFromObo()
if (it.number_of_children() > CATEGORYMAXCHILDREN)
```

Checked GO/PO/TO aren't practically affected by the global cap raise (their
`tpontology_*` tables already have thousands of distinct categories, so no
node in those hierarchies was hitting the 200 limit in practice).

**Gotcha hit while deploying this:** `docker cp agr_textpresso/textpressocentral
agr-textpresso-textpresso-1:/data/textpresso/textpressocentral` — the exact
command documented in the 2026-06-25 RELATED-synonym section above — silently
nested the copy under itself (`.../textpressocentral/textpressocentral/...`)
instead of overwriting, because the destination directory already existed
from that earlier session's copy. Two full rebuild-and-test cycles showed no
effect before this was caught (`docker exec ... find ... -iname main.cpp`
showed both the correct top-level path *and* the stale nested duplicate). If
`/data/textpresso/textpressocentral` already exists in the container,
`docker cp` individual changed files directly to their exact destination
paths instead of copying the whole directory.

After fixing the deploy and rebuilding `tpso` for real: `tpontology_zmays_genes_20260708_0`
went from 2 distinct categories to 40,420; `r1` confirmed as its own category
(`r1 (tpzm:0020929)`). Rebuilt lexica, re-annotated `MaizeTest100` (CAS-1 →
CAS-2, scoped to just that corpus via a symlinked staging tree, same pattern
as `scripts/install_sorghum_ontologies.sh`'s `reannotate_sorghum_corpus`),
and reindexed. Confirmed in the re-annotated CAS2 file: `category="r1 (tpzm:0020929)"`
now appears directly (279 distinct `tpzm:` categories in the Char et al. 2017
paper alone, up from 4).

Backed up `tpontology*`/`pcrelations*`/`ontologymembers` via `pg_dump` to
`agr_textpresso/.data/backups/ontology-<timestamp>/` before dropping/rebuilding,
following the existing pattern in `install_sorghum_ontologies.sh`.

### 3. `--category` REST search: a red herring and the real fix

After fix #2, `tpc_search.py --category "r1 (tpzm:0020929)"` *still* returned
"No results" — but so did a hand-picked "known-good" GO category
(`"anthocyanin biosynthesis"`), which looked like it confirmed the
pre-existing `get_category_matches_document_fulltext`-endpoint limitation
already noted in the 2026-06-18/2026-07-10 entries above. **That conclusion
was wrong** — the GO test was malformed (missing the `(GO:xxxxx)` ID suffix
that real stored categories always include; the correctly-formatted string
`"anthocyanin-containing compound metabolic process (GO:0046283)"` works
fine via `--category`, as does a short PO category like `"seed (PO:0009010)"`).
So the REST `--category` path itself is not universally broken — it's a
plain Lucene phrase-query match against `sentence_cat`/`fulltext_cat` fields
(`Query::add_categories_to_text()` in `agr_textpresso/libtpc/DataStructures.cpp`),
and it works when tested with a correctly-formatted category string.

The real cause was specific to every `tpzm:` category (root and per-gene
alike, both before and after fix #2): **`IndexManager` caches Lucene
`IndexReader`s for the lifetime of the process and never reopens them.**
`get_subreaders()` in `agr_textpresso/libtpc/IndexManager.cpp`:

```cpp
if (readers_map.find(index_id) == readers_map.end()) {
    if (exists(path(index_id + "/segments.gen")))
        readers_map[index_id] = IndexReader::open(...);
}
```

The running `textpressoapi` process (`ps aux`, PID 3953663) had been up
since **July 8** — before `zmays_genes_20260708.obo` was even first loaded
(July 10). It had never once seen any `tpzm:` category data, collapsed or
not, regardless of any reindexing done since, including everything in fix
#2. GO/PO/TO categories worked because that data predates July 8 and was
already in the reader's snapshot at startup. This also means the July 10
ontology swap was likely never actually visible via the REST API either,
though nobody had reason to check `--category` specifically until now.

Also found (but did **not** chase further, since it's not what `--category`
uses): `get_category_matches_document_fulltext` — a separate, dedicated
endpoint — returns HTTP 500 for every query tried, GO included. Unrelated to
the reader-staleness bug; if this endpoint is needed later it wants its own
investigation.

**Fix:** restarted `textpressoapi` (`kill -TERM` the old PID, relaunch with
the exact command from `/root/start_textpresso.sh`:
`textpressoapi -d /data/textpresso/textpressoapi_data/tokens.db`, via
`docker exec -d` so it survives after the exec session ends). Confirmed
`--category "r1 (tpzm:0020929)"` now returns correct results, against both
the local API (`localhost:18080`) and the public proxy
(`abd-textpresso.phoenixbioinformatics.org`).

**Takeaway for future ontology/index changes:** reindexing alone (writing
new Lucene segments to disk) is not sufficient for changes to become visible
via the REST API — `textpressoapi` must be restarted afterward to drop its
stale `IndexReader` cache. Add this as a step to the OBO-reload checklist in
the 2026-07-10 entry above whenever this guide's checklist is next revised.
Also noted: the container had accumulated dozens of `<defunct>` zombie
`textpressoapi` child processes going back to May, suggesting this isn't the
first time the process has exited/crashed without being relaunched or
reaped — worth a look if this becomes a recurring problem.

---

## Update log — 2026-07-13 (continued): fixing section detection (`--type references` etc.)

### Background

Spot-checking `bin/tpc_search_internal.py --annotate-sentences` output for
`MARK` (see fixes #1 above) surfaced results that were technically correct
matches but not useful ones — e.g. `HK` (from `Xiong W, He L, Lai J, Dooner
HK, Du C. 2014. HelitronScanner...`) matching `RELATED:hex7 (tpzm:0014921)`,
and separately `sci`/`SCI` (matching a `locus_synonym`) turning up in journal
abbreviations like "Plant Sci". Both terms are genuinely present in the OBO
as short `locus_synonym` entries (same generic-word contamination as the
2026-07-10 audit), but the specific complaint here was that they were coming
from the **bibliography**, not the paper's actual content — not something a
synonym-quality fix alone addresses, since the terms aren't wrong everywhere,
just unhelpful when matched inside citation text.

The API/GUI already expose a `--type references` search scope (and
`abstract`, `introduction`, `result`, etc.), suggesting section-boundary
detection should already let this kind of noise be filtered out. Investigated
whether that mechanism could be used to identify (and optionally exclude)
bibliography text.

### Finding: section detection has never worked for any paper in this deployment

`org.apache.uima.textpresso.section` (the CAS annotation type `--type
references`/`abstract`/etc. read from) had **zero occurrences** in every
CAS file sampled across every corpus (`MaizeTest100`, `MaizeTest`,
`ReproTest062`, `SorghumBase`, `PMCOA`). This isn't "some papers lack
markup" as previously assumed — it's universal.

### Root cause

Section boundaries are detected by `TdTokenizer` (the live tokenizer for the
PDF pipeline — confirmed via `CASManager.h`'s
`TAI2TPCAS_DESCRIPTOR = "/usr/local/uima_descriptors/TdTokenizer.xml"`,
which `articles2cas -t 4` — i.e. the `tokenize` step — loads). It uses an
exact-match trie (`trieSection_`) to search the raw document text for
isolated heading strings like `"References\n"` (see
`tp_uima_globals::sectionReferences()` in `uimaglobaldefinitions.h`).

The raw document text carries inline PDF layout markers produced by text
extraction, e.g. `<_pdf _cr/>`, `<_pdf _fsc=+11/>`, embedded directly in the
character stream (confirmed: these appear literally, unescaped, in the CAS
sofa text — not just as a display artifact). A heading is typically followed
by a marker before the actual line break, so the literal text is:

```
References <_pdf _cr/>\n
```

— which never contains the exact substring `"References\n"` the trie
searches for. Since PDF extraction inserts one of these markers at (almost)
every line break, this fails for every heading, in every paper, always —
matching the "zero everywhere" finding above.

(Initially misidentified the offending paper for the `HK`/Tidyverse example
above — traced it to the wrong CAS file at first, corrected by grepping
across the corpus for the actual citation text. Correct paper:
`MaizeTest100/10.1101_gr.277459.122`.)

### Fix (agr_textpresso/libtpc)

**File:** `libtpc/uima-annotators/TdTokenizer/TdTokenizer.cpp`

Added `CleanPdfTagsForSectionSearch()`: builds a copy of the document text
with every `<_pdf ...>` marker removed, and any space left dangling directly
before a newline as a result (the marker is almost always preceded by a
word-separating space) collapsed away — while recording, for every character
kept, its offset in the original text, so match positions found in the
cleaned copy can be translated back to the original document. Only the
section-heading trie search (`trieSection_->searchAllWords(...)`) was
switched to use this cleaned buffer; token and sentence boundary detection
are untouched, since those were already working correctly and weren't part
of this problem.

**File:** `libtpc/uima-annotators/uimaglobaldefinitions.h`

Per request, expanded `sectionReferences()` to also recognize `Bibliography`,
`Literature Cited`, `References Cited`, and `Works Cited` (same
normal/letter-spaced/uppercase/uppercase-letter-spaced variants as the
existing entries) — all still classified under the single `references` type,
so no other code needed to change. Other section types (`abstract`,
`introduction`, etc.) weren't expanded — worth a similar pass later if
useful, but out of scope here.

#### Gotcha: repeated the exact `docker cp` nesting mistake from earlier today

`libtpc` had never been copied into the container before. Ran
`mkdir -p /data/textpresso/libtpc` "to be safe" immediately before `docker
cp`, which meant the destination now existed and the copy nested itself
(`/data/textpresso/libtpc/libtpc/...`) — the identical failure mode
documented in the fix-#2 entry above, self-inflicted this time by creating
the directory first instead of letting `docker cp` create it. Fixed by
`rm -rf`-ing the wrapper and re-copying to the not-yet-existing path
directly. **Lesson reinforced: never `mkdir` the destination before `docker
cp`-ing a directory into a container — let `docker cp` create it.**

#### Build

`TdTokenizer` is a separate CMake target from `tpso`/`textpressoapi`
(`libtpc/CMakeLists.txt`, `add_library(TdTokenizer SHARED ...)`, deployed as
`/usr/local/lib/TdTokenizer.so` — no `lib` prefix,
`CMAKE_SHARED_LIBRARY_PREFIX` is explicitly cleared in the project, since
UIMA C++ annotators are `dlopen()`'d directly by the path/name in their
descriptor XML rather than resolved through the normal linker). Built with:

```bash
docker cp agr_textpresso/libtpc agr-textpresso-textpresso-1:/data/textpresso/libtpc
docker exec agr-textpresso-textpresso-1 bash -lc "
  cd /data/textpresso/libtpc && mkdir -p build && cd build &&
  cmake -DCMAKE_BUILD_TYPE=Release .. -DCMAKE_INSTALL_PREFIX=/usr/local &&
  make -j4 TdTokenizer
"
docker exec agr-textpresso-textpresso-1 bash -lc "
  cp /usr/local/lib/TdTokenizer.so /usr/local/lib/TdTokenizer.so.bak-\$(date -u +%Y%m%dT%H%M%SZ)
  cp /data/textpresso/libtpc/build/TdTokenizer.so /usr/local/lib/TdTokenizer.so
"
```

`TdTokenizer` only links `boost_regex`/Python, not `libtextpresso`, so this
built in seconds without needing the much heavier `libtextpresso` target
that shares the same `CMakeLists.txt`.

#### Gotcha: re-tokenizing an existing paper needs two prerequisite steps neither prior session nor this repo's tooling documented

Re-running `tokenize` (`articles2cas -t 4`) on an already-ingested paper is
**not** as simple as touching CAS-1 and re-running `annotate`/`index` (the
pattern used everywhere else in this log for ontology changes) — `tokenize`
operates one stage earlier and has its own prerequisites:

1. **`articles2cas -t 4` (`tai`/text-and-image file type) reads pre-extracted
   per-page `.txt` files (and images) from the *same directory as the PDF***
   (`textAndImageManager::loadTextFilenames()` — literally just lists
   `*.txt` next to the PDF). These are **not** present in
   `raw_files/pdf/<corpus>/<accession>/` by default — they're a transient
   intermediate produced by a separate tool, `pdf2txtimg` (invoked via
   `tpctools/tai.sh`, symlinked as `convert_text`), and appear to get
   cleaned up/never persisted after the original ingest. Confirmed:
   production `raw_files/pdf/.../10.1101_gr.277459.122/` contained only the
   `.pdf`, no `.txt` siblings, even though this paper already has CAS-1/CAS-2
   from a prior successful run.
2. **`convert_text`/`tai.sh` backgrounds its `pdf2txtimg` calls
   (`timeout 5m pdf2txtimg $i &`) with no final `wait`** — running it via a
   single `docker exec` invocation let the background job get killed when
   the exec session ended, silently producing no output. Had to invoke
   `pdf2txtimg` directly and synchronously instead:
   `pdf2txtimg <path-to-pdf>`.
3. **The `tokenize` wrapper script (`03pdf2cas4tai.sh`) can't be used to
   re-process an existing paper at all.** Its "does this need reprocessing"
   logic is two separate checks, and neither fits this case: (a) a `-nt`
   mtime comparison against the *parent directory* (not file) — touching the
   PDF file itself doesn't change its containing directory's mtime, so this
   silently no-ops; (b) even after working around that, the second loop only
   runs `articles2cas` while
   `$(ls "${CAS1_DIR}/${folder}" | wc -l) -lt $(ls "${PDF_DIR}/${folder}" | wc -l)`
   — a **count** of output vs. input directories. Since the CAS-1 output
   directory for an existing paper already exists, the counts are already
   equal and the loop body never executes, regardless of staleness. This
   script is additive-only (built for *new* papers appearing), not usable
   for reprocessing existing ones after a tokenizer fix. Worked around by
   invoking `articles2cas` directly, matching its real argument convention
   (confirmed from the script): run from the `tpcas-1` root as CWD, with
   `-i <raw_files/pdf>/<corpus>`, `-o <corpus-or-any-relative-dir>`,
   `-l <listfile-of-relative-accession-paths>`, `-t 4 -p`.
4. Also needed `export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH`
   before invoking `articles2cas`/`pdf2txtimg`/`convert_text` directly — set
   automatically by the wrapper scripts, not by login shell profile.

### Verification (single paper: `MaizeTest100/10.1101_gr.277459.122`)

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
  export LD_LIBRARY_PATH=/usr/local/lib:\$LD_LIBRARY_PATH
  pdf2txtimg /data/textpresso/raw_files/pdf/MaizeTest100/10.1101_gr.277459.122/10.1101_gr.277459.122.pdf
  echo '10.1101_gr.277459.122' > /data/textpresso/tmp/list.txt
  cd /data/textpresso/tmp && articles2cas -i /data/textpresso/raw_files/pdf/MaizeTest100 \
    -l /data/textpresso/tmp/list.txt -t 4 -o out -p
"
```

Result — 7 sections detected where there were previously 0, including a
`references` section spanning offsets 73935–90491 whose `content` is
*exactly* the paper's bibliography, containing both originally-reported
false-positive sources verbatim: `Xiong W, He L, Lai J, Dooner HK, Du C.
2014. HelitronScanner...` and `Wickham H, Averick M, Bryan J, ... 2019.
Welcome to the Tidyverse...`.

```
type="beginning of article"  (1)
type="materials and methods" (2)
type="result"                (1)
type="discussion"            (1)
type="acknowledgments"       (1)
type="references"            (1)
```

### Not yet done (superseded below — see synonym expansion + rollout)

This fix was verified on one paper, in a scratch output directory — not yet
applied to the corpus's real `tpcas-1`, and not yet propagated through
`annotate` (CAS-1 → CAS-2), `index`, or a `textpressoapi` restart (see fix #3
above — required after any reindex for changes to become visible via the
API).

---

## Update log — 2026-07-13 (continued): section-heading synonym expansion, and can one span have multiple types?

### Question: can the same text be assigned more than one section `type`?

Asked before expanding synonyms further, prompted by papers that use a single
combined header like "Results and Discussion". Answer: **yes, and no code
change is needed for it — just heading-set membership.**

`TdTokenizer::combineSectionAnnotations()` (`TdTokenizer.cpp`) is called once
per section type (`"result"`, `"discussion"`, etc.), each time with its own
independent heading-name set, and each call creates its own `section`
annotation for any raw heading tile that matches its set — there's no
mutual-exclusivity check anywhere. So if the same heading string (e.g.
`"Results and Discussion\n"`) is a member of *both* `sectionResult()` and
`sectionDiscussion()`, when that heading is found, both calls independently
match it and each creates a `section` annotation over the identical span —
one `type="result"`, one `type="discussion"`. Downstream,
`Tpcas2SingleIndex.cpp`'s `addSectionFields()` iterates every `section`
annotation and builds `<type>`/`<type>_cat` Lucene fields per annotation, so
both `--type result` and `--type discussion` would find that text. Confirmed
this is exactly how the fix below was implemented — no new type, no
structural change, just adding the same heading string to more than one set.

### Synonym expansion (`libtpc/uima-annotators/uimaglobaldefinitions.h`)

Since a full re-tokenize was already needed for the section-detection fix
above, took the opportunity to review heading coverage for all section types,
not just `references`:

| Function | Added |
|----------|-------|
| `sectionAbstract()` | `Summary` |
| `sectionIntroduction()` | `Literature Review` |
| `sectionResult()` + `sectionDiscussion()` | `Results and Discussion` (combined header, tagged as both `result` and `discussion`) |
| `sectionDiscussion()` + `sectionConclusion()` | `Discussion and Conclusion(s)` (combined header, tagged as both) |
| `sectionConclusion()` | `Concluding Remarks` |
| `sectionMaterialsMethods()` | `Experimental Procedures` |

Each addition follows the file's existing convention: plain, letter-spaced
(`S u m m a r y`, single space between letters — some PDF fonts render
headings with visible letter gaps), ALL CAPS, and ALL CAPS letter-spaced
variants.

**Deliberately left alone** (flagged, not decided unilaterally): `Background`
as a synonym for `Introduction`, and vice versa. Some journals do use
"Background" as their introduction section, but others use it as a genuinely
distinct section — unlike the combined headers above (which are truly one
physical heading meant to cover both meanings at once), merging these would
risk misclassifying whichever sense doesn't apply in a given paper. Left as
two separate, non-cross-linked types unless asked to merge them.

Rebuilt and redeployed `TdTokenizer.so` the same way as above (`touch` the
changed header so `make` actually recompiles — see the earlier `docker cp`
nesting gotcha for why this matters — then `make -j4 TdTokenizer`, copy to
`/usr/local/lib/TdTokenizer.so`). Re-verified against the same test paper:
all 7 expected section types detected correctly (`abstract`,
`acknowledgments`, `beginning of article`, `discussion`,
`materials and methods` ×2, `references`, `result`) — this particular paper
uses separate `Results`/`Discussion` headings rather than the combined form,
confirming the new combined-header entries don't interfere with normal
single-heading detection.

### Full-corpus rollout (`MaizeTest100`, 110 papers) — completed

Backed up both `tpcas-1/MaizeTest100` and `tpcas-2/MaizeTest100` (tar.gz to
`/data/textpresso/backups/`) before starting.

1. `pdf2txtimg` on all 110 PDFs in parallel
   (`find ... -print0 | xargs -0 -n 1 -P 8 -I{} timeout 300 pdf2txtimg {}`,
   run synchronously via `xargs`, not backgrounded — see the earlier
   `convert_text`/`tai.sh` backgrounding gotcha for why that matters).
   Verified per-page `.txt` output existed for every paper (1615 files, zero
   papers missing) before continuing.
2. `articles2cas -i raw_files/pdf/MaizeTest100 -l <full-listfile> -t 4
   -o MaizeTest100 -p`, run from `/data/textpresso/tpcas-1` as CWD, writing
   directly into production. All 110 `.tpcas` files produced, then
   `pigz -f`'d to match the corpus's normal `.tpcas.gz` convention.
   Spot-checked section types on 2 papers before proceeding further.
3. `annotate` (CAS-1 → CAS-2), scoped to `MaizeTest100` via the symlinked
   staging-tree pattern used earlier in this log (fix #2/#3 above).
4. `index` (CAS-2 → Lucene), full reindex.
5. Restarted `textpressoapi` (`kill -TERM` + relaunch via `docker exec -d`,
   same as fix #3 above — required for the new index to become visible via
   the REST API).

### Verification

```bash
# --type references now works and returns sensible results
python3 bin/tpc_search.py -c MaizeTest100 --type references "Wickham" --count 3
# -> correctly finds 10.1101_gr.277459.122 (the paper this whole
#    investigation traced back to) among the results

# regression checks -- confirm nothing else broke
python3 bin/tpc_search.py -c MaizeTest100 "flowering time" --count 2   # plain search: OK
python3 bin/tpc_search.py -c MaizeTest100 --type sentence \
    --category "r1 (tpzm:0020929)" --count 2                          # category search: OK
```

CAS2 for `10.1101_gr.277459.122` confirmed with all 7 section types
(`abstract`, `acknowledgments`, `beginning of article`, `discussion`,
`materials and methods` ×2, `references`, `result`) correctly propagated
through `annotate`.

### Net effect

- `--type references`/`abstract`/`introduction`/etc. section-scoped search
  is now functional for `MaizeTest100`, for the first time in this
  deployment's history (previously 0 section annotations existed anywhere).
- Downstream tooling (this repo's `casannot.py`/`tpc_search_internal.py`, or
  any future work) can now use section boundaries to exclude bibliography
  text from full-text/document-level gene-mention results — the original
  motivating problem (`HK`, `sci` matching inside citations) — by filtering
  on the `references` section span, without needing a synonym-quality fix to
  do it. (The synonym-quality fix from fix #1 above is still independently
  worthwhile and already applied — the two fixes address different sources
  of the same class of noisy match.)
- Combined section headers (`Results and Discussion`,
  `Discussion and Conclusion(s)`) are now tagged under both constituent
  types simultaneously, so `--type result` and `--type discussion` both
  return that content when a paper uses the combined form.

### Not yet done

- This rollout only covered `MaizeTest100`. Other corpora (`MaizeTest`,
  `PMCOA`, `ReproTest062`, `SorghumBase`) still have zero section
  annotations and would need the same treatment if section-scoped search is
  needed there.
- `casannot.py`/`tpc_search_internal.py` don't yet have a flag to exclude
  `references`-section annotations from `--annotate`/`--annotate-sentences`
  output by default — the section data now exists to support this, but nothing
  reads it yet. Natural next step if the bibliography-noise problem needs a
  client-side fix in this repo's tooling too.

---

## Update log — 2026-07-13 (continued): `--exclude-type` in `tpc_search.py` / `tpc_search_internal.py`

### Feature

Added `--exclude-type TYPE` (repeatable) to both search scripts, using the
section boundary data from the fixes above to exclude bibliography (or any
other section) noise from results — e.g.
`--type sentence --exclude-type references` to drop matches that come from
citations. Precision differs by script since only one has local CAS2 access:

**`tpc_search_internal.py` (precise, CAS2-based):**
- `--type sentence`: filters `matched_sentences` directly. Each API-returned
  sentence string is mapped back to a CAS2 position by exact text match
  (`_filter_matched_sentences`), then dropped if that position falls in an
  excluded section. Unmatched/ambiguous strings are kept rather than guessed
  at.
- `--type document`: the API returns no matched-sentence text for document
  queries, so instead of filtering individual matches, drops the whole
  document if **every determinable match is within the excluded section**.
  `_document_has_match_outside_excluded` re-runs the search scoped to just
  that one document with `--type sentence` (which covers the whole document,
  named sections and "uncovered" prose alike — not just named sections), maps
  each real match to a CAS2 position, and keeps the document if any match
  falls outside every excluded section. Kept (not dropped) whenever match
  status can't be determined (missing accession/corpus, API error, no CAS2
  file) — conservative by design, to avoid hiding a document with a real
  match elsewhere.
- `--annotate` / `--annotate-sentences`: filters the ontology summary /
  annotated-sentences list by CAS2 section, independent of `--type`, since
  these read the whole CAS2 file rather than the search match itself — so
  `--exclude-type` works with `--annotate --type document` too.

**`tpc_search.py` (best-effort, no CAS2 access):** only supported with
`--type document` (errors otherwise, pointing at `tpc_search_internal.py`).
`apply_document_level_exclusion` re-runs the query once per *other* named
section type (abstract, introduction, results, etc.) and keeps a document if
it matches in any of them — proof of a match outside the excluded section.
**Known gap, documented in `--help`:** a match sitting in prose not covered
by any detected section boundary won't be found by this check, so such a
document could be incorrectly dropped — `tpc_search_internal.py`'s
`--type sentence`-based check doesn't have this gap, since it covers the
whole document rather than only named sections.

### Bugs found and fixed while building/testing this

1. **`casannot.py`'s sentence/lexicalannotation regexes were silently
   dropping ~17% of sentences** (631 of 761, `10.1101_gr.277459.122`), a
   pre-existing bug unrelated to today's other changes. `_get_sofa`/
   `parse_cas_file` used `r"<textpresso:sentence\s[^/]*/>"` — `[^/]*`
   assumes no attribute value contains a literal `/`, but sentence `content`
   and section `content` attributes routinely contain DOIs/URLs (e.g.
   `doi:10.1007/s004380100514`), so the match silently stopped at the first
   `/` and `re.finditer` skipped that element entirely — no error, just
   missing data. Fixed by switching to non-greedy `r"<textpresso:sentence\s.*?/>"`
   with `re.DOTALL` for all three element regexes (sentence,
   lexicalannotation, and the new section regex). Caught because the new
   `--exclude-type` verification test showed 0 sections parsed for a file
   that definitely had a `references` section — tracing that led straight to
   this.
2. **API identifier format is inconsistent by query type.** `--type document`
   (and, it turns out, `--type sentence`/`--type references` too when tested
   directly) returns identifiers with a single slash after the corpus name
   (`MaizeTest100/acc/acc.tpcas`), not always the double-slash form
   (`MaizeTest100//acc/acc.tpcas`) `identifier_to_cas_path`'s docstring
   documents and that this session had only seen up to this point. Original
   `_document_has_match_outside_excluded` code split on `"//"` to extract the
   corpus name for the supplementary query, which silently found nothing and
   fell back to "keep" for every single-slash identifier — meaning the
   `--type document` precise-exclusion path never actually worked until this
   was found. Fixed by splitting on a single `"/"` instead
   (`identifier.split("/")[0]`), which correctly extracts the corpus name
   regardless of how many slashes follow it. (`identifier_to_cas_path` itself
   was already robust to both forms — `.replace("//", "/")` is a no-op when
   there's nothing to collapse.)
3. **`search()` can return `None` instead of `[]` for zero results.**
   `apply_document_level_exclusion`/`_document_has_match_outside_excluded`
   both iterate a supplementary `search()` call's return value directly;
   crashed with `TypeError: 'NoneType' object is not iterable` the first
   time a supplementary query genuinely matched nothing. Fixed with
   `search(...) or []` at both call sites.

### Verification

```bash
# tpc_search_internal.py, --type sentence: bibliography-only sentences dropped,
# legitimate in-text citations (Methods section) kept
python3 bin/tpc_search_internal.py -c MaizeTest100 --type sentence \
    --accession 10.1101_gr.277459.122 "Wickham" --exclude-type references
# -> 2 of 4 matched sentences kept (the two Methods-section mentions);
#    the two pure-bibliography lines dropped

# tpc_search_internal.py, --annotate: HK->hex7/hex4 false positives gone
python3 bin/tpc_search_internal.py -c MaizeTest100 --type document \
    --accession 10.1101_gr.277459.122 --annotate --exclude-type references
# -> "HK" no longer appears in the MAIZE_GENES_RELATED summary

# tpc_search_internal.py, --type document: precise per-document drop
python3 bin/tpc_search_internal.py -c MaizeTest100 --type document \
    "HelitronScanner" --exclude-type references
# -> No results (HelitronScanner appears only in the reference list)

# tpc_search.py, --type document: same case via the cruder named-section proxy
python3 bin/tpc_search.py -c MaizeTest100 --type document \
    "HelitronScanner" --exclude-type references
# -> No results (agrees with the precise check here, since this term
#    genuinely doesn't appear in any other named section either)
```

Also re-ran the standard regression set (plain `tpc_search.py` search,
`--annotate-sentences` without `--exclude-type`) to confirm no behavior
changed for existing usage.

---

## Update log — 2026-07-13 (continued): re-running `ontology_synonym_audit.py` with `--exclude-type references`

### Change

Added `--exclude-type` (repeatable) to `bin/ontology_synonym_audit.py`,
matching the flag added to the search scripts above. Rewrote `scan_corpus()`
to use `casannot.parse_cas_file()` (loaded via the same `importlib` pattern
as `tpc_search_internal.py`) instead of its own hand-rolled regex, so it
gets the section-detection fix, the `--exclude-type` filtering
(`casannot.exclude_sections`), and the earlier regex-truncation bugfix for
free, rather than duplicating (and risking re-introducing) that class of
bug. Removed the now-unused `gzip`/`html` imports and the old `ANNOT_RE`.

### Re-run: `MaizeTest100`, `zmays_genes_20260708.obo`, `--min-doc-freq 5`

```bash
python3 bin/ontology_synonym_audit.py --corpus MaizeTest100 --id-prefix "tpzm:" \
  --min-doc-freq 5 --exclude-type references \
  --obo-file /home/ec2-user/agr_textpresso/.data/obofiles4production/zmays_genes_20260708.obo
```

2,053 matches excluded as reference-section-only. Reportable term count
(doc_freq ≥ 5) dropped from 65 to 50. Comparing before/after doc_freq per
term:

- **Terms that dropped below the doc_freq ≥ 5 threshold entirely once
  references were excluded** — confirms these were bibliography artifacts,
  not genuine generic-word contamination in paper content: `hk` (13→0, the
  `Dooner HK`/`hex7`/`hex4` example that started this whole investigation),
  `cs` (12→0), `td` (12→0), `leucine-rich repeat` (8→0), `nr` (6→0),
  `cytochrome p450` (6→0).
- **Large reductions but still reportable**: `rs` (27→7), `as` (21→8), `ea`
  (16→6), `gs` (16→6), `expression` (64→51), `binding` (53→43).
- **Unaffected** (exactly the same doc_freq before/after): `red` (70),
  `p1` (14) — confirms the 2026-07-10 nuance note that these are genuinely
  frequent, legitimate gene symbols (`r1`, `p1`), not noise concentrated in
  citations.

### Root cause is unchanged

The `locus_synonym` field is still responsible for the overwhelming majority
of what's reportable: of 111 traced OBO entries across the 50 reportable
terms (post-exclusion), 104 (94%) are `locus_synonym` (71 `RELATED` + 33
`EXACT`). Excluding references cleans up the citation-artifact half of the
noise (author initials, journal abbreviations coincidentally matching short
synonyms) but doesn't touch the *other* source of noise identified on
2026-07-10 — generic-word/description-fragment synonyms sitting in real
paper text (e.g. `red`, `expression`, `binding`, `promoter`, none of which
are citation-related; these are genuine in-text matches of bad synonyms).
The 2026-07-10 entry's suggested next steps (reviewing/filtering
`locus_synonym` entries in the OBO itself) are still the relevant fix for
that remaining half — `--exclude-type` and the `locus_synonym` cleanup are
complementary, not substitutes for each other.

Full reports saved for reference: before/after term-frequency tables and the
complete OBO trace with the synonym-source tally are reproducible via the
command above (with/without `--exclude-type references`).

---

## Update log — 2026-07-13 (continued): `MaizeOA` overnight ingest — launch

### Background

`MaizeOA` (1438 open-access maize papers) already had PDFs staged
(`raw_files/pdf/MaizeOA`, present before this session, likely from a prior
manual staging step) but had never been through `tokenize`/`annotate`/
`index` — 0 files in `tpcas-1/MaizeOA`, `tpcas-2/MaizeOA`, and an empty
`luceneindex/MaizeOA` placeholder. Asked to get the full pipeline running
overnight, unsupervised.

### Blocker found before starting: disk at 100% capacity

`/data/textpresso` was a 50GB volume with **433MB free** — a hard blocker,
since even the low end of a naive projection for the full 1438-paper corpus
(scaling from real `MaizeTest100` numbers: 110 papers → 959M raw+extracted,
891M `tpcas-1`, 53M `tpcas-2`) comes to roughly **25–28GB needed**, nowhere
close to fitting.

**Cleanup performed** (freed 433MB → 11GB):
- `raw_files/maize_pdf/MaizeOA` (6.2G) — verified byte-for-byte redundant
  with `raw_files/pdf/MaizeOA` via `diff -rq` (0 differing files, exactly
  1438 matched on each side, just flat-file vs. per-accession-subdirectory
  layout). `raw_files/pdf/MaizeOA` is the pipeline-standard layout
  (`tai.sh`/`articles2cas` expect `<corpus>/<accession>/<accession>.pdf`),
  so the flat copy was the redundant one.
- `/data/textpresso/tpcas-1-batch/SorghumBase` (8.9G) — verified redundant
  with the official `tpcas-1/SorghumBase`: identical accession count (570)
  and matching sample content on both sides. Looked like intermediate batch
  staging output that had already been merged into the real location and
  never cleaned up (files dated May 6–19, over a month stale).
- `libtpc/` and `textpressocentral/` source+build trees (229M + 79M) — my
  own `docker cp` copies from earlier today's `TdTokenizer`/`tpso` rebuilds;
  binaries already deployed to `/usr/local/{lib,bin}`, source trees no
  longer needed.
- `luceneindex.bk/` (384M) — backup from an earlier reindex today, already
  superseded by a second, verified-successful reindex later the same day.
  `db.bk/` (13M, similar vintage) left alone — small, lower urgency.
  `backups/tpcas-{1,2}-MaizeTest100-pre-retokenize-*.tar.gz` (887M) — my own
  safety-net backups from today's retokenize work, no longer needed since
  that rollout was fully verified.

Even after all of this, ~11GB free is still well short of the ~25–28GB the
*full* 1438-paper corpus would need — confirmed with the user rather than
risk an overnight disk-full failure partway through. Decision: run a
disk-safe **subset** instead of the full corpus.

### Subset sizing and safety design

Selected the first 500 accessions alphabetically
(`ls raw_files/pdf/MaizeOA | sort | head -500`) — 2.0GB of raw PDFs, scaling
to a projected ~8.7–9.75GB total through all stages, leaving a real margin
against the 11GB available. Wrote `/tmp/run_maizeoa_overnight.sh`
(deployed into the container, launched via `docker exec -d` so it survives
the launching session ending) covering all five stages:

1. `pdf2txtimg` (per-page text + images), 6-way parallel via `xargs`-style
   job control, `timeout 300` per file
2. `articles2cas -t 4 -p` (tokenize, CAS-1) — scoped to the subset via `-l`
   listfile, picks up the patched `TdTokenizer` (section detection) and
   `uimaglobaldefinitions.h` synonym expansion automatically since those are
   already the live deployed binary
3. `annotate` (CAS-1 → CAS-2), scoped via the same symlinked-staging-tree
   pattern used for `MaizeTest100` earlier today
4. `index` (CAS-2 → Lucene), full reindex
5. `textpressoapi` restart (required for the new index/corpus to become
   visible via the REST API — see the 2026-07-13 `IndexReader` caching entry
   above)

Safety measures, since this runs genuinely unsupervised overnight:
- **Live disk-space circuit breaker**: checked before each new file in
  stage 1 and before starting each subsequent stage; stops enqueuing new
  work (doesn't kill in-flight work) if free space drops below a 1.5GB
  floor, and writes a partial-completion summary rather than continuing
  blind. Known gap: no fine-grained check *during* `articles2cas`/`annotate`/
  `index` themselves (single monolithic C++ invocations, not per-file
  scriptable without a rebuild) — residual risk accepted given the ~2GB
  margin already built into the subset sizing.
- Only papers that actually produced extracted text (checked via presence
  of `<accession>.00001.txt`) are carried into tokenize/annotate — covers
  both disk-guard skips and individual `pdf2txtimg` failures/timeouts.
- Per-stage start/end timestamps, and a background sampler logging free
  disk + free memory + summed RSS of the pipeline's own processes
  (`pdf2txtimg`, `articles2cas`, `runAECpp`, `indexmerger`, `textpressoapi`)
  every 30s throughout the run, so real timing/memory numbers are available
  afterward without having had to babysit it live.
- A final summary file (paper counts per stage, final disk usage, stage
  wall-clock times, peak per-process RSS) written on both successful
  completion and on any disk-guard abort.

Verified healthy shortly after launch: 43 of 500 papers extracted within the
first few minutes, disk usage stable, no errors in the log.

Remaining 938 papers (of 1438) not included in tonight's run — a follow-up
batch once this one's real resource usage is known and/or more disk is
available.

### Results — TO BE FILLED IN once the run completes

Logs: `/data/textpresso/logs/maizeoa-overnight-20260713T200030Z.{log,summary.txt,resources.log,stage_times.txt}`
and per-stage logs `maizeoa-overnight-{convert_text,tokenize,annotate,index}-20260713T200030Z.log`.

- [ ] Stage wall-clock times (convert_text / tokenize / annotate / index / restart_api)
- [ ] Peak per-process RSS during the run
- [ ] Final paper counts that made it through each stage (extraction / CAS-1 / CAS-2)
- [ ] Final disk usage and whether the circuit breaker ever fired
- [ ] Whether `MaizeOA` appears in `--list-corpora` / is searchable after the restart

---

## Update log — 2026-07-14: overnight `MaizeOA` run stalled — deadlock bug in the orchestration script, diagnosed and fixed

### Symptom

Checked in the next day (GUI didn't show `MaizeOA` as added). The overnight
run's main log (`maizeoa-overnight-20260713T200030Z.log`) had stopped at 220
bytes, still showing only the very first "STAGE START: convert_text" line
from 20:00 UTC the night before — but the background resource-sampler log
(`.resources.log`) had kept growing right up to the moment I checked
(13:39 UTC the next day, ~17.5 hours later). No summary file was ever
written. This pattern — one log frozen, a different log still actively
updating — was the tell that the script hadn't crashed, it had **hung**.

### Root cause: self-inflicted deadlock between `exec > >(tee ...)` and a bare `wait`

The script (`/tmp/run_maizeoa_overnight.sh`, see the launch entry above) did
two things that don't mix:

1. `exec > >(tee -a "${RUN_LOG}") 2>&1` — routes all of the script's own
   output through a `tee` process via process substitution, so it goes to
   both the terminal and the log file.
2. A resource-monitoring loop launched as a background job with `&`, running
   `while true; do ...; sleep 30; done` — **deliberately infinite**, meant to
   keep sampling disk/memory for the whole run and only get killed by an
   `EXIT` trap at the very end.

The monitor subshell inherited the same stdout as the rest of the script
(the `tee` pipe from #1), and never explicitly redirected it away. A pipe
only reports EOF to its reader once every process holding its write end has
closed it — and the monitor's infinite loop meant that write end stayed open
forever. Then, right after stage 1 (`convert_text`) finished launching all
its `pdf2txtimg` background jobs, the script called a bare `wait` (no
arguments) intending to wait for just those jobs. **Bare `wait` waits for
every background job of the shell**, not just the ones the caller has in
mind — including the monitor subshell. Since the monitor never exits on its
own, and the `tee` pipe it was keeping open would never see EOF either way,
`wait` blocked forever. Stage 1 had actually already succeeded (confirmed:
all 500 papers had their `.00001.txt` output) — the script was stuck
immediately *after* finishing the very stage whose completion it just logged
elsewhere, never printing "STAGE END: convert_text" and never reaching
tokenize/annotate/index/the `textpressoapi` restart. This is exactly why the
GUI showed nothing: none of the stages that actually populate `tpcas-1`,
`tpcas-2`, the Lucene index, or make the corpus visible via the API had run.

### Fix

- Replaced `exec > >(tee -a "${RUN_LOG}") 2>&1` with a plain
  `exec >> "${RUN_LOG}" 2>&1` — no process substitution, no pipe, nothing
  that can hang waiting for EOF. (Losing the live terminal echo doesn't
  matter here since the script always runs detached/unsupervised anyway.)
- Explicitly detached the resource monitor subshell's own stdin/stdout/stderr
  (`</dev/null >/dev/null 2>&1 &`) so it can never inherit anything from the
  parent script's file descriptors, regardless of how the parent redirects.
- Replaced the bare `wait` after the `convert_text` loop with waiting on the
  **specific PIDs** launched in that loop (collected into an array as each
  `pdf2txtimg` was backgrounded), so it can't accidentally sweep up the
  monitor or any other unrelated background job.
- Added an idempotency check to stage 1: skip re-running `pdf2txtimg` for any
  paper whose `.00001.txt` output already exists and is newer than the
  source PDF. This wasn't needed for the deadlock fix itself, but made
  resuming today fast — all 500 papers from the stalled run were already
  extracted, so the resumed run skipped straight to tokenize instead of
  redoing ~1.6GB of extraction work.

### Resume

Killed the 17.5-hour-stuck processes (`pkill -f run_maizeoa_overnight.sh`;
confirmed no orphaned `pdf2txtimg`/`tee` processes remained), redeployed the
fixed script, and relaunched the same way (`docker exec -d`, still scoped to
the same 500-paper subset). New run ID `20260714T134406Z`; confirmed within
seconds that all 500 papers were correctly recognized as already-extracted
and the run moved straight into `tokenize`.

**Lesson for any future long-running background/monitoring script in this
repo: never combine `exec > >(...)` (or any process-substitution pipe) with
a background job that's designed to outlive a later bare `wait`.** Either
avoid process substitution for logging (plain `>>` redirection is simpler
and sufficient for anything that doesn't need a live terminal view), or wait
on explicit PIDs/job specs rather than a bare `wait`, or both — this fix
does both, as defense in depth.

### Results — TO BE FILLED IN once the resumed run completes

New run's logs: `/data/textpresso/logs/maizeoa-overnight-20260714T134406Z.{log,summary.txt,resources.log,stage_times.txt}`
and per-stage logs `maizeoa-overnight-{tokenize,annotate,index}-20260714T134406Z.log`.
(`convert_text` had nothing to do this run — see above.)

- [x] Stage wall-clock times: tokenize 183s, annotate 310s, index 535s, restart_api 6s, convert_text 0s (nothing to do, already extracted)
- [x] Peak per-process RSS: `articles2cas` 143MB, `runAECpp` 1494MB, `textpressoapi` 337MB
- [x] Final paper counts: 500/500 through CAS-1 and CAS-2
- [x] Final disk usage: 42G/50G (84%), circuit breaker never fired
- [x] `MaizeOA` appeared in `--list-corpora` after the restart, but **search still returned zero results** — see next entry, this was not actually done.

---

## Update log — 2026-07-14 (continued): `MaizeOA` indexed but unsearchable — missing `.bib` sidecars short-circuit indexing silently

### Symptom

`--list-corpora` showed `MaizeOA`, and the resumed run's own logs claimed full success (500/500 CAS-1, 500/500 CAS-2, index stage completed with no errors). But every search against `-c MaizeOA` returned "No results" — confirmed via both `tpc_search.py` and a direct POST to `get_documents_count` on the real API (`0`, vs `567` for `SorghumBase`). `cc.cfg` (the corpus-count registry written by `updatecorpuscounter`) showed `MaizeOA 0`.

### Ruled out

- Stale `IndexReader` cache (the 2026-07-13 `--category` bug): restarting `textpressoapi` didn't help.
- Stale `luceneindex_new` / lockfile from an earlier interrupted run: a fully clean rebuild (`rm -rf luceneindex_new`, `rm -f 12index.lock`, reindex from scratch) still produced `MaizeOA 0`.
- CAS-2 corruption: manually decompressed and read a MaizeOA CAS-2 file directly — valid, well-formed XMI with real annotated fulltext.
- `cas2index` crashing: its own per-file trace (written to a hardcoded `/tmp/csi.$i.out`, bypassing whatever redirection the caller uses — a gotcha worth remembering for any future debugging here) showed every MaizeOA file being reached, no OOM, no disk errors, exit 0.

### Root cause

`IndexManager::add_cas_file_to_index` (`agr_textpresso/libtpc/IndexManager.cpp:845-848`):

```cpp
if (!exists(bib_file)) {
    std::cerr << "No .bib file found for file " << source.filename().string() << endl;
    return 0;
}
```

This check runs **before** the CAS-2 gzip is decompressed or the UIMA engine ever touches the text. If the `.bib` sidecar doesn't exist, the function returns immediately — no document is ever added to the Lucene writer. Confirmed with a side-by-side isolated repro: indexing one copied MaizeOA file alone printed only `processing cas file: ...` + `No .bib file found for file ...`; indexing one copied SorghumBase file (which has real `.bib` sidecars) alongside it produced the full `N(cats)=... / N(words)=...` word-by-word annotation trace. MaizeOA had **zero** `.bib` files — the overnight script's stage list (`convert_text -> tokenize -> annotate -> index -> restart_api`) never included the runbook's Section 6 `.bib`-backfill step, so every single MaizeOA paper hit this early-return, silently, on every reindex attempt (the original overnight run and the first "clean" rebuild both predate this diagnosis).

This is a sharper failure mode than the runbook's Section 11 item #1 ("missing `.bib` -> blank metadata columns") suggests — a **completely absent** `.bib` file (not merely an empty/placeholder one) drops the paper from the index entirely, not just its displayed metadata.

### Complication while fixing: two forgotten concurrent processes

While chasing this, found two of my own earlier troubleshooting processes (`run_tpc_pipeline_incremental.sh`, started 16:01 and 16:32) still running against the same container — I'd thought I'd killed them earlier and hadn't. One was mid-retokenize of MaizeOA (single-threaded, 36 minutes in), the other was mid-reindex. Between them they'd auto-generated placeholder `.bib` files (`author|<not uploaded>` etc., via the pipeline's built-in `ensure_pdf_bib_files` helper) for all 500 MaizeOA papers, and had driven `/data/textpresso` down to 847MB free (99% used) — mostly 4.1GB of uncompressed `.tpcas` scratch files left behind by the killed retokenize job that never got to its `pigz` compression pass.

**Cleanup before retrying:** killed both process trees, removed `12index.lock`, `07cas1tocas2.lock`, and the in-progress `luceneindex_new`, deleted the 865 leftover uncompressed `.tpcas` files (their `.tpcas.gz` counterparts were already present and untouched), removed my own scratch test directories, and verified counts before proceeding: 500/500 CAS-1, 500/500 CAS-2, 500/500 `.bib` (placeholders), no lockfiles, no stale processes, 4.8GB free.

**Lesson:** always verify a background process is actually gone (`ps`, not just recalling having sent a kill) before assuming the coast is clear, especially before running anything that touches the shared `/data/textpresso` tree.

### Fix and verification

The placeholder `.bib` files (auto-generated by the concurrent processes) were sufficient to satisfy the `exists(bib_file)` check — real metadata backfill (runbook Section 6/7, from `MaizeOA_papers.csv`) is still a separate follow-up, since these are just `<not uploaded>` placeholders. Ran one clean, single-actor reindex (`rm -rf luceneindex_new`, `rm -f 12index.lock`, `index -C tpcas-2 -i luceneindex`, ~17.5 min), then restarted `textpressoapi`.

Result: `cc.cfg` now shows `MaizeOA 492` (not 500 — see below). Verified via direct API POST: `get_documents_count` for `MaizeOA` returns `492` for keyword "the", `485` for "maize"; `SorghumBase` sanity check still correctly returns `567`.

The 8-paper gap (500 staged, 492 indexed) is fully accounted for: exactly 8 MaizeOA accessions share a basename with a paper already present under another corpus (likely `PMCOA`). `create_single_index.sh`'s file-list builder (`agr_textpresso/tpctools/cas2index/create_single_index.sh:67`) deduplicates by **accession basename only** across all corpora (`awk -F"/" '!x[$NF]++'`), silently dropping whichever corpus's copy sorts later when two corpora happen to stage a paper under the same accession. Pre-existing tool behavior, not introduced by this fix — not chased further, but worth knowing if paper counts ever look short again.

### Not yet done

- [x] Real `.bib` metadata backfill for `MaizeOA` from `MaizeOA_papers.csv` (runbook Section 6): all 500/500 accessions matched the CSV by DOI, all 500 already had non-empty `year` in the CSV so the Section 7 Crossref backfill wasn't needed. Reindexed (~18 min) and restarted `textpressoapi`; verified via `search_documents` that real title/journal/year now show correctly (e.g. `10.1002_cfg.395 | 2004 | Comp Funct Genomics | On the tetraploid origin of the maize genome.`).
- [ ] Decide whether the 8 basename-collision papers matter enough to rename/reindex separately.
- [ ] Consider whether `add_cas_file_to_index`'s missing-`.bib` early-return should instead fall back to indexing with placeholder metadata (like the metadata-population path already does), so a missing sidecar can never again silently zero out an entire corpus.

---

## Update log — 2026-07-14 (continued): synonym audit re-run against `MaizeTest100` + `MaizeOA` combined (610 papers)

Per the 2026-07-10 entry's suggested next step ("re-run against `MaizeOA` ... once available for a larger, more statistically reliable sample"), now that `MaizeOA` is indexed and searchable.

### Change: multi-corpus support in `ontology_synonym_audit.py`

`--corpus` is now repeatable (`action="append"`) instead of single-valued. `scan_corpus()` globs each corpus's CAS2 files separately and concatenates the file lists before counting -- doc/total counts merge naturally across corpora since documents are keyed by full file path, which already embeds the corpus name.

```bash
python3 bin/ontology_synonym_audit.py \
  --corpus MaizeTest100 --corpus MaizeOA \
  --id-prefix "tpzm:" --min-doc-freq 5 --exclude-type references \
  --obo-file /home/ec2-user/agr_textpresso/.data/obofiles4production/zmays_genes_20260708.obo
```

### Results

610 CAS2 files scanned (110 + 500), 9,646 reference-section matches excluded. 6,354 distinct matched terms; **352 appear in >= 5 of 610 documents** (note: same absolute `min-doc-freq=5` threshold as the 2026-07-10/07-13 runs, which is now a looser relative bar -- 0.8% of the corpus vs. 4.5% on the 110-paper `MaizeTest100`-only run -- so this reportable set is both larger and, at the margin, noisier than the earlier ones; the well-established top offenders (`red`, `expression`, `binding`, `promoter`, `transcription factor`, `ca`, `mrna`, `aba`, etc.) are unchanged and even more clearly dominant at this larger sample size).

Synonym type/source tally across all 352 terms' traced OBO entries: 513 `RELATED locus_synonym`, 203 `EXACT locus_synonym`, 73 `NAME` (genuine gene-symbol matches, e.g. `p1`/`p2`), 72 `EXACT locus`, 39 `EXACT jschnable_name`, 21 `EXACT locus_name`, 19 `EXACT description`, and single-digit counts for a handful of other fields -- `locus_synonym` is still responsible for the large majority (716/~950 = ~75%) of what's reportable, consistent with the 2026-07-10/07-13 root-cause finding.

Full term-by-term table (term, doc_freq, total, source fields, gene id + name) saved to `docs/synonym_audit_maizetest100_maizeoa_20260714.csv` for manual review -- handed off for curation decisions, not acted on in this session.

---

## Update log — 2026-07-14 (continued): `--category` sentence search returning references despite `--exclude-type references` -- another section-detection gap, fixed and deployed

### Symptom

Spot-checking sentences for top synonym-audit terms via `tpc_search_internal.py --type sentence --category "... (tpzm:...)" --exclude-type references` against `MaizeTest100`+`MaizeOA`: one result (`10.1093_g3journal_jkad197`, a G3 (Bethesda) paper) returned nothing but bibliography entries (author-list sentences), even though `--exclude-type references` was set.

### Root cause: this paper's `.tpcas` file has no `references` section at all

Traced with `casannot.parse_cas_file()` directly: `10.1093_g3journal_jkad197`'s only detected section types are `beginning of article, introduction, materials and methods, result, discussion` -- `discussion` runs all the way from 44406 to 63583 (nearly to the end of the 63598-character document), silently absorbing the entire bibliography. `_filter_matched_sentences()` in `tpc_search_internal.py` correctly checked the section at that sentence's position, found `discussion` (not `references`), and correctly kept it -- the filtering logic itself wasn't broken, it was just given the wrong section label for this paper.

Decompressed the raw XMI and found the actual heading text: **`"Literature cited"`** -- capital "L", lowercase "cited". `agr_textpresso/libtpc/uima-annotators/uimaglobaldefinitions.h`'s `sectionReferences()` is a hardcoded, case-sensitive `std::set` of exact string variants (Title Case, ALL CAPS, and spaced-letter versions of each), and every existing "Literature Cited" variant capitalizes "Cited". G3/Genetics Society journals apparently use sentence case for this heading, which matched none of them, so the section-detector (`TdTokenizer`) never recognized a references boundary in this paper at all.

### Fix: added lowercase/sentence-case variants for every multi-word section heading, not just this one

Per request, generalized beyond just `"Literature cited"`. Since `TdTokenizer`'s section matching is a literal-string trie (`trieSection_`, built from all the `section*()` sets in `uimaglobaldefinitions.h` -- confirmed via `TdTokenizer.cpp:194-217,283-294,313-317`) with no case-folding anywhere, any capitalization scheme not explicitly enumerated is invisible to it. Audited every multi-word heading phrase across all 12 section sets (`grep -oE` for two-or-more-word quoted literals) and added the missing all-lowercase + sentence-case (first word capitalized only) variants -- unspaced only, matching the existing convention that letter-spaced variants only exist for Title Case/ALL CAPS (a PDF-rendering artifact that wouldn't naturally occur on genuinely-lowercase text):

- `sectionIntroduction()`: `Literature Review` -> + `literature review` / `Literature review`
- `sectionResult()` / `sectionDiscussion()`: `Results and Discussion` -> + lowercase/sentence-case
- `sectionDiscussion()` / `sectionConclusion()`: `Discussion and Conclusion(s)` -> + lowercase/sentence-case (both plurality variants)
- `sectionConclusion()`: `Concluding Remarks` -> + lowercase/sentence-case
- `sectionMaterialsMethods()`: `Experimental Procedures` -> + lowercase/sentence-case (the `Material(s) and Method(s)` combos already had full case coverage here, oddly the most complete of any set -- this was clearly the template the others should have matched)
- `sectionReferences()`: `Literature Cited`, `References Cited`, `Works Cited` -> + lowercase/sentence-case for each (this is the one that fixes the reported bug)

`Beginning of Article` / `End of Article` were left alone -- confirmed these are synthetic markers the ingest pipeline itself inserts (not real PDF text), always in one canonical form.

### Deployment: no image rebuild, no container restart -- hot-swapped the compiled `.so` in place

Discovered the running container (`agr-textpresso-textpresso-1`, image built 2026-05-19) has **no live source mount at all** -- the Dockerfile `COPY`s `libtpc`/`textpressocentral`/`textpressoapi`/`tpctools` into the image only during build, compiles, then `rm -rf`s the source before the layer is finalized. Tried `docker-compose build` first; failed immediately -- the base image (`ubuntu-tpc`, built from `libtpc/Dockerfile_18.04`, which compiles UIMA C++ from source plus a full apt toolchain) isn't cached anywhere on this host and would need a from-scratch rebuild (30-90+ min, real risk of breaking the one working container over a stale apt mirror or package-version drift). Stopped and asked before proceeding; agreed to avoid that path.

Used the same escape hatch documented in the 2026-07-13 entry above instead: `TdTokenizer` is its own CMake target (`add_library(TdTokenizer SHARED ...)`, `libtpc/CMakeLists.txt`), built as `/usr/local/lib/TdTokenizer.so` with no `lib` prefix since UIMA C++ annotators are `dlopen()`'d by descriptor path/name rather than linked normally -- meaning it can be rebuilt and hot-swapped completely independently of `tpctools`/`textpressocentral`/the container lifecycle. The running container still has the full build toolchain installed (cmake, g++, Boost dev, the UIMA C++ library+headers, Lucene++) even though the source was deleted, since the Dockerfile only removes the source copies, not the toolchain that built them.

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "rm -rf /data/textpresso/libtpc"   # NOT mkdir first -- let docker cp create it (2026-07-13 lesson)
docker cp agr_textpresso/libtpc agr-textpresso-textpresso-1:/data/textpresso/libtpc
docker exec agr-textpresso-textpresso-1 bash -lc "
  cd /data/textpresso/libtpc && mkdir -p build && cd build &&
  cmake -DCMAKE_BUILD_TYPE=Release .. -DCMAKE_INSTALL_PREFIX=/usr/local &&
  make -j4 TdTokenizer
"
docker exec agr-textpresso-textpresso-1 bash -lc "
  cp /usr/local/lib/TdTokenizer.so /usr/local/lib/TdTokenizer.so.bak-\$(date -u +%Y%m%dT%H%M%SZ)
  cp /data/textpresso/libtpc/build/TdTokenizer.so /usr/local/lib/TdTokenizer.so
  ldconfig
"
```

Built clean, no errors. Backup: `TdTokenizer.so.bak-20260714T181752Z` (alongside the pre-existing `...-20260713T172823Z` from the prior fix).

### Verification (single paper: `MaizeOA/10.1093_g3journal_jkad197`)

Re-tokenized just this one paper into an isolated scratch directory (production `tpcas-1`/`tpcas-2` untouched). Repeated the exact `articles2cas` gotchas from 2026-07-13 -- most notably **`-t` is `--input-files-type`, not thread count** (1=pdf, 2=xml, 3=text; production actually passes `-t 4`, undocumented in `--help` but confirmed as the text+image/"tai" hybrid mode by the 2026-07-13 entry). Got this wrong on the first attempt (`-t 1`, i.e. plain PDF mode) and produced a silently-truncated CAS (262 sentences instead of 594, PoDoFo "not a PDF file" errors on every page since it was trying to parse the pre-extracted `.txt` sidecars as PDFs). Correct invocation:

```bash
export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH
cd /data/textpresso/tmp/tdtok-verify
echo "10.1093_g3journal_jkad197" > list.txt
articles2cas -i /data/textpresso/raw_files/pdf/MaizeOA -o out -l list.txt -t 4 -p
```

Result: 594 sentences (matches the original exactly), and now **6** sections instead of 5 -- `discussion` correctly stops at 50727 (right after "Author contributions"), and a new `references` section spans 50727-63583. The exact sentence from the bug report (`"Dobin A, Davis CA, Schlesinger F, ..."` at 53064-53159) now resolves to `references` instead of `discussion`.

### Not yet done

- [ ] Retroactively re-tokenize/re-annotate/reindex the other ~609 already-ingested `MaizeTest100`/`MaizeOA` papers so they pick up the fix too -- deferred by request. Per the 2026-07-13 entry, this is **not** a simple touch-CAS1-and-reindex: re-tokenizing an existing paper needs the same direct `articles2cas -i .../pdf/<corpus> -o <dir> -l <listfile> -t 4 -p` invocation used above (the standard wrapper scripts can't reprocess already-ingested papers), then re-annotate, then reindex -- a real, possibly lengthy operation across ~609 papers, not yet scoped for time/disk cost.
- [ ] Only fixed the `sectionReferences()`/`sectionIntroduction()`/`sectionResult()`/`sectionDiscussion()`/`sectionConclusion()`/`sectionMaterialsMethods()` multi-word phrases found in *this* OBO/heading set. If a paper uses some other heading capitalization scheme entirely (not lowercase/sentence-case/Title/ALL CAPS), it'll have the same silent-absorption failure mode -- worth another audit pass if this recurs.

---

## Update log — 2026-07-14 (continued): applying the 2026-07-10 "cheap first filter" to the combined synonym audit table

### v1: digit/hyphen/mixed-case heuristic (as originally proposed 2026-07-10)

Parsed `synonym_audit_combined.txt` (the 352-term combined-corpus run above) and applied the filter exactly as originally suggested: for terms with >=1 `locus_synonym`-sourced OBO entry, flag for removal unless some observed surface form has a digit, a lowercase-to-uppercase transition, or a hyphen. Output: `docs/synonym_audit_maizetest100_maizeoa_20260714_filtered.csv` (columns: `term, doc_freq, total, surface_forms, source_fields, gene_ids, filtered, filter_reason`) -- a `filtered` True/False column added for review, nothing removed from the actual OBO/index. 174/352 flagged `True`.

**Feedback:** this over-filtered real short gene abbreviations with no digit/hyphen/case-transition (`aba`, `gs`, `rs`, `rg`, ...) while under-filtering generic multi-word jargon that happened to contain a hyphen (`rna-binding protein`, `receptor-like kinase`, `leucine-rich repeat`, ... -- kept solely because of the hyphen, regardless of how generic the phrase actually was).

### v2: reworked to positively detect common English/molecular-biology words instead

Redesigned per feedback: rather than pattern-matching for "looks like a gene ID," directly detect "is this a common word," and flag for filtering only when *every* word in the term is one. Digit-presence stays as the one hard override (still the most reliable "real ID" signal). Hyphens are now just token separators, no special meaning.

Per term (only applied when >=1 `locus_synonym`-sourced entry exists, same as v1): split into tokens on whitespace/hyphen/slash/semicolon; each token counts as "generic" if it's a common English stopword, a short molecular-biology acronym (`rna`, `mrna`, `atp`, `utr`, `orf`, ...), a token >=5 characters ending in `-ase` (the standard enzyme-class suffix -- catches `atpase`/`methyltransferase`/`helicase`/`transposase`/`polygalacturonase`/`glycosyltransferase`, none of which are in a general English dictionary), a token >=5 characters found in `/usr/share/dict/words`, or in a small supplementary list of common plant/molecular-biology domain nouns the general dictionary doesn't have (`calmodulin`, `thioredoxin`, `glutaredoxin`, `ubiquitin`, `chorismate`, `expansin`, `pentatricopeptide`, ...). Filtered only if every token in every observed surface form is generic.

**Why the length->=5 cutoff and not lower:** `/usr/share/dict/words` (479,826 entries) is unreliable below 5 characters -- it contains huge numbers of abbreviations, proper nouns and junk (`2D`, `AA`, `Nyac`, `waer`, `akee`, `FRSS`), and specifically includes `gata` and `saur` as "words," which in this corpus are real gene-family abbreviations (GATA transcription factors, Small Auxin Up RNA genes), not English words. Confirmed by testing before committing to the threshold. A handful of short (3-4 letter) but unambiguously-common words the dictionary threshold excludes were added explicitly instead of lowering it broadly: `like`, `rich`, `late`, `red`, `zinc` -- found by manually spot-checking the output for obvious misses (`red` was the single highest-doc-freq term in the whole audit and was falling through unresolved before this).

### Result

98 terms `filtered=True`. Of the 254 `filtered=False`: 150 are confirmed real gene IDs (digit present) or out of the filter's scope (no `locus_synonym` source at all -- e.g. `c1`/`b1`/`d1`-style classical single-locus symbols), and 104 are short (2-4 letter) abbreviations (`myb`, `bhlh`, `bzip`, `pepc`, `comt`, `gata`, `saur`, `sam`, `met`, `pod`, ...) that no dictionary can reliably classify either way -- left `filtered=False` but with a distinct reason string (`"...kept for manual review"`) so they're visibly different from the 150 confirmed-real ones, rather than silently guessed at.

### Not yet done

- [ ] Manual review of the 98 `filtered=True` and especially the 104 "kept for manual review" rows -- handed off, not acted on.
- [ ] No changes made to the actual OBO file or index -- this is analysis output only.
- [ ] The curated short-word/domain-noun lists (`STOPWORDS`, `SHORT_BIO_ACRONYMS`, `DOMAIN_NOUNS` in the filter script) are almost certainly incomplete; expect to add more entries as the 104 unresolved rows get reviewed.

---

## Update log — 2026-07-15: disk sizing reference — per-paper cost and fixed Textpresso footprint

### Background

Asked how many more papers could safely be indexed on the current 50GB volume
(3.1GB free, 94% used, as of this check — tighter than the 42G/50G recorded
at the end of the 2026-07-14 entry, mostly `.vscode-server`/scratch growth
unrelated to the pipeline, not new papers). Answering that required breaking
down disk usage into a **fixed cost** (the Textpresso install itself, paid
once) and a **variable cost** (per paper ingested) — recorded here as a
general sizing reference for standing up a new instance, not specific to this
host's current headroom.

### Fixed cost — Textpresso itself (independent of corpus size)

Measured via `docker system df` and `du` on `/home/ec2-user/agr_textpresso/.data`,
excluding anything that scales with paper count:

| Component | Size | Notes |
|---|---|---|
| Docker image (`agr-textpresso-textpresso`) | 5.13GB | `ubuntu-tpc` base + compiled UIMA C++ toolchain (`libtpc`, `textpressocentral`, `tpctools`) |
| Docker container writable layer | 3.30GB | Running container's own layer; separate from the bind-mounted `.data` volume |
| Ontology + base DB data (`obofiles4production` + `db` + `textpressoapi_data` + misc) | ~90MB | OBO category files and the ontology Postgres tables — scales with which ontologies are loaded, not with paper count |
| **Total fixed footprint** | **~8.5GB** | Paid once per Textpresso instance, before any papers are loaded |

Not included: host OS, Docker Engine itself, or other software on the box —
this is the footprint of Textpresso specifically.

### Variable cost — per paper (scales with corpus size)

Measured from clean, single-batch corpora (avoiding `MaizeOA`'s `tpcas-1`,
which has 389 orphaned dirs from an old stalled run — see 2026-07-14 entry —
and would understate the true per-paper figure):

| Stage | Corpus sampled | Measured | Per paper |
|---|---|---|---|
| Raw PDF (`raw_files/pdf/<corpus>`) | MaizeOA, 1438 papers | 7.9GB | ~5.6MB |
| Tokenized CAS-1 (`tpcas-1/<corpus>`) | MaizeTest100, 110 papers | 891MB | ~8.1MB |
| | SorghumBase, 570 papers | 2.3GB | ~4.0MB |
| Annotated CAS-2 (`tpcas-2/<corpus>`, gzip) | MaizeTest100 / SorghumBase / MaizeOA | 53MB / 394MB / 241MB | ~0.5–0.7MB (consistent across all three) |
| Lucene index growth | Merged index, ~1170 docs total | 861MB | ~0.7MB |

CAS-1 varies more than the other stages (4–8MB/paper) since it's roughly
proportional to paper length/PDF complexity; CAS-2 and the index are the most
consistent stages across corpora. Summing the middle of these ranges:

**Total ≈ 10–13MB per paper**, end-to-end (PDF staged → tokenized → annotated
→ indexed and searchable). This cross-checks against the one directly-measured
full-pipeline delta in the 2026-07-14 entry — disk usage grew 39GB→42GB
(~6MB/paper) for tokenize+annotate+index alone on 500 already-staged PDFs;
adding the ~5.6MB/paper raw-PDF cost from this entry lands at ~11.6MB/paper,
consistent with the range above.

### Estimating total space for a new instance

```
total_GB ≈ 8.5 + (num_papers × 0.012)
```

E.g. a fresh instance loaded with 5,000 papers: `8.5 + 5000×0.012 ≈ 68.5GB`.
Add real margin on top — the existing pipeline's own disk-space circuit
breaker (2026-07-13 entry) stops enqueuing new work at a 1.5GB free floor,
and per-paper cost has real variance (a few outlier papers with large PDFs or
many pages can be well above the 10–13MB average), so this formula should be
treated as a planning floor, not a tight bound.
