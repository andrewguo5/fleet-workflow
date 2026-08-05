"""The ``fleet`` command surface.

Command groups by caller:
  human        init, recruit, qm, watch
  quartermaster status, inspect, msg
  worker       sync, inbox, done   (callsign inferred from the current worktree)
  agent hook   notify              (wired into Claude Code by init; never typed)

The engine owns every state mutation; agents only ever shell out to these commands.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from importlib import resources
from pathlib import Path

import typer
from rich.console import Console

from . import guide, hookinstall, mailbox, notify as notify_mod, status, statusline
from . import launch as launch_mod
from . import worktree as worktree_mod
from .callsign import FleetFullError, pick_available
from .store import FleetStore, NotAGitRepoError, _run_git
from .worker import Worker, now_stamp, today_stamp
from .worktree import WorktreeError, get_provider

app = typer.Typer(
    add_completion=False,
    help="Manage a fleet of parallel coding agents, each in its own git worktree.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_CONFIG_DIR = Path("~/.claude")
COMMAND_PROMPTS = ("fleet-start.md", "fleet-quartermaster.md")

# Slash commands the launched sessions are primed with, so an agent takes up its role
# without the human having to remember to type anything.
WORKER_PROMPT = "/fleet-start"
QUARTERMASTER_PROMPT = "/fleet-quartermaster"


def commands_dir() -> Path:
    """Where the slash-command prompts belong, for the agent that is running.

    Honors ``CLAUDE_CONFIG_DIR`` the way Claude Code itself does, so a wrapper like
    ``claude-work () { CLAUDE_CONFIG_DIR=~/.claude-work command claude "$@" }`` installs
    the prompts into the config it will actually read. Resolved per call, not at import,
    because the environment differs between the shell that ran ``fleet`` and any agent
    session it later launches.
    """
    return config_dir() / "commands"


def config_dir() -> Path:
    """The agent's config directory — where ``settings.json`` lives.

    Same ``CLAUDE_CONFIG_DIR`` resolution Claude Code uses, so the mail hook is written
    to the config the agent will actually read.
    """
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(configured) if configured else DEFAULT_CONFIG_DIR
    return base.expanduser()

# How many dirty paths `fleet done` lists before collapsing the rest into a count.
DIRTY_PREVIEW_LIMIT = 10

# How long a worker sits `standing-down` before any other fleet command releases its
# worktree. It is a proxy for "the session that ran `fleet done` has exited": cheap and
# self-correcting in both directions, where probing the directory for a live process
# would be platform-specific and slow on every `fleet status`. Being early costs one
# stranded session (recoverable with `cd ~`); being late costs a worktree lingering
# until the next command. Whole minutes, because `updated` is minute-resolution.
REAP_GRACE_MINUTES = 2


def _show_guide(value: bool) -> None:
    """Eager ``--guide`` callback: print the walkthrough and exit before any command.

    Deliberately does no git or state lookup — someone reading the guide has very
    likely not run ``fleet init`` yet, and may not even be inside a repo.
    """
    if not value:
        return
    guide.print_guide(console)
    raise typer.Exit()


@app.callback()
def main(
    show_guide: bool = typer.Option(
        False,
        "--guide",
        callback=_show_guide,
        is_eager=True,
        help="Show a linear walkthrough of the commands, in the order you'd run them.",
    ),
) -> None:
    """Manage a fleet of parallel coding agents, each in its own git worktree."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _store() -> FleetStore:
    try:
        store = FleetStore()
    except NotAGitRepoError:
        console.print("[red]fleet must be run inside a git repository.[/red]")
        raise typer.Exit(1)
    # Every state-touching command funnels through here, so each one sweeps up the
    # teardowns left half-finished before it. See `_reap_expired`.
    _reap_expired(store)
    return store


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
def init(
    commands_dir_override: str = typer.Option(
        None,
        "--commands-dir",
        help="Where to install the slash-command prompts. Defaults to $CLAUDE_CONFIG_DIR/commands, else ~/.claude/commands.",
    ),
    install_hook: bool = typer.Option(
        False, "--install-mail-hook", help="Install the mail-delivery Stop hook without prompting."
    ),
) -> None:
    """Install the prompt pack for the agent you're running and scaffold this project's
    fleet state directory. Idempotent.

    The prompts go to ``$CLAUDE_CONFIG_DIR/commands`` when that is set, so running this
    from a work-scoped agent installs them where that agent will look."""
    store = _store()
    store.ensure_dirs()

    target = Path(commands_dir_override).expanduser() if commands_dir_override else commands_dir()
    target.mkdir(parents=True, exist_ok=True)
    prompts = resources.files("fleet") / "prompts"
    for name in COMMAND_PROMPTS:
        (target / name).write_text((prompts / name).read_text(encoding="utf-8"), encoding="utf-8")
    # Protocol doc lives with the project's fleet state, not as a slash-command.
    store.atomic_write(store.base / "FLEET.md", (prompts / "FLEET.md").read_text(encoding="utf-8"))

    console.print(f"[green]fleet initialized[/green] for [bold]{store.repo_root}[/bold]")
    console.print(f"  state dir : {store.base}")
    console.print(f"  commands  : {target}/{{{', '.join(COMMAND_PROMPTS)}}}")
    if not commands_dir_override and not os.environ.get("CLAUDE_CONFIG_DIR"):
        console.print(
            "  [dim]running a work/personal-scoped agent? re-run init from that session,[/dim]\n"
            "  [dim]or pass --commands-dir, so the prompts land where it reads them.[/dim]"
        )
    # Always the real config dir, never `target.parent`: --commands-dir may point
    # somewhere arbitrary, and settings.json belongs where the agent actually reads it.
    _offer_mail_hook(config_dir(), assume_yes=install_hook)

    console.print("  recruit your first worker with: [bold]fleet recruit --agent \"<your-agent-cmd>\"[/bold]")
    console.print("  new to fleet? [bold]fleet --guide[/bold] walks through the whole loop.")


