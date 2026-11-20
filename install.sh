#!/bin/sh
# Install the Cairn CLI for this user, plus adapters for the harnesses you name.
#   ./install.sh                      CLI only (cairn on PATH)
#   ./install.sh codex opencode pi    CLI + those adapters
#   ./install.sh claude-code          CLI + the Claude Code plugin (or the commands, if claude isn't installed)
# Nothing is written into any project repository.
set -eu
KIT=$(cd "$(dirname "$0")" && pwd)
BIN="${CAIRN_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN"
install -m 0755 "$KIT/cli/cairn.py" "$BIN/cairn"
echo "installed $BIN/cairn"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  note: $BIN is not on your PATH; add it in your shell profile";; esac

add_agents_md() {  # append the protocol section to a global AGENTS.md once
  f="$1"; mkdir -p "$(dirname "$f")"
  if [ -f "$f" ] && grep -q "cairn:begin" "$f"; then echo "  $f already has the Cairn section"; return; fi
  { [ -s "$f" ] && echo; cat "$KIT/adapters/agents-md/AGENTS-snippet.md"; } >> "$f"
  echo "  added the Cairn section to $f"
}

for h in "$@"; do
  case "$h" in
    claude-code|claude)
      SRC=$(git -C "$KIT" remote get-url origin 2>/dev/null || true)
      case "$SRC" in   # a kit cloned from GitHub installs from there (so updates come from there); else from this folder
        https://github.com/*|git@github.com:*) SRC=$(echo "$SRC" | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##') ;;
        *) SRC="$KIT" ;;
      esac
      if command -v claude >/dev/null 2>&1 && claude plugin marketplace add "$SRC" >/dev/null 2>&1 \
         && claude plugin marketplace update cairn >/dev/null 2>&1 && claude plugin install cairn@cairn >/dev/null 2>&1 \
         && { claude plugin update cairn@cairn >/dev/null 2>&1 || true; }; then
        echo "  installed the Claude Code plugin from $SRC (new sessions load it)"
      else
        echo "Claude Code: run these once (from any directory):"
        echo "  claude plugin marketplace add $SRC"
        echo "  claude plugin install cairn@cairn"
      fi
      ;;
    codex)
      CH="${CODEX_HOME:-$HOME/.codex}"; mkdir -p "$CH"
      if [ -e "$CH/hooks.json" ]; then echo "  $CH/hooks.json exists: merge $KIT/adapters/codex/hooks.json into it"
      else cp "$KIT/adapters/codex/hooks.json" "$CH/hooks.json"; echo "  installed $CH/hooks.json (approve it with /hooks in Codex)"; fi
      add_agents_md "$CH/AGENTS.md"
      echo "  optional MCP server: see $KIT/adapters/codex/README.md"
      ;;
    opencode)
      D="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/plugins"; mkdir -p "$D"
      cp "$KIT/adapters/opencode/cairn.ts" "$D/cairn.ts"; echo "  installed $D/cairn.ts (experimental)"
      echo "  add the MCP server and AGENTS.md section: see $KIT/adapters/opencode/README.md"
      ;;
    pi)
      D="$HOME/.pi/agent/extensions"; mkdir -p "$D"
      cp "$KIT/adapters/pi/cairn.ts" "$D/cairn.ts"; echo "  installed $D/cairn.ts (experimental; /reload in pi)"
      ;;
    *) echo "unknown harness: $h (known: claude-code codex opencode pi)"; exit 1 ;;
  esac
done
