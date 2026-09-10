# Running Textpresso Annotation Locally

## Created 2026-09-09 · Rewritten 2026-09-10 after a full end-to-end run (MaizeOA, 1,438 papers) on a Windows laptop

Two parts:

- **[Part 1 — One-time local setup](#part-1--one-time-local-setup)** — build a local
  Textpresso instance in WSL2 whose ontology tables match the server's.
- **[Part 2 — Processing a batch of papers](#part-2--processing-a-batch-of-papers)** — turn a
  folder of PDFs into annotated CAS-2 files and hand them to the server operator.

---

## What this is for

The shared Textpresso search index lives on a server run by a collaborator. You
have a batch of new PDFs to get into it. Rather than send raw PDFs and wait for
the operator to run the whole pipeline, you run the **front half** locally:

```
stage PDFs → tokenize (CAS-1) → annotate (CAS-2) → .bib sidecars → package .tgz
```

and send the operator a tarball they merge and index. You do **not** build the
Lucene index, and you do **not** need the web UI, `textpressoapi`, any other
MOD's data, or AGR / Cognito credentials.

`scripts/process_batch.sh` in this repo automates all of Part 2. Part 1 is the
setup that has to happen once.

Related docs:

- [`TEXTPRESSO_ADD_NEW_CORPUS_GUIDE.md`](TEXTPRESSO_ADD_NEW_CORPUS_GUIDE.md) — the
  full server-side ingest guide. `process_batch.sh` runs its Steps 1–7; the
  operator runs Steps 8–10. The "If a PDF produces zero sentences" section there
  is the reference for PDF-parsing fixups.
- [`../Laura_work_updates_log.md`](../Laura_work_updates_log.md) — running work log.

---

## The one hard constraint: ontology parity

A CAS-2 file has its ontology annotations **baked in** as category strings:

```xml
<textpresso:lexicalannotation term="a1" category="Z. mays gene (tpzm:0000000)" .../>
```

The server's indexer reads those strings directly. For them to line up with the
server's category tree and search facets, **your local instance must annotate
using the same OBO ontology files the server currently has loaded.**

That is the entire dependency. Everything in Part 1 exists to make your local
`ontologymembers` list and per-ontology lexica identical to the server's.

- `process_batch.sh -O <list>` checks this before every batch and aborts on a
  mismatch. Get `<list>` from Part 1, step 6.
- When the server runs its **monthly ontology update** (first Tuesday), your
  local OBO files are stale. Re-sync them and rebuild the lexica (see
  [Maintenance](#maintenance)). Batches annotated against the old ontology may
  need re-annotation server-side.

---

# Part 1 — One-time local setup

Reference environment for this walkthrough: Windows 11, 32 GB RAM, Intel Ultra 7
165H (16 cores / 22 threads). Adjust the memory/CPU numbers below to your machine.

Everything runs **inside WSL2**, never from Git Bash, PowerShell, or a
`/mnt/c/...` path. Two things break otherwise:

- CAS-2 `images/` entries are **symlinks** — mangled on NTFS/DrvFs.
- The pipeline shell scripts need executable bits and LF line endings.

### 1. WSL2 + Docker Desktop

```powershell
wsl --install -d Ubuntu       # then reboot
```

Install Docker Desktop → **Settings → Resources → WSL Integration** → enable your
Ubuntu distro.

Modern WSL2 runs Ubuntu **and** the Docker containers in one shared VM, so the
`.wslconfig` `memory` value is the ceiling for both together. `annotate`
(`runAECpp`) loads ~1.1 M ontology rows and is the memory bottleneck. Create
`C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
memory=20GB      # 32 GB machine: leaves ~12 GB for Windows
processors=12    # of 22 logical cores
swap=8GB
```

On a 16 GB machine use `memory=10GB`, `processors=6`, and keep `-P 1` when
processing.

```powershell
wsl --shutdown          # then reopen the Ubuntu terminal
wsl -d Ubuntu -- free -g   # confirm the new total
```

### 2. Authenticate git, then clone (into the Linux filesystem)

`agr_textpresso` is private. The simplest auth on Windows is to reuse the
Windows Git Credential Manager from WSL — no `gh`, no SSH key:

```bash
git config --global core.autocrlf false   # the .sh scripts need LF endings
git config --global credential.helper "/mnt/c/Program\ Files/Git/mingw64/bin/git-credential-manager.exe"
```

(If Git for Windows is elsewhere, adjust the path; or run `gh auth login`, or add
an SSH key and use `git@github.com:` URLs.)

**Clone the agr_textpresso directory as `agr_textpresso`** — Docker Compose
derives the project name from it (normalizing `_` → `-`), giving the container
name `agr-textpresso-textpresso-1` that everything else assumes.

```bash
cd ~
git clone --branch maizegdb/maize-textpresso-fixes https://github.com/LTibbs/agr_textpresso.git
git clone https://github.com/LTibbs/maizegdb_textpresso_implementation.git
```

> The Windows checkout of `maizegdb_textpresso_implementation` was likely cloned
> with `core.autocrlf=true`, so its `process_batch.sh` has CRLF endings and fails
> from WSL with `bash\r: No such file or directory`. Always run the WSL clone.

### 3. Build the two images from source

You're on amd64 (same as the server); the ARM fixes in this branch are inert.

```bash
cd ~/agr_textpresso
docker build -t ubuntu-tpc ./libtpc     # base: UIMA / Wt / poppler / podofo. ~35-45 min, needs stable internet.
docker compose build                    # app image on top. ~10-15 min.
```

If the base build OOMs, lower the hardcoded `make -j8` to `-j4` in
`libtpc/Dockerfile` and rerun.

### 4. Configure `.env` and the data directory

```bash
cd ~/agr_textpresso
cp .env_example .env
sed -i 's|^TEXTPRESSO_DATA_DIR=.*|TEXTPRESSO_DATA_DIR='"$HOME"'/agr_textpresso/.data|' .env
sed -i 's|^TPC_UI_PORT=.*|TPC_UI_PORT=8080|; s|^TPC_API_PORT=.*|TPC_API_PORT=18080|' .env
touch sasl_passwd
mkdir -p .data/obofiles4production .data/postgres .data/imports/metadata .data/raw_files/pdf .data/handoff
```

Leave the `COGNITO_*` / `PERSISTENT_STORE_DB_*` variables blank — Compose warns
about them; they're only used by the paper-download and email steps, which you
skip.

**OBO files:** the `maizegdb/maize-textpresso-fixes` branch already ships them at
`.data/obofiles4production/` (`go.obo po.obo to.obo zmays_genes_20260813.obo`).
Confirm they're there:

```bash
ls -la ~/agr_textpresso/.data/obofiles4production/
```

If the branch ever drops them, ask the operator for a copy of everything in the
server container's `/data/textpresso/obofiles4production/`.

### 5. Start the container

```bash
docker compose up -d
docker compose logs -f textpresso     # watch first boot
```

First boot builds the lexica from the OBO files (`CreateLexica.bash` → `tpso`,
~15 min for `go.obo`). It's finished when the only Textpresso process left is
`initialize.sh -i` ("stay idle" — that's the steady state, not a hang):

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "pgrep -af 'CreateLexica|tpso ' || echo 'lexica build done'"
```

### 6. Verify ontology parity

```bash
C=agr-textpresso-textpresso-1

# The list — must match the server. Save it for `process_batch.sh -O`.
docker exec $C psql -At -d www-data -c "select list from ontologymembers order by list;"
#   go
#   po
#   to
#   zmays_genes_20260813

# The per-ontology lexica. The merged `tpontology` table does NOT persist on a
# laptop (annotate builds and drops it each run), so sum these instead:
docker exec $C psql -d www-data -c "do \$\$
declare t text; n bigint; tot bigint := 0;
begin
  for t in select tablename from pg_tables where tablename like 'tpontology\_%' order by 1 loop
    execute format('select count(*) from %I', t) into n;
    raise notice '% : %', rpad(t,34), n; tot := tot + n;
  end loop;
  raise notice 'TOTAL : %', tot;
end \$\$;"
#   tpontology_go_0                   : 872657
#   tpontology_po_0                   : 30363
#   tpontology_to_0                   : 10054
#   tpontology_zmays_genes_20260813_0 : 185762
#   TOTAL : 1098836        → matches the server's `select count(*) from tpontology`
```

Get the server's numbers from the operator:

```bash
# operator runs, against the server container:
docker exec <server-container> psql -At -d www-data \
  -c "select string_agg(list, ',' order by list) from ontologymembers;"
```

If the lists don't match, fix the OBO files in `.data/obofiles4production/` and
rebuild before processing anything:

```bash
docker exec $C bash -lc "CreateLexica.bash"
```

> `curl localhost:18080/.../available_corpora` returning **HTTP 500**
> (`No such file or directory: /usr/local/textpresso/tpcas/`) is expected on a
> fresh instance — nothing is indexed yet. The annotation pipeline never touches
> the API.

### 7. Deploy `generate_pdf_bib.py` into the container

Tracked in the `agr_textpresso` repo but not baked into the image; **re-copy it
after any image rebuild**:

```bash
C=agr-textpresso-textpresso-1
docker cp ~/agr_textpresso/Users/kchougul/development/codex_projects/Textpresso/tpctools/generate_pdf_bib.py \
  $C:/usr/local/bin/generate_pdf_bib.py
docker exec $C chmod +x /usr/local/bin/generate_pdf_bib.py
docker exec $C python3 /usr/local/bin/generate_pdf_bib.py --help   # sanity check
```

Setup is done. State persists across `docker compose down`/`up` (it all lives in
`.data/` and the image).

---

# Part 2 — Processing a batch of papers

### Prepare the inputs

**1. PDFs** — one file per paper, named `<accession>.pdf`, where the accession is
the **DOI with every `/` replaced by `_`**
(`10.1007/s00122-002-0966-5` → `10.1007_s00122-002-0966-5.pdf`). A flat folder is
fine; `process_batch.sh -s` stages them into the required
`raw_files/pdf/<Corpus>/<acc>/<acc>.pdf` layout. Parentheses and `+` in the
accession are OK (they occur in real DOIs).

**2. Metadata CSV** — copy into `~/agr_textpresso/.data/imports/metadata/`. Header
exactly:

```
doi,pubmed_id,title,abstract,authors,journal,year
```

The `doi` column may use either `/` or `_` — `generate_pdf_bib.py`'s
`normalize_accession()` matches both. Every PDF should have a row; a PDF with no
row gets a placeholder `.bib`, and **a placeholder/missing `.bib` makes the
indexer silently drop that paper**. `process_batch.sh` reports which staged
accessions have no metadata row so you can fix the CSV first. Rows with no
matching PDF are simply ignored.

Keep only the CSV(s) you want shipped in that directory — the handoff tarball
includes `imports/metadata/*.csv`.

### Run

From `~/maizegdb_textpresso_implementation`:

```bash
scripts/process_batch.sh \
  -c MaizeOA \
  -s /mnt/c/path/to/flat/pdf/folder \
  -O go,po,to,zmays_genes_20260813 \
  -P 2
```

| flag | meaning |
|---|---|
| `-c` | corpus name (directory name everywhere; the `-c` value for searching) |
| `-s` | folder of flat `<accession>.pdf` files to stage — omit if already staged under `.data/raw_files/pdf/<Corpus>/` |
| `-O` | the server's `ontologymembers` list — parity guard, aborts on mismatch |
| `-P` | tokenize/annotate workers. **2** is a good laptop default; `annotate` is the memory user. 16 GB machine: `1`. |
| `-t` | tokenizer mode: `4` (default; enables `--type abstract` etc. section search) or `1` |
| `-C` | container name — only if auto-detection fails (normally `agr-textpresso-textpresso-1`) |
| `-n` | dry run — print the plan and stop |
| `-k` | keep the scoped annotate staging tree under `tmp/` for debugging |

What it does, in order: preflight (container, ontology parity, stale locks, bib
helper) → stage + `/`-in-name guard + metadata cross-check → `pdf2txtimg`
(synchronous — the packaged wrapper backgrounds it with no `wait`) → `articles2cas
-t4` → verify every CAS-1 has a non-zero sentence count → build a symlink tree
containing only this corpus and run `annotate` on it → verify every CAS-2 exists →
`generate_pdf_bib.py` per accession → flag placeholder bibs → summary → package.

Re-running is safe; each stage checks its own outputs.

Rough timing (this laptop, `-P 2`): a 3-paper batch ~30 s; a 1,438-paper batch
~2–3 h, dominated by `pdf2txtimg` and `annotate`.

### Output

```
~/agr_textpresso/.data/handoff/<corpus>-<UTC-timestamp>.tgz            # raw + CAS-1 + CAS-2 + metadata CSV
~/agr_textpresso/.data/handoff/<corpus>-<UTC-timestamp>.manifest.txt   # the operator's steps + accession list
```

Send both to the server operator.

### Handle zero-sentence PDFs

The run warns about accessions whose CAS-1 has 0 sentences (scanned images, or
PDFs the bundled PoDoFo 0.9.3 can't parse). They'll still index by
title/abstract but body-text search won't find them. Fix per the **"If a PDF
produces zero sentences"** section of
[`TEXTPRESSO_ADD_NEW_CORPUS_GUIDE.md`](TEXTPRESSO_ADD_NEW_CORPUS_GUIDE.md)
(Ghostscript re-save, or `pdftotext` + strip C0 controls + `articles2cas -t 3`),
then re-run `process_batch.sh` for the corpus — it reprocesses in place — and
re-package.

### First batch: prove the whole chain locally

Once per new setup, build a local index and search it, to confirm the
annotations actually resolve before involving the operator:

```bash
C=agr-textpresso-textpresso-1

# `index` does `mv /data/textpresso/db …` unconditionally; on a first-ever
# build there's nothing there yet, so pre-create it to avoid a stray error:
docker exec $C bash -lc "mkdir -p /data/textpresso/db; rm -f /data/textpresso/tmp/12index.lock"
docker exec $C bash -lc "
  export LD_LIBRARY_PATH=\$LD_LIBRARY_PATH:/usr/local/lib PATH=\$PATH:/usr/local/bin
  index -C /data/textpresso/tpcas-2 -i /data/textpresso/luceneindex
"

# Restart the API detached — `nohup … &` inside `docker exec bash -lc` gets
# killed when the exec returns; `docker exec -d` + `setsid` survives:
docker exec $C bash -lc "pkill -9 -f textpressoapi; sleep 2"
docker exec -d $C bash -lc \
  "export LD_LIBRARY_PATH=\$LD_LIBRARY_PATH:/usr/local/lib PATH=\$PATH:/usr/local/bin; \
   setsid textpressoapi -d /data/textpresso/textpressoapi_data/tokens.db \
   >> /data/textpresso/textpressoapi_data/api.log 2>&1"
sleep 8

curl -s http://localhost:18080/v1/textpresso/api/available_corpora ; echo
curl -s -X POST http://localhost:18080/v1/textpresso/api/get_documents_count \
  -H 'Content-Type: application/json' \
  -d '{"query":{"type":"document","corpora":["MaizeOA"],"keywords":"the"}}' ; echo
curl -s -X POST http://localhost:18080/v1/textpresso/api/search_documents \
  -H 'Content-Type: application/json' \
  -d '{"query":{"type":"document","corpora":["MaizeOA"],"keywords":"a specific term you expect"},"count":2}'
```

`available_corpora` should list your corpus; the `"the"` count should equal your
paper count; `search_documents` should return real titles. A `type:"abstract"`
query returning hits confirms the `-t4` section tags indexed. Skip this for
routine later batches — the operator does the real indexing.

> The local index is independent of the handoff bundle. Building it does not
> change the CAS-2 files you ship; it just lets you search them locally.

### What the operator does with the bundle

Spelled out in each `.manifest.txt`. In short, inside the server container's
mounted `/data/textpresso`:

```bash
tar xzf <corpus>-<timestamp>.tgz -C /data/textpresso
# confirm obofiles4production/ still matches the batch's ontology list
index -C /data/textpresso/tpcas-2 -i /data/textpresso/luceneindex
pkill -f textpressoapi; sleep 2
nohup textpressoapi >> /data/textpresso/textpressoapi_data/api.log 2>&1 &
# verify the corpus's document count == accession count
```

If the server's monthly ontology update ran between when you built the batch and
when they merge it, they should re-annotate server-side rather than trust the
shipped CAS-2.

### Re-processing a corpus that's already partly on the server

Fine — use the **same corpus name**. The operator's `index` rebuilds the entire
shared index from all of `tpcas-2`, so overlapping accessions are just refreshed
with your newer CAS-2. The indexer dedupes by accession **basename** across all
corpora, so never ship a `<Corpus>Test` subset alongside the real `<Corpus>` —
`process_batch.sh` + a cleanup of the test corpus before the real run avoids this.

---

## Maintenance

**After the server's monthly ontology update** (first Tuesday):

```bash
# get fresh OBO files from the operator, then:
cp /path/to/new/*.obo ~/agr_textpresso/.data/obofiles4production/
docker exec agr-textpresso-textpresso-1 bash -lc "CreateLexica.bash"
# re-verify parity (Part 1, step 6)
```

Batches you already shipped that predate the update may need re-annotation on
the server.

**After rebuilding the images** (`docker compose build`): re-run Part 1, step 7
(`generate_pdf_bib.py` is not in the image).

**Restarting:** `docker compose down` / `docker compose up -d` is safe and fast —
no lexica rebuild (they're in Postgres, which is in `.data/postgres`).

---

## Gotchas

| Symptom | Cause / fix |
|---|---|
| CAS-2 category strings don't match the server's facets | Annotated against a different OBO set. Use `-O` every run; re-sync after each monthly update. |
| `process_batch.sh` aborts: "ontology mismatch" | Local `ontologymembers` ≠ the `-O` value. Fix OBO files, `CreateLexica.bash`, re-verify. |
| `annotate` worker killed / OOM | Lower `-P`; raise `.wslconfig` `memory`; close other apps. |
| Whole corpus has 0 section tags | `pdf2txtimg` pre-step was skipped or died. `process_batch.sh` runs it synchronously — don't bypass the script with a bare `articles2cas -t4`. |
| A paper is missing from the operator's index | Its `.bib` was a placeholder (no CSV row) → silently dropped. Check the CSV `doi` column matches the accession. |
| `table "pcrelations"/"tpontology" does not exist` in the annotate log | Normal — `annotate` drops then recreates those each run. Only `relation "..." does not exist` or `pqxx::undefined_table` mid-run is the real (parallel-race) problem; `process_batch.sh` greps for those specifically. |
| `available_corpora` → HTTP 500 (`.../tpcas/` not found) | Expected until something is indexed locally. Not needed for annotation. After a local index build it returns the corpus list. |
| `available_corpora` → empty `[]` after indexing | The API wasn't actually restarted (`nohup … &` in `docker exec bash -lc` dies on exit). Use `docker exec -d $C bash -lc "setsid textpressoapi … &>> …/api.log"`. |
| `index`: `mv: cannot stat '/data/textpresso/db'` | Harmless on a first-ever local build — nothing to back up. `mkdir -p /data/textpresso/db` beforehand to silence it. |
| `get_documents_count` → 400 Bad Request | This build wants the nested query object `{"query":{"type":"document","corpora":[…],"keywords":"…"}}`, not the old flat `{"query":"…","corpora":[…]}`. |
| `bash\r: No such file or directory` | Ran the Windows/`/mnt/c` copy of a script. Use the WSL `~` clone. |
| Base image build fails mid-download | Retry; it pulls tarballs from apache.org / github. |
| Ad-hoc `docker exec bash -lc "... $acc ..."` fails on an accession with `()` | Quote it: `bash -lc '... "$0" ...' "$acc"`. `process_batch.sh` already does. |

---

## Appendix: the manual pipeline

`process_batch.sh` wraps these. Run them by hand only for debugging a single
corpus — see [`TEXTPRESSO_ADD_NEW_CORPUS_GUIDE.md`](TEXTPRESSO_ADD_NEW_CORPUS_GUIDE.md)
Steps 5–7 for the exact commands.

```
raw_files/pdf/<Corpus>/<acc>/<acc>.pdf
   │  pdf2txtimg <acc>.pdf              (synchronous; writes <acc>.NNNNN.txt + images)
   │  articles2cas -i raw_files/pdf/<Corpus> -l <acclist> -t 4 -o <Corpus> -p
   ▼
tpcas-1/<Corpus>/<acc>/<acc>.tpcas.gz
   │  build a symlink tree of <Corpus> only, then:
   │  annotate -c <tree>/cas1 -C tpcas-2 -t tmp -P <n>
   ▼
tpcas-2/<Corpus>/<acc>/<acc>.tpcas.gz   + <acc>.bib  (generate_pdf_bib.py)
   │  ── handoff boundary ──
   ▼
index -C tpcas-2 -i luceneindex        (operator only)
```