def _confirm_interactively(prompt: str) -> bool:
    """Ask yes/no, treating a non-interactive run as "no".

    ``fleet init`` is routinely run without a TTY — from a script, from CI, from the
    test suite. A bare ``typer.confirm`` aborts there with a non-zero exit, which would
    turn an optional convenience into a hard failure of the whole init.
    """
    if not sys.stdin.isatty():
        return False
    try:
        return typer.confirm(prompt, default=False)
    except (typer.Abort, EOFError):
        return False


def _offer_mail_hook(agent_config: Path, assume_yes: bool = False) -> None:
    """Ask before touching the user's settings.json, then install the mail hook.

    Opt-in on purpose. Unlike the status line — which is scoped to a worktree and
    disappears with it — this edits a file fleet does not own and affects every session
    the user runs, so it is shown in full and declined by default. Declining costs only
    push delivery; `fleet sync` still reports unread mail.
    """
    settings_path = agent_config / hookinstall.SETTINGS_FILE
    try:
        if hookinstall.is_installed_in(agent_config):
            console.print(f"  mail hook : [green]already installed[/green] in {settings_path}")
            return
    except ValueError as e:
        console.print(f"  mail hook : [yellow]skipped[/yellow] — {e}")
        return

    console.print()
    console.print("  [bold]Deliver mail automatically?[/bold]")
    console.print("  Workers only notice mail when they run [bold]fleet sync[/bold], so a directive can")
    console.print("  sit unread. A Stop hook delivers it the moment a worker goes idle.")
    console.print(f"  This adds to [bold]{settings_path}[/bold]:")
    for line in hookinstall.preview().splitlines():
        console.print(f"    [dim]{line}[/dim]")
    console.print("  [dim]It exits immediately outside a fleet worktree. Remove it any time with[/dim]")
    console.print("  [dim]`fleet notify --uninstall`.[/dim]")

    if not (assume_yes or _confirm_interactively("  Add it?")):
        console.print("  mail hook : [yellow]skipped[/yellow] — workers see mail on their next `fleet sync`.")
        console.print("  [dim]enable it later with `fleet init --install-mail-hook`.[/dim]")
        return
    try:
        hookinstall.install(agent_config)
    except (ValueError, OSError) as e:
        console.print(f"  mail hook : [yellow]not installed[/yellow] — {e}")
        return
    console.print("  mail hook : [green]installed[/green]")
    _warn_if_hook_unresolvable()


