"""Failed launches, and recovering the workers they strand.

``launch`` execs, replacing the fleet process, so a failure after that point cannot be
caught or reported — the shell exits and fleet is already gone. Two consequences are
pinned here: ``recruit`` must resolve the agent command *before* provisioning anything,
and a worker that ends up stranded anyway must be removable from outside its worktree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet.launch import can_launch

from conftest import git

UNRESOLVABLE = "definitely-not-a-real-agent-command"


@pytest.fixture
def initialized(repo: Path, fleet) -> Path:
    fleet("init", cwd=repo)
    return repo


# --------------------------------------------------------------------------
# resolving the agent command
# --------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["echo", "ls", "git"])
def test_real_commands_resolve(command: str):
    assert can_launch(command)


@pytest.mark.parametrize("command", [UNRESOLVABLE, "", "   "])
def test_unresolvable_commands_are_rejected(command: str):
    assert not can_launch(command)


def test_resolution_checks_only_the_binary_not_its_arguments():
    """`--agent "claude --resume"` is legitimate; only the first token is a command."""
    assert can_launch("echo --some-flag value")


# --------------------------------------------------------------------------
# prevention: nothing is provisioned when the agent cannot start
# --------------------------------------------------------------------------

def test_recruit_fails_before_provisioning(initialized: Path, fleet):
    result = fleet("recruit", "--agent", UNRESOLVABLE, cwd=initialized)

    assert result.exit_code == 1
    assert "not found" in result.output


def test_failed_recruit_leaves_no_worktree(initialized: Path, fleet):
    fleet("recruit", "--agent", UNRESOLVABLE, cwd=initialized)

    assert not (initialized.parent / "wt").exists()


def test_failed_recruit_leaves_no_worker(initialized: Path, fleet, store):
    fleet("recruit", "--agent", UNRESOLVABLE, cwd=initialized)

    assert store.live_callsigns() == []


def test_failed_recruit_leaves_no_branch(initialized: Path, fleet):
    fleet("recruit", "--agent", UNRESOLVABLE, cwd=initialized)

    assert git("branch", "--list", "fleet/*", cwd=initialized) == ""


def test_failed_recruit_does_not_consume_a_callsign(initialized: Path, fleet):
    """The next real recruit should still get alpha, not bravo."""
    fleet("recruit", "--agent", UNRESOLVABLE, cwd=initialized)

    result = fleet("recruit", cwd=initialized)

    assert "alpha" in result.output


def test_recruit_without_an_agent_still_works(initialized: Path, fleet, worktree_of):
    """No --agent and no $FLEET_AGENT means print a cd hint; nothing to resolve."""
    result = fleet("recruit", cwd=initialized)

    assert result.ok, result.output
    assert worktree_of("alpha").is_dir()


def test_qm_reports_an_unresolvable_agent(initialized: Path, fleet):
    """qm provisions nothing, but exec would still exit silently."""
    result = fleet("qm", "--agent", UNRESOLVABLE, cwd=initialized)

    assert result.exit_code == 1
    assert "not found" in result.output


# --------------------------------------------------------------------------
# recovery: standing down a worker nobody is inside
# --------------------------------------------------------------------------

@pytest.fixture
def stranded(initialized: Path, fleet, worktree_of):
    """A recruited worker with no agent in it — the state a failed launch leaves."""
    fleet("recruit", cwd=initialized)
    return worktree_of("alpha")


def test_dismiss_removes_the_worktree(stranded: Path, fleet, initialized: Path):
    result = fleet("dismiss", "alpha", cwd=initialized)

    assert result.ok, result.output
    assert not stranded.exists()


def test_dismiss_works_from_the_repo_root(stranded: Path, fleet, initialized: Path, store):
    """The whole point: `done` needs the worktree, `dismiss` does not."""
    fleet("dismiss", "alpha", cwd=initialized)

    assert store.live_callsigns() == []


def test_dismiss_archives_the_record(stranded: Path, fleet, initialized: Path, store):
    fleet("dismiss", "alpha", cwd=initialized)

    archived = list(store.archive_dir.glob("*-alpha.md"))
    assert len(archived) == 1
    assert "status: done" in archived[0].read_text(encoding="utf-8")


def test_dismiss_preserves_the_branch(stranded: Path, fleet, initialized: Path):
    fleet("dismiss", "alpha", cwd=initialized)

    assert "fleet/alpha" in git("branch", "--list", "fleet/*", cwd=initialized)


def test_dismiss_frees_the_callsign(stranded: Path, fleet, initialized: Path):
    fleet("dismiss", "alpha", cwd=initialized)

    assert "alpha" in fleet("recruit", cwd=initialized).output


def test_dismiss_refuses_a_dirty_worktree(stranded: Path, fleet, initialized: Path):
    """Recovery must not become a way to lose work by accident."""
    (stranded / "scratch.txt").write_text("notes\n", encoding="utf-8")

    result = fleet("dismiss", "alpha", cwd=initialized)

    assert result.exit_code == 1
    assert "scratch.txt" in result.output
    assert stranded.exists()


def test_dismiss_refusal_names_itself_in_the_retry_hint(stranded: Path, fleet, initialized: Path):
    """The advice has to be the command you actually ran, not `fleet done`."""
    (stranded / "scratch.txt").write_text("notes\n", encoding="utf-8")

    result = fleet("dismiss", "alpha", cwd=initialized)

    assert "fleet dismiss alpha" in result.output


def test_dismiss_force_discards(stranded: Path, fleet, initialized: Path):
    (stranded / "scratch.txt").write_text("notes\n", encoding="utf-8")

    result = fleet("dismiss", "alpha", "--force", cwd=initialized)

    assert result.ok, result.output
    assert not stranded.exists()


def test_dismiss_unknown_callsign_lists_live_workers(stranded: Path, fleet, initialized: Path):
    result = fleet("dismiss", "zulu", cwd=initialized)

    assert result.exit_code == 1
    assert "no worker 'zulu'" in result.output
    assert "alpha" in result.output


def test_dismiss_survives_a_missing_worktree(stranded: Path, fleet, initialized: Path, store):
    """A worktree deleted by hand must not wedge the worker file forever."""
    git("worktree", "remove", "--force", str(stranded), cwd=initialized)

    result = fleet("dismiss", "alpha", cwd=initialized)

    assert result.ok, result.output
    assert store.live_callsigns() == []


def test_done_and_dismiss_produce_the_same_archive(initialized: Path, fleet, worktree_of, store):
    """Both routes share one teardown, so the resulting record must match."""
    fleet("recruit", cwd=initialized)
    git("commit", "-q", "--allow-empty", "-m", "work", cwd=worktree_of("alpha"))
    fleet("done", cwd=worktree_of("alpha"))
    via_done = next(store.archive_dir.glob("*-alpha.md")).read_text(encoding="utf-8")

    for path in store.archive_dir.glob("*-alpha.md"):
        path.unlink()
    fleet("recruit", cwd=initialized)
    fleet("dismiss", "alpha", cwd=initialized)
    via_dismiss = next(store.archive_dir.glob("*-alpha.md")).read_text(encoding="utf-8")

    assert "status: done" in via_done
    assert "status: done" in via_dismiss
