"""Shared fixtures.

Every test runs against a throwaway git repo with ``FLEET_STATE_HOME`` redirected into
``tmp_path``, so no test can read or write the developer's real fleet state. The
``fleet`` fixture invokes the CLI in-process through typer's runner, which keeps the
suite fast and gives us the exit code and output directly.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fleet.cli import app
from fleet.store import FleetStore

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fleet Test",
    "GIT_AUTHOR_EMAIL": "test@fleet.invalid",
    "GIT_COMMITTER_NAME": "Fleet Test",
    "GIT_COMMITTER_EMAIL": "test@fleet.invalid",
}


def git(*args: str, cwd: Path) -> str:
    """Run git, raising with captured stderr so failures are debuggable."""
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env={**GIT_ENV, "PATH": "/usr/bin:/bin:/usr/local/bin"}
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


@dataclass
class Result:
    """A CLI invocation's outcome."""

    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point fleet's state dir and the prompt-install dir inside tmp_path.

    Autouse and first: no test may touch the real ~/.claude-work or ~/.claude.
    """
    state_home = tmp_path / "state"
    monkeypatch.setenv("FLEET_STATE_HOME", str(state_home))
    monkeypatch.delenv("FLEET_AGENT", raising=False)
    # init resolves its install dir from CLAUDE_CONFIG_DIR; redirect it so the suite
    # never writes prompts into the developer's real ~/.claude or ~/.claude-work.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    return state_home


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with one commit, so worktrees have a base to branch from."""
    root = tmp_path / "demo"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "initial", cwd=root)
    return root


@pytest.fixture
def fleet(monkeypatch: pytest.MonkeyPatch):
    """Invoke the fleet CLI from a given directory, in-process.

    fleet resolves both the repo and the calling worker from the process cwd, so the
    directory a command runs from is part of its input and every call takes it
    explicitly.
    """
    runner = CliRunner()

    def run(*args: str, cwd: Path) -> Result:
        monkeypatch.chdir(cwd)
        outcome = runner.invoke(app, list(args))
        if outcome.exception is not None and not isinstance(outcome.exception, SystemExit):
            raise outcome.exception
        return Result(outcome.exit_code, outcome.output)

    return run


@pytest.fixture
def store(repo: Path) -> FleetStore:
    return FleetStore(cwd=repo)


@pytest.fixture
def worktree_of(repo: Path):
    """Path to a callsign's worktree. Worktrees are siblings of the repo."""

    def path_for(callsign: str) -> Path:
        return repo.parent / "wt" / callsign

    return path_for
