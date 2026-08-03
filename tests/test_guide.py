"""``fleet --guide``.

Beyond rendering, the tests pin two things that rot silently: that the walkthrough
only ever names commands the CLI actually has, and that reading it never requires a
repo or any prior setup — the reader is by definition someone who has not run
``fleet init`` yet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fleet import guide

# A step's command line starts with the binary, e.g. "fleet recruit --provider plain".
_FLEET_COMMAND = re.compile(r"^fleet ([a-z-]+)")


def cli_command_names() -> set[str]:
    """Command names typer actually registers, read from the app itself."""
    from fleet.cli import app

    return {command.name or command.callback.__name__ for command in app.registered_commands}


def guide_command_names() -> set[str]:
    """Command names the walkthrough tells the reader to run."""
    names = set()
    for section in guide.SECTIONS:
        for step in section.steps:
            match = _FLEET_COMMAND.match(step.command)
            if match:
                names.add(match.group(1))
    return names


# --------------------------------------------------------------------------
# the guide must describe the CLI that exists
# --------------------------------------------------------------------------

def test_every_command_in_the_guide_exists():
    """A step naming a removed or renamed command is a broken instruction."""
    unknown = guide_command_names() - cli_command_names()

    assert not unknown, f"guide references commands the CLI lacks: {sorted(unknown)}"


def test_the_guide_covers_the_core_workflow():
    """The path a first-timer has to walk; a gap here strands them."""
    covered = guide_command_names()

    assert {"init", "recruit", "sync", "done"} <= covered


def test_guide_mentions_the_dirty_refusal():
    """The one surprising behavior in the lifecycle, so it belongs in the walkthrough."""
    notes = " ".join(step.note for section in guide.SECTIONS for step in section.steps)

    assert "uncommitted" in notes


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def test_guide_renders_every_section_and_step(fleet, tmp_path: Path):
    result = fleet("--guide", cwd=tmp_path)

    assert result.ok
    for section in guide.SECTIONS:
        assert section.title in result.output
        for step in section.steps:
            # The command's first token survives wrapping even in a narrow terminal.
            assert step.command.split()[0] in result.output


def test_guide_needs_no_repo_and_no_state(fleet, tmp_path: Path):
    """Read before setup: no git repo, no `fleet init`, still works."""
    bare = tmp_path / "not-a-repo"
    bare.mkdir()

    result = fleet("--guide", cwd=bare)

    assert result.ok
    assert "walkthrough" in result.output


def test_guide_exits_before_running_a_command(fleet, tmp_path: Path):
    """--guide is eager, so it must win even when a subcommand is also given."""
    result = fleet("--guide", "status", cwd=tmp_path)

    assert result.ok
    assert "walkthrough" in result.output
    # `status` outside a repo would otherwise fail with the git-repo error.
    assert "must be run inside a git repository" not in result.output


# --------------------------------------------------------------------------
# the flag itself
# --------------------------------------------------------------------------

def test_help_advertises_the_guide(fleet, tmp_path: Path):
    """Discoverability: --help is where people look first."""
    result = fleet("--help", cwd=tmp_path)

    assert "--guide" in result.output


def test_bare_invocation_still_shows_help(fleet, tmp_path: Path):
    """Adding a callback must not turn a bare `fleet` into a no-op."""
    result = fleet(cwd=tmp_path)

    assert "Usage" in result.output


@pytest.mark.parametrize("command", ["init", "status"])
def test_existing_commands_still_route(command: str, repo: Path, fleet):
    """The callback sits in front of every command; make sure it passes them through."""
    result = fleet(command, cwd=repo)

    assert result.ok, result.output
