#!/usr/bin/env bash
# Cross-platform maintenance-cron installer -- THE ONE UNIT TEMPLATE.
#
# Arms the background jobs that keep the Voidloom brain's organs fresh. Runs
# standalone from the INSTALLED brain (`bash ~/.claude/memory/install/install_crons.sh`)
# and is what `install.sh --with-crons` (Step F) calls: there is no second
# renderer of a unit file anywhere in the package. Round-1 reflection, item 8:
# install.sh carried its own template (EnvironmentFile= without the leading `-`,
# After=network.target, a different Description) and a landing would have
# overwritten eight live units with a divergent body. Now one template, one
# roster, one writer.
#
# THE RENDERED BODY EQUALS THE PACKAGED BODY (round-1 iteration-2 reflection,
# item 2 -- the previous header claimed this and it was false: the template
# rendered After=/Wants=network-online.target, which 0 of 17 packaged units
# carry). The template renders the majority shape; what a packaged unit carries
# beyond it (Nice=, Environment=PATH=, Documentation=, After=default.target,
# SuccessExitStatus=, a timer's RandomizedDelaySec= or extra OnCalendar= lines)
# or lacks (WorkingDirectory=, the journal lines, TimeoutStartSec=) is declared
# per unit in the roster as service_extra / timer_extra / service_omit, and
# voidloom/tests/test_cron_roster.py renders every unit with --dry-run and
# proves the directive set equals the packaged unit's. Three things differ BY
# DESIGN and are excluded there: Description=, %h (rendered absolute), and the
# interpreter (/usr/bin/env python3 -> this install's python).
#
# WHICH JOBS: the ONE roster, install/cron_roster.json. Council #12 (2026-09-18)
# found this script arming four jobs from a table of its own (dreaming,
# gap-research, cognitive-map, pre-cognitive) while install.sh armed six from
# another, the package shipped seventeen timers, and `sleep-loop` -- the only
# organ that writes consolidation_sources -- was in neither. Both installers now
# read the roster through install/cron_roster.py; this file carries no table.
#
#   default        arms every unit the roster marks `armed-by-default`
#                  (sleep-loop, dreaming, anti-recency, whisper-digest,
#                  skill-graduation, gap-mapping, self-eval, organ-run-log --
#                  derived: `python3 install/cron_roster.py armed`)
#   WIZARD_CRON_ONLY="a,b"   arms EXACTLY the named units instead -- any roster unit,
#                  template or not. This is how a `ships-as-template` job
#                  (gap-research, cognitive-map, pre-cognitive, ...) is opted into:
#                  deliberately, by name, having read its `why` in the roster.
#   --dry-run      print every unit it would write (`DRY: <name>.service ExecStart=...`,
#                  `DRY: <name>.timer OnCalendar=...`, then the FULL body of each under
#                  `DRY-UNIT: <name>.service` / `DRY-UNIT: <name>.timer`, indented four
#                  spaces) and every manager call it would make; write nothing, call nothing.
#                  Diff a DRY-UNIT block against `systemctl --user cat wizard-library-<name>`
#                  before a wet run onto an existing install.
#
# WHERE IT READS FROM: its OWN location. <root>/install/install_crons.sh reads
# <root>/install/cron_roster.json, <root>/cron/_config_dir.sh and <root>/cron/.
# Run from the installed brain, <root> is the brain dir; run by install.sh Step F,
# <root> is the package, whose bytes install/lay.py has just verified against the
# laid copies (and which is the only copy that exists on a --dry-run).
#
# Detects Linux (systemd user timers) vs macOS (launchd agents). ALL paths are
# resolved at install time from the environment / this machine -- no path is
# hardcoded, so it installs anywhere the brain lives.
#
# PREREQS / GRACEFUL SKIP:
#   * A unit is armed only if its organ script is present beside the brain
#     (on a --dry-run before the lay: present in the package that will be laid).
#     A missing script => that one unit is skipped WITH A LINE (never fatal).
#   * A unit that THINKS (the roster's `thinks`) is armed only when the brain records
#     the owner's answer to the overnight-thinker question (<brain>/THINKER_RECORD,
#     written by organs/thinker.py after a proof; e) not now counts) -- else this
#     script refuses rc 2 before writing anything, naming the question. The model
#     and its credential come from that record, never from this script.
#   * The roster and the packaged cron/ directory must AGREE (every packaged
#     timer classified); a disagreement is a refusal, named, before anything is
#     written.
#   * cron/_config_dir.sh (the one config-dir + launchd-label resolver) must be
#     present; its absence is an incomplete install, refused rc 2 -- never a
#     fallback literal (round-1 reflection, item 12).
#
# WHAT IT CALLS ON THE MANAGER (declared in install/write_set.json `verbs`; the
# cold gate grades a recorder's log against that declaration):
#   Linux:  systemctl --user daemon-reload
#           systemctl --user enable --now wizard-library-<unit>.timer   (per armed unit;
#              the manager writes ~/.config/systemd/user/timers.target.wants/<timer>)
#           systemctl --user list-timers 'wizard-library-*' --no-pager    (a read)
#   macOS:  launchctl unload <plist> (tolerated), launchctl load <plist>   (per armed unit)
#
# OPTIONAL ENV OVERRIDES:
#   WIZARD_LIBRARY_HOME   brain dir          (default ~/.claude/memory)
#   WIZARD_LIBRARY_DB     brain db           (default $WIZARD_LIBRARY_HOME/claude_memory.db)
#   WIZARD_PYTHON_BIN     the interpreter when no Tier-1 venv exists (default: python3 on PATH).
#                         Must be an ABSOLUTE path when set: it is written as the literal
#                         first token of every ExecStart= (a relative `./py` was measured
#                         rendered verbatim); anything else is refused rc 2.
#   WIZARD_CONFIG_DIR     the sidecar config dir holding sleep-loop-config.json (default:
#                         ~/.voidloom, legacy dir honoured -- cron/_config_dir.sh); written
#                         into the sleep-loop unit's --config, so it must be ABSOLUTE
#   WIZARD_DREAM_HOUR     dreaming hour      (default 22, local time; dreaming only)
#   (no GPU override: EVERY unit, pre-cognitive included, takes its pin from the env file below
#    -- the owner's WIZARD_CUDA_DEVICE or install.sh's block, the one writer of that pin. The old
#    WIZARD_PRECOG_CUDA_DEVICE rendered a unit pin from the INVOKING SHELL -- measured: '0' beside
#    a file declaring WIZARD_CUDA_DEVICE=1, that unit on card 0 -- and is no longer honoured.)
#   WIZARD_CRON_ONLY      comma list of roster units to arm instead of the default set
#   WIZARD_ENV_FILE       the sidecar env file the units read (default: the one
#                         install.sh wrote -- ~/.voidloom/wizard-library-env)
set -e

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "install_crons.sh: unknown arg: $arg (only --dry-run and --help are accepted)" >&2; exit 2 ;;
  esac
