#!/usr/bin/env bash
# run_gate.sh -- THE one-command Voidloom no-regression gate.
#
# Every future Level-5 piece-builder runs THIS to prove "I did not regress
# recall." It chains:
#   1. corpus pre-flight  (corpus_presence.py)  -> honest N/M SCOREABLE denominators
#   2. eval BEFORE         (eval_recall.sh)      -> top-k snapshot, unpatched recall
#   3. eval AFTER          (eval_recall.sh)      -> top-k snapshot, patched recall
#   4. compare             (compare_recall.py)   -> set-overlap, drops, verdict
#   5. cap-floor           (check_must_retrieve) -> must-retrieve ids still present
#   6. assertions          (assertions.sh)       -> organ tables + trust verb
# then prints a SINGLE PASS/FAIL verdict with the honest denominators.
#
# CONFINEMENT: the gate copies the DB into /tmp itself and runs both BEFORE and
# AFTER recalls against that copy via eval_recall.sh's symlink technique. It
# NEVER writes the live DB or any live .py.
#
# Usage:
#   run_gate.sh --db <DB> \
#               [--before-cli <claude_memory.py>] [--after-cli <claude_memory.py>] \
#               [--queries <FILE>] [--must <must_retrieve.json>] \
#               [--exclusions <exclusions.json>] [--top-k 10] [--out-dir <DIR>] \
#               [--before-args "..."] [--after-args "..."]
#
# Self-sanity check (the harness's own proof): point both --before-cli and
# --after-cli at the SAME unmodified claude_memory.py -> a no-op change against
# itself MUST report 100% set-overlap, 0 drops.

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"

# D8-F7: these were three absolute paths into one machine. Resolved env-first
# from the package's own location, in the same order the engine, the CLI and the
# installer use, so the gate runs wherever the brain is. HERE is this script's
# directory; the tree root is two levels up (tools/harness/ -> tools/ -> root).
BRAIN_ROOT="${WIZARD_LIBRARY_HOME:-$(cd "$HERE/../.." && pwd)}"
DB="${WIZARD_LIBRARY_DB:-${CLAUDE_MEMORY_DB:-$BRAIN_ROOT/claude_memory.db}}"
BEFORE_CLI="${BEFORE_CLI:-$BRAIN_ROOT/organs/claude_memory.py}"
AFTER_CLI="${AFTER_CLI:-$BRAIN_ROOT/organs/claude_memory.py}"
QUERIES="$HERE/eval_queries.txt"
MUST="$HERE/must_retrieve.json"
EXCLUSIONS=""
TOPK=10
OUTDIR=""
BEFORE_ARGS=""
AFTER_ARGS=""

while [ $# -gt 0 ]; do
  case "$1" in
    --db)          DB="$2"; shift 2 ;;
    --before-cli)  BEFORE_CLI="$2"; shift 2 ;;
    --after-cli)   AFTER_CLI="$2"; shift 2 ;;
    --queries)     QUERIES="$2"; shift 2 ;;
    --must)        MUST="$2"; shift 2 ;;
    --exclusions)  EXCLUSIONS="$2"; shift 2 ;;
    --top-k)       TOPK="$2"; shift 2 ;;
    --out-dir)     OUTDIR="$2"; shift 2 ;;
    --before-args) BEFORE_ARGS="$2"; shift 2 ;;
    --after-args)  AFTER_ARGS="$2"; shift 2 ;;
    *) echo "run_gate: unknown arg $1" >&2; exit 2 ;;
  esac
done

[ -f "$DB" ] || { echo "run_gate: DB not found: $DB" >&2; exit 2; }
[ -f "$QUERIES" ] || { echo "run_gate: queries not found: $QUERIES" >&2; exit 2; }

if [ -z "$OUTDIR" ]; then OUTDIR="$(mktemp -d /tmp/voidloom_gate.XXXXXX)"; fi
mkdir -p "$OUTDIR"

# --- 0. Make a /tmp working copy of the DB (never touch the input DB live). --
WORKDB="$OUTDIR/work_copy.db"
cp "$DB" "$WORKDB"
echo "=============================================================="
echo " Voidloom GATE  (out-dir: $OUTDIR)"
echo " work DB copy : $WORKDB"
echo "=============================================================="

GATE_FAIL=0

