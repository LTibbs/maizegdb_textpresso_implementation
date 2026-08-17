# `tpc_search_combined.py` — usage guide

Reference for `bin/tpc_search_combined.py`, the Textpresso search
command-line tool, as of 2026-08-14.

## Overview

`tpc_search_combined.py` is the single entry point for Textpresso search:
plain keyword/metadata/section search plus CAS2 ontology-annotation modes
(`--annotate`, `--annotate-sentences`, `--related-synonyms`, `--ontology`,
`--cas-root`, precise `--exclude-type`), all in one standalone script. It
requires **only network access** — it does not need to run on the
Textpresso server. The annotation modes are backed by a small annotation
service (`cas_annotate_server.py`, in the `agr_textpresso` repo) that runs
alongside the Textpresso API and serves CAS2 data over HTTP; see
"Architecture: how annotation data reaches the client" below.

**History:** this used to be two scripts, `tpc_search.py` (search only) and
`tpc_search_internal.py` (search + annotation, originally requiring
server-side file access before the CAS2 data was served over HTTP). Both
still exist in `bin/` and still work, but are redundant now that
`tpc_search_combined.py` covers everything in one script with no need to
remember which tool a given flag lives in — they're kept only so existing
callers don't break, and are no longer being maintained going forward. All
examples below use `tpc_search_combined.py`; substitute one of the old
scripts only if you have a specific reason to.

The Textpresso instance runs inside a Docker container
(`agr-textpresso-textpresso-1`); its search API and the CAS2 annotation
service are both proxied through lighttpd and exposed publicly at:

```
http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api/        (search)
http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/annotate    (CAS2 annotation data)
```

## Basic search

### Usage

```
python3 bin/tpc_search_combined.py [options] [keywords]
```

### Search types (`--type`)

| Type | What it searches |
|------|-------------------|
| `sentence` | Full paper text; returns matching sentence text (default) |
| `document` | Full paper text; returns only the citations of documents with hits |
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
| `references` | References/bibliography section |

Section-scoped types require the paper's CAS2 file to carry section
boundaries. 

### Keyword syntax

The `keywords` argument is passed directly to the Lucene index. Boolean
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
--exclude KEYWORDS        Keywords to exclude from results
--case-sensitive          Case-sensitive keyword matching
--author NAME              Filter by author (substring match by default)
--exact-author             Require exact author match
--journal NAME             Filter by journal
--exact-journal            Require exact journal match
--year YEAR                Filter by publication year
--accession ID             Filter by DOI / accession. NOTE that slashes ("/") cause problems and so must be replaced by underscores ("_") e.g. search for 10.1007_s00425-012-1754-3 to retrieve doi 10.1007/s00425-012-1754-3
--paper-type TYPE          Filter by paper type (Journal_article, Review, ...)
--category CATEGORY        Restrict to ontology category (repeatable). Must be the exact stored string with ID suffix, e.g. "seed (PO:0009010)" or "adh1 (tpzm:0008786)" — a non-exact value is rejected with suggestions rather than run; see "Looking up --category values" below and bin/tpc_category_search.py
--categories-and           Require ALL categories to match (default: ANY)
--sort-by-year             Sort by year instead of relevance score
--format text|json         Output format (default: text)
--url URL                  API base URL
--list-corpora              List available corpora and exit
--exclude-type TYPE        Exclude a CAS section type from results (repeatable). Precise,
                           CAS2-based exclusion (not limited to --type document) — see
                           "--exclude-type — precise, CAS2-based section exclusion" below.
```

**`--exclude` vs. `--exclude-type`** — these are unrelated: `--exclude` drops
results containing given *keywords*; `--exclude-type` drops results whose
match falls only inside a given CAS *section* (e.g. bibliography). See the
dedicated section below for exactly how each `--type` mode filters.

### Examples

```bash
# Basic sentence search
python3 bin/tpc_search_combined.py -c MaizeTest100 "flowering time"

# Top N results -- --count caps how many come back (default 50, API max 200);
# results are already ranked by relevance score by default, so this gives the
# top N matches. --sort-by-year switches the ranking to year instead.
python3 bin/tpc_search_combined.py -c MaizeTest100 "flowering time" --count 5

# Multiple corpora
python3 bin/tpc_search_combined.py -c MaizeTest100 -c SorghumBase "drought tolerance"

# Section-scoped
python3 bin/tpc_search_combined.py -c MaizeTest100 --type abstract "drought"

