"""Callsign allocation and worktree provisioning.

The load-bearing claim is that ``fleet recruit`` is race-safe: two agents recruiting at
the same instant must never receive the same callsign, because a collision would put
two workers in one worktree. The lock is ``fcntl``-based and therefore cross-process,
so the concurrency test spawns real subprocesses — threads would share a file table and
prove nothing.
"""

from __future__ import annotations

import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from fleet import launch as launch_mod
from fleet.callsign import NATO_ALPHABET, FleetFullError, pick_available
from fleet.worker import today_stamp

from conftest import GIT_ENV, git, occupy_callsigns

CONCURRENT_RECRUITS = 8


def only_callsign(store) -> str:
    """The callsign of the single recruited worker.

    Callsigns are drawn at random, so a test that recruits one worker has to ask
    who it got rather than assume `alpha`.
    """
    live = store.live_callsigns()
    assert len(live) == 1, f"expected exactly one worker, got {live}"
    return live[0]


# --------------------------------------------------------------------------
# pure allocation
# --------------------------------------------------------------------------

def test_picks_a_nato_callsign():
    assert pick_available([]) in NATO_ALPHABET


def test_never_picks_a_taken_callsign():
    """The one safety property: a name in use must never be handed out again.

    Unseeded on purpose — a collision here would hand two agents one worktree, so
    it is worth checking against real draws rather than a fixed stream.
    """
    taken = ["alpha", "bravo", "charlie"]
    unseeded = random.SystemRandom()

    for _ in range(200):
        assert pick_available(taken, rng=unseeded) not in taken


def test_picks_the_last_free_callsign():
    """With exactly one name left, randomness has no room to get it wrong."""
    all_but_zulu = [c for c in NATO_ALPHABET if c != "zulu"]

    assert pick_available(all_but_zulu) == "zulu"


def test_reuses_a_freed_callsign():
    """Retiring a worker returns its name to the pool."""
    all_but_delta = [c for c in NATO_ALPHABET if c != "delta"]

    assert pick_available(all_but_delta) == "delta"


def test_is_case_insensitive():
    """A worker stored as ALPHA still occupies alpha."""
    taken_loudly = [c.upper() for c in NATO_ALPHABET if c != "romeo"]

    assert pick_available(taken_loudly) == "romeo"


def test_spreads_across_the_alphabet():
    """The point of the change: repeated first picks must not always be `alpha`.

    Uses an explicit unseeded RNG so this asserts against real randomness rather
    than the fixed stream the `predictable_callsigns` fixture installs. With 200
    draws over 26 names, a correct implementation covering only one name is not
    something that happens by chance.
    """
    picks = {pick_available([], rng=random.SystemRandom()) for _ in range(200)}

    assert len(picks) > 1, "allocation is not varying — still effectively sequential"


def test_is_deterministic_with_a_seeded_rng():
    seeded = lambda: pick_available([], rng=random.Random(1234))

    assert seeded() == seeded()


def test_raises_when_every_callsign_is_taken():
    with pytest.raises(FleetFullError):
        pick_available(NATO_ALPHABET)


# --------------------------------------------------------------------------
# provisioning
# --------------------------------------------------------------------------

def test_recruit_creates_worktree_and_branch(repo: Path, fleet, worktree_of, store):
    fleet("init", cwd=repo)

    result = fleet("recruit", cwd=repo)

    assert result.ok, result.output
    callsign = only_callsign(store)
    assert worktree_of(callsign).is_dir()
    assert f"fleet/{callsign}" in git("branch", "--list", "fleet/*", cwd=repo)


def test_recruit_records_provenance(repo: Path, fleet, store, worktree_of):
    """The worker file must describe its own worktree, or `done` can't find it."""
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)

    callsign = only_callsign(store)
    content = store.worker_path(callsign).read_text(encoding="utf-8")

    assert f"worker: {callsign}" in content
    assert f"branch: fleet/{callsign}" in content
    assert "status: recruited" in content
    assert str(worktree_of(callsign)) in content


