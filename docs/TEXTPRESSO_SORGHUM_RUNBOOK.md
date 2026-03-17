# Textpresso Sorghum Runbook

## Purpose

This document describes the reproducible steps used to run Sorghum literature in the Dockerized Textpresso stack, from
local PDF staging through searchable UI results.

It is written for collaborators who need to:

- build and start the local Textpresso Docker environment
- stage the Sorghum PDF corpus into the mounted data directory
- run a small test corpus first
- run the full `SorghumBase` ingest
- verify search from the UI and API
- understand the fixes that were needed for CAS2, indexing, metadata, and UI stability

## Repositories Used

Two local repositories were involved:

1. `Textpresso`
   Path used during this work:
   `/Users/kchougul/development/codex_projects/Textpresso`

2. `sorghumbase_textpresso_implementation`
   Path used during this work:
   `/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation`

The Sorghum PDFs were already downloaded locally under:

`/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/sorghum_run/pdfs`

## Host Prerequisites

- macOS or Linux
- Docker Desktop or Docker Engine
- Docker Compose
- git
- enough local disk for:
  - Docker image build artifacts
  - mounted Textpresso data
  - the Sorghum PDF corpus

Optional but useful:

- `curl`
- `python3`

## Textpresso Docker Prerequisites

From the `Textpresso` repository:

1. Copy `.env_example` to `.env` if needed.
2. Set the main ports and the data mount.

Example variables used by the Docker stack:

```bash
TPC_UI_PORT=8080
TPC_API_PORT=18080
TEXTPRESSO_DATA_DIR=/absolute/path/to/Textpresso/.data
```

The Docker stack maps the mounted data root to:

`/data/textpresso`

Inside the container, the important directories are:

- `/data/textpresso/raw_files/pdf`
- `/data/textpresso/tpcas-1`
- `/data/textpresso/tpcas-2`
- `/data/textpresso/luceneindex`
- `/data/textpresso/db`
- `/data/textpresso/imports/metadata`

## Build And Start Textpresso

From the `Textpresso` repository root:

```bash
docker compose build
docker compose up -d
docker compose ps
```

Expected ports:

- UI: `http://localhost:8080/tpc/search`
- API: `http://localhost:18080/v1/textpresso/api/available_corpora`

## Stage Sorghum PDFs Into The Data Mount

The local PDF source used here was:

`sorghumbase_textpresso_implementation/sorghum_run/pdfs`

The full PDF corpus was staged into the Textpresso data mount under:

`Textpresso/.data/raw_files/pdf/SorghumBase/<accession>/<accession>.pdf`

This layout is required by the incremental ingest pipeline.

Each paper should look like:

```text
.data/raw_files/pdf/SorghumBase/10.1007_s00425-022-03866-7/10.1007_s00425-022-03866-7.pdf
```

## Stage Sorghum Metadata

The metadata CSV used by the improved bib generator was staged at:

`Textpresso/.data/imports/metadata/sorghumbase_papers.csv`

This file is used to fill:

- title
- journal
- abstract
- PMID citation
- year when present
- authors when present

## Run A Small Test Corpus First

Before loading all PDFs, create a minimal test corpus:

`Textpresso/.data/raw_files/pdf/SorghumTest`

In this work, a 3-paper test set was used.

Example papers:

- `10.1007_978-1-0716-1816-5_12`
- `10.1007_978-1-0716-2067-0_5`
- `10.1007_978-1-0716-2537-8_17`

Run the incremental pipeline in the container:

```bash
docker exec agr-textpresso-textpresso-1 sh -lc \
  "run_tpc_pipeline_incremental.sh \
   -p /data/textpresso/raw_files/pdf \
   -x /data/textpresso/raw_files/xml \
   -c /data/textpresso/tpcas-1 \
   -C /data/textpresso/tpcas-2 \
   -t /data/textpresso/tmp \
   -i /data/textpresso/luceneindex \
   -P 4"
```

## Smoke Test

A smoke test script was added to the `Textpresso` repo:

`tpctools/smoke_test_pipeline.sh`

Run it from the `Textpresso` root:

```bash
./tpctools/smoke_test_pipeline.sh
```

This validates:

- PDF ingest
- CAS1 generation
- CAS2 generation
- index rebuild
- API searchability

## Full SorghumBase Ingest

After `SorghumTest` passed, the full corpus was staged as `SorghumBase` and ingested with the same pipeline.

Validation points after the full run:

1. `SorghumBase` appears in `available_corpora`
2. `get_documents_count` for `sorghum` in `SorghumBase` returns a nonzero result
3. `search_documents` returns results
4. the UI at `http://localhost:8080/tpc/search` shows rows for `SorghumBase`

## Problems Encountered And Fixes Applied

### 1. CAS2 generation failed

Symptoms:

- no usable `tpcas-2` output for new PDF corpora
- logs showed missing `pcrelations` and `tpontology`
- `runAECpp` output was not copied correctly

Fixes applied in the `Textpresso` repo:

- materialize ontology temp tables when only temporary lexicon tables exist
- run `runAECpp` with an explicit `LD_LIBRARY_PATH`
- preserve the pipeline even when ontology data is limited

Relevant file:

