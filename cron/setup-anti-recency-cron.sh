#!/usr/bin/env bash
# Setup script for the Wizard Library Anti-Recency weekly timer.
# Run once to install the systemd user service + timer.
#
# Prerequisites:
#   - anti_recency.py deployed to $HOME/.claude/memory/organs/
#   - migration_008_anti_recency.sql deployed alongside
#   - Database already has schema >= 2.4-L2-sleep-loop
#
# Usage:
#   bash setup-anti-recency-cron.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo "=== Wizard Library Anti-Recency Timer Setup ==="

# 1. Create systemd user directory if needed.
mkdir -p "${SYSTEMD_USER_DIR}"

# 2. Copy service and timer files.
cp "${SCRIPT_DIR}/wizard-library-anti-recency.service" "${SYSTEMD_USER_DIR}/"
cp "${SCRIPT_DIR}/wizard-library-anti-recency.timer" "${SYSTEMD_USER_DIR}/"

echo "[OK] Service files copied to ${SYSTEMD_USER_DIR}/"

# 3. Reload systemd user daemon.
systemctl --user daemon-reload
echo "[OK] systemd user daemon reloaded"

# 4. Enable and start the timer.
systemctl --user enable wizard-library-anti-recency.timer
systemctl --user start wizard-library-anti-recency.timer
echo "[OK] Timer enabled and started"

# 5. Show status.
echo ""
echo "Timer status:"
systemctl --user status wizard-library-anti-recency.timer --no-pager || true
echo ""
echo "Next trigger:"
systemctl --user list-timers wizard-library-anti-recency.timer --no-pager || true

echo ""
echo "=== Setup Complete ==="
echo "The resurfacing job will run every Wednesday at 8:00 AM local time."
echo "Manual trigger: systemctl --user start wizard-library-anti-recency.service"
echo "View logs: journalctl --user -u wizard-library-anti-recency.service"
