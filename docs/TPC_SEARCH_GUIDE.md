# `tpc_search.py` / `tpc_search_internal.py` — usage guide

Reference for the two Textpresso search command-line tools in
`bin/` as of 2026-07-15.

## Overview

Both scripts search a Textpresso corpus over the REST API. They share an
identical base option set; `tpc_search_internal.py` is a superset that adds
local CAS2 file access for ontology annotation.

| | `tpc_search.py` | `tpc_search_internal.py` |
|---|---|---|
| Requires | network access to the public API only | server-side access to the CAS2 data directory |
| Who can run it | anyone with network access | server users only |
| Adds | — | `--annotate`, `--annotate-sentences`, `--related-synonyms`, `--ontology`, `--cas-root`, precise `--exclude-type` |

The Textpresso instance runs inside a Docker container
(`agr-textpresso-textpresso-1`); its API is proxied through lighttpd and
exposed publicly at:

```
http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api/
```

## `tpc_search.py`

### Usage

```
python3 bin/tpc_search.py [options] [keywords]
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
--category CATEGORY        Restrict to ontology category (repeatable). Must include the ID suffix obtained from OBO file, e.g. "seed (PO:0009010)" or "adh1 (tpzm:0008786)"
--categories-and           Require ALL categories to match (default: ANY)
--sort-by-year             Sort by year instead of relevance score
--format text|json         Output format (default: text)
--url URL                  API base URL
--list-corpora              List available corpora and exit
--exclude-type TYPE        Drop a document-level result if the excluded section is the only named section it matches in (repeatable). Requires --type document — errors otherwise, pointing at tpc_search_internal.py.
```

**`--exclude` vs. `--exclude-type`** — these are unrelated: `--exclude` drops
results containing given *keywords*; `--exclude-type` drops results whose
match falls only inside a given CAS *section* (e.g. bibliography).

### `--exclude-type` — known gap in this script

`tpc_search.py` has no local CAS2 access, so it can't check section
boundaries directly. It approximates by re-running the query once per *other*
named section type and keeping the document if it matches in any of them —
proof of a match outside the excluded section. **This misses a match sitting
in prose not covered by any detected section boundary**, which could cause a
document to be dropped incorrectly. `tpc_search_internal.py`'s
`--type sentence`-based check does not have this gap (see below), so prefer
it when precision matters.

### Examples

```bash
# Basic sentence search
python3 bin/tpc_search.py -c MaizeTest100 "flowering time"

# Multiple corpora
python3 bin/tpc_search.py -c MaizeTest100 -c SorghumBase "drought tolerance"

# Section-scoped
python3 bin/tpc_search.py -c MaizeTest100 --type abstract "drought"

# Metadata filters
python3 bin/tpc_search.py -c MaizeTest100 --author "Buckler" --year 2014 "GWAS"
python3 bin/tpc_search.py -c MaizeOA --journal "Nature" --exact-journal "maize"

# Keyword modifiers
python3 bin/tpc_search.py -c MaizeTest100 --exclude "Arabidopsis" "kernel weight"
python3 bin/tpc_search.py -c MaizeOA --case-sensitive "ZmMADS"

# Category search (ID suffix required)
python3 bin/tpc_search.py -c MaizeTest100 --category "seed (PO:0009010)" "development"

# Drop bibliography-only document hits
python3 bin/tpc_search.py -c MaizeTest100 --type document "MARK" --exclude-type references

# JSON output (pipe-friendly)
python3 bin/tpc_search.py -c MaizeTest100 "anthocyanin" --format json | jq '.[].title'

# List available corpora
python3 bin/tpc_search.py --list-corpora
```

## `tpc_search_internal.py` — server-side search with annotation

Identical search interface to `tpc_search.py`, plus flags that read the
local CAS2 files.

**CAS2 root paths:**

| Context | Path |
|---------|------|
| Host (outside container) | `/home/ec2-user/agr_textpresso/.data/tpcas-2` |
| Inside the container | `/data/textpresso/tpcas-2` |

The default `--cas-root` is the host path.

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
--cas-root PATH         CAS2 root directory
--exclude-type TYPE     Exclude results from this CAS section type
                        (repeatable) — see below for per-mode behavior
```

### `--annotate` — ontology term summary per paper

Appends a grouped list of all ontology terms found anywhere in the paper to
each result. Works with text output (default) or JSON (`--format json`).

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

**RELATED synonyms:** Use `--ontology MAIZE_GENES_RELATED` to
see the RELATED-synonym matches.

```bash
# default: EXACT locus synonyms only
python3 bin/tpc_search_internal.py -c MaizeTest100 "adh1" --annotate

# include RELATED locus synonyms too
python3 bin/tpc_search_internal.py -c MaizeTest100 "adh1" --annotate --related-synonyms

# RELATED-only
python3 bin/tpc_search_internal.py -c MaizeTest100 "adh1" --annotate --ontology MAIZE_GENES_RELATED
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
python3 bin/tpc_search_internal.py -c MaizeTest100 "anthocyanin" --annotate-sentences

# All sentences
python3 bin/tpc_search_internal.py -c MaizeTest100 "anthocyanin" \
    --annotate-sentences --full-text

# Filter to GO and PO only
python3 bin/tpc_search_internal.py -c MaizeTest100 "anthocyanin" \
    --annotate-sentences --ontology GO --ontology PO
```

### `--exclude-type` — precise, CAS2-based section exclusion

Unlike `tpc_search.py`'s best-effort version, this script has direct CAS2
access and filters exactly, per mode:

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
python3 bin/tpc_search_internal.py -c MaizeTest100 --type sentence \
    --accession 10.1101_gr.277459.122 "Wickham" --exclude-type references

# document-level: precise drop -- "HelitronScanner" appears in exactly one
# MaizeTest100 paper, and only inside its bibliography (a citation of the
# HelitronScanner tool's own paper), so excluding references drops it to zero:
python3 bin/tpc_search.py -c MaizeTest100 --type document "HelitronScanner"              # -> 1 result
python3 bin/tpc_search_internal.py -c MaizeTest100 --type document \
    "HelitronScanner" --exclude-type references                                          # -> No results (correct)

# strip bibliography-sourced false positives out of an ontology summary
python3 bin/tpc_search_internal.py -c MaizeTest100 --type document \
    --accession 10.1101_gr.277459.122 --annotate --exclude-type references

# multiple excluded types
python3 bin/tpc_search_internal.py -c MaizeTest100 "adh1" \
    --annotate --exclude-type references --exclude-type acknowledgments
```

## Other notes
- **`--category` requires the exact stored string, ID suffix from the OBO file included** —
  e.g. `"seed (PO:0009010)"`, not `"seed"`. A malformed category string
  returns "No results" and can look like a broken endpoint rather than a
  formatting mistake.
- **Generic-word contamination in `MAIZE_GENES`/`MAIZE_GENES_RELATED`
  matches**: the `zmays_genes_20260708.obo` gene ontology has a known issue
  where its `locus_synonym` field contains common-English-word/jargon
  fragments (e.g. `red`, `expression`, `binding`, `promoter`) misfiled as
  gene synonyms, so `--annotate`/`--annotate-sentences` output can include
  false-positive gene matches for these terms. `--exclude-type references`
  removes the citation-sourced subset of this noise (author initials,
  journal abbreviations) but not genuine in-text false positives from the
  OBO itself
