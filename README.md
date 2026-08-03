# fleet

cute project to manage multiple coding agents without getting overwhelmed

## the idea

inspired by kun chen's "first mate" setup.

there are a few major pain points when working with multiple coding agents in the same repo:
- one agent can touch a file that another agent is reading and confuse them
- one agent's git actions can interfere with another's and mess up your git state, thus confusing you
- when one file is modified by multiple agents each working on distinct things, it becomes hard to commit it to git cleanly. it's unnatural to commit specific line edits of a file (maybe your agent can do it, but it will spend precious tokens figuring it out)

these pain points are of the "stepping on each other's shoes" category, which is a communication problem. there are many ideas out there to solve inter-agent communication, but this is not one of them. instead, the aim is to avoid all of that noise by providing each agent with their own worktree. there's no longer a need for agents to communicate if you just isolate all of them!

there is another category of problem, which occurs somewhere between the keyboard and the chair. that is, the human brain gets tired, loses focus, and starts a slow meltdown process due to constant context switching. at optimal conditions with good focus, a human can juggle maybe 3 or 4 tasks concurrently, but beyond that (and even at that level of concurrency), you need some tools to help you out. 

this project features two thematic solutions:
- a worktree-isolation alias for spinning up a worker-agent in a git worktree with one command (and handling safe teardown once it's done)
- a coordinator agent, named the "quartermaster", which monitors the agents and assists the human in context switching and managing the worker-agents.

LLM dump below, with my editorials in (a.g. ... ) (those are my initials, not latin for anything):

### Roles

- **Workers** — agents, each in its own worktree, holding a NATO callsign drawn at
  random (`delta`, `romeo`, …). Each self-manages its lane and owns all of its own
  git operations. Claude Code workers show their callsign in the status line, so
  split panes stay tellable apart.
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

State lives under `~/.claude/projects/<project-slug>/fleet/`, outside your repo, so every worktree shares one view and your repo stays
clean. It follows `CLAUDE_CONFIG_DIR` when that is set, so a work-scoped agent keeps its
own fleet. Set `FLEET_STATE_HOME` to relocate it.

Upgrading from a version that stored state under `~/.claude-work`? `fleet migrate`
moves a project's state to wherever the current config dir puts it (`--dry-run` to
preview). Until you do, the old location is still read, so nothing disappears.

## Choosing your agent

fleet is agnostic about *how* you launch your coding agent — you tell it the command.
Pass `--agent` per invocation, or set `FLEET_AGENT` once as your default:

```bash
fleet recruit --agent "claude"        # your personal agent
fleet recruit --agent "claude-work"   # a work-scoped wrapper (a.g. i have a separate claude alias for my work account)
export FLEET_AGENT="claude"           # or set a default and just run `fleet recruit`
```

If you don't set either, `fleet recruit` provisions the worktree and prints the `cd`
line for you to launch the agent yourself.

## Use

New here? Run `fleet --guide` for a linear walkthrough in your terminal, or read
[GUIDE.md](GUIDE.md) for the same thing on the page. This section is the short form.

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
fleet done                  # stand down when finished; the worktree is released after you exit
```

## Worktree backends

- **plain** (default) — stock `git worktree`, zero dependencies.
- **treehouse** (strict opt-in, `--provider treehouse`) — leases pooled worktrees from
  the [treehouse](https://github.com/kunchenguid/treehouse) CLI for build-cache and
  dependency reuse across sessions. fleet takes a durable lease (`get --lease`) tagged
  with the callsign as lease holder, so the pool will not hand the worktree to anyone
  else or prune it until `fleet done` returns it. Verified against treehouse's CLI
  contract by source inspection; not yet exercised against a live install.
  (a.g. i came across this when watching kun chen's agentic setup video, which inspired this project, but i haven't really tried it. i have a philosophy of not using things until i encounter the problem/painpoint that prompted someone to build it. it seems like nowadays agents can manage their own worktrees just fine, and i don't really care too much about the worktree provision time... yet...)

## Command reference

| Command | Who | What |
|---|---|---|
| `fleet --guide` | you | linear walkthrough for a first run |
| `fleet init [--install-mail-hook]` | you | install prompts + scaffold state + offer the mail hook |
| `fleet migrate [--dry-run]` | you | move state written by an older version |
| `fleet recruit [--agent] [--provider]` | you | provision a worktree + launch a worker |
| `fleet qm [--agent]` | you | launch the Quartermaster |
| `fleet watch [--interval]` | you | live dashboard |
| `fleet ls [--porcelain]` | you / QM | compact one-line-per-worker roster |
| `fleet status [--verbose]` | QM | one-shot readout |
| `fleet inspect <callsign>` | QM | one worker's full file + mail |
| `fleet msg <callsign\|all> "…"` | QM | async directive, delivered when the worker next goes idle |
| `fleet dismiss <callsign> [--force]` | you / QM | stand down a worker from outside its worktree |
| `fleet sync [flags]` | worker | update own state |
| `fleet inbox [--all]` | worker | drain mailbox by hand |
| `fleet notify --check` | you | verify mail delivery is installed and reachable |
| `fleet notify --hook` | agent | Stop-hook entry point; delivers mail (never typed) |
| `fleet done [--force]` | worker | stand down; worktree released after you exit |

`fleet done` refuses to tear down a worktree with uncommitted changes — including
untracked files — and lists what is at stake, so you never have to check before
standing a worker down. Pass `--force` to discard the work deliberately.

Teardown happens in two steps, and `fleet done` is only the first. It marks the worker
`standing-down` and leaves the worktree on disk, because the session that ran it is
*standing in that directory* — deleting it out from under a live process leaves it with
a cwd that no longer exists, and on macOS such a process can no longer spawn children at
all, which silently kills mail delivery and every other subprocess for the rest of its
life. Once the worker has been `standing-down` for a couple of minutes, the next `fleet`
command anyone runs releases the worktree, archives the record, and frees the callsign —
and because that command runs somewhere else, it can never strand its own caller. The
worker stays visible in `fleet status` in between, so a teardown that stalls is obvious
rather than silent. `fleet dismiss` skips the wait: it exists for worktrees nobody is
inside.

Mail is pushed, not polled. `fleet init` offers to install a Claude Code `Stop` hook
that delivers a worker's unread directives into its context the moment it finishes a
turn and goes idle — so `fleet msg all "…"` actually reaches everyone instead of
waiting on each worker to run `fleet sync`. Delivery informs; it never interrupts a
worker mid-task, and it cannot compel one to act. The hook is opt-in (it is the only
thing fleet writes to your `settings.json`), inert outside fleet worktrees, and
removable with `fleet notify --uninstall`. Decline it and mail still works — workers
just see it on their next sync.

The hook invokes `fleet` by name, so it depends on PATH. A hook whose command cannot be
found fails *silently* — no error, no warning, a clean exit — so `fleet init` warns if
the name will not resolve, and `fleet notify --check` reports both halves (installed?
reachable?) on demand, exiting non-zero if delivery would not work.

`fleet done` runs from inside a worktree, which is no help when the agent never
started. `fleet dismiss <callsign>` stands a worker down from anywhere in the repo,
under the same rules. `fleet recruit` also resolves your agent command *before*
provisioning, so a command your shell cannot run fails with nothing left behind.

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
