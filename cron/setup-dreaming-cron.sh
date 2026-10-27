#!/usr/bin/env bash
# Setup script for the Wizard Library Memory Dreaming cron infrastructure.
# WSL2 HYBRID pattern (memory #3674): systemd user timer + Windows Task Scheduler wake.
#
# Run this script ONCE to install. Idempotent — safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
# Per-file config resolution (package default, then legacy) -- ONE resolver
# shared with platform-detection.sh, never a second literal here.
. "${SCRIPT_DIR}/_config_dir.sh"
CONFIG_FILE="$(_config_path dreaming-config.json)"
CONFIG_DIR="$(dirname "${CONFIG_FILE}")"

echo "=== Wizard Library Memory Dreaming Setup ==="
echo ""

# Step 1: Create systemd user directory.
mkdir -p "${SYSTEMD_USER_DIR}"
echo "[1/6] Systemd user directory: ${SYSTEMD_USER_DIR}"

# Step 2: Copy service and timer files.
cp "${SCRIPT_DIR}/wizard-library-dreaming.service" "${SYSTEMD_USER_DIR}/"
cp "${SCRIPT_DIR}/wizard-library-dreaming.timer" "${SYSTEMD_USER_DIR}/"
echo "[2/6] Service and timer files installed."

# Step 3: Reload systemd daemon.
systemctl --user daemon-reload
echo "[3/6] Systemd daemon reloaded."

# Step 4: Enable and start the timer.
systemctl --user enable --now wizard-library-dreaming.timer
echo "[4/6] Timer enabled and started."

# Step 5: Create config file if it doesn't exist.
mkdir -p "${CONFIG_DIR}"
# The config names NO lock: the dream organs key their lock by the brain (<brain db>.dreaming.lock, beside it).
# Until Round 3 this wrote the machine-global "/tmp/wizard-library-dreaming.lock", which any brain on the machine
# could hold; a config already carrying it keeps working (the organ uses the brain's lock and says so).
if [ ! -f "${CONFIG_FILE}" ]; then
    cat > "${CONFIG_FILE}" << 'JSONEOF'
{
    "divergent_pairs": 7,
    "close_context_pairs": 7,
    "persona_pair_fraction": 0.10,
    "divergent_distance_min": 0.6,
    "close_context_distance_max": 0.40,
    "close_context_min_criteria": 2,
    "max_content_chars": 300,
    "claude_model": "sonnet",
    "claude_timeout_seconds": 60,
    "store_reflections": true,
    "digest_days": 7,
    "digest_limit": 5
}
JSONEOF
    echo "[5/6] Config file created: ${CONFIG_FILE}"
else
    echo "[5/6] Config file already exists: ${CONFIG_FILE} (not overwritten)"
fi

# Step 6: Create Windows Task Scheduler task to wake WSL at 9:59 PM.
# This ensures WSL is running when the systemd timer fires at 10:00 PM.
echo "[6/6] Creating Windows Task Scheduler wake task..."
POWERSHELL_CMD='
$taskName = "WakeWSL_Dreaming"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-e echo WSL_WAKE_FOR_DREAMING"
$trigger = New-ScheduledTaskTrigger -Daily -At "9:59PM"
$settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Wake WSL for Wizard Library Memory Dreaming" | Out-Null
Write-Host "Windows Task Scheduler: $taskName created (9:59 PM daily)"
'

# Try to run PowerShell from WSL. This may fail if not in an interactive session.
if command -v powershell.exe &> /dev/null; then
    powershell.exe -Command "${POWERSHELL_CMD}" 2>/dev/null || \
        echo "  WARNING: Could not create Windows task. Run this in an interactive terminal."
else
    echo "  WARNING: powershell.exe not found. Create the wake task manually."
    echo "  Task: 'WakeWSL_Dreaming' at 9:59 PM daily, Action: wsl.exe -e echo WSL_WAKE_FOR_DREAMING"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Verify with:"
echo "  systemctl --user list-timers | grep wizard-library"
echo "  systemctl --user status wizard-library-dreaming.timer"
echo ""
echo "Manual test run:"
echo "  python3 $HOME/.claude/memory/organs/claude_memory.py dream --dry-run"
echo ""
echo "View logs:"
echo "  journalctl --user -u wizard-library-dreaming.service --since today"
echo ""
echo "View recent dreams:"
echo "  python3 $HOME/.claude/memory/organs/claude_memory.py dream-digest"
