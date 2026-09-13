from datetime import date
from types import SimpleNamespace

from app.static_export.build import _recent_changes_view
from app.static_export.presentation import (
    ATTENTION_STATUS_PRESENTATION,
    CATEGORY_PRESENTATION,
    CLAIM_CATEGORY_PRESENTATION,
    CONFIDENCE_LABEL_PRESENTATION,
    LIFECYCLE_PRESENTATION,
    LOCALES,
    SOURCE_TYPE_PRESENTATION,
    STATUS_PRESENTATION,
    TEMPORAL_QUALIFIER_PRESENTATION,
    attention_status_wording,
    category_view,
    claim_category_label,
    confidence_label,
    source_type_view,
    status_view,
    temporal_qualifier_label,
    text,
)


def test_status_presentation_is_centralized_and_localized_without_domain_changes():
    assert status_view("completed") == ("Färdigställd", "completed")
    assert status_view("completed", "en") == ("Completed", "completed")
    assert text("project_type") == "Projekttyp"
    assert text("project_type", "en") == "Project type"


# --- RWI HQ "Bilingual Static Site - Slice 1: Localization Plumbing"
# mission: the mappings migrated from build.py into this module. ---


def test_category_view_is_bilingual_and_preserves_css_class():
    assert category_view("replacement") == ("Ersättning", "replace")
    assert category_view("replacement", "en") == ("Replacement", "replace")
    # Every canonical key from build.py's old _CATEGORY dict survived the move.
    assert set(CATEGORY_PRESENTATION) == {
        "new_installation", "replacement", "replacement_after_incident", "study",
        "potential_new_construction", "maintenance", "replacement_watch", "unknown",
    }


def test_category_view_unknown_value_falls_back_without_inventing_a_label():
    assert category_view("not_a_real_category") == ("not_a_real_category", "study")
    assert category_view(None) == ("Okänd", "study")
    assert category_view(None, "en") == ("Unclassified", "study")


def test_confidence_label_is_bilingual():
    assert confidence_label("high") == "Hög"
    assert confidence_label("high", "en") == "High"
    assert confidence_label("med") == "Medel"
    assert confidence_label("med", "en") == "Medium"
    assert confidence_label("low") == "Låg"
    assert confidence_label("low", "en") == "Low"


def test_claim_category_label_is_bilingual():
    assert claim_category_label("explicit_document_fact") == "Bekräftat sakförhållande"
    assert claim_category_label("explicit_document_fact", "en") == "Confirmed factual statement"
    assert set(CLAIM_CATEGORY_PRESENTATION) == {
        "explicit_document_fact", "procedural_request", "temporal_statement", "relationship",
    }


def test_temporal_qualifier_label_is_bilingual():
    assert temporal_qualifier_label("historical_fact") == "Historiskt förhållande"
    assert temporal_qualifier_label("historical_fact", "en") == "Historical fact"
    assert set(TEMPORAL_QUALIFIER_PRESENTATION) == {
        "historical_fact", "current_state_as_of_document_date", "planned_future_action",
        "requested_pending_approval", "completed", "unknown",
    }


def test_source_type_view_is_bilingual_with_anchor_and_tooltip():
    label_sv, anchor, tooltip_sv = source_type_view("aip_grant")
    label_en, anchor_en, tooltip_en = source_type_view("aip_grant", "en")
    assert (label_sv, anchor) == ("AIP-bidrag", "aip")
    assert (label_en, anchor_en) == ("AIP grant", "aip")
    assert tooltip_sv.startswith("Ett amerikanskt")
    assert tooltip_en.startswith("A US federal")


def test_source_type_view_none_value_returns_all_none():
    assert source_type_view(None) == (None, None, None)
    assert source_type_view(None, "en") == (None, None, None)


def test_source_type_view_unknown_value_falls_back_to_other_source():
    assert source_type_view("not_a_real_source_type") == ("Övrig källa", None, None)
    assert source_type_view("not_a_real_source_type", "en") == ("Other source", None, None)


def test_source_type_view_no_tooltip_entries_stay_none_in_both_locales():
    assert source_type_view("news") == ("Nyhetskälla", None, None)
    assert source_type_view("news", "en") == ("News source", None, None)


def test_attention_status_wording_is_bilingual():
    assert attention_status_wording("procurement") == "Upphandling pågår"
    assert attention_status_wording("procurement", "en") == "Procurement underway"
    assert attention_status_wording("not_a_real_status") is None
    assert set(ATTENTION_STATUS_PRESENTATION) == {
        "procurement", "under construction", "design", "master_plan",
        "environmental_review", "cip", "alp", "funded",
    }


def test_attention_reason_sentence_templates_are_bilingual():
    assert text("attention_vendor_confirmed").format(vendor="Runway Safe") == "Runway Safe bekräftad som leverantör"
    assert text("attention_vendor_confirmed", "en").format(vendor="Runway Safe") == "Runway Safe confirmed as vendor"
    assert text("attention_grant_current") == "Aktuellt federalt finansieringsunderlag finns"
    assert text("attention_grant_current", "en") == "Current federal funding evidence exists"
    assert text("attention_incident_unconfirmed") == "En incident har registrerats, ersättning inte bekräftad"
    assert (
        text("attention_incident_unconfirmed", "en")
        == "An incident has been recorded, replacement not confirmed"
    )


# --- Translation-mapping locale parity guard (mission test #13): every
# canonical key in every bilingual presentation mapping must carry both
# "sv" and "en" - a missing translation must fail a test, never silently
# render a raw key or fall through unnoticed. ---


def test_all_presentation_mappings_have_full_sv_en_parity():
    for name, entry in LOCALES["sv"].items():
        assert name in LOCALES["en"], f"LOCALES['en'] is missing key {name!r}"
    for name, entry in LOCALES["en"].items():
        assert name in LOCALES["sv"], f"LOCALES['sv'] is missing key {name!r}"

    for mapping_name, mapping in (
        ("STATUS_PRESENTATION", STATUS_PRESENTATION),
        ("LIFECYCLE_PRESENTATION", LIFECYCLE_PRESENTATION),
        ("CATEGORY_PRESENTATION", CATEGORY_PRESENTATION),
        ("CONFIDENCE_LABEL_PRESENTATION", CONFIDENCE_LABEL_PRESENTATION),
        ("CLAIM_CATEGORY_PRESENTATION", CLAIM_CATEGORY_PRESENTATION),
        ("TEMPORAL_QUALIFIER_PRESENTATION", TEMPORAL_QUALIFIER_PRESENTATION),
        ("ATTENTION_STATUS_PRESENTATION", ATTENTION_STATUS_PRESENTATION),
    ):
        for key, entry in mapping.items():
            assert "sv" in entry, f"{mapping_name}[{key!r}] is missing 'sv'"
            assert "en" in entry, f"{mapping_name}[{key!r}] is missing 'en'"

    for key, entry in SOURCE_TYPE_PRESENTATION.items():
        assert "sv" in entry and "en" in entry, f"SOURCE_TYPE_PRESENTATION[{key!r}] is missing sv/en"
        assert "sv_tooltip" in entry and "en_tooltip" in entry, (
            f"SOURCE_TYPE_PRESENTATION[{key!r}] is missing sv_tooltip/en_tooltip"
        )


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
