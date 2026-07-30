"""Callsign allocation and worktree provisioning.

The load-bearing claim is that ``fleet recruit`` is race-safe: two agents recruiting at
the same instant must never receive the same callsign, because a collision would put
two workers in one worktree. The lock is ``fcntl``-based and therefore cross-process,
so the concurrency test spawns real subprocesses — threads would share a file table and
prove nothing.
"""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from fleet.callsign import NATO_ALPHABET, FleetFullError, next_available

from conftest import GIT_ENV, git

CONCURRENT_RECRUITS = 8


# --------------------------------------------------------------------------
# pure allocation
# --------------------------------------------------------------------------

def test_first_callsign_is_alpha():
    assert next_available([]) == "alpha"


def test_skips_taken_callsigns():
    assert next_available(["alpha", "bravo"]) == "charlie"


def test_fills_the_lowest_gap():
    """Freed callsigns are reused, so a retired worker's name comes back first."""
    assert next_available(["alpha", "charlie"]) == "bravo"


def test_is_case_insensitive():
    assert next_available(["ALPHA"]) == "bravo"


def test_raises_when_every_callsign_is_taken():
    with pytest.raises(FleetFullError):
        next_available(NATO_ALPHABET)


# --------------------------------------------------------------------------
# provisioning
# --------------------------------------------------------------------------

def test_recruit_creates_worktree_and_branch(repo: Path, fleet, worktree_of, store):
    fleet("init", cwd=repo)

    result = fleet("recruit", cwd=repo)

    assert result.ok, result.output
    assert worktree_of("alpha").is_dir()
    assert "fleet/alpha" in git("branch", "--list", "fleet/*", cwd=repo)
    assert store.worker_path("alpha").exists()


def test_recruit_records_provenance(repo: Path, fleet, store, worktree_of):
    """The worker file must describe its own worktree, or `done` can't find it."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)

    content = store.worker_path("alpha").read_text(encoding="utf-8")

    assert "worker: alpha" in content
    assert "branch: fleet/alpha" in content
    assert "status: recruited" in content
    assert str(worktree_of("alpha")) in content


def test_sequential_recruits_get_distinct_callsigns(repo: Path, fleet):
    fleet("init", cwd=repo)

    first = fleet("recruit", cwd=repo)
    second = fleet("recruit", cwd=repo)

    assert "alpha" in first.output
    assert "bravo" in second.output


def test_recruit_reattaches_to_an_existing_branch(repo: Path, fleet, worktree_of):
    """Resume: a callsign whose branch survived teardown keeps its commits."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    worktree = worktree_of("alpha")
    (worktree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A", cwd=worktree)
    git("commit", "-q", "-m", "alpha work", cwd=worktree)
    fleet("done", cwd=worktree)

    fleet("recruit", cwd=repo)

    assert (worktree / "feature.py").exists()
    assert "alpha work" in git("log", "--oneline", cwd=worktree)


def test_unknown_provider_is_rejected(repo: Path, fleet):
    fleet("init", cwd=repo)

    result = fleet("recruit", "--provider", "bogus", cwd=repo)

    assert result.exit_code == 1
    assert "unknown worktree provider" in result.output


def test_recruit_refuses_when_fleet_is_full(repo: Path, fleet, store):
    """Synthesize a full roster rather than provisioning 26 real worktrees."""
    fleet("init", cwd=repo)
    store.ensure_dirs()
    for name in NATO_ALPHABET:
        store.worker_path(name).write_text(
            f"---\nworker: {name}\nstatus: running\n---\n", encoding="utf-8"
        )

    result = fleet("recruit", cwd=repo)

    assert result.exit_code == 1
    assert "26 callsigns" in result.output


# --------------------------------------------------------------------------
# concurrency
# --------------------------------------------------------------------------

def _recruit_in_subprocess(repo: Path, state_home: Path) -> subprocess.CompletedProcess:
    """One `fleet recruit`, in its own process, sharing the repo and state dir."""
    return subprocess.run(
        [sys.executable, "-m", "fleet.cli", "recruit"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={
            **GIT_ENV,
            "FLEET_STATE_HOME": str(state_home),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
            "HOME": str(repo.parent / "fakehome"),
        },
    )


def test_concurrent_recruits_never_collide(repo: Path, fleet, isolated_state: Path):
    """The core race: N simultaneous recruits, N distinct callsigns, N worktrees.

    A collision here would hand two agents the same worktree, so this is the one
    invariant worth spawning real processes to prove.
    """
    fleet("init", cwd=repo)

    with ThreadPoolExecutor(max_workers=CONCURRENT_RECRUITS) as pool:
        runs = list(
            pool.map(
                lambda _: _recruit_in_subprocess(repo, isolated_state),
                range(CONCURRENT_RECRUITS),
            )
        )

    for run in runs:
        assert run.returncode == 0, run.stderr

    worktrees = sorted(p.name for p in (repo.parent / "wt").iterdir() if p.is_dir())
    assert len(worktrees) == CONCURRENT_RECRUITS
    assert len(set(worktrees)) == CONCURRENT_RECRUITS
    assert worktrees == sorted(NATO_ALPHABET[:CONCURRENT_RECRUITS])


def test_concurrent_recruits_write_one_file_each(repo: Path, fleet, isolated_state: Path, store):
    """Allocation and the worker file must stay in step — no orphans, no overwrites."""
    fleet("init", cwd=repo)

    with ThreadPoolExecutor(max_workers=CONCURRENT_RECRUITS) as pool:
        list(
            pool.map(
                lambda _: _recruit_in_subprocess(repo, isolated_state),
                range(CONCURRENT_RECRUITS),
            )
        )

    assert len(store.live_callsigns()) == CONCURRENT_RECRUITS
    assert len(set(store.live_callsigns())) == CONCURRENT_RECRUITS
