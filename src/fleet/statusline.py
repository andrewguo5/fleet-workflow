"""The callsign badge Claude Code renders inside a worker's session.

A worker's callsign lives in fleet's state, not on screen, so an agent session gives
no indication of which worker you are talking to. Claude Code reads
``.claude/settings.json`` from its project directory and merges it over the user's
own settings, so writing one *into the worktree* scopes a ``statusLine`` to that
agent alone — the user's ``~/.claude/settings.json`` is never touched.

Two details make this safe to do on every recruit:

- The settings file is untracked, and ``fleet done`` refuses to tear down a dirty
  worktree. Left alone it would strand every worker. ``core.excludesFile`` is set
  ``--worktree`` so the exclusion applies to this worktree only; a plain
  ``.git/info/exclude`` would not work, because git reads that from the *common*
  git dir and it would follow the main repo around.
- The badge reads ``$FLEET_CALLSIGN`` first and falls back to the directory name,
  so it stays correct under a provider whose worktree is not named after the
  callsign.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

CALLSIGN_ENV_VAR = "FLEET_CALLSIGN"

# Relative to the worktree root. Claude Code's project-scope settings location.
SETTINGS_PATH = Path(".claude") / "settings.json"
BADGE_PATH = Path(".claude") / "fleet-callsign.sh"

# Written into the worktree's private git config so the badge does not read as
# uncommitted work. Kept beside the badge it excludes.
EXCLUDE_PATH = Path(".claude") / "exclude"
EXCLUDE_RULE = "/.claude/\n"

# Cyan, bold, and a filled hexagon: legible on light and dark terminals, and
# distinct from Claude Code's own footer text.
BADGE_SCRIPT = """#!/bin/sh
# Prints this worker's fleet callsign. Installed by `fleet recruit`.
# Prefers $FLEET_CALLSIGN (exported by fleet) over the worktree directory name.
callsign="${FLEET_CALLSIGN:-}"
if [ -z "$callsign" ]; then
    callsign=$(basename "$PWD")
fi
printf '\\033[1;36m\\342\\254\\242 %s\\033[0m' "$callsign"
"""


def _git_config(args: list[str], cwd: Path) -> None:
    """Best-effort ``git config``. A worktree that cannot be configured still gets
    its badge; the only cost is that the settings file shows as untracked."""
    subprocess.run(
        ["git", "config", *args],
        cwd=cwd, capture_output=True, text=True, check=False,
    )


def _exclude_from_git(worktree: Path) -> None:
    """Hide the badge from ``git status`` for this worktree only.

    ``extensions.worktreeConfig`` is what makes ``--worktree`` scoping available;
    without it the setting would land in the shared config and follow every
    worktree, including the user's main checkout.
    """
    _git_config(["extensions.worktreeConfig", "true"], worktree)
    _git_config(["--worktree", "core.excludesFile", str(worktree / EXCLUDE_PATH)], worktree)


def install(worktree: Path, callsign: str) -> None:
    """Give ``worktree`` a status line showing ``callsign``.

    Best-effort by design: a badge is a convenience, and a worker that cannot get
    one should still be recruited. Callers do not need to handle failure.
    """
    try:
        settings_file = worktree / SETTINGS_PATH
        badge_file = worktree / BADGE_PATH
        settings_file.parent.mkdir(parents=True, exist_ok=True)

        (worktree / EXCLUDE_PATH).write_text(EXCLUDE_RULE, encoding="utf-8")
        badge_file.write_text(BADGE_SCRIPT, encoding="utf-8")
        badge_file.chmod(0o755)
        settings_file.write_text(
            json.dumps({"statusLine": {"type": "command", "command": str(badge_file)}}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        _exclude_from_git(worktree)
    except OSError:
        pass
