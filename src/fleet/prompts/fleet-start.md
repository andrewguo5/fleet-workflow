---
description: Enlist this session as a fleet worker and run the observation → strategizing → execution loop, syncing state through the `fleet` CLI.
---

# Fleet worker — start

You have been launched by `fleet recruit` into your own git worktree. You are a
**worker** in a fleet of parallel agents. Your callsign is inferred from your worktree
(the `fleet` CLI knows it — you never pass it). Everything about your live state goes
through the `fleet` CLI; **never hand-edit files under the fleet state directory.**

## 1. Enlist

You were just briefed (or are about to be) by the human on what to work on. Once you
understand the task, record it:

```
fleet sync --thread "<short label>" --task "<the brief in your own words>" \
           --stage observation --status running --next "<your first concrete step>"
```

This flips you from `recruited` to a live, running worker the quartermaster can see.

## 2. Work the three stages

Follow the observation → strategizing → execution loop. At each **stage transition**
and after any meaningful step, heartbeat with `fleet sync`:

- `--stage observation|strategizing|execution` as you move through them.
- `--previous "<one line>"` / `--next "<one line>"` so the quartermaster and dashboard
  always show where you are.
- `--observe "<insight>"` to surface something worth the human's/QM's attention (a bug
  found in passing, a risk, a data anomaly). Triage in place if easy; otherwise
  `--todo "<item>"`.
- `--complain "<friction>"` for anything slowing you down (stale fixtures, missing
  access). The QM reads these.

Mail reaches you two ways. Directives are **delivered to you automatically** when you
finish a turn and go idle — they arrive as "Fleet mail" in your context, and they are
genuine instructions from the quartermaster or the human, not something to distrust.
Act on them; report what you did with `fleet sync`. Separately, every `fleet sync`
prints your unread-mail count; if it is ever non-zero, run `fleet inbox` to read the
backlog.

## 3. Git hygiene — you own it

You work on branch `fleet/<callsign>` in your worktree. Commit frequently to
checkpoint. When the work is truly done, **you** perform the squash + merge yourself
with a consolidated message summarizing your commits. fleet never does git for you.

## 4. When you need the human

If you hit a decision only the human should make, ask them **in this window** the
normal way (a question with a few options). Before you ask, mark yourself blocked so
the QM/dashboard can see it and point the human here:

```
fleet sync --status requesting-input --question "<the question> — (1) … (2) … (3) …"
```

The quartermaster will *not* answer for you; only the human, here, will. Once answered,
`fleet sync --status running` to clear the flag.

## 5. Finishing

After your own merge and your own `/get-some-sleep` handoff, stand down:

```
fleet done
```

This marks you `standing-down`. Do it only after the content work and handoff are
complete.

**Your worktree will still be there afterwards — that is correct, not a failure.**
Deleting it while you are standing in it would leave your session with a cwd that no
longer exists, after which you could not run *any* command for the rest of your life.
So `fleet done` stops short: once you exit, the next `fleet` command anyone runs
releases the worktree, archives your file, and frees your callsign. Report that you
stood down and finish your turn; there is nothing further for you to do, and nothing
to retry.
