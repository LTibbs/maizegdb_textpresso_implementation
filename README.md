# SorghumBase Textpresso Implementation

This repository now has two clearly separated layers:

- reusable classifier/package code at the repository root
- SorghumBase project material under `sorghumbase_textpresso_implementation/`

The SorghumBase project area contains the Sorghum-specific scripts, metadata, reports, runbooks, and collaborator
handoff documents used alongside a local Textpresso deployment.

It includes:

- PDF harvesting and metadata files for Sorghum literature
- scripts for PDF-to-text/classification workflows
- a reproducible runbook for loading the Sorghum corpus into the Dockerized Textpresso stack

## What To Read First

- [sorghumbase_textpresso_implementation/README.md](sorghumbase_textpresso_implementation/README.md): Sorghum project
  index and folder guide
- [sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_RUNBOOK.md](sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_RUNBOOK.md): end-to-end Docker setup, corpus staging,
  ingest, metadata refresh, and troubleshooting
- [sorghumbase_textpresso_implementation/docs/TEXTPRESSO_PATCHSET_GUIDE.md](sorghumbase_textpresso_implementation/docs/TEXTPRESSO_PATCHSET_GUIDE.md): curated Textpresso patch set for applying
  the Sorghum integration fixes onto a clean Textpresso checkout
- [sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_FLOWCHART.md](sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_FLOWCHART.md): Mermaid workflow diagram for the
  SorghumBase Textpresso process
- [sorghumbase_textpresso_implementation/docs/COLLABORATOR_HANDOFF.md](sorghumbase_textpresso_implementation/docs/COLLABORATOR_HANDOFF.md): short collaborator email/PR description text
- [sorghumbase_textpresso_implementation/docs/CLASSIFICATION_WORKFLOW.md](sorghumbase_textpresso_implementation/docs/CLASSIFICATION_WORKFLOW.md):
  classifier-specific workflow notes

## Repository Layout

- `sorghumbase_textpresso_implementation/`: SorghumBase project subtree
- `sorghumbase_textpresso_implementation/docs/`: Sorghum-specific runbooks, flowchart, patch guide, and handoff docs
- `sorghumbase_textpresso_implementation/scripts/`: Sorghum harvesting, conversion, training, and prediction scripts
- `sorghumbase_textpresso_implementation/metadata/`: Sorghum metadata files such as `sorghumbase_papers.csv`
- `sorghumbase_textpresso_implementation/reports/`: generated Sorghum reports
- `sorghum_run/`: local runtime inputs and outputs for Sorghum corpus work
- `textpresso_classifiers/`: classifier library code
- `bin/`: CLI entry points
- `tests/`: test coverage for the classifier utilities
- `docs/`: package/API documentation for the reusable classifier code

## Python Package Install

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
```

## Docker Build and Test

This repository also includes a standalone Dockerfile for the Sorghum classifier code.

Build it:

```bash
docker build -t sorghumbase-textpresso-implementation .
```

Run the default unit-test command:

```bash
docker run --rm sorghumbase-textpresso-implementation
```

Open an interactive shell instead:

```bash
docker run --rm -it sorghumbase-textpresso-implementation /bin/bash
```

## Textpresso Docker Workflow

This repository does not build the full Textpresso service by itself. For the running Sorghum search system, use the
Dockerized Textpresso stack described in:

- [sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_RUNBOOK.md](sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_RUNBOOK.md)

That document covers:

- Docker build prerequisites
- environment variables and data mount layout
- loading the Sorghum PDFs into the Textpresso data volume
- running a small test corpus
- running the full `SorghumBase` ingest
- fixing CAS2, metadata, and search-result issues

## Workflow Summary

The workflow is organized into these operational stages:

1. Prepare the Textpresso and SorghumBase repositories plus `.env`.
2. Build and start the Dockerized Textpresso stack.
3. Stage Sorghum PDFs and metadata into the mounted data directory.
4. Create and ingest a small `SorghumTest` corpus first.
5. Validate CAS, indexing, API, and UI search.
6. Ingest the full `SorghumBase` corpus.
7. Refresh `.bib` metadata from CSV, `pdfinfo`, and PDF text.
8. Re-verify API and UI search results.
9. Publish the runbook, patch set, handoff note, and workflow diagram.

Each stage’s inputs and outputs are documented in:

- [sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_FLOWCHART.md](sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_FLOWCHART.md)

Quick input/output summary:

| Stage | Input | Output |
|---|---|---|
| Docker startup | `.env`, Docker files | running UI/API services |
| Corpus staging | local PDFs, metadata CSV | `raw_files/pdf/SorghumBase`, `imports/metadata` |
| Test ingest | `SorghumTest` PDFs | test CAS/index/search artifacts |
| Full ingest | full Sorghum corpus | searchable `SorghumBase` corpus |
| Metadata refresh | CSV + PDF metadata | `.bib` sidecars with cleaner title/journal/year/author values |

## Notes

- This repository is intended to be used together with a local clone of the Textpresso Docker stack.
- Large runtime outputs such as harvested PDFs should stay out of git.
