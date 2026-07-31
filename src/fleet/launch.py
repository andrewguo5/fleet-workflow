"""In-place session launcher.

fleet never opens a new window: it assumes you are already in the terminal you want to
use. ``launch`` changes into the target directory and *execs* the coding-agent command
right there, so the current shell becomes the agent session. The agent command is
whatever you specify (``--agent`` / ``$FLEET_AGENT``) — e.g. ``claude`` or a wrapper
like ``claude-work``. We exec through an interactive shell so shell aliases/functions
resolve.

If no agent command is configured, ``launch`` returns a ``cd`` hint for the caller to
print instead of exec-ing.

A worker's callsign is exported as ``$FLEET_CALLSIGN`` into the exec'd environment,
so the session can identify itself without consulting fleet's state.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from .statusline import CALLSIGN_ENV_VAR

# Seconds to let the shell start up and resolve the command. Generous: an interactive
# shell sources the user's whole profile, which can be slow on a cold cache.
RESOLVE_TIMEOUT_SECONDS = 15


def _shell() -> str:
    return os.environ.get("SHELL", "/bin/zsh")


def can_launch(agent_cmd: str) -> bool:
    """Whether the agent command resolves in the user's interactive shell.

    Checked through ``$SHELL -i`` rather than ``shutil.which`` because the command is
    routinely a shell alias or function (``claude-work``), which exists only once the
    profile is sourced and is invisible to a PATH lookup.

    Callers use this to fail *before* provisioning a worktree: ``launch`` execs and
    never returns, so a command that cannot start would otherwise leave a worker
    stranded with no agent and no error.
    """
    binary = shlex.split(agent_cmd)[0] if agent_cmd.strip() else ""
    if not binary:
        return False
    try:
        result = subprocess.run(
            [_shell(), "-i", "-c", f"command -v {shlex.quote(binary)}"],
            capture_output=True,
            text=True,
            timeout=RESOLVE_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        # An unresolvable shell is not evidence the command is bad; let the caller
        # proceed rather than blocking a recruit on a slow or missing profile.
        return True
    return result.returncode == 0


def launch(
    directory: Path,
    agent_cmd: str | None,
    initial_prompt: str | None = None,
    callsign: str | None = None,
) -> str | None:
    """Exec the agent in ``directory``. Never returns on success (the process is
    replaced). Returns a ``cd`` hint string when no agent command is available.

    ``callsign`` is exported as ``$FLEET_CALLSIGN`` so the session can name itself —
    the status-line badge reads it, and it beats inferring identity from the
    directory name under a provider that does not name worktrees after callsigns.
    """
    directory = directory.resolve()
    if not agent_cmd:
        return f"cd {shlex.quote(str(directory))}   # then start your coding agent here"

    inner = f"exec {agent_cmd}"
    if initial_prompt:
        inner = f"exec {agent_cmd} {shlex.quote(initial_prompt)}"
    script = f"cd {shlex.quote(str(directory))} && {inner}"

    shell = _shell()
    env = os.environ.copy()
    if callsign:
        env[CALLSIGN_ENV_VAR] = callsign
    os.execvpe(shell, [shell, "-i", "-c", script], env)
    # unreachable
    return None
