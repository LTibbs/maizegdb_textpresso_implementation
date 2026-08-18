# Textpresso REST API — tutorial (no Python required)

This is the raw-HTTP counterpart to
[`TPC_SEARCH_GUIDE.md`](TPC_SEARCH_GUIDE.md), which documents the
`tpc_search_combined.py` command-line wrappers. Everything
those scripts do boils down to plain HTTP requests against two services, so
if you're calling the API from a language other than Python — or just want
to see the wire format directly — this is that version. Examples use `curl`
and `jq`; translate freely into whatever HTTP client you have.

Nothing here requires server-side access. All endpoints are public over the
network.

## Base URLs

```
http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api        search API (C++ textpressoapi)
http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/annotate   CAS2 ontology annotation service
```

Two separate services, both proxied off the same host. The first does
keyword/metadata search over the Lucene index; the second serves parsed
CAS2 ontology-annotation data (ontology terms, sentence positions, section
boundaries) for a given document. Search works standalone; annotation
lookups are usually driven by a document identifier you got back from a
search.

## 1. List available corpora

```bash
curl -s "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api/available_corpora"
```

```json
["MaizeTest100", "MaizeOA", "SorghumBase", ...]
```

A `GET` with no body. Use this to discover valid values for `corpora` below.

## 2. Search: `POST /search_documents`

This is the one endpoint that does all searching — keyword, metadata
filters, section-scoped, everything. Content-Type must be `application/json`.

### Minimal request

```bash
curl -s -X POST \
  -H "Content-Type: application/json" \
  -d '{
        "query": {
          "type": "sentence",
          "corpora": ["MaizeTest100"],
          "keywords": "flowering time"
        },
        "count": 5,
        "include_match_sentences": true
      }' \
  "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api/search_documents"
```

### Payload shape

```json
{
  "query": {
    "type": "sentence",
    "corpora": ["MaizeTest100", "SorghumBase"],
    "keywords": "maize AND drought",
    "exclude_keywords": "Arabidopsis",
    "case_sensitive": false,
    "author": "Buckler",
    "exact_match_author": false,
    "journal": "Plant Cell",
    "exact_match_journal": false,
    "year": "2022",
    "accession": "10.1007_s00425-012-1754-3",
    "paper_type": "Journal_article",
    "categories": ["seed (PO:0009010)"],
    "categories_and_ed": false,
    "sort_by_year": false
  },
  "count": 50,
  "include_match_sentences": true
}
```

Notes learned from the wrapper scripts' inline comments — these are easy to
get wrong by guessing:

- **`corpora` lives inside `query`**, not at the top level of the payload.
- **`include_match_sentences` is only valid when `query.type == "sentence"`.**
  Sending it with any other `type` returns HTTP 401 — a quirk of this API,
  not a real auth failure. Only set it for sentence-type queries.
- Every field in `query` is optional except `type` and `corpora` — omit keys
  you don't need rather than sending empty/null values.
- `count` is the top-level max-results field (default used by the CLI
  wrapper: 50; hard API max: 200).
- `accession` (DOI) values must have `/` replaced with `_`, e.g. search for
  `10.1007_s00425-012-1754-3` to retrieve DOI `10.1007/s00425-012-1754-3`.
