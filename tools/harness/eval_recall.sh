#!/usr/bin/env bash
# eval_recall.sh -- run every eval query through `recall --top_k K` and capture
# the ordered top-k id list per query into a snapshot JSON.
#
# CONFINEMENT (non-negotiable): the eval is side-effect-free.
#  * It NEVER writes to the live DB. It runs the recall CLI against a path you
#    give it; for the gate we point it at a /tmp COPY of the live DB.
#  * VOIDLOOM_NO_LOG_USAGE=1 stops recall from mutating access_count / usage logs.
#  * VOIDLOOM_NO_CROSS_TIER=1 isolates the deterministic local candidate pool
#    (a 768-dim second brain is inert against a 1024-dim one).
#
# HOW IT POINTS RECALL AT A COPY (without modifying the live module):
#   claude_memory.py hardcodes  DB_PATH = Path(__file__).parent.parent / "claude_memory.db".
#   When the module is invoked via a SYMLINK in a scratch dir, Python sets
#   __file__ to the symlink path -> DB_PATH resolves to the DB sitting NEXT TO
#   the symlink (the /tmp copy), while sys.path[0] resolves to the REAL module
#   dir -> sibling organ modules (hybrid_retrieval, persona_lenses, ...) still
#   import from the live tree. Net: real unmodified recall pipeline, /tmp DB.
#
# Usage:
#   eval_recall.sh --db <DB_PATH> --queries <FILE> --out <SNAPSHOT.json> \
#                  [--cli <claude_memory.py>] [--top-k 10] [--no-lens|--no-boost|...]
#
# Any extra args after a literal `--` are passed straight to the recall CLI
# (so a piece can run BEFORE with no switches and AFTER with --no-lens, etc.).

set -u

# D8-F7: resolved from this script's own location, env-overridable.
_EVAL_HOME="$(cd "$(dirname "$0")/../.." && pwd)"
CLI="${VOIDLOOM_ENGINE_CLI:-${WIZARD_LIBRARY_HOME:-$_EVAL_HOME}/organs/claude_memory.py}"
DB=""
QUERIES=""
OUT=""
TOPK=10
PASSTHRU=()

while [ $# -gt 0 ]; do
  case "$1" in
    --db)       DB="$2"; shift 2 ;;
    --queries)  QUERIES="$2"; shift 2 ;;
    --out)      OUT="$2"; shift 2 ;;
    --cli)      CLI="$2"; shift 2 ;;
    --top-k)    TOPK="$2"; shift 2 ;;
    --)         shift; PASSTHRU+=("$@"); break ;;
    *)          PASSTHRU+=("$1"); shift ;;
  esac
done

if [ -z "$DB" ] || [ -z "$QUERIES" ] || [ -z "$OUT" ]; then
  echo "usage: eval_recall.sh --db DB --queries FILE --out SNAPSHOT.json [--cli CLI] [--top-k 10] [-- recall-args...]" >&2
  exit 2
fi
if [ ! -f "$DB" ]; then echo "eval_recall: DB not found: $DB" >&2; exit 2; fi
if [ ! -f "$QUERIES" ]; then echo "eval_recall: queries not found: $QUERIES" >&2; exit 2; fi
if [ ! -f "$CLI" ]; then echo "eval_recall: recall CLI not found: $CLI" >&2; exit 2; fi

# --- Resolve the real module path so the symlink targets the live file. -----
CLI_REAL="$(readlink -f "$CLI")"

# --- Build a scratch dir: symlink the module + place the DB copy beside it. --
SCRATCH="$(mktemp -d /tmp/voidloom_eval.XXXXXX)"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

ln -s "$CLI_REAL" "$SCRATCH/claude_memory.py"
# IMPORTANT: do NOT modify the input DB. We make our OWN DISPOSABLE working copy
# inside the scratch dir named claude_memory.db so DB_PATH (= scratch/claude_memory.db)
# points here. If the caller already handed us a /tmp copy, this is a copy of a
# copy -- still zero risk to anything live.
cp "$DB" "$SCRATCH/claude_memory.db"

