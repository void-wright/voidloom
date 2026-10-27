#!/usr/bin/env python3
"""
corpus_presence.py  --  the #3952 fix.

THE FLAW (#3952): a prior eval used FTS5 keyword matching as a fast proxy for
hybrid recall to dodge the ~30s GPU embed cold-start. 18 of 30 queries returned
ZERO FTS5 results (the query terms did not match the corpus tokenization:
porter stem + unicode61), silently diluting the headline precision to a
meaningless +10.44pp when the real lift among *scoreable* queries was +26.1pp.
Root cause: query terms were ASSUMED to match the corpus, never verified.

THE WRONG "FIX" (do NOT do this): switch to OR-of-all-terms. That over-matches.
Every query then hits 500-3800 rows, equally meaningless -- a different flavour
of the same dishonesty.

THE REAL FIX (this module): a corpus-presence PRE-FLIGHT that classifies each
query's *measurability* against the live FTS5 index, with HONEST DENOMINATORS.
A query is only counted as measurable ("SCOREABLE") if at least one of its terms
is *distinctive* -- i.e. has a corpus document-frequency BELOW a ceiling. A term
matching 60%+ of the corpus (e.g. "the", "memory") is not discriminating; a query
whose ONLY hits come from such over-common terms is "BROAD", not scoreable. A
query no term of which matches anything is "VACUOUS". The report is always
"N/M SCOREABLE, K BROAD, J VACUOUS" -- never a bare metric over a polluted
denominator.

Classification per query:
  SCOREABLE : >=1 term with 0 < df <= DISTINCTIVE_CEILING (a discriminating hit)
  BROAD     : has hits, but every hit-term is over-common (df > DISTINCTIVE_CEILING)
  VACUOUS   : 0 terms match the corpus at all (the #3952 zero-result trap)

Stdlib + sqlite3 only. No pip dependency. Read-only against the DB.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Tunables (documented so a Reflector can scrutinise / a Curator can adjust).
# ---------------------------------------------------------------------------

# A term is "distinctive" iff its corpus document-frequency fraction is at or
# below this ceiling. 0.60 means: a term that matches >60% of memories is NOT
# discriminating. (Empirically on the live brain: "the"=69% -> BROAD; every
# distinctive science/system token is far below this.)
DISTINCTIVE_CEILING_FRACTION = 0.60

# Minimum token length to consider. 1-char tokens are noise; FTS5 also drops
# many. Kept low (2) so short distinctive acronyms (e.g. "JWT") still count;
# "JWT" is 3 chars anyway, this just filters single letters.
MIN_TOKEN_LEN = 2

# A small stoplist of structural English words that, even if rare in THIS
# corpus, carry no retrieval signal. This is belt-and-suspenders: the df
# ceiling already demotes the common ones, but an unusual corpus could leave a
# stopword rare. Intentionally tiny -- the df ceiling is the primary mechanism.
STRUCTURAL_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "by", "with", "as", "at", "it", "its", "this", "that",
})

FTS_TABLE = "memories_fts"
SOURCE_TABLE = "memories"


# ---------------------------------------------------------------------------
# Tokenisation: split a query into candidate terms the way a human would read
# them. We deliberately keep hyphenated compounds AS the raw token AND also
# probe their bare-word fallbacks, because FTS5 'porter unicode61' splits on
# the hyphen. We always quote each probe as a phrase so FTS5 treats it
# literally (avoids a stray term being parsed as an operator).
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9]|[A-Za-z0-9]")


def tokenize(query):
    """Return the de-duplicated, lower-cased candidate terms of a query.

    Hyphenated compounds (e.g. 'cross-tier') are kept whole because that is
    how a reader names them; the df probe below uses an FTS5 phrase match
    which will porter-stem each sub-token, so 'cross-tier' matches documents
    containing 'cross tier'. We do NOT explode into sub-tokens here -- the
    whole compound is the distinctive unit and we want its TRUE df.
    """
    seen = []
    seen_set = set()
    for m in _TOKEN_RE.finditer(query or ""):
        tok = m.group(0).lower().strip("-")
        if len(tok) < MIN_TOKEN_LEN:
            continue
        if tok in seen_set:
            continue
        seen_set.add(tok)
        seen.append(tok)
    return seen


# ---------------------------------------------------------------------------
# Document frequency: how many memories does a single term's FTS5 phrase match?
# We quote the term so a hyphen / punctuation never becomes an FTS5 operator.
# Errors (e.g. a token FTS5 still rejects) are treated as df=0 (no signal),
# never raised -- the harness must degrade gracefully.
# ---------------------------------------------------------------------------

def _phrase_df(conn, term):
    """Document frequency of one term as an FTS5 phrase. df=0 on any error."""
    # Escape embedded double-quotes per FTS5 string rules, then wrap in quotes.
    safe = '"' + term.replace('"', '""') + '"'
    try:
        cur = conn.execute(
            f"SELECT COUNT(*) FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH ?",
            (safe,),
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error:
        return 0


def corpus_size(conn):
    """Total documents in the FTS index -- the denominator the DF probes are
    measured against. We count the FTS table itself so the denominator is
    EXACTLY the population the MATCH counts come from (the trigger mirrors every
    memories row into memories_fts regardless of is_archived, so this equals
    COUNT(*) FROM memories on this brain; counting the FTS table keeps it
    self-consistent even if that ever changes). Falls back to the source table
    if the FTS table is unavailable."""
    for tbl in (FTS_TABLE, SOURCE_TABLE):
        try:
            cur = conn.execute(f"SELECT COUNT(*) FROM {tbl}")
            row = cur.fetchone()
            if row and row[0] is not None:
                return int(row[0])
        except sqlite3.Error:
            continue
    return 0


# ---------------------------------------------------------------------------
# The classifier.
# ---------------------------------------------------------------------------

def classify_query(conn, query, total_docs, ceiling_fraction=DISTINCTIVE_CEILING_FRACTION):
    """Classify a single query. Returns a dict (never raises on bad input)."""
    if total_docs <= 0:
        # Cannot measure anything against an empty corpus -- honest about it.
        return {
            "query": query,
            "classification": "VACUOUS",
            "reason": "empty corpus (0 documents)",
            "terms": [],
            "scoreable_terms": [],
            "max_distinctive_df": None,
            "total_docs": total_docs,
        }

    ceiling_docs = max(1, int(ceiling_fraction * total_docs))
    terms_info = []
    any_hit = False
    distinctive_terms = []

    for term in tokenize(query):
        df = _phrase_df(conn, term)
        frac = (df / total_docs) if total_docs else 0.0
        is_structural = term in STRUCTURAL_STOPWORDS
        # Distinctive iff it actually matches something AND is below the
        # ceiling AND is not a structural stopword.
        distinctive = (df > 0) and (df <= ceiling_docs) and (not is_structural)
        if df > 0:
            any_hit = True
        if distinctive:
            distinctive_terms.append(term)
        terms_info.append({
            "term": term,
            "df": df,
            "df_fraction": round(frac, 4),
            "structural_stopword": is_structural,
            "distinctive": distinctive,
        })

    if distinctive_terms:
        classification = "SCOREABLE"
        max_dist_df = max(t["df"] for t in terms_info if t["term"] in distinctive_terms)
        reason = (
            f"{len(distinctive_terms)} distinctive term(s) "
            f"(df <= {ceiling_docs} = {ceiling_fraction:.0%} of {total_docs}): "
            + ", ".join(distinctive_terms)
        )
    elif any_hit:
        classification = "BROAD"
        max_dist_df = None
        over = [t["term"] for t in terms_info if t["df"] > ceiling_docs or t["structural_stopword"]]
        reason = (
            "all matching terms are over-common (df > "
            f"{ceiling_docs}) or structural: " + ", ".join(over or ["<none>"])
            + " -- not discriminating (the OR-of-everything trap)"
        )
    else:
        classification = "VACUOUS"
        max_dist_df = None
        reason = (
            "0 terms match the corpus FTS5 index -- the #3952 zero-result trap "
            "(query terms assumed-to-match, never verified)"
        )

    return {
        "query": query,
        "classification": classification,
        "reason": reason,
        "terms": terms_info,
        "scoreable_terms": distinctive_terms,
        "max_distinctive_df": max_dist_df,
        "total_docs": total_docs,
    }


def classify_queries(conn, queries, ceiling_fraction=DISTINCTIVE_CEILING_FRACTION):
    """Classify a list of queries. Returns a list of per-query dicts.

    Public API consumed by run_gate.sh and test_harness.py. Never raises on a
    malformed query (None / empty / weird unicode) -- each is classified.
    """
    total = corpus_size(conn)
    out = []
    for q in queries:
        out.append(classify_query(conn, q, total, ceiling_fraction))
    return out


def summarize(results):
    """Honest denominator summary over a classified list."""
    m = len(results)
    n_score = sum(1 for r in results if r["classification"] == "SCOREABLE")
    k_broad = sum(1 for r in results if r["classification"] == "BROAD")
    j_vac = sum(1 for r in results if r["classification"] == "VACUOUS")
    return {
        "total": m,
        "scoreable": n_score,
        "broad": k_broad,
        "vacuous": j_vac,
        "scoreable_fraction": round(n_score / m, 4) if m else 0.0,
        "headline": f"{n_score}/{m} SCOREABLE, {k_broad} BROAD, {j_vac} VACUOUS",
    }


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def read_queries(path):
    """One query per non-empty, non-comment line."""
    qs = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            qs.append(s)
    return qs


def open_db_readonly(db_path):
    """Open the DB strictly read-only (file:...?mode=ro) so the pre-flight can
    never mutate state -- corpus presence is a measurement, not a write."""
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _print_human(results, summary, ceiling_fraction):
    width = max((len(r["query"]) for r in results), default=10)
    width = min(width, 60)
    print("=" * (width + 30))
    print(f"CORPUS-PRESENCE PRE-FLIGHT  (ceiling = {ceiling_fraction:.0%} of corpus)")
    print("=" * (width + 30))
    for r in results:
        q = (r["query"] or "")[:width].ljust(width)
        tag = r["classification"].ljust(9)
        dist = ",".join(r["scoreable_terms"][:4]) or "-"
        print(f"  [{tag}] {q}  <-{dist}>")
    print("-" * (width + 30))
    print(f"  {summary['headline']}")
    if summary["vacuous"]:
        print("  !! VACUOUS queries present -- these RE-COMMIT #3952 if scored. Fix them.")
    print("=" * (width + 30))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Corpus-presence pre-flight (#3952 fix).")
    # R4/P5 (sibling 3 of 3). A HARDCODED ABSOLUTE PATH as an argparse
    # default -- the strongest form of the class: it pre-empts the
    # environment AND is wrong on every machine but this one.
    ap.add_argument("--db", default=None,
                    help="path to the memory DB (opened read-only)")
    ap.add_argument("--queries", help="path to eval_queries.txt (one per line)")
    ap.add_argument("--query", action="append", default=[],
                    help="an inline query (repeatable)")
    ap.add_argument("--ceiling", type=float, default=DISTINCTIVE_CEILING_FRACTION,
                    help="distinctive df ceiling as a corpus fraction (default 0.60)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--require-all-scoreable", action="store_true",
                    help="exit 1 if ANY query is not SCOREABLE (gate mode)")
    args = ap.parse_args(argv)

    queries = list(args.query)
    if args.queries:
        queries.extend(read_queries(args.queries))
    if not queries:
        ap.error("no queries given (use --queries FILE or --query 'text')")

    try:
        # R4/P5. Resolve AFTER argparse so the environment is read at RUN
        # time. `args.db` now defaults to None; the fallback chain lives
        # here, once, and every later use reads `_db`.
        # R5/C6 (R4-F6). The hardcoded absolute was the LAST-RESORT tier here;
        # R4's own comment three lines above named it "wrong on every machine but
        # this one" and then kept it. Relative-self to the brain dir (this file
        # lives in voidloom_harness/, one level below it) is the house property.
        _db = (args.db or os.environ.get("WIZARD_LIBRARY_DB")
               or os.environ.get("CLAUDE_MEMORY_DB")
               or str(Path(__file__).resolve().parent.parent
                      / "claude_memory.db"))
        conn = open_db_readonly(_db)
    except sqlite3.Error as e:
        payload = {"status": "error", "error": f"cannot open db: {e}"}
        print(json.dumps(payload))
        return 2

    try:
        results = classify_queries(conn, queries, args.ceiling)
    finally:
        conn.close()

    summary = summarize(results)

    if args.json:
        print(json.dumps({
            "status": "ok",
            # R4/E2. The path this run ACTUALLY opened, never the argument
            # it was handed. A report that echoes its input cannot tell
            # you it opened something else.
            "db": _db,
            "ceiling_fraction": args.ceiling,
            "summary": summary,
            "results": results,
        }, indent=2))
    else:
        _print_human(results, summary, args.ceiling)

    if args.require_all_scoreable and summary["scoreable"] != summary["total"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
