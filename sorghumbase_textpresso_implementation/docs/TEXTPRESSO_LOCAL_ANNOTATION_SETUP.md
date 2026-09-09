# Running the Textpresso Annotation Pipeline Locally

## Created 2026-09-09

## Purpose

Set up a **local** Textpresso instance (e.g. on a laptop) that processes new
PDFs into annotated CAS-2 files, and hand those files to whoever operates the
shared server so they can merge them into the live search index.

This is the right approach when:

- you don't have shell access to the server, and
- the server operator can mount an external directory at `/data/textpresso`
  (so uploaded CAS-2 / raw / bib trees appear inside the container), and
- you're processing enough papers, often enough, that waiting on the operator
  for every batch is a bottleneck.

You run the **front half** of the pipeline only:

```
stage PDFs -> tokenize (CAS-1) -> annotate (CAS-2) -> .bib sidecars -> package
```

You do **not** build the Lucene index, and you do **not** need any other MOD's
data, the web UI, `textpressoapi`, or AGR / Cognito credentials. The server
operator runs `index` on their side after merging your files.

`scripts/process_batch.sh` in this repo automates everything from "stage PDFs"
through "package". This document is the one-time setup that has to happen
first, plus the rationale.

Related docs:

- [`TEXTPRESSO_ADD_NEW_CORPUS_GUIDE.md`](TEXTPRESSO_ADD_NEW_CORPUS_GUIDE.md) —
  the full server-side ingest guide. `process_batch.sh` runs its Steps 1–7;
  the server operator runs its Steps 8–10.
- [`../Laura_work_updates_log.md`](../Laura_work_updates_log.md) — the running
  work log; the pipeline bugs referenced below are documented there.

---

## The one hard constraint: ontology parity

A CAS-2 file carries its ontology annotations **baked in** as category
strings, e.g.:

```xml
<textpresso:lexicalannotation term="a1" category="Z. mays gene (tpzm:0000000)" .../>
```

The server's indexer reads those strings directly. For them to line up with
the server's category tree and search facets, **your local instance must
annotate using the same OBO ontology files the server currently has loaded.**

That's the entire dependency. Everything in "One-time setup" below exists to
make your local `ontologymembers` / `tpontology` tables identical to the
server's.

Consequences:

- Before each batch, confirm your local ontology set still matches the
  server's. `process_batch.sh -O <list>` enforces this and aborts on a
  mismatch.
- When the server runs its **monthly ontology update** (first Tuesday —
  `check_and_run_ontology_update.sh`), your local OBO files are now stale.
  Re-sync them and rebuild your local lexica. Batches you annotated against
  the old ontology may need re-annotation on the server (this is the same
  "stale category strings in CAS-2" problem documented in the work log).

---

## What to get from the server operator

1. **The OBO file set** — a copy of every file in the container's
   `/data/textpresso/obofiles4production/`. As of 2026-09 that is:

   ```
   go.obo   po.obo   to.obo   zmays_genes_20260813.obo      (~48 MB total)
   ```

   Ask them to also send the exact `ontologymembers` list so you can pass it
   to `process_batch.sh -O`:

   ```bash
   # server operator runs, against the running container:
   docker exec <server-container> psql -At -d www-data \
     -c "select string_agg(list, ',' order by list) from ontologymembers;"
   # e.g. -> go,po,to,zmays_genes_20260813
   ```

2. **(Optional) a PostgreSQL dump** — makes first startup fast and removes any
   doubt about parity, instead of rebuilding lexica locally:

   ```bash
   # server operator runs:
   docker exec <server-container> pg_dump -Fc www-data > www-data.tar
   pigz www-data.tar      # -> www-data.tar.gz
   ```

   You drop this at `.data/postgres/www-data.tar.gz` and `initialize.sh`
   restores it automatically on container start. **Even with the dump, place
   the OBO files too** (startup re-runs `CreateLexica.bash`, which reads the
   OBO directory).

3. **The corpus name** to use for your papers (e.g. `SorghumBase`), and
   confirmation your accessions won't collide with existing ones — the indexer
   deduplicates by accession basename across *all* corpora, so a collision
   silently drops a paper from search results system-wide.

You do **not** need the `.env` secrets. Cognito / AWS / SMTP values can stay
blank; they're only used by the paper-download and email-report steps, which
you skip.

---

## One-time setup (Windows laptop)

