#!/usr/bin/env python3
"""Build Textpresso match dictionaries from the maize ontology collection.

Implements the six decisions recorded in ONTOLOGY_BUILD.md. Reads the
`maize-ontology-collection/` tree (as published by refresh_ontologies.py) and
writes, into --outdir:

    match_dictionary.csv    one row per matchable string, with its tier
    <channel>_annot.obo     per-channel OBO carrying only matchable strings
    build_report.json       counts and drop accounting for every channel

Nothing here runs at annotation time. Tiers are decided once, here, and frozen
into match_dictionary.csv so the runtime gate (annotation_tiers.py) needs no
third-party imports. `wordfreq` is a build-time dependency only.

    python3 build_match_dictionary.py --collection maize-ontology-collection \
        --chebi-full chebi.obo --outdir build/

ChEBI is the one ontology NOT read from the collection's published file:
chebi_lite.obo carries zero synonyms across its 218,822 terms, so text matching
needs the full release (--chebi-full, 271 MB, from
ftp.ebi.ac.uk/pub/databases/chebi/ontology/chebi.obo). --chebi-skip builds
without it.
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

# ---------------------------------------------------------------- decisions

# Decision 1 -- ChEBI is scoped by agronomic role, not by star rating. (The
# 3:STAR subset is a curation-status flag, 62,119 terms, and says nothing about
# relevance to maize.)
CHEBI_ROLE_ROOTS = {
    "CHEBI:76924": "plant metabolite",
    "CHEBI:33286": "agrochemical",
    "CHEBI:26155": "plant growth regulator",
    "CHEBI:37848": "plant hormone",
    "CHEBI:33284": "nutrient",
    "CHEBI:25442": "mycotoxin",
    "CHEBI:27027": "micronutrient",
    "CHEBI:33937": "macronutrient",
    "CHEBI:24527": "herbicide",
    "CHEBI:24127": "fungicide",
    "CHEBI:24852": "insecticide",
    "CHEBI:33287": "fertilizer",
}
# ChEBI's has_role annotation is incomplete exactly where maize cares most:
# fumonisin B1, aflatoxin B1 and zearalenone carry no `has_role mycotoxin`
# (FB1 has "metabolite" and "carcinogenic agent"), so the ear-rot toxins fall
# outside the role closure. These families are added by is_a subtree.
CHEBI_FAMILY_SEEDS = {
    "CHEBI:38224": "fumonisin",
    "CHEBI:22271": "aflatoxin",
    "CHEBI:10106": "zearalenone",
    "CHEBI:55517": "trichothecene",
    "CHEBI:222613": "moniliformin",
    "CHEBI:3000": "beauvericin",
}
HAS_ROLE = "RO:0000087"          # ChEBI writes has_role as a bare RO id

# Decision 2 -- GO keeps every live term, gocheck_do_not_annotate included
# (2026-10-22). Excluding that subset removed 1,314 strings no other term
# carries -- 95,277 MaizeOpenAccess mentions, among them 'cell wall' (5,050),
# 'cell death', 'embryogenesis', 'cell cycle' and the meiotic stages. With
# tpso at depth 50 a do-not-annotate class is a category only when it is the
# matched term, and hygiene's ancestor collapse drops it whenever a more
# specific term covers the same span. The subset line is still carried onto
# the stanza, so anything that wants to treat these terms differently can.

# Decision 3 -- match dictionaries are built from native stanzas only; an
# imported stanza resolves against the ontology that owns it.
CHANNELS = [
    # (channel, path relative to the collection, native id prefixes)
    ("PO",        "ontologies/po.obo",       ("PO",)),
    ("TO",        "ontologies/to.obo",       ("TO",)),
    ("PECO",      "ontologies/peco.obo",     ("PECO",)),
    ("PSO",       "ontologies/pso.obo",      ("PSO",)),
    ("CO_322",    "ontologies/co_322.obo",   ("CO_322",)),
    ("PATO",      "ontologies/pato.obo",     ("PATO",)),
    ("GO",        "ontologies/go-basic.obo", ("GO",)),
    ("SO",        "ontologies/so.obo",       ("SO",)),
    ("ECO",       "ontologies/eco.obo",      ("ECO",)),
    # Species get their own channel instead of riding in as PSO imports, so a
    # cross-species search returns species AS species. 2,650 of these ids are
    # the same ones PSO imports for its stress logical definitions.
    ("NCBITaxon", "external/ext_ncbitaxon_maize_subset.obo", ("NCBITaxon",)),
]

# Decision 5 -- RELATED synonyms are in, but a form explicitly scoped to another
# species ("05-booting in wheat") is a false positive by construction.
MAIZE_SPECIES = ("maize", "zea mays", "zea", "corn")
SPECIES_SUFFIX = re.compile(r"\bin ([a-z][a-z .\-]{2,30})$", re.I)

# Decision 4 -- four-tier match-string policy.
TIER_BLOCK = "T3"
TIER_CONTEXT = "T2"
TIER_CASE = "T1"
TIER_SAFE = "T0"
TIER_DEFINED = "TD"

# Decision 4a -- abbreviations accepted only where the paper spells them out
# (2026-10-22). Each is <= 2 characters, so the tier rule would block it, and
# each has a dominant sense in maize papers the source ontologies do not give
# it: no ontology carries GA/JA/SA/BR/CK/ET as the hormone, and in GO 'GA' is
# an acylase and 'CK' a kinase. A TD string matches in its stored case and is
# kept only when one of its long-form patterns occurs in the paper body
# (references and acknowledgments excluded) -- see annotation_tiers. That is
# what separates them from their other senses in MaizeOpenAccess: CK is
# defined as the control check in 35 papers and as cytokinin in 27; JA, SA
# and GA are often author initials; BR is brace root in 7 papers.
#
# (channel, term id) -> {abbreviation: long-form regexes}. A pair the source
# ontology lacks is added as a RELATED synonym; one it already has (ER) is
# only re-tiered. Long forms are searched case-insensitively.
DEFINED_ABBREVIATIONS = {
    ("ChEBI", "CHEBI:24250"): {"GA": (r"\bgibberell",)},      # gibberellin
    ("ChEBI", "CHEBI:18292"): {"JA": (r"\bjasmon",)},         # jasmonic acid
    ("ChEBI", "CHEBI:16914"): {"SA": (r"\bsalicyl",)},        # salicylic acid
    ("ChEBI", "CHEBI:22921"): {"BR": (r"\bbrassino",)},       # brassinosteroid
    ("ChEBI", "CHEBI:23530"): {"CK": (r"\bcytokinin",)},      # cytokinin
    # word-bounded: 'polyethylene glycol' and 'ethylenediamine...' are common
    ("ChEBI", "CHEBI:18153"): {"ET": (r"\bethylene\b", r"\bethene\b")},  # ethene
    ("GO", "GO:0005783"): {"ER": (r"\bendoplasmic reticul",)},
    # E3 is BROAD on GO:0061630 upstream and RELATED only on broader terms
    ("GO", "GO:0061630"): {"E3": (r"\bubiquitin",)},
}

# Common English words that, in a maize paper, are almost always the plant
# structure: frequency alone would put them in T2 (context required), and the
# runtime gate then dropped 14,771 'leaf', 7,066 'root' and 6,914 'seed'
# mentions over MaizeOpenAccess (2026-10-21). They match freely, but only in
# the PO channel -- 'root' as NCBITaxon:1 stays T2. Genuine homonyms (style,
# coma, keel, bulb, bark) are deliberately NOT listed.
CHANNEL_FREE_FORMS = {
    "PO": frozenset({"leaf", "root", "seed", "stem", "silk", "bud", "ear", "kernel",
                     "husk", "cob", "tassel"}),
}

# PO, TO and PECO write the synonym scope -- and for PO the language -- into
# the quoted string itself: synonym: "ear (related)" RELATED [],
# "oreja de la inflorescencia (Spanish, exact)" EXACT Spanish [...]. Read
# literally, such a string can never occur in text: 1,653 of 1,655 TO synonyms
# and 2,668 of 5,605 PO synonyms (2026-10 releases), including PO's 'ear',
# 'kernel', 'husk' and 'leaf blade'. The suffix is stripped; a string tagged
# with a language other than English is dropped, since the corpus is English.
_SCOPE_IN_TEXT = re.compile(
    r"\s*\((?:([A-Za-z][A-Za-z ]*?),\s*)?(exact|related|narrow|broad)\)\s*$", re.I)


def clean_synonym_text(text):
    """-> (text without an embedded '(scope)' suffix, language or None)."""
    m = _SCOPE_IN_TEXT.search(text)
    if not m:
        return text, None
    lang = (m.group(1) or "").strip()
    return text[:m.start()].rstrip(), (lang if lang and lang.lower() != "english" else None)


# Opt-in (--derive-trait-stems): TO names carry a " trait" suffix authors
# never write ("flowering time trait"). Deriving the multi-word stem as a
# RELATED-scope string raised curator-trait lexicon coverage from 16.8% to
# 21.3% of occurrences in MaizeOpenAccess. It adds strings no ontology
# contains, so it is a policy choice, not a parsing fix.
_TRAIT_SUFFIX = re.compile(r"\s+trait$")


def abbreviation_shaped(s):
    """DNA, mRNA, ABA, QTL -- one short token carrying >=2 capitals."""
    return len(s.split()) == 1 and len(s) <= 6 and sum(c.isupper() for c in s) >= 2


def make_tier_fn(zipf):
    """Return tier(s) -> 'T0'..'T3'. `zipf` is wordfreq.zipf_frequency.

    Abbreviation shape is tested BEFORE frequency, otherwise DNA (Zipf 5.2 as a
    lowercase word) lands in T2 and SO loses its most common term.
    """

    def tier(s):
        tokens = s.split()
        z = zipf(s.lower(), "en") if len(tokens) == 1 else 0.0
        if len(s) <= 2:
            return TIER_BLOCK
        if abbreviation_shaped(s):
            return TIER_CASE
        if len(tokens) == 1 and z >= 5.0:
            return TIER_CONTEXT
        if len(s) <= 4 and z >= 3.0:
            return TIER_CONTEXT
        if len(tokens) == 1 and 2.5 <= z <= 3.5:
            return TIER_CASE
        return TIER_SAFE

    return tier


# ------------------------------------------------------------------ parsing

_SYN = re.compile(r'^synonym: "((?:[^"\\]|\\.)*)" (EXACT|RELATED|BROAD|NARROW)', re.M)
_OBO_ESCAPE = re.compile(r"\\(.)")
_OBO_ESCAPES = {"n": "\n", "t": "\t", "W": " "}


def obo_unescape(s):
    """OBO 1.2 escapes -> text: ChEBI writes "\\{(Z)-pent-2-en-1-yl\\}", which
    must reach match_dictionary.csv (and the matcher) as "{...}"."""
    return _OBO_ESCAPE.sub(lambda m: _OBO_ESCAPES.get(m.group(1), m.group(1)), s)


def obo_quoted(s):
    """Text -> the inside of an OBO quoted value."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def obo_unquoted(s):
    """Text -> an OBO name: value ({ starts trailing modifiers, ! a comment)."""
    return re.sub(r'([\\{}!])', r"\\\1", s)


