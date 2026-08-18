# Adding a New Document Corpus to Textpresso

## Updated Aug 14 2026

## Purpose

This is a step-by-step guide for getting a new batch of PDFs (from any species) into the shared Textpresso instance so they're full-text
searchable via the API and the `/tpc/search` UI.

It reflects the actual commands that work on the live host as of 2026-08-14
(validated end-to-end while ingesting a 45-paper GDR/Rosaceae corpus on
2026-08-12, and a 119-paper GrainGenes/wheat corpus — plus a full `-t1`→`-t4`
tokenizer-mode switch for that corpus and `SorghumBase` — on 2026-08-14).
Where an existing script or wrapper turned out to be unreliable, that's
called out explicitly with the direct command that works instead.

**2026-08-14 update: the tokenizer-mode recommendation in Step 5 flipped.**
Use `-t4`, not `-t1`, for new PDF-based corpora — see Step 5 for why.

If you only need to *search* existing corpora, see
use the
`bin/tpc_search.py` / `bin/tpc_search_internal.py` command-line tools instead
— this document is about *adding* new documents.

## What You're Working With

- **The running system**: a single Docker container named
  `agr-textpresso-textpresso-1`, built from the `agr_textpresso` repo checked
  out at `/home/ec2-user/agr_textpresso`. There is no separate `Textpresso`
  repo on this host — `agr_textpresso` *is* Textpresso.
- **Persistent data**: `/home/ec2-user/agr_textpresso/.data` on the host,
  mounted at `/data/textpresso` inside the container. All commands below that
  say "inside the container" assume `docker exec agr-textpresso-textpresso-1
  bash -lc "..."`.
- **API**: `http://localhost:18080/v1/textpresso/api/`
- **UI**: `http://localhost:8080/tpc/search`

Check disk space before starting — the pipeline needs real headroom and has
no graceful recovery if it runs out mid-run:

```bash
df -h /home/ec2-user/agr_textpresso/.data
```

Rough sizing: ~10–13MB of total disk per paper (raw PDF + CAS1 + CAS2 +
index growth combined). Don't start an ingest with less than ~2-3x your
corpus's PDF size free, and watch it as you go.

## Step 0: Decide on a Corpus Name and Ontology Set

Pick a short, unique corpus name (e.g. `GDR`, `SorghumBase`) — this becomes
the directory name everywhere and the value collaborators pass to `-c` when
searching.

Decide which ontologies should annotate this corpus. GO, PO, and TO
(Gene Ontology, Plant Ontology, Trait Ontology) are generic and safe for any
plant literature. **Organism-specific gene-name ontologies (e.g. the maize
gene OBO) should only be active for corpora about that organism** — matching
is full-text substring/synonym matching against the *entire* active lexicon,
so an active maize-gene ontology will also try to match gene synonyms against
non-maize papers. This is a real problem in practice: an earlier audit found
the maize gene OBO contains many synonym entries that are actually generic
English words (`red`, `binding`, `expression`, `promoter`, etc.), so it produces false
"gene match" annotations on *any* text, not just maize papers.

If your corpus should **not** get organism-specific gene annotations, see
Step 4 before running `annotate`.

## Step 1: Prepare Your PDFs and Metadata CSV

### PDF naming

Textpresso expects one PDF per accession, laid out as:

```
raw_files/pdf/<CorpusName>/<accession>/<accession>.pdf
```

Please note that *`<accession>` is the DOI with `/` replaced by `_`
(e.g. `10.1007/s00122-002-0966-5` → `10.1007_s00122-002-0966-5`).* If your
PDFs already arrive as flat files named `<accession>.pdf`, restage them:

```bash
SRC=/path/to/flat/pdf/dropoff
DST=/home/ec2-user/agr_textpresso/.data/raw_files/pdf/<CorpusName>
mkdir -p "$DST"
for f in "$SRC"/*.pdf; do
  acc=$(basename "$f" .pdf)
  mkdir -p "$DST/$acc"
  cp "$f" "$DST/$acc/$acc.pdf"
done
```

### Metadata CSV

Metadata (title, authors, journal, year, abstract, PubMed ID) is looked up by
DOI from any `*.csv` file dropped in
`/home/ec2-user/agr_textpresso/.data/imports/metadata/`. The CSV must have
this exact header row:

```
doi,pubmed_id,title,abstract,authors,journal,year
```

