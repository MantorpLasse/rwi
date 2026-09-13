"""RWI HQ "Bilingual Static Site - Slice 2: English Build + Language
Switcher" mission: focused tests for the full bilingual build - the
language switcher, hreflang alternates, page/path parity between locales,
html lang, and the two previously-Swedish-only static pages (om.html/
ordlista.html) rendering in English.

Every test uses an isolated in-memory database, matching this repository's
own established convention. Evidence/source-derived content is asserted
identical between locales wherever a test touches it - never translated.
"""
from __future__ import annotations

import json
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
    airport = Airport(name="Test Field", iata_code="TST", country="USA")
    session.add(airport)
    session.flush()
    source = Source(
        title="Original Source Title", source_type="aip_grant", reliability_level="official",
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


def _build(tmp_path: Path) -> Path:
    engine = _engine()
    with Session(engine) as session:
        _seed(session)
        output = tmp_path / "site"
        build_site(output, session=session, today=TODAY)
    return output


# --- 1/2/3: normal build creates both trees, Swedish paths unchanged, parity ---


class TestBuildProducesBothTrees:
    def test_normal_build_creates_swedish_root_and_en_tree(self, tmp_path):
        output = _build(tmp_path)
        assert (output / "index.html").exists()
        assert (output / "en" / "index.html").exists()

    def test_existing_swedish_paths_unchanged(self, tmp_path):
        output = _build(tmp_path)
        for path in ("index.html", "marknadslage.html", "om.html", "ordlista.html",
                     "airports/index.html", "signals/index.html", "data.json"):
            assert (output / path).exists(), path

    def test_page_count_parity_between_locales(self, tmp_path):
        output = _build(tmp_path)
        sv_pages = sorted(p.relative_to(output) for p in output.rglob("*.html") if "en" not in p.relative_to(output).parts)
        en_pages = sorted(p.relative_to(Path(output, "en")) for p in (output / "en").rglob("*.html"))
        assert len(sv_pages) == len(en_pages)
        assert len(sv_pages) > 0

    def test_no_templates_sv_or_templates_en_directories_introduced(self):
        templates_dir = Path(__file__).resolve().parents[1] / "app" / "static_export" / "templates"
        assert not (templates_dir.parent / "templates_sv").exists()
        assert not (templates_dir.parent / "templates_en").exists()
        # Confirms the ONE shared template set is still what's on disk.
        assert templates_dir.exists()


# --- 4/5/6: data.json parity -------------------------------------------------


class TestDataJsonParity:
    def test_both_data_json_files_exist(self, tmp_path):
        output = _build(tmp_path)
        assert (output / "data.json").exists()
        assert (output / "en" / "data.json").exists()

    def test_same_schema(self, tmp_path):
        output = _build(tmp_path)
        sv = json.loads((output / "data.json").read_text(encoding="utf-8"))
        en = json.loads((output / "en" / "data.json").read_text(encoding="utf-8"))
        assert set(sv.keys()) == set(en.keys()) == {"generated_at", "airports", "signals"}

    def test_canonical_fields_identical_localized_fields_differ(self, tmp_path):
        output = _build(tmp_path)
        sv = json.loads((output / "data.json").read_text(encoding="utf-8"))
        en = json.loads((output / "en" / "data.json").read_text(encoding="utf-8"))
        sv_sig, en_sig = sv["signals"][0], en["signals"][0]
        for field in ("id", "title", "source_title", "source_url", "airport_name"):
            assert sv_sig[field] == en_sig[field], field
        assert sv_sig["category_label"] != en_sig["category_label"]


# --- 8/9: html lang ------------------------------------------------------------


class TestHtmlLang:
    def test_swedish_pages_have_lang_sv(self, tmp_path):
        output = _build(tmp_path)
        html = (output / "index.html").read_text(encoding="utf-8")
        assert '<html lang="sv">' in html

    def test_english_pages_have_lang_en(self, tmp_path):
        output = _build(tmp_path)
        html = (output / "en" / "index.html").read_text(encoding="utf-8")
        assert '<html lang="en">' in html


# --- 10/11/12/15: language switcher present and correct on every sampled page --


class TestLanguageSwitcher:
    def _sampled_pairs(self, output: Path, signal_id: int, airport_id: int):
        return [
            ("index.html", "en/index.html"),
            ("marknadslage.html", "en/market.html"),
            ("om.html", "en/om.html"),
            ("ordlista.html", "en/ordlista.html"),
            (f"signals/{signal_id}.html", f"en/signals/{signal_id}.html"),
            (f"airports/{airport_id}.html", f"en/airports/{airport_id}.html"),
        ]

    def test_switcher_present_on_every_sampled_page(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport, signal = _seed(session)
            output = tmp_path / "site"
            build_site(output, session=session, today=TODAY)
            sig_id, air_id = signal.id, airport.id
        for sv_path, en_path in self._sampled_pairs(output, sig_id, air_id):
            for p in (sv_path, en_path):
                html = (output / p).read_text(encoding="utf-8")
                assert "lang-switch" in html, p
                assert 'aria-current="page"' in html, p

    def test_homepage_switch_links_correctly(self, tmp_path):
        output = _build(tmp_path)
        sv_html = (output / "index.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "index.html").read_text(encoding="utf-8")
        assert 'href="en/index.html"' in sv_html
        assert 'href="../index.html"' in en_html

    def test_nested_signal_detail_switch_links_correctly(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport, signal = _seed(session)
            output = tmp_path / "site"
            build_site(output, session=session, today=TODAY)
            sig_id = signal.id
        sv_html = (output / "signals" / f"{sig_id}.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "signals" / f"{sig_id}.html").read_text(encoding="utf-8")
        assert f'href="../en/signals/{sig_id}.html"' in sv_html
        assert f'href="../../signals/{sig_id}.html"' in en_html

    def test_nested_airport_detail_switch_links_correctly(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport, signal = _seed(session)
            output = tmp_path / "site"
            build_site(output, session=session, today=TODAY)
            air_id = airport.id
        sv_html = (output / "airports" / f"{air_id}.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "airports" / f"{air_id}.html").read_text(encoding="utf-8")
        assert f'href="../en/airports/{air_id}.html"' in sv_html
        assert f'href="../../airports/{air_id}.html"' in en_html

    def test_market_page_switch_handles_differing_filename(self, tmp_path):
        """The one page whose filename genuinely differs by locale
        (marknadslage.html <-> market.html) - proves the switcher's own
        page-path mapping, not just the common identity case."""
        output = _build(tmp_path)
        sv_html = (output / "marknadslage.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "market.html").read_text(encoding="utf-8")
        assert 'href="en/market.html"' in sv_html
        assert 'href="../marknadslage.html"' in en_html


# --- hreflang -----------------------------------------------------------------


class TestHreflang:
    def test_hreflang_alternates_present_and_correct(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport, signal = _seed(session)
            output = tmp_path / "site"
            build_site(output, session=session, today=TODAY)
            sig_id = signal.id
        sv_html = (output / "signals" / f"{sig_id}.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "signals" / f"{sig_id}.html").read_text(encoding="utf-8")
        assert f'hreflang="sv" href="{sig_id}.html"' in sv_html
        assert f'hreflang="en" href="../en/signals/{sig_id}.html"' in sv_html
        assert f'hreflang="sv" href="../../signals/{sig_id}.html"' in en_html
        assert f'hreflang="en" href="{sig_id}.html"' in en_html

    def test_no_canonical_link_emitted(self, tmp_path):
        """No production hostname/base URL exists anywhere in this
        repository's configuration - this mission explicitly forbids
        fabricating one, so rel="canonical" is never emitted (documented
        limitation, not an oversight)."""
        output = _build(tmp_path)
        for path in (output / "index.html", output / "en" / "index.html"):
            html = path.read_text(encoding="utf-8")
            assert 'rel="canonical"' not in html


# --- 18/19/20/21: no untranslated Swedish chrome; evidence/source unchanged ---


class TestNoLeakedChromeEvidencePreserved:
    def test_no_untranslated_swedish_chrome_in_english_tree(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            _seed(session)
            output = tmp_path / "site"
            build_site(output, session=session, today=TODAY)
        markers = [
            "Flygplats<", "Signaler<", "Läge<", "Projekttyp<", "Källa<", "Detaljer<",
            "Aktuella möjligheter", "Under bevakning", "Behöver research", "Ekonomi<",
            "Marknadsläge<", "Ordlista<", "ansvarsfriskrivning", "Bevakas",
        ]
        for html_path in (output / "en").rglob("*.html"):
            html = html_path.read_text(encoding="utf-8")
            for marker in markers:
                assert marker not in html, f"{marker!r} leaked into {html_path}"

    def test_original_evidence_source_notes_unchanged_between_locales(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport, signal = _seed(session)
            output = tmp_path / "site"
            build_site(output, session=session, today=TODAY)
            sig_id = signal.id
        sv_html = (output / "signals" / f"{sig_id}.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "signals" / f"{sig_id}.html").read_text(encoding="utf-8")
        needle = "Ursprunglig svensk källtext, ska aldrig översättas."
        assert needle in sv_html
        assert needle in en_html

    def test_source_title_identical_across_locales(self, tmp_path):
        engine = _engine()
        with Session(engine) as session:
            airport, signal = _seed(session)
            output = tmp_path / "site"
            build_site(output, session=session, today=TODAY)
            sig_id = signal.id
        sv_html = (output / "signals" / f"{sig_id}.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "signals" / f"{sig_id}.html").read_text(encoding="utf-8")
        assert "Original Source Title" in sv_html
        assert "Original Source Title" in en_html

    def test_airport_name_identical_across_locales(self, tmp_path):
        output = _build(tmp_path)
        sv_html = (output / "airports" / "1.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "airports" / "1.html").read_text(encoding="utf-8")
        assert "Test Field" in sv_html
        assert "Test Field" in en_html


# --- 22: lifecycle/status/category labels localized correctly -----------------


class TestLifecycleStatusCategoryLocalized:
    def test_lifecycle_and_category_labels_differ_by_locale_on_market_page(self, tmp_path):
        output = _build(tmp_path)
        sv_html = (output / "marknadslage.html").read_text(encoding="utf-8")
        en_html = (output / "en" / "market.html").read_text(encoding="utf-8")
        assert "Aktuella möjligheter" in sv_html
        assert "Active opportunities" in en_html
        assert "Under bevakning" in sv_html
        assert "Developing watch" in en_html


# --- 23/24: om.html and ordlista.html render in English ------------------------


class TestAboutAndGlossaryEnglish:
    def test_about_page_renders_in_english(self, tmp_path):
        output = _build(tmp_path)
        html = (output / "en" / "om.html").read_text(encoding="utf-8")
        assert "About" in html
        assert "not affiliated with" in html
        assert "Om sidan" not in html

    def test_glossary_page_renders_in_english(self, tmp_path):
        output = _build(tmp_path)
        html = (output / "en" / "ordlista.html").read_text(encoding="utf-8")
        assert "Glossary" in html
        assert "Engineered Material Arresting System" in html
        assert "Ordlista" not in html

    def test_about_and_glossary_still_render_in_swedish_unchanged(self, tmp_path):
        output = _build(tmp_path)
        om_html = (output / "om.html").read_text(encoding="utf-8")
        ordlista_html = (output / "ordlista.html").read_text(encoding="utf-8")
        assert "OM DEN HÄR SIDAN" in om_html
        assert "Ordlista" in ordlista_html
