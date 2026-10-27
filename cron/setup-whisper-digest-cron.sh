#!/usr/bin/env bash
# Setup script for the Whisper Digest weekly cron (systemd user timer).
# Run once after deployment to enable the Friday 8 AM local time weekly synthesis.
#
# Prerequisites:
#   - systemd --user working in WSL2 (requires systemctl --user is functional)
#   - an overnight thinker is recorded (for LLM mode): `voidloom thinker status`; the model's own
#     credential lives in $HOME/.voidloom/thinker/ (docs/OVERNIGHT_THINKER_SETUP.md), never in an env file
#   - $HOME/.voidloom/sleep-loop-env exists (reuses the sleep loop's env)
#   - whisper_network.py deployed to $HOME/.claude/memory/organs/
#   - migration_009 applied (run claude_memory.py init)
#
# Usage:
#   bash setup-whisper-digest-cron.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo "=== Whisper Digest Weekly Cron Setup ==="
echo ""

# 1. Create systemd user directory if needed.
mkdir -p "$SYSTEMD_USER_DIR"

# 2. Copy service + timer files.
cp "${SCRIPT_DIR}/wizard-library-whisper-digest.service" "$SYSTEMD_USER_DIR/"
cp "${SCRIPT_DIR}/wizard-library-whisper-digest.timer" "$SYSTEMD_USER_DIR/"
echo "[OK] Service and timer files installed to ${SYSTEMD_USER_DIR}/"

# 3. Reload systemd daemon.
systemctl --user daemon-reload
echo "[OK] systemd user daemon reloaded"

# 4. Enable and start the timer.
systemctl --user enable wizard-library-whisper-digest.timer
systemctl --user start wizard-library-whisper-digest.timer
echo "[OK] Timer enabled and started"

# 5. Verify.
echo ""
echo "=== Timer Status ==="
systemctl --user status wizard-library-whisper-digest.timer --no-pager || true
echo ""
echo "=== Next Trigger ==="
systemctl --user list-timers wizard-library-whisper-digest.timer --no-pager || true
echo ""
echo "=== Setup Complete ==="
echo "The whisper digest will run every Friday at 8:00 AM local time."
echo "Manual trigger: systemctl --user start wizard-library-whisper-digest.service"
echo "View logs: journalctl --user -u wizard-library-whisper-digest.service --since today"
