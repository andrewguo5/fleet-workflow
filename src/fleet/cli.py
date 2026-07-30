"""The ``fleet`` command surface.

Command groups by caller:
  human        init, recruit, qm, watch
  quartermaster status, inspect, msg
  worker       sync, inbox, done   (callsign inferred from the current worktree)

The engine owns every state mutation; agents only ever shell out to these commands.
"""

from __future__ import annotations

import os
import time
from importlib import resources
from pathlib import Path

import typer
from rich.console import Console

from . import mailbox, status
from .callsign import FleetFullError, next_available
from .store import FleetStore, NotAGitRepoError, _run_git
from .worker import Worker, now_stamp, today_stamp
from .worktree import WorktreeError, get_provider

app = typer.Typer(
    add_completion=False,
    help="Manage a fleet of parallel coding agents, each in its own git worktree.",
    no_args_is_help=True,
)
console = Console()

COMMANDS_DIR = Path("~/.claude/commands").expanduser()
COMMAND_PROMPTS = ("fleet-start.md", "fleet-quartermaster.md")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _store() -> FleetStore:
    try:
        return FleetStore()
    except NotAGitRepoError:
        console.print("[red]fleet must be run inside a git repository.[/red]")
        raise typer.Exit(1)


def _fail(message: str) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


def _resolve_self(store: FleetStore) -> str:
    """Which worker am I? Inferred from the current worktree."""
    try:
        top = Path(_run_git(["rev-parse", "--show-toplevel"], cwd=Path.cwd())).resolve()
    except NotAGitRepoError:
        _fail("not inside a git repository.")
    for w in status.load_workers(store):
        if w.worktree and Path(w.worktree).resolve() == top:
            return w.worker
    if top.parent.name == "wt" and store.worker_path(top.name).exists():
        return top.name
    _fail("this directory is not a fleet worker's worktree — run worker commands from your worktree.")


def _load_worker(store: FleetStore, callsign: str) -> Worker:
    path = store.worker_path(callsign)
    if not path.exists():
        _fail(f"no worker '{callsign}'.")
    return Worker.parse(path.read_text(encoding="utf-8"))


def _agent_cmd(agent: str | None) -> str | None:
    return agent or os.environ.get("FLEET_AGENT")


# --------------------------------------------------------------------------
# human commands
# --------------------------------------------------------------------------

@app.command()
def init() -> None:
    """Install the prompt pack into ~/.claude/commands and scaffold this project's
    fleet state directory. Idempotent."""
    store = _store()
    store.ensure_dirs()

    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    prompts = resources.files("fleet") / "prompts"
    for name in COMMAND_PROMPTS:
        (COMMANDS_DIR / name).write_text((prompts / name).read_text(encoding="utf-8"), encoding="utf-8")
    # Protocol doc lives with the project's fleet state, not as a slash-command.
    store.atomic_write(store.base / "FLEET.md", (prompts / "FLEET.md").read_text(encoding="utf-8"))

    console.print(f"[green]fleet initialized[/green] for [bold]{store.repo_root}[/bold]")
    console.print(f"  state dir : {store.base}")
    console.print(f"  commands  : {COMMANDS_DIR}/{{{', '.join(COMMAND_PROMPTS)}}}")
    console.print("  recruit your first worker with: [bold]fleet recruit --agent \"<your-agent-cmd>\"[/bold]")


