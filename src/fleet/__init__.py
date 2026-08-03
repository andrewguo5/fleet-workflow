"""fleet — manage a fleet of parallel coding agents in isolated git worktrees."""

from importlib import metadata

try:
    # Read from installed package metadata rather than a second literal. A hardcoded
    # copy silently drifts from pyproject.toml: this one sat at 0.1.0 through two
    # releases because nothing reads it on the release path.
    __version__ = metadata.version("fleet-workflow")
except metadata.PackageNotFoundError:
    # Running from a source checkout with no install (the test suite does this).
    __version__ = "0.0.0.dev0"
