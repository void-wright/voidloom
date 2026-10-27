#!/usr/bin/env bash
# uninstall.sh -- remove what install.sh put on this machine.
#
#   bash uninstall.sh                         preview: print everything it would remove, change nothing
#   bash uninstall.sh --yes                   remove the scheduled jobs, the session hooks and instruction
#                                             blocks, the `voidloom` command and the installed code --
#                                             KEEPING your memories (the brain database, its pre-upgrade
#                                             copies, the install records) and your env file
#   bash uninstall.sh --yes --remove-brain    ALSO delete the brain folder and ~/.voidloom (your memories,
#                                             your env file and any saved model sign-in), after you type
#                                             a confirmation phrase
#
# What it never touches: downloaded model caches (~/.cache/huggingface), pip caches, a persona you
# forged (it lives under ~/.claude/skills/<your-persona>), the AI tools themselves (claude, codex,
# kimi, agy) and their own sign-ins, and any file it did not install. Run it from a clone of the
# repository. Every path honours the same overrides install.sh does (WIZARD_CLAUDE_HOME, XDG_CONFIG_HOME).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YES=0; REMOVE_BRAIN=0
for a in "$@"; do
    case "$a" in
        --yes) YES=1 ;;
        --remove-brain) REMOVE_BRAIN=1 ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "uninstall: unknown option '$a' (try --help)" >&2; exit 2 ;;
    esac
done

CLAUDE_HOME="${WIZARD_CLAUDE_HOME:-${HOME}/.claude}"
BRAIN_DIR="${CLAUDE_HOME}/memory"
SKILLS_DIR="${CLAUDE_HOME}/skills"
LOCAL_BIN="${HOME}/.local/bin"
ENV_DIR="${HOME}/.voidloom"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
LAUNCHD_DIR="${HOME}/Library/LaunchAgents"
LAUNCHD_PREFIX="voidloom"

MANIFEST="${BRAIN_DIR}/install/lay_manifest.json"
[ -f "$MANIFEST" ] || MANIFEST="${HERE}/install/lay_manifest.json"
ROSTER="${BRAIN_DIR}/install/cron_roster.json"
[ -f "$ROSTER" ] || ROSTER="${HERE}/install/cron_roster.json"
[ -f "$MANIFEST" ] && [ -f "$ROSTER" ] || { echo "uninstall: cannot find install/lay_manifest.json and install/cron_roster.json -- run this from a clone of the repository" >&2; exit 2; }