The C++ / UIMA build and the pipeline scripts are Linux-native. On Windows,
run **everything inside WSL2** — not Git Bash, not PowerShell, not a
`/mnt/c/...` path. Two reasons that will bite you otherwise:

- CAS-2 `images/` entries are **symlinks**; they don't survive on NTFS/DrvFs.
- The build scripts rely on executable bits and LF line endings.

### 1. WSL2 + Docker Desktop

```powershell
wsl --install -d Ubuntu       # then reboot
```

Install Docker Desktop, and in **Settings -> Resources -> WSL Integration**
enable your Ubuntu distro. Give Docker real memory — `annotate` (`runAECpp`)
loads ~1.1M ontology rows and is the memory bottleneck. Create
`C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
memory=12GB
processors=6
```

Then `wsl --shutdown` from PowerShell and reopen the Ubuntu terminal.

### 2. Clone the Textpresso repo (into the Linux filesystem)

```bash
git config --global core.autocrlf false
cd ~
git clone --branch maizegdb/maize-textpresso-fixes \
  git@github-agr-textpresso:LTibbs/agr_textpresso.git
cd agr_textpresso
```

Also clone this repo (for `process_batch.sh`):

```bash
cd ~
git clone git@github-maizegdb-textpresso:LTibbs/maizegdb_textpresso_implementation.git
```

### 3. Build the images from source

You're on amd64 (same architecture as the server), so a plain build works —
the ARM fixes in this branch are inert.

```bash
cd ~/agr_textpresso
docker build -t ubuntu-tpc ./libtpc     # base: compiles UIMA / Wt / poppler / podofo. 1-2 h. Needs stable internet.
docker compose build                    # application image on top
```

If the base build runs out of memory, lower the hardcoded `make -j8` to `-j4`
in `libtpc/Dockerfile` and rebuild.

### 4. Configure `.env` and seed the data directory

```bash
cd ~/agr_textpresso
cp .env_example .env
mkdir -p .data
sed -i 's|^TEXTPRESSO_DATA_DIR=.*|TEXTPRESSO_DATA_DIR='"$HOME"'/agr_textpresso/.data|' .env
sed -i 's|^TPC_UI_PORT=.*|TPC_UI_PORT=8080|; s|^TPC_API_PORT=.*|TPC_API_PORT=18080|' .env
touch sasl_passwd

mkdir -p .data/obofiles4production .data/postgres .data/imports/metadata

cp /path/to/received/*.obo             .data/obofiles4production/
cp /path/to/received/www-data.tar.gz   .data/postgres/       # optional
cp /path/to/your_papers.csv            .data/imports/metadata/
```

Metadata CSV header must be exactly:

```
doi,pubmed_id,title,abstract,authors,journal,year
```

`doi` must match your PDF accession (DOI with `/` -> `_`; either form is
accepted).

### 5. Start the container and verify ontology parity

```bash
docker compose up -d
docker compose logs -f textpresso     # wait for postgres load + "initialize.sh -l" (lexica build) to finish
```

```bash
C=$(docker compose ps -q textpresso)

# Must match what the server operator reported:
docker exec $C psql -At -d www-data -c "select list from ontologymembers order by list;"
docker exec $C psql -At -d www-data -c "select count(*) from tpontology;"     # expect ~1.1M
```

If `ontologymembers` doesn't match the server, stop and fix it before
processing anything — recheck the OBO files in `.data/obofiles4production/`,
then rebuild:

```bash
docker exec $C bash -lc "CreateLexica.bash"
```

### 6. Deploy `generate_pdf_bib.py` into the container

It's tracked in the `agr_textpresso` repo but not baked into the image, and it
has to be re-copied after any image rebuild:

```bash
C=$(docker compose ps -q textpresso)
docker cp Users/kchougul/development/codex_projects/Textpresso/tpctools/generate_pdf_bib.py \
  $C:/usr/local/bin/generate_pdf_bib.py
docker exec $C chmod +x /usr/local/bin/generate_pdf_bib.py
```

Setup is done. This state persists across container restarts (it all lives in
`.data/` and the image).

---

## Per-batch workflow

From `~/maizegdb_textpresso_implementation`:

```bash
scripts/process_batch.sh \
  -c SorghumBase \
  -s ~/incoming/sorghum_pdfs_2026_09 \
  -O go,po,to,zmays_genes_20260813 \
  -P 1
```

