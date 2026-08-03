"""The callsign badge installed into each worker's worktree.

Two properties carry the feature. The badge must be scoped to the worktree, because
writing a status line into the user's own Claude Code settings would follow them into
every unrelated project. And it must not read as uncommitted work: ``fleet done``
refuses to tear down a dirty worktree, so an unexcluded badge would strand every
worker behind ``--force``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from fleet import launch, statusline
from fleet.statusline import BADGE_PATH, CALLSIGN_ENV_VAR, SETTINGS_PATH

from conftest import git


def run_badge(worktree: Path, env: dict[str, str] | None = None) -> str:
    """The badge as Claude Code would run it: from the worktree, with fleet's env."""
    result = subprocess.run(
        [str(worktree / BADGE_PATH)],
        cwd=worktree, capture_output=True, text=True, env=env,
    )
    return result.stdout


@pytest.fixture
def worker(repo: Path, fleet, worktree_of, store) -> Path:
    """One recruited worker's worktree, with a badge already installed."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    (callsign,) = store.live_callsigns()
    return worktree_of(callsign)


# --------------------------------------------------------------------------
# what gets installed
# --------------------------------------------------------------------------

def test_recruit_installs_a_status_line(worker: Path):
    settings = json.loads((worker / SETTINGS_PATH).read_text(encoding="utf-8"))

    assert settings["statusLine"]["type"] == "command"
    assert settings["statusLine"]["command"] == str(worker / BADGE_PATH)


def test_badge_is_executable(worker: Path):
    """Claude Code runs it as a command, not through a shell."""
    assert (worker / BADGE_PATH).stat().st_mode & 0o111


def test_badge_prefers_the_exported_callsign(worker: Path):
    """$FLEET_CALLSIGN is authoritative — a provider whose worktree is not named after
    the callsign must not mislead — but the directory name is a usable fallback for a
    resumed shell that never got the export."""
    exported = run_badge(worker, env={CALLSIGN_ENV_VAR: "romeo", "PATH": "/usr/bin:/bin"})
    assert "romeo" in exported
    assert worker.name not in exported

    assert worker.name in run_badge(worker, env={"PATH": "/usr/bin:/bin"})


# --------------------------------------------------------------------------
# it must not look like work
# --------------------------------------------------------------------------

def test_badge_leaves_the_worktree_clean(worker: Path):
    """The whole reason for the git-exclude: teardown refuses on a dirty worktree."""
    assert git("status", "--short", cwd=worker) == ""


def test_worker_can_be_torn_down(worker: Path, stand_down):
    """The failure this guards against: every worker stranded behind --force."""
    result = stand_down(worker)

    assert result.ok, result.output
    assert not worker.exists()


def test_real_work_still_blocks_teardown(worker: Path, fleet):
    """The exclusion must cover the badge only, not silence dirty-checking."""
    (worker / "notes.txt").write_text("scratch\n", encoding="utf-8")

    result = fleet("done", cwd=worker)

    assert result.exit_code == 1
    assert "notes.txt" in result.output


# --------------------------------------------------------------------------
# scoping — nothing outside the worktree may change
# --------------------------------------------------------------------------

def test_exclusion_is_scoped_to_the_worktree(worker: Path, repo: Path):
    """A shared excludesFile would follow the user into their own checkout."""
    in_main_repo = subprocess.run(
        ["git", "config", "--get", "core.excludesFile"],
        cwd=repo, capture_output=True, text=True,
    )

    assert in_main_repo.stdout.strip() == ""


def test_main_repo_stays_clean(worker: Path, repo: Path):
    """Recruiting must not leave anything to commit in the user's own checkout."""
    assert git("status", "--short", cwd=repo) == ""


# --------------------------------------------------------------------------
# how the callsign reaches the session
# --------------------------------------------------------------------------

def exec_env(worktree: Path, **kwargs) -> dict[str, str]:
    """The environment `launch` would exec the agent with.

    `launch` replaces the process, so the exec is stubbed; the env it assembles is
    the only channel through which the callsign reaches the agent.
    """
    with mock.patch.object(launch.os, "execvpe") as execvpe:
        launch.launch(worktree, "claude", **kwargs)
    _file, _argv, env = execvpe.call_args[0]
    return env


def test_launch_exports_the_callsign(tmp_path: Path):
    assert exec_env(tmp_path, callsign="delta")[CALLSIGN_ENV_VAR] == "delta"


def test_launch_without_a_callsign_exports_nothing(tmp_path: Path):
    """`qm` is not a worker and has no callsign to claim."""
    assert CALLSIGN_ENV_VAR not in exec_env(tmp_path)


def test_launch_preserves_the_surrounding_environment(tmp_path: Path):
    """The agent still needs PATH, HOME, and the user's own configuration."""
    assert "PATH" in exec_env(tmp_path, callsign="delta")


def test_install_survives_an_unwritable_worktree(tmp_path: Path):
    """A badge is a convenience; failing to write one must not fail a recruit."""
    unwritable = tmp_path / "readonly"
    unwritable.mkdir(mode=0o500)

    statusline.install(unwritable, "delta")  # must not raise

    assert not (unwritable / SETTINGS_PATH).exists()
