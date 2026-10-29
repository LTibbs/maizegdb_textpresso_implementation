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

## MaizeGDB Textpresso: annotation quality (hygiene, italics, tiers)

Context for the MaizeGDB Textpresso work across this repo and `agr_textpresso`.
Everything below was measured, not assumed; where a number appears, it came
from running something against the live corpus.

### Repos and where code lives

| repo | holds |
|---|---|
| `LTibbs/maizegdb_textpresso_implementation` | `bin/` CLI tools, `textpresso_classifiers/casannot.py`, `textpresso_gui/` (FastAPI backend + JS frontend), `docs/` |
| `LTibbs/agr_textpresso`, branch `maizegdb/maize-textpresso-fixes` | the Textpresso fork: `textpressoapi/` (C++ search API + Python sidecars), `libtpc/` (C++ Lucene/CAS), `.data/obofiles4production/` (the OBO files) |

Branch name contains a slash; raw fetches need `maizegdb%2Fmaize-textpresso-fixes`.

### The one architectural fact that matters

**All three consumers funnel annotation parsing through `casannot.py`:**

- `textpressoapi/cas_annotate_server.py` -> `/v1/textpresso/annotate` (public API)
- `bin/tpc_search_combined.py` -> `_load_annotations()` (CLI), module alias `_ca`
- `textpresso_gui/backend/main.py` -> imports the CLI module as `tpc` via
  importlib, calls `tpc._load_annotations()` and `tpc._ca.*`

So annotation-quality work goes in **one** place next to `casannot.py` and is
called from the two load paths. The GUI needs only a default and a toggle —
never its own filtering logic. Do not implement the same rule twice.

What this does **not** reach: `search_documents` results. Those come from the
Lucene index, which already contains the matches. Changing which documents a
`--category` query returns needs either per-hit verification (one `/annotate`
round-trip per hit, the trick `filter_gene_category_results()` already uses) or
a lexicon change plus re-index.

### Live service

- `https://textpresso.maizegdb.org/v1/textpresso/` — `api/`, `annotate/`, `category_search/`
- Corpus: **`MaizeOpenAccess`** (3,299 papers), which replaces and supersedes
  `MaizeOA`, `MaizeTest` and `MaizeTest100` — it contains every paper ingested
  in those three, and more. Use it for everything new. As of 2026-10-21 the
  live server still lists only the three old corpora; `MaizeOpenAccess` takes
  over there once it is indexed and the old three are removed.
- Accessions are DOIs with `/` replaced by `_` (`10.1093_jxb_erad297`)
- Gene annotations: `ontology=MAIZE_GENES` is **EXACT only**;
  `MAIZE_GENES_RELATED` is a separate channel. Requesting only the first
  silently measures half the system.
- `search_documents` returns at most 200 results per call, but `since_num` is an
  offset, so `tpc_search_combined.search()` pages past it (each page re-runs the
  ~6s search; pages run in parallel). `get_documents_count` gives the total. No
  date filter
- `/annotate` returns the **full** sentence list, so concatenating
  `sentences[].text` reconstructs the ingested body text

### Hard-won rules — do not relearn these

1. **Gate, do not delete.** Over 90,447 mentions: retaining the low-prior class
   and gating at confidence >= 0.4 keeps 92.4% of volume at 49.2%
   curator-confirmation and retains **99.6%** of curator-confirmed mentions;
   deleting the class keeps 88.7% / 49.7% / **96.6%**. Deletion is irreversible
   at the index level — you cannot score a mention that was never matched.

2. **Provenance sets the prior, never the verdict.** `locus_synonym` is a mixed
   field: it carries OBO-generation fragments (`promoter` on `ubi1`) *and*
   genuine curator-recorded literature aliases (`ZmVPP1` for `vpp5`, `ZmAUX1`
   for `aic1`). Every entry was curated from literature. Arguments of the form
   "it came from `locus_synonym`, so it is safe to remove" are invalid and have
   been rejected. Settle recall questions with **textual evidence**: the
   sole-evidence test (form occurs in text + gene is relevant + no retained
   surface form of that gene appears anywhere), run both against
   curator-recorded genes and against papers where the gene is *not* curated.

