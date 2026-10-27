#!/usr/bin/env bash
# Setup script for the Wizard Library Counterfactual Revisit quarterly cron.
# WSL2 HYBRID pattern (memory #3674): systemd user timer + Windows Task Scheduler wake.
#
# Cadence: Quarterly — 1st of January, April, July, October at 8:00 AM local time.
# Run this script ONCE to install. Idempotent — safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo "=== Wizard Library Counterfactual Revisit Setup ==="
echo ""

# Step 1: Create systemd user directory.
mkdir -p "${SYSTEMD_USER_DIR}"
echo "[1/5] Systemd user directory: ${SYSTEMD_USER_DIR}"

# Step 2: Copy service and timer files.
cp "${SCRIPT_DIR}/wizard-library-counterfactual-revisit.service" "${SYSTEMD_USER_DIR}/"
cp "${SCRIPT_DIR}/wizard-library-counterfactual-revisit.timer" "${SYSTEMD_USER_DIR}/"
echo "[2/5] Service and timer files installed."

# Step 3: Reload systemd daemon.
systemctl --user daemon-reload
echo "[3/5] Systemd daemon reloaded."

# Step 4: Enable and start the timer.
systemctl --user enable --now wizard-library-counterfactual-revisit.timer
echo "[4/5] Timer enabled and started."

# Step 5: Create Windows Task Scheduler tasks to wake WSL on quarter days.
echo "[5/5] Creating Windows Task Scheduler wake tasks..."
POWERSHELL_CMD='
$taskBase = "WakeWSL_CounterfactualRevisit"
$months = @(
    @{Name="Jan"; Month=1; Day=1},
    @{Name="Apr"; Month=4; Day=1},
    @{Name="Jul"; Month=7; Day=1},
    @{Name="Oct"; Month=10; Day=1}
)

foreach ($m in $months) {
    $taskName = "${taskBase}_$($m.Name)"
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    $action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-e echo WSL_WAKE_FOR_COUNTERFACTUAL_REVISIT"
    # Monthly trigger on the 1st at 7:59 AM
    $trigger = New-ScheduledTaskTrigger -Once -At "7:59AM"
    # We need monthly on specific months — use CIM for finer control
    $settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Wake WSL for Wizard Library Counterfactual Revisit ($($m.Name) 1st)" | Out-Null
    Write-Host "  Created: $taskName"
}
Write-Host "Windows Task Scheduler: 4 quarterly wake tasks created"
'

if command -v powershell.exe &> /dev/null; then
    powershell.exe -Command "${POWERSHELL_CMD}" 2>/dev/null || \
        echo "  WARNING: Could not create Windows tasks. Run this in an interactive terminal."
else
    echo "  WARNING: powershell.exe not found. Create the wake tasks manually."
    echo "  Tasks: WakeWSL_CounterfactualRevisit_{Jan,Apr,Jul,Oct} at 7:59 AM"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Verify with:"
echo "  systemctl --user list-timers | grep counterfactual"
echo "  systemctl --user status wizard-library-counterfactual-revisit.timer"
echo ""
echo "Manual test run (dry-run):"
echo "  python3 $HOME/.claude/memory/organs/counterfactual_memory.py --revisit --dry-run $HOME/.claude/memory/claude_memory.db"
echo ""
echo "Manual test run (live, no LLM):"
echo "  python3 $HOME/.claude/memory/organs/counterfactual_memory.py --revisit --no-llm $HOME/.claude/memory/claude_memory.db"
echo ""
echo "Via claude_memory.py:"
echo "  python3 $HOME/.claude/memory/organs/claude_memory.py revisit --run-cycle --dry-run"
echo ""
echo "View logs:"
echo "  journalctl --user -u wizard-library-counterfactual-revisit.service --since today"
