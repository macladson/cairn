# Working on the Cairn kit

Cairn lets coding agents run by different people share context (notes, findings, coverage, claims) through a
ledger on a git branch. This repo is the kit: the CLI, the agent protocol, the harness adapters, the daily job
template, profiles, scripts and tests. `README.md` is the user guide; `docs/design.md` explains the design and
`docs/overview.md` explains it in plain words.

## Layout

| Path | What it is |
| --- | --- |
| `cli/cairn.py` | The whole CLI: one file, Python 3.9+, standard library only. Installed as `cairn` by `install.sh`. |
| `skill/SKILL.md`, `skill/reference.md` | The protocol every agent follows, in any harness. |
| `adapters/` | Thin translators from a harness's events to `cairn hook ...`: Claude Code plugin, Codex hooks, OpenCode plugin, Pi extension, AGENTS.md section. |
| `adapters/claude-code/bin/cairn`, `.../skills/cairn/` | **Generated copies** of `cli/` and `skill/` for the plugin. Never edit them by hand. |
| `.claude-plugin/marketplace.json` | Makes this repo a Claude Code plugin marketplace. |
| `ci/cairn-daily.yml`, `ci/cairn-pr.yml` | Templates copied into a notes repo. They must not move into `.github/workflows/` here. |
| `.github/workflows/tests.yml` | This repo's own CI: runs the three test suites. |
| `profiles/example/` | An example profile to copy: vocabulary, settings and agent guide for an imaginary project. The tests use it. |
| `scripts/` | `setup-notes.sh` and `join.sh` (setup in one command each), `set-kit-repo.sh`, `sync-adapters.sh`. |
| `tests/` | End-to-end tests against local bare repos; no network needed. |

## Rules

- **Nothing project-specific anywhere in this repo.** A project's vocabulary is a profile, kept with that project; `profiles/example/` only shows the format. A test greps `cli/` and `skill/` for the example's terms.
- **The CLI stays one file with no dependencies.** People install it by copying it.
- **After editing `cli/cairn.py` or `skill/`, run `scripts/sync-adapters.sh`.** A test fails if the plugin's copies differ.
- **Adapters stay thin.** Logic goes in `cairn hook` so every harness gets it; an adapter only forwards events and prints the result.
- **Ledger writes never force-push.** Every write is a commit built off-tree and pushed fast-forward only, retried on rejection. Keep it that way: it is what makes claims atomic.
- **Releasing:** bump the version in `cli/cairn.py` (`VERSION`) and `adapters/claude-code/.claude-plugin/plugin.json` together (Claude Code updates the plugin only when that version changes), then tag `vX.Y.Z`. The bump is also what tells people to update: each notes repo's daily job records the version it ran with in the ledger (`latest_version`), and a session on an older Cairn gets a one-line notice. A fix released without a bump reaches nobody that way.

## Tests

```sh
python3 tests/test_cairn.py              # ~3.5 min: three people's agents, races, code moving underneath (160 checks)
python3 tests/stress_cairn.py 12 6 24    # ~20 s: claim and submit races
sh tests/test_getting_started.sh         # ~1 min: README setup, manual and scripted, against stand-ins for GitHub (needs PyYAML)
```

Run all three before pushing a change to `cli/`, `scripts/`, `install.sh` or `ci/`. `.github/workflows/tests.yml` runs them on every push to `main`.