3. **Authors use non-unique identifiers and we cannot refuse them.** `CO` is a
   curated abbreviation for both `col11` (`ncsu6(CO)`, `CONSTANS-LIKE`,
   `ZmCCT9`) and `cct1` (`CO CO-LIKE TIMING OF CAB1 protein domain1`). `PER`
   spans 18 genes, `HCT` 12. Ambiguity gets ranked, not excluded.

4. **Check the current OBO before calling anything a mis-attribution.** A
   previous session claimed `BAM`/`amyb2`, `CO`/`col11` and `PR`/`prp16` were
   OBO errors. All three were wrong: `prp16` genuinely carries
   `"pathogenesis-related protein PR-1"`, `"ZmPR1"`, `"ZmPR-1-8"` — it *is*
   pathogenesis-related protein 16, not the PRP16 splicing factor — and the
   standalone forms are among the 214 generic synonyms `zmays_genes_20260813.obo`
   removed.

   **Production gene OBO (2026-10-20): `zmays_genes_20260708.obo`**, with the
   2026-08-07 EXACT→RELATED case-collision fix applied (byte-identical to
   commit `a131028`). The 20260813 file is kept at
   `.data/backups/obo-20261020/` in `agr_textpresso`. This deliberately restores
   the 214 removed synonyms (`aba`, `co`, `no`, `as`, `bam`, `sam`, ...) so
   they are gated by the hygiene layer instead of deleted (rule 1). The
   italics index and class map must be built against the same OBO, or those
   restored forms are not in the index and the rule abstains on them.

5. **Curator-confirmation is not precision.** Curators record genes of
   curatorial interest, not every gene validly mentioned. Between-group
   differences are trustworthy; absolute levels are not. A feature that detects
   gene-hood rather than curatorial relevance must be validated against a
   different label — local lexical cues show nothing against curator
   confirmation (69.5% vs 68.6%) but a lot against italicisation (78.7% vs 38.4%).

6. **Italics is a negative test, gene ontologies only, and abstains.** GO/PO/TO
   concept names are not italicised. A high italic rate proves nothing (`et`,
   `al` and statistical `p`/`n`/`r` are italicised). The adopted filter is the
   strict-zero rule with both exemptions — see `docs/ITALICS_RULE.md`. It abstains
   when the form does not occur in the body at all, and abstention must never be
   read as zero. Confidence scoring was evaluated and **not** adopted: it landed
   within 1.3 points of the rule's precision at the same retention, for much
   more machinery. The fitted model lives in `maize_gene_context.py`, which is
   not in either repo (it was not part of the 2026-10-20 handoff).

   The two exemptions compose and both are needed: the EXACT channel (which *is*
   the OBO synonym scope, 99.5% agreement) carries 66.2% curator confirmation
   against RELATED's 24.9%; the `registered`/`identifier` class exemption then
   catches the residue where a registered name arrives via the RELATED channel
   (the `pop1` case).

   **Reference and acknowledgment sections must be excluded when counting
   italics.** Counting cited-paper titles adds non-italic occurrences to forms
   that are italicised wherever they are genuinely used, which makes the rule
   fire on real gene mentions. `jats_italics.py` does this; an earlier build did
   not.

7. **Short registered symbols cannot be deleted** — they are real genes.
   Curator-confirmation varies by symbol, not by length: `p2` 34.0%, `c1` 54.9%,
   `vp5` 54.7% against `mn7` 100%, `pr1` 97.4%, `gt1` 96.0%. Under current
   nomenclature a registered symbol almost always contains a digit, which is a
   usable first-pass morphology filter. Match short all-caps forms
   **case-sensitively** in their original OBO capitalisation.

8. **PDF-only ingest is the upstream cause of several problems.** `pdf2tpcas`
   destroys italics, p-value exponent signs (474 affected matches in 102
   papers), `q <= 0.05` (becomes `q # 0.05`), hyphenation across line breaks
   (470 occurrences) and table structure. Every MaizeOpenAccess paper is open
   access, but PMC efetch returns the paper's own JATS `<body>` for only
   **1,623 of 3,299** (1,560 with italic markup; an earlier count of 1,627
   included 77 papers fetched under the wrong PMCID): some have no PMC record, and publishers such as Genetics, PNAS and
   Bioinformatics block full-text XML download. A JATS ingest path would fix
   all of it at once for the covered papers; the italics sidecar is a bridge,
   not the destination.

   No other bulk route closes that gap (checked 2026-10-21). Of the 1,108
   missing papers that have a PMCID, the PMC S3 bucket and Europe PMC
   `fullTextXML` return only 16. The rest are PMC "free to read" without a
   reuse licence. Elsevier (126 papers) has Crossref TDM XML links but needs an
   API key; Wiley (346) needs a TDM token. The italics gap is instead filled
   from the source PDFs by `pdf_italics.py`, which takes coverage to **2,888
   of 3,299** (1,560 JATS + 1,328 PDF). The remaining 411 are mostly 1980s-90s scanned backfiles with
   a Courier OCR layer, where no italics exist to recover.