# --- 1. Corpus pre-flight: honest denominators, must be all SCOREABLE. -------
echo ""
echo ">>> [1/6] corpus pre-flight"
python3 "$HERE/corpus_presence.py" --db "$WORKDB" --queries "$QUERIES" --require-all-scoreable
PRE_RC=$?
if [ "$PRE_RC" -ne 0 ]; then
  echo "    NOTE: not all queries SCOREABLE -- denominators will note this (see above)."
  # Pre-flight non-scoreable is a WARNING for the gate's own queries since the
  # shipped set is 100% scoreable; a piece adding a bad query gets flagged here.
fi

# --- 2. eval BEFORE ----------------------------------------------------------
echo ""
echo ">>> [2/6] eval BEFORE  (cli: $BEFORE_CLI  args: '${BEFORE_ARGS}')"
BEFORE_SNAP="$OUTDIR/eval_before.json"
# shellcheck disable=SC2086
bash "$HERE/eval_recall.sh" --db "$WORKDB" --cli "$BEFORE_CLI" \
     --queries "$QUERIES" --out "$BEFORE_SNAP" --top-k "$TOPK" -- $BEFORE_ARGS \
  || { echo "run_gate: BEFORE eval failed" >&2; GATE_FAIL=1; }

# --- 3. eval AFTER -----------------------------------------------------------
echo ""
echo ">>> [3/6] eval AFTER   (cli: $AFTER_CLI  args: '${AFTER_ARGS}')"
AFTER_SNAP="$OUTDIR/eval_after.json"
# shellcheck disable=SC2086
bash "$HERE/eval_recall.sh" --db "$WORKDB" --cli "$AFTER_CLI" \
     --queries "$QUERIES" --out "$AFTER_SNAP" --top-k "$TOPK" -- $AFTER_ARGS \
  || { echo "run_gate: AFTER eval failed" >&2; GATE_FAIL=1; }

# --- 4. compare (set-overlap, drops) -----------------------------------------
echo ""
echo ">>> [4/6] no-regression comparison"
CMP_ARGS=("$BEFORE_SNAP" "$AFTER_SNAP")
[ -n "$EXCLUSIONS" ] && CMP_ARGS+=(--exclusions "$EXCLUSIONS")
python3 "$HERE/compare_recall.py" "${CMP_ARGS[@]}"
CMP_RC=$?
[ "$CMP_RC" -ne 0 ] && GATE_FAIL=1

# --- 5. cap-floor must-retrieve (checked on AFTER) ---------------------------
echo ""
echo ">>> [5/6] cap-floor must-retrieve check (on AFTER)"
if [ -f "$MUST" ]; then
  python3 "$HERE/check_must_retrieve.py" "$AFTER_SNAP" "$MUST"
  MR_RC=$?
  if [ "$MR_RC" -eq 3 ]; then
    echo "    NOT CHECKED: must_retrieve.json declares no anchors for this brain (see its _doc) -- the cap floor measured nothing"
  elif [ "$MR_RC" -ne 0 ]; then
    GATE_FAIL=1
  fi
else
  echo "    (no must_retrieve.json -- skipping cap-floor check)"
fi

# --- 6. organ + trust assertions ---------------------------------------------
echo ""
echo ">>> [6/6] organ + trust assertions"
bash "$HERE/assertions.sh" "$WORKDB" "$AFTER_CLI"
ASSERT_RC=$?
[ "$ASSERT_RC" -ne 0 ] && GATE_FAIL=1

# --- side-effect-free proof (from the BEFORE snapshot's fingerprint) ---------
echo ""
echo ">>> side-effect-free check"
python3 - "$BEFORE_SNAP" "$AFTER_SNAP" <<'PY'
import sys, json
for label, p in (("before", sys.argv[1]), ("after", sys.argv[2])):
    try:
        d = json.load(open(p))
        s = d.get("side_effect_check", {})
        print(f"  {label}: input_db_unchanged={s.get('input_db_unchanged')} "
              f"(md5 {s.get('input_db_md5_before')}) "
              f"scratch_access_count_delta={s.get('scratch_access_count_delta')} "
              f"(disposable, expected)")
    except Exception as e:
        print(f"  {label}: (no side-effect record: {e})")
PY

# --- Final verdict -----------------------------------------------------------
echo ""
echo "=============================================================="
if [ "$GATE_FAIL" -eq 0 ]; then
  echo " GATE VERDICT: PASS"
  echo "=============================================================="
  exit 0
else
  echo " GATE VERDICT: FAIL"
  echo "=============================================================="
  exit 1
fi