def test_sequential_recruits_get_distinct_callsigns(repo: Path, fleet, live_callsigns):
    fleet("init", cwd=repo)

    fleet("recruit", cwd=repo)
    fleet("recruit", cwd=repo)

    assert len(set(live_callsigns())) == 2


def test_recruit_reattaches_to_an_existing_branch(repo: Path, fleet, worktree_of, live_callsigns, store):
    """Resume: a callsign whose branch survived teardown keeps its commits.

    The freed callsign has to be the one re-picked for this to test anything, and
    random allocation won't guarantee that — so the pool is narrowed to a single
    free name by filling every other slot.
    """
    fleet("init", cwd=repo)
    fleet("recruit", cwd=repo)
    callsign = live_callsigns()[0]
    worktree = worktree_of(callsign)
    (worktree / "feature.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A", cwd=worktree)
    git("commit", "-q", "-m", "resumed work", cwd=worktree)
    fleet("done", cwd=worktree)

    occupy_callsigns(store, [c for c in NATO_ALPHABET if c != callsign])
    fleet("recruit", cwd=repo)

    assert (worktree / "feature.py").exists()
    assert "resumed work" in git("log", "--oneline", cwd=worktree)


# --------------------------------------------------------------------------
# enlisting the launched session
# --------------------------------------------------------------------------

def test_recruit_primes_the_session_to_enlist(repo: Path, fleet, monkeypatch, store, worktree_of):
    """The agent must enlist without the human typing anything.

    Left as a manual step, skipping it is silent: the session opens looking normal and
    simply never joins the fleet, so the worker is invisible to watch/status with no
    error to notice.
    """
    captured: dict = {}

    def fake_launch(directory, agent_cmd, initial_prompt=None, callsign=None):
        captured.update(
            directory=directory, agent_cmd=agent_cmd,
            initial_prompt=initial_prompt, callsign=callsign,
        )
        return None  # as if it exec'd

    monkeypatch.setattr(launch_mod, "launch", fake_launch)
    monkeypatch.setattr(launch_mod, "can_launch", lambda cmd: True)
    fleet("init", cwd=repo)

    fleet("recruit", "--agent", "claude", cwd=repo)

    callsign = only_callsign(store)
    assert captured["initial_prompt"] == "/fleet-start"
    assert captured["callsign"] == callsign
    assert captured["directory"] == worktree_of(callsign)


def test_recruit_without_an_agent_tells_you_to_enlist(repo: Path, fleet):
    """No --agent means no session to prime, so the step falls back to the human."""
    fleet("init", cwd=repo)

    result = fleet("recruit", cwd=repo)

    assert result.ok, result.output
    assert "/fleet-start" in result.output


def test_unknown_provider_is_rejected(repo: Path, fleet):
    fleet("init", cwd=repo)

    result = fleet("recruit", "--provider", "bogus", cwd=repo)

    assert result.exit_code == 1
    assert "unknown worktree provider" in result.output


def test_recruit_refuses_when_fleet_is_full(repo: Path, fleet, store):
    """Synthesize a full roster rather than provisioning 26 real worktrees."""
    fleet("init", cwd=repo)
    occupy_callsigns(store, NATO_ALPHABET)

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
    assert set(worktrees) <= set(NATO_ALPHABET)


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


# --------------------------------------------------------------------------
# freshness — the base a worktree is cut from
# --------------------------------------------------------------------------
#
# A worker that starts from a stale base does work that was never asked for: it
# re-solves problems already fixed on the trunk, and its merge conflicts with them.
# The base is therefore resolved explicitly rather than inherited from whatever the
# repo root happens to have checked out.


def test_recruit_branches_from_the_fetched_remote_trunk(cloned_repo: Path, advance_origin, fleet, worktree_of, store):
    """The bug this exists to prevent: local `main` is behind, and the worker inherits it.

    `origin/main` moves while the clone knows nothing about it. Recruit must fetch and
    cut from the remote ref, so the worktree contains upstream work that local `main`
    has never seen.
    """
    upstream_head = advance_origin("fixed upstream")
    assert upstream_head not in git("rev-parse", "main", cwd=cloned_repo)

    result = fleet("recruit", cwd=cloned_repo)

    assert result.ok, result.output
    worktree = worktree_of(only_callsign(store))
    assert git("rev-parse", "HEAD", cwd=worktree) == upstream_head
    assert (worktree / "fixed_upstream.txt").exists()