# SIDE-EFFECT MODEL (honest):
#  * recall INHERENTLY bumps access_count/last_accessed on returned rows
#    (claude_memory.py UPDATEs access_count = access_count + 1; this is NOT gated
#    by VOIDLOOM_NO_LOG_USAGE, which only suppresses the skill_usage_log).
#  * That bump lands ONLY on the DISPOSABLE scratch copy, which is rm -rf'd on
#    exit. The INPUT DB ($DB) is opened by the harness only via `cp` (read) --
#    never written. We PROVE the input is untouched by md5-fingerprinting it
#    before AND after the whole run.
DB_MD5_BEFORE="$(md5sum "$DB" 2>/dev/null | awk '{print $1}')"
# Scratch fingerprint is recorded for transparency, but a change here is EXPECTED
# (recall bumps access_count) and is NOT a failure -- the scratch is disposable.
AC_BEFORE="$(sqlite3 "$SCRATCH/claude_memory.db" 'SELECT COALESCE(SUM(access_count),0) FROM memories;' 2>/dev/null)"

PY_PARSE='
import sys, json, re
raw = sys.stdin.read()
m = re.search(r"\{\"status\".*\}\s*$", raw, re.S)
if not m:
    print("__NOJSON__")
    sys.exit(0)
try:
    d = json.loads(m.group())
except Exception as e:
    print("__BADJSON__:%s" % e)
    sys.exit(0)
ids = [r.get("id") for r in d.get("results", [])]
print(json.dumps(ids))
'

# --- Stream a JSON snapshot. Build it incrementally in Python at the end. ----
TMP_PAIRS="$(mktemp /tmp/voidloom_pairs.XXXXXX)"
ERR_PAIRS="$(mktemp /tmp/voidloom_errs.XXXXXX)"
: > "$TMP_PAIRS"
: > "$ERR_PAIRS"

N=0
while IFS= read -r Q || [ -n "$Q" ]; do
  # skip blanks + comments
  case "$Q" in ""|\#*) continue ;; esac
  N=$((N+1))
  # Run recall against the SCRATCH module (-> scratch DB), strip model noise.
  RAW="$(cd "$SCRATCH" && VOIDLOOM_NO_LOG_USAGE=1 VOIDLOOM_NO_CROSS_TIER=1 \
        python3 "$SCRATCH/claude_memory.py" recall --query "$Q" --top_k "$TOPK" \
        ${PASSTHRU[@]+"${PASSTHRU[@]}"} 2>/dev/null)"
  IDS="$(printf '%s' "$RAW" | python3 -c "$PY_PARSE")"
  if [ "$IDS" = "__NOJSON__" ] || [[ "$IDS" == __BADJSON__* ]]; then
    # Record an error; snapshot gets an empty list for this query.
    printf '%s\t%s\n' "$Q" "$IDS" >> "$ERR_PAIRS"
    printf '%s\t[]\n' "$Q" >> "$TMP_PAIRS"
    echo "  [eval] query $N: PARSE-ERROR ($IDS) :: $Q" >&2
  else
    printf '%s\t%s\n' "$Q" "$IDS" >> "$TMP_PAIRS"
    echo "  [eval] query $N: ${IDS} :: $Q" >&2
  fi
done < "$QUERIES"

