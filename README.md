# fleet

Run several coding agents in the same repo at once — safely, and without drowning in
context-switching. `fleet` gives each agent its own isolated git worktree, a stable
callsign, a shared status board, and a **Quartermaster** manager that holds the whole
picture for you.

It installs anywhere: a small Python package with a `fleet` CLI plus a bundled prompt
pack, portable across projects and machines.

## The idea

The mechanics that must be deterministic — allocating callsigns, provisioning
worktrees, writing state, delivering messages — live in Python and are race-safe. The
judgment — when to sync, how to run a task, how the Quartermaster reasons — lives in
bundled prompts. Agents never hand-edit state; they shell out to `fleet`.

### Roles

- **Workers** — agents, each in its own worktree, holding a NATO callsign (`alpha`,
  `bravo`, …). Each self-manages its lane and owns all of its own git operations.
- **Quartermaster** (`fleet qm`) — a manager agent that reads every worker's context,
  summarizes for you, relays your directives down, and tells you who needs attention.
- **Dashboard** (`fleet watch`) — a live, read-only, at-a-glance board.

You stay in control: you recruit and brief each worker, work hands-on with one at a
time, and lean on the Quartermaster to manage the rest.

## Install

```bash
uv tool install fleet-workflow      # or: pipx install fleet-workflow
```

For local development:

```bash
uv tool install --editable .        # or: pipx install --editable .
```

State lives under `~/.claude-work/projects/<project-slug>/fleet/` (falling back to
`~/.claude/…`), outside your repo, so every worktree shares one view and your repo stays
clean. Set `FLEET_STATE_HOME` to relocate it.

## Choosing your agent

fleet is agnostic about *how* you launch your coding agent — you tell it the command.
Pass `--agent` per invocation, or set `FLEET_AGENT` once as your default:

```bash
fleet recruit --agent "claude"        # your personal agent
fleet recruit --agent "claude-work"   # a work-scoped wrapper
export FLEET_AGENT="claude"           # or set a default and just run `fleet recruit`
```

If you don't set either, `fleet recruit` provisions the worktree and prints the `cd`
line for you to launch the agent yourself.

## Use

New here? [GUIDE.md](GUIDE.md) is a linear first-run walkthrough. This section is the
short form.

```bash
# once per project
fleet init

# recruit a worker (provisions a worktree and launches your chosen agent in it)
fleet recruit --agent "<your-agent-command>"

# supervise
fleet watch                              # live dashboard
fleet qm --agent "<your-agent-command>"  # talk to the Quartermaster

# the worker keeps its own state fresh
fleet sync --stage execution --status running --next "backfill 400 SKUs"
fleet inbox                 # read directives from the QM
fleet done                  # tear down + archive when finished
```

## Worktree backends

- **plain** (default) — stock `git worktree`, zero dependencies.
- **treehouse** (strict opt-in, `--provider treehouse`) — leases pooled worktrees from
  the [treehouse](https://github.com/kunchenguid/treehouse) CLI for build-cache and
  dependency reuse across sessions. fleet takes a durable lease (`get --lease`) tagged
  with the callsign as lease holder, so the pool will not hand the worktree to anyone
  else or prune it until `fleet done` returns it. Verified against treehouse's CLI
  contract by source inspection; not yet exercised against a live install.

## Command reference

| Command | Who | What |
|---|---|---|
| `fleet init` | you | install prompts + scaffold state |
| `fleet recruit [--agent] [--provider]` | you | provision a worktree + launch a worker |
| `fleet qm [--agent]` | you | launch the Quartermaster |
| `fleet watch [--interval]` | you | live dashboard |
| `fleet status [--verbose]` | QM | one-shot readout |
| `fleet inspect <callsign>` | QM | one worker's full file + mail |
| `fleet msg <callsign\|all> "…"` | QM | async directive to a worker |
| `fleet sync [flags]` | worker | update own state |
| `fleet inbox [--all]` | worker | drain mailbox |
| `fleet done [--force]` | worker | teardown + archive |

`fleet done` refuses to tear down a worktree with uncommitted changes — including
untracked files — and lists what is at stake, so you never have to check before
standing a worker down. Pass `--force` to discard the work deliberately.

## Development

```bash
uv tool install --editable .
python -m pytest
```

The suite drives the real CLI against throwaway git repos, with `FLEET_STATE_HOME`
redirected into a tmp dir so it never touches your own fleet state. Tests live in
`tests/` and are not shipped in the wheel.

## License

MIT
