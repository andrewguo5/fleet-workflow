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

from fleet import hookinstall
from fleet.cli import COMMAND_PROMPTS, commands_dir


@pytest.fixture
def unscoped(monkeypatch: pytest.MonkeyPatch):
    """No CLAUDE_CONFIG_DIR, as in a plain shell."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.fixture
def hook_reachable(monkeypatch: pytest.MonkeyPatch):
    """A `fleet` on PATH that can deliver mail — the healthy case.

    Both halves are stubbed because the real checks shell out, and a test must not
    depend on how fleet happens to be installed on the machine running it.
    """
    monkeypatch.setattr("fleet.hookinstall.resolves", lambda: "/usr/local/bin/fleet")
    monkeypatch.setattr("fleet.hookinstall.supports_delivery", lambda path: True)


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


# --------------------------------------------------------------------------
# the mail-delivery hook
# --------------------------------------------------------------------------

def test_does_not_install_the_hook_without_consent(repo: Path, fleet, tmp_path: Path, monkeypatch):
    """settings.json belongs to the user. A non-interactive init must never edit it."""
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    fleet("init", cwd=repo)

    assert not hookinstall.is_installed_in(config)


def test_init_still_succeeds_without_a_tty(repo: Path, fleet, tmp_path: Path, monkeypatch):
    """A confirm prompt that aborts would turn an optional extra into a failed init."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))

    result = fleet("init", cwd=repo)

    assert result.ok, result.output
    assert "skipped" in result.output


def test_installs_the_hook_when_asked(repo: Path, fleet, tmp_path: Path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    result = fleet("init", "--install-mail-hook", cwd=repo)

    assert result.ok, result.output
    assert hookinstall.is_installed_in(config)


def test_shows_the_exact_json_before_asking(repo: Path, fleet, tmp_path: Path, monkeypatch):
    """Consent to an unseen edit is not consent."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))

    output = fleet("init", cwd=repo).output

    assert hookinstall.HOOK_COMMAND in output
    assert "Stop" in output


def test_hook_goes_to_the_config_dir_not_the_commands_override(repo: Path, fleet, tmp_path: Path, monkeypatch):
    """--commands-dir may point anywhere; settings.json still belongs to the agent."""
    config = tmp_path / "config"
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    fleet("init", "--commands-dir", str(elsewhere), "--install-mail-hook", cwd=repo)

    assert hookinstall.is_installed_in(config)
    assert not (elsewhere / hookinstall.SETTINGS_FILE).exists()


def test_reports_an_already_installed_hook(repo: Path, fleet, tmp_path: Path, monkeypatch):
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    fleet("init", "--install-mail-hook", cwd=repo)

    output = fleet("init", cwd=repo).output

    assert "already installed" in output


def test_warns_when_the_hook_command_will_not_resolve(repo: Path, fleet, tmp_path: Path, monkeypatch):
    """Installing a hook that cannot run is silent forever; install time is the only
    moment the user can be told."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("fleet.hookinstall.resolves", lambda: None)

    output = fleet("init", "--install-mail-hook", cwd=repo).output

    assert "not on PATH" in output
    assert "fleet notify --check" in output


def test_no_warning_when_the_hook_resolves(repo: Path, fleet, tmp_path: Path, monkeypatch, hook_reachable):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))

    output = fleet("init", "--install-mail-hook", cwd=repo).output

    assert "not on PATH" not in output


def test_an_unresolvable_hook_is_still_installed(repo: Path, fleet, tmp_path: Path, monkeypatch):
    """A warning, not a refusal: PATH may simply differ between this process and the
    agent's, and the hook starts working as soon as fleet is reachable."""
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setattr("fleet.hookinstall.resolves", lambda: None)

    result = fleet("init", "--install-mail-hook", cwd=repo)

    assert result.ok, result.output
    assert hookinstall.is_installed_in(config)


# --------------------------------------------------------------------------
# fleet notify --check
# --------------------------------------------------------------------------

def test_check_passes_when_installed_and_resolvable(repo: Path, fleet, tmp_path: Path, monkeypatch, hook_reachable):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    fleet("init", "--install-mail-hook", cwd=repo)

    result = fleet("notify", "--check", cwd=repo)

    assert result.ok, result.output
    assert "mail delivery is working" in result.output


def test_check_fails_when_the_hook_is_missing(repo: Path, fleet, tmp_path: Path, monkeypatch, hook_reachable):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    fleet("init", cwd=repo)

    result = fleet("notify", "--check", cwd=repo)

    assert result.exit_code == 1
    assert "not installed" in result.output


def test_check_fails_when_the_binary_is_unreachable(repo: Path, fleet, tmp_path: Path, monkeypatch):
    """Installed but unresolvable — the case that is invisible at runtime."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("fleet.hookinstall.resolves", lambda: None)
    fleet("init", "--install-mail-hook", cwd=repo)

    result = fleet("notify", "--check", cwd=repo)

    assert result.exit_code == 1
    assert "not on PATH" in result.output


def test_check_reports_both_failures_independently(repo: Path, fleet, tmp_path: Path, monkeypatch):
    """Either alone breaks delivery, so a user with both needs to see both."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("fleet.hookinstall.resolves", lambda: None)
    fleet("init", cwd=repo)

    output = fleet("notify", "--check", cwd=repo).output

    assert "not installed" in output
    assert "not on PATH" in output


def test_check_fails_when_the_path_fleet_is_too_old(repo: Path, fleet, tmp_path: Path, monkeypatch):
    """The hook runs whichever fleet PATH finds. A resolvable name is not enough — an
    install predating `notify` fails just as silently as a missing one."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("fleet.hookinstall.resolves", lambda: "/usr/local/bin/fleet")
    monkeypatch.setattr("fleet.hookinstall.supports_delivery", lambda path: False)
    fleet("init", "--install-mail-hook", cwd=repo)

    result = fleet("notify", "--check", cwd=repo)

    assert result.exit_code == 1
    assert "too old" in result.output


def test_warns_at_install_when_the_path_fleet_is_too_old(repo: Path, fleet, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("fleet.hookinstall.resolves", lambda: "/usr/local/bin/fleet")
    monkeypatch.setattr("fleet.hookinstall.supports_delivery", lambda path: False)

    output = fleet("init", "--install-mail-hook", cwd=repo).output

    assert "too old" in output
