# Cairn record reference

Every record is one file, `records/<kind>/<2-char shard>/<id>.md`, on the ledger branch: a JSON front
matter block between `---` lines, then an optional Markdown body. You never write these files directly.
Pass fields as JSON to `cairn submit <kind> <file|->`; the CLI fills in `id`, `kind`, `created_at` and
`author`, sets `commit` to code others can resolve (HEAD if pushed, otherwise its merge-base with the main
branch), and derives `areas` from `locs`. Each ledger's own vocabulary (`cairn map`) decides which areas,
lenses and facets are valid.

## Common fields

| Field | Required | Notes |
| --- | --- | --- |
| `title` | yes | Up to 200 characters. Say what and where. |
| `commit` | yes, except tasks | The code you examined. Defaults as above. |
| `areas` | findings, notes | Area ids. Derived from `locs` if omitted. |
| `lens` | findings | One lens id. |
| `locs` | recommended | `[{"path": "src/x.py", "lines": "41-45", "symbol": "fn"}]`. Freshness is computed from these. |
| `assumptions` | recommended | Short statements that must stay true for the record to hold. |
| `depends_on` | optional | `[{"path": "src/y.py"}, {"symbol": "Foo"}, {"package": "serde"}]`. Watched for changes. |
| `facets` | optional | `{"<facet>": ["value", ...]}` using the ledger's facets. |
| `links` | optional | `resolves` (task), `supersedes`, `duplicate_of`, `cites`, `addresses` (for changes). |
| `tags` | optional | Free-form strings for search. |
| `body` | optional | Markdown. Required for notes. |

Length limits: `title` 200 characters, each of `assumptions` 300. A refusal says which field was over and by
how much. `cairn submit <kind> <file> --dry-run` checks a record without writing it.

## finding
Also `claim` (one sentence, max 600 characters), `severity` (info, low, medium, high, critical),
`confidence` (low, medium, high), `evidence` (`[{"type": "repro|test|trace|log|benchmark|code-reading",
"detail": "..."}]`, each detail max 2,000 characters), and
optionally `repro`: `{"run": "<command>", "expect": "fail"|"pass"}`. The command must match one of the
ledger's `repro_runners` (`cairn settings`); `cairn recheck <id> --run` executes it later.

## note
How a part of the code works, for the next agent. `areas`, `summary` (one paragraph, max 800 characters;
this is what gets injected) and `body` (max 8,000 characters: entry points, data flow, invariants,
gotchas, how to test). Pin `locs` to the key files so Cairn can tell when the note drifts. One live note
per topic: improve an existing note by submitting a new one with `"links": {"supersedes": "<old id>"}`.

```json
{"title": "How the sync worker processes jobs", "areas": ["sync"],
 "locs": [{"path": "src/sync/worker.py", "symbol": "run"}],
 "summary": "run() loops pulling jobs and calls retry(job); no backoff, no dedupe key.",
 "body": "## Entry points\n...\n## Invariants\n...\n## Gotchas\n...\n## How to test\n..."}
```

## report
Declares what was examined. `coverage`: `[{"area", "lens", "depth": "skim|standard|deep", "result":
"clean|issues", "not_examined"}]`. `not_examined` is required at `standard` and `deep`: a sentence or two
on what you did not read or check (or `"nothing: ..."` with what makes you sure). A report at the same commit with equal or greater depth for the same cell is refused.

## benchmark
`bench` (a registered id), `env` (paste `cairn env --json`), `metrics`:
`[{"name", "unit", "better": "lower|higher", "samples": [..]}]`. Same bench, commit and env class pool together.

## change (use `cairn work`, not submit)
`cairn work submit` records the commit, its base, a diffstat, the files touched, `links.addresses` and any
`--test "<cmd>=pass|fail"`, and stores the patch next to the record (a branch is pushed only for big
changes). Freshness for a change means: does it still apply to the main branch? `cairn work refresh`
re-applies clean ones; conflicting ones can become rebase tasks.

## review (use `cairn review`)
Verdicts: confirms, refutes, duplicate (`--of`), fixed, stale, retract (author only). Methods:
reproduced-blind, reproduced, checked-evidence, opinion. `--resolves <task>` answers a task.

## task (use `cairn task`, `cairn next`)
`priority` p0 to p3. Closes when a record or review `resolves` it.

## Status and freshness
Status (first match wins): retracted · fixed · duplicate · superseded · disputed · confirmed · open.
Notes, reports and benchmarks show `current` instead of `open`; changes show `proposed`.
Freshness is separate: `fresh`; `watch` (the file changed elsewhere, a dependency, a declared package or
a caller changed, or a change drifted but still applies); `expired` (too old or too much churn since it
was verified); `stale` (the located lines changed, or a change conflicts); `gone` (file deleted). A
reproducing confirmation at a newer commit makes a record fresh again.
