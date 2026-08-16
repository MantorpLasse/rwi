from datetime import date
from types import SimpleNamespace

from app.static_export.build import _recent_changes_view
from app.static_export.presentation import status_view, text


def test_status_presentation_is_centralized_and_localized_without_domain_changes():
    assert status_view("completed") == ("Färdigställd", "completed")
    assert status_view("completed", "en") == ("Completed", "completed")
    assert text("project_type") == "Projekttyp"
    assert text("project_type", "en") == "Project type"


def test_unknown_status_falls_back_without_inventing_a_status_meaning():
    assert status_view("future_unknown") == ("future unknown", "unknown")
    assert status_view(None) == ("Ej angiven", "unknown")


def test_dashboard_feed_is_bounded_and_excludes_watch_and_old_completed_items():
    def signal(id, *, status, published, planning_year=None):
        return SimpleNamespace(
            id=id, status=status, source_published_date=published,
            target_year=None, planning_year=planning_year,
            category_label="Projekt", category_class="new", title=f"Signal {id}",
            airport_id=1, airport_code="TST", airport_name="Test Field",
        )

    entries = _recent_changes_view(
        [],
        [
            signal(1, status="identified", published=date(2026, 8, 1)),
            signal(2, status="completed", published=date(2024, 1, 1)),
            signal(3, status="procurement", published=date(2026, 8, 2)),
            signal(4, status="design", published=date(2025, 1, 1), planning_year=2027),
            signal(5, status="under construction", published=date(2026, 8, 3)),
        ],
        limit=2,
        as_of=date(2026, 8, 16),
    )

    assert [entry.id for entry in entries] == [5, 3]
    assert all(entry.kind == "signal" for entry in entries)
    assert entries[0].date_label == "2026-08-03"


def test_dashboard_feed_default_limit_is_five():
    def signal(id, published):
        return SimpleNamespace(
            id=id, status="procurement", source_published_date=published,
            target_year=None, planning_year=None,
            category_label="Projekt", category_class="new", title=f"Signal {id}",
            airport_id=1, airport_code="TST", airport_name="Test Field",
        )

    entries = _recent_changes_view(
        [],
        [signal(i, date(2026, 1, i)) for i in range(1, 8)],
        as_of=date(2026, 8, 16),
    )

    assert len(entries) == 5
    assert [entry.id for entry in entries] == [7, 6, 5, 4, 3]
