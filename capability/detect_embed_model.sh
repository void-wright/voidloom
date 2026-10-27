#!/usr/bin/env bash
# ============================================================================
# Voidloom L5-X "The Adaptive Loom" -- detect_embed_model shell shim.
# ============================================================================
# Thin, fail-soft shell wrapper around capability/detect.py. Replaces the
# 4-line binary detect_embed_model() in install.sh with the rich stdlib
# detector while preserving the EXACT install.sh contract: echo "jina" or
# "nomic" (or the qwen3-0.6b tier model) to stdout, honour WIZARD_EMBED_MODEL.
#
# THE TIER-0 GUARANTEE: this is pure shell + a stdlib python3 invocation. No
# venv, no torch, no third-party tool. If python3 or the detector is somehow
# unavailable, it fail-softs to the same nvidia-smi heuristic the original
# detect_embed_model() used -- and ultimately to "nomic" -- so an install
# NEVER crashes on detection.
#
# Usage:
#   detect_embed_model.sh              # -> echoes the chosen embed model name
#   detect_embed_model.sh --json       # -> echoes the full capability JSON
#   detect_embed_model.sh --block      # -> echoes the additive manifest block JSON
#
# Env:
#   WIZARD_EMBED_MODEL   explicit override (jina|nomic|qwen3-0.6b) -- wins.
#   WIZARD_PYTHON_BIN    python3 to use (else python3 on PATH).
# ============================================================================
set -euo pipefail

SHIM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECT_PY="${SHIM_DIR}/detect.py"

# python3 detection mirrors install.sh detect_python().
pick_python() {
    if [ -n "${WIZARD_PYTHON_BIN:-}" ]; then echo "$WIZARD_PYTHON_BIN"; return; fi
    if command -v python3 >/dev/null 2>&1; then command -v python3; return; fi
    for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        [ -x "$p" ] && { echo "$p"; return; }
    done
    echo "python3"
}
PYTHON_BIN="$(pick_python)"

MODE="model"
case "${1:-}" in
    --json)  MODE="json" ;;
    --block) MODE="block" ;;
esac

# --- fast path: explicit override (model mode only) ---
if [ "$MODE" = "model" ] && [ -n "${WIZARD_EMBED_MODEL:-}" ]; then
    echo "$WIZARD_EMBED_MODEL"
    exit 0
fi

# --- primary: the stdlib detector ---
if [ -f "$DETECT_PY" ] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    case "$MODE" in
        json)
            if OUT="$("$PYTHON_BIN" "$DETECT_PY" --json 2>/dev/null)"; then
                printf '%s\n' "$OUT"; exit 0
            fi
            ;;
        block)
            if OUT="$("$PYTHON_BIN" "$DETECT_PY" --manifest-block --json 2>/dev/null)"; then
                printf '%s\n' "$OUT"; exit 0
            fi
            ;;
        model)
            # Pull chosen_embed_model out of the JSON with stdlib json (no jq dep).
            if OUT="$("$PYTHON_BIN" "$DETECT_PY" --json 2>/dev/null \
                      | "$PYTHON_BIN" -c 'import sys,json; print(json.load(sys.stdin)["chosen_embed_model"])' 2>/dev/null)"; then
                if [ -n "$OUT" ]; then echo "$OUT"; exit 0; fi
            fi
            ;;
    esac
fi

# --- fail-soft fallback: the original nvidia-smi heuristic ---
# (only reached if python3/detector are unavailable; preserves old behaviour)
if [ "$MODE" = "model" ]; then
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        echo "jina"
    else
        echo "nomic"
    fi
    exit 0
fi

# json/block modes with no python3: emit a minimal CPU-floor object.
echo '{"tier":"CPU","chosen_embed_model":"nomic","degraded":true,"reason":"detector unavailable -- shell floor"}'
