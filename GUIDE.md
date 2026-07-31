# fleet — first run

A linear walkthrough for your first fleet. Run the commands top to bottom; each line
below the command says what just happened. For the full command reference, see the
[README](README.md).

The same walkthrough is available from the CLI, without leaving your terminal:

```bash
fleet --guide
```

## Setup (once per machine)

```bash
uv tool install fleet-workflow
```

Installs the `fleet` CLI. Use `pipx install fleet-workflow` if you prefer pipx.

```bash
export FLEET_AGENT="claude"
```

Tells fleet how to launch your coding agent, so you don't pass `--agent` every time.
Put this in your shell profile. Any command works here — `claude`, or a wrapper like
`claude-work`.

## Setup (once per project)

```bash
cd ~/your-project
fleet init
```

Installs the prompt pack into `~/.claude/commands` and scaffolds this project's state
directory. Safe to re-run.

## Start your first worker

```bash
fleet recruit
```

Draws a free NATO callsign at random (say `delta`), creates an isolated worktree at
`../wt/delta` on a new `fleet/delta` branch, and launches your agent inside it. Your
current terminal *becomes* that agent session. `fleet ls` tells you who you got.

Claude Code sessions show their callsign in the status line (`⬢ delta`), so agents
stay tellable apart when several share a window in split panes. The callsign is also
exported as `$FLEET_CALLSIGN`. The status line is scoped to the worktree — your own
`~/.claude/settings.json` is left alone.

```
> /fleet-start
```

Type this **inside the agent session** that just opened. It briefs the agent on how to
be a worker. Then tell it what you want built, in your own words.

The agent now works on its own, keeping its status current as it goes. Leave it running.

## Add more workers

```bash
# in a new terminal tab, from the project root
fleet recruit
```

Gets the next callsign (`bravo`), its own worktree, its own branch. Repeat per parallel
task. Workers never touch each other's files.

## Watch the fleet

```bash
fleet watch
```

A live dashboard: every worker's stage, status, next step, and unread mail. Read-only
and safe to leave open in a spare tab. `Ctrl-C` to exit.

Look for status `requesting-input` — that means a worker is blocked on a question only
you can answer. Go to that worker's tab and answer it there.

## Supervise without reading everything

```bash
fleet qm
```

Launches the **Quartermaster**: a manager agent that reads every worker's state and
summarizes it for you. Ask it "who needs attention?" or "what's the fleet doing?"
instead of reading each worker yourself.

```bash
fleet msg alpha "prioritize the token refresh path"
fleet msg all "pause after your current step"
```

Sends a directive to one worker or broadcasts to all. It lands in their mailbox and
they pick it up on their next sync — this does not interrupt them.

## Finish a worker

The worker commits and merges its own branch — fleet never does git for you. Once its
work is merged and it's ready to stand down, **inside that worker's session**:

```bash
fleet done
```

Tears down the worktree and archives the record. The `fleet/alpha` branch is left
alone, and the callsign is freed for reuse.

If there are uncommitted changes, this **refuses and shows you what's uncommitted** —
you never need to check first. Commit or stash, then re-run. To discard the work
deliberately:

```bash
fleet done --force
```

## Day-to-day loop

Once you're set up, the rhythm is:

```bash
fleet recruit          # start a task
fleet watch            # see who needs you
fleet qm               # ask what's going on
fleet done             # retire a finished worker (from its own session)
```

## Where things live

State lives outside your repo, so worktrees share one view and your repo stays clean:

```
~/.claude/projects/<project-slug>/fleet/
```

It follows `CLAUDE_CONFIG_DIR` when that is set. Set `FLEET_STATE_HOME` to relocate it.
Worktrees are created next to your repo in `../wt/<callsign>`.