`doi` must match the accession (either form — with `/` or with `_` — is
accepted; see `normalize_accession()` in `generate_pdf_bib.py`). Every PDF
you stage should have a matching row; check before ingesting:

```bash
python3 - <<'EOF'
import csv, os
dois = set()
with open('/path/to/your_papers.csv') as f:
    for row in csv.DictReader(f):
        dois.add(row['doi'].strip())
pdf_dir = '/home/ec2-user/agr_textpresso/.data/raw_files/pdf/<CorpusName>'
pdfs = set(os.listdir(pdf_dir))
print("PDFs without a CSV row:", pdfs - dois)
print("CSV rows without a PDF:", dois - pdfs)
EOF
```

Copy the CSV into place:

```bash
cp /path/to/your_papers.csv /home/ec2-user/agr_textpresso/.data/imports/metadata/
```

### Check `generate_pdf_bib.py` is deployed

Metadata only actually gets used if this script is present in the container
— it's tracked in the `agr_textpresso` repo but has to be copied in manually
after any container rebuild (it isn't baked into the Docker image):

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "ls -la /usr/local/bin/generate_pdf_bib.py"
```

If missing:

```bash
docker cp /home/ec2-user/agr_textpresso/Users/kchougul/development/codex_projects/Textpresso/tpctools/generate_pdf_bib.py \
  agr-textpresso-textpresso-1:/usr/local/bin/generate_pdf_bib.py
docker exec agr-textpresso-textpresso-1 bash -lc "chmod +x /usr/local/bin/generate_pdf_bib.py"
```

Without this, papers still get indexed, but with placeholder metadata
(`author|<not uploaded>` etc.) instead of the real title/author/journal —
search results would show blank fields.

## Step 2: Validate No Conflicting Pipeline Is Running

Never start an ingest while another one is in flight against the same
container — it corrupts shared state (lockfiles, partial CAS output).

```bash
docker exec agr-textpresso-textpresso-1 bash -lc \
  "ps -eo pid,etime,args | grep -E 'run_tpc_pipeline|annotate|tokenize|articles2cas|index ' | grep -v grep"
docker exec agr-textpresso-textpresso-1 bash -lc \
  "find /data/textpresso/tmp -maxdepth 1 -iname '*.lock'"
```

Both should be empty/clean before proceeding.

## Step 3: Validate on a Small Subset First

Copy 2-3 representative accessions into `raw_files/pdf/<CorpusName>Test/`
(same layout) and run everything below against `<CorpusName>Test` before
committing to the full corpus. This catches PDF-parsing issues, metadata
mismatches, and pipeline errors cheaply.

## Step 4: (Optional) Exclude an Organism-Specific Ontology

Skip this step entirely if your corpus should get every currently-loaded
ontology (the common case for the same organism repeated corpora are already
being ingested for).

If you need to temporarily exclude an ontology (e.g. maize genes) for this
ingest:

**The critical gotcha**: `ontology.conf` (`/usr/local/etc/ontology.conf`
inside the container) is *not* actually what controls which OBO files get
loaded. The lexicon builder (`tpso`, via `CreateLexica.bash`) globs **every**
`.obo` file physically present in
`/data/textpresso/obofiles4production/`, regardless of whether it's listed
in `ontology.conf`. Editing the conf file alone does nothing — you must
physically move the `.obo` file out of that directory.

```bash
# 1. Move the ontology file out (keep it — you'll restore it after)
docker exec agr-textpresso-textpresso-1 bash -lc "
  mkdir -p /data/textpresso/obofilesbackup
  mv /data/textpresso/obofiles4production/<ontology_file>.obo \
     /data/textpresso/obofilesbackup/<ontology_file>.obo
"

# 2. Drop and rebuild the lexicon tables
docker exec agr-textpresso-textpresso-1 bash -lc "
psql -v ON_ERROR_STOP=1 -d www-data <<'SQL'
do \$\$
declare t text;
begin
  for t in select tablename from pg_tables
    where schemaname='public'
      and (tablename='ontologymembers' or tablename like 'tpontology%' or tablename like 'pcrelations%')
  loop
    execute format('drop table if exists %I cascade', t);
  end loop;
end \$\$;
SQL
CreateLexica.bash
"

# 3. Materialize the merged base tables the annotator actually reads
#    (see install_sorghum_ontologies.sh's materialize_base_tables()
#    in agr_textpresso/scripts/ for the exact SQL — it unions every
#    tpontology_*/pcrelations_* table into tpontology/pcrelations)

# 4. Verify the excluded ontology is really gone
docker exec agr-textpresso-textpresso-1 bash -lc \
  "psql -At -d www-data -c \"select list from ontologymembers order by list;\""
```

**After you finish annotating this corpus** (Step 6 below), restore the
ontology file and rebuild the lexicon again the same way (steps 1-3 above,
moving the file back in first) so other corpora aren't left without it.

There's a working reference script for exactly this pattern (originally
written for the GO/PO/TO-only Sorghum case, before the maize gene OBO
existed) at `agr_textpresso/scripts/install_sorghum_ontologies.sh` — it
handles the DB backup/rebuild/verify steps, but **does not** move `.obo`
files out of the directory, so on its own it's insufficient if a
same-directory `.obo` file needs excluding. Combine it with the manual `mv`
step above.

## Step 5: Generate CAS1 (Tokenize)

The packaged `tokenize` wrapper (`/usr/local/bin/03pdf2cas4tai.sh`) has an
unreliable newer-than-file detection when starting from an empty output
directory — it can silently produce zero output. Call `articles2cas`
directly instead, which is what the wrapper calls internally anyway.

**Use `-t 4` (tai mode), not `-t 1`, done directly (not via the wrapper).**
This recommendation flipped on 2026-08-14 — see "Why `-t4`, and why the old
`-t1` advice was wrong" below before skipping straight to `-t1` out of habit.

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
set -euo pipefail
export LD_LIBRARY_PATH=\"\${LD_LIBRARY_PATH:-}:/usr/local/lib\"
export PATH=\$PATH:/usr/local/bin
mkdir -p /data/textpresso/tpcas-1/<CorpusName>
ls /data/textpresso/raw_files/pdf/<CorpusName> > /tmp/<corpusname>_accessions.txt

# Step 5a: pre-extract per-page text (tai mode reads this, it does not
# read the PDF directly). Run synchronously, NOT backgrounded — the
# packaged convert_text/tai.sh wrapper backgrounds these calls with no
# final 'wait', so a docker exec session ending silently kills in-flight
# jobs and produces partial/zero output with no error. This inline xargs
# form has none of that problem:
for acc in \$(cat /tmp/<corpusname>_accessions.txt); do
  echo \"/data/textpresso/raw_files/pdf/<CorpusName>/\${acc}/\${acc}.pdf\"
done > /tmp/<corpusname>_pdf_paths.txt
cat /tmp/<corpusname>_pdf_paths.txt | xargs -n1 -P8 -I{} timeout 300 pdf2txtimg {}

# Verify every accession actually got per-page text before continuing —
# a PDF that failed extraction here will silently contribute nothing to
# tokenize below rather than erroring:
c=0
for acc in \$(cat /tmp/<corpusname>_accessions.txt); do
  [[ -f \"/data/textpresso/raw_files/pdf/<CorpusName>/\${acc}/\${acc}.00001.txt\" ]] && c=\$((c+1))
done
echo \"extracted: \${c} / \$(wc -l < /tmp/<corpusname>_accessions.txt)\"

# Step 5b: tokenize from the pre-extracted text
cd /data/textpresso/tpcas-1
articles2cas -i /data/textpresso/raw_files/pdf/<CorpusName> \
  -l /tmp/<corpusname>_accessions.txt -t 4 -o <CorpusName> -p
find <CorpusName> -name '*.tpcas' -print0 | xargs -0 -r gzip -f
find <CorpusName> -maxdepth 2
"
```

(`-t 4` = tai/text-and-image input, reading the per-page `.txt`/image files
`pdf2txtimg` just produced; `-p` = use the parent directory name, i.e. the
accession, as the output file's basename.) `LD_LIBRARY_PATH` must include
`/usr/local/lib` or `articles2cas`/`pdf2txtimg` fail immediately with
`TdTokenizer.so: cannot open shared object file`.

### Why `-t4`, and why the old `-t1` advice was wrong

The previous version of this guide (through 2026-08-12) said to always use
`-t1` because `-t4` "produces CAS1 files with zero sentence-boundary
annotations." That was true of what was actually tried at the time — but the
cause wasn't `-t4` itself, it was that nothing had pre-extracted the
per-page text `-t4` needs: the packaged `tokenize` wrapper's own
`pdf2txtimg` step is backgrounded with no `wait` (see Step 5a above) and
silently produces incomplete output. Run `pdf2txtimg` synchronously first,
as shown above, and `-t4` produces completely normal sentence-boundary
annotations — confirmed at full scale on 2026-08-14 across `MaizeTest`,
`MaizeTest100`, `MaizeOA`, `SorghumBase`, and this guide's own `GrainGenes`
ingest (0 zero-sentence accessions out of 1,232 papers processed this way
that day).

More importantly, **section-scoped search (`--type abstract`,
`--type references`, `--type discussion`, etc.) only works for corpora
tokenized via `-t4`.** Section-boundary detection lives in `TdTokenizer.cpp`
(the annotator `-t4` uses) and was fixed there in the 2026-07-13/07-14
sessions (see `Laura_work_updates_log.md`). `-t1`'s tokenizer,
`TpTokenizer.cpp`, has **no section-detection code at all** — not a bug to
fix, a capability that was simply never built there. A corpus tokenized via
`-t1` can never get section-scoped search without being re-tokenized via
`-t4`; there's no annotate-time fix for this, it's baked in at the CAS1
stage. (`CASManager.h`: `PDF2TPCAS_DESCRIPTOR` → `TpTokenizer.xml` for
`-t1`; `TAI2TPCAS_DESCRIPTOR` → `TdTokenizer.xml` for `-t4`.)

`-t4` also happened to route around 2 of 4 PoDoFo parse failures hit during
the 2026-08-14 `SorghumBase`/`GrainGenes` work, since `pdf2txtimg` uses a
different extraction backend than `-t1`'s direct PoDoFo parsing — a nice
side benefit, but don't rely on it universally; the PoDoFo fallback recipe
below is still needed for the cases `-t4` doesn't resolve on its own.

**`-t1` is still valid** if you specifically don't need section-scoped
search for this corpus and want to skip the extra `pdf2txtimg` step — it's
simpler and one step shorter. Use the exact command from before:
`articles2cas -i ... -t 1 -o <CorpusName> -p` (no `pdf2txtimg` pre-step
needed). Just know that decision is permanent for that corpus's CAS1 unless
you retokenize it later.

Verify before moving on:

```bash
docker exec agr-textpresso-textpresso-1 bash -lc \
  "zcat /data/textpresso/tpcas-1/<CorpusName>/<accession>/<accession>.tpcas.gz | grep -c '<textpresso:sentence'"
# expect a number in the low hundreds for a typical paper, not 0
```

Verify each accession got a `.tpcas.gz` file before moving on — and check
every accession has a *non-zero* sentence count, not just that the file
exists (a small fraction of real-world PDFs fail cleanly):

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
cd /data/textpresso/tpcas-1/<CorpusName>
for d in */; do
  acc=\${d%/}
  echo \"\${acc},\$(zcat \${acc}/\${acc}.tpcas.gz | grep -c '<textpresso:sentence')\"
done
" | awk -F, '\$2==0'
```

### If a PDF produces zero sentences

Textpresso's bundled PDF parser (PoDoFo 0.9.3) is older than mainstream
tools and sometimes fails on modern PDF producers with
`PdfInfo.cpp: ... PoDoFo encounter an error ... ePdfError_UnsupportedFilter`
while `pdftotext` (Poppler, also available in the container) reads the same
file fine. Two fallbacks, in order of preference:

**1. Re-save the PDF with Ghostscript** (works for many, not all, cases):

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
cp <pdf> <pdf>.orig
gs -q -dNOPAUSE -dBATCH -dSAFER -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 \
   -sOutputFile=/tmp/fixed.pdf <pdf>
mv /tmp/fixed.pdf <pdf>
"
```
Then rerun `articles2cas -t 1` for just that accession.

**2. Fall back to plain-text extraction** if Ghostscript doesn't help (or
only partially recovers text — compare sentence counts, don't assume any
non-zero result is complete):

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
mkdir -p /tmp/txt_fallback/<accession>
pdftotext -layout <original_pdf> /tmp/txt_fallback/<accession>/<accession>.txt
# CRITICAL: pdftotext inserts form-feed (0x0C) page-break characters that
# are invalid inside XML attribute values and make CAS2 annotation fail
# with 'XML parse fatal error ... invalid character 0xC in attribute value
# sofaString'. Strip them before running articles2cas:
tr -d '\014' < /tmp/txt_fallback/<accession>/<accession>.txt > /tmp/clean.txt
mv /tmp/clean.txt /tmp/txt_fallback/<accession>/<accession>.txt
export LD_LIBRARY_PATH=\"\${LD_LIBRARY_PATH:-}:/usr/local/lib\"
export PATH=\$PATH:/usr/local/bin
echo '<accession>' > /tmp/onelist.txt
cd /data/textpresso/tpcas-1
articles2cas -i /tmp/txt_fallback -l /tmp/onelist.txt -t 3 -o <CorpusName> -p
find <CorpusName>/<accession> -name '*.tpcas' -print0 | xargs -0 -r gzip -f
"
```
(`-t 3` = plain text input.) Restore the original, untouched PDF into
`raw_files/pdf/` afterward if you ran the Ghostscript step — keep the raw
file pristine even if the searchable text came from a derived source.

**Some source PDFs are genuinely scanned images with no text layer**
(common with older interlibrary-loan scans — check
`pdftotext <pdf> - | wc -l`; a handful of lines that turn out to be a
library cover page, not the paper, is the signature). No text-extraction
trick recovers content that was never encoded as text — real OCR would be
required. These papers will still get indexed (searchable by title/author/
abstract from your metadata CSV, and appear in the corpus), but full-text
search inside the body won't find anything. Flag these to whoever provided
the PDFs rather than silently shipping an empty-feeling record.

## Step 6: Generate CAS2 (Annotate)

`annotate` processes **everything** under the `-c` directory you give it —
it is not aware of which corpora have already been annotated. Never point it
directly at the real `/data/textpresso/tpcas-1` (that would re-annotate
every existing corpus). Instead build a scoped staging tree of symlinks
containing only the new corpus, exactly as
`install_sorghum_ontologies.sh`'s `reannotate_sorghum_corpus()` does:

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
set -euo pipefail
tmp_root=/data/textpresso/tmp/<corpusname>-annotate
rm -rf \"\${tmp_root}\"
mkdir -p \"\${tmp_root}/cas1/<CorpusName>\"
find /data/textpresso/tpcas-1/<CorpusName> -mindepth 1 -maxdepth 1 -type d -print0 |
while IFS= read -r -d '' src; do
  acc=\$(basename \"\${src}\")
  dst=\"\${tmp_root}/cas1/<CorpusName>/\${acc}\"
  mkdir -p \"\${dst}\"
  find \"\${src}\" -maxdepth 1 -name '*.tpcas.gz' -print -quit |
  while IFS= read -r cas; do ln -sf \"\${cas}\" \"\${dst}/\$(basename \"\${cas}\")\"; done
  if [[ -d \"\${src}/images\" ]]; then ln -sfn \"\${src}/images\" \"\${dst}/images\"; fi
done
annotate -c \"\${tmp_root}/cas1\" -C /data/textpresso/tpcas-2 -t /data/textpresso/tmp -P 2
"
```

Unlike the `tokenize` wrapper, `annotate` (`07cas1tocas2.sh`) works
reliably when invoked directly — no need to bypass it. Verify output landed
under `/data/textpresso/tpcas-2/<CorpusName>/<accession>/<accession>.tpcas.gz`
for every accession.

## Step 7: Generate `.bib` Metadata Sidecars

**A missing `.bib` file silently drops the paper from the index entirely**
(not just blank metadata — the paper won't be indexed at all;
`IndexManager::add_cas_file_to_index` bails out before touching the
document). Generate one per accession:

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
for acc in \$(cat /tmp/<corpusname>_accessions.txt); do
  python3 /usr/local/bin/generate_pdf_bib.py \
    --pdf /data/textpresso/raw_files/pdf/<CorpusName>/\${acc}/\${acc}.pdf \
    --bib /data/textpresso/tpcas-2/<CorpusName>/\${acc}/\${acc}.bib \
    --accession \${acc} \
    --metadata-dir /data/textpresso/imports/metadata
done
"
```

Spot check one `.bib` file to confirm it has real title/author/journal
values, not `<not uploaded>` placeholders (which means the CSV DOI didn't
match — check accession formatting).

## Step 8: Build the Lucene Index

`index` rebuilds the **entire** shared index from all of
`/data/textpresso/tpcas-2` (every corpus), then atomically swaps it in. This
is expected — it's how new corpora get merged into the shared search index
— but it means indexing time scales with total corpus size across *all*
MODs on this instance, not just your new papers (took roughly 15-20 minutes
for a few hundred papers as of 2026-08).

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
set -o pipefail
log=/data/textpresso/logs/<corpusname>-index-\$(date -u +%Y%m%dT%H%M%SZ).log
rm -rf /data/textpresso/luceneindex_new
rm -f /data/textpresso/tmp/12index.lock
index -C /data/textpresso/tpcas-2 -i /data/textpresso/luceneindex 2>&1 | tee \"\${log}\"
"
```

If it reports errors (`No space left`, `cannot create`, `std::exception`),
the old index is preserved at `/data/textpresso/luceneindex.bk` — restore it
before investigating further, don't leave the live index half-swapped.

Restart the API so it picks up the new index (`IndexReader`s are cached for
the life of the process and never reopened automatically):

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
pkill -f textpressoapi || true
sleep 2
nohup textpressoapi >> /data/textpresso/textpressoapi_data/api.log 2>&1 &
"
```

(Check how the container's entrypoint actually starts `textpressoapi` if
this doesn't come back up — the exact invocation can change; confirm with
`docker exec agr-textpresso-textpresso-1 ps aux | grep textpressoapi` before
and after.)

### If a PDF-mode extraction produces garbage bytes for non-ASCII characters

A rarer failure mode than zero-sentence extraction: the paper indexes and
searches fine *until* a search happens to return a sentence containing a
particular non-ASCII character (Greek letters, special symbols), at which
point the API response becomes invalid UTF-8 and breaks any strict JSON
client (including `bin/tpc_search.py`) — `UnicodeDecodeError: 'utf-8' codec
can't decode byte ... invalid start byte`. Root cause: Textpresso's bundled
PDF text extractor sometimes maps an unusual font's glyph (e.g. "α") to a
single raw Latin-1-range byte instead of decoding it into a proper
multi-byte UTF-8 sequence, and nothing downstream validates or repairs it —
so the bad byte gets baked into the CAS2 XMI and served as-is.

There's no config flag for this — it's a per-document defect you find by
testing search terms that hit the affected paper. Diagnose with a raw curl
+ Python UTF-8 check against `search_documents` (see
`Laura_work_updates_log.md`'s 2026-08-12 entry for the exact commands used
to trace one down to a specific accession and byte offset). Fix the same
way as the zero-sentence case: extract with `pdftotext`, strip control
characters, reprocess via `-t 3` (text mode):

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
python3 -c \"
import re
with open('/tmp/extracted.txt', 'r', encoding='utf-8', errors='replace') as f:
    text = f.read()
cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)  # all C0 controls except tab/LF/CR
with open('/tmp/extracted.txt', 'w', encoding='utf-8') as f:
    f.write(cleaned)