PY=""
for c in "${BRAIN_DIR}/venv/bin/python3" "$(sed -n 's/^interpreter=//p' "${BRAIN_DIR}/INSTALL_RECORD" 2>/dev/null)" python3; do
    [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
[ -n "$PY" ] || { echo "uninstall: no python3 found" >&2; exit 2; }

say() { printf '%s\n' "$*"; }
act() {  # act <description> <command...>: print in preview, run with --yes
    if [ "$YES" = 1 ]; then
        "${@:2}" >/dev/null 2>&1 && say "  removed  $1" || say "  (skipped) $1"
    else
        say "  would remove  $1"
    fi
}

if [ "$YES" = 1 ]; then say "Voidloom uninstall"; else say "Voidloom uninstall -- PREVIEW (nothing is changed; add --yes to do it)"; fi
say ""

# 1. scheduled jobs ------------------------------------------------------------------------------
say "1. Scheduled jobs"
UNITS="$("$PY" -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["units"]))' "$ROSTER")"
found=0
for u in $UNITS; do
    for kind in timer service; do
        f="${SYSTEMD_USER_DIR}/wizard-library-${u}.${kind}"
        [ -e "$f" ] || continue
        found=1
        if [ "$YES" = 1 ] && [ "$kind" = timer ] && command -v systemctl >/dev/null 2>&1; then
            systemctl --user disable --now "wizard-library-${u}.timer" >/dev/null 2>&1 || true
        fi
        act "$f" rm -f "$f"
        act "${SYSTEMD_USER_DIR}/timers.target.wants/wizard-library-${u}.timer" rm -f "${SYSTEMD_USER_DIR}/timers.target.wants/wizard-library-${u}.timer"
    done
    p="${LAUNCHD_DIR}/${LAUNCHD_PREFIX}.${u}.plist"
    if [ -e "$p" ]; then
        found=1
        [ "$YES" = 1 ] && command -v launchctl >/dev/null 2>&1 && launchctl unload "$p" >/dev/null 2>&1
        act "$p" rm -f "$p"
    fi
done
[ "$found" = 1 ] || say "  none installed"
[ "$YES" = 1 ] && [ "$found" = 1 ] && command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload >/dev/null 2>&1

# 2. session hooks and instruction blocks --------------------------------------------------------
say ""
say "2. Session hooks (Claude Code, Codex) and instruction blocks (CLAUDE.md / AGENTS.md)"
if [ -d "${BRAIN_DIR}/voidloom" ]; then
    if [ "$YES" = 1 ]; then
        for plat in claude codex; do
            (cd "$BRAIN_DIR" && "$PY" -m voidloom hooks --uninstall --yes --platform "$plat" >/dev/null 2>&1) \
                && say "  removed  the ${plat} session hook (if it was installed)" \
                || say "  (check)  the ${plat} hook: run 'voidloom hooks --status' -- it may not have been installed"
        done
        (cd "$BRAIN_DIR" && "$PY" -m voidloom instructions uninstall --yes >/dev/null 2>&1) \
            && say "  removed  the Voidloom instruction blocks" \
            || say "  (check)  instruction blocks: run 'voidloom instructions status'"
    else
        say "  would run  voidloom hooks --uninstall --yes --platform claude   (and --platform codex)"
        say "  would run  voidloom instructions uninstall --yes"
    fi
else
    say "  the installed package is not present; nothing to unhook"
fi

# 3. the command, the skills and the installed code ----------------------------------------------
say ""
say "3. The voidloom command, the installed skills and the installed code"
if [ -L "${LOCAL_BIN}/voidloom" ]; then
    act "${LOCAL_BIN}/voidloom" rm -f "${LOCAL_BIN}/voidloom"
fi
laid_files() {  # every installed file the manifest names (a function: bash 3.2 cannot parse a heredoc inside $( ))
    "$PY" - "$MANIFEST" "$BRAIN_DIR" "$SKILLS_DIR" "$CLAUDE_HOME" <<'PYEOF'
import json, sys, os
m, brain, skills, home = sys.argv[1:5]
d = json.load(open(m))
entries = d["entries"] if isinstance(d, dict) else d
roots = {"brain": brain, "skills": skills, "home": home}
for e in entries:
    if e.get("root") in roots and e.get("dst"):
        p = os.path.join(roots[e["root"]], e["dst"])
        if os.path.isfile(p) or os.path.islink(p):
            print(p)
PYEOF
}
LAID="$(laid_files)"
n=0
while IFS= read -r f; do
    [ -n "$f" ] || continue
    n=$((n + 1))
    [ "$YES" = 1 ] && rm -f "$f"
done <<< "$LAID"
if [ "$YES" = 1 ]; then say "  removed  ${n} installed file(s) (code, skills, commands)"; else say "  would remove  ${n} installed file(s) (code, skills, commands) listed in install/lay_manifest.json"; fi
for d in "${BRAIN_DIR}/venv"; do
    [ -d "$d" ] && act "$d (the Python environment)" rm -rf "$d"
done
if [ "$YES" = 1 ]; then
    find "$BRAIN_DIR" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
    find "$BRAIN_DIR" "$SKILLS_DIR/persona-forge" "$SKILLS_DIR/memory-operator" -depth -type d -empty -delete 2>/dev/null
    for c in council-architect council-dreamer council-seer council-skeptic council-synthesizer; do
        find "$SKILLS_DIR/$c" -depth -type d -empty -delete 2>/dev/null
    done
fi

# 4. memories (only with --remove-brain) ---------------------------------------------------------
say ""
if [ "$REMOVE_BRAIN" = 1 ]; then
    say "4. Your memories and settings -- --remove-brain"
    if [ "$YES" = 1 ]; then
        PHRASE="delete my memories"
        if [ -t 0 ]; then
            printf 'This permanently deletes %s and %s.\nType "%s" to continue: ' "$BRAIN_DIR" "$ENV_DIR" "$PHRASE"
            read -r answer
        else
            answer="${VOIDLOOM_CONFIRM_REMOVE_BRAIN:-}"
        fi
        if [ "$answer" = "$PHRASE" ]; then
            rm -rf "$BRAIN_DIR" && say "  removed  $BRAIN_DIR"
            rm -rf "$ENV_DIR" && say "  removed  $ENV_DIR"
        else
            say "  NOT removed: the confirmation phrase did not match. Your memories are untouched."
            [ -t 0 ] || say "  (no terminal: set VOIDLOOM_CONFIRM_REMOVE_BRAIN=\"$PHRASE\" to confirm)"
        fi
    else
        say "  would delete  $BRAIN_DIR  (your memories, records and pre-upgrade copies)"
        say "  would delete  $ENV_DIR  (your env file and any saved model sign-in)"
    fi
else
    say "4. Kept: your memories and settings"
    [ -d "$BRAIN_DIR" ] && say "  kept  $BRAIN_DIR  (the brain database and its records)"
    [ -d "$ENV_DIR" ] && say "  kept  $ENV_DIR  (your env file)"
    say "  to delete them as well: bash uninstall.sh --yes --remove-brain"
fi

say ""
say "Not touched: model caches (~/.cache/huggingface -- remove by hand if you want the space back),"
say "a persona you forged under ${SKILLS_DIR}/, and the AI tools and their own sign-ins."
exit 0
