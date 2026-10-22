# Re-index MaizeOpenAccess with the 2026-10-22 build

For a machine that already runs the **older** `agr_textpresso` image (GUI/API
working) and already has the MaizeOpenAccess PDFs. This rebuilds the image,
lexicons, CAS files and index so nothing from the old setup is left mixed in.
Background is in `docs/Laura_work_updates_log.md` (2026-10-21/22 entries) and
`docs/ONTOLOGY_BUILD.md`.

What changes: eleven ontology channels from the annotation build replace raw
GO/PO/TO; tpso depth 50 (each annotation records the matched term); JATS text
for the ~1,600 papers PMC has it for; non-ASCII and Greek-letter handling;
merged supplements get their own section; the annotation server gates with
tiers and stop-forms by default.

Expect: image build ~1 h; lexicons ~30-60 min after start-up; tokenize +
annotate of 3,299 papers several hours (`-P` sets workers; annotate is
memory-hungry); index ~30 min.

## 0. Variables used below

```bash
AGR=~/agr_textpresso                         # agr_textpresso clone
IMPL=~/maizegdb_textpresso_implementation    # this repo
DATA=$(grep '^TEXTPRESSO_DATA_DIR=' $AGR/.env | cut -d= -f2)   # often $AGR/.data
C=agr-textpresso-textpresso-1                # container name (docker ps)
BUNDLE=~/textpresso_handoff_20261022         # unpacked handoff bundle
```

The bundle (`textpresso_handoff_20261022.tar.gz`) holds what git does not:

| path in bundle | what | goes to |
|---|---|---|
| `jats/MaizeOpenAccess/` | staged JATS (`<acc>/<acc>.jats.json`, `.nxml.gz`, `.suppl.txt`) | `$DATA/raw_files/xml/MaizeOpenAccess/` |
| `jats/jats_status.tsv` | per-paper JATS fetch status | keep for reference |
| `jats/MaizeOpenAccess_papers.with_text_source.csv` | metadata + `text_source` column | keep for reference; **not** into `imports/metadata/` (two CSVs there would both be read) |
| `metadata/MaizeOpenAccess_papers.csv` | the metadata CSV `.bib` generation reads | `$DATA/imports/metadata/`, only if missing there |

Everything else (OBO files, annotation build, italics index, class map,
`ontology.conf`) is in git. Gitignored files the image build still needs and
the machine already has from its first build: `$AGR/.env`, `$AGR/sasl_passwd`,
`$AGR/main.cf`.

## 1. Stop and set the old outputs aside

```bash
cd $AGR && docker compose down
# GUI backend, if it runs on this host: stop the uvicorn process (port 8010)

STAMP=pre-20261022
sudo mv $DATA/luceneindex $DATA/luceneindex.$STAMP && sudo mkdir $DATA/luceneindex
for d in tpcas-1 tpcas-2; do
  [ -d $DATA/$d/MaizeOpenAccess ] && sudo mkdir -p $DATA/$d.$STAMP \
    && sudo mv $DATA/$d/MaizeOpenAccess $DATA/$d.$STAMP/
done
ls $DATA/tpcas-1 $DATA/tpcas-2   # MaizeOA, MaizeTest, MaizeTest100 must be gone
```

`MaizeOpenAccess` supersedes `MaizeOA`, `MaizeTest` and `MaizeTest100`; their
PDFs, CAS files and metadata are removed separately. Confirm they are gone
from `tpcas-2` before step 6: `index` keeps one copy per accession across
corpora, and `MaizeOA` sorts before `MaizeOpenAccess`, so a leftover copy
would win. Delete the `.$STAMP` copies once step 7 checks out.

If `$DATA/tpctools/` exists, it is a stale copy that cron runs
(`incremental_build.sh` weekly, `update_ontology.sh` monthly). Replace it
with `$AGR/tpctools/` or remove it. `update_ontology.sh` now exits when
`*_annot.obo` files are installed; an old copy does not, and would move AGR's
`go.obo` back in and re-annotate everything. If `MOD` in `.env` is blank,
both jobs exit at once anyway.

## 2. Update both repos

```bash
cd $AGR  && git status --short && git checkout maizegdb/maize-textpresso-fixes && git pull
cd $IMPL && git status --short && git checkout master && git pull
```

