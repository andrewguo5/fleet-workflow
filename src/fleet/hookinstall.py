"""Install the mail-delivery ``Stop`` hook into the agent's user settings.

The hook has to live in *user* settings (``~/.claude/settings.json``, or
``$CLAUDE_CONFIG_DIR``), not in the worktree beside the status line. Claude Code gates
project-scoped settings behind a workspace-trust dialog, and every ``fleet recruit``
creates a directory that has never been trusted — so a worktree hook would put a trust
prompt in front of the agent at the exact moment ``recruit`` execs it, on every single
recruit. User settings are already trusted, once, forever.

That is a real cost: the status line was deliberately built to leave user settings alone.
The hook is therefore *opt-in* — ``fleet init`` shows the exact JSON and asks — and it is
written to be inert everywhere except a fleet worktree, since it will now fire on every
turn of every session the user runs, in every project.
"""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS_FILE = "settings.json"

# `fleet` resolves on PATH for any session that can run the CLI at all. Deliberately not
# an absolute path: the user may install fleet in a venv, upgrade it, or move it, and a
# baked-in path would silently rot into a hook that fails on every turn forever.
HOOK_COMMAND = "fleet notify --hook"

HOOK_EVENT = "Stop"


def hook_entry() -> dict:
    return {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        # Refuse to guess at a corrupt file: overwriting it would destroy the user's
        # whole configuration, which is far worse than not installing a hook.
        raise ValueError(f"{path} is not valid JSON; leaving it untouched")
    return loaded if isinstance(loaded, dict) else {}


def is_installed(settings: dict) -> bool:
    """Whether our hook is already present. Keyed on the command string, so a settings
    file the user has since reorganized still counts as installed.

    Tolerant of any shape: this inspects a hand-edited file, and a malformed one means
    "not installed", never a crash. ``install`` is where bad shapes are reported.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    stop_hooks = hooks.get(HOOK_EVENT)
    if not isinstance(stop_hooks, list):
        return False
    for group in stop_hooks:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []) or []:
            if isinstance(hook, dict) and hook.get("command") == HOOK_COMMAND:
                return True
    return False


def is_installed_in(config_dir: Path) -> bool:
    """Whether the hook is present in ``config_dir``. Raises ValueError on a settings
    file we refuse to touch, so callers can report *why* they skipped."""
    return is_installed(_load_settings(config_dir / SETTINGS_FILE))


def preview() -> str:
    """The exact JSON ``install`` would add, for the user to approve."""
    return json.dumps({"hooks": {HOOK_EVENT: [hook_entry()]}}, indent=2)


def install(config_dir: Path) -> bool:
    """Add the Stop hook to ``config_dir/settings.json``, preserving everything else.

    Returns False if it was already there. Merges into any existing ``Stop`` hooks
    rather than replacing them — the user may well have their own.
    """
    path = config_dir / SETTINGS_FILE
    settings = _load_settings(path)
    if is_installed(settings):
        return False

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{path} has a 'hooks' key that is not an object; leaving it untouched")
    stop_hooks = hooks.setdefault(HOOK_EVENT, [])
    if not isinstance(stop_hooks, list):
        raise ValueError(f"{path} has a '{HOOK_EVENT}' key that is not a list; leaving it untouched")
    stop_hooks.append(hook_entry())

    path.parent.mkdir(parents=True, exist_ok=True)
    # Written whole rather than in place: settings.json is the user's, and a partial
    # write would be worse than no write.
    tmp = path.with_suffix(".json.fleet-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return True


def uninstall(config_dir: Path) -> bool:
    """Remove our hook, leaving any others intact. Returns False if it wasn't there."""
    path = config_dir / SETTINGS_FILE
    settings = _load_settings(path)
    if not is_installed(settings):
        return False

    stop_hooks = settings.get("hooks", {}).get(HOOK_EVENT, [])
    surviving = []
    for group in stop_hooks:
        if not isinstance(group, dict):
            surviving.append(group)
            continue
        kept = [
            h for h in (group.get("hooks", []) or [])
            if not (isinstance(h, dict) and h.get("command") == HOOK_COMMAND)
        ]
        if kept:
            surviving.append({**group, "hooks": kept})
    if surviving:
        settings["hooks"][HOOK_EVENT] = surviving
    else:
        # Leave no empty scaffolding behind in the user's file.
        settings["hooks"].pop(HOOK_EVENT, None)
        if not settings["hooks"]:
            settings.pop("hooks", None)

    tmp = path.with_suffix(".json.fleet-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return True