### Shared modules (drop-in, no framework)

Place beside `casannot.py` in **both** `textpresso_classifiers/` (CLI) and
`textpressoapi/` (server):

- `annotation_hygiene.py` — `clean_annotations()` returns `(kept, dropped)` with
  a reason code per drop. Kept import-free: callers pass `section_types_at` and
  `italic_rule` in. The abbreviation case check reads original-case spellings
  from `category_index.build_index()["surface_forms"]` (records drop the case),
  fires only when every stored spelling is itself abbreviation-cased (`Ltd` vs
  `LTD` drops; `Root` vs `root` is sentence case and stays), and never applies
  to `MAIZE_GENES*` — the gene OBO stores registered symbols in lowercase, so
  `HAK5`/`TIR1` would otherwise be dropped. Gene mentions are the italics
  rule's job. `DEFAULT_STOP_FORMS` (18 homonym abbreviations such as `SSR`,
  `NLB`, `NAC`, `PCC`) is applied by default, case-sensitively and only in the
  ontologies where the sense is wrong (`TEM` stays in ECO, `tRNA` in SO); see
  `docs/ONTOLOGY_BUILD.md` for why `SOD`/`CDK`/`PFK` are not on it.
- `annotation_italics.py` — per-paper italic lookup plus `never_italicised()`,
  the adopted rule, and `load_class_map()`.
- `annotation_tiers.py` — `TierIndex.from_csv()` over `match_dictionary.csv`
  plus `tier_rule()`, the injectable gate for the four-tier match-string policy
  (T1 must match the stored case, T2 needs sentence context, TD -- the curated
  abbreviations GA/JA/SA/BR/CK/ET/ER/E3 -- needs its long form somewhere in the
  paper body, T3 never entered the dictionary, except build `BLOCKED_FORMS` such
as `End` -- the "End of Article" marker -- which keep a T3 row so the gate drops
them before a re-index). Tiers are frozen at build time, so this module has no
  third-party imports. See `docs/ONTOLOGY_BUILD.md`. A lowercase stored form
  accepts its sentence-case spelling, and a plural the matcher produced is
  judged as its singular. It never judges `MAIZE_GENES*`. **On by default**
  (2026-10-22): server and CLI load
  `/data/textpresso/annotation_build/match_dictionary.csv.gz` when it exists;
  `TEXTPRESSO_MATCH_DICTIONARY` names another file, `0` turns it off, and
  `tiers=0` skips it per request. Pass `casannot.sentence_lookup(sentences)` as
  `sentence_at`, and `casannot.body_text(sentences, sections,
  DEFAULT_DROP_SECTIONS)` as `body_text` (without it TD forms are kept and
  flagged).
- `bin/build_match_dictionary.py` — builds `match_dictionary.csv` and the eleven
  `*_annot.obo` files from the ontology collection. Build-time only; needs
  `wordfreq`. Re-run it after any collection refresh, and remember ChEBI comes
  from the **full** `chebi.obo` — the lite file in the collection has zero
  synonyms, so a build against it silently loses every chemical name. The
  shipped build uses `--derive-trait-stems`. GO's `gocheck_do_not_annotate`
  terms are matched (2026-10-22); moving the `*_annot.obo` files into
  `obofiles4production/` also means moving the raw `go.obo`/`po.obo`/`to.obo`
  out — see "Re-indexing with the annotation build" in `docs/ONTOLOGY_BUILD.md`.
