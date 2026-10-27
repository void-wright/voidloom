#!/usr/bin/env bash
# Setup script for the Wizard Library Sleep Loop cron infrastructure.
# WSL2 HYBRID pattern (memory #3674): systemd user timer + Windows Task Scheduler wake.
#
# Run this script ONCE to install. Idempotent — safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
# Per-file config resolution (package default, then legacy) -- ONE resolver
# shared with platform-detection.sh, never a second literal here.
. "${SCRIPT_DIR}/_config_dir.sh"
CONFIG_FILE="$(_config_path sleep-loop-config.json)"
CONFIG_DIR="$(dirname "${CONFIG_FILE}")"

echo "=== Wizard Library Sleep Loop Setup ==="
echo ""

# Step 1: Create systemd user directory.
mkdir -p "${SYSTEMD_USER_DIR}"
echo "[1/6] Systemd user directory: ${SYSTEMD_USER_DIR}"

# Step 2: Copy service and timer files.
cp "${SCRIPT_DIR}/wizard-library-sleep-loop.service" "${SYSTEMD_USER_DIR}/"
cp "${SCRIPT_DIR}/wizard-library-sleep-loop.timer" "${SYSTEMD_USER_DIR}/"
echo "[2/6] Service and timer files installed."

# Step 3: Reload systemd daemon.
systemctl --user daemon-reload
echo "[3/6] Systemd daemon reloaded."

# Step 4: Enable and start the timer.
systemctl --user enable --now wizard-library-sleep-loop.timer
echo "[4/6] Timer enabled and started."

# Step 5: Create config file if it doesn't exist.
mkdir -p "${CONFIG_DIR}"
if [ ! -f "${CONFIG_FILE}" ]; then
    cp "${SCRIPT_DIR}/sleep-loop-config.json" "${CONFIG_FILE}"
    echo "[5/6] Config file created: ${CONFIG_FILE}"
else
    echo "[5/6] Config file already exists: ${CONFIG_FILE} (not overwritten)"
fi

# Step 6: Create Windows Task Scheduler task to wake WSL at 10:29 PM.
# This ensures WSL is running when the systemd timer fires at 10:30 PM.
echo "[6/6] Creating Windows Task Scheduler wake task..."
POWERSHELL_CMD='
$taskName = "WakeWSL_SleepLoop"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-e echo WSL_WAKE_FOR_SLEEP_LOOP"
$trigger = New-ScheduledTaskTrigger -Daily -At "10:29PM"
$settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Wake WSL for Wizard Library Sleep Loop" | Out-Null
Write-Host "Windows Task Scheduler: $taskName created (10:29 PM daily)"
'

# Try to run PowerShell from WSL. This may fail if not in an interactive session.
if command -v powershell.exe &> /dev/null; then
    powershell.exe -Command "${POWERSHELL_CMD}" 2>/dev/null || \
        echo "  WARNING: Could not create Windows task. Run this in an interactive terminal."
else
    echo "  WARNING: powershell.exe not found. Create the wake task manually."
    echo "  Task: 'WakeWSL_SleepLoop' at 10:29 PM daily, Action: wsl.exe -e echo WSL_WAKE_FOR_SLEEP_LOOP"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Verify with:"
echo "  systemctl --user list-timers | grep wizard-library"
echo "  systemctl --user status wizard-library-sleep-loop.timer"
echo ""
echo "Manual test run:"
echo "  python3 $HOME/.claude/memory/organs/claude_memory.py sleep --dry-run"
echo ""
echo "View logs:"
echo "  journalctl --user -u wizard-library-sleep-loop.service --since today"