- `-c` corpus name
- `-s` a directory of flat `<accession>.pdf` files to stage (omit if you've
  already placed them at
  `.data/raw_files/pdf/<CORPUS>/<accession>/<accession>.pdf`)
- `-O` the server's `ontologymembers` list — the parity guard; the run aborts
  on mismatch
- `-P` parallel workers (keep at 1–2 on a laptop)
- `-t` tokenizer mode: `4` (default; enables section-scoped search) or `1`

The script runs: preflight (locks, parity, bib helper) -> stage -> tokenize
-> verify CAS-1 sentence counts -> annotate (scoped to this corpus only) ->
verify CAS-2 -> `.bib` sidecars -> summary -> package.

Output, under the bind-mounted data dir on your laptop:

```
.data/handoff/<corpus>-<timestamp>.tgz            # raw + CAS-1 + CAS-2 + metadata CSV
.data/handoff/<corpus>-<timestamp>.manifest.txt   # operator's merge/index/verify steps
```

Send both to the server operator.

### First batch: prove the whole chain

For your very first batch, also run a local index + search once, to confirm
annotations actually resolve end-to-end before involving the operator:

```bash
C=$(docker compose ps -q textpresso)
docker exec $C bash -lc "
  rm -rf /data/textpresso/luceneindex_new
  rm -f /data/textpresso/tmp/12index.lock
  index -C /data/textpresso/tpcas-2 -i /data/textpresso/luceneindex
  pkill -f textpressoapi; sleep 2
  nohup textpressoapi >> /data/textpresso/textpressoapi_data/api.log 2>&1 &
"
curl -s http://localhost:18080/v1/textpresso/api/available_corpora
curl -s -X POST http://localhost:18080/v1/textpresso/api/get_documents_count \
  -H 'Content-Type: application/json' \
  -d '{"query":{"keywords":"<a term you expect","type":"document","corpora":["SorghumBase"]}}'
```

Skip this for routine batches — it's slow and the operator does the real
indexing anyway.

---

## What the server operator does with the bundle

Spelled out in each bundle's `.manifest.txt`. In short:

```bash
tar xzf <corpus>-<timestamp>.tgz -C /data/textpresso     # into the mounted dir
# confirm obofiles4production/ still matches the batch's ontology list
index -C /data/textpresso/tpcas-2 -i /data/textpresso/luceneindex
pkill -f textpressoapi; sleep 2
nohup textpressoapi >> /data/textpresso/textpressoapi_data/api.log 2>&1 &
# verify document count == accession count for the corpus
```

If the server's monthly ontology update ran between when you built the batch
and when they merge it, they should re-annotate the corpus server-side rather
than trust the shipped CAS-2 (its category strings would predate the update).

---

## Gotchas

| Risk | Mitigation |
|---|---|
| CAS-2 category strings don't match the server's tree | Annotate against the server's current OBO set; use `-O` on every run; re-sync after each monthly ontology update |
| Windows filesystem drops symlinks / exec bits | Work entirely in WSL2 `~`, never `/mnt/c`; `git config core.autocrlf false` |
| `annotate` OOM on the laptop | `-P 1`; 12 GB+ to Docker via `.wslconfig` |
| `-t 4` yields zero-section CAS-1 | The script runs `pdf2txtimg` synchronously first (the packaged wrapper backgrounds it with no `wait`) — don't bypass it |
| Missing `.bib` | Paper is silently dropped at index time (not just blank metadata). The script flags placeholder/missing bibs — check the CSV `doi` column matches the accession |
| Accession basename collision with an existing server corpus | Agree the corpus name and check accessions with the operator first; never ship a `<Corpus>Test` subset alongside the real corpus |
| Some PDFs fail the bundled parser (PoDoFo 0.9.3) | The script reports zero-sentence accessions; fix per the "zero sentences" section of `TEXTPRESSO_ADD_NEW_CORPUS_GUIDE.md` (Ghostscript re-save, or `pdftotext` + control-char strip + `-t 3`) |
| Base image build fails mid-download | Retry; it pulls tarballs from apache.org / github during the build |
| Image rebuilt -> `generate_pdf_bib.py` gone | Re-run setup step 6 |

---

## Why not just send the PDFs to the operator?

You can — that's the simpler path for small, infrequent batches. Local
processing is worth the setup cost when you're running a large or ongoing
pipeline and don't want every batch to block on someone else's availability. The
tradeoff you take on is the ontology-parity maintenance above.