\"
"
```

Note: stripping only the form-feed character (the fix for the zero-sentence
case above) is not always sufficient — longer documents can carry other
stray C0 control bytes anywhere in the text that also break CAS2's XML
attribute parsing (`invalid character 0x4 in attribute value 'sofaString'`,
etc.). Strip the whole C0 control range except tab/newline/CR, not just
form-feed.

### If you need to re-annotate a single already-annotated file in isolation

`annotate` (`07cas1tocas2.sh`) builds temporary merged `tpontology` /
`pcrelations` tables at the start of each run and **drops them again at the
end** — they don't persist between invocations. Its internal work-splitting
across `-P` parallel workers also assumes roughly even load; **re-running it
for a batch containing only one or two changed files can race** (one worker
reaches the table-drop cleanup while another is still mid-query), producing
`ERROR: relation "pcrelations" does not exist` or `pqxx::undefined_table`.
symptoms partway through what looks like a normal run.

The reliable path for a single-file touch-up is to bypass the wrapper:
materialize the merged tables yourself (same SQL as
`install_sorghum_ontologies.sh`'s `materialize_base_tables()`), call
`runAECpp` directly, then drop the tables again:

```bash
docker exec agr-textpresso-textpresso-1 bash -lc "
# 1. materialize tpontology/pcrelations from the *_N per-ontology tables
#    (see materialize_base_tables() in agr_textpresso/scripts/install_sorghum_ontologies.sh)

