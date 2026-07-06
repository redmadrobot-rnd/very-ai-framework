#!/usr/bin/env bash
#
# Resolve the GitMark CLI location for either Codex-native or Claude-native installs.
set -euo pipefail

for cli in \
  ".codex/skills/kb-search/gitmark.py" \
  ".claude/skills/kb-search/gitmark.py"
do
  if [ -f "$cli" ]; then
    exec python3 "$cli" "$@"
  fi
done

echo "GitMark CLI not found: expected .codex/skills/kb-search/gitmark.py or .claude/skills/kb-search/gitmark.py" >&2
exit 2
