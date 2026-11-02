#!/usr/bin/env bash
#
# stage_cas1_images.sh — copy the figure images pdf2txtimg extracted into
# tpcas-1/<CORPUS>/<acc>/images/ and fix CMYK JPEGs, so Textpresso Central's
# viewer can show them.
#
# articles2cas -t 4 records <acc>.NNNNN.img-NNN.{png,jpg} in the CAS but leaves
# tpcas-1/<acc>/images/ empty; the files stay in raw_files/pdf/<CORPUS>/<acc>/.
# The server pipeline copies them (03pdf2cas4tai.sh) and runs cmykinverter
# (invert_img); process_batch.sh did neither before 2026-10-30, so corpora built
# with it, MaizeOpenAccess included, have no figures in TPC.
# tpcas-2/<acc>/images is a symlink to the tpcas-1 directory, and TPC serves
# /tpc/images/<CORPUS>/<acc>/images/<file> from tpcas-2, so no re-index or
# restart is needed.
#
# Papers whose CAS-1 came from JATS (raw_files/xml/<CORPUS>/<acc>/<acc>.jats.json)
# get their PDF's images too: bin/add_jats_figures.py anchors them in the JATS
# text, and the viewers fetch them from the same directory.
#
# Safe to re-run: cp -n never overwrites, and cmykinverter skips the JPEGs
# already listed in each directory's cmyk.index. (Overwriting would be the bug:
# a fresh, un-inverted copy that cmyk.index says is already fixed.)
#
# Runs inside the Textpresso container, or anywhere /data/textpresso is the
# data root and cmykinverter is on PATH:
#
#   docker exec -i agr-textpresso-textpresso-1 bash -s -- MaizeOpenAccess \
#       < stage_cas1_images.sh
#
# Usage: stage_cas1_images.sh CORPUS [BASE]   (BASE default /data/textpresso)

set -euo pipefail
export PATH="$PATH:/usr/local/bin"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}:/usr/local/lib"
export LC_ALL=C   # byte-wise read of the JPEG signature below

CORPUS="${1:?usage: stage_cas1_images.sh CORPUS [BASE]}"
BASE="${2:-/data/textpresso}"
RAW_PDF="${BASE}/raw_files/pdf/${CORPUS}"
RAW_XML="${BASE}/raw_files/xml/${CORPUS}"
CAS1="${BASE}/tpcas-1/${CORPUS}"

command -v cmykinverter >/dev/null || { echo "ERROR: cmykinverter not on PATH" >&2; exit 1; }
[[ -d "$CAS1" ]] || { echo "ERROR: no $CAS1" >&2; exit 1; }

staged=0; jats=0; noraw=0; badjpg=0
for dir in "$CAS1"/*/; do
  acc="$(basename "$dir")"
  [[ -f "${RAW_XML}/${acc}/${acc}.jats.json" ]] && jats=$((jats+1))
  src="${RAW_PDF}/${acc}"
  [[ -d "$src" ]] || { noraw=$((noraw+1)); continue; }
  dst="${CAS1}/${acc}/images"
  mkdir -p "$dst"
  find "$src" -maxdepth 1 -type f \( -iname '*.gif' -o -iname '*.tif' -o -iname '*.png' \) \
      -exec cp -n -t "$dst" {} +
  # cmykinverter aborts on a .jpg that is not a JPEG (pdfimages writes a few),
  # and an abort skips writing cmyk.index, so the JPEGs it had already
  # inverted get inverted again on the next run. Copy only real JPEGs.
  for f in "$src"/*.[jJ][pP][gG] "$src"/*.[jJ][pP][eE][gG]; do
    [[ -f "$f" && ! -e "$dst/${f##*/}" ]] || continue
    magic=""; read -r -N 2 magic < "$f" || true
    if [[ "$magic" == $'\xff\xd8' ]]; then cp -n "$f" "$dst/"; else badjpg=$((badjpg+1)); fi
  done
  cmykinverter "$dst" || echo "WARN: cmykinverter failed in $dst -- its JPEGs may be half-inverted;" \
      "empty the directory and re-run this script" >&2
  staged=$((staged+1))
done

files=$(find "$CAS1" -mindepth 3 -maxdepth 3 -path '*/images/*' -type f ! -name cmyk.index | wc -l)
echo "  ${CORPUS}: images staged for ${staged} accession(s) (${jats} JATS-built), ${files} files in tpcas-1;" \
     "${noraw} with no raw_files dir, ${badjpg} non-JPEG .jpg skipped"
