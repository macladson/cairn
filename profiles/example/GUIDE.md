# Widget notes for agents

This is an example profile for an imaginary public project, example/widget, whose notes live in a private
companion repo. Copy the directory, replace its contents with your project's, and pass it to
`cairn init --profile <dir>`. These notes add to the Cairn skill; run `cairn map` for the areas, lenses and facets.

## Security first
- The project is public; this ledger and its work branches are private. Never put ledger content, finding
  details or fix branches in a public issue, PR, gist or fork.
- A vulnerability also goes through the project's security policy: report it to security@example.com. Record
  the report date in the finding body.
- File anything that could lose or corrupt stored data under the sensitive lens `data-loss`.

## Before you review or change code
- Read the project's own contributor and agent guides. Their rules are the `rule` facet: tag findings with the
  rule they break, e.g. `"facets": {"rule": ["no-blocking-io"]}`.
- Say which platforms a finding applies to with the `platform` facet, when it is platform-specific.

## Branches and releases
- Findings are checked against `develop`. `cairn show` also says whether the located code is in `release`
  (the latest release), which matters for severity and disclosure.

## Repros and tests
- Prefer a targeted failing test as evidence and put it in `repro`, e.g.
  `{"run": "pytest tests/test_sync.py::test_retry", "expect": "fail"}`. Only `pytest <path>::<test>` is accepted.

## Code for findings
- `cairn work start <id>` / `cairn work submit` push branches to the private notes repo, never to the project.
- A non-sensitive fix goes upstream as a normal PR from a fork, with a `Cairn-Fixes: <id>` line in the commit
  message. A security fix goes through security@example.com; do not open a public PR for it.