# Metadata filters
python3 bin/tpc_search_combined.py -c MaizeTest100 --author "Buckler" --year 2014 "GWAS"
python3 bin/tpc_search_combined.py -c MaizeOA --journal "Nature" --exact-journal "maize"

# Keyword modifiers
python3 bin/tpc_search_combined.py -c MaizeTest100 --exclude "Arabidopsis" "kernel weight"
python3 bin/tpc_search_combined.py -c MaizeOA --case-sensitive "ZmMADS"

# Category search (ID suffix required)
python3 bin/tpc_search_combined.py -c MaizeTest100 --category "seed (PO:0009010)" "development"

# Drop bibliography-only document hits
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document "MARK" --exclude-type references

# JSON output (pipe-friendly)
python3 bin/tpc_search_combined.py -c MaizeTest100 "anthocyanin" --format json | jq '.[].title'

# List available corpora
python3 bin/tpc_search_combined.py --list-corpora
```

## CAS2 ontology annotation

On top of the search options above, `tpc_search_combined.py` has flags that
read CAS2 ontology-annotation data. By default this data is fetched over HTTP from
`cas_annotate_server.py` (same `--url` host, `/v1/textpresso/annotate`), so
this script works from anywhere with network access — no server-side file
access is required, and no `--cas-root` needs to be passed.

**`--cas-root` — local-file fallback (server-side only, optional)**

Pass `--cas-root PATH` to instead bypass the network endpoint and parse CAS2
files directly from a local directory. Only useful when running directly on
the Textpresso server/host:

| Context | Path |
|---------|------|
| Host (outside container) | `/home/ec2-user/agr_textpresso/.data/tpcas-2` |
| Inside the container | `/data/textpresso/tpcas-2` |

Not needed for normal use — see "Architecture" below for why.

### Additional options

```
--annotate              Append an ontology term summary to each result
--annotate-sentences    Output sentence-level annotation JSON
--full-text             With --annotate-sentences: include unannotated sentences
--related-synonyms      Include RELATED-synonym annotations in output
                        (default: excluded; ignored when --ontology is set)
--ontology NAME         Restrict annotations to GO, PO, TO, MAIZE_GENES,
                        MAIZE_GENES_RELATED, or OTHER (repeatable;
                        default: all except MAIZE_GENES_RELATED)
--cas-root PATH         Bypass the network annotation endpoint; parse CAS2
                        files directly from this local root instead
                        (server-side use only; unset by default)
--exclude-type TYPE     Exclude results from this CAS section type
                        (repeatable) — see below for per-mode behavior
```

### `--annotate` — ontology term summary per paper

Appends a grouped list of all ontology terms found anywhere in the paper to
each result. Works with text output (default) or JSON (`--format json`).

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 "anthocyanin" --annotate
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

**RELATED synonyms:** Use `--ontology MAIZE_GENES_RELATED` to
see the RELATED-synonym matches.

```bash
# default: EXACT locus synonyms only
python3 bin/tpc_search_combined.py -c MaizeTest100 "adh1" --annotate

# include RELATED locus synonyms too
python3 bin/tpc_search_combined.py -c MaizeTest100 "adh1" --annotate --related-synonyms

# RELATED-only
python3 bin/tpc_search_combined.py -c MaizeTest100 "adh1" --annotate --ontology MAIZE_GENES_RELATED
```

### `--annotate-sentences` — full sentence-level annotation JSON

Always outputs JSON. For each result paper:

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
one annotation. Add `--full-text` to include all sentences in the paper.

```bash
# Annotated sentences only (default)
python3 bin/tpc_search_combined.py -c MaizeTest100 "anthocyanin" --annotate-sentences

# All sentences
python3 bin/tpc_search_combined.py -c MaizeTest100 "anthocyanin" \
    --annotate-sentences --full-text

# Filter to GO and PO only
python3 bin/tpc_search_combined.py -c MaizeTest100 "anthocyanin" \
    --annotate-sentences --ontology GO --ontology PO