- `bin/jats_italics.py` — builds the italic index from PubMed Central JATS,
  excluding references/acknowledgments. Two guards, both added 2026-10-21
  after they were found in the live index. First, esearch's `[DOI]` field also
  returns papers that *cite* the DOI, so each candidate's own
  `<article-meta>` DOI is checked; 77 of 1,625 entries had been built from
  someone else's paper. Second, a body with under 20 characters of italic
  text abstains, because some publishers deposit JATS with no italic markup
  at all and would otherwise read as "never italicised" for every form.
  `--cache-dir` keeps the esearch/efetch responses so a rebuild is offline.
- `bin/pdf_italics.py` — fills the same index from the source PDFs' fonts
  (PyMuPDF, build-time only) for accessions JATS lacks; JATS wins where both
  exist. Opaque typesetter fonts (`AdvP4041BD`, which report `ItalicAngle 0`
  even for the italic face) are calibrated per paper from conventionally
  italic words. A paper with no detectable italics is left **out** of the
  index, never written as zeros, so the rule abstains. Measured against JATS
  on the 1,500 papers both cover: 98.0% agreement on the zero/non-zero
  decision for non-exempt forms, 0.23% wrong-drop.
- Both italics builders tokenise through `jats_italics.fold_text()`, which
  mirrors the ingest's character fold (`jats_text._map_char`,
  `PdfTextNormalizer.h`): U+2010/U+2011 -> `-`, soft hyphen dropped, non-BMP
  NFKC-folded, plus ligatures and zero-width characters on the PDF path. The
  index is keyed by surface form, so if the fold and the CAS text disagree
  (`PYR1‐LIKE` split vs `pyr1-like`) the lookup misses and the rule
  abstains. **Change all three together**, then rebuild the index.
- `bin/build_class_map.py` — builds the ~98 KB surface-form provenance map, so
  the server need not parse a 10.5 MB OBO at startup. **Rebuild it with the
  italic index whenever the OBO changes**: unlisted forms resolve to `unknown`,
  which is not exempt, so a stale map makes the rule more aggressive.

NCBI allows 3 requests/second without an API key and each paper costs two
(esearch + efetch). The limiter in `jats_italics.py` exists because exceeding it
returns HTTP 429, which an earlier version swallowed in a bare `except`,
producing a silent 96% miss rate that looked exactly like "no PMC record".

Rebuild the index after a corpus refresh (`bin/` is this repo, `.data/` is
`agr_textpresso`; run from the latter with `$IMPL` pointing here):

```bash
python3 $IMPL/bin/jats_italics.py --accession-file maizeopenaccess_accessions.txt \
    --obo .data/obofiles4production/zmays_genes_20260708.obo \
    --cache-dir .data/italics/jats_cache \
    --out .data/italics/MaizeOpenAccess.jats.json  # ~65 min first run; offline with a warm cache
python3 $IMPL/bin/pdf_italics.py --accession-file maizeopenaccess_accessions.txt \
    --pdf-root /data/textpresso/raw_files/pdf/MaizeOpenAccess \
    --obo .data/obofiles4production/zmays_genes_20260708.obo \
    --jats-index .data/italics/MaizeOpenAccess.jats.json \
    --provenance-out .data/italics/MaizeOpenAccess.provenance.json \
    --out .data/italics/MaizeOpenAccess.json  # ~1.5 MB, ~2 min with 8 workers
python3 $IMPL/bin/build_class_map.py \
    --obo .data/obofiles4production/zmays_genes_20260708.obo \
    --out .data/italics/obo_short_form_classes.json   # ~98 KB
```

`--obo` pruning is what keeps the italic index at 1 MB instead of 37 MB.

Server deployment: the `agr_textpresso` Dockerfile copies an **explicit list**
of `textpressoapi/*.py` files into `/usr/local/textpresso/cas_annotate/`. Any
new module `cas_annotate_server.py` imports must be added to that `cp` line, or
the sidecar dies on import. The server reads `TEXTPRESSO_ITALICS_INDEX` /
`TEXTPRESSO_OBO_CLASS_MAP` (default `/data/textpresso/italics/...`, i.e.
`.data/italics/` on the host). The eleven-channel build outputs
(`*_annot.obo`, `match_dictionary.csv.gz`) live in `.data/annotation_build/`,
deliberately **not** in `obofiles4production/`: `tpso` globs every `.obo`
there, so moving them in is the re-index step, not a file copy.

