# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This repo serves two purposes:

1. **`textpresso_classifiers/`** — a reusable Python library for training and applying scikit-learn document classifiers to scientific papers (PDFs, Textpresso CAS files, plain text).
2. **`sorghumbase_textpresso_implementation/`** — project materials for integrating SorghumBase literature into the Textpresso search and curation system, using the classifier library above.

The two halves are largely independent. The classifier library can be used without any Sorghum context; the Sorghum project uses the classifier as one step in a larger pipeline.

## Commands

### Install
```bash
pip install -r requirements.txt
python setup.py install   # or: pip install -e .
```

NLTK data is required at runtime:
```python
import nltk; nltk.download('wordnet')
```

### Run tests
```bash
python -m pytest tests/
# or single test:
python -m pytest tests/test_classifiers.py::TestTextpressoDocumentClassifier::test_train_classifier
```

### Docker (classifier only)
```bash
docker build -t textpresso_classifiers .
docker run textpresso_classifiers          # runs unit tests by default
```

### Train a classifier
```bash
python bin/tp_doc_classifier.py \
  -t /path/to/training_dir \   # must contain positive/ and negative/ subdirs
  -T \                          # hold out 20% for testing
  -c classifier.pkl \           # save trained model
  -f cas_pdf \                  # file type: pdf | cas_pdf | cas_xml | txt
  -m SVM_LINEAR                 # model type (default)
```

### Apply a saved classifier
```bash
python bin/tp_doc_classifier.py \
  -c classifier.pkl \
  -p /path/to/new_docs \
  -f cas_pdf
```

### Compare classifiers
```bash
python bin/classifiers_comparison.py positive_dir/ negative_dir/ -f cas_pdf
```

### Fetch SorghumBase paper metadata
```bash
python sorghumbase_textpresso_implementation/scripts/fetch_sorghumbase_papers.py
# Output: sorghumbase_textpresso_implementation/metadata/sorghumbase_papers.csv
```

## Architecture

### Classifier library (`textpresso_classifiers/`)

**`classifiers.py`** is the core. `TextpressoDocumentClassifier` wraps scikit-learn and handles the full ML lifecycle:

1. **Load data** — `add_classified_docs_to_dataset()` recursively reads PDFs or CAS files and assigns binary labels (positive/negative).
2. **Split** — `generate_training_and_test_sets()` (80/20 default).
3. **Featurize** — `extract_features()` builds a TF-IDF or BOW matrix; optionally lemmatizes, uses n-grams, and selects top-N features via chi-squared.
4. **Train** — `train_classifier()` fits any supported sklearn estimator.
5. **Evaluate** — `test_classifier()` returns precision/recall/accuracy.
6. **Predict** — `predict_file()` or `predict_files()` for inference on new documents.
7. **Persist** — `save_to_file()` / `load_from_file()` via pickle.

