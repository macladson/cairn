# Cairn for Codex

Codex hooks use the same contract as Claude Code's (`hookSpecificOutput.additionalContext`, session and
tool events on stdin), so the same `cairn hook` commands serve both. Codex reads files through shell
commands; `cairn hook tool` recognises file paths inside them and inside `apply_patch` headers.

1. Install the CLI: `./install.sh codex` (puts `cairn` on PATH, installs `~/.codex/hooks.json` if you have
   none, and adds the Cairn section to `~/.codex/AGENTS.md`). If you already have a hooks file, merge
   `adapters/codex/hooks.json` into it by hand.
2. Start Codex and approve the new hooks with `/hooks` (Codex asks you to trust non-managed hooks).
3. Optional, for structured tools: add the MCP server to `~/.codex/config.toml`:

   ```toml
   [mcp_servers.cairn]
   command = "cairn"
   args = ["mcp"]
   ```
