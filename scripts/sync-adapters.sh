#!/bin/sh
# Copy the CLI and the skill into adapters that must be self-contained (the Claude Code plugin).
# Run after editing cli/ or skill/. tests/test_cairn.py fails if the copies drift.
set -eu
KIT=$(cd "$(dirname "$0")/.." && pwd)
install -m 0755 "$KIT/cli/cairn.py" "$KIT/adapters/claude-code/bin/cairn"
mkdir -p "$KIT/adapters/claude-code/skills/cairn"
cp "$KIT/skill/SKILL.md" "$KIT/skill/reference.md" "$KIT/adapters/claude-code/skills/cairn/"
echo "adapters synced"