def _warn_if_hook_unresolvable() -> None:
    """Say so now if the hook's command will not resolve when it runs.

    An unresolvable hook is completely silent at runtime — no error, no warning, a clean
    exit — so install time is the only chance to tell the user. Not fatal: PATH may
    simply differ between this process and the agent's, and the hook starts working the
    moment `fleet` is reachable.
    """
    resolved = hookinstall.resolves()
    if resolved is None:
        console.print(
            f"  [yellow]warning[/yellow]: [bold]{hookinstall.HOOK_BINARY}[/bold] is not on PATH here, so the hook\n"
            "  would do nothing. It fails silently — there is no error to notice later.\n"
            "  Put fleet on your PATH (a uv/pipx install lands in ~/.local/bin), then\n"
            "  confirm with [bold]fleet notify --check[/bold]."
        )
        return
    if not hookinstall.supports_delivery(resolved):
        console.print(
            f"  [yellow]warning[/yellow]: the [bold]{hookinstall.HOOK_BINARY}[/bold] on your PATH ({resolved})\n"
            "  is too old to deliver mail, and the hook runs that one, not this copy.\n"
            "  Upgrade it, then confirm with [bold]fleet notify --check[/bold]."
        )


@app.command()
def migrate(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would move, and change nothing."),
) -> None:
    """Move this project's fleet state to where the current config dir puts it.

    State used to live under ``~/.claude-work`` whenever that directory merely existed,
    regardless of which agent was running; it now follows ``CLAUDE_CONFIG_DIR``. This
    relocates state written the old way. Copies first and only removes the source once
    the copy is verified, so an interrupted run cannot lose a worker."""
    store = _store()
    destination = store.intended_base()
    source = store.legacy_base()

    if source is None:
        console.print(f"[green]already in place[/green] — {destination}")
        return

    workers = sorted(p.name for p in (source / "workers").glob("*.md")) if (source / "workers").exists() else []
    console.print(f"  from : {source}")
    console.print(f"  to   : {destination}")
    console.print(f"  workers: {', '.join(w.removesuffix('.md') for w in workers) or 'none'}")

    if dry_run:
        console.print("\n[dim]--dry-run: nothing moved.[/dim]")
        return

    if destination.exists() and any(destination.iterdir()):
        _fail(
            f"destination already has state: {destination}\n"
            "merge it by hand, or move it aside, then re-run."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)

    # Verify before deleting: every file in the source must exist at the destination.
    missing = [
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file() and not (destination / path.relative_to(source)).exists()
    ]
    if missing:
        _fail(
            f"copy incomplete ({len(missing)} file(s) missing); source left untouched at {source}"
        )

    shutil.rmtree(source)
    console.print(f"\n[green]migrated[/green] {len(workers)} worker(s) -> {destination}")


@app.command()
def recruit(
    agent: str = typer.Option(None, "--agent", help="Command that opens your coding agent, e.g. 'claude' or 'claude-work'. Falls back to $FLEET_AGENT."),
    provider: str = typer.Option("plain", "--provider", help="Worktree backend: plain | treehouse."),
    fetch: bool = typer.Option(True, "--fetch/--no-fetch", help="Fetch the remote first so the worktree is cut from an up-to-date trunk."),
) -> None:
    """Draw a free callsign at random, provision an isolated worktree+branch, write a worker
    stub, then chdir into the worktree and launch your agent there, primed to enlist.

    The branch is cut from the freshly-fetched remote trunk, so a worker never inherits
    an unpulled local trunk or whatever branch the main worktree happens to be on."""
    store = _store()
    store.ensure_dirs()

    # Resolve the agent command before provisioning anything. launch() execs and never
    # returns, so a command that cannot start would otherwise strand a worker holding a
    # worktree with no agent in it and no error on screen.
    resolved_agent = _agent_cmd(agent)
    if resolved_agent and not launch_mod.can_launch(resolved_agent):
        _fail(
            f"agent command not found: {resolved_agent!r}\n"
            "nothing was provisioned. pass a command your shell can run "
            "(--agent, or $FLEET_AGENT)."
        )

    # Resolve the base before taking the lock: the fetch is the slow part, and holding
    # the callsign lock across a network round-trip would serialize parallel recruits.
    base = worktree_mod.fresh_trunk(store.repo_root, fetch=fetch)

    with store.lock():
        try:
            callsign = pick_available(store.live_callsigns())
        except FleetFullError as e:
            _fail(str(e))
        # A branch that outlived its worker must never become the new worker's base.
        # Teardown normally collects it, so reaching here means something escaped that
        # path — a crash, a kill -9. Reclaim it in place rather than failing the
        # recruit: the callsign is legitimately free, and only the ref is in the way.
        _collect_branch(store, f"fleet/{callsign}", context="reclaimed")
        try:
            wt = get_provider(provider, store.repo_root).acquire(callsign, base=base.ref)
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

    statusline.install(Path(wt), callsign)

    console.print(f"[green]recruited[/green] [bold]{callsign}[/bold]")
    console.print(f"  branch   : fleet/{callsign}")
    console.print(f"  worktree : {wt}")
    console.print(f"  base     : {base.ref}" + ("" if base.fetched else " [dim](not fetched)[/dim]"))
    if base.warning:
        # Never let an unverified base pass silently — that is the whole failure mode.
        console.print(f"  [yellow]warning  : {base.warning}[/yellow]")

    # Prime the session with /fleet-start so the worker enlists on its own. Left to the
    # human, this step is silently skippable: the agent opens looking perfectly normal
    # and simply never joins the fleet, so it goes missing from watch/status with no
    # error anywhere. Same mechanism qm uses to prime the Quartermaster.
    hint = launch_mod.launch(
        Path(wt), resolved_agent, initial_prompt=WORKER_PROMPT, callsign=callsign
    )
    if hint:
        console.print("  no --agent given; open your agent yourself:")
        console.print(f"    [bold]{hint}[/bold]")
        console.print(f"  then run [bold]{WORKER_PROMPT}[/bold] in that session.")


