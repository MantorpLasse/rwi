"""RWI HQ "Bilingual Static Site" mission, Slice 1 (Localization Plumbing)
and Slice 2 (English Build + Language Switcher): focused tests proving
locale threading through the static-export view-model layer works.

Originally written for Slice 1, when the default (no locale passed) build
was still Swedish-only and no public English tree existed. Slice 2
superseded that: a normal `build_site()` call now always produces both the
Swedish root tree and its English mirror under `/en/` (see
tests/test_static_export_bilingual_site.py for Slice 2's own full-build,
language-switcher, and hreflang tests) - the small number of tests below
whose own premise depended on the old single-locale `locale=` kwarg or the
old "no /en/ output" behavior have been updated in place to match.

Every test uses an isolated in-memory database, matching this repository's
own established test_static_export_signal_lifecycle.py convention.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Installation, Signal, Source
from app.services.evidence_claim_semantics import (
    Claim,
    ClaimCategory,
    ClaimProvenance,
    RelationshipFact,
    TemporalContext,
    TemporalQualifier,
)
from app.static_export import build_site
from app.static_export.build import _airport_view, _claim_view, _installation_view, _signal_view

TODAY = date(2026, 9, 13)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_airport_and_signal(session: Session, **signal_overrides):
    airport = Airport(name="Test Field", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    source = Source(title="AIP Grant Source", source_type="aip_grant", reliability_level="official")
    session.add(source)
    session.flush()
    kwargs = dict(
        airport_id=airport.id, source_id=source.id, title="Test signal",
        category="replacement", confidence="high", status="procurement", published=True,
    )
    kwargs.update(signal_overrides)
    signal = Signal(**kwargs)
    session.add(signal)
    session.commit()
    return airport, signal


def _provenance(excerpt: str) -> ClaimProvenance:
    return ClaimProvenance(
        artifact_identity="test.artifact", source_locator="item-1",
        fragment_hash="deadbeef", raw_text_excerpt=excerpt,
    )


# --- 1/2/3/4/7: category/confidence/source-type labels through _signal_view ---


class TestSignalViewLocaleThreading:
    def test_default_locale_is_swedish(self):
        engine = _engine()
        with Session(engine) as session:
            _airport, signal = _seed_airport_and_signal(session)
            view = _signal_view(signal, today=TODAY, session=session)
            assert view.category_label == "Ersättning"
            assert view.category_class == "replace"
            assert view.confidence_label == "Hög"
            assert view.source_type_label == "AIP-bidrag"

    def test_explicit_sv_locale_matches_existing_swedish_labels(self):
        engine = _engine()
        with Session(engine) as session:
            _airport, signal = _seed_airport_and_signal(session)
            view = _signal_view(signal, today=TODAY, session=session, locale="sv")
            assert view.category_label == "Ersättning"
            assert view.confidence_label == "Hög"
            assert view.source_type_label == "AIP-bidrag"
            assert view.source_type_tooltip.startswith("Ett amerikanskt")

    def test_en_locale_produces_english_labels(self):
        engine = _engine()
        with Session(engine) as session:
            _airport, signal = _seed_airport_and_signal(session)
            view = _signal_view(signal, today=TODAY, session=session, locale="en")
            assert view.category_label == "Replacement"
            assert view.category_class == "replace"  # css class never translated
            assert view.confidence_label == "High"
            assert view.source_type_label == "AIP grant"
            assert view.source_type_tooltip.startswith("A US federal")

    def test_lifecycle_and_status_labels_available_in_both_locales(self):
        engine = _engine()
        with Session(engine) as session:
            # source_id=None here (rather than the fixture's default
            # aip_grant source) so SLT1's grant-derived rule never
            # intercepts before the confirmed_vendor bare-fallback rule
            # this test actually wants to exercise.
            _airport, signal = _seed_airport_and_signal(
                session, confirmed_vendor="Runway Safe", source_id=None, status=None,
            )
            sv_view = _signal_view(signal, today=TODAY, session=session, locale="sv")
            en_view = _signal_view(signal, today=TODAY, session=session, locale="en")
            assert sv_view.lifecycle_label == "Aktuell möjlighet"
            assert en_view.lifecycle_label == "Active opportunity"
            assert sv_view.attention_reason == "Runway Safe bekräftad som leverantör"
            assert en_view.attention_reason == "Runway Safe confirmed as vendor"

    def test_canonical_evidence_and_domain_fields_unchanged_between_locales(self):
        """Airport/source names, ids, dates, scores - never touched by
        locale threading."""
        engine = _engine()
        with Session(engine) as session:
            _airport, signal = _seed_airport_and_signal(session)
            sv_view = _signal_view(signal, today=TODAY, session=session, locale="sv")
            en_view = _signal_view(signal, today=TODAY, session=session, locale="en")
            for field in (
                "id", "title", "category", "status", "airport_id", "airport_name",
                "airport_code", "country", "source_title", "source_url", "confirmed_vendor",
            ):
                assert getattr(sv_view, field) == getattr(en_view, field), field


# --- 5/6: claim category / temporal qualifier labels through _claim_view ----


class TestClaimViewLocaleThreading:
    def _claim(self) -> Claim:
        return Claim(
            category=ClaimCategory.EXPLICIT_DOCUMENT_FACT,
            subject="the EMAS bed", statement="reached its life expectancy",
            provenance=_provenance("has reached its life expectancy"),
            temporal=TemporalContext(
                qualifier=TemporalQualifier.HISTORICAL_FACT, as_of_date=date(2024, 8, 19),
            ),
            relationship=RelationshipFact(party="Runway Safe", role="material_supplier"),
        )

    def test_claim_view_default_is_swedish(self):
        view = _claim_view(self._claim())
        assert view.category_label == "Bekräftat sakförhållande"
        assert view.temporal_label == "Historiskt förhållande"
        assert view.relationship_party == "Runway Safe"

    def test_claim_view_en_locale_translates_labels_only(self):
        view = _claim_view(self._claim(), "en")
        assert view.category_label == "Confirmed factual statement"
        assert view.temporal_label == "Historical fact"
        # Never translated: the claim's own statement/excerpt/relationship party.
        assert view.statement == "reached its life expectancy"
        assert view.excerpt == "has reached its life expectancy"
        assert view.relationship_party == "Runway Safe"

    def test_claim_view_with_no_temporal_context(self):
        claim = Claim(
            category=ClaimCategory.PROCEDURAL_REQUEST, subject="x", statement="y",
            provenance=_provenance("z"),
        )
        assert _claim_view(claim).temporal_label is None
        assert _claim_view(claim, "en").temporal_label is None


# --- 7: source-type labels through _installation_view -----------------------


class TestInstallationViewLocaleThreading:
    def test_installation_view_source_type_is_bilingual(self):
        engine = _engine()
        with Session(engine) as session:
            airport = Airport(name="Test Field", iata_code="TST", country="USA")
            session.add(airport)
            session.flush()
            source = Source(title="Master Plan Doc", source_type="master_plan", reliability_level="official")
            session.add(source)
            session.flush()
            installation = Installation(airport_id=airport.id, source_id=source.id, type="EMASMAX")
            session.add(installation)
            session.commit()

            sv_view = _installation_view(installation, "sv")
            en_view = _installation_view(installation, "en")
            assert sv_view.source_type_label == "Master Plan"
            assert en_view.source_type_label == "Master Plan"  # same term in both, by design
            assert sv_view.source_type_tooltip.startswith("En flygplats")
            assert en_view.source_type_tooltip.startswith("An airport's")


# --- 9/10: Jinja t() binding resolves per-build locale -----------------------


class TestJinjaTranslationBinding:
    def test_build_site_default_locale_renders_swedish_nav(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_airport_and_signal(session)
            output = tmp_path / "site_sv_default"
            build_site(output, session=session, today=TODAY)
        html = (output / "index.html").read_text(encoding="utf-8")
        assert "Översikt" in html
        assert "Signaler" in html
        assert 'lang="sv"' in html  # base.html's own hardcoded attribute, untouched this slice

    def test_build_site_single_sv_locale_matches_default_sv_tree(self, tmp_path):
        """RWI HQ 'Bilingual Static Site - Slice 2' mission: build_site()'s
        `locale=` kwarg was replaced by `locales=` (a tuple, default
        `("sv", "en")` - a normal build now always produces both trees).
        A single-locale `locales=("sv",)` build must still produce a
        byte-identical Swedish root tree to the default two-locale build's
        own Swedish half."""
        engine = _engine()
        with Session(engine) as session:
            _seed_airport_and_signal(session)
            output_default = tmp_path / "site_default"
            build_site(output_default, session=session, today=TODAY)
        engine2 = _engine()
        with Session(engine2) as session2:
            _seed_airport_and_signal(session2)
            output_sv_only = tmp_path / "site_sv_only"
            build_site(output_sv_only, session=session2, today=TODAY, locales=("sv",))
        assert (output_default / "index.html").read_text(encoding="utf-8") == (
            output_sv_only / "index.html"
        ).read_text(encoding="utf-8")

    def test_build_site_en_tree_renders_english_nav_text(self, tmp_path):
        """Slice 2: a normal build's /en/ subtree is now fully translated -
        superseding Slice 1's own 'not yet translated' finding."""
        engine = _engine()
        with Session(engine) as session:
            _seed_airport_and_signal(session)
            output = tmp_path / "site_en"
            build_site(output, session=session, today=TODAY)
        html = (output / "en" / "index.html").read_text(encoding="utf-8")
        assert "Overview" in html
        assert "Signals" in html
        assert 'lang="en"' in html

    def test_no_raw_translation_key_rendered_for_known_keys(self, tmp_path):
        """Every t("key") call site in the templates must resolve to real
        text in both locales - a raw key string appearing in the output
        would mean LOCALES["en"] is missing an entry text() falls back to
        returning verbatim."""
        engine = _engine()
        with Session(engine) as session:
            _seed_airport_and_signal(session)
            output = tmp_path / "site_en_rawkey"
            build_site(output, session=session, today=TODAY)
        html = (output / "en" / "index.html").read_text(encoding="utf-8")
        assert "nav_overview" not in html
        assert "nav_signals" not in html
        assert "hero_headline_1" not in html


