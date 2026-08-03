"""The ``fleet --guide`` walkthrough.

A first-time user needs the commands *in order*, not alphabetically like ``--help``.
The steps are data rather than a formatted blob so the same source can render to the
terminal and be checked against the CLI's real command list by the tests — a step
naming a command that no longer exists is a bug we want to fail loudly.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

# Indents, in columns: each step sits inside its panel, each note under its command.
STEP_INDENT = 2
NOTE_INDENT = 2


@dataclass(frozen=True)
class Step:
    """One move in the walkthrough.

    ``command`` is shown verbatim as the thing to type; ``note`` says what just
    happened in one line. ``where`` names the directory the command is run from,
    since fleet resolves both the repo and the calling worker from the cwd.
    """

    command: str
    note: str
    where: str = ""


@dataclass(frozen=True)
class Section:
    title: str
    blurb: str
    steps: tuple[Step, ...]


SECTIONS: tuple[Section, ...] = (
    Section(
        title="1. Set up",
        blurb="Once per machine, then once per project.",
        steps=(
            Step(
                command='export FLEET_AGENT="claude"',
                note="How fleet launches your agent. Any command works; put it in your shell profile.",
            ),
            Step(
                command="fleet init",
                note="Installs the prompt pack and scaffolds this project's state. Offers to install the mail-delivery hook. Safe to re-run.",
                where="your project root",
            ),
        ),
    ),
    Section(
        title="2. Start a worker",
        blurb="Each worker gets its own callsign, branch, and isolated worktree. Claude Code sessions show the callsign in their status line, so split panes stay tellable apart.",
        steps=(
            Step(
                command="fleet recruit",
                note="Draws a free callsign at random (say delta), provisions ../wt/delta on branch fleet/delta, and launches your agent there, already enlisted via /fleet-start.",
                where="your project root",
            ),
            Step(
                command="brief the worker in your own words",
                note="The session opens enlisted and waiting. Just tell it what you want built.",
                where="the new agent session",
            ),
        ),
    ),
    Section(
        title="3. Supervise",
        blurb="Run these from the project root while workers are running.",
        steps=(
            Step(
                command="fleet watch",
                note="Live dashboard. Watch for status 'requesting-input' — that worker is blocked on you.",
            ),
            Step(
                command="fleet qm",
                note="Launches the Quartermaster, a manager agent that summarizes every worker for you.",
            ),
            Step(
                command='fleet msg alpha "prioritize the auth path"',
                note="Sends a directive, delivered when alpha next goes idle. Use 'all' to broadcast to every worker.",
            ),
            Step(
                command="fleet notify --check",
                note="Confirms mail delivery is wired up and reachable. Run it if a directive never seems to land.",
            ),
            Step(
                command="fleet ls",
                note="Quick roster: one line per worker, prints and exits. Add --porcelain to pipe it.",
            ),
            Step(
                command="fleet status",
                note="Fuller one-shot readout when you want each worker's detail.",
            ),
        ),
    ),
    Section(
        title="4. Work as a worker",
        blurb="These run from inside a worktree; the callsign is inferred, never passed.",
        steps=(
            Step(
                command='fleet sync --stage execution --status running --next "backfill SKUs"',
                note="Updates your own state so the board and the Quartermaster stay current.",
                where="../wt/alpha",
            ),
            Step(
                command="fleet inbox",
                note="Drains queued directives by hand. Usually unnecessary — mail is delivered to you when you go idle.",
                where="../wt/alpha",
            ),
        ),
    ),
    Section(
        title="5. Finish",
        blurb="You commit and merge your own branch — fleet never runs content git ops.",
        steps=(
            Step(
                command="fleet done",
                note="Marks you standing-down. Refuses while anything is uncommitted, so you never have to check first. Your worktree is released once you exit it — the next fleet command anyone runs finishes the job.",
                where="../wt/alpha",
            ),
            Step(
                command="fleet done --force",
                note="Only when you mean to throw the uncommitted work away.",
                where="../wt/alpha",
            ),
            Step(
                command="fleet dismiss alpha",
                note="Stands a worker down from outside, for when its agent never started or has gone away.",
                where="your project root",
            ),
        ),
    ),
)

CLOSING = (
    "State lives outside your repo, so every worktree shares one view:\n"
    "  ~/.claude/projects/<project-slug>/fleet/   (follows CLAUDE_CONFIG_DIR;"
    " FLEET_STATE_HOME overrides)\n\n"
    "Worktrees are created next to your repo, at ../wt/<callsign>.\n"
    "Run [bold]fleet --help[/bold] for the full command reference."
)


def _render_step(step: Step) -> Padding:
    """One step, indented as a block so wrapped note lines keep their hanging indent
    instead of running back to the panel edge."""
    command = Text(f"$ {step.command}", style="bold cyan")
    if step.where:
        command.append(f"    ({step.where})", style="dim")
    note = Padding(Text(step.note, style="dim"), (0, 0, 0, NOTE_INDENT))
    return Padding(Group(command, note), (0, 0, 0, STEP_INDENT))


def render() -> Group:
    """The whole walkthrough, ready to print."""
    blocks: list = [
        Text.from_markup(
            "\n[bold]fleet[/bold] — a walkthrough, in the order you'd run things.\n"
        )
    ]
    for section in SECTIONS:
        body: list = [Text(section.blurb, style="italic dim"), Text()]
        for index, step in enumerate(section.steps):
            body.append(_render_step(step))
            if index != len(section.steps) - 1:
                body.append(Text())
        blocks.append(
            Panel(Group(*body), title=section.title, title_align="left", border_style="cyan")
        )
    blocks.append(Text.from_markup(f"\n{CLOSING}\n"))
    return Group(*blocks)


def print_guide(console: Console) -> None:
    console.print(render())