def test_recruit_reports_the_base_it_used(cloned_repo: Path, fleet):
    """The base is shown, so a stale worktree is never a silent outcome."""
    result = fleet("recruit", cwd=cloned_repo)

    assert "origin/main" in result.output


def test_recruit_ignores_the_checked_out_branch(cloned_repo: Path, advance_origin, fleet, worktree_of, store):
    """Bare `git worktree add -b` forks from the repo root's HEAD.

    So a main worktree parked on a feature branch silently became every new worker's
    base. The start-point is explicit now, and a detour like this must not reach it.
    """
    advance_origin("upstream work")
    git("checkout", "-q", "-b", "someones-experiment", cwd=cloned_repo)
    (cloned_repo / "experiment.txt").write_text("wip\n", encoding="utf-8")
    git("add", "-A", cwd=cloned_repo)
    git("commit", "-q", "-m", "unrelated experiment", cwd=cloned_repo)

    assert fleet("recruit", cwd=cloned_repo).ok

    worktree = worktree_of(only_callsign(store))
    assert not (worktree / "experiment.txt").exists()
    assert (worktree / "upstream_work.txt").exists()


def test_recruit_survives_an_unreachable_remote(cloned_repo: Path, fleet, store, worktree_of):
    """Being offline must not stop you working — but it must be said out loud."""
    git("remote", "set-url", "origin", str(cloned_repo / "does-not-exist"), cwd=cloned_repo)

    result = fleet("recruit", cwd=cloned_repo)

    assert result.ok, result.output
    assert "could not fetch" in result.output
    assert worktree_of(only_callsign(store)).is_dir()


def test_recruit_without_a_remote_uses_local_trunk(repo: Path, fleet, store, worktree_of):
    """A repo with no remote is not an error, just unverifiable."""
    result = fleet("recruit", cwd=repo)

    assert result.ok, result.output
    assert "no git remote" in result.output
    assert worktree_of(only_callsign(store)).is_dir()


def test_no_fetch_skips_the_network(cloned_repo: Path, advance_origin, fleet, worktree_of, store):
    """`--no-fetch` is a deliberate opt-out, so it must not quietly fetch anyway."""
    advance_origin("upstream work")

    result = fleet("recruit", "--no-fetch", cwd=cloned_repo)

    assert result.ok, result.output
    worktree = worktree_of(only_callsign(store))
    assert not (worktree / "upstream_work.txt").exists()
    assert "fetch skipped" in result.output


def test_recruit_never_reuses_a_leftover_branch(repo: Path, fleet, store, worktree_of):
    """A branch outliving its worker must not become the next worker's base.

    This is what poisons a callsign: the roster frees the name while the branch keeps
    the old state, so the redrawn callsign silently starts weeks behind. Teardown
    normally collects the branch, so this is the escaped case — a crash, a `kill -9` —
    and recruit reclaims it rather than handing over its state.
    """
    git("branch", "fleet/alpha", cwd=repo)
    git("checkout", "-q", "fleet/alpha", cwd=repo)
    (repo / "ancient.txt").write_text("stale\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "months-old work", cwd=repo)
    git("checkout", "-q", "main", cwd=repo)
    occupy_callsigns(store, [c for c in NATO_ALPHABET if c != "alpha"])

    assert fleet("recruit", cwd=repo).ok

    assert "alpha" in store.live_callsigns()
    assert not (worktree_of("alpha") / "ancient.txt").exists()
    # The old commit was never on the trunk, so it is kept rather than discarded.
    assert f"fleet/alpha.abandoned-{today_stamp()}" in git("branch", "--list", "fleet/*", cwd=repo)
