#!/usr/bin/env bash
# Wizard Library Federation: Platform Detection + Cron Setup Generator
#
# Detects the current platform (WSL2 / Linux / macOS) and generates the
# correct systemd service/timer files or launchd plist files from the
# WizardConfig settings.
#
# Usage:
#   ./platform-detection.sh detect              # Print detected platform
#   ./platform-detection.sh generate [--dry-run] # Generate cron files
#   ./platform-detection.sh install [--dry-run]  # Generate + install
#   ./platform-detection.sh status               # Show installed timers
#   ./platform-detection.sh test                 # Self-test
#
# Reads configuration from:
#   1. <config dir>/wizard-config.json (if exists) -- see _config_path below
#   2. WIZARD_* env vars (override JSON)
#   3. Built-in defaults (Tier-1 desktop)
#
# Design references:
#   - Memory #3674 (WSL2 HYBRID cron pattern)
#   - Memory #4032 (brain power tiers)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The config-dir resolver and LAUNCHD_PREFIX live in ONE sourced file so
# the setup scripts and this generator cannot drift.
. "${SCRIPT_DIR}/_config_dir.sh"
CONFIG_FILE="${WIZARD_CONFIG_PATH:-$(_config_path wizard-config.json)}"
LOG_DIR="$(_config_path logs)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
LAUNCHD_AGENTS_DIR="${HOME}/Library/LaunchAgents"
GENERATED_DIR="${SCRIPT_DIR}/generated"

# ============================================================
# Config reading (lightweight JSON parsing via python3)
# ============================================================

_read_config_key() {
    # Read a key from wizard-config.json. Falls back to $2 if missing.
    local key="$1"
    local default="${2:-}"

    # Env var override (WIZARD_<UPPER_KEY>)
    local env_var="WIZARD_$(echo "$key" | tr '[:lower:]' '[:upper:]')"
    local env_val="${!env_var:-}"
    if [ -n "$env_val" ]; then
        echo "$env_val"
        return
    fi

    # JSON file
    if [ -f "$CONFIG_FILE" ]; then
        local val
        val=$(python3 -c "
import json, sys
try:
    with open('$CONFIG_FILE') as f:
        d = json.load(f)
    v = d.get('$key', '')
    if v is not None and v != '' and v is not False: print(v)
    else: sys.exit(1)
except: sys.exit(1)
" 2>/dev/null) && echo "$val" && return
    fi

    echo "$default"
}

# Read config values
CRON_PATTERN=$(_read_config_key "cron_pattern" "wsl2_hybrid")
# NO packaged zone (reference-host round, R3 / iteration 2, item 7): an empty zone renders
# `OnCalendar=<time>` with no suffix, which systemd reads in the HOST's local time -- the
# author's America/New_York was the default here and rendered into every generated timer.
# A host that wants a zone names it (cron_timezone in wizard-config.json, or WIZARD_CRON_TIMEZONE).
CRON_TIMEZONE=$(_read_config_key "cron_timezone" "")
SLEEP_LOOP_TIME=$(_read_config_key "sleep_loop_time" "22:30")
# THE OWNER'S DECLARATION, OR NOTHING (model-choice Round 1, Wave B -- R-4 / EW5-08). This defaulted to "0" and
# rendered Environment=CUDA_VISIBLE_DEVICES=0 into every generated unit that loads a model: card 0 is the card that
# drives the display on a multi-GPU desktop, and a unit's own CUDA_VISIBLE_DEVICES outranks the env file's
# WIZARD_CUDA_DEVICE in the units' resolver (organs/voidloom_device.py) -- so an owner who declared his card, or answered
# the installer's GPU question, was overruled by a literal. Now the unit carries a pin ONLY when the owner declared one
# here (wizard-config.json gpu_device_index, or WIZARD_GPU_DEVICE_INDEX); otherwise it renders no pin at all and the unit
# takes its card the way every packaged unit does: from the env file (EnvironmentFile=), through the units' resolver.
GPU_DEVICE_INDEX=$(_read_config_key "gpu_device_index" "")
case "$GPU_DEVICE_INDEX" in ""|[0-9]|[0-9][0-9]) ;; *) echo "platform-detection: gpu_device_index '${GPU_DEVICE_INDEX}' is not a card index; no unit pin is rendered from it" >&2; GPU_DEVICE_INDEX="" ;; esac
DB_PATH=$(_read_config_key "db_path" "")
MODULE_DIR=$(_read_config_key "module_dir" "")
SLEEP_LOOP_CONFIG=$(_read_config_key "sleep_loop_config_path" "$(_config_path sleep-loop-config.json)")
EMBED_DEVICE=$(_read_config_key "embed_device" "auto")

