# Bug report: `--category`/`--expand-relationship-type` searches work against abd-textpresso but fail against textpresso.maizegdb.org

**Affects:** `https://textpresso.maizegdb.org/v1/textpresso/category_search` (and, by the
same mechanism, `.../v1/textpresso/annotate`)
**Does not affect:** `https://textpresso.maizegdb.org/v1/textpresso/api/search_documents`
and `.../api/available_corpora`, which both work correctly on `textpresso.maizegdb.org`

## Summary

The example command from
[TPC_SEARCH_TUTORIAL.md, section 4b](TPC_SEARCH_TUTORIAL.md#4b-expanding---category-to-include-related-ontology-terms):

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document \
  --category "seed (PO:0009010)" --expand-relationship-type part_of "development" \
  --count 3 --url "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api"
```

succeeds against `abd-textpresso.phoenixbioinformatics.org` (the host the tutorial's
`$BASE`/`$ANNOTATE`/`$CATSEARCH` variables point at) but **fails** with the same flags
if `--url` is pointed at `textpresso.maizegdb.org` instead:

```bash
python3 bin/tpc_search_combined.py -c MaizeTest100 --type document \
  --category "seed (PO:0009010)" --expand-relationship-type part_of "development" \
  --count 3 --url "https://textpresso.maizegdb.org/v1/textpresso/api"
```

```
API error 403: Forbidden
```

This is the same root cause already tracked in
[BUG_REPORT_textpresso_api_blocking.md](BUG_REPORT_textpresso_api_blocking.md): on
`textpresso.maizegdb.org`, `/annotate` and `/category_search` are unusable by any
client, while `/api/search_documents` and `/api/available_corpora` work fine. This
report adds a concrete, reproducible worked example (with `-v` traces) built directly
from the tutorial, since `--category`/`--expand-relationship-type` is the first
tutorial workflow that actually depends on the broken sidecar endpoint rather than the
working `/api/*` ones.

## Why `--category` triggers this specifically

`tpc_search_combined.py`'s `--category` + `--expand-relationship-type` handling calls
`category_search` (the `/v1/textpresso/category_search` sidecar, not `/api/*`) to
resolve `"seed (PO:0009010)"` plus its `part_of` descendants into a list of category
strings *before* it ever calls `search_documents`. If that lookup fails, the whole
command fails before a search is even attempted — this is why the tutorial's plain
keyword and `--type document` examples (sections 2–3, which never touch
`category_search`) are unaffected, but every `--category` example (section 4 onward)
is.

## Reproduction

### 1. The tutorial's own example command, run against each host

```
$ python3 bin/tpc_search_combined.py -c MaizeTest100 --type document \
    --category "seed (PO:0009010)" --expand-relationship-type part_of "development" \
    --count 3 --url "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api"

[1] Barrero C, Royo J, Grijota-Martinez C, Faye C, Paul W, Sanz S, Steinbiss HH, Hueros G. (2009). The promoter of ZmMRP-1, a maize transfer cell-specific transcriptional activator, is induced at solute exchange surfaces and responds to transport demands.. Planta. [10.1007_s00425-008-0823-0]

[2] Paulsmeyer MN, Juvik JA. (2023). Increasing aleurone layer number and pericarp yield for elevated nutrient content in maize.. G3 (Bethesda). [10.1093_g3journal_jkad085]

[3] Ren X, Pan Z, Zhao H, Zhao J, Cai M, Li J, Zhang Z, Qiu F. (2017). EMPTY PERICARP11 serves as a factor for splicing of mitochondrial nad1 intron and is required to ensure proper seed development in maize.. J Exp Bot. [10.1093_jxb_erx212]
```

```
$ python3 bin/tpc_search_combined.py -c MaizeTest100 --type document \
    --category "seed (PO:0009010)" --expand-relationship-type part_of "development" \
    --count 3 --url "https://textpresso.maizegdb.org/v1/textpresso/api"

API error 403: Forbidden
```

Same flags, same corpus, same category, only `--url` differs. The maizegdb host fails
outright.

### 2. `-v` trace: the `category_search` lookup that `--category`/`--expand-relationship-type` depends on

**abd-textpresso — succeeds (200 OK, real JSON, served by nginx from the app itself):**

```
$ curl -v -G "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/category_search" \
    --data-urlencode "q=seed (PO:0009010)" --data-urlencode "relationship_type=part_of"

* Host abd-textpresso.phoenixbioinformatics.org:80 was resolved.
*   Trying 52.40.183.244:80...
* Connected to abd-textpresso.phoenixbioinformatics.org (52.40.183.244) port 80
> GET /v1/textpresso/category_search?q=seed+%28PO%3a0009010%29&relationship_type=part_of HTTP/1.1
> Host: abd-textpresso.phoenixbioinformatics.org
> User-Agent: curl/8.11.0
> Accept: */*
>
< HTTP/1.1 200 OK
< Server: nginx/1.28.2
< Content-Type: application/json
< Content-Length: 4494
<
{"query": "seed (PO:0009010)", "matches": [{"id": "PO:0009010", "name": "seed", ... "relationship_types": ["part_of"], ...}, ... 18 more matches ...]}
* Connection #0 to host abd-textpresso.phoenixbioinformatics.org left intact
```

**textpresso.maizegdb.org — fails (503, Cloudflare's own page, not the app):**

```
$ curl -v -G "https://textpresso.maizegdb.org/v1/textpresso/category_search" \
    --data-urlencode "q=seed (PO:0009010)" --data-urlencode "relationship_type=part_of"

* Host textpresso.maizegdb.org:443 was resolved.
* Connected to textpresso.maizegdb.org (...) port 443
> GET /v1/textpresso/category_search?q=seed+%28PO%3a0009010%29&relationship_type=part_of HTTP/1.1
> Host: textpresso.maizegdb.org
> User-Agent: curl/8.11.0
> Accept: */*
>
< HTTP/1.1 503 Service Unavailable
< Date: Thu, 24 Sep 2026 18:28:50 GMT
< Content-Type: text/html; charset=UTF-8
< Connection: keep-alive
< Server: cloudflare
< Nel: {"report_to":"cf-nel","success_fraction":0.0,"max_age":604800}
< CF-RAY: a403d9e55874e0bb-DTW
<
<?xml version="1.0" encoding="iso-8859-1"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
 <head>
  <title>503 - Service Not Available</title>
 </head>
 <body>
  <h1>503 - Service Not Available</h1>
 </body>
</html>
* Connection #0 to host textpresso.maizegdb.org left intact
```

`Server: cloudflare` plus the generic static HTML body (no app-specific JSON, no
`X-Powered-By`/app headers) is Cloudflare's own "couldn't reach a healthy origin"
page for this path, not an error from `cas_annotate_server.py` itself — matching
case 4 of `BUG_REPORT_textpresso_api_blocking.md`. (The CLI's own error message
above said "403 Forbidden" rather than "503" because
`category_search()`/`expand_categories_by_relationship()` in `tpc_search_combined.py`
don't currently distinguish HTTP status codes in the message they raise — see
"Suggested follow-up" below.)

### 3. Control: `search_documents` (the `/api/*` path) succeeds on *both* hosts

To confirm this is specific to the `/annotate`+`/category_search` sidecar and not a
blanket problem with `textpresso.maizegdb.org`, the same category-filtered
`search_documents` POST (using category strings already resolved by hand) was run
against both hosts:

```
$ curl -v -X POST -H "Content-Type: application/json" \
    -d '{"query":{"type":"document","corpora":["MaizeTest100"],"keywords":"development",
         "categories":["seed (PO:0009010)","aleurone layer (PO:0005360)"]},"count":3}' \
    "http://abd-textpresso.phoenixbioinformatics.org/v1/textpresso/api/search_documents"

> POST /v1/textpresso/api/search_documents HTTP/1.1
> Host: abd-textpresso.phoenixbioinformatics.org
> Content-Type: application/json
> Content-Length: 156
>
< HTTP/1.1 200 OK
< Server: nginx/1.28.2
< Content-Type: application/json
< Content-Length: 1235
<
[{"year":"2023",...,"title":"Increasing aleurone layer number and pericarp yield for elevated nutrient content in maize.",...}, ...3 results...]
```

```
$ curl -v -X POST -H "Content-Type: application/json" \
    -d '{"query":{"type":"document","corpora":["MaizeTest100"],"keywords":"development",
         "categories":["seed (PO:0009010)","aleurone layer (PO:0005360)"]},"count":3}' \
    "https://textpresso.maizegdb.org/v1/textpresso/api/search_documents"

> POST /v1/textpresso/api/search_documents HTTP/1.1
> Host: textpresso.maizegdb.org
> Content-Type: application/json
> Content-Length: 156
>
< HTTP/1.1 200 OK
< Content-Type: application/json
< Content-Length: 1235
< Server: cloudflare
< CF-RAY: a403da19ec5be9b7-DTW
<
[{"year":"2023",...,"title":"Increasing aleurone layer number and pericarp yield for elevated nutrient content in maize.",...}, ...3 results, same 3 documents, slightly different scores...]
```

Both return the same 3 documents (scores differ slightly, presumably a different/older
index snapshot behind the maizegdb Cloudflare tunnel — not itself a bug this report is
about). The maizegdb request took noticeably longer (~6s vs ~1s) but did succeed. This
confirms `/api/*` is healthy on `textpresso.maizegdb.org`; only the `/annotate` and
`/category_search` sidecar is not.

## What this means for tutorial readers

Every `TPC_SEARCH_TUTORIAL.md` example that uses `--category`,
`--expand-relationship-type`, `--expand-ancestor-relationship-type`, `--annotate`, or
`--annotate-sentences` (sections 4 through 6, and 11) will fail today if run with
`--url` pointed at `textpresso.maizegdb.org`, even though the tutorial's own `$BASE`
(`abd-textpresso`) works. Plain keyword/metadata search (sections 2, 3, 7, 8, 9, 10,
12) is unaffected on either host.

## Requested action

Same as `BUG_REPORT_textpresso_api_blocking.md`:

1. Confirm whether `cas_annotate_server.py` (or an equivalent process serving
   `/annotate` and `/category_search`) is running and reachable behind the
   `textpresso.maizegdb.org` Cloudflare Tunnel/ingress config, the same way it is on
   `abd-textpresso.phoenixbioinformatics.org`.
2. Check the Cloudflare Tunnel/ingress routing for `textpresso.maizegdb.org` — confirm
   `/v1/textpresso/annotate` and `/v1/textpresso/category_search` route to a live
   backend the same way `/v1/textpresso/api/*` does on that zone.

## Suggested follow-up (this repo, not the server)

`tpc_search_combined.py`'s HTTP error handling currently surfaces every non-2xx
response as `API error <code>: <reason>` without including the response body or
distinguishing an app-level error from an edge/proxy failure (Cloudflare's 503 HTML
page vs. a real `{"error": ...}` JSON body from `cas_annotate_server.py`). Surfacing
the response body (or at least noting when `Server: cloudflare` and a non-JSON body
are present) would make failures like this one self-diagnosing from the CLI's own
error message, rather than requiring a manual `curl -v` to tell a WAF/tunnel problem
apart from a real application error.