**`fileutils.py`** handles text extraction from the three supported formats:
- **PDF** via PyPDF2
- **CAS** (Textpresso's gzip-compressed XML container, either PDF-backed or XML-backed)
- **Plain text**

CAS files are the native format used inside Textpresso. When integrating with Textpresso, classifiers operate on CAS files rather than raw PDFs.

### Sorghum project (`sorghumbase_textpresso_implementation/`)

This is a workflow project, not a library. The overall pipeline is:

```
SorghumBase WordPress API
        ↓
  fetch metadata (CSV)
        ↓
  harvest PDFs (manual / Unpaywall / interlibrary)
        ↓
  extract text (convert_doc_to_txt.py or pdf_to_text_and_labels.py)
        ↓
  manually label a training set (labels.tsv)
        ↓
  train classifier (tp_doc_classifier.py)
        ↓
  predict on new papers (tp_doc_classifier.py -p)
        ↓
  Textpresso ingest pipeline (in agr_textpresso repo)
```

The `scripts/` directory contains `fetch_sorghumbase_papers.py` (functional) and several placeholder scripts (`sorghum_pdf_harvester.py`, `pdf_to_text_and_labels.py`, `train_and_eval_classifier.py`, `predict_new_pdfs.py`) that define the intended CLI signatures but are not yet implemented.

Runtime artifacts (PDFs, extracted text, trained model, labels) live in `sorghum_run/`, not in `scripts/`.

### Relationship to `agr_textpresso`

This repo is responsible for **classification** — deciding which papers are relevant and what category they belong to. The `agr_textpresso` repo handles the **Textpresso ingest pipeline**: converting PDFs to CAS1 → CAS2 → Lucene index → search API. The two repos connect at the point where classified paper lists are fed into the Textpresso ingest.

Operational detail for the full Textpresso integration lives in:
- `sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_RUNBOOK.md` — step-by-step operational guide
- `sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_FLOWCHART.md` — Mermaid workflow diagram
- `sorghumbase_textpresso_implementation/docs/TEXTPRESSO_PATCHSET_GUIDE.md` — patches needed to make Textpresso work (ARM Docker, CAS2 pipeline, PDF bib fallback)

## Cross-repo integration with `agr_textpresso`

### Division of responsibility

| Concern | Repo |
|---------|------|
| Fetch paper metadata from SorghumBase | **this repo** (`fetch_sorghumbase_papers.py`) |
| Harvest PDFs | **this repo** (`sorghum_pdf_harvester.py`, not yet implemented) |
| Classify papers (relevant? what category?) | **this repo** (`tp_doc_classifier.py`) |
| Download PDFs + bibs from AGR API | **agr_textpresso** (`download_pdfs_bib_files.py`) |
| PDF → CAS-1 (tokenize) | **agr_textpresso** (`articles2cas` binary, symlinked as `tokenize`) |
| CAS-1 → CAS-2 (annotate with ontologies) | **agr_textpresso** (`runAECpp` binary, symlinked as `annotate`) |
| CAS-2 → Lucene index | **agr_textpresso** (`indexmerger` binary, symlinked as `index`) |
| Search API + Web UI | **agr_textpresso** (`textpressoapi`, `textpressocentral`) |

### Handoff point

The handoff between the two repos is **file-based, not API-based**. This repo produces a set of PDFs (and optionally a classification label per paper); those files are placed in the directory structure that `agr_textpresso` expects before its ingest pipeline runs:

```
/data/textpresso/raw_files/pdf/{organism}/   ← PDFs land here
/data/textpresso/raw_files/bib/{organism}/   ← .bib files land here
```

For a new MOD like SorghumBase (not yet in AGR), PDFs must be placed there manually or by a custom harvester. For existing AGR MODs, `agr_textpresso/tpctools/getPdfBiblio/download_pdfs_bib_files.py` populates this directory automatically on each weekly run.

### What `agr_textpresso` needs from classifiers (for existing MODs)

For MODs already in the AGR curation system, `download_pdfs_bib_files.py` calls the AGR reference API to get the paper list — the classifier output is not directly consumed by that script. Classification happens upstream in the curation workflow (curators mark papers as relevant in the AGR system before they appear in the reference list).

For a **new MOD like SorghumBase**, the classification output from this repo substitutes for that curation step: the predicted-positive papers are the ones whose PDFs get staged for ingest.

### CAS file format (shared between repos)

Both repos work with **CAS files** (Textpresso's gzip-compressed UIMA XMI containers). This repo's `fileutils.py` can read them for classifier training (`cas_pdf` and `cas_xml` file types). `agr_textpresso` produces them as pipeline intermediates. If you need to train a classifier on already-ingested Textpresso content, point `tp_doc_classifier.py -f cas_pdf` at `/data/textpresso/tpcas-2/`.

### Ontology categories

`agr_textpresso` maintains OBO category files in `/data/textpresso/obofiles4production/`, refreshed monthly. These are the category labels that appear in Textpresso search facets. The `label` values in this repo's `sorghum_run/labels.tsv` (`MOLECULAR_GENETICS`, `FIELD_PHYSIOLOGY`, etc.) should align with — or be mapped to — these OBO categories when the classifier output is used to drive Textpresso annotations.

### Running the combined workflow

1. **Classify** (this repo): train and run `tp_doc_classifier.py` to produce a list of relevant papers with category labels.
2. **Stage PDFs** (this repo → `agr_textpresso`): copy PDFs for predicted-positive papers into `/data/textpresso/raw_files/pdf/{organism}/`.
3. **Ingest** (`agr_textpresso`): run `incremental_build.sh` (or `tokenize` → `annotate` → `index` manually) inside the `agr_textpresso` container.
4. **Verify** (`agr_textpresso`): query the Lucene index via the web UI or REST API to confirm papers appear and are annotated correctly.

See `sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_RUNBOOK.md` for the full step-by-step operational guide including Docker setup, patch application, and smoke testing.

### Training data conventions

- Training directories must have `positive/` and `negative/` subdirectories.
- Labels for the Sorghum project are tracked in `sorghum_run/labels.tsv` (columns: `doc_id`, `label`, `explanation`).
- Current label categories in use: `MOLECULAR_GENETICS`, `FIELD_PHYSIOLOGY`.

### Legacy materials

`wormbase_tools/` contains the original shell scripts from the WormBase C. elegans project that this codebase grew out of. They are not used for Sorghum work.
