"""The worker file format.

Frontmatter and body sections are parsed and serialized by hand rather than with a YAML
dependency, so the edge cases that a library would absorb are ours to pin: values with
colons, quoting round-trips, and unknown keys written by a future version.
"""

from __future__ import annotations

import pytest

from fleet.worker import SECTION_ORDER, Worker


def render_parse(worker: Worker) -> Worker:
    """Round-trip a worker through its serialized form."""
    return Worker.parse(worker.render())


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------

def test_round_trips_scalar_fields():
    original = Worker(worker="alpha", stage="execution", status="running", thread="auth")

    result = render_parse(original)

    assert result.worker == "alpha"
    assert result.stage == "execution"
    assert result.status == "running"
    assert result.thread == "auth"


@pytest.mark.parametrize(
    "value",
    [
        "fix: the parser",          # next steps read like prose and contain ': '
        'the "hard" one',
        r"C:\repos\wt\alpha",       # backslashes, against hand-rolled escaping
    ],
    ids=["colon", "quotes", "backslashes"],
)
def test_round_trips_awkward_values(value: str):
    """Quoting is hand-rolled, so anything that could break it round-trips here."""
    assert render_parse(Worker(worker="alpha", thread=value)).thread == value


def test_omits_unset_fields():
    rendered = Worker(worker="alpha").render()

    assert "thread:" not in rendered
    assert "worker: alpha" in rendered


def test_preserves_unknown_frontmatter_keys():
    """Forward compatibility: a newer fleet's fields must not be dropped."""
    text = "---\nworker: alpha\nfuture_field: keep me\n---\n\n## Task\nwork\n"

    result = Worker.parse(text).render()

    assert "future_field: keep me" in result


def test_parses_a_file_with_no_body():
    result = Worker.parse("---\nworker: alpha\nstatus: running\n---\n")

    assert result.worker == "alpha"
    assert result.body.strip() == ""


# --------------------------------------------------------------------------
# body sections
# --------------------------------------------------------------------------

def test_sets_and_reads_a_section():
    worker = Worker(worker="alpha")

    worker.set_section("Task", "refactor auth")

    assert worker.get_section("Task") == "refactor auth"


def test_replaces_an_existing_section_rather_than_duplicating():
    worker = Worker(worker="alpha")
    worker.set_section("Task", "first")

    worker.set_section("Task", "second")

    assert worker.get_section("Task") == "second"
    assert worker.render().count("## Task") == 1


def test_section_lookup_is_case_insensitive():
    worker = Worker(worker="alpha")
    worker.set_section("Task", "work")

    assert worker.get_section("task") == "work"


def test_appends_bullets_in_order():
    worker = Worker(worker="alpha")

    worker.append_bullet("Observations", "first")
    worker.append_bullet("Observations", "second")

    assert worker.get_section("Observations") == "- first\n- second"


def test_numbered_bullets_increment():
    worker = Worker(worker="alpha")

    worker.append_bullet("To-Do", "first", numbered=True)
    worker.append_bullet("To-Do", "second", numbered=True)

    assert worker.get_section("To-Do") == "1. first\n2. second"


def test_removes_a_section():
    worker = Worker(worker="alpha")
    worker.set_section("Question", "which runner?")

    worker.remove_section("Question")

    assert worker.get_section("Question") is None


def test_new_sections_follow_the_declared_order():
    """Sections are inserted by rank so the file reads consistently."""
    worker = Worker(worker="alpha")

    worker.set_section("To-Do", "later")
    worker.set_section("Task", "brief")

    body = worker.render()
    assert body.index("## Task") < body.index("## To-Do")
    assert SECTION_ORDER.index("Task") < SECTION_ORDER.index("To-Do")


def test_unknown_sections_are_appended_and_kept():
    worker = Worker(worker="alpha")
    worker.set_section("Task", "brief")

    worker.set_section("Scratch", "notes")

    assert render_parse(worker).get_section("Scratch") == "notes"


def test_sections_survive_a_round_trip():
    worker = Worker(worker="alpha")
    worker.set_section("Task", "refactor auth")
    worker.append_bullet("Observations", "uses fcntl")

    result = render_parse(worker)

    assert result.get_section("Task") == "refactor auth"
    assert result.get_section("Observations") == "- uses fcntl"


def test_multiline_section_content_survives():
    worker = Worker(worker="alpha")
    worker.set_section("Task", "line one\nline two\nline three")

    assert render_parse(worker).get_section("Task") == "line one\nline two\nline three"
