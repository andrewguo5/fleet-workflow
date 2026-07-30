# fleet — protocol (one screen)

A fleet is several coding agents working the same repo in parallel, each isolated in
its own git worktree, coordinated so they don't clobber each other and so one human can
supervise many at a glance.

## Roles

- **Worker** — an agent in its own worktree, holding a NATO **callsign** (`alpha`…
  `zulu`). The callsign is its identity across worktree, branch (`fleet/<callsign>`),
  worker file, and mailbox. Self-manages its lane; owns all its git operations.
- **Quartermaster (QM)** — a manager *agent* (`fleet qm`) that reads every worker's
  state, summarizes for the human, relays directives down, and flags who needs
  attention. Never answers a worker's blocking question; never writes code.
- **You (human)** — recruit workers, brief them, work hands-on with one at a time, and
  lean on the QM to handle context-switching.

## Two management surfaces

- `fleet watch` — live, read-only, at-a-glance dashboard. A glance.
- `fleet qm` — the reasoning delegate. A conversation.

## The engine owns state

Every state change goes through the `fleet` CLI. Agents never hand-edit the fleet
state directory. Callsign allocation is lock-guarded; every write is atomic.

## Communication

- **You ↔ worker**: direct chat in the worker's window. Blocking questions are answered
  here, by you, only.
- **You ↔ QM**: direct chat in the QM's window.
- **QM → worker**: `fleet msg` → the worker drains it with `fleet inbox` (async,
  non-blocking directives — never to answer a blocked worker).
- **Worker → QM**: the worker surfaces observations/complaints/status in its file; the
  QM reads them (`fleet status --verbose`).

## Lifecycle

1. `fleet recruit --agent "<cmd>"` — provision a worktree + launch a worker in it.
2. Brief the worker; it runs `/fleet-start` to enlist and work the
   observation→strategizing→execution loop, syncing at checkpoints.
3. Supervise via `fleet watch` and/or `fleet qm`.
4. The worker does its own commits + squash-merge + `/get-some-sleep`, then `fleet done`
   to tear down and archive.

## Command quick reference

```
fleet init                       install prompts + scaffold state
fleet recruit [--agent][--provider]  provision worktree + launch worker
fleet qm [--agent]               launch the quartermaster
fleet watch [--interval]         live dashboard
fleet status [--verbose]         one-shot readout
fleet inspect <callsign>         one worker's full file + mail
fleet msg <callsign|all> "…"     QM → worker directive
fleet sync [flags]               worker updates own state
fleet inbox [--all]              worker drains mailbox
fleet done                       worker teardown + archive
```