**Restart vs re-index.** Anything read at response time (`casannot.py`
section handling, the hygiene/italics/tier modules, the italic index, the
class map, *tiers* in `match_dictionary.csv`) takes effect on a sidecar
restart. Anything that changes *which strings are matched* (the `*_annot.obo`
files, any OBO edit) reaches search and the CAS2 annotations only after the tpso
lexicon is rebuilt and the corpus re-tokenized, re-annotated and re-indexed.

**Merged supplements.** The literature pipeline appends supplementary files
behind a `SUPPLEMENTARY MATERIAL ... inserted by the MaizeGDB literature
pipeline` banner. With no heading after the bibliography, TdTokenizer labelled
them `references`, so the default references drop removed them: 1,378 of the
1,670 curator-recorded paper-gene pairs hygiene removed outright (2026-10-21).
`casannot.split_supplementary()` relabels everything from the banner onward
`supplementary`, which is **not** in the default drop set; over HTTP this needs
the server's `casannot.py` copy in sync. TdTokenizer writes the same
`supplementary` section from 2026-10-22 (`FindSupplementBanner`, same pattern
as `_SUPPLEMENT_BANNER_RE`; keep them in step), ignores headings inside the
supplement, and gives every section its full span as `content` (the string
`Tpcas2SingleIndex` indexes). `QueryType::supplementary` makes it searchable via
the API's and TPC's `type`. Until the corpus is re-tokenized and re-indexed, the
CAS2 and Lucene still say `references` for that span, and an older API build
treats an unknown `type` as `document`, so it **silently searches full text**.
`casannot.fix_sections()` judges headings on the article text only, so
CAS2s from before and after the change come out the same at read time.
Both sides judge a heading on the same evidence, the text after its line up
to 3000 chars or the next references-heading line (`following_text()` /
`FollowingText`), never on `content` or the raw segment, so they agree by
construction. Change the two together. Whole-corpus emulation of the switch
(2026-10-22): 3 of 3,299 papers change, all by keeping a real References
heading, 12-205 chars relabelled.

**Frame markers.** TdTokenizer and `jats_text.py` wrap every document in
`Beginning of Article` / `End of Article`, and the annotator matches them: TO's
RELATED synonym `End` (endosperm quality, TO:0000587) fired in every paper, and
a ±400-char synthetic sentence of bibliography let it pass T2.
`casannot.frame_marker_spans()` drops matches on the marker strings themselves
(not the frame sections, which can cover the real title) in `parse_cas_file()`
and `casdoc`, from 2026-10-29. That's read time only: the Lucene index still has
them, so a category search for `endosperm quality (TO:0000587)` returns all
3,299 MaizeOpenAccess papers until the annotator skips the markers or `End` is
taken out of the matched set, followed by a re-index.

### Rollout convention

The hygiene layer is **on by default** everywhere since the 2026-10 rollout:
the server (`TEXTPRESSO_HYGIENE_DEFAULT`, default `1`; set `0` to revert), the
CLI (`--no-hygiene` opts out; `--hygiene` is still accepted) and the GUI. It
was flipped without the planned off-by-default release because the instance
had not yet been shared publicly. Once it has, treat further changes to what
the default drops the same way: they change what published API scripts
receive, so announce them first.

Always surface the drop counts to curators ("96 shown, 66 filtered: 42
references/acknowledgments, 23 broader terms, 1 case mismatch") rather than
silently returning fewer rows, and keep the opt-out so borderline mentions stay
inspectable. For a curation database the borderline mentions are the ones a
curator needs to see. The API returns the report in its `hygiene` field; the CLI
prints it under `--annotate` and adds it to JSON output; the GUI shows it under
each annotation panel with a "Show unfiltered" toggle. Search-hit verification
(`filter_gene_category_results`) deliberately runs with hygiene **off**, so the
filter never removes a paper from search results.

### Testing against the live API

```bash
python3 bin/tpc_search_combined.py --list-corpora
python3 bin/tpc_search_combined.py -c MaizeOpenAccess --type document "anthocyanin" --count 3 --format json
curl -s -G "https://textpresso.maizegdb.org/v1/textpresso/annotate" \
  --data-urlencode "identifier=MaizeOpenAccess/10.1093_jxb_erad297/10.1093_jxb_erad297.tpcas" \
  --data-urlencode "ontology=MAIZE_GENES" | jq '.hygiene'
```