`git status` must be clean first for `.data/obofiles4production/`,
`.data/annotation_build/` and `.data/italics/` (tracked). After the pull:

```bash
ls $AGR/.data/obofiles4production
# chebi_annot co_322_annot eco_annot go_annot ncbitaxon_annot pato_annot
# peco_annot po_annot pso_annot so_annot to_annot  (.obo) + zmays_genes_20260708.obo
# -- and NO go.obo / po.obo / to.obo
```

If `$DATA` is **not** `$AGR/.data`, copy those into it, removing the old raw
files (tpso reads every `.obo` in the directory, so a leftover `go.obo`
doubles GO and undoes the build):

```bash
sudo rm -f $DATA/obofiles4production/{go,po,to}.obo
sudo rsync -a $AGR/.data/obofiles4production/ $DATA/obofiles4production/
sudo rsync -a $AGR/.data/annotation_build/ $DATA/annotation_build/
sudo rsync -a $AGR/.data/italics/ $DATA/italics/
```

## 3. Stage JATS (and metadata if missing)

```bash
sudo mkdir -p $DATA/raw_files/xml
sudo rsync -a $BUNDLE/jats/MaizeOpenAccess/ $DATA/raw_files/xml/MaizeOpenAccess/
ls $DATA/imports/metadata/   # must contain exactly one CSV: MaizeOpenAccess_papers.csv
# if it does not: sudo cp $BUNDLE/metadata/MaizeOpenAccess_papers.csv $DATA/imports/metadata/
```

PDFs stay where they are: `$DATA/raw_files/pdf/MaizeOpenAccess/<acc>/<acc>.pdf`
with their per-page `.txt` files. Every paper still needs its PDF, including
the JATS ones (`.bib` metadata, supplements).

## 4. Rebuild the image and start a fresh container

```bash
cd $AGR
docker compose build textpresso          # compiles libtpc, tpso, TjTokenizer, ...
docker compose up -d --force-recreate textpresso
```

A recreated container starts with an empty Postgres. The start-up script
restores `$DATA/postgres/www-data.tar.gz` if present — that dump never
contains lexicon tables (`dump.sh` excludes `tpontology*`, `pcrelation*`,
`ontologymembers`), so the old GO/PO/TO lexicon cannot come back — and then
builds the lexicons **in the background** (`initialize.sh -l`). Then:

```bash
# bib generator is not baked into the image; redeploy after every recreate
docker cp "$AGR/Users/kchougul/development/codex_projects/Textpresso/tpctools/generate_pdf_bib.py" \
  $C:/usr/local/bin/generate_pdf_bib.py
docker exec $C chmod +x /usr/local/bin/generate_pdf_bib.py

# wait for the lexicons (tpso, then generatelexicalvariations per table)
while docker exec $C bash -c "ps -eo args | grep -E 'tpso|generatelexicalvariations' | grep -qv grep"; do sleep 60; done

docker exec $C psql -At -d www-data -c "select string_agg(list, ',' order by list) from ontologymembers"
# expect: chebi_annot,co_322_annot,eco_annot,go_annot,ncbitaxon_annot,pato_annot,
#         peco_annot,po_annot,pso_annot,so_annot,to_annot,zmays_genes_20260708
docker exec $C psql -At -d www-data -c \
  "select tablename from pg_tables where tablename ~ '^(tpontology|pcrelations)_(go|po|to)(_[0-9]+)?$'"
# expect: nothing. If anything prints (a container that was not recreated),
# drop those tables:  docker exec $C psql -d www-data -c 'drop table "<name>"'
```

Startup log checks (`$DATA/textpressoapi_data/cas_annotate.log`):
`tier gate: 188114 strings from /data/textpresso/annotation_build/...` and an
italics index of ~2,888 papers. `tier gate: off -- WARNING ... not found`
means `$DATA/annotation_build/` is missing (step 2).

## 5. Tokenize and annotate (JATS first, PDF for the rest), with `.bib`s

```bash
$IMPL/sorghumbase_textpresso_implementation/scripts/process_batch.sh \
  -c MaizeOpenAccess -C $C -P 4 -t 4 -x \
  -O chebi_annot,co_322_annot,eco_annot,go_annot,ncbitaxon_annot,pato_annot,peco_annot,po_annot,pso_annot,so_annot,to_annot,zmays_genes_20260708
```