@app.command()
def qm(
    agent: str = typer.Option(None, "--agent", help="Command that opens your coding agent, e.g. 'claude' or 'claude-work'. Falls back to $FLEET_AGENT."),
) -> None:
    """Launch the Quartermaster agent in the repo root, primed with /fleet-quartermaster."""
    store = _store()
    store.ensure_dirs()
    resolved_agent = _agent_cmd(agent)
    # Nothing is provisioned here, but exec still replaces this process, so an
    # unresolvable command would exit silently with no explanation.
    if resolved_agent and not launch_mod.can_launch(resolved_agent):
        _fail(f"agent command not found: {resolved_agent!r}")
    hint = launch_mod.launch(
        store.repo_root, resolved_agent, initial_prompt=QUARTERMASTER_PROMPT
    )
    if hint:
        console.print("  no --agent given; start the quartermaster yourself:")
        console.print(f"    [bold]{hint}[/bold]")
        console.print(f"  then run [bold]{QUARTERMASTER_PROMPT}[/bold] in that session.")


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

@app.command(name="ls")
@app.command(name="list")
def list_cmd(
    porcelain: bool = typer.Option(
        False,
        "--porcelain",
        help="Tab-separated (callsign, status, stage, thread) for scripts. Stable output.",
    ),
) -> None:
    """List the active workers, one per line, and exit.

    The quick 'who is out there' answer. ``fleet status`` is the fuller board and
    ``fleet watch`` the live one."""
    store = _store()
    if porcelain:
        listing = status.porcelain_listing(store)
        if listing:
            # print, not console.print: no wrapping or markup in parsed output.
            print(listing)
        return
    console.print(status.build_listing(store))


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


def _check_mail_hook() -> None:
    """Report whether mail would actually be delivered, and exit non-zero if not.

    Two independent things have to hold, and either one failing is silent at runtime:
    the hook must be in the agent's settings, and its command must resolve when the
    agent runs it. Reported separately so the fix is obvious.
    """
    target = config_dir()
    settings_path = target / hookinstall.SETTINGS_FILE

    try:
        installed = hookinstall.is_installed_in(target)
    except ValueError as e:
        _fail(f"cannot read {settings_path}: {e}")

    if installed:
        console.print(f"[green]installed[/green]  hook present in {settings_path}")
    else:
        console.print(f"[yellow]not installed[/yellow]  no mail hook in {settings_path}")
        console.print("  add it with [bold]fleet init --install-mail-hook[/bold]")

    resolved = hookinstall.resolves()
    usable = False
    if resolved is None:
        console.print(f"[red]unresolved[/red] [bold]{hookinstall.HOOK_BINARY}[/bold] is not on PATH")
        console.print("  the hook would run and do nothing, with no error to notice.")
        console.print("  put fleet on your PATH (uv/pipx installs land in ~/.local/bin).")
    elif not hookinstall.supports_delivery(resolved):
        console.print(f"[red]too old[/red]    {resolved} does not support [bold]notify[/bold]")
        console.print("  the hook runs whichever fleet PATH finds — this one cannot deliver.")
        console.print("  upgrade that install, or put a newer fleet earlier on PATH.")
    else:
        usable = True
        console.print(f"[green]resolves[/green]   [bold]{hookinstall.HOOK_BINARY}[/bold] -> {resolved}")

    if installed and usable:
        console.print("\n[green]mail delivery is working.[/green]")
        return
    raise typer.Exit(1)