@app.command()
def recruit(
    agent: str = typer.Option(None, "--agent", help="Command that opens your coding agent, e.g. 'claude' or 'claude-work'. Falls back to $FLEET_AGENT."),
    provider: str = typer.Option("plain", "--provider", help="Worktree backend: plain | treehouse."),
) -> None:
    """Assign the next callsign, provision an isolated worktree+branch, write a worker
    stub, then chdir into the worktree and launch your agent there."""
    store = _store()
    store.ensure_dirs()

    with store.lock():
        try:
            callsign = next_available(store.live_callsigns())
        except FleetFullError as e:
            _fail(str(e))
        try:
            wt = get_provider(provider, store.repo_root).acquire(callsign)
        except WorktreeError as e:
            _fail(f"could not provision worktree: {e}")
        stub = Worker(
            worker=callsign,
            status="recruited",
            branch=f"fleet/{callsign}",
            worktree=wt,
            provider=provider,
            claimed=now_stamp(),
            updated=now_stamp(),
        )
        store.atomic_write(store.worker_path(callsign), stub.render())

    console.print(f"[green]recruited[/green] [bold]{callsign}[/bold]")
    console.print(f"  branch   : fleet/{callsign}")
    console.print(f"  worktree : {wt}")

    from .launch import launch  # imported late; launch() may exec and never return
    hint = launch(Path(wt), _agent_cmd(agent))
    if hint:
        console.print("  no --agent given; open your agent yourself:")
        console.print(f"    [bold]{hint}[/bold]")


@app.command()
def qm(
    agent: str = typer.Option(None, "--agent", help="Command that opens your coding agent, e.g. 'claude' or 'claude-work'. Falls back to $FLEET_AGENT."),
) -> None:
    """Launch the Quartermaster agent in the repo root, primed with /fleet-quartermaster."""
    store = _store()
    store.ensure_dirs()
    from .launch import launch
    hint = launch(store.repo_root, _agent_cmd(agent), initial_prompt="/fleet-quartermaster")
    if hint:
        console.print("  no --agent given; start the quartermaster yourself:")
        console.print(f"    [bold]{hint}[/bold]")
        console.print("  then run [bold]/fleet-quartermaster[/bold] in that session.")


@app.command()
def watch(
    interval: float = typer.Option(30.0, "--interval", help="Refresh seconds."),
) -> None:
    """Live, read-only dashboard of the whole fleet."""
    from rich.live import Live

    store = _store()
    try:
        with Live(status.build_table(store), console=console, refresh_per_second=4, screen=True) as live:
            while True:
                live.update(status.build_table(store))
                time.sleep(interval)
    except KeyboardInterrupt:
        pass


# --------------------------------------------------------------------------
# quartermaster commands
# --------------------------------------------------------------------------

@app.command(name="status")
def status_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Add a detail panel per worker."),
) -> None:
    """Compile and print the fleet's state (one-shot)."""
    store = _store()
    console.print(status.build_report(store, verbose=verbose))


@app.command()
def inspect(callsign: str = typer.Argument(..., help="Worker callsign.")) -> None:
    """Dump one worker's full file and its mail trail."""
    store = _store()
    worker = _load_worker(store, callsign)
    console.rule(f"workers/{callsign}.md")
    console.print(store.worker_path(callsign).read_text(encoding="utf-8"), markup=False)
    messages = mailbox.all_messages(store, callsign)
    if messages:
        console.rule(f"mail/{callsign}.md")
        for m in messages:
            console.print(m.render(), markup=False)


@app.command()
def msg(
    target: str = typer.Argument(..., help="Callsign, or 'all' to broadcast."),
    text: str = typer.Argument(..., help="Message body."),
) -> None:
    """Post an async directive to a worker's mailbox (quartermaster -> worker)."""
    store = _store()
    if target == "all":
        recipients = store.live_callsigns()
        if not recipients:
            _fail("no workers to message.")
    else:
        if not store.worker_path(target).exists():
            _fail(f"no worker '{target}'.")
        recipients = [target]
    for callsign in recipients:
        mailbox.post(store, callsign, text)
    console.print(f"[green]delivered[/green] to {', '.join(recipients)}")


# --------------------------------------------------------------------------
# worker commands (callsign inferred from cwd)
# --------------------------------------------------------------------------