- `-O` aborts unless the container's lexicons are exactly the new set; `-x`
  skips the handoff tarball (not needed on the server). It refuses to start
  while the lexicon build of step 4 is still running.
- It prints `CAS-1 source: N JATS, M PDF` (N ≈ 1,600) and, at the end, counts
  of CAS-1, CAS-2 and `.bib` that should all be 3,299. It is resumable:
  re-running skips papers already done.
- Known per-paper issues from the 2026-10-15 run (supplement-only PDFs,
  five papers with no text layer) are listed in the log entry of that date.

## 6. Index and restart the API

Anything still in `$DATA/tpcas-2` other than `MaizeOpenAccess` (e.g. `GDR`,
`SorghumBase`, `PMCOA`) is indexed too, with **old-lexicon** annotations, so
its categories do not match the new ones. Re-run step 5 for it (with its own
`-c`), or move it out like step 1.

```bash
docker exec $C bash -lc 'rm -f /data/textpresso/tmp/12index.lock; index -C /data/textpresso/tpcas-2 -i /data/textpresso/luceneindex'
docker exec $C bash -lc 'pkill -f textpressoapi; sleep 2; nohup textpressoapi -d /data/textpresso/textpressoapi_data/tokens.db >> /data/textpresso/textpressoapi_data/api.log 2>&1 &'
docker exec $C bash -lc 'pkill -f cas_annotate_server.py; sleep 1; nohup python3 /usr/local/textpresso/cas_annotate/cas_annotate_server.py >> /data/textpresso/textpressoapi_data/cas_annotate.log 2>&1 &'
```

Textpresso Central's web UI only offers corpora listed in
`literaturepermissions` (restored from the dump, so it still names the old
ones):

```bash
docker exec $C psql -d www-data -c "update literaturepermissions set preference = 'MaizeOpenAccess' where userid = 'default'"
# add any other corpora kept in tpcas-2, '|'-separated, e.g. 'GDR|MaizeOpenAccess|SorghumBase'
docker exec $C /root/initialize.sh -d  # dump, so the next container start restores it
```

## 7. Verify

```bash
cd $IMPL
TP=http://localhost:$(grep '^TPC_UI_PORT=' $AGR/.env | cut -d= -f2)/v1/textpresso
python3 bin/tpc_search_combined.py --url $TP/api --list-corpora       # MaizeOpenAccess
python3 bin/tpc_search_combined.py --url $TP/api -c MaizeOpenAccess --type document "anthocyanin" --count 3
# chromosome is its own category now (was "intracellular membraneless organelle")
curl -s -G $TP/category_search --data-urlencode q=chromosome | jq '.matches[0]'
# hygiene report: expect reasons section, tier_* (incl. tier_TD_*) and stop_form
curl -s -G $TP/annotate \
  --data-urlencode "identifier=MaizeOpenAccess/10.1093_jxb_erad297/10.1093_jxb_erad297.tpcas" \
  | jq '.hygiene'
```

The document count for MaizeOpenAccess should be 3,299, less the five
papers with no text layer if they are dropped at index time.

## 8. GUI backend (if it runs on this host)

```bash
cd $IMPL && pip install -r textpresso_gui/backend/requirements.txt
export TEXTPRESSO_CAS_ROOT=$DATA/tpcas-2
export TEXTPRESSO_MATCH_DICTIONARY=$DATA/annotation_build/match_dictionary.csv.gz
export TEXTPRESSO_ITALICS_INDEX=$DATA/italics/MaizeOpenAccess.json
export TEXTPRESSO_OBO_CLASS_MAP=$DATA/italics/obo_short_form_classes.json
uvicorn textpresso_gui.backend.main:app --port 8010
```

The `/data/textpresso/...` defaults only exist inside the container, so the
host needs these four set. Without `TEXTPRESSO_CAS_ROOT` the GUI falls back
to the API over HTTP, which is fine but slower.

## Rolling back

`$DATA/*.pre-20261022` hold the old CAS and index. Old OBOs:
`.data/backups/obo-raw-20261022/`; old annotation build:
`.data/backups/annotation_build-20261020/`. Code: check out the commits
before `53ea5c3` (agr_textpresso) and `e0b6ff0` (this repo) and rebuild.