# Resolve MODULE_DIR if empty (default to directory containing this script's parent)
if [ -z "$MODULE_DIR" ]; then
    # This script lives at <module_dir>/cron/. The engine is at
    # <module_dir>/organs/claude_memory.py in the current layout; the two flat
    # checks below are kept for older trees and ACE workspaces.
    parent_dir="$(dirname "$SCRIPT_DIR")"
    if [ -f "${parent_dir}/organs/claude_memory.py" ]; then
        MODULE_DIR="$parent_dir"
    elif [ -f "${parent_dir}/claude_memory.py" ]; then
        MODULE_DIR="$parent_dir"
    elif [ -f "${parent_dir}/../claude_memory.py" ]; then
        MODULE_DIR="$(cd "${parent_dir}/.." && pwd)"
    else
        # No guess. The old fallback was the author's workstation path, which
        # is wrong on every other machine and silently so.
        echo "ERROR: cannot locate claude_memory.py above ${SCRIPT_DIR};" >&2
        echo "       set module_dir in ${CONFIG_FILE} or WIZARD_MODULE_DIR." >&2
        exit 2
    fi
fi

# Resolve DB_PATH if empty
if [ -z "$DB_PATH" ]; then
    DB_PATH="${MODULE_DIR}/claude_memory.db"
fi

