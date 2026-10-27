#!/usr/bin/env bash
# Install the leak-gate git hooks into this clone.
#
#   bash tools/git-hooks/install.sh
#       pre-commit + pre-push run tools/leak_gate.py (PII, personal paths, secrets via gitleaks
#       when installed, and the private denylist when ~/.voidloom/org_denylist.txt exists).
#
#   bash tools/git-hooks/install.sh --maintainer --email <your GitHub noreply address>
#       also sets this clone's commit identity to that GitHub noreply address and makes the
#       gate STRICT here: any other author/committer email is refused, and a missing denylist
#       is an error instead of a warning. For the person who publishes this repository.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
HOOKS="$(git rev-parse --git-path hooks)"
case "$HOOKS" in /*) ;; *) HOOKS="$ROOT/$HOOKS" ;; esac
MAINT=0; EMAIL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --maintainer) MAINT=1 ;;
        --email) EMAIL="${2:-}"; shift ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
mkdir -p "$HOOKS"
for h in pre-commit pre-push; do
    install -m 0755 "$ROOT/tools/git-hooks/$h" "$HOOKS/$h"
    echo "installed $HOOKS/$h"
done
if [ "$MAINT" = 1 ]; then
    [[ "$EMAIL" =~ @users\.noreply\.github\.com$ ]] || { echo "--maintainer needs --email <...@users.noreply.github.com>" >&2; exit 2; }
    git config user.email "$EMAIL"
    git config voidloom.requireNoreply true
    git config voidloom.requireDenylist true
    echo "maintainer mode: user.email=$EMAIL, voidloom.requireNoreply=true, voidloom.requireDenylist=true (this clone only)"
fi
