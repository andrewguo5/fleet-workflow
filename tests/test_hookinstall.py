"""Installing the mail hook into the agent's settings.json.

This is the one place fleet writes to a file it does not own, so the tests are mostly
about *not damaging it*: preserving unrelated keys, merging alongside the user's own
Stop hooks, refusing to touch a file it cannot parse, and removing exactly what it added.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleet import hookinstall


def settings_at(config_dir: Path) -> dict:
    return json.loads((config_dir / hookinstall.SETTINGS_FILE).read_text(encoding="utf-8"))


def write_settings(config_dir: Path, data: dict) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / hookinstall.SETTINGS_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------

def test_installs_into_a_missing_settings_file(tmp_path: Path):
    assert hookinstall.install(tmp_path) is True

    assert hookinstall.is_installed_in(tmp_path)


def test_install_preserves_unrelated_settings(tmp_path: Path):
    write_settings(tmp_path, {"theme": "dark", "alwaysThinkingEnabled": True})

    hookinstall.install(tmp_path)

    settings = settings_at(tmp_path)
    assert settings["theme"] == "dark"
    assert settings["alwaysThinkingEnabled"] is True


def test_install_keeps_the_users_own_stop_hooks(tmp_path: Path):
    """Appending, not replacing — the user may already run something on Stop."""
    mine = {"hooks": [{"type": "command", "command": "my-own-script.sh"}]}
    write_settings(tmp_path, {"hooks": {"Stop": [mine]}})

    hookinstall.install(tmp_path)

    stop = settings_at(tmp_path)["hooks"]["Stop"]
    commands = [h["command"] for group in stop for h in group["hooks"]]
    assert "my-own-script.sh" in commands
    assert hookinstall.HOOK_COMMAND in commands


def test_install_keeps_other_hook_events(tmp_path: Path):
    write_settings(tmp_path, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}})

    hookinstall.install(tmp_path)

    assert "PreToolUse" in settings_at(tmp_path)["hooks"]


def test_install_is_idempotent(tmp_path: Path):
    assert hookinstall.install(tmp_path) is True
    assert hookinstall.install(tmp_path) is False

    stop = settings_at(tmp_path)["hooks"]["Stop"]
    ours = [h for group in stop for h in group["hooks"] if h["command"] == hookinstall.HOOK_COMMAND]
    assert len(ours) == 1


def test_install_refuses_to_touch_unparseable_settings(tmp_path: Path):
    """Overwriting would destroy the user's whole config — worse than no hook."""
    path = tmp_path / hookinstall.SETTINGS_FILE
    path.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(ValueError):
        hookinstall.install(tmp_path)

    assert path.read_text(encoding="utf-8") == "{ this is not json"


def test_install_refuses_when_hooks_is_the_wrong_shape(tmp_path: Path):
    write_settings(tmp_path, {"hooks": "not an object"})

    with pytest.raises(ValueError):
        hookinstall.install(tmp_path)


def test_installed_hook_command_is_not_an_absolute_path(tmp_path: Path):
    """A baked-in path rots when fleet is upgraded, moved, or installed in a venv."""
    hookinstall.install(tmp_path)

    stop = settings_at(tmp_path)["hooks"]["Stop"]
    command = [h["command"] for group in stop for h in group["hooks"]][0]
    assert not command.startswith("/")
    assert command.startswith("fleet ")


# --------------------------------------------------------------------------
# uninstall
# --------------------------------------------------------------------------

def test_uninstall_removes_our_hook(tmp_path: Path):
    hookinstall.install(tmp_path)

    assert hookinstall.uninstall(tmp_path) is True
    assert not hookinstall.is_installed_in(tmp_path)


def test_uninstall_leaves_the_users_hooks_alone(tmp_path: Path):
    mine = {"hooks": [{"type": "command", "command": "my-own-script.sh"}]}
    write_settings(tmp_path, {"theme": "dark", "hooks": {"Stop": [mine]}})
    hookinstall.install(tmp_path)

    hookinstall.uninstall(tmp_path)

    settings = settings_at(tmp_path)
    assert settings["theme"] == "dark"
    commands = [h["command"] for group in settings["hooks"]["Stop"] for h in group["hooks"]]
    assert commands == ["my-own-script.sh"]


def test_uninstall_leaves_no_empty_scaffolding(tmp_path: Path):
    """Ours was the only hook, so the keys fleet created should go with it."""
    write_settings(tmp_path, {"theme": "dark"})
    hookinstall.install(tmp_path)

    hookinstall.uninstall(tmp_path)

    assert settings_at(tmp_path) == {"theme": "dark"}


def test_uninstall_when_not_installed(tmp_path: Path):
    write_settings(tmp_path, {"theme": "dark"})

    assert hookinstall.uninstall(tmp_path) is False
    assert settings_at(tmp_path) == {"theme": "dark"}


# --------------------------------------------------------------------------
# resolvability
# --------------------------------------------------------------------------

def test_resolves_finds_the_binary_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An installed hook whose command cannot be found is silent at runtime, so this is
    the only place the problem can be detected."""
    fake = tmp_path / hookinstall.HOOK_BINARY
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert hookinstall.resolves() == str(fake)


def test_resolves_reports_a_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An empty PATH: nothing to find, and the login-shell fallback inherits it."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    assert hookinstall.resolves() is None


def test_hook_command_invokes_the_binary_it_checks(tmp_path: Path):
    """The name `resolves()` looks up must be the one settings.json actually runs, or
    the check passes while delivery stays broken."""
    assert hookinstall.HOOK_COMMAND.split()[0] == hookinstall.HOOK_BINARY


def fake_fleet(path: Path, notify_exit: int) -> Path:
    """A stand-in `fleet` whose `notify` succeeds or fails like an old build's would."""
    path.write_text(f"#!/bin/sh\nexit {notify_exit}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_supports_delivery_accepts_a_build_with_notify(tmp_path: Path):
    assert hookinstall.supports_delivery(str(fake_fleet(tmp_path / "fleet", 0)))


def test_supports_delivery_rejects_a_build_without_notify(tmp_path: Path):
    """An install predating `notify` exits with a usage error the harness discards, so
    it fails exactly as silently as a missing binary."""
    assert not hookinstall.supports_delivery(str(fake_fleet(tmp_path / "fleet", 2)))


def test_supports_delivery_rejects_an_unrunnable_path(tmp_path: Path):
    assert not hookinstall.supports_delivery(str(tmp_path / "does-not-exist"))