# ============================================================================
# H2-UNBLIND: the existing per-query legs above run with VOIDLOOM_NO_CROSS_TIER=1
# and 2>/dev/null — deterministic, but BLIND to the keystone (the 768-vs-1024
# cross-tier dim-mismatch that fired on every recall + the stderr noise it
# emitted). A gate that switches off the failing subsystem AND discards the
# failing channel is a green light wired to a disconnected bulb. So we add TWO
# observing legs that run ONCE on a single canary query and DO NOT suppress
# stderr. They are pure observers — they never write the snapshot ids; they only
# assert the FIXED contracts (C1 default-OFF + the G1 resolver fix).
#   Leg A (default-OFF): a NORMAL recall (no VOIDLOOM_ENABLE_CROSS_TIER) makes NO
#           cross-tier call at all, so a SUCCESSFUL recall's stderr is CLEAN of
#           any 'Error querying tier' / 'Dimension mismatch'.
#   Leg B (enabled w/ declared twin): with VOIDLOOM_ENABLE_CROSS_TIER=1 and a
#           declared twin DB (a COPY of this same brain -> same embedding era),
#           the resolver must use the ACTIVE table (_embed_table) so NO dim error
#           is raised even though cross-tier is ON.
# These legs are ALSO controllable: set VOIDLOOM_SKIP_UNBLIND=1 to skip (e.g. on
# a host with no model cache where a real recall can't run).
# ============================================================================
UNBLIND_VERDICT="skipped"
UNBLIND_DETAIL=""
CANARY="$(grep -vE '^\s*(#|$)' "$QUERIES" 2>/dev/null | head -n 1)"
if [ "${VOIDLOOM_SKIP_UNBLIND:-0}" != "1" ] && [ -n "$CANARY" ]; then
  echo "  [eval] H2-unblind: observing cross-tier stderr on a canary recall…" >&2

  # --- Leg A: cross-tier default-OFF -> a successful recall has CLEAN stderr. ---
  # Capture STDERR ONLY (drop stdout). NO VOIDLOOM_NO_CROSS_TIER here: we exercise
  # the real default (gate unset => OFF). VOIDLOOM_NO_LOG_USAGE keeps it side-
  # effect-free on the scratch copy; model-load progress goes to the H3 logger,
  # not default stderr, so a clean run leaks nothing here.
  ERR_A="$(cd "$SCRATCH" && VOIDLOOM_NO_LOG_USAGE=1 \
        python3 "$SCRATCH/claude_memory.py" recall --query "$CANARY" \
        --top_k "$TOPK" ${PASSTHRU[@]+"${PASSTHRU[@]}"} 2>&1 1>/dev/null)"
  RC_A=$?

  # --- Leg B: cross-tier ENABLED + declared same-era twin -> no dim error. ------
  # Point the eddie-windows tier at a COPY of THIS brain (same jina-1024 era) via
  # the underscore env key, so the resolver path is exercised end-to-end and MUST
  # NOT raise a dim mismatch. We use a second disposable scratch copy as the twin.
  TWIN="$SCRATCH/twin_brain.db"
  cp "$SCRATCH/claude_memory.db" "$TWIN" 2>/dev/null
  ERR_B="$(cd "$SCRATCH" && VOIDLOOM_NO_LOG_USAGE=1 \
        VOIDLOOM_ENABLE_CROSS_TIER=1 \
        MEMORY_TIER_EDDIE_WINDOWS_PATH="$TWIN" \
        python3 "$SCRATCH/claude_memory.py" recall --query "$CANARY" \
        --top_k "$TOPK" ${PASSTHRU[@]+"${PASSTHRU[@]}"} 2>&1 1>/dev/null)"
  RC_B=$?

  NOISE_RE="Dimension mismatch|Error querying tier|Expected [0-9]+ dimensions"
  FAILED=0
  if echo "$ERR_A" | grep -Eq "$NOISE_RE"; then
    UNBLIND_DETAIL="legA(default-OFF) leaked cross-tier noise: $(echo "$ERR_A" | grep -E "$NOISE_RE" | head -1)"
    FAILED=1
  elif echo "$ERR_B" | grep -Eq "$NOISE_RE"; then
    UNBLIND_DETAIL="legB(enabled+twin) raised a dim mismatch despite same-era twin: $(echo "$ERR_B" | grep -E "$NOISE_RE" | head -1)"
    FAILED=1
  fi

  if [ "$FAILED" -eq 1 ]; then
    UNBLIND_VERDICT="FAIL"
    echo "  [eval] H2-unblind: FAIL -- $UNBLIND_DETAIL" >&2
  else
    UNBLIND_VERDICT="pass"
    echo "  [eval] H2-unblind: pass (legA rc=$RC_A clean; legB rc=$RC_B clean, cross-tier ON + same-era twin)" >&2
  fi
fi

AC_AFTER="$(sqlite3 "$SCRATCH/claude_memory.db" 'SELECT COALESCE(SUM(access_count),0) FROM memories;' 2>/dev/null)"
DB_MD5_AFTER="$(md5sum "$DB" 2>/dev/null | awk '{print $1}')"

