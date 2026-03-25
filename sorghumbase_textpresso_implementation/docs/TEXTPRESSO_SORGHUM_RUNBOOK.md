# Textpresso Sorghum Runbook

## Purpose

This document is the reproducible operator guide for loading Sorghum literature into a local Textpresso instance and
making it searchable from both the API and UI.

It is written for a collaborator who needs to:

- understand the overall Textpresso architecture
- know which repository does what
- bring up the Dockerized Textpresso stack
- stage Sorghum PDFs and metadata
- validate the pipeline on a small corpus first
- ingest the full `SorghumBase` corpus
- verify API and UI search
- understand the implementation fixes that make the workflow reliable

## What This Repository Does And Does Not Do

This repository contains two different things that are easy to confuse:

1. Sorghum-specific project materials
   These live under `sorghumbase_textpresso_implementation/` and include:
   - Sorghum metadata
   - Sorghum helper scripts
   - runbooks
   - patch set and collaborator docs

2. A standalone Dockerfile for the classifier code in this repository
   This Dockerfile is useful for testing the Python classifier package in isolation.
   It does **not** run the full Textpresso search stack.

To run the actual searchable Textpresso instance, you still need the separate `Textpresso` repository and its Docker
Compose stack.

## Repositories And Responsibilities

### 1. `Textpresso`

Role:
- runs the real search system
- contains the Docker Compose stack
- contains the ingest pipeline
- contains the API and Wt UI

Path used during this work:
- `/Users/kchougul/development/codex_projects/Textpresso`

### 2. `sorghumbase_textpresso_implementation`

Role:
- holds Sorghum-specific inputs and collaborator documentation
- holds the Sorghum metadata CSV
- holds the curated Textpresso patch set
- holds Sorghum-specific helper scripts and reports

Path used during this work:
- `/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation`

## Architecture Overview

The workflow has two layers.

### Layer A: Source Inputs

These are the materials you start with:

- local Sorghum PDFs
- Sorghum metadata CSV
- Textpresso code
- Sorghum project docs and patch set

### Layer B: Textpresso Processing Pipeline

The Textpresso stack transforms those inputs in stages:

1. Raw PDFs
   Stored under `raw_files/pdf/<corpus>/<accession>/<accession>.pdf`

2. CAS1
   First-stage extracted document representation
   Output directory: `tpcas-1`

3. CAS2
   Second-stage annotated document representation
   Output directory: `tpcas-2`

4. `.bib` sidecars
   Metadata files attached to documents for title, journal, year, author, and related display fields

5. Lucene index and db files
   Searchable structures used by the API and UI

6. API and UI
   - API serves corpora, counts, documents, and sentences
   - Wt UI at `/tpc/search` provides the interactive search interface

If a document is visible in `raw_files/pdf` but not searchable, the failure is typically in CAS2 generation, bib
generation, indexing, or search-service refresh.

## Step 0: Prerequisites

### Host prerequisites

- macOS or Linux
- Docker Desktop or Docker Engine
- Docker Compose
- git
- `python3`
- enough disk space for:
  - Docker images
  - mounted Textpresso data
  - Sorghum PDFs
  - CAS and Lucene outputs

Optional but useful:

- `curl`
- `pdfinfo`

### Required local checkouts

You need both repositories side by side:

```text
/path/to/Textpresso
/path/to/Textpresso/sorghumbase_textpresso_implementation
```

### Recommended docs to keep open

- [TEXTPRESSO_SORGHUM_FLOWCHART.md](/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_FLOWCHART.md)
- [TEXTPRESSO_PATCHSET_GUIDE.md](/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/sorghumbase_textpresso_implementation/docs/TEXTPRESSO_PATCHSET_GUIDE.md)

## Step 1: Prepare The Textpresso Repository

### What this step does

This step prepares the repository that actually runs the Textpresso services and ingest pipeline.

### Why it matters

If this repository is missing the Sorghum integration fixes, the later stages can fail in several ways:

