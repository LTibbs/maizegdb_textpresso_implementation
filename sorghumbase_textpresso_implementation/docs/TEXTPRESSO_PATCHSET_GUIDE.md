# Textpresso Patch Set Guide

## Purpose

The main local `Textpresso` repository used during Sorghum integration contains unrelated in-progress changes, so it
was not safe to commit directly as a collaborator-facing branch.

Instead, this repository contains a curated patch set with only the Textpresso changes used for:

- ARM-compatible Docker/miniconda build fixes
- CAS2 pipeline repair
- fallback bib generation for PDF corpora
- smoke testing
- Lucene/index robustness fixes
- `textpressoapi` search stability fixes
- `tpc/search` UI stability fixes
- sidecar `.bib` metadata display in API and UI

Important distinction:

- this patch set targets the separate `Textpresso` repository
- the standalone Dockerfile in this repository validates only the Sorghum-side Python code and tests
- the patch set is what makes the full searchable Textpresso instance reproducible for Sorghum ingestion

## Patch File

- [patches/textpresso-sorghum-fixes.patch](patches/textpresso-sorghum-fixes.patch)

## Recommended Apply Workflow

Start from a clean checkout of the `Textpresso` repository.

```bash
cd /path/to/Textpresso
git status
```

The working tree should be clean before applying the patch.

Create a branch:

```bash
git checkout -b codex/sorghum-textpresso-fixes
```

Apply the patch:

```bash
git apply --reject --whitespace=fix \
  /path/to/sorghumbase_textpresso_implementation/sorghumbase_textpresso_implementation/docs/patches/textpresso-sorghum-fixes.patch
```

Review the result:

```bash
git status
git diff --stat
```

Commit it:

```bash
git add .
git commit -m "Apply Sorghum Textpresso integration fixes"
```

## Files Included In The Patch

Tracked file changes:

- `Dockerfile`
- `libtpc/Dockerfile_18.04`
- `libtpc/IndexManager.cpp`
- `libtpc/uima-annotators/TdTokenizer/TdTokenizer.cpp`
- `libtpc/uima-annotators/TpLexiconAnnotatorFromPg/TpLexiconAnnotatorFromPg.cpp`
- `libtpc/uima-annotators/TpLexiconAnnotatorFromPg/TpLexiconTrie.cpp`
- `libtpc/uima-annotators/TpTokenizer/TpTokenizer.cpp`
- `libtpc/uima-annotators/TxTokenizer/TxTokenizer.cpp`
- `libtpc/uima-annotators/uimaglobaldefinitions.h`
- `textpressoapi/main.cpp`
- `textpressocentral/TpC/Search.cpp`
- `tpctools/CMakeLists.txt`
- `tpctools/cas2index/create_single_index.sh`
- `tpctools/cas2index/saveidstodb.cpp`
- `tpctools/cas2index/update_corpus_counter.cpp`
- `tpctools/run_tpc_pipeline_incremental.sh`

New files included in the patch:

- `tpctools/generate_pdf_bib.py`
- `tpctools/smoke_test_pipeline.sh`

## After Applying

Use the runbook here for the operational steps:

- [TEXTPRESSO_SORGHUM_RUNBOOK.md](TEXTPRESSO_SORGHUM_RUNBOOK.md)

That document covers:

- repository roles and architecture
- Docker build and startup
- standalone Docker validation for this repo
- staging the Sorghum corpus
- test-corpus ingest
- full-corpus ingest
- metadata refresh
- verification steps

## Notes

- This is a curated patch set, not a guarantee that the target `Textpresso` repository has identical upstream history.
- If `git apply` reports conflicts, inspect the `.rej` files and merge those hunks manually.
- The patch was generated from the local work completed during the SorghumBase integration and validation effort.
