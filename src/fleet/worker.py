"""The worker file model: YAML-ish frontmatter (flat scalars) + a markdown body of
named ``## Sections``.

We parse/serialize by hand rather than depend on PyYAML: the frontmatter is always a
flat map of string scalars, so a tiny parser is enough and keeps the dependency
surface to typer + rich. Every write goes through ``FleetStore.atomic_write``; the
worker is the sole writer of its own file, and body sections the engine doesn't manage
are preserved verbatim across ``fleet sync`` calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

STAGES = ("observation", "strategizing", "execution")
STATUSES = ("recruited", "running", "waiting", "requesting-input", "done")

# Frontmatter emitted in this fixed order; None values are omitted.
FRONT_FIELDS = (
    "worker", "thread", "stage", "status", "branch", "worktree", "provider",
    "claimed", "updated", "previous_step", "next_step", "handoff",
)

# Body sections rendered in this order when created fresh.
SECTION_ORDER = ("Task", "Observations", "Complaints", "To-Do", "Question")


def now_stamp() -> str:
    """Local timestamp to the minute, ISO-ish (e.g. 2026-07-18T15:40)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def today_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _dump_scalar(value: str) -> str:
    """Quote a frontmatter value only when needed (colons, hashes, edge whitespace)."""
    needs_quote = (
        value != value.strip()
        or ": " in value
        or value.endswith(":")
        or value.startswith(("#", "&", "*", "!", "|", ">", "'", '"', "@", "`"))
        or value == ""
    )
    if needs_quote:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _load_scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return raw


@dataclass
class Worker:
    """One worker's live state. Frontmatter fields are attributes; the markdown body
    (its ``## Sections``) is kept as raw text and edited section-wise."""

    worker: str
    thread: str | None = None
    stage: str | None = None
    status: str | None = None
    branch: str | None = None
    worktree: str | None = None
    provider: str | None = None
    claimed: str | None = None
    updated: str | None = None
    previous_step: str | None = None
    next_step: str | None = None
    handoff: str | None = None
    body: str = ""
    _extra: dict[str, str] = field(default_factory=dict)

    # --- (de)serialization ------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "Worker":
        front: dict[str, str] = {}
        body = text
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                block = text[3:end].strip("\n")
                body = text[end + 4:].lstrip("\n")
                for line in block.splitlines():
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    if ":" not in line:
                        continue
                    key, _, raw = line.partition(":")
                    front[key.strip()] = _load_scalar(raw)
        known = {k: front.pop(k) for k in list(front) if k in FRONT_FIELDS}
        w = cls(worker=known.get("worker", ""), body=body, _extra=front)
        for k, v in known.items():
            setattr(w, k, v)
        return w

    def render(self) -> str:
        lines = ["---"]
        for key in FRONT_FIELDS:
            value = getattr(self, key)
            if value is not None and value != "":
                lines.append(f"{key}: {_dump_scalar(str(value))}")
        for key, value in self._extra.items():
            lines.append(f"{key}: {_dump_scalar(str(value))}")
        lines.append("---")
        text = "\n".join(lines) + "\n"
        if self.body.strip():
            text += "\n" + self.body.rstrip() + "\n"
        return text

    # --- section editing --------------------------------------------------

    def _sections(self) -> list[tuple[str, str]]:
        """Split the body into (title, content) pairs. Preamble (before the first
        ``## ``) is returned with an empty title."""
        sections: list[tuple[str, str]] = []
        current_title = ""
        current: list[str] = []
        for line in self.body.splitlines():
            if line.startswith("## "):
                sections.append((current_title, "\n".join(current).strip("\n")))
                current_title = line[3:].strip()
                current = []
            else:
                current.append(line)
        sections.append((current_title, "\n".join(current).strip("\n")))
        return sections

    def _write_sections(self, sections: list[tuple[str, str]]) -> None:
        chunks: list[str] = []
        for title, content in sections:
            if title == "":
                if content.strip():
                    chunks.append(content.strip())
                continue
            block = f"## {title}"
            if content.strip():
                block += "\n" + content.strip()
            chunks.append(block)
        self.body = "\n\n".join(chunks).strip() + "\n" if chunks else ""

    def get_section(self, title: str) -> str | None:
        for t, content in self._sections():
            if t.lower() == title.lower():
                return content
        return None

    def set_section(self, title: str, content: str) -> None:
        sections = self._sections()
        for i, (t, _) in enumerate(sections):
            if t.lower() == title.lower():
                sections[i] = (title, content)
                self._write_sections(sections)
                return
        self._insert_section(sections, title, content)

    def remove_section(self, title: str) -> None:
        sections = [(t, c) for (t, c) in self._sections() if t.lower() != title.lower()]
        self._write_sections(sections)

    def append_bullet(self, title: str, bullet: str, numbered: bool = False) -> None:
        existing = self.get_section(title) or ""
        lines = [ln for ln in existing.splitlines() if ln.strip()]
        marker = f"{len(lines) + 1}." if numbered else "-"
        lines.append(f"{marker} {bullet.strip()}")
        self.set_section(title, "\n".join(lines))

    def _insert_section(self, sections: list[tuple[str, str]], title: str, content: str) -> None:
        """Insert a new section honoring SECTION_ORDER."""
        try:
            target_rank = SECTION_ORDER.index(title)
        except ValueError:
            sections.append((title, content))
            self._write_sections(sections)
            return
        insert_at = len(sections)
        for i, (t, _) in enumerate(sections):
            if t in SECTION_ORDER and SECTION_ORDER.index(t) > target_rank:
                insert_at = i
                break
        sections.insert(insert_at, (title, content))
        self._write_sections(sections)