- `tpctools/run_tpc_pipeline_incremental.sh`

### 2. New PDF corpora were not indexed

Symptoms:

- corpus visible but keyword search returned zero results
- `cas2index` silently skipped records without valid `.bib` files

Fixes:

- auto-generate fallback `.bib` files for PDF-derived CAS output
- scan the correct nested CAS2 path depth
- fail fast on index rebuild failures
- restart `textpressoapi` after index rebuild

Relevant files:

- `tpctools/run_tpc_pipeline_incremental.sh`
- `tpctools/smoke_test_pipeline.sh`

### 3. `runAECpp` segfaulted after CAS2 write

Symptoms:

- CAS2 output was written, then the process crashed

Fix:

- replace header-defined static UIMA globals with long-lived accessor-backed singletons

Relevant files:

- `libtpc/uima-annotators/uimaglobaldefinitions.h`
- tokenizer and lexicon annotator sources in `libtpc/uima-annotators/`

### 4. `textpressoapi` crashed on `search_documents`

Symptoms:

- `get_documents_count` worked
- `search_documents` killed the API
- UI search could not show results reliably

Root cause:

- Lucene compressed-field decompression crashed in document detail loading

Fix:

- stop requesting detailed compressed metadata fields in the API search route
- use only safe fields for search hits
- derive safe fallback accession/title values from the filepath
- read title/journal/type/year/author from `.bib` sidecars instead

Relevant file:

- `textpressoapi/main.cpp`

### 5. `tpc/search` UI still crashed after API was fixed

Symptoms:

- backend API was healthy
- the Wt CGI app behind `/tpc/search` still segfaulted

Fix:

- patch the Wt search result builders to stop requesting `DOCUMENTS_FIELDS_DETAILED`
- use safe fields and `.bib` sidecar metadata instead
- rebuild and redeploy the `tpc` CGI binary

Relevant file:

- `textpressocentral/TpC/Search.cpp`

### 6. Author metadata was noisy

Symptoms:

- author lines looked like:
  - editor names
  - citation lines
  - license strings

Fix:

- improve `generate_pdf_bib.py`
- prefer clean `pdfinfo` metadata for author/title/year
- use tighter PDF first-page heuristics only as fallback
- refresh all `SorghumBase` `.bib` files in place

Relevant file:

- `tpctools/generate_pdf_bib.py`

## Reproducible Commands

### Start services

```bash
cd /path/to/Textpresso
docker compose build
docker compose up -d
docker compose ps
```

### Check corpora

```bash
curl -s http://localhost:18080/v1/textpresso/api/available_corpora
```

### Check document count

```bash
python3 - <<'PY'
import json, urllib.request
payload = {
    "query": {
        "keywords": "sorghum",
        "type": "document",
        "case_sensitive": False,
        "sort_by_year": False,
        "count": 5,
        "corpora": ["SorghumBase"],
    }
}
req = urllib.request.Request(
    "http://localhost:18080/v1/textpresso/api/get_documents_count",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
)
print(urllib.request.urlopen(req).read().decode())
PY
```

### Search documents

```bash
python3 - <<'PY'
import json, urllib.request
payload = {
    "query": {
        "keywords": "sorghum",
        "type": "document",
        "case_sensitive": False,
        "sort_by_year": False,
        "count": 5,
        "corpora": ["SorghumBase"],
    }
}
req = urllib.request.Request(
    "http://localhost:18080/v1/textpresso/api/search_documents",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
)
print(urllib.request.urlopen(req).read().decode())
PY
```

### Open the UI

Go to:

`http://localhost:8080/tpc/search`

Use:

- corpus: `SorghumBase`
- keyword: `sorghum`

If the page was already open during a rebuild, open a new tab or hard refresh.

## Refresh Sorghum `.bib` Files

After changing `generate_pdf_bib.py`, refresh the Sorghum metadata in the running container:

```bash
docker cp tpctools/generate_pdf_bib.py agr-textpresso-textpresso-1:/tmp/generate_pdf_bib.py

docker exec agr-textpresso-textpresso-1 sh -lc '
find /data/textpresso/tpcas-2/SorghumBase -mindepth 2 -maxdepth 2 -name "*.bib" | while read -r bib; do
  base=$(basename "$bib" .bib)
  pdf="/data/textpresso/raw_files/pdf/SorghumBase/$base/$base.pdf"
  if [ -f "$pdf" ]; then
    python3 /tmp/generate_pdf_bib.py \
      --pdf "$pdf" \
      --bib "$bib" \
      --accession "$base" \
      --metadata-dir /data/textpresso/imports/metadata >/dev/null 2>&1
  fi
done
'
```

The API and Wt UI read `.bib` sidecars at request time, so a metadata refresh does not require a reingest.

## Current Expected State

At the end of the successful run:

- `SorghumBase` is searchable from the UI
- `search_documents` returns nonzero `sorghum` results
- title, journal, type, year, and many author fields are populated from `.bib` metadata

## Remaining Known Limitations

- some PDFs only expose a single clean author in `pdfinfo`, so a subset of records may show first author only
- semantic ontology annotation quality still depends on the ontology assets present in the mounted data directory
- the main `Textpresso` repo contains additional local fixes not committed here; this document references them for reproducibility