- CAS2 generation can fail
- new PDF corpora can be skipped during indexing
- metadata can be blank
- API or UI document search can crash

### Procedure

Start in the `Textpresso` repo:

```bash
cd /path/to/Textpresso
git status
```

If you are starting from a clean upstream checkout, apply the curated patch set from this repository:

```bash
git checkout -b codex/sorghum-textpresso-fixes
git apply --reject --whitespace=fix \
  /path/to/sorghumbase_textpresso_implementation/sorghumbase_textpresso_implementation/docs/patches/textpresso-sorghum-fixes.patch
git add .
git commit -m "Apply Sorghum Textpresso integration fixes"
```

### Output of this step

- a patched `Textpresso` checkout that contains the Sorghum-related pipeline, search, and metadata fixes

## Step 2: Configure The Docker Environment

### What this step does

This step sets ports and tells the Docker stack where the persistent Textpresso data directory will live on the host.

### Why it matters

Textpresso writes all important runtime artifacts into the mounted data directory. If this is not configured correctly,
your ingest outputs will be lost or the services will not see the same files.

### Procedure

From the `Textpresso` repository:

```bash
cp .env_example .env
```

Edit `.env` and set at minimum:

```bash
TPC_UI_PORT=8080
TPC_API_PORT=18080
TEXTPRESSO_DATA_DIR=/absolute/path/to/Textpresso/.data
```

### Important mount concept

Inside the container, the mounted data root appears at:

```text
/data/textpresso
```

Important container-side subdirectories:

- `/data/textpresso/raw_files/pdf`
- `/data/textpresso/raw_files/xml`
- `/data/textpresso/tpcas-1`
- `/data/textpresso/tpcas-2`
- `/data/textpresso/tmp`
- `/data/textpresso/luceneindex`
- `/data/textpresso/db`
- `/data/textpresso/imports/metadata`

### Output of this step

- a configured `.env`
- a known persistent data mount location

## Step 3: Build And Start The Textpresso Services

### What this step does

This step builds the Textpresso Docker images and starts the searchable services locally.

### Why it matters

Nothing downstream works until the UI and API services are running against the same mounted data directory.

### Procedure

From the `Textpresso` repository root:

```bash
docker compose build
docker compose up -d
docker compose ps
```

### Expected outputs

