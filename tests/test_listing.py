"""``fleet ls`` — the compact roster.

``status`` renders a wide supervisory table and ``watch`` keeps it live; this is the
short answer to "who is out there", cheap enough to run beside other commands. The
``--porcelain`` form is a parsing contract for shell pipelines and the Quartermaster,
so its shape is pinned rather than left to drift with the pretty output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PORCELAIN_FIELDS = 4


@pytest.fixture
def fleet_of_two(repo: Path, fleet, worktree_of):
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    fleet("recruit", cwd=repo)
    fleet("sync", "--thread", "auth refactor", "--stage", "execution",
          "--status", "running", cwd=worktree_of("alpha"))
    return repo


# --------------------------------------------------------------------------
# the human-facing listing
# --------------------------------------------------------------------------

def test_lists_every_worker(fleet_of_two: Path, fleet):
    result = fleet("ls", cwd=fleet_of_two)

    assert result.ok, result.output
    assert "alpha" in result.output
    assert "bravo" in result.output


def test_shows_status_and_thread(fleet_of_two: Path, fleet):
    result = fleet("ls", cwd=fleet_of_two)

    assert "running" in result.output
    assert "auth refactor" in result.output


def test_one_line_per_worker(fleet_of_two: Path, fleet):
    """Compactness is the point; a wrapping table would defeat it."""
    result = fleet("ls", cwd=fleet_of_two)

    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 2


def test_reports_an_empty_fleet(repo: Path, fleet):
    fleet("init", cwd=repo)

    result = fleet("ls", cwd=repo)

    assert result.ok
    assert "no workers" in result.output


def test_flags_unread_mail(fleet_of_two: Path, fleet):
    fleet("msg", "alpha", "check the token path", cwd=fleet_of_two)

    result = fleet("ls", cwd=fleet_of_two)

    assert "1 unread" in result.output


def test_list_is_an_alias_for_ls(fleet_of_two: Path, fleet):
    assert fleet("list", cwd=fleet_of_two).output == fleet("ls", cwd=fleet_of_two).output


def test_requires_a_git_repo(tmp_path: Path, fleet):
    bare = tmp_path / "bare"
    bare.mkdir()

    result = fleet("ls", cwd=bare)

    assert result.exit_code == 1


# --------------------------------------------------------------------------
# the porcelain contract
# --------------------------------------------------------------------------

def test_porcelain_is_tab_separated_with_fixed_fields(fleet_of_two: Path, fleet):
    result = fleet("ls", "--porcelain", cwd=fleet_of_two)

    for line in result.output.strip().splitlines():
        assert len(line.split("\t")) == PORCELAIN_FIELDS


def test_porcelain_column_order_is_stable(fleet_of_two: Path, fleet):
    """callsign, status, stage, thread — parsers index these positionally."""
    result = fleet("ls", "--porcelain", cwd=fleet_of_two)

    alpha = next(l for l in result.output.splitlines() if l.startswith("alpha"))
    callsign, status, stage, thread = alpha.split("\t")
    assert (callsign, status, stage, thread) == ("alpha", "running", "execution", "auth refactor")


def test_porcelain_fills_empty_fields_with_a_placeholder(fleet_of_two: Path, fleet):
    """A recruited worker has no stage or thread; the field count must not change."""
    result = fleet("ls", "--porcelain", cwd=fleet_of_two)

    bravo = next(l for l in result.output.splitlines() if l.startswith("bravo"))
    assert bravo.split("\t") == ["bravo", "recruited", "-", "-"]


def test_porcelain_is_unstyled(fleet_of_two: Path, fleet):
    """Style escapes would corrupt a pipeline."""
    result = fleet("ls", "--porcelain", cwd=fleet_of_two)

    assert "\x1b[" not in result.output


def test_porcelain_of_an_empty_fleet_is_empty(repo: Path, fleet):
    """No header, no placeholder row: `if [ -z "$(fleet ls --porcelain)" ]` must work."""
    fleet("init", cwd=repo)

    result = fleet("ls", "--porcelain", cwd=repo)

    assert result.ok
    assert result.output.strip() == ""