done

SELF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The four path overrides this script writes into every unit (WorkingDirectory=, ExecStart= --
# including the sleep-loop's --config under WIZARD_CONFIG_DIR -- and EnvironmentFile=) must be ABSOLUTE: a relative value, a quoted `~/x` or a literal `$HOME/x` would
# be resolved against the cwd here and spelled as given into the units (round-1 iteration-3
# reflection, item 2 -- the same refusal install.sh makes for WIZARD_CLAUDE_HOME / WIZARD_ENV_FILE).
# WIZARD_PYTHON_BIN joins them (reference-host round, R10d): it becomes the literal first token of
# every ExecStart=, and a relative value was measured rendered as `ExecStart=./rel_py -u ...`.
for _v in WIZARD_LIBRARY_HOME WIZARD_LIBRARY_DB WIZARD_ENV_FILE WIZARD_CONFIG_DIR WIZARD_PYTHON_BIN; do
  eval "_val=\${${_v}:-}"
  if [ -n "$_val" ]; then case "$_val" in /*) ;; *) echo "${_v} must be an ABSOLUTE path: '${_val}'" >&2; exit 2 ;; esac; fi
done
unset _v _val; case "${WIZARD_PYTHON_BIN:-}" in *[[:space:]]*) echo "WIZARD_PYTHON_BIN must not contain whitespace (it is the literal first token of every ExecStart=): '${WIZARD_PYTHON_BIN}'" >&2; exit 2 ;; esac; case "${WIZARD_PYTHON_BIN:-}" in *%*) echo "WIZARD_PYTHON_BIN must not contain '%' (systemd expands it as a unit specifier in ExecStart=): '${WIZARD_PYTHON_BIN}'" >&2; exit 2 ;; esac   # same line: the harness CITEs lines below
BRAIN_DIR="${WIZARD_LIBRARY_HOME:-${HOME}/.claude/memory}"
DB="${WIZARD_LIBRARY_DB:-${BRAIN_DIR}/claude_memory.db}"
HOUR="${WIZARD_DREAM_HOUR:-22}"
ROSTER="${SELF_ROOT}/install/cron_roster.json"
ROSTER_PY="${SELF_ROOT}/install/cron_roster.py"
CRON_DIR="${SELF_ROOT}/cron"

# python that can import sqlite_vec (prefer the Tier-1 venv); also reads the roster.
if [ -x "${BRAIN_DIR}/venv/bin/python3" ]; then PY="${BRAIN_DIR}/venv/bin/python3"
else PY="${WIZARD_PYTHON_BIN:-$(command -v python3 || echo /usr/bin/python3)}"; fi

# The sidecar config dir + the launchd label prefix: ONE resolver, shared with
# install.sh and the cron/setup-*.sh scripts (cron/_config_dir.sh). The manifest
# lays it; its absence is an incomplete install, not a reason for a second literal.
[ -f "${CRON_DIR}/_config_dir.sh" ] || { echo "incomplete install: ${CRON_DIR}/_config_dir.sh is absent (the manifest lays it) -- re-run install.sh" >&2; exit 2; }
. "${CRON_DIR}/_config_dir.sh"
CONFIG_DIR="${CONFIG_DIR_DEFAULT}"
SLEEP_CFG="$(_config_path sleep-loop-config.json)"
# The env file the units read: install.sh's rule, spelled the same way.
ENV_FILE_DEFAULT="${HOME}/.voidloom/wizard-library-env"
if [ -n "${WIZARD_ENV_FILE:-}" ]; then case "$WIZARD_ENV_FILE" in /*) ;; *) echo "WIZARD_ENV_FILE must be an ABSOLUTE path: '${WIZARD_ENV_FILE}'" >&2; exit 2 ;; esac; ENV_FILE="$WIZARD_ENV_FILE"
else ENV_FILE="$ENV_FILE_DEFAULT"; fi
OS_FOR_UNITS="$(uname -s)"
# EVERY path this renderer writes into a systemd unit line, validated as RESOLVED -- whatever set it (a flag, an
# override, the default under $HOME, the venv), since this is the ONE unit template (reference-host round, iteration
# 4, LOW 2: the class "a value that cannot be the literal text of a unit line"). systemd expands '%' as a specifier
# in ExecStart=, WorkingDirectory=, Environment= and EnvironmentFile= (measured with `systemd-analyze verify`,
# offline: ExecStart=<dir>/py%hdir/python3 ran <dir>/py<home>dir/python3); it SPLITS ExecStart= and Environment= on
# whitespace; and it GLOBS an EnvironmentFile= path ('*', '?', '[', '\' -- another file, or none, would load, and
# the leading '-' makes a miss silent). launchd plists quote every path, so this binds the systemd renderer only.
if [ "$OS_FOR_UNITS" = "Linux" ]; then
  for _t in "interpreter|${PY}" "brain dir (WIZARD_LIBRARY_HOME)|${BRAIN_DIR}" "brain db (WIZARD_LIBRARY_DB)|${DB}" \
            "sleep-loop config (WIZARD_CONFIG_DIR)|${SLEEP_CFG}" "~/.local/bin (HOME)|${HOME}/.local/bin"; do
    case "${_t#*|}" in *[[:space:]]*|*%*) echo "the ${_t%%|*} '${_t#*|}' cannot be written into a systemd unit: it holds whitespace (ExecStart= splits on it) or '%' (a unit specifier). Refusing to render anything." >&2; exit 2 ;; esac
  done
  case "$ENV_FILE" in *%*|*\**|*\?*|*\[*|*\\*) echo "the env file path '${ENV_FILE}' cannot be every unit's EnvironmentFile=: systemd expands '%' and GLOBS '*', '?', '[' (and '\\'). Refusing to render anything." >&2; exit 2 ;; esac
  unset _t
fi

# THE OVERNIGHT-THINKER GATE (model-choice unit, Round 1, Wave B -- M10). This script is the ONLY armer, so the gate
# lives here too: a unit that THINKS (install/cron_roster.json `thinks`) is armed only when the brain records the
# owner's answer to "Should your brain think while you sleep, and with which model?" (<brain>/THINKER_RECORD, written
# only by organs/thinker.py after a proof -- e) not now is an answer too). Otherwise: REFUSED rc 2 before anything is
# written, the question named word for word. A --dry-run (which arms and writes nothing) SAYS a wet run would refuse
# and still shows the units; a --dry-run from install.sh is told the answer that run settled (VOIDLOOM_THINKER_SETTLED)
# -- honoured ONLY with --dry-run.
[ -f "$ROSTER" ] && [ -f "$ROSTER_PY" ] || { echo "roster missing: ${ROSTER} / ${ROSTER_PY} -- re-run install.sh; the manifest lays both." >&2; exit 2; }
if [ -n "${WIZARD_CRON_ONLY:-}" ]; then _sel="$(printf '%s' "$WIZARD_CRON_ONLY" | tr ',' ' ')"
else _sel="$("$PY" "$ROSTER_PY" --roster "$ROSTER" armed | tr '\n' ' ')"; fi
_thinking=""
for _u in $("$PY" "$ROSTER_PY" --roster "$ROSTER" thinks | tr '\n' ' '); do
  for _s in $_sel; do [ "$_s" = "$_u" ] && _thinking="${_thinking:+$_thinking }$_u"; done
done
THINKER_STATE=""
if [ -n "$_thinking" ]; then
  THINKER_STATE="$(WIZARD_LIBRARY_DB="$DB" "$PY" -B -c '
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
try:
    import thinker
    r = thinker.read_record(Path(sys.argv[2]))
    print("backend=%s proven_at=%s" % (r["backend"], r.get("proven_at", "")) if r else "absent")
except Exception as e:   # a torn / edited record, or no seam: no answer
    print("refused: %s" % e)
' "${SELF_ROOT}/organs" "$(dirname "$DB")/THINKER_RECORD" 2>&1)"
  case "$THINKER_STATE" in
    backend=*) ;;
    *)
      if [ "$DRY_RUN" -eq 1 ] && [ -n "${VOIDLOOM_THINKER_SETTLED:-}" ]; then
        THINKER_STATE="backend=${VOIDLOOM_THINKER_SETTLED} (settled by this dry run; recorded only by a wet run)"
      elif [ "$DRY_RUN" -eq 1 ]; then
        # a dry run arms nothing and writes nothing: it SAYS the gate would refuse, and still shows the units
        echo "  DRY: a wet run would be REFUSED (rc 2): ${_thinking} think overnight, and the brain at $(dirname "$DB") records no answer to the overnight-thinker question (THINKER_RECORD: ${THINKER_STATE}) -- \`voidloom thinker set <claude|codex|kimi|gemini|local|off>\` records one."
        THINKER_STATE="unanswered (a wet run refuses)"
      else
        {
          echo "install_crons.sh: REFUSED: ${_thinking} think overnight, and the brain at $(dirname "$DB") records no answer to"
          echo "  the overnight-thinker question (THINKER_RECORD: ${THINKER_STATE}). Put this question to the owner, word for word:"
          "$PY" -B -c 'import json,sys; q=json.load(open(sys.argv[1], encoding="utf-8"))["question"]; print("    " + q["ask"]); [print("      %s) %s" % (o[0], o[1])) for o in q["options"]]; print("    " + q["chat_note"])' "${SELF_ROOT}/organs/thinkers.json" 2>/dev/null
          echo "  Record the owner's answer with: voidloom thinker set <claude|codex|kimi|gemini|local|off> (it proves it first), or run"
          echo "  install.sh in a terminal (it asks), or pass install.sh --thinker claude|codex|kimi|gemini|off|local. An installing AI agent must put the"
          echo "  question to the human and pass the human's answer -- it must never choose the answer itself. Nothing was written."
        } >&2
        exit 2
      fi ;;
  esac
fi
unset _sel _u _s

OS="$(uname -s)"
U="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
LA="$HOME/Library/LaunchAgents"
if [ "$DRY_RUN" -eq 0 ]; then
  [ "$OS" = "Linux" ]  && mkdir -p "$U"
  [ "$OS" = "Darwin" ] && mkdir -p "$LA" "${BRAIN_DIR}/logs"
fi

[ -f "$ROSTER" ] && [ -f "$ROSTER_PY" ] || { echo "roster missing: ${ROSTER} / ${ROSTER_PY} -- re-run install.sh; the manifest lays both." >&2; exit 2; }

# The roster and the packaged cron/ directory must agree before anything is armed.
"$PY" "$ROSTER_PY" --roster "$ROSTER" check --cron-dir "$CRON_DIR" || {
  echo "install/cron_roster.json and ${CRON_DIR} disagree (names above). Refusing to arm." >&2; exit 1; }

PLAT="systemd"; [ "$OS" = "Darwin" ] && PLAT="launchd"
if [ -n "${WIZARD_CRON_ONLY:-}" ]; then
  ROWS_ARGS="--only ${WIZARD_CRON_ONLY}"
else
  ROWS_ARGS="--set armed"
fi
ROWS_FILE="$(mktemp)"
# shellcheck disable=SC2086
"$PY" "$ROSTER_PY" --roster "$ROSTER" rows $ROWS_ARGS --platform "$PLAT" > "$ROWS_FILE" || {
  rm -f "$ROWS_FILE"; echo "could not read the roster rows (see above)." >&2; exit 1; }

# {DB} {CFG} {BRAIN} {HOME_LOCAL_BIN} -> this install's paths.
substitute_exec() {
  local e="$1"
  e="${e//\{DB\}/$DB}"; e="${e//\{CFG\}/$SLEEP_CFG}"; e="${e//\{BRAIN\}/$BRAIN_DIR}"
  e="${e//\{HOME_LOCAL_BIN\}/${HOME}/.local/bin}"
  printf '%s' "$e"
}

# Which section a roster extra line belongs to, by its key: systemd refuses a
# [Unit] key inside [Service] (Documentation=, After=) and an [Install] key
# anywhere else (WantedBy=). Everything else is the unit's main section.
extra_section() {
  case "${1%%=*}" in
    Documentation|After|Before|Wants|Requires|Requisite|Conflicts|PartOf|BindsTo|Condition*|Assert*) echo unit ;;
    WantedBy|RequiredBy|Also|Alias|DefaultInstance) echo install ;;
    *) echo main ;;
  esac
}
# print_extras <section> <;-joined lines> -- every extra of that section, substituted.
print_extras() {
  local want="$1" lines="$2" l arr
  [ -n "$lines" ] || return 0
  IFS=';' read -ra arr <<< "$lines"        # read -ra: no glob expansion of a `*` in an OnCalendar=
  for l in "${arr[@]}"; do
    [ -n "$l" ] || continue
    case "$l" in After=*) continue ;; esac   # After= replaces the template line (CRON_AFTER), never repeats
    [ "$(extra_section "$l")" = "$want" ] && { substitute_exec "$l"; printf '\n'; }
  done
  return 0
}
# omitted <line> -- true if a template line is listed in this unit's service_omit (prefix match)
omitted() {
  local line="$1" o arr
  [ -n "${CRON_OMIT:-}" ] || return 1
  IFS=';' read -ra arr <<< "$CRON_OMIT"
  for o in "${arr[@]}"; do
    [ -n "$o" ] || continue
    case "$line" in "$o"*) return 0 ;; esac
  done
  return 1
}

# launchd StartCalendarInterval from the roster's launchd spec.
launchd_calendar() {
  local spec="$1"; local kind="${spec%%:*}"; local rest="${spec#*:}"
  case "$kind" in
    daily)
      local hh="${rest%%:*}"; local mm="${rest#*:}"
      printf '<dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>' "$((10#$hh))" "$((10#$mm))" ;;
    weekly)
      local dow="${rest%%:*}"; local t="${rest#*:}"; local hh="${t%%:*}"; local mm="${t#*:}"
      printf '<dict><key>Weekday</key><integer>%d</integer><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>' "$((10#$dow))" "$((10#$hh))" "$((10#$mm))" ;;
    monthly)
      local dd="${rest%%:*}"; local t="${rest#*:}"; local hh="${t%%:*}"; local mm="${t#*:}"
      printf '<dict><key>Day</key><integer>%d</integer><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>' "$((10#$dd))" "$((10#$hh))" "$((10#$mm))" ;;
    hourly)
      printf '<dict><key>Minute</key><integer>%d</integer></dict>' "$((10#$rest))" ;;
    halfhourly)
      # HH-HH:MM,MM -> an array of {Hour,Minute} entries (pre-cognitive: 08-21:15,45)
      local hours="${rest%%:*}"; local mins="${rest#*:}"; local h0="${hours%%-*}"; local h1="${hours#*-}"
      printf '<array>'
      local h; h=$((10#$h0))
      while [ "$h" -le "$((10#$h1))" ]; do
        local m; for m in ${mins//,/ }; do
          printf '<dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>' "$h" "$((10#$m))"
        done
        h=$((h + 1))
      done
      printf '</array>' ;;
    *)
      echo "launchd calendar kind '${kind}' is not one this installer renders" >&2; return 1 ;;
  esac
}

LINUX_INSTALLED=0
ARMED_NAMES=""

# THE unit template, in two renderers that print a body to stdout; install_linux
# either writes them (wet) or prints them under DRY-UNIT: (dry). Every generated
# service carries `EnvironmentFile=-<env file>` (leading `-`: an absent file is not
# fatal), exactly as the packaged cron/*.service do, and no inline CUDA pin -- the
# pin reaches the unit through the env file, which has exactly one writer
# (install.sh). A template line is omitted when the roster's service_omit names it;
# the roster's service_extra / timer_extra lines are added, routed by key.
# render_service <description> <timeout> <ExecStart...>   (reads CRON_AFTER, CRON_ENV_LINE,
#                CRON_EXEC_POST, CRON_SVC_EXTRA, CRON_OMIT)
render_service() {
  local desc="$1" timeout="$2"; shift 2
  local l
  echo "[Unit]"
  echo "Description=${desc}"
  echo "After=${CRON_AFTER:-network.target}"
  print_extras unit "${CRON_SVC_EXTRA:-}"
  echo "[Service]"
  for l in "Type=oneshot" "Environment=PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring"; do
    omitted "$l" || echo "$l"
  done
  [ -n "${CRON_ENV_LINE:-}" ] && echo "${CRON_ENV_LINE}"
  omitted "EnvironmentFile=" || echo "EnvironmentFile=-${ENV_FILE}"
  echo "ExecStart=$*"
  [ -n "${CRON_EXEC_POST:-}" ] && echo "ExecStartPost=${CRON_EXEC_POST}"
  for l in "WorkingDirectory=${BRAIN_DIR}" "StandardOutput=journal" "StandardError=journal"; do
    omitted "$l" || echo "$l"
  done
  [ -n "$timeout" ] && echo "TimeoutStartSec=${timeout}"
  print_extras main "${CRON_SVC_EXTRA:-}"
  if [ -n "$(print_extras install "${CRON_SVC_EXTRA:-}")" ]; then
    echo "[Install]"
    print_extras install "${CRON_SVC_EXTRA:-}"
  fi
  return 0
}
# render_timer <description> <OnCalendar>   (reads CRON_TMR_EXTRA)
render_timer() {
  local desc="$1" oncal="$2"
  echo "[Unit]"
  echo "Description=${desc} Timer"
  print_extras unit "${CRON_TMR_EXTRA:-}"
  echo "[Timer]"
  echo "OnCalendar=${oncal}"
  echo "Persistent=true"
  print_extras main "${CRON_TMR_EXTRA:-}"
  echo "[Install]"
  echo "WantedBy=timers.target"
  print_extras install "${CRON_TMR_EXTRA:-}"
  return 0
}

# install_linux <name> <description> <OnCalendar> <timeout> <ExecStart...>
install_linux() {
  local name="$1" desc="$2" oncal="$3" timeout="$4"; shift 4
  local svc="$U/wizard-library-${name}.service"
  local tmr="$U/wizard-library-${name}.timer"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  DRY: ${name}.service ExecStart=$*"
    [ -n "${CRON_EXEC_POST:-}" ] && echo "  DRY: ${name}.service ExecStartPost=${CRON_EXEC_POST}"
    echo "  DRY: ${name}.timer  OnCalendar=${oncal}"
    echo "  DRY-UNIT: ${name}.service"
    render_service "$desc" "$timeout" "$@" | sed 's/^/    /'
    echo "  DRY-UNIT: ${name}.timer"
    render_timer "$desc" "$oncal" | sed 's/^/    /'
    ARMED_NAMES="${ARMED_NAMES} ${name}"
    return 0
  fi
  render_service "$desc" "$timeout" "$@" > "$svc"
  render_timer "$desc" "$oncal" > "$tmr"
  LINUX_INSTALLED=1
  ARMED_NAMES="${ARMED_NAMES} ${name}"
  echo "  + ${name}: systemd user timer (${oncal})"
}

# install_macos <name> <description> -- <ExecStart args...>; calendar via CRON_PLIST_CAL
install_macos() {
  local name="$1" desc="$2"; shift 2
  local label="${LAUNCHD_PREFIX}.${name}"
  local plist="$LA/${label}.plist"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  DRY: ${label}.plist exec=$* when=${CRON_PLIST_WHEN:-?}"
    ARMED_NAMES="${ARMED_NAMES} ${name}"
    return 0
  fi
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0">'
    echo '<dict>'
    echo "  <key>Label</key><string>${label}</string>"
    echo '  <key>ProgramArguments</key>'
    echo '  <array>'
    for a in "$@"; do echo "    <string>${a}</string>"; done
    echo '  </array>'
    echo '  <key>EnvironmentVariables</key>'
    echo '  <dict>'
    echo "    <key>WIZARD_LIBRARY_DB</key><string>${DB}</string>"
    echo "    <key>WIZARD_LIBRARY_HOME</key><string>${BRAIN_DIR}</string>"
    echo "    <key>PYTHON_KEYRING_BACKEND</key><string>keyring.backends.null.Keyring</string>"
    [ -n "${CRON_PLIST_ENV_EXTRA:-}" ] && echo "    ${CRON_PLIST_ENV_EXTRA}"
    echo '  </dict>'
    echo "  <key>WorkingDirectory</key><string>${BRAIN_DIR}</string>"
    echo '  <key>StartCalendarInterval</key>'
    echo "  ${CRON_PLIST_CAL}"
    echo "  <key>StandardOutPath</key><string>${BRAIN_DIR}/logs/${name}.out.log</string>"
    echo "  <key>StandardErrorPath</key><string>${BRAIN_DIR}/logs/${name}.err.log</string>"
    echo '  <key>ProcessType</key><string>Background</string>'
    echo '</dict>'
    echo '</plist>'
  } > "$plist"
  launchctl unload "$plist" 2>/dev/null || true
  launchctl load "$plist"
  ARMED_NAMES="${ARMED_NAMES} ${name}"
  echo "  + ${name}: launchd agent -> ${plist}"
}

echo "Installing Voidloom maintenance crons (OS: ${OS})$([ "$DRY_RUN" -eq 1 ] && echo ' -- DRY RUN, nothing written, nothing called') ..."
echo "  brain:  ${BRAIN_DIR}"
echo "  python: ${PY}"
echo "  roster: ${ROSTER} ($( [ -n "${WIZARD_CRON_ONLY:-}" ] && echo "WIZARD_CRON_ONLY=${WIZARD_CRON_ONLY}" || echo 'armed-by-default set' ))"
echo "  env:    ${ENV_FILE}"

# The sleep loop's config lives in the sidecar dir; copied ONLY if absent.
ensure_sleep_config() {
  if [ -f "$SLEEP_CFG" ]; then echo "  = sleep-loop config present: ${SLEEP_CFG}"; return 0; fi
  if [ "$DRY_RUN" -eq 1 ]; then echo "  DRY: would create the sleep-loop config from the package default: ${SLEEP_CFG}"; return 0; fi
  mkdir -p "$CONFIG_DIR"
  cp "${CRON_DIR}/sleep-loop-config.json" "$SLEEP_CFG"
  echo "  + sleep-loop config created: ${SLEEP_CFG}"
}

if [ -n "${WIZARD_PRECOG_CUDA_DEVICE+x}" ]; then
  echo "  NOTE: WIZARD_PRECOG_CUDA_DEVICE is set in this shell and is NOT used: every unit, pre-cognitive included, takes" >&2
  echo "        its GPU from ${ENV_FILE} (declare WIZARD_CUDA_DEVICE=<n> there, outside the managed block)." >&2
fi
while IFS='|' read -r name execrel execpost oncal launchd timeout desc svc_extra tmr_extra svc_omit; do
  [ -n "$name" ] || continue
  organ="${execrel%% *}"                       # the first token: organs/<x>.py
  if [ ! -f "${BRAIN_DIR}/${organ}" ]; then
    if [ "$DRY_RUN" -eq 1 ] && [ -f "${SELF_ROOT}/${organ}" ]; then
      : # a dry run before the lay: the package ships it and Step A will lay it beside the brain
    else
      echo "  - ${name}: ${organ} not found beside the brain — skipped"
      continue
    fi
  fi
  case "$execrel" in *"{CFG}"*) ensure_sleep_config ;; esac
  execabs="${BRAIN_DIR}/$(substitute_exec "$execrel")"
  CRON_EXEC_POST=""
  [ -n "$execpost" ] && CRON_EXEC_POST="$PY -u ${BRAIN_DIR}/$(substitute_exec "$execpost")"
  # the per-unit override that predates the roster and is kept: the dreaming hour. (The pre-cognitive CUDA pin from the
  # invoking shell is GONE -- env-file writer pass, iteration 4, O1: a unit's pin is the owner's declaration in the env
  # file every unit reads, never a variable of the shell that happened to run this script.)
  if [ "$name" = "dreaming" ] && [ "$HOUR" != "22" ]; then
    oncal="*-*-* ${HOUR}:00:00"; launchd="daily:${HOUR}:00"
  fi
  # the roster's per-unit extras: an After= among them replaces the template's line
  CRON_SVC_EXTRA="$svc_extra"; CRON_TMR_EXTRA="$tmr_extra"; CRON_OMIT="$svc_omit"; CRON_AFTER=""
  case ";${svc_extra};" in *";After="*) CRON_AFTER=";${svc_extra}"; CRON_AFTER="${CRON_AFTER#*;After=}"; CRON_AFTER="${CRON_AFTER%%;*}" ;; esac
  CRON_ENV_LINE=""; CRON_PLIST_ENV_EXTRA=""
  if [ "$OS" = "Linux" ]; then
    # shellcheck disable=SC2086
    install_linux "$name" "Voidloom ${desc}" "$oncal" "$timeout" "$PY" -u $execabs
  elif [ "$OS" = "Darwin" ]; then
    CRON_PLIST_WHEN="$launchd"
    CRON_PLIST_CAL="$(launchd_calendar "$launchd")" || { echo "  - ${name}: no launchd calendar — skipped" >&2; continue; }
    # shellcheck disable=SC2086
    install_macos "$name" "Voidloom ${desc}" "$PY" -u $execabs
  fi
done < "$ROWS_FILE"
rm -f "$ROWS_FILE"

# ===========================================================================
# Finalize -- the manager calls, declared in install/write_set.json `verbs`
# ===========================================================================
if [ "$OS" = "Linux" ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  DRY: systemctl --user daemon-reload"
    for t in $ARMED_NAMES; do echo "  DRY: systemctl --user enable --now wizard-library-${t}.timer"; done
  elif [ "$LINUX_INSTALLED" = "1" ]; then
    systemctl --user daemon-reload
    for t in $ARMED_NAMES; do
      systemctl --user enable --now "wizard-library-${t}.timer" 2>/dev/null || echo "  ! could not enable wizard-library-${t}.timer (no user systemd session?)"
    done
    echo "Enabled timers:"
    systemctl --user list-timers 'wizard-library-*' --no-pager 2>/dev/null || true
  else
    echo "Nothing armed (every selected unit's organ was absent)."
  fi
elif [ "$OS" = "Darwin" ]; then
  : # launchd agents are loaded as they are installed above.
else
  echo "Unsupported OS: ${OS}. Crons not installed (Linux/macOS only)."
  exit 1
fi

echo ""
echo "Armed:${ARMED_NAMES:- (none)}"
echo "Reminders:"
if [ -n "$_thinking" ]; then
  case "$THINKER_STATE" in
    backend=off*) echo "  * ${_thinking}: e) not now -- they run their simple nightly routines; nothing thinks overnight."
                  echo "    Switch it on later with: voidloom thinker set <claude|codex|kimi|gemini|local> (it proves the model first)." ;;
    unanswered*)  echo "  * ${_thinking}: the overnight-thinker question has no recorded answer -- a wet run refuses to arm them." ;;
    *)            echo "  * ${_thinking} think with the overnight thinker the brain records (${THINKER_STATE#backend=})."
                  echo "    See it with: voidloom thinker status; change it with: voidloom thinker set <claude|codex|kimi|gemini|local|off>." ;;
  esac
fi
echo "  * every other packaged timer ships as a TEMPLATE with a reason in ${ROSTER};"
echo "    arm one deliberately:  WIZARD_CRON_ONLY=<name> bash ${BRAIN_DIR}/install/install_crons.sh"
echo "  * pre-cognitive loads the embedding model on the card the env file declares (WIZARD_CUDA_DEVICE=<n>"
echo "    outside its managed block), like every unit. The session-start banner itself never loads a model."
