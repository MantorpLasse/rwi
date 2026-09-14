"""RWI HQ "Bilingual Static Site - Polish Slice" mission: focused tests for
the three manual-preview-review issues fixed here:

1. "Project intelligence" phase-progression stage labels (previously a
   hardcoded Swedish-only dict in build.py, bypassing the locale-aware
   presentation architecture) now render in English on the English site.
2. The language switcher (`.lang-switch`) is visually grouped apart from the
   main nav via a small, existing-dark-theme-consistent CSS rule.
3. watch.js's click-time dynamic aria-label text (the one remaining runtime
   Swedish-only string Slice 2 could not reach, since it is painted by JS,
   not by build.py) is now locale-aware via the page's own <html lang>.

Every test uses an isolated in-memory database, matching this repository's
own established convention. Evidence/source-derived content is asserted
unaffected - this slice touches only presentation chrome.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Airport, Signal, Source
from app.static_export import build_site

TODAY = date(2026, 9, 13)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed(session: Session):
    """A signal with status "procurement" - status_role "procurement" - so
    the primary Signal always has a full 6-step project_phase progression
    (every stage always renders, only `state` differs), which is exactly
    what lets a single seed exercise all six stage labels including
    "identified" (the one previously missing from the shared presentation
    table)."""
    airport = Airport(name="Test Field", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    source = Source(
        title="Original Source Title", source_type="master_plan", reliability_level="official",
        url="https://example.test/source", publisher="Example Publisher",
    )
    session.add(source)
    session.flush()
    signal = Signal(
        airport_id=airport.id, source_id=source.id, title="Test signal",
        category="replacement", confidence="high", status="procurement", published=True,
        source_notes="Ursprunglig svensk källtext, ska aldrig översättas.",
    )
    session.add(signal)
    session.commit()
    return airport, signal


def _build(tmp_path: Path):
    engine = _engine()
    with Session(engine) as session:
        airport, signal = _seed(session)
        output = tmp_path / "site"
        build_site(output, session=session, today=TODAY)
        return output, airport.id, signal.id


# --- 1/2/3: Project intelligence stage labels -----------------------------


class TestProjectPhaseStageLabelsLocalized:
    def test_english_airport_detail_stage_labels_render_in_english(self, tmp_path):
        output, air_id, _ = _build(tmp_path)
        html = (output / "en" / "airports" / f"{air_id}.html").read_text(encoding="utf-8")
        for label in ("Identified", "Planning", "Funded", "Procurement", "Under construction", "Completed"):
            assert label in html, label

    def test_swedish_airport_detail_stage_labels_remain_swedish(self, tmp_path):
        output, air_id, _ = _build(tmp_path)
        html = (output / "airports" / f"{air_id}.html").read_text(encoding="utf-8")
        for label in ("Identifierad", "Planering", "Finansierad", "Upphandling", "Under byggnation", "Färdigställd"):
            assert label in html, label

    def test_no_swedish_stage_labels_leak_into_english_project_intelligence(self, tmp_path):
        output, air_id, _ = _build(tmp_path)
        html = (output / "en" / "airports" / f"{air_id}.html").read_text(encoding="utf-8")
        for swedish_label in ("Identifierad", "Planering", "Finansierad", "Upphandling", "Under byggnation", "Färdigställd"):
            assert swedish_label not in html, swedish_label


# --- 4/5/6: language switcher still correct after the polish -------------


class TestLanguageSwitcherStillCorrect:
    def test_switcher_present_in_both_locales(self, tmp_path):
        output, air_id, _ = _build(tmp_path)
        for path in (output / "airports" / f"{air_id}.html", output / "en" / "airports" / f"{air_id}.html"):
            html = path.read_text(encoding="utf-8")
            assert "lang-switch" in html

    def test_current_locale_marked_correctly(self, tmp_path):
        output, air_id, _ = _build(tmp_path)
        sv_html = (output / "airports" / f"{air_id}.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "airports" / f"{air_id}.html").read_text(encoding="utf-8")
        assert '<span aria-current="page">SV</span>' in sv_html
        assert '<span aria-current="page">EN</span>' in en_html

    def test_switch_target_urls_unchanged(self, tmp_path):
        output, air_id, _ = _build(tmp_path)
        sv_html = (output / "airports" / f"{air_id}.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "airports" / f"{air_id}.html").read_text(encoding="utf-8")
        assert f'href="../en/airports/{air_id}.html"' in sv_html
        assert f'href="../../airports/{air_id}.html"' in en_html


# --- 7: switcher grouping/spacing exists consistently ---------------------


class TestLanguageSwitcherGrouping:
    def test_lang_switch_css_rule_exists(self):
        css_path = Path(__file__).resolve().parents[1] / "app" / "static_export" / "static" / "style.css"
        css = css_path.read_text(encoding="utf-8")
        assert ".lang-switch {" in css
        assert "margin-left" in css.split(".lang-switch {", 1)[1].split("}", 1)[0]

    def test_lang_switch_class_present_on_every_built_page_header(self, tmp_path):
        output, air_id, sig_id = _build(tmp_path)
        for path in (
            output / "index.html", output / "en" / "index.html",
            output / "airports" / f"{air_id}.html", output / "en" / "airports" / f"{air_id}.html",
            output / "signals" / f"{sig_id}.html", output / "en" / "signals" / f"{sig_id}.html",
        ):
            assert 'class="lang-switch"' in path.read_text(encoding="utf-8"), path


# --- 8/9/10: watch.js localization -----------------------------------------


class TestWatchJsLocalized:
    def _watch_js(self, output: Path) -> str:
        return (output / "watch.js").read_text(encoding="utf-8")

    def test_watch_js_exposes_swedish_labels(self, tmp_path):
        output, _, _ = _build(tmp_path)
        js = self._watch_js(output)
        assert "Bevaka signal" in js
        assert "Sluta bevaka signal" in js

    def test_watch_js_exposes_english_labels(self, tmp_path):
        output, _, _ = _build(tmp_path)
        js = self._watch_js(output)
        assert "Watch signal" in js
        assert "Stop watching signal" in js

    def test_watch_js_is_a_single_shared_file_not_duplicated_per_locale(self, tmp_path):
        output, _, _ = _build(tmp_path)
        sv_js = (output / "watch.js").read_text(encoding="utf-8")
        en_js = (output / "en" / "watch.js").read_text(encoding="utf-8")
        assert sv_js == en_js

    def test_watch_js_derives_locale_from_html_lang_no_network_or_cookies(self, tmp_path):
        output, _, _ = _build(tmp_path)
        js = self._watch_js(output)
        assert "documentElement" in js
        assert "fetch(" not in js
        assert "XMLHttpRequest" not in js
        assert "document.cookie" not in js

    def test_watch_unwatch_core_behavior_unchanged(self, tmp_path):
        output, _, _ = _build(tmp_path)
        js = self._watch_js(output)
        assert "rwi_watched_signals" in js
        assert "localStorage" in js
        assert "star-toggle" in js
        assert "global.RWIWatch" in js
        assert "initStars" in js


# --- 11: no evidence/source content affected -------------------------------


class TestEvidenceUnaffected:
    def test_source_notes_and_title_unchanged_between_locales(self, tmp_path):
        output, _, sig_id = _build(tmp_path)
        sv_html = (output / "signals" / f"{sig_id}.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "signals" / f"{sig_id}.html").read_text(encoding="utf-8")
        needle = "Ursprunglig svensk källtext, ska aldrig översättas."
        assert needle in sv_html
        assert needle in en_html
        assert "Original Source Title" in sv_html
        assert "Original Source Title" in en_html
