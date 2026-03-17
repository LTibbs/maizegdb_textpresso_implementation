# SorghumBase Textpresso Implementation

This repository contains the SorghumBase-specific utilities and classifier workflows used alongside a local
Textpresso deployment. It includes:

- PDF harvesting and metadata files for Sorghum literature
- scripts for PDF-to-text/classification workflows
- a reproducible runbook for loading the Sorghum corpus into the Dockerized Textpresso stack

## What To Read First

- [docs/TEXTPRESSO_SORGHUM_RUNBOOK.md](docs/TEXTPRESSO_SORGHUM_RUNBOOK.md): end-to-end Docker setup, corpus staging,
  ingest, metadata refresh, and troubleshooting
- [docs/TEXTPRESSO_PATCHSET_GUIDE.md](docs/TEXTPRESSO_PATCHSET_GUIDE.md): curated Textpresso patch set for applying
  the Sorghum integration fixes onto a clean Textpresso checkout
- [docs/TEXTPRESSO_SORGHUM_FLOWCHART.md](docs/TEXTPRESSO_SORGHUM_FLOWCHART.md): Mermaid workflow diagram for the
  SorghumBase Textpresso process
- [docs/COLLABORATOR_HANDOFF.md](docs/COLLABORATOR_HANDOFF.md): short collaborator email/PR description text
- [sorghumbase_textpresso_implementation/CLASSIFICATION_WORKFLOW.md](sorghumbase_textpresso_implementation/CLASSIFICATION_WORKFLOW.md):
  classifier-specific workflow notes

## Repository Layout

- `sorghum_run/`: local runtime inputs and outputs for Sorghum corpus work
- `sorghumbase_textpresso_implementation/`: Python utilities and metadata files
- `textpresso_classifiers/`: classifier library code
- `bin/`: CLI entry points
- `tests/`: test coverage for the classifier utilities

## Python Package Install

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
```

## Textpresso Docker Workflow

This repository does not build the full Textpresso service by itself. For the running Sorghum search system, use the
Dockerized Textpresso stack described in:

- [docs/TEXTPRESSO_SORGHUM_RUNBOOK.md](docs/TEXTPRESSO_SORGHUM_RUNBOOK.md)

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

- [docs/TEXTPRESSO_SORGHUM_FLOWCHART.md](docs/TEXTPRESSO_SORGHUM_FLOWCHART.md)

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
