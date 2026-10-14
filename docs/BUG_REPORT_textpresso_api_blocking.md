# Bug report: textpresso.maizegdb.org blocks non-browser clients on 3 of 4 API endpoints

**Reported by:** Laura Tibbs-Cortes (ltibbs@iastate.edu)
**Date:** 2026-09-23
**Affects:** `https://textpresso.maizegdb.org/v1/textpresso/api/*`, `.../v1/textpresso/annotate`, `.../v1/textpresso/category_search`

## Summary

All four Textpresso REST endpoints on `textpresso.maizegdb.org` are edge-blocked (by Cloudflare, before reaching the origin) for any client that isn't a real browser, based on the request's declared `User-Agent`. This breaks:

- The repo's own CLI tool, `bin/tpc_search_combined.py`, run standalone (e.g. `python3 bin/tpc_search_combined.py --list-corpora` currently fails with `API error 403: Forbidden`).
- Any non-browser client (our own GUI backend included) trying to reach `/annotate` or `/category_search` at all — those two return either 403 or 503 depending on `User-Agent`, but never succeed, regardless of what UA string is sent.

`search_documents`/`available_corpora` are usable today with a workaround (send a browser-like `User-Agent`). `annotate` and `category_search` are **not usable at all right now**, by any client we've tried.

## Reproduction

### 1. `/api/available_corpora` with a normal browser-like UA → works

```
$ curl -v "https://textpresso.maizegdb.org/v1/textpresso/api/available_corpora"
> GET /v1/textpresso/api/available_corpora HTTP/1.1
> Host: textpresso.maizegdb.org
> User-Agent: curl/8.11.0
> Accept: */*
>
< HTTP/1.1 200 OK
< Server: cloudflare
< CF-RAY: a3fc2797ea60ae82-ORD
<
["MaizeOA","MaizeTest","MaizeTest100"]
```

### 2. `/api/available_corpora` with Python's default `urllib` UA → 403, Cloudflare WAF block

```
$ curl -v -H "User-Agent: Python-urllib/3.13" "https://textpresso.maizegdb.org/v1/textpresso/api/available_corpora"
> GET /v1/textpresso/api/available_corpora HTTP/1.1
> Host: textpresso.maizegdb.org
> Accept: */*
> User-Agent: Python-urllib/3.13
>
< HTTP/1.1 403 Forbidden
< Content-Type: text/plain; charset=UTF-8
< Content-Length: 17
< Server: cloudflare
< CF-RAY: a3fc27bd2dbec474-ORD
<
error code: 1010
```

Cloudflare error 1010 is a WAF/"browser integrity check" style block (see Cloudflare's own docs on error 1010: "The owner of this website has banned your access based on your browser's signature"). This confirms the block happens at Cloudflare's edge, before any request reaches MaizeGDB's origin server — the origin never sees the blocked requests.

**This specific case is workaroundable client-side** (send a browser-like `User-Agent`), which is what our GUI backend does. It still means the CLI tool (`bin/tpc_search_combined.py`) and any other plain Python client fails out of the box.

### 3. `/category_search` with a non-browser UA → same 403/error 1010

```
$ curl -v -H "User-Agent: Python-urllib/3.13" -G "https://textpresso.maizegdb.org/v1/textpresso/category_search" \
    --data-urlencode "q=seed" --data-urlencode "limit=3"
> GET /v1/textpresso/category_search?q=seed&limit=3 HTTP/1.1
> User-Agent: Python-urllib/3.13
>
< HTTP/1.1 403 Forbidden
< CF-RAY: a3fc27becff46090-ORD
<
error code: 1010
```

### 4. `/category_search` with a browser-like UA → 503, but NOT from the app

```
$ curl -v -H "User-Agent: Mozilla/5.0 (compatible; TextpressoMaizeGUI/1.0)" -G "https://textpresso.maizegdb.org/v1/textpresso/category_search" \
    --data-urlencode "q=seed" --data-urlencode "limit=3"
> GET /v1/textpresso/category_search?q=seed&limit=3 HTTP/1.1
> User-Agent: Mozilla/5.0 (compatible; TextpressoMaizeGUI/1.0)
>
< HTTP/1.1 503 Service Unavailable
< Content-Type: text/html; charset=UTF-8
< Server: cloudflare
< CF-RAY: a3fc27c0488ce811-ORD
<
<?xml version="1.0" encoding="iso-8859-1"?>
<!DOCTYPE html ...>
<html ...>
 <head>
  <title>503 - Service Not Available</title>
 </head>
 <body>
  <h1>503 - Service Not Available</h1>
 </body>
</html>
```

This is Cloudflare's own generic error page (`Server: cloudflare`, static boilerplate HTML, no app-specific JSON body) — not an error message from the Textpresso application itself. That shape means Cloudflare could not reach a healthy origin/tunnel for this specific path, as distinct from case 2/3/5's WAF block (error 1010) which never leaves the edge at all.

### 5 & 6. `/annotate` — identical pattern to `/category_search`

```
$ curl -v -H "User-Agent: Python-urllib/3.13" -G "https://textpresso.maizegdb.org/v1/textpresso/annotate" \
    --data-urlencode "identifier=MaizeTest100/10.1038_srep35479/10.1038_srep35479.tpcas"
< HTTP/1.1 403 Forbidden
< CF-RAY: a3fc27c208c852dc-ORD
<
error code: 1010

$ curl -v -H "User-Agent: Mozilla/5.0 (compatible; TextpressoMaizeGUI/1.0)" -G "https://textpresso.maizegdb.org/v1/textpresso/annotate" \
    --data-urlencode "identifier=MaizeTest100/10.1038_srep35479/10.1038_srep35479.tpcas"
< HTTP/1.1 503 Service Unavailable
< CF-RAY: a3fc27c339640059-ORD
<
<title>503 - Service Not Available</title>
```

## What this means

- **`/api/*` (search_documents, available_corpora):** blocked only for non-browser `User-Agent` strings. A WAF/Bot-Fight-Mode-style rule on the Cloudflare zone. Workaroundable client-side, but breaks the repo's own CLI tool and any script/server client out of the box.
- **`/annotate` and `/category_search`:** unusable by *any* client we've tried, browser-UA or not. The 403 case is the same edge WAF block as `/api/*`; the 503 case (once past the WAF, with a browser UA) is Cloudflare's own "couldn't reach the origin" page, not an application error — meaning either the backend process for these two routes (per repo docs, `cas_annotate_server.py`) is down, or Cloudflare's tunnel/routing config for these specific paths is misconfigured/pointing at a dead upstream.

## Requested action

1. **Check whether `cas_annotate_server.py` (or whatever currently serves `/annotate` and `/category_search`) is running and healthy.** The 503 responses above indicate Cloudflare cannot reach a working origin for these two paths specifically, even though `/api/*` on the same zone works fine once the WAF allows the request through.
2. **Check the Cloudflare Tunnel / ingress routing config** for `textpresso.maizegdb.org` — confirm `/v1/textpresso/annotate` and `/v1/textpresso/category_search` route to a live backend the same way `/v1/textpresso/api/*` does.
3. **Review the WAF/Bot Fight Mode rule** blocking non-browser `User-Agent` strings on this zone (error 1010 above) — consider relaxing it or allowlisting known automation, since it currently breaks the project's own command-line tooling with no way to opt out server-side.

Happy to provide additional repro detail, `CF-RAY` IDs for correlating with Cloudflare logs, or test against a fix.
