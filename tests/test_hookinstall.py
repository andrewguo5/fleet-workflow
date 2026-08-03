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


def test_install_damages_nothing_already_in_the_file(tmp_path: Path):
    """settings.json is the user's. Unrelated keys, their own Stop hooks, and other
    events all have to survive — we append, never replace."""
    mine = {"hooks": [{"type": "command", "command": "my-own-script.sh"}]}
    write_settings(tmp_path, {
        "theme": "dark",
        "hooks": {"Stop": [mine], "PreToolUse": [{"matcher": "Bash", "hooks": []}]},
    })

    hookinstall.install(tmp_path)

    settings = settings_at(tmp_path)
    assert settings["theme"] == "dark"
    assert "PreToolUse" in settings["hooks"]
    commands = [h["command"] for group in settings["hooks"]["Stop"] for h in group["hooks"]]
    assert commands == ["my-own-script.sh", hookinstall.HOOK_COMMAND]


def test_install_is_idempotent(tmp_path: Path):
    assert hookinstall.install(tmp_path) is True
    assert hookinstall.install(tmp_path) is False

    stop = settings_at(tmp_path)["hooks"]["Stop"]
    ours = [h for group in stop for h in group["hooks"] if h["command"] == hookinstall.HOOK_COMMAND]
    assert len(ours) == 1


def test_install_refuses_to_touch_settings_it_cannot_parse(tmp_path: Path):
    """Overwriting would destroy the user's whole config — worse than no hook."""
    path = tmp_path / hookinstall.SETTINGS_FILE
    path.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(ValueError):
        hookinstall.install(tmp_path)
    assert path.read_text(encoding="utf-8") == "{ this is not json"

    write_settings(tmp_path, {"hooks": "not an object"})
    with pytest.raises(ValueError):
        hookinstall.install(tmp_path)


# --------------------------------------------------------------------------
# uninstall
# --------------------------------------------------------------------------

def test_uninstall_removes_ours_and_only_ours(tmp_path: Path):
    mine = {"hooks": [{"type": "command", "command": "my-own-script.sh"}]}
    write_settings(tmp_path, {"theme": "dark", "hooks": {"Stop": [mine]}})
    hookinstall.install(tmp_path)

    assert hookinstall.uninstall(tmp_path) is True

    settings = settings_at(tmp_path)
    assert settings["theme"] == "dark"
    commands = [h["command"] for group in settings["hooks"]["Stop"] for h in group["hooks"]]
    assert commands == ["my-own-script.sh"]


def test_uninstall_leaves_no_empty_scaffolding(tmp_path: Path):
    """Ours was the only hook, so the keys fleet created go with it — and removing a
    hook that was never there changes nothing."""
    write_settings(tmp_path, {"theme": "dark"})
    hookinstall.install(tmp_path)

    hookinstall.uninstall(tmp_path)
    assert settings_at(tmp_path) == {"theme": "dark"}

    assert hookinstall.uninstall(tmp_path) is False
    assert settings_at(tmp_path) == {"theme": "dark"}


# --------------------------------------------------------------------------
# resolvability
# --------------------------------------------------------------------------

def test_resolves_finds_the_binary_and_reports_a_missing_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An installed hook whose command cannot be found is silent at runtime, so this is
    the only place the problem can be detected."""
    fake = tmp_path / hookinstall.HOOK_BINARY
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert hookinstall.resolves() == str(fake)

    # An empty PATH: nothing to find, and the login-shell fallback inherits it.
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert hookinstall.resolves() is None


def test_supports_delivery_rejects_a_build_without_notify(tmp_path: Path):
    """An install predating `notify` exits with a usage error the harness discards, so
    it fails exactly as silently as a missing binary."""
    def fake_fleet(notify_exit: int) -> str:
        path = tmp_path / f"fleet{notify_exit}"
        path.write_text(f"#!/bin/sh\nexit {notify_exit}\n", encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    assert hookinstall.supports_delivery(fake_fleet(0))
    assert not hookinstall.supports_delivery(fake_fleet(2))
    assert not hookinstall.supports_delivery(str(tmp_path / "does-not-exist"))