def parse_obo(path, want_roles=False):
    """Minimal OBO term reader. Returns a list of dicts in file order."""
    with open(path, encoding="utf-8") as fh:
        blob = fh.read()
    out = []
    for block in blob.split("\n[Term]\n")[1:]:
        # A trailing [Typedef]/[Instance] section rides along on the last term;
        # cut at the next stanza header or its relationship lines leak in.
        cut = re.search(r"^\[", block, re.M)
        if cut:
            block = block[:cut.start()]
        m = re.search(r"^id: (\S+)", block, re.M)
        if not m:
            continue
        name = re.search(r"^name: (.+)$", block, re.M)
        rec = {
            "id": m.group(1),
            "name": obo_unescape(name.group(1).strip()) if name else None,
            "synonyms": [(obo_unescape(s), sc) for s, sc in _SYN.findall(block)],
            "subsets": re.findall(r"^subset: (\S+)", block, re.M),
            "is_a": re.findall(r"^is_a: (\S+)", block, re.M),
            "obsolete": "is_obsolete: true" in block,
            "replaced_by": re.findall(r"^replaced_by: (\S+)", block, re.M),
            "consider": re.findall(r"^consider: (\S+)", block, re.M),
        }
        if want_roles:
            rec["roles"] = re.findall(r"^relationship: %s (\S+)" % HAS_ROLE, block, re.M)
        out.append(rec)
    return out


