# SorghumBase Textpresso Implementation

This repository contains the SorghumBase-specific utilities and classifier workflows used alongside a local
Textpresso deployment. It includes:

- PDF harvesting and metadata files for Sorghum literature
- scripts for PDF-to-text/classification workflows
- a reproducible runbook for loading the Sorghum corpus into the Dockerized Textpresso stack

## What To Read First

- [docs/TEXTPRESSO_SORGHUM_RUNBOOK.md](docs/TEXTPRESSO_SORGHUM_RUNBOOK.md): end-to-end Docker setup, corpus staging,
  ingest, metadata refresh, and troubleshooting
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

## Notes

- This repository is intended to be used together with a local clone of the Textpresso Docker stack.
- Large runtime outputs such as harvested PDFs should stay out of git.