```

### `--exclude-type` — precise, CAS2-based section exclusion

This script has access to full CAS2 data (over the network by default, or a
local file with `--cas-root`) and filters exactly, per mode:

- **`--type sentence`**: filters `matched_sentences` directly. Each
  API-returned sentence is mapped to its CAS2 position by exact text match;
  dropped if that position falls in an excluded section. A sentence that
  can't be matched back to a position is kept rather than guessed at.
- **`--type document`**: drops the whole document only if **every
  determinable match is within the excluded section** — verified by
  re-running the query scoped to just that document with `--type sentence`
  (which covers the whole document, not just named sections), so it does not
  have the "uncovered prose" gap `tpc_search.py`'s version has. Kept whenever
  match status can't be determined (missing accession/corpus, API error, no
  CAS2 file) — conservative by design.
- **`--annotate` / `--annotate-sentences`**: filters the ontology summary /
  annotated-sentences list by CAS2 section, independent of `--type`, since
  these read the whole CAS2 file rather than the search match. So
  `--exclude-type` works together with `--annotate --type document`, etc.

```bash
# sentence-level: drop bibliography-only matches
python3 bin/tpc_search_combined.py -c MaizeTest100 --type sentence \
    --accession 10.1101_gr.277459.122 "Wickham" --exclude-type references

# document-level: precise drop -- "HelitronScanner" appears in exactly one
# MaizeTest100 paper, and only inside its bibliography (a citation of the
# HelitronScanner tool's own paper), so excluding references drops it to zero:
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document "HelitronScanner"              # -> 1 result
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document \
    "HelitronScanner" --exclude-type references                                          # -> No results (correct)

# strip bibliography-sourced false positives out of an ontology summary
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document \
    --accession 10.1101_gr.277459.122 --annotate --exclude-type references

# multiple excluded types
python3 bin/tpc_search_combined.py -c MaizeTest100 "adh1" \
    --annotate --exclude-type references --exclude-type acknowledgments
```

## Architecture: how annotation data reaches the client

Before 2026-08-12, the `--annotate`/`--annotate-sentences`/precise
`--exclude-type` modes (then only in `tpc_search_internal.py`) worked by
parsing CAS2 files directly off disk (`textpresso_classifiers/casannot.py`),
which only worked for users with a filesystem-level checkout on the
Textpresso host. As of 2026-08-12 that data is served over HTTP instead, so
these modes work the same as plain search -- network access only, from
anywhere:

```
tpc_search_combined.py --url <base>
        │
        ├─ POST {base}/search_documents      (unchanged; C++ textpressoapi, port 18080)
        └─ GET  {base%/api → /annotate}?identifier=...   (cas_annotate_server.py)
```

`cas_annotate_server.py` (in the `agr_textpresso` repo, `textpressoapi/`
directory) is a small dependency-free Python `http.server` process that runs
alongside `textpressoapi` inside the same Docker container, binds to
`127.0.0.1:8082` (not reachable directly), and serves parsed CAS2 data
(`{sentences, annotations, sections}`) for a given document identifier. It
imports a mirrored copy of this repo's `casannot.py` -- **the two copies must
be kept in sync** if the CAS2 parsing logic changes (see the header comment
in either file).

Two proxy layers sit in front of it, both edited additively (new rules
alongside the existing search-API ones, nothing removed or changed):

1. **`agr_textpresso/lighttpd.conf`** (inside the container): a new
   `$HTTP["url"] =~ "^(.*)/v1/textpresso/annotate"` block proxies to
   `127.0.0.1:8082`. Deliberately a path with no substring overlap with the
   existing `/v1/textpresso/api` rule, so the two can never match the same
   request.
2. **Host-level nginx** (`/etc/nginx/conf.d/textpresso.conf`, *not* in any
   repo -- lives directly on the EC2 host in front of the container): the
   public domain's actual entry point. It allowlists specific paths;
   `/v1/textpresso/api/` was already routed straight to the container's
   published `18080` port, so a new `location /v1/textpresso/annotate` block
   was added routing through the container's published `8080` port (lighttpd)
   instead, since `8082` isn't published to the host. This is the layer to
   check first if the annotation endpoint ever seems unreachable from outside
   despite `tpc_search_combined.py`/`cas_annotate_server.py` looking fine.

`start_textpresso.sh` and the `Dockerfile` were also updated so a fresh image
build/container launches `cas_annotate_server.py` automatically; on the
currently-running container it was deployed by hand (`docker cp` + a manual
process start, since the image wasn't rebuilt).

## Looking up `--category` values

`--category` matches on a Lucene phrase query against the exact stored
`"name (ID)"` string, not a fuzzy or substring check — so historically, an
imperfect value didn't reliably fail loudly. Depending on what else shared
its leading word(s) in the corpus's category list, it could silently return
the same results as the correct string, a wrong/over-broad set, or nothing
(reproduced 2026-08-12: bare `"seed"` returned the identical result set as
`"seed (PO:0009010)"`, purely because no other stored category happened to
start with "seed" in that corpus — a coincidence, not a guarantee).

**As of 2026-08-12, this is fixed**: `tpc_search_combined.py` checks every
`--category` value against a live ontology index before running the query,
and refuses to run on anything that isn't an exact match — no more silent
wrong-or-lucky matching:

```
$ python3 bin/tpc_search_combined.py -c MaizeTest100 --category "seed" "development"
tpc_search_combined.py: error:
--category "seed" does not exactly match a known category. Closest matches:
  --category "seed (PO:0009010)"   (PO)
  --category "seed abscission (GO:0097548)"   (GO)
  --category "seed chalaza (PO:0006333)"   (PO)
  ...