@app.command()
def notify(
    hook: bool = typer.Option(False, "--hook", help="Run as a Claude Code Stop hook: JSON on stdin, JSON on stdout."),
    check: bool = typer.Option(False, "--check", help="Report whether mail delivery is installed and working."),
    uninstall: bool = typer.Option(False, "--uninstall", help="Remove the mail-delivery hook from your settings."),
) -> None:
    """Deliver unread mail to a worker that is going idle.

    Not meant to be typed. ``fleet init`` wires this into the agent's Stop hook, which
    fires when a worker finishes its turn — the moment mail should surface. Without it,
    mail sits unread until the worker happens to run ``fleet sync``.
    """
    if check:
        _check_mail_hook()
        return
    if uninstall:
        target = config_dir()
        try:
            removed = hookinstall.uninstall(target)
        except (ValueError, OSError) as e:
            _fail(str(e))
        if removed:
            console.print(f"[green]mail hook removed[/green] from {target / hookinstall.SETTINGS_FILE}")
        else:
            console.print("[yellow]no mail hook installed[/yellow] there.")
        return
    if not hook:
        console.print("[dim]notify is a hook entry point; run it with --hook, or let "
                      "[bold]fleet init[/bold] wire it up.[/dim]")
        return
    notify_mod.run_hook()


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


def _refuse_if_dirty(w: Worker, force: bool, invoked_as: str) -> None:
    """Block teardown while the worktree holds uncommitted work.

    Runs before anything is mutated: a refusal must leave the worker fully intact.
    """
    if force:
        return
    entries = worktree_mod.dirty_entries(Path(w.worktree)) if w.worktree else []
    if not entries:
        return
    console.print(f"[red]{w.worker} has uncommitted changes — not tearing down.[/red]")
    for line in entries[:DIRTY_PREVIEW_LIMIT]:
        # markup=False: porcelain paths may contain [brackets] that would
        # otherwise be parsed as style tags.
        console.print(f"  {line}", markup=False, style="yellow")
    if len(entries) > DIRTY_PREVIEW_LIMIT:
        console.print(f"  [dim]… and {len(entries) - DIRTY_PREVIEW_LIMIT} more[/dim]")
    console.print(f"\ncommit or stash the work, then re-run [bold]{invoked_as}[/bold].")
    console.print(f"to discard it instead: [bold]{invoked_as} --force[/bold]")
    raise typer.Exit(1)


def _begin_stand_down(store: FleetStore, callsign: str, force: bool, invoked_as: str) -> None:
    """Phase 1 of teardown: everything except deleting the worktree.

    ``fleet done`` runs as a subprocess of the session sitting *inside* the worktree,
    and a child cannot move its parent's cwd. Deleting the directory here would strand
    that session with a cwd that no longer exists — on macOS such a process can no
    longer spawn children at all, which silently kills the mail hook and every other
    subprocess for the rest of its life. So phase 1 stops short: the worker is marked
    ``standing-down`` and the worktree stays on disk. ``_reap`` finishes the job later,
    from a different session.
    """
    w = _load_worker(store, callsign)
    _refuse_if_dirty(w, force, invoked_as)

    w.status = "standing-down"
    w.updated = now_stamp()
    if force:
        # Remembered because the reap runs later, from a process that never saw the
        # flag; without it a worktree dirtied between the two phases would block.
        w.set_section("Teardown", "forced: uncommitted work is to be discarded on reap.")
    store.atomic_write(store.worker_path(callsign), w.render())

    console.print(
        f"[green]{callsign} standing down.[/green] "
        f"the worktree is released once you leave it "
        f"(automatically, within ~{REAP_GRACE_MINUTES} minutes)."
    )


