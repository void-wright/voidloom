#!/usr/bin/env bash
# Setup script for the Wizard Library Arcane Manifest cron infrastructure.
# WSL2 HYBRID pattern (memory #3674): systemd user timer + Windows Task Scheduler wake.
#
# Run this script ONCE to install. Idempotent — safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo "=== Wizard Library Arcane Manifest Setup ==="
echo ""

# Step 1: Create systemd user directory.
mkdir -p "${SYSTEMD_USER_DIR}"
echo "[1/4] Systemd user directory: ${SYSTEMD_USER_DIR}"

# Step 2: Copy service and timer files.
cp "${SCRIPT_DIR}/wizard-library-arcane-manifest.service" "${SYSTEMD_USER_DIR}/"
cp "${SCRIPT_DIR}/wizard-library-arcane-manifest.timer" "${SYSTEMD_USER_DIR}/"
echo "[2/4] Service and timer files installed."

# Step 3: Reload systemd daemon.
systemctl --user daemon-reload
echo "[3/4] Systemd daemon reloaded."

# Step 4: Enable and start the timer.
systemctl --user enable --now wizard-library-arcane-manifest.timer
echo "[4/4] Timer enabled and started."

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Timer status:"
systemctl --user status wizard-library-arcane-manifest.timer --no-pager 2>/dev/null || true
echo ""
echo "Next trigger:"
systemctl --user list-timers wizard-library-arcane-manifest.timer --no-pager 2>/dev/null || true
echo ""
echo "To run manually:"
echo "  python3 $HOME/.claude/memory/organs/claude_memory.py manifest --dry-run"
echo ""
echo "To check logs:"
echo "  journalctl --user -u wizard-library-arcane-manifest.service --since today"
echo ""
echo "Note: For WSL2, also create a Windows Task Scheduler task to wake WSL"
echo "at 8:59 AM local time on the 1st of Jan/Apr/Jul/Oct."
