#!/usr/bin/env bash
#
# process_batch.sh — run the Textpresso *annotation* pipeline on a batch of new
# PDFs on a local (laptop) Textpresso instance, and package the resulting CAS-2
# files for handoff to the server operator.
#
# This is the front half of the ingest pipeline only:
#
#     stage PDFs -> tokenize (CAS-1) -> annotate (CAS-2) -> .bib sidecars
#                -> validate -> package .tgz
#
# It deliberately does NOT build the Lucene index. The server operator merges
# the packaged trees into the mounted /data/textpresso and runs `index` there.
#
# See docs/TEXTPRESSO_LOCAL_ANNOTATION_SETUP.md for the full setup and the
# rationale (ontology parity, Windows/WSL2 notes, collaborator handoff).
#
# Usage:
#   scripts/process_batch.sh -c SorghumBase [options]
#
# Options:
#   -c CORPUS         Corpus name (required). Becomes the directory name
#                     everywhere and the value passed to `-c` when searching.
#   -s SRC_PDF_DIR    Directory of flat <accession>.pdf files to stage into the
#                     required layout first. Omit if you have already staged
#                     PDFs at raw_files/pdf/<CORPUS>/<accession>/<accession>.pdf
#   -C CONTAINER      Container name (default: auto-detected, else
#                     agr-textpresso-textpresso-1).
#   -P N              Parallel workers for tokenize/annotate (default: 1).
#                     Keep low on a laptop — annotate is memory-hungry.
#   -t MODE           Tokenizer mode: 4 (default, enables section-scoped
#                     search) or 1 (simpler, no section detection).
#   -O LIST           Expected `ontologymembers` list, comma-separated and
#                     sorted (e.g. go,po,to,zmays_genes_20260813). If given,
#                     the run aborts unless the local DB matches exactly.
#                     This is the ontology-parity guard — get the value from
#                     the server operator.
#   -o OUTDIR         Host-visible output dir for the package, relative to the
#                     data dir (default: handoff). The .tgz lands in
#                     <data>/handoff/ which is bind-mounted to your laptop.
#   -k                Keep the per-corpus scoped staging tree under tmp/ for
#                     debugging instead of removing it.
#   -n                Dry run: print what would happen, do nothing.
#   -h                This help.
#
# Steps can be resumed — re-running is safe. Each stage checks its own outputs.

set -euo pipefail

# ----------------------------------------------------------------------------
# Args
# ----------------------------------------------------------------------------
CORPUS=""
SRC_PDF_DIR=""
CONTAINER=""
NPROC=1
TOK_MODE=4
EXPECT_ONTO=""
OUTDIR="handoff"
KEEP_TMP=0
DRY_RUN=0

usage() { sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; s/^#$//' | head -n -1; exit "${1:-0}"; }

while getopts ":c:s:C:P:t:O:o:knh" opt; do
  case "$opt" in
    c) CORPUS="$OPTARG" ;;
    s) SRC_PDF_DIR="$OPTARG" ;;
    C) CONTAINER="$OPTARG" ;;
    P) NPROC="$OPTARG" ;;
    t) TOK_MODE="$OPTARG" ;;
    O) EXPECT_ONTO="$OPTARG" ;;
    o) OUTDIR="$OPTARG" ;;
    k) KEEP_TMP=1 ;;
    n) DRY_RUN=1 ;;
    h) usage 0 ;;
    \?) echo "Unknown option: -$OPTARG" >&2; usage 1 ;;
    :) echo "Option -$OPTARG requires an argument" >&2; usage 1 ;;
  esac
done

[[ -n "$CORPUS" ]] || { echo "ERROR: -c CORPUS is required" >&2; usage 1; }
[[ "$TOK_MODE" == "1" || "$TOK_MODE" == "4" ]] || { echo "ERROR: -t must be 1 or 4" >&2; exit 1; }

# lowercase, filesystem-safe tag for temp files
CORPUS_TAG="$(printf '%s' "$CORPUS" | tr '[:upper:] ' '[:lower:]_' | tr -cd 'a-z0-9_.-')"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"

