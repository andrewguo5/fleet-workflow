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

Installs the prompt pack into `~/.claude/commands` (or `$CLAUDE_CONFIG_DIR/commands`, so
run it from the agent you'll use) and scaffolds this project's state directory. Safe to
re-run.

## Start your first worker

```bash
fleet recruit
```

Draws a free NATO callsign at random (say `delta`), creates an isolated worktree at
`../wt/delta` on a new `fleet/delta` branch, and launches your agent inside it. Your
current terminal *becomes* that agent session. `fleet ls` tells you who you got.

The branch is cut from a **freshly fetched `origin/main`**, not from whatever your repo
happens to have checked out. A worker that starts stale re-solves problems already fixed
on the trunk and then conflicts with them on merge, so recruit fetches first and prints
the base it used. Offline, it says so and falls back to your local trunk rather than
refusing to work; `--no-fetch` skips the fetch deliberately.

Claude Code sessions show their callsign in the status line (`⬢ delta`), so agents
stay tellable apart when several share a window in split panes. The callsign is also
exported as `$FLEET_CALLSIGN`. The status line is scoped to the worktree — your own
`~/.claude/settings.json` is left alone.

The session opens already primed with `/fleet-start`, so the agent enlists itself and
briefs itself on how to be a worker. You don't type anything to make that happen — just
tell it what you want built, in your own words.

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

Sends a directive to one worker or broadcasts to all. It is delivered into the worker's
context the moment it next finishes a turn and goes idle, so a broadcast reaches everyone
without you chasing each session. This does not interrupt anyone: a worker mid-task
finishes what it is doing first, then reads its mail.

Automatic delivery is a `Stop` hook that `fleet init` offers to install into your agent's
`settings.json`. It is opt-in, inert outside fleet worktrees, and removable with
`fleet notify --uninstall`. Decline it and mail still works — workers just see it on
their next `fleet sync` instead.

If mail ever seems not to arrive, run `fleet notify --check`. The hook finds `fleet` on
your PATH, and when it can't it fails silently — `--check` is what makes that visible.

## Finish a worker

The worker commits and merges its own branch — fleet never does git for you. Once its
work is merged and it's ready to stand down, **inside that worker's session**:

```bash
fleet done
```

Marks the worker `standing-down`. **Your worktree is still there afterwards — that is
correct, not a failure.** `fleet done` runs from inside the worktree, and deleting a
directory out from under the session standing in it leaves that session unable to spawn
anything at all. So the delete waits: once you exit, the next `fleet` command anyone runs
releases the worktree, archives the record, and frees the callsign.

Teardown also collects the `fleet/alpha` branch, so the callsign comes back clean. If
your work landed on the trunk, the branch is deleted — every commit is already upstream,
so nothing is lost. If it didn't, the branch is **kept** and renamed to
`fleet/alpha.abandoned-<date>`: that branch may be the only copy of the work, but leaving
it under its original name would quietly reserve the callsign forever.

Use `fleet dismiss <callsign>` from outside the worktree when you want teardown to happen
immediately — that path is for worktrees nobody is inside, so it doesn't wait.

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
