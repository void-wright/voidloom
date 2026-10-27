# Voidloom No-Regression Standard (the codified bar)

**Status:** OFFICIAL. This document is the contract every Level-5 piece is held
to. It generalises an earlier empirical analysis into the standing PASS bar enforced by
`run_gate.sh`. A piece that cannot clear this bar has not earned a "no-regression"
claim, and its quality score is suspect (this is exactly the #3952 lesson).

---

## 0. Why this exists (the #3952 scar)

A prior eval used FTS5 keyword matching as a fast proxy for hybrid recall to
dodge the ~30s GPU embed cold-start. **18 of 30 queries returned ZERO FTS5
results** — the query terms never matched the corpus's `porter + unicode61`
tokenization — silently diluting the headline precision to a meaningless
**+10.44pp** when the real lift among *scoreable* queries was **+26.1pp**. The
root cause was **query terms assumed-to-match, never verified**, scored over a
**polluted denominator**.

The fix is NOT "switch to OR-of-all-terms" (that over-matches: every query then
hits 500–3800 rows, equally meaningless). The fix is a **corpus-presence
pre-flight** that classifies each query's measurability and reports **honest
N/M-scoreable denominators**, plus a **set-overlap** no-regression method that
catches a *dropped relevant memory* directly.

---

## 1. The gold-standard method (top-k SET overlap)

Run every eval query through `recall --top_k K` against an **identical copy** of
the DB **BEFORE** and **AFTER** a change. For each query, compare the two top-k
**id sets**:

| Per-query verdict | Meaning | Allowed? |
|---|---|---|
| **identical** | same set *and* same order | yes (no-op) |
| **pure-reorder** | same set, new order | **yes — this is the intended enrichment** (lens/foundation re-ranked the same K memories) |
| **set-changed** | membership delta (ids dropped and/or added) | **only if every drop is kill-switch-reversible** |
| **missing-after** | the query vanished from the AFTER snapshot | no — regression |

A **dropped relevant memory is the regression to catch.** Re-ordering is *not* a
regression; it is the whole point of the recall organs.

**A set-change is ONLY allowed if it is reversible via a NAMED kill-switch**
(`--no-lens` / `--no-boost` / `--no-cross-tier` / `--no-rerank`). The comparator
takes an optional *expected-exclusions* annotation; a drop of an annotated id is
**EXPLAINED** (e.g. a persona `exclude_tags` match removed by the lens), any
other drop is **UNEXPLAINED** and fails the gate.

---

## 2. The PASS thresholds (the official bar)

A change **PASSES** the no-regression gate iff **ALL** of:

1. **Mean top-k set-overlap ≥ 0.99** (99%).
2. **Min top-k set-overlap ≥ 0.90** (90%) — no single query may collapse.
3. **0 relevance-driven (UNEXPLAINED) drops.** Every set membership change must
   be a declared, kill-switch-reversible exclusion.
4. **0 missing-after queries** (the AFTER eval covered every query).
5. **Cap-floor must-retrieve set intact** — every memory id declared in
   `must_retrieve.json` for a query is still present in that query's top-k
   (a *direct* relevance-drop check, stronger than aggregate overlap).
6. **Honest denominators** — the corpus pre-flight reports **N/M SCOREABLE**;
   the headline metric is computed over the **SCOREABLE** denominator only, and
   any BROAD/VACUOUS queries are named, never silently averaged in.

Thresholds (1)–(2) are compared at the same 4-dp precision they are reported
(with a 1e-9 epsilon) so a piece sitting *exactly* on the bar — e.g. mean 0.99
from 18×1.0 + 2×0.90, the literal Loop A result — is **not** wrongly failed by
float rounding.

These numbers come from the empirical Loop A run: **mean set-overlap 99.0%, min
90.0%, 0 relevance-driven drops**, where the only two set-changes were
intentional persona exclusions (tags `social-media`, `pm-image-storage`), each
fully reversible with `--no-lens`, and the strict pre-organ baseline was
byte-reproducible with `--no-lens --no-boost`.

---

## 3. Honest denominators — the corpus-presence classifier

Before any score is reported, `corpus_presence.py` classifies every query
against `memories_fts` (`porter + unicode61`):

| Class | Definition |
|---|---|
| **SCOREABLE** | ≥1 *distinctive* term: a term whose corpus document-frequency is `0 < df ≤ 60% of corpus` and is not a structural stopword |
| **BROAD** | has hits, but every matching term is over-common (`df > 60%`) or structural — not discriminating (the OR-of-everything trap) |
| **VACUOUS** | 0 terms match the corpus — the #3952 zero-result trap |

The report is always **"N/M SCOREABLE, K BROAD, J VACUOUS"**. A metric averaged
over a denominator that includes VACUOUS/BROAD queries is forbidden — that is
the #3952 dishonesty by another name. **Every query added to `eval_queries.txt`
MUST classify SCOREABLE** (proven in the generation report). The 60% ceiling is
the discriminating threshold: empirically `"the"` (69% of the live corpus) is
demoted to non-distinctive while every real science/system token sits far below.

---

## 4. Side-effect-free guarantee

The eval is side-effect-free **with respect to the live brain**:

- It runs the **unmodified** `claude_memory.py` against a **disposable /tmp copy**
  of the DB (pointed there via a symlink of the module into the scratch dir, so
  `Path(__file__).parent.parent / "claude_memory.db"` resolves to the copy while sibling
  organ modules still import from the live tree).
- `VOIDLOOM_NO_LOG_USAGE=1` suppresses the usage log; `VOIDLOOM_NO_CROSS_TIER=1`
  isolates the deterministic local pool (a 768-dim second brain is inert).
- recall *does* bump `access_count` on returned rows, but that lands **only on
  the disposable scratch copy**. The **input DB file is md5-verified unchanged**
  before and after every run — that is the load-bearing confinement invariant,
  not a frozen scratch fingerprint.

---

## 5. The gate (one command)

```
run_gate.sh --db <DB> --before-cli <unpatched.py> --after-cli <patched.py>
```

chains: corpus pre-flight → eval BEFORE → eval AFTER → set-overlap compare →
cap-floor must-retrieve → organ + trust assertions → single PASS/FAIL verdict
with the honest denominators. **Self-sanity check:** point both `--before-cli`
and `--after-cli` at the same unmodified module → the gate MUST report **100%
set-overlap, 0 drops** (a no-op change against itself).

---

## 6. Verdict logic (authoritative)

```
PASS  iff  mean_overlap >= 0.99
      and  min_overlap  >= 0.90
      and  unexplained_drops == 0
      and  missing_after == 0
      and  cap_floor_breaches == 0
      and  (corpus pre-flight: 0 VACUOUS among the gate's own query set)
FAIL  otherwise
```

A piece that wants a set-change must *declare* it as a kill-switch-reversible
exclusion (annotate `--exclusions`) and prove the strict baseline is restorable
with the named switch — exactly as Loop A did. Anything else is a regression.
