# Cairn for OpenCode

Two pieces, use both:

1. **MCP server** (stable; gives agents `cairn_context`, `cairn_check`, `cairn_submit` and the other tools).
   In `opencode.json`:

   ```json
   { "mcp": { "cairn": { "type": "local", "command": ["cairn", "mcp"] } } }
   ```
2. **Plugin** (automatic injection; experimental). `./install.sh opencode` copies `cairn.ts` to
   `~/.config/opencode/plugins/`. It is written against OpenCode's current plugin docs but has not been run
   inside OpenCode yet.

Also add `adapters/agents-md/AGENTS-snippet.md` to your `AGENTS.md`, which OpenCode reads, so agents know the protocol.
