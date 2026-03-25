# SorghumBase Project Folder

This directory now holds the SorghumBase-specific implementation material for this repository. Everything in this
folder is specific to the Sorghum literature workflow and the Sorghum/Textpresso integration work.

## Folder Layout

- `docs/`: Sorghum runbook, workflow diagram, collaborator handoff, patch guide, and classifier workflow notes
- `scripts/`: Sorghum harvesting, PDF conversion, classifier training, and prediction scripts
- `metadata/`: source metadata files such as `sorghumbase_papers.csv`
- `reports/`: generated classifier reports

## Start Here

- [docs/TEXTPRESSO_SORGHUM_RUNBOOK.md](docs/TEXTPRESSO_SORGHUM_RUNBOOK.md)
- [docs/TEXTPRESSO_SORGHUM_FLOWCHART.md](docs/TEXTPRESSO_SORGHUM_FLOWCHART.md)
- [docs/TEXTPRESSO_PATCHSET_GUIDE.md](docs/TEXTPRESSO_PATCHSET_GUIDE.md)
- [docs/COLLABORATOR_HANDOFF.md](docs/COLLABORATOR_HANDOFF.md)
- [docs/CLASSIFICATION_WORKFLOW.md](docs/CLASSIFICATION_WORKFLOW.md)

## Main Sorghum Assets

- metadata CSV: [metadata/sorghumbase_papers.csv](metadata/sorghumbase_papers.csv)
- classifier report: [reports/classification_report.txt](reports/classification_report.txt)
- scripts:
  - [scripts/fetch_sorghumbase_papers.py](scripts/fetch_sorghumbase_papers.py)
  - [scripts/sorghum_pdf_harvester.py](scripts/sorghum_pdf_harvester.py)
  - [scripts/pdf_to_text_and_labels.py](scripts/pdf_to_text_and_labels.py)
  - [scripts/train_and_eval_classifier.py](scripts/train_and_eval_classifier.py)
  - [scripts/predict_new_pdfs.py](scripts/predict_new_pdfs.py)