BASE=/data/textpresso
RAW_PDF="${BASE}/raw_files/pdf/${CORPUS}"
CAS1="${BASE}/tpcas-1/${CORPUS}"
CAS2="${BASE}/tpcas-2/${CORPUS}"
STAGE_ROOT="${BASE}/tmp/${CORPUS_TAG}-annotate-${RUN_ID}"
ACC_LIST="/tmp/${CORPUS_TAG}_accessions.txt"
LOG_DIR="${BASE}/logs"

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
log()  { printf '\n\033[1;36m[%s] %s\033[0m\n' "$(date -u +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# Run a bash snippet inside the container with the Textpresso runtime env set.
dexec() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '  (dry-run) docker exec %s bash -lc: %s\n' "$CONTAINER" "$1"
    return 0
  fi
  docker exec "$CONTAINER" bash -lc '
    export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}:/usr/local/lib"
    export PATH="$PATH:/usr/local/bin"
    '"$1"
}

# ----------------------------------------------------------------------------
# 0. Preflight
# ----------------------------------------------------------------------------
log "Preflight"

command -v docker >/dev/null || die "docker not found on PATH"

if [[ -z "$CONTAINER" ]]; then
  CONTAINER="$(docker ps --filter 'ancestor=agr-textpresso-textpresso' --format '{{.Names}}' | head -n1 || true)"
  [[ -n "$CONTAINER" ]] || CONTAINER="agr-textpresso-textpresso-1"
fi
docker exec "$CONTAINER" true 2>/dev/null || die "container '$CONTAINER' is not running (start it: docker compose up -d)"
echo "  container: $CONTAINER"
echo "  corpus:    $CORPUS"
echo "  tokenizer: -t $TOK_MODE   workers: -P $NPROC"

# No competing pipeline / stale locks
BUSY="$(dexec "ps -eo args | grep -E 'run_tpc_pipeline|articles2cas|runAECpp|create_single_index|indexmerger' | grep -v grep || true")"
[[ -z "$BUSY" ]] || die "another pipeline process is running in the container:\n$BUSY"
LOCKS="$(dexec "find ${BASE}/tmp -maxdepth 1 -iname '*.lock' 2>/dev/null || true")"
if [[ -n "$LOCKS" ]]; then
  warn "stale lock files present:"; echo "$LOCKS"
  warn "remove them only if you are sure nothing is running: docker exec $CONTAINER rm -f $LOCKS"
  die "refusing to start with locks present"
fi

# generate_pdf_bib.py deployed?
dexec "test -f /usr/local/bin/generate_pdf_bib.py" \
  || die "generate_pdf_bib.py is not in the container. Deploy it (see setup doc, step 5) before running."

# Ontology parity guard
ONTO_NOW="$(dexec "psql -At -d www-data -c \"select string_agg(list, ',' order by list) from ontologymembers;\"" | tr -d '[:space:]')"
echo "  ontologymembers: ${ONTO_NOW:-<empty>}"
if [[ -n "$EXPECT_ONTO" ]]; then
  want="$(printf '%s' "$EXPECT_ONTO" | tr -d '[:space:]')"
  [[ "$ONTO_NOW" == "$want" ]] \
    || die "ontology mismatch — local DB has '${ONTO_NOW}', server expects '${want}'.\nRe-sync obofiles4production/ and rebuild lexica (setup doc, step 5) before annotating."
  echo "  ontology parity: OK"
else
  warn "no -O given: not verifying ontology parity against the server. Annotations may not match the server's category tree."
fi

TPONT_ROWS="$(dexec "psql -At -d www-data -c 'select count(*) from tpontology;'" | tr -d '[:space:]')"
echo "  tpontology rows: ${TPONT_ROWS:-0}"
[[ "${TPONT_ROWS:-0}" -gt 0 ]] || die "tpontology is empty — lexica not built. See setup doc, step 5."

# PDFs present?
if [[ -n "$SRC_PDF_DIR" ]]; then
  [[ -d "$SRC_PDF_DIR" ]] || die "SRC_PDF_DIR does not exist: $SRC_PDF_DIR"
fi

