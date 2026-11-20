# Cairn plugin for Claude Code

Gives Claude Code sessions shared context from a Cairn ledger: at session start a short orientation (work in
progress, open tasks, most severe findings, project rules), and the first time files in an area are opened,
that area's note (how the code works) and live findings, within a small token budget. It also installs the
`cairn` skill (the protocol for checking, claiming and recording work) and the `cairn` command, and records
token use per session so you can measure the effect. Repos without a Cairn ledger get nothing injected.
Setup: see "Join a project that uses Cairn" in the kit's README.
