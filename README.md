# Cairn

Cairn is a shared notebook for coding agents, written by agents for agents. When an agent works out how
part of a codebase behaves, it leaves a note. The next agent to open that code, in anyone's session and in
any harness, is handed the note instead of working it out again.

The notes live on a git branch, so there is no server and nothing to host. Alongside them agents record
what they found, what they have already examined and what they are working on, so that nobody's agent
repeats work another has done. Cairn checks every record against the code as it changes and flags the ones
that may no longer be true.

You don't write the notes or direct any of this. Once Cairn is installed, your agent:

- gets a short orientation at the start of a session, and the notes and open findings for an area when it
  first touches a file there;
- checks whether someone already holds or has covered a piece of work before starting it;
- leaves notes, findings and a record of what it examined when it finishes.

## Join a project that uses Cairn

You need git 2.40 or later, Python 3.9 or later, and access to the project's notes repo (ask whoever set it
up). Then, with `<owner>/<notes-repo>` as that repo and `claude-code` or `codex` as your harness:

```sh
git clone https://github.com/macladson/cairn.git ~/cairn
~/cairn/scripts/join.sh <owner>/<notes-repo> --project <path to your clone of the project> claude-code
```

This installs the `cairn` command, points your clone at the notes and installs the adapter for your harness.
Name more than one harness if you use several. In Codex, approve the hooks with `/hooks` afterwards.

To check it worked, start an agent session in your clone of the project: its first context should include
a line beginning `[cairn]`. `cairn brief` shows what is in the ledger.

| Harness | Support |
| --- | --- |
| Claude Code | Plugin, installed by `join.sh` |
| Codex | Hooks, installed by `join.sh` |
| OpenCode, Pi | Experimental adapters: `~/cairn/install.sh opencode` or `pi` |
| Anything else | Add [the AGENTS.md section](adapters/agents-md/AGENTS-snippet.md) to your agent's instructions, or use `cairn mcp` if it speaks MCP |

**Updating.** A session tells you when your copy of Cairn is older than the project's. Run
`git -C ~/cairn pull && ~/cairn/install.sh claude-code` (or your harness's name).

## Useful commands

Your agent runs these for itself, but they work for you too.

| Command | What it shows |
| --- | --- |
| `cairn brief` | The ledger at a glance: areas, open findings, coverage, who is working on what |
| `cairn context --paths <path>` | What an agent is given when it opens that file |
| `cairn show <id>` | One record in full, with whether it is still fresh |
| `cairn status --kind note` | Every record of one kind |
| `cairn guide` | The project's own rules for agents (`cairn guide set <file>` replaces them) |

## Set up Cairn for a project

This is done once, by one maintainer. First write a **profile** for the project: copy
[profiles/example](profiles/example) and edit its three files. `map.json` divides the code into areas and
names the angles to examine them from, `ledger.json` names the project and its branches, and `GUIDE.md`
holds the rules you want every agent to follow.

**A public project.** Agents' findings can be security-sensitive, so the notes go in a private repo of
their own and nothing is ever pushed to the project. With the GitHub CLI (`gh`) signed in:

```sh
git clone https://github.com/macladson/cairn.git ~/cairn
~/cairn/scripts/setup-notes.sh <you>/<project>-notes --project <path to a clone of the project> --profile <profile dir>
```

The script creates the private repo, copies in the project's development and release branches, creates the
ledger, and adds a small daily job that keeps the notes in step with the project. It is safe to run again.
Then give each teammate write access to the notes repo and point them at the section above.

**A private project.** The notes can live in the project's own repo. In a clone of it, run
`~/cairn/install.sh` and then `cairn init --profile <profile dir>`.

Once agents have used it for a couple of weeks, `cairn metrics` shows how much duplicate work was avoided,
and `cairn settings set context_holdout 0.2` lets you compare sessions with and without the shared context.

## More

- [docs/overview.md](docs/overview.md) explains Cairn in plain words and walks through setup step by step.
- [docs/design.md](docs/design.md) covers the design: record types, freshness, claims and the daily job.
- [AGENTS.md](AGENTS.md) is for working on Cairn itself.
