# Show figures for MaizeOpenAccess (2026-11-02)

For the machine serving `textpresso.maizegdb.org`. Today no MaizeOpenAccess
paper shows figures in the Textpresso Central viewer: every
`/tpc/images/MaizeOpenAccess/...` link returns 404. This fixes it on the
running server. **No image rebuild, re-index or restart is needed**, and search
results do not change.

## Why figures are missing

Two separate gaps, both from our `process_batch.sh`, not from your setup:

1. **No image files where the viewer looks.** The viewer serves
   `/tpc/images/<corpus>/<acc>/images/<file>` from `tpcas-2/<acc>/images`,
   a symlink to `tpcas-1/<acc>/images/`. Those folders are empty for all
   3,299 papers. `pdf2txtimg` extracted the images into
   `raw_files/pdf/MaizeOpenAccess/<acc>/`, but nothing copied them across;
   the upstream pipeline does that in `03pdf2cas4tai.sh`, followed by
   `cmykinverter`.
2. **The 1,624 papers built from JATS have no figure references at all.** Their
   CAS files come from the PMC article XML, which carries captions but not
   the image files. We now place each paper's PDF images in the JATS text: at
   the matching figure caption where one is found on the same PDF page, else
   where that page's text appears.

## 0. Variables used below

```bash
AGR=~/agr_textpresso                          # agr_textpresso clone
IMPL=~/maizegdb_textpresso_implementation     # this repo
C=agr-textpresso-textpresso-1                 # container name (docker ps)
```

## 1. Update this repo

```bash
cd $IMPL && git switch master && git pull
```

You need `sorghumbase_textpresso_implementation/scripts/stage_cas1_images.sh`
and `bin/add_jats_figures.py`. Both run inside the container through
`docker exec`, so nothing is installed.

## 2. Check that the extracted images are there

```bash
docker exec $C sh -c 'ls /data/textpresso/raw_files/pdf/MaizeOpenAccess/10.3390_plants14101438/ | grep -c img-'
# expect 14. If 0, raw_files on this machine has no pdf2txtimg output:
# stop and tell us.
docker exec $C df -h /data/textpresso   # step 3 needs ~12 GB free
```

## 3. Copy the images into tpcas-1

```bash
docker exec -i $C bash -s -- MaizeOpenAccess < $IMPL/sorghumbase_textpresso_implementation/scripts/stage_cas1_images.sh
# expect: images staged for 3299 accession(s) (1624 JATS-built), ~148,500 files
#         in tpcas-1; 0 with no raw_files dir, 2 non-JPEG .jpg skipped
```

It needs about **12 GB** free under the data directory: the images are copied,
not linked, because `cmykinverter` rewrites JPEGs in place and must not touch
the originals. On our laptop a full pass took about 25 minutes. It is safe to re-run: it never overwrites a
file, and `cmykinverter` records the JPEGs it has already corrected. The two
skipped `.jpg` files are not really JPEGs, and `cmykinverter` aborts on them.
If a `WARN: cmykinverter failed in <dir>` line appears, empty that `<dir>` and
run the script again.

PDF-built papers show figures as soon as this step finishes.

## 4. Add the figure annotations to the JATS-built papers

```bash
docker exec -i $C python3 - -c MaizeOpenAccess \
    --cas /data/textpresso/tpcas-1/MaizeOpenAccess \
    --cas /data/textpresso/tpcas-2/MaizeOpenAccess < $IMPL/bin/add_jats_figures.py
# expect: MaizeOpenAccess: 1624 JATS papers, 1607 with PDF images; 71112 images
#         placed by caption 22832 (32%), page 38081 (54%), near 10199 (14%), end 0 (0%)
#         updated 3214
```

Took about 30 minutes here; one paper (`10.1093_gbe_evt109`, a 75 MB CAS)
alone takes several minutes and ~4 GB of memory. It edits only CAS files whose `rawsource` is `jats`, and
skips any file that already has figures, so a re-run changes nothing. A
`text-mismatch` line means that paper's `jats.json` changed after its CAS was
built; that paper is left untouched.

## 5. Verify

```bash
for u in \
  MaizeOpenAccess/10.1002_1873-3468.14704/images/10.1002_1873-3468.14704.00001.img-000.png \
  MaizeOpenAccess/10.3390_plants14101438/images/10.3390_plants14101438.00003.img-000.png ; do
  curl -s -o /dev/null -w "%{http_code} $u\n" "https://textpresso.maizegdb.org/tpc/images/$u?nocache=$RANDOM"
done
# expect 200 for both (the first is a PDF-built paper, the second JATS-built)
```

The `?nocache=` matters: Cloudflare caches 404 responses for 30 minutes, so a
link that failed before the fix can keep failing for a while without it.

Then open either paper in the Textpresso Central viewer: figures appear inline
under "-- Image on page N --". For `10.3390_plants14101438`, Figure 1 appears
right above its caption ("Figure 1 Bioinformatics analysis and
characterization of ZmHPAT2").

## Undo

```bash
docker exec -i $C python3 - -c MaizeOpenAccess --remove \
    --cas /data/textpresso/tpcas-1/MaizeOpenAccess \
    --cas /data/textpresso/tpcas-2/MaizeOpenAccess < $IMPL/bin/add_jats_figures.py
```

This strips the figure annotations again, restoring each file byte for byte.
The copied image files can stay; nothing links to them without the
annotations.

## Next full rebuild

Nothing extra is needed. `process_batch.sh` (this repo's `master`) copies
the images and places figures for JATS papers itself. The JATS tokenizer change
is on `agr_textpresso` `main` (commit `d05c785`). With
it, `TjTokenizer` writes the figure annotations at tokenize time, from an
`images` list that `process_batch.sh` adds to each `jats.json`. It is picked up
by the normal `docker compose build textpresso`; until then `process_batch.sh`
adds the annotations to CAS-1 after tokenizing, so either image works.
