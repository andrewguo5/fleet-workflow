"""In-place session launcher.

fleet never opens a new window: it assumes you are already in the terminal you want to
use. ``launch`` changes into the target directory and *execs* the coding-agent command
right there, so the current shell becomes the agent session. The agent command is
whatever you specify (``--agent`` / ``$FLEET_AGENT``) — e.g. ``claude`` or a wrapper
like ``claude-work``. We exec through an interactive shell so shell aliases/functions
resolve.

If no agent command is configured, ``launch`` returns a ``cd`` hint for the caller to
print instead of exec-ing.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path


def launch(directory: Path, agent_cmd: str | None, initial_prompt: str | None = None) -> str | None:
    """Exec the agent in ``directory``. Never returns on success (the process is
    replaced). Returns a ``cd`` hint string when no agent command is available."""
    directory = directory.resolve()
    if not agent_cmd:
        return f"cd {shlex.quote(str(directory))}   # then start your coding agent here"

    inner = f"exec {agent_cmd}"
    if initial_prompt:
        inner = f"exec {agent_cmd} {shlex.quote(initial_prompt)}"
    script = f"cd {shlex.quote(str(directory))} && {inner}"

    shell = os.environ.get("SHELL", "/bin/zsh")
    os.execvp(shell, [shell, "-i", "-c", script])
    # unreachable
    return None
