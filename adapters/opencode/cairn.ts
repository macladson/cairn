// Cairn adapter for OpenCode: injects shared context from a Cairn ledger.
// Written against opencode.ai/docs/build/plugins/server (November 2026). NOT yet tested inside OpenCode:
// if the plugin API has moved, the MCP server (README.md) still gives agents the same context as tools.
// Needs the `cairn` CLI on PATH (./install.sh opencode).
import { Plugin } from "@opencode/plugin"
import { spawnSync } from "node:child_process"

function cairn(args: string[], input: unknown, cwd: string): string {
  const r = spawnSync("cairn", args, { input: JSON.stringify(input), encoding: "utf8", cwd, timeout: 30000 })
  return r.status === 0 && r.stdout ? r.stdout.trim() : ""
}

export default Plugin.define({
  id: "cairn",
  async setup(ctx: any) {
    const dir: string = ctx.location?.directory ?? process.cwd()
    const orientation = new Map<string, string>()
    const extra = new Map<string, string[]>()

    // Before each model call: the session orientation, plus area context gathered from tool calls.
    await ctx.session.hook("context", (event: any) => {
      const sid = String(event.sessionID ?? "opencode")
      if (!orientation.has(sid)) {
        orientation.set(sid, cairn(["hook", "session-start", "--format", "text", "--session", sid], { cwd: dir }, dir))
      }
      for (const text of [orientation.get(sid) ?? "", ...(extra.get(sid) ?? [])]) {
        if (text) event.system.push({ type: "text", text })
      }
    })

    // After a tool call: if it touched files in an area this session hasn't seen, fetch that area's context.
    await ctx.tool.hook("execute.after", (event: any) => {
      if (event.status !== "completed") return
      const sid = String(event.sessionID ?? "opencode")
      const { result, ...call } = event
      const text = cairn(["hook", "tool", "--format", "text", "--session", sid], { cwd: dir, tool_input: call }, dir)
      if (text) extra.set(sid, [...(extra.get(sid) ?? []), text])
    })
  },
})
