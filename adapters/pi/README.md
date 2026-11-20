# Cairn for pi

`./install.sh pi` puts `cairn` on PATH and copies `cairn.ts` to `~/.pi/agent/extensions/cairn.ts`, which pi
auto-discovers (`/reload` picks it up). The extension injects the orientation before the agent's first run
and appends area context to tool results the first time files in an area are touched. It is written against
pi's extension docs but has not been run inside pi yet. Pi has no MCP support by design; agents can also call
`cairn context --paths <files>` directly. Add `adapters/agents-md/AGENTS-snippet.md` to your `AGENTS.md`.
