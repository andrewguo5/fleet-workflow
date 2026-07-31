"""FleetStore — locates the per-project state directory, and owns the low-level
guarantees every writer relies on: a cross-process lock for callsign allocation and
atomic file writes.

The store lives *outside* the repo (under the claude-work project dir) so that every
git worktree of the same repo sees one shared fleet state, and so the repo itself stays
clean. All worktrees of a repo map to the same directory because the identity is
derived from the repo's *common* git dir, not the per-worktree checkout path.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class NotAGitRepoError(RuntimeError):
    """Raised when fleet is run outside a git repository."""


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NotAGitRepoError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def repo_root(cwd: Path | None = None) -> Path:
    """The main worktree root, stable across every linked worktree of the repo.

    We derive it from the *common* git dir (shared by all worktrees) rather than
    ``--show-toplevel`` (which differs per worktree).
    """
    common = Path(_run_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd))
    return common.parent if common.name == ".git" else common


def project_slug(cwd: Path | None = None) -> str:
    """The repo's absolute path with '/' -> '-' (e.g. '-Users-andrew-Rialto-rialto')."""
    return str(repo_root(cwd)).replace("/", "-")


DEFAULT_CONFIG_DIR = Path("~/.claude")


def _state_home() -> Path:
    """Root under which each project's fleet state lives.

    Follows ``CLAUDE_CONFIG_DIR`` — the same variable Claude Code and ``fleet init``
    honor — so a work-scoped agent keeps its fleet state beside its own config rather
    than in the personal one. ``FLEET_STATE_HOME`` overrides everything.

    Earlier versions instead used ``~/.claude-work`` whenever that directory merely
    existed, which put *every* project's state there regardless of which agent was
    running. That is preserved only as a fallback for state already written that way,
    so an upgrade does not appear to lose workers; new state follows the config dir.
    """
    override = os.environ.get("FLEET_STATE_HOME")
    if override:
        return Path(override).expanduser()

    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser() / "projects"
    return DEFAULT_CONFIG_DIR.expanduser() / "projects"


def legacy_state_home() -> Path | None:
    """Where a pre-``CLAUDE_CONFIG_DIR`` version would have put state, if it exists.

    Used by ``fleet migrate`` to find state stranded by the change, and by the store to
    keep reading it until it is moved.
    """
    legacy = Path("~/.claude-work/projects").expanduser()
    return legacy if legacy.parent.exists() else None


class FleetStore:
    """Filesystem layout + concurrency primitives for one project's fleet."""

    def __init__(self, cwd: Path | None = None) -> None:
        self.repo_root = repo_root(cwd)
        self.slug = str(self.repo_root).replace("/", "-")
        self.base = _state_home() / self.slug / "fleet"
        # Keep serving state written by a pre-CLAUDE_CONFIG_DIR version until it is
        # migrated: silently starting empty would look like every worker vanished.
        if not self.base.exists():
            legacy_root = legacy_state_home()
            if legacy_root is not None:
                legacy = legacy_root / self.slug / "fleet"
                if legacy.exists():
                    self.base = legacy

    @property
    def is_legacy_location(self) -> bool:
        """Whether this store is currently *reading* from the legacy location."""
        return self.base != self.intended_base()

    def intended_base(self) -> Path:
        """Where this project's state belongs under the current config."""
        return _state_home() / self.slug / "fleet"

    def legacy_base(self) -> Path | None:
        """This project's legacy state directory, if one exists and is not already the
        intended location.

        Checked directly rather than inferred from ``base``: once state exists at the
        destination the store stops falling back, but stranded legacy state may still
        be sitting there and must not be reported as migrated.
        """
        legacy_root = legacy_state_home()
        if legacy_root is None:
            return None
        legacy = legacy_root / self.slug / "fleet"
        if legacy == self.intended_base() or not legacy.exists():
            return None
        return legacy

    # --- directory layout -------------------------------------------------

    @property
    def workers_dir(self) -> Path:
        return self.base / "workers"

    @property
    def mail_dir(self) -> Path:
        return self.base / "mail"

    @property
    def archive_dir(self) -> Path:
        return self.base / "archive"

    @property
    def lock_path(self) -> Path:
        return self.base / ".lock"

    @property
    def quartermaster_notes(self) -> Path:
        return self.base / "quartermaster.md"

    def worker_path(self, callsign: str) -> Path:
        return self.workers_dir / f"{callsign}.md"

    def mail_path(self, callsign: str) -> Path:
        return self.mail_dir / f"{callsign}.md"

    def ensure_dirs(self) -> None:
        for d in (self.workers_dir, self.mail_dir, self.archive_dir):
            d.mkdir(parents=True, exist_ok=True)

    def worker_files(self) -> list[Path]:
        if not self.workers_dir.exists():
            return []
        return sorted(self.workers_dir.glob("*.md"))

    def live_callsigns(self) -> list[str]:
        return [p.stem for p in self.worker_files()]

    # --- concurrency primitives ------------------------------------------

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Exclusive, cross-process advisory lock guarding callsign allocation."""
        self.ensure_dirs()
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def atomic_write(path: Path, content: str) -> None:
        """Write via a temp file in the same dir + os.replace, so readers never see a
        partial file and concurrent writers don't interleave."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