# 2. run the annotator directly on one decompressed CAS1 file
export LD_LIBRARY_PATH=\"\${LD_LIBRARY_PATH:-}:/usr/local/lib\"
export PATH=\$PATH:/usr/local/bin
mkdir -p /tmp/manual/in /tmp/manual/out
cp <cas1_file>.tpcas.gz /tmp/manual/in/ && gunzip /tmp/manual/in/*.tpcas.gz
runAECpp /usr/local/uima_descriptors/TpLexiconAnnotatorFromPg.xml -xmi /tmp/manual/in /tmp/manual/out
pigz /tmp/manual/out/*.tpcas
cp /tmp/manual/out/*.tpcas.gz <destination_cas2_dir>/

# 3. drop the temp tables again
echo 'drop table pcrelations' | psql www-data
echo 'drop table tpontology' | psql www-data
"
```
Don't forget to also copy the `images/` directory alongside the CAS2 file
if the original had one, and to regenerate the `.bib` sidecar.

## Step 9: Verify

```bash
# corpus shows up
curl -s http://localhost:18080/v1/textpresso/api/available_corpora

# nonzero results for a known keyword
curl -s -X POST http://localhost:18080/v1/textpresso/api/get_documents_count \
  -H 'Content-Type: application/json' \
  -d '{"query":"<keyword you expect to match>","corpora":["<CorpusName>"]}'
```

Then in the UI (`http://localhost:8080/tpc/search`), hard-refresh, select
the new corpus, search, and confirm both results and real (non-placeholder)
metadata appear.

If you excluded an ontology in Step 4, spot-check a CAS2 file to confirm it
really has no annotations from that ontology:

```bash
docker exec agr-textpresso-textpresso-1 bash -lc \
  "zcat /data/textpresso/tpcas-2/<CorpusName>/<accession>/<accession>.tpcas.gz | grep -c 'tpzm'"
# expect 0
```

If you tokenized via `-t4` (the default per Step 5), spot-check that section
detection actually worked — a corpus-wide zero here usually means the
`pdf2txtimg` pre-step was skipped or failed silently:

```bash
docker exec agr-textpresso-textpresso-1 bash -lc \
  "zcat /data/textpresso/tpcas-1/<CorpusName>/<accession>/<accession>.tpcas.gz | grep -c 'textpresso:section'"
# expect > 0 for most papers (not every paper has recognizable headings —
# some legitimately return 0, e.g. book chapters with nonstandard section
# names — but a corpus-wide zero means something's wrong)
```

## Step 10: Clean Up and Restore Shared State

- **Delete your validation-subset corpus (Step 3) once you're done with it
  — don't leave it alongside the full corpus.** The index builder
  (`create_single_index.sh`) deduplicates papers by accession **basename
  only**, across *every* corpus, not per-corpus. If the same accession
  exists in both `<CorpusName>` and `<CorpusName>Test` (which it will, since
  the test subset is a copy of a few real accessions), one of the two
  copies silently disappears from search results system-wide with no error
  — this happened during the GDR ingest and cost an extra reindex cycle to
  catch. Remove the raw PDFs, CAS1, and CAS2 for the test corpus, then
  reindex once more:
  ```bash
  docker exec agr-textpresso-textpresso-1 bash -lc "
    rm -rf /data/textpresso/raw_files/pdf/<CorpusName>Test
    rm -rf /data/textpresso/tpcas-1/<CorpusName>Test
    rm -rf /data/textpresso/tpcas-2/<CorpusName>Test
  "
  ```
- If you moved an ontology `.obo` file out in Step 4, move it back and
  rebuild the lexicon again (same steps, reversed) so other corpora keep
  their normal annotation coverage.
- Remove any scoped staging directories you created under
  `/data/textpresso/tmp/`.
- Re-check disk space.
- After any of the above, reindex and restart `textpressoapi` again, then
  re-verify document counts for the real corpus match your paper count.

## Known Gotchas Checklist

- [ ] `generate_pdf_bib.py` present at `/usr/local/bin/` in the container
- [ ] No other `run_tpc_pipeline_incremental.sh`/`annotate`/`tokenize`
      process or lockfile active before you start
- [ ] Metadata CSV's `doi` column matches your PDF accession names
- [ ] Used the scoped-symlink pattern for `annotate` — never pointed `-c` at
      the full shared `tpcas-1`
- [ ] Every accession has a `.bib` file before indexing (missing = silently
      dropped from the index, not just blank metadata)
- [ ] If excluding an ontology: moved the `.obo` file itself out of
      `obofiles4production/`, not just edited `ontology.conf`
- [ ] Restored any temporarily-excluded ontology afterward
- [ ] Restarted `textpressoapi` after reindexing
- [ ] Verified via API `get_documents_count` *and* the UI, not just
      "corpus appears in `available_corpora`"