- `--category` in the CLI maps to `categories` (a list) here; each value must
  be the *exact* stored `"name (ID)"` string, e.g. `"seed (PO:0009010)"` —
  not a bare word. See "Looking up category values" below; the search API
  itself does not validate this for you, it just silently returns whatever
  matches (or doesn't) that literal string.
- `categories_and_ed: true` requires all listed categories to match (AND);
  default/`false` is OR (any).

### `query.type` values

| Type | What it searches | Returns |
|------|-------------------|---------|
| `sentence` | Full paper text | Matching sentence text (needs `include_match_sentences: true`) |
| `document` | Full paper text | Document-level hits only, no sentence text |
| `abstract` | Abstract section | Document-level hits |
| `title` | Title only | Document-level hits |
| `introduction` | Introduction section | Document-level hits |
| `result` | Results section | Document-level hits |
| `discussion` | Discussion section | Document-level hits |
| `conclusion` | Conclusion section | Document-level hits |
| `background` | Background section | Document-level hits |
| `design` | Study design section | Document-level hits |
| `materials and methods` | Methods section | Document-level hits |
| `acknowledgments` | Acknowledgments section | Document-level hits |
| `references` | Bibliography section | Document-level hits |

Section-scoped types only return hits for papers whose CAS2 file carries
section boundaries (not guaranteed for all ingested papers).

### Response shape

A JSON array of result objects:

```json
[
  {
    "identifier": "MaizeTest100//10.1038_srep35479/10.1038_srep35479.tpcas",
    "accession": "10.1038_srep35479",
    "title": "...",
    "author": "...",
    "year": "2016",
    "journal": "Sci Rep",
    "matched_sentences": ["... sentence text ..."]
  },
  ...
]
```

`matched_sentences` is only populated for `type: sentence` queries.
`identifier` format has been observed inconsistently — sometimes one slash
after the corpus name, sometimes two — so if you need the corpus name back
out of it, split on the first `/` rather than assuming a fixed number of
segments.

### Examples

```bash
# Multiple corpora, top 5 by relevance
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"sentence","corpora":["MaizeTest100","SorghumBase"],
       "keywords":"drought tolerance"},"count":5,"include_match_sentences":true}' \
  "$BASE/search_documents"

# Section-scoped (abstract only)
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"abstract","corpora":["MaizeTest100"],"keywords":"drought"},
       "count":50}' \
  "$BASE/search_documents"

# Metadata filters, no keywords
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"sentence","corpora":["MaizeTest100"],
       "author":"Buckler","year":"2014","keywords":"GWAS"},
       "count":50,"include_match_sentences":true}' \
  "$BASE/search_documents"

# Sort by year, cap count
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"sentence","corpora":["MaizeTest100"],
       "keywords":"yield","sort_by_year":true},
       "count":20,"include_match_sentences":true}' \
  "$BASE/search_documents"

# Category-restricted (exact stored string required)
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"sentence","corpora":["MaizeTest100"],
       "keywords":"development","categories":["seed (PO:0009010)"]},
       "count":50,"include_match_sentences":true}' \
  "$BASE/search_documents"

# Pipe-friendly: pull just titles out with jq
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"sentence","corpora":["MaizeTest100"],
       "keywords":"anthocyanin"},"count":50,"include_match_sentences":true}' \
  "$BASE/search_documents" | jq '.[].title'
```

(`$BASE` = `http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api`
in the examples above — export it once and reuse.)

## 3. Ontology annotation: `GET /v1/textpresso/annotate`

Separate service (`cas_annotate_server.py`), same host, different path —
note it's a sibling of `/v1/textpresso/api`, not nested under it. Given a
document identifier from a search result, returns the paper's parsed CAS2
data: sentences with positions, ontology annotations, and section
boundaries.

```bash
curl -s -G "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/annotate" \
  --data-urlencode "identifier=MaizeTest100//10.1038_srep35479/10.1038_srep35479.tpcas"
```

### Query parameters

| Param | Repeatable | Meaning |
|-------|------------|---------|
| `identifier` | no | Document identifier, exactly as returned by `/search_documents` |
| `ontology` | yes | Restrict annotations to one or more of `GO`, `PO`, `TO`, `MAIZE_GENES`, `MAIZE_GENES_RELATED`, `OTHER` |
| `related_synonyms` | no | `1` to include RELATED-synonym gene matches (ignored if `ontology` already includes `MAIZE_GENES_RELATED`) |

With no `ontology`/`related_synonyms` params, the default is all ontologies
except `MAIZE_GENES_RELATED` (i.e. EXACT gene-locus synonyms only, not
RELATED ones) — mirrors the CLI's `--annotate` default.

### Response shape

```json
{
  "sentences": [
    {"begin": 2615, "end": 2681, "text": "fie three endosperm development stages are distinct but overlapped"},
    ...
  ],
  "annotations": [
    {
      "begin": 2625, "end": 2646,
      "term": "endosperm development",
      "category": "seed development (GO:0048316)",
      "ontology": "GO",
      "onto_id": "GO:0048316"
    },
    ...
  ],
  "sections": [
    {"type": "references", "begin": 15000, "end": 18000},
    ...
  ]
}
```

`sentences` and `annotations` are flat lists keyed by character offset
(`begin`/`end`) into the paper's full text; `sections` gives the offset
ranges of named sections (e.g. `references`, `abstract`) so you can join
annotations/sentences to sections yourself by offset overlap. There is no
endpoint that returns this pre-joined — the CLI wrapper does the offset
matching client-side (see `casannot.py` for the exact logic, if you want to
replicate it: an annotation/sentence is "in" a section if its `begin`/`end`
falls within the section's range).

A 404 means no CAS2 data exists for that identifier (not indexed, or not
yet re-tokenized with section detection).

### Building an ontology summary (what `--annotate` does)

There's no summary endpoint — you fetch `annotations` and group them
yourself, client-side:

```bash
curl -s -G "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/annotate" \
  --data-urlencode "identifier=MaizeTest100//10.1038_srep35479/10.1038_srep35479.tpcas" \
  | jq -r '.annotations | group_by(.ontology) | map({(.[0].ontology): ([.[].term] | unique)}) | add'
```

```json
{
  "GO": ["anthocyanin biosynthesis", "biosynthetic process", "pigmentation", "seed development"],
  "MAIZE_GENES": ["R1", "pl1"],
  "PO": ["endosperm", "kernel", "pericarp", "seed"],
  "TO": ["heterosis"]
}
```

### Excluding a section (e.g. drop bibliography noise)

Filter `annotations` (or `sentences`) to those whose offsets don't fall
inside an excluded section's range:

```bash
curl -s -G "$BASE_ANNOTATE" --data-urlencode "identifier=$ID" | jq '
  . as $doc
  | ($doc.sections | map(select(.type == "references"))) as $excluded
  | $doc.annotations | map(select(
      . as $a | [$excluded[] | select($a.begin >= .begin and $a.end <= .end)] | length == 0
    ))
'
```

This is a client-side join — the annotate endpoint itself has no
`exclude_type` parameter; both CLI scripts do this same filtering after the
fact (`tpc_search_internal.py`'s precise, sentence-position-based version;
`tpc_search.py`'s cruder document-level approximation done entirely via
extra `/search_documents` calls per section type, described in
[TPC_SEARCH_GUIDE.md](TPC_SEARCH_GUIDE.md#--exclude-type--known-gap-in-this-script)).

## 4. Category lookup: `GET /v1/textpresso/category_search`

Same sidecar service as `/annotate`. Looks up candidate `categories` values
by free-text name/synonym — use this before searching, rather than guessing
a category string and having it silently over/under-match.

```bash
curl -s -G "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/category_search" \
  --data-urlencode "q=seed" \
  --data-urlencode "limit=8"
```

Repeat `ontology=GO`/`ontology=PO`/etc. to restrict.

### Response shape

```json
{
  "matches": [
    {"id": "PO:0009010", "name": "seed", "category": "seed (PO:0009010)", "ontology": "PO", "matched_on": "exact"},
    {"id": "GO:0097548", "name": "seed abscission", "category": "seed abscission (GO:0097548)", "ontology": "GO", "matched_on": "name_prefix"},
    ...
  ]
}
```

Use the `category` field verbatim as a `categories` entry in a
`/search_documents` payload — it's the exact stored string the search
backend requires. `matched_on` is ranked exact > name_prefix >
synonym_exact > name_substring > synonym_substring; the CLI wrapper always
recommends `matches[0]`.

```bash
# Find the right category string, then use it in a search
curl -s -G "$BASE_ANNOTATE_HOST/category_search" --data-urlencode "q=seed" \
  | jq -r '.matches[0].category'
# -> "seed (PO:0009010)"
```

## Putting it together: a full workflow in curl

```bash
BASE="http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api"
ANNOTATE="http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/annotate"

# 1. Search
RESULTS=$(curl -s -X POST -H "Content-Type: application/json" \
  -d '{"query":{"type":"sentence","corpora":["MaizeTest100"],
       "keywords":"anthocyanin"},"count":5,"include_match_sentences":true}' \
  "$BASE/search_documents")

# 2. Grab the first result's identifier
ID=$(echo "$RESULTS" | jq -r '.[0].identifier')

# 3. Fetch its ontology annotations
curl -s -G "$ANNOTATE" --data-urlencode "identifier=$ID" \
  | jq '.annotations | group_by(.ontology) | map({(.[0].ontology): [.[].term] | unique}) | add'
```

## Summary: endpoints at a glance

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `{api}/available_corpora` | GET | List searchable corpora |
| `{api}/search_documents` | POST (JSON body) | Keyword/metadata/section search |
| `{annotate-host}/annotate` | GET (query params) | Ontology annotations, sentences, sections for one document |
| `{annotate-host}/category_search` | GET (query params) | Look up exact `categories` strings by free-text term |

`{api}` = `.../v1/textpresso/api`, `{annotate-host}` = `.../v1/textpresso`
(one level up — `annotate` and `category_search` are siblings of `api`, not
children of it).

For the equivalent Python CLI tools that wrap all of this (with output
formatting, `--exclude-type` handling, category validation, etc.), see
[TPC_SEARCH_GUIDE.md](TPC_SEARCH_GUIDE.md).