- UI: [http://localhost:8080/tpc/search](http://localhost:8080/tpc/search)
- API corpora endpoint: [http://localhost:18080/v1/textpresso/api/available_corpora](http://localhost:18080/v1/textpresso/api/available_corpora)

### Verification

```bash
curl -s http://localhost:18080/v1/textpresso/api/available_corpora
```

Expected result:
- a JSON array of currently known corpora

## Step 4: Validate This Repo’s Standalone Dockerfile

### What this step does

This step validates the standalone Dockerfile in this repository.

### Why it matters

It verifies the Sorghum-side classifier package in isolation, but it does **not** create the searchable Textpresso UI.
This is a code-quality and reproducibility check for this repo itself.

### Procedure

From this repository root:

```bash
cd /path/to/sorghumbase_textpresso_implementation
docker build -t sorghumbase-textpresso-implementation .
docker run --rm sorghumbase-textpresso-implementation
```

### Expected output

- the unit tests pass inside the container

### Architectural relation

This step validates the Sorghum helper codebase. It is separate from the main Textpresso deployment in Steps 1 to 3.

## Step 5: Stage Sorghum Inputs Into The Textpresso Data Mount

### What this step does

This step places the Sorghum PDFs and metadata into the host directory mounted into the Textpresso container.

### Why it matters

The incremental ingest pipeline reads only from the mounted data layout. If the files are not placed in the correct
directories, the pipeline will not discover them.

### Input locations used in this work

Local PDFs:

```text
/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/sorghum_run/pdfs
```

Metadata CSV:

```text
/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/sorghumbase_textpresso_implementation/metadata/sorghumbase_papers.csv
```

### Required target layout

The full corpus should be staged as:

```text
Textpresso/.data/raw_files/pdf/SorghumBase/<accession>/<accession>.pdf
```

Example:

```text
.data/raw_files/pdf/SorghumBase/10.1007_s00425-022-03866-7/10.1007_s00425-022-03866-7.pdf
```

The metadata CSV should be staged as:

```text
Textpresso/.data/imports/metadata/sorghumbase_papers.csv
```

### Output of this step

- raw PDF corpus under `raw_files/pdf/SorghumBase`
- metadata CSV under `imports/metadata`

## Step 6: Create A Small Validation Corpus

### What this step does

This step creates a small subset corpus named `SorghumTest`.

### Why it matters

Always validate the pipeline on a small representative corpus before running the full ingest. It reduces debugging
time and makes failures cheaper to isolate.

### Example 3-paper validation set used in this work

- `10.1007_978-1-0716-1816-5_12`
- `10.1007_978-1-0716-2067-0_5`
- `10.1007_978-1-0716-2537-8_17`

These should be staged under:

```text
Textpresso/.data/raw_files/pdf/SorghumTest/<accession>/<accession>.pdf
```

### Output of this step

- a small corpus suitable for quick ingest and debugging

## Step 7: Run The Incremental Ingest Pipeline

### What this step does

This step converts staged PDFs into Textpresso search artifacts.

### Why it matters

This is the core transformation step that builds:

- CAS1
- CAS2
- `.bib` sidecars
- Lucene index
- db files

### Procedure

From the host:

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

### Meaning of the pipeline outputs

- `tpcas-1`: extracted text representation of each document
- `tpcas-2`: enriched representation after annotation
- `.bib`: metadata used by search results and detail display
- Lucene index: document and sentence retrieval structures
- db files: search support tables and lookup files

### Output of this step

- a searchable or nearly-searchable corpus depending on whether all downstream stages completed successfully

## Step 8: Run The Smoke Test

### What this step does

This runs the automated end-to-end smoke test added in the `Textpresso` repo.

### Why it matters

It verifies the full ingest/search chain instead of relying on manual spot checks only.

### Procedure

From the `Textpresso` root:

```bash
./tpctools/smoke_test_pipeline.sh
```

### What it validates

- PDF ingest
- CAS1 generation
- CAS2 generation
- bib generation
- index rebuild
- API searchability

### Output of this step

- pass/fail signal for the current ingest pipeline and API path

## Step 9: Verify The Small Test Corpus

### What this step does

This step confirms that `SorghumTest` is really searchable.

### Why it matters

A corpus being present on disk is not enough. A corpus being listed by the API is also not enough. Search itself must
work before the full corpus is ingested.

### API verification examples

Check corpora:

```bash
curl -s http://localhost:18080/v1/textpresso/api/available_corpora
```

Check a keyword count:

```bash
curl -s -X POST http://localhost:18080/v1/textpresso/api/get_documents_count \
  -H 'Content-Type: application/json' \
  -d '{"query":"sorghum","corpora":["SorghumTest"]}'
```

### UI verification

Open:

- [http://localhost:8080/tpc/search](http://localhost:8080/tpc/search)

Then:

1. choose corpus `SorghumTest`
2. search for `sorghum`
3. inspect that results show documents
4. inspect that metadata fields are present for at least some results

### Output of this step

- confirmation that the test corpus is genuinely searchable from both API and UI

## Step 10: Ingest The Full `SorghumBase` Corpus

### What this step does

This step repeats the same process on the full Sorghum corpus.

### Why it matters

Once `SorghumTest` is stable, the full corpus can be ingested with much less risk.

### Procedure

- ensure the entire corpus is staged under `raw_files/pdf/SorghumBase`
- rerun the same incremental pipeline command from Step 7

### Validation targets after the full run

1. `SorghumBase` appears in `available_corpora`
2. keyword counts are nonzero for known terms such as `sorghum`
3. `search_documents` returns rows
4. the UI shows `SorghumBase` results

### Output of this step

- full corpus CAS output, metadata, index, and live searchability

## Step 11: Refresh Metadata

### What this step does

This step improves `.bib` sidecar metadata for the ingested PDFs.

### Why it matters

Without this step, search results can show placeholder or incomplete values for:

- author
- journal
- year
- title

### Metadata sources used by the improved workflow

In priority order:

1. mounted CSV metadata
2. `pdfinfo` document metadata
3. PDF first-page text heuristics
4. placeholder fallback values only if all of the above fail

### Architectural relation

The API and Wt UI can read sidecar `.bib` files when indexed metadata is missing or unsafe to decompress. That makes
the `.bib` refresh step directly relevant to what users see in search results.

### Output of this step

- rewritten `.bib` sidecars with cleaner title, journal, year, and author values

## Step 12: Final Verification

### What this step does

This step confirms that the live user-facing system is healthy.

### Procedure

API checks:

```bash
curl -s http://localhost:18080/v1/textpresso/api/available_corpora
```

```bash
curl -s -X POST http://localhost:18080/v1/textpresso/api/get_documents_count \
  -H 'Content-Type: application/json' \
  -d '{"query":"sorghum","corpora":["SorghumBase"]}'
```

UI checks:

1. open [http://localhost:8080/tpc/search](http://localhost:8080/tpc/search)
2. hard refresh if the page was open before the latest restart
3. choose `SorghumBase`
4. search `sorghum`
5. inspect document rows and metadata fields

### Output of this step

- a confirmed working local Textpresso search system for SorghumBase

## Problems Encountered And Why The Fixes Matter

### 1. CAS2 generation failed

Symptoms:

- no usable `tpcas-2` output for new PDF corpora
- logs showed missing `pcrelations` and `tpontology`
- `runAECpp` output was not copied correctly

Why this matters architecturally:

- CAS2 is the annotated stage used before indexing
- without usable CAS2 outputs, the corpus cannot progress to stable search artifacts

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

Why this matters architecturally:

- indexing depends on document metadata and CAS2-side outputs
- if `.bib` generation fails, the searchable layer can silently miss documents even when earlier stages succeeded

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

Why this matters architecturally:

- a write-then-crash pattern can make the pipeline appear flaky and can hide real partial-output conditions

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

Why this matters architecturally:

- the system could ingest and index correctly but still fail at the final user-facing retrieval layer

Fix:

- stop requesting dangerous compressed metadata fields in the API search route
- use safe fields for search hits
- read title/journal/type/year/author from `.bib` sidecars instead

Relevant file:

- `textpressoapi/main.cpp`

### 5. The Wt UI crashed separately from the API

Symptoms:

- API behavior improved, but `/tpc/search` still failed on result rendering

Why this matters architecturally:

- Textpresso has two presentation/search surfaces
- fixing one path is not sufficient if the second binary still exercises the old crash path

Fix:

- patch the Wt search UI to avoid the same dangerous detailed-field retrieval path
- add `.bib` sidecar fallback for the UI result display

Relevant file:

- `textpressocentral/TpC/Search.cpp`

## Reproducibility Checklist

Before calling the setup complete, verify all of the following:

- the `Textpresso` repo is patched or otherwise contains the required Sorghum fixes
- Docker Compose services are running
- the mounted data directory is persistent and correctly configured
- Sorghum PDFs are staged under `raw_files/pdf/SorghumBase`
- Sorghum metadata CSV is staged under `imports/metadata/sorghumbase_papers.csv`
- `SorghumTest` ingests successfully
- the smoke test passes
- `SorghumBase` appears in `available_corpora`
- a query like `sorghum` returns nonzero counts for `SorghumBase`
- the UI shows search rows and metadata

## Related Documents

- [TEXTPRESSO_SORGHUM_FLOWCHART.md](/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_FLOWCHART.md)
- [TEXTPRESSO_PATCHSET_GUIDE.md](/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/sorghumbase_textpresso_implementation/docs/TEXTPRESSO_PATCHSET_GUIDE.md)
- [COLLABORATOR_HANDOFF.md](/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/sorghumbase_textpresso_implementation/docs/COLLABORATOR_HANDOFF.md)