# ----------------------------------------------------------------------------
# 1. Stage PDFs into the required layout
#    raw_files/pdf/<CORPUS>/<accession>/<accession>.pdf
# ----------------------------------------------------------------------------
if [[ -n "$SRC_PDF_DIR" ]]; then
  log "Stage PDFs from $SRC_PDF_DIR"
  shopt -s nullglob
  pdfs=( "$SRC_PDF_DIR"/*.pdf "$SRC_PDF_DIR"/*.PDF )
  shopt -u nullglob
  [[ ${#pdfs[@]} -gt 0 ]] || die "no *.pdf files in $SRC_PDF_DIR"
  echo "  ${#pdfs[@]} PDF(s) to stage"
  for pdf in "${pdfs[@]}"; do
    acc="$(basename "$pdf")"; acc="${acc%.pdf}"; acc="${acc%.PDF}"
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "  (dry-run) stage $acc"
      continue
    fi
    docker exec "$CONTAINER" mkdir -p "${RAW_PDF}/${acc}"
    docker cp "$pdf" "${CONTAINER}:${RAW_PDF}/${acc}/${acc}.pdf"
  done
else
  log "Stage PDFs — skipped (-s not given; assuming already staged)"
fi

STAGED_COUNT="$(dexec "find '${RAW_PDF}' -mindepth 2 -maxdepth 2 -iname '*.pdf' 2>/dev/null | wc -l" | tr -d '[:space:]')"
[[ "${STAGED_COUNT:-0}" -gt 0 ]] || die "no staged PDFs found under ${RAW_PDF}"
echo "  staged accessions: $STAGED_COUNT"
dexec "ls '${RAW_PDF}' > '${ACC_LIST}'; wc -l < '${ACC_LIST}'" >/dev/null || true

# Warn about metadata coverage
CSV_COUNT="$(dexec "ls ${BASE}/imports/metadata/*.csv 2>/dev/null | wc -l" | tr -d '[:space:]')"
[[ "${CSV_COUNT:-0}" -gt 0 ]] || warn "no metadata CSV in ${BASE}/imports/metadata/ — .bib files will be placeholders."

# ----------------------------------------------------------------------------
# 2. Tokenize -> CAS-1
# ----------------------------------------------------------------------------
log "Tokenize (CAS-1, -t $TOK_MODE)"
dexec "mkdir -p '${CAS1}' '${LOG_DIR}'"

if [[ "$TOK_MODE" == "4" ]]; then
  # -t4 reads pre-extracted per-page text/images; pdf2txtimg MUST run first and
  # MUST be synchronous (the packaged wrapper backgrounds it with no wait).
  log "  pdf2txtimg (per-page text/image extraction)"
  dexec "
    set -euo pipefail
    : > /tmp/${CORPUS_TAG}_pdf_paths.txt
    for acc in \$(cat '${ACC_LIST}'); do
      echo '${RAW_PDF}/'\"\${acc}\"'/'\"\${acc}\"'.pdf' >> /tmp/${CORPUS_TAG}_pdf_paths.txt
    done
    cat /tmp/${CORPUS_TAG}_pdf_paths.txt | xargs -r -n1 -P${NPROC} -I{} timeout 600 pdf2txtimg {} || true
    c=0; t=0
    for acc in \$(cat '${ACC_LIST}'); do
      t=\$((t+1))
      [[ -f '${RAW_PDF}/'\"\${acc}\"'/'\"\${acc}\"'.00001.txt' ]] && c=\$((c+1))
    done
    echo \"  per-page text extracted: \${c} / \${t}\"
    [[ \${c} -gt 0 ]] || { echo 'pdf2txtimg produced nothing' >&2; exit 1; }
  "
fi

TOK_LOG="${LOG_DIR}/${CORPUS_TAG}-tokenize-${RUN_ID}.log"
dexec "
  set -euo pipefail
  cd '${BASE}/tpcas-1'
  articles2cas -i '${RAW_PDF}' -l '${ACC_LIST}' -t ${TOK_MODE} -o '${CORPUS}' -p 2>&1 | tee '${TOK_LOG}'
  find '${CORPUS}' -name '*.tpcas' -print0 | xargs -0 -r gzip -f
"

# Verify: every accession has a non-zero-sentence CAS-1 file
log "  verify CAS-1"
ZERO_SENT="$(dexec "
  cd '${CAS1}'
  for d in */; do
    acc=\${d%/}
    f=\"\${acc}/\${acc}.tpcas.gz\"
    if [[ -f \"\$f\" ]]; then
      n=\$(zcat \"\$f\" | grep -c '<textpresso:sentence' || true)
    else
      n=missing
    fi
    [[ \"\$n\" == 0 || \"\$n\" == missing ]] && echo \"\${acc} (\${n})\"
  done
  true
")"
if [[ -n "$ZERO_SENT" ]]; then
  warn "these accessions produced no CAS-1 sentences (bad/scanned PDF — see setup doc 'zero sentences'):"
  echo "$ZERO_SENT" | sed 's/^/    /'
  warn "they will index by metadata only. Fix or accept before shipping."
fi

# ----------------------------------------------------------------------------
# 3. Annotate -> CAS-2  (scoped symlink tree so only THIS corpus is processed)
# ----------------------------------------------------------------------------
log "Annotate (CAS-2)"
ANN_LOG="${LOG_DIR}/${CORPUS_TAG}-annotate-${RUN_ID}.log"
dexec "
  set -euo pipefail
  rm -rf '${STAGE_ROOT}'
  mkdir -p '${STAGE_ROOT}/cas1/${CORPUS}'
  find '${CAS1}' -mindepth 1 -maxdepth 1 -type d -print0 |
  while IFS= read -r -d '' src; do
    acc=\$(basename \"\${src}\")
    dst=\"${STAGE_ROOT}/cas1/${CORPUS}/\${acc}\"
    mkdir -p \"\${dst}\"
    find \"\${src}\" -maxdepth 1 -name '*.tpcas.gz' -print -quit |
    while IFS= read -r cas; do ln -sf \"\${cas}\" \"\${dst}/\$(basename \"\${cas}\")\"; done
    [[ -d \"\${src}/images\" ]] && ln -sfn \"\${src}/images\" \"\${dst}/images\" || true
  done
  rm -f '${BASE}/tmp/07cas1tocas2.lock'
  annotate -c '${STAGE_ROOT}/cas1' -C '${BASE}/tpcas-2' -t '${BASE}/tmp' -P ${NPROC} 2>&1 | tee '${ANN_LOG}'
"

# annotate exit code is unreliable; grep the log
if dexec "grep -Eq 'No space left|Error opening output xmi|std::exception|relation \"pcrelations\" does not exist|undefined_table' '${ANN_LOG}'"; then
  die "annotate reported errors — inspect ${ANN_LOG} in the container. CAS-2 may be incomplete; do not ship."
fi

if [[ "$KEEP_TMP" == "0" ]]; then
  dexec "rm -rf '${STAGE_ROOT}'"
fi

# Verify every accession got a CAS-2 file
log "  verify CAS-2"
MISSING_CAS2="$(dexec "
  for acc in \$(cat '${ACC_LIST}'); do
    [[ -f '${CAS2}/'\"\${acc}\"'/'\"\${acc}\"'.tpcas.gz' ]] || echo \"\${acc}\"
  done
  true
")"
[[ -z "$MISSING_CAS2" ]] || { warn "no CAS-2 for:"; echo "$MISSING_CAS2" | sed 's/^/    /'; die "CAS-2 generation incomplete"; }

# ----------------------------------------------------------------------------
# 4. Generate .bib sidecars  (missing .bib == paper silently dropped at index)
# ----------------------------------------------------------------------------
log "Generate .bib sidecars"
dexec "
  set -euo pipefail
  for acc in \$(cat '${ACC_LIST}'); do
    python3 /usr/local/bin/generate_pdf_bib.py \
      --pdf '${RAW_PDF}/'\"\${acc}\"'/'\"\${acc}\"'.pdf' \
      --bib '${CAS2}/'\"\${acc}\"'/'\"\${acc}\"'.bib' \
      --accession \"\${acc}\" \
      --metadata-dir '${BASE}/imports/metadata' || echo \"  bib failed: \${acc}\" >&2
  done
"

PLACEHOLDER="$(dexec "
  for acc in \$(cat '${ACC_LIST}'); do
    b='${CAS2}/'\"\${acc}\"'/'\"\${acc}\"'.bib'
    { [[ -f \"\$b\" ]] && ! grep -q '<not uploaded>' \"\$b\"; } || echo \"\${acc}\"
  done
  true
")"
if [[ -n "$PLACEHOLDER" ]]; then
  warn "these accessions have missing or placeholder .bib metadata (check the CSV 'doi' column matches the accession):"
  echo "$PLACEHOLDER" | sed 's/^/    /'
fi

# ----------------------------------------------------------------------------
# 5. Summary
# ----------------------------------------------------------------------------
log "Batch summary"
dexec "
  echo '  raw PDFs : '\$(find '${RAW_PDF}' -mindepth 2 -maxdepth 2 -iname '*.pdf' | wc -l)
  echo '  CAS-1    : '\$(find '${CAS1}' -mindepth 2 -maxdepth 2 -name '*.tpcas.gz' | wc -l)
  echo '  CAS-2    : '\$(find '${CAS2}' -mindepth 2 -maxdepth 2 -name '*.tpcas.gz' | wc -l)
  echo '  .bib     : '\$(find '${CAS2}' -mindepth 2 -maxdepth 2 -name '*.bib' | wc -l)
  if [[ '${TOK_MODE}' == '4' ]]; then
    s=\$(for acc in \$(cat '${ACC_LIST}'); do zcat '${CAS1}/'\"\${acc}\"'/'\"\${acc}\"'.tpcas.gz' 2>/dev/null | grep -c 'textpresso:section' || true; done | awk '{t+=\$1} END{print t+0}')
    echo \"  section tags total: \${s}  (0 across the whole corpus means the -t4 pre-step failed)\"
  fi
"

# ----------------------------------------------------------------------------
# 6. Package for the server operator
# ----------------------------------------------------------------------------
log "Package handoff bundle"
PKG="${OUTDIR}/${CORPUS_TAG}-${RUN_ID}.tgz"
MANIFEST="${OUTDIR}/${CORPUS_TAG}-${RUN_ID}.manifest.txt"
dexec "
  set -euo pipefail
  mkdir -p '${BASE}/${OUTDIR}'
  cd '${BASE}'
  # Ship all three trees so the absolute 'images' symlinks in tpcas-2 resolve
  # once extracted under /data/textpresso on the server. Include the metadata
  # CSV(s) for reference. Symlinks are preserved (no -h).
  tar czf '${PKG}' \
    raw_files/pdf/${CORPUS} \
    tpcas-1/${CORPUS} \
    tpcas-2/${CORPUS} \
    \$(cd '${BASE}' && ls imports/metadata/*.csv 2>/dev/null || true)
  {
    echo 'Textpresso annotation batch — handoff manifest'
    echo \"corpus        : ${CORPUS}\"
    echo \"generated     : ${RUN_ID} (UTC)\"
    echo \"tokenizer     : articles2cas -t ${TOK_MODE}\"
    echo \"ontologies    : ${ONTO_NOW}\"
    echo \"tpontology    : ${TPONT_ROWS} rows\"
    echo \"accessions    : \"\$(cat '${ACC_LIST}' | wc -l)
    echo
    echo 'Server operator steps:'
    echo '  1. Extract this tarball into the mounted /data/textpresso:'
    echo \"       tar xzf \$(basename '${PKG}') -C /data/textpresso\"
    echo '  2. Confirm the container obofiles4production/ still matches:'
    echo \"       ${ONTO_NOW}\"
    echo '     (if the monthly ontology update ran since this batch was made,'
    echo '      the corpus should be re-annotated on the server instead.)'
    echo '  3. Rebuild the index and restart the API:'
    echo '       index -C /data/textpresso/tpcas-2 -i /data/textpresso/luceneindex'
    echo '       pkill -f textpressoapi; sleep 2; nohup textpressoapi >> /data/textpresso/textpressoapi_data/api.log 2>&1 &'
    echo '  4. Verify document count == accession count for this corpus.'
    echo
    echo 'Accessions:'
    sed 's/^/  /' '${ACC_LIST}'
  } > '${BASE}/${MANIFEST}'
  ls -lh '${PKG}' '${BASE}/${MANIFEST}'
"

log "Done."
cat <<EOF

  Package (on your laptop, under the bind-mounted .data dir):
      .data/${PKG}
      .data/${MANIFEST}

  Send both to the server operator. The manifest has their steps.
EOF
