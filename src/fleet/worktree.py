"""Worktree provisioning, behind a small provider protocol so the rest of the engine
is backend-agnostic and simply records whatever path the provider returns.

- ``PlainGit`` (default): stock ``git worktree`` at ``<repo>/../wt/<callsign>`` on a
  ``fleet/<callsign>`` branch. Zero external dependencies.
- ``Treehouse`` (strict opt-in): leases a pooled worktree from the ``treehouse`` CLI
  and checks out ``fleet/<callsign>`` inside it, for build-cache/dependency reuse.

Neither provider deletes branches — content git ops (including the eventual squash
merge and any branch cleanup) belong to the worker, not to fleet.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable


class WorktreeError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        raise WorktreeError(f"'{cmd[0]}' not found on PATH")
    if result.returncode != 0:
        raise WorktreeError(result.stderr.strip() or f"{' '.join(cmd)} failed")
    return result.stdout.strip()


def _git(args: list[str], cwd: Path) -> str:
    return _run(["git", *args], cwd)


@runtime_checkable
class WorktreeProvider(Protocol):
    def acquire(self, callsign: str) -> str:
        """Provision (or re-attach) a worktree for the callsign; return its path."""

    def release(self, callsign: str, worktree: str) -> None:
        """Tear down the worktree (the branch is left intact)."""


class PlainGit:
    """Stock ``git worktree`` backend."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.wt_base = repo_root.parent / "wt"

    def _branch_exists(self, branch: str) -> bool:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=self.repo_root, capture_output=True, text=True,
        )
        return result.returncode == 0

    def acquire(self, callsign: str) -> str:
        branch = f"fleet/{callsign}"
        path = self.wt_base / callsign
        if path.exists():
            return str(path)
        self.wt_base.mkdir(parents=True, exist_ok=True)
        if self._branch_exists(branch):
            # Re-attach to an existing branch (resume) rather than recreating it.
            _git(["worktree", "add", str(path), branch], cwd=self.repo_root)
        else:
            _git(["worktree", "add", str(path), "-b", branch], cwd=self.repo_root)
        return str(path)

    def release(self, callsign: str, worktree: str) -> None:
        path = Path(worktree)
        if path.exists():
            _git(["worktree", "remove", "--force", str(path)], cwd=self.repo_root)
        _git(["worktree", "prune"], cwd=self.repo_root)


class Treehouse:
    """Optional backend delegating worktree pooling to the ``treehouse`` CLI.

    Untested in this environment (treehouse not installed); implemented defensively by
    parsing the leased path out of ``treehouse get --lease`` stdout.
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def _treehouse(self, args: list[str], cwd: Path) -> str:
        return _run(["treehouse", *args], cwd)

    @staticmethod
    def _extract_path(output: str) -> str:
        for line in output.splitlines():
            token = line.strip().strip("'\"")
            if token.startswith("/") and Path(token).is_dir():
                return token
        raise WorktreeError(f"could not parse leased worktree path from: {output!r}")

    def acquire(self, callsign: str) -> str:
        out = self._treehouse(["get", "--lease"], cwd=self.repo_root)
        path = Path(self._extract_path(out))
        _git(["checkout", "-b", f"fleet/{callsign}"], cwd=path)
        return str(path)

    def release(self, callsign: str, worktree: str) -> None:
        self._treehouse(["return", worktree], cwd=self.repo_root)


def get_provider(name: str, repo_root: Path) -> WorktreeProvider:
    if name == "plain":
        return PlainGit(repo_root)
    if name == "treehouse":
        return Treehouse(repo_root)
    raise WorktreeError(f"unknown worktree provider: {name!r} (expected 'plain' or 'treehouse')")