@app.command()
def sync(
    stage: str = typer.Option(None, "--stage", help="observation | strategizing | execution"),
    stat: str = typer.Option(None, "--status", help="running | waiting | requesting-input | done"),
    thread: str = typer.Option(None, "--thread", help="Short free-text label for this workstream."),
    next_step: str = typer.Option(None, "--next", help="One-line next step."),
    previous_step: str = typer.Option(None, "--previous", help="One-line previous step."),
    task: str = typer.Option(None, "--task", help="Set the ## Task brief."),
    question: str = typer.Option(None, "--question", help="Set the ## Question (implies status=requesting-input)."),
    observe: str = typer.Option(None, "--observe", help="Append an observation."),
    complain: str = typer.Option(None, "--complain", help="Append a complaint."),
    todo: str = typer.Option(None, "--todo", help="Append a to-do item."),
    handoff: str = typer.Option(None, "--handoff", help="Path to the durable handoff shard."),
) -> None:
    """Update your own worker file, then report unread mail."""
    store = _store()
    callsign = _resolve_self(store)
    w = _load_worker(store, callsign)

    if thread is not None:
        w.thread = thread
    if stage is not None:
        w.stage = stage
    if next_step is not None:
        w.next_step = next_step
    if previous_step is not None:
        w.previous_step = previous_step
    if handoff is not None:
        w.handoff = handoff
    if task is not None:
        w.set_section("Task", task)
    if observe is not None:
        w.append_bullet("Observations", observe)
    if complain is not None:
        w.append_bullet("Complaints", complain)
    if todo is not None:
        w.append_bullet("To-Do", todo, numbered=True)

    if question is not None:
        w.set_section("Question", question)
        if stat is None:
            stat = "requesting-input"
    if stat is not None:
        w.status = stat
        if stat != "requesting-input":
            w.remove_section("Question")

    w.updated = now_stamp()
    store.atomic_write(store.worker_path(callsign), w.render())

    unread = mailbox.unread_count(store, callsign)
    console.print(f"[green]{callsign}[/green] synced ({w.stage or '?'}/{w.status or '?'})")
    if unread:
        console.print(f"[bold magenta]{unread} unread message(s)[/bold magenta] — run [bold]fleet inbox[/bold]")


@app.command()
def inbox(
    all_: bool = typer.Option(False, "--all", help="Show the full mail history, not just unread."),
) -> None:
    """Drain your mailbox: print unread messages and mark them read."""
    store = _store()
    callsign = _resolve_self(store)
    if all_:
        messages = mailbox.all_messages(store, callsign)
        if not messages:
            console.print("[dim]no mail.[/dim]")
        for m in messages:
            console.print(m.render(), markup=False)
        return
    unread = mailbox.drain(store, callsign)
    if not unread:
        console.print("[dim]no new mail.[/dim]")
        return
    console.print(f"[bold]{len(unread)} new message(s):[/bold]")
    for m in unread:
        console.print(f"  {m.stamp} {m.attribution} {m.text}", markup=False)


@app.command()
def done() -> None:
    """Mark done, tear down the worktree, and archive the worker file. Runs NO content
    git ops — commit, squash-merge, and handoff are the worker's own responsibility and
    must happen before this."""
    store = _store()
    callsign = _resolve_self(store)
    w = _load_worker(store, callsign)
    w.status = "done"
    w.updated = now_stamp()

    # Fold the mail trail into the archived record.
    messages = mailbox.all_messages(store, callsign)
    if messages:
        w.set_section("Mail (archived)", "\n".join(m.render() for m in messages))

    with store.lock():
        try:
            get_provider(w.provider or "plain", store.repo_root).release(callsign, w.worktree or "")
        except WorktreeError as e:
            console.print(f"[yellow]worktree teardown warning: {e}[/yellow]")
        archive_path = store.archive_dir / f"{today_stamp()}-{callsign}.md"
        store.atomic_write(archive_path, w.render())
        store.worker_path(callsign).unlink(missing_ok=True)
        store.mail_path(callsign).unlink(missing_ok=True)

    console.print(f"[green]{callsign} stood down.[/green] archived -> {archive_path}")


if __name__ == "__main__":
    app()
