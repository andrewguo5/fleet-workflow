"""Status compilation — the shared engine behind both the read-only dashboard
(``fleet watch``) and ``fleet status``. Derives everything by scanning the worker
files; there is no separate registry.
"""

from __future__ import annotations

from datetime import datetime

from rich.console import Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import mailbox
from .store import FleetStore
from .worker import Worker

STALE_AFTER_HOURS = 6

_STATUS_STYLE = {
    "running": "green",
    "waiting": "yellow",
    "requesting-input": "bold red",
    "recruited": "cyan",
    "done": "dim",
}


def load_workers(store: FleetStore) -> list[Worker]:
    workers = []
    for path in store.worker_files():
        workers.append(Worker.parse(path.read_text(encoding="utf-8")))
    return workers


def _age_minutes(stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return int((datetime.now() - then).total_seconds() // 60)


def is_stale(worker: Worker) -> bool:
    age = _age_minutes(worker.claimed)
    return age is not None and age > STALE_AFTER_HOURS * 60


def _fmt_age(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes / 60
    return f"{hours:.0f}h" if hours < 24 else f"{hours / 24:.0f}d"


def build_table(store: FleetStore) -> Table:
    workers = load_workers(store)
    table = Table(title="fleet — quartermaster view", title_style="bold", expand=True)
    table.add_column("Callsign", style="bold")
    table.add_column("Thread")
    table.add_column("Stage")
    table.add_column("Status")
    table.add_column("Next step")
    table.add_column("Mail", justify="right")
    table.add_column("Updated", justify="right")

    if not workers:
        table.add_row("—", "no workers recruited yet", "", "", "", "", "")
        return table

    for w in workers:
        status = w.status or "?"
        status_text = Text(status, style=_STATUS_STYLE.get(status, ""))
        unread = mailbox.unread_count(store, w.worker)
        mail_cell = Text(str(unread), style="bold magenta") if unread else Text("·", style="dim")
        updated = _fmt_age(_age_minutes(w.updated))
        if is_stale(w):
            updated = f"[red]{updated} stale[/red]"
        table.add_row(
            w.worker,
            escape(w.thread or "—"),
            escape(w.stage or "—"),
            status_text,
            escape(w.next_step or "—"),
            mail_cell,
            updated,
        )
    return table


def _worker_detail(w: Worker) -> Panel:
    parts: list[str] = []
    for section in ("Task", "Question", "Complaints", "Observations"):
        content = w.get_section(section)
        if content and content.strip():
            parts.append(f"[bold]{section}[/bold]\n{escape(content.strip())}")
    body = "\n\n".join(parts) if parts else "[dim](nothing surfaced)[/dim]"
    border = "red" if w.status == "requesting-input" else "cyan"
    header = f"{w.worker} — {escape(w.thread or 'unlabeled')}  ([{_STATUS_STYLE.get(w.status or '', '')}]" \
             f"{w.status or '?'}[/])"
    return Panel(body, title=header, border_style=border, title_align="left")


def build_report(store: FleetStore, verbose: bool = False) -> Group:
    """Table, plus (when verbose) a detail panel per worker highlighting what each has
    surfaced — the QM's deeper read."""
    renderables: list = [build_table(store)]
    if verbose:
        for w in load_workers(store):
            renderables.append(_worker_detail(w))
    return Group(*renderables)
