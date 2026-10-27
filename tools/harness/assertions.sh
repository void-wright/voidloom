#!/usr/bin/env bash
# Voidloom self-illumination harness -- each organ's output table must go
# 0 -> populated once the organ is built. This extends the inherited 6-organ
# script with the Trust verb assertion (live today) and graceful-degradation
# stubs for the not-yet-built L5 organs (D/A/F/G/E).
#
# Degradation contract (the harness must NOT fail a piece that isn't built yet):
#   table ABSENT            -> SKIP  (organ not built -- not a failure)
#   table PRESENT but EMPTY -> FAIL  (organ built but never ran -- a real defect)
#   table PRESENT + ROWS    -> PASS  (organ alive)
#
# The L5 organ table NAMES are declared in one data-driven list below so a
# Reflector/Curator can correct a name in one place as each piece lands.

set -u
# D8-F7: positional args still win; the DEFAULTS are now resolved rather than
# typed, so this runs on a host that is not the author's.
_HARNESS_HOME="$(cd "$(dirname "$0")/../.." && pwd)"
BRAIN_ROOT="${WIZARD_LIBRARY_HOME:-$_HARNESS_HOME}"
DB="${1:-${WIZARD_LIBRARY_DB:-${CLAUDE_MEMORY_DB:-$BRAIN_ROOT/claude_memory.db}}}"
CLI="${2:-$BRAIN_ROOT/organs/claude_memory.py}"

q(){ sqlite3 "$DB" "$1" 2>/dev/null; }

# ---- table_exists / row_count helpers ---------------------------------------
table_exists(){ # $1 = table name -> "1"/"0"
  local n
  n=$(q "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='$1';")
  echo "${n:-0}"
}
row_count(){ # $1 = table name -> integer (0 if absent/error)
  local n
  n=$(q "SELECT COUNT(*) FROM \"$1\";")
  echo "${n:-0}"
}

PASS=0; FAIL=0; SKIP=0
emit(){ # $1=state $2=line
  case "$1" in
    PASS) PASS=$((PASS+1)) ;;
    FAIL) FAIL=$((FAIL+1)) ;;
    SKIP) SKIP=$((SKIP+1)) ;;
  esac
  printf '%-4s %s\n' "[$1]" "$2"
}

# Generic "0->populated" organ assertion with the degradation contract.
organ_assert(){ # $1=label $2=table
  local label="$1" tbl="$2" ex rc
  ex=$(table_exists "$tbl")
  if [ "$ex" != "1" ]; then
    emit SKIP "$label : table '$tbl' absent -> organ not built yet"
    return
  fi
  rc=$(row_count "$tbl")
  if [ "${rc:-0}" -gt 0 ]; then
    emit PASS "$label : $tbl=$rc rows"
  else
    emit FAIL "$label : $tbl present but EMPTY (built, never ran)"
  fi
}

echo "================ Voidloom HARNESS ASSERTIONS ================"
echo "DB=$DB"
echo "------------------------------------------------------------"

# ===== Inherited 6 organs (L4) -- kept verbatim in spirit, with PASS/FAIL ====
echo "-- L4 organs --"
# A: recall-rerank is validated by the no-regression eval (run_gate.sh), not a
#    table count; here we just note it (always informational).
emit SKIP "A recall-rerank : validated by run_gate.sh no-regression eval (not a table count)"

organ_assert "B gap-research " "gap_research_log"
# B also requires at least one dispatched batch.
if [ "$(table_exists gap_inventory_batches)" = "1" ]; then
  Bd=$(q 'SELECT COUNT(*) FROM gap_inventory_batches WHERE research_dispatched>0;')
  if [ "${Bd:-0}" -gt 0 ]; then emit PASS "B gap-dispatch  : dispatched_batches=$Bd"; else emit FAIL "B gap-dispatch  : 0 dispatched batches"; fi
else
  emit SKIP "B gap-dispatch  : gap_inventory_batches absent"
fi

