#!/usr/bin/env bash
# Setup script for the Wizard Library Gap Mapping weekly cron infrastructure.
# WSL2 HYBRID pattern (memory #3674): systemd user timer + Windows Task Scheduler wake.
#
# Run this script ONCE to install. Idempotent — safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo "=== Wizard Library Gap Mapping Setup ==="
echo ""

# Step 1: Create systemd user directory.
mkdir -p "${SYSTEMD_USER_DIR}"
echo "[1/5] Systemd user directory: ${SYSTEMD_USER_DIR}"

# Step 2: Copy service and timer files.
cp "${SCRIPT_DIR}/wizard-library-gap-mapping.service" "${SYSTEMD_USER_DIR}/"
cp "${SCRIPT_DIR}/wizard-library-gap-mapping.timer" "${SYSTEMD_USER_DIR}/"
echo "[2/5] Service and timer files installed."

# Step 3: Reload systemd daemon.
systemctl --user daemon-reload
echo "[3/5] Systemd daemon reloaded."

# Step 4: Enable and start the timer.
systemctl --user enable --now wizard-library-gap-mapping.timer
echo "[4/5] Timer enabled and started."

# Step 5: Create Windows Task Scheduler task to wake WSL at 7:59 AM Monday.
echo "[5/5] Creating Windows Task Scheduler wake task..."
POWERSHELL_CMD='
$taskName = "WakeWSL_GapMapping"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-e echo WSL_WAKE_FOR_GAP_MAPPING"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "7:59AM"
$settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Wake WSL for Wizard Library Gap Mapping (Monday 8 AM)" | Out-Null
Write-Host "Windows Task Scheduler: $taskName created (Monday 7:59 AM weekly)"
'

if command -v powershell.exe &> /dev/null; then
    powershell.exe -Command "${POWERSHELL_CMD}" 2>/dev/null || \
        echo "  WARNING: Could not create Windows task. Run this in an interactive terminal."
else
    echo "  WARNING: powershell.exe not found. Create the wake task manually."
    echo "  Task: 'WakeWSL_GapMapping' Monday 7:59 AM weekly, Action: wsl.exe -e echo WSL_WAKE_FOR_GAP_MAPPING"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Verify with:"
echo "  systemctl --user list-timers | grep wizard-library"
echo "  systemctl --user status wizard-library-gap-mapping.timer"
echo ""
echo "Manual test run:"
echo "  python3 $HOME/.claude/memory/organs/gap_mapping.py --run $HOME/.claude/memory/claude_memory.db"
echo ""
echo "Or via claude_memory.py:"
echo "  python3 $HOME/.claude/memory/organs/claude_memory.py gaps --run"
echo ""
echo "View logs:"
echo "  journalctl --user -u wizard-library-gap-mapping.service --since today"
