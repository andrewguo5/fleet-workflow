"""Where ``fleet init`` installs the prompt pack.

The prompts are only useful to an agent that reads the config directory they land in.
Claude Code resolves that from ``CLAUDE_CONFIG_DIR``, and scoped wrappers set it — e.g.
``claude-work () { CLAUDE_CONFIG_DIR=~/.claude-work command claude "$@" }``. Installing
to a fixed ``~/.claude`` silently strands those sessions with no slash commands, so the
resolution is pinned here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet.cli import COMMAND_PROMPTS, commands_dir


@pytest.fixture
def unscoped(monkeypatch: pytest.MonkeyPatch):
    """No CLAUDE_CONFIG_DIR, as in a plain shell."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def test_defaults_to_the_personal_config_dir(unscoped):
    assert commands_dir() == Path("~/.claude/commands").expanduser()


def test_follows_claude_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "work-config"))

    assert commands_dir() == tmp_path / "work-config" / "commands"


def test_expands_a_tilde_in_the_config_dir(monkeypatch: pytest.MonkeyPatch):
    """The variable is routinely written as ~/.claude-work in a shell function."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/.claude-work")

    assert commands_dir() == Path("~/.claude-work/commands").expanduser()


def test_resolves_per_call_not_at_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A module-level constant would freeze whichever env imported fleet first."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "first"))
    first = commands_dir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "second"))

    assert commands_dir() != first


# --------------------------------------------------------------------------
# installation
# --------------------------------------------------------------------------

def test_installs_into_the_scoped_config_dir(repo: Path, fleet, monkeypatch, tmp_path: Path):
    config = tmp_path / "work-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    result = fleet("init", cwd=repo)

    assert result.ok, result.output
    for prompt in COMMAND_PROMPTS:
        assert (config / "commands" / prompt).is_file()


def test_does_not_touch_the_other_config_dir(repo: Path, fleet, monkeypatch, tmp_path: Path):
    """Installing for work must not write into the personal dir, or vice versa."""
    personal = tmp_path / "personal"
    (personal / "commands").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "work"))

    fleet("init", cwd=repo)

    assert list((personal / "commands").iterdir()) == []


def test_commands_dir_override_wins(repo: Path, fleet, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "ignored"))
    explicit = tmp_path / "explicit"

    result = fleet("init", "--commands-dir", str(explicit), cwd=repo)

    assert result.ok, result.output
    for prompt in COMMAND_PROMPTS:
        assert (explicit / prompt).is_file()
    assert not (tmp_path / "ignored").exists()


def test_creates_a_missing_config_dir(repo: Path, fleet, monkeypatch, tmp_path: Path):
    config = tmp_path / "brand-new" / "nested"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    assert fleet("init", cwd=repo).ok
    assert (config / "commands").is_dir()


def test_init_is_idempotent(repo: Path, fleet, monkeypatch, tmp_path: Path):
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    fleet("init", cwd=repo)
    second = fleet("init", cwd=repo)

    assert second.ok
    installed = sorted(p.name for p in (config / "commands").iterdir())
    assert installed == sorted(COMMAND_PROMPTS)


def test_reports_where_the_prompts_landed(repo: Path, fleet, monkeypatch, tmp_path: Path):
    """The output has to name the directory, or a mis-scoped install is invisible."""
    config = tmp_path / "work-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    result = fleet("init", cwd=repo)

    assert "work-config" in result.output


def test_hints_about_scoping_only_when_unscoped(repo: Path, fleet, unscoped, tmp_path: Path, monkeypatch):
    """Someone already scoped does not need to be told about scoping."""
    monkeypatch.setattr("fleet.cli.DEFAULT_CONFIG_DIR", tmp_path / "default")

    unscoped_output = fleet("init", cwd=repo).output
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "scoped"))
    scoped_output = fleet("init", cwd=repo).output

    assert "work/personal-scoped" in unscoped_output
    assert "work/personal-scoped" not in scoped_output


def test_writes_the_protocol_doc_to_state_not_commands(repo: Path, fleet, store, tmp_path: Path, monkeypatch):
    """FLEET.md is reference material, not a slash command."""
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    fleet("init", cwd=repo)

    assert (store.base / "FLEET.md").is_file()
    assert not (config / "commands" / "FLEET.md").exists()