organ_assert "C self-eval    " "self_eval_enacted_proposals"
organ_assert "D pre-cognitive" "pre_cognitive_recall_log"
organ_assert "E1 usage-feed  " "skill_usage_log"

# E2 cron cognitive-map: rows with trigger='cron' + an armed timer.
if [ "$(table_exists map_generation_log)" = "1" ]; then
  E2=$(q "SELECT COUNT(*) FROM map_generation_log WHERE trigger='cron';")
  TMR=$(systemctl --user list-timers wizard-library-cognitive-map.timer --no-pager 2>/dev/null | grep -c cognitive-map)
  if [ "${E2:-0}" -gt 0 ] && [ "${TMR:-0}" -gt 0 ]; then
    emit PASS "E2 cog-map     : cron_rows=$E2 timer_armed=$TMR"
  else
    emit FAIL "E2 cog-map     : cron_rows=$E2 timer_armed=$TMR (need both >0)"
  fi
else
  emit SKIP "E2 cog-map     : map_generation_log absent"
fi

# ===== L5 organ: Trust verb (LIVE today) =====================================
echo "-- L5 organs --"
# Trust is a read-time pure function (no dedicated table); assert the VERB
# responds and returns a score in [0,1]. id 5 is a stable anchor (=0.9494).
TRUST_JSON=$(python3 "$CLI" trust --id 5 --json 2>/dev/null)
TRUST_OK=$(printf '%s' "$TRUST_JSON" | python3 -c '
import sys, json, re
raw = sys.stdin.read()
m = re.search(r"\{.*\}\s*$", raw, re.S)
if not m:
    print("NO_JSON"); sys.exit(0)
try:
    d = json.loads(m.group())
except Exception as e:
    print("BAD_JSON:%s" % e); sys.exit(0)
# accept "score" or "trust" or "base_score" -- find a [0,1] float
val = None
for k in ("score", "trust", "trust_score"):
    if isinstance(d.get(k), (int, float)):
        val = float(d[k]); break
if val is None:
    # fall back to scanning any top-level float in [0,1]
    for v in d.values():
        if isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0:
            val = float(v); break
if val is None:
    print("NO_SCORE"); sys.exit(0)
print("%.4f" % val if 0.0 <= val <= 1.0 else "OUT_OF_RANGE:%s" % val)
')
case "$TRUST_OK" in
  NO_JSON|BAD_JSON*|NO_SCORE|OUT_OF_RANGE*)
    emit FAIL "I trust-verb   : trust --id 5 --json did not return a score in [0,1] ($TRUST_OK)" ;;
  *)
    emit PASS "I trust-verb   : trust(#5)=$TRUST_OK in [0,1] (verb wired)" ;;
esac

# ===== Not-yet-built L5 organ stubs (degrade to SKIP until landed) ============
# Each entry: "label|table". When a piece lands and populates its table, the
# stub flips SKIP -> PASS automatically. If a piece creates the table but never
# runs (empty), the stub FAILs -- catching the "built but inert" defect.
# NOTE: these table names are the harness's BEST-KNOWN names; a piece-builder
# updates the matching row here when they finalise their schema.
L5_STUBS=(
  "A  assoc-recall  |ppr_recall_log"           # L5-A  PPR associative recall
  "D  belief-engine |belief_edges"             # L5-D  belief engine / edge remediation
  "F  dream-forward |dream_forward_log"        # L5-F  dream pipeline (forward)
  "G  dream-ground  |dream_grounding_log"      # L5-G  dream pipeline (grounding)
  "E  comm-pred     |comm_prediction_log"      # L5-E  comm / prediction organ
)
for entry in "${L5_STUBS[@]}"; do
  label="${entry%%|*}"; tbl="${entry##*|}"
  organ_assert "$label" "$tbl"
done

echo "------------------------------------------------------------"
echo "SUMMARY: PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"
echo "============================================================"

# Gate semantics: FAIL only if a built organ is empty / trust verb broke.
# SKIPs (not-yet-built) never fail the gate.
[ "$FAIL" -eq 0 ]