# Find the interpreter the units run (B2 class, iterations 2-3). `command -v python3` was the first python on PATH,
# which never carries <brain>/venv/bin: on a venv Tier-1 install every generated unit ran the nightly organs without
# sqlite_vec, at Tier 0. Order: an EXPLICIT WIZARD_PYTHON_BIN first (the operator's own override outranks anything
# derived), then the interpreter install.sh RECORDED in <brain>/INSTALL_RECORD (absolute + executable), then
# <brain>/venv/bin/python3, then python3 on PATH as before. MODULE_DIR is the brain (it holds organs/claude_memory.py).
# Iteration 4: the explicit value is VALIDATED before it is used -- it must exist (a path, or a name on PATH), be
# executable, and run `-c 'import sys'`. One that fails is named on stderr with the reason, and the chain falls through
# to the record: a typo in an override must not become the interpreter every generated unit runs.
PYTHON3=""
if [ -n "${WIZARD_PYTHON_BIN:-}" ]; then
    _wpb="$WIZARD_PYTHON_BIN"
    case "$_wpb" in */*) ;; *) _wpb="$(command -v "$_wpb" 2>/dev/null || true)" ;; esac
    if [ -z "$_wpb" ] || [ ! -f "$_wpb" ]; then
        echo "WARNING: WIZARD_PYTHON_BIN=${WIZARD_PYTHON_BIN} REJECTED: no such file (nor a command on PATH); falling through to the recorded interpreter." >&2
    elif [ ! -x "$_wpb" ]; then
        echo "WARNING: WIZARD_PYTHON_BIN=${WIZARD_PYTHON_BIN} REJECTED: not executable; falling through to the recorded interpreter." >&2
    elif ! "$_wpb" -c 'import sys' >/dev/null 2>&1; then
        echo "WARNING: WIZARD_PYTHON_BIN=${WIZARD_PYTHON_BIN} REJECTED: does not run \`-c 'import sys'\`; falling through to the recorded interpreter." >&2
    else
        PYTHON3="$_wpb"
    fi
    unset _wpb
fi
if [ -z "$PYTHON3" ] && [ -f "${MODULE_DIR}/INSTALL_RECORD" ]; then
    _rec_py="$(awk 'index($0, "interpreter=") == 1 { v = substr($0, 13) } END { print v }' "${MODULE_DIR}/INSTALL_RECORD" 2>/dev/null || true)"
    case "$_rec_py" in /*) if [ -f "$_rec_py" ] && [ -x "$_rec_py" ]; then PYTHON3="$_rec_py"; fi ;; esac
    unset _rec_py
fi
if [ -z "$PYTHON3" ] && [ -x "${MODULE_DIR}/venv/bin/python3" ]; then PYTHON3="${MODULE_DIR}/venv/bin/python3"; fi
[ -n "$PYTHON3" ] || PYTHON3="$(command -v python3 2>/dev/null || echo "/usr/bin/python3")"

# ============================================================
# Platform Detection
# ============================================================

detect_platform() {
    local uname_s
    uname_s="$(uname -s)"

    case "$uname_s" in
        Linux)
            if grep -qi microsoft /proc/version 2>/dev/null; then
                echo "wsl2"
            else
                echo "linux"
            fi
            ;;
        Darwin)
            echo "macos"
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

PLATFORM=$(detect_platform)

# ============================================================
# Service/Timer Generators
# ============================================================

_cuda_env_line() {
    # Return the CUDA environment line for service files, or empty if no GPU.
    if [ -n "$GPU_DEVICE_INDEX" ] && [ "$EMBED_DEVICE" != "cpu" ]; then
        echo "Environment=CUDA_VISIBLE_DEVICES=${GPU_DEVICE_INDEX}"
    fi
}

_generate_systemd_service() {
    # Generate a .service file for a given wizard-library timer.
    # $1 = service name (e.g., "sleep-loop")
    # $2 = description
    # $3 = ExecStart command (after python3 path)
    # $4 = timeout seconds
    # $5 = needs_cuda (true/false)
    # $6 = extra EnvironmentFile line (or empty)
    local name="$1" desc="$2" exec_cmd="$3" timeout="$4" needs_cuda="$5" env_file="${6:-}"

    local cuda_line=""
    if [ "$needs_cuda" = "true" ]; then
        cuda_line=$(_cuda_env_line)
    fi

    local env_file_line=""
    if [ -n "$env_file" ]; then
        env_file_line="EnvironmentFile=${env_file}"
    fi

    cat <<SERVICEEOF
[Unit]
Description=Wizard Library ${desc}
After=network.target

[Service]
Type=oneshot
Environment=PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring
${cuda_line}
${env_file_line}
ExecStart=${PYTHON3} -u ${exec_cmd}
WorkingDirectory=${MODULE_DIR}
StandardOutput=journal
StandardError=journal
TimeoutStartSec=${timeout}
SERVICEEOF
}

_generate_systemd_timer() {
    # Generate a .timer file.
    # $1 = description
    # $2 = OnCalendar expression
    local desc="$1" calendar="$2"

    cat <<TIMEREOF
[Unit]
Description=Wizard Library ${desc}

[Timer]
OnCalendar=${calendar}
Persistent=true

[Install]
WantedBy=timers.target
TIMEREOF
}

_generate_launchd_plist() {
    # Generate a macOS launchd plist file.
    # $1 = label (e.g., "${LAUNCHD_PREFIX}.sleep-loop")
    # $2 = program arguments (space-separated)
    # $3 = hour
    # $4 = minute
    # $5 = weekday (0-6, or empty for daily)
    local label="$1" args="$2" hour="$3" minute="$4" weekday="${5:-}"

    local weekday_entry=""
    if [ -n "$weekday" ]; then
        weekday_entry="            <key>Weekday</key>
            <integer>${weekday}</integer>"
    fi

    # Build ProgramArguments array
    local args_xml=""
    for arg in $args; do
        args_xml="${args_xml}        <string>${arg}</string>
"
    done

    cat <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${label}</string>
    <key>ProgramArguments</key>
    <array>
${args_xml}    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${hour}</integer>
        <key>Minute</key>
        <integer>${minute}</integer>
${weekday_entry}
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/wizard-library.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/wizard-library.err</string>
    <key>WorkingDirectory</key>
    <string>${MODULE_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHON_KEYRING_BACKEND</key>
        <string>keyring.backends.null.Keyring</string>
    </dict>
</dict>
</plist>
PLISTEOF
}

# ============================================================
# Timer definitions (THE source of truth)
# ============================================================

# Parse sleep loop time (HH:MM)
SLEEP_HOUR="${SLEEP_LOOP_TIME%%:*}"
SLEEP_MIN="${SLEEP_LOOP_TIME##*:}"

# Timer schedule: times in cron_timezone
# sleep-loop:         daily at SLEEP_LOOP_TIME
# dreaming:           daily at 03:00
# anti-recency:       Sunday at 06:00
# gap-mapping:        Sunday at 07:00
# skill-graduation:   Sunday at 05:00
# whisper-digest:     Sunday at 04:00

generate_all_files() {
    local out_dir="$1"
    mkdir -p "$out_dir"

    local env_file="$(_config_path sleep-loop-env)"

    # --- sleep-loop ---
    _generate_systemd_service "sleep-loop" \
        "Sleep Loop -- nightly memory consolidation cycle" \
        "${MODULE_DIR}/claude_memory.py sleep --config ${SLEEP_LOOP_CONFIG}" \
        600 true "$env_file" \
        > "${out_dir}/wizard-library-sleep-loop.service"

    _generate_systemd_timer \
        "Sleep Loop Timer -- nightly at ${SLEEP_LOOP_TIME}${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        "*-*-* ${SLEEP_HOUR}:${SLEEP_MIN}:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        > "${out_dir}/wizard-library-sleep-loop.timer"

    # --- dreaming ---
    _generate_systemd_service "dreaming" \
        "Memory Dreaming -- nightly dual-pool random-walk synthesis" \
        "${MODULE_DIR}/claude_memory.py dream --config $(_config_path dreaming-config.json)" \
        1800 true "$env_file" \
        > "${out_dir}/wizard-library-dreaming.service"

    _generate_systemd_timer \
        "Dreaming Timer -- nightly at 03:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        "*-*-* 03:00:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        > "${out_dir}/wizard-library-dreaming.timer"

    # --- anti-recency ---
    _generate_systemd_service "anti-recency" \
        "Anti-Recency -- weekly foundation resurfacing cycle" \
        "${MODULE_DIR}/anti_recency.py --weekly ${DB_PATH}" \
        120 true "" \
        > "${out_dir}/wizard-library-anti-recency.service"

    _generate_systemd_timer \
        "Anti-Recency Timer -- weekly Sunday 06:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        "Sun *-*-* 06:00:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        > "${out_dir}/wizard-library-anti-recency.timer"

    # --- gap-mapping ---
    _generate_systemd_service "gap-mapping" \
        "Gap Mapping -- weekly knowledge gap inventory" \
        "${MODULE_DIR}/gap_mapping.py --run ${DB_PATH}" \
        600 false "" \
        > "${out_dir}/wizard-library-gap-mapping.service"

    _generate_systemd_timer \
        "Gap Mapping Timer -- weekly Sunday 07:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        "Sun *-*-* 07:00:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        > "${out_dir}/wizard-library-gap-mapping.timer"

    # --- skill-graduation ---
    _generate_systemd_service "skill-graduation" \
        "Skill Graduation -- weekly procedural memory tier evaluation" \
        "${MODULE_DIR}/claude_memory.py graduate --graduated-by cron" \
        120 true "" \
        > "${out_dir}/wizard-library-skill-graduation.service"

    _generate_systemd_timer \
        "Skill Graduation Timer -- weekly Sunday 05:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        "Sun *-*-* 05:00:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        > "${out_dir}/wizard-library-skill-graduation.timer"

    # --- whisper-digest ---
    _generate_systemd_service "whisper-digest" \
        "Whisper Digest -- weekly self-observation synthesis" \
        "${MODULE_DIR}/whisper_network.py --digest ${DB_PATH} --mode llm" \
        300 true "$env_file" \
        > "${out_dir}/wizard-library-whisper-digest.service"

    _generate_systemd_timer \
        "Whisper Digest Timer -- weekly Sunday 04:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        "Sun *-*-* 04:00:00${CRON_TIMEZONE:+ $CRON_TIMEZONE}" \
        > "${out_dir}/wizard-library-whisper-digest.timer"

    echo "Generated 12 systemd files in ${out_dir}/"

    # --- macOS launchd plist files (only generate on macOS or if forced) ---
    if [ "$PLATFORM" = "macos" ] || [ "${WIZARD_FORCE_LAUNCHD:-}" = "true" ]; then
        local plist_dir="${out_dir}/launchd"
        mkdir -p "$plist_dir"

        _generate_launchd_plist "${LAUNCHD_PREFIX}.sleep-loop" \
            "${PYTHON3} -u ${MODULE_DIR}/claude_memory.py sleep --config ${SLEEP_LOOP_CONFIG}" \
            "$SLEEP_HOUR" "$SLEEP_MIN" "" \
            > "${plist_dir}/${LAUNCHD_PREFIX}.sleep-loop.plist"

        _generate_launchd_plist "${LAUNCHD_PREFIX}.dreaming" \
            "${PYTHON3} -u ${MODULE_DIR}/claude_memory.py dream --config $(_config_path dreaming-config.json)" \
            3 0 "" \
            > "${plist_dir}/${LAUNCHD_PREFIX}.dreaming.plist"

        _generate_launchd_plist "${LAUNCHD_PREFIX}.anti-recency" \
            "${PYTHON3} -u ${MODULE_DIR}/anti_recency.py --weekly ${DB_PATH}" \
            6 0 0 \
            > "${plist_dir}/${LAUNCHD_PREFIX}.anti-recency.plist"

        _generate_launchd_plist "${LAUNCHD_PREFIX}.gap-mapping" \
            "${PYTHON3} -u ${MODULE_DIR}/gap_mapping.py --run ${DB_PATH}" \
            7 0 0 \
            > "${plist_dir}/${LAUNCHD_PREFIX}.gap-mapping.plist"

        _generate_launchd_plist "${LAUNCHD_PREFIX}.skill-graduation" \
            "${PYTHON3} -u ${MODULE_DIR}/claude_memory.py graduate --graduated-by cron" \
            5 0 0 \
            > "${plist_dir}/${LAUNCHD_PREFIX}.skill-graduation.plist"

        _generate_launchd_plist "${LAUNCHD_PREFIX}.whisper-digest" \
            "${PYTHON3} -u ${MODULE_DIR}/whisper_network.py --digest ${DB_PATH} --mode llm" \
            4 0 0 \
            > "${plist_dir}/${LAUNCHD_PREFIX}.whisper-digest.plist"

        echo "Generated 6 launchd plist files in ${plist_dir}/"
    fi
}

# ============================================================
# Installation
# ============================================================

install_timers() {
    local dry_run="${1:-false}"

    if [ "$CRON_PATTERN" = "none" ]; then
        echo "cron_pattern=none -- skipping timer installation."
        echo "Timers can be installed manually later by changing cron_pattern in wizard-config.json."
        return 0
    fi

    # Generate first
    generate_all_files "$GENERATED_DIR"

    case "$PLATFORM" in
        wsl2|linux)
            echo "Platform: ${PLATFORM} -- installing systemd user timers"
            mkdir -p "$SYSTEMD_USER_DIR"

            for f in "${GENERATED_DIR}"/wizard-library-*.{service,timer}; do
                [ -f "$f" ] || continue
                local basename
                basename="$(basename "$f")"
                if [ "$dry_run" = "true" ]; then
                    echo "  [DRY-RUN] Would copy ${basename} -> ${SYSTEMD_USER_DIR}/"
                else
                    cp "$f" "${SYSTEMD_USER_DIR}/"
                    echo "  Installed: ${basename}"
                fi
            done

            if [ "$dry_run" != "true" ]; then
                systemctl --user daemon-reload
                echo "  Systemd daemon reloaded."

                for timer in "${GENERATED_DIR}"/wizard-library-*.timer; do
                    [ -f "$timer" ] || continue
                    local timer_name
                    timer_name="$(basename "$timer")"
                    systemctl --user enable --now "$timer_name" 2>/dev/null || true
                    echo "  Enabled: ${timer_name}"
                done
            fi

            # WSL2-specific: Windows Task Scheduler wake task
            if [ "$PLATFORM" = "wsl2" ] && [ "$CRON_PATTERN" = "wsl2_hybrid" ]; then
                local wake_min=$((SLEEP_MIN - 1))
                if [ "$wake_min" -lt 0 ]; then
                    wake_min=59
                    SLEEP_HOUR=$((SLEEP_HOUR - 1))
                fi
                local wake_time
                wake_time=$(printf "%d:%02d" "$SLEEP_HOUR" "$wake_min")

                echo ""
                echo "  WSL2 HYBRID: Windows Task Scheduler wake task needed."
                if [ "$dry_run" = "true" ]; then
                    echo "  [DRY-RUN] Would create WakeWSL_WizardLibrary task at ${wake_time} PM"
                else
                    if command -v powershell.exe &>/dev/null; then
                        powershell.exe -Command "
\$taskName = 'WakeWSL_WizardLibrary'
\$existingTask = Get-ScheduledTask -TaskName \$taskName -ErrorAction SilentlyContinue
if (\$existingTask) { Unregister-ScheduledTask -TaskName \$taskName -Confirm:\$false }
\$action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument '-e echo WSL_WAKE_FOR_WIZARD'
\$trigger = New-ScheduledTaskTrigger -Daily -At '${wake_time}PM'
\$settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName \$taskName -Action \$action -Trigger \$trigger -Settings \$settings -Description 'Wake WSL for Wizard Library timers' | Out-Null
Write-Host 'Windows Task: WakeWSL_WizardLibrary created at ${wake_time} PM'
" 2>/dev/null || echo "  WARNING: Could not create Windows task. Run in interactive terminal."
                    else
                        echo "  WARNING: powershell.exe not found. Create wake task manually:"
                        echo "    Task: 'WakeWSL_WizardLibrary' at ${wake_time} PM daily"
                        echo "    Action: wsl.exe -e echo WSL_WAKE_FOR_WIZARD"
                    fi
                fi
            fi
            ;;

        macos)
            echo "Platform: macOS -- installing launchd agents"
            mkdir -p "$LAUNCHD_AGENTS_DIR"
            mkdir -p "${LOG_DIR}"

            local plist_dir="${GENERATED_DIR}/launchd"
            for f in "${plist_dir}"/*.plist; do
                [ -f "$f" ] || continue
                local basename
                basename="$(basename "$f")"
                if [ "$dry_run" = "true" ]; then
                    echo "  [DRY-RUN] Would copy ${basename} -> ${LAUNCHD_AGENTS_DIR}/"
                else
                    cp "$f" "${LAUNCHD_AGENTS_DIR}/"
                    launchctl load "${LAUNCHD_AGENTS_DIR}/${basename}" 2>/dev/null || true
                    echo "  Installed + loaded: ${basename}"
                fi
            done
            ;;

        *)
            echo "ERROR: Unsupported platform '${PLATFORM}'. Manual cron setup required."
            return 1
            ;;
    esac

    echo ""
    echo "Installation complete. Verify with:"
    case "$PLATFORM" in
        wsl2|linux) echo "  systemctl --user list-timers | grep wizard-library" ;;
        macos)      echo "  launchctl list | grep wizard-library" ;;
    esac
}

# ============================================================
# Status
# ============================================================

show_status() {
    echo "Platform: ${PLATFORM}"
    echo "Config:   ${CONFIG_FILE}"
    echo "Cron:     ${CRON_PATTERN}"
    echo "Timezone: ${CRON_TIMEZONE:-(none -- systemd reads OnCalendar in the local time of this host)}"
    echo "Module:   ${MODULE_DIR}"
    echo "DB:       ${DB_PATH}"
    echo "GPU:      ${GPU_DEVICE_INDEX:-none}"
    echo ""

    case "$PLATFORM" in
        wsl2|linux)
            echo "=== Systemd Timers ==="
            systemctl --user list-timers 2>/dev/null | grep -i wizard || echo "  (none found)"
            ;;
        macos)
            echo "=== Launchd Agents ==="
            launchctl list 2>/dev/null | grep wizard || echo "  (none found)"
            ;;
    esac
}

# ============================================================
# Self-Test
# ============================================================

run_self_test() {
    local passed=0 failed=0

    _assert() {
        local label="$1" actual="$2" expected="$3"
        if [ "$actual" = "$expected" ]; then
            passed=$((passed + 1))
        else
            failed=$((failed + 1))
            echo "  FAIL: ${label}: expected '${expected}', got '${actual}'"
        fi
    }

    _assert_contains() {
        local label="$1" haystack="$2" needle="$3"
        if echo "$haystack" | grep -q "$needle"; then
            passed=$((passed + 1))
        else
            failed=$((failed + 1))
            echo "  FAIL: ${label}: '${needle}' not found"
        fi
    }

    # T1: Platform detection returns a known value
    local p
    p=$(detect_platform)
    case "$p" in
        wsl2|linux|macos|unknown) _assert "T1: platform known" "ok" "ok" ;;
        *) _assert "T1: platform known" "$p" "wsl2|linux|macos|unknown" ;;
    esac

    # T2: Generate to temp dir
    local tmpdir
    tmpdir=$(mktemp -d)
    generate_all_files "$tmpdir" 2>/dev/null

    # T3: All 12 systemd files generated
    local svc_count timer_count
    svc_count=$(ls "${tmpdir}"/wizard-library-*.service 2>/dev/null | wc -l)
    timer_count=$(ls "${tmpdir}"/wizard-library-*.timer 2>/dev/null | wc -l)
    _assert "T3: 6 service files" "$svc_count" "6"
    _assert "T3: 6 timer files" "$timer_count" "6"

    # T4: Service files contain config-derived paths (not hardcoded)
    local sleep_svc
    sleep_svc=$(cat "${tmpdir}/wizard-library-sleep-loop.service")
    _assert_contains "T4: service has MODULE_DIR" "$sleep_svc" "$MODULE_DIR"
    _assert_contains "T4: service has python3" "$sleep_svc" "python3"

    # T5: Timer files contain the timezone WHEN one is configured (none is packaged)
    local sleep_timer
    sleep_timer=$(cat "${tmpdir}/wizard-library-sleep-loop.timer")
    [ -n "$CRON_TIMEZONE" ] && _assert_contains "T5: timer has timezone" "$sleep_timer" "$CRON_TIMEZONE"
    _assert_contains "T5: timer has sleep time" "$sleep_timer" "${SLEEP_HOUR}:${SLEEP_MIN}"

    # T6: GPU line present when configured, absent when empty
    local cuda_line
    cuda_line=$(_cuda_env_line)
    if [ -n "$GPU_DEVICE_INDEX" ] && [ "$EMBED_DEVICE" != "cpu" ]; then
        _assert_contains "T6: CUDA line present" "$cuda_line" "CUDA_VISIBLE_DEVICES"
    else
        _assert "T6: CUDA line empty for cpu" "$cuda_line" ""
    fi

    # T7: Gap-mapping service does NOT have CUDA (needs_cuda=false in definition)
    local gap_svc
    gap_svc=$(cat "${tmpdir}/wizard-library-gap-mapping.service")
    if echo "$gap_svc" | grep -q "CUDA_VISIBLE_DEVICES"; then
        _assert "T7: gap-mapping no CUDA" "has_cuda" "no_cuda"
    else
        _assert "T7: gap-mapping no CUDA" "ok" "ok"
    fi

    # T8: Config key reading with defaults
    local test_val
    test_val=$(_read_config_key "nonexistent_key_12345" "fallback_default")
    _assert "T8: missing key uses default" "$test_val" "fallback_default"

    # Cleanup
    rm -rf "$tmpdir"

    echo ""
    echo "  ${passed} passed, ${failed} failed out of $((passed + failed)) assertions"
    [ "$failed" -eq 0 ] && return 0 || return 1
}

# ============================================================
# Main
# ============================================================

case "${1:-}" in
    detect)
        echo "$PLATFORM"
        ;;
    generate)
        dry_run="false"
        [ "${2:-}" = "--dry-run" ] && dry_run="true"
        if [ "$dry_run" = "true" ]; then
            # --dry-run writes NOTHING into the tree (reference-host round, iteration 2, item 16:
            # it ignored the flag and left 12 files in cron/generated/): generate into a temp
            # dir, print every file, remove it.
            _gen_tmp="$(mktemp -d)"
            generate_all_files "$_gen_tmp"
            for _gen_f in "$_gen_tmp"/*; do
                [ -f "$_gen_f" ] || continue
                echo "==== DRY: $(basename "$_gen_f") (would be written to ${GENERATED_DIR}/)"
                cat "$_gen_f"
            done
            rm -rf "$_gen_tmp"
        else
            generate_all_files "$GENERATED_DIR"
        fi
        ;;
    install)
        dry_run="false"
        [ "${2:-}" = "--dry-run" ] && dry_run="true"
        install_timers "$dry_run"
        ;;
    status)
        show_status
        ;;
    test)
        run_self_test
        ;;
    *)
        echo "Wizard Library Platform Detection + Cron Setup"
        echo ""
        echo "Usage: $(basename "$0") <command> [options]"
        echo ""
        echo "Commands:"
        echo "  detect              Print detected platform (wsl2/linux/macos/unknown)"
        echo "  generate [--dry-run] Generate service/timer/plist files"
        echo "  install [--dry-run]  Generate + install timers"
        echo "  status               Show installed timers and config"
        echo "  test                 Run self-tests"
        echo ""
        echo "Config: ${CONFIG_FILE}"
        echo "Platform: ${PLATFORM}"
        ;;
esac
