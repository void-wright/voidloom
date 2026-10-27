#!/usr/bin/env bash
# ============================================================================
# Voidloom — Persona Forge installer (additive, reversible, never-clobber)
# ============================================================================
# Installs the Persona Forge, the guided conversation in which the HUMAN designs
# their own working persona for this brain:
#   * the persona-forge skill  -> ~/.claude/skills/persona-forge/
#   * the /persona-forge command -> ~/.claude/commands/persona-forge.md
#
# install.sh CALLS this (it is not optional) after its acceptance gate has
# passed, so the forge only arrives on a brain already verified alive. The
# forge itself runs nothing: it is static skill + command files. The first
# Claude Code session afterwards offers it (the session-start preamble carries a
# short neutral offer while `voidloom persona status` is absent), or the human
# types /persona-forge.
#
# SAFETY CONTRACT (the same as install/install_council.sh):
#   * ADDITIVE      — only adds the forge skill + its command.
#   * REVERSIBLE    — a same-named item that DIFFERS from the packaged one is
#                     moved aside to *.voidloom-bak.<ts> before anything is
#                     written. An identical one is left exactly as it is (a
#                     re-install makes no backup litter).
#   * NEVER CLOBBER — nothing the user already has is destroyed.
#
# USAGE
#   A. Sourced by install.sh:   source install/install_persona_forge.sh; install_persona_forge
#      (inherits DRY_RUN / CLAUDE_HOME / PKG_ROOT / say() from the caller)
#   B. Standalone:
#          bash install/install_persona_forge.sh            # install
#          bash install/install_persona_forge.sh --dry-run  # print what would happen
#          bash install/install_persona_forge.sh --help
#      Honors WIZARD_CLAUDE_HOME for the install root; finds the forge files
#      relative to this script (the package checkout).
# ============================================================================

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    set -uo pipefail
    _PF_DRY_RUN=0
    for arg in "$@"; do
        case "$arg" in
            --dry-run) _PF_DRY_RUN=1 ;;
            --help|-h)
                sed -n '2,35p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
                exit 0 ;;
            *) echo "[persona-forge] Unknown arg: $arg (try --help)"; exit 2 ;;
        esac
    done
    : "${CLAUDE_HOME:=${WIZARD_CLAUDE_HOME:-${HOME}/.claude}}"
    : "${PKG_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
    DRY_RUN="${_PF_DRY_RUN}"
    say() { echo "[persona-forge] $*"; }
    _PF_STANDALONE=1
else
    : "${CLAUDE_HOME:=${WIZARD_CLAUDE_HOME:-${HOME}/.claude}}"
    : "${PKG_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
    : "${DRY_RUN:=0}"
    type say >/dev/null 2>&1 || say() { echo "[persona-forge] $*"; }
    _PF_STANDALONE=0
fi

# a write, or its DRY line -- argv passed as-is, never through eval
_pf_run() { if [ "${DRY_RUN}" -eq 1 ]; then echo "  DRY: $*"; else "$@"; fi; }
_PF_FAILED=0
_PF_LAID_NEW=0      # 1 when this run copied anything (a first lay or a changed forge); install.sh asks its hook question only then
_PF_SKILL="persona-forge"
_PF_CMD="persona-forge.md"

# _pf_place SRC DST -- identical: leave it; different: move aside, then copy; absent: copy.
_pf_place() {
    local src="$1" dst="$2" ts bak
    if [ ! -e "$src" ]; then
        say "  MISSING in package: ${src}"; _PF_FAILED=1; return 1
    fi
    if [ -e "$dst" ] || [ -L "$dst" ]; then
        if [ ! -L "$dst" ] && diff -rq "$src" "$dst" >/dev/null 2>&1; then
            say "  already current: ${dst}"
            return 0
        fi
        ts="$(date +%Y%m%d-%H%M%S)"
        bak="${dst}.voidloom-bak.${ts}"
        say "  EXISTS and differs — backing up your current item first:"
        say "      ${dst}  ->  ${bak}"
        _pf_run mv "${dst}" "${bak}" || { say "  FAILED: could not move ${dst} aside; it was NOT replaced."; _PF_FAILED=1; return 1; }
    fi
    _PF_LAID_NEW=1
    if [ -d "$src" ]; then
        if _pf_run mkdir -p "${dst}" && _pf_run cp -R "${src}/." "${dst}/"; then
            say "  $([ "${DRY_RUN}" -eq 1 ] && echo "would be installed" || echo installed) (dir):  ${dst}"
        else
            say "  FAILED: ${dst} was not installed (the copy above failed)."; _PF_FAILED=1; return 1
        fi
    else
        if _pf_run mkdir -p "$(dirname "${dst}")" && _pf_run cp -f "${src}" "${dst}"; then
            say "  $([ "${DRY_RUN}" -eq 1 ] && echo "would be installed" || echo installed) (file): ${dst}"
        else
            say "  FAILED: ${dst} was not installed (the copy above failed)."; _PF_FAILED=1; return 1
        fi
    fi
}

install_persona_forge() {
    local skill_src="${PKG_ROOT}/persona/skills/${_PF_SKILL}"
    local cmd_src="${PKG_ROOT}/persona/commands/${_PF_CMD}"
    local skills_dst="${CLAUDE_HOME}/skills"
    local cmds_dst="${CLAUDE_HOME}/commands"

    say "Persona Forge -> ${skills_dst}/${_PF_SKILL}/  +  ${cmds_dst}/${_PF_CMD}  (invoke as /persona-forge)"
    [ "${DRY_RUN}" -eq 1 ] && say "** DRY RUN — no changes will be made **"

    if [ ! -f "${skill_src}/SKILL.md" ] || [ ! -f "${cmd_src}" ]; then
        if [ -f "${skills_dst}/${_PF_SKILL}/SKILL.md" ] && [ -f "${cmds_dst}/${_PF_CMD}" ]; then
            say "  forge source not in ${PKG_ROOT} (run from the package checkout to refresh); the installed copy is present."
            return 0
        fi
        say "  FAILED: the forge files are missing from the package (${skill_src}/SKILL.md, ${cmd_src})."
        return 1
    fi

    _pf_run mkdir -p "${skills_dst}" "${cmds_dst}" || { say "FAILED: could not create ${skills_dst} / ${cmds_dst}."; return 1; }
    _pf_place "${skill_src}" "${skills_dst}/${_PF_SKILL}"
    _pf_place "${cmd_src}" "${cmds_dst}/${_PF_CMD}"

    if [ "${_PF_FAILED}" -ne 0 ]; then
        say "Persona Forge NOT fully installed: the FAILED lines above name what was not copied."
        return 1
    fi
    say "Persona Forge $([ "${DRY_RUN}" -eq 1 ] && echo "would be installed" || echo installed). To remove later: delete ${skills_dst}/${_PF_SKILL} and ${cmds_dst}/${_PF_CMD}"
    say "(any *.voidloom-bak.* items beside them are your originals — restore by renaming back)"
    return 0
}

if [ "${_PF_STANDALONE}" -eq 1 ]; then
    install_persona_forge
    exit $?
fi