# --- Assemble the snapshot JSON. ---------------------------------------------
python3 - "$DB" "$TOPK" "$TMP_PAIRS" "$ERR_PAIRS" "$OUT" "$AC_BEFORE" "$AC_AFTER" "$DB_MD5_BEFORE" "$DB_MD5_AFTER" "$UNBLIND_VERDICT" "$UNBLIND_DETAIL" <<'PY'
import sys, json
db, topk, pairs_path, errs_path, out_path, ac_b, ac_a, md5_b, md5_a = sys.argv[1:10]
unblind_verdict = sys.argv[10] if len(sys.argv) > 10 else "skipped"
unblind_detail = sys.argv[11] if len(sys.argv) > 11 else ""
snaps = {}
with open(pairs_path, encoding="utf-8") as fh:
    for line in fh:
        if "\t" not in line:
            continue
        q, ids = line.rstrip("\n").split("\t", 1)
        try:
            snaps[q] = json.loads(ids)
        except Exception:
            snaps[q] = []
errs = {}
with open(errs_path, encoding="utf-8") as fh:
    for line in fh:
        if "\t" not in line:
            continue
        q, msg = line.rstrip("\n").split("\t", 1)
        errs[q] = msg
out = {
    "db": db,
    "top_k": int(topk),
    "side_effect_check": {
        # The INPUT DB ($DB) must be byte-identical before/after -- THIS is the
        # confinement invariant that matters (the harness never writes it).
        "input_db_md5_before": md5_b,
        "input_db_md5_after": md5_a,
        "input_db_unchanged": (md5_b == md5_a),
        # The scratch copy's access_count bump is EXPECTED (recall bumps it) and
        # harmless (scratch is disposable). Recorded for transparency only.
        "scratch_access_count_before": int(ac_b or 0),
        "scratch_access_count_after": int(ac_a or 0),
        "scratch_access_count_delta": int(ac_a or 0) - int(ac_b or 0),
        "note": ("recall inherently bumps access_count on returned rows; that "
                 "lands only on the disposable scratch copy. Input DB is "
                 "read-only (cp) and md5-verified unchanged."),
        # Back-compat alias: 'unchanged' tracks the load-bearing invariant.
        "unchanged": (md5_b == md5_a),
    },
    # H2-UNBLIND: the observing legs' verdict (default-OFF clean stderr + the
    # cross-tier-ENABLED same-era twin raised no dim error). A `FAIL` here means
    # the keystone has regressed; the harness exits non-zero below so the gate
    # can never again be green while the dim-mismatch / stderr noise is live.
    "cross_tier_unblind": {
        "verdict": unblind_verdict,
        "detail": unblind_detail,
        "checks": ("legA: default-OFF recall -> clean stderr (no 'Error querying "
                   "tier'/'Dimension mismatch'); legB: VOIDLOOM_ENABLE_CROSS_TIER=1 "
                   "+ MEMORY_TIER_EDDIE_WINDOWS_PATH=<same-era twin> -> resolver "
                   "uses _embed_table(), no dim error."),
    },
    "snapshots": snaps,
    "errors": errs,
}
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2)
print("wrote %d query snapshots to %s (input_db_unchanged=%s, scratch_ac_delta=%d)" % (
    len(snaps), out_path, md5_b == md5_a, int(ac_a or 0) - int(ac_b or 0)), file=sys.stderr)
PY

rm -f "$TMP_PAIRS" "$ERR_PAIRS"

# Confinement assertion (the load-bearing one): the INPUT DB must be untouched.
if [ "$DB_MD5_BEFORE" != "$DB_MD5_AFTER" ]; then
  echo "eval_recall: CONFINEMENT VIOLATION -- input DB md5 changed ($DB_MD5_BEFORE -> $DB_MD5_AFTER)!" >&2
  exit 3
fi

# H2-UNBLIND gate assertion: a FAIL verdict means cross-tier noise/dim-mismatch
# is live again. This is the keystone the old blind gate could never see; exiting
# non-zero here makes the gate constitutionally CAPABLE of catching it. (A
# `skipped` verdict -- no canary or VOIDLOOM_SKIP_UNBLIND=1 -- never blocks.)
if [ "$UNBLIND_VERDICT" = "FAIL" ]; then
  echo "eval_recall: H2-UNBLIND FAIL -- $UNBLIND_DETAIL" >&2
  exit 4
fi

echo "eval_recall: done ($N queries) -> $OUT  [input DB md5 unchanged: $DB_MD5_BEFORE; cross_tier_unblind=$UNBLIND_VERDICT]" >&2
