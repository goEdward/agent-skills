#!/usr/bin/env bash
set -euo pipefail

# Automatically resolve repository root from this script's location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${SKILLS_REPO_DIR:-$(cd "$SCRIPT_DIR/../../../.." && pwd)}"

echo "==> Pulling latest skills in: $REPO_DIR"
python3 "$REPO_DIR/skills/sync-skills/scripts/sync_tool.py" pull --repo "$REPO_DIR" --refresh-setup
echo "==> Done."
