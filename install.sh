#!/usr/bin/env bash
# ============================================================================
# Voidloom — Brain Install (Linux/WSL2-first; macOS/BSD-clean)
# ============================================================================
# Reconciled FULL installer for the voidloom memory brain. Drop the package
# anywhere, run this script, and it lays a runnable tool onto a fresh machine:
#   1. lays every tracked file where install/lay_manifest.json says it lands
#      (ONE line: install/lay.py executes the manifest; there is no directory
#      list in this script -- Council #12, the installer is a prologue)
#   2. puts the `voidloom` command on PATH (symlink into ~/.local/bin)
#   3. obtains/verifies the brain DB (bundled snapshot, --brain-db, --attach-db,
#      or fresh init)
#   4. (Tier 1, optional) builds a venv, installs the VERSION-PINNED semantic stack,
#      pre-caches the embedder + the reranker THIS host will load, verifies
#      SEMANTIC recall
#   5. writes a marker-guarded, append-safe env file (idempotent re-runs)
#   6. runs the self_check acceptance gate so a stranger KNOWS the install works
#   7. (optional, --with-crons) arms the maintenance timers named armed-by-default
#      in install/cron_roster.json -- the ONE roster both installers read; the
#      sleep loop is in it (Council #12: a brain that never consolidates is the
#      93-day defect, shipped by design). The units are rendered by
#      install/install_crons.sh, the ONE unit template; this script renders none.
#
# EVERY WRITE OUTSIDE THE INSTALL ROOT IS DECLARED in install/write_set.json,
# per step and with a reason; `--print-write-set` prints that declaration with
# every path resolved for THIS invocation, and
# voidloom/tests/harness/outside_target_diff.py snapshots a scratch $HOME before
# and after a run and refuses on any difference the declaration does not name.
#
# THE EMBEDDER IS BRAIN-DICTATED. The installer reads the brain DB's
# active_embedding_table and aligns WIZARD_EMBED_MODEL to it (whichever model
# embedded that brain) so the recall path matches the brain's vectors out of
# the box. For a FRESH/blank brain (no metadata yet) it falls back to a GPU/CPU
# heuristic (GPU present -> qwen-1024, else nomic-768; resolve_embed_model).
#
# INSTALL != ONBOARD: INSTALL (this script) is DETERMINISTIC — lay files, wire
# the CLI, verify. ONBOARD is recall-driven and happens on the first session.
#
# TWO-TIER, lite-first:
#   * Tier 0 (ALWAYS succeeds, stdlib-only): the engine + the brain DB + the
#     `voidloom` CLI on PATH. Keyword recall works on ANY python3.
#   * Tier 1 (OPTIONAL upgrade, graceful fallback): a venv with the semantic
#     stack + the cached embedder + reranker. Adds SEMANTIC recall. Any failure
#     falls back cleanly to Tier 0 with exact upgrade instructions (exit 0).
#
# Usage:
#   bash install.sh                          # full install (bundled brain; tier auto)
#   bash install.sh --brain-db /path/to.db   # use an explicit brain DB snapshot (COPIED into place)
#   bash install.sh --attach-db /abs/path.db # ATTACH an existing brain IN PLACE: nothing is copied.
#                                            # Refused (rc 2) if the path is missing or has no
#                                            # `memories` table, or if --brain-db/--init-blank is
#                                            # also given. A pre-image is written BESIDE the brain
#                                            # via sqlite3 backup() (never cp) before the migrator
#                                            # runs; the env file gets WIZARD_LIBRARY_DB='<path>'.
#                                            # The agnostic seed pack is loaded once if absent.
#                                            # With Tier 1, every unembedded memory in the brain is
#                                            # embedded (embedding is part of installation); pass
#                                            # --tier0-only to skip that. --dry-run prints the
#                                            # pre-image path it would write.
#   bash install.sh --init-blank             # initialize a fresh empty brain (new user)
#   bash install.sh --force-cpu              # force the CPU EMBEDDER (nomic-768) even if a GPU exists (REFUSED with --tier1-ambient).
#                                            # Also RECORDED: WIZARD_ACCELERATOR=cpu outside the managed block of the env
#                                            # file, under a dated comment, so every job of the brain stays on the CPU
#                                            # (doctor: cpu_only True). Same as --accelerator cpu.
#   bash install.sh --accelerator auto|cuda|rocm|mps|xpu|cpu
#                                            # which compute the brain uses, and so which torch wheel CHANNEL the venv
#                                            # installs (install/accelerator_plan.py). auto (the default) asks the
#                                            # organs' resolver; a value that contradicts this machine is REFUSED.
#                                            # The run SAYS the machine, the backend, its proof label, the channel and
#                                            # the reranker decision with its reason before anything is installed.
#                                            # It does NOT change which torch wheel pip installs: on Linux
#                                            # the default PyPI torch IS the CUDA build (53 dists, 15
#                                            # nvidia-* wheels -- measured 2026-09-16). For a genuinely
#                                            # CPU-only stack see install/requirements.txt's CPU-ONLY note.
#   bash install.sh --tier0-only             # skip the Tier 1 venv attempt entirely
#   bash install.sh --tier1-ambient /abs/python3
#                                            # UPGRADE an install whose Tier 1 runs through an interpreter
#                                            # OUTSIDE the brain (no venv): no venv, no pip, no model
#                                            # pre-cache, no re-embedding, tier0_only never written; the
#                                            # recall tier, the interpreter and the vector population are
#                                            # left exactly as found. Accepted only when a brain exists,
#                                            # declares a Tier-1 table, is not tier0_only, and owns no
#                                            # venv -- else refused rc 2 naming why. The interpreter is
#                                            # PROVEN first (install/tier1_probe.py: imports, a vec query, the
#                                            # pin vs every card's LIVE display_active -- none Enabled: declare
#                                            # WIZARD_DISPLAY_INDEX=<n>|none AND the pin outside the block -- one offline
#                                            # semantic recall), then recorded in <brain>/INSTALL_RECORD; later
#                                            # runs need no flag (a DIFFERENT path is refused: move the record aside).
#   bash install.sh --with-crons             # also arm the armed-by-default roster (default OFF):
#                                            # install/cron_roster.json names every packaged timer
#                                            # as armed-by-default or ships-as-template, with a reason
#   bash install.sh --env-only               # only (re)write the env file (no scheduler, no Tier1)
#   bash install.sh --gpu <n> --display-gpu <m|none>
#                                            # a host with TWO OR MORE GPUs and no WIZARD_CUDA_DEVICE (nor
#                                            # CUDA_VISIBLE_DEVICES) declared outside the env file's managed block:
#                                            # the ANSWERS to the two questions the installer otherwise asks at the
#                                            # terminal -- which card drives the monitor(s), which card the brain's
#                                            # background jobs use. Recorded, outside the block, as the owner's
#                                            # WIZARD_DISPLAY_INDEX= and WIZARD_CUDA_DEVICE= lines (dated "chosen at
#                                            # install"); a re-run reads them and never asks again. With no terminal
#                                            # and no flags such a host is REFUSED (rc 2) before anything is touched,
#                                            # the cards listed: the installer never picks the card. An installing AI
#                                            # agent must ask the human and pass the human's answers -- never choose.
#                                            # A shell's CUDA_VISIBLE_DEVICES / WIZARD_CUDA_DEVICE answers NOTHING.
#   bash install.sh --thinker claude|codex|kimi|gemini|off|local
#                                            # the OWNER's answer to the overnight-thinker question, where no
#                                            # terminal can ask it: "Should your brain think while you sleep, and
#                                            # with which model?"  a) Claude  b) Codex  c) Kimi  d) Gemini (Google;
#                                            # runs through Google's Antigravity program)  e) Not now -- no model;
#                                            # keep the simple nightly routines  f) Other (which model?). Pass the
#                                            # NAME (the letters still work; d and f changed meaning on 2026-10-16
#                                            # and print a note when passed as a flag).
#                                            # Asked at the terminal on EVERY interactive install and upgrade,
#                                            # before anything is laid; a recorded answer (<brain>/THINKER_RECORD)
#                                            # is re-used and PROVEN again, never re-asked. With no terminal, no
#                                            # answer and timers armed or about to be (--with-crons): REFUSED rc 2
#                                            # before anything is touched. For f) (local) also --thinker-model <name> and
#                                            # --thinker-endpoint <http://...> [--thinker-api ollama|openai] or
#                                            # --thinker-command '<template with {model} {prompt}>'. An installing
#                                            # AI agent must put the question to the human word for word and pass
#                                            # the human's answer -- never choose it (docs/OVERNIGHT_THINKER_SETUP.md).
#   bash install.sh --install-thinker-cli    # the OWNER's yes to "Run it now? [y/N]": when the answer's program
#                                            # (claude, codex, kimi, agy) is not installed, run the vendor's own
#                                            # official installer (shown first, word for word; never sudo), then
#                                            # find the program and use it by full path. Without it and with no
#                                            # terminal, a missing program is REFUSED (rc 2, nothing touched) with
#                                            # the exact command in the refusal. An installing AI agent passes it
#                                            # only on the human's yes to "may I run <vendor>'s installer?".
#   bash install.sh --approvals auto|manual  # the OWNER's approvals choice (2026-10-26): once a month the self-eval
#                                            # proposes changes; auto = the brain approves its own proposals (signed
#                                            # policy:auto, named at each session start for 7 days), manual = they wait for
#                                            # you in `voidloom approvals --tui`. Belief links and governance holds
#                                            # always wait for a person. Asked at a terminal (blank = auto); a recorded
#                                            # choice is re-used; with no terminal a NEW install takes auto and an
#                                            # UPGRADE with no recorded choice is left as it is. Change it any time:
#                                            # voidloom approvals --mode auto|manual
#   bash install.sh --load-seed              # an UPGRADE (the ambient shape) adds no memories by itself; this is the
#                                            # OWNER's answer "yes, add the seed pack's new and changed entries"
#                                            # (a changed entry's older memory is archived, never deleted). Asked at a
#                                            # terminal when the pack holds entries this brain has not received; with
#                                            # no terminal and no flag they are REPORTED as the owner's choice, never
#                                            # loaded. A fresh install always loads the whole pack.
#   bash install.sh --wire-hooks             # STEP P's question, answered yes where no terminal can ask it: wire the
#                                            # SessionStart hook (voidloom hooks install --platform claude) so the first
#                                            # session offers the Persona Forge. --no-wire-hooks answers no. Unanswered
#                                            # with no terminal = not wired (the final banner says so).
#   bash install.sh --print-write-set        # print the declared write-set (install/write_set.json)
#                                            # with every path resolved for this invocation; exit 0,
#                                            # nothing written. Combine with the flags you intend.
#   bash install.sh --dry-run                # print what would happen; make no changes
#   bash install.sh --preflight [--python P] # BEFORE installing: prove this machine (venv, torch, sqlite-vec, the
#                                            # golden vectors on its accelerator) in a scratch folder that is then
#                                            # deleted; writes preflight.json to send back. Installs nothing.
#   bash install.sh --help
#
# Idempotent — safe to re-run. Fail-loud on the parts that matter (Tier 0);
# fail-soft (and self-explaining) on the optional upgrade. Detects macOS via
# uname -s=Darwin. bash 3.2 / BSD-userland clean.
# ============================================================================
set -uo pipefail
# NOTE: deliberately NOT `set -e`. Tier 1 is allowed to fail without aborting
# the install; the critical (Tier 0) steps are guarded with explicit checks.

# ----------------------------------------------------------------------------
# 0. SELF-LOCATE PACKAGE + PARSE ARGS + PLATFORM
# ----------------------------------------------------------------------------
PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# `bash install.sh --preflight [...]` (handoff round 1, L4, decision D1): prove this machine BEFORE anything is installed
# -- install/preflight.py, which installs nothing into the home and writes one file to send back (preflight.json). It
# answers first, like --help: no path check, no question, no write outside its own scratch folder.
if [ "${1:-}" = "--preflight" ]; then
    shift
    _pf_py="${WIZARD_PYTHON_BIN:-$(command -v python3 || true)}"
    [ -n "$_pf_py" ] || { echo "[install] --preflight needs a python3 on PATH (or WIZARD_PYTHON_BIN=<path>)." >&2; exit 2; }
    exec "$_pf_py" -B "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install/preflight.py" "$@"
fi
OS="$(uname -s)"; for _a in "$@"; do case "$_a" in --help|-h) awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"; exit 0 ;; esac; done; unset _a   # Darwin | Linux. --help answers FIRST: a home the path checks below refuse can still read it
CLAUDE_HOME="${WIZARD_CLAUDE_HOME:-${HOME}/.claude}"
# A path override that is not ABSOLUTE is refused, the shape --attach-db already has: a relative
# value, a quoted `~/x` (never tilde-expanded) or a literal `$HOME/x` would lay the whole brain
# relative to the cwd and write that spelling into the env file and into every unit (round-1
# iteration-3 reflection, item 2: measured, `--dry-run` said "Install into: rel_claude").
case "$CLAUDE_HOME" in /*) ;; *) echo "[install] WIZARD_CLAUDE_HOME must be an ABSOLUTE path (it is written into the env file and into every unit): '${CLAUDE_HOME}'" >&2; exit 2 ;; esac; case "$CLAUDE_HOME" in *%*|*\?*|*#*) echo "[install] WIZARD_CLAUDE_HOME must not contain '%', '?' or '#': systemd expands '%' as a unit specifier in every unit line, and '?'/'#' end the path of the SQLite URIs the organs open the brain with (a read would open, and may CREATE, the path's prefix): '${CLAUDE_HOME}'" >&2; exit 2 ;; esac
BRAIN_DIR="${CLAUDE_HOME}/memory"
SKILLS_DIR="${CLAUDE_HOME}/skills"
DB_PATH="${BRAIN_DIR}/claude_memory.db"
# THE SIDECAR ENV FILE -- ONE PATH, AND THE UNITS AGREE WITH IT.
#
# CRUCIBLE-3 D9-F4 (found while re-deriving FIRST_NIGHT.md): the package once had
# three answers for this file, and the units disagreed with the installer, so a
# miss was SILENT on some units and a hard failure on others.
#
# ~/.voidloom/ is the product's own directory, it already exists on a running
# install (canary_control, heartbeat), and it is what the units and the docs say.
# It is the single answer; WIZARD_ENV_FILE overrides it.
ENV_FILE_DEFAULT="${HOME}/.voidloom/wizard-library-env"
if [ -n "${WIZARD_ENV_FILE:-}" ]; then
    ENV_FILE="$WIZARD_ENV_FILE"
    case "$ENV_FILE" in /*) ;; *) echo "[install] WIZARD_ENV_FILE must be an ABSOLUTE path (every unit's EnvironmentFile= names it): '${ENV_FILE}'" >&2; exit 2 ;; esac
    ENV_FILE_ORIGIN="WIZARD_ENV_FILE"
else
    ENV_FILE="$ENV_FILE_DEFAULT"
    ENV_FILE_ORIGIN="package default"
fi
ENV_DIR="$(dirname "${ENV_FILE}")"; case "$ENV_FILE" in *%*|*\**|*\?*|*\[*|*\\*) echo "[install] the env file path '${ENV_FILE}' (${ENV_FILE_ORIGIN}) must not contain '%', '*', '?', '[' or a backslash: every unit names it as EnvironmentFile=, where systemd expands '%' as a specifier and GLOBS the path (it would load another file, or none -- silently, under the leading '-')" >&2; exit 2 ;; esac
# The sidecar CONFIG dir and the sleep-loop config path come from the ONE
# resolver the cron scripts use (cron/_config_dir.sh: per-file, legacy-aware,
# never a second literal here). Sourced from the package; it defines functions
# and two defaults and executes nothing.
if [ -f "${PKG_ROOT}/cron/_config_dir.sh" ]; then
    . "${PKG_ROOT}/cron/_config_dir.sh"
    CONFIG_DIR="${CONFIG_DIR_DEFAULT}"
    SLEEP_CFG="$(_config_path sleep-loop-config.json)"
else
    CONFIG_DIR="${WIZARD_CONFIG_DIR:-${HOME}/.voidloom}"
    SLEEP_CFG="${CONFIG_DIR}/sleep-loop-config.json"
fi
case "$CONFIG_DIR" in /*) ;; *) echo "[install] WIZARD_CONFIG_DIR must be an ABSOLUTE path (it is written into the sleep-loop unit's ExecStart and is where the config is copied): '${CONFIG_DIR}'" >&2; exit 2 ;; esac; case "$CONFIG_DIR" in *%*) echo "[install] WIZARD_CONFIG_DIR must not contain '%' (systemd expands it as a unit specifier in the sleep-loop unit's ExecStart=): '${CONFIG_DIR}'" >&2; exit 2 ;; esac
LOCAL_BIN="${HOME}/.local/bin"
OWNER_NAME="${WIZARD_OWNER_NAME:-the brain}"
# THE INVOKING SHELL'S PIN VARIABLES, captured before anything below exports one (env-file writer pass, iteration 5, G1 /
# EW4-04 / EW4-10). A CUDA_VISIBLE_DEVICES or WIZARD_CUDA_DEVICE set in the shell that runs this installer is NOT a
# declaration and answers no question: it may have been exported for another tool, or by an AI agent, and nothing says
# the owner chose it. So it is IGNORED -- this run neither pins nor writes anything from it; the card comes from the env
# file's declaration (or the owner's answer, recorded as one) -- and the run SAYS so (gpu_shell_note) and records it
# (INSTALL_RECORD gpu_invoking_shell). Measured on the iteration-4 bytes: a shell's WIZARD_CUDA_DEVICE=0 became every
# unit's durable pin, unrecorded as the shell's (EW4-04), and outranked the file's declaration for this run's own model
# loads (EW4-10). Values of the two pin keys only (never another variable's).
SHELL_PIN_VARS=""
[ -n "${CUDA_VISIBLE_DEVICES+set}" ] && SHELL_PIN_VARS="CUDA_VISIBLE_DEVICES='${CUDA_VISIBLE_DEVICES}'"
[ -n "${WIZARD_CUDA_DEVICE+set}" ] && SHELL_PIN_VARS="${SHELL_PIN_VARS:+$SHELL_PIN_VARS }WIZARD_CUDA_DEVICE='${WIZARD_CUDA_DEVICE}'"
# THE WRITER'S RULE, not a second one: install/install_crons.sh writes the units to
# ${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user, and this declaration must resolve
# to the same directory (round-1 iteration-2 reflection, item 1: the two diverged and
# a caller's XDG_CONFIG_HOME let 16 unit files escape the snapshot under GREEN).
# Same rule for the Tier-1 caches pip and huggingface_hub write under
# ${XDG_CACHE_HOME:-$HOME/.cache} (install/write_set.json Step C).
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
LAUNCHD_DIR="${HOME}/Library/LaunchAgents"
# MUST equal cron/_config_dir.sh LAUNCHD_PREFIX -- one product, one label family;
# voidloom/tests/test_launchd_prefix_agrees.py refuses if the two literals drift.
# Used here by --print-write-set to resolve ${LAUNCHD_PREFIX} in install/write_set.json
# (the plists install_crons.sh writes and the launchctl verbs it calls on Darwin).
LAUNCHD_PREFIX="voidloom"

# Marker fence for the append-safe env-file write (M5). Managed lines live
# strictly between these markers; anything outside survives a re-run intact.
ENV_BEGIN="# >>> wizard-library managed >>>"
ENV_END="# <<< wizard-library managed <<<"

DRY_RUN=0; TIER0_ONLY=0; WITH_CRONS=0; ENV_ONLY=0; FORCE_CPU=0
INIT_BLANK=0; PRINT_WRITE_SET=0; LOAD_SEED=0
WIRE_HOOKS=""                        # --wire-hooks / --no-wire-hooks: STEP P's question; unset + no terminal = not wired
GPU_ANSWER=""; DISPLAY_ANSWER=""     # --gpu / --display-gpu: the owner's answers to G1's two questions
THINKER_FLAG=""; THINKER_MODEL=""; THINKER_ENDPOINT=""; THINKER_API=""; THINKER_COMMAND=""   # --thinker ... (M10)
INSTALL_THINKER_CLI=0                # --install-thinker-cli: the owner's yes to running the vendor's own installer
APPROVALS_FLAG=""                    # --approvals auto|manual: the owner's approvals choice (2026-10-26)
EXPLICIT_BRAIN_DB=""
ATTACH_DB=""
TIER1_AMBIENT=""
ACCEL_REQUEST=""                     # --accelerator auto|cuda|rocm|mps|xpu|cpu (handoff round 1, L4)
for arg in "$@"; do
    case "$arg" in
        --dry-run)      DRY_RUN=1 ;;
        --tier0-only)   TIER0_ONLY=1 ;;
        --with-crons)   WITH_CRONS=1 ;;
        --env-only)     ENV_ONLY=1 ;;
        --force-cpu)    FORCE_CPU=1 ;;
        --init-blank)   INIT_BLANK=1 ;;
        --print-write-set) PRINT_WRITE_SET=1 ;;
        --load-seed)    LOAD_SEED=1 ;;
        --wire-hooks)   WIRE_HOOKS=1 ;;
        --no-wire-hooks) WIRE_HOOKS=0 ;;
        --brain-db)     EXPLICIT_BRAIN_DB="__EXPECT__" ;;
        --attach-db)    ATTACH_DB="__EXPECT__" ;;
        --tier1-ambient) TIER1_AMBIENT="__EXPECT__" ;;
        --gpu)          GPU_ANSWER="__EXPECT__" ;;
        --display-gpu)  DISPLAY_ANSWER="__EXPECT__" ;;
        --accelerator)  ACCEL_REQUEST="__EXPECT__" ;;
        --preflight)    echo "[install] --preflight runs on its own, as the FIRST argument: bash install.sh --preflight [--python P] [--out FILE] -- it installs nothing. Nothing was touched." >&2; exit 2 ;;
        --thinker)          THINKER_FLAG="__EXPECT__" ;;
        --thinker-model)    THINKER_MODEL="__EXPECT__" ;;
        --thinker-endpoint) THINKER_ENDPOINT="__EXPECT__" ;;
        --thinker-api)      THINKER_API="__EXPECT__" ;;
        --thinker-command)  THINKER_COMMAND="__EXPECT__" ;;
        --install-thinker-cli) INSTALL_THINKER_CLI=1 ;;
        --approvals)        APPROVALS_FLAG="__EXPECT__" ;;
        --help|-h)
            # CRUCIBLE-3 D7-F5: this was `sed -n '2,50p'` -- a TYPED line number
            # into the script's own source. Correct when written; three lines past
            # the header the moment the header grew, so `--help` ended with
            # `set -uo pipefail` and two lines of the comment beneath it. A help
            # text that leaks its own implementation is the smallest possible
            # version of this cycle's whole subject: a figure nobody re-derives.
            #
            # The header ends where the comments end. awk finds that, so the
            # header may grow to any length and --help still stops at the right
            # place. Line 1 is the shebang and is skipped.
            awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' \
                "${BASH_SOURCE[0]}"
            exit 0 ;;
        *)
            if [ "$EXPLICIT_BRAIN_DB" = "__EXPECT__" ]; then
                EXPLICIT_BRAIN_DB="${arg:-__EMPTY__}"
            elif [ "$ATTACH_DB" = "__EXPECT__" ]; then
                ATTACH_DB="${arg:-__EMPTY__}"
            elif [ "$TIER1_AMBIENT" = "__EXPECT__" ]; then
                TIER1_AMBIENT="${arg:-__EMPTY__}"
            elif [ "$GPU_ANSWER" = "__EXPECT__" ]; then
                GPU_ANSWER="${arg:-__EMPTY__}"
            elif [ "$DISPLAY_ANSWER" = "__EXPECT__" ]; then
                DISPLAY_ANSWER="${arg:-__EMPTY__}"
            elif [ "$ACCEL_REQUEST" = "__EXPECT__" ]; then
                ACCEL_REQUEST="${arg:-__EMPTY__}"
            elif [ "$THINKER_FLAG" = "__EXPECT__" ]; then
                THINKER_FLAG="${arg:-__EMPTY__}"
            elif [ "$THINKER_MODEL" = "__EXPECT__" ]; then
                THINKER_MODEL="${arg:-__EMPTY__}"
            elif [ "$THINKER_ENDPOINT" = "__EXPECT__" ]; then
                THINKER_ENDPOINT="${arg:-__EMPTY__}"
            elif [ "$THINKER_API" = "__EXPECT__" ]; then
                THINKER_API="${arg:-__EMPTY__}"
            elif [ "$THINKER_COMMAND" = "__EXPECT__" ]; then
                THINKER_COMMAND="${arg:-__EMPTY__}"
            elif [ "$APPROVALS_FLAG" = "__EXPECT__" ]; then
                APPROVALS_FLAG="${arg:-__EMPTY__}"
            else
                echo "[install] Unknown arg: $arg (try --help)"; exit 2
            fi ;;
    esac
done
[ "$EXPLICIT_BRAIN_DB" = "__EXPECT__" ] && { echo "[install] --brain-db requires a path"; exit 2; }
[ "$ATTACH_DB" = "__EXPECT__" ] && { echo "[install] --attach-db requires a path"; exit 2; }
[ "$TIER1_AMBIENT" = "__EXPECT__" ] && { echo "[install] --tier1-ambient requires the ABSOLUTE path of a python3" >&2; exit 2; }
# MC-R1-05 (Round 1 iteration 2): an EXPLICITLY EMPTY value (`--flag ""`, an agent's empty "$ANSWER") is refused, never
# taken as an absent flag -- measured: `--thinker ''` with no terminal laid a brain, 'not asked'; an empty --attach-db or
# --tier1-ambient would change the install's SHAPE in silence. The class: every flag that takes a value.
_refuse_empty() { [ "$2" = "__EMPTY__" ] && { echo "[install] $1 was given an EMPTY value -- pass $3, never a blank (a blank answer is asked again at a terminal, and refused here). Nothing was touched." >&2; exit 2; }; return 0; }
_refuse_empty --brain-db "$EXPLICIT_BRAIN_DB" "a path"
_refuse_empty --attach-db "$ATTACH_DB" "a path"
_refuse_empty --tier1-ambient "$TIER1_AMBIENT" "the ABSOLUTE path of a python3"
case "$APPROVALS_FLAG" in
    ""|auto|manual) ;;
    __EXPECT__) echo "[install] --approvals requires a value: auto or manual. Nothing was touched." >&2; exit 2 ;;
    __EMPTY__) echo "[install] --approvals was given an EMPTY value -- pass auto or manual. Nothing was touched." >&2; exit 2 ;;
    *) echo "[install] --approvals takes auto or manual, not '${APPROVALS_FLAG}'. Nothing was touched." >&2; exit 2 ;;
esac
case "$ACCEL_REQUEST" in
    "") ACCEL_REQUEST="auto" ;;
    __EXPECT__) echo "[install] --accelerator requires a value: auto, cuda, rocm, mps, xpu or cpu. Nothing was touched." >&2; exit 2 ;;
    __EMPTY__) echo "[install] --accelerator was given an EMPTY value -- pass auto, cuda, rocm, mps, xpu or cpu. Nothing was touched." >&2; exit 2 ;;
    auto|cuda|rocm|mps|xpu|cpu) ;;
    *) echo "[install] --accelerator takes auto, cuda, rocm, mps, xpu or cpu, not '${ACCEL_REQUEST}'. Nothing was touched." >&2; exit 2 ;;
esac
# --force-cpu IS --accelerator cpu (one decision, two spellings); any other explicit backend contradicts it
if [ "$FORCE_CPU" -eq 1 ]; then
    case "$ACCEL_REQUEST" in
        auto|cpu) ACCEL_REQUEST="cpu" ;;
        *) echo "[install] --force-cpu and --accelerator ${ACCEL_REQUEST} contradict each other: --force-cpu means the CPU. Pass one of them. Nothing was touched." >&2; exit 2 ;;
    esac
elif [ "$ACCEL_REQUEST" = "cpu" ]; then
    FORCE_CPU=1
fi
case "$GPU_ANSWER" in
    "") ;;
    __EXPECT__) echo "[install] --gpu requires a card number (the card the brain's background jobs use). Nothing was touched." >&2; exit 2 ;;
    __EMPTY__) echo "[install] --gpu was given an EMPTY value -- pass a card number. Nothing was touched." >&2; exit 2 ;;
    -1|[Nn][Oo][Nn][Ee]) echo "[install] --gpu ${GPU_ANSWER} REFUSED: WIZARD_CUDA_DEVICE takes a card index only; '-1' / 'none' are not written by this installer (a hand-written one means CPU only). To keep this run off every GPU pass --force-cpu; to leave the brain's jobs unpinned write WIZARD_CUDA_DEVICE= (EMPTY) by hand outside the managed block. Nothing was touched." >&2; exit 2 ;;
    *[!0-9]*) echo "[install] --gpu takes a card number as nvidia-smi lists it (0, 1, ...), not '${GPU_ANSWER}'. Nothing was touched." >&2; exit 2 ;;
esac
# --thinker: the owner's answer, compared as FIXED TEXT (the answer is the case SUBJECT, never a pattern: R-2)
_tq="[install] --thinker answers the overnight-thinker question: a) Claude  b) Codex  c) Kimi  d) Gemini  e) Not now  f) Other (which model?)"
_thinker_needs_value() { case "$2" in __EXPECT__) echo "[install] $1 requires a value. Nothing was touched." >&2; exit 2 ;; __EMPTY__) echo "[install] $1 was given an EMPTY value -- pass the owner's answer, never a blank (a blank answer is asked again at a terminal, and refused here). Nothing was touched." >&2; exit 2 ;; esac; return 0; }
_thinker_needs_value --thinker "$THINKER_FLAG"; _thinker_needs_value --thinker-model "$THINKER_MODEL"
_thinker_needs_value --thinker-endpoint "$THINKER_ENDPOINT"; _thinker_needs_value --thinker-api "$THINKER_API"
_thinker_needs_value --thinker-command "$THINKER_COMMAND"
# 2026-10-16 (the owner's ruling): d) is Gemini and Other moved to f); e) Not now kept its letter. The NAME is the
# documented non-interactive form; a letter whose meaning changed prints a one-release note (the words are the
# catalog's question.letter_notes, bound by voidloom/tests/test_thinker_get_cleanroom.py).
THINKER_LETTER_NOTE=""
case "$THINKER_FLAG" in
    "") ;;
    a|A|claude) THINKER_FLAG="claude" ;;
    b|B|codex)  THINKER_FLAG="codex" ;;
    c|C|kimi)   THINKER_FLAG="kimi" ;;
    d|D)        THINKER_FLAG="gemini"; THINKER_LETTER_NOTE=d ;;
    gemini)     THINKER_FLAG="gemini" ;;
    e|E|off)    THINKER_FLAG="off" ;;
    f|F)        THINKER_FLAG="local"; THINKER_LETTER_NOTE=f ;;
    local)      THINKER_FLAG="local" ;;
    *) echo "${_tq}. It takes a name -- claude, codex, kimi, gemini, off or local -- (or the letter a-f), not '${THINKER_FLAG}'. Nothing was touched." >&2; exit 2 ;;
esac
case "$THINKER_API" in ""|ollama|openai) ;; *) echo "[install] --thinker-api takes ollama or openai, not '${THINKER_API}'. Nothing was touched." >&2; exit 2 ;; esac
if [ -z "$THINKER_FLAG" ] && [ -n "${THINKER_MODEL}${THINKER_ENDPOINT}${THINKER_API}${THINKER_COMMAND}" ]; then
    echo "[install] --thinker-model / --thinker-endpoint / --thinker-api / --thinker-command complete an answer given with --thinker claude|codex|kimi|gemini|off|local; there is none. Nothing was touched." >&2; exit 2
fi
if [ "$THINKER_FLAG" = "local" ]; then
    [ -n "$THINKER_MODEL" ] || { echo "${_tq}. f) Other needs the model: --thinker-model <name as its server or tool knows it>. Nothing was touched." >&2; exit 2; }
    if [ -n "$THINKER_COMMAND" ]; then
        [ -z "$THINKER_ENDPOINT$THINKER_API" ] || { echo "[install] f) Other is reached ONE way: --thinker-endpoint (a server) or --thinker-command (a command template), not both. Nothing was touched." >&2; exit 2; }
        case "$THINKER_COMMAND" in *"{prompt}"*) ;; *) echo "[install] --thinker-command must contain {prompt} (and usually {model}), e.g. 'ollama run {model} {prompt}'. Nothing was touched." >&2; exit 2 ;; esac
    else
        case "$THINKER_ENDPOINT" in http://?*|https://?*) ;; *) echo "${_tq}. f) Other needs how the brain reaches it: --thinker-endpoint <http://host:port> (with --thinker-api ollama|openai) or --thinker-command '<template with {model} {prompt}>'. Nothing was touched." >&2; exit 2 ;; esac
        [ -n "$THINKER_API" ] || { echo "[install] f) Other through a server needs what KIND of server it is: --thinker-api ollama (an ollama server) or --thinker-api openai (an OpenAI-compatible one) -- the installer never guesses it. Nothing was touched." >&2; exit 2; }
    fi
elif [ -n "$THINKER_FLAG" ] && [ -n "${THINKER_ENDPOINT}${THINKER_API}${THINKER_COMMAND}" ]; then
    echo "[install] --thinker-endpoint / --thinker-api / --thinker-command belong to f) Other (--thinker local); --thinker ${THINKER_FLAG}$([ "$THINKER_LETTER_NOTE" = d ] && printf ' (the letter d now means d) Gemini; it was Other until 2026-10-16: pass --thinker local for Other)') takes at most --thinker-model. Nothing was touched." >&2; exit 2
fi
unset _tq
case "$DISPLAY_ANSWER" in
    "") ;;
    __EXPECT__) echo "[install] --display-gpu requires a card number, or 'none' (the card that drives your monitor(s)). Nothing was touched." >&2; exit 2 ;;
    __EMPTY__) echo "[install] --display-gpu was given an EMPTY value -- pass a card number or 'none'. Nothing was touched." >&2; exit 2 ;;
    [Nn][Oo][Nn][Ee]) DISPLAY_ANSWER="none" ;;
    *[!0-9]*) echo "[install] --display-gpu takes a card number as nvidia-smi lists it (0, 1, ...), or 'none', not '${DISPLAY_ANSWER}'. Nothing was touched." >&2; exit 2 ;;
esac

# --- paths a systemd UNIT LINE carries (reference-host round, iteration 4, LOW 2 -- the class "a value that cannot be
# the literal text of a unit line"). '%' is refused above for every path a unit names (systemd expands it as a
# specifier: measured, `%h` became the home directory in ExecStart=). WHITESPACE matters only where a line splits on
# it: ExecStart= (the interpreter and every organ argument live under the brain dir, the config dir and
# ~/.local/bin) and Environment=PATH={HOME_LOCAL_BIN}:... -- so it is refused when this run renders units (Linux,
# --with-crons), and nowhere else: a home with a space in it still installs without crons (launchd plists and the
# env file quote it).
if [ "$OS" = "Linux" ] && [ "$WITH_CRONS" -eq 1 ]; then
    for _up in "WIZARD_CLAUDE_HOME|${CLAUDE_HOME}" "WIZARD_CONFIG_DIR|${CONFIG_DIR}" "HOME (for ~/.local/bin)|${LOCAL_BIN}" \
               "--attach-db|${ATTACH_DB}"; do
        case "${_up#*|}" in *[[:space:]]*|*%*) echo "[install] ${_up%%|*} must not contain whitespace (or '%') when --with-crons renders systemd units: systemd splits ExecStart= (and Environment=) on whitespace and expands '%' as a specifier, so '${_up#*|}' cannot be a literal unit token. Nothing was touched." >&2; exit 2 ;; esac
    done
    unset _up
fi

# ----------------------------------------------------------------------------
# Detect a usable python3 -- FIRST, because the --attach-db check below reads a
# brain with it and everything after needs it.
# Detect a usable python3 (prefer >= 3.10). Env override > common locations.
# No personal paths: $HOME-relative fallbacks only.
# ----------------------------------------------------------------------------
detect_python() {
    if [ -n "${WIZARD_PYTHON_BIN:-}" ] && command -v "$WIZARD_PYTHON_BIN" >/dev/null 2>&1; then
        command -v "$WIZARD_PYTHON_BIN"; return
    fi
    for p in python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 \
             "${HOME}/.pyenv/shims/python3" "${HOME}/anaconda3/bin/python3" "${HOME}/miniconda3/bin/python3"; do
        if command -v "$p" >/dev/null 2>&1; then command -v "$p"; return; fi
    done
    echo "python3"
}
PY_SYS="$(detect_python)"

# --- --attach-db: refuse BEFORE anything is touched, and re-point DB_PATH ------
#
# Council #12, round 1. `--brain-db` COPIES a snapshot into ${BRAIN_DIR}; a second
# machine that already holds a brain must not be copied, it must be
# attached in place: the machinery is laid, the env file names THAT file, the
# migrator brings it up to this package's schema behind a pre-image. Three
# refusals, each rc 2 and each naming the reason, because an attach that guesses
# is a brain-destroying guess:
#   * the path does not exist, or is not a file;
#   * it has no `memories` table (it is not a brain of this product);
#   * --brain-db or --init-blank was also given (two sources for one brain).
# Read with immutable=1 (no -wal/-shm sidecars are created by the check, so a
# --dry-run leaves the user's directory as it found it); if the table is not
# visible that way -- possible only for a schema that has never been
# checkpointed -- re-read with mode=ro. That READ runs below the sidecar guard
# (dryrun_sidecar_snapshot), which retires whatever the mode=ro read materialised.
if [ -n "$ATTACH_DB" ]; then
    if [ -n "$EXPLICIT_BRAIN_DB" ] || [ "$INIT_BLANK" -eq 1 ]; then
        echo "[install] --attach-db cannot be combined with --brain-db or --init-blank: one brain, one source. Refusing." >&2
        exit 2
    fi
    case "$ATTACH_DB" in
        *%*|*\?*|*#*) echo "[install] --attach-db: the path must not contain '%', '?' or '#' (it is written into every unit, where systemd expands '%', and every organ opens it as a SQLite URI that '?'/'#' would cut short): ${ATTACH_DB}" >&2; exit 2 ;;
        /*) ;;
        *)  echo "[install] --attach-db needs an ABSOLUTE path (it is written into the env file and into every unit): ${ATTACH_DB}" >&2; exit 2 ;;
    esac
    if [ ! -f "$ATTACH_DB" ]; then
        echo "[install] --attach-db: no such file: ${ATTACH_DB}. Nothing was touched." >&2
        exit 2
    fi
    # The brain-shape READ ("is it a brain of this product?") runs BELOW, after the sidecar guard is armed
    # (reference-host round, iteration 4, LOW 3): it used to run here, 170 lines before the guard, and its mode=ro
    # fallback materialised a -wal/-shm that nothing retired -- measured on a WAL-mode non-brain: "Refusing; nothing
    # was touched" with 2 sidecars left beside the file. DB_PATH is re-pointed now so the guard keys on this file.
    DB_PATH="$ATTACH_DB"
    ATTACH_PREIMAGE="$(dirname "$ATTACH_DB")/$(basename "$ATTACH_DB").pre-attach-$(date -u +%Y%m%dT%H%M%SZ).db"
fi

# --- the env file as it stands, OUTSIDE the managed block --------------------------
#
# Reference-host round, R1 -- THE OUT-OF-BLOCK KEY RULE (all shapes). The managed block
# is regenerated on every run and EnvironmentFile= / `source` are last-wins, so a block
# that writes a key the owner already assigned outside the markers silently overrides (or
# is overridden by) the owner's line depending on where the block sits. The block now
# writes NO key that is assigned outside it.
#
# ONE READER, AND IT READS THE FILE AS ITS CONSUMER DOES (reference-host round, iteration 4, HIGH 1; memory
# #21871). Every unit loads this file through systemd's EnvironmentFile=, and these helpers used to read it with a
# SHELL's rules. Measured by the iteration-3 reflector: an owner line `CUDA_VISIBLE_DEVICES=1 # the compute card`
# was read as '1' -- P4 proved card 1, the record said gpu_pin=1, three "one pin" lines agreed -- while systemd
# handed every unit '1 # the compute card' (EnvironmentFile= has NO inline comments), the units' resolver rejected
# it, and every unit fell to card 0: the display card. install/env_file.py is a port of systemd's own tokenizer
# (bound to it byte-for-byte by voidloom/tests/test_env_file_reader.py), and EVERY read below goes through it: the
# block rule (`keys`), the pin (`unitpin`: systemd's value through the units' own resolver rules), the display
# declaration (`get`), the read-back (`effective`) and, once per run, `check` (env_file_check, below), which
# refuses the constructs a shell and systemd read differently wherever a decision rests on them. Nothing here
# ever prints the whole file, or any value but a pin's or the display declaration's (it may carry a token).
ENV_READER="${PKG_ROOT}/install/env_file.py"
# ENV_VIEW is the file every read below goes through: the env file itself -- except in a DRY run that took the owner's GPU
# answers (G1), where it is the verified image of the env file WITH those answers recorded (a dry run writes nothing, and
# still shows the run the answers make). A wet run records the answers IN the env file, so the two are the same file.
ENV_VIEW="$ENV_FILE"; G1_VIEW_DIR=""
env_outside() {   # env_outside keys|has|get KEY|effective KEY|unitpin KEY|provenance KEY|check SHAPE
    [ -f "$ENV_VIEW" ] || return 0
    "$PY_SYS" -B "$ENV_READER" --file "$ENV_VIEW" --begin "$ENV_BEGIN" --end "$ENV_END" --organs "${PKG_ROOT}/organs" "$@"
}
env_outside_has() { env_outside has | grep -qxF -- "$1"; }   # the keys systemd assigns OUTSIDE the block (FIXED TEXT: R-2's class)

# --- nvidia-smi, found where it lives (reference-host round, R7) -------------------
# `command -v nvidia-smi` alone answered "no GPU" under a clean PATH on WSL2, where the
# driver ships it in /usr/lib/wsl/lib -- so gpu_present was false, no CUDA pin reached the
# env block, and (the same fact seen from the units) gpu_guard reported "no GPUs visible"
# on every dream-daytime run. PATH first, then the driver's directory. The directory list
# MUST equal organs/voidloom_device.py NVIDIA_SMI_DIRS (the one locator the organs use);
# voidloom/tests/test_nvidia_smi_off_path.py refuses if the two drift. Finding the tool
# that LISTS the cards is not choosing one: the pin rules below are unchanged by it.
NVIDIA_SMI_DIRS="/usr/lib/wsl/lib"
resolve_nvidia_smi() {
    if command -v nvidia-smi >/dev/null 2>&1; then command -v nvidia-smi; return 0; fi
    local _d
    for _d in $NVIDIA_SMI_DIRS; do
        if [ -x "${_d}/nvidia-smi" ] && [ -f "${_d}/nvidia-smi" ]; then printf '%s\n' "${_d}/nvidia-smi"; return 0; fi
    done
    return 1
}
NVSMI="$(resolve_nvidia_smi || true)"

# --- "--dry-run changes nothing" is an ABSOLUTE promise, so it is kept absolutely -
#
# CRUCIBLE-3 D9-F11: a dry run left `claude_memory.db-wal` and `-shm` behind. They
# are ephemeral SQLite sidecars, the .db itself is untouched and no state is lost —
# which is exactly why it survived: the harm is nil and the SENTENCE is still false.
# The installer opens the brain read-only (`?mode=ro`) to read
# active_embedding_table, and for a WAL database even a read-only connection
# materialises the shared-memory index.
#
# Rejected: `&immutable=1`, which creates no sidecars and reads PRE-WAL content —
# trading a false promise for a possibly-wrong report, which is the worse of the
# two on a tree whose whole defect class is "reported something it did not observe".
#
# So: remember which sidecars existed BEFORE, and on exit let SQLite RETIRE the ones that
# appeared -- NEVER `rm` them (reference-host round, iteration 3; ruler VL-REF-v1.2; memory
# #21838). The first version of this guard deleted them, on the premise that a sidecar a
# live process uses "existed before us". False: a writer that opens the brain DURING the
# run has no sidecar before us either, and deleting its -wal destroys its commits silently
# (measured on the iteration-2 installer: a writer that committed during a dry run lost 10
# of 10 rows with NO crash and integrity_check 'ok'; a refusal lost a committed row in 20 of
# 20 runs; the base c6d7af2 dry run reproduced it). A read-write open + close retires the
# -wal and -shm only when it is the LAST connection -- SQLite's own lock decides -- and it
# checkpoints any committed frames first, so nothing another process committed is lost.
#
# Reference-host round, iteration 2: the guard is armed HERE, before the FIRST read of
# the brain. The shape gate below (brain_decl) now reads with `mode=ro` too -- it used
# immutable=1, and a tier0_only=1 committed into the WAL behind an open reader passed
# refusal (f) and the tier post-condition (measured; memory #21830) -- so its sidecars
# must be inside the guard. The pre-existing set is taken for EVERY run: a dry run
# retires the sidecars that appeared on exit, and so do a shape REFUSAL, which promises
# "Nothing was touched" (refuse_shape), and --print-write-set. A wet run keeps them
# (declared, install/write_set.json step B).
SIDECARS_PREEXISTING=""
SIDECAR_PY=""     # the interpreter the installer STARTED with (detect_python), captured before the shape gate re-points PY_SYS
# SQLite names a database's -wal/-shm after the file it OPENS, and it opens a symlink's TARGET (reference-host round,
# iteration 4, LOW 4: measured, a dry run on <brain>/claude_memory.db -> <elsewhere>/brain.db left brain.db-wal/-shm
# beside the target while this guard looked beside the link and retired nothing). Resolved once, with the link.
SIDECAR_BASE=""
dryrun_sidecar_snapshot() {
    SIDECAR_PY="$PY_SYS"
    SIDECAR_BASE="$(readlink -f "$DB_PATH" 2>/dev/null || printf '%s' "$DB_PATH")"; SIDECAR_BASE="${SIDECAR_BASE:-$DB_PATH}"
    for _sfx in -wal -shm; do
        [ -e "${SIDECAR_BASE}${_sfx}" ] && SIDECARS_PREEXISTING="${SIDECARS_PREEXISTING} ${_sfx}"
    done
    if [ "$DRY_RUN" -eq 1 ]; then
        trap dryrun_sidecar_restore EXIT
    elif [ "$PRINT_WRITE_SET" -eq 1 ]; then
        trap own_sidecar_restore EXIT      # --print-write-set "writes nothing and creates no sidecar"
    fi
    return 0
}
own_sidecar_restore() {   # let SQLite retire the -wal/-shm that appeared during this run; NEVER unlink one
    [ -n "${G1_VIEW_DIR:-}" ] && rm -rf "$G1_VIEW_DIR" && G1_VIEW_DIR=""     # a dry run's view of the owner's answers (G1)
    [ -n "${THINKER_ENV_DIR:-}" ] && rm -rf "$THINKER_ENV_DIR" && THINKER_ENV_DIR=""   # the thinker proof's env image (M10)
    local _new=0
    for _sfx in -wal -shm; do
        case " ${SIDECARS_PREEXISTING} " in *" ${_sfx} "*) continue ;; esac   # it was here before us; leave it
        [ -e "${SIDECAR_BASE:-$DB_PATH}${_sfx}" ] && _new=1
    done
    [ "$_new" -eq 1 ] || return 0
    # A read-write open + close. SQLite deletes the -wal and -shm on close ONLY when this is the
    # last connection (it must win the exclusive lock), after checkpointing any committed frames:
    # a writer that opened the brain during this run keeps its files and its commits. mode=rw never
    # CREATES a database. If the open fails (locked, unreadable, no interpreter) the files stay --
    # the safe direction: a leftover sidecar costs nothing, a deleted one can cost commits.
    "${SIDECAR_PY:-$PY_SYS}" - "$DB_PATH" <<'PYEOF' 2>/dev/null || true
import os, sqlite3, sys, urllib.request
uri = "file:%s?mode=rw" % urllib.request.pathname2url(os.path.abspath(sys.argv[1]))
c = sqlite3.connect(uri, uri=True, timeout=0.2)
try:
    c.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
finally:
    c.close()
PYEOF
    return 0
}
dryrun_sidecar_restore() {
    [ "$DRY_RUN" -eq 1 ] || return 0
    own_sidecar_restore
}
# ARMED HERE, and this line is the point: the pair above is inert without it, and
# a guard defined on no path is the defect this package keeps finding in itself.
# DRY_RUN is already parsed by now; DB_PATH is set at :62 (and by --attach-db above).
dryrun_sidecar_snapshot

hd_attach_check() {   # bash 3.2 (macOS /bin/bash) cannot parse a heredoc inside $( ): the command and its heredoc live here, called as $(hd_attach_check) (round 2, B2)
    "$PY_SYS" - "$ATTACH_DB" <<'PYEOF' 2>&1
import os, sqlite3, sys, urllib.request
db = sys.argv[1]
def uri(p, q):
    return "file:%s?%s" % (urllib.request.pathname2url(os.path.abspath(p)), q)
def has_memories(u):
    c = sqlite3.connect(u, uri=True)
    try:
        return c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='memories'").fetchone()[0] == 1
    finally:
        c.close()
try:
    ok = has_memories(uri(db, "immutable=1"))
    if not ok:
        ok = has_memories(uri(db, "mode=ro"))
except Exception as e:
    print(f"unreadable: {type(e).__name__}: {e}"); sys.exit(2)
print("ok" if ok else "no-memories-table")
sys.exit(0 if ok else 2)
PYEOF
}

# --- --attach-db, the READ: is it a brain of this product? (below the guard: iteration 4, LOW 3) ------------------
# Every URI here is built with escaping (iteration 4, LOW 2: a '?' or '#' in a path spliced raw into `file:...?mode=ro`
# ends the path early, and SQLite opens -- read-write-CREATE, since the unknown query key is ignored -- the prefix).
if [ -n "$ATTACH_DB" ]; then
    _attach_check="$(hd_attach_check)"
    if [ "$_attach_check" != "ok" ]; then
        echo "[install] --attach-db: ${ATTACH_DB} is not a brain this package can attach (${_attach_check}). Refusing; nothing was touched." >&2
        own_sidecar_restore     # SQLite retires the -wal/-shm the mode=ro read materialised (never an unlink)
        exit 2
    fi
fi

# --- THE INSTALL SHAPE: declared and recorded, never inferred (R1) ------------------
#
# Governing principle (the accepted design, 2026-09-25): an upgrade converges the PACKAGE
# (files, schema, env block, units) while leaving HOST STATE -- recall tier, interpreter,
# vector population -- exactly as found, unless the owner says a word. The installer had
# two shapes and its own reference host fits neither (Tier 1 through an interpreter
# outside the brain, the tier declared by the DB), so the reference upgrade of 2026-09-20
# ran the package's verbs by hand. The third shape:
#   venv     the Tier-1 venv under the brain (Step C builds it) -- today's default, unchanged
#   tier0    --tier0-only -- unchanged
#   ambient  --tier1-ambient <ABS python3> once, then read from <brain>/INSTALL_RECORD
# The installer NEVER reads PATH to pick a shape: a stranger whose base python has torch
# gets the venv shape, as before, because no record says otherwise.
INSTALL_RECORD="${BRAIN_DIR}/INSTALL_RECORD"
# Read BEFORE this run writes its own record: an earlier install exists (an upgrade). The approvals question uses it --
# a missing approvals choice on an existing brain is not consent to change how that brain decides.
# An existing brain counts even with no INSTALL_RECORD (one written before 2026-09-26, a run whose gate failed, or an
# --attach-db of an existing brain): review 2, N3 -- such a brain was switched to auto as "a new install".
PRIOR_INSTALL=0; { [ -f "$INSTALL_RECORD" ] || [ -s "$DB_PATH" ] || [ -n "$ATTACH_DB" ] || [ -n "$EXPLICIT_BRAIN_DB" ]; } && PRIOR_INSTALL=1
INSTALL_LOG="${BRAIN_DIR}/INSTALL_LOG"
record_get() {
    [ -f "$INSTALL_RECORD" ] || return 0
    awk -v k="$1" 'index($0, k "=") == 1 { v = substr($0, length(k) + 2) } END { if (v != "") print v }' "$INSTALL_RECORD"
}
REC_SHAPE="$(record_get shape)"
REC_INTERP="$(record_get interpreter)"
brain_decl() {   # the brain's own declarations, read without writing: "<active_table>|<tier0_only>"
    # mode=ro, NEVER immutable=1 (reference-host round, iteration 2; memory #21830). This
    # read decides the brain's CURRENT state three times -- the four-condition gate, and the
    # tier post-condition's before and after -- and immutable=1 skips the WAL: with any
    # reader holding the brain open (a live organ, a session hook) a committed tier0_only=1
    # stayed in the WAL and this read did not see it. Measured on iteration 1: refusal (f)
    # bypassed, and a mutant that wrote tier0_only=1 printed "post-condition held", rc 0.
    # The package had rejected this exact read once already (the dry-run note above).
    # Prints NOTHING when the brain cannot be read, so an unreadable brain never passes as
    # a declaration (the gate refuses an empty table; the post-condition refuses empty).
    "$PY_SYS" - "$DB_PATH" <<'PYEOF' 2>/dev/null
import os, sqlite3, sys, urllib.request   # the URI is ESCAPED (iteration 4, LOW 2: a '?' or '#' in the path would cut it short)
db = sys.argv[1]
try:
    c = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(os.path.abspath(db)), uri=True)
    try:
        t = c.execute("SELECT value FROM metadata WHERE key='active_embedding_table'").fetchone()
        z = c.execute("SELECT value FROM metadata WHERE key='tier0_only'").fetchone()
    finally:
        c.close()
except sqlite3.Error:
    sys.exit(0)
print("%s|%s" % (t[0] if t else "", z[0] if z else ""))
PYEOF
}
# A refusal names where the SHAPE came from (the flag, or <brain>/INSTALL_RECORD on a run with no
# flag), and lets SQLite retire the -wal/-shm that appeared during the run (own_sidecar_restore:
# never an unlink) -- "Nothing was touched".
# "Nothing was touched" is TRUE until G1 records the owner's GPU answers in the env file (a wet run); a refusal after that
# says exactly what was written, and that a re-run reads it and does not ask again (iteration 5, G1).
NOTHING_TOUCHED="Nothing was touched."
refuse_shape() {
    echo "[install] --tier1-ambient REFUSED${AMBIENT_SOURCE:+ (shape from ${AMBIENT_SOURCE})}: $* ${NOTHING_TOUCHED}" >&2
    own_sidecar_restore
    exit 2
}
SHAPE="venv"; AMBIENT_SOURCE=""; AMBIENT_PY=""; AMBIENT_TIER=""; DECL_TABLE=""; DECL_T0=""
if [ -n "$TIER1_AMBIENT" ] || [ "$REC_SHAPE" = "ambient" ]; then
    SHAPE="ambient"
    if [ -n "$TIER1_AMBIENT" ]; then AMBIENT_PY="$TIER1_AMBIENT"; AMBIENT_SOURCE="--tier1-ambient"
    else AMBIENT_PY="$REC_INTERP"; AMBIENT_SOURCE="${INSTALL_RECORD}"; fi
    # (g) a record names one interpreter; a flag naming another is a SWITCH, which is an owner's act
    if [ -n "$TIER1_AMBIENT" ] && [ -n "$REC_INTERP" ] && [ "$REC_SHAPE" = "ambient" ] && [ "$TIER1_AMBIENT" != "$REC_INTERP" ]; then
        refuse_shape "this install is recorded as ambient Tier 1 through ${REC_INTERP} (${INSTALL_RECORD}); --tier1-ambient names ${TIER1_AMBIENT}. Switching the interpreter every unit runs is the owner's decision: move ${INSTALL_RECORD} aside deliberately, then re-run with the new path."
    fi
    case "$AMBIENT_PY" in /*) ;; *) refuse_shape "the interpreter must be an ABSOLUTE path (it becomes the literal first token of every unit's ExecStart=): '${AMBIENT_PY}'" ;; esac
    # systemd splits ExecStart= on whitespace (iteration 3, item 6: a path with a space passed every proof, then
    # 8 units were rendered AND their timers enabled before the read-back failed the run)
    case "$AMBIENT_PY" in *[[:space:]]*) refuse_shape "the interpreter path '${AMBIENT_PY}' contains whitespace: systemd splits ExecStart= on it, so it cannot be the literal first token of a unit's ExecStart=." ;; esac
    # ... and systemd EXPANDS '%' as a unit specifier (iteration 4, LOW 2: measured by the iteration-3 reflector with
    # `systemd-analyze verify`, offline: ExecStart=<dir>/py%hdir/python3 ran <dir>/py<the home dir>dir/python3)
    case "$AMBIENT_PY" in *%*) refuse_shape "the interpreter path '${AMBIENT_PY}' contains '%': systemd expands '%' as a unit specifier in ExecStart= (measured: '%h' became the home directory), so it cannot be the literal first token of a unit's ExecStart=." ;; esac
    [ "$TIER0_ONLY" -eq 1 ] && refuse_shape "--tier0-only contradicts an ambient Tier 1 (it would record tier0_only=1 and switch semantic recall off)."
    # ruler VL-REF-v1.2: the ambient shape PROVES the GPU the units will use (P4) or it does not run. --force-cpu
    # moved nothing here -- the units run the host's own CUDA torch -- while P4 was SKIPPED and the record said
    # "the units run the embedder on the CPU" (iteration 3, item 1: measured, the units resolved card 0).
    [ "$FORCE_CPU" -eq 1 ] && refuse_shape "--force-cpu contradicts an ambient Tier 1: the units run ${AMBIENT_PY} with the host's own torch, and nothing this installer writes moves them to the CPU -- P4 would be skipped while every unit resolves a card. Declare the card instead: WIZARD_CUDA_DEVICE=<index> outside the managed block."
    { [ -n "$EXPLICIT_BRAIN_DB" ] || [ "$INIT_BLANK" -eq 1 ]; } && refuse_shape "--brain-db / --init-blank bring a DIFFERENT brain; the ambient shape upgrades the brain that exists."
    # (d) a fresh home: nothing to upgrade
    if [ ! -s "$DB_PATH" ]; then
        # the record drove this run (no flag): "run without --tier1-ambient" would repeat it
        [ "$AMBIENT_SOURCE" = "$INSTALL_RECORD" ] && refuse_shape "no brain at ${DB_PATH}, but ${INSTALL_RECORD} records an ambient install: move that record aside deliberately, then run without --tier1-ambient to build a fresh (venv) install."
        refuse_shape "no brain at ${DB_PATH}: ambient is an UPGRADE shape; a fresh install builds its venv (run without --tier1-ambient)."
    fi
    _decl="$(brain_decl)"; DECL_TABLE="${_decl%%|*}"; DECL_T0="${_decl#*|}"
    case "$DECL_TABLE" in
        memory_embeddings_v3) AMBIENT_TIER="qwen" ;;
        memory_embeddings_v2) AMBIENT_TIER="jina" ;;
        memory_embeddings)    AMBIENT_TIER="nomic" ;;
        *) refuse_shape "the brain at ${DB_PATH} declares no Tier-1 table (metadata.active_embedding_table='${DECL_TABLE}'): there is no ambient Tier 1 to keep." ;;
    esac
    # (f) the brain declares keyword-only
    case "$(printf '%s' "$DECL_T0" | tr '[:upper:]' '[:lower:]')" in
        ""|0|false|no|off) ;;
        *) refuse_shape "the brain declares keyword-only (metadata.tier0_only='${DECL_T0}'); clear it deliberately first." ;;
    esac
    # (e) this install owns a venv
    [ -e "${BRAIN_DIR}/venv/bin/python3" ] && refuse_shape "this install owns a venv (${BRAIN_DIR}/venv/bin/python3): its units run that interpreter; the ambient shape is for an install with none."
    # -f as well as -x: a DIRECTORY is -x (measured: `--tier1-ambient /usr/bin` passed here and died rc 1 in lay.py)
    { [ -f "$AMBIENT_PY" ] && [ -x "$AMBIENT_PY" ]; } || refuse_shape "the interpreter ${AMBIENT_PY} is not an executable file."
    if [ -n "${WIZARD_EMBED_MODEL:-}" ] && [ "$WIZARD_EMBED_MODEL" != "$AMBIENT_TIER" ]; then
        refuse_shape "WIZARD_EMBED_MODEL=${WIZARD_EMBED_MODEL} disagrees with the brain's declared tier ${AMBIENT_TIER} (${DECL_TABLE}); the ambient shape never changes the recall tier."
    fi
    if [ -n "${WIZARD_PYTHON_BIN:-}" ] && [ "$WIZARD_PYTHON_BIN" != "$AMBIENT_PY" ]; then
        refuse_shape "WIZARD_PYTHON_BIN=${WIZARD_PYTHON_BIN} disagrees with the ambient interpreter ${AMBIENT_PY} (${AMBIENT_SOURCE}); one interpreter, from one source."
    fi
    # ONE INTERPRETER from here on: the lay, the migrator, the engines, the env block's PATH
    # (LOCAL_BIN, then THIS interpreter's dir) and every unit's ExecStart.
    PY_SYS="$AMBIENT_PY"
elif [ "$TIER0_ONLY" -eq 1 ]; then
    SHAPE="tier0"
fi
# The verified pre-image the ambient upgrade takes before its migrations (the same backup()
# --attach-db takes), beside the brain. Declared by glob in install/write_set.json.
UPGRADE_PREIMAGE=""
[ "$SHAPE" = "ambient" ] && [ -z "$ATTACH_DB" ] && UPGRADE_PREIMAGE="$(dirname "$DB_PATH")/$(basename "$DB_PATH").pre-upgrade-$(date -u +%Y%m%dT%H%M%SZ).db"

case "$OS" in
    Darwin) PLATFORM="macOS (launchd LaunchAgents)" ;;
    Linux)  PLATFORM="Linux (systemd user timers)" ;;
    *)      PLATFORM="UNKNOWN ($OS) — only env + Tier 0 supported" ;;
esac

say()  { echo "[install] $*"; }
step() { echo; echo "[install] ===== $* ====="; }
warn() { echo "[install] WARNING: $*" >&2; }
# EVERY FATAL names what this run has already RECORDED (model-choice Round 1, Wave B -- EW5-01's class, fixed for the
# class, not the site): the owner's GPU answers (G1) and the overnight thinker's record (M10) are written before later
# steps that can fail, and a failure message that omits them told the owner "nothing changed" of a file that had.
RECORDED_NOTE=""
die()  { echo "[install] FATAL: $*${RECORDED_NOTE:+ $RECORDED_NOTE}" >&2; exit 1; }
# run_argv CMD ARG... -- a write, or its DRY line. WITHOUT eval: each argument is passed as it is, so a path holding a
# quote, a space or a '$' is one argument (env-file writer pass, iteration 4, EW2-12: the old `run()` was `eval "$*"`, and
# under a HOME holding a ' the voidloom CLI's `run "ln -sf '...' '...'"` failed in eval -- "unexpected EOF while looking
# for matching ''" -- while the next line printed "voidloom CLI symlinked onto PATH" for a link that was never made; every
# `run` over an interpolated path was that class, so `run` is gone)
run_argv() { if [ "$DRY_RUN" -eq 1 ]; then echo "  DRY: $*"; else "$@"; fi; }


# --- "--dry-run changes nothing": the sidecar guard is armed ABOVE, before the shape
# gate's first read of the brain (reference-host round, iteration 2) -- see
# dryrun_sidecar_snapshot, just ahead of "THE INSTALL SHAPE".

py_version() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?"; }
py_ge_310()  { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; }
is_system_python() { case "$1" in /usr/bin/python3) return 0 ;; *) return 1 ;; esac; }

# ----------------------------------------------------------------------------
# THE ACCELERATOR PLAN (handoff round 1, L4) -- decided ONCE, here, before anything is touched, by
# install/accelerator_plan.py (stdlib; no torch yet): the backend this machine has (the organs' resolver, contract C1),
# the torch wheel channel for it, its proof label (contract C4's words), and the reranker RAM gate (Drew's ruling: ON
# only with a non-CPU accelerator AND MemTotal >= 15.0 GiB; never on CPU-only). An owner's WIZARD_RERANKER=off
# outside the managed block closes the gate; an =on line never opens it. An EXPLICIT --accelerator that contradicts the machine is refused here; an
# `auto` plan with no torch wheel (an Intel Mac on Python 3.13) does not stop the install -- Tier 1 is skipped, said.
# ----------------------------------------------------------------------------
ACCEL_PLAN_PY="${PKG_ROOT}/install/accelerator_plan.py"
# where the gate is recorded (contract C3): the organs' own path for wizard-config.json -- WIZARD_CONFIG_PATH, else
# organs/voidloom_env.py env_path (the file wizard_config.py reads), resolved once, here, so --print-write-set names it
WIZARD_CONFIG_JSON="${WIZARD_CONFIG_PATH:-$("$PY_SYS" -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import voidloom_env; print(voidloom_env.env_path("wizard-config.json"))' "${PKG_ROOT}/organs" 2>/dev/null)}"
ACCEL_BACKEND="cpu"; ACCEL_LABEL=""; ACCEL_BASIS=""; ACCEL_RESOLVER=""; ACCEL_INDEX=""; ACCEL_SPEC="torch"
ACCEL_BUILD=""; ACCEL_DESC=""; ACCEL_RERANK=0; ACCEL_RERANK_WHY=""; ACCEL_RAM_GIB=""; ACCEL_SENTENCE=""
ACCEL_REFUSAL=""; ACCEL_NOTES=""; ACCEL_JSON=""
ACCEL_RERANK_OVERRIDE="$(env_outside get WIZARD_RERANKER 2>/dev/null)"
accel_plan() {   # accel_plan REQUEST -- (re)decide the plan into the ACCEL_* variables (Step C re-plans after an MPS flip)
    # The plan is stdlib (C1's first stage never imports torch), so it runs in THIS shell's environment: hiding every
    # card here made the real resolver read "the operator declared no device" and plan the CPU on a CUDA host.
    # An owner's --gpu answer is the card the plan describes (validated digits by the parser above); otherwise the
    # shell's own pin or the resolver's declared/default card.
    # `env CMD` runs CMD unchanged, so the array is never empty: bash < 4.4 (macOS /bin/bash 3.2.57) calls "${a[@]}" of
    # an EMPTY array an unbound variable under set -u, and every install without --gpu died here (round 2, B1).
    # --pin-from: the pin came from the owner's --gpu answer, so the basis must not call it "ambient" (round 2, l3).
    case "$GPU_ANSWER" in
        ''|*[!0-9]*) _ap_pin=(env); _ap_from="" ;;
        *) _ap_pin=(env "CUDA_VISIBLE_DEVICES=${GPU_ANSWER}"); _ap_from="your --gpu ${GPU_ANSWER} answer" ;;
    esac
    ACCEL_JSON="$("${_ap_pin[@]}" "$PY_SYS" -B "$ACCEL_PLAN_PY" plan --organs "${PKG_ROOT}/organs" \
        --request "$1" --python "$(py_version "$PY_SYS")" --os "$OS" --arch "$(uname -m)" \
        --reranker-override "$ACCEL_RERANK_OVERRIDE" --pin-from "$_ap_from")"
    _ap_rc=$?
    case "$_ap_rc" in 0|2) ;; *) die "install/accelerator_plan.py could not plan this machine's compute (rc ${_ap_rc})." ;; esac
    ACCEL_REFUSAL=""; ACCEL_NOTES=""
    while IFS="$(printf '\t')" read -r _ap_k _ap_v; do
        case "$_ap_k" in
            backend) ACCEL_BACKEND="${_ap_v:-cpu}" ;;   label) ACCEL_LABEL="$_ap_v" ;;
            basis) ACCEL_BASIS="$_ap_v" ;;              resolver) ACCEL_RESOLVER="$_ap_v" ;;
            index) ACCEL_INDEX="$_ap_v" ;;              spec) ACCEL_SPEC="$_ap_v" ;;
            build) ACCEL_BUILD="$_ap_v" ;;              desc) ACCEL_DESC="$_ap_v" ;;
            rerank) ACCEL_RERANK="${_ap_v:-0}" ;;       rerank_why) ACCEL_RERANK_WHY="$_ap_v" ;;
            ram_gib) ACCEL_RAM_GIB="$_ap_v" ;;          sentence) ACCEL_SENTENCE="$_ap_v" ;;
            refusal) ACCEL_REFUSAL="$_ap_v" ;;          notes) ACCEL_NOTES="$_ap_v" ;;
        esac
    done <<EOF_ACCEL
$(printf '%s' "$ACCEL_JSON" | "$PY_SYS" -B "$ACCEL_PLAN_PY" fields)
EOF_ACCEL
}
if [ -f "$ACCEL_PLAN_PY" ]; then
    accel_plan "$ACCEL_REQUEST"
    if [ -n "$ACCEL_REFUSAL" ] && [ "$ACCEL_REQUEST" != "auto" ]; then
        echo "[install] REFUSED: ${ACCEL_REFUSAL}. Nothing was touched." >&2; exit 2
    fi
    if [ "$FORCE_CPU" -eq 1 ] && env_outside_has WIZARD_ACCELERATOR; then
        _ap_have="$(env_outside get WIZARD_ACCELERATOR)"
        case "$_ap_have" in
            cpu) ;;
            *) echo "[install] REFUSED: --force-cpu means the CPU, and ${ENV_FILE} line $(env_outside line WIZARD_ACCELERATOR) declares WIZARD_ACCELERATOR=${_ap_have} -- your own line. Edit that line to WIZARD_ACCELERATOR=cpu (or remove it), then re-run. Nothing was touched." >&2; exit 2 ;;
        esac
    fi
else
    ACCEL_REFUSAL="install/accelerator_plan.py is missing from ${PKG_ROOT}"
fi

# ----------------------------------------------------------------------------
# --print-write-set -- the declaration, RESOLVED for this invocation, and exit.
#
# Council #12 (F4): every write outside ${CLAUDE_HOME} is declared in
# install/write_set.json per step with a reason. This prints that declaration
# with every ${VAR} replaced by the value THIS run would use and `active` set
# from the flags given, so the snapshot-diff harness grades the run against the
# same resolution the run performs -- not against a copy somebody typed. It
# exits here, before the embedder resolution opens the brain, so it writes
# nothing and creates no sidecar.
# ----------------------------------------------------------------------------
if [ "$PRINT_WRITE_SET" -eq 1 ]; then
    _ws_armed=""
    if [ -f "${PKG_ROOT}/install/cron_roster.py" ]; then
        # rc captured from the reader itself (PIPESTATUS[0]), never from `tr`: a
        # malformed roster must refuse here, not print a declaration with no unit
        # paths in it (round-1 reflection, item 14).
        _ws_armed="$("$PY_SYS" "${PKG_ROOT}/install/cron_roster.py" --roster "${PKG_ROOT}/install/cron_roster.json" armed | tr '\n' ','; exit "${PIPESTATUS[0]}")"; _ws_rc=$?
        [ "$_ws_rc" -eq 0 ] || die "--print-write-set: install/cron_roster.json is unreadable (rc ${_ws_rc}); the declaration cannot be resolved."
    else
        die "--print-write-set: install/cron_roster.py is missing from ${PKG_ROOT}; the declaration cannot be resolved."
    fi
    WS_HOME="$HOME" WS_CLAUDE_HOME="$CLAUDE_HOME" WS_BRAIN_DIR="$BRAIN_DIR" WS_LOCAL_BIN="$LOCAL_BIN" \
    WS_ENV_FILE="$ENV_FILE" WS_ENV_DIR="$ENV_DIR" WS_CONFIG_DIR="$CONFIG_DIR" WS_SLEEP_CFG="$SLEEP_CFG" \
    WS_ENV_FILE_TARGET="$("$PY_SYS" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$ENV_FILE" 2>/dev/null || printf '%s' "$ENV_FILE")" \
    WS_BRAIN_DIR_TARGET="$("$PY_SYS" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$BRAIN_DIR" 2>/dev/null || printf '%s' "$BRAIN_DIR")" \
    WS_SYSTEMD_USER_DIR="$SYSTEMD_USER_DIR" WS_LAUNCHD_DIR="$LAUNCHD_DIR" WS_LAUNCHD_PREFIX="$LAUNCHD_PREFIX" \
    WS_CACHE_HOME="$CACHE_HOME" WS_WIZARD_CONFIG="$WIZARD_CONFIG_JSON" WS_WIZARD_CONFIG_DIR="$(dirname "$WIZARD_CONFIG_JSON")" \
    WS_ATTACH_DB="${ATTACH_DB:-}" WS_ATTACH_PREIMAGE="${ATTACH_PREIMAGE:-}" WS_TMPDIR="${TMPDIR:-/tmp}" \
    WS_OS="$OS" WS_WITH_CRONS="$WITH_CRONS" WS_TIER0_ONLY="$TIER0_ONLY" WS_DRY_RUN="$DRY_RUN" \
    WS_ENV_ONLY="$ENV_ONLY" WS_ARMED="$_ws_armed" WS_WIRE_HOOKS="$WIRE_HOOKS" \
    WS_SHAPE="$SHAPE" WS_DB_PATH="$DB_PATH" WS_INSTALL_RECORD="$INSTALL_RECORD" WS_INSTALL_LOG="$INSTALL_LOG" \
    WS_AMBIENT_PY="${AMBIENT_PY:-}" \
    WS_DB_DIR="$(dirname "$DB_PATH")" WS_THINKER_FLAG_GIVEN="$THINKER_FLAG" WS_INSTALL_THINKER_CLI="$INSTALL_THINKER_CLI" \
    WS_THINKER_HOME="$("$PY_SYS" -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import voidloom_env; print(voidloom_env.preferred_dir() / "thinker")' "${PKG_ROOT}/organs" 2>/dev/null || printf '%s' "${HOME}/.voidloom/thinker")" \
    WS_THINKER_ANSWER="$(if [ -n "$THINKER_FLAG" ]; then printf '%s' "$THINKER_FLAG"; elif [ "$ENV_ONLY" -ne 1 ]; then "$PY_SYS" -B -c '
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
try:
    import thinker
    r = thinker.read_record(Path(sys.argv[2]))
    print((r or {}).get("backend", ""))
except Exception:
    pass
' "${PKG_ROOT}/organs" "$(dirname "$DB_PATH")/THINKER_RECORD" 2>/dev/null; fi)" \
    "$PY_SYS" - "${PKG_ROOT}/install/write_set.json" <<'PYEOF'
import json, os, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
env = {k[3:]: v for k, v in os.environ.items() if k.startswith("WS_")}
def resolve(path):
    for k, v in env.items():
        path = path.replace("${%s}" % k, v)
    return path
dry, tier0, crons, envonly = env["DRY_RUN"] == "1", env["TIER0_ONLY"] == "1", env["WITH_CRONS"] == "1", env["ENV_ONLY"] == "1"
armed = [u for u in env.get("ARMED", "").split(",") if u]
def active(step):
    if dry:
        return False                      # a dry run writes nothing, anywhere
    w = step.get("when", "always")
    if step.get("platform") and step["platform"] != env["OS"]:
        return False
    if step.get("shape") and step["shape"] != env["SHAPE"]:
        return False                      # R1: a step of one install shape is inactive in another
    if envonly and step["step"] not in ("D", "transient"):
        return False                      # --env-only writes the env file and exits
    if w == "always":
        return True
    if w == "--with-crons":
        return crons
    if w == "--attach-db":
        return bool(env.get("ATTACH_DB"))
    if w == "not --tier0-only":
        return not tier0
    if w == "venv shape":
        return env["SHAPE"] == "venv"
    if w == "--wire-hooks":
        # STEP P's question: a flag answer is known here; an interactive yes is not (like the thinker's answer)
        return env.get("WIRE_HOOKS", "") == "1"
    # M10/M17: the overnight thinker's answer -- --thinker, or the answer THINKER_RECORD already holds (an interactive
    # answer is unknowable here); "to prove" = it names a model (not e/off). The record of an e) answer is KEPT on a
    # re-run (nothing to prove), so only a --thinker answer or a model to prove rewrites it.
    ans = env.get("THINKER_ANSWER", "")
    if w == "a thinker answer":
        return bool(ans) and (ans != "off" or bool(env.get("THINKER_FLAG_GIVEN")))
    if w == "a thinker to prove":
        return bool(ans) and ans != "off"
    if w == "an offered thinker install":
        # Gemini-thinker Round 1 (L-I): --install-thinker-cli with an answer whose program is a vendor CLI; whether the
        # program is missing is a fact of the run, so the step is active whenever the run MAY install it
        return env.get("INSTALL_THINKER_CLI", "0") == "1" and bool(env.get("THINKER_FLAG_GIVEN")) and \
            ans in ("claude", "codex", "kimi", "gemini")
    raise SystemExit("write_set.json: unknown `when` %r" % w)
out = {"installer": "install.sh", "os": env["OS"],
       "invocation": {"dry_run": dry, "tier0_only": tier0, "with_crons": crons,
                      "env_only": envonly, "attach_db": env.get("ATTACH_DB") or None,
                      "shape": env["SHAPE"], "ambient_interpreter": env.get("AMBIENT_PY") or None},
       "target_root": resolve(doc["target_root"]),
       "resolved": {k: env.get(k, "") for k in doc["variables"]},
       "armed_units": armed, "declared": []}
for st in doc["steps"]:
    act = active(st)
    for w in st["writes"]:
        wact = act and (not w.get("backend") or w["backend"] == env.get("THINKER_ANSWER", ""))
        paths = [w["path"]]
        if w.get("expand") == "armed_units":
            paths = [w["path"].replace("{unit}", u) for u in armed]
        for pth in paths:
            r = resolve(pth)
            if "${" in r:
                raise SystemExit("write_set.json: unresolved variable in %r" % r)
            out["declared"].append({"step": st["step"], "name": st["name"], "when": st.get("when", "always"),
                                    "platform": st.get("platform"), "shape": st.get("shape"), "path": r,
                                    "kind": w["kind"], "why": w["why"], "active": wact})
# The VERBS: calls made to a scheduler/manager rather than writes to the filesystem
# (the HKCU class on Linux). Same active() rule; {unit} expands the same way. The
# cold gate grades a recorder shim's log against this list (harness verb `verbs`).
out["verbs"] = []
for v in doc.get("verbs", []):
    act = active(v)
    calls = [v["call"]]
    if v.get("expand") == "armed_units":
        calls = [v["call"].replace("{unit}", u) for u in armed]
    for c in calls:
        r = resolve(c)
        if "${" in r:
            raise SystemExit("write_set.json: unresolved variable in verb %r" % r)
        out["verbs"].append({"step": v["step"], "when": v.get("when", "always"), "platform": v.get("platform"),
                             "shape": v.get("shape"), "call": r, "effect": v.get("effect", "write"),
                             "match": v.get("match", "exact"), "why": v["why"], "active": act})
# M17: the paths the installer and every child it runs must leave BYTE-IDENTICAL (the owner's everyday vendor logins)
_installing = env.get("THINKER_ANSWER", "") if env.get("INSTALL_THINKER_CLI", "0") == "1" and not dry else ""
out["untouched"] = [{"path": resolve(u["path"]), "why": u["why"]} for u in doc.get("untouched", [])
                    if not (u.get("unless_installing") and u["unless_installing"] == _installing)]
print(json.dumps(out, indent=1))
PYEOF
    exit $?
fi

# --- THE ENV FILE, READ AS THE UNITS READ IT -- checked ONCE, before any pin is resolved (iteration 4, HIGH 1) -----
# install/env_file.py `check` (its docstring holds the whole policy): it REFUSES, every shape, a file systemd would
# reject whole (a NUL, invalid UTF-8 in an assignment -- the units' leading '-' makes that silent), a managed-block
# marker inside a value, and a pin line whose value -- as systemd hands it over -- the units' own resolver rejects
# (`CUDA_VISIBLE_DEVICES=1 # the compute card`: the units fall to card 0 while a shell reads 1). In the AMBIENT
# shape it also refuses a pin line, or the display declaration, that a shell reads differently from the units --
# an `export WIZARD_CUDA_DEVICE=1` is NOT a declaration (systemd drops it), and P4 may rest only on a declaration
# every reader agrees on; elsewhere that is a warning. Every other line a shell and systemd read differently is
# named, once, by line and key -- never by value.
refuse_env() {
    if [ "$SHAPE" = "ambient" ]; then refuse_shape "$*"; fi
    echo "[install] REFUSED (${ENV_FILE}): $* ${NOTHING_TOUCHED}" >&2
    own_sidecar_restore
    exit 2
}
env_file_check() {
    [ -f "$ENV_FILE" ] || return 0
    local _out _rc _l _refusals=""
    _out="$(env_outside check "$SHAPE")"; _rc=$?
    while IFS= read -r _l; do
        case "$_l" in
            "WARN	"*)   warn "${_l#WARN	}" ;;
            "REFUSE	"*) _refusals="${_refusals:+$_refusals }${_l#REFUSE	}" ;;
        esac
    done <<EOF
$_out
EOF
    case "$_rc" in
        0) return 0 ;;
        2) refuse_env "$_refusals" ;;
        *) die "install/env_file.py could not read ${ENV_FILE} (rc ${_rc}); no pin is resolved from a file this installer cannot read." ;;
    esac
}
env_file_check

# THE ONE COPY over the env file, shared by its two writes (iteration 5): the managed block (Step D, write_env_file) and the
# owner's GPU answers (G1, g1_record). Only a verified, non-empty image is copied; the copy is compared byte for byte; a
# failed or short copy puts the owner's file back from its pre-image (or leaves none where there was none) and says which.
env_install_image() {   # env_install_image IMAGE WORKDIR WHAT
    local _img="$1" _wd="$2"
    run_argv mkdir -p "$(dirname "${ENV_FILE}")"
    [ -s "$_img" ] || { rm -rf "$_wd"; die "the verified image of ${ENV_FILE} is EMPTY; this write did not change the file."; }
    if [ -f "$ENV_FILE" ]; then
        cp -p "$ENV_FILE" "${_wd}/preimage" || { rm -rf "$_wd"; die "could not take a pre-image of ${ENV_FILE}; this write did not change the file."; }
    fi
    # `cp`, never `cp -f`: -f UNLINKS a destination it cannot open -- a symlink whose target is read-only among them --
    # and writes a new regular file in its place (EW1-01). Without it a destination that cannot be opened is a failed
    # copy that changed nothing, and a symlinked env file is written through, in its target, the link left in place.
    if ! cp "$_img" "$ENV_FILE" || ! cmp -s "$_img" "$ENV_FILE"; then
        # a short or failed copy (a full disk, a file-size limit, a destination that became unwritable during the run):
        # put the owner's file back as it was -- or, where there was none, leave none -- and say which it was
        if [ -f "${_wd}/preimage" ] && cmp -s "${_wd}/preimage" "$ENV_FILE"; then
            _ew_t="$ENV_FILE"; [ -L "$ENV_FILE" ] && _ew_t="$("$PY_SYS" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$ENV_FILE" 2>/dev/null || readlink "$ENV_FILE")"
            rm -rf "$_wd"; die "the copy over ${ENV_FILE} failed before it changed a byte (the file still reads back as its pre-image, byte-compared); this write did not change the file. Make ${_ew_t} writable again (chmod u+w '${_ew_t}'), then re-run this install."
        fi
        if [ -f "${_wd}/preimage" ]; then
            if cp "${_wd}/preimage" "$ENV_FILE" && cmp -s "${_wd}/preimage" "$ENV_FILE"; then
                rm -rf "$_wd"; die "the copy over ${ENV_FILE} did not read back as the verified image; the file was RESTORED from its pre-image (byte-compared)."
            fi
            die "the copy over ${ENV_FILE} did not read back as the verified image and the file could NOT be restored; its pre-image is ${_wd}/preimage -- restore it by hand: cp '${_wd}/preimage' '${ENV_FILE}'"
        fi
        { [ -f "$ENV_FILE" ] && [ ! -L "$ENV_FILE" ]; } && rm -f "$ENV_FILE"
        rm -rf "$_wd"; die "${ENV_FILE} could not be written as the verified image (there was no file before this run, and none is left)."
    fi
}
# --- THE ENV FILE'S DESTINATION, checked before anything may write it (moved ahead of the GPU choice, iteration 5) -----
case "${WIZARD_ENV_EXTRA_PATH:-}" in ""|/*) ;; *) echo "[install] WIZARD_ENV_EXTRA_PATH must be a colon-separated list of ABSOLUTE dirs: '${WIZARD_ENV_EXTRA_PATH}'. Nothing was touched." >&2; own_sidecar_restore; exit 2 ;; esac
refuse_write() {   # refuse_write MESSAGE -- the write (or its rehearsal) refused
    [ -n "${_pre_dir:-}" ] && rm -rf "$_pre_dir"
    [ "$SHAPE" = "ambient" ] && refuse_shape "$*"
    echo "[install] REFUSED (${ENV_FILE}): $* ${NOTHING_TOUCHED}" >&2
    own_sidecar_restore
    exit 2
}
# THE DESTINATION IS PART OF THE WRITE (env-file writer pass, iteration-1 reflection EW1-01/EW1-02). The env file is
# rewritten IN PLACE -- `cp` opens the path and writes through any symlink into the file it names -- and never replaced.
# Measured on the iteration-1 artifacts: `cp -f` unlinked a symlinked env file whose TARGET was read-only and wrote a
# regular file in the link's place (rc 0, the owner's real file left stale, "mode verified 600" of the new file); a
# dangling link, an env directory this user cannot write, or a FIFO at the path failed only at Step D -- after the lay,
# the brain and the migrations -- or hung. Each is refused here, by name, before anything is touched -- and BEFORE the GPU
# choice below (iteration 5, G1), which may record the owner's answers in this very file.
if [ -L "$ENV_FILE" ] && [ ! -e "$ENV_FILE" ]; then
    refuse_write "${ENV_FILE} is a symlink to '$(readlink "$ENV_FILE")', which does not exist -- cp will not write through a dangling link. Create the target (mode 600) or remove the link, then re-run."
elif [ -e "$ENV_FILE" ]; then
    if ! { [ -f "$ENV_FILE" ] && [ -w "$ENV_FILE" ]; }; then
        _ew_t="$ENV_FILE"; [ -L "$ENV_FILE" ] && _ew_t="$("$PY_SYS" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$ENV_FILE" 2>/dev/null || readlink "$ENV_FILE")"
        refuse_write "${ENV_FILE}$([ "$_ew_t" != "$ENV_FILE" ] && printf ' (a symlink to %s)' "$_ew_t") is not a writable regular file -- it is rewritten IN PLACE, through any symlink, and never replaced. Make ${_ew_t} a regular file this user can write (chmod u+w '${_ew_t}'), then re-run."
    fi
else
    _ew_d="$(dirname "$ENV_FILE")"; while [ ! -e "$_ew_d" ] && [ ! -L "$_ew_d" ] && [ "$_ew_d" != "/" ]; do _ew_d="$(dirname "$_ew_d")"; done
    { [ -d "$_ew_d" ] && [ -w "$_ew_d" ] && [ -x "$_ew_d" ]; } || refuse_write "the env file ${ENV_FILE} does not exist and its directory cannot be created or written (nearest existing: ${_ew_d}$(if [ -L "$_ew_d" ] && [ ! -e "$_ew_d" ]; then printf ', a symlink to %s, which leads to no existing directory' "$(readlink "$_ew_d")"; elif [ -L "$_ew_d" ] && [ ! -d "$_ew_d" ]; then printf ', a symlink to %s, which is not a directory' "$(readlink "$_ew_d")"; elif [ ! -d "$_ew_d" ]; then printf ', which is not a directory'; fi)). Fix that directory (or the link: mount what it points to, or point it at a directory), then re-run."
fi
unset _ew_d _ew_t

# ----------------------------------------------------------------------------
# GPU presence (advisory: drives the BLANK-brain embedder default + the venv
# CUDA hint). The recall embedder is BRAIN-DICTATED whenever a brain exists.
# ----------------------------------------------------------------------------
gpu_present() {
    [ "$FORCE_CPU" -eq 1 ] && return 1
    gpu_listed
}
gpu_listed() {   # the driver lists cards -- whatever --force-cpu says about THIS run's own device (#8)
    [ -n "$NVSMI" ] && "$NVSMI" >/dev/null 2>&1
}
gpu_count() {
    if [ -n "$NVSMI" ]; then
        "$NVSMI" -L 2>/dev/null | grep -c '^GPU ' || echo 0
    else
        echo 0
    fi
}

hd_embed_model_from_db() {   # bash 3.2 (macOS /bin/bash) cannot parse a heredoc inside $( ): the command and its heredoc live here, called as $(hd_embed_model_from_db) (round 2, B2)
    "$PY_SYS" - "$DB_PATH" <<'PYEOF' 2>/dev/null || true
import os, sqlite3, sys, urllib.request   # escaped URI (iteration 4, LOW 2)
try:
    c = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(os.path.abspath(sys.argv[1])), uri=True)
    row = c.execute("SELECT value FROM metadata WHERE key='active_embedding_table'").fetchone()
    c.close()
except Exception:
    row = None
tab = (row[0] if row else "")
print({"memory_embeddings_v3": "qwen",
       "memory_embeddings_v2": "jina",
       "memory_embeddings":    "nomic"}.get(tab, ""))
PYEOF
}

# ----------------------------------------------------------------------------
# Resolve the embed model from the brain DB (brain-dictated). Reads the
# active_embedding_table metadata read-only; never loads a model. Falls back to
# a GPU/CPU heuristic for a fresh/blank brain.
#   memory_embeddings_v3 -> qwen (1024, apache-2.0)  <-- the SHIPPED GPU default
#   memory_embeddings_v2 -> jina (1024, cc-by-nc-4.0) <-- NON-COMMERCIAL, never a default
#   memory_embeddings    -> nomic (768, apache-2.0)   <-- the CPU tier
# Override with WIZARD_EMBED_MODEL=qwen|jina|nomic.
#
# jina and qwen are the same width and the same base model (Qwen/Qwen3-0.6B-Base)
# and are NOT interchangeable: one table per model keeps this reverse lookup
# honest. jina cannot ship -- cc-by-nc-4.0 forbids commercial use -- so it is
# reachable only by explicit request, never by hardware default.
# ----------------------------------------------------------------------------
resolve_embed_model() {
    # R1: in the ambient shape the brain DICTATES and nothing overrides it -- not
    # --force-cpu (a DEVICE choice, which in this shape must not change the recall tier)
    # and not WIZARD_EMBED_MODEL (a disagreeing value was refused before this line).
    if [ "$SHAPE" = "ambient" ]; then echo "$AMBIENT_TIER"; return; fi
    if [ -n "${WIZARD_EMBED_MODEL:-}" ]; then echo "$WIZARD_EMBED_MODEL"; return; fi
    if [ "$FORCE_CPU" -eq 1 ]; then echo "nomic"; return; fi
    if [ -f "$DB_PATH" ]; then
        local m
        m="$(hd_embed_model_from_db)"
        case "$m" in qwen|jina|nomic) echo "$m"; return ;; esac
    fi
    # Fresh/blank brain: pick by hardware. GPU gets qwen, NOT jina -- jina is
    # non-commercial and must never be selected for someone by default.
    if gpu_present; then echo "qwen"; else echo "nomic"; fi
}
# >>> tier facts (iteration 5: DERIVED from the engine's registry; nothing below types a repo, a width or a table)
# engine_registry PY ENGINE -- THE ONE READER of the engine's tier registry, organs/claude_memory.py EMBED_TIERS, by AST
# (never by import: that would load the engine's heavy imports). One line per tier: tier<TAB>table<TAB>dim<TAB>repo.
# This run's tier facts come from it here, and Step C's C.declare reads the SAME output (ENGINE_REGISTRY), so install.sh
# carries no second copy of what the engine declares. rc != 0: the registry could not be read.
engine_registry() {
    "$1" - "$2" <<'ENGINE_REGISTRY_PY'
import ast, sys
tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
consts, tiers = {}, None
for n in tree.body:
    if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
        if isinstance(n.value, ast.Constant) and isinstance(n.value.value, (str, int)):
            consts[n.targets[0].id] = n.value.value
        elif n.targets[0].id == "EMBED_TIERS" and isinstance(n.value, ast.Dict):
            tiers = n.value
if tiers is None:
    sys.exit("EMBED_TIERS not found in %s" % sys.argv[1])
val = lambda e: e.value if isinstance(e, ast.Constant) else consts[e.id]
rows = []
for k, v in zip(tiers.keys, tiers.values):
    t, d, r = (val(e) for e in v.elts[:3])
    rows.append("%s\t%s\t%d\t%s" % (val(k), t, int(d), r))
if not rows:
    sys.exit("EMBED_TIERS in %s is empty" % sys.argv[1])
print("\n".join(rows))
ENGINE_REGISTRY_PY
}
EMBED_MODEL="$(resolve_embed_model)"
if ! ENGINE_REGISTRY="$(engine_registry "$PY_SYS" "${PKG_ROOT}/organs/claude_memory.py")" || [ -z "$ENGINE_REGISTRY" ]; then
    echo "[install] REFUSED: the engine's tier registry (EMBED_TIERS in ${PKG_ROOT}/organs/claude_memory.py) could not be read with ${PY_SYS}; the package is incomplete or damaged -- re-download it. Nothing was touched." >&2
    exit 2
fi
_tier_row="$(printf '%s\n' "$ENGINE_REGISTRY" | awk -F'\t' -v m="$EMBED_MODEL" '$1 == m')"
if [ -z "$_tier_row" ]; then
    # an unknown WIZARD_EMBED_MODEL degrades to the CPU tier, as before -- chosen by KEY, its facts still the engine's
    EMBED_MODEL="nomic"
    _tier_row="$(printf '%s\n' "$ENGINE_REGISTRY" | awk -F'\t' -v m="$EMBED_MODEL" '$1 == m')"
fi
IFS="$(printf '\t')" read -r _ EMBED_TABLE EMBED_DIM EMBED_REPO <<EOF_TIER_ROW
$_tier_row
EOF_TIER_ROW
unset _tier_row
[ "$EMBED_MODEL" = "jina" ] && warn "WIZARD_EMBED_MODEL=jina selected. jina-embeddings-v5 is CC-BY-NC-4.0 -- NON-COMMERCIAL. Fine for personal use; it must not be used in anything you sell or distribute commercially."
# <<< tier facts
# The reranker DEFAULT hybrid recall lands on.
#
# CRUCIBLE-3 D9-F2: this pre-cached BAAI/bge-reranker-v2-m3 -- the FALLBACK -- and
# the guide called it "the reranker that your DEFAULT hybrid recall uses ... which
# is why the installer fetches it up front so nothing surprise-downloads on your
# first `recall`". Both halves were false at once. The runtime default is
# Qwen/Qwen3-Reranker-0.6B (organs/hybrid_retrieval.py:_resolve_reranker_backend),
# so a fresh install fetched 1.1 GB it would not load and then pulled 1.2 GB it had
# promised not to, on the reviewer's first recall.
#
# The repair is not a better sentence. It is to RESOLVE THE BACKEND THE SAME WAY
# THE RUNTIME RESOLVES IT and pre-cache THAT -- so the installer cannot describe a
# model the process will not load. The two resolvers are kept textually parallel on
# purpose; if one changes, this comment is where the other is found.
QWEN_RERANKER_REPO="Qwen/Qwen3-Reranker-0.6B"     # the runtime DEFAULT (causal-LM)
BGE_RERANKER_REPO="BAAI/bge-reranker-v2-m3"       # the Apache-2.0 fallback (CrossEncoder)

# Mirrors organs/hybrid_retrieval.py:198-208 exactly: an unrecognised value is not
# an error and does not disable reranking -- it degrades to the default.
resolve_reranker_backend() {
    _rb="$(printf '%s' "${VOIDLOOM_RERANKER:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$_rb" in
        qwen|bge) printf '%s' "$_rb" ;;
        *)        printf 'qwen' ;;
    esac
}
RERANKER_BACKEND="$(resolve_reranker_backend)"
if [ "$RERANKER_BACKEND" = "bge" ]; then
    RERANKER_REPO="$BGE_RERANKER_REPO"; RERANKER_FALLBACK_REPO="$QWEN_RERANKER_REPO"
else
    RERANKER_REPO="$QWEN_RERANKER_REPO"; RERANKER_FALLBACK_REPO="$BGE_RERANKER_REPO"
fi
# The fallback is only ever reached when the default FAILS to load, so fetching it
# up front doubles the download for a path most installs never take. Opt in with
# VOIDLOOM_PRECACHE_FALLBACK=1 (worth it on a machine that will go offline).
PRECACHE_FALLBACK="${VOIDLOOM_PRECACHE_FALLBACK:-0}"

# GPU device pin (env, never in Python). Honored from a pre-set CUDA_VISIBLE_DEVICES.
#
# CRUCIBLE-3 D7-F7: this defaulted to `CUDA_DEVICE="${WIZARD_CUDA_DEVICE:-0}"` — it
# PINNED DEVICE 0 whenever the user had not chosen, and then wrote that choice into
# the env file (:281) and into every systemd unit (:994), making it permanent. On
# most multi-GPU desktops device 0 is the card driving the display, and a background
# memory service that takes it does not fail — it succeeds while starving the
# session. The failure is silent, which is why nobody reports it.
#
# The fix is NOT "default to 1": on a single-GPU host device 0 is the only device
# and is correct. The fix is to stop GUESSING. Resolution order, most authoritative
# first, each step an observation rather than an assumption:
#   1. the env file DECLARES it, OUTSIDE the managed block -- by the owner's hand, or by his answer recorded at install
#      (G1, below) -- CUDA_VISIBLE_DEVICES first, then WIZARD_CUDA_DEVICE, in the order the UNITS' resolver reads the file
#      (effective_pin below; organs/voidloom_device.py). (Reference-host round, R7: with the monitor asleep BOTH cards read
#      display_active=Disabled -- measured 2026-09-19.)
#   2. exactly one GPU -- no ambiguity exists; use it ("the only GPU").
#   3. two or more and NOTHING declared -- the installer does not pick: G1 (below) ASKS the owner at the terminal, or takes
#      his answers from --gpu / --display-gpu, and records them as his declarations; with neither it REFUSES, rc 2, before
#      anything is touched. (Env-file writer pass, iteration 5, the owner's ruling: "declared and recorded, never
#      inferred". Measured on the iteration-4 bytes: with nothing declared and the monitor asleep, the display_active
#      heuristic took the first Disabled card -- card 0, the display card, by POSITION -- and wrote it into every unit.)
#   The invoking shell's CUDA_VISIBLE_DEVICES / WIZARD_CUDA_DEVICE is step NOTHING: it is not a declaration (SHELL_PIN_VARS).
#   One observation remains, for THIS RUN'S OWN steps only: under the owner's `WIZARD_CUDA_DEVICE=` ("do not pin the
#   units") on a multi-GPU host, a card that reports display_active=Disabled while ANOTHER card reports Enabled (never a
#   first-Disabled guess: reflector-4) -- the block writes no pin over that declaration, so nothing is recorded from it.
decl_source() {   # decl_source KEY -- where the env file's declaration of KEY came from, for the messages and the record
    local _pv
    _pv="$(env_outside provenance "$1")" || _pv=""
    printf 'declared by %s in %s (outside the managed block%s)' "$1" "$ENV_FILE" "${_pv:+; $_pv}"
}
resolve_gpu_device() {   # prints "<index>|<where it came from>"; rc 1 = no card for this run
    # ASSIGNED-EMPTY is not ABSENT (iteration 3, items 3-4): an owner's `CUDA_VISIBLE_DEVICES=` is CUDA's own
    # "no device", which the units honour (voidloom_device: ambient-empty) -- so the installer honours it too;
    # an owner's `WIZARD_CUDA_DEVICE=` is "do not pin the units" (env:declared-empty), and the block then writes
    # no CUDA_VISIBLE_DEVICES over it (write_env_file), so any card chosen below is THIS RUN's own, and says so.
    local _own=""
    if env_outside_has CUDA_VISIBLE_DEVICES; then
        _declared="$(env_outside unitpin CUDA_VISIBLE_DEVICES)"
        local _empty=""; [ -z "$_declared" ] && _empty=" -- EMPTY: no device"   # (not ${_declared:-...}: that appends a set value)
        printf '%s|%s' "$_declared" "$(decl_source CUDA_VISIBLE_DEVICES)${_empty}"; return 0
    fi
    if env_outside_has WIZARD_CUDA_DEVICE; then
        # Drew's ruling (memory anchor vl_gpu_minus1_ruling): WIZARD_CUDA_DEVICE=-1 / none is NO GPU for every consumer
        # -- the units (voidloom_device: env-file:declared-no-gpu) and THIS run's own steps alike: no card, never a
        # heuristic one. The installer itself still never WRITES it (--gpu refuses it; --force-cpu is the CPU run).
        case "$(env_outside get WIZARD_CUDA_DEVICE | tr '[:upper:]' '[:lower:]' | tr -d "\"' ")" in
            -1|none) printf '|%s -- NO GPU: the units and this run use no card' "$(decl_source WIZARD_CUDA_DEVICE)"; return 0 ;;
        esac
        _declared="$(env_outside unitpin WIZARD_CUDA_DEVICE)"
        if [ -n "$_declared" ]; then printf '%s|%s' "$_declared" "$(decl_source WIZARD_CUDA_DEVICE)"; return 0; fi
        _own=" -- for this run's own steps only: the owner's WIZARD_CUDA_DEVICE= in ${ENV_FILE} is EMPTY ('do not pin'), so the units stay unpinned"
    fi
    _n="$(gpu_count)"
    if [ "${_n:-0}" -le 1 ]; then printf '0|%s%s' "the only GPU" "$_own"; return 0; fi
    if [ -n "$_own" ]; then
        # A card can be told apart only when ANOTHER card reports display_active=Enabled: with every card Disabled (a
        # monitor asleep -- measured on the reference host 2026-09-19) the first Disabled card is a guess by POSITION, and on
        # that host it is card 0, the display card (reflector-4, executed).
        _free="$("$NVSMI" --query-gpu=index,display_active --format=csv,noheader 2>/dev/null \
                 | awk -F', *' '$2 == "Enabled" { e = 1 } $2 == "Disabled" && f == "" { f = $1 } END { if (e && f != "") print f }')"
        if [ -n "$_free" ]; then printf '%s|%s' "$_free" "chosen because nvidia-smi reports it display_active=Disabled while another card reports Enabled${_own}"; return 0; fi
    fi
    return 1
}
effective_pin() {
    # The device the UNITS will see after this run: the env file read as systemd's EnvironmentFile= hands it to
    # them, then their own resolver's rules (organs/voidloom_device.py; env_outside unitpin -- iteration 4, HIGH 1):
    # the owner's CUDA_VISIBLE_DEVICES outside the block (EMPTY included:
    # CUDA's "no device"), else the owner's WIZARD_CUDA_DEVICE outside it (the block writes no
    # CUDA_VISIBLE_DEVICES over it; EMPTY = do not pin), else the block's own (GPU_DEVICE).
    if env_outside_has CUDA_VISIBLE_DEVICES; then env_outside unitpin CUDA_VISIBLE_DEVICES; return 0; fi
    if env_outside_has WIZARD_CUDA_DEVICE; then env_outside unitpin WIZARD_CUDA_DEVICE; return 0; fi
    printf '%s' "${GPU_DEVICE:-}"
}
effective_pin_key() {
    # WHO declared that pin: the env-file key OUTSIDE the managed block that effective_pin read, or nothing
    # when the pin is this run's own choice (the invoking shell, the only card, the display_active heuristic).
    # P4 reads it (ruler VL-REF-v1.2): under basis owner-declared the PIN must be the owner's declaration too.
    if env_outside_has CUDA_VISIBLE_DEVICES; then echo CUDA_VISIBLE_DEVICES; return 0; fi
    if env_outside_has WIZARD_CUDA_DEVICE; then echo WIZARD_CUDA_DEVICE; return 0; fi
    return 0
}

# ----------------------------------------------------------------------------
# THE ASK HELPER -- THE ONE WAY this installer puts a question to its OWNER. Functions only; bash 3.2 / BSD clean.
# (A section of install.sh, not a file of its own: its first reuser, the model-choice round's a-e thinker question, is
# asked by this script too, and a new package file would change docs/LAYOUT.md's derived file count -- a document main
# has changed since 524c63e.)
#
# WHY (env-file writer pass, iteration 5, G1 -- the owner's ruling, 2026-09-26): "declared and recorded, never inferred".
# Where the machine cannot know an answer (which GPU drives the monitor), the installer ASKS the owner and records his
# answer as HIS declaration -- and where nobody can be asked, it refuses and names the flag that carries the answer. It
# never picks one for him. Reused by: install.sh's GPU choice (G1) and -- by rule -- the model-choice round's a-e thinker
# question, so every question the installer asks behaves the same way:
#
#   ask_tty                          rc 0 when a terminal is reachable through /dev/tty, for reading AND writing -- even
#                                    when stdin is a pipe (`curl ... | bash`); rc 1 otherwise (a cron job, a CI runner, an
#                                    AI agent's tool call). Nothing here ever reads stdin: stdin may be the script itself.
#   ask_say TEXT...                  one line to the terminal (never stdout: stdout may be captured into a log)
#   ask_line VAR PROMPT CHECK        prints PROMPT on the terminal and reads one line from it. A BLANK answer is asked again.
#                                    CHECK (a function name) is called with the trimmed answer: rc 0 accepts it, any other
#                                    rc asks again (CHECK prints why, through ask_say). rc 0: VAR holds the answer;
#                                    rc 3: the terminal gave no more input (end of input) -- no answer, the caller refuses.
#                                    EVERY CHECK COMPARES THE ANSWER AS FIXED TEXT -- `[ "$1" = "<literal>" ]`, `case "$1" in
#                                    <literal>|<literal>) ...` (the answer is the SUBJECT, never the pattern), or
#                                    `grep -qxF -- "$1"` against a list -- and NEVER builds a grep / regex / glob PATTERN out
#                                    of the answer. Measured on 0b061d7 (EW5-02): the GPU question's `grep -qx "$1"` took '.',
#                                    '-e0', '.*', '[1]' and '0*' as matches instead of asking again, and '0*' switched the
#                                    monitor card's typed confirmation off. An answer shaped like a pattern is a wrong
#                                    answer: it is asked again (voidloom/tests/test_installer_questions.py binds this).
#   ask_confirm EXPECTED PROMPT      the TYPED confirmation: prints PROMPT, reads one line; rc 0 only when the trimmed line
#                                    is EXPECTED exactly; rc 1 for anything else (the caller asks its question again); rc 3
#                                    at end of input.
#   ask_yes_no PROMPT                a [y/N] question: rc 0 for y / yes (any case), rc 1 for n / no or a BLANK line (the
#                                    capital N is the answer nothing typed means), rc 3 at end of input; anything else
#                                    is asked again -- compared as FIXED TEXT like every check here.
#   ask_refusal QUESTION FLAGS       the non-interactive refusal's words (to stderr): the question needs an answer, no
#                                    terminal is reachable to ask it, the FLAGS that carry the answer, and the rule for an
#                                    installing AI agent. The CALLER exits (rc 2) after its own clean-up.
#
# The answer is never taken from the environment: a variable in the invoking shell is not the owner's act (it may have
# been set for another tool, or by an agent), so it answers nothing.

ask_tty() {
    # Opening /dev/tty fails (ENXIO) when the process has no controlling terminal; a subshell keeps the attempt's
    # file descriptors and its error message out of the caller.
    ( : </dev/tty >/dev/tty ) 2>/dev/null
}

ask_say() {
    printf '%s\n' "$*" >/dev/tty
}

_ask_trim() {   # _ask_trim TEXT -- TEXT without leading and trailing blanks (spaces, tabs, a CR)
    local _t="$1"
    _t="${_t#"${_t%%[![:space:]]*}"}"
    _t="${_t%"${_t##*[![:space:]]}"}"
    printf '%s' "$_t"
}

ask_line() {    # ask_line VAR PROMPT CHECK
    local _var="$1" _prompt="$2" _check="$3" _raw _ans
    while :; do
        printf '%s ' "$_prompt" >/dev/tty
        if ! IFS= read -r _raw </dev/tty; then
            # end of input: a partial line with no newline is still an answer; nothing at all is none
            [ -n "$_raw" ] || { printf '\n' >/dev/tty; return 3; }
        fi
        _ans="$(_ask_trim "$_raw")"
        if [ -z "$_ans" ]; then
            ask_say "  An answer is needed here (nothing is chosen for you)."
            continue
        fi
        if "$_check" "$_ans"; then
            printf -v "$_var" '%s' "$_ans"
            return 0
        fi
    done
}

ask_confirm() {  # ask_confirm EXPECTED PROMPT
    local _want="$1" _prompt="$2" _raw
    printf '%s ' "$_prompt" >/dev/tty
    if ! IFS= read -r _raw </dev/tty; then
        [ -n "$_raw" ] || { printf '\n' >/dev/tty; return 3; }
    fi
    [ "$(_ask_trim "$_raw")" = "$_want" ]
}

ask_yes_no() {   # ask_yes_no PROMPT -- rc 0 yes, 1 no (blank = no), 3 end of input
    local _raw _ans
    while :; do
        printf '%s ' "$1" >/dev/tty
        if ! IFS= read -r _raw </dev/tty; then
            [ -n "$_raw" ] || { printf '\n' >/dev/tty; return 3; }
        fi
        _ans="$(_ask_trim "$_raw")"
        case "$_ans" in
            y|Y|yes|Yes|YES) return 0 ;;
            ""|n|N|no|No|NO) return 1 ;;
        esac
        ask_say "  '$_ans' is not y or n (a blank line is n)."
    done
}

ask_refusal() {  # ask_refusal QUESTION FLAGS
    printf '%s\n' "[install] REFUSED: ${1} This needs the owner's answer, and no terminal is reachable to ask it (no /dev/tty: a cron job, a CI runner, or an AI agent's tool call). Pass the answer as ${2}. An installing AI agent must put this question to the human and pass the human's answer as ${2} -- it must never choose the answer itself." >&2
}
# ------------------------------------------------------------------------ (end of THE ASK HELPER)

# ----------------------------------------------------------------------------
# G1 -- THE OWNER'S GPU CHOICE (env-file writer pass, iteration 5; the owner's ruling, 2026-09-26: "There is value in
# enabling the explicit picking of a GPU when more than one is detected ... It would also then work if you expanded this
# to a 4-5 GPU setup"). Governing principle: DECLARED AND RECORDED, NEVER INFERRED.
#   * one GPU: unchanged -- the only GPU, no question (one plain line when it drives a display);  no GPU: unchanged.
#   * two or more, and NOTHING declared (neither WIZARD_CUDA_DEVICE nor CUDA_VISIBLE_DEVICES outside the managed block):
#       - a terminal reachable through /dev/tty (even when stdin is a pipe): a table of EVERY card (index, name, memory,
#         display_active), nothing pre-selected, and two questions -- which card drives the monitor(s) ('none' allowed),
#         which card the brain's background jobs use -- asked again on a blank or invalid answer; a work card that is the
#         monitor card, or reads Enabled, costs a TYPED confirmation (the card number again);
#       - no terminal: the answers from --display-gpu / --gpu; with neither, REFUSED rc 2 before anything is touched, the
#         cards listed and the flags named -- and an installing AI agent is told to ask the human, never to choose.
#     The answers are written as the OWNER'S declarations, OUTSIDE the managed block, through the pass's one writer
#     (install/env_file.py `declare`: the append path, verified as systemd reads it), each under a comment "chosen at
#     install <date>" -- WIZARD_DISPLAY_INDEX=<m|none> and WIZARD_CUDA_DEVICE=<n>, exactly what the reference desktop
#     carries by hand. From then on every reader treats them as declarations; a re-run never asks again.
#   * a declaration already in the file is honoured and NEVER re-asked; --gpu / --display-gpu that disagree with it are
#     refused (the file is changed by editing its line, never by a flag).
# The questions are asked through THE ASK HELPER above, the installer's one way to ask its owner (the model-choice
# round's a-e thinker question reuses it).
# ----------------------------------------------------------------------------
G1_HOW=""; G1_DATE=""; G1_LINES=""; G1_WORK=""; G1_DISPLAY=""; G1_CARDS=""
gpu_cards() {   # every card, one line each: <index>|<name>|<total memory>|<display_active: Enabled|Disabled|unknown>
    "$PY_SYS" -B - "$NVSMI" <<'PYEOF'
import re, subprocess, sys
smi = sys.argv[1]
def q(args):
    try:
        p = subprocess.run([smi] + args, capture_output=True, text=True, timeout=20)
    except Exception:
        return ""
    return p.stdout if p.returncode == 0 else ""
disp = {}
for line in q(["--query-gpu=index,display_active", "--format=csv,noheader"]).splitlines():
    parts = [x.strip() for x in line.split(",")]
    if len(parts) == 2 and re.fullmatch(r"\d+", parts[0]):
        disp[parts[0]] = parts[1] if parts[1] in ("Enabled", "Disabled") else "unknown"
rows = []
for line in q(["--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu,compute_cap",
               "--format=csv,noheader,nounits"]).splitlines():
    parts = [x.strip() for x in line.split(",")]
    if len(parts) >= 4 and re.fullmatch(r"\d+", parts[0]):
        rows.append((parts[0], parts[2], parts[3] + " MiB" if re.fullmatch(r"\d+", parts[3]) else "?"))
if not rows:
    for line in q(["-L"]).splitlines():
        m = re.match(r"GPU (\d+): (.*?) \(UUID", line)
        if m:
            rows.append((m.group(1), m.group(2), "?"))
for i, name, mem in rows:
    print("%s|%s|%s|%s" % (i, name.replace("|", "/"), mem, disp.get(i, "unknown")))
PYEOF
}
g1_table() {   # the cards as the owner reads them, one line each (printed by the caller to the terminal or to stderr)
    printf '%s\n' "  card  name                             memory       display_active (now)"
    printf '%s\n' "$G1_CARDS" | awk -F'|' '{ printf "  %-5s %-32s %-12s %s\n", $1, $2, $3, $4 }'
    printf '%s\n' "  (display_active is what the driver reports NOW: a monitor that is asleep reads Disabled, so this installer never guesses from it.)"
}
g1_listed() {   # g1_listed ANSWER -- digits only, compared with the listed indices as FIXED TEXT (never a pattern: EW5-02)
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$G1_CARDS" | cut -d'|' -f1 | grep -qxF -- "$1"
}
g1_state()  { printf '%s\n' "$G1_CARDS" | awk -F'|' -v c="$1" '$1 == c { print $4 }'; }
g1_indices() { printf '%s\n' "$G1_CARDS" | cut -d'|' -f1 | tr '\n' ' ' | sed 's/ $//; s/ /, /g'; }
g1_check_display() {   # ask_line's CHECK for the monitor question
    case "$1" in [Nn][Oo][Nn][Ee]) return 0 ;; esac
    g1_listed "$1" && return 0
    ask_say "  '$1' is not one of the cards listed above ($(g1_indices)), nor 'none'."
    return 1
}
g1_check_work() {      # ask_line's CHECK for the work-card question
    g1_listed "$1" && return 0
    case "$1" in -1|[Nn][Oo][Nn][Ee]) ask_say "  '$1' is not a card: WIZARD_CUDA_DEVICE takes a card index only; '-1' / 'none' are not written by this installer (a hand-written one means CPU only). To keep this run off every GPU pass --force-cpu; to leave the brain's jobs unpinned write WIZARD_CUDA_DEVICE= (EMPTY) by hand outside the managed block."; return 1 ;; esac
    ask_say "  '$1' is not one of the cards listed above ($(g1_indices))."
    return 1
}
g1_cost() {   # g1_cost CARD -- why choosing it needs a typed confirmation, or nothing when it does not
    local _why=""
    [ "$1" = "$G1_DISPLAY" ] && _why="card $1 is the card you said drives your monitor(s)"
    if [ "$(g1_state "$1")" = "Enabled" ]; then
        _why="${_why:+${_why}, and it }${_why:-card $1 }reports display_active=Enabled: it drives a display right now"
    fi
    [ -n "$_why" ] && printf '%s' "${_why}. A background model on it can take most of its memory and starve your desktop -- the screen stutters, freezes or goes black while the job runs -- and nothing reports it."
}
g1_exit_refused() { own_sidecar_restore; exit 2; }
g1_ambient_cost_refusal() {   # g1_ambient_cost_refusal COST -- the words for a card this AMBIENT upgrade cannot take
    printf '%s' "this upgrade (--tier1-ambient) PROVES that the brain's jobs never share a card with a display (its check P4), and ${1%%. A background*}"
}
g1_choosable_left() {   # in the ambient shape: is at least one listed card free of a cost (P4 would pass it)?
    local _c
    for _c in $(printf '%s\n' "$G1_CARDS" | cut -d'|' -f1); do
        [ -z "$(g1_cost "$_c")" ] && return 0
    done
    return 1
}
g1_ask() {   # the owner answers at the terminal (THE ASK HELPER)
    local _rc _cost
    ask_say ""
    ask_say "[install] This host has $(printf '%s\n' "$G1_CARDS" | wc -l | tr -d ' ') GPUs, and nothing in ${ENV_FILE} declares which one the brain's background jobs use."
    ask_say "[install] A background job on the card that drives your monitor can starve your desktop, silently -- so this installer does not"
    ask_say "[install] guess. It asks you, and records your answers in that file (outside the managed block), where you can edit them later."
    ask_say "[install] If you are an AI agent running this installer for someone: stop, put these two questions to them, and pass their answers as --display-gpu/--gpu -- never answer them yourself."
    ask_say ""
    g1_table >/dev/tty
    ask_say ""
    if [ -n "$G1_DISPLAY" ]; then
        ask_say "  Your monitor card is already declared in ${ENV_FILE}: WIZARD_DISPLAY_INDEX=${G1_DISPLAY} (not asked again)."
    else
        ask_line G1_DISPLAY "Which card drives your monitor(s)? (a card number, or 'none')" g1_check_display; _rc=$?
        [ "$_rc" -eq 0 ] || { echo "[install] REFUSED: the GPU question got no answer (end of input). ${NOTHING_TOUCHED}" >&2; g1_exit_refused; }
        case "$G1_DISPLAY" in [Nn][Oo][Nn][Ee]) G1_DISPLAY="none" ;; esac
    fi
    # R-1 (EW5-01): the ambient upgrade PROVES the card (P4) before anything is laid, and P4 refuses every card the typed
    # confirmation guards -- so in that shape such a card is never offered behind a confirmation, and never recorded
    if [ "$SHAPE" = "ambient" ] && ! g1_choosable_left; then
        echo "[install] REFUSED: every card this host lists either drives your monitor(s) by your answer or reports display_active=Enabled, and $(g1_ambient_cost_refusal "the jobs would share a display card"). No card can pass that check; nothing is recorded. ${NOTHING_TOUCHED}" >&2
        g1_exit_refused
    fi
    while :; do
        ask_line G1_WORK "Which card should the brain's background jobs use? (a card number)" g1_check_work; _rc=$?
        [ "$_rc" -eq 0 ] || { echo "[install] REFUSED: the GPU question got no answer (end of input). ${NOTHING_TOUCHED}" >&2; g1_exit_refused; }
        _cost="$(g1_cost "$G1_WORK")"
        [ -n "$_cost" ] || break
        if [ "$SHAPE" = "ambient" ]; then
            ask_say "  Card ${G1_WORK} cannot be chosen in this upgrade: $(g1_ambient_cost_refusal "$_cost"). Choose another card."
            continue
        fi
        ask_say "  Careful: ${_cost}"
        ask_confirm "$G1_WORK" "  To use card ${G1_WORK} anyway, type ${G1_WORK} again (anything else goes back to the question):"; _rc=$?
        [ "$_rc" -eq 0 ] && break
        [ "$_rc" -eq 3 ] && { echo "[install] REFUSED: the GPU question got no answer (end of input). ${NOTHING_TOUCHED}" >&2; g1_exit_refused; }
        ask_say "  Not confirmed -- choose again."
    done
    G1_HOW="answered at the terminal"
}
g1_flags() {   # the answers came as --gpu / --display-gpu
    local _missing="" _cost _rc
    [ -n "$GPU_ANSWER" ] || _missing="--gpu <card>"
    [ -n "$G1_DISPLAY" ] || [ -n "$DISPLAY_ANSWER" ] || _missing="${_missing:+$_missing and }--display-gpu <card|none>"
    if [ -n "$_missing" ]; then
        { echo "[install] This host has $(printf '%s\n' "$G1_CARDS" | wc -l | tr -d ' ') GPUs:"; g1_table | sed 's/^/[install] /'; } >&2
        ask_refusal "Two questions answer which GPU the brain's background jobs use (which card drives the monitor(s); which card the jobs use), and $([ -n "$GPU_ANSWER$DISPLAY_ANSWER" ] && echo "the flags given answer only one" || echo "none was answered")." "${_missing}"
        echo "[install] Nothing was touched." >&2
        g1_exit_refused
    fi
    g1_listed "$GPU_ANSWER" || { echo "[install] REFUSED: --gpu ${GPU_ANSWER} names no card nvidia-smi lists ($(g1_indices)). Nothing was touched." >&2; g1_exit_refused; }
    if [ -n "$DISPLAY_ANSWER" ]; then
        if [ -n "$G1_DISPLAY" ] && [ "$G1_DISPLAY" != "$DISPLAY_ANSWER" ]; then
            echo "[install] REFUSED: ${ENV_FILE} already declares WIZARD_DISPLAY_INDEX=${G1_DISPLAY}, and --display-gpu ${DISPLAY_ANSWER} disagrees. A flag never overwrites a declaration: to change it, edit that line (outside the managed block), then re-run. Nothing was touched." >&2
            g1_exit_refused
        fi
        [ "$DISPLAY_ANSWER" = "none" ] || g1_listed "$DISPLAY_ANSWER" || { echo "[install] REFUSED: --display-gpu ${DISPLAY_ANSWER} names no card nvidia-smi lists ($(g1_indices)), and is not 'none'. Nothing was touched." >&2; g1_exit_refused; }
        G1_DISPLAY="$DISPLAY_ANSWER"
    fi
    G1_WORK="$GPU_ANSWER"
    _cost="$(g1_cost "$G1_WORK")"
    if [ -n "$_cost" ] && [ "$SHAPE" = "ambient" ]; then
        # R-1 (EW5-01): never recorded, never offered behind a confirmation -- P4 would refuse it after the recording
        echo "[install] REFUSED: --gpu ${G1_WORK}: $(g1_ambient_cost_refusal "$_cost"). Choose another card with --gpu. Nothing was touched." >&2
        g1_exit_refused
    fi
    if [ -n "$_cost" ]; then
        if ask_tty; then
            ask_say "  Careful: ${_cost}"
            ask_confirm "$G1_WORK" "  To use card ${G1_WORK} anyway, type ${G1_WORK} again:"; _rc=$?
            [ "$_rc" -eq 0 ] || { echo "[install] REFUSED: --gpu ${G1_WORK} was not confirmed (the card number was not typed again). Choose another card with --gpu. Nothing was touched." >&2; g1_exit_refused; }
        else
            echo "[install] REFUSED: --gpu ${G1_WORK}: ${_cost} Choosing it needs the owner's TYPED confirmation, and no terminal is reachable to ask for it: run the installer in a terminal (it asks), or write both lines by hand OUTSIDE the managed block of ${ENV_FILE} (WIZARD_DISPLAY_INDEX=${G1_DISPLAY} and WIZARD_CUDA_DEVICE=${G1_WORK}) -- an installing AI agent must leave that to the human. Nothing was touched." >&2
            g1_exit_refused
        fi
    fi
    G1_HOW="from --gpu ${G1_WORK}$([ -n "$DISPLAY_ANSWER" ] && echo " --display-gpu ${DISPLAY_ANSWER}")"
}
G1_DEFERRED=0; G1_WHAT=""
g1_record() {   # the answers become the OWNER's declarations, through the ONE writer (install/env_file.py declare)
    local _wd _out _rc _what="WIZARD_CUDA_DEVICE=${G1_WORK}" _now
    # EW5-06: the cards may change while the question is open -- an answer is recorded only if nvidia-smi still lists it
    _now="$(gpu_cards | cut -d'|' -f1)"
    printf '%s\n' "$_now" | grep -qxF -- "$G1_WORK" || { echo "[install] REFUSED: card ${G1_WORK} is no longer listed by nvidia-smi (the cards changed while the question was open: now $(printf '%s' "$_now" | tr '\n' ' ')). ${NOTHING_TOUCHED}" >&2; g1_exit_refused; }
    case "$G1_DISPLAY" in none) ;; *) printf '%s\n' "$_now" | grep -qxF -- "$G1_DISPLAY" || { echo "[install] REFUSED: card ${G1_DISPLAY} (your monitor card) is no longer listed by nvidia-smi (the cards changed while the question was open). ${NOTHING_TOUCHED}" >&2; g1_exit_refused; } ;; esac
    G1_DATE="$(date -u +%Y-%m-%d)"
    _wd="$(mktemp -d 2>/dev/null)" || die "could not create a temporary directory to record your GPU answers; nothing was touched."
    set --
    if ! env_outside_has WIZARD_DISPLAY_INDEX; then
        _what="WIZARD_DISPLAY_INDEX=${G1_DISPLAY} and ${_what}"
        set -- "$@" "WIZARD_DISPLAY_INDEX=${G1_DISPLAY}" "WIZARD_DISPLAY_INDEX: chosen at install ${G1_DATE} (${G1_HOW}) -- the card that drives the monitor(s); none = no card does. Edit this line to change it."
    fi
    set -- "$@" "WIZARD_CUDA_DEVICE=${G1_WORK}" "WIZARD_CUDA_DEVICE: chosen at install ${G1_DATE} (${G1_HOW}) -- the card the brain's background jobs use. Edit this line to change it; the installer never asks again while it is here."
    [ -f "$ENV_FILE" ] && cp -p "$ENV_FILE" "${_wd}/source"     # what the image was made from (R-1: the deferred recording)
    _out="$("$PY_SYS" -B "$ENV_READER" --file "$ENV_FILE" --begin "$ENV_BEGIN" --end "$ENV_END" declare "${_wd}/image" "$@")"; _rc=$?
    case "$_rc" in
        0) G1_LINES="$(printf '%s\n' "$_out" | sed -n 's/^OK	lines=//p')" ;;
        2) rm -rf "$_wd"; refuse_write "your GPU answers cannot be recorded in this file: $(printf '%s\n' "$_out" | sed -n 's/^REFUSE	//p' | tr '\n' ' ')" ;;
        *) rm -rf "$_wd"; die "install/env_file.py could not record your GPU answers in ${ENV_FILE} (rc ${_rc}); nothing was touched." ;;
    esac
    G1_WHAT="$_what"
    if [ "$DRY_RUN" -eq 1 ]; then
        ENV_VIEW="${_wd}/image"; G1_VIEW_DIR="$_wd"
        say "  DRY: would record your answers in ${ENV_FILE}, OUTSIDE the managed block (lines ${G1_LINES}), each under a dated comment: ${_what} (${G1_HOW}). This dry run shows the run those answers make; it writes nothing."
        return 0
    fi
    if [ "$SHAPE" = "ambient" ]; then
        # R-1 (EW5-01): the ambient upgrade PROVES before it lays -- and a refused upgrade must leave the units EXACTLY as
        # they were. So the answers are HELD (this run reads them through ENV_VIEW, the verified image) and written only
        # after P1-P5 pass (g1_record_held, Step A.0). Measured on 0b061d7: the answers were recorded first, P4 refused,
        # and the existing units read the refused card from then on.
        ENV_VIEW="${_wd}/image"; G1_VIEW_DIR="$_wd"; G1_DEFERRED=1
        say "  GPU: your answers (${_what}, ${G1_HOW}) are HELD until the ambient proofs pass: only then are they recorded in ${ENV_FILE} (outside the managed block, dated). A refused upgrade records nothing and leaves the units exactly as they were."
        return 0
    fi
    g1_install_image "$_wd"
}
g1_install_image() {   # g1_install_image WORKDIR -- the recording itself (the verified image over the env file)
    local _wd="$1"
    env_install_image "${_wd}/image" "$_wd" "your GPU answers"
    run_argv chmod 600 "$ENV_FILE" 2>/dev/null || true
    rm -rf "$_wd"
    NOTHING_TOUCHED="Nothing else was touched: only your GPU answers were recorded, as your declarations, in ${ENV_FILE} (lines ${G1_LINES}: ${G1_WHAT}) -- a re-run reads them and does not ask again."
    RECORDED_NOTE="${RECORDED_NOTE:+$RECORDED_NOTE }(Your GPU answers WERE recorded in ${ENV_FILE}, lines ${G1_LINES}: ${G1_WHAT} -- a re-run reads them and does not ask again.)"
    say "  GPU: recorded your answers in ${ENV_FILE} (lines ${G1_LINES}, OUTSIDE the managed block, dated): ${G1_WHAT} (${G1_HOW}). They are your declarations now: edit those lines to change them; this installer never asks again while they are there."
}
g1_record_held() {   # the ambient proofs passed: record the answers G1 held (R-1) -- from the SAME file the image was made of
    [ "$G1_DEFERRED" -eq 1 ] || return 0
    local _wd="$G1_VIEW_DIR"
    if [ -f "${_wd}/source" ]; then cmp -s "${_wd}/source" "$ENV_FILE"; else [ ! -e "$ENV_FILE" ]; fi \
        || die "${ENV_FILE} changed while this run held your GPU answers (between the question and the end of the proofs); they were NOT recorded and nothing was laid. Re-run the installer."
    G1_VIEW_DIR=""; ENV_VIEW="$ENV_FILE"; G1_DEFERRED=0
    g1_install_image "$_wd"
}
gpu_choice() {   # G1 on a host with two or more GPUs and nothing declared
    G1_CARDS="$(gpu_cards)"
    [ -n "$G1_CARDS" ] || { echo "[install] REFUSED: nvidia-smi reports $(gpu_count) GPUs but lists none by index, so no card can be named. Nothing was touched." >&2; g1_exit_refused; }
    G1_DISPLAY=""
    env_outside_has WIZARD_DISPLAY_INDEX && G1_DISPLAY="$(env_outside get WIZARD_DISPLAY_INDEX)"
    # EW5-06: a hand WIZARD_DISPLAY_INDEX that names no listed card (and is not 'none') is no answer: named, and the monitor
    # question is asked for this run (the owner's line is never rewritten by the installer -- he corrects it)
    case "$G1_DISPLAY" in
        '') ;;
        [Nn][Oo][Nn][Ee]) G1_DISPLAY="none" ;;
        *) if ! g1_listed "$G1_DISPLAY"; then
               echo "[install] NOTE: ${ENV_FILE} declares WIZARD_DISPLAY_INDEX=${G1_DISPLAY} (line $(env_outside line WIZARD_DISPLAY_INDEX)), which names no card nvidia-smi lists ($(g1_indices)) and is not 'none': it answers nothing here. Correct that line by hand; this run asks (or takes --display-gpu) instead." >&2
               G1_DISPLAY=""
           fi ;;
    esac
    if [ -n "$GPU_ANSWER$DISPLAY_ANSWER" ]; then
        g1_flags
    elif ask_tty; then
        g1_ask
    else
        { echo "[install] This host has $(printf '%s\n' "$G1_CARDS" | wc -l | tr -d ' ') GPUs:"; g1_table | sed 's/^/[install] /'; } >&2
        local _flags="--display-gpu <card|none> --gpu <card>" _bp
        [ -n "$G1_DISPLAY" ] && _flags="--gpu <card>"      # the monitor card is already declared: one answer is missing
        ask_refusal "This host has $(printf '%s\n' "$G1_CARDS" | wc -l | tr -d ' ') GPUs and nothing in ${ENV_FILE} declares which one the brain's background jobs use (WIZARD_CUDA_DEVICE, outside the managed block); this installer never picks it for you (a background job on the display card starves the desktop, silently). The cards are listed above." "$_flags"
        # R-5 / EW5-12: an UPGRADE of an install an earlier installer pinned: say which card the old block pins, and whose
        _bp="$(env_outside effective CUDA_VISIBLE_DEVICES)"
        [ -n "$_bp" ] && echo "[install] The managed block of ${ENV_FILE} currently pins card ${_bp} (CUDA_VISIBLE_DEVICES='${_bp}', line $(env_outside effective-line CUDA_VISIBLE_DEVICES)) -- an earlier installer's choice, not a declaration: the units keep reading it until this run is answered." >&2
        echo "[install] Nothing was touched." >&2
        g1_exit_refused
    fi
    g1_record
}
gpu_flags_unused() {   # --gpu / --display-gpu given where G1 asked nothing: agreement is noted, disagreement refused
    [ -n "$GPU_ANSWER$DISPLAY_ANSWER" ] && [ -z "$G1_HOW" ] || return 0
    if ! gpu_listed || [ "$(gpu_count)" -lt 2 ]; then
        say "  NOTE: --gpu / --display-gpu answer the question a host with two or more GPUs is asked; this run sees $(gpu_listed && gpu_count || echo 0), so they are not used and nothing is recorded."
        return 0
    fi
    local _k _have
    _k="$(effective_pin_key)"
    if [ -n "$GPU_ANSWER" ] && [ -n "$_k" ]; then
        _have="$(env_outside unitpin "$_k")"
        if [ "$_have" != "$GPU_ANSWER" ]; then
            echo "[install] REFUSED: ${ENV_FILE} already declares ${_k}=${_have} (outside the managed block), and --gpu ${GPU_ANSWER} disagrees. A flag never overwrites a declaration: to change the card, edit that line, then re-run without --gpu. Nothing was touched." >&2
            own_sidecar_restore; exit 2
        fi
        say "  NOTE: --gpu ${GPU_ANSWER} agrees with ${_k}=${_have} already declared in ${ENV_FILE}; nothing to record."
    fi
    if [ -n "$DISPLAY_ANSWER" ]; then
        if env_outside_has WIZARD_DISPLAY_INDEX; then
            _have="$(env_outside get WIZARD_DISPLAY_INDEX)"
            if [ "$_have" != "$DISPLAY_ANSWER" ]; then
                echo "[install] REFUSED: ${ENV_FILE} already declares WIZARD_DISPLAY_INDEX=${_have} (outside the managed block), and --display-gpu ${DISPLAY_ANSWER} disagrees. A flag never overwrites a declaration: to change it, edit that line, then re-run. Nothing was touched." >&2
                own_sidecar_restore; exit 2
            fi
            say "  NOTE: --display-gpu ${DISPLAY_ANSWER} agrees with WIZARD_DISPLAY_INDEX=${_have} already declared in ${ENV_FILE}; nothing to record."
        else
            say "  NOTE: --display-gpu ${DISPLAY_ANSWER} is NOT recorded: the card is already declared (${_k}), and this installer records GPU answers only where nothing is declared. To declare the monitor card, add WIZARD_DISPLAY_INDEX=${DISPLAY_ANSWER} to ${ENV_FILE} on a line of its own OUTSIDE the managed block."
        fi
    fi
}
gpu_shell_note() {   # the invoking shell's pin variables: never a declaration (SHELL_PIN_VARS), and the run says so
    [ -n "$SHELL_PIN_VARS" ] || return 0
    local _c="no pinned card"; [ -n "$GPU_DEVICE" ] && _c="card ${GPU_DEVICE}"
    say "  NOTE: the invoking shell's pin variables (${SHELL_PIN_VARS}) are not a declaration: this installer writes nothing from them, and neither this run nor the units use them. This run and the units use ${_c} (${_gpu_resolved#*|}). To choose a card, declare WIZARD_CUDA_DEVICE=<index> in ${ENV_FILE} outside the managed block -- or, where nothing is declared, answer the installer's question (or pass --gpu / --display-gpu)."
}

# ----------------------------------------------------------------------------
# M10 -- THE OVERNIGHT THINKER: THE a-f QUESTION (model-choice unit, Round 1, Wave B; the owner's rulings 2026-09-26,
# and 2026-10-16: d) Gemini added, e) Not now kept, Other moved to f), names are the non-interactive form)
#
#   "Should your brain think while you sleep, and with which model?"
#       a) Claude   b) Codex   c) Kimi   d) Gemini (Google; runs through Google's Antigravity program)
#       e) Not now -- no model; keep the simple nightly routines   f) Other (which model?)
#
# The words are DATA (organs/thinkers.json `question`), so the installer, install/install_crons.sh's refusal and the
# agent-facing instructions (README.md, docs/ONBOARDING_GUIDE.md) say the same thing, word for word. NOTHING IS ASSUMED:
#   * the answer is SETTLED here, before anything is written -- a flag (--thinker), the answer ALREADY RECORDED in
#     <brain>/THINKER_RECORD (re-used, and proven again below, never re-asked silently), or the question at the terminal
#     (THE ASK HELPER, on EVERY interactive install and upgrade: the facts FOUND shown beside each letter, nothing
#     pre-selected, a blank or a pattern-shaped answer asked again, a follow-up for f; a letter this operating system
#     cannot take -- the catalog's `platforms`, e.g. Gemini on a Mac -- is refused with its reason and asked again);
#   * with no terminal, no flag, no record, and thinking timers armed or about to be (--with-crons), the run is REFUSED
#     rc 2 with the question named -- nothing touched. Past Claude usage (a token in the env file, armed timers) is shown
#     as EVIDENCE, never taken as the answer;
#   * the answer is PROVEN before Step A lays anything (thinker_prove): the matching Part of
#     docs/OVERNIGHT_THINKER_SETUP.md is shown, the installer waits for its one step, then organs/thinker.py `set` (the
#     package's own copy, Wave A's ask-and-probe entry point) proves it in the timers' environment -- systemd-run --user
#     with the env file THIS run will write -- and only then writes THINKER_RECORD. A failed proof lays nothing.
#   * e) is recorded as `off`: the organs keep their simple (heuristic) nightly paths and nothing claims to think.
#   * THE PROGRAM (Gemini-thinker Round 1, lane L-I; SYNTHESIS R4): for a CLI answer the program (claude, codex, kimi,
#     agy) is DETECTED while the answer is settled -- before the guide's Part is shown. When it is missing the vendor's
#     own command (organs/thinkers.json `get`, verified at execution) is shown verbatim and the owner is asked "Run it
#     now? [y/N]" (N at the question: pick another letter); no terminal needs --install-thinker-cli, else REFUSED rc 2
#     with the command in a `cause:` line. It RUNS in thinker_prove, before the Part (organs/thinker_get.py: never sudo,
#     never silent, never a second install -- a missing Node.js stops with a link), then the program is found again
#     through binary.candidates and handed to `thinker set --binary` by FULL path. Every later refusal names the program
#     it kept (NOTHING_TOUCHED / RECORDED_NOTE), never "Nothing was touched".
# ----------------------------------------------------------------------------
THINKER_PY="${PKG_ROOT}/organs/thinker.py"
THINKER_GET_PY="${PKG_ROOT}/organs/thinker_get.py"
THINKER_ANSWER=""; THINKER_HOW=""; THINKER_BY=""; THINKER_Q=""; THINKER_ENV_DIR=""; THINKER_PROVEN=0
THINKER_PROG=""; THINKER_PROG_CMD=""; THINKER_GET_YES=0; THINKER_GOT=""; THINKER_REFUSALS=""
REC_TH_BACKEND=""; REC_TH_MODEL=""; REC_TH_ENDPOINT=""; REC_TH_COMMAND=""; REC_TH_BY=""; REC_TH_AT=""; REC_TH_ERROR=""; REC_TH_AGENTIC=""
thinker_facts() {   # the question, the facts found and the recorded answer, as TAB-separated lines (never a credential)
    WIZARD_LIBRARY_DB="$DB_PATH" "$PY_SYS" -B "$THINKER_PY" _question --env-file "$ENV_FILE" 2>/dev/null \
        | "$PY_SYS" -B -c '
import json, sys
d = json.load(sys.stdin)
if d.get("catalog_error"):
    print("ERROR\t" + d["catalog_error"].replace("\t", " ")); sys.exit(0)
q = d["question"]
print("ASK\t" + q["ask"]); print("CHAT\t" + q["chat_note"]); print("AGENT\t" + q["agent_rule"])
print("NAMES\t" + q.get("names", ""))
for letter, note in sorted((q.get("letter_notes") or {}).items()):
    print("LNOTE\t%s\t%s" % (letter, note.replace("\t", " ")))
for letter, words, answer in q["options"]:
    print("OPT\t%s\t%s\t%s\t%s\t%s" % (letter, words, answer, "; ".join(d["facts"].get(letter) or []),
                                        d["guide_part"].get(letter, "")))
for e in d.get("evidence") or []:
    print("EVIDENCE\t" + e.replace("\t", " "))
for letter, sv in sorted((d.get("saved") or {}).items()):
    print("SAVED\t%s\t%s\t%s\t%s" % (letter, sv.get("line", ""), (sv.get("where") or "").replace("\t", " "),
                                     sv.get("fingerprint", "")))
r = d.get("record") or {}
for k in ("backend", "model", "endpoint", "command_template", "command_argv", "accepted_agentic_risk", "answered_by", "proven_at"):
    print("REC\t%s\t%s" % (k, (r.get(k) or "").replace("\t", " ")))
if d.get("record_error"):
    print("REC\terror\t" + d["record_error"].replace("\t", " "))
'
}
thinker_q() {   # thinker_q KIND [FIELD] -- one row of the facts (TAB-separated), read with awk as FIXED TEXT
    printf '%s\n' "$THINKER_Q" | awk -F'\t' -v k="$1" -v f="${2:-}" '$1 == k && (f == "" || $2 == f)'
}
thinker_letter_of() {   # thinker_letter_of ANSWER -- the letter (f for every local backend)
    case "$1" in claude) echo a ;; codex) echo b ;; kimi) echo c ;; gemini) echo d ;; off) echo e ;; local|local-*) echo f ;; esac
}
thinker_answer_of() {   # thinker_answer_of LETTER -- the answer `thinker set` takes (FIXED TEXT: the letter is the subject)
    case "$1" in a|A) echo claude ;; b|B) echo codex ;; c|C) echo kimi ;; d|D) echo gemini ;; e|E) echo off ;; f|F) echo local ;; esac
}
thinker_refusal_of() {  # thinker_refusal_of LETTER -- why this operating system cannot take it ('' = it can)
    printf '%s\n' "$THINKER_REFUSALS" | awk -F'\t' -v l="$1" '$1 == l { print $2; exit }'
}
thinker_words_of() { thinker_q OPT | awk -F'\t' -v l="$1" '$2 == l { print $3 }'; }
thinker_part_of() { thinker_q OPT | awk -F'\t' -v l="$1" '$2 == l { print $6 }'; }
thinker_saved() {   # thinker_saved LETTER line|where|fp -- the credential ALREADY saved for that letter ('' when none)
    thinker_q SAVED | awk -F'\t' -v l="$1" -v f="$2" '$2 == l { if (f == "line") print $3; else if (f == "where") print $4; else print $5 }'
}
thinker_render() {   # thinker_render PREFIX -- the question as the owner reads it: every letter, the facts found beside it
    local _p="$1"
    printf '%s\n' "${_p}$(thinker_q ASK | cut -f2)"
    thinker_q OPT | while IFS="$(printf '\t')" read -r _k _l _w _a _f _g; do
        printf '%s\n' "${_p}  ${_l}) ${_w}"
        [ -n "$_f" ] && printf '%s\n' "${_p}       found: ${_f}"
    done
    thinker_q EVIDENCE | while IFS="$(printf '\t')" read -r _k _e; do printf '%s\n' "${_p}  (found: ${_e})"; done
    printf '%s\n' "${_p}$(thinker_q CHAT | cut -f2)"
}
thinker_units_thinking() {   # the roster's thinking units: all (default) or the armed-by-default set (armed)
    "$PY_SYS" -B "${PKG_ROOT}/install/cron_roster.py" --roster "${PKG_ROOT}/install/cron_roster.json" thinks \
        --set "${1:-all}" 2>/dev/null | tr '\n' ' ' | sed 's/ $//'
}
thinker_armed_now() {   # thinking units whose timer is ALREADY on this host (a file read; the manager is not asked)
    local _u _out=""
    for _u in $(thinker_units_thinking all); do
        case "$OS" in
            Linux)  [ -e "${SYSTEMD_USER_DIR}/wizard-library-${_u}.timer" ] && _out="${_out:+$_out }${_u}" ;;
            Darwin) [ -e "${LAUNCHD_DIR}/${LAUNCHD_PREFIX}.${_u}.plist" ] && _out="${_out:+$_out }${_u}" ;;
        esac
    done
    printf '%s' "$_out"
}
thinker_check_letter() {   # ask_line's CHECK: FIXED TEXT (the answer is the case SUBJECT -- R-2)
    local _r
    case "$1" in
        a|b|c|d|e|f|A|B|C|D|E|F)
            _r="$(thinker_refusal_of "$(printf '%s' "$1" | tr 'A-F' 'a-f')")"
            [ -z "$_r" ] && return 0
            ask_say "  $_r"
            return 1 ;;
    esac
    ask_say "  '$1' is not one of the letters a, b, c, d, e or f."
    return 1
}
thinker_check_model() {
    case "$1" in *[[:space:]]*) ask_say "  A model name has no spaces in it (for example qwen2.5:7b)."; return 1 ;; esac
    return 0
}
thinker_check_how() {
    case "$1" in 1|2|3) return 0 ;; esac
    ask_say "  '$1' is not one of 1, 2 or 3."
    return 1
}
thinker_check_endpoint() {
    case "$1" in *[[:space:]]*) ;; http://?*|https://?*) return 0 ;; esac
    ask_say "  An address starts with http:// or https:// and has no spaces (for example http://127.0.0.1:11434)."
    return 1
}
thinker_check_command() {   # MC-R1-01: the seam's own check (organs/thinker.py _check_command), never a looser copy
    local _j _rc
    case "$1" in *"{prompt}"*) ;; *) ask_say "  The command must contain {prompt} (and usually {model}), for example: ollama run {model} {prompt}"; return 1 ;; esac
    _j="$("$PY_SYS" -B "$THINKER_PY" _check_command --command "$1" --model "${THINKER_MODEL:-}" 2>/dev/null)"; _rc=$?
    [ "$_rc" -eq 0 ] && return 0
    ask_say "  $(printf '%s' "$_j" | "$PY_SYS" -B -c 'import json,sys; print(json.loads(sys.stdin.read().strip().splitlines()[-1]).get("message", "refused"))' 2>/dev/null || echo "refused")"
    [ "$_rc" -eq 3 ] && ask_say "  The installer never accepts that risk for you: name a program whose tools are proven off (ollama run {model} {prompt}) or a server, or answer this later yourself with: voidloom thinker set local --command '<template>' --model <name> --accept-agentic"
    return 1
}
thinker_refuse_command() {   # the flag's d) command template, checked by the seam's own check BEFORE anything is written
    local _j _rc _m
    _j="$("$PY_SYS" -B "$THINKER_PY" _check_command --command "$THINKER_COMMAND" --model "${THINKER_MODEL:-}" 2>/dev/null)"; _rc=$?
    [ "$_rc" -eq 0 ] && return 0
    _m="$(printf '%s' "$_j" | "$PY_SYS" -B -c 'import json,sys; print(json.loads(sys.stdin.read().strip().splitlines()[-1]).get("message", "refused"))' 2>/dev/null || echo "refused")"
    echo "[install] --thinker-command is refused: ${_m}.$([ "$_rc" -eq 3 ] && printf ' The installer never accepts that risk for an owner: answer another way now, and if you (the owner) accept it, run voidloom thinker set local --command '"'"'<template>'"'"' --model <name> --accept-agentic yourself after the install.') ${NOTHING_TOUCHED}" >&2
    own_sidecar_restore
    exit 2
}
thinker_refused_no_answer() {   # thinker_refused_no_answer WHY -- rc 2, the question named word for word, nothing touched
    {
        echo "[install] The overnight-thinker question has no answer, and ${1}. Put this question to the owner, word for word:"
        thinker_render "[install]   "
    } >&2
    ask_refusal "Who thinks overnight is the owner's choice, and nothing records one yet (no ${DB_PATH%/*}/THINKER_RECORD${REC_TH_ERROR:+ that can be read: ${REC_TH_ERROR}}); this installer never picks it." "$(thinker_q NAMES | cut -f2)"
    echo "[install] For f) Other (--thinker local) also name the model and how it is reached: --thinker-model <name> with --thinker-endpoint <http://...> --thinker-api <ollama|openai>, or with --thinker-command '<template with {model} {prompt}>'." >&2
    echo "[install] ${NOTHING_TOUCHED}" >&2
    own_sidecar_restore
    exit 2
}
thinker_prog_field() {   # thinker_prog_field JSON FIELD -- one field of organs/thinker_get.py's JSON line ('' when absent)
    printf '%s' "$1" | "$PY_SYS" -B -c '
import json, sys
try:
    d = json.loads(sys.stdin.read().strip().splitlines()[-1])
except Exception:
    d = {}
v = d.get(sys.argv[1], "")
if isinstance(v, list):
    v = "; ".join("%s (%s)" % (x.get("name", ""), x.get("link", "")) if isinstance(x, dict) else str(x) for x in v)
print(str(v).replace("\n", " "))
' "$2" 2>/dev/null
}
thinker_program_settle() {   # thinker_program_settle ask|flag -- the answer's PROGRAM, before the guide's Part (R4)
    # rc 0: settled -- found (THINKER_PROG), or the owner's yes to the vendor's installer (THINKER_GET_YES; it runs in
    # thinker_prove), or a dry run that says what a wet run would do. rc 1 (at the question only): the owner said N, or
    # the installer needs a program this installer will not install -- the caller asks for another letter.
    # Every refusal here is rc 2 with the command in it, before anything is touched.
    THINKER_PROG=""; THINKER_PROG_CMD=""; THINKER_GET_YES=0
    case "$THINKER_ANSWER" in ""|off|local) return 0 ;; esac
    # r1 F4: Gemini's calls run ONLY inside bubblewrap (organs/thinker.py sandboxed_argv) -- checked by EXECUTION here,
    # before the program offer and the guide's Part, with the same sentence as thinker.py NO_SANDBOX_CAUSE; never sudo
    if [ "$THINKER_ANSWER" = gemini ] && [ "$(uname -s)" = Linux ] && ! bwrap --dev-bind / / -- /bin/true >/dev/null 2>&1; then
        _sb="Gemini needs the bubblewrap sandbox so the brain's calls can't see your own Gemini sign-in: install it with \`sudo apt install bubblewrap\` (or your distro's package), then run this again"
        if [ "$1" = ask ] && ask_tty; then ask_say "[install] ${_sb}."; return 1; fi
        if [ "$DRY_RUN" -eq 1 ]; then say "  DRY: ${_sb} (a wet run is REFUSED, rc 2, cause: no-sandbox)."; fi
        echo "[install] REFUSED: ${_sb}. ${NOTHING_TOUCHED}" >&2
        echo "  cause: no-sandbox" >&2
        own_sidecar_restore; exit 2
    fi
    local _j _l _w _short _needs _vendor _whose
    _j="$("$PY_SYS" -B "$THINKER_GET_PY" check "$THINKER_ANSWER" 2>/dev/null)"
    _l="$(thinker_letter_of "$THINKER_ANSWER")"; _w="$(thinker_words_of "$_l")"; _short="${_w%% (*}"
    if [ -n "$(thinker_prog_field "$_j" error)" ] || [ -z "$_j" ]; then
        echo "[install] REFUSED: ${_l}) ${_w}: the package's thinker catalog cannot say how to find its program ($(thinker_prog_field "$_j" error)). The package is incomplete or damaged -- re-download it. ${NOTHING_TOUCHED}" >&2
        own_sidecar_restore; exit 2
    fi
    THINKER_PROG="$(thinker_prog_field "$_j" found)"
    if [ -n "$THINKER_PROG" ]; then
        say "  ${_short}'s program: found at ${THINKER_PROG}"
        return 0
    fi
    THINKER_PROG_CMD="$(thinker_prog_field "$_j" command)"
    _needs="$(thinker_prog_field "$_j" needs_missing)"; _vendor="$(thinker_prog_field "$_j" vendor)"
    _whose="its"; [ -n "$_vendor" ] && _whose="${_vendor}'s"
    if [ -z "$THINKER_PROG_CMD" ]; then
        echo "[install] REFUSED: ${_l}) ${_w} needs its program, which is not on this computer, and the package names no installer for this operating system. Install it yourself, then re-run. ${NOTHING_TOUCHED}" >&2
        echo "  cause: not-installed" >&2
        own_sidecar_restore; exit 2
    fi
    if ask_tty; then
        ask_say ""
        "$PY_SYS" -B "$THINKER_GET_PY" screen "$THINKER_ANSWER" --words "$_short" 2>/dev/null | while IFS= read -r _x; do ask_say "[install] $_x"; done
        if [ -n "$_needs" ]; then
            [ "$1" = ask ] && return 1
            echo "[install] REFUSED: ${_l}) ${_w}: its installer needs ${_needs}, which this installer never installs for you. Install that, then re-run. ${NOTHING_TOUCHED}" >&2
            echo "  cause: not-installed" >&2
            own_sidecar_restore; exit 2
        fi
        if [ "$INSTALL_THINKER_CLI" -eq 1 ]; then
            ask_say "[install] --install-thinker-cli: your yes is given; it runs before the sign-in step."
            THINKER_GET_YES=1; return 0
        fi
        ask_yes_no "Run it now? [y/N]   (N: pick another letter, or install it yourself and re-run)"
        case $? in
            0) THINKER_GET_YES=1; return 0 ;;
            3) echo "[install] REFUSED: the overnight-thinker question got no answer (end of input). ${NOTHING_TOUCHED}" >&2; own_sidecar_restore; exit 2 ;;
        esac
        [ "$1" = ask ] && return 1
        echo "[install] REFUSED: ${_l}) ${_w} needs ${_whose} program, which is not on this computer, and your answer was N. Install it yourself (the vendor's own command: ${THINKER_PROG_CMD}), or answer another way, then re-run. ${NOTHING_TOUCHED}" >&2
        echo "  cause: not-installed" >&2
        own_sidecar_restore; exit 2
    fi
    if [ -n "$_needs" ] && [ "$DRY_RUN" -eq 1 ]; then
        say "  DRY: ${_short}'s program is not installed, and its installer needs ${_needs}, which this installer never installs: a wet run is REFUSED (rc 2) until that is installed."
        return 0
    fi
    if [ -n "$_needs" ]; then
        echo "[install] REFUSED: ${_l}) ${_w} needs its program, which is not on this computer, and its installer needs ${_needs}, which this installer never installs for you. Install that, then re-run. ${NOTHING_TOUCHED}" >&2
        echo "  cause: not-installed" >&2
        own_sidecar_restore; exit 2
    fi
    [ "$INSTALL_THINKER_CLI" -eq 1 ] && { THINKER_GET_YES=1; say "  ${_short}'s program: not installed -- --install-thinker-cli: the vendor's own installer runs before anything is laid: ${THINKER_PROG_CMD}"; return 0; }
    if [ "$DRY_RUN" -eq 1 ]; then
        say "  DRY: ${_short}'s program is not installed: a wet run with no terminal is REFUSED (rc 2) unless --install-thinker-cli is passed (the owner's yes to running: ${THINKER_PROG_CMD})."
        return 0
    fi
    {
        echo "[install] REFUSED: ${_l}) ${_w} needs ${_whose} program, which is not on this computer, and no terminal is reachable to ask \"Run it now? [y/N]\". The vendor's own installer is:"
        echo "    ${THINKER_PROG_CMD}"
        echo "[install] Pass --install-thinker-cli to have this installer run it (an installing AI agent: only on the human's yes to \"may I run ${_whose} installer from the internet?\"), or install it yourself, then re-run. ${NOTHING_TOUCHED}"
        echo "  cause: not-installed"
    } >&2
    own_sidecar_restore; exit 2
}
thinker_ask() {   # the question at the terminal (THE ASK HELPER)
    local _l _rc _how
    ask_say ""
    ask_say "[install] One question about the overnight thinking (nothing is chosen for you; the facts found are shown beside each letter):"
    thinker_render "[install] " >/dev/tty
    ask_say "[install] $(thinker_q AGENT | cut -f2)"
    while :; do
        ask_line _l "Your answer (a, b, c, d, e or f):" thinker_check_letter; _rc=$?
        [ "$_rc" -eq 0 ] || { echo "[install] REFUSED: the overnight-thinker question got no answer (end of input). ${NOTHING_TOUCHED}" >&2; own_sidecar_restore; exit 2; }
        THINKER_ANSWER="$(thinker_answer_of "$_l")"
        # the program, BEFORE anything else is asked or shown: present, or the owner's y to the vendor's installer;
        # N (or a need this installer will not install) goes back to the letter
        thinker_program_settle ask && break
        ask_say "[install] Pick another letter (or install it yourself and re-run)."
    done
    if [ "$THINKER_ANSWER" = "local" ]; then
        ask_line THINKER_MODEL "f) Other -- which model? Type its name as its server or tool knows it (for example qwen2.5:7b):" thinker_check_model; _rc=$?
        [ "$_rc" -eq 0 ] || { echo "[install] REFUSED: the overnight-thinker question got no answer (end of input). ${NOTHING_TOUCHED}" >&2; own_sidecar_restore; exit 2; }
        ask_say "  How does the brain reach ${THINKER_MODEL}?"
        ask_say "    1) an ollama server (an address like http://127.0.0.1:11434)"
        ask_say "    2) an OpenAI-compatible server (an address like http://127.0.0.1:8000)"
        ask_say "    3) a command the brain runs, with {model} and {prompt} in it (for example: ollama run {model} {prompt})"
        ask_line _how "Answer 1, 2 or 3:" thinker_check_how; _rc=$?
        [ "$_rc" -eq 0 ] || { echo "[install] REFUSED: the overnight-thinker question got no answer (end of input). ${NOTHING_TOUCHED}" >&2; own_sidecar_restore; exit 2; }
        if [ "$_how" = "3" ]; then
            ask_line THINKER_COMMAND "The command, with {prompt} in it:" thinker_check_command; _rc=$?
        else
            THINKER_API="ollama"; [ "$_how" = "2" ] && THINKER_API="openai"
            ask_line THINKER_ENDPOINT "The server's address (http://... or https://...):" thinker_check_endpoint; _rc=$?
        fi
        [ "$_rc" -eq 0 ] || { echo "[install] REFUSED: the overnight-thinker question got no answer (end of input). ${NOTHING_TOUCHED}" >&2; own_sidecar_restore; exit 2; }
    fi
    THINKER_HOW="question"; THINKER_BY="owner"
    ask_say "  Your answer: $(thinker_letter_of "$THINKER_ANSWER")) $(thinker_words_of "$(thinker_letter_of "$THINKER_ANSWER")")${THINKER_MODEL:+ -- ${THINKER_MODEL}}. It is proven before anything is laid, then recorded."
}
thinker_settle() {   # the answer, settled BEFORE anything is written (no write happens in here)
    [ "$ENV_ONLY" -eq 1 ] && return 0          # --env-only lays no organ and arms no unit
    THINKER_Q="$(thinker_facts)"
    if [ -z "$THINKER_Q" ] || [ -n "$(thinker_q ERROR)" ]; then
        echo "[install] REFUSED: the overnight-thinker catalog (${PKG_ROOT}/organs/thinkers.json) cannot be read: $(thinker_q ERROR | cut -f2)${THINKER_Q:+}. The package is incomplete or damaged -- re-download it. ${NOTHING_TOUCHED}" >&2
        own_sidecar_restore; exit 2
    fi
    REC_TH_BACKEND="$(thinker_q REC backend | cut -f3)"; REC_TH_MODEL="$(thinker_q REC model | cut -f3)"
    REC_TH_ENDPOINT="$(thinker_q REC endpoint | cut -f3)"; REC_TH_COMMAND="$(thinker_q REC command_argv | cut -f3)"
    [ -n "$REC_TH_COMMAND" ] || REC_TH_COMMAND="$(thinker_q REC command_template | cut -f3)"
    REC_TH_AGENTIC="$(thinker_q REC accepted_agentic_risk | cut -f3)"
    REC_TH_BY="$(thinker_q REC answered_by | cut -f3)"; REC_TH_AT="$(thinker_q REC proven_at | cut -f3)"
    REC_TH_ERROR="$(thinker_q REC error | cut -f3)"
    THINKER_REFUSALS="$("$PY_SYS" -B "$THINKER_GET_PY" refusals 2>/dev/null)"
    if [ -n "$THINKER_FLAG" ]; then
        THINKER_ANSWER="$THINKER_FLAG"; THINKER_HOW="flag"; THINKER_BY="owner-flag"
        # one release: a letter whose meaning changed on 2026-10-16 says so (the catalog's words)
        [ -n "$THINKER_LETTER_NOTE" ] && echo "[install] $(thinker_q LNOTE "$THINKER_LETTER_NOTE" | cut -f3)" >&2
        if [ -n "$(thinker_refusal_of "$(thinker_letter_of "$THINKER_ANSWER")")" ]; then
            echo "[install] REFUSED: --thinker ${THINKER_ANSWER}: $(thinker_refusal_of "$(thinker_letter_of "$THINKER_ANSWER")") ${NOTHING_TOUCHED}" >&2
            own_sidecar_restore; exit 2
        fi
        [ -n "$THINKER_COMMAND" ] && thinker_refuse_command
        say "Thinker:      --thinker ${THINKER_ANSWER} -- $(thinker_letter_of "$THINKER_ANSWER")) $(thinker_words_of "$(thinker_letter_of "$THINKER_ANSWER")")${THINKER_MODEL:+ (${THINKER_MODEL})}$([ -n "$REC_TH_BACKEND" ] && [ "$(thinker_letter_of "$REC_TH_BACKEND")" != "$(thinker_letter_of "$THINKER_ANSWER")" ] && printf ' (replaces the recorded %s)' "$REC_TH_BACKEND"); proven before anything is laid"
        thinker_program_settle flag
    elif [ -n "$REC_TH_BACKEND" ]; then
        THINKER_ANSWER="$REC_TH_BACKEND"; THINKER_HOW="record"; THINKER_BY="${REC_TH_BY:-owner}"
        case "$REC_TH_BACKEND" in
            local-http-ollama) THINKER_ANSWER=local; THINKER_API=ollama; THINKER_ENDPOINT="$REC_TH_ENDPOINT" ;;
            local-http-openai) THINKER_ANSWER=local; THINKER_API=openai; THINKER_ENDPOINT="$REC_TH_ENDPOINT" ;;
            local-command)     THINKER_ANSWER=local; THINKER_COMMAND="$REC_TH_COMMAND" ;;
        esac
        THINKER_MODEL="$REC_TH_MODEL"
        say "Thinker:      the answer recorded in ${DB_PATH%/*}/THINKER_RECORD is re-used, never re-asked: $(thinker_letter_of "$REC_TH_BACKEND")) $(thinker_words_of "$(thinker_letter_of "$REC_TH_BACKEND")") (${REC_TH_BACKEND}, proven ${REC_TH_AT:-?})$([ "$REC_TH_BACKEND" != off ] && echo " -- proven again before anything is laid")"
    elif ask_tty; then
        [ -n "$REC_TH_ERROR" ] && ask_say "[install] ${DB_PATH%/*}/THINKER_RECORD cannot be read (${REC_TH_ERROR}): it answers nothing, so the question is asked."
        thinker_ask
    else
        local _armed _arming=""
        _armed="$(thinker_armed_now)"
        [ "$WITH_CRONS" -eq 1 ] && _arming="$(thinker_units_thinking armed)"
        if [ -n "$_arming" ]; then
            thinker_refused_no_answer "this run arms timers that think (--with-crons: ${_arming})"
        elif [ -n "$_armed" ]; then
            thinker_refused_no_answer "this host's timers already think (${_armed} -- the upgraded organs read the answer from THINKER_RECORD)"
        fi
        say "Thinker:      not asked (no terminal) and no timer that thinks is armed: nothing thinks overnight until the owner answers -- \`voidloom thinker set <claude|codex|kimi|gemini|local|off>\` or a re-run in a terminal."
    fi
}
thinker_show_part() {   # thinker_show_part LETTER -- the matching Part of the guide, word for word, from the package
    local _g="${PKG_ROOT}/docs/OVERNIGHT_THINKER_SETUP.md" _part
    _part="$(thinker_part_of "$1")"
    [ -n "$_part" ] && [ -f "$_g" ] || { ask_say "  (docs/OVERNIGHT_THINKER_SETUP.md is not in this package: nothing to show.)"; return 0; }
    ask_say ""
    ask_say "[install] ----- docs/OVERNIGHT_THINKER_SETUP.md, Part ${_part} (the one step for $1) $(printf '%.0s-' 1 2 3 4 5)"
    # Round 3 G1, item 3: when the Part's one step is ALREADY done, say so FIRST -- the owner must not make a new
    # credential to replace a valid one (memory #23617)
    [ -n "$(thinker_saved "$1" line)" ] && ask_say "[install] >>> $(thinker_saved "$1" line) -- nothing to do in this Part (the one already saved: $(thinker_saved "$1" where), fingerprint $(thinker_saved "$1" fp))."
    awk -v p="$_part" 'BEGIN { hit = 0 } /^## / { if (hit) exit; if (index($0, "Part " p ":") > 0) hit = 1 } hit' "$_g" >/dev/tty
    ask_say "[install] ----- end of Part ${_part} -----"
}
thinker_mkbrain() {   # the brain directory the record lives in and the probe runs from; a failed proof removes what it made
    local _d="$1"
    THINKER_MADE=""
    while [ ! -d "$_d" ]; do THINKER_MADE="${THINKER_MADE:+$THINKER_MADE
}${_d}"; _d="$(dirname "$_d")"; done      # deepest first
    [ -n "$THINKER_MADE" ] && mkdir -p "$1"
    return 0
}
thinker_unmake() {   # rmdir (never rm -r) exactly the directories thinker_mkbrain made, deepest first, if still empty
    [ -n "${THINKER_MADE:-}" ] || return 0
    printf '%s\n' "$THINKER_MADE" | while IFS= read -r _d; do [ -n "$_d" ] && rmdir "$_d" 2>/dev/null; done
    THINKER_MADE=""
}
thinker_prove() {   # BEFORE Step A: the Part, the one step, then `thinker set` proves in the timers' environment, then records
    [ -n "$THINKER_ANSWER" ] || return 0
    local _l _w _pe _out _rc _bd="${DB_PATH%/*}"
    _l="$(thinker_letter_of "$THINKER_ANSWER")"; _w="$(thinker_words_of "$_l")"
    step "THE OVERNIGHT THINKER -- ${_l}) ${_w}: proven in the timers' environment before anything is laid"
    if [ "$DRY_RUN" -eq 1 ]; then
        [ "$THINKER_ANSWER" = off ] && { say "  DRY: would record e) not now in ${_bd}/THINKER_RECORD (nothing to prove; nothing thinks overnight)."; return 0; }
        [ -n "$THINKER_PROG_CMD" ] && [ "$THINKER_GET_YES" -eq 1 ] && say "  DRY: the program is not installed: would run the vendor's own installer (your yes) -- ${THINKER_PROG_CMD} -- then find the program again by full path"
        say "  DRY: would show docs/OVERNIGHT_THINKER_SETUP.md Part $(thinker_part_of "$_l") and wait for its one step (at a terminal), then run"
        say "  DRY: ${THINKER_PY} set ${THINKER_ANSWER}${THINKER_MODEL:+ --model ${THINKER_MODEL}} --source installer (the proof: an answer call -- the exact answer to 'What is N plus 1?' -- and a tool-block call, through systemd-run --user with the env file this run writes), and record ${_bd}/THINKER_RECORD only if it passes."
        return 0
    fi
    if [ "$THINKER_ANSWER" = off ] && [ "$THINKER_HOW" = record ]; then
        say "  e) not now is recorded (${REC_TH_AT:-?}): kept as it is -- there is no model to prove, and nothing thinks overnight."
        return 0
    fi
    # THE PROGRAM (R4): the owner's yes, settled above, runs the vendor's own installer NOW -- before the guide's Part,
    # before the brain directory is made -- and the program is found again (PATH, then binary.candidates) by full path
    if [ -z "$THINKER_PROG" ] && [ -n "$THINKER_PROG_CMD" ] && [ "$THINKER_GET_YES" -eq 1 ]; then
        local _gj _grc
        say "  the program is not installed: running the vendor's own installer, as you said yes to: ${THINKER_PROG_CMD}"
        _gj="$("$PY_SYS" -B "$THINKER_GET_PY" run "$THINKER_ANSWER")"; _grc=$?
        THINKER_PROG="$(thinker_prog_field "$_gj" found)"
        if [ "$_grc" -ne 0 ] || [ -z "$THINKER_PROG" ]; then
            echo "[install] REFUSED: the vendor's installer ran (your yes) but the ${_w%% (*} program is still not found ($(thinker_prog_field "$_gj" note)). Nothing was recorded and nothing was laid; whatever that installer wrote is its own (its output is above). Install it yourself, then re-run.${RECORDED_NOTE:+ $RECORDED_NOTE}" >&2
            echo "  cause: not-installed" >&2
            own_sidecar_restore; exit 2
        fi
        THINKER_GOT="$THINKER_PROG"
        say "  installed: ${THINKER_GOT} -- used by full path from here on"
        NOTHING_TOUCHED="Nothing else was touched: the one change this run made is the ${_w%% (*} program it installed at your yes (${THINKER_GOT}), which is kept -- remove it yourself if you do not want it."
        RECORDED_NOTE="${RECORDED_NOTE:+$RECORDED_NOTE }(The ${_w%% (*} program WAS installed at your yes, at ${THINKER_GOT}, and is kept.)"
    fi
    thinker_mkbrain "$_bd"
    if [ "$THINKER_ANSWER" != off ] && [ "$THINKER_HOW" != record ] && ask_tty; then
        # the backend's own words before its sign-in (KAEL-EM screen 1; the owner's ruling 2026-10-16: the sign-in itself
        # is `voidloom thinker login`, which `thinker set` starts below -- this installer builds no sign-in of its own)
        if [ -z "$(thinker_saved "$_l" line)" ]; then
            "$PY_SYS" -B "$THINKER_GET_PY" signin "$THINKER_ANSWER" --program "$THINKER_PROG" 2>/dev/null | while IFS= read -r _x; do ask_say "[install] $_x"; done
        fi
        thinker_show_part "$_l"
        if [ -n "$(thinker_saved "$_l" line)" ]; then
            ask_say "[install] $(thinker_saved "$_l" line). (To replace it instead: Ctrl-C now, then run \`voidloom thinker login ${THINKER_ANSWER}\`.) Ctrl-C stops it; nothing has been laid."
        else
            ask_say "[install] Do the one step above (the key, the sign-in or the running model), then press Enter here. The installer then asks for the key twice (a, c) or starts the sign-in (b, d) itself, and proves it. Ctrl-C stops it; nothing has been laid."
        fi
        printf '%s ' "  Press Enter when ready:" >/dev/tty
        IFS= read -r _rc </dev/tty || { thinker_unmake; echo "[install] REFUSED: the overnight-thinker step got no answer (end of input). ${NOTHING_TOUCHED}" >&2; own_sidecar_restore; exit 2; }
    fi
    # THE TIMERS' ENVIRONMENT IS THE ENV FILE THIS RUN WILL WRITE: the same block and the same one writer (splice) Step D
    # runs, over the file as it stands now -- a verified image in a temporary directory, removed after the proof
    THINKER_ENV_DIR="$(mktemp -d 2>/dev/null)" || die "could not create a temporary directory for the thinker's proof."
    build_env_block > "${THINKER_ENV_DIR}/block"
    "$PY_SYS" -B "$ENV_READER" --file "$ENV_VIEW" --begin "$ENV_BEGIN" --end "$ENV_END" --organs "${PKG_ROOT}/organs" \
        splice "${THINKER_ENV_DIR}/block" "${THINKER_ENV_DIR}/wizard-library-env" >/dev/null \
        || die "the env file this run will write could not be rehearsed for the thinker's proof (install/env_file.py splice refused it); nothing was laid."
    _pe=direct; [ "$OS" = "Linux" ] && command -v systemd-run >/dev/null 2>&1 && _pe=systemd
    set -- set "$THINKER_ANSWER" --source installer --answered-by "$THINKER_BY" --probe-env "$_pe" --timeout 240
    [ -n "$THINKER_MODEL" ] && set -- "$@" --model "$THINKER_MODEL"
    [ -n "$THINKER_ENDPOINT" ] && set -- "$@" --endpoint "$THINKER_ENDPOINT" --api "$THINKER_API"
    [ -n "$THINKER_COMMAND" ] && set -- "$@" --command "$THINKER_COMMAND"
    # a program this run installed is called by FULL path: a fresh shell (and the timers) may lack its folder on PATH
    [ -n "$THINKER_GOT" ] && set -- "$@" --binary "$THINKER_GOT"
    # the OWNER's recorded --accept-agentic is re-used on a re-proof of the recorded answer (never granted here)
    [ "$THINKER_HOW" = record ] && [ "$REC_TH_AGENTIC" = true ] && set -- "$@" --accept-agentic
    set -- "$@" --env-file "${THINKER_ENV_DIR}/wizard-library-env"
    ask_tty || set -- "$@" --no-login
    # Round 3 G1: say how long the wait can be BEFORE it starts -- a stranger who sees nothing for minutes presses Ctrl-C
    say "  proving it now: usually well under a minute; if the model's service stalls, the tool check is asked again (up to 3 times in 4 minutes), so the whole proof can take up to about 8 minutes. Nothing is laid until it ends."
    _out="$(WIZARD_LIBRARY_DB="$DB_PATH" WIZARD_ENV_FILE="$ENV_FILE" "$PY_SYS" -B "$THINKER_PY" "$@")"; _rc=$?
    printf '%s\n' "$_out" | while IFS= read -r _x; do say "  $_x"; done
    rm -rf "$THINKER_ENV_DIR"; THINKER_ENV_DIR=""
    # Round 3 G1, item 1c: the seam names ONE cause (`  cause: <c>`) and its next step; only a CREDENTIAL cause sends the
    # owner to the guide's Part -- a stall, a quota or the network says nothing against a valid credential (#23617)
    local _cause _fix
    _cause="$(printf '%s\n' "$_out" | awk '/^  cause: / { print $2; exit }')"
    case "$_cause" in
        auth|no-credential) _fix="Fix the credential with docs/OVERNIGHT_THINKER_SETUP.md Part $(thinker_part_of "$_l")" ;;
        "")                 _fix="The thinker's own words above say what failed" ;;
        *)                  _fix="The cause (${_cause}) and its next step are named above -- nothing in docs/OVERNIGHT_THINKER_SETUP.md needs redoing" ;;
    esac
    if [ "$_rc" -ne 0 ] && [ "$THINKER_HOW" = record ]; then
        # MC-R1-07 (Round 1 iteration 2): a RECORDED answer that no longer proves is REPORTED, never a reason to refuse
        # an upgrade -- refusing protected nothing (the armed timers keep calling the recorded backend either way) and
        # left e) not now as the only way to lay unrelated fixes. A NEW answer that does not prove still refuses (below).
        warn "the overnight thinker this brain records (${_l}) ${_w}, ${_bd}/THINKER_RECORD) did NOT prove again (organs/thinker.py set rc ${_rc}, above). The record is kept EXACTLY as it was and this upgrade continues; each night that cannot reach it is logged and counted by \`voidloom doctor\`. ${_fix}; then run \`voidloom thinker set ${THINKER_ANSWER}\`."
        THINKER_MADE=""
        return 0
    fi
    if [ "$_rc" -ne 0 ]; then
        thinker_unmake
        echo "[install] REFUSED: the overnight thinker ${_l}) ${_w} was NOT proven (${_cause:+cause: ${_cause}; }organs/thinker.py set rc ${_rc}, above), so nothing was recorded and nothing was laid: ${_bd}/THINKER_RECORD is exactly as it was. ${_fix}; then re-run -- or answer e) not now with --thinker off.${RECORDED_NOTE:+ $RECORDED_NOTE}" >&2
        own_sidecar_restore
        [ "$_rc" -eq 1 ] && exit 1
        exit 2
    fi
    THINKER_PROVEN=1; THINKER_MADE=""
    RECORDED_NOTE="${RECORDED_NOTE:+$RECORDED_NOTE }(The overnight thinker WAS proven and recorded in ${_bd}/THINKER_RECORD: ${_l}) ${_w}.)"
}
thinker_settle

# ----------------------------------------------------------------------------
# APPROVALS -- the owner's ruling, 2026-10-26: "default to auto approvals ... turn this in the install to default on,
# with the option to turn it to manual approval as a step during installation." Once a month the self-eval proposes
# changes; in auto mode the brain approves its own proposals (signed policy:auto, said at the next session start), in
# manual mode they wait for a person. Belief links and governance holds are NOT covered: they always wait for a person.
#
# THE ONE DELIBERATE EXCEPTION to "nothing is chosen for you" in this file, because the owner ruled a default:
#   --approvals auto|manual       the answer, where no terminal can ask it
#   a choice already recorded     re-used, never re-asked
#   a terminal                    asked; a blank line is a) automatic
#   no terminal, a NEW install    automatic (the default), and the change command is printed
#   no terminal, an UPGRADE with no recorded choice   left as it is (a person decides): a missing setting on an existing
#                                 brain is not consent to change how it decides; the command to switch is printed
# Settled here, before anything is written; recorded after the self-check gate passes (approvals_record).
# ----------------------------------------------------------------------------
APPROVALS_ANSWER=""; APPROVALS_HOW=""
approvals_settle() {
    local _rec _raw _a
    [ "$ENV_ONLY" -eq 1 ] && return 0          # --env-only lays no organ and records no choice
    if [ -n "$APPROVALS_FLAG" ]; then APPROVALS_ANSWER="$APPROVALS_FLAG"; APPROVALS_HOW="flag"; return 0; fi
    _rec="$(WIZARD_CONFIG_PATH="$WIZARD_CONFIG_JSON" "$PY_SYS" -B "${PKG_ROOT}/organs/approvals_policy.py" show --json 2>/dev/null \
            | "$PY_SYS" -B -c 'import json,sys; d=json.load(sys.stdin); print(d["mode"] if d.get("recorded") else "")' 2>/dev/null)"
    if [ -n "$_rec" ]; then APPROVALS_ANSWER="$_rec"; APPROVALS_HOW="record"; return 0; fi
    if ask_tty; then
        ask_say ""
        ask_say "[install] Approvals. Once a month the brain reviews its own health and proposes improvements."
        ask_say "          a) Automatic (recommended) -- the brain approves its own monthly proposals and names what it"
        ask_say "             decided at the start of each session for the next 7 days. Change it any time: voidloom approvals --mode manual"
        ask_say "          b) Manual -- each proposal waits for you in: voidloom approvals --tui"
        ask_say "          (Belief links and governance holds are not affected by this: they always wait for a person.)"
        [ "$PRIOR_INSTALL" -eq 1 ] && ask_say "          On this brain today: no choice is recorded, so each proposal waits for you. A blank line switches it to a)."
        while :; do
            printf '%s ' "Your answer, a or b (a blank line is a):" >/dev/tty
            if ! IFS= read -r _raw </dev/tty; then
                [ -n "$_raw" ] || { printf '\n' >/dev/tty; break; }
            fi
            _a="$(_ask_trim "$_raw")"
            case "$_a" in
                ""|a|A|auto|Auto|AUTO) APPROVALS_ANSWER="auto"; APPROVALS_HOW="question"; return 0 ;;
                b|B|manual|Manual|MANUAL) APPROVALS_ANSWER="manual"; APPROVALS_HOW="question"; return 0 ;;
            esac
            ask_say "  '$_a' is not a or b (a blank line is a)."
        done
    fi
    if [ "$PRIOR_INSTALL" -eq 1 ]; then APPROVALS_ANSWER=""; APPROVALS_HOW="kept"; return 0; fi
    APPROVALS_ANSWER="auto"; APPROVALS_HOW="default"
}
approvals_settle

GPU_DEVICE=""; _gpu_resolved=""
# #8 (stranger test, 2026-10-13): --force-cpu decides THIS run's device; it does not answer which card the brain's
# background jobs use. It used to discard the owner's --gpu / --display-gpu ("not used, nothing is recorded") and Step D
# then warned "else card 0" on the same 2-GPU host. Given as flags, the answers are recorded as on any other run; with
# no flags, --force-cpu asks nothing (the run itself touches no card).
if { gpu_present || { [ "$FORCE_CPU" -eq 1 ] && [ -n "$GPU_ANSWER$DISPLAY_ANSWER" ] && gpu_listed; }; } \
   && [ "$(gpu_count)" -ge 2 ] && ! env_outside_has CUDA_VISIBLE_DEVICES && ! env_outside_has WIZARD_CUDA_DEVICE; then
    gpu_choice
fi
gpu_flags_unused
if gpu_present; then
    if _gpu_resolved="$(resolve_gpu_device)"; then
        GPU_DEVICE="${_gpu_resolved%%|*}"
        case "$_gpu_resolved" in *"-- NO GPU:"*)
            [ "$SHAPE" = "ambient" ] && refuse_shape "${ENV_FILE} declares WIZARD_CUDA_DEVICE=$(env_outside get WIZARD_CUDA_DEVICE) (NO GPU) outside the managed block; the ambient shape PROVES the units' card (P4) and has no CPU fallback. Put a card number on that line, or upgrade without --tier1-ambient." ;;
        esac
        export CUDA_VISIBLE_DEVICES="$GPU_DEVICE"
        if [ "$(gpu_count)" -gt 1 ]; then
            say "GPU device:   ${GPU_DEVICE} -- ${_gpu_resolved#*|}"
            # EW5-06: a DECLARED index is checked against the cards nvidia-smi lists (a hand WIZARD_CUDA_DEVICE=5 on a
            # 2-card host was honoured in silence); it stays the owner's word -- the run says so, it does not rewrite it
            case "$GPU_DEVICE" in ''|*[!0-9]*) ;; *)
                gpu_cards | cut -d'|' -f1 | grep -qxF -- "$GPU_DEVICE" \
                    || warn "card ${GPU_DEVICE} (${_gpu_resolved#*|}) is not one nvidia-smi lists ($(gpu_cards | cut -d'|' -f1 | tr '\n' ' ' | sed 's/ $//')): the jobs will find no device there. Edit that line in ${ENV_FILE}." ;;
            esac
        elif [ "$("$NVSMI" --query-gpu=index,display_active --format=csv,noheader 2>/dev/null | awk -F', *' 'NR == 1 { print $2 }')" = "Enabled" ]; then
            say "GPU device:   0 -- the only GPU, and it drives a display: the brain's background jobs share it with your desktop."
        fi
    else
        GPU_DEVICE=""
        # reachable only under the owner's `WIZARD_CUDA_DEVICE=` ("do not pin the units") on a host with two or more GPUs
        # where no card reports display_active=Enabled: the units stay unpinned (his word), and THIS run has no card it can
        # tell apart for its own model loads -- it never guesses one by position (reflector-4).
        # ruler VL-REF-v1.2: in the ambient shape there is no CPU fallback, and P4 proves a pin the owner's line withholds
        [ "$SHAPE" = "ambient" ] && refuse_shape "this host has $(gpu_count) GPUs, and ${ENV_FILE} declares WIZARD_CUDA_DEVICE= EMPTY ('do not pin the units') outside the managed block; the ambient shape PROVES the units' card (P4) and has no CPU fallback. Put the card number on that line (WIZARD_CUDA_DEVICE=<index>), then re-run."
        echo "[install] REFUSED: ${ENV_FILE} declares WIZARD_CUDA_DEVICE= EMPTY ('do not pin the units'), and this run still needs one card for its own model loads: with $(gpu_count) GPUs and none reporting display_active=Enabled (a monitor asleep reads that way), no card can be told apart as the one that drives no display, and this installer never guesses one. Wake the monitor and re-run, or re-run with --force-cpu (the CPU tier for this run's own steps), or put a card number on that line. ${NOTHING_TOUCHED}" >&2
        own_sidecar_restore
        exit 2
    fi
    # THE RUN'S CHILDREN READ THE PIN VARIABLES THE UNITS WILL READ -- never the invoking shell's (G1, EW4-10). systemd
    # hands every unit the file's WIZARD_CUDA_DEVICE (voidloom_device.declared_device() reads it FIRST: the machine
    # manifest's compute_index, P4's derived manifest), so this run's own P4, Step A.1 and self-check see exactly that --
    # measured on the iteration-4 bytes: a shell's WIZARD_CUDA_DEVICE=0 outranked the file's declaration of 1 for this
    # run's own model loads, with no note.
    if env_outside_has WIZARD_CUDA_DEVICE; then export WIZARD_CUDA_DEVICE="$(env_outside get WIZARD_CUDA_DEVICE)"; else unset WIZARD_CUDA_DEVICE; fi
    gpu_shell_note
fi
# ONE PIN (reference-host round, iteration 2, item 5). In the ambient shape the card P4 proves
# is the one the UNITS will read from the env file (effective_pin), and every later step of
# THIS run -- the proofs, the migrations' P3, Step E's self-check -- must use that same card.
# Measured on iteration 1: with the owner's CUDA_VISIBLE_DEVICES=1 outside the block and the
# shell pinning nothing, the installer exported card 0 (its heuristic) for itself while P4
# proved card 1, and Step E loaded an embedder on the unproven card. Now the installer's own
# device IS the units' pin, and assert_one_pin re-checks it before each step that loads a model.
AMBIENT_PIN=""; AMBIENT_PIN_KEY=""; AMBIENT_PIN_SOURCE=""
if [ "$SHAPE" = "ambient" ] && [ "$FORCE_CPU" -ne 1 ]; then
    AMBIENT_PIN="$(effective_pin)"
    AMBIENT_PIN_KEY="$(effective_pin_key)"
    if [ -n "$AMBIENT_PIN_KEY" ]; then
        AMBIENT_PIN_SOURCE="$(decl_source "$AMBIENT_PIN_KEY")"
    else
        AMBIENT_PIN_SOURCE="${_gpu_resolved#*|}"; AMBIENT_PIN_SOURCE="${AMBIENT_PIN_SOURCE:-no pin resolved}"
    fi
    if [ -n "$AMBIENT_PIN" ]; then
        if [ -n "$GPU_DEVICE" ] && [ "$GPU_DEVICE" != "$AMBIENT_PIN" ]; then
            warn "this run had resolved GPU ${GPU_DEVICE} for its own steps (${_gpu_resolved#*|}), but the units will read ${AMBIENT_PIN} from ${ENV_FILE}: ONE pin -- every step of this run uses ${AMBIENT_PIN}, the card P4 proves."
        fi
        GPU_DEVICE="$AMBIENT_PIN"
        export CUDA_VISIBLE_DEVICES="$AMBIENT_PIN"
    fi
fi
assert_one_pin() {   # assert_one_pin WHERE -- the installer's device == the units' pin == the card P4 proved
    [ "$SHAPE" = "ambient" ] && [ -n "$AMBIENT_PIN" ] || return 0
    if [ "${CUDA_VISIBLE_DEVICES:-}" != "$AMBIENT_PIN" ] || [ "${GPU_DEVICE:-}" != "$AMBIENT_PIN" ]; then
        die "ONE PIN broken before ${1}: this run's CUDA_VISIBLE_DEVICES='${CUDA_VISIBLE_DEVICES:-}', GPU_DEVICE='${GPU_DEVICE:-}', the units' pin (the card P4 proves) '${AMBIENT_PIN}'. Nothing loads a model on an unproven card."
    fi
    say "  one pin: ${1} runs on CUDA_VISIBLE_DEVICES=${AMBIENT_PIN} -- the units' pin, the card P4 proves."
}
build_env_block() {   # prints the managed block this run writes (write_env_file, and the pre-flight just below)
    local pydir; pydir="$(dirname "$PY_SYS")"
        echo "$ENV_BEGIN"
        echo "# Generated by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Edit lines OUTSIDE this"
        echo "# block freely — they survive re-runs. Lines INSIDE are regenerated each run."
        # node's directory, discovered — NOT assumed. The scheduled organs shell
        # out to `claude -p`, and Claude Code runs plugin hooks written in Node.
        # If node is not on the unit's PATH those hooks fail and `claude -p` exits
        # non-zero, so an LLM-calling organ produces NOTHING while a hand-run of
        # the same command succeeds — because an interactive shell has node and a
        # systemd unit does not. Measured here on 2026-09-07: the quarterly
        # manifest organ returned word_count 0 and exit 1 under systemd, and 2,998
        # words and exit 0 once node was reachable. Version managers (nvm, fnm,
        # volta, asdf) all put node somewhere PATH-less, so resolve it rather than
        # guessing a location.
        local nodedir=""
        if command -v node >/dev/null 2>&1; then
            nodedir="$(dirname "$(readlink -f "$(command -v node)")")"
        fi
        # SYSTEMD CANNOT READ `export KEY=value`, AND DROPS IT SILENTLY.
        # MEASURED on this host with a throwaway unit: an EnvironmentFile line
        # `BARE_KEY=bare_ok` resolves; `export EXPORTED_KEY='exported_ok'` resolves
        # to UNSET, with no unit failure and no log line. EIGHTEEN shipped units
        # name this file as EnvironmentFile=, so every assignment below was
        # invisible to all of them -- including the CUDA pin that keeps a
        # background job off the display GPU.
        #
        # (The operator's own file on this machine carries 10 bare lines and zero
        # exports: it had been corrected by hand at some point, which is precisely
        # why the installer's version was never noticed.)
        #
        # Bare assignments are what the consumer needs. A SHELL that wants these
        # exported uses the standard idiom, now documented in the block itself:
        #     set -a; . ~/.voidloom/wizard-library-env; set +a
        #
        # Every value is EXPANDED AT INSTALL TIME rather than left literal: systemd
        # does NOT expand ${...} inside an EnvironmentFile, so a literal `\${PATH}`
        # would have written a path entry whose name is the six characters ${PATH}.
        # Quoting is fine either way -- measured: systemd strips single and double
        # quotes ('1' and "/a/b" both resolve correctly).
        # The PATH line is CURATED, never the invoking shell's PATH (2026-09-20).
        # It used to end in `:${PATH}` -- the operator's ENTIRE install-time PATH,
        # frozen into the file every unit sources. Measured on the reference host:
        # a re-run from a WSL session would have written 3,107 bytes with 62
        # /mnt/c/... Windows entries in place of a 109-byte line; a re-run from a
        # cold gate wrote its systemctl SHIM directory into the units' PATH. The
        # units need the CLI dir, the interpreter's dir, node's dir, nvidia-smi's dir
        # (R7) and the system dirs -- nothing else. Extra dirs a host genuinely needs go in
        # WIZARD_ENV_EXTRA_PATH (colon-separated, appended verbatim, absolute).
        local sysdirs="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        local extra="${WIZARD_ENV_EXTRA_PATH:-}"
        if [ -n "$extra" ]; then
            case "$extra" in /*) ;; *) echo "[install] WIZARD_ENV_EXTRA_PATH must be a colon-separated list of ABSOLUTE dirs: '${extra}'" >&2; exit 2 ;; esac
        fi
        # nvidia-smi's directory, discovered the same way (reference-host round, R7): the
        # organs that enumerate GPUs (gpu_guard, voidloom_manifest) shell out to it, and on
        # WSL2 it lives in /usr/lib/wsl/lib, which an interactive shell's PATH carries and a
        # unit's did not -- so every dream-daytime run said "no GPUs visible -> cloud-claude-p".
        local smidir=""
        if [ -n "$NVSMI" ]; then
            smidir="$(dirname "$(readlink -f "$NVSMI" 2>/dev/null || printf '%s' "$NVSMI")")"
            case ":${sysdirs}:${pydir}:${nodedir}:" in *":${smidir}:"*) smidir="" ;; esac
        fi
        local _p="${LOCAL_BIN}:${pydir}"
        if [ -n "$nodedir" ] && [ "$nodedir" != "/usr/bin" ] && [ "$nodedir" != "/bin" ]; then _p="${_p}:${nodedir}"; fi
        [ -n "$smidir" ] && _p="${_p}:${smidir}"
        _p="${_p}:${sysdirs}${extra:+:$extra}"
        # THE OUT-OF-BLOCK KEY RULE (R1, all shapes): a key the owner assigns OUTSIDE the
        # markers is theirs; the block does not write it, and says so in a comment line.
        local _outside; _outside="$(env_outside keys)"
        _blk() {   # _blk KEY LINE
            if printf '%s\n' "$_outside" | grep -qxF -- "$1"; then
                echo "# $1 is assigned OUTSIDE this block in this file; the block leaves it to that line"
            else
                echo "$2"
            fi
        }
        _blk PATH "PATH=\"${_p}\""
        _blk WIZARD_LIBRARY_HOME "WIZARD_LIBRARY_HOME='${BRAIN_DIR}'"
        _blk WIZARD_LIBRARY_DB "WIZARD_LIBRARY_DB='${DB_PATH}'"
        _blk WIZARD_EMBED_MODEL "WIZARD_EMBED_MODEL=${EMBED_MODEL}"
        # The rule is keyed on NAMES, but CUDA_VISIBLE_DEVICES OUTRANKS WIZARD_CUDA_DEVICE in every unit's resolver
        # (organs/voidloom_device.py: ambient before env), so a block CUDA_VISIBLE_DEVICES would override an owner's
        # out-of-block WIZARD_CUDA_DEVICE by PRECEDENCE (iteration 3, item 4: measured, the owner's 1 lost to a
        # shell's 0; the owner's `WIZARD_CUDA_DEVICE=` -- do not pin -- lost to the heuristic's card).
        if [ -n "${GPU_DEVICE:-}" ]; then
            if printf '%s\n' "$_outside" | grep -qx WIZARD_CUDA_DEVICE; then
                echo "# CUDA_VISIBLE_DEVICES is not written: WIZARD_CUDA_DEVICE is assigned OUTSIDE this block, and a block CUDA_VISIBLE_DEVICES would outrank it in every unit's resolver"
            else
                _blk CUDA_VISIBLE_DEVICES "CUDA_VISIBLE_DEVICES='${GPU_DEVICE}'"
            fi
        fi
        echo "# Bare KEY=value, deliberately: systemd's EnvironmentFile= cannot parse"
        echo "# 'export KEY=value' and drops it SILENTLY. To export these into a shell:"
        echo "#     set -a; . '${ENV_FILE}'; set +a"
        echo "# The overnight thinker's credential is not kept here: voidloom thinker login <claude|kimi|codex|gemini> saves it in its own"
        echo "# locked folder (docs/OVERNIGHT_THINKER_SETUP.md). Change who thinks with: voidloom thinker set <claude|codex|kimi|gemini|local|off>"
        echo "$ENV_END"
}
# THE WRITE IS REHEARSED BEFORE ANYTHING IS TOUCHED (env-file writer pass, W1 + W5; reference-host round residue #21956).
# The writer's side used to refuse only at Step D -- measured on iteration 4: a home named o'brien laid 313 files,
# created the brain, loaded the seed pack, then died rc 1 -- and nothing checked the file AS WRITTEN at all. Now the
# whole write runs here first, into a temporary directory, with the very code Step D runs: the block this run builds
# (verify-block: systemd and a shell read it alike), the file it would produce (install/env_file.py `splice`: the
# writer, placed by the reader's own markers, bytes in and bytes out, verified as written), and the pin that file hands
# the units (`effective-pin`: systemd's reading of the WHOLE file, every line and every block, last-wins, through the
# units' own resolver) -- which must be this run's pin. Any refusal is rc 2, by name, before a file is laid.
EXPECT_PIN="$(effective_pin)"
EXPECT_PIN_WHAT="the pin this run resolved (${_gpu_resolved#*|})"
[ -n "${_gpu_resolved:-}" ] || EXPECT_PIN_WHAT="no pin (this run resolved none)"
[ -n "$(effective_pin_key)" ] && EXPECT_PIN_WHAT="the owner's declaration ($(effective_pin_key) outside the managed block)"
[ "$SHAPE" = "ambient" ] && EXPECT_PIN_WHAT="${EXPECT_PIN_WHAT}; the card P4 proves before anything is laid"
_pre_dir="$(mktemp -d 2>/dev/null)" || die "could not create a temporary directory to rehearse the env-file write (TMPDIR='${TMPDIR:-/tmp}'). ${NOTHING_TOUCHED}"
build_env_block > "${_pre_dir}/block"
if ! _vb="$("$PY_SYS" -B "$ENV_READER" --file "${_pre_dir}/block" --begin "$ENV_BEGIN" --end "$ENV_END" verify-block)"; then
    refuse_write "the managed block this run would write into ${ENV_FILE} is read differently by systemd and by a shell: $(printf '%s' "$_vb" | tr '\n' ' ')"
fi
_sp="$("$PY_SYS" -B "$ENV_READER" --file "$ENV_VIEW" --begin "$ENV_BEGIN" --end "$ENV_END" --organs "${PKG_ROOT}/organs" splice "${_pre_dir}/block" "${_pre_dir}/image")"; _sp_rc=$?
[ "$ENV_VIEW" != "$ENV_FILE" ] && _sp="${_sp//"$ENV_VIEW"/$ENV_FILE}"     # a dry run's view is named as the file it shows
case "$_sp_rc" in
    0) ;;
    2) refuse_write "$(printf '%s\n' "$_sp" | sed -n 's/^REFUSE	//p' | tr '\n' ' ')" ;;
    *) rm -rf "$_pre_dir"; die "install/env_file.py could not rehearse the write of ${ENV_FILE} (rc ${_sp_rc}; named above). ${NOTHING_TOUCHED}" ;;
esac
_img_pin="$("$PY_SYS" -B "$ENV_READER" --file "${_pre_dir}/image" --organs "${PKG_ROOT}/organs" effective-pin)"; _ip_rc=$?
case "$_ip_rc" in 0) ;; 1) _img_pin="" ;; *) refuse_write "the env file this run would write is one the units' resolver cannot read a pin from (effective-pin rc ${_ip_rc})." ;; esac
[ "$_img_pin" = "$EXPECT_PIN" ] || refuse_write "ONE PIN: the env file this run would write hands the units '${_img_pin:-<no pin>}' (systemd's reading of the WHOLE file, every block included, through the units' resolver), while this run's pin is '${EXPECT_PIN:-<no pin>}' -- ${EXPECT_PIN_WHAT}."
rm -rf "$_pre_dir"; unset _pre_dir _sp _sp_rc _img_pin _ip_rc _vb

# ----------------------------------------------------------------------------
# BANNER
# ----------------------------------------------------------------------------
say "=================================================================="
say " Voidloom — Brain Install"
say "=================================================================="
say "Platform:     ${PLATFORM}"
say "Package:      ${PKG_ROOT}"
say "Install into: ${CLAUDE_HOME}"
say "Brain DB:     ${DB_PATH}"
say "Embed tier:   ${EMBED_MODEL} (${EMBED_REPO}, ${EMBED_DIM}-dim, table=${EMBED_TABLE})"
if [ "$PRECACHE_FALLBACK" = "1" ]; then
    _rr_fb_note="also pre-cached"
else
    _rr_fb_note="fetched only if the default fails to load"
fi
# Names the backend this install will ACTUALLY pre-cache and load. The old line
# named the fallback and then said the default "loads lazily" -- true, and the
# reason 1.2 GB arrived unannounced on the first recall.
# The accelerator plan, said before anything is installed (handoff round 1, L4: "the installer's sentence names the
# machine and the gate's reason in plain words"). A dry run says "would"; nothing it names has happened.
_will="will"; [ "$DRY_RUN" -eq 1 ] && _will="would"
say "Compute:      ${ACCEL_BACKEND} [${ACCEL_LABEL:-no label}] -- ${ACCEL_BASIS:-not resolved} (resolver: ${ACCEL_RESOLVER:-none})"
[ -n "$ACCEL_SENTENCE" ] && say "              ${ACCEL_SENTENCE}"
[ -n "$ACCEL_NOTES" ] && say "              note: ${ACCEL_NOTES}"
if [ "$SHAPE" = "ambient" ]; then
    say "Torch:        the host's own (${PY_SYS}); this shape installs no torch"
elif [ "$SHAPE" = "tier0" ]; then
    say "Torch:        none (--tier0-only)"
elif [ -n "$ACCEL_REFUSAL" ]; then
    say "Torch:        NONE -- Tier 1 cannot be installed on this machine as it is: ${ACCEL_REFUSAL}"
else
    say "Torch:        ${_will} install ${ACCEL_SPEC} from $([ -n "$ACCEL_INDEX" ] && echo "${ACCEL_INDEX}" || echo "PyPI") -- ${ACCEL_DESC}"
fi
if [ "$SHAPE" = "ambient" ]; then
    # item 9: this shape pre-caches and loads nothing -- the line may not say it does
    say "Reranker:     ${RERANKER_REPO} (backend ${RERANKER_BACKEND}: NOT pre-cached or loaded by this install -- the ambient shape uses the host's cache as found)"
    say "              gate: $([ "$ACCEL_RERANK" = "1" ] && echo ON || echo OFF) -- ${ACCEL_RERANK_WHY}"
elif [ "$ACCEL_RERANK" = "1" ]; then
    say "Reranker:     ON -- ${ACCEL_RERANK_WHY}"
    say "              ${RERANKER_REPO} (backend ${RERANKER_BACKEND}: ${_will} be pre-cached AND loaded by this install)"
    say "              fallback ${RERANKER_FALLBACK_REPO} -- ${_rr_fb_note}"
else
    say "Reranker:     OFF -- ${ACCEL_RERANK_WHY}"
    say "              ${RERANKER_REPO} ${_will} NOT be downloaded. If this machine later gains the RAM or an accelerator, re-run install.sh: it decides again and fetches it then."
fi
[ -n "$GPU_DEVICE" ] && say "GPU device:   ${GPU_DEVICE} (CUDA_VISIBLE_DEVICES)"
say "Python:       ${PY_SYS} (v$(py_version "$PY_SYS"))"
case "$SHAPE" in
    ambient) say "Shape:        AMBIENT Tier 1 -- ${AMBIENT_TIER} through ${AMBIENT_PY} (from ${AMBIENT_SOURCE}); no venv, no pip, no pre-cache, no re-embedding" ;;
    tier0)   say "Shape:        Tier 0 only (--tier0-only)" ;;
    *)       say "Shape:        venv (Tier 1 in ${BRAIN_DIR}/venv when Step C can build it)" ;;
esac
[ -n "$NVSMI" ] && say "nvidia-smi:   ${NVSMI}"
say "Env file:     ${ENV_FILE}   (${ENV_FILE_ORIGIN})"
say "Crons:        $([ "$WITH_CRONS" -eq 1 ] && echo 'ENABLED (--with-crons)' || echo 'disabled (default)')"
[ -n "$THINKER_ANSWER" ] && say "Thinker:      $(thinker_letter_of "$THINKER_ANSWER")) $(thinker_words_of "$(thinker_letter_of "$THINKER_ANSWER")")${THINKER_MODEL:+ (${THINKER_MODEL})} -- ${THINKER_HOW}; proven, then recorded, before Step A"
[ "$DRY_RUN" -eq 1 ] && say "** DRY RUN — no changes will be made **"

# Sanity: the engine must be in the package (this IS the shippable surface).
[ -f "${PKG_ROOT}/organs/claude_memory.py" ] || die "package incomplete — organs/claude_memory.py not found in ${PKG_ROOT}. Is this a valid brain package?"

# ----------------------------------------------------------------------------
# write_env_file — marker-guarded, APPEND-SAFE env-file write [M5 fix].
#   Managed lines live strictly between ENV_BEGIN/ENV_END. A re-run rewrites ONLY
#   that block; any hand-added line OUTSIDE the markers survives intact. This is
#   the "never overwrites your config — appends with markers" promise, honored.
#   Defined BEFORE the --env-only short-circuit so both call sites can reach it.
# ----------------------------------------------------------------------------
write_env_file() {
    if [ "$DRY_RUN" -eq 1 ]; then
        say "  DRY: would write the managed block (PATH, WIZARD_LIBRARY_DB/HOME, WIZARD_EMBED_MODEL$([ -n "${GPU_DEVICE:-}" ] && echo ', CUDA pin')) into ${ENV_FILE} between the managed markers, preserving any user-added lines outside them."
        return 0
    fi
    run_argv mkdir -p "$(dirname "${ENV_FILE}")"
    # THE WRITE, CHECKED AGAINST SYSTEMD'S READING OF THE FILE AS WRITTEN (env-file writer pass, W1). This used to be
    # three writers with three reading rules of their own: a block REPLACER (`open(encoding="utf-8").read().
    # splitlines()`, its exit status never checked -- measured on 524c63e: one Latin-1 byte in an owner's comment made it
    # write an EMPTY temp file, which `cp -f` installed, rc 0, "your other lines preserved"; a form feed, a CR or a
    # U+2028 re-split an owner's line; a missing end marker dropped every later owner line; only the first of two blocks
    # was rewritten while systemd read the stale second one last), an APPENDER (after an owner's open quote systemd read
    # the whole block into that value) and a token RESCUE (`grep '^(export )?...'`: it wrote an `export` line systemd
    # drops, and missed an indented one systemd reads). All three are now install/env_file.py `splice` -- the one
    # reader's own writer: the block placed by the reader's markers, every owner byte kept, the token systemd delivered
    # before delivered after, the image VERIFIED as written (re-read from the disk, read back as systemd reads it) and
    # its exit status CHECKED. Only a verified, non-empty image is copied over the env file, the copy is compared byte
    # for byte, and the pin the WHOLE file hands the units is read back (every shape, --env-only included).
    local _wd _sp _sp_rc _vb
    _wd="$(mktemp -d 2>/dev/null)" || die "could not create a temporary directory for the env-file write; this write did not change ${ENV_FILE}."
    build_env_block > "${_wd}/block"
    if ! _vb="$("$PY_SYS" -B "$ENV_READER" --file "${_wd}/block" --begin "$ENV_BEGIN" --end "$ENV_END" verify-block)"; then
        rm -rf "$_wd"
        die "the managed block this run would write into ${ENV_FILE} is read differently by systemd and by a shell -- not writing it: $(printf '%s' "$_vb" | tr '\n' ' ')"
    fi
    _sp="$("$PY_SYS" -B "$ENV_READER" --file "$ENV_FILE" --begin "$ENV_BEGIN" --end "$ENV_END" --organs "${PKG_ROOT}/organs" splice "${_wd}/block" "${_wd}/image")"; _sp_rc=$?
    if [ "$_sp_rc" -ne 0 ]; then
        rm -rf "$_wd"
        die "this write did not change ${ENV_FILE}: its writer (install/env_file.py splice) refused or failed (rc ${_sp_rc}): $(printf '%s\n' "$_sp" | sed -n 's/^REFUSE	//p' | tr '\n' ' ')(the rehearsal before Step A accepted it: the file changed during this run, or the writer failed -- named above)"
    fi
    env_install_image "${_wd}/image" "$_wd" "the managed block"
    case "$(printf '%s\n' "$_sp" | sed -n 's/^OK	\([a-z]*\)	.*/\1/p')" in
        replaced) say "  env file: managed block replaced in place; every byte outside it kept (verified as written)." ;;
        appended) say "  env file: managed block appended; the existing content kept byte for byte (verified as written)." ;;
        *)        say "  env file created with the managed block (verified as written)." ;;
    esac
    case "$_sp" in *"rescued=CLAUDE_CODE_OAUTH_TOKEN"*)
        say "  env file: CLAUDE_CODE_OAUTH_TOKEN re-appended OUTSIDE the block as a line systemd reads (its value is the one the units had, or the one an old block's export line meant; never printed)." ;; esac
    rm -rf "$_wd"
    # CRUCIBLE-3 U6: this was `chmod 600 … 2>/dev/null || true`. `|| true` is not an
    # omission, it is a deliberate suppression: the mode was never observed, and on a
    # filesystem that ACCEPTS the call and ignores it (or where the user is not the
    # owner) the env file carrying the token path stays world-readable while the
    # installer says nothing at all. The brain's own rule, from a 2026-08-26 build:
    # "a flag reporting 'the call did not raise' will say true while the directory is
    # world-readable — read it back from stat."
    run_argv chmod 600 "${ENV_FILE}" 2>/dev/null || true
    # THE MODE THAT MATTERS IS THE TARGET'S (reference-host round, R2). `stat` without -L
    # reports a SYMLINK's own mode -- always 777 on Linux -- so on a host whose env file
    # is a link into another directory this warned "mode 777 ... the chmod did not hold"
    # while the file every unit reads was 600 (measured 2026-09-20). chmod follows the
    # link, so the read-back must too: -L on BOTH the GNU and the BSD branch, and the
    # warning names the file whose mode it read.
    if [ -e "${ENV_FILE}" ]; then
        _mode="$(stat -L -c '%a' "${ENV_FILE}" 2>/dev/null || stat -L -f '%Lp' "${ENV_FILE}" 2>/dev/null || echo '?')"
        _mode_path="${ENV_FILE}"
        if [ -L "${ENV_FILE}" ]; then
            _mode_path="$("$PY_SYS" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${ENV_FILE}" 2>/dev/null || echo "${ENV_FILE}")"
        fi
        if [ "${_mode}" = "600" ]; then
            say "  env file mode verified 600 (read back from stat -L, not inferred): ${_mode_path}"
        else
            warn "the env file at ${_mode_path} is mode ${_mode}, not 600 — the chmod did not hold on this filesystem. It may name a token path readable by other users. Fix by hand: chmod 600 '${_mode_path}'"
        fi
    fi
    # THE ONE PIN, READ BACK FROM THE FILE AS WRITTEN (env-file writer pass, W1; the iteration-4 reflector's (d), every
    # shape): the pin every unit reads is systemd's reading of the WHOLE file -- every line, every block, last-wins --
    # through the units' own resolver order. It must be the pin this run resolved (in the ambient shape, the card P4
    # proved). Measured on 524c63e: a stale second managed block carrying CUDA_VISIBLE_DEVICES='0' survived the rewrite
    # and won -- rc 0, INSTALL_RECORD gpu_pin=1, every unit on card 0.
    local _wp _wp_rc
    _wp="$("$PY_SYS" -B "$ENV_READER" --file "$ENV_FILE" --organs "${PKG_ROOT}/organs" effective-pin)"; _wp_rc=$?
    case "$_wp_rc" in 0) ;; 1) _wp="" ;; *) die "ONE PIN unreadable from ${ENV_FILE} AS WRITTEN (effective-pin rc ${_wp_rc}): the units' resolver cannot read a pin from it. No unit was armed by this run." ;; esac
    [ "$_wp" = "${EXPECT_PIN:-}" ] || die "ONE PIN broken by the env file AS WRITTEN: systemd hands the units '${_wp:-<no pin>}' from ${ENV_FILE} (read whole, every block included) while this run's pin is '${EXPECT_PIN:-<no pin>}' -- ${EXPECT_PIN_WHAT:-}. No unit was armed by this run."
    # EW1-04 (iteration-1 reflection): with NO pin key in the file, the units' resolver does not stop at systemd's reading
    # -- it falls to its own layers (measured: an `export WIZARD_CUDA_DEVICE=0` systemd drops resolved '0|env-file'; two
    # display cards resolved card 0 by default) -- so the line says what reaches the units, and what they then do
    if [ -n "$_wp" ]; then
        say "  one pin: ${ENV_FILE}, read whole as systemd reads it, hands the units '${_wp}' -- ${EXPECT_PIN_WHAT:-}."
    else
        say "  one pin: ${ENV_FILE}, read whole as systemd reads it, hands the units NO pin key -- ${EXPECT_PIN_WHAT:-}; their resolver (organs/voidloom_device.py) then falls back to its own layers: a WIZARD_CUDA_DEVICE line it reads in its own env file by its own rules, wizard-config.json's gpu_device_index, else card 0."
        if [ "$FORCE_CPU" -eq 1 ] || [ "$(env_outside get WIZARD_ACCELERATOR 2>/dev/null)" = "cpu" ]; then
            # the CPU answer reaches the units as WIZARD_ACCELERATOR=cpu (contract C2; recorded below by this run, or
            # already the owner's line): the resolver then answers the CPU before any card fallback -- no card is used,
            # so there is no card to warn about (clean-room proof #28100: this warning fired on the owner's CPU answer)
            say "  no card: WIZARD_ACCELERATOR=cpu $([ "$(env_outside get WIZARD_ACCELERATOR 2>/dev/null)" = "cpu" ] && echo "(${ENV_FILE} line $(env_outside line WIZARD_ACCELERATOR))" || echo "(recorded by this run, below)") keeps every unit on the CPU."
        elif [ "$(gpu_count | head -n 1)" -gt 1 ] 2>/dev/null; then
            warn "no pin reaches the units on a host with $(gpu_count | head -n 1) GPUs: their resolver falls back to its own layers, else card 0 -- which may drive a display. Declare WIZARD_CUDA_DEVICE=<index> OUTSIDE the managed block of ${ENV_FILE}, then re-run with --env-only."
        fi
    fi
}

# ----------------------------------------------------------------------------
# accel_declare / accel_record / gate_record -- the accelerator plan, RECORDED (handoff round 1, L4).
#   accel_declare VALUE COMMENT: WIZARD_ACCELERATOR=VALUE (contract C2: a writer writes only auto|cpu) OUTSIDE the
#     managed block, under a dated comment, through the ONE writer (install/env_file.py declare): the line an owner
#     writes by hand, read by every unit and by the organs' resolver. An existing line with the same value is kept; a
#     different one is the owner's and is never rewritten (--force-cpu was refused against it before anything ran).
#   accel_record: --force-cpu (= --accelerator cpu) is DURABLE -- the clean room (#28100, #28117) found --force-cpu
#     alone left every unit resolving a card ("cpu_only: False" in doctor) unless a hand-written line said otherwise.
#   gate_record: the reranker decision, contract C3 -- reranker_enabled + reranker_gate{accelerator, ram_gib,
#     threshold_gib 15.0, decided_at} merged into wizard-config.json (backup before, read back after; an unreadable
#     config is never clobbered). The organs re-check the gate live (L2); this is the decision the install made.
# ----------------------------------------------------------------------------
accel_declare() {   # accel_declare VALUE COMMENT -- 0 recorded/kept, 1 an owner's different line stands (said)
    local _have _wd _out _rc
    if env_outside_has WIZARD_ACCELERATOR; then
        _have="$(env_outside get WIZARD_ACCELERATOR)"
        if [ "$_have" = "$1" ]; then
            say "  ${ENV_FILE} already declares WIZARD_ACCELERATOR=$1 (line $(env_outside line WIZARD_ACCELERATOR)); kept."
            return 0
        fi
        warn "${ENV_FILE} line $(env_outside line WIZARD_ACCELERATOR) declares WIZARD_ACCELERATOR=${_have} -- your own line, which this installer never rewrites; WIZARD_ACCELERATOR=$1 was NOT recorded."
        return 1
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        say "  DRY: would record WIZARD_ACCELERATOR=$1 in ${ENV_FILE}, OUTSIDE the managed block, under the comment: $2"
        return 0
    fi
    _wd="$(mktemp -d 2>/dev/null)" || die "could not create a temporary directory to record WIZARD_ACCELERATOR=$1."
    _out="$("$PY_SYS" -B "$ENV_READER" --file "$ENV_FILE" --begin "$ENV_BEGIN" --end "$ENV_END" declare "${_wd}/image" "WIZARD_ACCELERATOR=$1" "$2")"; _rc=$?
    case "$_rc" in
        0) ;;
        2) rm -rf "$_wd"; warn "WIZARD_ACCELERATOR=$1 cannot be recorded in ${ENV_FILE}: $(printf '%s\n' "$_out" | sed -n 's/^REFUSE	//p' | tr '\n' ' ')"; return 1 ;;
        *) rm -rf "$_wd"; die "install/env_file.py could not record WIZARD_ACCELERATOR=$1 in ${ENV_FILE} (rc ${_rc})." ;;
    esac
    env_install_image "${_wd}/image" "$_wd" "WIZARD_ACCELERATOR=$1"
    run_argv chmod 600 "$ENV_FILE" 2>/dev/null || true
    rm -rf "$_wd"
    say "  recorded WIZARD_ACCELERATOR=$1 in ${ENV_FILE} ($(printf '%s\n' "$_out" | sed -n 's/^OK	//p'), OUTSIDE the managed block, dated): $2"
    return 0
}
accel_record() {
    [ "$FORCE_CPU" -eq 1 ] || return 0
    [ "$SHAPE" = "ambient" ] && return 0          # refused before anything ran (--force-cpu contradicts an ambient Tier 1)
    accel_declare cpu "WIZARD_ACCELERATOR: chosen at install $(date -u +%Y-%m-%d) (--force-cpu) -- every job of this brain computes on the CPU. Edit this line to change it (auto: use the accelerator this machine has)."
}
gate_record() {
    [ -n "$ACCEL_JSON" ] && [ -z "$ACCEL_REFUSAL" ] || { say "  the reranker gate is not recorded: there is no accelerator plan for this machine (${ACCEL_REFUSAL:-none})."; return 0; }
    [ -n "$WIZARD_CONFIG_JSON" ] || { warn "the path of wizard-config.json could not be resolved; the reranker gate was NOT recorded."; return 0; }
    local _out _rc _dry=""
    [ "$DRY_RUN" -eq 1 ] && _dry="--dry-run"
    _out="$(printf '%s' "$ACCEL_JSON" | "$PY_SYS" -B "$ACCEL_PLAN_PY" write-gate --config "$WIZARD_CONFIG_JSON" --plan - $_dry)"; _rc=$?
    _out="$(printf '%s' "$_out" | "$PY_SYS" -B -c 'import json,sys; print(json.load(sys.stdin).get("message",""))' 2>/dev/null)"
    if [ "$_rc" -eq 0 ]; then
        say "  $([ "$DRY_RUN" -eq 1 ] && echo "DRY: ")${_out} -- the reranker is $([ "$ACCEL_RERANK" = "1" ] && echo ON || echo OFF): ${ACCEL_RERANK_WHY}."
    else
        warn "${_out:-accelerator_plan.py write-gate failed (rc ${_rc})}"
    fi
}

# ----------------------------------------------------------------------------
# --env-only short-circuit (write env file + exit; used by sync re-runs).
# ----------------------------------------------------------------------------
if [ "$ENV_ONLY" -eq 1 ]; then
    step "Env-only mode — (re)writing ${ENV_FILE}"
    write_env_file
    accel_record
    gate_record
    say "Env-only mode — done."
    exit 0
fi

# ----------------------------------------------------------------------------
# THE RECORD AND THE LOG (reference-host round, R1)
#
# Measured: PACKAGE_ORIGIN on the reference host was written BY HAND after the 2026-09-20
# upgrade; the installer recorded nothing about what it did or with which interpreter.
# Now every wet run that gets past its refusals leaves ONE line in <brain>/INSTALL_LOG
# (append-only; success, failure and the step it stopped at), and a successful one
# rewrites <brain>/INSTALL_RECORD (key=value: the CURRENT shape). The ambient shape READS
# the record on the next run. PACKAGE_ORIGIN is history and is never touched here.
# ----------------------------------------------------------------------------
STEPS_RUN=""
note_step() { STEPS_RUN="${STEPS_RUN:+$STEPS_RUN,}$1"; }
AMBIENT_PROBES=""
LOG_WRITTEN=0
WORK_STARTED=0
FAIL_DETAIL=""
append_install_log() {   # append_install_log RESULT
    [ "$DRY_RUN" -eq 1 ] && return 0
    [ -d "$BRAIN_DIR" ] || return 0
    printf '%s shape=%s interpreter=%s result=%s steps=%s probes=%s%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SHAPE" "$PY_SYS" "$1" "${STEPS_RUN:-none}" "${AMBIENT_PROBES:-none}" \
        "${FAIL_DETAIL:+ detail=$(printf '%s' "$FAIL_DETAIL" | tr '\n' ' ' | cut -c1-300)}" >> "$INSTALL_LOG" \
        && LOG_WRITTEN=1
}
on_exit() {
    local _rc=$?
    if [ "$DRY_RUN" -ne 1 ] && [ "$WORK_STARTED" -eq 1 ] && [ "$LOG_WRITTEN" -eq 0 ]; then
        append_install_log "failed-rc${_rc}"
    fi
    [ -n "${PROBE_TMP:-}" ] && rm -rf "$PROBE_TMP"
    [ -n "${G1_VIEW_DIR:-}" ] && rm -rf "$G1_VIEW_DIR"      # GPU answers held for the ambient proofs (R-1): never recorded
    [ -n "${THINKER_ENV_DIR:-}" ] && rm -rf "$THINKER_ENV_DIR"   # the env file as this run will write it, for the thinker proof
    dryrun_sidecar_restore
}
trap on_exit EXIT

# The ambient shape's proofs run the PACKAGE's code (the bytes about to be laid) through
# the candidate interpreter; every result is kept for the record.
PROBE_PY="${PKG_ROOT}/install/tier1_probe.py"
PROBE_TMP=""
# (AMBIENT_PIN and effective_pin are resolved with the GPU device, above the banner: ONE pin.)
# P4 reads display_active LIVE (ruler VL-REF-v1.1, memory #21831); the owner's escape for a host
# where no card reports an active display is a DECLARATION outside the managed block, never an
# inference: WIZARD_DISPLAY_INDEX=<index>|none. Ruler VL-REF-v1.2: on that basis the PIN must be the
# owner's declaration too (AMBIENT_PIN_KEY: CUDA_VISIBLE_DEVICES or WIZARD_CUDA_DEVICE outside the block) --
# a declaration may authorise a pin, it may not launder the installer's guess (measured on iteration 2:
# both cards Disabled, WIZARD_DISPLAY_INDEX=none, no pin declared -> the heuristic's card 0 passed P4).
DISPLAY_DECL="$(env_outside get WIZARD_DISPLAY_INDEX)"
hd_probe_summary() {   # bash 3.2 (macOS /bin/bash) cannot parse a heredoc inside $( ): the command and its heredoc live here, called as $(hd_probe_summary) (round 2, B2)
    "$PY_SYS" - "$1" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:
    print("  probe output unreadable: %s" % e); print("SUMMARY - unreadable"); sys.exit(0)
for s in d.get("steps", []):
    f = s.get("facts") or {}
    if s["ok"]:
        brief = ", ".join("%s=%s" % (k, f[k]) for k in ("torch", "sentence_transformers", "sqlite_vec", "dim",
                          "pin", "pin_declared_by", "display_basis", "live_display_enabled", "display_declared", "rows", "query_dim",
                          "skipped", "unvectored_active", "active") if k in f)
        print("  %s ok  %s" % (s["step"], brief))
        for w in f.get("warnings", []) or []:
            print("  %s WARNING  %s" % (s["step"], w))
    else:
        print("  %s FAILED (rc %s): %s" % (s["step"], s["rc"], s.get("error") or s.get("stderr_tail")))
        if "live_display_active" in f:      # what P4 OBSERVED, even when it refused
            print("  %s observed display_active %s (declared WIZARD_DISPLAY_INDEX: %s; the pin %s: %s)"
                  % (s["step"], f["live_display_active"], f.get("display_declared") or "none", f.get("pin", ""),
                     f.get("pin_source") or "?"))
print("SUMMARY %s %s" % (",".join("%s:%s" % (s["step"], "ok" if s["ok"] else "FAILED") for s in d.get("steps", [])),
                         d.get("failed_step") or "-"))
PYEOF
}

run_probe() {   # run_probe STEPS OUT_JSON -- prints each step; returns 0 iff all passed
    local _fc=""; [ "$FORCE_CPU" -eq 1 ] && _fc="--force-cpu"
    "$PY_SYS" -B "$PROBE_PY" run --db "$DB_PATH" --organs "${PKG_ROOT}/organs" \
        --manifest "${BRAIN_DIR}/voidloom_manifest.json" --pin "$AMBIENT_PIN" \
        --display-index "$DISPLAY_DECL" --pin-declared-by "$AMBIENT_PIN_KEY" --pin-source "$AMBIENT_PIN_SOURCE" \
        --env-file "$ENV_FILE" \
        --requirements "${PKG_ROOT}/install/requirements.txt" --steps "$1" $_fc > "$2"
    local _rc=$?
    local _summary
    _summary="$(hd_probe_summary "$2")"
    printf '%s\n' "$_summary" | grep -v '^SUMMARY ' | while IFS= read -r _l; do say "$_l"; done
    local _sl; _sl="$(printf '%s\n' "$_summary" | grep '^SUMMARY ' | tail -1)"
    local _probes="${_sl#SUMMARY }"; _probes="${_probes%% *}"
    AMBIENT_PROBES="${AMBIENT_PROBES:+$AMBIENT_PROBES;}${_probes}"
    if [ "$_rc" -ne 0 ]; then
        FAIL_DETAIL="probe ${_sl##* } failed: $(printf '%s\n' "$_summary" | grep 'FAILED' | head -1)"
        return 1
    fi
    return 0
}

g1_p4_lines() {   # R-1: what a refused ambient proof says about the GPU answers and the lines behind the card
    if [ "${G1_DEFERRED:-0}" -eq 1 ]; then
        # the answers G1 took in THIS run were held for the proofs and never written: the units read exactly what they did
        printf '. Your GPU answers (%s) were HELD for the proofs and never recorded: %s is unchanged, the units read exactly what they read before, and a re-run asks again' "${G1_WHAT:-}" "$ENV_FILE"
        return 0
    fi
    case "$FAIL_DETAIL" in "probe P4 "*) ;; *) return 0 ;; esac
    local _k _l _out=""
    for _k in ${AMBIENT_PIN_KEY:-} WIZARD_DISPLAY_INDEX; do
        _l="$(env_outside line "$_k")" || _l=""
        [ -n "$_l" ] && _out="${_out:+$_out; }${_k}= on line ${_l}"
    done
    [ -n "$_out" ] && printf '. The card P4 refused comes from %s of %s (outside the managed block): edit those lines, then re-run -- this installer never asks again while they are there' "$_out" "$ENV_FILE"
    return 0
}
readback_units() {
    # The manager's own view of every unit this run armed (`systemctl --user show -p
    # ExecStart`): its first token must be the recorded interpreter. Only if the manager
    # answers with no path (no user session) is the rendered unit FILE read instead, and
    # the line says which source it read. Then the PATH the units will get -- the LAST
    # PATH= in the env file, as EnvironmentFile= reads it -- must carry the interpreter's
    # directory (C5: in this shape the block's PATH begins LOCAL_BIN:dirname(interpreter)).
    local _armed _u _line _first _src _bad=0 _n=0 _pydir _path
    _armed="$("$PY_SYS" "$CRON_ROSTER_PY" --roster "$CRON_ROSTER" armed)" || { FAIL_DETAIL="the roster is unreadable at read-back"; return 1; }
    for _u in $_armed; do
        [ -f "${SYSTEMD_USER_DIR}/wizard-library-${_u}.service" ] || continue
        _n=$((_n + 1))
        _line="$(systemctl --user show -p ExecStart "wizard-library-${_u}.service" 2>/dev/null)"
        _first="$(printf '%s\n' "$_line" | sed -n 's/.*path=\([^ ;]*\).*/\1/p' | head -1)"
        _src="systemctl --user show"
        if [ -z "$_first" ]; then
            _first="$(sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' "${SYSTEMD_USER_DIR}/wizard-library-${_u}.service" 2>/dev/null | head -1)"
            _src="the unit file (the manager gave no path)"
        fi
        if [ "$_first" = "$PY_SYS" ]; then
            say "  read-back ${_u}: ExecStart runs ${_first} (${_src})"
        else
            warn "read-back ${_u}: ExecStart runs '${_first:-<nothing>}' (${_src}), not the recorded interpreter ${PY_SYS}"
            _bad=$((_bad + 1))
        fi
    done
    [ "$_n" -gt 0 ] || { FAIL_DETAIL="no armed unit file to read back"; return 1; }
    _pydir="$(dirname "$PY_SYS")"
    _path="$(env_outside effective PATH)"
    case ":${_path}:" in
        *":${_pydir}:"*) say "  read-back env file: the units' PATH carries ${_pydir}" ;;
        *) warn "read-back env file: the units' PATH ('${_path}') does not carry ${_pydir}"; _bad=$((_bad + 1)) ;;
    esac
    [ "$_bad" -eq 0 ] || { FAIL_DETAIL="${_bad} read-back mismatch(es) over ${_n} unit(s)"; return 1; }
    READBACK_UNITS="$_n"
    return 0
}

write_install_record() {
    # key=value, written to a temp file beside it and moved into place. Every value is
    # DERIVED here or carried from a proof this run executed -- none is typed.
    local _tmp="${INSTALL_RECORD}.tmp.$$"
    REC_WRITTEN_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" REC_SHAPE_V="$SHAPE" REC_PY="$PY_SYS" \
    REC_VENV_PY="$([ "$SHAPE" = "venv" ] && [ "${TIER1_OK:-0}" -eq 1 ] && [ -x "${BRAIN_DIR}/venv/bin/python3" ] && echo "${BRAIN_DIR}/venv/bin/python3")" \
    REC_PKG_ROOT="$PKG_ROOT" REC_DB="$DB_PATH" REC_MODEL="$EMBED_MODEL" REC_DIM="$EMBED_DIM" \
    REC_PIN="${AMBIENT_PIN:-${GPU_DEVICE:-}}" REC_PIN_SOURCE="${AMBIENT_PIN_SOURCE:-${_gpu_resolved#*|}}" REC_SHELL_PINS="${SHELL_PIN_VARS}" REC_STEPS="$STEPS_RUN" REC_PROBES="$AMBIENT_PROBES" \
    REC_TIER1="$([ "${TIER1_OK:-0}" -eq 1 ] && echo active || echo not-completed)" \
    REC_PREIMAGE="${UPGRADE_PREIMAGE:-${ATTACH_PREIMAGE:-}}" REC_PROBE_DIR="${PROBE_TMP:-}" \
    REC_READBACK="${READBACK_UNITS:-}" REC_SOURCE="${AMBIENT_SOURCE:-}" REC_RUN_STARTED="${RUN_STARTED_UTC:-}" \
    REC_ACCEL="${ACCEL_BACKEND:-}" REC_LABEL="${ACCEL_LABEL:-}" REC_ACCEL_FAILED="${ACCEL_FAILED_BACKEND:-}" \
    REC_RERANK="${ACCEL_RERANK:-0}" REC_RERANK_WHY="${ACCEL_RERANK_WHY:-}" \
    "$PY_SYS" - "$_tmp" <<'PYEOF' || { rm -f "$_tmp"; return 1; }
import hashlib, json, os, re, sqlite3, sys, urllib.request   # escaped URI (iteration 4, LOW 2)
e = os.environ.get
rec = {"schema": "1", "written_at": e("REC_WRITTEN_AT"), "shape": e("REC_SHAPE_V")}
rec["interpreter"] = e("REC_VENV_PY") or e("REC_PY")
pkg = e("REC_PKG_ROOT")
try:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', open(os.path.join(pkg, "voidloom", "__init__.py"), encoding="utf-8").read())
    rec["package_version"] = m.group(1) if m else ""
except OSError:
    rec["package_version"] = ""
try:
    rec["lay_manifest_sha256"] = hashlib.sha256(open(os.path.join(pkg, "install", "lay_manifest.json"), "rb").read()).hexdigest()
except OSError:
    rec["lay_manifest_sha256"] = ""
rec["db"] = e("REC_DB")
try:
    c = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(os.path.abspath(e("REC_DB"))), uri=True)
    t = c.execute("SELECT value FROM metadata WHERE key='active_embedding_table'").fetchone()
    rec["active_embedding_table"] = t[0] if t else ""
    try:
        rec["schema_migrations_journal_rows"] = str(c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0])
    except sqlite3.Error:
        rec["schema_migrations_journal_rows"] = "absent"
    # item 12: the migrations THIS run applied (the journal count above is the whole history;
    # applied_at is written by schema_migrator in the same %Y-%m-%dT%H:%M:%SZ form as the start)
    try:
        if not e("REC_RUN_STARTED"):
            rec["migrations_applied_this_run"] = "unknown (no run start recorded)"
        else:
            rec["migrations_applied_this_run"] = ",".join(r[0] for r in c.execute(
                "SELECT name FROM schema_migrations WHERE applied_at >= ? ORDER BY name",
                (e("REC_RUN_STARTED"),))) or "none"
    except sqlite3.Error:
        rec["migrations_applied_this_run"] = "journal absent"
    c.close()
except sqlite3.Error as ex:
    rec["active_embedding_table"] = "unreadable: %s" % ex
rec.update({"embed_model": e("REC_MODEL"), "embed_dim": e("REC_DIM"), "gpu_pin": e("REC_PIN"),
            "gpu_pin_source": e("REC_PIN_SOURCE") or ("none: no GPU pinned" if not e("REC_PIN") else ""),
            # declared by <KEY> in <file> (outside the managed block[; chosen at install <date>, <how>]) | the only GPU | ...
            "gpu_invoking_shell": ("ignored, never a declaration: " + e("REC_SHELL_PINS")) if e("REC_SHELL_PINS") else "none set",
            "tier1": e("REC_TIER1"), "steps": e("REC_STEPS"), "probes": e("REC_PROBES"),
            "preimage": e("REC_PREIMAGE"), "readback_units": e("REC_READBACK"), "shape_source": e("REC_SOURCE")})
# model-choice unit (M8): the thinker's record is <brain>/THINKER_RECORD, written ONLY by organs/thinker.py; this
# record carries a DERIVED POINTER to it (read through the seam's own reader, torn/edited records named), never a copy
try:
    sys.path.insert(0, os.path.join(pkg, "organs"))
    import thinker as _thk
    _tr = os.path.join(os.path.dirname(os.path.abspath(e("REC_DB"))), "THINKER_RECORD")
    try:
        _trec = _thk.read_record(_thk.Path(_tr))
        rec["thinker_record"] = _tr if _trec is not None else "absent"
        rec["thinker_backend"] = (_trec or {}).get("backend", "unset")
        rec["thinker_proven_at"] = (_trec or {}).get("proven_at", "")
    except _thk.RecordError as _x:
        rec["thinker_record"] = _tr
        rec["thinker_backend"] = "refused: %s" % _x
except Exception as _x:  # noqa: BLE001 - the pointer never fails the record
    rec["thinker_record"] = "unreadable: %s: %s" % (type(_x).__name__, _x)
d = e("REC_PROBE_DIR")
def steps(name):
    try:
        return {s["step"]: s for s in json.load(open(os.path.join(d, name), encoding="utf-8"))["steps"]}
    except Exception:
        return {}
if d and os.path.exists(os.path.join(d, "pre.json")):
    pre, cnt = steps("pre.json"), steps("counts_after.json")
    f1 = (pre.get("P1") or {}).get("facts") or {}
    for k_src, k_dst in (("sys_version", "sys_version"), ("torch", "torch_version"),
                         ("sentence_transformers", "sentence_transformers_version"), ("sqlite_vec", "sqlite_vec_version")):
        rec[k_dst] = str(f1.get(k_src, ""))
    f3 = (steps("post.json").get("P3") or pre.get("P3") or {}).get("facts") or {}
    rec["p3_rows"] = str(f3.get("rows", "")); rec["p3_query_dim"] = str(f3.get("query_dim", ""))
    rec["model_repo"] = str(f3.get("model_repo", ""))
    f4 = (pre.get("P4") or {}).get("facts") or {}
    rec["p4"] = json.dumps(f4, sort_keys=True)
    rec["p4_display_basis"] = str(f4.get("display_basis", ""))    # observed | owner-declared | single-card
    rec["p5_warnings"] = " | ".join(((pre.get("P5") or {}).get("facts") or {}).get("warnings") or []) or "none"
    fc = (cnt.get("counts") or {}).get("facts") or {}
    for k in ("active", "vectors", "unvectored_active", "orphan_vectors"):
        rec[k] = str(fc.get(k, ""))
# CONTRACT C4 (handoff round 1, L4): the accelerator, its proof label, and every probe P1..P8 -- pass / FAIL: why /
# skipped: why / not run: why -- from the reports THIS run wrote. L2's doctor and banner read these keys.
rec["accelerator"] = e("REC_ACCEL") or ""
rec["proof_label"] = e("REC_LABEL") or ""
if e("REC_ACCEL_FAILED"):
    rec["accelerator_failed"] = e("REC_ACCEL_FAILED")
rec["reranker_enabled"] = "true" if e("REC_RERANK") == "1" else "false"
rec["reranker_gate_reason"] = e("REC_RERANK_WHY") or ""
probes = {}
for name in ("pre.json", "post.json", "p67.json", "p67_cpu.json", "p8.json"):
    for k, v in (steps(name) if d else {}).items():
        if k.startswith("P"):
            probes[k] = v                    # a later report of the same step wins (the CPU re-proof after a flip)
for n in ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"):
    st = probes.get(n)
    if st is None:
        if n == "P3" and e("REC_SHAPE_V") == "venv" and e("REC_TIER1") == "active":
            v = "pass (C.verify: one semantic recall through the facade, at tier 1)"
        else:
            v = "not run (%s)" % (("P1, P2, P4 and P5 are the ambient shape's proofs; this is the %s shape" % e("REC_SHAPE_V"))
                                  if n in ("P1", "P2", "P4", "P5") else
                                  ("Tier 1 is not installed in the %s shape" % e("REC_SHAPE_V")) if e("REC_SHAPE_V") == "tier0"
                                  else "Tier 1 did not reach it")
    elif not st.get("ok"):
        v = "FAIL: %s" % (st.get("error") or st.get("stderr_tail") or "")
    elif (st.get("facts") or {}).get("skipped"):
        v = "skipped: %s" % st["facts"]["skipped"]
    else:
        v = "pass"
    rec["probe." + n] = v
f6 = ((probes.get("P6") or {}).get("facts") or {})
if f6.get("min_cosine") is not None:
    rec["p6_device"] = str(f6.get("device")); rec["p6_min_cosine"] = str(f6.get("min_cosine"))
    rec["p6_min_cosine_required"] = str(f6.get("min_cosine_required"))
f8 = ((probes.get("P8") or {}).get("facts") or {})
if f8.get("p50_s") is not None:
    rec["p8_p50_s"] = str(f8.get("p50_s")); rec["p8_times_s"] = ",".join(str(x) for x in f8.get("times_s") or [])
lines = ["# <brain>/INSTALL_RECORD -- the CURRENT install shape, written by install.sh (reference-host round, R1).",
         "# Rewritten on every successful wet run; the ambient shape READS shape= and interpreter= on the next one.",
         "# To switch the interpreter every unit runs, move this file aside deliberately: that is an owner's act."]
for k, v in rec.items():
    lines.append("%s=%s" % (k, str(v if v is not None else "").replace("\n", " ")))
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
PYEOF
    mv -f "$_tmp" "$INSTALL_RECORD"
}

if [ "$DRY_RUN" -ne 1 ]; then
    WORK_STARTED=1; RUN_STARTED_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi
if [ "$SHAPE" = "ambient" ]; then
    TIER_BEFORE="$(brain_decl)"
    [ -n "$TIER_BEFORE" ] || die "could not read the brain's declarations at ${DB_PATH} (mode=ro) before the proofs; nothing was laid."
    step "STEP A.0 — PROVING the ambient Tier 1 through ${PY_SYS} before anything is laid"
    assert_one_pin "the proofs"
    if [ "$DRY_RUN" -eq 1 ]; then
        say "  DRY: would prove P1 imports, P2 a vec query at the brain's declared width, P4 the pin"
        say "  DRY: '${AMBIENT_PIN:-<none>}' (${AMBIENT_PIN_SOURCE:-no pin resolved}) against every card's LIVE display_active"
        say "  DRY: (declared WIZARD_DISPLAY_INDEX: ${DISPLAY_DECL:-none}; with no card Enabled the pin must be DECLARED too),"
        say "  DRY: P3 one offline semantic recall through the package's recall path, P5 the version pins --"
        say "  DRY: by EXECUTING ${PROBE_PY} with ${PY_SYS}."
    else
        PROBE_TMP="$(mktemp -d)"
        if run_probe "P1,P2,P4,P3,P5" "${PROBE_TMP}/pre.json"; then
            note_step "proofs"
            say "  the candidate PROVED Tier 1 by execution (the record keeps every fact above)."
            g1_record_held      # R-1: the GPU answers G1 held are recorded only now, after P4 passed on them
        else
            die "the ambient Tier 1 was NOT proven through ${PY_SYS}: ${FAIL_DETAIL}. Nothing was laid, no venv was built, tier0_only was not written, the brain's content was not changed (a read-only proof may leave SQLite -wal/-shm sidecars beside it)$(g1_p4_lines)."
        fi
        run_probe "counts" "${PROBE_TMP}/counts_before.json" \
            || die "could not count the brain's vectors through ${PY_SYS}: ${FAIL_DETAIL}"
    fi
fi
# ----------------------------------------------------------------------------
# THE SEED PACK ON AN UPGRADE IS THE OWNER'S CHOICE (Round 3 G1, item 2; memory #23616). The ambient shape adds no
# memories to the brain it found BY ITSELF (R1), and its acceptance gate used to FAIL on exactly that policy: seed pack
# 1.5.0 carried 1 new + 11 changed entries, self_check required all 74, and the owner read the old blanket banner
# ("ACCEPTANCE GATE FAILED" over an install that had worked) -- and re-ran it. Now: the pack's delta is
# read here, READ-ONLY (load-seed --plan opens the brain mode=ro), before anything is laid, and the new and changed
# entries are the owner's to add -- --load-seed, or y/n at a terminal; with neither they are REPORTED, never loaded.
# The gate (STEP E, --seed-optional) requires exactly the entries this brain has received.
# ----------------------------------------------------------------------------
SEED_LOAD=0; SEED_HOW=""; SEED_PLAN_NEW=0; SEED_PLAN_CHG=0; SEED_PLAN_VER=""
seed_plan_read() {   # seed_plan_read ENGINE PACK -- "<new> <changed> <version>" for THIS brain, read-only; '' when unknown
    WIZARD_LIBRARY_DB="${DB_PATH}" "${PY_SYS}" -B "$1" load-seed --file "$2" --plan 2>/dev/null \
        | "${PY_SYS}" -B -c 'import json,sys
try:
    d = json.loads(sys.stdin.read().strip().splitlines()[-1])
    print("%d %d %s" % (len(d["new"]), len(d["changed"]), d.get("seed_pack_version") or "?"))
except Exception:
    print("")' 2>/dev/null
}
seed_check_yn() {   # ask_line's CHECK for the seed question: FIXED TEXT
    case "$1" in y|Y|yes|Yes|YES|n|N|no|No|NO) return 0 ;; esac
    ask_say "  Answer y (add them) or n (leave the brain as it is)."
    return 1
}
seed_settle() {     # the ambient shape only; no write happens in here
    [ "$SHAPE" = "ambient" ] || return 0
    [ -f "${PKG_ROOT}/seed/seed_pack.json" ] && [ -f "${PKG_ROOT}/organs/claude_memory_lite.py" ] && [ -f "$DB_PATH" ] || return 0
    local _p _a _rc
    _p="$(seed_plan_read "${PKG_ROOT}/organs/claude_memory_lite.py" "${PKG_ROOT}/seed/seed_pack.json")"
    [ -n "$_p" ] || return 0
    read -r SEED_PLAN_NEW SEED_PLAN_CHG SEED_PLAN_VER <<< "$_p"
    [ "${SEED_PLAN_NEW:-0}" -gt 0 ] || [ "${SEED_PLAN_CHG:-0}" -gt 0 ] || return 0
    if [ "$LOAD_SEED" -eq 1 ]; then
        SEED_LOAD=1; SEED_HOW="--load-seed"
        say "Seed pack:    ${SEED_PLAN_VER} has ${SEED_PLAN_NEW} new and ${SEED_PLAN_CHG} changed entries this brain has not received -- added in STEP B.1 (--load-seed: the owner's answer)."
        return 0
    fi
    if [ "$DRY_RUN" -ne 1 ] && ask_tty; then
        ask_say ""
        ask_say "[install] One question about the starter memories: seed pack ${SEED_PLAN_VER} has ${SEED_PLAN_NEW} new and ${SEED_PLAN_CHG} changed entries this brain has not received. An upgrade adds none by itself. Adding them is your choice: only those entries are added; a changed entry's older memory is archived, never deleted; nothing else in the brain changes."
        ask_line _a "Add them now? (y or n):" seed_check_yn; _rc=$?
        if [ "$_rc" -ne 0 ]; then
            SEED_LOAD=0; SEED_HOW="no answer (end of input): left as they are"
        else
            case "$_a" in y|Y|yes|Yes|YES) SEED_LOAD=1; SEED_HOW="answered y at the terminal" ;; *) SEED_LOAD=0; SEED_HOW="answered n at the terminal" ;; esac
        fi
    else
        SEED_LOAD=0; SEED_HOW="no terminal and no --load-seed"
    fi
    say "Seed pack:    ${SEED_PLAN_VER} has ${SEED_PLAN_NEW} new and ${SEED_PLAN_CHG} changed entries this brain has not received -- $([ "$SEED_LOAD" -eq 1 ] && echo "added in STEP B.1" || echo "NOT added, reported as your choice") (${SEED_HOW})."
}
seed_settle

# M10: the owner's answer to the overnight-thinker question is PROVEN in the timers' environment, then recorded -- after
# the ambient proofs (a refused upgrade records nothing) and BEFORE Step A lays anything (a failed proof lays nothing)
thinker_prove

# ----------------------------------------------------------------------------
# A FRESH MACHINE NEEDS NO FLAG (public-release stranger test, 2026-10-27): with no brain at
# ${DB_PATH}, none bundled in the package, and no --brain-db / --attach-db, the only sensible
# brain is a new one. This used to die at STEP B ("no brain snapshot found") AFTER STEP A had
# laid every file, so the README's plainest command failed on every fresh clone. Decided here,
# before anything is laid, and said out loud.
if [ "$INIT_BLANK" -eq 0 ] && [ -z "$ATTACH_DB" ] && [ -z "$EXPLICIT_BRAIN_DB" ] && [ "$SHAPE" != "ambient" ] \
   && [ ! -f "$DB_PATH" ] && [ ! -f "${PKG_ROOT}/brain/claude_memory.db" ] && [ ! -f "${PKG_ROOT}/claude_memory.db" ]; then
    INIT_BLANK=1
    say "No brain on this machine yet, and none bundled: starting a fresh one (the same as --init-blank)."
fi

# STEP A — LAY FILES: one line, one manifest, one executor.
#
# Council #12 (2026-09-18). This step used to be fourteen `cp` sites -- six of
# them globs under `2>/dev/null || true` -- and it silently skipped 62 of the 299
# tracked files (docs/cycle1, docs/cycle2, docs/gates, tools/harness, the five
# council lenses) while printing "laying down persona + council skills" over a
# copy of persona/skills/ alone. install.ps1 described a layout that no longer
# existed. Two hand descriptions of one fact, neither bound to the tree.
#
# Now: install/lay_manifest.json is DERIVED from `git ls-files --stage` by
# tools/build_manifest.py --lay (refusing any merge stage != 0), every entry
# carries src/root/dst/sha256/bytes/exec/platform/component, and install/lay.py
# (stdlib, Python >= 3.8, the same file install.ps1 will call) creates every
# destination directory first, copies every entry for this platform, re-hashes
# each one and refuses -- rc 3, naming the file -- on any mismatch.
# voidloom/tests/test_lay_manifest.py binds the manifest to the tree in both
# directions, so a tracked file that is not in the manifest is red at push, not
# silent at install. Council entries (component: council) are NOT laid here;
# install/install_council.sh lays them under its never-clobber contract.
#
# What remains below the one line is not laying: a runtime directory, a
# generated file, and the launcher symlink -- each named in install/write_set.json
# or in the literal-binding test's runtime allowlist.
# ----------------------------------------------------------------------------
step "STEP A — Laying files into ${CLAUDE_HOME}"
LAY_PY="${PKG_ROOT}/install/lay.py"
LAY_MANIFEST="${PKG_ROOT}/install/lay_manifest.json"
[ -f "$LAY_PY" ] || die "package incomplete — install/lay.py not found in ${PKG_ROOT}."
[ -f "$LAY_MANIFEST" ] || die "package incomplete — install/lay_manifest.json not found in ${PKG_ROOT}. It is generated by tools/build_manifest.py --lay and ships with the package."
_lay_dry=""; [ "$DRY_RUN" -eq 1 ] && _lay_dry="--dry-run"
"$PY_SYS" "$LAY_PY" apply --manifest "$LAY_MANIFEST" --platform posix \
    --root "brain=${BRAIN_DIR}" --root "skills=${SKILLS_DIR}" --root "home=${CLAUDE_HOME}" ${_lay_dry}
_lay_rc=$?
[ "$_lay_rc" -eq 0 ] || die "install/lay.py refused or failed (rc ${_lay_rc}); every problem is named above. Nothing after this line is a claim that the files are laid."
note_step "lay"

# A.1 — the ONE runtime directory the engine expects beside the brain.
run_argv mkdir -p "${BRAIN_DIR}/logs"
# A.1b-i — voidloom_manifest.json is GENERATED here, never shipped. It records
# the hardware THIS host actually has (tier, GPU count, which index drives the
# display). A packaged manifest would assert the packager's machine: on a
# single-GPU box that means claiming a compute index that does not exist. The
# module is the SOLE writer by its own contract, and it merges on upgrade, so
# re-running the installer never clobbers a tuned policy.
# C2/M9: this read `${DRY:-0}`, and `DRY` is assigned NOWHERE in this file --
# grep it. The guard expanded to "0" on every run, so `--dry-run` wrote the
# manifest and (at the seed-pack site below) loaded memories into a brain while
# printing "no changes will be made".
if [ "$DRY_RUN" != "1" ] && [ -f "${BRAIN_DIR}/organs/voidloom_manifest.py" ]; then
    if "${PY_SYS}" "${BRAIN_DIR}/organs/voidloom_manifest.py" write "${BRAIN_DIR}/voidloom_manifest.json" >/dev/null 2>&1; then
        say "  hardware manifest written for THIS host"
    else
        warn "manifest write failed; model selection falls back to its built-in defaults"
    fi
fi
# A.1d — the PATH symlink for the voidloom CLI (the package dir itself is laid
#         by the manifest, exec bit included). voidloom is a thin facade over
#         claude_memory.py: a package dir beside the brain and a shell shim that
#         runs it in place (no pip required). This symlink is an outside-target
#         write and is declared in install/write_set.json (Step A).
if [ "$DRY_RUN" -eq 1 ] || [ -f "${BRAIN_DIR}/voidloom/voidloom" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  DRY: mkdir -p ${LOCAL_BIN}; ln -sf ${BRAIN_DIR}/voidloom/voidloom ${LOCAL_BIN}/voidloom"
    else
        # EW2-12: quoted arguments, no eval -- and a failure STOPS here instead of printing a success line
        { mkdir -p "${LOCAL_BIN}" && ln -sf "${BRAIN_DIR}/voidloom/voidloom" "${LOCAL_BIN}/voidloom"; } \
            || die "could not put the voidloom CLI on PATH at ${LOCAL_BIN}/voidloom (mkdir -p / ln -sf failed above)"
    fi
    say "  voidloom CLI symlinked onto PATH: ${LOCAL_BIN}/voidloom"
    say "  (ensure ${LOCAL_BIN} is on your PATH; then 'voidloom doctor' verifies the brain)"
fi
# A.2 — hooks: `voidloom hooks install` owns hook wiring (C3 retired the Node
#        hook this step used to lay). hooks/*.py are laid by the manifest under
#        ${BRAIN_DIR}/hooks/; nothing here touches ${CLAUDE_HOME}/hooks.

# ----------------------------------------------------------------------------
# STEP B — OBTAIN / VERIFY THE BRAIN DB
#   Bundled snapshot (brain/claude_memory.db) > --brain-db > --init-blank.
#   Idempotent: an existing DB is left in place. Tier 0 CANNOT silently ship a
#   blank brain unless --init-blank is explicit.
# ----------------------------------------------------------------------------
step "STEP B — Obtaining the brain DB"
TIER0_OK=0
BRAIN_SRC=""
resolve_brain_source() {
    if [ -n "$EXPLICIT_BRAIN_DB" ]; then
        [ -f "$EXPLICIT_BRAIN_DB" ] || die "explicit --brain-db not found: ${EXPLICIT_BRAIN_DB}"
        BRAIN_SRC="$EXPLICIT_BRAIN_DB"; return 0
    fi
    if [ -f "${PKG_ROOT}/brain/claude_memory.db" ]; then
        BRAIN_SRC="${PKG_ROOT}/brain/claude_memory.db"; return 0
    fi
    if [ -f "${PKG_ROOT}/claude_memory.db" ]; then
        BRAIN_SRC="${PKG_ROOT}/claude_memory.db"; return 0
    fi
    return 1
}

# A VERIFIED pre-image: sqlite3's online backup API (never `cp`, which copies a WAL-mode
# file mid-write and calls the result a backup), read back -- it opens, and its memories
# count equals the source's. Used by --attach-db and, before its migrations, by the ambient
# upgrade (R1, amendment C7). The body is the one --attach-db has used since round 1.
write_verified_preimage() {   # write_verified_preimage SRC DST
    "$PY_SYS" - "$1" "$2" <<'PYEOF'
import os, sqlite3, sys, urllib.request   # escaped URIs (iteration 4, LOW 2)
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(os.path.abspath(src)), uri=True, isolation_level=None)
d = sqlite3.connect(dst)
try:
    # ONE read snapshot for the count AND the copy (iteration 3, item 9): the count used to come from a NEW
    # connection after backup(), so any concurrent writer made a good image "unverified" (measured: a writer
    # every 10 ms -> 1574 vs 1576, FATAL). backup() reads through this connection, inside this transaction.
    s.execute("BEGIN")
    a = s.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    s.backup(d)
    s.execute("COMMIT")
finally:
    d.close(); s.close()
b = sqlite3.connect("file:%s?immutable=1" % urllib.request.pathname2url(os.path.abspath(dst)), uri=True).execute("SELECT COUNT(*) FROM memories").fetchone()[0]
print(f"  pre-image written: {dst} ({b} memories; source holds {a})")
sys.exit(0 if a == b else 1)
PYEOF
}

if [ "$DRY_RUN" -eq 1 ]; then
    if [ "$SHAPE" = "ambient" ] && [ -z "$ATTACH_DB" ]; then
        say "  DRY: would UPGRADE the existing brain at ${DB_PATH} in place (ambient shape): write its verified"
        say "  DRY: pre-image via sqlite3 backup() to ${UPGRADE_PREIMAGE}, apply this package's migrations, re-prove P3."
    elif [ -n "$ATTACH_DB" ]; then
        say "  DRY: would ATTACH the existing brain at ${DB_PATH} in place (nothing copied),"
        say "  DRY: write its pre-image via sqlite3 backup() to ${ATTACH_PREIMAGE},"
        say "  DRY: then apply this package's migrations to it and verify recall."
    else
        say "  DRY: would resolve the brain source (bundled snapshot / --brain-db / --init-blank),"
        say "  DRY: copy or init it at ${DB_PATH}, then verify recall."
    fi
    TIER0_OK=1
else
    # R4/P7, THE INSTALLER HALF. `-f` alone accepts a 0-BYTE file, so a
    # single failed init used to wedge every subsequent run: this branch
    # said "leaving it in place (idempotent)", B.2 then reported
    # "missing required table/view: memories" and died, and no message
    # anywhere named the 0-byte file as the cause. Reproduced end to end
    # 2026-09-03. `-s` (non-empty) is the cheap half; the explicit branch
    # below is the half that tells the user what to do.
    if [ -n "$ATTACH_DB" ]; then
        # --attach-db: the brain stays where it is. Before the migrator touches
        # it, a PRE-IMAGE is written beside it by sqlite3's online backup API --
        # never `cp`, which copies a WAL-mode file mid-write and calls the result
        # a backup -- and read back: it opens, and its memories count equals the
        # source's. No verified pre-image, no migration.
        say "  attaching the existing brain at ${DB_PATH} (in place; nothing is copied)"
        if write_verified_preimage "$DB_PATH" "$ATTACH_PREIMAGE"; then
            say "  pre-image verified by read-back (memories count equal): ${ATTACH_PREIMAGE}"
            note_step "preimage"
        else
            die "could not write a VERIFIED pre-image beside ${DB_PATH} (wanted ${ATTACH_PREIMAGE}). Refusing to migrate an attached brain without one."
        fi
    elif [ -f "$DB_PATH" ] && [ ! -s "$DB_PATH" ]; then
        die "the brain DB at ${DB_PATH} is 0 bytes -- an earlier init failed and left it behind. It is not a brain, and this script will not overwrite a file it did not create. Remove it and re-run: rm '${DB_PATH}'"
    elif [ -f "$DB_PATH" ] && [ "$SHAPE" = "ambient" ]; then
        # R1 (C7): the ambient UPGRADE migrates the brain it found, in place -- behind the same
        # verified pre-image --attach-db takes. No verified pre-image, no migration.
        say "  upgrading the existing brain at ${DB_PATH} in place (ambient shape; nothing is copied or replaced)"
        if write_verified_preimage "$DB_PATH" "$UPGRADE_PREIMAGE"; then
            say "  pre-image verified by read-back (memories count equal): ${UPGRADE_PREIMAGE}"
            note_step "preimage"
        else
            die "could not write a VERIFIED pre-image beside ${DB_PATH} (wanted ${UPGRADE_PREIMAGE}). Refusing to migrate the brain without one."
        fi
    elif [ -f "$DB_PATH" ]; then
        say "  brain DB already exists at ${DB_PATH}; leaving it in place (idempotent)."
        say "  (to replace it: rm '${DB_PATH}' then re-run, or pass --brain-db <snapshot>.)"
    elif resolve_brain_source; then
        SRC_SZ="$(wc -c < "$BRAIN_SRC" 2>/dev/null || echo 0)"
        say "  cloning the brain: ${BRAIN_SRC} ($(( SRC_SZ / 1048576 )) MB) -> ${DB_PATH}"
        run_argv cp -f "${BRAIN_SRC}" "${DB_PATH}" || die "could not copy the brain snapshot ${BRAIN_SRC} to ${DB_PATH}"
    elif [ "$INIT_BLANK" -eq 1 ]; then
        say "  no snapshot found; initializing a FRESH empty brain (--init-blank)."
        # WIZARD_EMBED_MODEL MUST be passed. `init` resolves the tier itself, and on
        # a fresh DB there is no metadata to read — so without this it defaults to
        # nomic, creates only the 768 table, and stamps the brain nomic/768, while
        # the installer has already PRINTED "Embed tier: qwen ... table=
        # memory_embeddings_v3". The announcement and the action disagreed, and the
        # only symptom was a tier stamp nobody reads until first recall. The Tier-1
        # path below passes it; this one did not.
        if WIZARD_EMBED_MODEL="$EMBED_MODEL" WIZARD_LIBRARY_DB="$DB_PATH" "$PY_SYS" "${BRAIN_DIR}/organs/claude_memory.py" init >/dev/null 2>&1; then
            say "  fresh brain initialized at ${DB_PATH}."
        else
            # C3 -- THE TIER-0 BRAIN, and the reason line 26 stops being a lie.
            #
            # `claude_memory.py init` hard-requires sqlite_vec, so on the very
            # interpreter this script promises ("Tier 0 ALWAYS succeeds,
            # stdlib-only ... keyword recall works on ANY python3") it cannot
            # run. Measured on /usr/bin/python3 3.14: FATAL at STEP B, no brain,
            # install aborted -- the guaranteed tier failing at the guaranteed
            # step. schema_migrator is pure stdlib and builds the brain from
            # schema.sql + every migration the package ships; on that same
            # interpreter it produced 57 objects with the two vec0 migrations
            # reported UNMEASURABLE rather than failed.
            say "  full-engine init unavailable on ${PY_SYS}; building the brain at Tier 0 (stdlib)."
            rm -f "$DB_PATH"
            if "$PY_SYS" -c "import sqlite3,sys; sqlite3.connect(sys.argv[1]).close()" "$DB_PATH" \
               && WIZARD_LIBRARY_DB="$DB_PATH" "$PY_SYS" "${BRAIN_DIR}/organs/schema_migrator.py" apply --db "$DB_PATH"; then
                say "  fresh brain initialized at Tier 0: ${DB_PATH}."
                say "  (semantic recall needs the Tier-1 venv; keyword recall works now.)"
            else
                die "could not create a brain at ${DB_PATH} by either route. Inspect: WIZARD_LIBRARY_DB='${DB_PATH}' ${PY_SYS} '${BRAIN_DIR}/organs/claude_memory.py' init   and   ${PY_SYS} '${BRAIN_DIR}/organs/schema_migrator.py' apply --db '${DB_PATH}'"
            fi
        fi
    else
        die "no brain snapshot found (expected brain/claude_memory.db in the package). Pass --brain-db <path> to clone an existing brain, or --init-blank to start fresh."
    fi

    # B.1.5 — BRING THE BRAIN UP TO THIS PACKAGE'S SCHEMA, WHATEVER ITS SOURCE.
    #
    # C2.  All three sources above landed a brain and NONE of them applied the
    # package's migrations. `claude_memory.py init` applies SEVEN hand-named
    # ones (001-005, 015, 016), each gated on a Python module import, while
    # `migrations/` ships 29; the clone and bundled-snapshot branches apply
    # none at all. Measured on a fresh --init-blank: 24 objects against a
    # reference of 66, and every gate below printed PASS over it.
    #
    # The required set is DERIVED, not typed: schema_migrator builds a throwaway
    # brain from this package's own schema.sql + migration_*.sql and reads its
    # sqlite_master. That reference is tier-relative by construction -- on an
    # interpreter without sqlite_vec the two vec0 migrations cannot build, so
    # they are absent from BOTH sides and reported UNMEASURABLE rather than
    # failed.
    MIGRATOR="${BRAIN_DIR}/organs/schema_migrator.py"
    if [ "$DRY_RUN" = "1" ]; then
        say "  DRY: would apply this package's migrations to ${DB_PATH}"
    elif [ -f "$MIGRATOR" ]; then
        if WIZARD_LIBRARY_DB="$DB_PATH" "$PY_SYS" "$MIGRATOR" apply --db "$DB_PATH"; then
            say "  brain schema matches this package."
            note_step "migrations"
            # R1: P3 again, AFTER the migrations -- a post-migration gate: the same one offline
            # semantic recall through the package's recall path must still answer.
            if [ "$SHAPE" = "ambient" ]; then
                assert_one_pin "the post-migration P3"
                if run_probe "P3" "${PROBE_TMP}/post.json"; then
                    say "  P3 re-proven after the migrations: semantic recall still answers."
                    note_step "post-migration-P3"
                else
                    die "semantic recall did NOT survive this package's migrations (${FAIL_DETAIL}). The verified pre-image is ${UPGRADE_PREIMAGE:-${ATTACH_PREIMAGE:-}}; restore it by hand if you choose. Nothing after this line ran."
                fi
            fi
        else
            die "the brain at ${DB_PATH} could not be brought up to this package's schema. Inspect: WIZARD_LIBRARY_DB='${DB_PATH}' ${PY_SYS} '${MIGRATOR}' audit --json"
        fi
    else
        die "organs/schema_migrator.py is missing from this package; the installer cannot verify the brain's schema. This file ships with the package -- re-download it."
    fi

    # B.2 — sanity: required tables present + the embedding tier matches the DB.
    "$PY_SYS" - "$DB_PATH" "$EMBED_TABLE" "$BRAIN_DIR" <<'PYEOF' || die "the installed brain DB failed its sanity check."
import os, pathlib, sqlite3, sys, urllib.request   # escaped URIs (iteration 4, LOW 2)
db, want_tab, brain_dir = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    c = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(os.path.abspath(db)), uri=True)
except Exception as e:
    sys.stderr.write(f"FATAL: cannot open brain DB: {e}\n"); sys.exit(1)
tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
# C2/M2: this asserted a TYPED 2-item list -- ("memories", "memories_fts") --
# and so passed on a brain carrying 24 of the 66 objects this package's own
# migrations build. The required set now comes from the package, executed.
# The organs live beside the BRAIN DIRECTORY, not beside the DB: under --attach-db
# the brain is wherever the operator keeps it, and deriving the organs dir from
# the DB's parent (as this line did) imported nothing and failed the gate on a
# healthy, freshly migrated brain.
sys.path.insert(0, str(pathlib.Path(brain_dir) / "organs"))
import schema_migrator as _sm
_ref, _unmeasurable, _ref_errors = _sm.build_reference(_sm.migrations_dir())
# D2-F1: if a shipped migration ERRORED while building the reference, the objects
# it would have created never enter _ref -- so `_ref - tabs` is empty for a reason
# that has nothing to do with the brain, and this gate would pass a brain missing
# them. Refuse loudly: the comparison is meaningless, not passing.
if _ref_errors:
    sys.stderr.write(
        "FATAL: this package's own migrations did not all execute, so the brain "
        "cannot be checked against them:\n")
    for _fe in _ref_errors:
        sys.stderr.write(f"  {_fe['file']}: {_fe['errors'][0]}\n")
    sys.stderr.write("This is a defect in the PACKAGE. Do not read the object count "
                     "below as a pass.\n")
    sys.exit(1)
_missing = sorted(_ref - tabs)
if _missing:
    sys.stderr.write(
        f"FATAL: brain DB is missing {len(_missing)} of the {len(_ref)} objects this "
        f"package's migrations build: {', '.join(_missing[:10])}"
        f"{' ...' if len(_missing) > 10 else ''}\n")
    sys.exit(1)
for _u in _unmeasurable:
    sys.stderr.write(f"UNMEASURABLE: {_u}\n")
n = c.execute("SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived,0)=0").fetchone()[0]
row = c.execute("SELECT value FROM metadata WHERE key='active_embedding_table'").fetchone()
active = (row[0] if row else "(none)")
if want_tab not in tabs and active != "(none)":
    sys.stderr.write(f"WARN: chosen embed table {want_tab} not in DB (active={active}); semantic recall may differ.\n")
c.close()
print(f"  brain sanity OK: {n} live memories, {len(tabs)} objects "
      f"(>= the {len(_ref)} this package builds), active embedding table = {active}.")
PYEOF

    # B.3 — verify recall is alive (the Tier-0 acceptance gate, stdlib-only path).
    #
    # C2/M3. This line called `claude_memory.py` -- the FULL engine -- while
    # calling itself the stdlib-only Tier-0 gate. Measured: it imported torch,
    # transformers, sentence_transformers, numpy and sqlite_vec, 12.18 s wall
    # and 3.56 GB max RSS on a WARM cache (a cold machine additionally pulls
    # ~1.7 GB), on a `--tier0-only` install. And BOTH arms set TIER0_OK=1, so
    # whatever it found was discarded. A gate that cannot change an outcome is
    # a sentence, not a gate.
    #
    # The Tier-0 engine is `claude_memory_lite.py`: stdlib FTS5, no model, and
    # it reports its own mode so the claim is readable rather than assumed.
    if WIZARD_LIBRARY_DB="$DB_PATH" "$PY_SYS" "${BRAIN_DIR}/organs/claude_memory_lite.py" \
         recall --query "voidloom brain memory" --top_k 1 2>/dev/null | grep -q '"mode": *"fts5-keyword"'; then
        say "  Tier 0 VERIFIED — stdlib keyword recall over the brain is live."
        TIER0_OK=1
    else
        warn "Tier-0 keyword recall did not report fts5-keyword mode; the brain is NOT verified at Tier 0."
        TIER0_OK=0
    fi
fi

# ----------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# STEP B.1 — LOAD THE SEED PACK  (Tier 0; runs before any Tier 1 attempt)
#
# The pack is agnostic starter knowledge: lessons that hold in ANY living brain,
# never this brain's contents. It is loaded by claude_memory_lite.py, the stdlib
# FTS5 engine, so it works before torch, sqlite_vec or a venv exist -- which is
# the whole point of doing it here rather than in STEP C.
#
# install.ps1 has done this since day one (:227). install.sh never did, and never
# even copied the file, so `--init-blank` produced a brain with nothing in it and
# the acceptance gate in STEP E passed by construction: it asked whether the seed
# had been loaded into a brain where loading was impossible.
#
# Guarded three ways, because a seed load must never be the reason an install
# fails: the pack must exist, the engine must exist, and a non-zero load is
# reported and carried on from. An empty brain is a working brain; a missing
# seed is a poorer one, not a broken one.
# ---------------------------------------------------------------------------
SEED_PACK="${BRAIN_DIR}/seed/seed_pack.json"
LITE_ENGINE="${BRAIN_DIR}/organs/claude_memory_lite.py"
if [ "$SHAPE" = "ambient" ] && [ "${SEED_LOAD:-0}" -eq 1 ] && [ "$DRY_RUN" != "1" ] && [ -f "$SEED_PACK" ] && [ -f "$LITE_ENGINE" ]; then
    # Round 3 G1, item 2: the OWNER chose to add the pack's new and changed entries (seed_settle: --load-seed, or y at a
    # terminal). The loader's per-memory anchor delta adds only those; a changed entry's older row is archived, never
    # deleted; a second run adds zero. This shape still embeds nothing: the added entries are keyword-recallable now.
    step "STEP B.1 — seed pack (the owner's choice: ${SEED_HOW})"
    _seed_out="$(WIZARD_LIBRARY_DB="${DB_PATH}" "${PY_SYS}" "$LITE_ENGINE" load-seed --file "$SEED_PACK" 2>&1)"; _seed_rc=$?
    if [ "$_seed_rc" -eq 0 ]; then
        say "  seed pack ${SEED_PLAN_VER}: $(printf '%s' "$_seed_out" | "${PY_SYS}" -B -c 'import json,sys
try:
    d = json.loads(sys.stdin.read().strip().splitlines()[-1])
    print("added %d new and %d changed entries (older revisions archived, never deleted: %d); a second run adds zero" % (len(d.get("new") or []), len(d.get("changed") or []), len(d.get("superseded_ids") or [])))
except Exception:
    print("loaded (its summary line could not be read)")' 2>/dev/null)."
        say "  this shape embeds nothing: they are keyword-recallable now; to embed them (with the brain's other unvectored memories) when you choose:"
        say "      CUDA_VISIBLE_DEVICES=${GPU_DEVICE} ${PY_SYS} ${BRAIN_DIR}/organs/embed_backfill.py ${DB_PATH}"
    else
        warn "the seed pack load you chose returned rc ${_seed_rc} (${_seed_out##*$'\n'}); the brain is as it was before the load. Re-run with --load-seed to try again."
    fi
elif [ "$SHAPE" = "ambient" ]; then
    # R1: an UPGRADE adds no memories to the brain it found BY ITSELF. The seed pack would arrive
    # with no vectors (this shape embeds nothing) and change the vector population the
    # principle leaves as found -- measured on the fixture: +66 unvectored. Adding the pack's
    # new entries is the owner's choice (seed_settle above; Round 3 G1, item 2).
    say "  seed pack NOT loaded: an upgrade adds no memories to an existing brain by itself (host state)."
    # M15: the seed loader is a per-memory anchor DELTA, so an already-seeded brain CAN receive the pack's new and
    # changed entries -- but not from this shape, by R1. Say exactly what the pack would add, READ-ONLY (--plan opens
    # the brain mode=ro and writes nothing), and the two commands that add and embed it when the owner chooses to.
    if [ "$DRY_RUN" != "1" ] && [ -f "$SEED_PACK" ] && [ -f "$LITE_ENGINE" ]; then
        _seed_plan="$(WIZARD_LIBRARY_DB="${DB_PATH}" "${PY_SYS}" "$LITE_ENGINE" load-seed --file "$SEED_PACK" --plan 2>/dev/null \
            | "${PY_SYS}" -c 'import json,sys
try:
    d = json.loads(sys.stdin.read().strip().splitlines()[-1])
    print("%d %d %s" % (len(d["new"]), len(d["changed"]), d.get("seed_pack_version") or "?"))
except Exception:
    print("")' 2>/dev/null)"
        if [ -n "$_seed_plan" ]; then
            read -r _sp_new _sp_chg _sp_ver <<< "$_seed_plan"
            if [ "$_sp_new" -eq 0 ] && [ "$_sp_chg" -eq 0 ]; then
                say "  seed pack ${_sp_ver}: this brain already holds every entry (checked read-only, per memory)."
            else
                say "  seed pack ${_sp_ver} has ${_sp_new} new and ${_sp_chg} changed entries this brain has not received (checked read-only, per memory) -- YOUR choice, not a failure. To add them: re-run with --load-seed (or answer y at a terminal), or now:"
                say "      WIZARD_LIBRARY_DB=${DB_PATH} ${PY_SYS} ${LITE_ENGINE} load-seed --file ${SEED_PACK}"
                say "      CUDA_VISIBLE_DEVICES=${GPU_DEVICE} ${PY_SYS} ${BRAIN_DIR}/organs/embed_backfill.py ${DB_PATH}"
                say "    (the first adds only those entries -- a changed entry's older memory is archived, never deleted -- and a second run adds zero; the second embeds them)"
            fi
        fi
    fi
elif [ "$DRY_RUN" = "1" ]; then                       # C2/M9: was ${DRY:-0}, always "0"
    say "  DRY would load the seed pack via claude_memory_lite.py load-seed"
elif [ -f "$SEED_PACK" ] && [ -f "$LITE_ENGINE" ]; then
    step "STEP B.1 — seed pack"
    # The lite engine has NO --db flag; it resolves WIZARD_LIBRARY_DB first
    # (claude_memory_lite.py:30,62). Passing --db exits 2 on unrecognized
    # arguments -- which is how this line was written the first time, and why it
    # is tested by execution before it ships rather than after.
    # ${DB_PATH}, not a re-spelling of the default: under --attach-db the brain is
    # NOT at ${BRAIN_DIR}/claude_memory.db, and a literal here would have seeded
    # (and created) a second, empty file beside the machinery.
    if WIZARD_LIBRARY_DB="${DB_PATH}" \
       "${PY_SYS}" "$LITE_ENGINE" load-seed --file "$SEED_PACK"; then
        say "  seed pack loaded"
    else
        say "  seed pack present but load returned non-zero -- continuing; the brain is usable, just emptier"
    fi
else
    [ -f "$SEED_PACK" ] || say "  no seed_pack.json in the package -- skipping (not an error)"
fi

# STEP C — TIER 1 ATTEMPT (OPTIONAL; GRACEFUL FALLBACK TO TIER 0)
#   Build a venv, install the VERSION-pinned semantic stack, pre-cache the
#   embedder + the reranker (Qwen3-Reranker-0.6B; bge under VOIDLOOM_RERANKER=bge -- and only where the
#   reranker gate allows it), verify SEMANTIC recall. Any failure -> Tier 0.
# ----------------------------------------------------------------------------
if [ "$SHAPE" = "ambient" ]; then
    step "STEP C — Tier 1 (ambient, ${EMBED_MODEL}-${EMBED_DIM}) through ${PY_SYS}: nothing to build"
else
    step "STEP C — Tier 1 (optional): venv + semantic stack (${EMBED_MODEL}-${EMBED_DIM})"
fi
TIER1_OK=0
TIER1_SKIP_REASON=""
VENV_DIR="${BRAIN_DIR}/venv"
VENV_PY="${VENV_DIR}/bin/python3"
REQ_FILE="${BRAIN_DIR}/install/requirements.txt"

probe_fact() {   # probe_fact JSON STEP KEY -- one fact of one step of a tier1_probe report ('' when absent)
    "$PY_SYS" - "$1" "$2" "$3" <<'PROBE_FACT_PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
for s in d.get("steps", []):
    if s.get("step") == sys.argv[2]:
        f = s.get("facts") or {}
        v = f.get(sys.argv[3])
        if sys.argv[3] == "error":
            v = s.get("error")
        print("" if v is None else v)
PROBE_FACT_PY
}
probe_tmp_ensure() { [ -n "$PROBE_TMP" ] && [ -d "$PROBE_TMP" ] && return 0; PROBE_TMP="$(mktemp -d 2>/dev/null)"; }
hd_probe_fail_detail() {   # bash 3.2 (macOS /bin/bash) cannot parse a heredoc inside $( ): the command and its heredoc live here, called as $(hd_probe_fail_detail) (round 2, B2)
    "$PY_SYS" - "$1" <<'PROBE_FAIL_PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:
    print("the probe's report is unreadable (%s)" % e); sys.exit(0)
for s in d.get("steps", []):
    if not s.get("ok"):
        print("%s: %s" % (s.get("step"), s.get("error") or s.get("stderr_tail"))); break
PROBE_FAIL_PY
}

run_probe_venv() {   # run_probe_venv STEPS OUT_JSON [--force-cpu] -- the venv's own python runs the LAID probe; rc 0 iff all passed
    local _fc="${3:-}"
    probe_tmp_ensure || { FAIL_DETAIL="no temporary directory"; return 1; }
    "$VENV_PY" -B "${BRAIN_DIR}/install/tier1_probe.py" run --db "$DB_PATH" --organs "${BRAIN_DIR}/organs" \
        --pin "${GPU_DEVICE:-}" --backend "$ACCEL_BACKEND" --reranker "$([ "$ACCEL_RERANK" = "1" ] && echo 1 || echo 0)" \
        --steps "$1" $_fc > "$2" 2>/dev/null
    local _rc=$?
    FAIL_DETAIL="$(hd_probe_fail_detail "$2")"
    return $_rc
}
T1_FLIPPED=0
t1_device_proof() {   # P6 (+ P7 when the gate is ON) on the plan's device; the MPS/ROCm/XPU self-protection on failure
    local _steps="P6" _why _was="$ACCEL_BACKEND"
    [ "$ACCEL_RERANK" = "1" ] && _steps="P6,P7"
    local _fc=""; [ "$FORCE_CPU" -eq 1 ] && _fc="--force-cpu"
    probe_tmp_ensure || { warn "no temporary directory for the device proof"; return 1; }
    if run_probe_venv "$_steps" "${PROBE_TMP}/p67.json" $_fc; then
        say "  P6 golden vectors on ${ACCEL_BACKEND} ($(probe_fact "${PROBE_TMP}/p67.json" P6 device)): every text agrees with the CPU reference (worst cosine $(probe_fact "${PROBE_TMP}/p67.json" P6 min_cosine), required $(probe_fact "${PROBE_TMP}/p67.json" P6 min_cosine_required)); top-3 identical.$([ "$ACCEL_RERANK" = "1" ] && echo " P7: the reranker ranks relevant above irrelevant on every fixture query.")"
        return 0
    fi
    _why="$FAIL_DETAIL"
    local _act="" _lab="" _cmt="" _ann="" _k _v
    while IFS="$(printf '\t')" read -r _k _v; do
        case "$_k" in action) _act="$_v" ;; label) _lab="$_v" ;; comment) _cmt="$_v" ;; announce) _ann="$_v" ;; esac
    done <<EOF_PROOF
$("$PY_SYS" -B "$ACCEL_PLAN_PY" on-proof-failure --backend "$_was" --reason "$_why" --owner-line "$(env_outside get WIZARD_ACCELERATOR 2>/dev/null)")
EOF_PROOF
    case "$_act" in
        flip)
            if accel_declare cpu "$_cmt"; then
                echo "[install] ${_ann}" >&2          # ONE line, this once (gpu-safety-002)
                T1_FLIPPED=1
                export WIZARD_ACCELERATOR=cpu
                FORCE_CPU=1
                accel_plan cpu                  # the CPU's plan: gate OFF (never on CPU-only), the CPU's channel words
                ACCEL_LABEL="$_lab"; ACCEL_FAILED_BACKEND="$_was"
                if run_probe_venv "P6" "${PROBE_TMP}/p67_cpu.json" --force-cpu; then
                    say "  P6 golden vectors on the CPU: every text agrees with the reference (worst cosine $(probe_fact "${PROBE_TMP}/p67_cpu.json" P6 min_cosine)); this brain embeds on the CPU."
                    return 0
                fi
                warn "P6 failed on the CPU as well (${FAIL_DETAIL}): Tier 1 is not verified on this machine."
                return 1
            fi
            warn "the ${_was} proof FAILED (${_why}) and WIZARD_ACCELERATOR=cpu could not be recorded (above): Tier 1 is not verified -- nothing is embedded on a device that failed its proof."
            ACCEL_LABEL="$_lab"; ACCEL_FAILED_BACKEND="$_was"
            return 1 ;;
        keep-owner)
            ACCEL_LABEL="$_lab"; ACCEL_FAILED_BACKEND="$_was"
            warn "$_ann"
            return 0 ;;
        *)
            warn "${_ann:-the ${_was} device failed its proof: ${_why}}."
            return 1 ;;
    esac
}
ACCEL_FAILED_BACKEND=""
tier1_upgrade_hint() {
    cat <<HINT
  ----------------------------------------------------------------
  Tier 0 is ACTIVE — your brain answers keyword recall now.
  The optional SEMANTIC upgrade (Tier 1, ${EMBED_MODEL}-${EMBED_DIM}) was not completed:
    reason: ${1:-unknown}
  To add semantic recall later, install Python >= 3.10, then re-run:
    bash $(basename "${BASH_SOURCE[0]}")
  or build the venv by hand:
    ${PY_SYS} -m venv '${VENV_DIR}'
    '${VENV_PY}' -m pip install -r '${REQ_FILE}'
    WIZARD_EMBED_MODEL=${EMBED_MODEL} WIZARD_LIBRARY_DB='${DB_PATH}' \\
      voidloom recall --query "voidloom" --top-k 3
  ----------------------------------------------------------------
HINT
}

# #12 (stranger test, 2026-10-13): --force-cpu chose the CPU EMBEDDER and still let PyPI hand
# the venv its DEFAULT torch, which on Linux is the CUDA build: a 5.8 GB venv (15 nvidia-*
# wheels + triton) on a run that had declared it would use no GPU. Worse than the bytes: on
# WSL2 that build SEGFAULTS (rc 139) on torch.cuda.is_available() whenever CUDA_VISIBLE_DEVICES
# selects no card ('' or -1, measured), so a CPU-only host could not load the embedder at all.
# install/requirements.txt has always documented the recipe that holds -- torch from the
# PyTorch CPU index FIRST, then -r requirements.txt (torch already satisfied); --force-cpu now
# runs it. On a venv an earlier run filled with the CUDA build, the build is SWAPPED in place
# (same version, +cpu) and the CUDA-only wheels nothing else needs are removed.
# macOS: PyPI's torch there has no CUDA, so there is nothing to choose.
TORCH_CPU_INDEX="${VOIDLOOM_TORCH_CPU_INDEX:-https://download.pytorch.org/whl/cpu}"
torch_build_in_venv() {   # prints "absent" | "cpu|cuda|rocm|xpu <version>" -- importing torch never initialises a device
    "$VENV_PY" - <<'T1_TORCH_BUILD_PY' 2>/dev/null || echo "unreadable"
import importlib.metadata as md
try:
    v = md.version("torch")
except md.PackageNotFoundError:
    print("absent"); raise SystemExit(0)
import torch
# the wheel's local version names its channel (+rocm7.2, +xpu, +cpu, +cu128); PyPI's Linux CUDA wheel carries none,
# so the build attributes decide there: torch.version.hip on a ROCm build, torch.version.cuda on a CUDA one
if "+rocm" in v or getattr(torch.version, "hip", None):
    b = "rocm"
elif "+xpu" in v or getattr(torch.version, "xpu", None):
    b = "xpu"
elif getattr(torch.version, "cuda", None):
    b = "cuda"
else:
    b = "cpu"
print(b + " " + v)
T1_TORCH_BUILD_PY
}
torch_into_venv() {   # the plan's channel: the venv's torch is the build for ACCEL_BACKEND, or this returns non-zero (said)
    local _b _want="$ACCEL_BUILD"
    case "$_want" in
        cpu)  [ "$OS" = "Darwin" ] && return 0; cpu_torch_into_venv; return $? ;;
        cuda) return 0 ;;                         # PyPI's default torch on Linux IS the CUDA build (requirements below)
        macos)
            [ "$ACCEL_SPEC" = "torch" ] && return 0   # Apple Silicon: PyPI's torch, via the requirements below
            say "  torch for an Intel Mac: installing ${ACCEL_SPEC} (the last x86_64 macOS torch, and the numpy it was built against)..."
            # shellcheck disable=SC2086
            "$VENV_PY" -m pip install $ACCEL_SPEC >/dev/null 2>&1 \
                || { warn "pip could not install ${ACCEL_SPEC} for this Intel Mac."; return 1; }
            return 0 ;;
        rocm|xpu)
            _b="$(torch_build_in_venv)"
            case "$_b" in
                "$_want "*) say "  the venv already holds the ${_want} build of torch (${_b#* })."; return 0 ;;
                absent) say "  installing the ${_want} build of torch from ${ACCEL_INDEX} before the semantic stack..."
                        "$VENV_PY" -m pip install --index-url "$ACCEL_INDEX" torch >/dev/null 2>&1 \
                            || { warn "pip could not install the ${_want} build of torch from ${ACCEL_INDEX}."; return 1; } ;;
                *)      say "  this venv holds the ${_b%% *} build of torch (${_b#* }); replacing it with the ${_want} build from ${ACCEL_INDEX}..."
                        "$VENV_PY" -m pip install --force-reinstall --no-deps --index-url "$ACCEL_INDEX" torch >/dev/null 2>&1 \
                            || { warn "pip could not replace torch with the ${_want} build from ${ACCEL_INDEX}; the ${_b%% *} build stays."; return 1; } ;;
            esac
            _b="$(torch_build_in_venv)"
            case "$_b" in
                "$_want "*) say "  torch in the venv: the ${_want} build (${_b#* })." ;;
                *) warn "the venv's torch is not the ${_want} build after the install (${_b})."; return 1 ;;
            esac ;;
        *) warn "the accelerator plan names no torch build ('${_want}')."; return 1 ;;
    esac
}
hd_t1_orphans() {   # bash 3.2 (macOS /bin/bash) cannot parse a heredoc inside $( ): the command and its heredoc live here, called as $(hd_t1_orphans) (round 2, B2)
    "$VENV_PY" - <<'T1_ORPHANS_PY' 2>/dev/null
import importlib.metadata as md, re
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name as cn
dists = {}
for d in md.distributions():
    n = d.metadata.get("Name")
    if n:
        dists[cn(n)] = d
cand = {n for n in dists if n.startswith(("nvidia-", "cuda-")) or n == "triton"}
needed = set()
for n, d in dists.items():
    if n in cand:
        continue
    for r in d.requires or []:
        try:
            rq = Requirement(r)
        except Exception:
            continue
        if rq.marker is not None and not rq.marker.evaluate({"extra": ""}):
            continue
        needed.add(cn(rq.name))
# a candidate needed by a needed candidate stays too
grew = True
while grew:
    grew = False
    for n in list(cand & needed):
        for r in dists[n].requires or []:
            try:
                rq = Requirement(r)
            except Exception:
                continue
            if rq.marker is not None and not rq.marker.evaluate({"extra": ""}):
                continue
            m = cn(rq.name)
            if m not in needed:
                needed.add(m); grew = True
print(" ".join(sorted(cand - needed)))
T1_ORPHANS_PY
}

cpu_torch_into_venv() {   # --force-cpu: the venv's torch is the CPU build, or this returns non-zero (said)
    [ "$OS" = "Darwin" ] && { say "  --force-cpu on macOS: PyPI's torch carries no CUDA; nothing to choose."; return 0; }
    local _b _v
    _b="$(torch_build_in_venv)"
    case "$_b" in
        cpu*)
            say "  --force-cpu: the venv already holds the CPU build of torch (${_b#cpu })."
            return 0 ;;
        absent)
            say "  --force-cpu: installing the CPU build of torch from ${TORCH_CPU_INDEX} before the semantic stack..."
            "$VENV_PY" -m pip install --index-url "$TORCH_CPU_INDEX" torch >/dev/null 2>&1 \
                || { warn "pip could not install the CPU build of torch from ${TORCH_CPU_INDEX}."; return 1; } ;;
        cuda*)
            _v="${_b#cuda }"; _v="${_v%%+*}"
            say "  --force-cpu: this venv holds the CUDA build of torch (${_b#cuda }) from an earlier run; swapping it for the CPU build of the same version (${_v}+cpu)..."
            { "$VENV_PY" -m pip install --force-reinstall --no-deps --index-url "$TORCH_CPU_INDEX" "torch==${_v}+cpu" >/dev/null 2>&1 \
              || "$VENV_PY" -m pip install --force-reinstall --no-deps --index-url "$TORCH_CPU_INDEX" torch >/dev/null 2>&1; } \
                || { warn "pip could not swap torch for the CPU build from ${TORCH_CPU_INDEX}; the CUDA build stays."; return 1; }
            # The CUDA-only wheels (nvidia-*, cuda-*, triton) that NOTHING still installed requires.
            local _orphans
            _orphans="$(hd_t1_orphans)"
            if [ -n "$_orphans" ]; then
                # shellcheck disable=SC2086
                "$VENV_PY" -m pip uninstall -y $_orphans >/dev/null 2>&1 \
                    && say "  removed $(printf '%s\n' $_orphans | wc -l | tr -d ' ') CUDA-only wheel(s) nothing else needs: ${_orphans}" \
                    || warn "could not remove the CUDA-only wheels (${_orphans}); they are unused, and take space."
            fi ;;
        *)
            warn "could not read which torch build the venv holds (${_b}); installing the CPU build from ${TORCH_CPU_INDEX}."
            "$VENV_PY" -m pip install --index-url "$TORCH_CPU_INDEX" torch >/dev/null 2>&1 \
                || { warn "pip could not install the CPU build of torch from ${TORCH_CPU_INDEX}."; return 1; } ;;
    esac
    _b="$(torch_build_in_venv)"
    case "$_b" in
        cpu*) say "  torch in the venv: the CPU build (${_b#cpu })." ;;
        *)    warn "--force-cpu: the venv's torch is not the CPU build after the install (${_b})."; return 1 ;;
    esac
}
pip_expect_line() {   # ONE line, before pip starts, on what it will cost (#12)
    if [ "$ACCEL_BUILD" = "rocm" ] || [ "$ACCEL_BUILD" = "xpu" ]; then
        printf '%s' "pip next: the ${ACCEL_BUILD} build of torch from ${ACCEL_INDEX} + the semantic stack in ${VENV_DIR} -- its size is NOT measured (no ${ACCEL_BUILD} host has run this installer; label ${ACCEL_LABEL}). Models are fetched after this (the embedder; the reranker only when its gate is ON)."
    elif [ "$ACCEL_BUILD" = "macos" ] && [ "$ACCEL_SPEC" != "torch" ]; then
        printf '%s' "pip next: torch 2.2.2 for an Intel Mac (CPU) + the semantic stack -- a few minutes on a fresh venv. Models are fetched after this (the embedder only: the reranker is never on CPU-only)."
    elif [ "$FORCE_CPU" -eq 1 ] && [ "$OS" != "Darwin" ]; then
        printf '%s' "pip next: the CPU build of torch + the semantic stack -- expect about 1.5 GB in ${VENV_DIR} and a few minutes on a fresh venv (CUDA build: about 6 GB; both measured 2026-10-13 on Linux x86_64). Models are fetched after this (~1.8 GB, once)."
    elif [ "$OS" = "Linux" ]; then
        printf '%s' "pip next: the semantic stack with PyPI's default torch, which on Linux is the CUDA build -- expect about 6 GB in ${VENV_DIR} and 5-15 minutes on a fresh venv (measured 2026-10-13 on Linux x86_64; --force-cpu installs the CPU build instead, about 1.5 GB). Models are fetched after this (~1.8 GB, once)."
    else
        printf '%s' "pip next: the semantic stack -- a few minutes on a fresh venv. Models are fetched after this (~1.8 GB, once)."
    fi
}

hd_cuda_ok() {   # bash 3.2 (macOS /bin/bash) cannot parse a heredoc inside $( ): the command and its heredoc live here, called as $(hd_cuda_ok) (round 2, B2)
    VOIDLOOM_ORGANS="${BRAIN_DIR}/organs" "$VENV_PY" - <<'PYEOF'
import os, sys
try:
    sys.path.insert(0, os.environ["VOIDLOOM_ORGANS"])
    import voidloom_device
    import torch
    print("yes" if voidloom_device.cuda_available(torch) else "no")
except Exception:
    print("no")
PYEOF
}

hd_t1_declare() {   # bash 3.2 (macOS /bin/bash) cannot parse a heredoc inside $( ): the command and its heredoc live here, called as $(hd_t1_declare) (round 2, B2)
    EMBED_TABLE="$EMBED_TABLE" EMBED_DIM="$EMBED_DIM" EMBED_REPO="$EMBED_REPO" \
        ENGINE_REGISTRY="$ENGINE_REGISTRY" "$VENV_PY" - "$DB_PATH" <<'T1_DECLARE_PY'
import os, sqlite3, sys
db = sys.argv[1]
tab, dim, repo = os.environ["EMBED_TABLE"], int(os.environ["EMBED_DIM"]), os.environ["EMBED_REPO"]


def engine_tiers():
    """{table: (dim, repo)} from ENGINE_REGISTRY -- the output of install.sh's engine_registry(), THE ONE READER of the
    engine's EMBED_TIERS (iteration 5: this block used to carry its own AST reader beside it)."""
    out = {}
    for line in os.environ.get("ENGINE_REGISTRY", "").splitlines():
        f = line.split("\t")
        if len(f) == 4 and f[2].isdigit():
            out[f[1]] = (int(f[2]), f[3])
    if not out:
        raise ValueError("ENGINE_REGISTRY is empty")
    return out


try:
    _reg = engine_tiers()
except Exception as e:
    print("ERROR\tthe engine's tier registry (EMBED_TIERS) could not be read (%s: %s); nothing was changed"
          % (type(e).__name__, e))
    sys.exit(0)
KNOWN = {t: d for t, (d, _r) in _reg.items()}        # one table per model -- the bijection the engine keeps
REPO_OF = {t: r for t, (_d, r) in _reg.items()}
try:
    import sqlite_vec
    c = sqlite3.connect(db, timeout=60.0)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    row = c.execute("SELECT value FROM metadata WHERE key='active_embedding_table'").fetchone()
    declared = (str(row[0]).strip() if row and row[0] is not None else "")
    if declared and declared not in KNOWN:
        print("ERROR\tthe brain declares an unknown embedding table %r; nothing was changed" % declared)
        sys.exit(0)
    action = "kept"
    if not declared:
        # Iteration 2 (B1 residual): an undeclared brain may still HOLD vectors (metadata lost, a hand-built brain).
        # The vectors decide, not this run's resolution: exactly one known table with rows is the brain's tier
        # ("adopted", its own model and width); several with rows and this run's among them -> this run's; several
        # without it -> refuse (which space is live is the owner's call); none -> this run's tier, as before.
        REPO = REPO_OF
        held = []
        for t in KNOWN:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone() is not None:
                if c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] > 0:
                    held.append(t)
        if len(held) == 1:
            pick, action = held[0], ("declared" if held[0] == tab else "adopted")
        elif len(held) > 1 and tab not in held:
            print("ERROR\tthe brain declares no tier and %s all hold vectors; which one is live is the owner's "
                  "call (set metadata.active_embedding_table), so nothing was changed" % ", ".join(held))
            sys.exit(0)
        else:
            pick, action = tab, "declared"
        for k, v in (("active_embedding_table", pick), ("active_embedding_model", REPO[pick] if pick != tab else repo),
                     ("active_embedding_dim", str(KNOWN[pick]))):
            c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (k, v))
        declared = pick
    made = []
    for t in dict.fromkeys((declared, "memory_embeddings")):
        if c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone() is None:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS %s USING vec0("
                      "memory_id INTEGER PRIMARY KEY, embedding float[%d])" % (t, KNOWN[t]))
            made.append(t)
    z = c.execute("SELECT value FROM metadata WHERE key='tier0_only'").fetchone()
    t0 = (str(z[0]).strip() if z and z[0] is not None else "")
    if t0.lower() not in ("", "0", "false", "no", "off"):
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', '0')")
    else:
        t0 = ""
    c.commit()
    c.close()
    print("OK\t%s\t%s\t%s\t%s" % (action, declared, ",".join(made) or "-", t0 or "-"))
except Exception as e:
    print("ERROR\t%s: %s" % (type(e).__name__, e))
T1_DECLARE_PY
}

hd_t1_verify() {   # bash 3.2 (macOS /bin/bash) cannot parse a heredoc inside $( ): the command and its heredoc live here, called as $(hd_t1_verify) (round 2, B2)
    WIZARD_EMBED_MODEL="$EMBED_MODEL" WIZARD_LIBRARY_DB="$DB_PATH" \
        WIZARD_LIBRARY_HOME="$BRAIN_DIR" BRAIN_DIR="$BRAIN_DIR" "$VENV_PY" - <<'T1_VERIFY_PY'
import json, os, sqlite3, subprocess, sys, urllib.request   # escaped URI (iteration 4, LOW 2)
db = os.environ["WIZARD_LIBRARY_DB"]
brain = os.environ["BRAIN_DIR"]          # the dir that CONTAINS the laid voidloom/ package

# The active tier's table is a sqlite-vec VIRTUAL table, so counting it needs the
# extension loaded exactly as the engine loads it; the venv has it (pip just put it there).
vectors, tab, count_err = None, None, None
try:
    c = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(os.path.abspath(db)), uri=True)
    try:
        import sqlite_vec
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.enable_load_extension(False)
    except Exception:
        pass
    row = c.execute("SELECT value FROM metadata WHERE key='active_embedding_table'").fetchone()
    tab = row[0] if row else None
    if tab:
        try:
            vectors = c.execute(f"SELECT COUNT(*) FROM {tab}").fetchone()[0]
        except Exception as e:
            count_err = str(e)
    c.close()
except Exception as e:
    count_err = str(e)

if not tab:
    print("  Tier 1 NOT VERIFIED: the brain declares no active_embedding_table, so there is no "
          "semantic tier to verify (keyword results prove nothing about it).", file=sys.stderr)
    sys.exit(1)
if vectors == 0:
    print(f"  Tier 1 NOT VERIFIED: active embedding table {tab} holds 0 vectors -- "
          f"semantic recall cannot return anything the keyword arm would not.",
          file=sys.stderr)
    sys.exit(1)

# Through the facade, with THIS interpreter -- the one the launcher runs on an install
# that built a venv (voidloom/voidloom reads INSTALL_RECORD).
env = dict(os.environ)
env["PYTHONPATH"] = brain + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
p = subprocess.run([sys.executable, "-c",
                    "import sys, runpy; sys.path.pop(0); "
                    "runpy.run_module('voidloom', run_name='__main__', alter_sys=True)",
                    "recall", "--query", "voidloom brain memory", "--top-k", "3", "--json"],
                   capture_output=True, text=True, env=env, cwd=brain)
try:
    d = json.loads(p.stdout[p.stdout.index("{"):])
except Exception:
    print("  Tier 1 NOT VERIFIED: `voidloom recall --json` did not return a readable envelope "
          f"(rc {p.returncode}): {p.stderr.strip()[-400:]}", file=sys.stderr)
    sys.exit(1)
data = d.get("data") if isinstance(d.get("data"), dict) else d
_res = data.get("results") or []
n = data.get("result_count") if isinstance(data.get("result_count"), int) else len(_res)
tier = data.get("tier")
if data.get("status") != "ok" or n == 0:
    print(f"  Tier 1 NOT VERIFIED: recall returned status={data.get('status')} with {n} results.",
          file=sys.stderr)
    sys.exit(1)
if tier != 1 or data.get("degraded"):
    deg = data.get("degraded")
    why = deg.get("tier1_error") if isinstance(deg, dict) else None
    print(f"  Tier 1 NOT VERIFIED: recall answered at tier {tier} (mode {data.get('mode')})"
          f"{': ' + str(why) if why else ''}.", file=sys.stderr)
    sys.exit(1)
# SAY WHAT WAS OBSERVED, and no more. The weaker sentence when the count was unreadable.
if vectors:
    print(f"VERIFIED tier=1 mode={data.get('mode')} vectors={vectors} results={n} table={tab}")
else:
    print(f"PARTIAL tier=1 mode={data.get('mode')} results={n} table={tab} count_unavailable={count_err}")
T1_VERIFY_PY
}

if [ "$SHAPE" = "ambient" ]; then
    # R1: NO venv, NO pip, NO model pre-cache, NO backfill, tier0_only NEVER written. The
    # interpreter was proven by execution before Step A and again after the migrations.
    # Amendment C4: this shape embeds NOTHING. It derives and prints how much of the brain
    # has no vector, and says whose decision that is.
    say "  no venv, no pip, no model pre-cache, no re-embedding; tier0_only is not written."
    if [ "$DRY_RUN" -eq 1 ]; then
        say "  DRY: would count the brain's vectors through ${PY_SYS} and print unvectored / active."
    else
        run_probe "counts" "${PROBE_TMP}/counts_after.json" \
            || die "could not count the brain's vectors through ${PY_SYS} after the migrations: ${FAIL_DETAIL}"
        "$PY_SYS" - "${PROBE_TMP}/counts_before.json" "${PROBE_TMP}/counts_after.json" <<'PYEOF'
import json, sys
def facts(p):
    return json.load(open(p, encoding="utf-8"))["steps"][0]["facts"]
b, a = facts(sys.argv[1]), facts(sys.argv[2])
print("[install]   unvectored / active: %d / %d in %s (%d vectors, %d of them for no memory); before this run: %d / %d"
      % (a["unvectored_active"], a["active"], a["table"], a["vectors"], a["orphan_vectors"],
         b["unvectored_active"], b["active"]))
print("[install]   re-embedding them is the owner's decision (awaits the embedder-provenance lock); this shape embeds nothing.")
PYEOF
        TIER1_OK=1
    fi
elif [ "$TIER0_ONLY" -eq 1 ]; then
    TIER1_SKIP_REASON="--tier0-only requested"
    say "  skipping Tier 1 ($TIER1_SKIP_REASON)"; tier1_upgrade_hint "$TIER1_SKIP_REASON"
    # CRUCIBLE-3 D7-F2 / D8-F2 / U03-1: --tier0-only used to scope the INSTALLER
    # and nothing else. The runtime picked its tier by TRYING the import, so on a
    # host whose ambient interpreter has torch the first documented command
    # downloaded 1.7 GB and executed remote Python -- after this installer had
    # printed "Tier 1 ... not completed". Record the choice where the runtime will
    # meet it, so the flag binds the product and not just this script.
    if [ "$DRY_RUN" -eq 1 ]; then
        say "  DRY: would record tier0_only=1 in the brain so the RUNTIME honours it too"
    else
        run_argv "${PY_SYS}" -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('INSERT OR REPLACE INTO metadata (key,value) VALUES (?,?)',('tier0_only','1')); c.commit(); c.close()" "${DB_PATH}" \
            || die "could not record tier0_only=1 in ${DB_PATH}"
        say "  recorded tier0_only=1 in the brain — the runtime will not resolve or fetch a Tier-1 model (override: VOIDLOOM_TIER0_ONLY=0)"
    fi
elif [ -n "$ACCEL_REFUSAL" ]; then
    # the plan found no torch for this machine as it is (an Intel Mac on Python 3.13): Tier 0 stays, said, with the fix
    TIER1_SKIP_REASON="$ACCEL_REFUSAL"
    if [ "$DRY_RUN" -eq 1 ]; then
        say "  DRY: would skip Tier 1: ${ACCEL_REFUSAL}"
    else
        say "  skipping Tier 1: ${ACCEL_REFUSAL}"; tier1_upgrade_hint "$TIER1_SKIP_REASON"
    fi
elif [ "$DRY_RUN" -eq 1 ]; then
    if [ "$PRECACHE_FALLBACK" = "1" ]; then _rr_dry=" + ${RERANKER_FALLBACK_REPO}"; else _rr_dry=""; fi
    say "  DRY: would build venv, install torch (${ACCEL_SPEC}) from $([ -n "$ACCEL_INDEX" ] && echo "${ACCEL_INDEX}" || echo "PyPI") for ${ACCEL_BACKEND}, then pip install the version-pinned stack,"
    if [ "$ACCEL_RERANK" = "1" ]; then
        say "       pre-cache ${EMBED_REPO} + ${RERANKER_REPO}${_rr_dry} (backend ${RERANKER_BACKEND}; the reranker gate is ON: ${ACCEL_RERANK_WHY}),"
    else
        say "       pre-cache ${EMBED_REPO} only (the reranker gate is OFF: ${ACCEL_RERANK_WHY}; ${RERANKER_REPO} would not be downloaded),"
    fi
    say "       prove the ${ACCEL_BACKEND} device against the golden vectors (P6) before anything is embedded,"
    say "       EMBED the seed pack into ${EMBED_TABLE} via organs/embed_backfill.py, then verify ${EMBED_MODEL} semantic recall."
    gpu_present && say "  DRY: (GPU) would verify torch.cuda.is_available() and pin CUDA_VISIBLE_DEVICES=${GPU_DEVICE:-<resolved at run time>}."
else
    TIER1_PY="$PY_SYS"
    is_system_python "$TIER1_PY" && warn "the chosen python is the system python ($TIER1_PY) — venv/pip can be fragile; Tier 0 already works."
    py_ge_310 "$TIER1_PY" || warn "python $(py_version "$TIER1_PY") < 3.10; the semantic stack prefers >= 3.10."

    if "$TIER1_PY" -m venv "$VENV_DIR" 2>/dev/null && [ -x "$VENV_PY" ]; then
        say "  venv created at ${VENV_DIR}"
        # CRUCIBLE-3 D05-F7: four sites here said "HASH-pinned", and
        # ${REQ_FILE} carries ZERO `--hash=` entries -- `grep -c sha256:` is 0.
        # The hashes were deliberately moved to requirements.hashes.txt because
        # pip turns on --require-hashes for the ENTIRE transitive tree the moment
        # any requirement is hashed, which made Tier 1 uninstallable; the file
        # that records that is scrupulously honest and the installer's narration
        # was simply never updated to follow. It printed "hash-pinned" to the
        # user's terminal on every dry run.
        #
        # What this actually does: THREE == VERSION PINS, no hashes. The top-level
        # supply chain (the packages a human chose) is version-locked; transitive
        # integrity relies on PyPI's TLS plus those pinned roots. A fully
        # hash-locked install is MODE B in install/requirements.txt, generated
        # per-platform with `uv pip compile --generate-hashes`.
        # Prefer the pinned requirements; fall back to a loose install only if absent.
        # R-7 (EW4-06): the arguments are an ARRAY, never a string re-read by `eval` -- a path holding a ' (or a space
        # the eval re-split) made the old `eval "'$VENV_PY' -m pip install -r '${REQ_FILE}'"` fail, and the step then
        # reported "pip install of the semantic stack failed": a false failure about a path, not about pip
        if [ -f "$REQ_FILE" ]; then PIP_ARGS=(-r "$REQ_FILE"); else PIP_ARGS=(sentence-transformers sqlite_vec einops); fi
        say "  $(pip_expect_line)"
        if "$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1 \
           && torch_into_venv \
           && "$VENV_PY" -m pip install "${PIP_ARGS[@]}" >/dev/null 2>&1; then
            say "  semantic stack installed (sentence-transformers + sqlite_vec + einops)"
            case "$ACCEL_BUILD" in
                cpu|rocm|xpu)
                    if [ "$OS" != "Darwin" ]; then
                        case "$(torch_build_in_venv)" in
                            "$ACCEL_BUILD "*) ;;
                            *) warn "after the semantic stack the venv's torch is not the ${ACCEL_BUILD} build ($(torch_build_in_venv)); a requirement replaced it." ;;
                        esac
                    fi ;;
            esac
            export WIZARD_EMBED_MODEL="$EMBED_MODEL"
            export WIZARD_LIBRARY_DB="$DB_PATH"
            export WIZARD_LIBRARY_HOME="$BRAIN_DIR"
            export PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring"

            # C.cuda-gate — when a GPU is present, REQUIRE CUDA; do not silently fall to CPU.
            if gpu_present; then
                # R-6 (EW4-05): torch is asked only when CUDA_VISIBLE_DEVICES selects a device (organs/voidloom_device,
                # laid by Step A): under an empty/device-less value torch.cuda.is_available() is a cuInit that SEGFAULTS
                # the interpreter on WSL2 -- an rc 139 read as "no CUDA" by accident. Now it is "no", said, never a crash.
                CUDA_OK="$(hd_cuda_ok)"
                if [ "$CUDA_OK" != "yes" ]; then
                    TORCH_VER="$("$VENV_PY" -c 'import torch; print(torch.__version__)' 2>/dev/null || echo 'unknown')"
                    cat >&2 <<GATE
[install] WARNING: a GPU was DETECTED but CUDA is NOT enabled for PyTorch (torch ${TORCH_VER}).
[install] Semantic recall will run on CPU (slower). To use the GPU, install the matching
[install] torch CUDA wheel into this venv, e.g.:
[install]   '${VENV_PY}' -m pip install --force-reinstall torch \\
[install]     --index-url https://download.pytorch.org/whl/cu124
[install] then re-run. (Pass --force-cpu to silence this and pin CPU/nomic.)
GATE
                else
                    say "  CUDA enablement VERIFIED — torch.cuda.is_available() == True on device ${GPU_DEVICE:-(cpu)}."
                fi
            fi

            # C.cache — pre-cache the embedder + THE RERANKER THIS INSTALL WILL LOAD.
            #
            # snapshot_download, not CrossEncoder(): the default backend is a
            # Qwen3ForCausalLM and CrossEncoder cannot instantiate it -- a second,
            # independent reason the old line could only ever warm the fallback.
            # The snapshot is the same cache both loaders read, so warming it
            # serves whichever backend runs.
            # The reranker gate (Drew's ruling, 2026-10-14): a machine the gate turns OFF downloads no reranker at all
            # ("not even installed"); the reason is said, with what to run if the machine changes.
            if [ "$ACCEL_RERANK" = "1" ]; then
                say "  pre-caching the ${EMBED_MODEL}-${EMBED_DIM} embedder + the ${RERANKER_BACKEND} reranker ${RERANKER_REPO} (one-time download)..."
                _pc_rr="$RERANKER_REPO"; _pc_fb="$PRECACHE_FALLBACK"
            else
                say "  pre-caching the ${EMBED_MODEL}-${EMBED_DIM} embedder only (one-time download); the reranker is OFF on this machine -- ${ACCEL_RERANK_WHY} -- so ${RERANKER_REPO} is NOT downloaded. If this machine gains the RAM or an accelerator, re-run install.sh."
                _pc_rr=""; _pc_fb="0"
            fi
            WIZARD_EMBED_MODEL="$EMBED_MODEL" EMBED_REPO="$EMBED_REPO" \
            RERANKER_REPO="$_pc_rr" RERANKER_FALLBACK_REPO="$RERANKER_FALLBACK_REPO" \
            PRECACHE_FALLBACK="$_pc_fb" \
            "$VENV_PY" - <<'PYEOF' || warn "model pre-cache hit a snag; recall will lazy-download on first use."
import os


def _bytes_of(path):
    """Bytes ACTUALLY on disk under a cache dir -- derived, never typed.

    The guide carried "~1.4GB total" as a fact about a set that did not include
    the model the first recall loads. A figure the installer measures cannot be
    wrong about the install it just performed."""
    # FOLLOW the symlinks. A hub snapshot dir is entirely symlinks into blobs/,
    # so follow_symlinks=False measures the LINKS: the first version of this
    # function reported 779 bytes for a 1.2 GB model, which is what a size probe
    # written in the moment of lowest perceived difficulty looks like.
    # Dedupe by (st_dev, st_ino) so a blob shared by two entries counts once.
    total = 0
    seen = set()
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                st = os.stat(os.path.join(root, fn))   # follows
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key)
            total += st.st_size
    return total


try:
    from sentence_transformers import SentenceTransformer
    from huggingface_hub import snapshot_download
    SentenceTransformer(os.environ["EMBED_REPO"], trust_remote_code=True)
    print("  cached " + os.environ["EMBED_REPO"] + " (embedder)")
    wanted = []
    if os.environ.get("RERANKER_REPO"):         # empty: the reranker gate is OFF on this machine (nothing fetched)
        wanted.append((os.environ["RERANKER_REPO"], "reranker (this install loads this one)"))
    if wanted and os.environ.get("PRECACHE_FALLBACK") == "1":
        wanted.append((os.environ["RERANKER_FALLBACK_REPO"], "reranker fallback"))
    total = 0
    for repo, what in wanted:
        n = _bytes_of(snapshot_download(repo))
        total += n
        print("  cached %s (%s): %s bytes" % (repo, what, format(n, ",")))
    if wanted:
        print("  reranker bytes on disk: %s" % format(total, ","))
        print("  embedder + reranker cached.")
    else:
        print("  embedder cached (no reranker: the gate is off).")
except Exception as e:
    print("  pre-cache note: %s" % e)
PYEOF

            # C.embed — GIVE THE SEED PACK ITS VECTORS. Drew's ruling, 2026-09-17:
            # EMBEDDING IS PART OF INSTALLATION.
            #
            # CRUCIBLE-3 seat 221-F2: a fresh install loaded 66 seed memories and
            # held ZERO vectors -- 100% of a new brain's content keyword-only --
            # and no shipped verb could fix it, because the only backfill that
            # existed was hardcoded to Jina. Measured on a clone of the tracked
            # package: init -> load-seed -> `memories: 66  vectors: 0`, under the
            # seed loader's own note that "Tier 1 (full CLI) can re-embed them",
            # which was a promise with nothing behind it.
            #
            # The alternative was to embed lazily at first recall. Rejected, and
            # the reason is about WHEN THE COST LANDS rather than whether it is
            # paid: deferring does not save the work, it moves it into the user's
            # first moment of need -- and they asked for recall precisely BECAUSE
            # they are mid-task. Install is already a go-and-make-tea operation
            # with a progress narration. The cost is invisible here and
            # intolerable there.
            #
            # BEFORE C.verify, deliberately: the verify below prints the weaker
            # "PARTIAL" sentence when the tier's table holds no vectors. Embedding
            # first means the installer both DOES the work and PROVES it, in that
            # order.
            #
            # embed_backfill never raises and never guesses: a host with no
            # encoder gets rc 3 and a sentence saying the brain is keyword-only,
            # which is a true statement about a WORKING brain. So this is a `say`,
            # not a `die`.
            # C.declare — A BRAIN BORN AT TIER 0 DECLARES NO TIER (stranger test, 2026-10-13, B1).
            #
            # `--init-blank --tier0-only`, or a Tier-0 fallback because python3-venv was missing,
            # builds the brain with schema_migrator on a stdlib python: the vec0 migrations are
            # UNMEASURABLE there, and the three metadata keys the full engine's `init` records
            # (organs/claude_memory.py _cmd_init_body: active_embedding_table / _model / _dim)
            # are never written. Re-running this installer after venv was fixed then built the
            # venv, cached the models and printed "Tier 1 VERIFIED" and "ACTIVE" -- while
            # embed_backfill REFUSED (EmbedTierUnresolved, rc 3) four lines above, every
            # recall stayed keyword-only, and any re-embedding update died on
            # "no such table: memory_embeddings". Measured on a 1,920-memory Tier-0 brain.
            #
            # So the Tier-1 path declares what `init` would have declared, for the tier this
            # run resolved and cached, ONLY where the brain declares nothing (a declared tier
            # is never repointed: same dimension is not the same space), and creates the
            # declared tier's vec table and the nomic fallback table when they are missing --
            # exactly the two tables `init` creates.
            #
            # And an earlier `--tier0-only` left tier0_only=1, which the RUNTIME honours
            # (voidloom/_facade.py) -- so a Tier 1 this run proves would still answer at
            # Tier 0. This run was asked for Tier 1 (no --tier0-only), which is the upgrade
            # path the Tier-0 hint itself prints ("re-run: bash install.sh"), so the flag is
            # cleared here and PUT BACK if Tier 1 does not verify below.
            # THE VECTOR MIGRATIONS A TIER-0 BIRTH SKIPPED (public-release clean room, 2026-10-27). On a machine whose
            # python3 cannot import sqlite_vec -- a stock Ubuntu / GitHub runner -- STEP B builds the brain with
            # schema_migrator and reports the vec0 migrations (013, 031) UNMEASURABLE. This step then built a venv
            # that CAN measure them, and never went back: the brain shipped without memory_embeddings_v2/_v3, the
            # installer's own gate passed (it read the schema through the same blind interpreter), and the README's
            # self-check failed "schema complete" the moment it ran through the venv. Re-apply with the venv: an
            # additive, idempotent pass that executes only what is missing (a no-op on a brain that has it all).
            if "$VENV_PY" "${BRAIN_DIR}/organs/schema_migrator.py" apply --db "$DB_PATH" >/dev/null 2>&1; then
                say "  vector-table migrations checked with the venv's sqlite_vec (applied any a Tier-0 birth skipped)."
            else
                warn "re-applying the vector-table migrations with ${VENV_PY} failed; run: WIZARD_LIBRARY_DB='${DB_PATH}' '${VENV_PY}' '${BRAIN_DIR}/organs/schema_migrator.py' apply --db '${DB_PATH}'"
            fi

            T1_DECL_OK=0; T1_T0_PREV=""
            _decl_out="$(hd_t1_declare)"
            case "$_decl_out" in
                OK*)
                    IFS="$(printf '\t')" read -r _ _d_act _d_tab _d_made _d_t0 <<EOF_DECL
$_decl_out
EOF_DECL
                    T1_DECL_OK=1
                    if [ "$_d_act" = "declared" ]; then
                        say "  the brain declared no embedding tier (it was born at Tier 0); declared ${EMBED_MODEL}-${EMBED_DIM}: active_embedding_table=${_d_tab}, active_embedding_model=${EMBED_REPO}, active_embedding_dim=${EMBED_DIM}."
                    elif [ "$_d_act" = "adopted" ]; then
                        say "  the brain declared no embedding tier, but its vectors are all in ${_d_tab}: declared THAT table (its own model and width), not this run's ${EMBED_TABLE}."
                        warn "this run resolved ${EMBED_MODEL} (${EMBED_TABLE}) and cached its model; the brain's tier is ${_d_tab}, so embedding writes ${_d_tab} with that table's model (fetched on first use)."
                    else
                        say "  the brain already declares its embedding tier (${_d_tab}); kept as declared."
                        [ "$_d_tab" = "$EMBED_TABLE" ] || warn "the brain declares ${_d_tab}, and this run resolved ${EMBED_TABLE} (${EMBED_MODEL}); the brain's declaration wins -- embedding writes ${_d_tab}."
                    fi
                    [ "$_d_made" = "-" ] || say "  created the missing vector table(s): ${_d_made}."
                    if [ "$_d_t0" != "-" ]; then
                        T1_T0_PREV="$_d_t0"
                        say "  the brain recorded tier0_only=${_d_t0} (an earlier --tier0-only run); this run was asked for Tier 1, so it is cleared now -- and put back if Tier 1 does not verify below."
                    fi
                    ;;
                *)
                    warn "could not declare the brain's embedding tier: ${_decl_out#ERROR	}"
                    ;;
            esac
            tier1_restore_t0() {   # put tier0_only back exactly as this run found it
                [ -n "$T1_T0_PREV" ] || return 0
                "$VENV_PY" -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('INSERT OR REPLACE INTO metadata (key,value) VALUES (?,?)',('tier0_only',sys.argv[2])); c.commit(); c.close()" "$DB_PATH" "$T1_T0_PREV" \
                    && say "  tier0_only=${T1_T0_PREV} put back: Tier 1 did not verify, so the brain stays keyword-only as it was." \
                    || warn "could not put tier0_only=${T1_T0_PREV} back in ${DB_PATH}"
            }

            # C.prove -- THE DEVICE IS PROVEN BEFORE ANYTHING IS EMBEDDED (handoff round 1, L4). P6: the package's own
            # encoder on the device the organs chose agrees with the CPU-fp32 golden vectors; P7 (gate ON only): the
            # reranker ranks relevant above irrelevant there. An MPS/ROCm/XPU machine that FAILS is not left to embed
            # on a backend that just failed its proof: WIZARD_ACCELERATOR=cpu is recorded (the owner's own line is the
            # named opt-out and is never rewritten), announced in one line (gpu-safety-002's shape: the value, the
            # reason, the result, the opt-out), the record says proof_label=FAILED-ON-THIS-MACHINE, and the CPU is proven
            # in its place. A CUDA or CPU failure is a Tier-1 failure, said: those are the PROVEN rows.
            T1_DEVICE_OK=1
            if [ "$T1_DECL_OK" -eq 1 ]; then
                if t1_device_proof; then :; else T1_DEVICE_OK=0; fi
            fi

            say "  embedding the seed pack (embedding is part of installation)…"
            T1_EMBED_REFUSED=0
            if "$VENV_PY" "${BRAIN_DIR}/organs/embed_backfill.py" "$DB_PATH"; then
                say "  seed pack embedded — semantic recall works from the first query."
            else
                _bf_rc=$?
                if [ "$_bf_rc" -eq 3 ]; then
                    T1_EMBED_REFUSED=1
                    warn "the seed pack could NOT be embedded on this host, so this brain is KEYWORD-ONLY (FTS5) until it is. Nothing is broken and nothing was lost — recall works, it just will not rank semantically. Re-run: ${VENV_PY} ${BRAIN_DIR}/organs/embed_backfill.py ${DB_PATH}"
                else
                    warn "the seed-pack embedding reported failures (rc ${_bf_rc}); the memories it could not embed are tagged 'embedding-missing' and are findable by SQL. Re-run the same command to retry them."
                fi
            fi

            # C.verify — confirm SEMANTIC recall returns against the brain (through the voidloom facade, at tier 1).
            #
            # CRUCIBLE-3 D6-F3: this gate grepped for '"status": "ok"' — which a
            # BM25-ONLY hybrid answer returns, and which a ZERO-RESULT recall also
            # returns. Run against a brain with zero vectors in every table it
            # MATCHED, and the installer would have printed
            # "Tier 1 VERIFIED — nomic-768 SEMANTIC recall returns memories" over a
            # brain with no semantics at all. The gate could not distinguish the
            # capability it names from its absence.
            #
            # It now asserts on the evidence the CLAIM needs: the tier's own
            # embedding table holds vectors, AND the recall returned at least one
            # result. Either one alone still admits the failure it was blind to.
            # NOT 2>/dev/null. This gate prints WHY it refused -- "0 vectors",
            # "no readable envelope", "status=... with N results" -- and that
            # stream was being discarded, so a refusal reached the operator as the
            # contentless "semantic recall verification did not return ok". The
            # gate explained itself to /dev/null. Diagnosing the dirname defect
            # above required re-running it by hand with stderr restored.
            #
            # B1 (stranger test, 2026-10-13): it still printed "Tier 1 VERIFIED" over a brain that
            # declared NO tier -- the PARTIAL arm ("count_unavailable=no active table recorded")
            # passed whenever the keyword arm returned anything, four lines after embed_backfill
            # had REFUSED. A brain with no declared table has no Tier 1 to verify, and a REFUSED
            # embedding is not a verified one: both are NOT VERIFIED now. And the recall is asked
            # through the `voidloom` facade -- what the user will run -- which reports the TIER it
            # answered at; the raw engine never says, so a Tier-0 answer (tier0_only, a failed
            # Tier-1 import) passed this gate as long as it returned rows.
            if [ "$T1_DECL_OK" -ne 1 ]; then
                tier1_restore_t0
                tier1_upgrade_hint "the brain's embedding tier could not be declared (see the warning above)"
            elif [ "$T1_DEVICE_OK" -ne 1 ]; then
                tier1_restore_t0
                tier1_upgrade_hint "the ${ACCEL_BACKEND} device failed its proof (P6/P7, above)"
            elif [ "$T1_EMBED_REFUSED" -eq 1 ]; then
                tier1_restore_t0
                tier1_upgrade_hint "embedding was REFUSED (embed_backfill rc 3, above), so nothing is semantic"
            elif T1_VERIFY_LINE="$(hd_t1_verify)"; then
                say "  Tier 1 VERIFIED — ${EMBED_MODEL}-${EMBED_DIM}: ${T1_VERIFY_LINE}"
                TIER1_OK=1
                # P8: five timed recalls through the package's own path; p50 DERIVED here and recorded, never typed
                probe_tmp_ensure
                if run_probe_venv "P8" "${PROBE_TMP}/p8.json" $([ "$FORCE_CPU" -eq 1 ] && echo --force-cpu); then
                    say "  P8 timed recall on ${ACCEL_BACKEND}: $(probe_fact "${PROBE_TMP}/p8.json" P8 p50_s) s per recall (p50 of $(probe_fact "${PROBE_TMP}/p8.json" P8 queries))."
                else
                    warn "P8 (timed recall) did not complete: ${FAIL_DETAIL}. Tier 1 is verified; the timing is not recorded."
                fi
            else
                tier1_restore_t0
                tier1_upgrade_hint "semantic recall verification did not return ok"
            fi
        else
            tier1_upgrade_hint "pip install of the semantic stack failed"
        fi
    else
        tier1_upgrade_hint "venv creation failed (need python3 -m venv)"
    fi
fi

# ----------------------------------------------------------------------------
# STEP D — ENV FILE  (marker-guarded, APPEND-SAFE; idempotent re-runs)  [M5 fix]
#   write_env_file is DEFINED earlier (before the --env-only short-circuit) so it
#   is callable from both there and here. Managed lines live strictly between
#   ENV_BEGIN/ENV_END; a re-run rewrites ONLY that block. This is the
#   "never overwrites your config — appends with markers" promise, honored.
# ----------------------------------------------------------------------------
step "STEP D — Env file (marker-guarded, append-safe)"
write_env_file
accel_record
gate_record
note_step "env"
if [ "$DRY_RUN" -eq 1 ]; then
    say "  DRY: ${ENV_FILE} would be written (mode 0600); nothing was written."
else
    say "  ${ENV_FILE} written (mode 0600). 'source ${ENV_FILE}' to load it into a shell."
fi

# ----------------------------------------------------------------------------
# STEP E — SELF-CHECK ACCEPTANCE GATE
#   Prefer the shipped self_check.py (stdlib-only Tier-0 acceptance test); fall
#   back to `voidloom doctor` (CLI-on-PATH + brain health) when self_check is
#   absent. A green gate is the stranger's proof the install actually works.
# ----------------------------------------------------------------------------
step "STEP E — Self-check acceptance gate"
#
# C2/M4. Both arms of this gate used to grep `"status": *"ok"` over `voidloom
# doctor`. Measured three ways:
#   * WITHOUT --json the render is not JSON and the pattern NEVER matches, so
#     the fallback could not rescue anything, ever.
#   * WITH --json it ALWAYS matches -- on the ENVELOPE. `verb_doctor` has
#     exactly one return, `ok(...)` (AST-verified), so `healthy` was never read.
#   * `set -uo pipefail` without `-e` meant a failed gate still exited 0.
# Green or red by construction, and unable to change the outcome either way.
#
# It now reads `healthy`, which is the field that would DIFFER, and it can fail
# the install. It also runs the schema audit, because `voidloom doctor` has no
# schema check at all (measured: its checks are local_fs_guard, db_present,
# db_path, db_size_mb, engine_importable, models_present) -- so doctor alone
# cannot see the very defect this class exists to end.
SELF_CHECK="${BRAIN_DIR}/install/self_check.py"
ACCEPT_OK=0
# Round 3 G1, item 2: the DONE banner names EXACTLY which checks failed and what they mean (GATE_FAILS: one line per
# failed REQUIRED check -- name, what it means, the detail, the fix), and the owner's CHOICES the gate found (GATE_CHOICES)
# are said as choices, never as failures.
GATE_FAILS=""; GATE_CHOICES=""; GATE_SUMMARY=""
gate_read() {   # gate_read JSON_FILE -- self_check's --json envelope as TAB lines: SUMMARY / FAIL / CHOICE
    "$PY_SYS" -B - "$1" <<'GATEPY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:
    print("ERROR\tself_check.py printed no readable result (%s)" % type(e).__name__); sys.exit(0)
clean = lambda x: " ".join(str(x or "").split())
print("SUMMARY\t%s\t%s" % (d.get("required_passed"), d.get("required_total")))
for c in d.get("checks") or []:
    if c.get("required") and not c.get("ok"):
        print("FAIL\t%s\t%s\t%s\t%s" % (clean(c.get("name")), clean(c.get("means")), clean(c.get("detail")), clean(c.get("fix"))))
for ch in d.get("choices") or []:
    print("CHOICE\t" + clean(ch))
GATEPY
}
if [ "$DRY_RUN" -eq 1 ]; then
    say "  DRY: would run self_check.py (or 'voidloom doctor') to confirm the install is alive."
    ACCEPT_OK=1
elif [ -f "$SELF_CHECK" ]; then
    # R1: the ambient upgrade adds no memories, so a brain that never carried the seed pack
    # is checked with --seed-optional (the pack's checks reported not applicable, recall
    # checked over the brain's own content) -- never by loading the pack to make a check pass.
    _sc_opt=""; [ "$SHAPE" = "ambient" ] && _sc_opt="--seed-optional"
    assert_one_pin "Step E's self-check"
    _sc_dir="$(mktemp -d 2>/dev/null)" || die "could not create a temporary directory for Step E's result."
    WIZARD_LIBRARY_DB="$DB_PATH" WIZARD_EMBED_MODEL="$EMBED_MODEL" "$PY_SYS" "$SELF_CHECK" --json $_sc_opt \
        > "${_sc_dir}/sc.json" 2> "${_sc_dir}/sc.err"; _sc_rc=$?
    _sc_lines="$(gate_read "${_sc_dir}/sc.json")"
    [ -n "$_sc_lines" ] || _sc_lines="$(printf 'ERROR\tself_check.py printed no readable result (rc %s): %s' "$_sc_rc" "$(tail -c 300 "${_sc_dir}/sc.err" | tr '\n\t' '  ')")"
    rm -rf "$_sc_dir"
    GATE_SUMMARY="$(printf '%s\n' "$_sc_lines" | awk -F'\t' '$1 == "SUMMARY" { print $2 "/" $3; exit }')"
    GATE_FAILS="$(printf '%s\n' "$_sc_lines" | awk -F'\t' '$1 == "FAIL" || $1 == "ERROR"')"
    GATE_CHOICES="$(printf '%s\n' "$_sc_lines" | awk -F'\t' '$1 == "CHOICE" { print $2 }')"
    if [ "$_sc_rc" -eq 0 ] && [ -z "$GATE_FAILS" ]; then
        say "  self_check PASSED — required ${GATE_SUMMARY:-?} — the install is alive."
        [ -n "$GATE_CHOICES" ] && say "  (and it found $(printf '%s\n' "$GATE_CHOICES" | grep -c .) choice(s) that are yours to make -- named under DONE; they are not failures)"
        ACCEPT_OK=1
        note_step "selfcheck"
        # N3 (Crucible D02): the organs carry 83 legs of self-check between them
        # and NOTHING EVER RAN THEM at install time. `self_check.py` grades the
        # BRAIN -- schema, seed, recall, tier -- and never the hooks, the notice,
        # the canary or the preamble, which is the entire surface an operator is
        # about to depend on. Ten seconds, and a failure is a WARNING rather than
        # a gate: these organs grade the delivery, and a delivery fault must be
        # loud without making a working brain refuse to finish installing.
        if [ -f "${BRAIN_DIR}/organs/voidloom_hooks_status.py" ]; then
            if WIZARD_LIBRARY_DB="$DB_PATH" "$PY_SYS" \
                 "${BRAIN_DIR}/organs/voidloom_hooks_status.py" --self-check >/dev/null 2>&1; then
                say "  hooks --self-check PASSED — the delivery machinery grades itself clean."
            else
                warn "hooks --self-check reported failures. The brain is alive; the HOOK machinery is not verified. Inspect: ${PY_SYS} '${BRAIN_DIR}/organs/voidloom_hooks_status.py' --self-check --json"
            fi
        fi
    else
        [ -n "$GATE_FAILS" ] || GATE_FAILS="$(printf 'ERROR\tself_check.py exited %s with no failed check named' "$_sc_rc")"
        warn "self_check.py: $(printf '%s\n' "$GATE_FAILS" | grep -c .) REQUIRED check(s) FAILED (required passed: ${GATE_SUMMARY:-?}) -- each is named, with what it means, under DONE. Inspect: WIZARD_LIBRARY_DB='${DB_PATH}' ${PY_SYS} '${SELF_CHECK}'${_sc_opt:+ ${_sc_opt}}"
    fi
else
    SCHEMA_OK=0
    if [ -f "${BRAIN_DIR}/organs/schema_migrator.py" ] \
       && WIZARD_LIBRARY_DB="$DB_PATH" "$PY_SYS" "${BRAIN_DIR}/organs/schema_migrator.py" audit --db "$DB_PATH" >/dev/null 2>&1; then
        SCHEMA_OK=1
    fi
    DOCTOR_OK=0
    if [ -x "${LOCAL_BIN}/voidloom" ] \
       && WIZARD_LIBRARY_DB="$DB_PATH" "${LOCAL_BIN}/voidloom" doctor --json 2>/dev/null | grep -q '"brain_healthy": *true'; then
        DOCTOR_OK=1
    fi
    if [ "$SCHEMA_OK" -eq 1 ] && [ "$DOCTOR_OK" -eq 1 ]; then
        say "  no self_check.py; schema audit + 'voidloom doctor' healthy — the brain is usable."
        ACCEPT_OK=1
    else
        warn "acceptance gate did not pass (schema_ok=${SCHEMA_OK} doctor_healthy=${DOCTOR_OK}). Inspect: WIZARD_LIBRARY_DB='${DB_PATH}' ${PY_SYS} '${BRAIN_DIR}/organs/schema_migrator.py' audit"
        [ "$SCHEMA_OK" -eq 1 ] || GATE_FAILS="${GATE_FAILS:+$GATE_FAILS
}$(printf 'FAIL\tschema audit (no self_check.py in this install)\tthe brain may lack tables the organs of this package use\tschema_migrator.py audit did not pass\tWIZARD_LIBRARY_DB=%s %s %s/organs/schema_migrator.py apply' "$DB_PATH" "$PY_SYS" "$BRAIN_DIR")"
        [ "$DOCTOR_OK" -eq 1 ] || GATE_FAILS="${GATE_FAILS:+$GATE_FAILS
}$(printf 'FAIL\tvoidloom doctor (no self_check.py in this install)\tthe voidloom command or the brain did not report healthy\tvoidloom doctor --json did not say brain_healthy true\tvoidloom doctor')"
    fi
fi
if [ "$TIER0_OK" -ne 1 ] && [ "$DRY_RUN" -ne 1 ] && [ "${ENV_ONLY:-0}" -ne 1 ]; then
    warn "Tier 0 was not verified; the 'works on any python3' promise is NOT met on this host."
    ACCEPT_OK=0
    GATE_FAILS="${GATE_FAILS:+$GATE_FAILS
}$(printf 'FAIL\tTier 0 verified\tkeyword recall over the brain was not shown to work with this python3\tthe Tier 0 step above did not pass\tsee the Tier 0 step above')"
fi

# ----------------------------------------------------------------------------
# STEP F — OPTIONAL CRONS (default OFF) — the roster, read from ONE file, rendered
# by ONE template
#
# Council #12 (2026-09-18). This script carried a six-row CRON_DEFS table,
# install/install_crons.sh carried a four-row table of its own, the package
# ships seventeen timers, and `sleep-loop` -- the only organ that writes
# consolidation_sources -- was in neither. A stranger who installed with crons
# got a brain that never consolidated: the 93-day defect, shipped by design.
#
# The table is gone. install/cron_roster.json names EVERY packaged timer as
# armed-by-default or ships-as-template, each with a reason; install/cron_roster.py
# is its one reader; voidloom/tests/test_cron_roster.py binds it to cron/*.timer
# and cron/*.service in both directions.
#
# Round-1 reflection, item 8: this script ALSO carried its own unit template --
# `EnvironmentFile=` without the leading `-`, `After=network.target`, a different
# Description -- so `--with-crons` on an existing install would have overwritten
# eight live units with a body that differed from the packaged units and from
# what install/install_crons.sh writes. That template is gone too. Step F now
# CALLS install/install_crons.sh (the package copy: on a --dry-run nothing is
# laid yet, and on a wet run lay.py has just verified the laid copy against these
# very bytes), with this run's brain dir, DB path, env file and interpreter
# passed explicitly. One roster, one template, one writer of the CUDA pin (the
# env file). Two defects of the old table stay closed by the roster's own fields:
# exec paths are `organs/<x>.py ...` bound to the packaged unit's ExecStart, and
# the units run ${BRAIN_DIR}/venv/bin/python3 when Tier 1 built it, else the
# system python -- install_crons.sh applies that rule.
# ----------------------------------------------------------------------------
CRON_ROSTER="${PKG_ROOT}/install/cron_roster.json"
CRON_ROSTER_PY="${PKG_ROOT}/install/cron_roster.py"
CRONS_SH="${PKG_ROOT}/install/install_crons.sh"
ARMED_COUNT=0

install_crons_via_roster() {
    [ -f "$CRON_ROSTER" ] && [ -f "$CRON_ROSTER_PY" ] && [ -f "$CRONS_SH" ] \
        || die "package incomplete — install/cron_roster.json, install/cron_roster.py or install/install_crons.sh missing from ${PKG_ROOT}."
    local _armed_list
    if ! _armed_list="$("$PY_SYS" "$CRON_ROSTER_PY" --roster "$CRON_ROSTER" armed)"; then
        die "could not read the armed roster from ${CRON_ROSTER} (see above)."
    fi
    ARMED_COUNT="$(printf '%s\n' "$_armed_list" | grep -c . || true)"
    [ "${ARMED_COUNT:-0}" -gt 0 ] || die "the roster names no armed-by-default unit; refusing to report an empty arming as success."
    say "  arming the roster with ${CRONS_SH} -- the ONE unit template (also what a re-run of"
    say "  ${BRAIN_DIR}/install/install_crons.sh renders); interpreter for the units: ${BRAIN_DIR}/venv/bin/python3"
    say "  when Tier 1 built it, else ${PY_SYS}."
    local _dry=""; [ "$DRY_RUN" -eq 1 ] && _dry="--dry-run"
    # shellcheck disable=SC2086
    # THE GATE LIVES IN install_crons.sh TOO (M10): it refuses to arm a unit that thinks while the brain records no answer.
    # A dry run records nothing, so it is told the answer this run settled -- honoured by install_crons.sh ONLY with
    # --dry-run (a wet run reads THINKER_RECORD, which thinker_prove wrote before Step A).
    # "$BASH": the child runs under the shell running THIS script, not whichever bash PATH finds first -- on a Mac with
    # a Homebrew bash ahead of /bin/bash that would test a shell the friend does not have (round 2, m7's class).
    if ! WIZARD_LIBRARY_HOME="$BRAIN_DIR" WIZARD_LIBRARY_DB="$DB_PATH" WIZARD_ENV_FILE="$ENV_FILE" \
         WIZARD_PYTHON_BIN="$PY_SYS" VOIDLOOM_THINKER_SETTLED="$([ "$DRY_RUN" -eq 1 ] && printf '%s' "$THINKER_ANSWER")" \
         "$BASH" "$CRONS_SH" $_dry; then
        die "install/install_crons.sh failed (see above); the roster was not armed."
    fi
}

# CRUCIBLE-3 D01-F6 found "34 shipped cron unit files consumed by no installer
# path" and read them as ORPHANS. Re-measured for cycle 2, the truth is the
# opposite: those files are the DEFINITION of timers actually running on the
# author's machine, and this script UNDER-INSTALLED -- six generated units for
# a package of seventeen, saying nothing about the difference.
#
# The roster makes the gap a DECISION rather than a silence: every packaged
# timer is armed-by-default or ships-as-template, with a reason a reader can
# act on (`install/cron_roster.json`, field `why`). This report states which is
# which, derived from the roster and the packaged directory, never typed.
report_cron_coverage() {
    local shipped; shipped=0
    for f in "${PKG_ROOT}"/cron/wizard-library-*.timer; do [ -e "$f" ] && shipped=$((shipped + 1)); done
    say "  cron coverage: armed ${ARMED_COUNT} of the ${shipped} timers this package ships (install/cron_roster.json)."
    say "  the rest ship as templates under ${BRAIN_DIR}/cron/ with a stated reason each; opt in with"
    say "  WIZARD_CRON_ONLY=<name> bash ${BRAIN_DIR}/install/install_crons.sh, or read docs/FIRST_NIGHT.md first."
}

approvals_record() {
    local _by _out _rc _dry=""
    case "$APPROVALS_HOW" in
        record) say "  approvals: ${APPROVALS_ANSWER} (your recorded choice). Change it any time: voidloom approvals --mode auto|manual"; return 0 ;;
        kept)   say "  approvals: no choice is recorded on this brain, so its monthly proposals wait for a person. To let the brain decide them: voidloom approvals --mode auto"; return 0 ;;
    esac
    [ -n "$APPROVALS_ANSWER" ] || return 0
    if [ "${ACCEPT_OK:-0}" -ne 1 ] && [ "$DRY_RUN" -ne 1 ]; then
        warn "the approvals choice (${APPROVALS_ANSWER}) was NOT recorded: the self-check above did not pass. Re-run after fixing it, or later: voidloom approvals --mode ${APPROVALS_ANSWER}"
        return 0
    fi
    [ -n "$WIZARD_CONFIG_JSON" ] || { warn "the path of wizard-config.json could not be resolved; the approvals choice (${APPROVALS_ANSWER}) was NOT recorded."; return 0; }
    [ "$DRY_RUN" -eq 1 ] && _dry="--dry-run"
    case "$APPROVALS_HOW" in flag) _by="owner-flag" ;; question) _by="owner" ;; *) _by="installer-default" ;; esac
    _out="$(WIZARD_CONFIG_PATH="$WIZARD_CONFIG_JSON" "$PY_SYS" -B "${PKG_ROOT}/organs/approvals_policy.py" set "$APPROVALS_ANSWER" --by "$_by" $_dry 2>&1)"; _rc=$?
    if [ "$_rc" -eq 0 ]; then
        say "  approvals: ${_out}$([ "$APPROVALS_HOW" = default ] && echo " (the default for a new install with no terminal to ask)"). Change it any time: voidloom approvals --mode $([ "$APPROVALS_ANSWER" = auto ] && echo manual || echo auto)"
    else
        warn "approvals: ${_out:-approvals_policy.py set failed (rc ${_rc})}"
    fi
}
step "STEP E2 — Approvals choice"
approvals_record

step "STEP F — Optional maintenance crons"
if [ "$WITH_CRONS" -eq 1 ]; then
    case "$OS" in
        Linux|Darwin) install_crons_via_roster; note_step "crons" ;;
        *)            warn "--with-crons unsupported on ${OS}; env file is written, wire crons by hand." ;;
    esac
    report_cron_coverage
    # R1 -- ONE INTERPRETER, READ BACK. The ambient shape rendered every unit with the
    # recorded interpreter (WIZARD_PYTHON_BIN, as the literal first token of ExecStart=);
    # now the manager is asked what it will run. A mismatch fails the run.
    if [ "$SHAPE" = "ambient" ] && [ "$OS" = "Linux" ] && [ "$DRY_RUN" -ne 1 ]; then
        if readback_units; then
            note_step "readback"
        else
            die "the READ-BACK failed (${FAIL_DETAIL}): the units do not run the recorded interpreter ${PY_SYS}. They were rendered by this run; inspect them with: systemctl --user cat wizard-library-<unit>"
        fi
    fi
else
    say "  crons disabled (default). Arm later with: bash install.sh --with-crons"
    say "  (install/cron_roster.json names what that arms, and why the rest ship as templates)"
fi

# R1 -- THE TIER POST-CONDITION (ambient): the brain's declarations read at the start
# equal those read at the end, or the run fails. An upgrade that changed the recall tier
# or switched keyword-only on has changed host state nobody ordered changed.
if [ "$SHAPE" = "ambient" ] && [ "$DRY_RUN" -ne 1 ]; then
    TIER_AFTER="$(brain_decl)"
    # equal EMPTINESS is not a held post-condition: an unreadable brain at both ends proves nothing
    if [ -n "$TIER_AFTER" ] && [ "$TIER_AFTER" = "$TIER_BEFORE" ]; then
        say "  post-condition held: active_embedding_table|tier0_only = '${TIER_AFTER}' at the start and at the end."
        note_step "postcondition"
    else
        die "POST-CONDITION FAILED: the brain declared '${TIER_BEFORE}' (active_embedding_table|tier0_only) at the start and '${TIER_AFTER}' now. The pre-image is ${UPGRADE_PREIMAGE:-${ATTACH_PREIMAGE:-<none>}}."
    fi
fi

# ----------------------------------------------------------------------------
# STEP P — THE PERSONA FORGE (mandatory, and LAST: only on a verified brain)
#
# The package ships no persona; the human designs their own in a guided
# conversation inside Claude Code. This step lays that conversation (the
# persona-forge skill + /persona-forge, lay_manifest component `forge`) under
# install/install_persona_forge.sh's never-clobber contract. It runs ONLY when
# the acceptance gate (STEP E) passed: the forge is the step after "all the
# actual pieces are working and verified", never a substitute for them. The
# offer itself arrives in the first session (the preamble carries it while
# `voidloom persona status` is absent); the banner at the very end says so.
# ----------------------------------------------------------------------------
step "STEP P — Persona Forge (mandatory next step after a verified install)"
PERSONA_FORGE_SH="${PKG_ROOT}/install/install_persona_forge.sh"
PERSONA_FORGE_OK=0
HOOKS_WIRED=""          # yes | no (and why) -- printed in the final banner
# Gated on an EXPLICIT acceptance PASS: ACCEPT_OK is set to 0 before STEP E and to 1 only by a passing gate, and is
# read here with a default of 0, so a run in which the gate never executed never lays the forge.
if [ ! -f "$PERSONA_FORGE_SH" ]; then
    die "package incomplete — install/install_persona_forge.sh not found in ${PKG_ROOT}."
elif [ "$DRY_RUN" -eq 1 ]; then
    . "$PERSONA_FORGE_SH"
    install_persona_forge
    say "  DRY: then ask 'Wire the session-start hook now ...? [Y/n]' (or --wire-hooks / --no-wire-hooks)"
elif [ "${ACCEPT_OK:-0}" != "1" ]; then   # spelled differently from the DONE banner's test on purpose: test_installer_seed_choice_r3 extracts THAT block by its first line
    warn "  Persona Forge NOT laid: the acceptance gate (STEP E) did not pass. Fix that first; a re-run lays it."
else
    # shellcheck source=install/install_persona_forge.sh
    . "$PERSONA_FORGE_SH"
    if install_persona_forge; then
        PERSONA_FORGE_OK=1
        note_step "persona-forge"
    else
        warn "  the Persona Forge was not fully laid (named above); the brain is unaffected. Re-run: bash ${PERSONA_FORGE_SH}"
    fi
fi
# THE FIRST-RUN PROMPT RIDES THE SESSIONSTART HOOK, which nothing above wires. So the owner is asked here (a flag
# where no terminal is reachable). Unanswered with no terminal = NOT wired, and the banner says so: the hook writes
# into ${CLAUDE_HOME}/settings.json, which this installer never touches without an answer.
# Read the REAL hook state first, so the banner reports what is true (an earlier install, wire_persona.sh or a hand
# `voidloom hooks install` may have wired it already). Output only -- the status verb's own rc is not consulted.
_hooks_now="$(WIZARD_LIBRARY_HOME="$BRAIN_DIR" CLAUDE_CONFIG_DIR="$CLAUDE_HOME" \
    "$PY_SYS" -B "${BRAIN_DIR}/organs/voidloom_hooks_status.py" --status --json 2>/dev/null \
    | "$PY_SYS" -B -c 'import json, sys
try:
    rows = json.load(sys.stdin)["data"]["platforms"]
    print("yes" if any(r.get("id") == "claude-code" and r.get("installed") for r in rows) else "no")
except Exception:
    print("unknown")' 2>/dev/null)" || true
if [ "$PERSONA_FORGE_OK" -eq 1 ] && [ "${_hooks_now:-}" = "yes" ]; then
    HOOKS_WIRED="yes (already wired)"
elif [ "$PERSONA_FORGE_OK" -eq 1 ]; then
    # Asked ONCE: at the run that lays the forge (a first install, or a changed forge) -- never on every upgrade.
    if [ -z "$WIRE_HOOKS" ] && [ "${_PF_LAID_NEW:-0}" -eq 1 ] && ask_tty; then
        ask_say ""
        ask_say "Wire the session-start hook now so your first session offers the Persona Forge? [Y/n]"
        ask_say "  (it adds ONE hook entry to ${CLAUDE_HOME}/settings.json via 'voidloom hooks install --platform claude';"
        ask_say "   nothing else in that file changes, and 'voidloom hooks uninstall' removes it)"
        printf '%s ' ">" >/dev/tty
        _wh=""; IFS= read -r _wh </dev/tty || true
        case "$(_ask_trim "$_wh")" in
            n|N|no|NO|No) WIRE_HOOKS=0 ;;
            *)            WIRE_HOOKS=1 ;;
        esac
    fi
    if [ "$WIRE_HOOKS" = "1" ]; then
        if WIZARD_LIBRARY_HOME="$BRAIN_DIR" WIZARD_LIBRARY_DB="$DB_PATH" CLAUDE_CONFIG_DIR="$CLAUDE_HOME" \
           "$PY_SYS" -B "${BRAIN_DIR}/organs/voidloom_hooks_status.py" --install --platform claude --yes; then
            HOOKS_WIRED="yes"
            note_step "hooks"
        else
            HOOKS_WIRED="no -- 'voidloom hooks install --platform claude' reported a problem (above); settings.json was not clobbered"
        fi
    elif [ "$WIRE_HOOKS" = "0" ]; then
        HOOKS_WIRED="no -- your answer (--no-wire-hooks or n)"
    elif [ "${_PF_LAID_NEW:-0}" -ne 1 ]; then
        HOOKS_WIRED="not asked (the forge was already installed; 'voidloom hooks status' shows the hook)"
    else
        HOOKS_WIRED="no -- no terminal to ask and no --wire-hooks flag"
    fi
fi

# ----------------------------------------------------------------------------
# FINAL — SUMMARY (install != onboard)
# ----------------------------------------------------------------------------
step "DONE"
say "Tier 0 (keyword recall over the brain):  $([ "$TIER0_OK" -eq 1 ] && echo 'ACTIVE' || echo 'CHECK ABOVE')"
say "Tier 1 (${EMBED_MODEL}-${EMBED_DIM} SEMANTIC recall): $([ "${TIER1_OK:-0}" -eq 1 ] && echo 'ACTIVE' || echo 'not completed (Tier 0 still works; see hints above)')$([ "$SHAPE" = "ambient" ] && echo " -- ambient, through ${PY_SYS}")"
[ -L "${LOCAL_BIN}/voidloom" ] && say "voidloom CLI:                            on PATH (${LOCAL_BIN}/voidloom) ✓"
if [ "$DRY_RUN" -ne 1 ] && [ -f "$THINKER_PY" ]; then
    # the overnight thinker, as the seam reports it (organs/thinker.py status: the record, the credential, and -- M13, the
    # bounded part -- plain words on moving an owner's CLAUDE_CODE_OAUTH_TOKEN line; no machine ever removes that line)
    WIZARD_LIBRARY_DB="$DB_PATH" WIZARD_ENV_FILE="$ENV_FILE" "$PY_SYS" -B "$THINKER_PY" status 2>/dev/null \
        | awk 'NR == 1 { print "Overnight " $0; next } /note:/ { print $0 }' | while IFS= read -r _x; do say "$_x"; done
fi
if [ -n "$GATE_CHOICES" ]; then
    echo
    say "Your choice (not a failure) -- the acceptance gate found:"
    printf '%s\n' "$GATE_CHOICES" | while IFS= read -r _x; do [ -n "$_x" ] && say "  - ${_x}"; done
fi
echo
say "Confirm it's alive:"
say "  source '${ENV_FILE}'"
say "  voidloom doctor          # CLI-on-PATH + brain health"
say "  voidloom recall --query 'hello' --top-k 3"
echo
say "INSTALL is deterministic and DONE. (Ensure ${LOCAL_BIN} is on your PATH so"
say "'voidloom' resolves — the env file's managed PATH line handles this once sourced.)"

# C2/M4. `set -uo pipefail` deliberately omits `-e` so Tier 1 may fail without
# aborting; the consequence was that STEP E could not fail ANYTHING -- a warn
# and `exit 0` say "installed" to every caller and every CI. The acceptance
# verdict now reaches the exit code. Tier 1 is still allowed to fail freely:
# ACCEPT_OK covers Tier 0, the schema and the self-check only.
if [ "${ACCEPT_OK:-1}" -ne 1 ]; then
    # Round 3 G1, item 2: name EXACTLY which checks failed and what each means -- never a blanket "nothing above works"
    # (memory #23616: that sentence, printed over an install whose only "failures" were the installer's own seed policy,
    # sent the owner to re-run a finished install). A choice the gate found is never here: it is listed above as a choice.
    echo
    _gf_n="$(printf '%s\n' "$GATE_FAILS" | grep -c .)"
    warn "ACCEPTANCE GATE FAILED — ${_gf_n} required check(s) did not pass${GATE_SUMMARY:+ (required passed: ${GATE_SUMMARY})}, so this brain is NOT verified on these points:"
    printf '%s\n' "$GATE_FAILS" | while IFS="$(printf '\t')" read -r _k _n _m _d _f; do
        [ -n "$_k" ] || continue
        if [ "$_k" = "ERROR" ]; then warn "  - ${_n}"; continue; fi
        warn "  - ${_n}: ${_m:-a required check failed}${_d:+ (${_d})}${_f:+. Fix: ${_f}}"
    done
    warn "The files are laid and every check not named here passed (listed in STEP E). Fix the check(s) named above, then re-run; ${INSTALL_RECORD} is updated only by a run whose gate passes."
    FAIL_DETAIL="acceptance gate (Step E): $(printf '%s\n' "$GATE_FAILS" | awk -F'\t' '{ printf "%s%s", (NR > 1 ? "; " : ""), $2 }')"
    append_install_log "acceptance-failed"
    exit 5
fi
if [ "$DRY_RUN" -ne 1 ]; then
    if write_install_record; then
        say "Record:  ${INSTALL_RECORD} (shape=${SHAPE}); log: ${INSTALL_LOG}"
        append_install_log "ok"
    else
        FAIL_DETAIL="could not write ${INSTALL_RECORD}"
        append_install_log "record-failed"
        die "the install finished but ${INSTALL_RECORD} could not be written; the next ambient run would not know its shape."
    fi
fi
# THE MANDATORY NEXT STEP — printed last, so it is the line the owner (or the AI agent installing for them) reads.
_pf_state="$(WIZARD_LIBRARY_HOME="$BRAIN_DIR" "$PY_SYS" -B "${BRAIN_DIR}/organs/voidloom_persona.py" status 2>/dev/null | head -n 1)" || true
echo
say "=================================================================="
if [ "$PERSONA_FORGE_OK" -eq 1 ] && [ "${_pf_state:-}" = "persona: forged" ]; then
    say " Persona: already forged -- nothing to do. (/persona-forge revises it.)"
else
    say " MANDATORY NEXT STEP: open Claude Code in any folder and type /persona-forge"
    say " -- you design your persona; Claude fills in the details. ${_pf_state:-persona: unknown}"
    say " Session-start hook wired: ${HOOKS_WIRED:-no}"
    if [ "${HOOKS_WIRED:-no}" = "yes" ] || [ "${HOOKS_WIRED:-no}" = "yes (already wired)" ]; then
        say " (the first session will also offer it by itself)"
    else
        say " (wire it to have the first session offer it by itself:"
        say "    voidloom hooks install --platform claude"
        say "  or, hook-free: voidloom instructions install -- its doctrine block carries the same rule)"
    fi
fi
say "=================================================================="
exit 0
