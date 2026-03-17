# Textpresso Sorghum Workflow Diagram

This diagram summarizes the SorghumBase ingestion and search-enablement workflow described in the runbook.

```mermaid
flowchart TD
    A["Clone Repositories
    Textpresso
    SorghumBase implementation"] --> B["Configure Environment
    .env
    TEXTPRESSO_DATA_DIR
    TPC_UI_PORT / TPC_API_PORT"]

    B --> C["Build and Start Docker Stack
    docker compose build
    docker compose up -d"]

    C --> D["Stage Inputs Into Data Mount
    PDFs -> raw_files/pdf/SorghumBase
    metadata CSV -> imports/metadata"]

    D --> E["Create Small Test Corpus
    SorghumTest"]
    E --> F["Run Incremental Pipeline
    CAS1
    CAS2
    bib
    index"]

    F --> G{"Smoke Test Passes?"}
    G -- "No" --> H["Debug Pipeline
    CAS2 temp tables
    LD_LIBRARY_PATH
    bib fallback
    index/db rebuild"]
    H --> F

    G -- "Yes" --> I["Stage Full Corpus
    SorghumBase"]
    I --> J["Run Full Incremental Pipeline"]

    J --> K["Generate / Refresh bib Metadata
    CSV metadata
    pdfinfo fallback
    PDF first-page fallback"]

    K --> L["Rebuild Search Surfaces
    Lucene index
    db
    textpressoapi restart"]

    L --> M{"Search Stable?"}
    M -- "No" --> N["Apply Runtime Fixes
    runAECpp segfault fix
    API compressed-field workaround
    Wt UI search fix
    sidecar bib metadata fallback"]
    N --> L

    M -- "Yes" --> O["Verify API
    available_corpora
    get_documents_count
    search_documents"]
    O --> P["Verify UI
    /tpc/search
    SorghumBase
    keyword: sorghum"]

    P --> Q{"Metadata Clean?"}
    Q -- "No" --> R["Improve Metadata Extraction
    prefer CSV
    prefer pdfinfo
    tighten author heuristics
    refresh all .bib files"]
    R --> O

    Q -- "Yes" --> S["Publish Deliverables
    runbook
    curated patch set
    collaborator handoff"]
```

## Main Artifacts

- Runbook: [TEXTPRESSO_SORGHUM_RUNBOOK.md](/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/docs/TEXTPRESSO_SORGHUM_RUNBOOK.md)
- Patch guide: [TEXTPRESSO_PATCHSET_GUIDE.md](/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/docs/TEXTPRESSO_PATCHSET_GUIDE.md)
- Curated patch: [textpresso-sorghum-fixes.patch](/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/docs/patches/textpresso-sorghum-fixes.patch)
- Handoff note: [COLLABORATOR_HANDOFF.md](/Users/kchougul/development/codex_projects/Textpresso/sorghumbase_textpresso_implementation/docs/COLLABORATOR_HANDOFF.md)