def _reap(store: FleetStore, callsign: str, force: bool = False) -> Path | None:
    """Phase 2 of teardown: release the worktree, archive the record, free the callsign.

    Correct precisely because it never runs inside the directory it deletes — a
    *different* fleet invocation always triggers it, so stranding is structurally
    impossible rather than merely avoided. Shared by the automatic sweep and by
    ``dismiss``, so the two can never drift on what teardown means.

    Returns the archive path, or None if the worker file has already been reaped by a
    concurrent invocation.
    """
    path = store.worker_path(callsign)
    if not path.exists():
        return None
    w = Worker.parse(path.read_text(encoding="utf-8"))
    forced = force or (w.get_section("Teardown") or "").startswith("forced")

    w.status = "done"
    w.updated = now_stamp()
    w.remove_section("Teardown")

    # Fold the mail trail into the archived record.
    messages = mailbox.all_messages(store, callsign)
    if messages:
        w.set_section("Mail (archived)", "\n".join(m.render() for m in messages))

    with store.lock():
        try:
            get_provider(w.provider or "plain", store.repo_root).release(
                callsign, w.worktree or "", force=forced
            )
        except WorktreeError as e:
            console.print(f"[yellow]worktree teardown warning: {e}[/yellow]")
        # Only after the worktree is gone: git refuses to delete a checked-out branch.
        _collect_branch(store, w.branch or f"fleet/{callsign}")
        archive_path = store.archive_dir / f"{today_stamp()}-{callsign}.md"
        store.atomic_write(archive_path, w.render())
        store.worker_path(callsign).unlink(missing_ok=True)
        store.mail_path(callsign).unlink(missing_ok=True)
    return archive_path


def _collect_branch(store: FleetStore, branch: str, context: str = "deleted") -> None:
    """Clear a branch out of a callsign's way, without ever destroying commits.

    This is garbage collection, not a content git op: a branch whose every patch is
    already upstream holds nothing that deleting it could lose. Leaving it behind is
    what poisons the callsign pool — the worker file is archived and the callsign
    becomes drawable again while the branch lives on forever, so a later recruit finds
    a stale branch wearing its name.

    Unlanded work is never deleted, only renamed aside: that branch may be the only
    copy. It does not get to keep the callsign either, which is the whole point.

    Deliberately does not fetch. Teardown and recruit both call this on a path where a
    network round-trip would be an unwelcome surprise, and a misjudgement can only make
    it keep a branch it could have deleted — never the reverse.
    """
    if not worktree_mod.branch_exists(branch, store.repo_root):
        return
    base = worktree_mod.fresh_trunk(store.repo_root, fetch=False)
    try:
        if worktree_mod.has_landed(branch, base.ref, store.repo_root):
            worktree_mod.delete_branch(branch, store.repo_root)
            console.print(f"  {context} {branch} [dim](landed on {base.ref})[/dim]")
            return
        kept = worktree_mod.abandon_branch(branch, today_stamp(), store.repo_root)
        console.print(
            f"  [yellow]{branch} has work not on {base.ref}[/yellow] — kept as {kept}."
        )
    except WorktreeError as e:
        console.print(f"  [yellow]could not collect {branch}: {e}[/yellow]")


def _cwd_or_none() -> Path | None:
    """The resolved cwd, or None when it no longer exists.

    A deleted cwd is not hypothetical here — it is the state this whole feature exists
    to prevent, and a session already in it still runs fleet commands. ``Path.cwd()``
    raises there, and letting that escape would break every command for exactly the
    people worst affected.
    """
    try:
        return Path.cwd().resolve()
    except OSError:
        return None


def _contains(directory: Path, candidate: Path) -> bool:
    """Whether ``candidate`` is ``directory`` or sits beneath it."""
    resolved = directory.resolve() if directory.exists() else directory
    return resolved == candidate or resolved in candidate.parents


def _reap_expired(store: FleetStore) -> None:
    """Finish teardown for every worker that has been ``standing-down`` past the grace
    period. Called by ``_store()``, so any fleet command sweeps the ones before it.

    Deliberately silent and total: this is a side effect of an unrelated command, so it
    must neither narrate nor be able to fail one. A worker whose reap raises is left
    ``standing-down`` for the next command to retry.
    """
    here = _cwd_or_none()
    for w in status.load_workers(store):
        if w.status != "standing-down":
            continue
        age = status.age_minutes(w.updated)
        if age is None or age < REAP_GRACE_MINUTES:
            continue
        if here is not None and w.worktree and _contains(Path(w.worktree), here):
            # Never delete the directory the deleting process is standing in — that is
            # the entire bug. Subdirectories count: the cwd goes with the worktree.
            continue
        try:
            _reap(store, w.worker)
        except Exception:
            pass