# --- 15/16: data.json schema unchanged, no /en/ output ----------------------


class TestDataJsonAndOutputTreeUnchanged:
    def test_default_data_json_schema_unchanged(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed_airport_and_signal(session)
            output = tmp_path / "site"
            build_site(output, session=session, today=TODAY)
        data = json.loads((output / "data.json").read_text(encoding="utf-8"))
        assert set(data.keys()) == {"generated_at", "airports", "signals"}
        assert isinstance(data["signals"], list)
        assert data["signals"][0]["category_label"] == "Ersättning"

    def test_en_output_directory_generated_by_default_build(self, tmp_path):
        """RWI HQ 'Bilingual Static Site - Slice 2' mission: supersedes
        Slice 1's own 'no /en/ output yet' finding - a normal build now
        always produces the full English mirror under /en/."""
        engine = _engine()
        with Session(engine) as session:
            _seed_airport_and_signal(session)
            output = tmp_path / "site"
            build_site(output, session=session, today=TODAY)
        assert (output / "en" / "index.html").exists()
        assert (output / "en" / "data.json").exists()
        assert (output / "en" / "style.css").exists()

    def test_locales_sv_only_produces_no_en_output(self, tmp_path):
        """Passing a narrower `locales` tuple (e.g. for a fast, isolated
        test build) still works - only the requested locale trees are
        written."""
        engine = _engine()
        with Session(engine) as session:
            _seed_airport_and_signal(session)
            output = tmp_path / "site_sv_only"
            build_site(output, session=session, today=TODAY, locales=("sv",))
        assert not (output / "en").exists()
        assert (output / "index.html").exists()


# --- Airport-level locale threading (full page pass) ------------------------


class TestAirportViewLocaleThreading:
    def test_airport_view_signals_and_installations_both_translate(self):
        engine = _engine()
        with Session(engine) as session:
            airport, signal = _seed_airport_and_signal(session)
            install_source = Source(title="Master Plan", source_type="master_plan", reliability_level="official")
            session.add(install_source)
            session.flush()
            installation = Installation(airport_id=airport.id, source_id=install_source.id, type="EMASMAX")
            session.add(installation)
            session.commit()
            session.refresh(airport)

            view_sv, _evidence_sv = _airport_view(airport, today=TODAY, session=session, locale="sv")
            view_en, _evidence_en = _airport_view(airport, today=TODAY, session=session, locale="en")

            # The fixture signal's source_type is "aip_grant", so it is
            # classified as a funding signal (see _airport_view()'s own
            # primary_signals/funding_signals split), not a primary one.
            assert view_sv.funding_signals[0].category_label == "Ersättning"
            assert view_en.funding_signals[0].category_label == "Replacement"
            assert view_sv.installations[0].source_type_label == "Master Plan"
            assert view_en.installations[0].source_type_label == "Master Plan"
            # Airport identity itself never varies by locale.
            assert view_sv.name == view_en.name == "Test Field"
