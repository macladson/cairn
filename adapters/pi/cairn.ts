// Cairn adapter for pi (coding agent): injects shared context from a Cairn ledger.
// Written against pi's extensions docs (November 2026). NOT yet tested inside pi.
// Needs the `cairn` CLI on PATH (./install.sh pi installs both).
import { spawnSync } from "node:child_process"
import { randomUUID } from "node:crypto"

export default function (pi: any) {
  let sid = randomUUID()
  let oriented = false
  const cwd = process.cwd()
  const cairn = (args: string[], input: unknown): string => {
    const r = spawnSync("cairn", args, { input: JSON.stringify(input), encoding: "utf8", cwd, timeout: 30000 })
    return r.status === 0 && r.stdout ? r.stdout.trim() : ""
  }

  pi.on("session_start", async () => {
    sid = randomUUID()
    oriented = false
  })

  // Once per session, before the agent's first run: the orientation.
  pi.on("before_agent_start", async () => {
    if (oriented) return
    oriented = true
    const text = cairn(["hook", "session-start", "--format", "text", "--session", sid], { cwd })
    if (text) return { message: { customType: "cairn-context", content: text } }
  })

  // After a tool call that touched files in a new area: append that area's context to the result.
  pi.on("tool_result", async (event: any) => {
    const text = cairn(["hook", "tool", "--format", "text", "--session", sid], { cwd, tool_input: event.input ?? {} })
    if (text) return { content: [...(event.content ?? []), { type: "text", text }] }
  })

  process.on("exit", () => {
    spawnSync("cairn", ["hook", "session-end", "--session", sid, "--harness", "pi"], { input: "{}", cwd })
  })
}