@app.command()
def done(
    force: bool = typer.Option(False, "--force", help="Tear down even if the worktree has uncommitted changes (they are discarded)."),
) -> None:
    """Mark done and begin teardown. Runs NO content git ops — commit, squash-merge,
    and handoff are the worker's own responsibility and must happen before this.

    Teardown completes in two steps. This one archives nothing and deletes nothing: it
    marks the worker ``standing-down`` and leaves the worktree in place, because you
    are standing in it. Once you exit, the next fleet command run by anyone releases
    it. Refuses to run while the worktree is dirty, so you never have to check first."""
    store = _store()
    _begin_stand_down(store, _resolve_self(store), force, invoked_as="fleet done")


@app.command()
def sweep(
    delete_unlanded: bool = typer.Option(False, "--delete-unlanded", help="Also delete orphan branches whose work never landed. Destroys commits."),
    fetch: bool = typer.Option(True, "--fetch/--no-fetch", help="Fetch the remote first, so 'landed' is judged against the current trunk."),
) -> None:
    """Clear leftover ``fleet/*`` branches that no live worker holds.

    Standdown already collects a worker's branch, so this is for what escapes that
    path: crashed agents, killed sessions, and branches created before fleet collected
    them at all. Left alone they leak callsigns — the roster frees the name while the
    branch keeps it.

    Landed branches are deleted outright. Branches still holding unique work are
    renamed to ``fleet/<callsign>.abandoned-<date>``: the commits survive untouched,
    but the callsign goes back in the pool."""
    store = _store()
    base = worktree_mod.fresh_trunk(store.repo_root, fetch=fetch)
    if base.warning:
        console.print(f"[yellow]{base.warning}[/yellow]")

    held = {f"fleet/{c}" for c in store.live_callsigns()}
    orphans = [b for b in worktree_mod.fleet_branches(store.repo_root) if b not in held]
    if not orphans:
        console.print("[green]nothing to sweep[/green] — no orphan fleet branches.")
        return

    deleted, kept = [], []
    for branch in orphans:
        unlanded = worktree_mod.unlanded_commits(branch, base.ref, store.repo_root)
        try:
            if unlanded and not delete_unlanded:
                kept.append((worktree_mod.abandon_branch(branch, today_stamp(), store.repo_root), len(unlanded)))
                continue
            worktree_mod.delete_branch(branch, store.repo_root)
            deleted.append((branch, len(unlanded)))
        except WorktreeError as e:
            console.print(f"[yellow]could not sweep {branch}: {e}[/yellow]")

    for branch, lost in deleted:
        note = f" [red](discarded {lost} unlanded commit(s))[/red]" if lost else ""
        console.print(f"  [green]deleted[/green] {branch}{note}")
    for kept_name, count in kept:
        console.print(f"  [yellow]kept[/yellow]    {kept_name} — {count} commit(s) not on {base.ref}")

    console.print(f"swept {len(deleted)} branch(es), kept {len(kept)}.")
    if kept:
        console.print(
            f"  [dim]kept branches were renamed aside so their callsigns are free again.[/dim]\n"
            f"  [dim]inspect one with `git log {base.trunk}..<branch>`; merge it, or "
            "re-run with --delete-unlanded to discard.[/dim]"
        )


@app.command()
def dismiss(
    callsign: str = typer.Argument(..., help="Worker to stand down."),
    force: bool = typer.Option(False, "--force", help="Tear down even if the worktree has uncommitted changes (they are discarded)."),
) -> None:
    """Stand down a worker by callsign, from anywhere in the repo.

    Recovery for a worker nobody is inside: an agent that failed to launch, crashed on
    startup, or was abandoned. ``fleet done`` must run from within the worktree, which
    is exactly what you cannot do when no session is there. Same rules as ``done`` —
    uncommitted work blocks teardown, and any commits the branch still holds are kept.

    Tears down in one step rather than two. The delay ``done`` accepts exists to spare
    the session inside the worktree; dismiss is *for* worktrees nobody is inside, so
    waiting would only slow recovery."""
    store = _store()
    if not store.worker_path(callsign).exists():
        live = ", ".join(store.live_callsigns()) or "none"
        _fail(f"no worker '{callsign}'. live workers: {live}")
    w = _load_worker(store, callsign)
    _refuse_if_dirty(w, force, invoked_as=f"fleet dismiss {callsign}")
    archive_path = _reap(store, callsign, force=force)
    console.print(f"[green]{callsign} stood down.[/green] archived -> {archive_path}")


if __name__ == "__main__":
    app()
