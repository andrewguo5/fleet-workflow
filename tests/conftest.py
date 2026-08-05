"""Shared fixtures.

Every test runs against a throwaway git repo with ``FLEET_STATE_HOME`` redirected into
``tmp_path``, so no test can read or write the developer's real fleet state. The
``fleet`` fixture invokes the CLI in-process through typer's runner, which keeps the
suite fast and gives us the exit code and output directly.
"""

from __future__ import annotations

import random
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fleet import callsign as callsign_mod
from fleet.cli import app
from fleet.store import FleetStore
from fleet.worker import Worker

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fleet Test",
    "GIT_AUTHOR_EMAIL": "test@fleet.invalid",
    "GIT_COMMITTER_NAME": "Fleet Test",
    "GIT_COMMITTER_EMAIL": "test@fleet.invalid",
}

# Any fixed value works; this one is arbitrary. What matters is that allocation
# is reseeded per test so the suite never depends on run-to-run luck.
CALLSIGN_SEED = 20260731


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
    # Set in every worker's shell, so the suite fails when run from inside a fleet
    # worktree unless it is scrubbed — which is exactly where fleet gets developed.
    monkeypatch.delenv("FLEET_CALLSIGN", raising=False)
    # init resolves its install dir from CLAUDE_CONFIG_DIR; redirect it so the suite
    # never writes prompts into the developer's real ~/.claude or ~/.claude-work.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    return state_home


@pytest.fixture(autouse=True)
def predictable_callsigns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make random callsign allocation repeatable within a test.

    Production draws callsigns at random so a fleet does not always open with the
    same two workers. Tests still need to name the worker they just recruited, so
    the global RNG is reseeded to a fixed value: allocation stays exercised for
    real, but the sequence is identical on every run. Use `recruited()` to learn
    which callsign a recruit produced rather than hardcoding one.
    """
    monkeypatch.setattr(callsign_mod, "random", random.Random(CALLSIGN_SEED))


def occupy_callsigns(store, names) -> None:
    """Fill roster slots with stub worker files, no worktree provisioning.

    Cheaper than recruiting for real, and it lets a test control which callsigns
    remain free — the only way to steer a random allocator toward a chosen name.
    """
    store.ensure_dirs()
    for name in names:
        store.worker_path(name).write_text(
            f"---\nworker: {name}\nstatus: running\n---\n", encoding="utf-8"
        )


def age_past_grace(store, callsign: str) -> None:
    """Backdate a worker's heartbeat so the next fleet command reaps it.

    ``fleet done`` only marks a worker ``standing-down``; the worktree is released by
    whatever command runs next, once the worker has been in that state longer than
    ``REAP_GRACE_MINUTES``. Tests that care about the finished state rewind the clock
    rather than sleep through it.
    """
    from fleet.cli import REAP_GRACE_MINUTES

    path = store.worker_path(callsign)
    w = Worker.parse(path.read_text(encoding="utf-8"))
    stale = datetime.now() - timedelta(minutes=REAP_GRACE_MINUTES + 1)
    w.updated = stale.strftime("%Y-%m-%dT%H:%M")
    path.write_text(w.render(), encoding="utf-8")


@pytest.fixture
def stand_down(fleet, store):
    """Run a worker's full two-phase teardown, from outside its worktree.

    The common case for tests that only care about the end state: `fleet done` in the
    worktree, then age the clock and trigger the reap from the repo root — which is
    also what really happens, since the reaping command runs in a different directory.
    """

    def run(worktree: Path, *args: str):
        result = fleet("done", *args, cwd=worktree)
        if result.ok:
            age_past_grace(store, worktree.name)
            fleet("status", cwd=store.repo_root)
        return result

    return run


@pytest.fixture
def live_callsigns(store):
    """The callsigns currently live, alphabetically — who the recruits actually got.

    Not recruit order: the store lists workers by filename. A test that recruits
    one worker can take `live_callsigns()[0]`; one that recruits several and
    cares which came first should capture each callsign as it goes.
    """

    def live() -> list[str]:
        return store.live_callsigns()

    return live


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
def recruited(repo: Path, fleet, worktree_of, store):
    """A repo with one recruited worker, and the path to its worktree.

    The callsign is drawn at random, so it is read back from the store rather
    than assumed.
    """
    fleet("init", cwd=repo)
    result = fleet("recruit", cwd=repo)
    assert result.ok, result.output
    (callsign,) = store.live_callsigns()
    return worktree_of(callsign)


@pytest.fixture
def cloned_repo(tmp_path: Path) -> Path:
    """A clone with a real ``origin``, so freshness can actually be tested.

    The staleness this guards against only exists where there is a remote to fall
    behind, so the plain `repo` fixture cannot express it. Returns the clone; use
    `advance_origin` to move the remote ahead of it.
    """
    upstream = tmp_path / "upstream.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    (seed / "README.md").write_text("# demo\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "initial", cwd=seed)
    git("init", "-q", "--bare", "-b", "main", str(upstream), cwd=tmp_path)
    git("remote", "add", "origin", str(upstream), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)

    clone = tmp_path / "clone"
    git("clone", "-q", str(upstream), str(clone), cwd=tmp_path)
    return clone


@pytest.fixture
def advance_origin(tmp_path: Path):
    """Push a new commit to the remote without touching the local clone.

    This is precisely the stale state fleet must survive: `origin/main` has moved and
    the local `main` has never heard about it.
    """

    def push(message: str = "upstream work") -> str:
        seed = tmp_path / "seed"
        (seed / f"{message.replace(' ', '_')}.txt").write_text(message, encoding="utf-8")
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", message, cwd=seed)
        git("push", "-q", "origin", "main", cwd=seed)
        return git("rev-parse", "HEAD", cwd=seed)

    return push


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
def store(request, repo: Path) -> FleetStore:
    """The fleet state for the repo under test.

    Freshness tests work in `cloned_repo` rather than `repo`, and fleet keys its state
    off the repo root — so a store pointed at the wrong one would report an empty
    roster and quietly assert nothing.
    """
    if "cloned_repo" in request.fixturenames:
        return FleetStore(cwd=request.getfixturevalue("cloned_repo"))
    return FleetStore(cwd=repo)


@pytest.fixture
def worktree_of(repo: Path):
    """Path to a callsign's worktree. Worktrees are siblings of the repo."""

    def path_for(callsign: str) -> Path:
        return repo.parent / "wt" / callsign

    return path_for