def obo_header_version(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("data-version:"):
                return line.split(":", 1)[1].strip()
            if line.startswith("[Term]"):
                break
    return "unknown"


def obo_subsetdefs(path):
    """-> ["goslim_plant \"Plant GO slim\"", ...], the header's subsetdef values."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("[Term]"):
                break
            if line.startswith("subsetdef:"):
                out.append(line.split(":", 1)[1].strip())
    return out


# -------------------------------------------------------------- ChEBI scope


def chebi_subset(chebi_terms, referenced_ids, report):
    """Referenced closure + agronomic roles + mycotoxin families (decision 1)."""
    live = {t["id"]: t for t in chebi_terms if not t["obsolete"]}
    children = defaultdict(set)
    for tid, t in live.items():
        for parent in t["is_a"]:
            children[parent].add(tid)

    def descendants(seeds):
        seen, stack = set(), list(seeds)
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(children.get(node, ()))
        return seen & set(live)

    def ancestors(seeds):
        seen, stack = set(), list(seeds)
        while stack:
            for parent in live.get(stack.pop(), {}).get("is_a", []):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return seen & set(live)

    referenced = {i for i in referenced_ids if i in live}
    reason = {i: "referenced_by_collection" for i in referenced}
    keep = set(referenced) | ancestors(referenced)
    for tid in keep:
        reason.setdefault(tid, "is_a_ancestor")

    for root, label in CHEBI_ROLE_ROOTS.items():
        subtree = descendants({root})
        bearers = {tid for tid, t in live.items()
                   if any(r in subtree for r in t.get("roles", ()))}
        for tid in bearers:
            reason.setdefault(tid, "role:" + label)
        report["chebi_role_bearers"][label] = len(bearers)
        keep |= bearers

    for seed, label in CHEBI_FAMILY_SEEDS.items():
        if seed not in live:
            continue
        fam = descendants({seed})
        report["chebi_family_added"][label] = len(fam - keep)
        for tid in fam:
            reason.setdefault(tid, "family:" + label)
        keep |= fam

    return keep, reason


def referenced_chebi_ids(collection):
    """Every CHEBI id the other collection files mention."""
    ids = set()
    for sub in ("ontologies", "external"):
        d = os.path.join(collection, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".obo") or fn == "chebi_lite.obo":
                continue
            with open(os.path.join(d, fn), encoding="utf-8") as fh:
                ids |= set(re.findall(r"CHEBI:\d+", fh.read()))
    return ids


# ------------------------------------------------------------------- build


def species_scope(text):
    """'05-booting in wheat' -> 'wheat'; None when unscoped."""
    m = SPECIES_SUFFIX.search(text)
    return m.group(1).strip().lower() if m else None


def strings_for(term, derive_trait_stems=False):
    """Yield (string, kind): primary label, then EXACT, then RELATED synonyms,
    then (opt-in) derived trait stems. Synonyms are cleaned of an embedded
    scope suffix; a non-English one is yielded with kind 'non_english', and an
    abbreviation that only became matchable by the cleaning with kind 'code',
    so the caller can count the drop."""
    if term["name"]:
        yield term["name"], "label"
    for want, kind in (("EXACT", "exact"), ("RELATED", "related")):
        for syn, scope in term["synonyms"]:
            if scope == want:
                text, lang = clean_synonym_text(syn)
                if lang:
                    kind_out = "non_english"
                elif text != syn and abbreviation_shaped(text):
                    # only live because the suffix was stripped: a database
                    # trait code (PNWT, LFLG, CANTMP), not something authors
                    # write -- and some collide with real text (KUP, LAD)
                    kind_out = "code"
                else:
                    kind_out = kind
                yield text, kind_out
    if derive_trait_stems and term["name"] and _TRAIT_SUFFIX.search(term["name"]):
        stem = _TRAIT_SUFFIX.sub("", term["name"])
        if len(stem.split()) >= 2:
            yield stem, "derived"


def build(args):
    try:
        from wordfreq import zipf_frequency
    except ImportError:
        sys.exit("build-time dependency missing: pip install wordfreq")
    tier_of = make_tier_fn(zipf_frequency)

    report = {
        "collection": os.path.abspath(args.collection),
        "channels": {},
        "drops": Counter(),
        "chebi_role_bearers": {},
        "chebi_family_added": {},
        "versions": {},
    }
    rows = []
    annot_terms = defaultdict(list)
    sources = {}     # channel -> ({id: parsed term}, header subsetdefs), for write_outputs

    channels = list(CHANNELS)
    if not args.chebi_skip:
        channels.append(("ChEBI", args.chebi_full, ("CHEBI",)))

    for channel, relpath, prefixes in channels:
        path = relpath if channel == "ChEBI" else os.path.join(args.collection, relpath)
        if not os.path.exists(path):
            sys.exit("missing input for channel %s: %s" % (channel, path))
        terms = parse_obo(path, want_roles=(channel == "ChEBI"))
        report["versions"][channel] = obo_header_version(path)
        sources[channel] = ({t["id"]: t for t in terms}, obo_subsetdefs(path))

        keep_ids, scope_reason = None, {}
        if channel == "ChEBI":
            keep_ids, scope_reason = chebi_subset(
                terms, referenced_chebi_ids(args.collection), report)

        stats = Counter()
        seen_strings = set()
        for term in terms:
            if term["id"].split(":")[0] not in prefixes:
                stats["skipped_imported"] += 1
                report["drops"]["imported_stanza"] += 1
                continue
            if term["obsolete"]:
                stats["obsolete_retained"] += 1          # decision 6
                annot_terms[channel].append((term, []))
                continue
            curated = DEFINED_ABBREVIATIONS.get((channel, term["id"]), {})
            if keep_ids is not None and term["id"] not in keep_ids and not curated:
                stats["skipped_out_of_scope"] += 1
                report["drops"]["chebi_out_of_scope"] += 1
                continue

            kept_strings = []
            derive = getattr(args, "derive_trait_stems", False) and channel == "TO"
            candidates = list(strings_for(term, derive))
            have = {t.strip() for t, _ in candidates}
            candidates += [(abbr, "curated") for abbr in curated if abbr not in have]
            for text, kind in candidates:
                text = text.strip()
                if not text:
                    continue
                if kind == "non_english":
                    stats["dropped_non_english"] += 1
                    report["drops"]["non_english_synonym"] += 1
                    continue
                if kind == "code":
                    stats["dropped_trait_code"] += 1
                    report["drops"]["suffixed_abbreviation_code"] += 1
                    continue
                scope = species_scope(text) if kind in ("related", "derived") else None
                if scope and not any(sp in scope for sp in MAIZE_SPECIES):
                    stats["dropped_species_scoped"] += 1
                    report["drops"]["non_maize_species_scoped"] += 1
                    continue
                if text in curated:
                    tier = TIER_DEFINED
                elif text.lower() in CHANNEL_FREE_FORMS.get(channel, ()):
                    tier = TIER_SAFE
                else:
                    tier = tier_of(text)
                if tier == TIER_BLOCK:
                    stats["dropped_tier3"] += 1
                    report["drops"]["tier_T3_blocked"] += 1
                    continue
                key = (channel, term["id"], text.lower(), kind)
                if key in seen_strings:
                    continue
                seen_strings.add(key)
                tokens = text.split()
                rows.append({
                    "match_string": text,
                    "tier": tier,
                    "channel": channel,
                    "term_id": term["id"],
                    "term_label": term["name"] or "",
                    "string_kind": kind,
                    "inclusion_reason": ("curated_abbreviation" if kind == "curated"
                                         else scope_reason.get(term["id"], "native_term")),
                    "species_scope": scope or "",
                    "n_chars": len(text),
                    "n_tokens": len(tokens),
                    "zipf_freq": ("%.2f" % zipf_frequency(text.lower(), "en"))
                                 if len(tokens) == 1 else "",
                    "long_forms": json.dumps(list(curated[text])) if tier == TIER_DEFINED
                                  else "",
                })
                kept_strings.append((text, kind, tier))
                stats["strings_" + tier] += 1
            stats["terms_matchable" if kept_strings else "terms_unmatchable"] += 1
            annot_terms[channel].append((term, kept_strings))
        report["channels"][channel] = dict(stats)

    emitted = {(r["channel"], r["term_id"], r["match_string"])
               for r in rows if r["tier"] == TIER_DEFINED}
    missing = sorted("%s %s %s" % (ch, tid, abbr)
                     for (ch, tid), forms in DEFINED_ABBREVIATIONS.items()
                     for abbr in forms if (ch, tid, abbr) not in emitted
                     and (ch != "ChEBI" or not args.chebi_skip))
    if missing:
        # a renamed, obsoleted or merged term would otherwise lose its
        # abbreviation without a trace
        print("WARNING: curated abbreviations not emitted: " + "; ".join(missing),
              file=sys.stderr)
    report["curated_abbreviations_missing"] = missing

    write_outputs(args, rows, annot_terms, report, sources)
    return report


def write_outputs(args, rows, annot_terms, report, sources=None):
    """sources: channel -> ({id: parsed term}, subsetdefs) from the input OBO.
    Source subsets (goslim_plant, ...) are carried onto every stanza and stub,
    because tpso's ontology.conf can restrict a channel's categories to them;
    without them a restricted channel collapses to its root. Stubs carry the
    source label as property_value stub_label, which tpso uses for the
    category name (a stub has no name: line, so it adds no match string)."""
    sources = sources or {}
    os.makedirs(args.outdir, exist_ok=True)
    cols = ["match_string", "tier", "channel", "term_id", "term_label", "string_kind",
            "inclusion_reason", "species_scope", "n_chars", "n_tokens", "zipf_freq",
            "long_forms"]
    rows.sort(key=lambda r: (r["channel"], r["term_id"], r["string_kind"], r["match_string"]))
    with open(os.path.join(args.outdir, "match_dictionary.csv"), "w",
              newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)

    for channel, items in sorted(annot_terms.items()):
        out = os.path.join(args.outdir, "%s_annot.obo" % channel.lower())
        written = {term["id"] for term, _kept in items}
        # Parents pruned by scoping (GO's do-not-annotate grouping classes,
        # out-of-scope ChEBI, imported stanzas) are re-emitted as nameless
        # stubs: ancestor expansion keeps working, and a stub contributes no
        # match strings -- the same shape TO and PECO already ship upstream.
        by_id, subsetdefs = sources.get(channel, ({}, []))
        # Stubs keep their own source is_a (closed transitively), so the graph
        # above a pruned class still reaches the root and depths stay right.
        dangling, todo = set(), {p for term, _kept in items for p in term["is_a"]} - written
        while todo:
            stub = todo.pop()
            dangling.add(stub)
            todo |= set(by_id.get(stub, {}).get("is_a", ())) - written - dangling
        dangling = sorted(dangling)
        tier_names = sorted({"tier_%s" % t for _term, kept in items for _, _, t in kept})
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("format-version: 1.2\n")
            fh.write("data-version: %s/annotation-build\n"
                     % report["versions"].get(channel, "unknown"))
            for sd in subsetdefs:
                fh.write("subsetdef: %s\n" % sd)
            for tn in tier_names:
                fh.write('subsetdef: %s "match-string tier %s (annotation build)"\n'
                         % (tn, tn[len("tier_"):]))
            fh.write("ontology: %s\n" % channel.lower())
            fh.write("remark: matchable strings only; per-term tiers in subset tier_T0..T2\n")
            fh.write("remark: obsolete stanzas retained for id resolution with their label\n")
            fh.write("remark: moved to property_value retired_label, so no matcher reads it\n\n")
            for term, kept in items:
                fh.write("[Term]\n")
                fh.write("id: %s\n" % term["id"])
                if term["obsolete"]:
                    fh.write("is_obsolete: true\n")
                    if term["name"]:
                        fh.write('property_value: retired_label "%s" xsd:string\n'
                                 % obo_quoted(term["name"]))
                    for rb in term["replaced_by"]:
                        fh.write("replaced_by: %s\n" % rb)
                    for c in term["consider"]:
                        fh.write("consider: %s\n" % c)
                    fh.write("\n")
                    continue
                label = next((t for t, k, _ in kept if k == "label"), None)
                if label:
                    fh.write("name: %s\n" % obo_unquoted(label))
                elif term["name"]:
                    # the label is not a match string, but tpso and
                    # category_index still need a category name: stub_label
                    fh.write('property_value: unmatchable_label "%s" xsd:string\n'
                             % obo_quoted(term["name"]))
                    fh.write('property_value: stub_label "%s" xsd:string\n'
                             % obo_quoted(term["name"]))
                for text, kind, _tier in kept:
                    if kind != "label":
                        # a derived trait stem or curated abbreviation is not
                        # an ontology synonym; it rides the RELATED channel so
                        # it is gated like one
                        scope = "RELATED" if kind in ("derived", "curated") else kind.upper()
                        fh.write('synonym: "%s" %s []\n'
                                 % (obo_quoted(text), scope))
                for tier in sorted({t for _, _, t in kept}):
                    fh.write("subset: tier_%s\n" % tier)
                for subset in term["subsets"]:
                    fh.write("subset: %s\n" % subset)
                for parent in term["is_a"]:
                    fh.write("is_a: %s\n" % parent)
                fh.write("\n")
            for stub in dangling:
                src = by_id.get(stub)
                fh.write("[Term]\n")
                fh.write("id: %s\n" % stub)
                fh.write("property_value: graph_stub \"true\" xsd:string\n")
                if src and src["name"]:
                    fh.write('property_value: stub_label "%s" xsd:string\n'
                             % obo_quoted(src["name"]))
                for subset in (src["subsets"] if src else []):
                    fh.write("subset: %s\n" % subset)
                for parent in (src["is_a"] if src else []):
                    fh.write("is_a: %s\n" % parent)
                fh.write("\n")
            report["channels"].setdefault(channel, {})["graph_stubs"] = len(dangling)

    report["drops"] = dict(report["drops"])
    report["totals"] = {
        "strings": len(rows),
        "terms": sum(1 for items in annot_terms.values() for _t, k in items if k),
        "by_tier": dict(Counter(r["tier"] for r in rows)),
        "by_kind": dict(Counter(r["string_kind"] for r in rows)),
        "by_channel": dict(Counter(r["channel"] for r in rows)),
    }
    with open(os.path.join(args.outdir, "build_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collection", default="maize-ontology-collection",
                    help="root of the published ontology collection")
    ap.add_argument("--chebi-full", default="chebi.obo",
                    help="full chebi.obo (the lite file has no synonyms)")
    ap.add_argument("--chebi-skip", action="store_true",
                    help="build without the ChEBI channel")
    ap.add_argument("--derive-trait-stems", action="store_true",
                    help="also match multi-word TO names without their ' trait' "
                         "suffix ('flowering time trait' -> 'flowering time')")
    ap.add_argument("--outdir", default="build")
    args = ap.parse_args()
    report = build(args)
    totals = report["totals"]
    print("%d strings over %d matchable terms" % (totals["strings"], totals["terms"]))
    print("by tier:", totals["by_tier"])
    print("by kind:", totals["by_kind"])
    print("by channel:", totals["by_channel"])
    print("drops:", report["drops"])


if __name__ == "__main__":
    main()