Example: --category "seed (PO:0009010)"
```

A query with no close matches at all gets a plainer nudge rather than a wall
of near-misses, including a keyword-search fallback:

```
$ python3 bin/tpc_search_combined.py -c MaizeTest100 --category "qwxyzplant" "development"
tpc_search_combined.py: error:
--category "qwxyzplant" has no matches found. Try a different word with
bin/tpc_category_search.py "<term>", or drop --category and search by
keyword instead, e.g.:
  python3 bin/tpc_search_combined.py -c MaizeTest100 "qwxyzplant"
```

This check is fail-open on infrastructure problems: if the lookup service
itself is unreachable, it's skipped silently and the search runs as given —
an outage in the suggestion service shouldn't block ordinary search.

### `bin/tpc_category_search.py` — search the ontology directly

A standalone tool for finding the right `--category` string up front,
instead of waiting to be told it's wrong. Searches GO/PO/TO/MAIZE_GENES by
name or synonym (the same index the `--category` check above uses) and
prints candidates ranked by match quality (exact > name-prefix >
synonym-exact > name-substring > synonym-substring):

```bash
# search all ontologies
python3 bin/tpc_category_search.py "seed"

# restrict to one ontology
python3 bin/tpc_category_search.py "adh1" --ontology MAIZE_GENES

# multiple ontologies, more results
python3 bin/tpc_category_search.py "anthocyanin" --ontology GO --ontology PO --limit 10

# JSON output (pipe-friendly)
python3 bin/tpc_category_search.py "seed" --format json
```

Text output includes a copy-pasteable example using the top match:

```
Categories matching "seed":

  seed (PO:0009010)             [PO, exact]
  seed abscission (GO:0097548)  [GO, name_prefix]
  seed chalaza (PO:0006333)     [PO, name_prefix]
  ...

Example:
  python3 bin/tpc_search_combined.py -c <corpus> --category "seed (PO:0009010)" "<keywords>"
```

### Architecture

Both the `--category` validation and the standalone tool call a new
`/v1/textpresso/category_search` endpoint on the same `cas_annotate_server.py`
sidecar used for `--annotate`/`--annotate-sentences` (see "Architecture: how
annotation data reaches the client" above) — same process, same proxy
pattern, one more route added the same additive way. On startup the server
builds an in-memory index from the OBO files listed in `ontology.conf`
(`textpressoapi/category_index.py`, `agr_textpresso` repo): 67,569
categories and 247,270 distinct synonyms as of 2026-08-12, built in a couple
of seconds. The index is only refreshed on server restart, so a monthly
ontology update (see `CLAUDE.md`'s `update_ontology.sh` cron) should be
followed by a restart of `cas_annotate_server.py` to pick up new/changed
categories -- the same "old data until restarted" caveat already noted for
`textpressoapi`/lighttpd's `IndexReader` caching in the 2026-07-13 entry of
`Laura_work_updates_log.md`.

## Other notes
- **Generic-word contamination in `MAIZE_GENES`/`MAIZE_GENES_RELATED`
  matches**: the `zmays_genes_20260708.obo` gene ontology has a known issue
  where its `locus_synonym` field contains common-English-word/jargon
  fragments (e.g. `red`, `expression`, `binding`, `promoter`) misfiled as
  gene synonyms, so `--annotate`/`--annotate-sentences` output can include
  false-positive gene matches for these terms. `--exclude-type references`
  removes the citation-sourced subset of this noise (author initials,
  journal abbreviations) but not genuine in-text false positives from the
  OBO itself
