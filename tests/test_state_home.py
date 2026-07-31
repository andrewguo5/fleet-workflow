"""Where fleet state lives, and moving state written by an older version.

State follows ``CLAUDE_CONFIG_DIR`` so a work-scoped agent keeps its fleet beside its
own config. Earlier versions instead used ``~/.claude-work`` whenever that directory
merely existed, which put every project there regardless of the agent in use. Both the
fallback that keeps such state readable and the migration that relocates it are pinned
here, because the failure mode of getting either wrong is a fleet that appears to have
vanished.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet.store import FleetStore, _state_home


@pytest.fixture
def unscoped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("FLEET_STATE_HOME", raising=False)


def write_legacy_state(home: Path, repo: Path, callsign: str = "alpha") -> Path:
    """Plant state where a pre-config-dir version would have put it."""
    slug = str(repo.resolve()).replace("/", "-")
    base = home / ".claude-work" / "projects" / slug / "fleet"
    (base / "workers").mkdir(parents=True)
    (base / "workers" / f"{callsign}.md").write_text(
        f"---\nworker: {callsign}\nstatus: running\nthread: legacy\n---\n", encoding="utf-8"
    )
    return base


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def test_follows_claude_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("FLEET_STATE_HOME", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "work"))

    assert _state_home() == tmp_path / "work" / "projects"


def test_defaults_to_the_personal_config_dir(unscoped):
    assert _state_home() == Path("~/.claude/projects").expanduser()


def test_fleet_state_home_overrides_the_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The explicit override has to win, or the test suite could not isolate itself."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("FLEET_STATE_HOME", str(tmp_path / "explicit"))

    assert _state_home() == tmp_path / "explicit"


def test_work_and_personal_resolve_to_different_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("FLEET_STATE_HOME", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "work"))
    work = _state_home()
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")

    assert work != _state_home()


# --------------------------------------------------------------------------
# the legacy fallback
# --------------------------------------------------------------------------

def test_reads_legacy_state_when_nothing_is_at_the_new_location(
    unscoped, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path
):
    """An upgrade must not look like every worker disappeared."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("fleet.store.DEFAULT_CONFIG_DIR", tmp_path / ".claude")
    legacy = write_legacy_state(tmp_path, repo)

    assert FleetStore(cwd=repo).base == legacy


def test_prefers_the_new_location_when_both_exist(
    unscoped, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("fleet.store.DEFAULT_CONFIG_DIR", tmp_path / ".claude")
    write_legacy_state(tmp_path, repo)
    slug = str(repo.resolve()).replace("/", "-")
    intended = tmp_path / ".claude" / "projects" / slug / "fleet"
    (intended / "workers").mkdir(parents=True)

    assert FleetStore(cwd=repo).base == intended


def test_legacy_state_is_still_reported_when_the_new_location_exists(
    unscoped, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path
):
    """Stranded state must not be masked: once the store stops falling back, migrate
    still has to find it, or it is silently orphaned."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("fleet.store.DEFAULT_CONFIG_DIR", tmp_path / ".claude")
    legacy = write_legacy_state(tmp_path, repo)
    slug = str(repo.resolve()).replace("/", "-")
    (tmp_path / ".claude" / "projects" / slug / "fleet" / "workers").mkdir(parents=True)

    assert FleetStore(cwd=repo).legacy_base() == legacy


# --------------------------------------------------------------------------
# migration
# --------------------------------------------------------------------------

@pytest.fixture
def with_legacy(unscoped, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("fleet.store.DEFAULT_CONFIG_DIR", tmp_path / ".claude")
    legacy = write_legacy_state(tmp_path, repo)
    slug = str(repo.resolve()).replace("/", "-")
    return legacy, tmp_path / ".claude" / "projects" / slug / "fleet"


def test_migrate_moves_state_to_the_config_dir(with_legacy, fleet, repo: Path):
    legacy, intended = with_legacy

    result = fleet("migrate", cwd=repo)

    assert result.ok, result.output
    assert (intended / "workers" / "alpha.md").is_file()
    assert not legacy.exists()


def test_migrate_preserves_content(with_legacy, fleet, repo: Path):
    legacy, intended = with_legacy
    before = (legacy / "workers" / "alpha.md").read_text(encoding="utf-8")

    fleet("migrate", cwd=repo)

    assert (intended / "workers" / "alpha.md").read_text(encoding="utf-8") == before


def test_dry_run_changes_nothing(with_legacy, fleet, repo: Path):
    legacy, intended = with_legacy

    result = fleet("migrate", "--dry-run", cwd=repo)

    assert result.ok
    assert (legacy / "workers" / "alpha.md").is_file()
    assert not intended.exists()


def test_dry_run_names_the_workers_it_would_move(with_legacy, fleet, repo: Path):
    result = fleet("migrate", "--dry-run", cwd=repo)

    assert "alpha" in result.output


def test_migrate_is_idempotent(with_legacy, fleet, repo: Path):
    fleet("migrate", cwd=repo)

    second = fleet("migrate", cwd=repo)

    assert second.ok
    assert "already in place" in second.output


def test_migrate_refuses_when_the_destination_has_state(with_legacy, fleet, repo: Path):
    """Never merge blindly: both sides must survive for the user to reconcile."""
    legacy, intended = with_legacy
    (intended / "workers").mkdir(parents=True)
    (intended / "workers" / "bravo.md").write_text(
        "---\nworker: bravo\nstatus: running\n---\n", encoding="utf-8"
    )

    result = fleet("migrate", cwd=repo)

    assert result.exit_code == 1
    assert "already has state" in result.output
    assert (legacy / "workers" / "alpha.md").is_file()
    assert (intended / "workers" / "bravo.md").is_file()


def test_migrated_workers_are_listed(with_legacy, fleet, repo: Path):
    """The end-to-end guarantee: after migrating, the fleet is still there."""
    fleet("migrate", cwd=repo)

    assert "alpha" in fleet("ls", cwd=repo).output


def test_migrate_with_nothing_to_move_is_a_noop(unscoped, monkeypatch, tmp_path: Path, repo: Path, fleet):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("fleet.store.DEFAULT_CONFIG_DIR", tmp_path / ".claude")

    result = fleet("migrate", cwd=repo)

    assert result.ok
    assert "already in place" in result.output
